"""Offline synthetic identities only; no HTTP, DB or Telegram connection."""
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from core.content_ops.review_ingress import IngressPolicy, ReviewIngressError
from core.content_ops.review_edit_ingress import (
    EditBindings, PostgresEditReplyOwner, handle_edit_reply_webhook,
)

NOW = 1_800_000_000
ACTOR = "12345678-1234-4234-8234-123456789abc"
POLICY = IngressPolicy("fixture_webhook_" + "z" * 32, 101, -(10**12 + 7),
                       "fixture-room", ((201, ACTOR),))
BINDINGS = EditBindings(b"fixture-binding-key-not-a-secret-123456")


def update():
    chat = {"id": POLICY.chat_id, "type": "supergroup"}
    return {"update_id": 7, "message": {
        "message_id": 32, "date": NOW - 1, "chat": chat,
        "from": {"id": 201, "is_bot": False}, "text": "수정한 한국어 공지",
        "reply_to_message": {"message_id": 31, "date": NOW - 5, "chat": dict(chat),
                             "from": {"id": 101, "is_bot": True}}}}


def receipt():
    return dict(status="revision_saved", content_version_id=str(uuid4()), reused=False,
                rereview_required=True, execution_authorized=False)


class Owner:
    def __init__(self):
        self.events = []
        self.result = receipt()

    def save_edit_reply(self, event):
        self.events.append(event)
        return self.result


def call(owner, data=None, **overrides):
    args = dict(enabled=True, raw_body=json.dumps(data or update()).encode(),
                headers=[("Content-Type", "application/json"),
                         ("X-Telegram-Bot-Api-Secret-Token", POLICY.webhook_secret)],
                policy=POLICY, bindings=BINDINGS, owner=owner, now=NOW)
    args.update(overrides)
    return handle_edit_reply_webhook(**args)


@pytest.mark.parametrize("enabled", [False, None, "true", 1])
def test_disabled_never_touches_any_dependency(enabled):
    assert handle_edit_reply_webhook(enabled=enabled, raw_body=object(), policy=object(),
                                     bindings=object(), owner=object()) == {
        "status": "disabled", "public_send_attempted": False}


def test_matching_text_reply_passes_only_keyed_bindings_and_plain_copy():
    owner = Owner()
    assert call(owner) == owner.result
    event = owner.events[0]
    assert event.actor_id == ACTOR and event.replacement_text == update()["message"]["text"]
    assert event.bot_binding == BINDINGS.digest("bot", POLICY.bot_id)
    assert event.message_binding == BINDINGS.digest("prompt", POLICY.bot_id, POLICY.chat_id, 31)
    assert event.human_binding == BINDINGS.digest("human", POLICY.bot_id, 201)
    assert "공지" not in repr(event) and str(POLICY.chat_id) not in repr(event)
    assert BINDINGS.digest("human", 101) != BINDINGS.digest("bot", 101)
    assert BINDINGS.digest("bot", 101) != EditBindings(b"other-fixture-key" * 3).digest("bot", 101)


def test_replay_key_pins_message_not_update_or_replacement_body():
    owner = Owner(); u = update()
    call(owner, u)
    u["update_id"] += 1; u["message"]["text"] = "충돌을 DB에서 거부할 문안"
    call(owner, u)
    assert owner.events[0].operation_key == owner.events[1].operation_key
    u["message"]["message_id"] += 1
    call(owner, u)
    assert owner.events[1].operation_key != owner.events[2].operation_key


@pytest.mark.parametrize("path,value", [
    (("from", "id"), 999), (("from", "id"), True), (("from", "is_bot"), True),
    (("chat", "id"), -9), (("chat", "type"), "channel"), (("chat", "username"), "public"),
    (("reply_to_message", "chat", "id"), -9),
    (("reply_to_message", "from", "id"), 102),
    (("reply_to_message", "from", "is_bot"), False),
    (("reply_to_message", "message_id"), 32),
    (("reply_to_message", "date"), NOW),
    (("reply_to_message", "date"), NOW - 1801),
    (("reply_to_message",), None), (("message_id",), True),
    (("date",), NOW + 1), (("date",), NOW - 1801),
    (("message_thread_id",), 8), (("text",), ""), (("text",), "   "),
    (("text",), "x" * 3701), (("text",), "😀" * 1851),
    (("text",), "\ud800"), (("text",), "hidden\x01control"),
    (("entities",), [{"type": "text_link", "offset": 0, "length": 1, "url": "hidden"}]),
])
def test_invalid_message_has_zero_owner_io(path, value):
    owner = Owner(); u = update(); part = u["message"]
    for key in path[:-1]:
        part = part[key]
    part[path[-1]] = value
    with pytest.raises(ReviewIngressError):
        call(owner, u)
    assert owner.events == []


@pytest.mark.parametrize("part", ["reply", "prompt"])
@pytest.mark.parametrize("field", ["forward_origin", "sender_chat", "via_bot", "edit_date",
                                    "business_connection_id", "photo", "external_reply", "quote"])
def test_forwarded_edited_or_media_input_never_saves(part, field):
    owner = Owner(); u = update(); msg = u["message"]
    if part == "prompt": msg = msg["reply_to_message"]
    msg[field] = {}
    with pytest.raises(ReviewIngressError): call(owner, u)
    assert owner.events == []


