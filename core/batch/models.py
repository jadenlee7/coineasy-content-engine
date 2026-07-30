from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping


_CLIENTS = frozenset({"yellow", "origintrail", "squid", "babylon"})
_MODEL_BY_TIER = {
    "S": "gpt-5.6-luna",
    "M": "gpt-5.6-terra",
}
_MAX_COST_BY_TIER = {
    "S": Decimal("0.05"),
    "M": Decimal("0.50"),
}
_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
_RISK_TIERS = frozenset({"T0", "T1", "T2", "T3", "T4"})
_STAGES = frozenset({"generate", "review", "qa", "summarize"})
_WORKFLOW_KINDS = frozenset({
    "official_source_nonurgent_pack",
    "daily_digest",
    "weekly_client_report",
    "content_qa",
    "naver_seo_article",
    "naver_seo_refresh",
    "partnership_screen",
    "partnership_deep_brief",
    "analytics_narrative",
    "visual_copy_qa",
    "release_changelog",
})
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,199}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_TERMINAL_BATCH_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})
_BATCH_STATUSES = _TERMINAL_BATCH_STATUSES | frozenset({
    "validating",
    "in_progress",
    "finalizing",
    "cancelling",
})


def canonical_input_sha256(
    *,
    instructions: str,
    input_text: str,
    output_schema: Mapping[str, object],
) -> str:
    payload = {
        "input": input_text,
        "instructions": instructions,
        "output_schema": dict(output_schema),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


@dataclass(frozen=True)
class BatchWorkItem:
    job_id: str
    client_id: str
    agent_id: str
    workflow_kind: str
    stage: str
    attempt: int
    priority: str
    risk_tier: str
    deadline_at: datetime
    model_tier: str
    model: str
    instructions: str
    input_text: str
    input_sha256: str
    output_schema: Mapping[str, object]
    max_output_tokens: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    max_cost_usd: Decimal
    approval_required: bool = True
    interactive: bool = False
    incident_or_release_blocker: bool = False
    live_tools_required: bool = False
    source_snapshot_complete: bool = True
    input_immutable: bool = True
    retry_idempotent: bool = True
    remaining_batch_stages: int = 1
    budget_reserved: bool = True
    recovery_required: bool = False
    attempt_started_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", _uuid(self.job_id, "job_id"))
        if self.client_id not in _CLIENTS:
            raise ValueError("unsupported batch client")
        if not _IDENTIFIER_RE.fullmatch(self.agent_id):
            raise ValueError("agent_id is invalid")
        if self.workflow_kind not in _WORKFLOW_KINDS:
            raise ValueError("unsupported workflow_kind")
        if self.stage not in _STAGES:
            raise ValueError("unsupported batch stage")
        if not 1 <= self.attempt <= 2:
            raise ValueError("batch attempt must be 1 or 2")
        if self.priority not in _PRIORITIES:
            raise ValueError("priority is invalid")
        if self.risk_tier not in _RISK_TIERS:
            raise ValueError("risk_tier is invalid")
        if self.deadline_at.tzinfo is None:
            raise ValueError("deadline_at must be timezone-aware")
        if self.model_tier not in _MODEL_BY_TIER:
            raise ValueError("Batch pilot supports only S and M model tiers")
        if self.model != _MODEL_BY_TIER[self.model_tier]:
            raise ValueError("model does not match model_tier")
        if not 1 <= len(self.instructions.strip()) <= 20_000:
            raise ValueError("instructions must contain 1 to 20000 characters")
        if not 1 <= len(self.input_text.strip()) <= 1_000_000:
            raise ValueError("input_text must contain 1 to 1000000 characters")
        if not _SHA256_RE.fullmatch(self.input_sha256):
            raise ValueError("input_sha256 is invalid")
        expected_hash = canonical_input_sha256(
            instructions=self.instructions,
            input_text=self.input_text,
            output_schema=self.output_schema,
        )
        if self.input_sha256 != expected_hash:
            raise ValueError("input_sha256 does not match the immutable prompt")
        if not isinstance(self.output_schema, Mapping):
            raise ValueError("output_schema must be a mapping")
        if (
            self.output_schema.get("type") != "object"
            or self.output_schema.get("additionalProperties") is not False
        ):
            raise ValueError(
                "output_schema must be a closed JSON object schema"
            )
        if not 1 <= self.max_output_tokens <= 128_000:
            raise ValueError("max_output_tokens must be between 1 and 128000")
        if not 1 <= self.estimated_input_tokens <= 1_050_000:
            raise ValueError("estimated_input_tokens is out of range")
        if not 1 <= self.estimated_output_tokens <= self.max_output_tokens:
            raise ValueError("estimated_output_tokens is out of range")
        if (
            self.max_cost_usd <= 0
            or self.max_cost_usd > _MAX_COST_BY_TIER[self.model_tier]
        ):
            raise ValueError("max_cost_usd exceeds the model-tier hard cap")
        if self.approval_required is not True:
            raise ValueError("Batch pilot outputs must require approval")
        if not 1 <= self.remaining_batch_stages <= 2:
            raise ValueError("remaining_batch_stages must be 1 or 2")
        if not isinstance(self.recovery_required, bool):
            raise ValueError("recovery_required must be a boolean")
        if self.attempt_started_at is not None and (
            not isinstance(self.attempt_started_at, datetime)
            or self.attempt_started_at.tzinfo is None
        ):
            raise ValueError("attempt_started_at must be timezone-aware")
        if self.recovery_required and self.attempt_started_at is None:
            raise ValueError("recovery requires an attempt start time")

    @property
    def custom_id(self) -> str:
        return f"{self.job_id}:{self.stage}:{self.attempt}"

    def response_body(self) -> dict[str, object]:
        effort = "low" if self.model_tier == "S" else "medium"
        return {
            "model": self.model,
            "instructions": self.instructions.strip(),
            "input": self.input_text.strip(),
            "max_output_tokens": self.max_output_tokens,
            "reasoning": {"effort": effort},
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": f"coineasy_{self.workflow_kind}_{self.stage}"[:64],
                    "strict": True,
                    "schema": dict(self.output_schema),
                },
                "verbosity": "low" if self.model_tier == "S" else "medium",
            },
        }

    def batch_request(self) -> dict[str, object]:
        return {
            "custom_id": self.custom_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": self.response_body(),
        }


