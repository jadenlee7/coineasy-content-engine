"""Fake-transaction prompt owner checks; no database or provider I/O."""
import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import httpx

from core.content_ops.private_review_prompt_courier import (
    PrivateReviewPromptCourier, PromptCommand, TelegramPrivatePromptSender,
)
from core.content_ops.private_review_prompt_owner import (
    PostgresPrivatePromptOwner, PromptOwnerError,
)
from core.content_ops.prompt_attempt import prompt_instruction
from core.content_ops.prompt_reservation import card_parent_binding
from core.content_ops.review_edit_ingress import EditBindings


def uid(n):
    return f"10000000-0000-4000-8000-{n:012d}"


BOT, ROOM, HUMAN = 123456, -1001234567890, 789012
CARD, REVIEW, ACTOR, ATTEMPT = (uid(n) for n in range(1, 5))
WORKSPACE, ITEM, VERSION = (uid(n) for n in range(5, 8))
CALLBACK = "synthetic_callback_1"
ACTION_KEY = hashlib.sha256(CALLBACK.encode()).hexdigest()
BINDINGS = EditBindings(b"synthetic-private-prompt-owner-key" * 2)
NOW = datetime(2026, 9, 23, 10, 0, 0, 500000, tzinfo=timezone.utc)


def command():
    return PromptCommand(CALLBACK, ATTEMPT, BOT, ROOM, HUMAN)


def fixture():
    review = dict(id=REVIEW, workspace_id=WORKSPACE, client_id="yellow",
        content_item_id=ITEM, content_version_id=VERSION,
        version_fingerprint="f" * 64, epoch=1, state="edit_requested",
        expires_at=(NOW + timedelta(minutes=20)).isoformat())
    card = dict(id=CARD, review_id=REVIEW, epoch=0,
        version_fingerprint="f" * 64, active=True,
        delivered_at=(NOW - timedelta(minutes=2)).isoformat(),
        expires_at=(NOW + timedelta(minutes=10)).isoformat(),
        bindings=dict(bot=BINDINGS.digest("bot", BOT),
            room=BINDINGS.digest("room", BOT, ROOM),
            message=BINDINGS.digest("card-message@2", BOT, ROOM, 11),
            packet_receipt="a" * 64, card_receipt="b" * 64,
            parent_binding="0" * 64, thread_id=None),
        parts=[dict(kind=kind,
            message_binding=BINDINGS.digest("card-message@2", BOT, ROOM, n),
            payload_sha256="d" * 64, outcome="sent")
            for kind, n in zip(("image", "telegram", "x"), (12, 13, 14))])
    card["bindings"]["parent_binding"] = card_parent_binding(BINDINGS, review, card)
    attempt = dict(id=ATTEMPT, card_id=CARD, review_id=REVIEW, actor_id=ACTOR,
        epoch=1, edit_action_key=ACTION_KEY, version_fingerprint="f" * 64,
        bot_binding=card["bindings"]["bot"],
        room_binding=card["bindings"]["room"],
        human_binding=BINDINGS.digest("human", BOT, HUMAN),
        thread_id=None,
        parent_binding_sha256=card["bindings"]["parent_binding"],
        expected_text_sha256=hashlib.sha256(
            prompt_instruction("edit_telegram").encode()).hexdigest(),
        started_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(minutes=10)).isoformat())
    return attempt, review, card


class Connection:
    autocommit = False

    def __init__(self, *, mutate=None, commit_error=False):
        self.attempt, self.review, self.card = deepcopy(fixture())
        if mutate:
            mutate(self)
        self.commit_error = commit_error
        self.statements = []
        self.exit_kind = None

    def __enter__(self):
        return self

    def __exit__(self, kind, _value, _tb):
        self.exit_kind = kind
        if kind is None and self.commit_error:
            raise RuntimeError("synthetic commit detail")

    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, sql, params):
                connection.statements.append((sql, params))

            def fetchall(self):
                if len(connection.statements) == 1:
                    return [(ACTOR, REVIEW, CARD, "edit_telegram")]
                if len(connection.statements) == 3:
                    return [(connection.attempt, connection.review, connection.card)]
                raise AssertionError("unexpected fetchall")

            def fetchone(self):
                if len(connection.statements) == 2:
                    return ({"status": "attempt_recorded", "attempt_id": ATTEMPT,
                             "reused": False, "execution_authorized": False},
                            NOW + timedelta(microseconds=1))
                raise AssertionError("unexpected fetchone")

        return Cursor()


def reserve(connection):
    calls = []

    def factory():
        calls.append(1)
        return connection

    owner = PostgresPrivatePromptOwner(factory, BINDINGS)
    return owner, calls


def test_exact_committed_reservation_projects_one_attempt():
    connection = Connection()
    owner, calls = reserve(connection)
    result = asyncio.run(owner.reserve_prompt(command=command(), action_key=ACTION_KEY))
    assert result.action == "edit_telegram"
    assert result.attempt.attempt_id == ATTEMPT
    assert result.attempt.started_at == int(NOW.timestamp()) + 1
    assert result.receipt["reused"] is False
    assert calls == [1] and connection.exit_kind is None
    assert len(connection.statements) == 3
    assert "reserve_content_ops_button_prompt_for_runtime" in connection.statements[1][0]
    with pytest.raises(PromptOwnerError, match="replay_denied"):
        asyncio.run(owner.reserve_prompt(command=command(), action_key=ACTION_KEY))
    assert calls == [1]


