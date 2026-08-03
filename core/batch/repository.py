from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping, Sequence

import httpx

from core.automation.settings import _supabase_url
from core.batch.models import ActiveBatch, BatchSnapshot, BatchWorkItem
from core.batch.openai_client import (
    BATCH_COMPLETION_WINDOW,
    BATCH_OUTPUT_RETENTION_SECONDS,
)


_CLIENTS = frozenset({"yellow", "origintrail", "squid", "babylon"})
_SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,199}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_BUDGET_KEY_RE = re.compile(r"^batch-general:\d{4}-\d{2}-\d{2}$")
_CANARY_MAX_MICROUSD = 50_000
_PRIORITY_RANK = {"P0": 3, "P1": 2, "P2": 1, "P3": 0}
_PRIORITY_FROM_RANK = {rank: priority for priority, rank in _PRIORITY_RANK.items()}
_BATCH_JOB_STATUSES = frozenset({
    "queued",
    "claimed",
    "submitted",
    "in_progress",
    "retry_wait",
    "completed",
    "failed",
})


class BatchRepositoryError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise BatchRepositoryError(f"invalid_{name}", retryable=False)
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise BatchRepositoryError(f"invalid_{name}", retryable=False) from exc


def _text(
    value: object,
    name: str,
    *,
    minimum: int = 1,
    maximum: int = 512,
) -> str:
    if not isinstance(value, str):
        raise BatchRepositoryError(f"invalid_{name}", retryable=False)
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise BatchRepositoryError(f"invalid_{name}", retryable=False)
    return normalized


def _provider_id(value: object, name: str) -> str:
    normalized = _text(value, name, maximum=200)
    if not _PROVIDER_ID_RE.fullmatch(normalized):
        raise BatchRepositoryError(f"invalid_{name}", retryable=False)
    return normalized


def _datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise BatchRepositoryError(f"invalid_{name}", retryable=False)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatchRepositoryError(f"invalid_{name}", retryable=False) from exc
    if parsed.tzinfo is None:
        raise BatchRepositoryError(f"invalid_{name}", retryable=False)
    return parsed


