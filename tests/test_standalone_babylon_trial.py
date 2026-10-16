"""One readiness-anchored Babylon original, with no provider or hosted I/O."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.automation.models import AutomationState, ClaimedJob, PendingSource, QueueResult, StyleReferencePack
from core.automation.news_generation_journal import request_digest
from core.automation.trial_generation_gate import TrialGenerationDenied, TrialGenerationGate
from core.automation.trial_manifest import TrialManifestDenied, validate_manifest
from core.automation.trial_owner import LIMIT_MICROS, POLICY_SHA256, TrialError, TrialOwner, TrialPlan, canonical_sha256
from core.automation.trial_producer_adapter import TrialNaturalProducerAdapter, TrialProducerBlocked, fixed_natural_input_sha256
from core.content_ops.trial_courier_adapter import TrialCourierError, TrialManifest, canonical_sha256 as courier_sha
from core.content_ops.trial_private_review_runtime import TrialCourierSettings, TrialRuntimeError
from core.content_ops.trial_window import (FRESH_TRIAL_ID, NEW_BABYLON_TRIAL_ID, TRIAL_ID, KST,
    STANDALONE_BABYLON_EARLIEST, STANDALONE_BABYLON_TRIAL_ID,
    trial_anchor_allowed, trial_identity_policy)

START = datetime(2026, 10, 16, 19, 46, tzinfo=timezone.utc)
NOW = START + timedelta(minutes=1)
POST_ID = "2111094309701210167"
BODY = "Babylon official launch fixture. " + "Full original long-form source. " * 30
IMAGE = "https://pbs.twimg.com/media/fixture.png"


def plan(start=START, **changes):
    payload = {"contract": "ybo-server-trial@1", "trial_id": STANDALONE_BABYLON_TRIAL_ID,
        "not_before": start.isoformat().replace("+00:00", "Z"),
        "deadline": (start + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        "kst_date": start.astimezone(KST).date().isoformat(), "client_ids": ["babylon"],
        "max_per_client": 1, "max_total": 1, "estimated_daily_limit_micros": LIMIT_MICROS,
        "estimate_policy_sha256": POLICY_SHA256, "release_sha": "a" * 40, **changes}
    return TrialPlan.from_mapping(payload, canonical_sha256(payload))


def post(post_id=POST_ID, now=NOW, **changes):
    return {"id": post_id, "text": BODY,
        "url": "https://x.com/babylonlabs_io/status/" + post_id,
        "created_at": (now - timedelta(minutes=5)).isoformat(),
        "source_image_url": IMAGE, "media": [{"type": "photo", "url": IMAGE}],
        "is_retweet": False, "is_reply": False, "is_quote": False,
        "is_note_tweet": True, "metrics": {}, **changes}


def test_exact_identity_dynamic_readiness_day_and_legacy_policies():
    policy = trial_identity_policy(STANDALONE_BABYLON_TRIAL_ID)
    assert policy.client_ids == ("babylon",) and policy.max_per_client == policy.max_total == 1
    assert policy.natural and policy.latest_standalone and policy.kst_date is None
    assert policy.earliest == STANDALONE_BABYLON_EARLIEST
    assert not trial_anchor_allowed(STANDALONE_BABYLON_TRIAL_ID, policy.earliest - timedelta(seconds=1))
    assert trial_anchor_allowed(STANDALONE_BABYLON_TRIAL_ID, policy.earliest)
    assert plan().kst_date.isoformat() == "2026-10-17"
    assert plan(start=START + timedelta(days=1)).kst_date.isoformat() == "2026-10-18"
    for legacy in (TRIAL_ID, FRESH_TRIAL_ID, NEW_BABYLON_TRIAL_ID):
        assert not trial_identity_policy(legacy).latest_standalone
    for legacy in (FRESH_TRIAL_ID, NEW_BABYLON_TRIAL_ID):
        assert trial_identity_policy(legacy).kst_date.isoformat() == "2026-10-16"
        with pytest.raises(TrialError):
            plan(trial_id=legacy)
    for invalid in (str(uuid4()), STANDALONE_BABYLON_TRIAL_ID.upper(), None):
        with pytest.raises(ValueError, match="trial_id_invalid"):
            trial_identity_policy(invalid)


@pytest.mark.parametrize("changes", [
    {"client_ids": ["origintrail"]}, {"client_ids": ["babylon", "yellow"]},
    {"max_total": 2}, {"max_per_client": 2},
    {"estimated_daily_limit_micros": LIMIT_MICROS + 1},
    {"kst_date": "2026-10-16"}, {"deadline": "2026-10-16T21:46:01Z"},
    {"not_before": "2026-10-16T19:45:02Z", "deadline": "2026-10-16T21:45:02Z"},
    {"not_before": "2026-10-17T14:00:00Z", "deadline": "2026-10-17T16:00:00Z"},
])
def test_scope_earliest_exact_window_and_kst_boundary_fail_closed(changes):
    with pytest.raises(TrialError):
        plan(**changes)


def test_owner_readiness_anchor_cannot_replay_or_reanchor(tmp_path):
    candidate = plan()
    path = tmp_path / f"{candidate.trial_id}.trial-owner.sqlite3"
    owner = TrialOwner(path, candidate)
    assert owner.admit(candidate.trial_id, START - timedelta(seconds=1)) is None
    assert not path.exists()
    assert owner.admit(candidate.trial_id, NOW) is not None
    assert owner.admit(candidate.trial_id, NOW) is None
    assert TrialOwner(path, candidate).admit(candidate.trial_id, NOW) is None
    changed = plan(start=START + timedelta(days=1))
    with pytest.raises(TrialError, match="trial_owner_plan_mismatch"):
        TrialOwner(path, changed).admit(changed.trial_id, changed.not_before)
    assert owner.admit(candidate.trial_id, candidate.deadline) is None
    assert owner.inspect(candidate.deadline)["status"] == "expired"


def test_generation_accepts_only_exact_durable_phase_and_release(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    candidate = plan()
    owner = TrialOwner(private / f"{candidate.trial_id}.trial-owner.sqlite3", candidate)
    admission = owner.admit(candidate.trial_id, NOW)
    identifiers = {key: str(uuid4()) for key in ("job_id", "request_id", "source_item_id")}
    payload = {"source_content": BODY, "quality_context": {"client_id": "babylon", **identifiers}}
    assert owner.reserve_phase(admission, "babylon:intake", "babylon", "1" * 64, NOW)
    assert owner.finish_phase(admission, "babylon:intake", "2" * 64, NOW)
    assert owner.reserve_phase(admission, "babylon:generate", "babylon",
        request_digest("babylon", payload), NOW, identifiers=identifiers)
    env = {"YBO_TRIAL_API_MODE": "true", "YBO_TRIAL_PLAN_JSON": json.dumps(candidate.payload),
        "YBO_TRIAL_PLAN_SHA256": candidate.sha256, "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT": str(private)}
    gate = TrialGenerationGate.from_env(env, clock=lambda: NOW, stamp_reader=lambda: "a" * 40)
    args = dict(trial_id=candidate.trial_id, client_id="babylon",
        request_id=identifiers["request_id"], payload=payload)
    assert gate.require_generation(**args) is None
    for changed in ({"trial_id": NEW_BABYLON_TRIAL_ID}, {"client_id": "yellow"},
                    {"payload": {**payload, "source_content": "changed"}}):
        with pytest.raises(TrialGenerationDenied):
            gate.require_generation(**{**args, **changed})
    for changed in ({"YBO_PRESERVED_BABYLON_RECEIPT_ID": str(uuid4())},
                    {"RAILWAY_GIT_COMMIT_SHA": "b" * 40}):
        with pytest.raises(TrialGenerationDenied):
            TrialGenerationGate.from_env({**env, **changed}, clock=lambda: NOW, stamp_reader=lambda: "a" * 40)
    gate.clock = lambda: candidate.deadline
    gate.owner.clock = gate.clock
    with pytest.raises(TrialGenerationDenied):
        gate.require_generation(**args)


def test_exact_manifest_and_courier_runtime_accept_readiness_day():
    candidate = plan(start=START + timedelta(days=1))
    entry = {"client_id": "babylon", **{key: str(uuid4()) for key in
        ("job_id", "request_id", "source_item_id", "content_version_id")},
        "source_sha256": "a" * 64, "banner_sha256": "b" * 64,
        "telegram_copy_sha256": "c" * 64, "x_copy_sha256": "d" * 64}
    entry["content_item_id"] = entry["request_id"]
    payload = {"contract": "ybo-trial-courier-manifest@1", "trial_id": candidate.trial_id,
        "plan_sha256": candidate.sha256, "release_sha": "a" * 40,
        "not_before": candidate.payload["not_before"], "deadline": candidate.payload["deadline"],
        "destination_policy_sha256": "e" * 64, "entries": [entry]}
    assert validate_manifest(payload, plan=candidate, destination_policy_sha256="e" * 64) == payload
    assert TrialManifest.from_mapping(payload, courier_sha(payload)).payload == payload
    for changed in ({"trial_id": NEW_BABYLON_TRIAL_ID},
                    {"entries": [{**entry, "client_id": "origintrail"}]},
                    {"entries": [entry, entry]}):
        invalid = {**payload, **changed}
        with pytest.raises(TrialManifestDenied):
            validate_manifest(invalid, plan=candidate, destination_policy_sha256="e" * 64)
        with pytest.raises(TrialCourierError):
            TrialManifest.from_mapping(invalid, courier_sha(invalid))
    from test_trial_private_review_runtime import env
    from test_content_ops_daily_private_review_runtime import SHA
    configured = env(CONTENT_OPS_TRIAL_ID=candidate.trial_id,
        CONTENT_OPS_TRIAL_NOT_BEFORE=payload["not_before"], CONTENT_OPS_TRIAL_DEADLINE=payload["deadline"])
    assert TrialCourierSettings.from_env(configured, stamp_reader=lambda: SHA).trial_id == candidate.trial_id
    with pytest.raises(TrialRuntimeError):
        TrialCourierSettings.from_env({**configured, "CONTENT_OPS_TRIAL_ID": NEW_BABYLON_TRIAL_ID},
            stamp_reader=lambda: SHA)


def adapter_fixture(tmp_path, posts, *, candidate=None, now=NOW):
    candidate = candidate or plan()
    owner = TrialOwner(tmp_path / f"{candidate.trial_id}.trial-owner.sqlite3", candidate)
    workspace_id, job_id = str(uuid4()), str(uuid4())
    calls, recorded, queued = [], [], []
    sources = {}
    holder = SimpleNamespace(slot=None)
    class Repository:
        async def get_state(self, **kwargs):
            calls.append("state")
            assert kwargs["kst_date"] == candidate.kst_date
            return AutomationState(None, False, ())
        async def record_trial_official_sources(self, **kwargs):
            calls.append("record")
            recorded.extend(kwargs["source_items"])
            pending = []
            for value in recorded:
                source_id = sources.setdefault(value["external_id"], str(uuid4()))
                pending.append(PendingSource(source_id, value["external_id"], value["source_content"],
                    value["source_url"], value["source_image_url"], value["published_at"],
                    tuple(value["media"]), value["metrics"], value["is_note_tweet"]))
            return AutomationState(kwargs["next_cursor"], False, tuple(pending))
        async def get_or_create_style_reference_pack(self, **kwargs):
            calls.append("style")
            return StyleReferencePack(kwargs["request_id"], kwargs["primary_source_item_id"], "b" * 32, ())
        async def queue_job(self, **kwargs):
            calls.append("queue")
            queued.append(kwargs)
            return QueueResult(job_id, "queued", False)
        async def _rpc(self, name, params):
            calls.append(name)
            slot = holder.slot
            if name == "claim_exact_natural_review_draft_job":
                assert "target_receipt_id" not in params
                assert params["target_worker_id"] == f"ybo-trial:{candidate.trial_id}"
                assert params["target_input_sha256"] == fixed_natural_input_sha256(slot.job_input(),
                    kst_date=candidate.kst_date)
                return {"fixture": "claimed"}
            assert name == "get_quality_banner_generation_context"
            original = next(value for value in recorded if sources[value["external_id"]] == slot.source_item_id)
            return {"job_id": job_id, "request_id": slot.request_id, "workspace_id": workspace_id,
                "client_id": "babylon", "source_item_id": slot.source_item_id,
                "source_published_at": original["published_at"],
                "source_sha256": hashlib.sha256(slot.source_content.encode()).hexdigest(),
                "source_content": slot.source_content, "source_url": slot.source_url,
                "source_image_url": slot.source_image_url}
        def _claimed_job(self, raw, *, worker_id):
            slot = holder.slot
            return ClaimedJob(job_id, "babylon", candidate.kst_date, "daily_news", slot.request_id,
                slot.source_item_id, slot.source_content, slot.source_url, slot.source_image_url,
                False, 1, 3, worker_id)
        async def bind_execution_plane(self, **kwargs):
            calls.append("bind")
            return "studio_sync"
    class X:
        async def get_recent_tweets(self, handle, **kwargs):
            calls.append("x")
            assert kwargs == {"hours": 24, "max_results": 100, "since_id": None, "require_complete": True}
            return posts
    adapter = TrialNaturalProducerAdapter(repository=Repository(), x_client=X(), studio=object(),
        owner=owner, workspace_id=workspace_id, trial_id=candidate.trial_id, clock=lambda: now)
    return SimpleNamespace(adapter=adapter, calls=calls, recorded=recorded, queued=queued, holder=holder)


@pytest.mark.asyncio
async def test_newer_reply_and_retweet_allow_full_note_original_and_exact_claim(tmp_path):
    original = post()
    fixture = adapter_fixture(tmp_path, [post(str(int(POST_ID) + 2), is_reply=True), original,
        post(str(int(POST_ID) + 1), is_retweet=True)])
    for excluded in ("yellow", "origintrail"):
        assert await fixture.adapter.intake(excluded, before_effect=lambda: None) is None
    assert fixture.calls == []
    slot = await fixture.adapter.intake("babylon", before_effect=lambda: None)
    fixture.holder.slot = slot
    assert slot is not None and slot.recovery_receipt_id is None
    assert slot.source_content == BODY.strip() and len(slot.source_content) > 280
    assert [value["external_id"] for value in fixture.recorded] == [POST_ID]
    assert fixture.recorded[0]["source_content"] == BODY and fixture.recorded[0]["is_note_tweet"] is True
    assert fixture.queued[0]["kst_date"] == plan().kst_date
    prepared = await fixture.adapter.claim_and_prepare(slot,
        worker_id=f"ybo-trial:{STANDALONE_BABYLON_TRIAL_ID}", before_effect=lambda: None)
    assert prepared.payload["source_content"] == BODY.strip()
    assert prepared.payload["quality_context"]["source_sha256"] == hashlib.sha256(BODY.strip().encode()).hexdigest()
    assert fixture.calls[-3:] == ["claim_exact_natural_review_draft_job", "bind", "get_quality_banner_generation_context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("posts", [[], [post(is_reply=True)], [post(is_retweet=True)],
    [post(is_reply=True), post(str(int(POST_ID) + 1), is_retweet=True)]])
async def test_no_eligible_original_has_no_persistence_or_queue(tmp_path, posts):
    fixture = adapter_fixture(tmp_path, posts)
    assert await fixture.adapter.intake("babylon", before_effect=lambda: None) is None
    assert fixture.calls == ["state", "x"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"is_reply": "false"}, {"is_retweet": 0},
    {"is_quote": None}, {"is_note_tweet": 1}, {"id": "bad"}, {"id": "0"}])
async def test_ambiguous_flags_or_ids_fail_before_persistence(tmp_path, changes):
    fixture = adapter_fixture(tmp_path, [post(**changes)])
    with pytest.raises(TrialProducerBlocked, match="trial_producer_source_evidence_invalid"):
        await fixture.adapter.intake("babylon", before_effect=lambda: None)
    assert fixture.calls == ["state", "x"]


@pytest.mark.asyncio
async def test_duplicate_source_identity_is_not_a_valid_timeline(tmp_path):
    fixture = adapter_fixture(tmp_path, [post(), post()])
    with pytest.raises(TrialProducerBlocked, match="trial_producer_source_evidence_invalid"):
        await fixture.adapter.intake("babylon", before_effect=lambda: None)
    assert fixture.calls == ["state", "x"]


@pytest.mark.asyncio
async def test_newest_quote_blocks_without_persisting_or_promoting_older_original(tmp_path):
    fixture = adapter_fixture(tmp_path, [post(), post(str(int(POST_ID) + 1), is_quote=True)])
    assert await fixture.adapter.intake("babylon", before_effect=lambda: None) is None
    assert fixture.calls == ["state", "x"]


@pytest.mark.asyncio
@pytest.mark.parametrize("newest", [
    {"text": "gm"}, {"text": "Babylon official launch fixture forbidden-term"},
    {"created_at": (NOW - timedelta(hours=25)).isoformat()},
])
async def test_newest_unsuitable_original_never_falls_back_to_older(tmp_path, monkeypatch, newest):
    from core.automation import trial_producer_adapter as module
    config = SimpleNamespace(active=True, content_sources=SimpleNamespace(
        twitter=SimpleNamespace(handle="babylonlabs_io")),
        routing=SimpleNamespace(skip_patterns=("forbidden-term",)))
    monkeypatch.setattr(module, "load_client_config", lambda _: config)
    fixture = adapter_fixture(tmp_path, [post(), post(str(int(POST_ID) + 1), **newest)])
    assert await fixture.adapter.intake("babylon", before_effect=lambda: None) is None
    assert fixture.calls == ["state", "x", "record"] and not fixture.queued


@pytest.mark.asyncio
async def test_newest_unusable_media_is_blocked_not_replaced_by_older(tmp_path):
    fixture = adapter_fixture(tmp_path, [post(), post(str(int(POST_ID) + 1),
        source_image_url="", media=[{"type": "video", "url": "https://video.twimg.com/fixture.mp4"}])])
    with pytest.raises(TrialProducerBlocked, match="trial_producer_official_image_required"):
        await fixture.adapter.intake("babylon", before_effect=lambda: None)
    assert not fixture.queued


@pytest.mark.asyncio
@pytest.mark.parametrize("trial_id", [FRESH_TRIAL_ID, NEW_BABYLON_TRIAL_ID])
async def test_old_natural_trials_keep_reply_latest_block(tmp_path, trial_id):
    old_start = START - timedelta(days=1)
    old_now = old_start + timedelta(minutes=1)
    candidate = plan(start=old_start, trial_id=trial_id)
    fixture = adapter_fixture(tmp_path, [post(now=old_now),
        post(str(int(POST_ID) + 1), now=old_now, is_reply=True)], candidate=candidate, now=old_now)
    assert await fixture.adapter.intake("babylon", before_effect=lambda: None) is None
    assert fixture.calls == ["state", "x"]
