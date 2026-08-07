from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.batch.bridge import BatchQueueBridge
from core.batch.models import BatchWorkItem, canonical_input_sha256
from core.batch.policy import BatchPolicy


NOW = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)
SCHEMA = {
    "type": "object",
    "properties": {"headline": {"type": "string"}},
    "required": ["headline"],
    "additionalProperties": False,
}


class QueueRepository:
    def __init__(self):
        self.budget_calls = []
        self.queue_calls = []

    async def configure_daily_budget(self, **kwargs):
        self.budget_calls.append(kwargs)

    async def queue_job(self, **kwargs):
        self.queue_calls.append(kwargs)
        return kwargs["item"].job_id


def item(*, deadline_at: datetime) -> BatchWorkItem:
    instructions = "Write a review-only draft."
    input_text = "Immutable OriginTrail evidence."
    return BatchWorkItem(
        job_id="11111111-1111-4111-8111-111111111111",
        client_id="origintrail",
        agent_id="origintrail_client_agent",
        workflow_kind="official_source_nonurgent_pack",
        stage="generate",
        attempt=1,
        priority="P2",
        risk_tier="T1",
        deadline_at=deadline_at,
        model_tier="S",
        model="gpt-5.6-luna",
        instructions=instructions,
        input_text=input_text,
        output_schema=SCHEMA,
        max_output_tokens=1_000,
        estimated_input_tokens=1_000,
        estimated_output_tokens=500,
        max_cost_usd=Decimal("0.05"),
        input_sha256=canonical_input_sha256(
            instructions=instructions,
            input_text=input_text,
            output_schema=SCHEMA,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deadline_at",
    [
        NOW + timedelta(hours=30),
        NOW + timedelta(hours=1),
    ],
    ids=["deadline-open", "deadline-drain"],
)
async def test_existing_readback_is_always_replay_only(deadline_at):
    repository = QueueRepository()
    bridge = BatchQueueBridge(
        repository=repository,
        policy=BatchPolicy(allowed_clients=frozenset({"origintrail"})),
    )

    admission = await bridge.queue(
        item=item(deadline_at=deadline_at),
        idempotency_key="a" * 64,
        budget_key="batch-general:2026-07-31",
        budget_window_start=datetime(
            2026,
            7,
            30,
            15,
            tzinfo=timezone.utc,
        ),
        budget_window_end=datetime(
            2026,
            7,
            31,
            15,
            tzinfo=timezone.utc,
        ),
        daily_cap_usd=Decimal("0.05"),
        now=NOW,
        allow_existing_readback=True,
    )

    assert admission.mode == "batch"
    assert admission.reason == "batch_idempotent_readback"
    assert repository.budget_calls == []
    assert len(repository.queue_calls) == 1
    assert repository.queue_calls[0]["replay_only"] is True
