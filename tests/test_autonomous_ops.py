from __future__ import annotations

import unittest

from core.autonomous_ops import (
    AUTONOMOUS_OPS_PROTOCOL_VERSION,
    AutonomousOpsSnapshot,
    plan_snapshot,
    snapshot_fingerprint,
)


WORKSPACE = "11111111-1111-4111-8111-111111111111"


def _snapshot(**overrides: int) -> AutonomousOpsSnapshot:
    values = {
        "batch_failed_count": 0,
        "batch_stale_count": 0,
        "cost_overage_count": 0,
        "buzz_delivery_failed_count": 0,
        "buzz_delivery_unknown_count": 0,
        "review_ack_unknown_count": 0,
        "operations_response_unknown_count": 0,
        "unexpected_publication_count": 0,
        "nonterminal_batch_count": 0,
        "actual_cost_microusd": 227,
        **overrides,
    }
    draft = AutonomousOpsSnapshot(
        workspace_id=WORKSPACE,
        observed_at_epoch=1_786_500_000,
        observation_date_kst="2026-08-13",
        snapshot_sha256="0" * 64,
        **values,
    )
    return AutonomousOpsSnapshot(
        **{**draft.__dict__, "snapshot_sha256": snapshot_fingerprint(draft)}
    )


class AutonomousOpsPolicyTests(unittest.TestCase):
    def test_healthy_snapshot_is_idle(self):
        self.assertIsNone(plan_snapshot(_snapshot()))

    def test_containment_risk_wins_over_availability(self):
        plan = plan_snapshot(_snapshot(
            unexpected_publication_count=1,
            batch_failed_count=3,
            buzz_delivery_unknown_count=2,
        ))
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.category, "unexpected_publication")
        self.assertEqual(plan.severity, "critical")
        self.assertEqual(plan.execution_mode, "propose_only")
        self.assertFalse(plan.external_writes)
        self.assertFalse(plan.automatic_publication)

    def test_unknown_delivery_never_proposes_resend(self):
        plan = plan_snapshot(_snapshot(buzz_delivery_unknown_count=1))
        assert plan is not None
        self.assertEqual(plan.category, "buzz_delivery_unknown")
        text = " ".join(plan.steps_ko)
        self.assertIn("읽기 전용", text)
        self.assertIn("중복 전송 없이", text)
        self.assertNotIn("재전송", text)

    def test_incident_key_is_stable_for_category_and_day(self):
        first = plan_snapshot(_snapshot(batch_failed_count=1))
        second = plan_snapshot(_snapshot(batch_failed_count=9))
        assert first is not None and second is not None
        self.assertEqual(first.incident_key, second.incident_key)
        self.assertRegex(first.incident_key, r"^[a-f0-9]{64}$")

    def test_exact_snapshot_contract_and_hash(self):
        snapshot = _snapshot(batch_stale_count=1)
        raw = {
            "workspace_id": snapshot.workspace_id,
            "protocol_version": AUTONOMOUS_OPS_PROTOCOL_VERSION,
            "observed_at_epoch": snapshot.observed_at_epoch,
            "observation_date_kst": snapshot.observation_date_kst,
            "snapshot_sha256": snapshot.snapshot_sha256,
            **snapshot.metrics(),
        }
        self.assertEqual(AutonomousOpsSnapshot.from_mapping(raw), snapshot)
        for invalid in (
            {**raw, "extra": 1},
            {**raw, "batch_failed_count": -1},
            {**raw, "snapshot_sha256": "f" * 64},
            {**raw, "protocol_version": "origintrail-autonomous-ops@2"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    ValueError, "autonomous_ops_snapshot_invalid"
                ):
                    AutonomousOpsSnapshot.from_mapping(invalid)


if __name__ == "__main__":
    unittest.main()
