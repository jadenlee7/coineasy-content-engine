from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, ROUND_UP

from core.batch.models import BatchRouteDecision, BatchWorkItem


_MODEL_PRICES_PER_MILLION = {
    "gpt-5.6-luna": (
        Decimal("0.20"),
        Decimal("0.02"),
        Decimal("1.20"),
    ),
    "gpt-5.6-terra": (
        Decimal("2.00"),
        Decimal("0.20"),
        Decimal("12.00"),
    ),
}
_BATCH_DISCOUNT = Decimal("0.50")
_RESERVATION_BUFFER = Decimal("1.20")
_LONG_CONTEXT_THRESHOLD = 272_000
_BATCH_STAGE_SLACK_HOURS = 26
_MAX_MODEL_INPUT_TOKENS = 1_050_000
_PROVIDER_INPUT_TOKEN_OVERHEAD = 4_096


def estimated_batch_cost_usd(item: BatchWorkItem) -> Decimal:
    return actual_batch_cost_usd(
        model=item.model,
        input_tokens=item.estimated_input_tokens,
        cached_input_tokens=0,
        output_tokens=item.estimated_output_tokens,
    )


def actual_batch_cost_usd(
    *,
    model: str,
    input_tokens: int,
    cached_input_tokens: int = 0,
    output_tokens: int,
) -> Decimal:
    if model not in _MODEL_PRICES_PER_MILLION:
        raise ValueError("unsupported Batch billing model")
    if (
        input_tokens < 0
        or cached_input_tokens < 0
        or cached_input_tokens > input_tokens
        or output_tokens < 0
    ):
        raise ValueError("token usage cannot be negative")
    input_rate, cached_input_rate, output_rate = _MODEL_PRICES_PER_MILLION[model]
    if input_tokens > _LONG_CONTEXT_THRESHOLD:
        input_rate *= Decimal("2")
        cached_input_rate *= Decimal("2")
        output_rate *= Decimal("1.5")
    uncached_input_tokens = input_tokens - cached_input_tokens
    cost = (
        Decimal(uncached_input_tokens) * input_rate
        + Decimal(cached_input_tokens) * cached_input_rate
        + Decimal(output_tokens) * output_rate
    ) / Decimal("1000000")
    return (cost * _BATCH_DISCOUNT).quantize(
        Decimal("0.000001"),
        rounding=ROUND_UP,
    )


def reserved_batch_cost_usd(item: BatchWorkItem) -> Decimal:
    return (estimated_batch_cost_usd(item) * _RESERVATION_BUFFER).quantize(
        Decimal("0.000001"),
        rounding=ROUND_UP,
    )


def maximum_batch_cost_usd(item: BatchWorkItem) -> Decimal:
    """Conservatively bound one request before it can consume provider spend.

    Byte length is an upper bound for byte-level BPE tokens. The fixed overhead
    covers provider-side framing that is not present in the serialized body.
    """
    serialized = json.dumps(
        item.response_body(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    input_token_bound = len(serialized) + _PROVIDER_INPUT_TOKEN_OVERHEAD
    if input_token_bound > _MAX_MODEL_INPUT_TOKENS:
        raise ValueError("Batch request exceeds the model input bound")
    return actual_batch_cost_usd(
        model=item.model,
        input_tokens=input_token_bound,
        cached_input_tokens=0,
        output_tokens=item.max_output_tokens,
    )


class BatchPolicy:
    def __init__(self, *, allowed_clients: frozenset[str]):
        if not allowed_clients or not allowed_clients <= {
            "yellow",
            "origintrail",
            "squid",
            "babylon",
        }:
            raise ValueError("allowed_clients is invalid")
        self.allowed_clients = allowed_clients

    def route(
        self,
        item: BatchWorkItem,
        *,
        now: datetime,
    ) -> BatchRouteDecision:
        if now.tzinfo is None:
            raise ValueError("routing clock must be timezone-aware")
        if item.client_id not in self.allowed_clients:
            return BatchRouteDecision("out_of_scope", "pilot_client_not_enabled")
        if (
            not item.source_snapshot_complete
            or not item.input_immutable
            or not item.retry_idempotent
        ):
            return BatchRouteDecision("rejected", "batch_evidence_not_immutable")
        if item.risk_tier in {"T3", "T4"}:
            return BatchRouteDecision("manual_sync", "risk_requires_manual_sync")
        if item.interactive:
            return BatchRouteDecision("manual_sync", "interactive_request")
        if item.incident_or_release_blocker:
            return BatchRouteDecision("manual_sync", "incident_or_release_blocker")
        if item.live_tools_required:
            return BatchRouteDecision("manual_sync", "live_tool_required")
        minimum_slack = _BATCH_STAGE_SLACK_HOURS * item.remaining_batch_stages
        slack_hours = (item.deadline_at - now).total_seconds() / 3600
        if slack_hours < minimum_slack:
            return BatchRouteDecision("manual_sync", "batch_deadline_too_close")
        reservation = reserved_batch_cost_usd(item)
        if reservation > item.max_cost_usd:
            return BatchRouteDecision("waiting_budget", "job_cost_cap_exceeded")
        try:
            maximum_cost = maximum_batch_cost_usd(item)
        except ValueError:
            return BatchRouteDecision("rejected", "batch_input_exceeds_context")
        if maximum_cost > item.max_cost_usd:
            return BatchRouteDecision(
                "waiting_budget",
                "job_hard_cap_insufficient",
            )
        if not item.budget_reserved:
            return BatchRouteDecision("waiting_budget", "daily_budget_unavailable")
        return BatchRouteDecision(
            "batch",
            "batch_first_eligible",
            reserved_cost_usd=reservation,
        )
