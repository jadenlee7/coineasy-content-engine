"""Fresh Babylon one-shot stays separate from the preserved-job recovery."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

import pytest

from core.automation.models import AutomationState, ClaimedJob, PendingSource, QueueResult, StyleReferencePack
from core.automation.trial_controller import HostedTrialController
from core.automation.trial_generation_gate import TrialGenerationDenied, TrialGenerationGate
from core.automation.trial_manifest import TrialManifestDenied, validate_manifest
from core.automation.trial_owner import LIMIT_MICROS, POLICY_SHA256, TrialError, TrialOwner, TrialPlan, canonical_sha256
from core.automation.trial_producer_adapter import TrialNaturalProducerAdapter, TrialProducerBlocked, fixed_natural_input_sha256
from core.content_ops.trial_courier_adapter import TrialCourierError, TrialManifest, canonical_sha256 as courier_sha
from core.content_ops.trial_private_review_runtime import TrialCourierSettings, TrialRuntimeError
from core.content_ops.trial_window import FRESH_TRIAL_ID, TRIAL_ID

START = datetime(2026, 10, 15, 15, 10, tzinfo=timezone.utc)
NOW = START + timedelta(minutes=1)
POST_ID = "2110800000000000000"
BODY = "Babylon official launch fixture"
SOURCE_URL = "https://x.com/babylonlabs_io/status/" + POST_ID


def fresh_plan(**changes):
    payload = {"contract": "ybo-server-trial@1", "trial_id": FRESH_TRIAL_ID,
        "not_before": "2026-10-15T15:10:00Z", "deadline": "2026-10-15T17:10:00Z",
        "kst_date": "2026-10-16", "client_ids": ["babylon"], "max_per_client": 1,
        "max_total": 1, "estimated_daily_limit_micros": LIMIT_MICROS,
        "estimate_policy_sha256": POLICY_SHA256, "release_sha": "a" * 40, **changes}
    return TrialPlan.from_mapping(payload, canonical_sha256(payload))


@pytest.mark.parametrize("changes", [
    {"trial_id": str(uuid4())}, {"client_ids": ["babylon", "origintrail"]},
    {"client_ids": ["origintrail"]}, {"max_total": 2},
    {"not_before": "2026-10-14T15:10:00Z", "deadline": "2026-10-14T17:10:00Z",
     "kst_date": "2026-10-15"},
    {"not_before": "2026-10-16T15:10:00Z", "deadline": "2026-10-16T17:10:00Z",
     "kst_date": "2026-10-17"},
])
def test_fresh_plan_cannot_widen_identity_clients_quota_or_day(changes):
    with pytest.raises(TrialError):
        fresh_plan(**changes)


def test_fresh_owner_refuses_foreign_client_before_phase_or_generation(tmp_path):
    plan = fresh_plan()
    owner = TrialOwner(tmp_path / f"{plan.trial_id}.trial-owner.sqlite3", plan)
    admission = owner.admit(plan.trial_id, NOW)
    assert admission is not None
    for client in ("yellow", "origintrail"):
        with pytest.raises(TrialError, match="trial_phase_invalid"):
            owner.reserve_phase(admission, f"{client}:intake", client, "a" * 64, NOW)
        assert not owner.generation_authorized(client, {k: str(uuid4()) for k in
            ("job_id", "request_id", "source_item_id")}, "a" * 64, NOW)
    assert owner.inspect(NOW)["phases"] == 0


def test_fresh_generation_gate_needs_no_recovery_receipt_but_rejects_one(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    plan = fresh_plan()
    env = {"YBO_TRIAL_API_MODE": "true", "YBO_TRIAL_PLAN_JSON": json.dumps(plan.payload),
        "YBO_TRIAL_PLAN_SHA256": plan.sha256, "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT": str(private)}
    gate = TrialGenerationGate.from_env(env, clock=lambda: NOW, stamp_reader=lambda: "a" * 40)
    assert gate.plan.trial_id == FRESH_TRIAL_ID
    with pytest.raises(TrialGenerationDenied):
        gate.require_generation(trial_id=FRESH_TRIAL_ID, client_id="origintrail",
            request_id=str(uuid4()), payload={})
    with pytest.raises(TrialGenerationDenied):
        TrialGenerationGate.from_env({**env, "YBO_PRESERVED_BABYLON_RECEIPT_ID": str(uuid4())},
            clock=lambda: NOW, stamp_reader=lambda: "a" * 40)
    legacy = {**plan.payload, "trial_id": TRIAL_ID, "client_ids": ["babylon", "origintrail"],
        "max_total": 2}
    legacy_plan = TrialPlan.from_mapping(legacy, canonical_sha256(legacy))
    with pytest.raises(TrialGenerationDenied):
        TrialGenerationGate.from_env({**env, "YBO_TRIAL_PLAN_JSON": json.dumps(legacy_plan.payload),
            "YBO_TRIAL_PLAN_SHA256": legacy_plan.sha256}, stamp_reader=lambda: "a" * 40)


def test_fresh_manifest_cannot_contain_second_or_foreign_client():
    plan = fresh_plan()
    def entry(client):
        return {"client_id": client, **{k: str(uuid4()) for k in
            ("job_id", "request_id", "source_item_id", "content_version_id")},
            "source_sha256": "a" * 64, "banner_sha256": "b" * 64,
            "telegram_copy_sha256": "c" * 64, "x_copy_sha256": "d" * 64}
    one = entry("babylon")
    one["content_item_id"] = one["request_id"]
    payload = {"contract": "ybo-trial-courier-manifest@1", "trial_id": plan.trial_id,
        "plan_sha256": plan.sha256, "release_sha": "a" * 40,
        "not_before": plan.payload["not_before"], "deadline": plan.payload["deadline"],
        "destination_policy_sha256": "e" * 64, "entries": [one]}
    assert validate_manifest(payload, plan=plan, destination_policy_sha256="e" * 64) == payload
    assert TrialManifest.from_mapping(payload, courier_sha(payload)).payload == payload
    for invalid in ([one, {**one, "client_id": "origintrail"}],
                    [{**one, "client_id": "origintrail"}], [{**one, "client_id": "yellow"}]):
        changed = {**payload, "entries": invalid}
        with pytest.raises(TrialManifestDenied):
            validate_manifest(changed, plan=plan, destination_policy_sha256="e" * 64)
        with pytest.raises(TrialCourierError):
            TrialManifest.from_mapping(changed, courier_sha(changed))


def test_fresh_courier_settings_accept_only_new_day():
    from test_trial_private_review_runtime import env
    from test_content_ops_daily_private_review_runtime import SHA
    configured = env(CONTENT_OPS_TRIAL_ID=FRESH_TRIAL_ID,
        CONTENT_OPS_TRIAL_NOT_BEFORE="2026-10-15T15:10:00Z",
        CONTENT_OPS_TRIAL_DEADLINE="2026-10-15T17:10:00Z")
    settings = TrialCourierSettings.from_env(configured, stamp_reader=lambda: SHA)
    assert settings.trial_id == FRESH_TRIAL_ID
    old_day = {**configured, "CONTENT_OPS_TRIAL_NOT_BEFORE": "2026-10-14T15:10:00Z",
        "CONTENT_OPS_TRIAL_DEADLINE": "2026-10-14T17:10:00Z"}
    with pytest.raises(TrialRuntimeError):
        TrialCourierSettings.from_env(old_day, stamp_reader=lambda: SHA)


@pytest.mark.asyncio
async def test_fresh_controller_skips_other_clients_before_preflight_or_intake(tmp_path):
    plan = fresh_plan()
    owner = TrialOwner(tmp_path / f"{plan.trial_id}.trial-owner.sqlite3", plan, clock=lambda: NOW)
    gate = TrialGenerationGate(plan=plan, owner=owner, clock=lambda: NOW)
    calls = []
    class Producer:
        async def intake(self, client_id, *, before_effect):
            before_effect()
            calls.append(("intake", client_id))
            return None
    async def preflight():
        calls.append(("preflight", "babylon"))
    async def readiness():
        return None
    controller = HostedTrialController(gate=gate, producer=Producer(), catalog=object(),
        preflight=preflight, readiness=readiness, destination_policy_sha256="e" * 64)
    result = await controller.run()
    assert result["status"] == "completed_no_new_source"
    assert result["skipped_clients"] == ["yellow", "babylon", "origintrail"]
    assert calls == [("preflight", "babylon"), ("intake", "babylon")]
    assert owner.inspect(NOW)["status"] == "completed"


@pytest.mark.asyncio
async def test_fresh_babylon_intake_and_exact_natural_claim_without_receipt(tmp_path):
    plan = fresh_plan()
    owner = TrialOwner(tmp_path / f"{plan.trial_id}.trial-owner.sqlite3", plan)
    workspace_id, source_id, job_id = (str(uuid4()) for _ in range(3))
    published = (NOW - timedelta(minutes=5)).isoformat()
    post = {"id": POST_ID, "text": BODY, "url": SOURCE_URL, "created_at": published,
        "source_image_url": "", "media": [], "is_retweet": False, "is_reply": False,
        "is_quote": False, "is_note_tweet": False, "metrics": {}}
    pending = PendingSource(source_id, POST_ID, BODY, SOURCE_URL, "", published, ())
    calls = []
    class Repository:
        async def get_state(self, **kwargs):
            calls.append("state")
            return AutomationState(None, False, ())
        async def record_trial_official_sources(self, **kwargs):
            calls.append("record")
            return AutomationState(POST_ID, False, (pending,))
        async def get_or_create_style_reference_pack(self, **kwargs):
            calls.append("style")
            return StyleReferencePack(kwargs["request_id"], source_id, "b" * 32, ())
        async def queue_job(self, **kwargs):
            calls.append("queue")
            assert kwargs["kst_date"] == plan.kst_date and kwargs["source_image_url"] == ""
            return QueueResult(job_id, "queued", False)
        async def _rpc(self, name, params):
            calls.append(name)
            if name == "claim_exact_natural_review_draft_job":
                assert "target_receipt_id" not in params
                assert params["target_worker_id"] == f"ybo-trial:{FRESH_TRIAL_ID}"
                assert params["target_input_sha256"] == fixed_natural_input_sha256(slot.job_input(),
                    kst_date=plan.kst_date)
                return {"fixture": "claimed"}
            assert name == "get_quality_banner_generation_context"
            return {"job_id": job_id, "request_id": slot.request_id,
                "workspace_id": workspace_id, "client_id": "babylon", "source_item_id": source_id,
                "source_published_at": published,
                "source_sha256": hashlib.sha256(BODY.encode()).hexdigest(),
                "source_content": BODY, "source_url": SOURCE_URL, "source_image_url": ""}
        def _claimed_job(self, raw, *, worker_id):
            assert raw == {"fixture": "claimed"}
            return ClaimedJob(job_id, "babylon", plan.kst_date, "daily_news", slot.request_id,
                source_id, BODY, SOURCE_URL, "", False, 1, 3, worker_id)
        async def bind_execution_plane(self, **kwargs):
            calls.append("bind")
            return "studio_sync"
    class X:
        async def get_recent_tweets(self, handle, **kwargs):
            calls.append("x")
            assert kwargs["require_complete"] is True and kwargs["since_id"] is None
            return [post]
    repository = Repository()
    adapter = TrialNaturalProducerAdapter(repository=repository, x_client=X(), studio=object(),
        owner=owner, workspace_id=workspace_id, trial_id=FRESH_TRIAL_ID, clock=lambda: NOW)
    for excluded in ("yellow", "origintrail"):
        assert await adapter.intake(excluded, before_effect=lambda: None) is None
    assert calls == []
    slot = await adapter.intake("babylon", before_effect=lambda: None)
    assert slot is not None and slot.recovery_receipt_id is None
    assert calls == ["state", "x", "record", "style", "queue"]
    prepared = await adapter.claim_and_prepare(slot, worker_id=f"ybo-trial:{FRESH_TRIAL_ID}",
        before_effect=lambda: None)
    assert prepared.payload["source_image_url"] == ""
    assert calls[-3:] == ["claim_exact_natural_review_draft_job", "bind",
        "get_quality_banner_generation_context"]
    with pytest.raises(TrialProducerBlocked):
        await adapter.claim_and_prepare(replace(slot, recovery_receipt_id=str(uuid4())),
            worker_id=f"ybo-trial:{FRESH_TRIAL_ID}", before_effect=lambda: None)
    assert calls[-1] == "get_quality_banner_generation_context"


@pytest.mark.asyncio
async def test_fresh_empty_official_timeline_cannot_promote_pending_source(tmp_path):
    plan = fresh_plan()
    owner = TrialOwner(tmp_path / f"{plan.trial_id}.trial-owner.sqlite3", plan)
    class Repository:
        async def get_state(self, **kwargs):
            return AutomationState(POST_ID, False, ())
        async def record_trial_official_sources(self, **kwargs):
            raise AssertionError("no live latest evidence")
    class X:
        async def get_recent_tweets(self, *_args, **kwargs):
            assert kwargs["since_id"] is None and kwargs["require_complete"] is True
            return []
    adapter = TrialNaturalProducerAdapter(repository=Repository(), x_client=X(), studio=object(),
        owner=owner, workspace_id=str(uuid4()), trial_id=FRESH_TRIAL_ID, clock=lambda: NOW)
    assert await adapter.intake("babylon", before_effect=lambda: None) is None
