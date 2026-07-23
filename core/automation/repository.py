from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

import httpx

from core.automation.models import (
    AutomationState,
    ClaimedJob,
    PendingSource,
    QueueResult,
)
from core.automation.settings import AUTOMATION_CLIENTS, _supabase_url
from core.sources.x_client import XClient


_X_STATUS_RE = re.compile(
    r"^https://x\.com/[A-Za-z0-9_]{1,15}/status/([0-9]{1,19})$"
)
_SAFE_ERROR_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_JOB_STATUSES = frozenset({
    "queued",
    "running",
    "retrying",
    "succeeded",
    "failed",
    "skipped",
    "already_reserved",
    "daily_limit_reached",
})


class AutomationRepositoryError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise AutomationRepositoryError(f"invalid_{name}", retryable=False)
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise AutomationRepositoryError(f"invalid_{name}", retryable=False) from exc


def _cursor(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.isdigit() or len(value) > 19:
        raise AutomationRepositoryError("invalid_feed_cursor", retryable=False)
    return value


def _pending_source(value: object) -> PendingSource:
    if not isinstance(value, Mapping):
        raise AutomationRepositoryError("invalid_pending_source", retryable=False)
    source_item_id = _uuid(value.get("source_item_id"), "source_item_id")
    source_content = value.get("source_content")
    source_url = value.get("source_url")
    source_image_url = value.get("source_image_url", "")
    published_at = value.get("published_at")
    match = _X_STATUS_RE.fullmatch(source_url) if isinstance(source_url, str) else None
    post_id = value.get("external_id") or value.get("post_id")
    if post_id is None and match:
        post_id = match.group(1)
    if (
        not isinstance(source_content, str)
        or not 1 <= len(source_content.strip()) <= 60_000
        or not match
        or not isinstance(post_id, str)
        or not post_id.isdigit()
        or post_id != match.group(1)
        or not isinstance(published_at, str)
        or not XClient._valid_provider_datetime(published_at)
        or not isinstance(source_image_url, str)
        or (source_image_url and not XClient._allowed_media_url(source_image_url))
    ):
        raise AutomationRepositoryError("invalid_pending_source", retryable=False)
    raw_media = value.get("media", [])
    raw_metrics = value.get("metrics", {})
    if (
        not isinstance(raw_media, list)
        or len(raw_media) > 16
        or any(not isinstance(item, Mapping) for item in raw_media)
        or not isinstance(raw_metrics, Mapping)
    ):
        raise AutomationRepositoryError("invalid_pending_source", retryable=False)
    metrics = {
        name: count
        for name in ("like_count", "retweet_count", "reply_count", "quote_count")
        if isinstance((count := raw_metrics.get(name)), int) and 0 <= count <= 2_147_483_647
    }
    return PendingSource(
        source_item_id=source_item_id,
        post_id=post_id,
        source_content=source_content.strip(),
        source_url=source_url,
        source_image_url=source_image_url,
        published_at=published_at,
        media=tuple(dict(item) for item in raw_media),
        metrics=metrics,
        is_note_tweet=value.get("is_note_tweet") is True,
    )


def _state(value: object) -> AutomationState:
    if not isinstance(value, Mapping):
        raise AutomationRepositoryError("invalid_automation_state", retryable=False)
    reserved = value.get("draft_reserved_today")
    pending = value.get("pending_sources")
    if not isinstance(reserved, bool) or not isinstance(pending, list) or len(pending) > 32:
        raise AutomationRepositoryError("invalid_automation_state", retryable=False)
    return AutomationState(
        last_cursor=_cursor(value.get("last_cursor")),
        draft_reserved_today=reserved,
        pending_sources=tuple(_pending_source(item) for item in pending),
    )


class SupabaseAutomationRepository:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.supabase_url = _supabase_url(supabase_url)
        self.service_role_key = service_role_key.strip()
        if len(self.service_role_key) < 32 or len(self.service_role_key) > 8_192:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY has an invalid length")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def _rpc(self, name: str, payload: Mapping[str, object]) -> object:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.supabase_url}/rest/v1/rpc/{name}",
                    headers={
                        "apikey": self.service_role_key,
                        "Authorization": f"Bearer {self.service_role_key}",
                        "Content-Type": "application/json",
                    },
                    json=dict(payload),
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise AutomationRepositoryError(
                "automation_database_unavailable",
                retryable=True,
            ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise AutomationRepositoryError(
                "automation_database_rpc_failed",
                retryable=response.status_code in {408, 409, 425, 429, 500, 502, 503, 504},
            )
        try:
            return response.json()
        except ValueError as exc:
            raise AutomationRepositoryError(
                "automation_database_invalid_response",
                retryable=False,
            ) from exc

    async def get_state(
        self,
        *,
        workspace_id: str,
        client_id: str,
        kst_date: date,
        pending_limit: int = 8,
    ) -> AutomationState:
        raw = await self._rpc("get_official_x_automation_state", {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_client_id": self._client(client_id),
            "target_kst_date": kst_date.isoformat(),
            "target_pending_limit": max(1, min(pending_limit, 16)),
        })
        return _state(raw)

    async def record_sources(
        self,
        *,
        workspace_id: str,
        client_id: str,
        handle: str,
        poll_request_id: str,
        expected_cursor: str | None,
        next_cursor: str | None,
        source_items: Sequence[Mapping[str, object]],
        polled_at: datetime,
    ) -> AutomationState:
        if len(source_items) > 200:
            raise ValueError("source_items exceeds the bounded poll size")
        raw = await self._rpc("record_official_x_sources", {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_client_id": self._client(client_id),
            "target_handle": XClient._normalize_username(handle),
            "target_poll_request_id": _uuid(poll_request_id, "poll_request_id"),
            "target_expected_cursor": _cursor(expected_cursor),
            "target_next_cursor": _cursor(next_cursor),
            "target_items": [dict(item) for item in source_items],
            "target_polled_at": polled_at.isoformat(),
        })
        return _state(raw)

    async def queue_job(
        self,
        *,
        workspace_id: str,
        client_id: str,
        kst_date: date,
        source_item_ids: Iterable[str],
        content_kind: str,
        request_id: str,
        source_content: str,
        source_url: str,
        source_image_url: str = "",
        manual_only: bool = False,
    ) -> QueueResult:
        ids = tuple(_uuid(item, "source_item_id") for item in source_item_ids)
        if not 1 <= len(ids) <= 8 or len(set(ids)) != len(ids):
            raise ValueError("source_item_ids must contain 1 to 8 unique ids")
        if content_kind not in {"daily_news", "article", "tutorial"}:
            raise ValueError("unsupported content kind")
        raw = await self._rpc("queue_review_draft_job", {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_client_id": self._client(client_id),
            "target_kst_date": kst_date.isoformat(),
            "target_source_item_ids": list(ids),
            "target_content_kind": content_kind,
            "target_request_id": _uuid(request_id, "request_id"),
            "target_source_content": source_content,
            "target_source_url": source_url,
            "target_source_image_url": source_image_url,
            "target_manual_only": manual_only,
        })
        if not isinstance(raw, Mapping):
            raise AutomationRepositoryError("invalid_queue_response", retryable=False)
        status = raw.get("status")
        job_id = raw.get("job_id")
        if status not in _JOB_STATUSES or (job_id is not None and not isinstance(job_id, str)):
            raise AutomationRepositoryError("invalid_queue_response", retryable=False)
        return QueueResult(
            job_id=_uuid(job_id, "job_id") if job_id is not None else None,
            status=status,
            reused=raw.get("reused") is True,
        )

    async def claim_job(
        self,
        *,
        workspace_id: str,
        worker_id: str,
        lease_seconds: int = 900,
    ) -> ClaimedJob | None:
        worker_id = self._worker(worker_id)
        raw = await self._rpc("claim_review_draft_job", {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_worker_id": worker_id,
            "target_lease_seconds": max(60, min(lease_seconds, 1_800)),
        })
        if raw is None:
            return None
        if not isinstance(raw, Mapping) or not isinstance(raw.get("input"), Mapping):
            raise AutomationRepositoryError("invalid_claim_response", retryable=False)
        job_input = raw["input"]
        attempts = raw.get("attempts")
        max_attempts = raw.get("max_attempts")
        client_id = raw.get("client_id")
        content_kind = job_input.get("content_kind")
        if (
            raw.get("locked_by") != worker_id
            or not isinstance(attempts, int)
            or not isinstance(max_attempts, int)
            or attempts < 1
            or max_attempts < attempts
            or content_kind not in {"daily_news", "article", "tutorial"}
        ):
            raise AutomationRepositoryError("invalid_claim_response", retryable=False)
        return ClaimedJob(
            job_id=_uuid(raw.get("job_id") or raw.get("id"), "job_id"),
            client_id=self._client(client_id),
            content_kind=content_kind,
            request_id=_uuid(job_input.get("request_id"), "request_id"),
            source_content=self._text(job_input.get("source_content"), "source_content", 60_000),
            source_url=self._text(job_input.get("source_url"), "source_url", 2_048),
            source_image_url=self._text(
                job_input.get("source_image_url", ""),
                "source_image_url",
                2_048,
                allow_empty=True,
            ),
            manual_only=job_input.get("manual_only") is True,
            attempts=attempts,
            max_attempts=max_attempts,
            locked_by=worker_id,
        )

    async def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        content_item_id: str,
        content_version_id: str,
    ) -> None:
        await self._rpc("complete_review_draft_job", {
            "target_job_id": _uuid(job_id, "job_id"),
            "target_worker_id": self._worker(worker_id),
            "target_content_item_id": _uuid(content_item_id, "content_item_id"),
            "target_content_version_id": _uuid(content_version_id, "content_version_id"),
        })

    async def fail_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        retryable: bool,
        retry_at: datetime | None,
    ) -> None:
        if not _SAFE_ERROR_RE.fullmatch(error_code):
            error_code = "automation_job_failed"
        await self._rpc("fail_review_draft_job", {
            "target_job_id": _uuid(job_id, "job_id"),
            "target_worker_id": self._worker(worker_id),
            "target_error_code": error_code,
            "target_error_message": error_code,
            "target_retryable": retryable,
            "target_retry_at": retry_at.isoformat() if retry_at else None,
        })

    @staticmethod
    def _client(value: object) -> str:
        if not isinstance(value, str) or value not in AUTOMATION_CLIENTS:
            raise ValueError("unsupported automation client")
        return value

    @staticmethod
    def _worker(value: object) -> str:
        if (
            not isinstance(value, str)
            or not 8 <= len(value) <= 120
            or not re.fullmatch(r"[A-Za-z0-9:_-]+", value)
        ):
            raise ValueError("worker_id is invalid")
        return value

    @staticmethod
    def _text(value: object, name: str, maximum: int, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str):
            raise AutomationRepositoryError(f"invalid_{name}", retryable=False)
        normalized = value.strip()
        if (not allow_empty and not normalized) or len(normalized) > maximum:
            raise AutomationRepositoryError(f"invalid_{name}", retryable=False)
        return normalized
