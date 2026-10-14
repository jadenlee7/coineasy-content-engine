"""Local-only exact natural producer contract; no hosted/provider traffic."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from core.automation.generation_client import GeneratedCatalogResult, StudioGenerationClient
from core.automation.models import (AutomationState, ClaimedJob, PendingSource,
    QueueResult, StyleReferencePack)
from core.automation.news_generation_journal import request_digest
from core.automation.trial_owner import (CLIENTS, LIMIT_MICROS, NOT_BEFORE,
    POLICY_SHA256, TrialOwner, TrialPlan, canonical_sha256)
from core.automation.trial_producer_adapter import (DAY, ClaimedTrialGeneration,
    NaturalSlot, TrialNaturalProducerAdapter, TrialProducerBlocked, fixed_natural_input_sha256)

NOW = datetime(2026, 10, 14, 13, 14, 51, tzinfo=timezone.utc)
BODY = "Yellow launches a mainnet upgrade for this exact trial source"
IMAGE = "https://pbs.twimg.com/media/fixture.jpg"
CANONICAL_IMAGE = IMAGE + "?name=orig"
SOURCE = "https://x.com/Yellow/status/123456789"


class FakeRepository:
    def __init__(self, slot):
        self.slot = slot
        self.calls = []
        self.completed = []
        self.claim_result = self._job()
        self.context = {"job_id": slot.job_id, "request_id": slot.request_id,
            "workspace_id": slot.workspace_id, "client_id": slot.client_id,
            "source_item_id": slot.source_item_id,
            "source_published_at": (NOW - timedelta(hours=1)).isoformat(),
            "source_sha256": hashlib.sha256(BODY.encode()).hexdigest(),
            "source_content": BODY, "source_url": SOURCE, "source_image_url": IMAGE}

    def _job(self):
        s = self.slot
        return ClaimedJob(s.job_id, s.client_id, DAY, "daily_news", s.request_id,
            s.source_item_id, BODY, SOURCE, IMAGE, False, 1, 3, "trial-worker")

    async def _rpc(self, name, params):
        self.calls.append((name, params))
        if name == "claim_exact_natural_review_draft_job":
            return {"fixture": "claimed"}
        if name == "get_quality_banner_generation_context":
            return deepcopy(self.context)
        raise AssertionError(name)

    def _claimed_job(self, raw, *, worker_id):
        assert raw == {"fixture": "claimed"} and worker_id == "trial-worker"
        return self.claim_result

    async def bind_execution_plane(self, **kwargs):
        self.calls.append(("bind", kwargs))
        return "studio_sync"

    async def complete_job(self, **kwargs):
        self.completed.append(kwargs)


@pytest.fixture
def trial(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    data = {"contract": "ybo-server-trial@1", "trial_id": str(uuid4()),
        "not_before": NOT_BEFORE, "deadline": "2026-10-14T14:44:51Z",
        "kst_date": "2026-10-14", "client_ids": list(CLIENTS),
        "max_per_client": 1, "max_total": 3,
        "estimated_daily_limit_micros": LIMIT_MICROS,
        "estimate_policy_sha256": POLICY_SHA256, "release_sha": "a" * 40}
    plan = TrialPlan.from_mapping(data, canonical_sha256(data))
    owner = TrialOwner(private / f"{plan.trial_id}.trial-owner.sqlite3", plan)
    slot = NaturalSlot(str(uuid4()), "yellow", str(uuid4()), str(uuid4()),
        str(uuid4()), BODY, SOURCE, IMAGE,
        StyleReferencePack("", "", "b" * 32, ()))
    slot = NaturalSlot(slot.workspace_id, slot.client_id, slot.job_id, slot.request_id,
        slot.source_item_id, slot.source_content, slot.source_url, slot.source_image_url,
        StyleReferencePack(slot.request_id, slot.source_item_id, "b" * 32, ()))
    repository = FakeRepository(slot)
    studio = StudioGenerationClient(base_url="https://coineasy-newscard.netlify.app",
        automation_token="synthetic-token-0123456789-0123456789")
    adapter = TrialNaturalProducerAdapter(repository=repository, x_client=object(),
        studio=studio, owner=owner, workspace_id=slot.workspace_id,
        trial_id=plan.trial_id, clock=lambda: NOW)
    return SimpleNamespace(owner=owner, slot=slot, repository=repository,
        studio=studio, adapter=adapter, plan=plan)


def _reserve(trial, prepared):
    admission = trial.owner.admit(trial.plan.trial_id, NOW)
    assert admission is not None
    assert trial.owner.reserve_phase(admission, "yellow:intake", "yellow", "1" * 64,
        NOW, identifiers=trial.slot.identifiers())
    assert trial.owner.finish_phase(admission, "yellow:intake", "2" * 64, NOW)
    assert trial.owner.reserve_phase(admission, "yellow:generate", "yellow",
        prepared.payload_sha256, NOW, identifiers=trial.slot.identifiers())
    return admission


def test_fixed_natural_input_sha_uses_exact_jsonb_shape(trial):
    data = trial.slot.job_input()
    text = "{" + ", ".join(json.dumps(k) + ": " + json.dumps(data[k]) for k in (
        "kst_date", "workflow", "request_id", "source_url", "manual_only",
        "content_kind", "source_content", "source_item_ids", "source_image_url")) + "}"
    assert fixed_natural_input_sha256(data) == hashlib.sha256(text.encode()).hexdigest()
    with pytest.raises(TrialProducerBlocked):
        fixed_natural_input_sha256({**data, "kst_date": "2026-10-13"})
    with pytest.raises(TrialProducerBlocked):
        fixed_natural_input_sha256({**data, "extra": "untrusted"})


@pytest.mark.asyncio
async def test_exact_claim_and_full_canonical_payload(trial):
    prepared = await trial.adapter.claim_and_prepare(trial.slot, worker_id="trial-worker",
        before_effect=lambda: None)
    name, params = trial.repository.calls[0]
    assert name == "claim_exact_natural_review_draft_job"
    assert params == {"target_workspace_id": trial.slot.workspace_id,
        "target_client_id": "yellow", "target_job_id": trial.slot.job_id,
        "target_request_id": trial.slot.request_id,
        "target_source_item_id": trial.slot.source_item_id,
        "target_input_sha256": fixed_natural_input_sha256(trial.slot.job_input()),
        "target_style_pack_hash": "b" * 32, "target_worker_id": "trial-worker",
        "target_lease_seconds": 900}
    assert prepared.payload["source_image_url"] == CANONICAL_IMAGE
    assert prepared.payload["quality_context"]["source_image_url"] == CANONICAL_IMAGE
    assert prepared.payload["brand_review_guidance"] is None
    assert prepared.payload_sha256 == request_digest("yellow", prepared.payload)
    assert trial.repository.calls[1][0] == "bind"
    assert trial.repository.calls[2][0] == "get_quality_banner_generation_context"


@pytest.mark.asyncio
async def test_intake_uses_latest_natural_source_and_new_daily_slot(trial):
    post = {"id": "123456789", "text": BODY, "url": SOURCE,
        "created_at": (NOW - timedelta(hours=1)).isoformat(),
        "source_image_url": IMAGE,
        "media": [{"type": "photo", "url": IMAGE}],
        "is_retweet": False, "is_reply": False, "is_quote": False,
        "metrics": {"like_count": 0}}
    source_id = trial.slot.source_item_id
    observed = []
    class X:
        async def get_recent_tweets(self, handle, **kwargs):
            observed.append(("x", handle, kwargs))
            return [post]
    async def state(**kwargs):
        return AutomationState(None, False, ())
    async def record(**kwargs):
        observed.append(("record", kwargs))
        return AutomationState("123456789", False, (PendingSource(source_id,
            post["id"], BODY, SOURCE, IMAGE, post["created_at"],
            ({"type": "photo", "url": IMAGE},), post["metrics"]),))
    async def pack(**kwargs):
        observed.append(("pack", kwargs))
        return StyleReferencePack(kwargs["request_id"], source_id, "b" * 32, ())
    async def queue(**kwargs):
        observed.append(("queue", kwargs))
        return QueueResult(trial.slot.job_id, "queued", False)
    trial.repository.get_state = state
    trial.repository.record_sources = record
    trial.repository.get_or_create_style_reference_pack = pack
    trial.repository.queue_job = queue
    trial.adapter.x_client = X()
    result = await trial.adapter.intake("yellow", before_effect=lambda: None)
    assert result is not None and result.source_item_id == source_id
    assert [item[0] for item in observed] == ["x", "record", "pack", "queue"]
    assert observed[0][2]["require_complete"] is True
    assert observed[-1][1]["manual_only"] is False
    assert observed[-1][1]["source_item_ids"] == [source_id]


@pytest.mark.asyncio
async def test_intake_reused_slot_is_not_generation_authority(trial):
    async def state(**kwargs):
        return AutomationState(None, True, ())
    trial.repository.get_state = state
    assert await trial.adapter.intake("yellow", before_effect=lambda: None) is None


@pytest.mark.asyncio
async def test_intake_never_falls_back_below_newest_ineligible_post(trial):
    async def state(**kwargs):
        return AutomationState(None, False, ())
    async def forbidden_record(**kwargs):
        raise AssertionError("newer ineligible source must stop intake")
    class X:
        async def get_recent_tweets(self, handle, **kwargs):
            base = {"text": BODY, "created_at": (NOW - timedelta(hours=1)).isoformat(),
                "is_retweet": False, "is_reply": False, "source_image_url": IMAGE,
                "media": [{"type": "photo", "url": IMAGE}]}
            return [{**base, "id": "123456790", "is_quote": True},
                {**base, "id": "123456789", "is_quote": False}]
    trial.repository.get_state = state
    trial.repository.record_sources = forbidden_record
    trial.adapter.x_client = X()
    assert await trial.adapter.intake("origintrail", before_effect=lambda: None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["wrong_date", "claim_denied", "stale_source", "bad_source_sha"])
async def test_claim_rejects_wrong_date_denial_and_untrusted_context(trial, case):
    if case == "wrong_date":
        trial.adapter.clock = lambda: NOW - timedelta(days=1)
    elif case == "claim_denied":
        trial.repository._rpc = lambda *a, **kw: _return_none()
    elif case == "stale_source":
        trial.repository.context["source_published_at"] = (NOW - timedelta(hours=24)).isoformat()
    else:
        trial.repository.context["source_sha256"] = "f" * 64
    with pytest.raises(TrialProducerBlocked):
        await trial.adapter.claim_and_prepare(trial.slot, worker_id="trial-worker",
            before_effect=lambda: None)


async def _return_none():
    return None


@pytest.mark.asyncio
async def test_generation_one_exact_post_then_normal_complete(trial):
    prepared = await trial.adapter.claim_and_prepare(trial.slot, worker_id="trial-worker",
        before_effect=lambda: None)
    _reserve(trial, prepared)
    calls = []
    async def fake_contract(client, *, expected_release_sha):
        assert expected_release_sha == trial.plan.payload["release_sha"]
        calls.append("contract")
        return "a" * 40
    def fake_validate(response, **kwargs):
        assert response.status_code == 200
        assert kwargs["source_image_url"] == CANONICAL_IMAGE
        calls.append("validated")
        return GeneratedCatalogResult(trial.slot.request_id, str(uuid4()),
            (str(uuid4()),), False)
    def handler(request):
        assert request.method == "POST"
        assert request.headers["x-ybo-trial-id"] == trial.plan.trial_id
        assert request.headers["x-ybo-trial-plan-sha256"] == trial.plan.sha256
        assert request.headers["idempotency-key"] == trial.slot.request_id
        body = json.loads(request.content)
        assert body["trial_payload"] == prepared.payload
        assert {k: body[k] for k in body if k != "trial_payload"} == {
            k: prepared.payload[k] for k in ("source_content", "source_type", "source_url",
                "source_image_url", "mock_mode", "template_style", "style_references",
                "style_reference_pack_hash")}
        assert "quality_context" not in body and "brand_review_guidance" not in body
        calls.append("posted")
        return httpx.Response(200, json={})
    trial.studio.transport = httpx.MockTransport(handler)
    trial.studio._require_generation_contract = fake_contract
    trial.studio._validated_generation_result = fake_validate
    result = await trial.adapter.generate_and_complete(prepared, before_effect=lambda: None)
    assert result.content_item_id == trial.slot.request_id
    assert calls == ["contract", "posted", "validated"]
    assert len(trial.repository.completed) == 1


@pytest.mark.asyncio
async def test_release_mismatch_blocks_before_generation_post(trial):
    prepared = await trial.adapter.claim_and_prepare(trial.slot, worker_id="trial-worker",
        before_effect=lambda: None)
    _reserve(trial, prepared)
    async def wrong_contract(client, *, expected_release_sha):
        assert expected_release_sha == trial.plan.payload["release_sha"]
        return "b" * 40
    def forbidden_post(request):
        raise AssertionError("release mismatch must stop before POST")
    trial.studio.transport = httpx.MockTransport(forbidden_post)
    trial.studio._require_generation_contract = wrong_contract
    with pytest.raises(TrialProducerBlocked, match="trial_producer_release_mismatch"):
        await trial.adapter.generate_and_complete(prepared, before_effect=lambda: None)
    assert trial.repository.completed == []


@pytest.mark.asyncio
async def test_exact_pending_ack_allows_only_get_only_continuation(trial):
    prepared = await trial.adapter.claim_and_prepare(trial.slot, worker_id="trial-worker",
        before_effect=lambda: None)
    _reserve(trial, prepared)
    sent, sleeps = [], []
    async def fake_contract(client, *, expected_release_sha):
        assert expected_release_sha == trial.plan.payload["release_sha"]
        return "a" * 40
    async def fake_sleep(seconds):
        sleeps.append(seconds)
    def fake_validate(response, **kwargs):
        assert response.status_code == 200
        return GeneratedCatalogResult(trial.slot.request_id, str(uuid4()), (str(uuid4()),), False)
    def handler(request):
        sent.append((request.headers.get("x-ybo-trial-collect-only"), json.loads(request.content)))
        return (httpx.Response(503, json={"error": "quality_banner_pending"}) if len(sent) == 1
            else httpx.Response(200, json={}))
    trial.studio.transport = httpx.MockTransport(handler)
    trial.studio._require_generation_contract = fake_contract
    trial.studio._validated_generation_result = fake_validate
    trial.adapter.sleep = fake_sleep
    await trial.adapter.generate_and_complete(prepared, before_effect=lambda: None)
    assert [header for header, _ in sent] == [None, "true"]
    assert sent[0][1] == sent[1][1] and sleeps == [15]
    assert len(trial.repository.completed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"error": "quality_banner_collect_unknown"},
    {"error": "quality_banner_pending", "retry": True},
])
async def test_non_exact_pending_response_never_continues(trial, body):
    prepared = await trial.adapter.claim_and_prepare(trial.slot, worker_id="trial-worker",
        before_effect=lambda: None)
    _reserve(trial, prepared)
    sent = []
    async def fake_contract(client, *, expected_release_sha):
        assert expected_release_sha == trial.plan.payload["release_sha"]
        return "a" * 40
    def handler(request):
        sent.append(request)
        return httpx.Response(503, json=body)
    trial.studio.transport = httpx.MockTransport(handler)
    trial.studio._require_generation_contract = fake_contract
    with pytest.raises(TrialProducerBlocked, match="trial_producer_generation_outcome_unknown"):
        await trial.adapter.generate_and_complete(prepared, before_effect=lambda: None)
    assert len(sent) == 1 and trial.repository.completed == []


@pytest.mark.asyncio
async def test_collect_only_is_bounded_to_thirty_calls(trial):
    prepared = await trial.adapter.claim_and_prepare(trial.slot, worker_id="trial-worker",
        before_effect=lambda: None)
    _reserve(trial, prepared)
    sent = []
    async def fake_contract(client, *, expected_release_sha):
        assert expected_release_sha == trial.plan.payload["release_sha"]
        return "a" * 40
    async def no_wait(seconds):
        assert seconds == 15
    def handler(request):
        sent.append(request.headers.get("x-ybo-trial-collect-only"))
        return httpx.Response(503, json={"error": "quality_banner_pending"})
    trial.studio.transport = httpx.MockTransport(handler)
    trial.studio._require_generation_contract = fake_contract
    trial.adapter.sleep = no_wait
    with pytest.raises(TrialProducerBlocked, match="trial_producer_collect_deadline"):
        await trial.adapter.generate_and_complete(prepared, before_effect=lambda: None)
    assert len(sent) == 30 and sent[0] is None and set(sent[1:]) == {"true"}
    assert trial.repository.completed == []


@pytest.mark.asyncio
async def test_terminal_owner_after_pending_stops_collect_post(trial):
    prepared = await trial.adapter.claim_and_prepare(trial.slot, worker_id="trial-worker",
        before_effect=lambda: None)
    admission = _reserve(trial, prepared)
    sent = []
    async def fake_contract(client, *, expected_release_sha):
        assert expected_release_sha == trial.plan.payload["release_sha"]
        return "a" * 40
    def handler(request):
        sent.append(request.headers.get("x-ybo-trial-collect-only"))
        return httpx.Response(503, json={"error": "quality_banner_pending"})
    trial.studio.transport = httpx.MockTransport(handler)
    trial.studio._require_generation_contract = fake_contract
    async def terminate_after_pending(seconds):
        assert trial.owner.terminate(admission, "unknown", NOW)
    trial.adapter.sleep = terminate_after_pending
    with pytest.raises(TrialProducerBlocked, match="trial_producer_owner_not_reserved"):
        await trial.adapter.generate_and_complete(prepared, before_effect=lambda: None)
    assert sent == [None] and trial.repository.completed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_phase", "changed_payload", "terminal", "deadline", "transport_unknown"])
async def test_generation_fails_closed_without_replay(trial, case):
    prepared = await trial.adapter.claim_and_prepare(trial.slot, worker_id="trial-worker",
        before_effect=lambda: None)
    admission = _reserve(trial, prepared) if case != "no_phase" else None
    if case == "changed_payload":
        prepared.payload["source_content"] += " mutation"
    elif case == "terminal":
        assert trial.owner.terminate(admission, "unknown", NOW)
    elif case == "deadline":
        trial.adapter.clock = lambda: NOW + timedelta(hours=2)
    seen = []
    async def fake_contract(client, *, expected_release_sha):
        assert expected_release_sha == trial.plan.payload["release_sha"]
        seen.append("contract")
        return "a" * 40
    def handler(request):
        seen.append("post")
        raise httpx.ConnectError("ambiguous", request=request)
    trial.studio.transport = httpx.MockTransport(handler)
    trial.studio._require_generation_contract = fake_contract
    with pytest.raises(TrialProducerBlocked):
        await trial.adapter.generate_and_complete(prepared, before_effect=lambda: None)
    assert seen == (["contract", "post"] if case == "transport_unknown" else [])
    assert trial.repository.completed == []
