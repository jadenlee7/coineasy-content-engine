from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json

import httpx
import pytest

from core.automation.content_signals import (
    ContentSignalsSnapshot,
    DemandTerm,
    LearningGap,
    PromotionCandidate,
)
from core.automation.repository import (
    AutomationRepositoryError,
    SupabaseAutomationRepository,
)


WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
JOB_ID = "33333333-3333-4333-8333-333333333333"
REQUEST_ID = "44444444-4444-4444-8444-444444444444"
RECOVERY_ID = "66666666-6666-4666-8666-666666666666"
APPROVAL_ID = "77777777-7777-4777-8777-777777777777"
RELEASE_SHA = "a" * 40
SERVICE_KEY = "service-role-key-that-is-longer-than-thirty-two-characters"
PROMOTION_URL = "https://x.com/squidkorea/status/123456789"
PROMOTION_CANDIDATE_ID = hashlib.sha256(
    (
        "content-performance-v1\n"
        f"squid\nx\n{PROMOTION_URL}"
    ).encode("utf-8")
).hexdigest()


def _repo(handler):
    return SupabaseAutomationRepository(
        supabase_url="https://project-ref.supabase.co",
        service_role_key=SERVICE_KEY,
        transport=httpx.MockTransport(handler),
    )


def _signals_snapshot(*, with_candidate: bool = False):
    now = datetime(2026, 7, 27, 6, 0, tzinfo=timezone.utc)
    return ContentSignalsSnapshot(
        schema_version="1.2",
        client_id="squid",
        generated_at=now,
        window_start=now - timedelta(days=7),
        window_end=now,
        demand_terms=(
            DemandTerm(
                term="bridge",
                weight=0.9,
                sources=("community", "telegram_content"),
            ),
        ),
        promotion_policy_version="content-performance-v1",
        promotion_candidates=(
            PromotionCandidate(
                candidate_id=PROMOTION_CANDIDATE_ID,
                channel="x",
                source_url=PROMOTION_URL,
                published_at=now - timedelta(hours=18),
                score=0.875,
                reach_percentile=0.9,
                interaction_percentile=0.8,
                community_match_count=3,
                cohort_size=12,
                observation_age_hours=18,
                recommended_formats=("article", "tutorial"),
                reason_codes=(
                    "high_reach",
                    "high_interaction",
                    "community_alignment",
                    "tutorial_learning_signal",
                ),
            ),
        ) if with_candidate else (),
        tutorial_priority=0.48,
        learning_gaps=(
            LearningGap(
                category="squid_advanced",
                attempts=48,
                participants=12,
                accuracy_pct=52,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_state_rpc_is_service_authenticated_and_returns_safe_pending_sources():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/rpc/get_official_x_automation_state"
        assert request.headers["apikey"] == SERVICE_KEY
        assert request.headers["authorization"] == f"Bearer {SERVICE_KEY}"
        return httpx.Response(200, json={
            "last_cursor": "123",
            "draft_reserved_today": False,
            "pending_sources": [{
                "source_item_id": SOURCE_ID,
                "external_id": "456",
                "source_content": "A complete official product update.",
                "source_url": "https://x.com/Yellow/status/456",
                "source_image_url": "https://pbs.twimg.com/media/official.jpg",
                "published_at": "2026-07-22T08:00:00Z",
                "media": [],
                "metrics": {"like_count": 12},
                "is_note_tweet": True,
            }],
        })

    state = await _repo(handler).get_state(
        workspace_id=WORKSPACE_ID,
        client_id="yellow",
        kst_date=date(2026, 7, 22),
    )

    assert state.last_cursor == "123"
    assert state.draft_reserved_today is False
    assert state.pending_sources[0].post_id == "456"
    assert state.pending_sources[0].is_note_tweet is True


@pytest.mark.asyncio
async def test_repository_rejects_unallowlisted_pending_media():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "last_cursor": None,
            "draft_reserved_today": False,
            "pending_sources": [{
                "source_item_id": SOURCE_ID,
                "external_id": "456",
                "source_content": "A complete official product update.",
                "source_url": "https://x.com/Yellow/status/456",
                "source_image_url": "https://example.com/untrusted.jpg",
                "published_at": "2026-07-22T08:00:00Z",
                "media": [],
                "metrics": {},
            }],
        })

    with pytest.raises(AutomationRepositoryError, match="invalid_pending_source"):
        await _repo(handler).get_state(
            workspace_id=WORKSPACE_ID,
            client_id="yellow",
            kst_date=date(2026, 7, 22),
        )


@pytest.mark.asyncio
async def test_claim_requires_the_exact_worker_lease_owner():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "client_id": "yellow",
            "attempts": 1,
            "max_attempts": 3,
            "origintrail_batch_eligible": False,
            "batch_handoff_recovery_only": False,
            "locked_by": "another-worker",
            "input": {
                "content_kind": "daily_news",
                "request_id": REQUEST_ID,
                "source_content": "A complete official product update.",
                "source_url": "https://x.com/Yellow/status/456",
                "source_image_url": "",
                "manual_only": False,
            },
        })

    with pytest.raises(AutomationRepositoryError, match="invalid_claim_response"):
        await _repo(handler).claim_job(
            workspace_id=WORKSPACE_ID,
            worker_id="official-x:test-worker",
        )


