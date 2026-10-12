"""Offline composition checks; no DB or Telegram calls."""
import asyncio
from dataclasses import FrozenInstanceError, replace
import json

import httpx
import pytest

from core.content_ops.private_review_bot_policy import ExistingReviewBotPolicy
from core.content_ops.private_review_card_sender import PrivateCardSenderError
from core.content_ops.private_review_prompt_courier import PrivateReviewPromptCourier
from core.content_ops.private_review_prompt_runtime import compose_private_prompt_factory
from core.content_ops.prompt_attempt import prompt_instruction
from core.content_ops.review_edit_ingress import EditBindings


BOT, ROOM = 123456, -1001234567890
TOKEN = str(BOT) + ":" + "x" * 32
BINDINGS = EditBindings(b"b" * 32)


def no_connection():
    raise AssertionError("DB connection during composition")


@pytest.mark.parametrize("enabled", [False, None, "true", 1])
def test_default_off_never_inspects_dependencies(enabled):
    assert compose_private_prompt_factory(enabled=enabled,
        connection_factory=object(), bindings=object(), bot_token=object(),
        bot_id=object(), chat_id=object(), bot_policy=object()) is None


def test_on_builds_fresh_one_shot_couriers_without_io():
    factory = compose_private_prompt_factory(enabled=True,
        connection_factory=no_connection, bindings=BINDINGS,
        bot_token=TOKEN, bot_id=BOT, chat_id=ROOM)
    first, second = factory(), factory()
    assert type(first) is type(second) is PrivateReviewPromptCourier
    assert first is not second and first._owner is not second._owner
    assert first._sender is not second._sender
    assert first._sender._bot_id == second._sender._bot_id == BOT
    assert first._sender._chat_id == second._sender._chat_id == ROOM
    assert first._sender._membership_status == second._sender._membership_status == "member"


@pytest.mark.parametrize("change", ["connection_factory", "bindings", "bot_token",
                                      "bot_id", "chat_id", "bot_policy"])
def test_on_rejects_invalid_configuration_before_any_io(change):
    values = dict(connection_factory=no_connection, bindings=BINDINGS,
                  bot_token=TOKEN, bot_id=BOT, chat_id=ROOM)
    values[change] = object()
    with pytest.raises(ValueError, match="^private_prompt_runtime_invalid$"):
        compose_private_prompt_factory(enabled=True, **values)


def policy(status="administrator"):
    return ExistingReviewBotPolicy(BOT, ROOM, "fixture-private-room", status)


def factory(bot_policy):
    return compose_private_prompt_factory(enabled=True,
        connection_factory=no_connection, bindings=BINDINGS,
        bot_token=TOKEN, bot_id=BOT, chat_id=ROOM, bot_policy=bot_policy)


def transport(methods, *, status="administrator", public=False, linked=False,
              bot_id=BOT, chat_id=ROOM, username="coineasy_review_bot"):
    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        if method == "getMe":
            result = {"id": bot_id, "is_bot": True, "username": username}
        elif method == "getChat":
            result = {"id": chat_id, "type": "supergroup"}
            if public:
                result["username"] = "fixture_public_room"
            if linked:
                result["linked_chat_id"] = -1009876543210  # Synthetic only.
        elif method == "getChatMember":
            result = {"status": status, "user": {"id": bot_id, "is_bot": True}}
        else:
            assert method == "sendMessage"
            body = json.loads(request.content)
            assert body == {"chat_id": ROOM, "text": prompt_instruction("edit_x"),
                "protect_content": True, "link_preview_options": {"is_disabled": True}}
            result = {"message_id": 1, "text": body["text"]}
        return httpx.Response(200, json={"ok": True, "result": result})
    return httpx.MockTransport(handler)


def test_exact_admin_policy_is_retained_by_each_fresh_one_shot_sender():
    projection = policy()
    fresh = factory(projection)
    with pytest.raises(FrozenInstanceError):
        projection.membership_status = "member"
    first, second = fresh(), fresh()
    assert first is not second and first._sender is not second._sender
    for courier in (first, second):
        sender, methods = courier._sender, []
        assert sender._membership_status == "administrator"
        sender._transport = transport(methods)

        async def send():
            await sender.preflight(bot_id=BOT, chat_id=ROOM)
            assert (await sender.send_once(prompt_instruction("edit_x"), bot_id=BOT, chat_id=ROOM)).http_status == 200
            with pytest.raises(PrivateCardSenderError):
                await sender.send_once(prompt_instruction("edit_x"), bot_id=BOT, chat_id=ROOM)

        asyncio.run(send())
        assert methods == ["getMe", "getChat", "getChatMember", "sendMessage"]


@pytest.mark.parametrize("bad", [object(), {"membership_status": "administrator"},
    replace(policy(), bot_id=BOT + 1), replace(policy(), chat_id=ROOM - 1)])
def test_unknown_or_different_destination_policy_is_rejected_before_io(bad):
    with pytest.raises(ValueError, match="^private_prompt_runtime_invalid$"):
        factory(bad)


@pytest.mark.parametrize("projection,observed", [
    (policy(), {"status": "member"}),
    (policy("member"), {"status": "administrator"}),
    (None, {"status": "administrator"}),
    (policy(), {"public": True}),
    (policy(), {"linked": True}),
    (policy(), {"bot_id": BOT + 1}),
    (policy(), {"chat_id": ROOM - 1}),
    (policy(), {"username": "other_bot"}),
])
def test_policy_drift_public_linked_or_wrong_identity_never_falls_back_or_sends(projection, observed):
    sender, methods = factory(projection)()._sender, []
    sender._transport = transport(methods, **observed)

    async def reject():
        with pytest.raises(PrivateCardSenderError):
            await sender.preflight(bot_id=BOT, chat_id=ROOM)
        with pytest.raises(PrivateCardSenderError):
            await sender.send_once(prompt_instruction("edit_x"), bot_id=BOT, chat_id=ROOM)

    asyncio.run(reject())
    assert "sendMessage" not in methods


def test_explicit_member_projection_still_requires_exact_member():
    sender, methods = factory(policy("member"))()._sender, []
    sender._transport = transport(methods, status="member")
    asyncio.run(sender.preflight(bot_id=BOT, chat_id=ROOM))
    assert sender._verified
    assert methods == ["getMe", "getChat", "getChatMember"]
