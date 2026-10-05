from __future__ import annotations

import copy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
import httpx

from core.automation.generation_client import GeneratedCatalogResult, GenerationRequestError, StudioGenerationClient
from core.automation.models import ClaimedJob, StyleReferencePack
from core.automation.same_day_refresh import (
    RefreshQueueManifest, RefreshRunManifest, SameDayRefreshRunner,
    SupabaseSameDayRefreshRepository,
)


NOW = datetime(2026, 10, 5, 3, tzinfo=timezone.utc)
WORKSPACE = "11111111-1111-4111-8111-111111111111"
REFRESH = "22222222-2222-4222-8222-222222222222"
REQUEST = "33333333-3333-4333-8333-333333333333"
SOURCE = "44444444-4444-4444-8444-444444444444"
JOB = "55555555-5555-4555-8555-555555555555"
VERSION = "66666666-6666-4666-8666-666666666666"
OLD_JOB = "77777777-7777-4777-8777-777777777777"
OLD_ITEM = "88888888-8888-4888-8888-888888888888"
OLD_VERSION = "99999999-9999-4999-8999-999999999999"
SHA = "a" * 40
WORKER = f"same-day-refresh:{REFRESH}"


def manifest(**changes):
    base = RefreshRunManifest(workspace_id=WORKSPACE, client_id="yellow",
        kst_date=date(2026, 10, 5), refresh_id=REFRESH, request_id=REQUEST,
        source_item_id=SOURCE, release_sha=SHA, expires_at=NOW + timedelta(hours=1), job_id=JOB)
    return replace(base, **changes)


def queue_manifest(**changes):
    values = manifest().safe_identity()
    values["kst_date"], values["expires_at"] = manifest().kst_date, manifest().expires_at
    base = RefreshQueueManifest(**values, predecessor_job_id=OLD_JOB,
        predecessor_content_item_id=OLD_ITEM, predecessor_content_version_id=OLD_VERSION)
    return replace(base, **changes)


def raw_claim(**changes):
    result = {"job_id": JOB, "workspace_id": WORKSPACE, "client_id": "yellow", "status": "running",
        "attempts": 1, "max_attempts": 1, "locked_by": WORKER,
        "lease_expires_at": (NOW + timedelta(minutes=15)).isoformat(),
        "refresh_id": REFRESH, "release_sha": SHA, "execution_plane": "studio_sync",
        "origintrail_batch_eligible": False, "batch_handoff_recovery_only": False,
        "input": {"workflow": "official_x_review_draft_v1", "kst_date": "2026-10-05",
            "source_item_ids": [SOURCE], "content_kind": "daily_news", "request_id": REQUEST,
            "source_content": "Fresh official infrastructure update, not the stale predecessor.",
            "source_url": "https://x.com/Yellow/status/123456789", "source_image_url": "",
            "manual_only": True, "same_day_refresh_id": REFRESH}}
    result.update(changes)
    return result


def inspection(**changes):
    value = {"status": "refresh_ready", "refresh_id": REFRESH, "workspace_id": WORKSPACE,
        "client_id": "yellow", "kst_date": "2026-10-05", "job_id": JOB, "request_id": REQUEST,
        "source_item_id": SOURCE, "content_item_id": REQUEST, "content_version_id": VERSION,
        "banner_asset_id": OLD_ITEM, "banner_sha256": "b" * 64,
        "source_published_at": (NOW - timedelta(hours=7)).isoformat(),
        "source_age_seconds": 7 * 3600, "release_sha": SHA, "execution_plane": "studio_sync",
        "execution_authorized": False, "delivery_authorized": False}
    value.update(changes)
    return value


class FacadeFake(SupabaseSameDayRefreshRepository):
    def __init__(self, response):
        self.response, self.calls = response, []

    async def _rpc(self, name, payload):
        self.calls.append((name, payload))
        if isinstance(self.response, Exception):
            raise self.response
        return copy.deepcopy(self.response)


