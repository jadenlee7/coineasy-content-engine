from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence

from core.batch.models import (
    ActiveBatch,
    BatchItemOutcome,
    BatchSnapshot,
    BatchWorkItem,
)
from core.batch.openai_client import OpenAIBatchClient, OpenAIBatchError
from core.batch.policy import (
    BatchPolicy,
    actual_batch_cost_usd,
)
from core.batch.repository import BatchRepositoryError, batch_dispatch_key


_BUNDLE_NAMESPACE = uuid.UUID("837d2e11-6c75-44bb-8daa-4d19d587444c")
_RETRYABLE_ITEM_CODES = frozenset({
    "openai_batch_item_retryable",
    "request_timeout",
})


class BatchRepository(Protocol):
    async def claim_jobs(self, **kwargs) -> tuple[BatchWorkItem, ...]: ...
    async def expire_jobs(self, **kwargs) -> tuple[int, int]: ...
    async def register_batch(self, **kwargs) -> None: ...
    async def list_active_batches(self, **kwargs) -> tuple[ActiveBatch, ...]: ...
    async def update_batch_poll(self, snapshot: BatchSnapshot) -> None: ...
    async def complete_job(self, **kwargs) -> None: ...
    async def fail_job(self, **kwargs) -> None: ...
    async def finalize_batch(self, provider_batch_id: str) -> None: ...


class BatchProvider(Protocol):
    @staticmethod
    def build_jsonl(items: Sequence[BatchWorkItem]) -> bytes: ...
    async def upload_input_file(self, **kwargs) -> str: ...
    async def create_batch(self, **kwargs) -> BatchSnapshot: ...
    async def find_recent_batch(self, **kwargs) -> BatchSnapshot | None: ...
    async def retrieve_batch(self, provider_batch_id: str) -> BatchSnapshot: ...
    async def download_file(self, file_id: str) -> bytes: ...

    @staticmethod
    def parse_result_file(payload: bytes) -> tuple[BatchItemOutcome, ...]: ...


@dataclass
class BatchDispatchSummary:
    claimed: int = 0
    submitted: int = 0
    recovered: int = 0
    manual_sync_required: int = 0
    waiting_budget: int = 0
    rejected: int = 0
    provider_batches_polled: int = 0
    completed: int = 0
    failed: int = 0
    expired_unsubmitted: int = 0
    ambiguous_claimed: int = 0
    errors: int = 0
    outcomes: list[dict[str, str]] = field(default_factory=list)

    def add(self, status: str, detail: str = "") -> None:
        item = {"status": status}
        if detail:
            item["detail"] = detail
        self.outcomes.append(item)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.errors == 0,
            "claimed": self.claimed,
            "submitted": self.submitted,
            "recovered": self.recovered,
            "manual_sync_required": self.manual_sync_required,
            "waiting_budget": self.waiting_budget,
            "rejected": self.rejected,
            "provider_batches_polled": self.provider_batches_polled,
            "completed": self.completed,
            "failed": self.failed,
            "expired_unsubmitted": self.expired_unsubmitted,
            "ambiguous_claimed": self.ambiguous_claimed,
            "errors": self.errors,
            "outcomes": list(self.outcomes),
        }


