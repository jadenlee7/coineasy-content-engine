from __future__ import annotations

import logging
import time
import uuid
from typing import Callable, Protocol

from core.publications.models import (
    ClaimedTelegramPublication,
    PublicationRunResult,
)
from core.publications.repository import (
    PublicationRepositoryError,
    SupabasePublicationRepository,
)
from core.publications.settings import PublicationSettings
from core.publishers.telegram_exact import (
    ExactTelegramPublisher,
    TelegramExactError,
    load_telegram_exact_config,
    telegram_send_photo_fingerprint,
)


class PublicationRepository(Protocol):
    async def claim(self, *, worker_id: str, lease_seconds: int): ...
    async def download_asset(self, claim: ClaimedTelegramPublication) -> bytes: ...
    async def mark_attempt(
        self, claim: ClaimedTelegramPublication, request_sha256: str
    ) -> None: ...
    async def complete(self, claim: ClaimedTelegramPublication, request_sha256: str, **kwargs) -> None: ...
    async def fail(self, claim: ClaimedTelegramPublication, **kwargs) -> str: ...


PublisherFactory = Callable[[str], ExactTelegramPublisher]
_LOGGER = logging.getLogger(__name__)


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1_000))


class ExactTelegramPublicationWorker:
    def __init__(
        self,
        *,
        repository: PublicationRepository,
        publisher_factory: PublisherFactory,
        allowed_clients: tuple[str, ...] = ("squid",),
        lease_seconds: int = 180,
        worker_id: str | None = None,
    ):
        if tuple(allowed_clients) != ("squid",):
            raise ValueError("the exact Telegram canary is Squid-only")
        if not 180 <= lease_seconds <= 600:
            raise ValueError("publication lease must be between 180 and 600 seconds")
        self.repository = repository
        self.publisher_factory = publisher_factory
        self.allowed_clients = frozenset(allowed_clients)
        self.lease_seconds = lease_seconds
        self.worker_id = worker_id or f"telegram-publication:{uuid.uuid4()}"

    async def _fail_before_attempt(
        self,
        claim: ClaimedTelegramPublication,
        *,
        code: str,
        retryable: bool,
    ) -> PublicationRunResult:
        try:
            status = await self.repository.fail(
                claim,
                error_code=code,
                retryable_before_attempt=retryable,
            )
        except PublicationRepositoryError:
            return PublicationRunResult(
                ok=False,
                claimed=True,
                publication_id=claim.publication_id,
                status="failed",
                error="publication_failure_record_unavailable",
            )
        return PublicationRunResult(
            ok=False,
            claimed=True,
            publication_id=claim.publication_id,
            status=status,
            error=code,
        )

    async def run_once(self) -> PublicationRunResult:
        run_started_at = time.monotonic()
        claim = await self.repository.claim(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if claim is None:
            _LOGGER.info("exact_telegram_publication_idle elapsed_ms=%d", _elapsed_ms(run_started_at))
            return PublicationRunResult(ok=True, claimed=False, status="idle")
        _LOGGER.info(
            "exact_telegram_publication_claimed publication_id=%s client_id=%s attempts=%d elapsed_ms=%d",
            claim.publication_id,
            claim.client_id,
            claim.attempts,
            _elapsed_ms(run_started_at),
        )
        if claim.client_id not in self.allowed_clients:
            return await self._fail_before_attempt(
                claim,
                code="publication_client_not_allowed",
                retryable=False,
            )

        try:
            phase_started_at = time.monotonic()
            publisher = self.publisher_factory(claim.client_id)
            await publisher.preflight()
            _LOGGER.info(
                "exact_telegram_publication_preflight_ok publication_id=%s elapsed_ms=%d",
                claim.publication_id,
                _elapsed_ms(phase_started_at),
            )
            phase_started_at = time.monotonic()
            image_bytes = await self.repository.download_asset(claim)
            _LOGGER.info(
                "exact_telegram_publication_asset_verified publication_id=%s byte_size=%d elapsed_ms=%d",
                claim.publication_id,
                len(image_bytes),
                _elapsed_ms(phase_started_at),
            )
        except TelegramExactError as exc:
            return await self._fail_before_attempt(
                claim,
                code=exc.code,
                retryable=exc.retryable_before_attempt,
            )
        except PublicationRepositoryError as exc:
            return await self._fail_before_attempt(
                claim,
                code=exc.code,
                retryable=exc.retryable,
            )
        except Exception:
            return await self._fail_before_attempt(
                claim,
                code="telegram_publication_preflight_failed",
                retryable=False,
            )

        try:
            request_sha256 = telegram_send_photo_fingerprint(
                public_username=publisher.config.public_username,
                caption=claim.telegram_text,
                image_bytes=image_bytes,
            )
        except Exception:
            return await self._fail_before_attempt(
                claim,
                code="telegram_publication_request_invalid",
                retryable=False,
            )
        try:
            phase_started_at = time.monotonic()
            await self.repository.mark_attempt(claim, request_sha256)
            _LOGGER.info(
                "exact_telegram_publication_fenced publication_id=%s elapsed_ms=%d",
                claim.publication_id,
                _elapsed_ms(phase_started_at),
            )
        except Exception:
            # The fence may have committed even if its response was lost. Never
            # call Telegram or attempt to clear the lease from this uncertain state.
            return PublicationRunResult(
                ok=False,
                claimed=True,
                publication_id=claim.publication_id,
                status="delivery_unknown",
                error="publication_attempt_fence_unavailable",
            )

        try:
            phase_started_at = time.monotonic()
            receipt = await publisher.send_photo_once(
                image_bytes=image_bytes,
                caption=claim.telegram_text,
            )
            _LOGGER.info(
                "exact_telegram_publication_provider_accepted publication_id=%s message_id=%d elapsed_ms=%d",
                claim.publication_id,
                receipt.message_id,
                _elapsed_ms(phase_started_at),
            )
        except Exception:
            _LOGGER.error(
                "exact_telegram_publication_delivery_unknown publication_id=%s elapsed_ms=%d",
                claim.publication_id,
                _elapsed_ms(phase_started_at),
            )
            try:
                await self.repository.fail(
                    claim,
                    error_code="telegram_delivery_unknown",
                    retryable_before_attempt=False,
                )
            except PublicationRepositoryError:
                pass
            return PublicationRunResult(
                ok=False,
                claimed=True,
                publication_id=claim.publication_id,
                status="delivery_unknown",
                error="telegram_delivery_unknown",
            )

        try:
            phase_started_at = time.monotonic()
            await self.repository.complete(
                claim,
                request_sha256,
                message_id=receipt.message_id,
                chat_username=receipt.chat_username,
                provider_date=receipt.provider_date,
            )
            _LOGGER.info(
                "exact_telegram_publication_completed publication_id=%s elapsed_ms=%d total_elapsed_ms=%d",
                claim.publication_id,
                _elapsed_ms(phase_started_at),
                _elapsed_ms(run_started_at),
            )
        except Exception:
            # Telegram succeeded. A retry would duplicate the public post, so
            # leave the fenced lease for delivery-unknown reconciliation.
            return PublicationRunResult(
                ok=False,
                claimed=True,
                publication_id=claim.publication_id,
                status="delivery_unknown",
                error="publication_completion_unavailable",
            )
        return PublicationRunResult(
            ok=True,
            claimed=True,
            publication_id=claim.publication_id,
            status="published",
        )


def build_exact_telegram_publication_worker(
    settings: PublicationSettings,
    *,
    repository: PublicationRepository | None = None,
    publisher_factory: PublisherFactory | None = None,
) -> ExactTelegramPublicationWorker:
    effective_repository = repository or SupabasePublicationRepository(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        workspace_id=settings.workspace_id,
    )
    effective_factory = publisher_factory or (
        lambda client_id: ExactTelegramPublisher(load_telegram_exact_config(
            client_id,
            clients_dir=settings.clients_dir,
        ), send_timeout_seconds=settings.send_timeout_seconds)
    )
    return ExactTelegramPublicationWorker(
        repository=effective_repository,
        publisher_factory=effective_factory,
        allowed_clients=settings.allowed_clients,
        lease_seconds=settings.lease_seconds,
    )


__all__ = [
    "ExactTelegramPublicationWorker",
    "build_exact_telegram_publication_worker",
]