@pytest.mark.asyncio
async def test_exact_queue_has_new_ids_and_no_source_body_in_rpc_or_receipt():
    m = queue_manifest()
    raw = {key: m.safe_identity()[key] for key in
           ("refresh_id", "request_id", "source_item_id", "client_id", "kst_date", "release_sha", "expires_at")}
    raw.update(job_id=JOB, status="queued", reused=False)
    repo = FacadeFake(raw)
    assert await repo.queue_refresh(m, now=NOW) == raw
    name, payload = repo.calls[0]
    assert name == "queue_official_x_same_day_refresh"
    assert payload["target_predecessor_content_item_id"] == OLD_ITEM != REQUEST
    assert payload["target_source_item_id"] == SOURCE
    assert set(payload) == {"target_workspace_id", "target_client_id", "target_kst_date",
        "target_predecessor_job_id", "target_predecessor_content_item_id", "target_predecessor_content_version_id",
        "target_source_item_id", "target_refresh_id", "target_request_id", "target_release_sha", "target_expires_at"}


@pytest.mark.asyncio
async def test_claim_exact_refresh_is_sync_manual_one_attempt_and_never_normal_bind():
    repo = FacadeFake(raw_claim())
    result = await repo.claim_refresh(manifest(), worker_id=WORKER, now=NOW)
    assert result.manual_only is True and result.attempts == result.max_attempts == 1
    assert not result.origintrail_batch_eligible
    assert repo.calls[0][0] == "claim_official_x_same_day_refresh"
    assert repo.calls[0][1] == {"target_workspace_id": WORKSPACE, "target_refresh_id": REFRESH,
        "target_job_id": JOB, "target_request_id": REQUEST, "target_source_item_id": SOURCE,
        "target_release_sha": SHA, "target_worker_id": WORKER, "target_lease_seconds": 900}


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"release_sha": "b" * 40}, {"workspace_id": OLD_ITEM}, {"job_id": OLD_JOB},
    {"refresh_id": OLD_VERSION}, {"execution_plane": "openai_batch"}, {"attempts": True},
    {"max_attempts": 3}, {"origintrail_batch_eligible": True}, {"batch_handoff_recovery_only": True},
    {"locked_by": "another-worker"}, {"lease_expires_at": NOW.isoformat()},
    {"unexpected_provider_body": "must never escape"},
])
async def test_claim_rejects_tampered_scalar_receipt(changes):
    repo = FacadeFake(raw_claim(**changes))
    with pytest.raises((ValueError, RuntimeError)):
        await repo.claim_refresh(manifest(), worker_id=WORKER, now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"manual_only": False}, {"same_day_refresh_id": OLD_VERSION}, {"workflow": "another_workflow"},
    {"request_id": OLD_ITEM}, {"source_item_ids": [OLD_ITEM]}, {"kst_date": "2026-10-04"},
    {"content_kind": "article"}, {"source_url": "https://x.com/SquidRouter/status/123456789"},
])
async def test_claim_rejects_input_binding_tamper(changes):
    raw = raw_claim()
    raw["input"].update(changes)
    with pytest.raises(ValueError):
        await FacadeFake(raw).claim_refresh(manifest(), worker_id=WORKER, now=NOW)


@pytest.mark.asyncio
async def test_private_readiness_is_fresh_seven_hour_source_not_stale_32_hour_predecessor():
    raw = inspection()
    receipt = await FacadeFake(raw).inspect_refresh(workspace_id=WORKSPACE,
        refresh_id=REFRESH, content_version_id=VERSION)
    assert receipt["source_age_seconds"] == 7 * 3600
    assert 32 * 3600 > 86400  # Stale predecessor age is not the refreshed source's age.
    assert receipt["content_item_id"] == REQUEST != OLD_ITEM
    assert receipt["execution_authorized"] is receipt["delivery_authorized"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"delivery_authorized": True}, {"execution_authorized": True}, {"content_version_id": OLD_VERSION},
    {"content_item_id": OLD_ITEM}, {"source_age_seconds": 86400}, {"source_age_seconds": float("nan")},
    {"source_age_seconds": True}, {"banner_sha256": "b" * 63}, {"banner_asset_id": None},
    {"draft_body": "forbidden"},
])
async def test_readiness_rejects_authority_hash_age_and_bound_version_tamper(changes):
    with pytest.raises(ValueError):
        await FacadeFake(inspection(**changes)).inspect_refresh(workspace_id=WORKSPACE,
            refresh_id=REFRESH, content_version_id=VERSION)


