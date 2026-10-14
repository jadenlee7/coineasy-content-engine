"""Exact public-route readiness is configuration proof, not owner authority."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from core.automation.trial_generation_gate import TrialGenerationGate, TrialGenerationDenied
from core.automation.trial_owner import TrialPlan, POLICY_SHA256, canonical_sha256


@pytest.fixture
def ready(tmp_path, monkeypatch):
    from api import server, security
    from core.automation.trial_owner import TrialOwner
    payload = {"contract": "ybo-server-trial@1", "trial_id": str(uuid4()),
        "not_before": "2026-10-14T12:44:51Z", "deadline": "2026-10-14T14:44:51Z",
        "kst_date": "2026-10-14", "client_ids": ["yellow", "babylon", "origintrail"],
        "max_per_client": 1, "max_total": 3, "estimated_daily_limit_micros": 10000000,
        "estimate_policy_sha256": POLICY_SHA256, "release_sha": "a" * 40}
    plan = TrialPlan.from_mapping(payload, canonical_sha256(payload))
    tmp_path.chmod(0o700)
    now = [datetime(2026, 10, 14, 14, 20, tzinfo=timezone.utc)]
    env = {"YBO_TRIAL_CONTROLLER_ENABLED": "true", "YBO_TRIAL_API_MODE": "true",
        "YBO_TRIAL_PLAN_JSON": json.dumps(payload), "YBO_TRIAL_PLAN_SHA256": plan.sha256,
        "RAILWAY_GIT_COMMIT_SHA": "a" * 40, "NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT": str(tmp_path),
        **{key: "false" for key in ("QUALITY_BANNER_ENABLED", "NEWS_CARD_IDEMPOTENCY_ENABLED",
            "TELEGRAM_PUBLICATION_ENABLED", "CONTENT_OPS_REVIEW_ENABLED", "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED")}}
    for key, value in env.items(): monkeypatch.setenv(key, value)
    monkeypatch.setattr(security, "API_KEYS", {"admin": "fixture-admin-key-only", "yellow": "fixture-client-key-only"})
    original = TrialGenerationGate.from_env
    gates = []
    def make_gate(environment):
        gates.append(True)
        return original(environment, clock=lambda: now[0], stamp_reader=lambda: "a" * 40)
    monkeypatch.setattr(server.TrialGenerationGate, "from_env", make_gate)
    def forbidden(*args, **kwargs): raise AssertionError("readiness touched owner")
    monkeypatch.setattr(TrialOwner, "_transaction", forbidden)
    monkeypatch.setattr(TrialOwner, "inspect", forbidden)
    monkeypatch.setattr(TrialOwner, "admit", forbidden)
    monkeypatch.setattr(server, "list_active_clients", lambda: [SimpleNamespace(client_id=c, active=True)
        for c in payload["client_ids"]])
    return SimpleNamespace(client=TestClient(server.app), plan=plan, now=now, root=tmp_path,
        gates=gates, server=server, headers={"X-API-Key": "fixture-admin-key-only"})


def test_exact_on_readiness_has_no_owner_or_task_dependency(ready):
    r = ready.client.get("/internal/trial/readiness", headers=ready.headers)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["vary"] == "X-API-Key"
    assert r.json() == {"schema_version": "ybo-api-readiness@1", "ready": True,
        "trial_id": ready.plan.trial_id, "plan_sha256": ready.plan.sha256,
        "release_sha": "a" * 40, "controller_enabled": True, "broad_public_flags_off": True,
        "client_ids": ["yellow", "babylon", "origintrail"], "observed_at": ready.now[0].isoformat()}
    assert list(ready.root.iterdir()) == []


@pytest.mark.parametrize("value", [None, "false", "TRUE"])
def test_previous_off_runtime_refused_before_gate(ready, monkeypatch, value):
    if value is None: monkeypatch.delenv("YBO_TRIAL_CONTROLLER_ENABLED")
    else: monkeypatch.setenv("YBO_TRIAL_CONTROLLER_ENABLED", value)
    r = ready.client.get("/internal/trial/readiness", headers=ready.headers)
    assert r.status_code == 503 and r.json() == {"detail": "trial_generation_disabled"}
    assert ready.gates == [] and list(ready.root.iterdir()) == []


@pytest.mark.parametrize("key,value", [
    ("YBO_TRIAL_API_MODE", "false"), ("YBO_TRIAL_PLAN_SHA256", "0" * 64),
    ("RAILWAY_GIT_COMMIT_SHA", "b" * 40), ("CONTENT_OPS_REVIEW_ENABLED", "true"),
    ("CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED", "true"), ("QUALITY_BANNER_ENABLED", "true"),
    ("CONTENT_OPS_REVIEW_ENABLED", "invalid"), ("CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED", "TRUE")])
def test_plan_release_and_flags_fail_closed(ready, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    assert ready.client.get("/internal/trial/readiness", headers=ready.headers).status_code == 503
    assert list(ready.root.iterdir()) == []


@pytest.mark.parametrize("at", [datetime(2026, 10, 14, 12, 44, 50, tzinfo=timezone.utc),
    datetime(2026, 10, 14, 14, 44, 51, tzinfo=timezone.utc)])
def test_readiness_fixed_window(ready, at):
    ready.now[0] = at
    assert ready.client.get("/internal/trial/readiness", headers=ready.headers).status_code == 503
    assert list(ready.root.iterdir()) == []


@pytest.mark.parametrize("key", ["", "invalid", "fixture-client-key-only"])
def test_readiness_admin_only_before_gate(ready, key):
    assert ready.client.get("/internal/trial/readiness", headers={"X-API-Key": key}).status_code in {401, 403}
    assert ready.gates == [] and list(ready.root.iterdir()) == []


def test_missing_client_not_ready(ready, monkeypatch):
    monkeypatch.setattr(ready.server, "list_active_clients", lambda: [])
    assert ready.client.get("/internal/trial/readiness", headers=ready.headers).status_code == 503
    assert list(ready.root.iterdir()) == []


def test_missing_optional_api_flags_preserves_existing_default_off(ready, monkeypatch):
    monkeypatch.delenv("CONTENT_OPS_REVIEW_ENABLED")
    monkeypatch.delenv("CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED")
    assert ready.client.get("/internal/trial/readiness", headers=ready.headers).status_code == 200
    assert list(ready.root.iterdir()) == []
