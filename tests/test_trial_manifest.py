"""Local durable authorization tests; no provider, DB or Telegram connection."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from core.automation.trial_manifest import TrialManifestDenied, TrialManifestStore
from core.automation.trial_owner import TrialOwner, TrialPlan, canonical_sha256, POLICY_SHA256
from core.automation.trial_generation_gate import TrialGenerationGate
from core.automation.news_generation_journal import request_digest

NOW = datetime(2026, 10, 14, 13, 4, 51, tzinfo=timezone.utc)


@pytest.fixture
def trial(tmp_path):
    from api.server import NewsCardRequest
    tmp_path.chmod(0o700)
    p = {"contract": "ybo-server-trial@1", "trial_id": str(uuid4()),
        "not_before": "2026-10-14T12:44:51Z", "deadline": "2026-10-14T14:44:51Z",
        "kst_date": "2026-10-14", "client_ids": ["yellow", "babylon", "origintrail"],
        "max_per_client": 1, "max_total": 3, "estimated_daily_limit_micros": 10_000_000,
        "estimate_policy_sha256": POLICY_SHA256, "release_sha": "a" * 40}
    plan = TrialPlan.from_mapping(p, canonical_sha256(p))
    owner = TrialOwner(tmp_path / f"{plan.trial_id}.trial-owner.sqlite3", plan)
    admission = owner.admit(plan.trial_id, NOW)
    ids = {k: str(uuid4()) for k in ("job_id", "request_id", "source_item_id")}
    context = {**ids, "client_id": "yellow"}
    payload = NewsCardRequest(source_content="Official test-only fixture.", quality_context=context).model_dump(mode="json")
    assert owner.reserve_phase(admission, "yellow:intake", "yellow", "1" * 64, NOW)
    assert owner.finish_phase(admission, "yellow:intake", "2" * 64, NOW)
    assert owner.reserve_phase(admission, "yellow:generate", "yellow", request_digest("yellow", payload), NOW, identifiers=ids)
    entry = {"client_id": "yellow", **ids, "source_sha256": "1" * 64,
        "content_item_id": ids["request_id"], "content_version_id": str(uuid4()), "banner_sha256": "2" * 64,
        "telegram_copy_sha256": "3" * 64, "x_copy_sha256": "4" * 64}
    manifest = {"contract": "ybo-trial-courier-manifest@1", "trial_id": plan.trial_id,
        "plan_sha256": plan.sha256, "release_sha": p["release_sha"], "not_before": p["not_before"],
        "deadline": p["deadline"], "destination_policy_sha256": "5" * 64, "entries": [entry]}
    store = TrialManifestStore(owner=owner, destination_policy_sha256="5" * 64, clock=lambda: NOW)
    gate = TrialGenerationGate(plan=plan, owner=owner, clock=lambda: NOW)
    return SimpleNamespace(owner=owner, admission=admission, plan=plan, manifest=manifest,
                           store=store, entry=entry, gate=gate, payload=payload, ids=ids)


def ready(trial):
    artifact = {k: v for k, v in trial.entry.items() if k != "job_id"}
    artifact["status"] = "needs_review"
    sha = canonical_sha256(artifact)
    assert trial.owner.finish_phase(trial.admission, "yellow:generate", sha, NOW)
    assert trial.owner.reserve_phase(trial.admission, "yellow:review", "yellow", sha, NOW)
    assert trial.owner.finish_phase(trial.admission, "yellow:review", "6" * 64, NOW)
    assert trial.owner.reserve_phase(trial.admission, "yellow:send", "yellow", sha, NOW)


def read(trial):
    return trial.store.resolve(trial_id=trial.plan.trial_id, plan_sha256=trial.plan.sha256)


def test_manifest_is_not_permission_and_no_http_admission(trial):
    with pytest.raises(TrialManifestDenied):
        trial.store.publish(trial.manifest, trial.admission)
    assert not trial.store.path.exists()
    with pytest.raises(TrialManifestDenied):
        read(trial)


def test_exact_completed_review_immutable_manifest_and_terminal_off(trial):
    ready(trial)
    sha = trial.store.publish(trial.manifest, trial.admission)
    receipt = read(trial)
    assert receipt == {"active": True, "observed_at": NOW.isoformat(),
        "manifest": trial.manifest, "manifest_sha256": sha}
    with pytest.raises(TrialManifestDenied):
        trial.store.publish(trial.manifest, trial.admission)  # no overwrite, even same bytes
    assert trial.owner.terminate(trial.admission, "completed", NOW)
    with pytest.raises(TrialManifestDenied):
        read(trial)


@pytest.mark.parametrize("field", ["client_id", "job_id", "source_item_id", "content_item_id",
    "content_version_id", "banner_sha256", "telegram_copy_sha256", "x_copy_sha256", "source_sha256"])
def test_tampered_manifest_not_owned(trial, field):
    ready(trial)
    changed = deepcopy(trial.manifest)
    changed["entries"][0][field] = ("babylon" if field == "client_id" else
        "9" * 64 if field.endswith("sha256") else str(uuid4()))
    with pytest.raises(TrialManifestDenied):
        trial.store.publish(changed, trial.admission)
    assert not trial.store.path.exists()


def test_expired_and_unbound_policy_fail_closed(trial, tmp_path):
    ready(trial)
    trial.store.publish(trial.manifest, trial.admission)
    denied = TrialManifestStore(owner=trial.owner, destination_policy_sha256="9" * 64, clock=lambda: NOW)
    with pytest.raises(TrialManifestDenied):
        denied.resolve(trial_id=trial.plan.trial_id, plan_sha256=trial.plan.sha256)
    trial.store.clock = lambda: NOW + timedelta(hours=2)
    with pytest.raises(TrialManifestDenied):
        read(trial)
    assert trial.owner.inspect(NOW + timedelta(hours=2))["effective_on"] is False


def api_client(trial, monkeypatch):
    from api import security, server
    monkeypatch.setattr(security, "API_KEYS", {"admin": "fixture-admin-key-long", "yellow": "fixture-client-key-long"})
    for name in ("QUALITY_BANNER_ENABLED", "NEWS_CARD_IDEMPOTENCY_ENABLED", "TELEGRAM_PUBLICATION_ENABLED"):
        monkeypatch.setenv(name, "false")
    monkeypatch.setenv("YBO_TRIAL_DESTINATION_POLICY_SHA256", "5" * 64)
    monkeypatch.setattr(server.TrialGenerationGate, "from_env", lambda env: trial.gate)
    return TestClient(server.app)


def test_authenticated_generation_authorizer_has_no_creation_or_provider_work(trial, monkeypatch):
    client = api_client(trial, monkeypatch)
    request = {"trial_id": trial.plan.trial_id, "plan_sha256": trial.plan.sha256,
        "release_sha": trial.plan.payload["release_sha"], "client_id": "yellow",
        "request_id": trial.ids["request_id"], "payload": trial.payload}
    path = "/internal/trial/generation-authorize"
    for key in ("", "fixture-client-key-long"):
        assert client.post(path, headers={"X-API-Key": key}, json=request).status_code in (401, 403)
    result = client.post(path, headers={"X-API-Key": "fixture-admin-key-long"}, json=request)
    assert result.status_code == 200
    assert result.json() == {k: v for k, v in {**request, "authorized": True, "observed_at": NOW.isoformat()}.items() if k != "payload"}
    changed = deepcopy(request)
    changed["payload"]["source_content"] += " altered"
    assert client.post(path, headers={"X-API-Key": "fixture-admin-key-long"}, json=changed).status_code == 403
    assert trial.owner.inspect(NOW)["phases"] == 2
    assert not trial.store.path.exists()


def test_manifest_endpoint_only_reads_server_owned_bytes(trial, monkeypatch):
    client = api_client(trial, monkeypatch)
    request = {"trial_id": trial.plan.trial_id, "plan_sha256": trial.plan.sha256}
    path = "/internal/trial/manifest"
    headers = {"X-API-Key": "fixture-admin-key-long"}
    assert client.post(path, headers=headers, json=request).status_code == 409
    assert client.post(path, headers=headers, json={**request, "manifest": trial.manifest}).status_code == 422
    ready(trial)
    trial.store.publish(trial.manifest, trial.admission)
    response = client.post(path, headers=headers, json=request)
    assert response.status_code == 200 and response.json()["manifest"] == trial.manifest
    monkeypatch.setenv("QUALITY_BANNER_ENABLED", "true")
    assert client.post(path, headers=headers, json=request).status_code == 503


def test_gateway_receipt_only_closes_exact_reserved_delivery(trial, monkeypatch):
    client = api_client(trial, monkeypatch)
    path = "/internal/trial/delivery-confirm"
    headers = {"X-API-Key": "fixture-admin-key-long"}
    request = {"trial_id": trial.plan.trial_id, "plan_sha256": trial.plan.sha256,
        "manifest_sha256": canonical_sha256(trial.manifest), "content_version_id": trial.entry["content_version_id"],
        "card_id": str(uuid4()), "outbox_id": str(uuid4()), "confirmed_parts": 4, "status": "sent"}
    assert client.post(path, headers=headers, json=request).status_code == 409
    ready(trial)
    trial.store.publish(trial.manifest, trial.admission)
    assert client.post(path, headers={"X-API-Key": "fixture-client-key-long"}, json=request).status_code == 403
    for change in ({"manifest_sha256": "9" * 64}, {"content_version_id": str(uuid4())}):
        assert client.post(path, headers=headers, json={**request, **change}).status_code == 409
        assert trial.owner.inspect(NOW)["effective_on"] is True
    assert client.post(path, headers=headers, json=request).json() == {"recorded": True}
    assert trial.owner.inspect(NOW) == {"status": "completed", "effective_on": False, "phases": 4}
    assert client.post(path, headers=headers, json=request).status_code == 409  # never reopens


def test_trusted_clock_resamples_after_lock_not_stale_request_snapshot(trial):
    clock = [NOW + timedelta(seconds=2)]
    owner = TrialOwner(trial.owner.path, trial.plan, clock=lambda: clock[0])
    assert owner.is_active(trial.admission, NOW + timedelta(seconds=1))
    # Earlier request snapshot reaches the lock later: not a clock rollback.
    clock[0] += timedelta(seconds=1)
    assert owner.is_active(trial.admission, NOW)
    # A request waiting past deadline cannot authorize based on its old time.
    clock[0] = NOW + timedelta(hours=2)
    assert owner.is_active(trial.admission, NOW) is False
    assert owner.inspect(clock[0])["status"] == "expired"


def test_new_admission_waiting_past_deadline_gets_no_authority(trial, tmp_path):
    plan_data = {**trial.plan.payload, "trial_id": str(uuid4())}
    plan = TrialPlan.from_mapping(plan_data, canonical_sha256(plan_data))
    owner = TrialOwner(tmp_path / f"{plan.trial_id}.trial-owner.sqlite3", plan,
                       clock=lambda: NOW + timedelta(hours=2))
    assert owner.admit(plan.trial_id, NOW) is None
    assert owner.inspect(NOW + timedelta(hours=2))["effective_on"] is False


def test_untrusted_manifest_file_paths_denied(trial, tmp_path):
    ready(trial)
    target = tmp_path / "not-an-owned-manifest.json"
    target.write_text(json.dumps(trial.manifest))
    trial.store.path.symlink_to(target)
    with pytest.raises(TrialManifestDenied):
        read(trial)
