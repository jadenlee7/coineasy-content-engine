from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json

import httpx
import pytest

from core.automation.content_signals import ContentSignalsSnapshot, DemandTerm
from core.automation.repository import (
    AutomationRepositoryError,
    SupabaseAutomationRepository,
)


WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
JOB_ID = "33333333-3333-4333-8333-333333333333"
REQUEST_ID = "44444444-4444-4444-8444-444444444444"
SERVICE_KEY = "service-role-key-that-is-longer-than-thirty-two-characters"


def _repo(handler):
    return SupabaseAutomationRepository(
        supabase_url="https://project-ref.supabase.co",
        service_role_key=SERVICE_KEY,
        transport=httpx.MockTransport(handler),
    )


def _signals_snapshot():
    now = datetime(2026, 7, 27, 6, 0, tzinfo=timezone.utc)
    return ContentSignalsSnapshot(
        schema_version="1.0",
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

    await _repo(handler).record_ranking_evidence(
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
    assert captured["target_demand_terms"] == [{
        "term": "bridge",
        "weight": 0.9,
        "sources": ["community", "telegram_content"],
    }]


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
        if name == "claim_review_draft_job":
            return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})
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
    await repo.claim_job(
        workspace_id=WORKSPACE_ID,
        worker_id="official-x:test-worker",
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
    assert set(requests["claim_review_draft_job"]) == {
        "target_workspace_id", "target_worker_id", "target_lease_seconds",
    }
    assert set(requests["complete_review_draft_job"]) == {
        "target_job_id", "target_worker_id", "target_content_item_id",
        "target_content_version_id",
    }
    assert set(requests["fail_review_draft_job"]) == {
        "target_job_id", "target_worker_id", "target_error_code",
        "target_error_message", "target_retryable", "target_retry_at",
    }