@pytest.mark.parametrize("change", [
    lambda c: c.attempt.update(actor_id=uid(99)),
    lambda c: c.attempt.update(parent_binding_sha256="0" * 64),
    lambda c: c.attempt.update(expected_text_sha256="0" * 64),
    lambda c: c.card["bindings"].update(room="0" * 64),
    lambda c: c.review.update(state="held"),
])
def test_changed_owner_projection_rolls_back_without_send(change):
    connection = Connection(mutate=change)
    owner, _ = reserve(connection)
    with pytest.raises(PromptOwnerError, match="^private_prompt_owner_unknown$"):
        asyncio.run(owner.reserve_prompt(command=command(), action_key=ACTION_KEY))
    assert connection.exit_kind is not None


def test_uncertain_commit_never_retries_reservation():
    connection = Connection(commit_error=True)
    owner, calls = reserve(connection)
    with pytest.raises(PromptOwnerError, match="^private_prompt_owner_unknown$"):
        asyncio.run(owner.reserve_prompt(command=command(), action_key=ACTION_KEY))
    with pytest.raises(PromptOwnerError, match="replay_denied"):
        asyncio.run(owner.reserve_prompt(command=command(), action_key=ACTION_KEY))
    assert calls == [1]


def test_callback_key_mismatch_stops_before_connection():
    owner, calls = reserve(Connection())
    with pytest.raises(PromptOwnerError, match="^private_prompt_owner_unknown$"):
        asyncio.run(owner.reserve_prompt(command=command(), action_key="a" * 64))
    assert calls == []


def test_confirmation_requires_same_committed_attempt_and_is_once_only():
    owner, _ = reserve(Connection())
    calls = []

    class Receipt:
        def record_prompt_response(self, **kwargs):
            calls.append(kwargs)
            return {"status": "prompt_registered", "prompt_id": ATTEMPT,
                    "reused": False, "execution_authorized": False}

    owner._receipt = Receipt()
    kwargs = dict(attempt_id=ATTEMPT, bot_id=BOT, chat_id=ROOM, human_id=HUMAN,
        http_status=200, raw_response=b'{}', observed_at=NOW)
    with pytest.raises(PromptOwnerError, match="replay_denied"):
        asyncio.run(owner.confirm_prompt(**kwargs))
    assert calls == []
    asyncio.run(owner.reserve_prompt(command=command(), action_key=ACTION_KEY))
    with pytest.raises(PromptOwnerError, match="replay_denied"):
        asyncio.run(owner.confirm_prompt(**{**kwargs, "human_id": HUMAN + 1}))
    assert calls == []
    result = asyncio.run(owner.confirm_prompt(**kwargs))
    assert result["status"] == "prompt_registered"
    assert calls[0]["enabled"] is True and calls[0]["attempt_id"] == ATTEMPT
    with pytest.raises(PromptOwnerError, match="replay_denied"):
        asyncio.run(owner.confirm_prompt(**kwargs))
    assert len(calls) == 1


def test_fake_db_owner_and_mock_telegram_complete_one_private_prompt():
    owner, _ = reserve(Connection())
    owner._receipt = type("Receipt", (), {"record_prompt_response": lambda self, **_:
        {"status": "prompt_registered", "prompt_id": ATTEMPT,
         "reused": False, "execution_authorized": False}})()
    methods = []
    provider_second = int(NOW.timestamp()) + 1

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        if method == "getMe":
            result = {"id": BOT, "is_bot": True, "username": "coineasy_review_bot"}
        elif method == "getChat":
            result = {"id": ROOM, "type": "supergroup"}
        elif method == "getChatMember":
            result = {"status": "member", "user": {"id": BOT, "is_bot": True}}
        else:
            assert method == "sendMessage"
            body = json.loads(request.content)
            assert body["chat_id"] == ROOM
            assert body["text"] == prompt_instruction("edit_telegram")
            result = {"message_id": 101, "date": provider_second,
                "chat": {"id": ROOM, "type": "supergroup"},
                "from": {"id": BOT, "is_bot": True}, "text": body["text"]}
        return httpx.Response(200, json={"ok": True, "result": result})

    sender = TelegramPrivatePromptSender(bot_token=str(BOT) + ":" + "x" * 32,
        bot_id=BOT, chat_id=ROOM, transport=httpx.MockTransport(handler),
        clock=lambda: datetime.fromtimestamp(provider_second, timezone.utc))
    seconds = iter((int(NOW.timestamp()), provider_second))

    async def advance(_duration):
        return None

    courier = PrivateReviewPromptCourier(owner, sender, BINDINGS,
        clock=lambda: next(seconds), sleep=advance)
    assert asyncio.run(courier.run(command(), enabled=True)) == {
        "status": "prompt_registered", "private_send_attempts": 1,
        "public_send_attempted": False}
    assert methods == ["getMe", "getChat", "getChatMember", "sendMessage"]
