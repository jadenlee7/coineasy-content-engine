"""Synthetic one-shot orchestration: zero database/provider/network I/O."""
import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

from core.content_ops.private_review_card_canary import CanonicalPng, PrivateCardCanary
from core.content_ops.private_review_card_courier import PrivateCardCourier
from core.content_ops.private_review_card_receipt import ObservedSend
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.worker import ReviewClaim


NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
EPOCH = int(NOW.timestamp())
W = "11111111-1111-4111-8111-111111111111"
I = "22222222-2222-4222-8222-222222222222"
V = "33333333-3333-4333-8333-333333333333"
R = "44444444-4444-4444-8444-444444444444"
C = "55555555-5555-4555-8555-555555555555"
O = "66666666-6666-4666-8666-666666666666"
T = "77777777-7777-4777-8777-777777777777"
BOT, ROOM = 123456, -1001234567890
PNG = b"\x89PNG\r\n\x1a\nsynthetic-image"
SHA = hashlib.sha256(PNG).hexdigest()


def claimed(token):
    return ReviewClaim.parse({"outbox_id": O, "claim_token": token,
        "client_id": "yellow", "kst_date": "2026-09-23",
        "content_item_id": I, "content_version_id": V,
        "source_item_id": "88888888-8888-4888-8888-888888888888",
        "generate_job_id": "99999999-9999-4999-8999-999999999999",
        "banner_sha256": SHA, "title": "합성 검수용 제목",
        "telegram_copy": "합성 Telegram 전문", "x_copy": "합성 X 전문",
        "source_url": "https://x.com/yellow/status/123456789",
        "source_published_at": "2026-09-23T08:00:00Z"}, NOW)


class Gateway:
    def __init__(self, events, *, fail_begin=False, empty=False, wrong_version=False):
        self.events, self.fail_begin, self.empty = events, fail_begin, empty
        self.wrong_version = wrong_version
        self.packet_sha = None

    async def reconcile(self):
        self.events.append("reconcile")
        return 0 if self.empty else 1

    async def claim(self, token):
        self.events.append("claim")
        value = claimed(token)
        return replace(value, content_version_id=R) if self.wrong_version else value

    async def begin(self, claim, packet_sha256):
        self.events.append("begin")
        self.packet_sha = packet_sha256
        if self.fail_begin:
            raise TimeoutError("uncertain database ACK")
        return {"status": "begun", "outbox_id": claim.outbox_id,
                "execution_authorized": False}


class Reader:
    def __init__(self, events, *, bad=False):
        self.events, self.bad = events, bad

    async def load_png(self, claim):
        self.events.append("png")
        return CanonicalPng(claim.content_item_id, claim.content_version_id,
                            SHA, PNG + b"tampered" if self.bad else PNG)


class Owner:
    def __init__(self, events, *, fail_prepare=False):
        self.events, self.fail_prepare = events, fail_prepare
        self.packet_sha = None

    async def prepare_review(self, **args):
        self.events.append("prepare")
        assert args == {"workspace_id": W, "outbox_id": O,
                        "claim_token": T, "content_version_id": V,
                        "review_id": R}
        if self.fail_prepare:
            raise TimeoutError("uncertain prepare ACK")
        return {"status": "review_prepared", "review_id": R,
                "version_fingerprint": "a" * 64, "epoch": 0,
                "state": "active", "expires_at": "2026-09-23T09:30:00+00:00",
                "execution_authorized": False}

    async def bind_outbox(self, **args):
        self.events.append("bind")
        self.packet_sha = args["packet_sha256"]
        return {"status": "bound", "execution_authorized": False}

    async def reserve_part(self, **args):
        self.events.append("reserve:" + str(args["part_index"]))
        return {"status": "reserved", "new_attempt": True,
                "execution_authorized": False}

    async def confirm_part(self, **args):
        self.events.append("confirm:" + str(args["part_index"]))
        return {"status": "confirmed", "new_confirmation": True,
                "execution_authorized": False}

    async def register_card(self, evidence):
        self.events.append("register")
        return {"status": "card_recorded", "card_id": evidence["target_card_id"],
                "reused": False, "execution_authorized": False}

    async def read_terminal(self, **args):
        self.events.append("terminal")
        return {"status": "sent", "card_id": args["card_id"],
                "outbox_id": args["outbox_id"], "execution_authorized": False}