@dataclass(frozen=True)
class BatchBundle:
    bundle_id: str
    dispatch_key: str
    client_id: str
    items: tuple[BatchWorkItem, ...]
    reserved_cost_usd: Decimal
    provider_input_file_id: str | None = None
    provider_batch_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundle_id", _uuid(self.bundle_id, "bundle_id"))
        if not _SHA256_RE.fullmatch(self.dispatch_key):
            raise ValueError("dispatch_key is invalid")
        if self.client_id not in _CLIENTS:
            raise ValueError("unsupported batch client")
        if not 1 <= len(self.items) <= 50_000:
            raise ValueError("bundle must contain 1 to 50000 items")
        if any(item.client_id != self.client_id for item in self.items):
            raise ValueError("a bundle cannot cross client boundaries")
        if len({item.custom_id for item in self.items}) != len(self.items):
            raise ValueError("bundle custom_id values must be unique")
        if len({item.model for item in self.items}) != 1:
            raise ValueError("a bundle must use one model")
        if self.reserved_cost_usd <= 0:
            raise ValueError("reserved_cost_usd must be positive")
        for value in (self.provider_input_file_id, self.provider_batch_id):
            if value is not None and not _PROVIDER_ID_RE.fullmatch(value):
                raise ValueError("provider id is invalid")


@dataclass(frozen=True)
class BatchRouteDecision:
    mode: str
    reason: str
    reserved_cost_usd: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.mode not in {
            "batch",
            "manual_sync",
            "waiting_budget",
            "out_of_scope",
            "rejected",
        }:
            raise ValueError("invalid route mode")
        if not _IDENTIFIER_RE.fullmatch(self.reason):
            raise ValueError("route reason is invalid")
        if self.reserved_cost_usd < 0:
            raise ValueError("reserved_cost_usd cannot be negative")


@dataclass(frozen=True)
class BatchSnapshot:
    provider_batch_id: str
    input_file_id: str
    status: str
    output_file_id: str | None
    error_file_id: str | None
    request_total: int
    request_completed: int
    request_failed: int
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        for value in (
            self.provider_batch_id,
            self.input_file_id,
            self.output_file_id,
            self.error_file_id,
        ):
            if value is not None and not _PROVIDER_ID_RE.fullmatch(value):
                raise ValueError("provider id is invalid")
        if self.status not in _BATCH_STATUSES:
            raise ValueError("provider batch status is invalid")
        counts = (
            self.request_total,
            self.request_completed,
            self.request_failed,
            self.input_tokens,
            self.output_tokens,
        )
        if any(not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("provider batch counts are invalid")
        if self.request_completed + self.request_failed > self.request_total:
            raise ValueError("provider batch counts are inconsistent")
        if (
            self.status == "completed"
            and self.output_file_id is None
            and self.error_file_id is None
        ):
            raise ValueError("completed provider batch requires a result file")

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_BATCH_STATUSES


@dataclass(frozen=True)
class BatchItemOutcome:
    custom_id: str
    ok: bool
    output: Mapping[str, object] | None
    error_code: str | None
    model: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_basis: str = "none"

    def __post_init__(self) -> None:
        if not 10 <= len(self.custom_id) <= 255:
            raise ValueError("custom_id is invalid")
        if self.ok != (self.output is not None and self.error_code is None):
            raise ValueError("batch item outcome is inconsistent")
        if self.error_code is not None and not _IDENTIFIER_RE.fullmatch(
            self.error_code
        ):
            raise ValueError("batch item error_code is invalid")
        if self.cost_basis not in {"none", "usage", "reservation"}:
            raise ValueError("batch item cost basis is invalid")
        if self.ok and self.cost_basis != "usage":
            raise ValueError("successful batch item requires usage accounting")
        if self.cost_basis == "usage":
            if self.model not in set(_MODEL_BY_TIER.values()):
                raise ValueError("usage-accounted batch item model is invalid")
        elif (
            self.model is not None
            or self.input_tokens != 0
            or self.cached_input_tokens != 0
            or self.output_tokens != 0
        ):
            raise ValueError("non-usage outcome cannot report token usage")
        if (
            self.input_tokens < 0
            or self.cached_input_tokens < 0
            or self.cached_input_tokens > self.input_tokens
            or self.output_tokens < 0
        ):
            raise ValueError("batch item usage is invalid")


@dataclass(frozen=True)
class ActiveBatch:
    provider_batch_id: str
    status: str

    def __post_init__(self) -> None:
        if not _PROVIDER_ID_RE.fullmatch(self.provider_batch_id):
            raise ValueError("provider batch id is invalid")
        if self.status not in _BATCH_STATUSES:
            raise ValueError("provider batch status is invalid")
