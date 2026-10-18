"""Reusable explicit contract, exact durable ownership, no hosted effects."""
from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.automation.news_generation_journal import request_digest
from core.automation.models import AutomationState
from core.automation.trial_controller import HostedTrialController
from core.automation.trial_generation_gate import TrialGenerationDenied, TrialGenerationGate
from core.automation.trial_owner import NATURAL_ONCE_CONTRACT, TrialError, TrialOwner, TrialPlan, canonical_sha256
from core.automation.trial_producer_adapter import TrialProducerBlocked, trial_worker_id
from core.content_ops.trial_window import (TRIAL_ID, FRESH_TRIAL_ID, NEW_BABYLON_TRIAL_ID,
    STANDALONE_BABYLON_TRIAL_ID, trial_identity_policy)
from test_standalone_babylon_trial import NOW, POST_ID, BODY, plan as old_plan, post, adapter_fixture


def plan(**changes):
    return old_plan(contract=NATURAL_ONCE_CONTRACT, trial_id=str(uuid4()), **changes)


def test_new_explicit_contract_does_not_widen_legacy_identity_registry():
    candidate = plan()
    assert candidate.natural_once and candidate.eligible_clients == ("babylon",)
    assert candidate.identity_policy.natural and candidate.identity_policy.latest_standalone
    with pytest.raises(ValueError):
        trial_identity_policy(candidate.trial_id)
    with pytest.raises(TrialError):
        old_plan(trial_id=candidate.trial_id)
    with pytest.raises(TrialError):
        old_plan(contract="unknown@1", trial_id=candidate.trial_id)
    for historical in (TRIAL_ID, FRESH_TRIAL_ID, NEW_BABYLON_TRIAL_ID,
            STANDALONE_BABYLON_TRIAL_ID, "1a2bef47-4827-4bc3-9b4d-5baf2714015d"):
        with pytest.raises(TrialError):
            old_plan(contract=NATURAL_ONCE_CONTRACT, trial_id=historical)


@pytest.mark.parametrize("changes", [
    {"client_ids": ["yellow"]}, {"client_ids": ["babylon", "origintrail"]},
    {"max_per_client": True}, {"max_total": 2}, {"estimated_daily_limit_micros": 10_000_001},
    {"estimate_policy_sha256": "f" * 64}, {"release_sha": "main"},
    {"deadline": "2026-10-16T21:46:01Z"}, {"kst_date": "2026-10-16"},
    {"not_before": "2026-10-17T14:00:00Z", "deadline": "2026-10-17T16:00:00Z"},
])
def test_new_contract_fixed_scope_and_window(changes):
    with pytest.raises(TrialError):
        plan(**changes)


def test_plan_immutable_and_completed_owner_cannot_reopen(tmp_path):
    candidate = plan()
    owner = TrialOwner(tmp_path / f"{candidate.trial_id}.trial-owner.sqlite3", candidate)
    admission = owner.admit(candidate.trial_id, NOW)
    assert admission is not None
    assert owner.terminate(admission, "completed", NOW)
    original = owner.path.read_bytes()
    assert TrialOwner(owner.path, candidate).admit(candidate.trial_id, NOW) is None
    changed = {**candidate.payload, "not_before": "2026-10-17T19:46:00Z",
        "deadline": "2026-10-17T21:46:00Z", "kst_date": "2026-10-18"}
    altered = TrialPlan.from_mapping(changed, canonical_sha256(changed))
    with pytest.raises(TrialError, match="trial_owner_plan_mismatch"):
        TrialOwner(owner.path, altered).admit(altered.trial_id, altered.not_before)
    assert owner.path.read_bytes() == original