@pytest.mark.asyncio
async def test_database_error_body_is_not_exposed():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={
            "message": "raw source content and internal SQL detail",
        })

    with pytest.raises(AutomationRepositoryError) as error:
        await _repo(handler).get_state(
            workspace_id=WORKSPACE_ID,
            client_id="yellow",
            kst_date=date(2026, 7, 22),
        )
    assert error.value.code == "automation_database_rpc_failed"
    assert "internal SQL" not in str(error.value)


@pytest.mark.asyncio
async def test_source_intake_rejects_more_than_the_database_limit():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("oversized source intake reached the database")

    with pytest.raises(ValueError, match="bounded poll size"):
        await _repo(handler).record_sources(
            workspace_id=WORKSPACE_ID,
            client_id="squid",
            handle="@SquidRouter",
            poll_request_id=REQUEST_ID,
            expected_cursor=None,
            next_cursor=None,
            source_items=[{} for _ in range(101)],
            polled_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_ranking_evidence_rpc_receipt_matches_immutable_snapshot_hash():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            request.url.path
            == "/rest/v1/rpc/record_content_signal_ranking_evidence"
        )
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "recorded": True,
            "snapshot_hash": captured["target_snapshot_hash"],
            "reused": False,
        })

    snapshot_hash = await _repo(handler).record_ranking_evidence(
        workspace_id=WORKSPACE_ID,
        snapshot=_signals_snapshot(),
        ranking_version="official-x-demand-v1",
    )

    assert set(captured) == {
        "target_workspace_id",
        "target_client_id",
        "target_snapshot_hash",
        "target_schema_version",
        "target_generated_at",
        "target_window_start",
        "target_window_end",
        "target_ranking_version",
        "target_demand_terms",
    }
    assert len(captured["target_snapshot_hash"]) == 64
    assert snapshot_hash == captured["target_snapshot_hash"]
    assert captured["target_schema_version"] == "1.1"
    assert captured["target_demand_terms"] == [{
        "term": "bridge",
        "weight": 0.9,
        "sources": ["community", "telegram_content"],
    }]


@pytest.mark.asyncio
async def test_ranking_evidence_v2_accepts_schema_12_snapshot_and_keeps_wire_schema():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "recorded": True,
            "snapshot_hash": captured["target_snapshot_hash"],
            "reused": False,
        })

    snapshot_hash = await _repo(handler).record_ranking_evidence(
        workspace_id=WORKSPACE_ID,
        snapshot=_signals_snapshot(),
        ranking_version="official-x-demand-v2",
    )

    assert snapshot_hash == captured["target_snapshot_hash"]
    assert captured["target_schema_version"] == "1.1"
    assert captured["target_ranking_version"] == "official-x-demand-v2"