def _microusd(value: Decimal) -> int:
    return int(
        (value * Decimal("1000000")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _sha256_argument(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _uuid_argument(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID")
    try:
        normalized = str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID") from exc
    if normalized != value:
        raise ValueError(f"{name} must be a canonical UUID")
    return normalized


def _canary_expiry(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("canary expires_at must be timezone-aware")
    return value


def _canary_limit_microusd(value: object) -> int:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= 0
    ):
        raise ValueError("canary hard limit must be a positive Decimal")
    limit = _microusd(value)
    if Decimal(limit) / Decimal("1000000") != value:
        raise ValueError("canary hard limit must use exact micro-USD precision")
    if limit > _CANARY_MAX_MICROUSD:
        raise ValueError("canary hard limit exceeds 50000 micro-USD")
    return limit


class SupabaseBatchRepository:
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
        self.service_role_key = service_role_key.strip()
        if not 32 <= len(self.service_role_key) <= 8_192:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY has an invalid length")
        self.workspace_id = _uuid(workspace_id, "workspace_id")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def configure_daily_budget(
        self,
        *,
        budget_key: str,
        window_start: datetime,
        window_end: datetime,
        limit_usd: Decimal,
    ) -> None:
        if (
            window_start.tzinfo is None
            or window_end.tzinfo is None
            or window_end <= window_start
            or window_end - window_start > timedelta(days=1)
        ):
            raise ValueError("budget window is invalid")
        if limit_usd <= 0:
            raise ValueError("budget limit must be positive")
        await self._rpc("configure_agent_batch_budget", {
            "target_workspace_id": self.workspace_id,
            "target_budget_key": _text(
                budget_key,
                "budget_key",
                maximum=80,
            ),
            "target_period_start": window_start.isoformat(),
            "target_period_end": window_end.isoformat(),
            "target_hard_limit_microusd": _microusd(limit_usd),
        })

    async def configure_canary_grant(
        self,
        *,
        config_subject_sha256: str,
        config_approval_id: str,
        dispatch_subject_sha256: str,
        dispatch_approval_id: str,
        job_id: str,
        input_sha256: str,
        request_sha256: str,
        expires_at: datetime,
        hard_limit_usd: Decimal,
    ) -> None:
        binding = self._canary_binding(
            config_subject_sha256=config_subject_sha256,
            config_approval_id=config_approval_id,
            dispatch_subject_sha256=dispatch_subject_sha256,
            dispatch_approval_id=dispatch_approval_id,
            job_id=job_id,
            input_sha256=input_sha256,
            request_sha256=request_sha256,
            expires_at=expires_at,
            hard_limit_usd=hard_limit_usd,
        )
        raw = await self._rpc(
            "configure_origintrail_batch_canary_grant",
            {
                "target_workspace_id": self.workspace_id,
                **binding,
                "target_max_provider_batches": 1,
            },
        )
        self._validate_canary_receipt(
            raw,
            binding=binding,
            require_consumed=False,
            response_code="invalid_batch_canary_grant_response",
        )

    async def claim_canary_job(
        self,
        *,
        worker_id: str,
        config_subject_sha256: str,
        config_approval_id: str,
        dispatch_subject_sha256: str,
        dispatch_approval_id: str,
        job_id: str,
        input_sha256: str,
        request_sha256: str,
        expires_at: datetime,
        hard_limit_usd: Decimal,
        lease_seconds: int = 900,
    ) -> tuple[BatchWorkItem, ...]:
        binding = self._canary_binding(
            config_subject_sha256=config_subject_sha256,
            config_approval_id=config_approval_id,
            dispatch_subject_sha256=dispatch_subject_sha256,
            dispatch_approval_id=dispatch_approval_id,
            job_id=job_id,
            input_sha256=input_sha256,
            request_sha256=request_sha256,
            expires_at=expires_at,
            hard_limit_usd=hard_limit_usd,
        )
        raw = await self._rpc(
            "claim_origintrail_batch_canary_job",
            {
                "target_workspace_id": self.workspace_id,
                "target_worker_id": self._worker(worker_id),
                **binding,
                "target_max_provider_batches": 1,
                "target_lease_seconds": max(60, min(lease_seconds, 1_800)),
            },
        )
        if raw is None:
            return ()
        if not isinstance(raw, list) or len(raw) > 1:
            raise BatchRepositoryError(
                "invalid_batch_canary_claim_response",
                retryable=False,
            )
        if not raw:
            return ()
        receipt = raw[0]
        self._validate_canary_receipt(
            receipt,
            binding=binding,
            require_consumed=True,
            response_code="invalid_batch_canary_claim_response",
        )
        if not isinstance(receipt, Mapping):
            raise BatchRepositoryError(
                "invalid_batch_canary_claim_response",
                retryable=False,
            )
        provider_create_allowed = receipt.get("provider_create_allowed")
        item = self._claimed_item(receipt)
        if (
            item.job_id != job_id
            or item.client_id != "origintrail"
            or item.agent_id != "origintrail_client_agent"
            or item.workflow_kind != "official_source_nonurgent_pack"
            or item.stage != "generate"
            or item.attempt != 1
            or item.input_sha256 != input_sha256
            or item.request_sha256 != request_sha256
            or _microusd(item.max_cost_usd)
            > binding["target_hard_limit_microusd"]
            or not isinstance(provider_create_allowed, bool)
            or provider_create_allowed == item.recovery_required
            or receipt.get("reused") != item.recovery_required
        ):
            raise BatchRepositoryError(
                "invalid_batch_canary_claim_response",
                retryable=False,
            )
        return (item,)

    async def authorize_canary_provider_create(
        self,
        *,
        worker_id: str,
        config_subject_sha256: str,
        config_approval_id: str,
        dispatch_subject_sha256: str,
        dispatch_approval_id: str,
        job_id: str,
        input_sha256: str,
        request_sha256: str,
        expires_at: datetime,
        hard_limit_usd: Decimal,
        intent_id: str,
        dispatch_key: str,
        create_request_sha256: str,
        input_file_id: str,
    ) -> datetime:
        binding = self._canary_binding(
            config_subject_sha256=config_subject_sha256,
            config_approval_id=config_approval_id,
            dispatch_subject_sha256=dispatch_subject_sha256,
            dispatch_approval_id=dispatch_approval_id,
            job_id=job_id,
            input_sha256=input_sha256,
            request_sha256=request_sha256,
            expires_at=expires_at,
            hard_limit_usd=hard_limit_usd,
        )
        normalized_intent_id = _uuid_argument(intent_id, "intent_id")
        normalized_dispatch_key = _sha256_argument(
            dispatch_key,
            "dispatch_key",
        )
        normalized_create_request_sha = _sha256_argument(
            create_request_sha256,
            "create_request_sha256",
        )
        normalized_input_file_id = _provider_id(
            input_file_id,
            "input_file_id",
        )
        expected_metadata = {
            "dispatch_key": normalized_dispatch_key,
            "bundle_id": normalized_intent_id,
            "client_id": "origintrail",
            "policy": "batch_first_v1",
        }
        if normalized_create_request_sha != provider_create_request_sha256(
            input_file_id=normalized_input_file_id,
            completion_window=BATCH_COMPLETION_WINDOW,
            metadata=expected_metadata,
            output_expires_after={
                "anchor": "created_at",
                "seconds": BATCH_OUTPUT_RETENTION_SECONDS,
            },
        ):
            raise ValueError("create_request_sha256 does not match request")
        raw = await self._rpc(
            "authorize_origintrail_batch_provider_create",
            {
                "target_workspace_id": self.workspace_id,
                "target_worker_id": self._worker(worker_id),
                **binding,
                "target_max_provider_batches": 1,
                "target_intent_id": normalized_intent_id,
                "target_dispatch_key": normalized_dispatch_key,
                "target_create_request_sha256": normalized_create_request_sha,
                "target_input_file_id": normalized_input_file_id,
            },
        )
        invalid = BatchRepositoryError(
            "invalid_batch_provider_create_authorization",
            retryable=True,
        )
        if not isinstance(raw, Mapping):
            raise invalid
        expected = {
            "provider_create_intent_id": normalized_intent_id,
            "intent_status": "armed",
            "provider_create_allowed": True,
            "job_id": binding["target_job_id"],
            "attempt": 1,
            "config_subject_sha256": binding[
                "target_config_subject_sha256"
            ],
            "config_approval_id": binding["target_config_approval_id"],
            "dispatch_subject_sha256": binding[
                "target_dispatch_subject_sha256"
            ],
            "dispatch_approval_id": binding["target_dispatch_approval_id"],
            "input_sha256": binding["target_input_sha256"],
            "request_sha256": binding["target_request_sha256"],
            "dispatch_key": normalized_dispatch_key,
            "create_request_sha256": normalized_create_request_sha,
            "input_file_id": normalized_input_file_id,
        }
        if (
            any(raw.get(key) != value for key, value in expected.items())
            or raw.get("reused") is not False
        ):
            raise invalid
        try:
            return _datetime(
                raw.get("create_not_after"),
                "create_not_after",
            )
        except BatchRepositoryError as exc:
            raise invalid from exc

    async def register_canary_batch(
        self,
        *,
        worker_id: str,
        intent_id: str,
        config_subject_sha256: str,
        job_id: str,
        input_sha256: str,
        request_sha256: str,
        dispatch_key: str,
        create_request_sha256: str,
        input_file_id: str,
        snapshot: BatchSnapshot,
    ) -> None:
        normalized_intent_id = _uuid_argument(intent_id, "intent_id")
        normalized_config_subject = _sha256_argument(
            config_subject_sha256,
            "config_subject_sha256",
        )
        normalized_job_id = _uuid_argument(job_id, "job_id")
        normalized_input_sha = _sha256_argument(
            input_sha256,
            "input_sha256",
        )
        normalized_request_sha = _sha256_argument(
            request_sha256,
            "request_sha256",
        )
        normalized_dispatch_key = _sha256_argument(
            dispatch_key,
            "dispatch_key",
        )
        normalized_create_request_sha = _sha256_argument(
            create_request_sha256,
            "create_request_sha256",
        )
        normalized_input_file_id = _provider_id(
            input_file_id,
            "input_file_id",
        )
        normalized_batch_id = _provider_id(
            snapshot.provider_batch_id,
            "batch_id",
        )
        expected_metadata = {
            "dispatch_key": normalized_dispatch_key,
            "bundle_id": normalized_intent_id,
            "client_id": "origintrail",
            "policy": "batch_first_v1",
        }
        expected_create_request_sha = provider_create_request_sha256(
            input_file_id=normalized_input_file_id,
            completion_window=BATCH_COMPLETION_WINDOW,
            metadata=expected_metadata,
            output_expires_after={
                "anchor": "created_at",
                "seconds": BATCH_OUTPUT_RETENTION_SECONDS,
            },
        )
        if (
            normalized_create_request_sha != expected_create_request_sha
            or snapshot.input_file_id != normalized_input_file_id
            or snapshot.metadata != expected_metadata
        ):
            raise BatchRepositoryError(
                "invalid_batch_provider_create_snapshot",
                retryable=False,
            )
        raw = await self._rpc(
            "register_origintrail_batch_provider_create",
            {
                "target_workspace_id": self.workspace_id,
                "target_worker_id": self._worker(worker_id),
                "target_intent_id": normalized_intent_id,
                "target_config_subject_sha256": normalized_config_subject,
                "target_job_id": normalized_job_id,
                "target_input_sha256": normalized_input_sha,
                "target_request_sha256": normalized_request_sha,
                "target_dispatch_key": normalized_dispatch_key,
                "target_create_request_sha256": normalized_create_request_sha,
                "target_input_file_id": normalized_input_file_id,
                "target_batch_id": normalized_batch_id,
            },
        )
        invalid = BatchRepositoryError(
            "invalid_batch_provider_create_registration",
            retryable=True,
        )
        if not isinstance(raw, Mapping):
            raise invalid
        if (
            raw.get("provider_create_intent_id") != normalized_intent_id
            or raw.get("intent_status") != "registered"
            or raw.get("job_id") != normalized_job_id
            or raw.get("attempt") != 1
            or raw.get("dispatch_key") != normalized_dispatch_key
            or raw.get("create_request_sha256")
            != normalized_create_request_sha
            or raw.get("input_file_id") != normalized_input_file_id
            or raw.get("provider_batch_id") != normalized_batch_id
            or not isinstance(raw.get("registered_at"), str)
            or not isinstance(raw.get("reused"), bool)
        ):
            raise invalid
        try:
            _datetime(raw["registered_at"], "registered_at")
        except BatchRepositoryError as exc:
            raise invalid from exc

    async def queue_job(
        self,
        *,
        item: BatchWorkItem,
        idempotency_key: str,
        budget_key: str,
        replay_only: bool = False,
    ) -> str:
        if not _SHA256_RE.fullmatch(idempotency_key):
            raise ValueError("idempotency_key must be a SHA-256 hex digest")
        if not isinstance(replay_only, bool):
            raise ValueError("replay_only must be a boolean")
        payload = {
            "instructions": item.instructions,
            "input": item.input_text,
            "output_schema": dict(item.output_schema),
            "estimated_output_tokens": item.estimated_output_tokens,
            "risk_tier": item.risk_tier,
            "approval_required": item.approval_required,
            "interactive": item.interactive,
            "incident_or_release_blocker": item.incident_or_release_blocker,
            "live_tools_required": item.live_tools_required,
            "source_snapshot_complete": item.source_snapshot_complete,
            "input_immutable": item.input_immutable,
            "retry_idempotent": item.retry_idempotent,
            "remaining_batch_stages": item.remaining_batch_stages,
            "request_sha256": item.request_sha256,
        }
        normalized_budget_key = _text(
            budget_key,
            "budget_key",
            maximum=80,
        )
        try:
            raw = await self._rpc("queue_agent_batch_job", {
                "target_workspace_id": self.workspace_id,
                "target_client_id": item.client_id,
                "target_job_id": item.job_id,
                "target_idempotency_key": idempotency_key,
                "target_agent_id": item.agent_id,
                "target_workflow_kind": item.workflow_kind,
                "target_stage": item.stage,
                "target_priority": _PRIORITY_RANK[item.priority],
                "target_latency_class": "batch_24h",
                "target_model": item.model,
                "target_model_tier": item.model_tier,
                "target_deadline_at": item.deadline_at.isoformat(),
                "target_input_payload": payload,
                "target_input_sha256": item.input_sha256,
                "target_estimated_input_tokens": item.estimated_input_tokens,
                "target_max_output_tokens": item.max_output_tokens,
                "target_max_cost_microusd": _microusd(item.max_cost_usd),
                "target_budget_key": normalized_budget_key,
                "target_custom_id": item.custom_id,
                "target_replay_only": replay_only,
            })
        except BatchRepositoryError as exc:
            if exc.code == "batch_database_invalid_response":
                # The transaction may have committed even when a proxy
                # returned malformed JSON. A bounded retry uses the same
                # idempotency key to read the durable queue row.
                raise BatchRepositoryError(
                    exc.code,
                    retryable=True,
                ) from exc
            raise
        if not isinstance(raw, Mapping):
            raise BatchRepositoryError(
                "invalid_batch_queue_response",
                retryable=True,
            )
        try:
            job_id = _uuid(raw.get("job_id") or raw.get("id"), "job_id")
        except BatchRepositoryError as exc:
            raise BatchRepositoryError(
                "invalid_batch_queue_response",
                retryable=True,
            ) from exc
        if job_id != item.job_id:
            raise BatchRepositoryError(
                "invalid_batch_queue_response",
                retryable=True,
            )
        reused = raw.get("reused")
        receipt_budget_key = raw.get("budget_key")
        reserved_microusd = raw.get("reserved_microusd")
        if (
            raw.get("custom_id") != item.custom_id
            or raw.get("status") not in _BATCH_JOB_STATUSES
            or not isinstance(reused, bool)
            or isinstance(reserved_microusd, bool)
            or reserved_microusd != _microusd(item.max_cost_usd)
            or not isinstance(receipt_budget_key, str)
            or _BUDGET_KEY_RE.fullmatch(receipt_budget_key) is None
            or (reused is False and receipt_budget_key != normalized_budget_key)
            or (replay_only and reused is not True)
        ):
            raise BatchRepositoryError(
                "invalid_batch_queue_response",
                retryable=True,
            )
        return job_id

    async def claim_jobs(
        self,
        *,
        worker_id: str,
        allowed_clients: frozenset[str],
        limit: int,
        lease_seconds: int = 900,
    ) -> tuple[BatchWorkItem, ...]:
        worker_id = self._worker(worker_id)
        if not allowed_clients or not allowed_clients <= _CLIENTS:
            raise ValueError("allowed_clients is invalid")
        raw = await self._rpc("claim_agent_batch_jobs", {
            "target_workspace_id": self.workspace_id,
            "target_worker_id": worker_id,
            "target_client_ids": sorted(allowed_clients),
            "target_limit": max(1, min(limit, 100)),
            "target_lease_seconds": max(60, min(lease_seconds, 1_800)),
        })
        if raw is None:
            return ()
        if not isinstance(raw, list) or len(raw) > 100:
            raise BatchRepositoryError("invalid_batch_claim_response", retryable=False)
        return tuple(self._claimed_item(value) for value in raw)

    async def expire_jobs(
        self,
        *,
        allowed_clients: frozenset[str],
    ) -> tuple[int, int]:
        if not allowed_clients or not allowed_clients <= _CLIENTS:
            raise ValueError("allowed_clients is invalid")
        raw = await self._rpc("expire_agent_batch_jobs", {
            "target_workspace_id": self.workspace_id,
            "target_client_ids": sorted(allowed_clients),
        })
        if not isinstance(raw, Mapping):
            raise BatchRepositoryError(
                "invalid_batch_expiry_response",
                retryable=False,
            )
        expired_count = raw.get("expired_job_count")
        released_microusd = raw.get("released_microusd")
        ambiguous_count = raw.get("ambiguous_claimed_count")
        if (
            isinstance(expired_count, bool)
            or not isinstance(expired_count, int)
            or expired_count < 0
            or isinstance(released_microusd, bool)
            or not isinstance(released_microusd, int)
            or released_microusd < 0
            or isinstance(ambiguous_count, bool)
            or not isinstance(ambiguous_count, int)
            or ambiguous_count < 0
        ):
            raise BatchRepositoryError(
                "invalid_batch_expiry_response",
                retryable=False,
            )
        return expired_count, ambiguous_count

    async def register_batch(
        self,
        *,
        worker_id: str,
        input_file_id: str,
        snapshot: BatchSnapshot,
        job_ids: Sequence[str],
    ) -> None:
        await self._rpc("register_agent_batch", {
            "target_workspace_id": self.workspace_id,
            "target_worker_id": self._worker(worker_id),
            "target_input_file_id": _provider_id(input_file_id, "input_file_id"),
            "target_batch_id": _provider_id(
                snapshot.provider_batch_id,
                "batch_id",
            ),
            "target_job_ids": [_uuid(item, "job_id") for item in job_ids],
        })

    async def list_active_batches(self, *, limit: int = 50) -> tuple[ActiveBatch, ...]:
        raw = await self._rpc("list_active_agent_batches", {
            "target_workspace_id": self.workspace_id,
            "target_limit": max(1, min(limit, 100)),
        })
        if raw is None:
            return ()
        if not isinstance(raw, list) or len(raw) > 100:
            raise BatchRepositoryError("invalid_active_batch_response", retryable=False)
        result: list[ActiveBatch] = []
        for value in raw:
            if not isinstance(value, Mapping):
                raise BatchRepositoryError(
                    "invalid_active_batch_response",
                    retryable=False,
                )
            result.append(ActiveBatch(
                provider_batch_id=_provider_id(
                    value.get("batch_id") or value.get("provider_batch_id"),
                    "batch_id",
                ),
                status=_text(
                    value.get("provider_status") or value.get("status"),
                    "batch_status",
                    maximum=32,
                ),
            ))
        return tuple(result)

    async def update_batch_poll(self, snapshot: BatchSnapshot) -> None:
        await self._rpc("update_agent_batch_poll", {
            "target_workspace_id": self.workspace_id,
            "target_batch_id": snapshot.provider_batch_id,
            "target_provider_status": snapshot.status,
            "target_output_file_id": snapshot.output_file_id,
            "target_error_file_id": snapshot.error_file_id,
        })

    async def complete_job(
        self,
        *,
        job_id: str,
        provider_batch_id: str,
        output: Mapping[str, object],
        input_tokens: int,
        output_tokens: int,
        actual_cost_usd: Decimal,
    ) -> str:
        normalized_job_id = _uuid(job_id, "job_id")
        normalized_batch_id = _provider_id(provider_batch_id, "batch_id")
        actual_cost_microusd = _microusd(actual_cost_usd)
        payload = dict(output)
        try:
            raw = await self._rpc("complete_agent_batch_job", {
                "target_workspace_id": self.workspace_id,
                "target_job_id": normalized_job_id,
                "target_batch_id": normalized_batch_id,
                "target_result_code": "needs_review",
                "target_result_payload": payload,
                "target_input_tokens": max(0, input_tokens),
                "target_output_tokens": max(0, output_tokens),
                "target_actual_cost_microusd": actual_cost_microusd,
            })
        except BatchRepositoryError as exc:
            if exc.code == "batch_database_invalid_response":
                raise BatchRepositoryError(
                    "invalid_batch_settlement_response",
                    retryable=True,
                ) from exc
            raise
        return self._settlement_signal(
            raw,
            job_id=normalized_job_id,
            provider_batch_id=normalized_batch_id,
            outcome_kind="completion",
            input_tokens=max(0, input_tokens),
            output_tokens=max(0, output_tokens),
            actual_cost_microusd=actual_cost_microusd,
            normal_statuses=frozenset({"completed"}),
        )

    async def fail_job(
        self,
        *,
        job_id: str,
        provider_batch_id: str | None,
        error_code: str,
        retryable: bool,
        available_at: datetime | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        actual_cost_usd: Decimal | None = None,
        charge_full_reservation: bool = False,
    ) -> str:
        if not _SAFE_CODE_RE.fullmatch(error_code):
            error_code = "batch_job_failed"
        exact_accounting = actual_cost_usd is not None
        exact_values_invalid = (
            exact_accounting
            and (
                not isinstance(actual_cost_usd, Decimal)
                or not actual_cost_usd.is_finite()
                or actual_cost_usd < 0
                or not isinstance(input_tokens, int)
                or isinstance(input_tokens, bool)
                or input_tokens < 0
                or not isinstance(output_tokens, int)
                or isinstance(output_tokens, bool)
                or output_tokens < 0
            )
        )
        unpaired_usage = (
            not exact_accounting
            and (input_tokens is not None or output_tokens is not None)
        )
        billed_failure_context_invalid = (
            (charge_full_reservation or exact_accounting)
            and (provider_batch_id is None or retryable)
        )
        if (
            not isinstance(charge_full_reservation, bool)
            or (charge_full_reservation and exact_accounting)
            or exact_values_invalid
            or unpaired_usage
            or billed_failure_context_invalid
        ):
            raise ValueError("failed Batch accounting is invalid")
        normalized_job_id = _uuid(job_id, "job_id")
        normalized_batch_id = (
            _provider_id(provider_batch_id, "batch_id")
            if provider_batch_id is not None
            else None
        )
        actual_cost_microusd = (
            _microusd(actual_cost_usd) if actual_cost_usd is not None else None
        )
        try:
            raw = await self._rpc("fail_agent_batch_job", {
                "target_workspace_id": self.workspace_id,
                "target_job_id": normalized_job_id,
                "target_expected_batch_id": normalized_batch_id,
                "target_error_code": error_code,
                "target_retryable": retryable,
                "target_available_at": (
                    available_at.isoformat() if available_at else None
                ),
                "target_input_tokens": input_tokens,
                "target_output_tokens": output_tokens,
                "target_actual_cost_microusd": actual_cost_microusd,
                "target_charge_full_reservation": charge_full_reservation,
            })
        except BatchRepositoryError as exc:
            if exc.code == "batch_database_invalid_response":
                raise BatchRepositoryError(
                    "invalid_batch_settlement_response",
                    retryable=True,
                ) from exc
            raise
        return self._settlement_signal(
            raw,
            job_id=normalized_job_id,
            provider_batch_id=normalized_batch_id,
            outcome_kind="failure",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            actual_cost_microusd=actual_cost_microusd,
            normal_statuses=_BATCH_JOB_STATUSES,
        )

    @staticmethod
    def _settlement_signal(
        raw: object,
        *,
        job_id: str,
        provider_batch_id: str | None,
        outcome_kind: str,
        input_tokens: int | None,
        output_tokens: int | None,
        actual_cost_microusd: int | None,
        normal_statuses: frozenset[str],
    ) -> str:
        invalid = BatchRepositoryError(
            "invalid_batch_settlement_response",
            retryable=True,
        )
        if not isinstance(raw, Mapping):
            raise invalid
        try:
            receipt_job_id = _uuid(raw.get("job_id"), "job_id")
        except BatchRepositoryError as exc:
            raise invalid from exc
        status = raw.get("status")
        if (
            raw.get("job_id") != job_id
            or receipt_job_id != job_id
            or not isinstance(raw.get("reused"), bool)
        ):
            raise invalid
        settlement = raw.get("settlement")
        if settlement is None:
            if status not in normal_statuses:
                raise invalid
            if outcome_kind == "completion":
                receipt_actual = raw.get("actual_cost_microusd")
                if (
                    status != "completed"
                    or isinstance(receipt_actual, bool)
                    or not isinstance(receipt_actual, int)
                    or receipt_actual != actual_cost_microusd
                ):
                    raise invalid
            if outcome_kind == "failure" and actual_cost_microusd is not None:
                receipt_input = raw.get("actual_input_tokens")
                receipt_output = raw.get("actual_output_tokens")
                receipt_actual = raw.get("actual_cost_microusd")
                if (
                    status != "failed"
                    or isinstance(receipt_input, bool)
                    or not isinstance(receipt_input, int)
                    or receipt_input != input_tokens
                    or isinstance(receipt_output, bool)
                    or not isinstance(receipt_output, int)
                    or receipt_output != output_tokens
                    or isinstance(receipt_actual, bool)
                    or not isinstance(receipt_actual, int)
                    or receipt_actual != actual_cost_microusd
                ):
                    raise invalid
            return str(status)
        if settlement != "cost_cap_breached":
            raise invalid
        receipt_input = raw.get("input_tokens")
        receipt_output = raw.get("output_tokens")
        cap = raw.get("reservation_cap_microusd")
        actual = raw.get("actual_cost_microusd")
        overage = raw.get("overage_microusd")
        budget_spent = raw.get("budget_spent_microusd")
        if (
            status != "failed"
            or raw.get("error_code") != "batch_cost_cap_breached"
            or provider_batch_id is None
            or raw.get("provider_batch_id") != provider_batch_id
            or raw.get("outcome_kind") != outcome_kind
            or isinstance(receipt_input, bool)
            or not isinstance(receipt_input, int)
            or receipt_input < 0
            or receipt_input != input_tokens
            or isinstance(receipt_output, bool)
            or not isinstance(receipt_output, int)
            or receipt_output < 0
            or receipt_output != output_tokens
            or isinstance(cap, bool)
            or not isinstance(cap, int)
            or cap <= 0
            or isinstance(actual, bool)
            or not isinstance(actual, int)
            or actual != actual_cost_microusd
            or actual <= cap
            or isinstance(overage, bool)
            or not isinstance(overage, int)
            or overage != actual - cap
            or isinstance(budget_spent, bool)
            or not isinstance(budget_spent, int)
            or budget_spent != cap
            or raw.get("resolution_status") != "unresolved"
            or not isinstance(raw.get("outcome_fingerprint"), str)
            or _SHA256_RE.fullmatch(raw["outcome_fingerprint"]) is None
        ):
            raise invalid
        return "cost_cap_breached"

    async def finalize_batch(self, provider_batch_id: str) -> None:
        await self._rpc("finalize_agent_batch", {
            "target_workspace_id": self.workspace_id,
            "target_batch_id": _provider_id(provider_batch_id, "batch_id"),
        })

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
            raise BatchRepositoryError(
                "batch_database_unavailable",
                retryable=True,
            ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            postgres_code = None
            try:
                error_body = response.json()
                if isinstance(error_body, Mapping):
                    postgres_code = error_body.get("code")
            except ValueError:
                pass
            if postgres_code == "55P03":
                raise BatchRepositoryError(
                    "batch_provider_create_fenced",
                    retryable=True,
                )
            raise BatchRepositoryError(
                "batch_database_rpc_failed",
                retryable=response.status_code in {
                    408,
                    409,
                    425,
                    429,
                    500,
                    502,
                    503,
                    504,
                },
            )
        try:
            return response.json()
        except ValueError as exc:
            raise BatchRepositoryError(
                "batch_database_invalid_response",
                retryable=False,
            ) from exc

    def _claimed_item(self, value: object) -> BatchWorkItem:
        if not isinstance(value, Mapping):
            raise BatchRepositoryError("invalid_batch_claim_response", retryable=False)
        payload = value.get("input_payload")
        if not isinstance(payload, Mapping):
            raise BatchRepositoryError("invalid_batch_claim_response", retryable=False)
        priority = value.get("priority")
        if isinstance(priority, int):
            priority = _PRIORITY_FROM_RANK.get(priority)
        max_cost = value.get("max_cost_microusd")
        if not isinstance(max_cost, int) or max_cost <= 0:
            raise BatchRepositoryError("invalid_batch_claim_response", retryable=False)
        estimated_output = payload.get("estimated_output_tokens")
        if not isinstance(estimated_output, int):
            raise BatchRepositoryError("invalid_batch_claim_response", retryable=False)
        output_schema = payload.get("output_schema")
        if not isinstance(output_schema, Mapping):
            raise BatchRepositoryError("invalid_batch_claim_response", retryable=False)
        recovery_required = value.get("recovery_required")
        if not isinstance(recovery_required, bool):
            raise BatchRepositoryError("invalid_batch_claim_response", retryable=False)
        attempt_started_at = (
            _datetime(value.get("attempt_started_at"), "attempt_started_at")
            if recovery_required
            else None
        )
        try:
            item = BatchWorkItem(
                job_id=_uuid(value.get("job_id"), "job_id"),
                client_id=_text(value.get("client_id"), "client_id", maximum=32),
                agent_id=_text(value.get("agent_id"), "agent_id", maximum=64),
                workflow_kind=_text(
                    value.get("workflow_kind"),
                    "workflow_kind",
                    maximum=80,
                ),
                stage=_text(value.get("stage"), "stage", maximum=32),
                attempt=value.get("attempt"),
                priority=priority,
                risk_tier=payload.get("risk_tier"),
                deadline_at=_datetime(
                    value.get("deadline") or value.get("deadline_at"),
                    "deadline",
                ),
                model_tier=_text(value.get("model_tier"), "model_tier", maximum=8),
                model=_text(value.get("model"), "model", maximum=80),
                instructions=_text(
                    payload.get("instructions"),
                    "instructions",
                    maximum=20_000,
                ),
                input_text=_text(
                    payload.get("input"),
                    "input",
                    maximum=1_000_000,
                ),
                input_sha256=_text(
                    value.get("input_sha256"),
                    "input_sha256",
                    maximum=64,
                ),
                output_schema=dict(output_schema),
                max_output_tokens=value.get("max_output_tokens"),
                estimated_input_tokens=value.get("estimated_input_tokens"),
                estimated_output_tokens=estimated_output,
                max_cost_usd=Decimal(max_cost) / Decimal("1000000"),
                approval_required=payload.get("approval_required") is True,
                interactive=payload.get("interactive") is True,
                incident_or_release_blocker=(
                    payload.get("incident_or_release_blocker") is True
                ),
                live_tools_required=payload.get("live_tools_required") is True,
                source_snapshot_complete=(
                    payload.get("source_snapshot_complete") is True
                ),
                input_immutable=payload.get("input_immutable") is True,
                retry_idempotent=payload.get("retry_idempotent") is True,
                remaining_batch_stages=payload.get("remaining_batch_stages", 1),
                budget_reserved=True,
                recovery_required=recovery_required,
                attempt_started_at=attempt_started_at,
            )
        except (TypeError, ValueError) as exc:
            raise BatchRepositoryError(
                "invalid_batch_claim_response",
                retryable=False,
            ) from exc
        if (
            value.get("latency_class") != "batch_24h"
            or value.get("custom_id") != item.custom_id
        ):
            raise BatchRepositoryError("invalid_batch_claim_response", retryable=False)
        return item

    @staticmethod
    def _canary_binding(
        *,
        config_subject_sha256: str,
        config_approval_id: str,
        dispatch_subject_sha256: str,
        dispatch_approval_id: str,
        job_id: str,
        input_sha256: str,
        request_sha256: str,
        expires_at: datetime,
        hard_limit_usd: Decimal,
    ) -> dict[str, object]:
        return {
            "target_config_subject_sha256": _sha256_argument(
                config_subject_sha256,
                "config_subject_sha256",
            ),
            "target_config_approval_id": _uuid_argument(
                config_approval_id,
                "config_approval_id",
            ),
            "target_dispatch_subject_sha256": _sha256_argument(
                dispatch_subject_sha256,
                "dispatch_subject_sha256",
            ),
            "target_dispatch_approval_id": _uuid_argument(
                dispatch_approval_id,
                "dispatch_approval_id",
            ),
            "target_job_id": _uuid_argument(job_id, "job_id"),
            "target_input_sha256": _sha256_argument(
                input_sha256,
                "input_sha256",
            ),
            "target_request_sha256": _sha256_argument(
                request_sha256,
                "request_sha256",
            ),
            "target_expires_at": _canary_expiry(expires_at).isoformat(),
            "target_hard_limit_microusd": _canary_limit_microusd(
                hard_limit_usd
            ),
        }

    @staticmethod
    def _validate_canary_receipt(
        value: object,
        *,
        binding: Mapping[str, object],
        require_consumed: bool,
        response_code: str,
    ) -> None:
        if not isinstance(value, Mapping):
            raise BatchRepositoryError(response_code, retryable=False)
        expected = {
            "canary_config_subject_sha256": binding[
                "target_config_subject_sha256"
            ],
            "canary_config_approval_id": binding[
                "target_config_approval_id"
            ],
            "canary_dispatch_subject_sha256": binding[
                "target_dispatch_subject_sha256"
            ],
            "canary_dispatch_approval_id": binding[
                "target_dispatch_approval_id"
            ],
            "canary_job_id": binding["target_job_id"],
            "canary_input_sha256": binding["target_input_sha256"],
            "canary_request_sha256": binding["target_request_sha256"],
            "canary_hard_limit_microusd": binding[
                "target_hard_limit_microusd"
            ],
            "canary_max_provider_batches": 1,
        }
        if any(
            value.get(key) != expected_value
            for key, expected_value in expected.items()
        ):
            raise BatchRepositoryError(response_code, retryable=False)
        try:
            receipt_expiry = _datetime(
                value.get("canary_expires_at"),
                "canary_expires_at",
            )
            expected_expiry = _datetime(
                binding["target_expires_at"],
                "canary_expires_at",
            )
        except BatchRepositoryError as exc:
            raise BatchRepositoryError(response_code, retryable=False) from exc
        consumed = value.get("canary_provider_batches_consumed")
        consumed_at = value.get("canary_consumed_at")
        reused = value.get("reused")
        invalid_consumption = (
            require_consumed
            and (
                isinstance(consumed, bool)
                or consumed != 1
                or not isinstance(consumed_at, str)
            )
        ) or (
            not require_consumed
            and (
                isinstance(consumed, bool)
                or not isinstance(consumed, int)
                or consumed not in {0, 1}
                or (consumed == 0 and consumed_at is not None)
                or (consumed == 1 and not isinstance(consumed_at, str))
            )
        )
        if (
            receipt_expiry != expected_expiry
            or invalid_consumption
            or not isinstance(reused, bool)
        ):
            raise BatchRepositoryError(response_code, retryable=False)
        if consumed_at is not None:
            try:
                _datetime(consumed_at, "canary_consumed_at")
            except BatchRepositoryError as exc:
                raise BatchRepositoryError(response_code, retryable=False) from exc

    @staticmethod
    def _worker(value: object) -> str:
        if (
            not isinstance(value, str)
            or not 8 <= len(value) <= 120
            or re.fullmatch(r"[A-Za-z0-9:_-]+", value) is None
        ):
            raise ValueError("worker_id is invalid")
        return value


def batch_dispatch_key(items: Sequence[BatchWorkItem]) -> str:
    if not items:
        raise ValueError("cannot build a dispatch key for an empty batch")
    fingerprint = [
        {
            "custom_id": item.custom_id,
            "input_sha256": item.input_sha256,
            "model": item.model,
            "request_sha256": item.request_sha256,
        }
        for item in sorted(items, key=lambda item: item.custom_id)
    ]
    encoded = json.dumps(
        fingerprint,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def provider_create_request_sha256(
    *,
    input_file_id: str,
    completion_window: str,
    metadata: Mapping[str, str],
    output_expires_after: Mapping[str, object],
) -> str:
    if (
        not isinstance(input_file_id, str)
        or _PROVIDER_ID_RE.fullmatch(input_file_id) is None
        or completion_window != BATCH_COMPLETION_WINDOW
        or not isinstance(metadata, Mapping)
        or not 1 <= len(metadata) <= 16
        or output_expires_after != {
            "anchor": "created_at",
            "seconds": BATCH_OUTPUT_RETENTION_SECONDS,
        }
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not 1 <= len(key) <= 64
            or not 1 <= len(value) <= 512
            for key, value in metadata.items()
        )
    ):
        raise ValueError("provider create request binding is invalid")
    encoded = json.dumps(
        {
            "schema": "coineasy.batch.provider_create.v1",
            "input_file_id": input_file_id,
            "endpoint": "/v1/responses",
            "completion_window": completion_window,
            "metadata": dict(metadata),
            "output_expires_after": dict(output_expires_after),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