def test_canonical_configuration_exact_release_and_durable_generation(tmp_path):
    tmp_path.chmod(0o700)
    candidate = plan()
    owner = TrialOwner(tmp_path / f"{candidate.trial_id}.trial-owner.sqlite3", candidate)
    ids = {key: str(uuid4()) for key in ("job_id", "request_id", "source_item_id")}
    payload = {"source_content": BODY, "quality_context": {"client_id": "babylon", **ids}}
    env = {"YBO_TRIAL_API_MODE": "true", "YBO_TRIAL_PLAN_JSON": candidate.payload_json,
        "YBO_TRIAL_PLAN_SHA256": candidate.sha256, "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "QUALITY_BANNER_RELEASE_SHA": "a" * 40, "NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT": str(tmp_path)}
    gate = TrialGenerationGate.from_env(env, clock=lambda: NOW, stamp_reader=lambda: "a" * 40)
    args = {"trial_id": candidate.trial_id, "client_id": "babylon", "request_id": ids["request_id"], "payload": payload}
    with pytest.raises(TrialGenerationDenied):
        gate.require_generation(**args)
    assert not owner.path.exists()
    admission = owner.admit(candidate.trial_id, NOW)
    assert owner.reserve_phase(admission, "babylon:intake", "babylon", "1" * 64, NOW)
    assert owner.finish_phase(admission, "babylon:intake", "2" * 64, NOW)
    assert owner.reserve_phase(admission, "babylon:generate", "babylon", request_digest("babylon", payload), NOW, identifiers=ids)
    assert gate.require_generation(**args) is None
    for changes in ({"payload": {**payload, "source_content": "changed"}},
                    {"request_id": str(uuid4())}, {"client_id": "yellow"}):
        with pytest.raises(TrialGenerationDenied):
            gate.require_generation(**{**args, **changes})
    for changes in ({"YBO_TRIAL_PLAN_JSON": json.dumps(candidate.payload)},
                    {"YBO_TRIAL_PLAN_JSON": candidate.payload_json[:-1] + ',"max_total":1}'},
                    {"YBO_TRIAL_PLAN_SHA256": "b" * 64},
                    {"QUALITY_BANNER_RELEASE_SHA": "b" * 40},
                    {"YBO_PRESERVED_BABYLON_RECEIPT_ID": str(uuid4())}):
        with pytest.raises(TrialGenerationDenied):
            TrialGenerationGate.from_env({**env, **changes}, clock=lambda: NOW, stamp_reader=lambda: "a" * 40)
    assert owner.terminate(admission, "completed", NOW)
    with pytest.raises(TrialGenerationDenied):
        gate.require_generation(**args)


@pytest.mark.asyncio
async def test_exact_worker_uses_request_not_trial_or_historical_identity(tmp_path):
    candidate = plan()
    fixture = adapter_fixture(tmp_path, [post()], candidate=candidate)
    slot = await fixture.adapter.intake("babylon", before_effect=lambda: None)
    fixture.holder.slot = slot
    worker = trial_worker_id(candidate, slot.request_id)
    assert worker == "babylon-natural-once:" + slot.request_id
    before = list(fixture.calls)
    for wrong in ("ybo-trial:" + candidate.trial_id, "babylon-natural-once:" + candidate.trial_id):
        with pytest.raises(TrialProducerBlocked):
            await fixture.adapter.claim_and_prepare(slot, worker_id=wrong, before_effect=lambda: None)
    assert fixture.calls == before
    original_rpc = fixture.adapter.repository._rpc
    async def rpc(name, params):
        if name == "claim_exact_natural_review_draft_job":
            assert params["target_worker_id"] == worker
            assert params["target_request_id"] == slot.request_id
            fixture.calls.append(name)
            return {"fixture": "claimed"}
        return await original_rpc(name, params)
    fixture.adapter.repository._rpc = rpc
    prepared = await fixture.adapter.claim_and_prepare(slot, worker_id=worker, before_effect=lambda: None)
    assert prepared.worker_id == worker and prepared.payload["source_content"] == BODY.strip()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["₿    ₿    ₿\n   \\₍•̀ᴗ•́₎/\n        │\n    ╱ ╲",
    "@babylonlabs_io https://example.com/announcement", "Launch soon", None])