@pytest.mark.parametrize("changes", [
    {"release_sha": "short"}, {"refresh_id": "not-uuid"}, {"request_id": SOURCE},
    {"kst_date": date(2026, 10, 4)}, {"expires_at": NOW},
    {"expires_at": NOW + timedelta(hours=3)}, {"expires_at": NOW.replace(tzinfo=None)},
])
def test_manifest_rejects_invalid_identity_day_expiry(changes):
    with pytest.raises(ValueError):
        manifest(**changes).validate(NOW)


class RunnerRepo:
    def __init__(self, *, claim_error=None, complete_error=None, pack_error=None, fail_error=None):
        self.calls = []
        self.claim_error, self.complete_error = claim_error, complete_error
        self.pack_error, self.fail_error = pack_error, fail_error
        self.claimed = SupabaseSameDayRefreshRepository._claimed_job(
            FacadeFake(None), raw_claim(), worker_id=WORKER)

    async def claim_refresh(self, m, **kwargs):
        self.calls.append(("claim", kwargs))
        if self.claim_error:
            raise self.claim_error
        return self.claimed

    async def get_or_create_style_reference_pack(self, **kwargs):
        self.calls.append(("style", kwargs))
        if self.pack_error:
            raise self.pack_error
        return StyleReferencePack(REQUEST, SOURCE, "c" * 32, ())

    async def complete_job(self, **kwargs):
        self.calls.append(("complete", kwargs))
        if self.complete_error:
            raise self.complete_error

    async def fail_job(self, **kwargs):
        self.calls.append(("fail", kwargs))
        if self.fail_error:
            raise self.fail_error


class GenerationFake:
    def __init__(self, *, release_error=None, generation_error=None, result=None):
        self.calls, self.release_error, self.generation_error = [], release_error, generation_error
        self.result = result or GeneratedCatalogResult(REQUEST, VERSION, (OLD_ITEM,), False)

    async def require_release(self, sha):
        self.calls.append(("release", sha))
        if self.release_error:
            raise self.release_error

    async def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        kwargs["before_generation_post"]()
        if self.generation_error:
            raise self.generation_error
        return self.result


@pytest.mark.asyncio
async def test_one_exact_success_reuses_source_style_generation_and_completion_only():
    repo, gen = RunnerRepo(), GenerationFake()
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: NOW)
    result = await runner.generate_once(manifest())
    assert result["status"] == "needs_review" and result["content_version_id"] == VERSION
    assert [call[0] for call in repo.calls] == ["claim", "style", "complete"]
    assert [call[0] for call in gen.calls] == ["release", "generate"]
    args = gen.calls[1][1]
    assert args["content_kind"] == "daily_news" and args["request_id"] == REQUEST
    assert args["expected_studio_release_sha"] == SHA
    assert args["style_reference_pack_hash"] == "c" * 32
    assert args["template_style"] == "classic"
    assert result["private_send_attempted"] is result["public_send_attempted"] is False
    assert (await runner.generate_once(manifest()))["status"] == "already_attempted"
    assert len(gen.calls) == 2


@pytest.mark.asyncio
async def test_release_failure_makes_zero_claims_and_zero_generation():
    repo, gen = RunnerRepo(), GenerationFake(release_error=RuntimeError("private response"))
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: NOW)
    assert (await runner.generate_once(manifest()))["status"] == "release_blocked"
    assert repo.calls == [] and [c[0] for c in gen.calls] == ["release"]


@pytest.mark.asyncio
async def test_claim_lost_ack_is_consumed_and_never_reclaimed_or_generated():
    repo, gen = RunnerRepo(claim_error=TimeoutError("secret")), GenerationFake()
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: NOW)
    receipt = await runner.generate_once(manifest())
    assert receipt["status"] == "claim_unknown" and receipt["generation_attempted"] is False
    assert (await runner.generate_once(manifest()))["status"] == "already_attempted"
    assert [c[0] for c in repo.calls] == ["claim"] and len(gen.calls) == 1


