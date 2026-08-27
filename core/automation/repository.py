from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

import httpx

from core.automation.models import (
    AutomationState,
    ClaimedJob,
    FailedDraftRecoveryInspection,
    PendingSource,
    QueueResult,
    StyleReference,
    StyleReferencePack,
)
from core.automation.content_signals import (
    CONTENT_RANKING_EVIDENCE_SCHEMA_VERSION,
    CONTENT_SIGNALS_SCHEMA_VERSION,
    ContentSignalsSnapshot,
)
from core.automation.settings import AUTOMATION_CLIENTS, _supabase_url
from core.sources.x_client import XClient


_X_STATUS_RE = re.compile(
    r"^https://x\.com/[A-Za-z0-9_]{1,15}/status/([0-9]{1,19})$"
)
_SAFE_ERROR_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_RELEASE_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_RECOVERY_SUBJECT_KEYS = frozenset({
    "contract",
    "workspace_id",
    "job_id",
    "recovery_id",
    "request_id",
    "source_item_id",
    "kst_date",
    "job_input_sha256",
    "source_snapshot_sha256",
    "style_pack_sha256",
    "failed_output_sha256",
    "failure_code",
    "failed_attempts",
    "failed_max_attempts",
    "approval_id",
    "approved_by",
    "approved_at",
    "expires_at",
    "release_sha",
    "claims_allowed",
    "same_job",
    "same_request_id",
    "automatic_approval",
    "automatic_publication",
    "human_review_required",
    "legacy_failure_requires_explicit_review",
    "failed_output_snapshot",
})
_RECOVERY_INSPECTION_KEYS = frozenset({
    "eligible",
    "authorized",
    "recovery_id",
    "job_id",
    "request_id",
    "source_item_id",
    "approval_subject",
    "approval_subject_sha256",
    "claims_allowed",
    "claims_consumed",
    "expires_at",
    "release_sha",
})
_RECOVERY_FAILURE_CODES = frozenset({
    "squid_visual_localization_incomplete",
    "squid_copy_discovery_unavailable",
})
_CONTENT_SIGNAL_RANKING_VERSIONS = frozenset({
    "official-x-demand-v1",
    "official-x-demand-v2",
})
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
_OFFICIAL_HANDLES = {
    "yellow": "Yellow",
    "origintrail": "origin_trail",
    "squid": "SquidRouter",
    "babylon": "babylonlabs_io",
}


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


def _date(value: object, name: str) -> date:
    if not isinstance(value, str):
        raise AutomationRepositoryError(f"invalid_{name}", retryable=False)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise AutomationRepositoryError(
            f"invalid_{name}",
            retryable=False,
        ) from exc
    if parsed.isoformat() != value:
        raise AutomationRepositoryError(f"invalid_{name}", retryable=False)
    return parsed


