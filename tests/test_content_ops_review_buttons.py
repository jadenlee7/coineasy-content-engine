"""Offline controller tests. FakeOwner is not a production authorization DB."""
import unittest
from dataclasses import replace

from core.content_ops.review_buttons import (
    ButtonReviewError, ButtonSigner, ReviewSnapshot, VerifiedCallback,
    delivery_label, handle_review_callback, review_messages,
)

NOW = 1789326000
W = "11111111-1111-4111-8111-111111111111"
I = "22222222-2222-4222-8222-222222222222"
V = "33333333-3333-4333-8333-333333333333"
ROOM = "fixture-private-room-binding"


def snapshot(client="yellow"):
    handles = {"yellow": "Yellow", "squid": "SquidRouter",
               "babylon": "babylonlabs_io", "origintrail": "origin_trail"}
    return ReviewSnapshot(W, client, I, V,
        f"https://x.com/{handles[client]}/status/123", "2026-09-13T14:39:05Z",
        "Telegram 공지\n원문 확인", "짧은 X 공지", "a" * 64, "daily_ready")


class FakeOwner:
    """Minimal transaction fixture: checks are reviewer/version/hash specific."""
    def __init__(self, current):
        self.current = current
        self.reads = 0
        self.applies = 0
        self.operations = {}
        self.checks = set()
        self.outbox = set()
        self.edit_prompt = None

    def read_review(self, room, message):
        self.reads += 1
        if (room, message) != (ROOM, "fixture-message"):
            raise ButtonReviewError("review_message_unregistered")
        return self.current

    def apply_review_action(self, **kw):
        self.applies += 1
        if kw["snapshot_sha256"] != self.current.digest() or kw["version_id"] != self.current.content_version_id:
            raise ButtonReviewError("review_owner_version_conflict")
        key = kw["idempotency_key"]
        if key in self.operations:
            return {**self.operations[key], "reused": True}
        actor = kw["actor_id"]
        action = kw["action"]
        if action in {"source_checked", "claims_checked"}:
            self.checks.add((actor, self.current.digest(), action))
            status = "checked"
        elif action.startswith("edit_"):
            self.edit_prompt = (actor, self.current.digest(), action)
            status = "edit_requested"
        elif action == "hold":
            self.checks.clear()
            status = "held"
        else:
            if not all((actor, self.current.digest(), x) in self.checks for x in ("source_checked", "claims_checked")):
                raise ButtonReviewError("review_owner_human_attestation_required")
            for channel in ("telegram", "x"):
                self.outbox.add((self.current.client_id, self.current.content_version_id, channel))
            status = "queued"
        result = {"status": status, "reused": False}
        self.operations[key] = result
        return result