@pytest.mark.asyncio
async def test_generation_timeout_one_attempt_marks_terminal_not_retrying():
    repo = RunnerRepo()
    gen = GenerationFake(generation_error=GenerationRequestError("studio_generation_unavailable", retryable=True))
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: NOW)
    result = await runner.generate_once(manifest())
    assert result["status"] == "generation_unknown" and result["generation_attempted"] is True
    assert repo.calls[-1][0] == "fail"
    assert repo.calls[-1][1]["retryable"] is False and repo.calls[-1][1]["retry_at"] is None
    assert (await runner.generate_once(manifest()))["status"] == "already_attempted"
    assert len([c for c in gen.calls if c[0] == "generate"]) == 1


@pytest.mark.asyncio
async def test_completion_unknown_leaves_lease_and_never_regenerates_or_fails():
    repo, gen = RunnerRepo(complete_error=TimeoutError("provider body")), GenerationFake()
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: NOW)
    result = await runner.generate_once(manifest())
    assert result["status"] == "completion_ack_unknown" and result["content_version_id"] == VERSION
    assert [c[0] for c in repo.calls] == ["claim", "style", "complete"]
    assert (await runner.generate_once(manifest()))["status"] == "already_attempted"
    assert len(gen.calls) == 2


@pytest.mark.asyncio
async def test_malformed_claim_from_injected_facade_cannot_generate():
    repo, gen = RunnerRepo(), GenerationFake()
    repo.claimed = replace(repo.claimed, manual_only=False)
    result = await SameDayRefreshRunner(repository=repo, generation=gen,
        now_factory=lambda: NOW).generate_once(manifest())
    assert result["status"] == "claim_unknown"
    assert [c[0] for c in repo.calls] == ["claim"] and len(gen.calls) == 1


@pytest.mark.asyncio
async def test_bad_generation_item_never_completes_or_retries():
    repo = RunnerRepo()
    gen = GenerationFake(result=GeneratedCatalogResult(OLD_ITEM, VERSION, (), False))
    result = await SameDayRefreshRunner(repository=repo, generation=gen,
        now_factory=lambda: NOW).generate_once(manifest())
    assert result["status"] == "generation_unknown"
    assert [c[0] for c in repo.calls] == ["claim", "style", "fail"]


@pytest.mark.asyncio
async def test_midnight_after_release_does_not_claim_stale_day():
    ticks = iter([NOW, datetime(2026, 10, 5, 15, tzinfo=timezone.utc)])
    repo, gen = RunnerRepo(), GenerationFake()
    result = await SameDayRefreshRunner(repository=repo, generation=gen,
        now_factory=lambda: next(ticks)).generate_once(manifest())
    assert result["status"] == "claim_unknown" and repo.calls == []


@pytest.mark.asyncio
async def test_style_failure_never_generates_and_failure_ack_unknown_never_retries():
    repo = RunnerRepo(pack_error=ValueError("source"), fail_error=TimeoutError("private"))
    gen = GenerationFake()
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: NOW)
    assert (await runner.generate_once(manifest()))["status"] == "failure_ack_unknown"
    assert len(gen.calls) == 1
    assert (await runner.generate_once(manifest()))["status"] == "already_attempted"


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed,expires_delta", [(900, 3600), (301, 300)])
async def test_style_pack_delay_exhausting_lease_or_expiry_never_starts_provider(elapsed, expires_delta):
    ticks = iter([NOW, NOW, NOW + timedelta(seconds=elapsed)])
    repo, gen = RunnerRepo(), GenerationFake()
    result = await SameDayRefreshRunner(repository=repo, generation=gen,
        now_factory=lambda: next(ticks)).generate_once(
            manifest(expires_at=NOW + timedelta(seconds=expires_delta)))
    assert result["status"] == "preparation_expired" and not result["generation_attempted"]
    assert [c[0] for c in repo.calls] == ["claim", "style", "fail"]
    assert [c[0] for c in gen.calls] == ["release"]