def _aware_iso(value: datetime, name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.isoformat()


def _aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _validated_recovery_subject(
    value: object,
    *,
    workspace_id: str,
    job_id: str,
    recovery_id: str,
    approval_id: str,
    approved_by: str,
    approved_at: datetime,
    expires_at: datetime,
    release_sha: str,
) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != _RECOVERY_SUBJECT_KEYS:
        raise ValueError("recovery subject shape is invalid")
    request_id = _uuid(value.get("request_id"), "recovery_request_id")
    source_item_id = _uuid(
        value.get("source_item_id"),
        "recovery_source_item_id",
    )
    safe_failure = value.get("failed_output_snapshot")
    failure_code = value.get("failure_code")
    if (
        value.get("contract") != "squid-failed-draft-recovery@1"
        or _uuid(value.get("workspace_id"), "recovery_workspace_id")
            != workspace_id
        or _uuid(value.get("job_id"), "recovery_job_id") != job_id
        or _uuid(value.get("recovery_id"), "recovery_id") != recovery_id
        or _uuid(value.get("approval_id"), "approval_id") != approval_id
        or value.get("approved_by") != approved_by
        or _aware_datetime(value.get("approved_at"), "approved_at")
            != approved_at
        or _aware_datetime(value.get("expires_at"), "expires_at")
            != expires_at
        or value.get("release_sha") != release_sha
        or _date(value.get("kst_date"), "recovery_kst_date") is None
        or type(value.get("claims_allowed")) is not int
        or value.get("claims_allowed") != 1
        or not isinstance(failure_code, str)
        or failure_code not in _RECOVERY_FAILURE_CODES
        or type(value.get("failed_attempts")) is not int
        or value.get("failed_attempts") != 3
        or type(value.get("failed_max_attempts")) is not int
        or value.get("failed_max_attempts") != 3
        or value.get("same_job") is not True
        or value.get("same_request_id") is not True
        or value.get("automatic_approval") is not False
        or value.get("automatic_publication") is not False
        or value.get("human_review_required") is not True
        or value.get("legacy_failure_requires_explicit_review") is not True
        or not isinstance(safe_failure, Mapping)
        or set(safe_failure) != {
            "execution_plane",
            "last_error_code",
            "last_failure_error_code",
            "last_failure_retryable",
            "finished_at",
        }
        or safe_failure.get("execution_plane") != "studio_sync"
        or safe_failure.get("last_error_code") != failure_code
        or safe_failure.get("last_failure_error_code") != failure_code
        or safe_failure.get("last_failure_retryable") is not False
    ):
        raise ValueError("recovery subject binding is invalid")
    _aware_datetime(safe_failure.get("finished_at"), "recovery_finished_at")
    for name in (
        "job_input_sha256",
        "source_snapshot_sha256",
        "style_pack_sha256",
        "failed_output_sha256",
    ):
        if (
            not isinstance(value.get(name), str)
            or _SHA256_RE.fullmatch(value[name]) is None
        ):
            raise ValueError("recovery subject digest is invalid")
    return request_id, source_item_id


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


def _style_reference(value: object, client_id: str) -> StyleReference:
    if not isinstance(value, Mapping):
        raise AutomationRepositoryError("invalid_style_reference_pack", retryable=False)
    source_item_id = _uuid(value.get("source_item_id"), "style_reference_source_item_id")
    source_url = value.get("source_url")
    text = value.get("text")
    published_at = value.get("published_at")
    expected_handle = _OFFICIAL_HANDLES[client_id]
    match = _X_STATUS_RE.fullmatch(source_url) if isinstance(source_url, str) else None
    if (
        not match
        or source_url.split("/")[3] != expected_handle
        or not isinstance(text, str)
        or not 1 <= len(text.strip()) <= 600
        or not isinstance(published_at, str)
        or not XClient._valid_provider_datetime(published_at)
    ):
        raise AutomationRepositoryError("invalid_style_reference_pack", retryable=False)
    return StyleReference(
        source_item_id=source_item_id,
        source_url=source_url,
        text=text.strip(),
        published_at=published_at,
    )


def _style_reference_pack(
    value: object,
    *,
    client_id: str,
    request_id: str,
    primary_source_item_id: str,
) -> StyleReferencePack:
    if not isinstance(value, Mapping):
        raise AutomationRepositoryError("invalid_style_reference_pack", retryable=False)
    references = value.get("references")
    pack_hash = value.get("reference_pack_hash")
    if (
        _uuid(value.get("request_id"), "style_reference_request_id") != request_id
        or _uuid(
            value.get("primary_source_item_id"),
            "style_reference_primary_source_item_id",
        ) != primary_source_item_id
        or not isinstance(pack_hash, str)
        or re.fullmatch(r"[a-f0-9]{32}", pack_hash) is None
        or not isinstance(references, list)
        or len(references) > 3
    ):
        raise AutomationRepositoryError("invalid_style_reference_pack", retryable=False)
    parsed = tuple(_style_reference(item, client_id) for item in references)
    if (
        len({item.source_item_id for item in parsed}) != len(parsed)
        or primary_source_item_id in {item.source_item_id for item in parsed}
    ):
        raise AutomationRepositoryError("invalid_style_reference_pack", retryable=False)
    return StyleReferencePack(
        request_id=request_id,
        primary_source_item_id=primary_source_item_id,
        reference_pack_hash=pack_hash,
        references=parsed,
        reused=value.get("reused") is True,
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

    async def record_ranking_evidence(
        self,
        *,
        workspace_id: str,
        snapshot: ContentSignalsSnapshot,
        ranking_version: str,
    ) -> str:
        if (
            ranking_version not in _CONTENT_SIGNAL_RANKING_VERSIONS
            or (
                ranking_version == "official-x-demand-v2"
                and snapshot.schema_version != CONTENT_SIGNALS_SCHEMA_VERSION
            )
        ):
            raise ValueError("unsupported content signal ranking version")
        demand_terms = [
            {
                "term": term.term,
                "weight": term.weight,
                "sources": list(term.sources),
            }
            for term in snapshot.demand_terms
        ]
        promotion_candidates = [
            {
                "candidate_id": candidate.candidate_id,
                "channel": candidate.channel,
                "source_url": candidate.source_url,
                "published_at": candidate.published_at.isoformat(),
                "score": candidate.score,
                "reach_percentile": candidate.reach_percentile,
                "interaction_percentile": candidate.interaction_percentile,
                "community_match_count": candidate.community_match_count,
                "cohort_size": candidate.cohort_size,
                "observation_age_hours": candidate.observation_age_hours,
                "recommended_formats": list(candidate.recommended_formats),
                "reason_codes": list(candidate.reason_codes),
            }
            for candidate in snapshot.promotion_candidates
        ]
        fingerprint = {
            "schema_version": snapshot.schema_version,
            "client_id": snapshot.client_id,
            "generated_at": snapshot.generated_at.isoformat(),
            "window_start": snapshot.window_start.isoformat(),
            "window_end": snapshot.window_end.isoformat(),
            "ranking_version": ranking_version,
            "demand_terms": demand_terms,
            "promotion_policy_version": snapshot.promotion_policy_version,
            "promotion_candidates": promotion_candidates,
            "tutorial_priority": snapshot.tutorial_priority,
            "learning_gaps": [
                {
                    "category": gap.category,
                    "attempts": gap.attempts,
                    "participants": gap.participants,
                    "accuracy_pct": gap.accuracy_pct,
                }
                for gap in snapshot.learning_gaps
            ],
        }
        snapshot_hash = hashlib.sha256(json.dumps(
            fingerprint,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        raw = await self._rpc("record_content_signal_ranking_evidence", {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_client_id": self._client(snapshot.client_id),
            "target_snapshot_hash": snapshot_hash,
            "target_schema_version": CONTENT_RANKING_EVIDENCE_SCHEMA_VERSION,
            "target_generated_at": snapshot.generated_at.isoformat(),
            "target_window_start": snapshot.window_start.isoformat(),
            "target_window_end": snapshot.window_end.isoformat(),
            "target_ranking_version": ranking_version,
            "target_demand_terms": demand_terms,
        })
        if (
            not isinstance(raw, Mapping)
            or raw.get("recorded") is not True
            or raw.get("snapshot_hash") != snapshot_hash
        ):
            raise AutomationRepositoryError(
                "invalid_ranking_evidence_receipt",
                retryable=False,
            )
        return snapshot_hash

    async def record_learning_evidence(
        self,
        *,
        workspace_id: str,
        snapshot: ContentSignalsSnapshot,
        snapshot_hash: str,
    ) -> None:
        if (
            snapshot.schema_version != "1.2"
            or not isinstance(snapshot_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", snapshot_hash) is None
        ):
            raise ValueError("unsupported content learning evidence")
        learning = {
            "tutorial_priority": snapshot.tutorial_priority,
            "gaps": [
                {
                    "category": gap.category,
                    "attempts": gap.attempts,
                    "participants": gap.participants,
                    "accuracy_pct": gap.accuracy_pct,
                }
                for gap in snapshot.learning_gaps
            ],
        }
        raw = await self._rpc("record_content_learning_evidence", {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_client_id": self._client(snapshot.client_id),
            "target_snapshot_hash": snapshot_hash,
            "target_schema_version": snapshot.schema_version,
            "target_generated_at": snapshot.generated_at.isoformat(),
            "target_window_start": snapshot.window_start.isoformat(),
            "target_window_end": snapshot.window_end.isoformat(),
            "target_learning_method": "tutorial-priority-v1",
            "target_learning": learning,
        })
        if (
            not isinstance(raw, Mapping)
            or raw.get("recorded") is not True
            or raw.get("snapshot_hash") != snapshot_hash
        ):
            raise AutomationRepositoryError(
                "invalid_learning_evidence_receipt",
                retryable=False,
            )

    async def record_promotion_candidates(
        self,
        *,
        workspace_id: str,
        snapshot: ContentSignalsSnapshot,
        snapshot_hash: str,
    ) -> int:
        if (
            snapshot.schema_version != "1.2"
            or snapshot.promotion_policy_version != "content-performance-v1"
            or not isinstance(snapshot_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", snapshot_hash) is None
        ):
            raise ValueError("unsupported content promotion evidence")
        candidates = [
            {
                "candidate_id": candidate.candidate_id,
                "channel": candidate.channel,
                "source_url": candidate.source_url,
                "published_at": candidate.published_at.isoformat(),
                "score": candidate.score,
                "reach_percentile": candidate.reach_percentile,
                "interaction_percentile": candidate.interaction_percentile,
                "community_match_count": candidate.community_match_count,
                "cohort_size": candidate.cohort_size,
                "observation_age_hours": candidate.observation_age_hours,
                "recommended_formats": list(candidate.recommended_formats),
                "reason_codes": list(candidate.reason_codes),
            }
            for candidate in snapshot.promotion_candidates
        ]
        raw = await self._rpc("record_content_promotion_candidates", {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_client_id": self._client(snapshot.client_id),
            "target_snapshot_hash": snapshot_hash,
            "target_schema_version": CONTENT_RANKING_EVIDENCE_SCHEMA_VERSION,
            "target_generated_at": snapshot.generated_at.isoformat(),
            "target_window_start": snapshot.window_start.isoformat(),
            "target_window_end": snapshot.window_end.isoformat(),
            "target_policy_version": snapshot.promotion_policy_version,
            "target_candidates": candidates,
        })
        if (
            not isinstance(raw, Mapping)
            or raw.get("recorded") is not True
            or raw.get("snapshot_hash") != snapshot_hash
            or raw.get("candidate_count") != len(candidates)
            or isinstance(raw.get("recommendation_count"), bool)
            or not isinstance(raw.get("recommendation_count"), int)
            or not 0 <= raw["recommendation_count"] <= len(candidates) * 2
        ):
            raise AutomationRepositoryError(
                "invalid_promotion_evidence_receipt",
                retryable=False,
            )
        return raw["recommendation_count"]

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
        if len(source_items) > 100:
            raise ValueError("source_items exceeds the bounded poll size")
        normalized_client = self._client(client_id)
        rpc_name = (
            "record_origintrail_nonquote_sources"
            if normalized_client == "origintrail"
            else "record_official_x_sources"
        )
        raw = await self._rpc(rpc_name, {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_client_id": normalized_client,
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

    async def get_or_create_style_reference_pack(
        self,
        *,
        workspace_id: str,
        client_id: str,
        request_id: str,
        primary_source_item_id: str,
        reference_limit: int = 3,
    ) -> StyleReferencePack:
        normalized_client = self._client(client_id)
        normalized_request_id = _uuid(request_id, "request_id")
        normalized_primary_source_item_id = _uuid(
            primary_source_item_id,
            "primary_source_item_id",
        )
        raw = await self._rpc("get_or_create_official_x_style_reference_pack", {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_client_id": normalized_client,
            "target_request_id": normalized_request_id,
            "target_primary_source_item_id": normalized_primary_source_item_id,
            "target_reference_limit": max(0, min(reference_limit, 3)),
        })
        return _style_reference_pack(
            raw,
            client_id=normalized_client,
            request_id=normalized_request_id,
            primary_source_item_id=normalized_primary_source_item_id,
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
        return self._claimed_job(raw, worker_id=worker_id)

    def _claimed_job(
        self,
        raw: object,
        *,
        worker_id: str,
        failed_draft_recovery_only: bool = False,
    ) -> ClaimedJob:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("input"), Mapping):
            raise AutomationRepositoryError("invalid_claim_response", retryable=False)
        job_input = raw["input"]
        attempts = raw.get("attempts")
        max_attempts = raw.get("max_attempts")
        batch_handoff_recovery_only = raw.get(
            "batch_handoff_recovery_only"
        )
        origintrail_batch_eligible = raw.get(
            "origintrail_batch_eligible"
        )
        client_id = raw.get("client_id")
        content_kind = job_input.get("content_kind")
        raw_source_item_ids = job_input.get("source_item_ids")
        if (
            raw.get("locked_by") != worker_id
            or type(attempts) is not int
            or type(max_attempts) is not int
            or attempts < 1
            or max_attempts < attempts
            or not isinstance(batch_handoff_recovery_only, bool)
            or not isinstance(origintrail_batch_eligible, bool)
            or (
                origintrail_batch_eligible
                and client_id != "origintrail"
            )
            or (
                batch_handoff_recovery_only
                and attempts != max_attempts
            )
            or content_kind not in {"daily_news", "article", "tutorial"}
            or not isinstance(raw_source_item_ids, list)
            or len(raw_source_item_ids) != 1
        ):
            raise AutomationRepositoryError("invalid_claim_response", retryable=False)
        return ClaimedJob(
            job_id=_uuid(raw.get("job_id") or raw.get("id"), "job_id"),
            client_id=self._client(client_id),
            kst_date=_date(job_input.get("kst_date"), "kst_date"),
            content_kind=content_kind,
            request_id=_uuid(job_input.get("request_id"), "request_id"),
            primary_source_item_id=_uuid(
                raw_source_item_ids[0],
                "primary_source_item_id",
            ),
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
            origintrail_batch_eligible=origintrail_batch_eligible,
            batch_handoff_recovery_only=(
                batch_handoff_recovery_only
            ),
            failed_draft_recovery_only=failed_draft_recovery_only,
        )

    async def inspect_failed_draft_recovery(
        self,
        *,
        workspace_id: str,
        job_id: str,
        recovery_id: str,
        approval_id: str,
        approved_by: str,
        approved_at: datetime,
        expires_at: datetime,
        release_sha: str,
    ) -> FailedDraftRecoveryInspection:
        normalized_workspace_id = _uuid(workspace_id, "workspace_id")
        normalized_job_id = _uuid(job_id, "job_id")
        normalized_recovery_id = _uuid(recovery_id, "recovery_id")
        normalized_approval_id = _uuid(approval_id, "approval_id")
        if not _RELEASE_SHA_RE.fullmatch(release_sha):
            raise ValueError("release_sha must be an exact Git SHA")
        if not re.fullmatch(r"[A-Za-z0-9@._:-]{3,120}", approved_by):
            raise ValueError("approved_by is invalid")
        raw = await self._rpc("inspect_squid_failed_draft_recovery", {
            "target_workspace_id": normalized_workspace_id,
            "target_job_id": normalized_job_id,
            "target_recovery_id": normalized_recovery_id,
            "target_approval_id": normalized_approval_id,
            "target_approved_by": approved_by,
            "target_approved_at": _aware_iso(approved_at, "approved_at"),
            "target_expires_at": _aware_iso(expires_at, "expires_at"),
            "target_release_sha": release_sha,
        })
        if not isinstance(raw, Mapping) or set(raw) != _RECOVERY_INSPECTION_KEYS:
            raise AutomationRepositoryError(
                "invalid_recovery_inspection_response",
                retryable=False,
            )
        try:
            subject_request_id, subject_source_item_id = (
                _validated_recovery_subject(
                    raw.get("approval_subject"),
                    workspace_id=normalized_workspace_id,
                    job_id=normalized_job_id,
                    recovery_id=normalized_recovery_id,
                    approval_id=normalized_approval_id,
                    approved_by=approved_by,
                    approved_at=approved_at,
                    expires_at=expires_at,
                    release_sha=release_sha,
                )
            )
            raw_request_id = _uuid(raw.get("request_id"), "request_id")
            raw_source_item_id = _uuid(
                raw.get("source_item_id"),
                "source_item_id",
            )
            response_expires_at = _aware_datetime(
                raw.get("expires_at"),
                "expires_at",
            )
        except (AutomationRepositoryError, ValueError) as exc:
            raise AutomationRepositoryError(
                "invalid_recovery_inspection_response",
                retryable=False,
            ) from exc
        if (
            raw.get("eligible") is not True
            or type(raw.get("authorized")) is not bool
            or _uuid(raw.get("recovery_id"), "recovery_id")
                != normalized_recovery_id
            or _uuid(raw.get("job_id"), "job_id") != normalized_job_id
            or raw_request_id != subject_request_id
            or raw_source_item_id != subject_source_item_id
            or type(raw.get("claims_allowed")) is not int
            or raw.get("claims_allowed") != 1
            or type(raw.get("claims_consumed")) is not int
            or raw.get("claims_consumed") not in {0, 1}
            or not isinstance(raw.get("approval_subject_sha256"), str)
            or _SHA256_RE.fullmatch(raw["approval_subject_sha256"]) is None
            or raw.get("release_sha") != release_sha
            or response_expires_at != expires_at
        ):
            raise AutomationRepositoryError(
                "invalid_recovery_inspection_response",
                retryable=False,
            )
        return FailedDraftRecoveryInspection(
            recovery_id=normalized_recovery_id,
            job_id=normalized_job_id,
            request_id=subject_request_id,
            source_item_id=subject_source_item_id,
            approval_subject=dict(raw["approval_subject"]),
            approval_subject_sha256=raw["approval_subject_sha256"],
            authorized=raw["authorized"],
            claims_allowed=1,
            claims_consumed=raw["claims_consumed"],
            expires_at=raw["expires_at"],
            release_sha=release_sha,
        )

    async def authorize_failed_draft_recovery(
        self,
        *,
        workspace_id: str,
        job_id: str,
        recovery_id: str,
        approval_id: str,
        approved_by: str,
        approved_at: datetime,
        expires_at: datetime,
        release_sha: str,
        approval_subject_sha256: str,
    ) -> bool:
        if _SHA256_RE.fullmatch(approval_subject_sha256) is None:
            raise ValueError("approval_subject_sha256 is invalid")
        inspection = await self.inspect_failed_draft_recovery(
            workspace_id=workspace_id,
            job_id=job_id,
            recovery_id=recovery_id,
            approval_id=approval_id,
            approved_by=approved_by,
            approved_at=approved_at,
            expires_at=expires_at,
            release_sha=release_sha,
        )
        if inspection.approval_subject_sha256 != approval_subject_sha256:
            raise AutomationRepositoryError(
                "recovery_approval_subject_changed",
                retryable=False,
            )
        raw = await self._rpc("authorize_squid_failed_draft_recovery", {
            "target_workspace_id": _uuid(workspace_id, "workspace_id"),
            "target_job_id": _uuid(job_id, "job_id"),
            "target_recovery_id": _uuid(recovery_id, "recovery_id"),
            "target_approval_id": _uuid(approval_id, "approval_id"),
            "target_approved_by": approved_by,
            "target_approved_at": _aware_iso(approved_at, "approved_at"),
            "target_expires_at": _aware_iso(expires_at, "expires_at"),
            "target_release_sha": release_sha,
            "target_approval_subject_sha256": approval_subject_sha256,
        })
        expected_keys = {
            "authorized",
            "reused",
            "recovery_id",
            "job_id",
            "request_id",
            "approval_subject_sha256",
            "claims_allowed",
            "claims_consumed",
            "expires_at",
            "release_sha",
        }
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            raise AutomationRepositoryError(
                "invalid_recovery_authorization_response",
                retryable=False,
            )
        try:
            response_expires_at = _aware_datetime(
                raw.get("expires_at"),
                "expires_at",
            )
        except ValueError as exc:
            raise AutomationRepositoryError(
                "invalid_recovery_authorization_response",
                retryable=False,
            ) from exc
        if (
            raw.get("authorized") is not True
            or type(raw.get("reused")) is not bool
            or _uuid(raw.get("recovery_id"), "recovery_id")
                != inspection.recovery_id
            or _uuid(raw.get("job_id"), "job_id") != inspection.job_id
            or _uuid(raw.get("request_id"), "request_id")
                != inspection.request_id
            or raw.get("approval_subject_sha256")
                != approval_subject_sha256
            or type(raw.get("claims_allowed")) is not int
            or raw.get("claims_allowed") != 1
            or type(raw.get("claims_consumed")) is not int
            or raw.get("claims_consumed") != 0
            or raw.get("release_sha") != release_sha
            or response_expires_at != expires_at
        ):
            raise AutomationRepositoryError(
                "invalid_recovery_authorization_response",
                retryable=False,
            )
        return raw["reused"]

    async def claim_failed_draft_recovery(
        self,
        *,
        workspace_id: str,
        job_id: str,
        recovery_id: str,
        approval_subject_sha256: str,
        release_sha: str,
        worker_id: str,
        lease_seconds: int = 900,
    ) -> ClaimedJob | None:
        normalized_workspace_id = _uuid(workspace_id, "workspace_id")
        normalized_job_id = _uuid(job_id, "job_id")
        normalized_recovery_id = _uuid(recovery_id, "recovery_id")
        worker_id = self._worker(worker_id)
        if _SHA256_RE.fullmatch(approval_subject_sha256) is None:
            raise ValueError("approval_subject_sha256 is invalid")
        if _RELEASE_SHA_RE.fullmatch(release_sha) is None:
            raise ValueError("release_sha must be an exact Git SHA")
        raw = await self._rpc("claim_squid_failed_draft_recovery", {
            "target_workspace_id": normalized_workspace_id,
            "target_job_id": normalized_job_id,
            "target_recovery_id": normalized_recovery_id,
            "target_approval_subject_sha256": approval_subject_sha256,
            "target_release_sha": release_sha,
            "target_worker_id": worker_id,
            "target_lease_seconds": max(60, min(lease_seconds, 1_800)),
        })
        if not isinstance(raw, Mapping):
            raise AutomationRepositoryError(
                "invalid_recovery_claim_response",
                retryable=False,
            )
        common = {
            "claim_granted",
            "generation_allowed",
            "failed_draft_recovery_only",
            "recovery_id",
            "job_id",
            "request_id",
            "approval_subject_sha256",
            "claims_allowed",
            "claims_consumed",
            "release_sha",
        }
        try:
            response_request_id = _uuid(raw.get("request_id"), "request_id")
            response_recovery_id = _uuid(
                raw.get("recovery_id"),
                "recovery_id",
            )
            response_job_id = _uuid(raw.get("job_id"), "job_id")
        except AutomationRepositoryError as exc:
            raise AutomationRepositoryError(
                "invalid_recovery_claim_response",
                retryable=False,
            ) from exc
        if (
            type(raw.get("claim_granted")) is not bool
            or type(raw.get("generation_allowed")) is not bool
            or raw.get("failed_draft_recovery_only") is not True
            or response_recovery_id != normalized_recovery_id
            or response_job_id != normalized_job_id
            or raw.get("approval_subject_sha256")
                != approval_subject_sha256
            or type(raw.get("claims_allowed")) is not int
            or raw.get("claims_allowed") != 1
            or type(raw.get("claims_consumed")) is not int
            or raw.get("claims_consumed") not in {0, 1}
            or raw.get("release_sha") != release_sha
        ):
            raise AutomationRepositoryError(
                "invalid_recovery_claim_response",
                retryable=False,
            )
        if raw.get("claim_granted") is False:
            if set(raw) != common or raw.get("generation_allowed") is not False:
                raise AutomationRepositoryError(
                    "invalid_recovery_claim_response",
                    retryable=False,
                )
            return None
        success_keys = common | {
            "workspace_id",
            "client_id",
            "status",
            "attempts",
            "max_attempts",
            "origintrail_batch_eligible",
            "batch_handoff_recovery_only",
            "locked_by",
            "lease_expires_at",
            "input",
        }
        if set(raw) != success_keys:
            raise AutomationRepositoryError(
                "invalid_recovery_claim_response",
                retryable=False,
            )
        try:
            response_workspace_id = _uuid(
                raw.get("workspace_id"),
                "workspace_id",
            )
            _aware_datetime(
                raw.get("lease_expires_at"),
                "lease_expires_at",
            )
        except (AutomationRepositoryError, ValueError) as exc:
            raise AutomationRepositoryError(
                "invalid_recovery_claim_response",
                retryable=False,
            ) from exc
        response_input = raw.get("input")
        if (
            raw.get("claim_granted") is not True
            or raw.get("generation_allowed") is not True
            or raw.get("claims_consumed") != 1
            or response_workspace_id != normalized_workspace_id
            or raw.get("client_id") != "squid"
            or raw.get("status") != "running"
            or raw.get("attempts") != raw.get("max_attempts")
            or raw.get("origintrail_batch_eligible") is not False
            or raw.get("batch_handoff_recovery_only") is not False
            or not isinstance(response_input, Mapping)
            or response_input.get("workflow")
                != "official_x_review_draft_v1"
            or response_input.get("manual_only") is not False
        ):
            raise AutomationRepositoryError(
                "invalid_recovery_claim_response",
                retryable=False,
            )
        try:
            claimed = self._claimed_job(
                raw,
                worker_id=worker_id,
                failed_draft_recovery_only=True,
            )
        except AutomationRepositoryError as exc:
            raise AutomationRepositoryError(
                "invalid_recovery_claim_response",
                retryable=False,
            ) from exc
        if claimed.request_id != response_request_id:
            raise AutomationRepositoryError(
                "invalid_recovery_claim_response",
                retryable=False,
            )
        return claimed

    async def bind_execution_plane(
        self,
        *,
        job_id: str,
        worker_id: str,
        requested_plane: str,
    ) -> str:
        if requested_plane not in {"studio_sync", "openai_batch"}:
            raise ValueError("unsupported execution plane")
        normalized_job_id = _uuid(job_id, "job_id")
        try:
            raw = await self._rpc("bind_review_draft_execution_plane", {
                "target_job_id": normalized_job_id,
                "target_worker_id": self._worker(worker_id),
                "target_requested_plane": requested_plane,
            })
        except AutomationRepositoryError as exc:
            if exc.code == "automation_database_invalid_response":
                raise AutomationRepositoryError(
                    "invalid_execution_plane_response",
                    retryable=True,
                ) from exc
            raise
        try:
            returned_plane = (
                raw.get("execution_plane")
                if isinstance(raw, Mapping)
                else None
            )
            reused = (
                raw.get("reused")
                if isinstance(raw, Mapping)
                else None
            )
            valid = (
                isinstance(raw, Mapping)
                and _uuid(raw.get("job_id"), "job_id") == normalized_job_id
                and returned_plane in {"studio_sync", "openai_batch"}
                and isinstance(reused, bool)
                and (reused or returned_plane == requested_plane)
            )
        except AutomationRepositoryError as exc:
            raise AutomationRepositoryError(
                "invalid_execution_plane_response",
                retryable=True,
            ) from exc
        if not valid:
            raise AutomationRepositoryError(
                "invalid_execution_plane_response",
                retryable=True,
            )
        return returned_plane

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

    async def complete_batch_handoff(
        self,
        *,
        job_id: str,
        worker_id: str,
        batch_job_id: str,
        input_sha256: str,
    ) -> None:
        if not isinstance(input_sha256, str) or not _SHA256_RE.fullmatch(
            input_sha256
        ):
            raise ValueError("input_sha256 must be a SHA-256 hex digest")
        normalized_job_id = _uuid(job_id, "job_id")
        normalized_batch_job_id = _uuid(batch_job_id, "batch_job_id")
        raw = await self._rpc("complete_review_draft_batch_handoff", {
            "target_job_id": normalized_job_id,
            "target_worker_id": self._worker(worker_id),
            "target_batch_job_id": normalized_batch_job_id,
            "target_input_sha256": input_sha256,
        })
        if (
            not isinstance(raw, Mapping)
            or _uuid(raw.get("job_id"), "job_id") != normalized_job_id
            or raw.get("status") != "succeeded"
            or _uuid(raw.get("batch_job_id"), "batch_job_id")
            != normalized_batch_job_id
            or not isinstance(raw.get("reused"), bool)
        ):
            raise AutomationRepositoryError(
                "invalid_batch_handoff_response",
                retryable=False,
            )

    async def recover_batch_handoff(
        self,
        *,
        job_id: str,
        worker_id: str,
    ) -> bool:
        normalized_job_id = _uuid(job_id, "job_id")
        raw = await self._rpc("recover_review_draft_batch_handoff", {
            "target_job_id": normalized_job_id,
            "target_worker_id": self._worker(worker_id),
        })
        if raw is None:
            return False
        try:
            valid = (
                isinstance(raw, Mapping)
                and _uuid(raw.get("job_id"), "job_id") == normalized_job_id
                and raw.get("status") == "succeeded"
                and _uuid(raw.get("batch_job_id"), "batch_job_id")
                    == normalized_job_id
                and isinstance(raw.get("input_sha256"), str)
                and _SHA256_RE.fullmatch(raw["input_sha256"]) is not None
                and isinstance(raw.get("reused"), bool)
            )
        except AutomationRepositoryError as exc:
            raise AutomationRepositoryError(
                "invalid_batch_handoff_recovery_response",
                retryable=False,
            ) from exc
        if not valid:
            raise AutomationRepositoryError(
                "invalid_batch_handoff_recovery_response",
                retryable=False,
            )
        return True

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
