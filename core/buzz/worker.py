from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Protocol
from urllib.parse import urlsplit

from core.buzz.cli import (
    BuzzCliConfig,
    BuzzCliError,
    BuzzCliPublisher,
    buzz_message_fingerprints,
    format_origintrail_message,
)
from core.buzz.errors import BuzzAdapterError
from core.buzz.models import BuzzAttachment, BuzzDeliveryRunResult, BuzzShadowEvent
from core.buzz.settings import BuzzDeliverySettings


class ShadowReader(Protocol):
    async def first_event(self) -> BuzzShadowEvent | None: ...
    async def banner(self, event: BuzzShadowEvent) -> BuzzAttachment: ...


class DeliveryControl(Protocol):
    async def claim(self, event: BuzzShadowEvent, **kwargs): ...
    async def mark_attempt(self, event_id: str, **kwargs) -> bool: ...
    async def complete(self, event_id: str, **kwargs) -> None: ...
    async def fail(self, event_id: str, **kwargs) -> str: ...
    async def reconcile(self, **kwargs) -> dict[str, int]: ...


class RelayPublisher(Protocol):
    async def preflight(self) -> None: ...
    async def send_once(self, message: str, attachment: BuzzAttachment): ...


class OriginTrailBuzzDeliveryWorker:
    def __init__(
        self,
        *,
        shadow: ShadowReader,
        control: DeliveryControl,
        publisher: RelayPublisher,
        studio_origin: str,
        relay_url: str,
        channel_id: str,
        lease_seconds: int = 180,
        worker_id: str | None = None,
        reconcile_limit: int = 25,
    ):
        if not 180 <= lease_seconds <= 600:
            raise ValueError("Buzz delivery lease must be between 180 and 600 seconds")
        if not 1 <= reconcile_limit <= 100:
            raise ValueError("Buzz reconcile limit must be between 1 and 100")
        self.shadow = shadow
        self.control = control
        self.publisher = publisher
        self.studio_origin = studio_origin.rstrip("/")
        self.relay_url = relay_url.rstrip("/")
        self.channel_id = str(uuid.UUID(channel_id))
        self.lease_seconds = lease_seconds
        self.worker_id = worker_id or f"origintrail-buzz:{uuid.uuid4()}"
        self.reconcile_limit = reconcile_limit

    async def _fail_before_attempt(
        self, event_id: str, *, code: str, retryable: bool
    ) -> BuzzDeliveryRunResult:
        try:
            status = await self.control.fail(
                event_id,
                worker_id=self.worker_id,
                error_code=code,
                retryable_before_attempt=retryable,
            )
        except Exception:
            return BuzzDeliveryRunResult(
                ok=False,
                claimed=True,
                status="failed",
                event_id=event_id,
                error="buzz_delivery_failure_record_unavailable",
            )
        return BuzzDeliveryRunResult(
            ok=False,
            claimed=True,
            status=status,
            event_id=event_id,
            error=code,
        )

    async def _reconcile_leases(self) -> dict[str, object]:
        # Best-effort: this is the only caller of the server-side reconcile
        # transition, so every run must attempt it — a receipt stuck in
        # claimed/attempt_started past its lease is otherwise never surfaced.
        # A reconcile fault must not block the delivery decision below.
        try:
            counts = await self.control.reconcile(limit=self.reconcile_limit)
        except Exception:
            return {"ok": False, "error": "buzz_delivery_reconcile_unavailable"}
        return {"ok": True, **counts}

    async def run_once(self) -> BuzzDeliveryRunResult:
        reconcile = await self._reconcile_leases()
        result = await self._deliver_once()
        return replace(result, reconcile=reconcile)

    async def _deliver_once(self) -> BuzzDeliveryRunResult:
        try:
            event = await self.shadow.first_event()
        except BuzzAdapterError as exc:
            return BuzzDeliveryRunResult(
                ok=False, claimed=False, status="failed", error=exc.code
            )
        if event is None:
            return BuzzDeliveryRunResult(ok=True, claimed=False, status="idle")
        try:
            attachment = await self.shadow.banner(event)
        except BuzzAdapterError as exc:
            return BuzzDeliveryRunResult(
                ok=False,
                claimed=False,
                status="failed",
                event_id=event.event_id,
                error=exc.code,
            )
        try:
            message = format_origintrail_message(
                event, studio_origin=self.studio_origin
            )
            message_sha, request_sha = buzz_message_fingerprints(
                relay_url=self.relay_url,
                channel_id=self.channel_id,
                message=message,
                attachment=attachment,
            )
        except BuzzCliError as exc:
            return BuzzDeliveryRunResult(
                ok=False,
                claimed=False,
                status="failed",
                event_id=event.event_id,
                error=exc.code,
            )
        try:
            claim = await self.control.claim(
                event,
                channel_id=self.channel_id,
                message_sha256=message_sha,
                request_sha256=request_sha,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        except BuzzAdapterError as exc:
            return BuzzDeliveryRunResult(
                ok=False,
                claimed=False,
                status="failed",
                event_id=event.event_id,
                error=exc.code,
            )
        if not claim.claim_granted:
            return BuzzDeliveryRunResult(
                ok=claim.status == "delivered",
                claimed=False,
                status=claim.status,
                event_id=event.event_id,
            )

        try:
            await self.publisher.preflight()
        except BuzzCliError as exc:
            return await self._fail_before_attempt(
                event.event_id,
                code="buzz_cli_preflight_failed",
                retryable=exc.retryable_before_attempt,
            )
        except Exception:
            return await self._fail_before_attempt(
                event.event_id,
                code="buzz_cli_preflight_failed",
                retryable=False,
            )

        try:
            authorized = await self.control.mark_attempt(
                event.event_id,
                worker_id=self.worker_id,
                request_sha256=request_sha,
            )
        except Exception:
            return BuzzDeliveryRunResult(
                ok=False,
                claimed=True,
                status="delivery_unknown",
                event_id=event.event_id,
                error="buzz_delivery_attempt_fence_unavailable",
            )
        if not authorized:
            return BuzzDeliveryRunResult(
                ok=False,
                claimed=True,
                status="delivery_unknown",
                event_id=event.event_id,
                error="buzz_delivery_attempt_already_started",
            )

        try:
            receipt = await self.publisher.send_once(message, attachment)
        except Exception:
            try:
                await self.control.fail(
                    event.event_id,
                    worker_id=self.worker_id,
                    error_code="buzz_delivery_unknown",
                    retryable_before_attempt=False,
                )
            except Exception:
                pass
            return BuzzDeliveryRunResult(
                ok=False,
                claimed=True,
                status="delivery_unknown",
                event_id=event.event_id,
                error="buzz_delivery_unknown",
            )

        try:
            await self.control.complete(
                event.event_id,
                worker_id=self.worker_id,
                request_sha256=request_sha,
                relay_event_id=receipt.event_id,
            )
        except Exception:
            return BuzzDeliveryRunResult(
                ok=False,
                claimed=True,
                status="delivery_unknown",
                event_id=event.event_id,
                error="buzz_delivery_completion_unavailable",
            )
        return BuzzDeliveryRunResult(
            ok=True,
            claimed=True,
            status="delivered",
            event_id=event.event_id,
        )


def build_origintrail_buzz_delivery_worker(
    settings: BuzzDeliverySettings,
) -> OriginTrailBuzzDeliveryWorker:
    from core.buzz.clients import BuzzDeliveryControlClient, BuzzShadowClient

    shadow = BuzzShadowClient(url=settings.shadow_url, token=settings.shadow_token)
    control = BuzzDeliveryControlClient(
        url=settings.delivery_url, token=settings.delivery_token
    )
    publisher = BuzzCliPublisher(BuzzCliConfig(
        cli_path=settings.cli_path,
        relay_url=settings.relay_url,
        private_key=settings.private_key,
        auth_tag=settings.auth_tag,
        channel_id=settings.channel_id,
    ))
    parsed = urlsplit(settings.shadow_url)
    studio_origin = f"{parsed.scheme}://{parsed.netloc}"
    return OriginTrailBuzzDeliveryWorker(
        shadow=shadow,
        control=control,
        publisher=publisher,
        studio_origin=studio_origin,
        relay_url=settings.relay_url,
        channel_id=settings.channel_id,
        lease_seconds=settings.lease_seconds,
    )


__all__ = ["OriginTrailBuzzDeliveryWorker", "build_origintrail_buzz_delivery_worker"]