@pytest.mark.asyncio
async def test_style_pack_crossing_kst_midnight_consumes_claim_without_provider():
    start = datetime(2026, 10, 5, 14, 55, tzinfo=timezone.utc)
    ticks = iter([start, start, start + timedelta(minutes=5)])
    repo, gen = RunnerRepo(), GenerationFake()
    result = await SameDayRefreshRunner(repository=repo, generation=gen,
        now_factory=lambda: next(ticks)).generate_once(manifest(expires_at=start + timedelta(minutes=10)))
    assert result["status"] == "preparation_expired" and len(gen.calls) == 1
    assert repo.calls[-1][0] == "fail"


@pytest.mark.asyncio
@pytest.mark.parametrize("assets", [(), (OLD_ITEM, OLD_VERSION), ("bad-id",), [OLD_ITEM]])
async def test_generation_result_requires_one_canonical_asset_uuid(assets):
    repo = RunnerRepo()
    gen = GenerationFake(result=GeneratedCatalogResult(REQUEST, VERSION, assets, False))
    result = await SameDayRefreshRunner(repository=repo, generation=gen,
        now_factory=lambda: NOW).generate_once(manifest())
    assert result["status"] == "generation_unknown" and repo.calls[-1][0] == "fail"
    assert len([c for c in gen.calls if c[0] == "generate"]) == 1


@pytest.mark.asyncio
async def test_exact_source_image_reference_is_allowlisted_and_canonical_before_generation():
    repo, gen = RunnerRepo(), GenerationFake()
    repo.claimed = replace(repo.claimed, source_image_url="https://pbs.twimg.com/media/official.jpg")
    result = await SameDayRefreshRunner(repository=repo, generation=gen,
        now_factory=lambda: NOW).generate_once(manifest())
    assert result["ok"]
    args = gen.calls[-1][1]
    assert args["source_image_url"] == "https://pbs.twimg.com/media/official.jpg?name=orig"
    assert args["template_style"] == "remix"
    assert repo.claimed.source_image_url == "https://pbs.twimg.com/media/official.jpg"


@pytest.mark.asyncio
@pytest.mark.parametrize("image", ["https://arbitrary.test/banner.png", "http://pbs.twimg.com/media/a.png",
    "https://pbs.twimg.com/media/a.png?token=secret", "https://user:pass@pbs.twimg.com/media/a.png"])
async def test_disallowed_image_reference_blocks_before_style_or_provider(image):
    repo, gen = RunnerRepo(), GenerationFake()
    repo.claimed = replace(repo.claimed, source_image_url=image)
    result = await SameDayRefreshRunner(repository=repo, generation=gen,
        now_factory=lambda: NOW).generate_once(manifest())
    assert result["status"] == "claim_unknown"
    assert [c[0] for c in repo.calls] == ["claim"] and len(gen.calls) == 1


@pytest.mark.asyncio
async def test_raw_claim_lease_cannot_outlive_explicit_manifest_expiry():
    with pytest.raises(ValueError):
        await FacadeFake(raw_claim()).claim_refresh(
            manifest(expires_at=NOW + timedelta(minutes=5)), worker_id=WORKER, now=NOW)


@pytest.mark.asyncio
async def test_exact_claim_already_consumed_does_not_generate():
    repo, gen = RunnerRepo(), GenerationFake()
    repo.claimed = None
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: NOW)
    assert (await runner.generate_once(manifest()))["status"] == "already_consumed"
    assert [c[0] for c in repo.calls] == ["claim"] and len(gen.calls) == 1


@pytest.mark.asyncio
async def test_queue_lost_ack_has_no_retry_or_generation():
    class QueueLostAck:
        def __init__(self):
            self.calls = 0

        async def queue_refresh(self, m, *, now):
            self.calls += 1
            raise TimeoutError("sensitive raw response")

    repo, gen = QueueLostAck(), GenerationFake()
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: NOW)
    receipt = await runner.queue_once(queue_manifest())
    assert receipt["status"] == "queue_unknown" and "sensitive" not in str(receipt)
    assert (await runner.queue_once(queue_manifest()))["status"] == "already_attempted"
    assert repo.calls == 1 and gen.calls == []