class BatchDispatcher:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        provider: BatchProvider,
        policy: BatchPolicy,
        worker_id: str,
        max_claims: int = 100,
        max_requests_per_batch: int = 1,
        now_factory=lambda: datetime.now(timezone.utc),
    ):
        if (
            not 8 <= len(worker_id) <= 120
            or any(character.isspace() for character in worker_id)
        ):
            raise ValueError("worker_id is invalid")
        if not 1 <= max_claims <= 100:
            raise ValueError("max_claims must be between 1 and 100")
        if max_requests_per_batch != 1:
            raise ValueError(
                "the first Batch experiment requires one request per batch"
            )
        self.repository = repository
        self.provider = provider
        self.policy = policy
        self.worker_id = worker_id
        self.max_claims = max_claims
        self.max_requests_per_batch = max_requests_per_batch
        self.now_factory = now_factory

    async def run_once(self) -> BatchDispatchSummary:
        summary = BatchDispatchSummary()
        await self.poll_once(summary=summary)
        await self.submit_once(summary=summary)
        return summary

    async def cleanup_expired_once(
        self,
        *,
        summary: BatchDispatchSummary | None = None,
    ) -> BatchDispatchSummary:
        summary = summary or BatchDispatchSummary()
        try:
            expired_count, ambiguous_count = (
                await self.repository.expire_jobs(
                    allowed_clients=self.policy.allowed_clients,
                )
            )
        except BatchRepositoryError as exc:
            summary.errors += 1
            summary.add("expiry_cleanup_failed", exc.code)
            return summary
        summary.expired_unsubmitted += expired_count
        summary.ambiguous_claimed += ambiguous_count
        if expired_count:
            summary.add(
                "expired_unsubmitted",
                f"jobs={expired_count}",
            )
        if ambiguous_count:
            summary.add(
                "ambiguous_claimed_manual_recovery",
                f"jobs={ambiguous_count}",
            )
        return summary

    async def submit_once(
        self,
        *,
        summary: BatchDispatchSummary | None = None,
    ) -> BatchDispatchSummary:
        summary = summary or BatchDispatchSummary()
        try:
            claimed = await self.repository.claim_jobs(
                worker_id=self.worker_id,
                allowed_clients=self.policy.allowed_clients,
                limit=self.max_claims,
                lease_seconds=900,
            )
        except BatchRepositoryError as exc:
            summary.errors += 1
            summary.add("claim_failed", exc.code)
            return summary
        summary.claimed += len(claimed)
        eligible: list[BatchWorkItem] = []
        now = self._now()
        for item in claimed:
            decision = self.policy.route(item, now=now)
            if decision.mode == "batch":
                eligible.append(item)
                continue
            try:
                await self._fail_routed_item(
                    item,
                    decision.mode,
                    decision.reason,
                )
            except BatchRepositoryError as exc:
                summary.errors += 1
                summary.add("route_record_failed", exc.code)
                continue
            if decision.mode == "manual_sync":
                summary.manual_sync_required += 1
            elif decision.mode == "waiting_budget":
                summary.waiting_budget += 1
            else:
                summary.rejected += 1
            summary.add(decision.mode, decision.reason)

        grouped: dict[tuple[str, str], list[BatchWorkItem]] = {}
        for item in eligible:
            grouped.setdefault((item.client_id, item.model), []).append(item)
        for items in grouped.values():
            ordered = sorted(items, key=lambda item: (item.priority, item.custom_id))
            for offset in range(0, len(ordered), self.max_requests_per_batch):
                await self._submit_group(
                    tuple(ordered[offset:offset + self.max_requests_per_batch]),
                    summary,
                )
        return summary

    async def poll_once(
        self,
        *,
        summary: BatchDispatchSummary | None = None,
    ) -> BatchDispatchSummary:
        summary = summary or BatchDispatchSummary()
        try:
            active = await self.repository.list_active_batches(limit=100)
        except BatchRepositoryError as exc:
            summary.errors += 1
            summary.add("poll_list_failed", exc.code)
            return summary
        for batch in active:
            try:
                snapshot = await self.provider.retrieve_batch(
                    batch.provider_batch_id
                )
                if snapshot.provider_batch_id != batch.provider_batch_id:
                    raise OpenAIBatchError(
                        "openai_batch_identity_mismatch",
                        retryable=False,
                    )
                await self.repository.update_batch_poll(snapshot)
                summary.provider_batches_polled += 1
                if not snapshot.terminal:
                    continue
                try:
                    await self._reconcile_terminal(snapshot, summary)
                except OpenAIBatchError as exc:
                    if exc.retryable:
                        raise
                    # A terminal provider batch cannot become healthy on a
                    # later poll. Conservatively close any unsettled jobs in
                    # SQL so malformed/duplicate/unknown results cannot hold
                    # reservations forever or starve the active-batch scan.
                    await self.repository.finalize_batch(
                        snapshot.provider_batch_id
                    )
                    summary.errors += 1
                    summary.add("batch_quarantined", exc.code)
                except BatchRepositoryError as exc:
                    if exc.retryable:
                        raise
                    await self.repository.finalize_batch(
                        snapshot.provider_batch_id
                    )
                    summary.errors += 1
                    summary.add("batch_quarantined", exc.code)
                except ValueError:
                    await self.repository.finalize_batch(
                        snapshot.provider_batch_id
                    )
                    summary.errors += 1
                    summary.add(
                        "batch_quarantined",
                        "batch_result_invalid",
                    )
            except (BatchRepositoryError, OpenAIBatchError) as exc:
                summary.errors += 1
                summary.add("poll_failed", exc.code)
            except ValueError:
                summary.errors += 1
                summary.add("poll_failed", "batch_result_invalid")
        return summary

    async def _submit_group(
        self,
        items: tuple[BatchWorkItem, ...],
        summary: BatchDispatchSummary,
    ) -> None:
        dispatch_key = batch_dispatch_key(items)
        bundle_id = str(uuid.uuid5(_BUNDLE_NAMESPACE, dispatch_key))
        try:
            if items[0].recovery_required:
                attempt_started_at = items[0].attempt_started_at
                if attempt_started_at is None:
                    raise ValueError("recovery attempt start is missing")
                snapshot = await self.provider.find_recent_batch(
                    dispatch_key=dispatch_key,
                    not_before=attempt_started_at - timedelta(minutes=5),
                )
            else:
                snapshot = None
            recovered = snapshot is not None
            if snapshot is None:
                payload = self.provider.build_jsonl(items)
                input_file_id = await self.provider.upload_input_file(
                    payload=payload,
                    bundle_id=bundle_id,
                )
                snapshot = await self.provider.create_batch(
                    input_file_id=input_file_id,
                    metadata={
                        "dispatch_key": dispatch_key,
                        "bundle_id": bundle_id,
                        "client_id": items[0].client_id,
                        "policy": "batch_first_v1",
                    },
                )
        except OpenAIBatchError as exc:
            # Any provider error after entering the upload/create path is
            # ambiguous: a file or billable Batch may already exist even when
            # the response is malformed or non-2xx. Preserve this attempt and
            # let the expired lease use metadata recovery before doing more.
            summary.errors += 1
            summary.add("submit_failed", exc.code)
            return
        except ValueError:
            summary.errors += 1
            summary.add("submit_failed", "batch_submission_invalid")
            await self._fail_submission_items(
                items,
                error_code="batch_submission_invalid",
                retryable=False,
            )
            return
        try:
            await self.repository.register_batch(
                worker_id=self.worker_id,
                input_file_id=snapshot.input_file_id,
                snapshot=snapshot,
                job_ids=[item.job_id for item in items],
            )
        except BatchRepositoryError as exc:
            # A provider batch may already exist. Leave the DB lease to expire;
            # the deterministic dispatch_key lets the next worker recover it.
            summary.errors += 1
            summary.add("batch_registration_failed", exc.code)
            return
        if recovered:
            summary.recovered += len(items)
            summary.add("batch_recovered", f"items={len(items)}")
        else:
            summary.submitted += len(items)
            summary.add("batch_submitted", f"items={len(items)}")

    async def _reconcile_terminal(
        self,
        snapshot: BatchSnapshot,
        summary: BatchDispatchSummary,
    ) -> None:
        outcomes: list[BatchItemOutcome] = []
        if snapshot.output_file_id is not None:
            payload = await self.provider.download_file(snapshot.output_file_id)
            outcomes.extend(self.provider.parse_result_file(payload))
        if snapshot.error_file_id is not None:
            payload = await self.provider.download_file(snapshot.error_file_id)
            outcomes.extend(self.provider.parse_result_file(payload))
        if len({item.custom_id for item in outcomes}) != len(outcomes):
            raise OpenAIBatchError(
                "openai_duplicate_batch_result",
                retryable=False,
            )
        for outcome in outcomes:
            job_id, _stage, attempt = self._custom_id_parts(outcome.custom_id)
            if outcome.ok:
                actual_cost = actual_batch_cost_usd(
                    model=outcome.model or "",
                    input_tokens=outcome.input_tokens,
                    cached_input_tokens=outcome.cached_input_tokens,
                    output_tokens=outcome.output_tokens,
                )
                try:
                    await self.repository.complete_job(
                        job_id=job_id,
                        provider_batch_id=snapshot.provider_batch_id,
                        output=outcome.output or {},
                        input_tokens=outcome.input_tokens,
                        output_tokens=outcome.output_tokens,
                        actual_cost_usd=actual_cost,
                    )
                except BatchRepositoryError as exc:
                    if exc.retryable:
                        raise
                    # A provider-success result can still violate the ledger's
                    # workflow-specific review schema. It already incurred
                    # usage, so settle it as a terminal failure instead of
                    # leaving the reservation held and polling forever.
                    await self.repository.fail_job(
                        job_id=job_id,
                        provider_batch_id=snapshot.provider_batch_id,
                        error_code="batch_result_rejected",
                        retryable=False,
                        available_at=None,
                        input_tokens=outcome.input_tokens,
                        output_tokens=outcome.output_tokens,
                        actual_cost_usd=actual_cost,
                    )
                    summary.failed += 1
                    summary.add("batch_result_rejected", exc.code)
                else:
                    summary.completed += 1
            else:
                retryable = (
                    outcome.cost_basis == "none"
                    and outcome.error_code in _RETRYABLE_ITEM_CODES
                    and attempt < 2
                )
                accounting: dict[str, object] = {}
                if outcome.cost_basis == "usage":
                    accounting = {
                        "input_tokens": outcome.input_tokens,
                        "output_tokens": outcome.output_tokens,
                        "actual_cost_usd": actual_batch_cost_usd(
                            model=outcome.model or "",
                            input_tokens=outcome.input_tokens,
                            cached_input_tokens=outcome.cached_input_tokens,
                            output_tokens=outcome.output_tokens,
                        ),
                    }
                elif outcome.cost_basis == "reservation":
                    accounting = {"charge_full_reservation": True}
                await self.repository.fail_job(
                    job_id=job_id,
                    provider_batch_id=snapshot.provider_batch_id,
                    error_code=outcome.error_code or "batch_job_failed",
                    retryable=retryable,
                    available_at=(
                        self._now() + timedelta(minutes=15)
                        if retryable
                        else None
                    ),
                    **accounting,
                )
                summary.failed += 1
        missing_count = max(snapshot.request_total - len(outcomes), 0)
        await self.repository.finalize_batch(snapshot.provider_batch_id)
        summary.failed += missing_count
        summary.add(
            "batch_reconciled",
            (
                f"status={snapshot.status},"
                f"completed={snapshot.request_completed},"
                f"failed={snapshot.request_failed},"
                f"missing={missing_count}"
            ),
        )

    async def _fail_routed_item(
        self,
        item: BatchWorkItem,
        mode: str,
        reason: str,
    ) -> None:
        retryable = mode == "waiting_budget"
        await self.repository.fail_job(
            job_id=item.job_id,
            provider_batch_id=None,
            error_code=reason,
            retryable=retryable,
            available_at=(
                self._now() + timedelta(hours=1) if retryable else None
            ),
        )

    async def _fail_submission_items(
        self,
        items: Sequence[BatchWorkItem],
        *,
        error_code: str,
        retryable: bool,
    ) -> None:
        for item in items:
            can_retry = retryable and item.attempt < 2
            try:
                await self.repository.fail_job(
                    job_id=item.job_id,
                    provider_batch_id=None,
                    error_code=error_code,
                    retryable=can_retry,
                    available_at=(
                        self._now() + timedelta(minutes=15)
                        if can_retry
                        else None
                    ),
                )
            except BatchRepositoryError:
                # The original provider/database error remains the primary
                # bounded failure. The expired lease is recovered by the DB.
                continue

    def _now(self) -> datetime:
        value = self.now_factory()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("dispatcher clock must be timezone-aware")
        return value

    @staticmethod
    def _custom_id_parts(custom_id: str) -> tuple[str, str, int]:
        parts = custom_id.split(":")
        if len(parts) != 3:
            raise OpenAIBatchError("openai_invalid_custom_id", retryable=False)
        try:
            job_id = str(uuid.UUID(parts[0]))
            attempt = int(parts[2])
        except (ValueError, TypeError) as exc:
            raise OpenAIBatchError(
                "openai_invalid_custom_id",
                retryable=False,
            ) from exc
        if parts[1] not in {"generate", "review", "qa", "summarize"}:
            raise OpenAIBatchError("openai_invalid_custom_id", retryable=False)
        if attempt not in {1, 2}:
            raise OpenAIBatchError("openai_invalid_custom_id", retryable=False)
        return job_id, parts[1], attempt


def build_batch_dispatcher(
    *,
    repository: BatchRepository,
    api_key: str,
    allowed_clients: frozenset[str],
    worker_id: str,
    max_claims: int = 100,
    max_requests_per_batch: int = 1,
) -> BatchDispatcher:
    return BatchDispatcher(
        repository=repository,
        provider=OpenAIBatchClient(api_key=api_key),
        policy=BatchPolicy(allowed_clients=allowed_clients),
        worker_id=worker_id,
        max_claims=max_claims,
        max_requests_per_batch=max_requests_per_batch,
    )
