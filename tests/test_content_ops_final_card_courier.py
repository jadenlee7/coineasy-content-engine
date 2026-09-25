"""Synthetic courier contract tests; no Telegram, DB, or public provider I/O."""
import asyncio
import hashlib
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from core.content_ops.final_card_courier import (
    FinalCardCourier, FinalCardCourierError, PreparedFinalCard, final_card_packet_sha256,
    prepare_final_card,
)
from core.content_ops.final_publication_confirmation import (
    FinalConfirmationSigner, FinalConfirmationSnapshot,
)
from core.content_ops.private_review_card_receipt import ObservedSend
from core.content_ops.review_buttons import ReviewSnapshot
from core.content_ops.review_edit_ingress import EditBindings


NOW = int(datetime(2026, 9, 25, 13, 0, tzinfo=timezone.utc).timestamp())
W = "11111111-1111-4111-8111-111111111111"
I = "22222222-2222-4222-8222-222222222222"
V = "33333333-3333-4333-8333-333333333333"
R = "44444444-4444-4444-8444-444444444444"
C = "55555555-5555-4555-8555-555555555555"
A = "66666666-6666-4666-8666-666666666666"
D = "77777777-7777-4777-8777-777777777777"
BOT, ROOM, HUMAN = 123456, -1001234567890, 234567
PNG = b"\x89PNG\r\n\x1a\nfixture-final-card-banner"
RELEASE = "a" * 40


def prepared():
    source_at = datetime.fromtimestamp(NOW - 3600, timezone.utc)
    review = ReviewSnapshot(W, "yellow", I, V,
        "https://x.com/Yellow/status/123", source_at.isoformat(),
        "검수 완료된 Telegram 전문", "Verified X copy",
        hashlib.sha256(PNG).hexdigest(), "daily_ready")
    snapshot = FinalConfirmationSnapshot(review, R, C, A, A, A, 0, 0, 0,
        "b" * 64, 0, 0, RELEASE)
    return PreparedFinalCard(snapshot, D, PNG, BOT, ROOM, HUMAN, None, NOW)


class FakeOwner:
    def __init__(self, *, fail_confirm_at=None, fail_registration=False,
                 terminal_present=True):
        self.reservation = None
        self.parts = {}
        self.confirmed = {}
        self.registered = False
        self.reserve_calls = 0
        self.begin_calls = 0
        self.register_calls = 0
        self.read_calls = 0
        self.fail_confirm_at = fail_confirm_at
        self.fail_registration = fail_registration
        self.terminal_present = terminal_present

    async def reserve_delivery(self, **values):
        self.reserve_calls += 1
        if self.reservation is not None:
            return {"status": "delivery_unknown", "execution_authorized": False}
        self.reservation = values
        return {"status": "delivery_reserved", "delivery_id": values["delivery_id"],
                "expires_at": datetime.fromtimestamp(NOW + 600,
                    timezone.utc).isoformat(), "execution_authorized": False}

    async def begin_part(self, **values):
        self.begin_calls += 1
        index = values["part_index"]
        if index in self.parts:
            return {"status": "delivery_unknown", "reused": True,
                    "execution_authorized": False}
        if set(self.confirmed) != set(range(index)):
            return {"status": "blocked", "reused": False,
                    "execution_authorized": False}
        self.parts[index] = values
        return {"status": "attempt_recorded", "reused": False,
                "execution_authorized": False}

    async def confirm_part(self, **values):
        index = values["part_index"]
        self.confirmed[index] = values
        if index == self.fail_confirm_at:
            raise OSError("lost commit acknowledgement")
        return {"status": "confirmed", "reused": False,
                "execution_authorized": False}

    async def register_card(self, *, delivery_id):
        self.register_calls += 1
        if set(self.confirmed) != set(range(4)):
            raise AssertionError("incomplete card")
        self.registered = True
        if self.fail_registration:
            raise OSError("lost registration acknowledgement")
        return {"status": "card_registered", "card_id": delivery_id,
                "reused": False, "execution_authorized": False}

    async def read_registered(self, *, delivery_id):
        self.read_calls += 1
        return {"status": "card_registered" if self.registered and self.terminal_present
                else "unknown", "card_id": delivery_id if self.registered
                and self.terminal_present else None, "execution_authorized": False}