class Sender:
    def __init__(self, events):
        self.events = events
        self.sent = 0

    async def preflight(self, **args):
        self.events.append("preflight")
        assert args == {"bot_id": BOT, "chat_id": ROOM}

    async def send_once(self, request, *, png):
        index = self.sent
        self.sent += 1
        self.events.append("send:" + str(index))
        assert (png is not None) == (index == 0)
        result = {"message_id": 100 + index, "date": EPOCH + index,
                  "chat": {"id": ROOM, "type": "supergroup"},
                  "from": {"id": BOT, "is_bot": True}}
        result["caption" if index == 0 else "text"] = request["text"]
        if index == 0:
            result["photo"] = [{"file_id": "synthetic-file"}]
        if index == 3:
            result["reply_markup"] = request["reply_markup"]
        return ObservedSend(request["method"], 200,
            json.dumps({"ok": True, "result": result}, ensure_ascii=False).encode(),
            datetime.fromtimestamp(EPOCH + index, timezone.utc))


def setup(*, fail_begin=False, bad_image=False, fail_prepare=False,
          empty=False, wrong_version=False):
    events = []
    gateway = Gateway(events, fail_begin=fail_begin, empty=empty,
                      wrong_version=wrong_version)
    owner = Owner(events, fail_prepare=fail_prepare)
    reader = Reader(events, bad=bad_image)
    sender = Sender(events)
    signer, bindings = ButtonSigner(b"s" * 32), EditBindings(b"e" * 32)
    courier = PrivateCardCourier(owner, sender, signer, bindings, clock=lambda: EPOCH)
    ids = iter((T, R, C))
    runner = PrivateCardCanary(workspace_id=W, content_version_id=V,
        bot_id=BOT, chat_id=ROOM,
        gateway=gateway, owner=owner, png_reader=reader, courier=courier,
        signer=signer, bindings=bindings, clock=lambda: NOW,
        uuid_factory=lambda: next(ids))
    return runner, gateway, owner, sender, events


def test_default_off_and_empty_queue_have_no_send():
    runner, _, _, sender, events = setup()
    assert asyncio.run(runner.run()) == {"status": "disabled",
                                           "public_send_attempted": False}
    assert events == [] and sender.sent == 0
    empty, _, _, sender, events = setup(empty=True)
    assert asyncio.run(empty.run(enabled=True))["status"] == "no_candidate"
    assert events == ["reconcile"] and sender.sent == 0


def test_one_claimed_outbox_precedes_four_durable_private_parts():
    runner, gateway, owner, sender, events = setup()
    result = asyncio.run(runner.run(enabled=True))
    assert result == {"status": "card_recorded", "confirmed_parts": 4,
                      "public_send_attempted": False}
    assert gateway.packet_sha == owner.packet_sha
    assert sender.sent == 4
    assert events == ["reconcile", "claim", "png", "prepare", "begin",
        "preflight", "bind", "reserve:0", "send:0", "confirm:0",
        "reserve:1", "send:1", "confirm:1",
        "reserve:2", "send:2", "confirm:2",
        "reserve:3", "send:3", "confirm:3", "register", "terminal"]
    assert asyncio.run(runner.run(enabled=True))["status"] == "replay_denied"
    assert sender.sent == 4


def test_image_or_owner_failure_stops_before_begin_and_send():
    for args, expected in [({"wrong_version": True}, ["reconcile", "claim"]),
                           ({"bad_image": True}, ["reconcile", "claim", "png"]),
                           ({"fail_prepare": True}, ["reconcile", "claim", "png", "prepare"])]:
        runner, _, _, sender, events = setup(**args)
        assert asyncio.run(runner.run(enabled=True))["status"] == "blocked"
        assert events == expected and sender.sent == 0


def test_lost_begin_ack_never_starts_courier_or_retries():
    runner, _, _, sender, events = setup(fail_begin=True)
    assert asyncio.run(runner.run(enabled=True)) == {
        "status": "outbox_unknown", "public_send_attempted": False}
    assert events == ["reconcile", "claim", "png", "prepare", "begin"]
    assert sender.sent == 0
    assert asyncio.run(runner.run(enabled=True))["status"] == "replay_denied"
