from __future__ import annotations

import hashlib
import re
import struct
import uuid
from datetime import datetime
from typing import Mapping
from urllib.parse import quote

import httpx

from core.publications.models import (
    ClaimedTelegramPublication,
    PublicationRecoverySummary,
    StoredPng,
)
from core.publications.settings import (
    PUBLICATION_CLIENTS,
    PUBLICATION_TELEGRAM_USERNAMES,
    _supabase_url,
)


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_WORKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_PNG_BYTES = 10 * 1024 * 1024
_FAILURE_CODES = frozenset({
    "publication_client_not_allowed",
    "telegram_publication_config_invalid",
    "telegram_publication_channel_inactive",
    "telegram_publication_credentials_invalid",
    "telegram_publication_target_invalid",
    "telegram_publication_target_mismatch",
    "telegram_preflight_unavailable",
    "telegram_preflight_rejected",
    "telegram_response_invalid",
    "publication_asset_unavailable",
    "publication_asset_invalid",
    "telegram_publication_preflight_failed",
    "telegram_publication_request_invalid",
    "telegram_delivery_unknown",
})


class PublicationRepositoryError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise PublicationRepositoryError(f"invalid_{name}")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise PublicationRepositoryError(f"invalid_{name}") from exc


def _aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not 20 <= len(value) <= 40:
        raise PublicationRepositoryError(f"invalid_{name}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublicationRepositoryError(f"invalid_{name}") from exc
    if parsed.tzinfo is None:
        raise PublicationRepositoryError(f"invalid_{name}")
    return parsed


def _positive_int(value: object, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise PublicationRepositoryError(f"invalid_{name}")
    return value


def _nonnegative_int(value: object, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise PublicationRepositoryError(f"invalid_{name}")
    return value


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read the mandatory PNG IHDR dimensions without decoding the image."""
    if (
        len(data) < 24
        or not data.startswith(_PNG_SIGNATURE)
        or data[12:16] != b"IHDR"
    ):
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width == 0 or height == 0:
        return None
    return width, height


class _SupabasePublicationRpcClient:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        workspace_id: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.supabase_url = _supabase_url(supabase_url)
        if not 32 <= len(service_role_key.strip()) <= 8_192:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY has an invalid length")
        self.service_role_key = service_role_key.strip()
        self.workspace_id = _uuid(workspace_id, "workspace_id")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
        }

    async def _rpc(self, name: str, payload: Mapping[str, object]) -> object:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.supabase_url}/rest/v1/rpc/{name}",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=dict(payload),
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise PublicationRepositoryError(
                "publication_database_unavailable", retryable=True
            ) from None
        if not 200 <= response.status_code < 300:
            raise PublicationRepositoryError(
                "publication_database_rpc_failed",
                retryable=response.status_code in {408, 409, 425, 429, 500, 502, 503, 504},
            )
        try:
            return response.json()
        except ValueError as exc:
            raise PublicationRepositoryError("publication_database_invalid_response") from exc


class SupabasePublicationRepository(_SupabasePublicationRpcClient):

    @staticmethod
    def _worker_id(value: str) -> str:
        if not isinstance(value, str) or not _WORKER_RE.fullmatch(value):
            raise ValueError("worker_id is invalid")
        return value

    async def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ClaimedTelegramPublication | None:
        worker_id = self._worker_id(worker_id)
        if not 180 <= lease_seconds <= 600:
            raise ValueError("lease_seconds must be between 180 and 600")
        raw = await self._rpc("claim_exact_telegram_publication_job", {
            "target_workspace_id": self.workspace_id,
            "target_worker_id": worker_id,
            "target_lease_seconds": lease_seconds,
        })
        if raw is None:
            return None
        if not isinstance(raw, Mapping) or not isinstance(raw.get("asset"), Mapping):
            raise PublicationRepositoryError("invalid_publication_claim_response")
        asset_raw = raw["asset"]
        client_id = raw.get("client_id")
        telegram_text = raw.get("telegram_text")
        telegram_public_username = raw.get("telegram_public_username")
        attempts = raw.get("attempts")
        max_attempts = raw.get("max_attempts")
        if (
            client_id not in PUBLICATION_CLIENTS
            or telegram_public_username
                != PUBLICATION_TELEGRAM_USERNAMES.get(str(client_id))
            or raw.get("locked_by") != worker_id
            or isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or attempts < 1
            or max_attempts < attempts
            or not isinstance(telegram_text, str)
            or not telegram_text.strip()
            or len(telegram_text) > 1_024
        ):
            raise PublicationRepositoryError("invalid_publication_claim_response")
        asset_id = _uuid(asset_raw.get("asset_id"), "publication_asset_id")
        byte_size = _positive_int(asset_raw.get("byte_size"), "asset_byte_size", _MAX_PNG_BYTES)
        width = _positive_int(asset_raw.get("width"), "asset_width", 10_000)
        height = _positive_int(asset_raw.get("height"), "asset_height", 10_000)
        sha256 = asset_raw.get("sha256")
        storage_path = asset_raw.get("storage_path")
        expected_path = f"{self.workspace_id}/{client_id}/{asset_id}/news-card.png"
        if (
            asset_raw.get("storage_bucket") != "content-studio"
            or asset_raw.get("mime_type") != "image/png"
            or not isinstance(sha256, str)
            or not _SHA256_RE.fullmatch(sha256)
            or storage_path != expected_path
        ):
            raise PublicationRepositoryError("invalid_publication_claim_response")
        return ClaimedTelegramPublication(
            job_id=_uuid(raw.get("job_id"), "publication_job_id"),
            publication_id=_uuid(raw.get("publication_id"), "publication_id"),
            content_item_id=_uuid(raw.get("content_item_id"), "content_item_id"),
            content_version_id=_uuid(raw.get("content_version_id"), "content_version_id"),
            approval_id=_uuid(raw.get("approval_id"), "approval_id"),
            client_id=str(client_id),
            attempts=attempts,
            max_attempts=max_attempts,
            locked_by=worker_id,
            lease_expires_at=_aware_datetime(raw.get("lease_expires_at"), "lease_expires_at"),
            telegram_text=telegram_text,
            asset=StoredPng(
                asset_id=asset_id,
                storage_bucket="content-studio",
                storage_path=expected_path,
                mime_type="image/png",
                byte_size=byte_size,
                sha256=sha256,
                width=width,
                height=height,
            ),
        )

    async def download_asset(self, claim: ClaimedTelegramPublication) -> bytes:
        asset = claim.asset
        encoded_path = "/".join(quote(part, safe="") for part in asset.storage_path.split("/"))
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "GET",
                    f"{self.supabase_url}/storage/v1/object/{asset.storage_bucket}/{encoded_path}",
                    headers=self._headers(),
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise PublicationRepositoryError(
                            "publication_asset_unavailable",
                            retryable=response.status_code in {408, 425, 429, 500, 502, 503, 504},
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    declared = response.headers.get("content-length")
                    if content_type != "image/png":
                        raise PublicationRepositoryError("publication_asset_invalid")
                    if declared is not None:
                        try:
                            if int(declared) != asset.byte_size:
                                raise PublicationRepositoryError("publication_asset_invalid")
                        except ValueError as exc:
                            raise PublicationRepositoryError("publication_asset_invalid") from exc
                    output = bytearray()
                    async for chunk in response.aiter_bytes():
                        output.extend(chunk)
                        if len(output) > _MAX_PNG_BYTES:
                            raise PublicationRepositoryError("publication_asset_invalid")
        except PublicationRepositoryError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise PublicationRepositoryError(
                "publication_asset_unavailable", retryable=True
            ) from None
        data = bytes(output)
        if (
            len(data) != asset.byte_size
            or _png_dimensions(data) != (asset.width, asset.height)
            or hashlib.sha256(data).hexdigest() != asset.sha256
        ):
            raise PublicationRepositoryError("publication_asset_invalid")
        return data

    async def mark_attempt(
        self,
        claim: ClaimedTelegramPublication,
        request_sha256: str,
    ) -> None:
        if not _SHA256_RE.fullmatch(request_sha256):
            raise ValueError("request_sha256 is invalid")
        raw = await self._rpc("mark_exact_telegram_attempt_started", {
            "target_job_id": claim.job_id,
            "target_worker_id": claim.locked_by,
            "target_request_sha256": request_sha256,
        })
        if (
            not isinstance(raw, Mapping)
            or _uuid(raw.get("job_id"), "publication_job_id") != claim.job_id
            or _uuid(raw.get("publication_id"), "publication_id")
                != claim.publication_id
            or raw.get("request_sha256") != request_sha256
            or raw.get("status") != "publishing"
            or raw.get("attempt_started") is not True
            or raw.get("reused") is not False
        ):
            raise PublicationRepositoryError("invalid_publication_attempt_response")

    async def complete(
        self,
        claim: ClaimedTelegramPublication,
        request_sha256: str,
        *,
        message_id: int,
        chat_username: str,
        provider_date: datetime,
    ) -> None:
        raw = await self._rpc("complete_exact_telegram_publication_job", {
            "target_job_id": claim.job_id,
            "target_worker_id": claim.locked_by,
            "target_request_sha256": request_sha256,
            "target_message_id": message_id,
            "target_chat_username": chat_username,
            "target_provider_date": provider_date.isoformat(),
        })
        if (
            not isinstance(raw, Mapping)
            or _uuid(raw.get("job_id"), "publication_job_id") != claim.job_id
            or _uuid(raw.get("publication_id"), "publication_id") != claim.publication_id
            or raw.get("status") != "published"
            or not isinstance(raw.get("reused"), bool)
        ):
            raise PublicationRepositoryError("invalid_publication_completion_response")

    async def fail(
        self,
        claim: ClaimedTelegramPublication,
        *,
        error_code: str,
        retryable_before_attempt: bool,
    ) -> str:
        if error_code not in _FAILURE_CODES:
            error_code = "telegram_publication_request_invalid"
        raw = await self._rpc("fail_exact_telegram_publication_job", {
            "target_job_id": claim.job_id,
            "target_worker_id": claim.locked_by,
            "target_error_code": error_code,
            "target_retryable_before_attempt": retryable_before_attempt,
        })
        status = raw.get("status") if isinstance(raw, Mapping) else None
        job_status = raw.get("job_status") if isinstance(raw, Mapping) else None
        if (
            not isinstance(raw, Mapping)
            or _uuid(raw.get("job_id"), "publication_job_id") != claim.job_id
            or _uuid(raw.get("publication_id"), "publication_id")
                != claim.publication_id
            or status not in {"queued", "failed", "delivery_unknown"}
            or job_status != ("retrying" if status == "queued" else "failed")
            or not isinstance(raw.get("reused"), bool)
        ):
            raise PublicationRepositoryError("invalid_publication_failure_response")
        return str(status)


class SupabasePublicationRecoveryRepository(_SupabasePublicationRpcClient):
    async def reconcile_expired_leases(
        self,
        *,
        limit: int = 100,
    ) -> PublicationRecoverySummary:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("recovery limit must be between 1 and 100")
        raw = await self._rpc(
            "reconcile_expired_exact_telegram_publication_leases",
            {
                "target_workspace_id": self.workspace_id,
                "target_limit": limit,
            },
        )
        expected_keys = {
            "workspace_id",
            "reconciled_count",
            "retrying_count",
            "failed_count",
            "delivery_unknown_count",
        }
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            raise PublicationRepositoryError("invalid_publication_recovery_response")
        workspace_id = _uuid(raw.get("workspace_id"), "workspace_id")
        reconciled = _nonnegative_int(
            raw.get("reconciled_count"), "reconciled_count", limit
        )
        retrying = _nonnegative_int(
            raw.get("retrying_count"), "retrying_count", limit
        )
        failed = _nonnegative_int(raw.get("failed_count"), "failed_count", limit)
        delivery_unknown = _nonnegative_int(
            raw.get("delivery_unknown_count"), "delivery_unknown_count", limit
        )
        if (
            raw.get("workspace_id") != workspace_id
            or workspace_id != self.workspace_id
            or retrying + failed + delivery_unknown != reconciled
        ):
            raise PublicationRepositoryError("invalid_publication_recovery_response")
        return PublicationRecoverySummary(
            workspace_id=workspace_id,
            reconciled_count=reconciled,
            retrying_count=retrying,
            failed_count=failed,
            delivery_unknown_count=delivery_unknown,
        )


__all__ = [
    "PublicationRepositoryError",
    "SupabasePublicationRepository",
    "SupabasePublicationRecoveryRepository",
]
