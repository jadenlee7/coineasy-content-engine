from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.batch.models import BatchWorkItem, canonical_input_sha256
from core.batch.policy import (
    BatchPolicy,
    actual_batch_cost_usd,
    estimated_batch_cost_usd,
    maximum_batch_cost_usd,
    reserved_batch_cost_usd,
)


NOW = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)
SCHEMA = {
    "type": "object",
    "properties": {"draft": {"type": "string"}},
    "required": ["draft"],
    "additionalProperties": False,
}


def _item(**overrides) -> BatchWorkItem:
    values = {
        "job_id": "11111111-1111-4111-8111-111111111111",
        "client_id": "squid",
        "agent_id": "squid_client_agent",
        "workflow_kind": "official_source_nonurgent_pack",
        "stage": "generate",
        "attempt": 1,
        "priority": "P2",
        "risk_tier": "T1",
        "deadline_at": NOW + timedelta(hours=30),
        "model_tier": "S",
        "model": "gpt-5.6-luna",
        "instructions": "Produce a source-locked Korean draft.",
        "input_text": "Immutable official source evidence.",
        "output_schema": SCHEMA,
        "max_output_tokens": 2_000,
        "estimated_input_tokens": 10_000,
        "estimated_output_tokens": 1_000,
        "max_cost_usd": Decimal("0.05"),
    }
    values.update(overrides)
    values.setdefault(
        "input_sha256",
        canonical_input_sha256(
            instructions=values["instructions"],
            input_text=values["input_text"],
            output_schema=values["output_schema"],
        ),
    )
    return BatchWorkItem(**values)


def test_eligible_squid_work_routes_to_batch_and_reserves_120_percent():
    decision = BatchPolicy(allowed_clients=frozenset({"squid"})).route(
        _item(),
        now=NOW,
    )

    assert decision.mode == "batch"
    assert decision.reason == "batch_first_eligible"
    assert estimated_batch_cost_usd(_item()) == Decimal("0.001600")
    assert decision.reserved_cost_usd == Decimal("0.001920")
    assert decision.reserved_cost_usd == reserved_batch_cost_usd(_item())


@pytest.mark.parametrize(
    "model,cached_input_tokens,expected",
    [
        ("gpt-5.6-luna", 0, Decimal("0.070000")),
        ("gpt-5.6-luna", 100_000, Decimal("0.061000")),
        ("gpt-5.6-terra", 0, Decimal("0.700000")),
        ("gpt-5.6-terra", 100_000, Decimal("0.610000")),
    ],
)
def test_batch_cost_uses_current_model_rates_and_50_percent_discount(
    model,
    cached_input_tokens,
    expected,
):
    assert actual_batch_cost_usd(
        model=model,
        input_tokens=100_000,
        cached_input_tokens=cached_input_tokens,
        output_tokens=100_000,
    ) == expected


@pytest.mark.parametrize(
    "hours, remaining_stages, expected_mode",
    [
        (25 + 59 / 60, 1, "manual_sync"),
        (26, 1, "batch"),
        (51 + 59 / 60, 2, "manual_sync"),
        (52, 2, "batch"),
    ],
)
def test_batch_deadline_boundary_is_26_hours_per_remaining_stage(
    hours,
    remaining_stages,
    expected_mode,
):
    decision = BatchPolicy(allowed_clients=frozenset({"squid"})).route(
        _item(
            deadline_at=NOW + timedelta(hours=hours),
            remaining_batch_stages=remaining_stages,
        ),
        now=NOW,
    )
    assert decision.mode == expected_mode


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"interactive": True}, "interactive_request"),
        ({"incident_or_release_blocker": True}, "incident_or_release_blocker"),
        ({"live_tools_required": True}, "live_tool_required"),
        ({"risk_tier": "T3"}, "risk_requires_manual_sync"),
    ],
)
def test_urgent_or_live_work_never_silently_uses_batch(overrides, reason):
    decision = BatchPolicy(allowed_clients=frozenset({"squid"})).route(
        _item(**overrides),
        now=NOW,
    )
    assert decision.mode == "manual_sync"
    assert decision.reason == reason


