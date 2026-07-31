from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from core.batch.models import BatchWorkItem
from core.batch.policy import BatchPolicy


class BatchQueueRepository(Protocol):
    async def configure_daily_budget(self, **kwargs) -> None: ...
    async def queue_job(self, **kwargs) -> str: ...


@dataclass(frozen=True)
class BatchAdmission:
    mode: str
    reason: str
    job_id: str | None


class BatchQueueBridge:
    """Single admission boundary shared by every CoinEasy agent role."""

    def __init__(
        self,
        *,
        repository: BatchQueueRepository,
        policy: BatchPolicy,
    ):
        self.repository = repository
        self.policy = policy

    async def queue(
        self,
        *,
        item: BatchWorkItem,
        idempotency_key: str,
        budget_key: str,
        budget_window_start: datetime,
        budget_window_end: datetime,
        daily_cap_usd: Decimal,
        now: datetime,
        allow_existing_readback: bool = False,
    ) -> BatchAdmission:
        if not isinstance(allow_existing_readback, bool):
            raise ValueError("allow_existing_readback must be a boolean")
        decision = self.policy.route(item, now=now)
        replay_only = (
            allow_existing_readback
            and decision.mode == "manual_sync"
            and decision.reason == "batch_deadline_too_close"
        )
        if decision.mode != "batch" and not replay_only:
            return BatchAdmission(
                mode=decision.mode,
                reason=decision.reason,
                job_id=None,
            )
        if replay_only:
            job_id = await self.repository.queue_job(
                item=item,
                idempotency_key=idempotency_key,
                budget_key=budget_key,
                replay_only=True,
            )
            return BatchAdmission(
                mode="batch",
                reason="batch_idempotent_readback",
                job_id=job_id,
            )
        self._validate_budget_window(
            budget_key=budget_key,
            window_start=budget_window_start,
            window_end=budget_window_end,
            daily_cap_usd=daily_cap_usd,
            now=now,
        )
        await self.repository.configure_daily_budget(
            budget_key=budget_key,
            window_start=budget_window_start,
            window_end=budget_window_end,
            limit_usd=daily_cap_usd,
        )
        job_id = await self.repository.queue_job(
            item=item,
            idempotency_key=idempotency_key,
            budget_key=budget_key,
            replay_only=False,
        )
        return BatchAdmission(
            mode="batch",
            reason=decision.reason,
            job_id=job_id,
        )

    @staticmethod
    def _validate_budget_window(
        *,
        budget_key: str,
        window_start: datetime,
        window_end: datetime,
        daily_cap_usd: Decimal,
        now: datetime,
    ) -> None:
        kst = ZoneInfo("Asia/Seoul")
        if (
            window_start.tzinfo is None
            or window_end.tzinfo is None
            or now.tzinfo is None
        ):
            raise ValueError("Batch budget clocks must be timezone-aware")
        start_kst = window_start.astimezone(kst)
        end_kst = window_end.astimezone(kst)
        if (
            start_kst.time().isoformat() != "00:00:00"
            or end_kst.time().isoformat() != "00:00:00"
            or end_kst.date() - start_kst.date() != timedelta(days=1)
            or not window_start <= now < window_end
            or budget_key
            != f"batch-general:{start_kst.date().isoformat()}"
        ):
            raise ValueError("Batch budget must match the active KST day")
        if (
            not isinstance(daily_cap_usd, Decimal)
            or not daily_cap_usd.is_finite()
            or daily_cap_usd <= 0
            or daily_cap_usd > Decimal("6.00")
        ):
            raise ValueError(
                "Batch daily cap must be a positive decimal no greater than 6"
            )
