"""One-shot courier contract tests with no Telegram or database I/O."""
import asyncio
import hashlib
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from core.content_ops.private_review_card_courier import PreparedCard, PrivateCardCourier
from core.content_ops.private_review_card_receipt import (
    ObservedSend, prepare_private_card, private_card_packet_sha256,
)
from core.content_ops.review_buttons import ButtonSigner, ReviewSnapshot
from core.content_ops.review_edit_ingress import EditBindings


NOW = int(datetime(2026, 9, 23, 7, 0, tzinfo=timezone.utc).timestamp())
BOT, ROOM = 123456, -1001234567890
W = "11111111-1111-4111-8111-111111111111"
I = "22222222-2222-4222-8222-222222222222"
V = "33333333-3333-4333-8333-333333333333"
R = "44444444-4444-4444-8444-444444444444"
C = "55555555-5555-4555-8555-555555555555"
O = "66666666-6666-4666-8666-666666666666"
T = "77777777-7777-4777-8777-777777777777"
PNG = b"\x89PNG\r\n\x1a\nfixture-image-bytes"


def prepared():
    sha = hashlib.sha256(PNG).hexdigest()
    snapshot = ReviewSnapshot(W, "babylon", I, V,
        "https://x.com/babylonlabs_io/status/123", "2026-09-23T06:00:00Z",
        "Telegram 전문", "X 전문", sha, "blocked")
    review = {"id": R, "workspace_id": W, "client_id": "babylon",
              "content_item_id": I, "content_version_id": V,
              "version_fingerprint": "b" * 64, "epoch": 0, "state": "active",
              "expires_at": datetime.fromtimestamp(NOW + 1800, timezone.utc).isoformat()}
    requests = prepare_private_card(snapshot, ButtonSigner(b"s" * 32),
        "fixture-private-room", now=NOW)
    packet_sha = private_card_packet_sha256(requests, sha, R, C)
    return PreparedCard(review, snapshot, C, PNG, BOT, ROOM, None,
                        "fixture-private-room", NOW, O, T, packet_sha)


class Owner:
    def __init__(self, *, deny_at=None, fail_confirm_at=None, fail_register=False,
                 fail_bind=False):
        self.deny_at, self.fail_confirm_at = deny_at, fail_confirm_at
        self.fail_register, self.fail_bind = fail_register, fail_bind
        self.bound = []
        self.reserved = []
        self.confirmed = []
        self.registered = []
        self.terminal_reads = []
        self.terminal_sent = not fail_register

    async def bind_outbox(self, **values):
        self.bound.append(values)
        if self.fail_bind:
            raise OSError("uncertain outbox bind")
        return {"status": "bound", "execution_authorized": False}

    async def reserve_part(self, **values):
        self.reserved.append(values)
        if values["part_index"] == self.deny_at:
            return {"status": "reserved", "new_attempt": False,
                    "execution_authorized": False}
        return {"status": "reserved", "new_attempt": True,
                "execution_authorized": False}

    async def confirm_part(self, **values):
        self.confirmed.append(values)
        if values["part_index"] == self.fail_confirm_at:
            raise OSError("uncertain confirmation commit")
        return {"status": "confirmed", "new_confirmation": True,
                "execution_authorized": False}

    async def register_card(self, evidence):
        self.registered.append(evidence)
        if self.fail_register:
            raise OSError("uncertain commit")
        return {"status": "card_recorded", "card_id": evidence["target_card_id"],
                "reused": False, "execution_authorized": False}

    async def read_terminal(self, **values):
        self.terminal_reads.append(values)
        if self.terminal_sent:
            return {"status": "sent", "card_id": values["card_id"],
                    "outbox_id": values["outbox_id"], "execution_authorized": False}
        return {"status": "not_confirmed", "card_id": None,
                "outbox_id": None, "execution_authorized": False}