@pytest.mark.asyncio
async def test_ranking_evidence_v2_rejects_a_non_schema_12_snapshot():
    snapshot = replace(_signals_snapshot(), schema_version="1.1")

    with pytest.raises(
        ValueError,
        match="unsupported content signal ranking version",
    ):
        await _repo(lambda _request: httpx.Response(500)).record_ranking_evidence(
            workspace_id=WORKSPACE_ID,
            snapshot=snapshot,
            ranking_version="official-x-demand-v2",
        )


@pytest.mark.asyncio
async def test_ranking_evidence_v1_keeps_legacy_snapshot_compatibility():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "recorded": True,
            "snapshot_hash": captured["target_snapshot_hash"],
            "reused": False,
        })

    await _repo(handler).record_ranking_evidence(
        workspace_id=WORKSPACE_ID,
        snapshot=replace(_signals_snapshot(), schema_version="1.1"),
        ranking_version="official-x-demand-v1",
    )

    assert captured["target_schema_version"] == "1.1"
    assert captured["target_ranking_version"] == "official-x-demand-v1"


@pytest.mark.asyncio
async def test_promotion_candidate_rpc_is_bound_to_ranking_snapshot():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            request.url.path
            == "/rest/v1/rpc/record_content_promotion_candidates"
        )
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "recorded": True,
            "snapshot_hash": captured["target_snapshot_hash"],
            "candidate_count": 1,
            "matched_count": 1,
            "evidence_count": 1,
            "recommendation_count": 2,
        })

    recommendation_count = await _repo(handler).record_promotion_candidates(
        workspace_id=WORKSPACE_ID,
        snapshot=_signals_snapshot(with_candidate=True),
        snapshot_hash="b" * 64,
    )

    assert recommendation_count == 2
    assert set(captured) == {
        "target_workspace_id",
        "target_client_id",
        "target_snapshot_hash",
        "target_schema_version",
        "target_generated_at",
        "target_window_start",
        "target_window_end",
        "target_policy_version",
        "target_candidates",
    }
    assert captured["target_policy_version"] == "content-performance-v1"
    assert captured["target_schema_version"] == "1.1"
    assert (
        captured["target_candidates"][0]["candidate_id"]
        == PROMOTION_CANDIDATE_ID
    )
    assert captured["target_candidates"][0]["recommended_formats"] == [
        "article",
        "tutorial",
    ]


@pytest.mark.asyncio
async def test_learning_evidence_rpc_is_bound_to_the_same_snapshot_hash():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            request.url.path
            == "/rest/v1/rpc/record_content_learning_evidence"
        )
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "recorded": True,
            "snapshot_hash": captured["target_snapshot_hash"],
            "reused": False,
        })

    await _repo(handler).record_learning_evidence(
        workspace_id=WORKSPACE_ID,
        snapshot=_signals_snapshot(),
        snapshot_hash="c" * 64,
    )

    assert captured["target_schema_version"] == "1.2"
    assert captured["target_learning_method"] == "tutorial-priority-v1"
    assert captured["target_learning"] == {
        "tutorial_priority": 0.48,
        "gaps": [{
            "category": "squid_advanced",
            "attempts": 48,
            "participants": 12,
            "accuracy_pct": 52,
        }],
    }


@pytest.mark.asyncio
async def test_ranking_evidence_rejects_a_mismatched_database_receipt():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "recorded": True,
            "snapshot_hash": "0" * 64,
        })

    with pytest.raises(
        AutomationRepositoryError,
        match="invalid_ranking_evidence_receipt",
    ):
        await _repo(handler).record_ranking_evidence(
            workspace_id=WORKSPACE_ID,
            snapshot=_signals_snapshot(),
            ranking_version="official-x-demand-v1",
        )