@pytest.mark.asyncio
async def test_real_generation_client_mock_transport_timeout_is_one_daily_post_without_retry():
    requests = []

    def transport(request):
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            assert request.url.path == "/api/studio-capabilities"
            return httpx.Response(200, json={"schema_version": "1.0",
                "generation_contract": "double-fact-check@1",
                "generated_content_kinds": ["daily_news", "article", "tutorial"],
                "tutorial_claims_contract": "lessons@1", "netlify_release_sha": SHA})
        assert request.url.path == "/api/news-card/yellow"
        assert request.headers["idempotency-key"] == REQUEST
        assert request.headers["x-studio-expected-release-sha"] == SHA
        raise httpx.ReadTimeout("synthetic acknowledgement lost", request=request)

    repo = RunnerRepo()
    gen = StudioGenerationClient(base_url="https://coineasy-newscard.netlify.app",
        automation_token="synthetic-only-token-" + "a" * 32,
        transport=httpx.MockTransport(transport))
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: NOW)
    assert (await runner.generate_once(manifest()))["status"] == "generation_unknown"
    assert (await runner.generate_once(manifest()))["status"] == "already_attempted"
    assert requests == [("GET", "/api/studio-capabilities"),
                        ("GET", "/api/studio-capabilities"), ("POST", "/api/news-card/yellow")]
    assert repo.calls[-1][1]["retryable"] is False


@pytest.mark.asyncio
async def test_real_repository_mock_transport_queue_unknown_ack_is_one_rpc_without_retry():
    requests = []

    def transport(request):
        requests.append((request.method, request.url.path))
        assert request.url.path == "/rest/v1/rpc/queue_official_x_same_day_refresh"
        raise httpx.ReadTimeout("synthetic lost acknowledgement", request=request)

    repo = SupabaseSameDayRefreshRepository(supabase_url="https://synthetic.supabase.co",
        service_role_key="synthetic-key-" + "x" * 32, transport=httpx.MockTransport(transport))
    runner = SameDayRefreshRunner(repository=repo, now_factory=lambda: NOW)
    assert (await runner.queue_once(queue_manifest()))["status"] == "queue_unknown"
    assert (await runner.queue_once(queue_manifest()))["status"] == "already_attempted"
    assert requests == [("POST", "/rest/v1/rpc/queue_official_x_same_day_refresh")]


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["expiry", "kst_midnight", "lease"])
async def test_second_release_get_crossing_action_boundary_never_makes_paid_post(boundary):
    start = NOW if boundary != "kst_midnight" else datetime(2026, 10, 5, 14, 59, 59, tzinfo=timezone.utc)
    expires = start + timedelta(seconds=1 if boundary == "expiry" else 3600)
    clock = {"now": start}
    requests = []

    def transport(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET", "an expired action window must make zero generation POSTs"
        if len(requests) == 2:
            clock["now"] += timedelta(seconds=901 if boundary == "lease" else 2)
        return httpx.Response(200, json={"schema_version": "1.0",
            "generation_contract": "double-fact-check@1",
            "generated_content_kinds": ["daily_news", "article", "tutorial"],
            "tutorial_claims_contract": "lessons@1", "netlify_release_sha": SHA})

    repo = RunnerRepo()
    gen = StudioGenerationClient(base_url="https://coineasy-newscard.netlify.app",
        automation_token="synthetic-only-token-" + "a" * 32,
        transport=httpx.MockTransport(transport))
    runner = SameDayRefreshRunner(repository=repo, generation=gen, now_factory=lambda: clock["now"])
    receipt = await runner.generate_once(manifest(expires_at=expires))
    assert receipt["status"] == "preparation_expired" and receipt["generation_attempted"] is False
    assert [c[0] for c in repo.calls] == ["claim", "style", "fail"]
    assert repo.calls[-1][1]["retryable"] is False
    # Once consumed, no new release/claim/POST is attempted even if caller time
    # is reset for a same-manifest replay test.
    clock["now"] = start
    assert (await runner.generate_once(manifest(expires_at=expires)))["status"] == "already_attempted"
    assert requests == [("GET", "/api/studio-capabilities"), ("GET", "/api/studio-capabilities")]
