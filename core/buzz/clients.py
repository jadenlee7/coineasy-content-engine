from __future__ import annotations

import hashlib
import re
import secrets
import struct
import uuid
from collections.abc import Mapping

import httpx

from core.buzz.errors import BuzzAdapterError
from core.buzz.models import BuzzAttachment, BuzzDeliveryClaim, BuzzShadowEvent


_HASH = re.compile(r"^[a-f0-9]{64}$")
_SOURCE = re.compile(r"^https://x[.]com/origin_trail/status/[0-9]{1,19}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_STATUSES = frozenset(
    {"pending", "claimed", "attempt_started", "delivered", "delivery_unknown", "failed"}
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_BANNER_BYTES = 4 * 1_024 * 1_024


def _uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise BuzzAdapterError(f"invalid_{name}")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise BuzzAdapterError(f"invalid_{name}") from exc


def _event(raw: object) -> BuzzShadowEvent:
    if not isinstance(raw, Mapping):
        raise BuzzAdapterError("buzz_shadow_invalid_response")
    event_id = raw.get("event_id")
    job_id = _uuid(raw.get("job_id"), "buzz_job_id")
    cost = raw.get("actual_cost_microusd")
    source_url = raw.get("source_url")
    finished_at = raw.get("finished_at")
    headline_ko = raw.get("headline_ko")
    summary_ko = raw.get("summary_ko")
    if (
        not isinstance(event_id, str)
        or not _HASH.fullmatch(event_id)
        or raw.get("event_type") != "origintrail.batch_review_ready.v1"
        or raw.get("review_ref") != f"batch:{job_id}"
        or raw.get("client_id") != "origintrail"
        or raw.get("agent_id") != "origintrail_client_agent"
        or raw.get("workflow_kind") != "official_source_nonurgent_pack"
        or raw.get("result_code") != "needs_review"
        or raw.get("model_tier") not in {"S", "M"}
        or isinstance(cost, bool)
        or not isinstance(cost, int)
        or not 0 <= cost <= 500_000
        or not isinstance(finished_at, str)
        or not 20 <= len(finished_at) <= 40
        or not isinstance(source_url, str)
        or not _SOURCE.fullmatch(source_url)
        or raw.get("studio_review_path") != f"/?batch={job_id}"
        or not isinstance(headline_ko, str)
        or headline_ko.strip() != headline_ko
        or not 1 <= len(headline_ko) <= 120
        or not isinstance(summary_ko, str)
        or summary_ko.strip() != summary_ko
        or not 1 <= len(summary_ko) <= 1_800
        or "@" in headline_ko
        or "@" in summary_ko
        or _CONTROL.search(headline_ko) is not None
        or _CONTROL.search(summary_ko) is not None
    ):
        raise BuzzAdapterError("buzz_shadow_invalid_response")
    return BuzzShadowEvent(
        event_id=event_id,
        event_type="origintrail.batch_review_ready.v1",
        job_id=job_id,
        review_ref=f"batch:{job_id}",
        client_id="origintrail",
        agent_id="origintrail_client_agent",
        workflow_kind="official_source_nonurgent_pack",
        result_code="needs_review",
        model_tier=str(raw["model_tier"]),
        actual_cost_microusd=cost,
        finished_at=finished_at,
        source_url=source_url,
        studio_review_path=f"/?batch={job_id}",
        headline_ko=headline_ko,
        summary_ko=summary_ko,
    )


class BuzzShadowClient:
    def __init__(self, *, url: str, token: str, transport=None):
        self.url = url
        self.token = token
        self.transport = transport

    async def first_event(self) -> BuzzShadowEvent | None:
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=False, transport=self.transport
            ) as client:
                response = await client.get(
                    self.url,
                    params={"limit": "1"},
                    headers={"x-coineasy-buzz-key": self.token},
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise BuzzAdapterError(
                "buzz_shadow_unavailable", retryable_before_attempt=True
            ) from None
        if response.status_code != 200:
            raise BuzzAdapterError(
                "buzz_shadow_unavailable", retryable_before_attempt=True
            )
        try:
            raw = response.json()
        except ValueError as exc:
            raise BuzzAdapterError("buzz_shadow_invalid_response") from exc
        if (
            not isinstance(raw, Mapping)
            or raw.get("schema_version") != "1.0"
            or raw.get("mode") != "shadow_read_only"
            or not isinstance(raw.get("events"), list)
            or len(raw["events"]) > 1
        ):
            raise BuzzAdapterError("buzz_shadow_invalid_response")
        events = raw["events"]
        return None if not events else _event(events[0])

    async def banner(self, event: BuzzShadowEvent) -> BuzzAttachment:
        filename = f"origintrail-review-{event.job_id}.png"
        url = f"{self.url}/{event.job_id}/banner.png"
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=False, transport=self.transport
            ) as client:
                async with client.stream(
                    "GET",
                    url,
                    headers={"x-coineasy-buzz-key": self.token},
                ) as response:
                    if response.status_code != 200:
                        raise BuzzAdapterError(
                            "buzz_banner_unavailable", retryable_before_attempt=True
                        )
                    declared = response.headers.get("content-length")
                    if (
                        response.headers.get("content-type") != "image/png"
                        or response.headers.get("content-disposition")
                        != f'inline; filename="{filename}"'
                        or (
                            declared is not None
                            and (
                                not declared.isdigit()
                                or not 24 <= int(declared) <= _MAX_BANNER_BYTES
                            )
                        )
                    ):
                        raise BuzzAdapterError("buzz_banner_invalid_response")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_BANNER_BYTES:
                            raise BuzzAdapterError("buzz_banner_invalid_response")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    server_sha = response.headers.get(
                        "x-coineasy-content-sha256", ""
                    )
        except BuzzAdapterError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise BuzzAdapterError(
                "buzz_banner_unavailable", retryable_before_attempt=True
            ) from None

        content_sha = hashlib.sha256(content).hexdigest()
        if (
            (declared is not None and len(content) != int(declared))
            or not content.startswith(_PNG_SIGNATURE)
            or content[12:16] != b"IHDR"
            or struct.unpack(">II", content[16:24]) != (1_200, 630)
            or not _HASH.fullmatch(server_sha)
            or not secrets.compare_digest(content_sha, server_sha)
        ):
            raise BuzzAdapterError("buzz_banner_invalid_response")
        return BuzzAttachment(
            filename=filename,
            media_type="image/png",
            content_sha256=content_sha,
            content=content,
        )


