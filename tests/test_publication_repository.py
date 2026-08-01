from __future__ import annotations

import hashlib
import json
import struct
from datetime import datetime, timezone

import httpx
import pytest

from core.publications.models import ClaimedTelegramPublication, StoredPng
from core.publications.repository import (
    PublicationRepositoryError,
    SupabasePublicationRepository,
)


WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
JOB_ID = "22222222-2222-4222-8222-222222222222"
PUBLICATION_ID = "33333333-3333-4333-8333-333333333333"
ITEM_ID = "44444444-4444-4444-8444-444444444444"
VERSION_ID = "55555555-5555-4555-8555-555555555555"
APPROVAL_ID = "66666666-6666-4666-8666-666666666666"
ASSET_ID = "77777777-7777-4777-8777-777777777777"
WORKER_ID = "worker:test-1234"
PNG = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\x0dIHDR"
    + struct.pack(">II", 1200, 675)
    + b"exact-approved-png"
)


def _asset() -> StoredPng:
    return StoredPng(
        asset_id=ASSET_ID,
        storage_bucket="content-studio",
        storage_path=f"{WORKSPACE_ID}/squid/{ASSET_ID}/news-card.png",
        mime_type="image/png",
        byte_size=len(PNG),
        sha256=hashlib.sha256(PNG).hexdigest(),
        width=1200,
        height=675,
    )


def _claim() -> ClaimedTelegramPublication:
    return ClaimedTelegramPublication(
        job_id=JOB_ID,
        publication_id=PUBLICATION_ID,
        content_item_id=ITEM_ID,
        content_version_id=VERSION_ID,
        approval_id=APPROVAL_ID,
        client_id="squid",
        attempts=1,
        max_attempts=3,
        locked_by=WORKER_ID,
        lease_expires_at=datetime(2026, 8, 1, 12, 5, tzinfo=timezone.utc),
        telegram_text="승인된 텔레그램 문구 그대로",
        asset=_asset(),
    )


def _claim_json() -> dict[str, object]:
    claim = _claim()
    return {
        "job_id": claim.job_id,
        "publication_id": claim.publication_id,
        "content_item_id": claim.content_item_id,
        "content_version_id": claim.content_version_id,
        "approval_id": claim.approval_id,
        "client_id": claim.client_id,
        "attempts": claim.attempts,
        "max_attempts": claim.max_attempts,
        "locked_by": claim.locked_by,
        "lease_expires_at": claim.lease_expires_at.isoformat(),
        "telegram_public_username": "squid_kor_update",
        "telegram_text": claim.telegram_text,
        "asset": vars(claim.asset),
    }


def _repository(handler) -> SupabasePublicationRepository:
    return SupabasePublicationRepository(
        supabase_url="https://project.supabase.co",
        service_role_key="s" * 40,
        workspace_id=WORKSPACE_ID,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_claim_pins_workspace_worker_version_approval_caption_and_asset():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_claim_json())

    claim = await _repository(handler).claim(
        worker_id=WORKER_ID,
        lease_seconds=180,
    )

    assert claim == _claim()
    assert len(requests) == 1
    assert requests[0].url.path.endswith(
        "/rest/v1/rpc/claim_exact_telegram_publication_job"
    )
    assert json.loads(requests[0].content) == {
        "target_workspace_id": WORKSPACE_ID,
        "target_worker_id": WORKER_ID,
        "target_lease_seconds": 180,
    }
    assert requests[0].headers["authorization"] == "Bearer " + "s" * 40


