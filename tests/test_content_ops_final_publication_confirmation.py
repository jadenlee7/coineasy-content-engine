"""Offline second-stage card tests; FakeOwner is NOT publication authority."""

import unittest
from dataclasses import replace

from core.content_ops.final_publication_confirmation import (
    FinalConfirmationError, FinalConfirmationSigner, FinalConfirmationSnapshot,
    VerifiedFinalCallback, final_confirmation_messages, handle_final_confirmation,
)
from core.content_ops.review_buttons import ReviewSnapshot


NOW = 1790340000
W = "11111111-1111-4111-8111-111111111111"
I = "22222222-2222-4222-8222-222222222222"
V = "33333333-3333-4333-8333-333333333333"
R = "44444444-4444-4444-8444-444444444444"
C = "55555555-5555-4555-8555-555555555555"
A = "66666666-6666-4666-8666-666666666666"
OTHER = "77777777-7777-4777-8777-777777777777"
ROOM = "fixture-private-room"
ROOM = "b" * 64
MESSAGE = "d" * 64
BOT = "e" * 64
HUMAN = "f" * 64


def snapshot(client="yellow"):
    handles = {"yellow": "Yellow", "babylon": "babylonlabs_io",
               "squid": "SquidRouter", "origintrail": "origin_trail"}
    review = ReviewSnapshot(W, client, I, V,
        f"https://x.com/{handles[client]}/status/123",
        "2026-09-25T12:15:00Z", "확인된 텔레그램 공지 전문",
        "확인된 X 문안", "a" * 64, "daily_ready")
    return FinalConfirmationSnapshot(review, R, C, A, A, A, 2, 2, 2,
        "b" * 64, 0, 0, "c" * 40)


class FakeOwner:
    def __init__(self, current):
        self.current = current
        self.reads = 0
        self.applies = 0
        self.decisions = {}

    def read_confirmation(self, room, message, bot, human):
        self.reads += 1
        if (room, message, bot, human) != (ROOM, MESSAGE, BOT, HUMAN):
            raise FinalConfirmationError("final_confirmation_unregistered")
        return self.current

    def apply_final_decision(self, **request):
        self.applies += 1
        if request["snapshot_sha256"] != self.current.digest() or (
                request["version_id"] != self.current.review.content_version_id):
            raise FinalConfirmationError("final_confirmation_version_conflict")
        key = request["idempotency_key"]
        if key in self.decisions:
            return {**self.decisions[key], "reused": True}
        result = {"status": "confirmed_pending_publication_owner"
                  if request["action"] == "confirm_publication" else "held",
                  "decision_id": OTHER, "reused": False}
        self.decisions[key] = result
        return result


class FinalPublicationConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.s = snapshot()
        self.signer = FinalConfirmationSigner(b"f" * 32)
        self.owner = FakeOwner(self.s)

    def event(self, action="p", **changes):
        token = self.signer.issue(self.s, action, ROOM, now=NOW, expires_at=NOW + 900)
        return replace(VerifiedFinalCallback("callback-1", A, False, ROOM,
            MESSAGE, BOT, HUMAN, token), **changes)

    def handle(self, event=None, **changes):
        args = dict(enabled=True, signer=self.signer, owner=self.owner,
                    allowed_reviewers=frozenset({A, OTHER}), room_binding=ROOM,
                    runtime_release_sha=self.s.release_sha, now=NOW)
        args.update(changes)
        return handle_final_confirmation(event or self.event(), **args)

    def test_disabled_before_owner_io(self):
        self.assertEqual(self.handle(enabled=False),
                         {"status": "disabled", "public_send_attempted": False})
        self.assertEqual((self.owner.reads, self.owner.applies), (0, 0))
        self.assertEqual(self.handle(enabled="true")["status"], "disabled")

    def test_all_clients_show_exact_copy_banner_and_destinations(self):
        for client in ("yellow", "babylon", "squid", "origintrail"):
            with self.subTest(client=client):
                s = snapshot(client)
                packet = final_confirmation_messages(s, self.signer, ROOM, now=NOW)
                self.assertEqual(packet["telegram"]["text"], s.review.telegram_copy)
                self.assertEqual(packet["x"]["text"], s.review.x_copy)
                self.assertEqual(packet["banner"], {"sha256": s.review.banner_sha256})
                self.assertEqual(len(packet["controls"]["reply_markup"]["inline_keyboard"][0]), 2)
                self.assertIn("Typefully", packet["controls"]["text"])
                self.assertIn("공식 채널", packet["controls"]["text"])
                self.assertIn("공개 게시 대기열이나 전송 완료를 뜻하지 않습니다", packet["controls"]["text"])
                for button in packet["controls"]["reply_markup"]["inline_keyboard"][0]:
                    self.assertEqual(len(button["callback_data"]), 55)
                    self.assertTrue(button["callback_data"].startswith("ce2:"))

    def test_checks_must_be_same_reviewer_and_epoch(self):
        for changed in (replace(self.s, source_check_actor_id=OTHER),
                        replace(self.s, claims_check_actor_id=OTHER),
                        replace(self.s, source_check_epoch=1),
                        replace(self.s, claims_check_epoch=3)):
            with self.subTest(changed=changed), self.assertRaisesRegex(
                    FinalConfirmationError, "checks_incomplete"):
                final_confirmation_messages(changed, self.signer, ROOM, now=NOW)

    def test_only_unapproved_unpublished_daily_version(self):
        for changed in (replace(self.s, approval_count=1),
                        replace(self.s, publication_count=1),
                        replace(self.s, review=replace(self.s.review, eligibility="blocked"))):
            with self.subTest(changed=changed), self.assertRaises(FinalConfirmationError):
                final_confirmation_messages(changed, self.signer, ROOM, now=NOW)

    def test_source_time_must_be_explicit_and_timezone_aware(self):
        for value in ("2026-09-25T12:15:00", "unknown", "2026-09-25T12:15:00Z\n게시 승인"):
            with self.subTest(value=value), self.assertRaises(FinalConfirmationError):
                final_confirmation_messages(replace(self.s,
                    review=replace(self.s.review, source_published_at=value)),
                    self.signer, ROOM, now=NOW)

    def test_confirm_is_private_decision_never_a_queue_or_provider_send(self):
        self.assertEqual(self.handle(),
                         {"status": "confirmed_pending_publication_owner",
                          "decision_id": OTHER, "reused": False,
                          "public_send_attempted": False})
        self.assertEqual(self.owner.applies, 1)
        self.assertEqual(self.handle()["reused"], True)
        self.assertEqual(len(self.owner.decisions), 1)

    def test_hold_is_separate_decision(self):
        result = self.handle(self.event("h"))
        self.assertEqual(result["status"], "held")
        self.assertFalse(result["public_send_attempted"])

    def test_stale_card_and_changed_copy_or_banner_rejected_before_write(self):
        for changed in (replace(self.s, card_id=OTHER),
                        replace(self.s, version_fingerprint="c" * 64),
                        replace(self.s, review=replace(self.s.review, x_copy="new X")),
                        replace(self.s, review=replace(self.s.review, banner_sha256="c" * 64)),
                        replace(self.s, review=replace(self.s.review, content_version_id=OTHER))):
            with self.subTest(changed=changed):
                self.owner.current = changed
                with self.assertRaisesRegex(FinalConfirmationError, "stale_or_invalid"):
                    self.handle()
        self.assertEqual(self.owner.applies, 0)

    def test_actor_room_and_message_are_bound(self):
        for event in (self.event(actor_id="outsider"), self.event(actor_is_bot=True),
                      self.event(room_binding="other-room")):
            with self.subTest(event=event), self.assertRaisesRegex(
                    FinalConfirmationError, "actor_forbidden"):
                self.handle(event)
        self.assertEqual(self.owner.reads, 0)
        with self.assertRaisesRegex(FinalConfirmationError, "actor_forbidden"):
            self.handle(self.event(actor_id=OTHER))
        self.assertEqual(self.owner.reads, 1)
        with self.assertRaisesRegex(FinalConfirmationError, "unregistered"):
            self.handle(self.event(message_binding="0" * 64))
        for event in (self.event(bot_binding="0" * 64),
                      self.event(human_binding="0" * 64)):
            with self.assertRaises(FinalConfirmationError):
                self.handle(event)
        self.assertEqual(self.owner.applies, 0)

    def test_runtime_release_must_match_before_decision(self):
        with self.assertRaisesRegex(FinalConfirmationError, "release_conflict"):
            self.handle(runtime_release_sha="0" * 40)
        self.assertEqual(self.owner.applies, 0)

    def test_private_card_or_tampered_token_cannot_confirm(self):
        token = self.event().token
        for changed in ("ce1:" + token[4:], "ce2:" + "A" + token[5:], token[:-1] + "A"):
            with self.subTest(changed=changed), self.assertRaises(FinalConfirmationError):
                self.handle(self.event(token=changed))
        self.assertEqual(self.owner.applies, 0)

    def test_expiry_and_bad_owner_readback_fail_closed(self):
        with self.assertRaisesRegex(FinalConfirmationError, "expired"):
            self.handle(now=NOW + 900)
        self.owner.apply_final_decision = lambda **kw: {"status": "sent", "reused": False}
        with self.assertRaisesRegex(FinalConfirmationError, "readback_unknown"):
            self.handle()


if __name__ == "__main__":
    unittest.main()