class FakeSender:
    def __init__(self, *, fail_at=None, wrong_room_at=None, old_message_at=None):
        self.fail_at = fail_at
        self.wrong_room_at = wrong_room_at
        self.old_message_at = old_message_at
        self.preflights = []
        self.calls = []

    async def preflight(self, **values):
        self.preflights.append(values)

    async def send_once(self, request, *, png):
        index = len(self.calls)
        self.calls.append((request, png))
        if index == self.fail_at:
            raise TimeoutError("provider outcome unknown")
        message = {"message_id": 99 if index == self.old_message_at else 100 + index,
                   "date": NOW + index,
                   "chat": {"id": ROOM + (1 if index == self.wrong_room_at else 0),
                            "type": "supergroup"},
                   "from": {"id": BOT, "is_bot": True}}
        message["caption" if index == 0 else "text"] = request["text"]
        if index == 0:
            message["photo"] = [{"file_id": "fixture-photo"}]
        if index == 3:
            message["reply_markup"] = request["reply_markup"]
        return ObservedSend(request["method"], 200,
            json.dumps({"ok": True, "result": message}, ensure_ascii=False).encode(),
            datetime.fromtimestamp(NOW + index, timezone.utc))


class FinalCardCourierTest(unittest.TestCase):
    def setUp(self):
        self.owner = FakeOwner()
        self.sender = FakeSender()
        self.signer = FinalConfirmationSigner(b"f" * 32)
        self.bindings = EditBindings(b"e" * 32)
        self.courier = FinalCardCourier(self.owner, self.sender, self.signer,
            self.bindings, runtime_release_sha=RELEASE, clock=lambda: NOW)

    def run_card(self, candidate=None, **kwargs):
        return asyncio.run(self.courier.run(candidate or prepared(), **kwargs))

    def test_default_off_performs_zero_io(self):
        self.assertEqual(self.run_card(), {"status": "disabled", "confirmed_parts": 0,
                                          "public_send_attempted": False})
        self.assertEqual((self.sender.preflights, self.owner.reserve_calls,
                          self.sender.calls), ([], 0, []))

    def test_callback_and_identity_keys_must_be_separate(self):
        with self.assertRaises(FinalCardCourierError):
            FinalCardCourier(self.owner, self.sender,
                FinalConfirmationSigner(b"e" * 32), self.bindings,
                runtime_release_sha=RELEASE)

    def test_four_bound_receipts_register_one_private_card(self):
        self.assertEqual(self.run_card(enabled=True),
            {"status": "card_registered", "confirmed_parts": 4,
             "public_send_attempted": False})
        self.assertEqual([part["part_index"] for part in self.owner.parts.values()],
                         [0, 1, 2, 3])
        self.assertEqual(len(self.sender.calls), 4)
        self.assertEqual(self.sender.calls[0][1], PNG)
        self.assertTrue(all(png is None for _, png in self.sender.calls[1:]))
        self.assertEqual((self.owner.register_calls, self.owner.read_calls), (1, 1))
        self.assertEqual(self.owner.reservation["release_sha"], RELEASE)
        self.assertEqual(self.owner.reservation["snapshot_sha256"],
                         prepared().snapshot.digest())
        self.assertRegex(self.owner.reservation["packet_sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(self.run_card(enabled=True)["status"], "blocked")
        self.assertEqual(len(self.sender.calls), 4)

    def test_packet_hash_binds_parent_and_release(self):
        candidate = prepared()
        room = self.bindings.digest("room", BOT, ROOM)
        requests = prepare_final_card(candidate.snapshot, self.signer, room, now=NOW)
        params = dict(requests=requests, banner_sha256=candidate.snapshot.review.banner_sha256,
            snapshot_sha256=candidate.snapshot.digest(), delivery_id=D, review_id=R,
            parent_card_id=C, release_sha=RELEASE)
        actual = final_card_packet_sha256(**params)
        self.assertNotEqual(actual, final_card_packet_sha256(**{
            **params, "parent_card_id": I}))
        self.assertNotEqual(actual, final_card_packet_sha256(**{
            **params, "release_sha": "c" * 40}))

    def test_unknown_send_stops_before_controls_and_registration(self):
        self.sender.fail_at = 2
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        self.assertEqual(len(self.sender.calls), 3)
        self.assertEqual(self.owner.register_calls, 0)

    def test_new_process_does_not_resend_after_unknown(self):
        self.sender.fail_at = 1
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        second = FakeSender()
        fresh = FinalCardCourier(self.owner, second, self.signer,
            self.bindings, runtime_release_sha=RELEASE, clock=lambda: NOW)
        self.assertEqual(asyncio.run(fresh.run(prepared(), enabled=True))["status"],
                         "delivery_unknown")
        self.assertEqual(second.calls, [])
        self.assertEqual(self.owner.reserve_calls, 2)

    def test_lost_confirmation_never_sends_next_part(self):
        self.owner.fail_confirm_at = 1
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        self.assertEqual(len(self.sender.calls), 2)
        self.assertEqual(self.owner.register_calls, 0)

    def test_lost_registration_ack_reconciles_only_exact_id(self):
        self.owner.fail_registration = True
        self.assertEqual(self.run_card(enabled=True)["status"], "card_registered")
        self.assertEqual((self.owner.register_calls, self.owner.read_calls), (1, 1))
        self.owner = FakeOwner(fail_registration=True, terminal_present=False)
        self.courier = FinalCardCourier(self.owner, FakeSender(), self.signer,
            self.bindings, runtime_release_sha=RELEASE, clock=lambda: NOW)
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        self.assertEqual(self.owner.register_calls, 1)

    def test_wrong_room_receipt_cannot_confirm_or_register(self):
        self.sender.wrong_room_at = 0
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        self.assertEqual(self.owner.confirmed, {})
        self.assertEqual(self.owner.register_calls, 0)

    def test_nonmonotonic_message_receipt_stops_next_part(self):
        self.sender.old_message_at = 1
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        self.assertEqual(len(self.sender.calls), 2)
        self.assertEqual(set(self.owner.confirmed), {0})
        self.assertEqual(self.owner.register_calls, 0)

    def test_bad_banner_release_or_old_source_blocks_before_io(self):
        candidate = prepared()
        old_source = datetime.fromtimestamp(NOW - 25 * 3600,
            timezone.utc).isoformat()
        for changed in (replace(candidate, png=PNG + b"tampered"),
                        replace(candidate, snapshot=replace(candidate.snapshot,
                            release_sha="c" * 40)),
                        replace(candidate, snapshot=replace(candidate.snapshot,
                            review=replace(candidate.snapshot.review,
                                source_published_at=old_source)))):
            with self.subTest(changed=changed):
                self.assertEqual(self.run_card(changed, enabled=True)["status"], "blocked")
        self.assertEqual((self.sender.preflights, self.owner.reserve_calls), ([], 0))

    def test_oversized_full_copy_is_blocked_not_truncated(self):
        candidate = prepared()
        changed = replace(candidate, snapshot=replace(candidate.snapshot,
            review=replace(candidate.snapshot.review, telegram_copy="A" * 4097)))
        self.assertEqual(self.run_card(changed, enabled=True)["status"], "blocked")
        self.assertEqual(self.owner.reserve_calls, 0)

    def test_source_expiry_between_parts_stops_next_send(self):
        candidate = prepared()
        near_expiry = datetime.fromtimestamp(NOW - 24 * 3600 + 1,
            timezone.utc).isoformat()
        candidate = replace(candidate, snapshot=replace(candidate.snapshot,
            review=replace(candidate.snapshot.review,
                source_published_at=near_expiry)))
        moments = iter((NOW, NOW, NOW + 2))
        self.courier._clock = lambda: next(moments)
        self.assertEqual(self.run_card(candidate, enabled=True)["status"],
                         "delivery_unknown")
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(self.owner.begin_calls, 1)


if __name__ == "__main__":
    unittest.main()