async def test_insufficient_latest_text_never_queues_or_falls_back(tmp_path, text):
    fixture = adapter_fixture(tmp_path, [post(), post(str(int(POST_ID) + 1), text=text)], candidate=plan())
    assert await fixture.adapter.intake("babylon", before_effect=lambda: None) is None
    assert fixture.calls == ["state", "x"] and not fixture.queued and not fixture.recorded


@pytest.mark.asyncio
async def test_newer_cached_cursor_absent_from_fresh_timeline_stops_before_record(tmp_path):
    fixture = adapter_fixture(tmp_path, [post()], candidate=plan())
    async def state(**kwargs):
        fixture.calls.append("state")
        return AutomationState(str(int(POST_ID) + 1), False, ())
    fixture.adapter.repository.get_state = state
    assert await fixture.adapter.intake("babylon", before_effect=lambda: None) is None
    assert fixture.calls == ["state", "x"] and not fixture.queued


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"source_content": BODY + " Unobserved claim."}, {"source_url": "https://x.com/other/status/123"},
    {"source_image_url": "https://pbs.twimg.com/media/other.png"},
    {"published_at": "2026-10-16T19:40:00Z"}, {"is_note_tweet": False},
])
async def test_persisted_source_must_match_freshly_observed_latest_body(tmp_path, changes):
    fixture = adapter_fixture(tmp_path, [post()], candidate=plan())
    original = fixture.adapter.repository.record_trial_official_sources
    async def record(**kwargs):
        state = await original(**kwargs)
        return replace(state, pending_sources=(replace(state.pending_sources[0], **changes),))
    fixture.adapter.repository.record_trial_official_sources = record
    assert await fixture.adapter.intake("babylon", before_effect=lambda: None) is None
    assert fixture.calls == ["state", "x", "record"] and not fixture.queued


@pytest.mark.asyncio
async def test_new_contract_generation_closes_without_old_manifest_or_send(tmp_path):
    tmp_path.chmod(0o700)
    candidate = plan()
    owner = TrialOwner(tmp_path / f"{candidate.trial_id}.trial-owner.sqlite3", candidate, clock=lambda: NOW)
    gate = TrialGenerationGate(plan=candidate, owner=owner, clock=lambda: NOW)
    ids = {key: str(uuid4()) for key in ("job_id", "request_id", "source_item_id")}
    calls = []
    class Producer:
        async def intake(self, client, *, before_effect):
            before_effect(); calls.append("intake")
            assert client == "babylon"
            return SimpleNamespace(identifiers=lambda: ids)
        async def claim_and_prepare(self, slot, *, worker_id, before_effect):
            before_effect(); calls.append("claim")
            assert worker_id == "babylon-natural-once:" + ids["request_id"]
            return SimpleNamespace(payload_sha256="1" * 64)
        async def generate_and_complete(self, prepared, *, before_effect):
            before_effect(); calls.append("generate")
            return object()
    class Catalog:
        async def verify(self, prepared, generated):
            calls.append("catalog")
            return {"client_id": "babylon", **ids, "content_item_id": ids["request_id"],
                "content_version_id": str(uuid4()), "source_sha256": "1" * 64, "banner_sha256": "2" * 64,
                "telegram_copy_sha256": "3" * 64, "x_copy_sha256": "4" * 64}
    async def ready(): return None
    async def forbidden_sleep(_): raise AssertionError("No courier wait allowed")
    controller = HostedTrialController(gate=gate, producer=Producer(), catalog=Catalog(),
        preflight=ready, readiness=ready, destination_policy_sha256="5" * 64, sleep=forbidden_sleep)
    result = await controller.run()
    assert result["status"] == "ready_for_private_review" and result["effective_on"] is False
    assert result["generated_clients"] == ["babylon"] and result["public_send_attempted"] is False
    assert calls == ["intake", "claim", "generate", "catalog"]
    assert not controller.store.path.exists()
    assert owner.inspect(NOW)["status"] == "completed" and owner.inspect(NOW)["phases"] == 3
    assert (await controller.run())["status"] == "not_admitted"
    assert calls == ["intake", "claim", "generate", "catalog"]
