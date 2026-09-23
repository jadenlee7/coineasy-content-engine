"""Signed private callback through one-shot prompt receipt, all offline."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone

from core.content_ops.polling_review_adapter import PollingReviewAdapter
from core.content_ops.private_review_card_receipt import ObservedSend
from core.content_ops.private_review_prompt_courier import (
    PrivateReviewPromptCourier, PromptReservation,
)
from core.content_ops.prompt_attempt import prompt_instruction
from core.content_ops.prompt_receipt import PromptAttempt
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import IngressPolicy
import test_content_ops_review_ingress as ingress_fixture

NOW = ingress_fixture.NOW


ROOM = -1001234567890
BOT, HUMAN = 101, 201
REVIEW = "22222222-2222-4222-8222-222222222222"
ACTOR = "33333333-3333-4333-8333-333333333333"
BINDINGS = EditBindings(b"b" * 32)


class PromptOwner:
    def __init__(self):
        self.reservations = []
        self.confirmations = []

    async def reserve_prompt(self, *, command, action_key):
        self.reservations.append((command, action_key))
        text = prompt_instruction("edit_x")
        attempt = PromptAttempt(command.attempt_id, REVIEW, ACTOR, 1,
            action_key, "a" * 64, "b" * 64,
            hashlib.sha256(text.encode()).hexdigest(), BOT, ROOM, HUMAN,
            NOW - 1, NOW + 100)
        return PromptReservation(attempt, "edit_x", {
            "status": "attempt_recorded", "attempt_id": command.attempt_id,
            "reused": False, "execution_authorized": False})

    async def confirm_prompt(self, **values):
        self.confirmations.append(values)
        return {"status": "prompt_registered", "prompt_id": values["attempt_id"],
                "reused": False, "execution_authorized": False}


class PromptSender:
    def __init__(self):
        self.preflights = []
        self.sends = []

    async def preflight(self, **values):
        self.preflights.append(values)

    async def send_once(self, text, **values):
        self.sends.append((text, values))
        result = {"message_id": 100, "date": NOW,
                  "chat": {"id": ROOM, "type": "supergroup"},
                  "from": {"id": BOT, "is_bot": True}, "text": text}
        return ObservedSend("sendMessage", 200,
            json.dumps({"ok": True, "result": result}).encode(),
            datetime.fromtimestamp(NOW, timezone.utc))


def test_signed_edit_callback_reaches_exact_one_shot_prompt_receipt():
    case = ingress_fixture.ReviewIngressTest(); case.setUp()
    case.policy = IngressPolicy(case.policy.webhook_secret, BOT, ROOM,
        case.policy.room_binding, ((HUMAN, "reviewer-one"),))

    def registered(**values):
        assert values == dict(bot_id=BOT, chat_id=ROOM, message_id=31,
                              room_binding=case.policy.room_binding)
        return "fixture-message"

    case.owner.resolve_review_message = registered
    instances = []

    def fresh_courier():
        owner, sender = PromptOwner(), PromptSender()
        instances.append((owner, sender))
        return PrivateReviewPromptCourier(owner, sender, BINDINGS,
                                          clock=lambda: NOW)

    adapter = PollingReviewAdapter(enabled=True, policy=case.policy,
        signer=case.signer, review_owner=case.owner,
        prompt_courier_factory=fresh_courier)
    update = case.update("x", actor=HUMAN, private=True)
    assert asyncio.run(adapter.handle_callback(update, now=NOW)) == {
        "status": "prompt_registered", "execution_authorized": False}
    owner, sender = instances[0]
    assert len(owner.reservations) == len(owner.confirmations) == 1
    assert len(sender.preflights) == len(sender.sends) == 1
    command, key = owner.reservations[0]
    assert key == hashlib.sha256(update["callback_query"]["id"].encode()).hexdigest()
    assert (command.bot_id, command.chat_id, command.human_id) == (BOT, ROOM, HUMAN)
    assert sender.sends[0][0] == prompt_instruction("edit_x")
    assert not case.owner.outbox

    # A redelivered Telegram update has an existing action receipt. The
    # bridge must not create a second owner, attempt or provider send.
    assert asyncio.run(adapter.handle_callback(update, now=NOW)) == {
        "status": "prompt_status_unknown", "execution_authorized": False}
    assert len(instances) == 1 and len(sender.sends) == 1
