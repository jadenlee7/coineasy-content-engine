"""Offline card evidence only: no DB, bot API, or publication calls."""
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta

from core.content_ops.private_review_card_receipt import (
    CardReceiptError, ObservedSend, card_registration_evidence, prepare_private_card,
)
from core.content_ops.prompt_reservation import card_parent_binding
from core.content_ops.review_buttons import ButtonSigner, ReviewSnapshot
from core.content_ops.review_edit_ingress import EditBindings


NOW = int(datetime(2026, 9, 23, 7, 0, tzinfo=timezone.utc).timestamp())
BOT, ROOM = 123456, -1001234567890
W = "11111111-1111-4111-8111-111111111111"
I = "22222222-2222-4222-8222-222222222222"
V = "33333333-3333-4333-8333-333333333333"
R = "44444444-4444-4444-8444-444444444444"
C = "55555555-5555-4555-8555-555555555555"


def snapshot(client="babylon"):
    handles = {"yellow": "Yellow", "babylon": "babylonlabs_io",
               "squid": "SquidRouter", "origintrail": "origin_trail"}
    return ReviewSnapshot(W, client, I, V,
        f"https://x.com/{handles[client]}/status/123", "2026-09-23T06:00:00Z",
        "한국어 Telegram 공지 전문", "X 공지 전문", "a" * 64, "blocked")


def review(client="babylon"):
    return {"id": R, "workspace_id": W, "client_id": client,
            "content_item_id": I, "content_version_id": V,
            "version_fingerprint": "b" * 64, "epoch": 0, "state": "active",
            "expires_at": datetime.fromtimestamp(NOW + 1800, timezone.utc).isoformat()}


class CardReceiptTest(unittest.TestCase):
    def setUp(self):
        self.signer = ButtonSigner(b"s" * 32)
        self.bindings = EditBindings(b"e" * 32)
        self.room_binding = "fixture-private-room"

    def evidence(self, client="babylon", **changes):
        s = snapshot(client)
        parts = prepare_private_card(s, self.signer, self.room_binding, now=NOW)
        observed = []
        for i, expected in enumerate(parts):
            result = {"message_id": 100 + i, "date": NOW + i,
                      "chat": {"id": ROOM, "type": "supergroup"},
                      "from": {"id": BOT, "is_bot": True}}
            result["caption" if i == 0 else "text"] = expected["text"]
            if i == 0:
                result["photo"] = [{"file_id": "fixture-photo"}]
            if i == 3:
                result["reply_markup"] = expected["reply_markup"]
            observed.append(ObservedSend(expected["method"], 200,
                json.dumps({"ok": True, "result": result}, ensure_ascii=False).encode(),
                datetime.fromtimestamp(NOW + i, timezone.utc)))
        args = {"review": review(client), "snapshot": s, "card_id": C,
                "signer": self.signer, "bindings": self.bindings,
                "room_binding": self.room_binding, "bot_id": BOT, "chat_id": ROOM,
                "thread_id": None, "now": NOW, "banner_sha256": s.banner_sha256,
                "observations": observed}
        args.update(changes)
        return args

    def test_complete_exact_card_registration_for_all_clients(self):
        for client in ("yellow", "babylon", "squid", "origintrail"):
            with self.subTest(client=client):
                args = self.evidence(client)
                result = card_registration_evidence(**args)
                self.assertEqual(result["target_review_id"], R)
                self.assertEqual(result["target_card_id"], C)
                self.assertEqual([p["kind"] for p in result["target_parts"]],
                                 ["image", "telegram", "x"])
                self.assertTrue(all(p["outcome"] == "sent" for p in result["target_parts"]))
                card = {"id": C, "review_id": R, "epoch": 0,
                        "version_fingerprint": "b" * 64,
                        "bindings": result["target_bindings"], "parts": result["target_parts"],
                        "delivered_at": result["delivered"], "expires_at": result["expires"]}
                self.assertEqual(card_parent_binding(self.bindings, args["review"], card),
                                 result["target_bindings"]["parent_binding"])
                self.assertNotIn("approve_and_publish", str(prepare_private_card(
                    args["snapshot"], self.signer, self.room_binding, now=NOW)))

    def test_incomplete_and_unknown_delivery_have_no_card(self):
        args = self.evidence()
        for observations in (args["observations"][:3],
                             [*args["observations"][:2],
                              replace(args["observations"][2], http_status=504),
                              args["observations"][3]]):
            with self.subTest(count=len(observations)):
                with self.assertRaises(CardReceiptError):
                    card_registration_evidence(**{**args, "observations": observations})

    def test_wrong_bot_room_copy_or_markup_rejected(self):
        args = self.evidence()
        for index, field, value in ((0, "from", {"id": BOT + 1, "is_bot": True}),
                                    (1, "chat", {"id": ROOM + 1, "type": "supergroup"}),
                                    (2, "text", "changed copy"),
                                    (3, "reply_markup", {"inline_keyboard": []})):
            with self.subTest(index=index, field=field):
                observations = list(args["observations"])
                body = json.loads(observations[index].raw_response)
                body["result"][field] = value
                observations[index] = replace(observations[index],
                    raw_response=json.dumps(body, ensure_ascii=False).encode())
                with self.assertRaises(CardReceiptError):
                    card_registration_evidence(**{**args, "observations": observations})

    def test_duplicate_message_or_stale_source_rejected(self):
        args = self.evidence()
        observations = list(args["observations"])
        body = json.loads(observations[3].raw_response)
        body["result"]["message_id"] = 101
        observations[3] = replace(observations[3], raw_response=json.dumps(body).encode())
        with self.assertRaises(CardReceiptError):
            card_registration_evidence(**{**args, "observations": observations})
        stale = replace(args["snapshot"], source_published_at="2026-09-21T06:00:00Z")
        with self.assertRaises(CardReceiptError):
            card_registration_evidence(**{**args, "snapshot": stale})

    def test_wrong_banner_expired_review_and_malformed_response_rejected(self):
        args = self.evidence()
        with self.assertRaises(CardReceiptError):
            card_registration_evidence(**{**args, "banner_sha256": "c" * 64})
        expired = {**args["review"], "expires_at": datetime.fromtimestamp(
            NOW + 2, timezone.utc).isoformat()}
        with self.assertRaises(CardReceiptError):
            card_registration_evidence(**{**args, "review": expired})
        observations = list(args["observations"])
        observations[3] = replace(observations[3], raw_response=b'{"ok":true,"ok":true}')
        with self.assertRaises(CardReceiptError):
            card_registration_evidence(**{**args, "observations": observations})


if __name__ == "__main__":
    unittest.main()