@pytest.mark.asyncio
async def test_repository_payload_names_match_the_database_rpc_contract():
    requests: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        requests[name] = json.loads(request.content)
        if name == "record_official_x_sources":
            return httpx.Response(200, json={
                "last_cursor": None,
                "draft_reserved_today": False,
                "pending_sources": [],
            })
        if name == "queue_review_draft_job":
            return httpx.Response(200, json={
                "job_id": JOB_ID,
                "status": "queued",
                "reused": False,
            })
        if name == "get_or_create_official_x_style_reference_pack":
            return httpx.Response(200, json={
                "request_id": REQUEST_ID,
                "primary_source_item_id": SOURCE_ID,
                "reference_pack_hash": "a" * 32,
                "references": [{
                    "source_item_id": "55555555-5555-4555-8555-555555555555",
                    "source_url": "https://x.com/Yellow/status/123",
                    "text": "A prior official writing reference.",
                    "published_at": "2026-07-21T08:00:00Z",
                }],
                "reused": False,
            })
        if name == "claim_review_draft_job":
            return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})
        if name == "bind_review_draft_execution_plane":
            return httpx.Response(200, json={
                "job_id": JOB_ID,
                "execution_plane": "studio_sync",
                "reused": False,
            })
        return httpx.Response(200, json={"job_id": JOB_ID, "status": "succeeded"})

    repo = _repo(handler)
    await repo.record_sources(
        workspace_id=WORKSPACE_ID,
        client_id="yellow",
        handle="@Yellow",
        poll_request_id=REQUEST_ID,
        expected_cursor=None,
        next_cursor=None,
        source_items=[],
        polled_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )
    await repo.queue_job(
        workspace_id=WORKSPACE_ID,
        client_id="yellow",
        kst_date=date(2026, 7, 22),
        source_item_ids=[SOURCE_ID],
        content_kind="daily_news",
        request_id=REQUEST_ID,
        source_content="A sufficiently long official update.",
        source_url="https://x.com/Yellow/status/456",
    )
    pack = await repo.get_or_create_style_reference_pack(
        workspace_id=WORKSPACE_ID,
        client_id="yellow",
        request_id=REQUEST_ID,
        primary_source_item_id=SOURCE_ID,
    )
    await repo.claim_job(
        workspace_id=WORKSPACE_ID,
        worker_id="official-x:test-worker",
    )
    await repo.bind_execution_plane(
        job_id=JOB_ID,
        worker_id="official-x:test-worker",
        requested_plane="studio_sync",
    )
    await repo.complete_job(
        job_id=JOB_ID,
        worker_id="official-x:test-worker",
        content_item_id=REQUEST_ID,
        content_version_id="55555555-5555-4555-8555-555555555555",
    )
    await repo.fail_job(
        job_id=JOB_ID,
        worker_id="official-x:test-worker",
        error_code="studio_generation_unavailable",
        retryable=True,
        retry_at=datetime(2026, 7, 22, 0, 15, tzinfo=timezone.utc),
    )

    assert set(requests["record_official_x_sources"]) == {
        "target_workspace_id", "target_client_id", "target_handle",
        "target_poll_request_id", "target_expected_cursor", "target_next_cursor",
        "target_items", "target_polled_at",
    }
    assert set(requests["queue_review_draft_job"]) == {
        "target_workspace_id", "target_client_id", "target_kst_date",
        "target_source_item_ids", "target_content_kind", "target_request_id",
        "target_source_content", "target_source_url", "target_source_image_url",
        "target_manual_only",
    }
    assert set(requests["get_or_create_official_x_style_reference_pack"]) == {
        "target_workspace_id", "target_client_id", "target_request_id",
        "target_primary_source_item_id", "target_reference_limit",
    }
    assert pack.reference_pack_hash == "a" * 32
    assert pack.references[0].source_url == "https://x.com/Yellow/status/123"
    assert set(requests["claim_review_draft_job"]) == {
        "target_workspace_id", "target_worker_id", "target_lease_seconds",
    }
    assert set(requests["bind_review_draft_execution_plane"]) == {
        "target_job_id", "target_worker_id", "target_requested_plane",
    }
    assert set(requests["complete_review_draft_job"]) == {
        "target_job_id", "target_worker_id", "target_content_item_id",
        "target_content_version_id",
    }
    assert set(requests["fail_review_draft_job"]) == {
        "target_job_id", "target_worker_id", "target_error_code",
        "target_error_message", "target_retryable", "target_retry_at",
    }


