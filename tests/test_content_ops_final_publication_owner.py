"""Offline channel-admission tests; no provider or production connections."""
from dataclasses import replace
import unittest

from core.content_ops.final_decision_owner import FinalDecisionOwnerError
from core.content_ops.final_publication_owner import PostgresFinalPublicationOwner
from core.content_ops.final_publication_confirmation import (
    FinalConfirmationError, FinalConfirmationSigner, VerifiedFinalCallback,
    final_confirmation_messages, handle_final_confirmation,
)
from tests.test_content_ops_final_decision_owner import FakeConnection
from tests.test_content_ops_final_publication_confirmation import (
    A, BOT, C, HUMAN, MESSAGE, ROOM, V, NOW, snapshot, FakeOwner,
)

ID = "77777777-7777-4777-8777-777777777777"


class FinalPublicationOwnerTest(unittest.TestCase):
    def setUp(self):
        self.s = replace(snapshot(), decision_mode="telegram_and_typefully_draft",
                         parent_card_id=ID)
        self.connections = []
        self.receipt = dict(status="channels_queued", decision_id=ID,
            approval_id=ID, publication_id=ID, job_id=ID, typefully_slot_id=ID,
            reused=False, execution_authorized=False)

    def factory(self):
        conn = FakeConnection(self.receipt)
        self.connections.append(conn)
        return conn

    def owner(self, enabled=True):
        return PostgresFinalPublicationOwner(self.factory, lambda *_: self.s,
            release_sha=self.s.release_sha, enabled=enabled)

    def request(self):
        return dict(snapshot_sha256=self.s.digest(), version_id=V, card_id=C,
            version_fingerprint=self.s.version_fingerprint,
            message_binding=MESSAGE, bot_binding=BOT, room_binding=ROOM,
            human_binding=HUMAN, runtime_release_sha=self.s.release_sha,
            reviewer_id=A, action="confirm_publication", idempotency_key="1" * 64)

    def test_default_off_zero_database_io(self):
        with self.assertRaisesRegex(FinalDecisionOwnerError, "disabled"):
            self.owner(False).apply_final_decision(**self.request())
        self.assertEqual(self.connections, [])

    def test_atomic_fixed_sql_returns_only_after_commit(self):
        result = self.owner().apply_final_decision(**self.request())
        self.assertEqual(result, dict(status="channels_queued", decision_id=ID, reused=False))
        self.assertEqual(len(self.connections), 1)
        self.assertTrue(self.connections[0].committed)
        query, args = self.connections[0].cursor_value.queries[1]
        self.assertIn("private.record_content_ops_final_publication", query)
        self.assertEqual(args[:3], (C, A, V))

    def test_record_only_snapshot_is_never_upgraded(self):
        self.s = snapshot()
        with self.assertRaisesRegex(FinalDecisionOwnerError, "mode_conflict"):
            self.owner().read_confirmation(ROOM, MESSAGE, BOT, HUMAN)
        self.assertEqual(self.connections, [])

    def test_mode_is_mac_bound_and_copy_fits_both_channel_owners(self):
        signer = FinalConfirmationSigner(b"k" * 32)
        token = signer.issue(snapshot(), "p", ROOM, now=NOW, expires_at=NOW+900)
        with self.assertRaisesRegex(FinalConfirmationError, "stale_or_invalid"):
            signer.verify(token, self.s, ROOM, now=NOW)
        packet = final_confirmation_messages(self.s, signer, ROOM, now=NOW)
        self.assertIn("공식 Telegram 게시", packet["controls"]["text"])
        self.assertIn("X는 자동 게시하지", packet["controls"]["text"])
        for changed in (replace(self.s.review, x_copy="x"*281),
                        replace(self.s.review, telegram_copy="😀"*513)):
            with self.assertRaisesRegex(FinalConfirmationError, "copy_too_long"):
                replace(self.s, review=changed).validate()

    def test_record_only_owner_cannot_accept_queue_card(self):
        signer = FinalConfirmationSigner(b"k" * 32)
        token = signer.issue(self.s, "p", ROOM, now=NOW, expires_at=NOW+900)
        event = VerifiedFinalCallback("cb1", A, False, ROOM, MESSAGE, BOT, HUMAN, token)
        owner = FakeOwner(self.s)
        with self.assertRaisesRegex(FinalConfirmationError, "mode_conflict"):
            handle_final_confirmation(event, enabled=True, signer=signer,
                owner=owner, allowed_reviewers=frozenset({A}), room_binding=ROOM,
                runtime_release_sha=self.s.release_sha, now=NOW)
        self.assertEqual(owner.applies, 0)

    def test_hold_has_no_channel_ids_and_terminal_is_read_only(self):
        self.receipt = dict(status="held", decision_id=ID, approval_id=None,
            publication_id=None, job_id=None, typefully_slot_id=None,
            reused=False, execution_authorized=False)
        self.assertEqual(self.owner().apply_final_decision(
            **{**self.request(), "action":"hold"})["status"], "held")
        self.receipt.pop("reused")
        terminal = self.owner().read_terminal(card_id=C, reviewer_id=A,
            idempotency_key="1"*64)
        self.assertEqual(terminal["status"], "held")
        self.assertIn("read_content_ops_final_publication_terminal",
            self.connections[-1].cursor_value.queries[1][0])

    def test_malformed_receipt_and_commit_loss_are_unknown_not_retry(self):
        self.receipt["execution_authorized"] = True
        with self.assertRaisesRegex(FinalDecisionOwnerError, "outcome_unknown"):
            self.owner().apply_final_decision(**self.request())
        self.assertFalse(self.connections[0].committed)
        class LostCommit(FakeConnection):
            def __exit__(self, *_): raise RuntimeError("secret provider content")
        owner = PostgresFinalPublicationOwner(lambda: LostCommit({
            **self.receipt, "execution_authorized":False}), lambda *_:self.s,
            release_sha=self.s.release_sha, enabled=True)
        with self.assertRaisesRegex(FinalDecisionOwnerError, "outcome_unknown") as error:
            owner.apply_final_decision(**self.request())
        self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