@pytest.mark.parametrize("change", ["edited", "mixed", "wrong_secret", "duplicate_json", "duplicate_header"])
def test_authentication_and_raw_update_boundaries(change):
    owner = Owner(); u = update(); kw = {}
    if change == "edited": u["edited_message"] = u.pop("message")
    if change == "mixed": u["callback_query"] = {}
    if change == "wrong_secret": kw = dict(raw_body=b"invalid", headers=[])
    if change == "duplicate_json": kw = dict(raw_body=b'{"update_id":1,"update_id":2}')
    if change == "duplicate_header": kw = dict(headers=[
        ("X-Telegram-Bot-Api-Secret-Token", POLICY.webhook_secret),
        ("x-telegram-bot-api-secret-token", POLICY.webhook_secret)])
    with pytest.raises(ReviewIngressError): call(owner, u, **kw)
    assert owner.events == []


@pytest.mark.parametrize("field,value", [("execution_authorized", True), ("reused", 1),
    ("rereview_required", False), ("status", "published"), ("content_version_id", "not-uuid"),
    ("content_version_id", "00000000-0000-0000-0000-000000000000"), ("private_extra", "hidden")])
def test_untrusted_owner_receipt_never_claims_success(field, value):
    owner = Owner(); owner.result[field] = value
    with pytest.raises(ReviewIngressError, match="^review_edit_outcome_unknown$"):
        call(owner)
    assert len(owner.events) == 1


class Connection:
    """Psycopg-style transaction contract fake. No actual DB/network."""
    autocommit = False

    def __init__(self, result=None, commit_error=False):
        self.result = result or receipt(); self.commit_error = commit_error
        self.statements = []; self.exits = []

    def __enter__(self): return self
    def __exit__(self, kind, value, tb):
        self.exits.append(kind)
        if self.commit_error: raise RuntimeError("private-db-error")

    def cursor(self):
        connection = self
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, sql, params): connection.statements.append((sql, params))
            def fetchone(self): return (connection.result,)
        return Cursor()


def test_adapter_parameterizes_one_statement_and_waits_for_commit():
    connection = Connection(); factory_calls = []
    def factory(): factory_calls.append(1); return connection
    owner = PostgresEditReplyOwner(factory)
    assert call(owner) == connection.result
    assert factory_calls == [1] and connection.exits == [None]
    sql, params = connection.statements[0]
    assert "save_content_ops_button_edit_reply" in sql and "actor_id=%s::uuid" in sql
    assert update()["message"]["text"] not in sql and params[-2] == update()["message"]["text"]
    assert len(connection.statements) == 1


def test_commit_ack_lost_is_unknown_without_retry_or_leaked_error():
    connection = Connection(commit_error=True); owner = PostgresEditReplyOwner(lambda: connection)
    with pytest.raises(ReviewIngressError, match="^review_edit_outcome_unknown$"):
        call(owner)
    assert len(connection.statements) == 1


def test_invalid_readback_raises_inside_transaction_for_rollback():
    connection = Connection(); connection.result["execution_authorized"] = True
    with pytest.raises(ReviewIngressError): call(PostgresEditReplyOwner(lambda: connection))
    assert connection.exits == [ReviewIngressError]


def test_autocommit_connection_is_rejected_before_sql():
    connection = Connection(); connection.autocommit = True
    with pytest.raises(ReviewIngressError): call(PostgresEditReplyOwner(lambda: connection))
    assert connection.statements == []


def test_database_actor_mapping_requires_uuid_before_connect():
    connections = []
    owner = PostgresEditReplyOwner(lambda: connections.append(1))
    with pytest.raises(ReviewIngressError):
        call(owner, policy=replace(POLICY, reviewers=((201, "opaque-but-not-db-uuid"),)))
    assert connections == []


@pytest.mark.parametrize("text", ["t.me/" + "+fixture-invite",
    "api.telegram.org/" + "bot" + "123456:" + "a" * 35,
    "private marker " + str(-(10**12 + 7)), "x\x7fy"])
def test_sensitive_text_patterns_reject_without_owner_io(text):
    owner = Owner(); u = update(); u["message"]["text"] = text
    with pytest.raises(ReviewIngressError): call(owner, u)
    assert owner.events == []


def test_matching_forum_topic_works_but_boolean_topic_is_not_an_id():
    owner = Owner(); u = update()
    for msg in (u["message"], u["message"]["reply_to_message"]): msg["message_thread_id"] = 9
    call(owner, u)
    assert len(owner.events) == 1
    for msg in (u["message"], u["message"]["reply_to_message"]): msg["message_thread_id"] = True
    with pytest.raises(ReviewIngressError): call(owner, u)
    assert len(owner.events) == 1


def test_owner_exception_is_fixed_unknown_and_never_retried():
    class FailingOwner:
        calls = 0
        def save_edit_reply(self, event):
            self.calls += 1
            raise RuntimeError("sensitive-owner-detail")
    owner = FailingOwner()
    with pytest.raises(ReviewIngressError, match="^review_edit_outcome_unknown$"):
        call(owner)
    assert owner.calls == 1
