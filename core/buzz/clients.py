from __future__ import annotations

import hashlib
import json
import re
import secrets
import struct
import uuid
from collections.abc import Mapping

import httpx

from core.buzz.errors import BuzzAdapterError
from core.buzz.models import (
    BuzzAttachment,
    BuzzDeliveryClaim,
    BuzzReviewAcknowledgement,
    BuzzReviewDecision,
    BuzzReviewRecordResult,
    BuzzReviewTarget,
    BuzzShadowEvent,
)


_HASH = re.compile(r"^[a-f0-9]{64}$")
_SOURCE = re.compile(r"^https://x[.]com/origin_trail/status/[0-9]{1,19}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_STATUSES = frozenset(
    {"pending", "claimed", "attempt_started", "delivered", "delivery_unknown", "failed"}
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_BANNER_BYTES = 4 * 1_024 * 1_024
_ACK_TEMPLATE_VERSION = "origintrail-buzz-review-ack@1"
_ACK_MODE = "durable_review_acknowledgement"


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
        attachment_sha256: str,
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
            "attachment_sha256": attachment_sha256,
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
            or raw.get("attachment_sha256") not in (None, attachment_sha256)
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
            attachment_sha256=attachment_sha256,
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


class BuzzReviewControlClient:
    def __init__(self, *, url: str, token: str, transport=None):
        self.url = url
        self.token = token
        self.transport = transport

    async def _post(
        self,
        body: Mapping[str, object],
        *,
        retry_commit_unknown: bool = False,
    ) -> Mapping[str, object]:
        encoded_body = json.dumps(
            dict(body),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        max_attempts = 2 if retry_commit_unknown else 1
        response: httpx.Response | None = None
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=False, transport=self.transport
        ) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.post(
                        self.url,
                        headers={
                            "x-coineasy-buzz-review-key": self.token,
                            "content-type": "application/json",
                        },
                        content=encoded_body,
                    )
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt + 1 < max_attempts:
                        continue
                    raise BuzzAdapterError(
                        "buzz_review_control_unavailable"
                    ) from None
                if (
                    500 <= response.status_code <= 599
                    and attempt + 1 < max_attempts
                ):
                    continue
                break
        if response is None:
            raise BuzzAdapterError("buzz_review_control_unavailable")
        if response.status_code != 200:
            raise BuzzAdapterError("buzz_review_control_unavailable")
        try:
            raw = response.json()
        except ValueError as exc:
            raise BuzzAdapterError("buzz_review_control_invalid_response") from exc
        if not isinstance(raw, Mapping):
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        return raw

    async def first_target(self) -> BuzzReviewTarget | None:
        raw = await self._post({"action": "list", "limit": 1})
        targets = raw.get("targets")
        workspace_id = raw.get("workspace_id")
        if (
            raw.get("schema_version") != "2.0"
            or raw.get("mode") != "publish_intent_review"
            or not isinstance(workspace_id, str)
            or not isinstance(targets, list)
            or len(targets) > 1
        ):
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        try:
            uuid.UUID(workspace_id)
        except ValueError:
            raise BuzzAdapterError("buzz_review_control_invalid_response") from None
        if not targets:
            return None
        target = targets[0]
        if not isinstance(target, Mapping) or set(target) != {
            "job_id",
            "delivery_event_id",
            "channel_id",
            "root_relay_event_id",
            "message_sha256",
            "protocol_version",
            "delivered_at_epoch",
        }:
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        try:
            job_id = str(uuid.UUID(str(target["job_id"])))
            channel_id = str(uuid.UUID(str(target["channel_id"])))
        except (ValueError, AttributeError):
            raise BuzzAdapterError("buzz_review_control_invalid_response") from None
        delivery_event_id = target["delivery_event_id"]
        root_relay_event_id = target["root_relay_event_id"]
        message_sha256 = target["message_sha256"]
        protocol_version = target["protocol_version"]
        delivered_at_epoch = target["delivered_at_epoch"]
        if (
            not isinstance(delivery_event_id, str)
            or not _HASH.fullmatch(delivery_event_id)
            or not isinstance(root_relay_event_id, str)
            or not _HASH.fullmatch(root_relay_event_id)
            or not isinstance(message_sha256, str)
            or not _HASH.fullmatch(message_sha256)
            or protocol_version != "origintrail-buzz-review@2"
            or isinstance(delivered_at_epoch, bool)
            or not isinstance(delivered_at_epoch, int)
            or not 1 <= delivered_at_epoch <= 4_294_967_295
        ):
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        return BuzzReviewTarget(
            workspace_id=workspace_id,
            job_id=job_id,
            delivery_event_id=delivery_event_id,
            channel_id=channel_id,
            root_relay_event_id=root_relay_event_id,
            message_sha256=message_sha256,
            protocol_version=protocol_version,
            delivered_at_epoch=delivered_at_epoch,
        )

    async def record(
        self,
        decision: BuzzReviewDecision,
        *,
        durable_acknowledgement: bool = False,
    ) -> BuzzReviewRecordResult:
        raw = await self._post(
            {
                "action": (
                    "record_with_ack"
                    if durable_acknowledgement
                    else "record"
                ),
                "job_id": decision.target.job_id,
                "delivery_event_id": decision.target.delivery_event_id,
                "channel_id": decision.target.channel_id,
                "root_relay_event_id": decision.target.root_relay_event_id,
                "message_sha256": decision.target.message_sha256,
                "protocol_version": decision.target.protocol_version,
                "decision_event_id": decision.decision_event_id,
                "reviewer_pubkey": decision.reviewer_pubkey,
                "decision": decision.decision,
                "reason": decision.reason,
                "command_sha256": decision.command_sha256,
                "command_created_at_epoch": decision.command_created_at_epoch,
            },
            retry_commit_unknown=True,
        )
        expected_keys = {
            "schema_version", "mode", "workspace_id", "job_id",
            "delivery_event_id", "channel_id", "root_relay_event_id",
            "message_sha256", "protocol_version", "decision_event_id",
            "reviewer_pubkey", "decision", "reason", "command_sha256",
            "command_created_at_epoch", "reused",
        }
        if durable_acknowledgement:
            expected_keys.add("acknowledgement_status")
        if (
            set(raw) != expected_keys
            or raw.get("schema_version") != "2.0"
            or raw.get("mode") != "publish_intent_review"
            or raw.get("workspace_id") != decision.target.workspace_id
            or raw.get("job_id") != decision.target.job_id
            or raw.get("delivery_event_id") != decision.target.delivery_event_id
            or raw.get("channel_id") != decision.target.channel_id
            or raw.get("root_relay_event_id") != decision.target.root_relay_event_id
            or raw.get("message_sha256") != decision.target.message_sha256
            or raw.get("protocol_version") != decision.target.protocol_version
            or raw.get("decision_event_id") != decision.decision_event_id
            or raw.get("reviewer_pubkey") != decision.reviewer_pubkey
            or raw.get("decision") != decision.decision
            or raw.get("reason") != decision.reason
            or raw.get("command_sha256") != decision.command_sha256
            or raw.get("command_created_at_epoch")
            != decision.command_created_at_epoch
            or not isinstance(raw.get("reused"), bool)
        ):
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        acknowledgement_status = raw.get("acknowledgement_status")
        if durable_acknowledgement:
            if acknowledgement_status not in _STATUSES:
                raise BuzzAdapterError("buzz_review_control_invalid_response")
        elif acknowledgement_status is not None:
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        return BuzzReviewRecordResult(
            reused=bool(raw["reused"]),
            acknowledgement_status=(
                str(acknowledgement_status)
                if durable_acknowledgement
                else "disabled"
            ),
        )

    @staticmethod
    def _acknowledgement(
        raw: object,
        *,
        workspace_id: str,
    ) -> BuzzReviewAcknowledgement:
        if not isinstance(raw, Mapping) or set(raw) != {
            "job_id",
            "channel_id",
            "root_relay_event_id",
            "decision_event_id",
            "decision",
            "reason",
            "command_created_at_epoch",
            "template_version",
            "message",
            "status",
            "claim_granted",
            "reused",
            "message_sha256",
            "request_sha256",
            "delivery_started_at_epoch",
            "relay_event_id",
        }:
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        try:
            job_id = str(uuid.UUID(str(raw["job_id"])))
            channel_id = str(uuid.UUID(str(raw["channel_id"])))
        except (ValueError, AttributeError):
            raise BuzzAdapterError(
                "buzz_review_control_invalid_response"
            ) from None
        root_relay_event_id = raw["root_relay_event_id"]
        decision_event_id = raw["decision_event_id"]
        decision = raw["decision"]
        reason = raw["reason"]
        command_created_at_epoch = raw["command_created_at_epoch"]
        message = raw["message"]
        status = raw["status"]
        message_sha256 = raw["message_sha256"]
        request_sha256 = raw["request_sha256"]
        delivery_started_at_epoch = raw["delivery_started_at_epoch"]
        relay_event_id = raw["relay_event_id"]
        reason_valid = (
            decision == "approved" and reason is None
        ) or (
            decision == "changes_requested"
            and isinstance(reason, str)
            and reason == reason.strip()
            and 1 <= len(reason) <= 500
            and len(reason.encode("utf-8")) <= 1_500
            and _CONTROL.search(reason) is None
        )
        try:
            message_size = len(message.encode("utf-8"))
        except (AttributeError, UnicodeEncodeError):
            message_size = 0
        hashes_valid = (
            isinstance(message_sha256, str)
            and _HASH.fullmatch(message_sha256) is not None
            and (
                request_sha256 is None
                or (
                    isinstance(request_sha256, str)
                    and _HASH.fullmatch(request_sha256) is not None
                )
            )
            and (
                relay_event_id is None
                or (
                    isinstance(relay_event_id, str)
                    and _HASH.fullmatch(relay_event_id) is not None
                )
            )
        )
        if (
            not _HASH.fullmatch(str(root_relay_event_id))
            or not _HASH.fullmatch(str(decision_event_id))
            or not reason_valid
            or isinstance(command_created_at_epoch, bool)
            or not isinstance(command_created_at_epoch, int)
            or not 1 <= command_created_at_epoch <= 4_294_967_295
            or raw["template_version"] != _ACK_TEMPLATE_VERSION
            or not isinstance(message, str)
            or not 1 <= message_size <= 1_024
            or "@" in message
            or "nostr:npub1" in message.lower()
            or status not in _STATUSES
            or not isinstance(raw["claim_granted"], bool)
            or not isinstance(raw["reused"], bool)
            or not hashes_valid
            or (
                delivery_started_at_epoch is not None
                and (
                    isinstance(delivery_started_at_epoch, bool)
                    or not isinstance(delivery_started_at_epoch, int)
                    or not 1 <= delivery_started_at_epoch <= 4_294_967_295
                )
            )
            or (
                status in {"attempt_started", "delivered", "delivery_unknown"}
                and (
                    message_sha256 is None
                    or request_sha256 is None
                    or delivery_started_at_epoch is None
                )
            )
            or (status == "delivered") != (relay_event_id is not None)
        ):
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        return BuzzReviewAcknowledgement(
            workspace_id=workspace_id,
            job_id=job_id,
            channel_id=channel_id,
            root_relay_event_id=str(root_relay_event_id),
            decision_event_id=str(decision_event_id),
            decision=str(decision),
            reason=reason if isinstance(reason, str) else None,
            command_created_at_epoch=command_created_at_epoch,
            template_version=_ACK_TEMPLATE_VERSION,
            message=message,
            status=str(status),
            claim_granted=bool(raw["claim_granted"]),
            reused=bool(raw["reused"]),
            message_sha256=(
                str(message_sha256) if message_sha256 is not None else None
            ),
            request_sha256=(
                str(request_sha256) if request_sha256 is not None else None
            ),
            delivery_started_at_epoch=delivery_started_at_epoch,
            relay_event_id=(
                str(relay_event_id) if relay_event_id is not None else None
            ),
        )

    @staticmethod
    def _ack_envelope(
        raw: Mapping[str, object],
        *,
        fields: frozenset[str],
    ) -> str:
        workspace_id = raw.get("workspace_id")
        if (
            set(raw) != {"schema_version", "mode", "workspace_id", *fields}
            or raw.get("schema_version") != "1.0"
            or raw.get("mode") != _ACK_MODE
            or not isinstance(workspace_id, str)
        ):
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        try:
            return str(uuid.UUID(workspace_id))
        except ValueError:
            raise BuzzAdapterError(
                "buzz_review_control_invalid_response"
            ) from None

    async def claim_acknowledgement(
        self,
        *,
        job_id: str | None,
        worker_id: str,
        lease_seconds: int,
    ) -> BuzzReviewAcknowledgement | None:
        raw = await self._post({
            "action": "ack_claim",
            "job_id": job_id,
            "worker_id": worker_id,
            "lease_seconds": lease_seconds,
        })
        workspace_id = self._ack_envelope(
            raw, fields=frozenset({"acknowledgement"})
        )
        acknowledgement = raw.get("acknowledgement")
        if acknowledgement is None:
            return None
        parsed = self._acknowledgement(
            acknowledgement,
            workspace_id=workspace_id,
        )
        if job_id is not None and parsed.job_id != job_id:
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        return parsed

    async def mark_acknowledgement_attempt(
        self,
        job_id: str,
        *,
        worker_id: str,
        message_sha256: str,
        request_sha256: str,
    ) -> bool:
        raw = await self._post({
            "action": "ack_attempt",
            "job_id": job_id,
            "worker_id": worker_id,
            "message_sha256": message_sha256,
            "request_sha256": request_sha256,
        })
        self._ack_envelope(raw, fields=frozenset({
            "job_id", "status", "message_sha256", "request_sha256",
            "authorized_once", "reused",
        }))
        if (
            raw.get("job_id") != job_id
            or raw.get("status") != "attempt_started"
            or raw.get("message_sha256") != message_sha256
            or raw.get("request_sha256") != request_sha256
            or not isinstance(raw.get("authorized_once"), bool)
            or not isinstance(raw.get("reused"), bool)
        ):
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        return raw["authorized_once"] is True and raw["reused"] is False

    async def complete_acknowledgement(
        self,
        job_id: str,
        *,
        worker_id: str,
        request_sha256: str,
        relay_event_id: str,
        reconciled: bool,
    ) -> None:
        raw = await self._post(
            {
                "action": "ack_complete",
                "job_id": job_id,
                "worker_id": worker_id,
                "request_sha256": request_sha256,
                "relay_event_id": relay_event_id,
                "reconciled": reconciled,
            },
            retry_commit_unknown=True,
        )
        self._ack_envelope(raw, fields=frozenset({
            "job_id", "status", "request_sha256", "relay_event_id", "reused",
        }))
        if (
            raw.get("job_id") != job_id
            or raw.get("status") != "delivered"
            or raw.get("request_sha256") != request_sha256
            or raw.get("relay_event_id") != relay_event_id
            or not isinstance(raw.get("reused"), bool)
        ):
            raise BuzzAdapterError("buzz_review_control_invalid_response")

    async def fail_acknowledgement(
        self,
        job_id: str,
        *,
        worker_id: str,
        error_code: str,
        retryable_before_attempt: bool,
    ) -> str:
        raw = await self._post({
            "action": "ack_fail",
            "job_id": job_id,
            "worker_id": worker_id,
            "error_code": error_code,
            "retryable_before_attempt": retryable_before_attempt,
        })
        self._ack_envelope(
            raw, fields=frozenset({"job_id", "status", "reused"})
        )
        if (
            raw.get("job_id") != job_id
            or raw.get("status") not in _STATUSES
            or not isinstance(raw.get("reused"), bool)
        ):
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        return str(raw["status"])

    async def reconcile_acknowledgements(self, *, limit: int) -> dict[str, int]:
        raw = await self._post({"action": "ack_reconcile", "limit": limit})
        self._ack_envelope(raw, fields=frozenset({
            "reconciled_count", "pending_count", "failed_count",
            "delivery_unknown_count",
        }))
        counts: dict[str, int] = {}
        for key in (
            "reconciled_count",
            "pending_count",
            "failed_count",
            "delivery_unknown_count",
        ):
            value = raw.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BuzzAdapterError("buzz_review_control_invalid_response")
            counts[key] = value
        return counts

    async def first_unknown_acknowledgement(
        self,
    ) -> BuzzReviewAcknowledgement | None:
        raw = await self._post({"action": "ack_unknown", "limit": 1})
        workspace_id = self._ack_envelope(
            raw, fields=frozenset({"acknowledgements"})
        )
        acknowledgements = raw.get("acknowledgements")
        if not isinstance(acknowledgements, list) or len(acknowledgements) > 1:
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        if not acknowledgements:
            return None
        parsed = self._acknowledgement(
            acknowledgements[0],
            workspace_id=workspace_id,
        )
        if parsed.status != "delivery_unknown" or parsed.claim_granted:
            raise BuzzAdapterError("buzz_review_control_invalid_response")
        return parsed