def test_batch_failure_policy_does_not_encode_an_automatic_sync_fallback():
    policy = BatchPolicy(allowed_clients=frozenset({"squid"}))

    assert policy.route(_item(budget_reserved=False), now=NOW).mode == "waiting_budget"
    assert policy.route(
        _item(client_id="yellow"),
        now=NOW,
    ).mode == "out_of_scope"
    assert policy.route(
        _item(input_immutable=False),
        now=NOW,
    ).mode == "rejected"


def test_cost_cap_fails_closed_before_submission():
    decision = BatchPolicy(allowed_clients=frozenset({"squid"})).route(
        _item(
            model_tier="M",
            model="gpt-5.6-terra",
            max_cost_usd=Decimal("0.01"),
        ),
        now=NOW,
    )
    assert decision.mode == "waiting_budget"
    assert decision.reason == "job_cost_cap_exceeded"


def test_provider_maximum_not_just_estimate_must_fit_the_hard_cap():
    output_bound = _item(
        max_output_tokens=128_000,
        estimated_output_tokens=1,
    )
    input_bound = _item(
        input_text="a" * 600_000,
        estimated_input_tokens=1,
        max_output_tokens=1,
        estimated_output_tokens=1,
    )

    assert maximum_batch_cost_usd(output_bound) > output_bound.max_cost_usd
    assert BatchPolicy(allowed_clients=frozenset({"squid"})).route(
        output_bound,
        now=NOW,
    ).reason == "job_hard_cap_insufficient"
    assert BatchPolicy(allowed_clients=frozenset({"squid"})).route(
        input_bound,
        now=NOW,
    ).reason == "job_hard_cap_insufficient"


def test_long_context_multiplier_is_included_in_reservation():
    item = _item(
        estimated_input_tokens=300_000,
        estimated_output_tokens=10_000,
        max_output_tokens=10_000,
        max_cost_usd=Decimal("0.05"),
    )
    assert estimated_batch_cost_usd(item) == Decimal("0.069000")
    assert reserved_batch_cost_usd(item) == Decimal("0.082800")


def test_prompt_hash_and_model_tier_are_fail_closed():
    with pytest.raises(ValueError, match="immutable prompt"):
        _item(input_sha256="a" * 64)
    with pytest.raises(ValueError, match="model does not match"):
        _item(model="gpt-5.6-terra")
    with pytest.raises(ValueError, match="must require approval"):
        _item(approval_required=False)
    with pytest.raises(ValueError, match="model-tier hard cap"):
        _item(max_cost_usd=Decimal("0.051"))


def test_batch_request_disables_cache_writes_tools_and_storage():
    request = _item().batch_request()
    body = request["body"]

    assert request["url"] == "/v1/responses"
    assert request["method"] == "POST"
    assert body["model"] == "gpt-5.6-luna"
    assert body["store"] is False
    assert body["reasoning"] == {"effort": "low"}
    assert body["prompt_cache_options"] == {"mode": "explicit"}
    assert "prompt_cache_breakpoint" not in json.dumps(body, sort_keys=True)
    assert body["text"]["format"]["strict"] is True
    assert "tools" not in body


def test_request_sha256_binds_provider_cost_deadline_and_job_controls():
    item = _item()
    equivalent = _item(
        deadline_at=item.deadline_at.astimezone(timezone(timedelta(hours=9))),
        max_cost_usd=Decimal("0.0500"),
        output_schema={
            "required": ["draft"],
            "properties": {"draft": {"type": "string"}},
            "type": "object",
            "additionalProperties": False,
        },
    )

    assert item.request_sha256 == equivalent.request_sha256
    assert item.request_sha256 == (
        "5f36f8c2da03cbd45a13078e3d1770fa20075a6cb77b239c40498a00ac35cbe1"
    )

    changed_items = (
        _item(max_output_tokens=1_999),
        _item(estimated_input_tokens=9_999),
        _item(estimated_output_tokens=999),
        _item(max_cost_usd=Decimal("0.04")),
        _item(deadline_at=item.deadline_at + timedelta(seconds=1)),
        _item(priority="P1"),
        _item(
            model_tier="M",
            model="gpt-5.6-terra",
            max_cost_usd=Decimal("0.50"),
        ),
    )
    assert all(
        changed.request_sha256 != item.request_sha256
        for changed in changed_items
    )
