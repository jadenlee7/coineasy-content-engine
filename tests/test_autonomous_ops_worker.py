from __future__ import annotations

import unittest

from core.autonomous_ops import (
    AutonomousOpsSnapshot,
    AutonomousOpsTask,
    snapshot_fingerprint,
)
from core.autonomous_ops_worker import (
    AutonomousOpsError,
    OriginTrailAutonomousOpsWorker,
)


WORKSPACE = "11111111-1111-4111-8111-111111111111"
TASK = "22222222-2222-4222-8222-222222222222"


def _snapshot(*, failed: int = 0) -> AutonomousOpsSnapshot:
    draft = AutonomousOpsSnapshot(
        workspace_id=WORKSPACE,
        observed_at_epoch=1_786_500_000,
        observation_date_kst="2026-08-13",
        batch_failed_count=failed,
        batch_stale_count=0,
        cost_overage_count=0,
        buzz_delivery_failed_count=0,
        buzz_delivery_unknown_count=0,
        review_ack_unknown_count=0,
        operations_response_unknown_count=0,
        unexpected_publication_count=0,
        nonterminal_batch_count=0,
        actual_cost_microusd=227,
        snapshot_sha256="0" * 64,
    )
    return AutonomousOpsSnapshot(
        **{**draft.__dict__, "snapshot_sha256": snapshot_fingerprint(draft)}
    )


class FakeControl:
    def __init__(self, snapshot: AutonomousOpsSnapshot):
        self.snapshot = snapshot
        self.calls: list[str] = []
        self.observe_error: AutonomousOpsError | None = None
        self.record_error: AutonomousOpsError | None = None

    async def observe(self):
        self.calls.append("observe")
        if self.observe_error:
            raise self.observe_error
        return self.snapshot

    async def record(self, snapshot, plan):
        self.calls.append("record")
        if self.record_error:
            raise self.record_error
        return AutonomousOpsTask(
            workspace_id=snapshot.workspace_id,
            task_id=TASK,
            incident_key=plan.incident_key,
            category=plan.category,
            severity=plan.severity,
            title_ko=plan.title_ko,
            summary_ko=plan.summary_ko,
            steps_ko=plan.steps_ko,
            status="proposed",
            reused=False,
            automatic_execution=False,
        )


class AutonomousOpsWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_observation_is_read_only(self):
        control = FakeControl(_snapshot())
        result = await OriginTrailAutonomousOpsWorker(control).run_once()
        self.assertEqual(result.as_dict(), {"ok": True, "status": "healthy"})
        self.assertEqual(control.calls, ["observe"])

    async def test_incident_creates_one_proposal_without_execution(self):
        control = FakeControl(_snapshot(failed=1))
        result = await OriginTrailAutonomousOpsWorker(control).run_once()
        self.assertEqual(result.status, "proposed")
        self.assertEqual(result.category, "batch_failed")
        self.assertEqual(result.task_id, TASK)
        self.assertEqual(control.calls, ["observe", "record"])

    async def test_observation_failure_records_nothing(self):
        control = FakeControl(_snapshot(failed=1))
        control.observe_error = AutonomousOpsError(
            "autonomous_ops_control_unavailable"
        )
        result = await OriginTrailAutonomousOpsWorker(control).run_once()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertEqual(control.calls, ["observe"])

    async def test_record_failure_never_executes_an_action(self):
        control = FakeControl(_snapshot(failed=1))
        control.record_error = AutonomousOpsError(
            "autonomous_ops_control_unavailable"
        )
        result = await OriginTrailAutonomousOpsWorker(control).run_once()
        self.assertFalse(result.ok)
        self.assertEqual(result.category, "batch_failed")
        self.assertEqual(control.calls, ["observe", "record"])


if __name__ == "__main__":
    unittest.main()
