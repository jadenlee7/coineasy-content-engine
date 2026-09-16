"""Offline cancellation token, authenticated ingress and transaction tests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import base64
import json

import pytest

from core.content_ops import review_cancellation as cancellation
from core.content_ops.prompt_reservation import card_parent_binding
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import IngressPolicy


def uid(n): return f"10000000-0000-4000-8000-{n:012d}"


NOW = 1_800_000_000
BOT, ROOM, HUMAN, MESSAGE = 101, -301, 201, 10
ACTOR = uid(7)
BINDINGS = EditBindings(b"synthetic-cancellation-binding-key"*2)
POLICY = IngressPolicy("synthetic_cancellation_secret_0000001", BOT, ROOM,
    "synthetic_room", ((HUMAN, ACTOR),))
HEADERS = [("Content-Type", "application/json"),
    ("X-Telegram-Bot-Api-Secret-Token", POLICY.webhook_secret)]


def records():
    now = datetime.fromtimestamp(NOW, timezone.utc)
    review = dict(id=uid(1), workspace_id=uid(2), client_id="yellow",
        content_item_id=uid(3), content_version_id=uid(4), version_fingerprint="f"*64,
        epoch=1, state="edit_requested")
    card = dict(id=uid(5), review_id=review["id"], epoch=0, version_fingerprint="f"*64,
        active=True, delivered_at=(now-timedelta(seconds=5)).isoformat(),
        expires_at=(now+timedelta(minutes=10)).isoformat(),
        bindings=dict(bot=BINDINGS.digest("bot", BOT), room=BINDINGS.digest("room", BOT, ROOM),
            message=BINDINGS.digest("card-message@2", BOT, ROOM, MESSAGE), packet_receipt="a"*64,
            card_receipt="b"*64, parent_binding="0"*64, thread_id=None),
        parts=[dict(kind=kind, message_binding=BINDINGS.digest("card-message@2", BOT, ROOM, n),
            payload_sha256="d"*64, outcome="sent") for n, kind in enumerate(("image", "telegram", "x"), 1)])
    card["bindings"]["parent_binding"] = card_parent_binding(BINDINGS, review, card)
    return review, card


def signer(): return cancellation.CancellationSigner(b"synthetic-cancellation-signing-key"*2)


def target():
    review, card = records()
    return cancellation.cancellation_target(review, card, BINDINGS)


def token(): return signer().issue(target(), now=NOW, expires_at=NOW+300)


def update():
    return dict(update_id=99, callback_query={"id": "synthetic_callback", "data": token(),
        "from": {"id": HUMAN, "is_bot": False}, "message": {"message_id": MESSAGE,
            "date": NOW-5, "chat": {"id": ROOM, "type": "supergroup"},
            "from": {"id": BOT, "is_bot": True}}})


def revoked(reused=False):
    return dict(status="card_revoked", card_id=uid(5), reused=reused, execution_authorized=False)


def test_cancel_button_has_separate_compact_token_and_no_public_send_action():
    value = token()
    assert type(value) is str and len(value) == 51 and value.isascii()
    assert signer().verify(value, target(), now=NOW) == uid(5)
    assert cancellation.cancellation_button(target(), signer(), now=NOW, expires_at=NOW+300) == {
        "text": "⛔ 이 검수 취소", "callback_data": value}
    assert uid(5) not in value and uid(5) not in repr(target())


@pytest.mark.parametrize("key", [None, "a"*64, b"", b"a"*31, bytearray(b"a"*64)])
def test_signer_requires_separate_strict_bytes_key(key):
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): cancellation.CancellationSigner(key)


@pytest.mark.parametrize("field,value", [("card_id", uid(99)), ("review_id", uid(99)),
    ("version_fingerprint", "e"*64), ("epoch", 1), ("bot_binding", "e"*64),
    ("room_binding", "e"*64), ("message_binding", "e"*64),
    ("thread_id", 5), ("parent_binding_sha256", "e"*64)])
def test_token_binds_every_cancellation_target_field(field, value):
    changed = replace(target(), **{field: value})
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): signer().verify(token(), changed, now=NOW)


@pytest.mark.parametrize("field,value", [("card_id", "bad"),
    ("card_id", "00000000-0000-0000-0000-000000000000"),
    ("review_id", None), ("version_fingerprint", "F"*64), ("epoch", True),
    ("epoch", -1), ("epoch", 1.0), ("thread_id", True), ("thread_id", 0),
    ("parent_binding_sha256", "bad")])
def test_malformed_target_cannot_be_signed(field, value):
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"):
        signer().issue(replace(target(), **{field: value}), now=NOW, expires_at=NOW+300)


@pytest.mark.parametrize("now,expires", [(True, NOW+300), (0, NOW+300),
    (NOW, NOW), (NOW, NOW-1), (NOW, NOW+1801), (NOW, True), (NOW, 2**32)])
def test_issue_rejects_invalid_or_extended_lifetime(now, expires):
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): signer().issue(target(), now=now, expires_at=expires)


def test_bad_key_mac_and_expiry_are_rejected():
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"):
        cancellation.CancellationSigner(b"other-key"*8).verify(token(), target(), now=NOW)
    value = token(); changed = value[:30]+("A" if value[30] != "A" else "B")+value[31:]
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): signer().verify(changed, target(), now=NOW)
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): signer().verify(value, target(), now=NOW+300)


@pytest.mark.parametrize("value", [None, True, "", "A"*50, "A"*52, "="*51, "한"*51])
def test_malformed_token_rejected(value):
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): signer().verify(value, target(), now=NOW)


def test_review_namespace_token_cannot_be_reinterpreted_as_cancellation():
    raw = bytearray(base64.urlsafe_b64decode(token()+"="))
    raw[0] = 1
    old = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): signer().verify(old, target(), now=NOW)


@pytest.mark.parametrize("active", [True, False])
def test_target_allows_original_revoked_card_and_historical_review_epoch(active):
    review, card = records(); card["active"] = active
    review["state"], review["epoch"] = "held", 9
    result = cancellation.cancellation_target(review, card, BINDINGS)
    assert result.card_id == card["id"] and result.epoch == card["epoch"]


@pytest.mark.parametrize("mutation", ["part", "parent", "legacy", "review", "card_time"])
def test_unbound_or_legacy_card_is_not_a_cancellation_target(mutation):
    review, card = records()
    if mutation == "part": card["parts"][0]["payload_sha256"] = "9"*64
    if mutation == "parent": card["bindings"]["parent_binding"] = "9"*64
    if mutation == "legacy": card["bindings"]["parent_binding"] = BINDINGS.digest("prompt-parent@1", card["id"])
    if mutation == "review": card["review_id"] = uid(99)
    if mutation == "card_time": card["expires_at"] = "infinity"
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): cancellation.cancellation_target(review, card, BINDINGS)


class Owner:
    def __init__(self, result=None, fail=False):
        self.result = revoked() if result is None else result
        self.calls = []; self.fail = fail
    def cancel_card(self, event, **kwargs):
        self.calls.append((event, kwargs))
        if self.fail: raise RuntimeError("synthetic-private-owner-detail")
        return self.result


def webhook(owner, payload=None, **overrides):
    kwargs = dict(enabled=True, raw_body=json.dumps(update() if payload is None else payload).encode(),
        headers=HEADERS, policy=POLICY, bindings=BINDINGS, signer=signer(), owner=owner, now=NOW)
    kwargs.update(overrides)
    return cancellation.handle_cancellation_webhook(**kwargs)


def event():
    value = target()
    return cancellation.VerifiedCancellation(card_id=value.card_id, actor_id=ACTOR,
        bot_binding=value.bot_binding, room_binding=value.room_binding,
        message_binding=value.message_binding, human_binding=BINDINGS.digest("human", BOT, HUMAN),
        thread_id=None, token=token())


def owner_call(owner, **overrides):
    kwargs = dict(event=event(), enabled=True, signer=signer(), bindings=BINDINGS, now=NOW)
    kwargs.update(overrides)
    return owner.cancel_card(**kwargs)


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "true"])
def test_default_off_ingress_and_owner_perform_no_io(enabled):
    owner = Owner(); connections = []
    result = webhook(owner, enabled=enabled, raw_body=object(), policy=None)
    assert result["status"] == "disabled" and owner.calls == []
    db_owner = cancellation.PostgresCardCancellationOwner(lambda: connections.append(1))
    assert owner_call(db_owner, enabled=enabled, event=object())["status"] == "disabled"
    assert db_owner.cancel_card()["status"] == "disabled" and connections == []


def test_authenticated_ingress_projects_only_bound_human_event():
    owner = Owner()
    assert webhook(owner) == revoked()
    assert len(owner.calls) == 1
    actual, kwargs = owner.calls[0]
    assert actual == event()
    assert kwargs["enabled"] is True and kwargs["now"] == NOW
    assert kwargs["bindings"] is BINDINGS
    assert str(ROOM) not in repr(actual) and ACTOR not in repr(actual)


@pytest.mark.parametrize("mutation", ["wrong_human", "bot_human", "wrong_bot", "wrong_room", "public_room",
    "forward", "anonymous", "bad_message", "bad_date", "bad_topic", "inline", "legacy_token", "mixed"])
def test_invalid_callback_identity_and_message_refused_before_owner(mutation):
    owner = Owner(); payload = update(); query = payload["callback_query"]; message = query["message"]
    if mutation == "wrong_human": query["from"]["id"] = 999
    if mutation == "bot_human": query["from"]["is_bot"] = True
    if mutation == "wrong_bot": message["from"]["id"] = BOT+1
    if mutation == "wrong_room": message["chat"]["id"] = ROOM-1
    if mutation == "public_room": message["chat"]["username"] = "synthetic_public"
    if mutation == "forward": message["forward_origin"] = {}
    if mutation == "anonymous": message["sender_chat"] = {}
    if mutation == "bad_message": message["message_id"] = True
    if mutation == "bad_date": message["date"] = NOW+1
    if mutation == "bad_topic": message["message_thread_id"] = True
    if mutation == "inline": query["inline_message_id"] = "synthetic"
    if mutation == "legacy_token":
        raw = bytearray(base64.urlsafe_b64decode(query["data"]+"=")); raw[0] = 1
        query["data"] = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    if mutation == "mixed": payload["message"] = {}
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): webhook(owner, payload)
    assert owner.calls == []


@pytest.mark.parametrize("kwargs", [dict(headers=[]), dict(raw_body=b'{"update_id":1,"update_id":2}'),
    dict(headers=HEADERS+[HEADERS[-1]]), dict(raw_body=b"not-json")])
def test_invalid_auth_and_raw_json_have_zero_owner_io(kwargs):
    owner = Owner()
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): webhook(owner, **kwargs)
    assert owner.calls == []


def test_non_uuid_policy_actor_cannot_reach_owner():
    owner = Owner(); policy = replace(POLICY, reviewers=((HUMAN, "opaque_actor"),))
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_invalid$"): webhook(owner, policy=policy)
    assert owner.calls == []


def test_owner_failure_does_not_leak_private_detail_or_retry():
    owner = Owner(fail=True)
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$") as exc: webhook(owner)
    assert "synthetic-private" not in str(exc.value) and len(owner.calls) == 1


class Connection:
    autocommit = False
    def __init__(self, fail_execute=None, commit_error=False):
        review, card = records()
        self.rows = [(review["content_item_id"],), (review,), (card,),
            (datetime.fromtimestamp(NOW+1, timezone.utc),),
            (revoked(), datetime.fromtimestamp(NOW+2, timezone.utc))]
        self.statements, self.exits = [], []
        self.fail_execute, self.commit_error = fail_execute, commit_error
    def __enter__(self): return self
    def __exit__(self, kind, value, tb):
        self.exits.append(kind)
        if kind is None and self.commit_error: raise RuntimeError("synthetic-private-commit-detail")
    def cursor(self):
        connection = self
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, sql, params=()):
                connection.statements.append((sql, params))
                if len(connection.statements) == connection.fail_execute:
                    raise RuntimeError("synthetic-private-driver-detail")
            def fetchone(self): return connection.rows[len(connection.statements)-1]
        return Cursor()


@pytest.mark.parametrize("reused", [False, True])
def test_db_owner_locks_exact_card_and_returns_only_after_commit(reused):
    connection = Connection(); connection.rows[4] = (revoked(reused), connection.rows[4][1])
    if reused: connection.rows[2][0]["active"] = False
    calls = []
    def factory(): calls.append(1); return connection
    assert owner_call(cancellation.PostgresCardCancellationOwner(factory)) == revoked(reused)
    assert calls == [1] and connection.exits == [None] and len(connection.statements) == 5
    for index, table in enumerate(("content_items", "content_ops_button_reviews", "content_ops_button_cards")):
        assert table in connection.statements[index][0].lower()
        assert "for update" in connection.statements[index][0].lower()
    assert "revoke_content_ops_button_card" in connection.statements[4][0]
    for sql, params in connection.statements:
        assert isinstance(params, (tuple, list))
        assert ACTOR not in sql and token() not in sql and "f"*64 not in sql


@pytest.mark.parametrize("index", [0, 1, 2])
def test_missing_item_review_card_blocks_revoke(index):
    connection = Connection(); connection.rows[index] = None
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"): owner_call(cancellation.PostgresCardCancellationOwner(lambda: connection))
    assert len(connection.statements) == index+1 and connection.exits[0] is not None


@pytest.mark.parametrize("field,value", [("message_binding", "e"*64), ("room_binding", "e"*64),
    ("bot_binding", "e"*64), ("thread_id", 5), ("actor_id", "bad")])
def test_event_mismatch_never_calls_revoke(field, value):
    connection = Connection()
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"):
        owner_call(cancellation.PostgresCardCancellationOwner(lambda: connection), event=replace(event(), **{field: value}))
    assert len(connection.statements) < 5


@pytest.mark.parametrize("index", [3, 4])
def test_db_clock_expiry_before_or_after_revoke_rolls_back(index):
    connection = Connection(); expired = datetime.fromtimestamp(NOW+300, timezone.utc)
    connection.rows[index] = (expired,) if index == 3 else (revoked(), expired)
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"): owner_call(cancellation.PostgresCardCancellationOwner(lambda: connection))
    assert len(connection.statements) == index+1 and connection.exits[0] is not None


@pytest.mark.parametrize("index", [3, 4])
@pytest.mark.parametrize("invalid_time", [
    datetime.fromtimestamp(NOW+2, timezone.utc).replace(tzinfo=None),
    datetime.fromtimestamp(NOW+2, timezone.utc).isoformat(),
    NOW+2, True, None,
])
def test_database_clock_must_be_actual_aware_datetime(index, invalid_time):
    connection = Connection()
    connection.rows[index] = (invalid_time,) if index == 3 else (revoked(), invalid_time)
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"):
        owner_call(cancellation.PostgresCardCancellationOwner(lambda: connection))
    assert len(connection.statements) == index+1 and connection.exits[0] is not None


def test_database_clock_cannot_go_backwards_across_revoke():
    connection = Connection()
    connection.rows[4] = (revoked(), datetime.fromtimestamp(NOW, timezone.utc))
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"):
        owner_call(cancellation.PostgresCardCancellationOwner(lambda: connection))
    assert len(connection.statements) == 5 and connection.exits[0] is not None


@pytest.mark.parametrize("field,value", [("status", "published"), ("card_id", uid(99)),
    ("card_id", "bad"), ("reused", 1), ("execution_authorized", True),
    ("execution_authorized", 0), ("extra", "private")])
def test_malformed_revoke_result_cannot_commit(field, value):
    connection = Connection(); connection.rows[4][0][field] = value
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"): owner_call(cancellation.PostgresCardCancellationOwner(lambda: connection))
    assert len(connection.statements) == 5 and connection.exits[0] is not None


@pytest.mark.parametrize("row", [None, (), (revoked(),), (None, datetime.fromtimestamp(NOW, timezone.utc)),
    (revoked(), datetime.fromtimestamp(NOW, timezone.utc), "extra")])
def test_malformed_postclock_row_rolls_back(row):
    connection = Connection(); connection.rows[4] = row
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"): owner_call(cancellation.PostgresCardCancellationOwner(lambda: connection))
    assert connection.exits[0] is not None


def test_lost_commit_ack_is_unknown_never_retried():
    connection = Connection(commit_error=True); calls = []
    def factory(): calls.append(1); return connection
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$") as exc: owner_call(cancellation.PostgresCardCancellationOwner(factory))
    assert "synthetic-private" not in str(exc.value)
    assert calls == [1] and connection.exits == [None] and len(connection.statements) == 5


@pytest.mark.parametrize("index", [1, 3, 5])
def test_driver_failure_unknown_no_retry(index):
    connection = Connection(fail_execute=index); calls = []
    def factory(): calls.append(1); return connection
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$") as exc: owner_call(cancellation.PostgresCardCancellationOwner(factory))
    assert "synthetic-private" not in str(exc.value) and calls == [1]
    assert len(connection.statements) == index and connection.exits[0] is not None


def test_authenticated_webhook_to_transaction_has_one_commit_and_bound_rpc_parameters():
    connection = Connection(); calls = []
    def factory(): calls.append(1); return connection
    assert webhook(cancellation.PostgresCardCancellationOwner(factory)) == revoked()
    assert calls == [1] and connection.exits == [None]
    assert connection.statements[0][1] == (uid(5), event().bot_binding,
        event().room_binding, event().message_binding)
    assert connection.statements[4][1] == (uid(5), uid(1), "f"*64,
        ACTOR, event().bot_binding, event().human_binding)


@pytest.mark.parametrize("autocommit", [True, None, 0, 1])
def test_non_transactional_connection_is_rejected_before_any_sql(autocommit):
    connection = Connection(); connection.autocommit = autocommit
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"):
        owner_call(cancellation.PostgresCardCancellationOwner(lambda: connection))
    assert connection.statements == [] and connection.exits[0] is not None


def test_wrong_signing_key_cannot_call_revoke_even_with_authenticated_callback():
    connection = Connection()
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"):
        webhook(cancellation.PostgresCardCancellationOwner(lambda: connection),
            signer=cancellation.CancellationSigner(b"other-key"*8))
    assert len(connection.statements) == 4 and connection.exits[0] is not None


@pytest.mark.parametrize("field,value", [("status", "published"), ("card_id", uid(99)),
    ("reused", 1), ("execution_authorized", True), ("execution_authorized", 0), ("extra", "private")])
def test_ingress_rejects_malformed_owner_readback_without_retry(field, value):
    result = revoked(); result[field] = value; owner = Owner(result=result)
    with pytest.raises(cancellation.CancellationError, match="^review_cancellation_outcome_unknown$"):
        webhook(owner)
    assert len(owner.calls) == 1
