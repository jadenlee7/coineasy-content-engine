"""Offline fixed-SQL adapter tests; no database or provider connection."""
import unittest

from core.content_ops.final_decision_owner import (
    FinalDecisionOwnerError, PostgresFinalDecisionOwner,
)
from tests.test_content_ops_final_publication_confirmation import (
    A, BOT, C, HUMAN, MESSAGE, ROOM, V, snapshot,
)


class FakeCursor:
    def __init__(self, receipt):
        self.receipt = receipt
        self.queries = []

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, query, args=None): self.queries.append((query, args))
    def fetchone(self): return (self.receipt,)


class FakeConnection:
    autocommit = False

    def __init__(self, receipt):
        self.cursor_value = FakeCursor(receipt)
        self.committed = False

    def __enter__(self): return self
    def __exit__(self, error_type, *_):
        self.committed = error_type is None
        return False
    def cursor(self): return self.cursor_value


class FinalDecisionOwnerTest(unittest.TestCase):
    def setUp(self):
        self.s = snapshot()
        self.connections = []
        self.receipt = {"status": "confirmed_pending_publication_owner",
                        "decision_id": "77777777-7777-4777-8777-777777777777",
                        "reused": False, "execution_authorized": False}

    def factory(self):
        connection = FakeConnection(self.receipt)
        self.connections.append(connection)
        return connection

    def reader(self, room, message, bot, human):
        if (room, message, bot, human) != (ROOM, MESSAGE, BOT, HUMAN):
            raise ValueError("unregistered")
        return self.s

    def owner(self, enabled=True):
        return PostgresFinalDecisionOwner(self.factory, self.reader,
            release_sha=self.s.release_sha, enabled=enabled)

    def request(self):
        return dict(snapshot_sha256=self.s.digest(), version_id=V, card_id=C,
            version_fingerprint=self.s.version_fingerprint,
            message_binding=MESSAGE, bot_binding=BOT, room_binding=ROOM,
            human_binding=HUMAN, runtime_release_sha=self.s.release_sha,
            reviewer_id=A, action="confirm_publication", idempotency_key="1" * 64)

    def test_default_off_does_not_read_or_write(self):
        owner = self.owner(enabled=False)
        with self.assertRaisesRegex(FinalDecisionOwnerError, "disabled"):
            owner.read_confirmation(ROOM, MESSAGE, BOT, HUMAN)
        with self.assertRaisesRegex(FinalDecisionOwnerError, "disabled"):
            owner.apply_final_decision(**self.request())
        self.assertEqual(self.connections, [])

    def test_exact_registered_snapshot_and_fixed_sql_commit(self):
        owner = self.owner()
        self.assertIs(owner.read_confirmation(ROOM, MESSAGE, BOT, HUMAN), self.s)
        result = owner.apply_final_decision(**self.request())
        self.assertEqual(result, {key: self.receipt[key]
            for key in ("status", "decision_id", "reused")})
        self.assertEqual(len(self.connections), 1)
        self.assertTrue(self.connections[0].committed)
        queries = self.connections[0].cursor_value.queries
        self.assertEqual(len(queries), 2)
        self.assertIn("private.record_content_ops_final_decision", queries[1][0])
        self.assertEqual(queries[1][1][0:3], (C, A, V))

    def test_wrong_release_or_bad_receipt_fails_closed(self):
        owner = self.owner()
        request = self.request()
        request["runtime_release_sha"] = "0" * 40
        with self.assertRaisesRegex(FinalDecisionOwnerError, "arguments_invalid"):
            owner.apply_final_decision(**request)
        self.assertEqual(self.connections, [])
        self.receipt = {**self.receipt, "execution_authorized": True}
        with self.assertRaisesRegex(FinalDecisionOwnerError, "outcome_unknown"):
            owner.apply_final_decision(**self.request())
        self.assertFalse(self.connections[0].committed)

    def test_read_terminal_is_evidence_only(self):
        self.receipt = {"status": "not_recorded", "decision_id": None,
                        "execution_authorized": False}
        result = self.owner().read_terminal(card_id=C, reviewer_id=A,
            idempotency_key="1" * 64)
        self.assertEqual(result["status"], "not_recorded")
        self.assertIn("private.read_content_ops_final_decision_terminal",
            self.connections[0].cursor_value.queries[1][0])


if __name__ == "__main__":
    unittest.main()
