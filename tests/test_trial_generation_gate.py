"""Local exact-manifest API gate tests; no hosted or provider calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from core.automation.news_generation_journal import request_digest
from core.automation.trial_generation_gate import TrialGenerationDenied, TrialGenerationGate
from core.automation.trial_owner import (CLIENTS, LIMIT_MICROS, NOT_BEFORE,
    POLICY_SHA256, TrialOwner, TrialPlan, canonical_sha256)

NOW = datetime(2026, 10, 14, 15, 40, 51, tzinfo=timezone.utc)
RELEASE = "a" * 40


def _body():
    source = "Official source fixture for one review card."
    request_id = str(uuid4())
    return request_id, {
        "source_content": source, "source_type": "tweet",
        "source_url": "https://x.com/Yellow/status/123456789",
        "source_image_url": "https://pbs.twimg.com/media/fixture.png",
        "template_style": "remix", "mock_mode": False,
        "quality_context": {
            "job_id": str(uuid4()), "request_id": request_id,
            "workspace_id": str(uuid4()), "client_id": "yellow",
            "source_item_id": str(uuid4()),
            "source_published_at": "2026-10-13T23:00:00Z",
            "source_sha256": "a" * 64, "source_content": source,
            "source_url": "https://x.com/Yellow/status/123456789",
            "source_image_url": "https://pbs.twimg.com/media/fixture.png",
        },
    }


@pytest.fixture
def admitted(tmp_path):
    from api.server import NewsCardRequest
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    data = {"contract": "ybo-server-trial@1", "trial_id": str(uuid4()),
        "not_before": NOT_BEFORE, "deadline": "2026-10-14T17:10:51Z",
        "kst_date": "2026-10-15", "client_ids": list(CLIENTS),
        "max_per_client": 1, "max_total": 3,
        "estimated_daily_limit_micros": LIMIT_MICROS,
        "estimate_policy_sha256": POLICY_SHA256, "release_sha": RELEASE}
    plan = TrialPlan.from_mapping(data, canonical_sha256(data))
    owner = TrialOwner(private / f"{plan.trial_id}.trial-owner.sqlite3", plan)
    request_id, body = _body()
    payload = NewsCardRequest(**body).model_dump(mode="json")
    admission = owner.admit(plan.trial_id, NOW)
    assert admission is not None
    assert owner.reserve_phase(admission, "yellow:intake", "yellow", "1" * 64, NOW)
    assert owner.finish_phase(admission, "yellow:intake", "2" * 64, NOW)
    ids = {name: body["quality_context"][name] for name in ("job_id", "request_id", "source_item_id")}
    assert owner.reserve_phase(admission, "yellow:generate", "yellow",
        request_digest("yellow", payload), NOW, identifiers=ids)
    gate = TrialGenerationGate(plan=plan, owner=owner, clock=lambda: NOW)
    return SimpleNamespace(private=private, data=data, plan=plan, owner=owner,
        admission=admission, request_id=request_id, body=body, payload=payload, gate=gate)


def _check(admitted, **overrides):
    return admitted.gate.require_generation(trial_id=overrides.get("trial_id", admitted.plan.trial_id),
        client_id=overrides.get("client_id", "yellow"),
        request_id=overrides.get("request_id", admitted.request_id),
        payload=overrides.get("payload", admitted.payload))


def test_exact_durable_generation_manifest_and_replay_read(admitted):
    assert _check(admitted) is None
    assert _check(admitted) is None  # no second admission or provider attempt
    assert admitted.owner.admit(admitted.plan.trial_id, NOW) is None


@pytest.mark.parametrize("change", ["trial_id", "client_id", "request_id", "payload"])
def test_wrong_identity_or_exact_sha_denied(admitted, change):
    kwargs = {change: ("babylon" if change == "client_id" else str(uuid4()))}
    if change == "payload":
        payload = deepcopy(admitted.payload)
        payload["source_content"] += " changed"
        kwargs = {"payload": payload}
    with pytest.raises(TrialGenerationDenied, match="trial_generation_not_admitted"):
        _check(admitted, **kwargs)


@pytest.mark.parametrize("status", ["blocked", "unknown", "completed"])
def test_terminal_owner_is_effectively_off(admitted, status):
    assert admitted.owner.terminate(admitted.admission, status, NOW)
    with pytest.raises(TrialGenerationDenied, match="trial_generation_not_admitted"):
        _check(admitted)


def test_deadline_and_missing_owner_fail_closed(admitted):
    admitted.gate.clock = lambda: NOW + timedelta(hours=2)
    with pytest.raises(TrialGenerationDenied, match="trial_generation_not_admitted"):
        _check(admitted)
    admitted.owner.path.unlink()
    with pytest.raises(TrialGenerationDenied, match="trial_generation_not_admitted"):
        _check(admitted)


def test_env_requires_exact_release_and_preexisting_owner(admitted):
    env = {"YBO_TRIAL_API_MODE": "true", "YBO_TRIAL_PLAN_JSON": json.dumps(admitted.data),
        "YBO_TRIAL_PLAN_SHA256": admitted.plan.sha256,
        "RAILWAY_GIT_COMMIT_SHA": RELEASE,
        "NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT": str(admitted.private)}
    assert TrialGenerationGate.from_env(env, clock=lambda: NOW, stamp_reader=lambda: RELEASE).require_generation(
        trial_id=admitted.plan.trial_id, client_id="yellow",
        request_id=admitted.request_id, payload=admitted.payload) is None
    with pytest.raises(TrialGenerationDenied, match="trial_generation_release_mismatch"):
        TrialGenerationGate.from_env({**env, "RAILWAY_GIT_COMMIT_SHA": "b" * 40},
            stamp_reader=lambda: RELEASE)
    with pytest.raises(TrialGenerationDenied, match="trial_generation_owner_unavailable"):
        TrialGenerationGate.from_env({**env, "YBO_TRIAL_PLAN_SHA256": "0" * 64},
            stamp_reader=lambda: RELEASE)
    with pytest.raises(TrialGenerationDenied, match="trial_generation_release_mismatch"):
        TrialGenerationGate.from_env(env, stamp_reader=lambda: "b" * 40)


def test_api_trial_mode_denies_missing_marker_and_exact_mismatch_before_quality(admitted, monkeypatch):
    from api import security, server
    from core.automation import quality_news_card
    monkeypatch.setattr(security, "API_KEYS", {"admin": "synthetic-trial-admin-key"})
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_ENABLED", "true")
    monkeypatch.setenv("QUALITY_BANNER_ENABLED", "true")
    monkeypatch.setenv("YBO_TRIAL_API_MODE", "true")
    monkeypatch.setenv("YBO_TRIAL_PLAN_JSON", json.dumps(admitted.data))
    monkeypatch.setenv("YBO_TRIAL_PLAN_SHA256", admitted.plan.sha256)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", RELEASE)
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT", str(admitted.private))
    monkeypatch.setattr(server.TrialGenerationGate, "from_env", lambda env: admitted.gate)
    called = []
    monkeypatch.setattr(server, "_pin_quality_news_mode", lambda *a: called.append("pin"))
    monkeypatch.setattr(quality_news_card, "service_from_env", lambda env: called.append("factory"))
    client = TestClient(server.app)
    headers = {"x-api-key": "synthetic-trial-admin-key", "Idempotency-Key": admitted.request_id}
    path = "/clients/yellow/generate/news-card"
    assert client.post(path, headers=headers, json=admitted.body).status_code == 403
    assert not called
    headers["X-YBO-Trial-ID"] = admitted.plan.trial_id
    changed = deepcopy(admitted.body)
    changed["source_content"] += " altered"
    assert client.post(path, headers=headers, json=changed).status_code == 403
    assert not called


def test_api_trial_only_uses_durable_gate_with_global_quality_off(admitted, monkeypatch):
    from api import security, server
    from core.automation import quality_news_card
    monkeypatch.setattr(security, "API_KEYS", {"admin": "synthetic-trial-admin-key"})
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_ENABLED", "false")
    monkeypatch.setenv("QUALITY_BANNER_ENABLED", "false")
    monkeypatch.setenv("YBO_TRIAL_API_MODE", "true")
    monkeypatch.setenv("YBO_TRIAL_PLAN_JSON", json.dumps(admitted.data))
    monkeypatch.setenv("YBO_TRIAL_PLAN_SHA256", admitted.plan.sha256)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", RELEASE)
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT", str(admitted.private))
    monkeypatch.setattr(server.TrialGenerationGate, "from_env", lambda env: admitted.gate)
    calls = []
    monkeypatch.setattr(server, "_pin_quality_news_mode", lambda *a: calls.append("pin"))
    async def generate(**kwargs):
        calls.append("generate")
        kwargs["before_effect"]()  # real owner readback, no provider
        return {"client_id": "yellow", "content_type": "news_card", "spec": {},
            "png_path": "/synthetic/card.png", "manifest_path": "/synthetic/manifest.json",
            "template_style": "remix", "requested_template_style": "remix",
            "source_image_used": True, "source_image_url": admitted.body["source_image_url"],
            "source_image_sha256": "a" * 64, "source_visual_path": None,
            "figma_template": None, "duration_ms": 1}
    def factory(env, *, trial_authority=None):
        assert trial_authority is admitted.gate and env["QUALITY_BANNER_ENABLED"] == "false"
        assert env["NEWS_CARD_IDEMPOTENCY_ENABLED"] == "false"
        calls.append("factory")
        return SimpleNamespace(generate=generate)
    monkeypatch.setattr(quality_news_card, "service_from_env", factory)
    headers = {"x-api-key": "synthetic-trial-admin-key", "Idempotency-Key": admitted.request_id,
        "X-YBO-Trial-ID": admitted.plan.trial_id}
    path = "/clients/yellow/generate/news-card"
    client = TestClient(server.app)
    assert client.post(path, headers=headers, json=admitted.body).status_code == 200
    assert calls == ["pin", "factory", "generate"]
    stripped = deepcopy(admitted.body)
    stripped.pop("quality_context")
    assert client.post(path, headers=headers, json=stripped).status_code == 403
    assert calls == ["pin", "factory", "generate"]
    assert admitted.owner.terminate(admitted.admission, "unknown", NOW)
    assert client.post(path, headers=headers, json=admitted.body).status_code == 403
    assert calls == ["pin", "factory", "generate"]


def test_collect_only_header_requires_exact_trial_and_reaches_service(admitted, monkeypatch):
    from api import security, server
    from core.automation import quality_news_card
    monkeypatch.setattr(security, "API_KEYS", {"admin": "synthetic-trial-admin-key"})
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_ENABLED", "false")
    monkeypatch.setenv("QUALITY_BANNER_ENABLED", "false")
    monkeypatch.setenv("YBO_TRIAL_API_MODE", "true")
    monkeypatch.setattr(server.TrialGenerationGate, "from_env", lambda env: admitted.gate)
    monkeypatch.setattr(server, "_pin_quality_news_mode", lambda *a: None)
    seen = []
    async def generate(**kwargs):
        seen.append(kwargs.get("collect_only"))
        kwargs["before_effect"]()
        return {"client_id": "yellow", "content_type": "news_card", "spec": {},
            "png_path": "/synthetic/card.png", "manifest_path": "/synthetic/manifest.json",
            "template_style": "remix", "requested_template_style": "remix",
            "source_image_used": True, "source_image_url": admitted.body["source_image_url"],
            "source_image_sha256": "a" * 64, "source_visual_path": None,
            "figma_template": None, "duration_ms": 1}
    monkeypatch.setattr(quality_news_card, "service_from_env", lambda env, **kw:
        SimpleNamespace(generate=generate))
    client = TestClient(server.app)
    path = "/clients/yellow/generate/news-card"
    headers = {"x-api-key": "synthetic-trial-admin-key", "Idempotency-Key": admitted.request_id,
        "X-YBO-Trial-ID": admitted.plan.trial_id, "X-YBO-Trial-Collect-Only": "true"}
    assert client.post(path, headers=headers, json=admitted.body).status_code == 200
    assert seen == [True]
    assert client.post(path, headers={**headers, "X-YBO-Trial-Collect-Only": "false"},
        json=admitted.body).status_code == 403
    without_trial = {k: v for k, v in headers.items() if k != "X-YBO-Trial-ID"}
    assert client.post(path, headers=without_trial, json=admitted.body).status_code == 403
    assert seen == [True]