@pytest.mark.asyncio
async def test_claim_rejects_a_short_lease_before_any_database_call():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=None)

    with pytest.raises(ValueError, match="between 180 and 600"):
        await _repository(handler).claim(
            worker_id=WORKER_ID,
            lease_seconds=179,
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_claim_rejects_noncanonical_public_username():
    response = _claim_json()
    response["telegram_public_username"] = "attacker_channel"
    repository = _repository(lambda _request: httpx.Response(200, json=response))

    with pytest.raises(PublicationRepositoryError, match="invalid_publication_claim"):
        await repository.claim(worker_id=WORKER_ID, lease_seconds=180)


@pytest.mark.asyncio
async def test_download_requires_exact_bytes_hash_and_ihdr_dimensions():
    repository = _repository(
        lambda _request: httpx.Response(
            200,
            headers={
                "content-type": "image/png",
                "content-length": str(len(PNG)),
            },
            content=PNG,
        )
    )
    assert await repository.download_asset(_claim()) == PNG

    wrong_dimensions = PNG[:16] + struct.pack(">II", 1, 1) + PNG[24:]
    bad = _repository(
        lambda _request: httpx.Response(200, content=wrong_dimensions)
    )
    with pytest.raises(PublicationRepositoryError, match="publication_asset_invalid"):
        await bad.download_asset(_claim())


@pytest.mark.asyncio
async def test_reused_attempt_fence_never_authorizes_another_send():
    digest = "a" * 64
    response = {
        "job_id": JOB_ID,
        "publication_id": PUBLICATION_ID,
        "request_sha256": digest,
        "status": "publishing",
        "attempt_started": True,
        "reused": True,
    }
    repository = _repository(lambda _request: httpx.Response(200, json=response))

    with pytest.raises(PublicationRepositoryError, match="invalid_publication_attempt"):
        await repository.mark_attempt(_claim(), digest)


@pytest.mark.asyncio
async def test_new_attempt_fence_authorizes_the_single_provider_call():
    digest = "b" * 64
    response = {
        "job_id": JOB_ID,
        "publication_id": PUBLICATION_ID,
        "request_sha256": digest,
        "status": "publishing",
        "attempt_started": True,
        "reused": False,
    }
    repository = _repository(lambda _request: httpx.Response(200, json=response))

    assert await repository.mark_attempt(_claim(), digest) is None


@pytest.mark.asyncio
async def test_completion_rpc_records_only_the_verified_provider_receipt():
    digest = "c" * 64
    provider_date = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "publication_id": PUBLICATION_ID,
            "status": "published",
            "reused": False,
        })

    await _repository(handler).complete(
        _claim(),
        digest,
        message_id=321,
        chat_username="squid_kor_update",
        provider_date=provider_date,
    )

    assert seen == {
        "target_job_id": JOB_ID,
        "target_worker_id": WORKER_ID,
        "target_request_sha256": digest,
        "target_message_id": 321,
        "target_chat_username": "squid_kor_update",
        "target_provider_date": provider_date.isoformat(),
    }


@pytest.mark.asyncio
async def test_failure_rpc_preserves_delivery_unknown_status():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "publication_id": PUBLICATION_ID,
            "status": "delivery_unknown",
            "job_status": "failed",
            "reused": False,
        })

    status = await _repository(handler).fail(
        _claim(),
        error_code="telegram_delivery_unknown",
        retryable_before_attempt=False,
    )

    assert status == "delivery_unknown"
    assert "target_error_message" not in seen
    assert seen["target_error_code"] == "telegram_delivery_unknown"
    assert seen["target_retryable_before_attempt"] is False


@pytest.mark.asyncio
async def test_pre_attempt_failure_returns_queued_publication_status():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "publication_id": PUBLICATION_ID,
            "status": "queued",
            "job_status": "retrying",
            "reused": False,
        })

    status = await _repository(handler).fail(
        _claim(),
        error_code="telegram_preflight_unavailable",
        retryable_before_attempt=True,
    )

    assert status == "queued"
    assert seen == {
        "target_job_id": JOB_ID,
        "target_worker_id": WORKER_ID,
        "target_error_code": "telegram_preflight_unavailable",
        "target_retryable_before_attempt": True,
    }


@pytest.mark.asyncio
async def test_arbitrary_failure_code_is_replaced_before_the_rpc():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "publication_id": PUBLICATION_ID,
            "status": "failed",
            "job_status": "failed",
            "reused": False,
        })

    await _repository(handler).fail(
        _claim(),
        error_code="secret_or_freeform_payload",
        retryable_before_attempt=False,
    )

    assert seen["target_error_code"] == "telegram_publication_request_invalid"
