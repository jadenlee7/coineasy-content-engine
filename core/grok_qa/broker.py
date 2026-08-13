from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from core.grok_qa.models import (
    OFFICIAL_X_HANDLES,
    GrokQaDeliveryResult,
    GrokQaModelResult,
    GrokQaWorkClaim,
)
from core.grok_qa.settings import validate_grok_qa_worker_secret_boundary
from core.grok_qa.worker import GrokQaBrokerError


GROK_QA_DISPATCH_URL = (
    "https://coineasy-newscard.netlify.app/api/grok-qa/dispatch"
)
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_SAFE_ERROR_RE = re.compile(r"^[a-z][a-z0-9_]{2,79}$")
_TOKEN_REUSE_NAMES = (
    "XAI_API_KEY",
    "API_SECRET",
    "STUDIO_ACCESS_TOKEN",
    "STUDIO_AUTOMATION_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "GROK_QA_CONNECTOR_TOKEN",
    "GROK_QA_RELAY_TOKEN",
    "PUBLICATION_WORKER_TOKEN",
    "X_BEARER_TOKEN",
    "TYPEFULLY_API_KEY",
    "TELEGRAM_REVIEW_BOT_TOKEN",
    "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN",
)


def _record(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GrokQaBrokerError(code, retryable=False)
    return value


def _text(value: object, minimum: int, maximum: int, code: str) -> str:
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise GrokQaBrokerError(code, retryable=False)
    return value


def _validated_dispatch_url(value: str) -> str:
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise ValueError("GROK_QA_DISPATCH_URL is invalid") from exc
    expected = urlsplit(GROK_QA_DISPATCH_URL)
    if (
        parsed != expected
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GROK_QA_DISPATCH_URL is outside the allowlist")
    return normalized


def _validated_dispatch_token(
    env: Mapping[str, str],
) -> str:
    value = env.get("GROK_QA_DISPATCH_TOKEN", "")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("GROK_QA_DISPATCH_TOKEN is invalid") from exc
    if (
        not 32 <= len(encoded) <= 512
        or any(character <= 32 or character == 127 for character in encoded)
    ):
        raise ValueError("GROK_QA_DISPATCH_TOKEN is invalid")
    for name in _TOKEN_REUSE_NAMES:
        other = env.get(name, "")
        if other and secrets.compare_digest(value.encode("utf-8"), other.encode("utf-8")):
            raise ValueError("GROK_QA_DISPATCH_TOKEN must be dedicated")
    return value


class HttpGrokQaBroker:
    def __init__(
        self,
        *,
        token: str,
        url: str = GROK_QA_DISPATCH_URL,
        timeout_seconds: float = 30.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.url = _validated_dispatch_url(url)
        self.token = token
        try:
            encoded = token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("invalid Grok QA broker token") from exc
        if (
            not 32 <= len(encoded) <= 512
            or any(character <= 32 or character == 127 for character in encoded)
            or not 5 <= timeout_seconds <= 60
        ):
            raise ValueError("invalid Grok QA broker configuration")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> "HttpGrokQaBroker":
        env = os.environ if environ is None else environ
        validate_grok_qa_worker_secret_boundary(env)
        url = _validated_dispatch_url(
            env.get("GROK_QA_DISPATCH_URL", GROK_QA_DISPATCH_URL)
        )
        token = _validated_dispatch_token(env)
        raw_timeout = env.get("GROK_QA_BROKER_TIMEOUT_SECONDS", "30").strip()
        try:
            timeout = int(raw_timeout)
        except ValueError as exc:
            raise ValueError(
                "GROK_QA_BROKER_TIMEOUT_SECONDS must be an integer"
            ) from exc
        return cls(
            token=token,
            url=url,
            timeout_seconds=timeout,
            transport=transport,
        )

    async def _request(self, body: dict[str, object]) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=body,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GrokQaBrokerError(
                "grok_qa_broker_unavailable", retryable=True
            ) from exc
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise GrokQaBrokerError(
                "grok_qa_broker_response_too_large", retryable=False
            )
        if not 200 <= response.status_code < 300:
            code = "grok_qa_broker_request_failed"
            try:
                raw = response.json()
                candidate = raw.get("error") if isinstance(raw, dict) else None
                if isinstance(candidate, str) and _SAFE_ERROR_RE.fullmatch(candidate):
                    code = candidate
            except ValueError:
                pass
            raise GrokQaBrokerError(
                code,
                retryable=response.status_code in {408, 409, 425, 429, 500, 502, 503, 504},
            )
        try:
            return _record(response.json(), "grok_qa_broker_invalid_response")
        except ValueError as exc:
            raise GrokQaBrokerError(
                "grok_qa_broker_invalid_response", retryable=False
            ) from exc

    async def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_clients: tuple[str, ...],
        canary_content_version_id: Optional[str],
    ) -> Optional[GrokQaWorkClaim]:
        if canary_content_version_id is not None:
            try:
                parsed_canary_id = uuid.UUID(canary_content_version_id)
            except (AttributeError, ValueError) as exc:
                raise ValueError(
                    "invalid Grok QA canary content version"
                ) from exc
            if (
                parsed_canary_id.int == 0
                or str(parsed_canary_id) != canary_content_version_id
            ):
                raise ValueError("invalid Grok QA canary content version")
        raw = await self._request({
            "action": "claim",
            "worker_id": worker_id,
            "lease_seconds": lease_seconds,
            "allowed_clients": list(allowed_clients),
            "canary_content_version_id": canary_content_version_id,
        })
        if (
            raw.get("schema_version") != "1.0"
            or raw.get("mode") != "official_x_grok_qa_dispatch"
        ):
            raise GrokQaBrokerError(
                "grok_qa_broker_invalid_response", retryable=False
            )
        job_value = raw.get("job")
        if job_value is None:
            return None
        job = _record(job_value, "grok_qa_broker_invalid_response")
        package = _record(
            raw.get("review_package"), "grok_qa_broker_invalid_response"
        )
        if (
            job.get("status") != "claimed"
            or job.get("claim_granted") is not True
            or job.get("client_id") not in allowed_clients
            or package.get("content_item_id") != job.get("content_item_id")
            or package.get("content_version_id") != job.get("content_version_id")
            or package.get("client_id") != job.get("client_id")
            or package.get("content_kind") != job.get("content_kind")
            or (
                canary_content_version_id is not None
                and job.get("content_version_id")
                != canary_content_version_id
            )
        ):
            raise GrokQaBrokerError(
                "grok_qa_broker_identity_mismatch", retryable=False
            )
        expected_handle = OFFICIAL_X_HANDLES.get(str(job.get("client_id")))
        if (
            not expected_handle
            or str(job.get("source_author_handle", "")).lstrip("@").lower()
            != expected_handle.lower()
            or job.get("source_event_type") not in {
                "official_x_review_draft_completed",
                "origintrail_batch_review_pack_materialized",
            }
        ):
            raise GrokQaBrokerError(
                "grok_qa_broker_source_mismatch", retryable=False
            )
        review_text = json.dumps(
            package,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        staged_fields = (
            job.get("verdict"),
            job.get("verdict_sha256"),
            job.get("model"),
            job.get("prompt_version"),
            job.get("input_sha256"),
            job.get("banner_sha256"),
            job.get("provider_response_id"),
            job.get("cost_in_usd_ticks"),
            job.get("x_search_citations"),
            job.get("x_search_calls"),
        )
        if any(value is not None for value in staged_fields) and not all(
            value is not None for value in staged_fields
        ):
            raise GrokQaBrokerError(
                "grok_qa_broker_staged_result_incomplete", retryable=False
            )
        provider_call_required = job.get("provider_call_required")
        staged_result_available = all(
            value is not None for value in staged_fields
        )
        if (
            not isinstance(provider_call_required, bool)
            or provider_call_required == staged_result_available
        ):
            raise GrokQaBrokerError(
                "grok_qa_broker_provider_call_state_invalid",
                retryable=False,
            )
        image: bytes | None = None
        image_sha256: str | None = None
        banner_value = raw.get("banner_image")
        if banner_value is not None:
            banner = _record(
                banner_value, "grok_qa_broker_image_invalid"
            )
            if (
                banner.get("mime_type") != "image/png"
                or not isinstance(banner.get("data"), str)
            ):
                raise GrokQaBrokerError(
                    "grok_qa_broker_image_invalid", retryable=False
                )
            try:
                image = base64.b64decode(banner["data"], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise GrokQaBrokerError(
                    "grok_qa_broker_image_invalid", retryable=False
                ) from exc
            banner_meta = _record(
                package.get("banner"), "grok_qa_broker_image_invalid"
            )
            image_sha256_value = banner_meta.get("sha256")
            if (
                not isinstance(image_sha256_value, str)
                or hashlib.sha256(image).hexdigest() != image_sha256_value
            ):
                raise GrokQaBrokerError(
                    "grok_qa_broker_image_invalid", retryable=False
                )
            image_sha256 = image_sha256_value
            if (
                job.get("banner_sha256") is not None
                and job.get("banner_sha256") != image_sha256
            ):
                raise GrokQaBrokerError(
                    "grok_qa_broker_image_invalid", retryable=False
                )
        elif not all(value is not None for value in staged_fields):
            raise GrokQaBrokerError(
                "grok_qa_broker_image_invalid", retryable=False
            )
        staged_result = None
        if all(value is not None for value in staged_fields):
            try:
                staged_result = GrokQaModelResult.model_validate({
                    "provider_response_id": job["provider_response_id"],
                    "model": job["model"],
                    "cost_in_usd_ticks": job["cost_in_usd_ticks"],
                    "input_sha256": job["input_sha256"],
                    "x_search_performed": True,
                    "x_search_citations": job["x_search_citations"],
                    "x_search_calls": job["x_search_calls"],
                    "verdict": job["verdict"],
                })
            except ValidationError as exc:
                raise GrokQaBrokerError(
                    "grok_qa_broker_staged_result_invalid", retryable=False
                ) from exc
        try:
            return GrokQaWorkClaim.model_validate({
                "content_item_id": job.get("content_item_id"),
                "content_version_id": job.get("content_version_id"),
                "client_id": job.get("client_id"),
                "content_kind": job.get("content_kind"),
                "title": package.get("title"),
                "source_url": job.get("source_url"),
                "source_published_at": job.get("source_published_at"),
                "review_text": review_text,
                "image_png": image,
                "image_sha256": image_sha256,
                "attempt": job.get("attempts"),
                "max_attempts": job.get("max_attempts"),
                "provider_call_required": provider_call_required,
                "staged_result": staged_result,
                "staged_verdict_sha256": job.get("verdict_sha256"),
                "staged_prompt_version": job.get("prompt_version"),
            })
        except ValidationError as exc:
            raise GrokQaBrokerError(
                "grok_qa_broker_claim_invalid", retryable=False
            ) from exc

    async def mark_provider_attempt(
        self,
        *,
        claim: GrokQaWorkClaim,
        worker_id: str,
        input_sha256: str,
        banner_sha256: str,
        model: str,
        prompt_version: str,
    ) -> bool:
        if model != "grok-4.5" or prompt_version != "official-x-grok-qa@1":
            raise GrokQaBrokerError(
                "grok_qa_provider_identity_invalid", retryable=False
            )
        raw = await self._request({
            "action": "mark_provider_attempt",
            "content_item_id": claim.content_item_id,
            "content_version_id": claim.content_version_id,
            "worker_id": worker_id,
            "input_sha256": input_sha256,
            "banner_sha256": banner_sha256,
        })
        return (
            raw.get("schema_version") == "1.0"
            and raw.get("authorized_once") is True
            and raw.get("content_item_id") == claim.content_item_id
            and raw.get("content_version_id") == claim.content_version_id
            and raw.get("input_sha256") == input_sha256
            and raw.get("banner_sha256") == banner_sha256
            and isinstance(raw.get("provider_attempt_started_at"), str)
        )

    async def deliver(self, **values: object) -> GrokQaDeliveryResult:
        action = values.pop("action", None)
        supplied_hash = values.pop("verdict_sha256", None)
        if action != "deliver" or not isinstance(supplied_hash, str):
            raise GrokQaBrokerError(
                "grok_qa_broker_delivery_invalid", retryable=False
            )
        staged = await self._request({"action": "stage", **values})
        authoritative_hash = staged.get("verdict_sha256")
        if (
            staged.get("schema_version") != "1.0"
            or staged.get("content_item_id") != values.get("content_item_id")
            or staged.get("content_version_id") != values.get("content_version_id")
            or staged.get("status") != "claimed"
            or not isinstance(authoritative_hash, str)
            or not re.fullmatch(r"[a-f0-9]{64}", authoritative_hash)
            or staged.get("model") != values.get("model")
            or staged.get("prompt_version") != values.get("prompt_version")
            or staged.get("provider_response_id")
            != values.get("provider_response_id")
            or staged.get("input_sha256") != values.get("input_sha256")
            or staged.get("banner_sha256") != values.get("banner_sha256")
            or staged.get("cost_in_usd_ticks")
            != values.get("cost_in_usd_ticks")
            or staged.get("x_search_citations")
            != values.get("x_search_citations")
            or staged.get("x_search_calls") != values.get("x_search_calls")
            or not isinstance(staged.get("reused"), bool)
        ):
            raise GrokQaBrokerError(
                "grok_qa_broker_stage_invalid", retryable=False
            )
        delivered = await self._request({
            "action": "deliver",
            **values,
            "verdict_sha256": authoritative_hash,
        })
        try:
            return GrokQaDeliveryResult.model_validate({
                "accepted": delivered.get("accepted"),
                "duplicate": delivered.get("duplicate"),
                "delivery_status": delivered.get("delivery_status"),
            })
        except ValidationError as exc:
            raise GrokQaBrokerError(
                "grok_qa_broker_delivery_unknown", retryable=False
            ) from exc

    async def fail(
        self,
        *,
        claim: GrokQaWorkClaim,
        worker_id: str,
        error_code: str,
        retryable: bool,
    ) -> None:
        retry_at = (
            (datetime.now(timezone.utc) + timedelta(minutes=1))
            .isoformat()
            .replace("+00:00", "Z")
            if retryable
            else None
        )
        raw = await self._request({
            "action": "fail",
            "content_item_id": claim.content_item_id,
            "content_version_id": claim.content_version_id,
            "worker_id": worker_id,
            "error_code": error_code,
            "retryable": retryable,
            "retry_at": retry_at,
        })
        if (
            raw.get("schema_version") != "1.0"
            or raw.get("content_item_id") != claim.content_item_id
            or raw.get("content_version_id") != claim.content_version_id
            or raw.get("status") not in {
                "pending", "failed", "obsolete", "provider_unknown"
            }
        ):
            raise GrokQaBrokerError(
                "grok_qa_broker_failure_invalid", retryable=False
            )

    async def reconcile(self, *, limit: int = 10) -> dict[str, object]:
        if not 1 <= limit <= 100:
            raise ValueError("Grok QA reconcile limit is invalid")
        raw = await self._request({"action": "reconcile", "limit": limit})
        expected = (
            "reconciled", "pending", "sent", "failed", "obsolete",
            "provider_unknown", "delivery_unknown",
        )
        if raw.get("schema_version") != "1.0" or any(
            not isinstance(raw.get(key), int) or int(raw[key]) < 0
            for key in expected
        ):
            raise GrokQaBrokerError(
                "grok_qa_broker_reconcile_invalid", retryable=False
            )
        return raw


__all__ = [
    "GROK_QA_DISPATCH_URL",
    "HttpGrokQaBroker",
]