@pytest.mark.asyncio
async def test_origintrail_intake_uses_the_nonquote_wrapper_rpc():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == (
            "/rest/v1/rpc/record_origintrail_nonquote_sources"
        )
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "last_cursor": "456",
            "draft_reserved_today": False,
            "pending_sources": [],
        })

    await _repo(handler).record_sources(
        workspace_id=WORKSPACE_ID,
        client_id="origintrail",
        handle="@origin_trail",
        poll_request_id=REQUEST_ID,
        expected_cursor=None,
        next_cursor="456",
        source_items=[{
            "external_id": "456",
            "source_content": "A standalone OriginTrail update.",
            "source_url": "https://x.com/origin_trail/status/456",
            "published_at": "2026-07-22T00:00:00+00:00",
            "media": [],
            "metrics": {},
            "is_note_tweet": False,
            "is_quote": False,
        }],
        polled_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert captured["target_client_id"] == "origintrail"
    assert captured["target_items"][0]["is_quote"] is False


@pytest.mark.asyncio
async def test_execution_plane_binding_returns_the_existing_plane():
    def handler(_request):
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "execution_plane": "studio_sync",
            "reused": True,
        })

    plane = await _repo(handler).bind_execution_plane(
        job_id=JOB_ID,
        worker_id="official-x:test-worker",
        requested_plane="openai_batch",
    )

    assert plane == "studio_sync"


@pytest.mark.asyncio
async def test_uncertain_execution_plane_receipt_is_retryable():
    repository = _repo(
        lambda _request: httpx.Response(200, text="not-json")
    )

    with pytest.raises(AutomationRepositoryError) as caught:
        await repository.bind_execution_plane(
            job_id=JOB_ID,
            worker_id="official-x:test-worker",
            requested_plane="openai_batch",
        )

    assert caught.value.code == "invalid_execution_plane_response"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_first_execution_plane_receipt_must_match_the_request():
    repository = _repo(
        lambda _request: httpx.Response(200, json={
            "job_id": JOB_ID,
            "execution_plane": "studio_sync",
            "reused": False,
        })
    )

    with pytest.raises(AutomationRepositoryError) as caught:
        await repository.bind_execution_plane(
            job_id=JOB_ID,
            worker_id="official-x:test-worker",
            requested_plane="openai_batch",
        )

    assert caught.value.code == "invalid_execution_plane_response"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_claim_parses_kst_date_and_completes_exact_batch_handoff():
    requests = {}
    worker_id = "official-x:test-worker"
    input_sha256 = "a" * 64

    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        requests[name] = json.loads(request.content)
        if name == "claim_review_draft_job":
            return httpx.Response(200, json={
                "job_id": JOB_ID,
                "workspace_id": WORKSPACE_ID,
                "client_id": "origintrail",
                "status": "running",
                "attempts": 1,
                "max_attempts": 3,
                "origintrail_batch_eligible": True,
                "batch_handoff_recovery_only": False,
                "locked_by": worker_id,
                "input": {
                    "kst_date": "2026-07-22",
                    "content_kind": "daily_news",
                    "request_id": REQUEST_ID,
                    "source_item_ids": [SOURCE_ID],
                    "source_content": (
                        "A sufficiently long official OriginTrail update."
                    ),
                    "source_url": (
                        "https://x.com/origin_trail/status/456"
                    ),
                    "source_image_url": "",
                    "manual_only": False,
                },
            })
        if name == "complete_review_draft_batch_handoff":
            return httpx.Response(200, json={
                "job_id": JOB_ID,
                "status": "succeeded",
                "batch_job_id": JOB_ID,
                "reused": False,
            })
        raise AssertionError(f"unexpected RPC: {name}")

    repository = _repo(handler)
    claimed = await repository.claim_job(
        workspace_id=WORKSPACE_ID,
        worker_id=worker_id,
    )
    assert claimed is not None
    assert claimed.kst_date == date(2026, 7, 22)
    assert claimed.origintrail_batch_eligible is True
    assert claimed.batch_handoff_recovery_only is False

    await repository.complete_batch_handoff(
        job_id=claimed.job_id,
        worker_id=worker_id,
        batch_job_id=claimed.job_id,
        input_sha256=input_sha256,
    )

    assert requests["complete_review_draft_batch_handoff"] == {
        "target_job_id": JOB_ID,
        "target_worker_id": worker_id,
        "target_batch_job_id": JOB_ID,
        "target_input_sha256": input_sha256,
    }