class ReviewButtonsTest(unittest.TestCase):
    def setUp(self):
        self.s = snapshot()
        self.signer = ButtonSigner(b"k" * 32)
        self.owner = FakeOwner(self.s)

    def event(self, action="a", **changes):
        token = self.signer.issue(self.s, action, ROOM, now=NOW, expires_at=NOW + 1800)
        return replace(VerifiedCallback("fixture-" + action, "reviewer-one", False,
            ROOM, "fixture-message", token), **changes)

    def run_event(self, event=None, **changes):
        args = dict(enabled=True, signer=self.signer, owner=self.owner,
                    allowed_reviewers=frozenset({"reviewer-one", "reviewer-two"}), room_binding=ROOM, now=NOW)
        args.update(changes)
        return handle_review_callback(event or self.event(), **args)

    def test_disabled_zero_owner_io(self):
        self.assertEqual(self.run_event(enabled=False)["status"], "disabled")
        self.assertEqual((self.owner.reads, self.owner.applies), (0, 0))

    def test_string_true_does_not_enable(self):
        self.assertEqual(self.run_event(enabled="true")["status"], "disabled")

    def test_all_four_clients_exact_copy(self):
        for client in ("yellow", "babylon", "squid", "origintrail"):
            with self.subTest(client=client):
                s = snapshot(client)
                m = review_messages(s, self.signer, ROOM, now=NOW)
                self.assertTrue(m["telegram"]["text"].endswith(s.telegram_copy))
                self.assertTrue(m["x"]["text"].endswith(s.x_copy))
                buttons = sum(m["controls"]["reply_markup"]["inline_keyboard"], [])
                self.assertEqual(len(buttons), 7)
                for b in buttons:
                    self.assertEqual(len(b["callback_data"].encode()), 51)
                    self.assertNotIn(client, b["callback_data"])

    def test_private_card_binds_banner_review_without_public_approval(self):
        s = replace(self.s, eligibility="blocked")
        m = review_messages(s, self.signer, ROOM, now=NOW, private_only=True)
        rows = m["controls"]["reply_markup"]["inline_keyboard"]
        self.assertEqual([button["text"] for button in rows[2]],
                         ["✅ 공식 원문 확인", "✅ 문안·배너 확인"])
        self.assertEqual(sum(len(row) for row in rows), 6)
        self.assertIn("공식 채널 게시 승인이 아닙니다", m["controls"]["text"])
        self.assertTrue(all(button["callback_data"].startswith("ce1:")
                            for row in rows for button in row))

    def test_no_approval_without_both_human_checks(self):
        with self.assertRaisesRegex(ButtonReviewError, "attestation"):
            self.run_event()
        self.assertFalse(self.owner.outbox)

    def test_happy_path_queues_both_not_sends(self):
        self.run_event(self.event("s")); self.run_event(self.event("c"))
        result = self.run_event()
        self.assertEqual(result, {"status": "queued", "reused": False, "public_send_attempted": False})
        self.assertEqual(len(self.owner.outbox), 2)

    def test_replay_does_not_duplicate(self):
        self.run_event(self.event("s")); self.run_event(self.event("c"))
        self.run_event()
        self.assertTrue(self.run_event()["reused"])
        self.run_event(self.event(callback_id="another-click"))
        self.assertEqual(len(self.owner.outbox), 2)

    def test_checks_cannot_be_combined_across_reviewers(self):
        self.run_event(self.event("s"))
        self.run_event(self.event("c", actor_id="reviewer-two"))
        with self.assertRaisesRegex(ButtonReviewError, "attestation"):
            self.run_event()

    def test_bad_actor_and_room_before_lookup(self):
        for changes in ({"actor_id":"outsider"}, {"actor_is_bot":True}, {"room_binding":"another-room"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ButtonReviewError, "forbidden"):
                self.run_event(self.event(**changes))
        self.assertEqual(self.owner.reads, 0)

    def test_forged_message_cannot_select_another_packet(self):
        with self.assertRaisesRegex(ButtonReviewError, "unregistered"):
            self.run_event(self.event(message_binding="another-message"))

    def test_mutation_invalidates_each_binding(self):
        for kw in ({"telegram_copy":"바뀐 공지"}, {"x_copy":"바뀐 X"}, {"banner_sha256":"b"*64},
                   {"content_version_id":"44444444-4444-4444-8444-444444444444"},
                   {"source_url":"https://x.com/Yellow/status/456"}, {"workspace_id":I}):
            with self.subTest(kw=kw):
                self.owner.current = replace(self.s, **kw)
                with self.assertRaisesRegex(ButtonReviewError, "stale_or_invalid"):
                    self.run_event()
        self.assertEqual(self.owner.applies, 0)

    def test_cross_client_rejected(self):
        self.owner.current = snapshot("squid")
        with self.assertRaisesRegex(ButtonReviewError, "stale_or_invalid"):
            self.run_event()

    def test_expired_at_boundary(self):
        with self.assertRaisesRegex(ButtonReviewError, "expired"):
            self.run_event(now=NOW + 1800)

    def test_tampered_token(self):
        e = self.event()
        with self.assertRaises(ButtonReviewError):
            self.run_event(replace(e, token=e.token[:30] + ("A" if e.token[30] != "A" else "B") + e.token[31:]))

    def test_recap_cannot_enter_daily_publication(self):
        for state in ("recap_requires_separate_approval", "blocked"):
            self.s = replace(self.s, eligibility=state); self.owner.current = self.s
            with self.assertRaisesRegex(ButtonReviewError, "separate_approval"):
                self.run_event()
        self.assertEqual(self.owner.applies, 0)

    def test_edit_request_actor_binding(self):
        self.assertEqual(self.run_event(self.event("x"))["status"], "edit_requested")
        self.assertEqual(self.owner.edit_prompt, ("reviewer-one", self.s.digest(), "edit_x"))

    def test_hold_clears_checks(self):
        self.run_event(self.event("s")); self.run_event(self.event("c")); self.run_event(self.event("h"))
        with self.assertRaises(ButtonReviewError):
            self.run_event()

    def test_owner_race_revalidated(self):
        real = self.owner.apply_review_action
        def raced(**kw):
            self.owner.current = replace(self.s, x_copy="concurrent edit")
            return real(**kw)
        self.owner.apply_review_action = raced
        with self.assertRaisesRegex(ButtonReviewError, "version_conflict"):
            self.run_event(self.event("s"))

    def test_unknown_receipts_are_not_success(self):
        self.assertIn("자동 재전송 안 함", delivery_label("confirmed", "unknown"))
        self.assertIn("일부", delivery_label("confirmed", "failed"))
        self.assertIn("공개 링크 확인 필요", delivery_label("confirmed", "confirmed"))

    def test_snapshot_rejects_wrong_source_and_private_material(self):
        for kw in ({"source_url":"https://x.com/SquidRouter/status/1"},
                   {"telegram_copy":"t.me/+" + "synthetic_private_invite"}, {"telegram_copy":"x"*3701}):
            with self.subTest(kw=kw), self.assertRaises(ButtonReviewError):
                replace(self.s, **kw).digest()

    def test_malformed_event_not_parsed_as_trusted(self):
        with self.assertRaises(ButtonReviewError):
            self.run_event({"actor_id":"reviewer-one"})


if __name__ == "__main__":
    unittest.main()
