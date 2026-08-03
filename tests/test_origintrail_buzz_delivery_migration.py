from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260803120000_origintrail_buzz_delivery_receipts.sql"
SQL = MIGRATION.read_text()


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public[.]{name}[(].*?\n[$][$];",
        SQL,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(f"missing function {name}")
    return match.group(0)


class OriginTrailBuzzDeliveryMigrationTests(unittest.TestCase):
    def test_receipt_table_is_private_force_rls_and_origintrail_only(self):
        self.assertIn("alter table agent_runtime.buzz_delivery_receipts force row level security", SQL)
        self.assertIn("client_id = 'origintrail'", SQL)
        self.assertIn("agent_id = 'origintrail_client_agent'", SQL)
        self.assertIn("workflow_kind = 'official_source_nonurgent_pack'", SQL)
        self.assertIn("from public, anon, authenticated, service_role", SQL)
        self.assertNotIn("grant select on", SQL.lower())

    def test_event_id_is_recomputed_from_workspace_and_job(self):
        claim = _function("claim_origintrail_buzz_delivery")
        self.assertIn("private.origintrail_buzz_event_id", claim)
        self.assertIn("OriginTrail Buzz event identity does not match", claim)
        self.assertIn("review_job.input ->> 'source_url'", claim)
        self.assertIn("origin_trail/status", claim)

    def test_fresh_attempt_is_the_only_single_send_authorization(self):
        attempt = _function("mark_origintrail_buzz_delivery_attempt")
        self.assertIn("'authorized_once', true", attempt)
        self.assertIn("'reused', false", attempt)
        self.assertIn("'authorized_once', false", attempt)
        self.assertIn("delivery_started_at is not null", attempt)

    def test_unknown_outcomes_cannot_return_to_pending(self):
        failure = _function("fail_origintrail_buzz_delivery")
        reconcile = _function("reconcile_origintrail_buzz_delivery_leases")
        self.assertIn("next_status := 'delivery_unknown'", failure)
        self.assertIn("target_retryable_before_attempt", failure)
        self.assertIn("when delivery.status = 'attempt_started' then 'delivery_unknown'", reconcile)
        self.assertNotIn("delivery_unknown' then 'pending", SQL)

    def test_only_service_role_gets_rpc_execute(self):
        for name in (
            "claim_origintrail_buzz_delivery",
            "mark_origintrail_buzz_delivery_attempt",
            "complete_origintrail_buzz_delivery",
            "fail_origintrail_buzz_delivery",
            "reconcile_origintrail_buzz_delivery_leases",
        ):
            self.assertRegex(SQL, rf"grant execute on function public[.]{name}[(]")
        self.assertNotRegex(SQL, r"grant execute .* to (anon|authenticated)")


if __name__ == "__main__":
    unittest.main()