@pytest.mark.asyncio
async def test_recover_batch_handoff_reads_only_the_stored_receipt():
    requests = {}
    worker_id = "official-x:recovery-worker"

    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        requests[name] = json.loads(request.content)
        assert name == "recover_review_draft_batch_handoff"
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "status": "succeeded",
            "batch_job_id": JOB_ID,
            "input_sha256": "b" * 64,
            "review_state": "pending",
            "reused": False,
        })

    recovered = await _repo(handler).recover_batch_handoff(
        job_id=JOB_ID,
        worker_id=worker_id,
    )

    assert recovered is True
    assert requests["recover_review_draft_batch_handoff"] == {
        "target_job_id": JOB_ID,
        "target_worker_id": worker_id,
    }


@pytest.mark.asyncio
async def test_recover_batch_handoff_allows_an_exact_receipt_miss():
    def handler(request):
        assert request.url.path.endswith(
            "/rpc/recover_review_draft_batch_handoff"
        )
        return httpx.Response(
            200,
            content=b"null",
            headers={"content-type": "application/json"},
        )

    recovered = await _repo(handler).recover_batch_handoff(
        job_id=JOB_ID,
        worker_id="official-x:recovery-worker",
    )

    assert recovered is False


def _recovery_subject(
    failure_code: str = "squid_visual_localization_incomplete",
) -> dict[str, object]:
    return {
        "contract": "squid-failed-draft-recovery@1",
        "workspace_id": WORKSPACE_ID,
        "job_id": JOB_ID,
        "recovery_id": RECOVERY_ID,
        "request_id": REQUEST_ID,
        "source_item_id": SOURCE_ID,
        "kst_date": "2026-08-25",
        "job_input_sha256": "1" * 64,
        "source_snapshot_sha256": "2" * 64,
        "style_pack_sha256": "3" * 64,
        "failed_output_sha256": "4" * 64,
        "failure_code": failure_code,
        "failed_attempts": 3,
        "failed_max_attempts": 3,
        "approval_id": APPROVAL_ID,
        "approved_by": "codex:user",
        "approved_at": "2026-08-25T08:00:00+00:00",
        "expires_at": "2026-08-25T09:00:00+00:00",
        "release_sha": RELEASE_SHA,
        "claims_allowed": 1,
        "same_job": True,
        "same_request_id": True,
        "automatic_approval": False,
        "automatic_publication": False,
        "human_review_required": True,
        "legacy_failure_requires_explicit_review": True,
        "failed_output_snapshot": {
            "execution_plane": "studio_sync",
            "last_error_code": failure_code,
            "last_failure_error_code": failure_code,
            "last_failure_retryable": False,
            "finished_at": "2026-08-25T07:47:42+00:00",
        },
    }


