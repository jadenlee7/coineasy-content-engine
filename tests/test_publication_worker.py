from __future__ import annotations

import hashlib
import struct
from datetime import datetime, timezone

import pytest

from core.publications.models import (
    ClaimedTelegramPublication,
    StoredPng,
    TelegramReceipt,
)
from core.publications.repository import PublicationRepositoryError
from core.publications.worker import ExactTelegramPublicationWorker
from core.publishers.telegram_exact import TelegramExactConfig, TelegramExactError


PNG = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\x0dIHDR"
    + struct.pack(">II", 1200, 675)
    + b"approved"
)


def _claim() -> ClaimedTelegramPublication:
    workspace = "11111111-1111-4111-8111-111111111111"
    asset_id = "77777777-7777-4777-8777-777777777777"
    return ClaimedTelegramPublication(
        job_id="22222222-2222-4222-8222-222222222222",
        publication_id="33333333-3333-4333-8333-333333333333",
        content_item_id="44444444-4444-4444-8444-444444444444",
        content_version_id="55555555-5555-4555-8555-555555555555",
        approval_id="66666666-6666-4666-8666-666666666666",
        client_id="squid",
        attempts=1,
        max_attempts=3,
        locked_by="worker:test-1234",
        lease_expires_at=datetime(2026, 8, 1, 12, 5, tzinfo=timezone.utc),
        telegram_text="승인된 문구 그대로",
        asset=StoredPng(
            asset_id=asset_id,
            storage_bucket="content-studio",
            storage_path=f"{workspace}/squid/{asset_id}/news-card.png",
            mime_type="image/png",
            byte_size=len(PNG),
            sha256=hashlib.sha256(PNG).hexdigest(),
            width=1200,
            height=675,
        ),
    )


class FakeRepository:
    def __init__(
        self,
        *,
        claim=None,
        mark_error: Exception | None = None,
        complete_error: Exception | None = None,
    ):
        self.claimed = _claim() if claim is None else claim
        self.mark_error = mark_error
        self.complete_error = complete_error
        self.events: list[tuple] = []

    async def claim(self, *, worker_id: str, lease_seconds: int):
        self.events.append(("claim", worker_id, lease_seconds))
        return self.claimed

    async def download_asset(self, claim):
        self.events.append(("download", claim.content_version_id))
        return PNG

    async def mark_attempt(self, claim, request_sha256):
        self.events.append(("mark", request_sha256))
        if self.mark_error:
            raise self.mark_error

    async def complete(self, claim, request_sha256, **kwargs):
        self.events.append(("complete", request_sha256, kwargs))
        if self.complete_error:
            raise self.complete_error

    async def fail(self, claim, **kwargs):
        self.events.append(("fail", kwargs))
        return (
            "queued"
            if kwargs["retryable_before_attempt"]
            else "delivery_unknown"
            if any(event[0] == "mark" for event in self.events)
            else "failed"
        )


class FakePublisher:
    def __init__(self, *, preflight_error=None, send_error=None):
        self.config = TelegramExactConfig(
            client_id="squid",
            public_username="squid_kor_update",
            chat_id="@squid_kor_update",
            bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijk12345",
        )
        self.preflight_error = preflight_error
        self.send_error = send_error
        self.events: list[tuple] = []

    async def preflight(self):
        self.events.append(("preflight",))
        if self.preflight_error:
            raise self.preflight_error

    async def send_photo_once(self, *, image_bytes: bytes, caption: str):
        self.events.append(("send", image_bytes, caption))
        if self.send_error:
            raise self.send_error
        return TelegramReceipt(
            message_id=42,
            chat_username="squid_kor_update",
            provider_date=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        )


def _worker(repository, publisher):
    return ExactTelegramPublicationWorker(
        repository=repository,
        publisher_factory=lambda _client: publisher,
        allowed_clients=("squid",),
        worker_id="worker:test-1234",
    )


def test_worker_constructor_cannot_expand_the_squid_canary():
    with pytest.raises(ValueError, match="Squid-only"):
        ExactTelegramPublicationWorker(
            repository=FakeRepository(),
            publisher_factory=lambda _client: FakePublisher(),
            allowed_clients=("squid", "yellow"),
            worker_id="worker:test-1234",
        )


