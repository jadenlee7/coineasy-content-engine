"""Mock HTTP only. No real Telegram token, room, or network request."""
import asyncio
import json
import unittest
import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import httpx

from core.content_ops.private_review_card_receipt import (
    prepare_private_card, private_card_packet_sha256, validate_part_response,
)
from core.content_ops.private_review_card_courier import PreparedCard, PrivateCardCourier
from core.content_ops.private_review_card_sender import (
    PrivateCardSenderError, TelegramPrivateCardSender,
)
from core.content_ops.review_buttons import ButtonSigner, ReviewSnapshot
from core.content_ops.review_edit_ingress import EditBindings


NOW = int(datetime(2026, 9, 23, 7, 0, tzinfo=timezone.utc).timestamp())
BOT, ROOM = 123456, -1001234567890  # Synthetic fixtures only.
TOKEN = str(BOT) + ":" + "x" * 32
PNG = b"\x89PNG\r\n\x1a\nfixture-image-bytes"
SNAPSHOT = ReviewSnapshot("11111111-1111-4111-8111-111111111111", "babylon",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "https://x.com/babylonlabs_io/status/123", "2026-09-23T06:00:00Z",
    "Telegram 전문", "X 전문", "a" * 64, "blocked")
PACKET = prepare_private_card(SNAPSHOT, ButtonSigner(b"s" * 32),
                              "fixture-private-room", now=NOW)