def _recovery_inspection(
    *,
    authorized: bool = False,
    failure_code: str = "squid_visual_localization_incomplete",
) -> dict[str, object]:
    return {
        "eligible": True,
        "authorized": authorized,
        "recovery_id": RECOVERY_ID,
        "job_id": JOB_ID,
        "request_id": REQUEST_ID,
        "source_item_id": SOURCE_ID,
        "approval_subject": _recovery_subject(failure_code),
        "approval_subject_sha256": "5" * 64,
        "claims_allowed": 1,
        "claims_consumed": 0,
        "expires_at": "2026-08-25T09:00:00+00:00",
        "release_sha": RELEASE_SHA,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_code",
    [
        "squid_visual_localization_incomplete",
        "squid_copy_discovery_unavailable",
    ],
)
async def test_inspect_failed_draft_recovery_uses_exact_safe_rpc_contract(
    failure_code: str,
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(
            "/rpc/inspect_squid_failed_draft_recovery"
        )
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=_recovery_inspection(failure_code=failure_code),
        )

    inspected = await _repo(handler).inspect_failed_draft_recovery(
        workspace_id=WORKSPACE_ID,
        job_id=JOB_ID,
        recovery_id=RECOVERY_ID,
        approval_id=APPROVAL_ID,
        approved_by="codex:user",
        approved_at=datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc),
        release_sha=RELEASE_SHA,
    )

    assert inspected.request_id == REQUEST_ID
    assert inspected.source_item_id == SOURCE_ID
    assert inspected.approval_subject_sha256 == "5" * 64
    assert set(captured) == {
        "target_workspace_id",
        "target_job_id",
        "target_recovery_id",
        "target_approval_id",
        "target_approved_by",
        "target_approved_at",
        "target_expires_at",
        "target_release_sha",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate",),
    [
        (lambda response: response.update({"claims_allowed": True}),),
        (
            lambda response: response["approval_subject"].update(
                {"same_request_id": False}
            ),
        ),
        (
            lambda response: response["approval_subject"].update(
                {"approved_by": "another-operator"}
            ),
        ),
        (
            lambda response: response.update(
                {"request_id": "99999999-9999-4999-8999-999999999999"}
            ),
        ),
        (
            lambda response: response["approval_subject"].update(
                {"failure_code": "squid_placement_audit_unavailable"}
            ),
        ),
        (
            lambda response: response["approval_subject"][
                "failed_output_snapshot"
            ].update({"last_failure_error_code": "different_failure"}),
        ),
    ],
)
async def test_recovery_inspection_rejects_loose_types_or_unbound_subject(
    mutate,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        response = _recovery_inspection()
        mutate(response)
        return httpx.Response(200, json=response)

    with pytest.raises(
        AutomationRepositoryError,
        match="invalid_recovery_inspection_response",
    ):
        await _repo(handler).inspect_failed_draft_recovery(
            workspace_id=WORKSPACE_ID,
            job_id=JOB_ID,
            recovery_id=RECOVERY_ID,
            approval_id=APPROVAL_ID,
            approved_by="codex:user",
            approved_at=datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc),
            expires_at=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc),
            release_sha=RELEASE_SHA,
        )


@pytest.mark.asyncio
async def test_authorize_failed_draft_recovery_rechecks_subject_before_write():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        calls.append((name, json.loads(request.content)))
        if name == "inspect_squid_failed_draft_recovery":
            return httpx.Response(200, json=_recovery_inspection())
        assert name == "authorize_squid_failed_draft_recovery"
        return httpx.Response(200, json={
            "authorized": True,
            "reused": False,
            "recovery_id": RECOVERY_ID,
            "job_id": JOB_ID,
            "request_id": REQUEST_ID,
            "approval_subject_sha256": "5" * 64,
            "claims_allowed": 1,
            "claims_consumed": 0,
            "expires_at": "2026-08-25T09:00:00+00:00",
            "release_sha": RELEASE_SHA,
        })

    reused = await _repo(handler).authorize_failed_draft_recovery(
        workspace_id=WORKSPACE_ID,
        job_id=JOB_ID,
        recovery_id=RECOVERY_ID,
        approval_id=APPROVAL_ID,
        approved_by="codex:user",
        approved_at=datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc),
        release_sha=RELEASE_SHA,
        approval_subject_sha256="5" * 64,
    )

    assert reused is False
    assert [name for name, _payload in calls] == [
        "inspect_squid_failed_draft_recovery",
        "authorize_squid_failed_draft_recovery",
    ]
    assert set(calls[1][1]) == {
        "target_workspace_id",
        "target_job_id",
        "target_recovery_id",
        "target_approval_id",
        "target_approved_by",
        "target_approved_at",
        "target_expires_at",
        "target_release_sha",
        "target_approval_subject_sha256",
    }


