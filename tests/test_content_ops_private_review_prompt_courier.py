"""Synthetic edit-prompt handoff: no DB, Telegram, poller or public I/O."""
import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import httpx

from core.content_ops.private_review_card_receipt import ObservedSend
from core.content_ops.private_review_prompt_courier import (
    PrivateReviewPromptCourier, PromptCommand, PromptReservation,
    TelegramPrivatePromptSender,
)
from core.content_ops.prompt_attempt import prompt_instruction
from core.content_ops.prompt_receipt import PromptAttempt
from core.content_ops.review_edit_ingress import EditBindings


NOW = int(datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc).timestamp())
ATTEMPT = "11111111-1111-4111-8111-111111111111"
REVIEW = "22222222-2222-4222-8222-222222222222"
ACTOR = "33333333-3333-4333-8333-333333333333"
BOT, ROOM, HUMAN = 123456, -1001234567890, 789012
CALLBACK = "private_callback_1"
BINDINGS = EditBindings(b"b" * 32)


def command():
    return PromptCommand(CALLBACK, ATTEMPT, BOT, ROOM, HUMAN)


class Owner:
    def __init__(self):
        self.reservations = []
        self.confirmations = []
        self.reused = False
        self.wrong_actor = False
        self.expired = False
        self.confirm_unknown = False

    async def reserve_prompt(self, *, command, action_key):
        self.reservations.append((command, action_key))
        text = prompt_instruction("edit_telegram")
        attempt = PromptAttempt(command.attempt_id, REVIEW, ACTOR, 1,
            action_key, "a" * 64, "b" * 64,
            hashlib.sha256(text.encode()).hexdigest(), BOT, ROOM,
            HUMAN + int(self.wrong_actor), NOW - 1,
            NOW - 1 if self.expired else NOW + 100)
        return PromptReservation(attempt, "edit_telegram", {
            "status": "attempt_recorded", "attempt_id": ATTEMPT,
            "reused": self.reused, "execution_authorized": False})

    async def confirm_prompt(self, **values):
        self.confirmations.append(values)
        if self.confirm_unknown:
            raise TimeoutError("uncertain commit acknowledgement")
        return {"status": "prompt_registered", "prompt_id": ATTEMPT,
                "reused": False, "execution_authorized": False}


class Sender:
    def __init__(self):
        self.preflights = []
        self.sends = []
        self.timeout = False
        self.wrong_room = False
        self.date_offset = 0

    async def preflight(self, **values):
        self.preflights.append(values)

    async def send_once(self, text, **values):
        self.sends.append((text, values))
        if self.timeout:
            raise TimeoutError("uncertain provider outcome")
        result = {"message_id": 101, "date": NOW + self.date_offset,
                  "chat": {"id": ROOM - int(self.wrong_room), "type": "supergroup"},
                  "from": {"id": BOT, "is_bot": True}, "text": text}
        return ObservedSend("sendMessage", 200,
            json.dumps({"ok": True, "result": result}).encode(),
            datetime.fromtimestamp(NOW + self.date_offset, timezone.utc))


def setup():
    owner, sender = Owner(), Sender()
    courier = PrivateReviewPromptCourier(owner, sender, BINDINGS,
        clock=lambda: NOW)
    return courier, owner, sender


def test_default_off_has_zero_io():
    courier, owner, sender = setup()
    assert asyncio.run(courier.run(command())) == {
        "status": "disabled", "private_send_attempts": 0,
        "public_send_attempted": False}
    assert not owner.reservations and not sender.preflights and not sender.sends


def test_reserved_edit_prompt_is_sent_once_and_confirmed():
    courier, owner, sender = setup()
    assert asyncio.run(courier.run(command(), enabled=True)) == {
        "status": "prompt_registered", "private_send_attempts": 1,
        "public_send_attempted": False}
    assert sender.preflights == [{"bot_id": BOT, "chat_id": ROOM}]
    assert len(owner.reservations) == len(sender.sends) == len(owner.confirmations) == 1
    assert sender.sends[0][0] == prompt_instruction("edit_telegram")
    assert owner.confirmations[0]["attempt_id"] == ATTEMPT
    assert asyncio.run(courier.run(command(), enabled=True))["status"] == "replay_denied"
    assert len(sender.sends) == 1


def test_reused_or_expired_reservation_never_calls_send():
    for change in ("reused", "wrong_actor", "expired"):
        courier, owner, sender = setup()
        setattr(owner, change, True)
        assert asyncio.run(courier.run(command(), enabled=True))["status"] == "blocked"
        assert len(owner.reservations) == 1 and not sender.sends


