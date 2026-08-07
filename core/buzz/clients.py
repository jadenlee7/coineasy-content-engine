from __future__ import annotations

import re
import uuid
from collections.abc import Mapping

import httpx

from core.buzz.errors import BuzzAdapterError
from core.buzz.models import BuzzDeliveryClaim, BuzzShadowEvent


_HASH = re.compile(r"^[a-f0-9]{64}$")
_SOURCE = re.compile(r"^https://x[.]com/origin_trail/status/[0-9]{1,19}$")
_STATUSES = frozenset(
    {"pending", "claimed", "attempt_started", "delivered", "delivery_unknown", "failed"}
)


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

    async def reconcile(self, *, limit: int) -> dict[str, int]:
        raw = await self._post({"action": "reconcile", "limit": limit})
        workspace = raw.get("workspace_id")
        if not isinstance(workspace, str):
            raise BuzzAdapterError("buzz_delivery_control_invalid_response")
        try:
            uuid.UUID(workspace)
        except ValueError:
            raise BuzzAdapterError(
                "buzz_delivery_control_invalid_response"
            ) from None
        counts: dict[str, int] = {}
        for key in (
            "reconciled_count",
            "pending_count",
            "failed_count",
            "delivery_unknown_count",
        ):
            value = raw.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BuzzAdapterError("buzz_delivery_control_invalid_response")
            counts[key] = value
        return counts