class BuzzDeliveryControlClient:
    def __init__(self, *, url: str, token: str, transport=None):
        self.url = url
        self.token = token
        self.transport = transport

    async def _post(self, body: Mapping[str, object]) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=False, transport=self.transport
            ) as client:
                response = await client.post(
                    self.url,
                    headers={
                        "x-coineasy-buzz-delivery-key": self.token,
                        "content-type": "application/json",
                    },
                    json=dict(body),
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise BuzzAdapterError("buzz_delivery_control_unavailable") from None
        if response.status_code != 200:
            raise BuzzAdapterError("buzz_delivery_control_unavailable")
        try:
            raw = response.json()
        except ValueError as exc:
            raise BuzzAdapterError("buzz_delivery_control_invalid_response") from exc
        if not isinstance(raw, Mapping):
            raise BuzzAdapterError("buzz_delivery_control_invalid_response")
        return raw

    async def claim(
        self,
        event: BuzzShadowEvent,
        *,
        channel_id: str,
        message_sha256: str,
        request_sha256: str,
        worker_id: str,
        lease_seconds: int,
    ) -> BuzzDeliveryClaim:
        raw = await self._post({
            "action": "claim",
            "event_id": event.event_id,
            "job_id": event.job_id,
            "channel_id": channel_id,
            "message_sha256": message_sha256,
            "request_sha256": request_sha256,
            "worker_id": worker_id,
            "lease_seconds": lease_seconds,
        })
        status = raw.get("status")
        if (
            raw.get("event_id") != event.event_id
            or raw.get("job_id") != event.job_id
            or raw.get("channel_id") != channel_id
            or raw.get("message_sha256") != message_sha256
            or raw.get("request_sha256") != request_sha256
            or status not in _STATUSES
            or not isinstance(raw.get("claim_granted"), bool)
            or not isinstance(raw.get("reused"), bool)
        ):
            raise BuzzAdapterError("buzz_delivery_control_invalid_response")
        return BuzzDeliveryClaim(
            event_id=event.event_id,
            job_id=event.job_id,
            channel_id=channel_id,
            message_sha256=message_sha256,
            request_sha256=request_sha256,
            status=str(status),
            claim_granted=bool(raw["claim_granted"]),
            reused=bool(raw["reused"]),
        )

    async def mark_attempt(
        self, event_id: str, *, worker_id: str, request_sha256: str
    ) -> bool:
        raw = await self._post({
            "action": "attempt",
            "event_id": event_id,
            "worker_id": worker_id,
            "request_sha256": request_sha256,
        })
        if (
            raw.get("event_id") != event_id
            or raw.get("request_sha256") != request_sha256
            or not isinstance(raw.get("authorized_once"), bool)
            or not isinstance(raw.get("reused"), bool)
        ):
            raise BuzzAdapterError("buzz_delivery_control_invalid_response")
        return raw["authorized_once"] is True and raw["reused"] is False

    async def complete(
        self,
        event_id: str,
        *,
        worker_id: str,
        request_sha256: str,
        relay_event_id: str,
    ) -> None:
        raw = await self._post({
            "action": "complete",
            "event_id": event_id,
            "worker_id": worker_id,
            "request_sha256": request_sha256,
            "relay_event_id": relay_event_id,
        })
        if (
            raw.get("event_id") != event_id
            or raw.get("request_sha256") != request_sha256
            or raw.get("relay_event_id") != relay_event_id
            or raw.get("status") != "delivered"
        ):
            raise BuzzAdapterError("buzz_delivery_control_invalid_response")

    async def fail(
        self,
        event_id: str,
        *,
        worker_id: str,
        error_code: str,
        retryable_before_attempt: bool,
    ) -> str:
        raw = await self._post({
            "action": "fail",
            "event_id": event_id,
            "worker_id": worker_id,
            "error_code": error_code,
            "retryable_before_attempt": retryable_before_attempt,
        })
        if raw.get("event_id") != event_id or raw.get("status") not in _STATUSES:
            raise BuzzAdapterError("buzz_delivery_control_invalid_response")
        return str(raw["status"])