def test_bad_command_stops_before_preflight_or_owner():
    courier, owner, sender = setup()
    bad = replace(command(), chat_id=42)
    assert asyncio.run(courier.run(bad, enabled=True))["status"] == "blocked"
    assert not sender.preflights and not owner.reservations


def test_unknown_send_or_receipt_commit_never_retries():
    for stage in ("send", "confirm"):
        courier, owner, sender = setup()
        if stage == "send":
            sender.timeout = True
        else:
            owner.confirm_unknown = True
        assert asyncio.run(courier.run(command(), enabled=True)) == {
            "status": "delivery_unknown", "private_send_attempts": 1,
            "public_send_attempted": False}
        assert asyncio.run(courier.run(command(), enabled=True))["status"] == "replay_denied"
        assert len(sender.sends) == 1


def test_wrong_room_response_never_confirms():
    courier, owner, sender = setup()
    sender.wrong_room = True
    assert asyncio.run(courier.run(command(), enabled=True))["status"] == "delivery_unknown"
    assert not owner.confirmations


def test_precise_db_reservation_waits_for_eligible_provider_second():
    owner, sender = Owner(), Sender()
    moments = iter((NOW, NOW + 1))
    sleeps = []

    async def advance(seconds):
        sleeps.append(seconds)

    original = owner.reserve_prompt

    async def reserve(*, command, action_key):
        result = await original(command=command, action_key=action_key)
        return replace(result, attempt=replace(result.attempt,
            started_at=NOW + 1))

    owner.reserve_prompt = reserve
    sender.date_offset = 1
    courier = PrivateReviewPromptCourier(owner, sender, BINDINGS,
        clock=lambda: next(moments), sleep=advance)
    assert asyncio.run(courier.run(command(), enabled=True))["status"] == "prompt_registered"
    assert sleeps == [1]
    assert len(sender.sends) == 1
    assert len(owner.confirmations) == 1


def test_real_send_only_adapter_uses_existing_bot_and_private_room():
    methods = []
    token = str(BOT) + ":" + "x" * 32  # Synthetic, never a real credential.

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        if method == "getMe":
            result = {"id": BOT, "is_bot": True,
                      "username": "coineasy_review_bot"}
        elif method == "getChat":
            result = {"id": ROOM, "type": "supergroup"}
        elif method == "getChatMember":
            result = {"status": "member", "user": {"id": BOT, "is_bot": True}}
        else:
            assert method == "sendMessage"
            body = json.loads(request.content)
            assert body == {"chat_id": ROOM,
                "text": prompt_instruction("edit_telegram"),
                "protect_content": True,
                "link_preview_options": {"is_disabled": True}}
            result = {"message_id": 101, "date": NOW,
                "chat": {"id": ROOM, "type": "supergroup"},
                "from": {"id": BOT, "is_bot": True},
                "text": body["text"]}
        return httpx.Response(200, json={"ok": True, "result": result})

    owner = Owner()
    sender = TelegramPrivatePromptSender(bot_token=token, bot_id=BOT,
        chat_id=ROOM, transport=httpx.MockTransport(handler),
        clock=lambda: datetime.fromtimestamp(NOW, timezone.utc))
    courier = PrivateReviewPromptCourier(owner, sender, BINDINGS,
        clock=lambda: NOW)
    assert asyncio.run(courier.run(command(), enabled=True))["status"] == "prompt_registered"
    assert methods == ["getMe", "getChat", "getChatMember", "sendMessage"]
    assert len(owner.confirmations) == 1


def test_prompt_sender_does_not_repeat_uncertain_provider_call():
    methods = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        if method == "getMe":
            result = {"id": BOT, "is_bot": True,
                      "username": "coineasy_review_bot"}
        elif method == "getChat":
            result = {"id": ROOM, "type": "supergroup"}
        elif method == "getChatMember":
            result = {"status": "member", "user": {"id": BOT, "is_bot": True}}
        else:
            return httpx.Response(500, json={"ok": False, "description": "unknown"})
        return httpx.Response(200, json={"ok": True, "result": result})

    owner = Owner()
    sender = TelegramPrivatePromptSender(bot_token=str(BOT) + ":" + "x" * 32,
        bot_id=BOT, chat_id=ROOM, transport=httpx.MockTransport(handler),
        clock=lambda: datetime.fromtimestamp(NOW, timezone.utc))
    courier = PrivateReviewPromptCourier(owner, sender, BINDINGS,
        clock=lambda: NOW)
    assert asyncio.run(courier.run(command(), enabled=True))["status"] == "delivery_unknown"
    assert asyncio.run(courier.run(command(), enabled=True))["status"] == "replay_denied"
    assert methods.count("sendMessage") == 1 and not owner.confirmations