def _recovery_claim(*, granted: bool) -> dict[str, object]:
    common = {
        "claim_granted": granted,
        "generation_allowed": granted,
        "failed_draft_recovery_only": True,
        "recovery_id": RECOVERY_ID,
        "job_id": JOB_ID,
        "request_id": REQUEST_ID,
        "approval_subject_sha256": "5" * 64,
        "claims_allowed": 1,
        "claims_consumed": 1 if granted else 0,
        "release_sha": RELEASE_SHA,
    }
    if not granted:
        return common
    return {
        **common,
        "workspace_id": WORKSPACE_ID,
        "client_id": "squid",
        "status": "running",
        "attempts": 3,
        "max_attempts": 3,
        "origintrail_batch_eligible": False,
        "batch_handoff_recovery_only": False,
        "locked_by": f"squid-recovery:{RECOVERY_ID}",
        "lease_expires_at": "2026-08-25T08:15:00+00:00",
        "input": {
            "workflow": "official_x_review_draft_v1",
            "kst_date": "2026-08-25",
            "source_item_ids": [SOURCE_ID],
            "content_kind": "daily_news",
            "request_id": REQUEST_ID,
            "source_content": "A sufficiently long Squid official update.",
            "source_url": "https://x.com/SquidRouter/status/123",
            "source_image_url": "https://pbs.twimg.com/media/source.jpg",
            "manual_only": False,
        },
    }


@pytest.mark.asyncio
async def test_targeted_recovery_claim_requires_explicit_generation_fence():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(
            "/rpc/claim_squid_failed_draft_recovery"
        )
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_recovery_claim(granted=True))

    claimed = await _repo(handler).claim_failed_draft_recovery(
        workspace_id=WORKSPACE_ID,
        job_id=JOB_ID,
        recovery_id=RECOVERY_ID,
        approval_subject_sha256="5" * 64,
        release_sha=RELEASE_SHA,
        worker_id=f"squid-recovery:{RECOVERY_ID}",
    )

    assert claimed is not None
    assert claimed.failed_draft_recovery_only is True
    assert claimed.request_id == REQUEST_ID
    assert claimed.attempts == claimed.max_attempts == 3
    assert set(captured) == {
        "target_workspace_id",
        "target_job_id",
        "target_recovery_id",
        "target_approval_subject_sha256",
        "target_release_sha",
        "target_worker_id",
        "target_lease_seconds",
    }


@pytest.mark.asyncio
async def test_consumed_recovery_claim_never_becomes_generation_work():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_recovery_claim(granted=False))

    claimed = await _repo(handler).claim_failed_draft_recovery(
        workspace_id=WORKSPACE_ID,
        job_id=JOB_ID,
        recovery_id=RECOVERY_ID,
        approval_subject_sha256="5" * 64,
        release_sha=RELEASE_SHA,
        worker_id=f"squid-recovery:{RECOVERY_ID}",
    )

    assert claimed is None


@pytest.mark.asyncio
async def test_recovery_claim_rejects_unexpected_raw_fields():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            **_recovery_claim(granted=True),
            "provider_body": "must never reach the runner",
        })

    with pytest.raises(
        AutomationRepositoryError,
        match="invalid_recovery_claim_response",
    ):
        await _repo(handler).claim_failed_draft_recovery(
            workspace_id=WORKSPACE_ID,
            job_id=JOB_ID,
            recovery_id=RECOVERY_ID,
            approval_subject_sha256="5" * 64,
            release_sha=RELEASE_SHA,
            worker_id=f"squid-recovery:{RECOVERY_ID}",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"claims_consumed": True},
        {"attempts": True, "max_attempts": True},
        {"workspace_id": "99999999-9999-4999-8999-999999999999"},
    ],
)
async def test_recovery_claim_rejects_boolean_counters_and_identity_drift(
    mutation,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            **_recovery_claim(granted=True),
            **mutation,
        })

    with pytest.raises(
        AutomationRepositoryError,
        match="invalid_recovery_claim_response",
    ):
        await _repo(handler).claim_failed_draft_recovery(
            workspace_id=WORKSPACE_ID,
            job_id=JOB_ID,
            recovery_id=RECOVERY_ID,
            approval_subject_sha256="5" * 64,
            release_sha=RELEASE_SHA,
            worker_id=f"squid-recovery:{RECOVERY_ID}",
        )