def test_worker_constructor_rejects_a_short_delivery_lease():
    with pytest.raises(ValueError, match="between 180 and 600"):
        ExactTelegramPublicationWorker(
            repository=FakeRepository(),
            publisher_factory=lambda _client: FakePublisher(),
            allowed_clients=("squid",),
            lease_seconds=179,
            worker_id="worker:test-1234",
        )


@pytest.mark.asyncio
async def test_success_fences_before_one_exact_send_then_completes():
    repository = FakeRepository()
    publisher = FakePublisher()

    result = await _worker(repository, publisher).run_once()

    assert result.as_dict() == {
        "ok": True,
        "claimed": True,
        "status": "published",
        "publication_id": _claim().publication_id,
    }
    assert [event[0] for event in repository.events] == [
        "claim", "download", "mark", "complete"
    ]
    assert publisher.events == [
        ("preflight",),
        ("send", PNG, "승인된 문구 그대로"),
    ]
    complete = repository.events[-1]
    assert complete[2]["message_id"] == 42
    assert complete[2]["chat_username"] == "squid_kor_update"


@pytest.mark.asyncio
async def test_mark_response_loss_never_calls_telegram_or_clears_the_lease():
    repository = FakeRepository(
        mark_error=PublicationRepositoryError("database_response_lost")
    )
    publisher = FakePublisher()

    result = await _worker(repository, publisher).run_once()

    assert result.status == "delivery_unknown"
    assert result.error == "publication_attempt_fence_unavailable"
    assert [event[0] for event in repository.events] == ["claim", "download", "mark"]
    assert publisher.events == [("preflight",)]


@pytest.mark.asyncio
async def test_send_timeout_is_delivery_unknown_and_never_retried():
    repository = FakeRepository()
    publisher = FakePublisher(
        send_error=TelegramExactError("telegram_delivery_unknown")
    )

    result = await _worker(repository, publisher).run_once()

    assert result.status == "delivery_unknown"
    assert [event[0] for event in publisher.events] == ["preflight", "send"]
    assert [event[0] for event in repository.events] == [
        "claim", "download", "mark", "fail"
    ]
    assert repository.events[-1][1]["retryable_before_attempt"] is False


@pytest.mark.asyncio
async def test_completion_response_loss_never_resends_or_marks_failed():
    repository = FakeRepository(
        complete_error=PublicationRepositoryError("database_response_lost")
    )
    publisher = FakePublisher()

    result = await _worker(repository, publisher).run_once()

    assert result.status == "delivery_unknown"
    assert result.error == "publication_completion_unavailable"
    assert [event[0] for event in publisher.events] == ["preflight", "send"]
    assert [event[0] for event in repository.events] == [
        "claim", "download", "mark", "complete"
    ]


@pytest.mark.asyncio
async def test_preflight_outage_can_retry_because_attempt_was_not_marked():
    repository = FakeRepository()
    publisher = FakePublisher(
        preflight_error=TelegramExactError(
            "telegram_preflight_unavailable",
            retryable_before_attempt=True,
        )
    )

    result = await _worker(repository, publisher).run_once()

    assert result.status == "queued"
    assert publisher.events == [("preflight",)]
    assert [event[0] for event in repository.events] == ["claim", "fail"]
    assert repository.events[-1][1]["retryable_before_attempt"] is True


@pytest.mark.asyncio
async def test_disallowed_client_fails_without_loading_any_publisher():
    claim = _claim()
    object.__setattr__(claim, "client_id", "yellow")
    repository = FakeRepository(claim=claim)
    factory_called = False

    def factory(_client):
        nonlocal factory_called
        factory_called = True
        return FakePublisher()

    worker = ExactTelegramPublicationWorker(
        repository=repository,
        publisher_factory=factory,
        allowed_clients=("squid",),
        worker_id="worker:test-1234",
    )
    result = await worker.run_once()

    assert result.status == "failed"
    assert factory_called is False
    assert [event[0] for event in repository.events] == ["claim", "fail"]