class Sender:
    def __init__(self, *, fail_at=None):
        self.fail_at = fail_at
        self.preflight_calls = []
        self.calls = []

    async def preflight(self, **values):
        self.preflight_calls.append(values)

    async def send_once(self, request, *, png):
        index = len(self.calls)
        self.calls.append((request, png))
        if index == self.fail_at:
            raise TimeoutError("unknown provider result")
        result = {"message_id": 100 + index, "date": NOW + index,
                  "chat": {"id": ROOM, "type": "supergroup"},
                  "from": {"id": BOT, "is_bot": True}}
        result["caption" if index == 0 else "text"] = request["text"]
        if index == 0:
            result["photo"] = [{"file_id": "fixture-photo"}]
        if index == 3:
            result["reply_markup"] = request["reply_markup"]
        return ObservedSend(request["method"], 200,
            json.dumps({"ok": True, "result": result}, ensure_ascii=False).encode(),
            datetime.fromtimestamp(NOW + index, timezone.utc))


class CardCourierTest(unittest.TestCase):
    def setUp(self):
        self.owner = Owner()
        self.sender = Sender()
        self.courier = PrivateCardCourier(self.owner, self.sender,
            ButtonSigner(b"s" * 32), EditBindings(b"e" * 32), clock=lambda: NOW)

    def run_card(self, **values):
        return asyncio.run(self.courier.run(prepared(), **values))

    def test_disabled_means_zero_io(self):
        self.assertEqual(self.run_card(), {"status": "disabled", "confirmed_parts": 0,
                                          "public_send_attempted": False})
        self.assertEqual((self.sender.calls, self.owner.reserved, self.owner.registered),
                         ([], [], []))
        self.assertEqual(self.run_card(enabled="true")["status"], "disabled")
        self.assertEqual(self.sender.preflight_calls, [])

    def test_four_confirmed_parts_then_one_registration(self):
        self.assertEqual(self.run_card(enabled=True),
                         {"status": "card_recorded", "confirmed_parts": 4,
                          "public_send_attempted": False})
        self.assertEqual([r["part_index"] for r in self.owner.reserved], [0, 1, 2, 3])
        self.assertEqual(self.owner.bound, [{"review_id": R, "outbox_id": O,
            "claim_token": T, "packet_sha256": prepared().packet_sha256}])
        self.assertEqual([r["part_index"] for r in self.owner.confirmed], [0, 1, 2, 3])
        self.assertEqual(len(self.sender.calls), 4)
        self.assertIs(self.sender.calls[0][1], PNG)
        self.assertTrue(all(png is None for _, png in self.sender.calls[1:]))
        self.assertEqual(len(self.owner.registered), 1)
        self.assertEqual(self.owner.terminal_reads,
            [{"review_id": R, "card_id": C, "outbox_id": O}])
        self.assertEqual([r["payload_sha256"] for r in self.owner.reserved[:3]],
            [p["payload_sha256"] for p in self.owner.registered[0]["target_parts"]])
        self.assertEqual([r["message_binding"] for r in self.owner.confirmed[:3]],
            [p["message_binding"] for p in self.owner.registered[0]["target_parts"]])

    def test_reused_reservation_stops_before_provider_call(self):
        self.owner.deny_at = 2
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        self.assertEqual(len(self.sender.calls), 2)
        self.assertEqual(self.owner.registered, [])

    def test_uncertain_outbox_bind_never_sends(self):
        self.owner.fail_bind = True
        self.assertEqual(self.run_card(enabled=True)["status"], "blocked")
        self.assertEqual(self.sender.calls, [])
        self.assertEqual(self.owner.reserved, [])

    def test_missing_outbox_claim_never_binds_or_sends(self):
        candidate = replace(prepared(), claim_token=None)
        self.assertEqual(asyncio.run(self.courier.run(candidate, enabled=True))["status"],
                         "blocked")
        self.assertEqual(self.owner.bound, [])
        self.assertEqual(self.sender.preflight_calls, [])

    def test_packet_mismatch_never_binds_or_sends(self):
        candidate = replace(prepared(), packet_sha256="d" * 64)
        self.assertEqual(asyncio.run(self.courier.run(candidate, enabled=True))["status"],
                         "blocked")
        self.assertEqual(self.owner.bound, [])
        self.assertEqual(self.sender.preflight_calls, [])

    def test_unknown_provider_delivery_never_sends_controls_or_registers(self):
        self.sender.fail_at = 2
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        self.assertEqual(len(self.sender.calls), 3)
        self.assertEqual(self.owner.registered, [])

    def test_unknown_confirmation_never_sends_next_part(self):
        self.owner.fail_confirm_at = 1
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        self.assertEqual(len(self.sender.calls), 2)
        self.assertEqual(len(self.owner.confirmed), 2)
        self.assertEqual(self.owner.registered, [])

    def test_uncertain_registration_not_retried(self):
        self.owner.fail_register = True
        self.owner.terminal_sent = False
        self.assertEqual(self.run_card(enabled=True)["status"], "delivery_unknown")
        self.assertEqual(len(self.owner.registered), 1)
        self.assertEqual(len(self.owner.terminal_reads), 1)
        self.assertEqual(len(self.sender.calls), 4)

    def test_lost_registration_ack_resolves_only_by_exact_terminal_readback(self):
        self.owner.fail_register = True
        self.owner.terminal_sent = True
        self.assertEqual(self.run_card(enabled=True)["status"], "card_recorded")
        self.assertEqual(len(self.owner.registered), 1)
        self.assertEqual(len(self.owner.terminal_reads), 1)
        self.assertEqual(len(self.sender.calls), 4)

    def test_same_card_cannot_be_rerun_even_with_naive_owner(self):
        self.assertEqual(self.run_card(enabled=True)["status"], "card_recorded")
        self.assertEqual(self.run_card(enabled=True)["status"], "blocked")
        self.assertEqual(len(self.sender.calls), 4)

    def test_image_mismatch_stops_before_preflight_or_reservation(self):
        candidate = prepared()
        candidate = PreparedCard(candidate.review, candidate.snapshot, candidate.card_id,
                                 PNG + b"tampered", candidate.bot_id, candidate.chat_id,
                                 candidate.thread_id, candidate.room_binding, candidate.now)
        result = asyncio.run(self.courier.run(candidate, enabled=True))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.sender.preflight_calls, [])
        self.assertEqual(self.owner.reserved, [])

    def test_stale_source_stops_before_preflight_or_reservation(self):
        candidate = prepared()
        stale = ReviewSnapshot(candidate.snapshot.workspace_id,
            candidate.snapshot.client_id, candidate.snapshot.content_item_id,
            candidate.snapshot.content_version_id, candidate.snapshot.source_url,
            "2026-09-21T06:00:00Z", candidate.snapshot.telegram_copy,
            candidate.snapshot.x_copy, candidate.snapshot.banner_sha256, "blocked")
        candidate = PreparedCard(candidate.review, stale, candidate.card_id,
            candidate.png, candidate.bot_id, candidate.chat_id,
            candidate.thread_id, candidate.room_binding, candidate.now)
        result = asyncio.run(self.courier.run(candidate, enabled=True))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.sender.preflight_calls, [])
        self.assertEqual(self.owner.reserved, [])

    def test_stale_clock_stops_before_preflight_or_reservation(self):
        candidate = prepared()
        candidate = PreparedCard(candidate.review, candidate.snapshot, candidate.card_id,
            candidate.png, candidate.bot_id, candidate.chat_id,
            candidate.thread_id, candidate.room_binding, NOW - 10)
        result = asyncio.run(self.courier.run(candidate, enabled=True))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.sender.preflight_calls, [])
        self.assertEqual(self.owner.reserved, [])

    def test_source_expiry_between_parts_stops_next_reservation(self):
        candidate = prepared()
        near_expiry = replace(candidate.snapshot, source_published_at=
            datetime.fromtimestamp(NOW - 24 * 3600 + 1, timezone.utc)
            .isoformat().replace("+00:00", "Z"))
        requests = prepare_private_card(near_expiry, ButtonSigner(b"s" * 32),
            candidate.room_binding, now=NOW)
        candidate = replace(candidate, snapshot=near_expiry,
            packet_sha256=private_card_packet_sha256(requests,
                near_expiry.banner_sha256, R, C))
        moments = iter((NOW, NOW, NOW + 2))
        self.courier._clock = lambda: next(moments)
        result = asyncio.run(self.courier.run(candidate, enabled=True))
        self.assertEqual(result["status"], "delivery_unknown")
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(len(self.owner.reserved), 1)


if __name__ == "__main__":
    unittest.main()