class TelegramSenderTest(unittest.TestCase):
    def sender(self, handler, *, clock=None):
        return TelegramPrivateCardSender(bot_token=TOKEN, bot_id=BOT, chat_id=ROOM,
            transport=httpx.MockTransport(handler),
            clock=clock or (lambda: datetime.fromtimestamp(NOW + 3, timezone.utc)))

    def test_exact_private_preflight_then_four_one_shot_sends(self):
        methods = []
        send_index = 0

        def handler(request):
            nonlocal send_index
            method = request.url.path.rsplit("/", 1)[-1]
            methods.append(method)
            self.assertEqual(request.url.host, "api.telegram.org")
            if method == "getMe":
                result = {"id": BOT, "is_bot": True,
                          "username": "coineasy_review_bot"}
            elif method == "getChat":
                result = {"id": ROOM, "type": "supergroup"}
            elif method == "getChatMember":
                result = {"status": "member", "user": {"id": BOT, "is_bot": True}}
            else:
                expected = PACKET[send_index]
                self.assertEqual(method, expected["method"])
                if method == "sendPhoto":
                    self.assertIn(PNG, request.content)
                    self.assertIn(expected["text"].encode(), request.content)
                else:
                    body = json.loads(request.content)
                    self.assertEqual(body["chat_id"], ROOM)
                    self.assertEqual(body["text"], expected["text"])
                    self.assertTrue(body["protect_content"])
                    if expected["kind"] == "controls":
                        self.assertEqual(body["reply_markup"], expected["reply_markup"])
                result = {"message_id": 100 + send_index, "date": NOW + send_index,
                          "chat": {"id": ROOM, "type": "supergroup"},
                          "from": {"id": BOT, "is_bot": True}}
                result["caption" if send_index == 0 else "text"] = expected["text"]
                if send_index == 0:
                    result["photo"] = [{"file_id": "fixture"}]
                if send_index == 3:
                    result["reply_markup"] = expected["reply_markup"]
                send_index += 1
            return httpx.Response(200, json={"ok": True, "result": result})

        async def run():
            sender = self.sender(handler)
            await sender.preflight(bot_id=BOT, chat_id=ROOM)
            for index, request in enumerate(PACKET):
                observed = await sender.send_once(request, png=PNG if index == 0 else None)
                mid, _, _ = validate_part_response(observed, request,
                    bot_id=BOT, chat_id=ROOM, thread_id=None)
                self.assertEqual(mid, 100 + index)
            with self.assertRaises(PrivateCardSenderError):
                await sender.send_once(PACKET[3], png=None)

        asyncio.run(run())
        self.assertEqual(methods, ["getMe", "getChat", "getChatMember",
                                   "sendPhoto", "sendMessage", "sendMessage", "sendMessage"])

    def test_wrong_room_or_malformed_controls_never_send(self):
        methods = []
        def handler(request):
            methods.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"ok": True, "result": {"id": BOT}})

        async def run():
            sender = self.sender(handler)
            with self.assertRaises(PrivateCardSenderError):
                await sender.preflight(bot_id=BOT, chat_id=ROOM + 1)
            self.assertEqual(methods, [])
            sender._verified = True  # Isolate request validation in this fixture.
            malformed = {**PACKET[3], "reply_markup": {"inline_keyboard": []}}
            with self.assertRaises(PrivateCardSenderError):
                await sender.send_once(malformed, png=None)
            self.assertEqual(methods, [])

    def test_provider_failure_redacts_response_and_token(self):
        def handler(_request):
            return httpx.Response(500, json={"ok": False, "description": TOKEN})
        async def run():
            sender = self.sender(handler)
            with self.assertRaises(PrivateCardSenderError) as raised:
                await sender.preflight(bot_id=BOT, chat_id=ROOM)
            self.assertNotIn(TOKEN, str(raised.exception))
        asyncio.run(run())

    def test_public_room_or_admin_bot_fails_preflight_without_send(self):
        for room_public, bot_admin in ((True, False), (False, True)):
            methods = []

            def handler(request):
                method = request.url.path.rsplit("/", 1)[-1]
                methods.append(method)
                if method == "getMe":
                    result = {"id": BOT, "is_bot": True,
                              "username": "coineasy_review_bot"}
                elif method == "getChat":
                    result = {"id": ROOM, "type": "supergroup"}
                    if room_public:
                        result["username"] = "public_fixture"
                else:
                    result = {"status": "administrator" if bot_admin else "member",
                              "user": {"id": BOT, "is_bot": True}}
                return httpx.Response(200, json={"ok": True, "result": result})

            async def run():
                sender = self.sender(handler)
                with self.assertRaises(PrivateCardSenderError):
                    await sender.preflight(bot_id=BOT, chat_id=ROOM)
                with self.assertRaises(PrivateCardSenderError):
                    await sender.send_once(PACKET[0], png=PNG)

            asyncio.run(run())
            self.assertEqual(methods, ["getMe", "getChat"] +
                             ([] if room_public else ["getChatMember"]))

    def test_control_first_and_unknown_image_cannot_advance(self):
        methods = []
        def handler(request):
            methods.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"ok": False,
                                             "description": "ambiguous fixture"})

        async def run():
            sender = self.sender(handler)
            sender._verified = True  # Isolate the send state machine.
            with self.assertRaises(PrivateCardSenderError):
                await sender.send_once(PACKET[3], png=None)
            self.assertEqual(methods, [])
            with self.assertRaises(PrivateCardSenderError):
                await sender.send_once(PACKET[0], png=PNG)
            with self.assertRaises(PrivateCardSenderError):
                await sender.send_once(PACKET[1], png=None)

        asyncio.run(run())
        self.assertEqual(methods, ["sendPhoto"])

    def test_mock_sender_composes_with_one_shot_courier(self):
        snapshot = replace(SNAPSHOT, banner_sha256=hashlib.sha256(PNG).hexdigest())
        review = {"id": "44444444-4444-4444-8444-444444444444",
                  "workspace_id": snapshot.workspace_id, "client_id": snapshot.client_id,
                  "content_item_id": snapshot.content_item_id,
                  "content_version_id": snapshot.content_version_id,
                  "version_fingerprint": "b" * 64, "epoch": 0, "state": "active",
                  "expires_at": datetime.fromtimestamp(NOW + 1800, timezone.utc).isoformat()}
        card_id = "55555555-5555-4555-8555-555555555555"
        packet = prepare_private_card(snapshot, ButtonSigner(b"s" * 32),
            "fixture-private-room", now=NOW)
        calls = []

        def handler(request):
            method = request.url.path.rsplit("/", 1)[-1]
            calls.append(method)
            if method == "getMe":
                result = {"id": BOT, "is_bot": True,
                          "username": "coineasy_review_bot"}
            elif method == "getChat":
                result = {"id": ROOM, "type": "supergroup"}
            elif method == "getChatMember":
                result = {"status": "member", "user": {"id": BOT, "is_bot": True}}
            else:
                index = len(calls) - 4
                request_part = packet[index]
                result = {"message_id": 100 + index, "date": NOW + index,
                          "chat": {"id": ROOM, "type": "supergroup"},
                          "from": {"id": BOT, "is_bot": True}}
                result["caption" if index == 0 else "text"] = request_part["text"]
                if index == 0:
                    result["photo"] = [{"file_id": "fixture"}]
                if index == 3:
                    result["reply_markup"] = request_part["reply_markup"]
            return httpx.Response(200, json={"ok": True, "result": result})

        class Owner:
            async def bind_outbox(self, **_fields):
                return {"status": "bound", "execution_authorized": False}
            async def reserve_part(self, **_fields):
                return {"status": "reserved", "new_attempt": True,
                        "execution_authorized": False}
            async def confirm_part(self, **_fields):
                return {"status": "confirmed", "new_confirmation": True,
                        "execution_authorized": False}
            async def register_card(self, evidence):
                return {"status": "card_recorded",
                        "card_id": evidence["target_card_id"], "reused": False,
                        "execution_authorized": False}

        sender = self.sender(handler, clock=lambda: datetime.fromtimestamp(
            NOW + 3, timezone.utc))
        courier = PrivateCardCourier(Owner(), sender, ButtonSigner(b"s" * 32),
            EditBindings(b"e" * 32), clock=lambda: NOW)
        candidate = PreparedCard(review, snapshot, card_id, PNG, BOT, ROOM,
                                 None, "fixture-private-room", NOW,
                                 "66666666-6666-4666-8666-666666666666",
                                 "77777777-7777-4777-8777-777777777777",
                                 private_card_packet_sha256(packet,
                                     snapshot.banner_sha256, review["id"], card_id))
        result = asyncio.run(courier.run(candidate, enabled=True))
        self.assertEqual(result["status"], "card_recorded")
        self.assertEqual(result["confirmed_parts"], 4)
        self.assertEqual(calls, ["getMe", "getChat", "getChatMember",
                                 "sendPhoto", "sendMessage", "sendMessage", "sendMessage"])


if __name__ == "__main__":
    unittest.main()
