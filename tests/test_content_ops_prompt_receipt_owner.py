"""Reserved-attempt response owner contract; synthetic fixtures and fake DB only."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import pytest
from core.content_ops.prompt_attempt import prompt_instruction
from core.content_ops.prompt_receipt import PromptReceiptError, ValidatedPromptReceipt
from core.content_ops.prompt_receipt_owner import PostgresPromptReceiptOwner
from core.content_ops.prompt_reservation import card_parent_binding
from core.content_ops.review_edit_ingress import EditBindings


def uid(n): return f"10000000-0000-4000-8000-{n:012d}"


ITEM_ID, REVIEW_ID, ACTOR_ID, RECEIPT_ID, PROMPT_ID = (uid(n) for n in range(1, 6))
CARD_ID, WORKSPACE_ID, VERSION_ID = (uid(n) for n in range(6, 9))
OBSERVED = datetime(2026, 9, 14, 12, 0, 10, 123456, tzinfo=timezone.utc)
STARTED = OBSERVED.replace(second=0, microsecond=0)
FINGERPRINT, UNKNOWN = "f"*64, "^prompt_registration_unknown$"
BINDINGS = EditBindings(b"synthetic-owner-reservation-key-0001")
BOT, CHAT, HUMAN = 101, -202, 303
TEXT = prompt_instruction("edit_telegram")


def registered(reused=False):
    return dict(status="prompt_registered", prompt_id=PROMPT_ID, reused=reused, execution_authorized=False)


def reserved():
    return dict(status="attempt_recorded", attempt_id=RECEIPT_ID, reused=True, execution_authorized=False)


def response(**changes):
    message = dict(message_id=21, date=int(OBSERVED.timestamp()),
        chat=dict(id=CHAT, type="supergroup"), text=TEXT)
    message["from"] = dict(id=BOT, is_bot=True)
    message.update(changes)
    return json.dumps(dict(ok=True, result=message)).encode()


def fixture_rows():
    review = dict(id=REVIEW_ID, workspace_id=WORKSPACE_ID, client_id="squid",
        content_item_id=ITEM_ID, content_version_id=VERSION_ID,
        version_fingerprint=FINGERPRINT, epoch=2, state="edit_requested")
    card = dict(id=CARD_ID, review_id=REVIEW_ID, epoch=1, version_fingerprint=FINGERPRINT,
        bindings=dict(bot=BINDINGS.digest("bot", BOT), room=BINDINGS.digest("room", BOT, CHAT),
            message=BINDINGS.digest("card-message@2", BOT, CHAT, 14), packet_receipt="b"*64,
            card_receipt="c"*64, parent_binding="d"*64, thread_id=None),
        parts=[dict(kind=kind, message_binding=BINDINGS.digest("card-message@2", BOT, CHAT, n),
            payload_sha256=str(n%10)*64, outcome="sent")
            for kind, n in zip(("image", "telegram", "x"), (11, 12, 13))],
        delivered_at=(STARTED-timedelta(seconds=30)).isoformat(),
        expires_at=(STARTED+timedelta(minutes=10)).isoformat(), active=True)
    card["bindings"]["parent_binding"] = card_parent_binding(BINDINGS, review, card)
    attempt = dict(id=RECEIPT_ID, card_id=CARD_ID, review_id=REVIEW_ID, actor_id=ACTOR_ID,
        epoch=2, edit_action_key="a"*64, version_fingerprint=FINGERPRINT,
        bot_binding=card["bindings"]["bot"], room_binding=card["bindings"]["room"],
        human_binding=BINDINGS.digest("human", BOT, HUMAN), thread_id=None,
        parent_binding_sha256=card["bindings"]["parent_binding"],
        expected_text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
        started_at=STARTED.isoformat(), expires_at=(STARTED+timedelta(minutes=10)).isoformat())
    return [(ITEM_ID,), (review,), (card,), (attempt,),
        (reserved(), OBSERVED+timedelta(seconds=1)), None, (True,), (registered(),)]


class Connection:
    autocommit = False
    def __init__(self, execute_error=None, fetch_error=None, commit_error=False):
        self.rows = fixture_rows()
        self.execute_error, self.fetch_error, self.commit_error = execute_error, fetch_error, commit_error
        self.statements, self.events, self.exits = [], [], []
    def __enter__(self): self.events.append("begin"); return self
    def __exit__(self, kind, value, tb):
        self.exits.append(kind); self.events.append("rollback" if kind else "commit")
        if kind is None and self.commit_error: raise RuntimeError("synthetic-private-commit-detail")
    def cursor(self):
        connection = self
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): connection.events.append("cursor_closed")
            def execute(self, sql, params):
                connection.statements.append((sql, params))
                if len(connection.statements) == connection.execute_error:
                    raise RuntimeError("synthetic-private-query-detail")
            def fetchone(self):
                index = len(connection.statements)
                if index == connection.fetch_error: raise RuntimeError("synthetic-private-fetch-detail")
                return connection.rows[index-1]
        return Cursor()


def call(owner, **overrides):
    args = dict(enabled=True, attempt_id=RECEIPT_ID, bindings=BINDINGS,
        bot_id=BOT, chat_id=CHAT, human_id=HUMAN, thread_id=None,
        http_status=200, raw_response=response(), observed_at=OBSERVED)
    args.update(overrides)
    return owner.record_prompt_response(**args)


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "true", [], {}])
def test_both_apis_default_off_without_io(enabled):
    calls = []; owner = PostgresPromptReceiptOwner(lambda: calls.append(1))
    expected = dict(status="disabled", execution_authorized=False)
    assert call(owner, enabled=enabled, raw_response=object(), observed_at=object()) == expected
    assert owner.record_prompt_receipt(object(), enabled=enabled) == expected
    assert owner.record_prompt_response() == expected
    assert calls == []


@pytest.mark.parametrize("value", [None, {}, object(), ValidatedPromptReceipt(
    RECEIPT_ID, REVIEW_ID, ACTOR_ID, 2, "a"*64, "b"*64, "c"*64, "d"*64,
    "e"*64, "f"*64, int(OBSERVED.timestamp()))])
def test_legacy_receipt_only_bypass_always_refused(value):
    calls = []
    with pytest.raises(PromptReceiptError, match=UNKNOWN):
        PostgresPromptReceiptOwner(lambda: calls.append(1)).record_prompt_receipt(
            value, enabled=True, observed_at=OBSERVED, version_fingerprint=FINGERPRINT)
    assert calls == []


@pytest.mark.parametrize("reused", [False, True])
def test_atomic_lock_order_parameterization_and_commit_ack(reused):
    connection = Connection(); connection.rows[7] = (registered(reused),); calls = []
    def factory(): calls.append(1); return connection
    assert call(PostgresPromptReceiptOwner(factory)) == registered(reused)
    assert calls == [1] and connection.exits == [None] and connection.events[-1] == "commit"
    assert len(connection.statements) == 8
    sqls = [sql.lower() for sql, _ in connection.statements]
    for index, table, lock in [(0, "content_items", "for update"),
        (1, "content_ops_button_reviews", "for update"), (2, "content_ops_button_cards", "for share"),
        (3, "content_ops_button_prompt_attempts", "for share")]:
        assert table in sqls[index] and lock in sqls[index]
    assert "reserve_content_ops_button_prompt_attempt" in sqls[4]
    assert "insert into" in sqls[5] and "on conflict" in sqls[5] and "do nothing" in sqls[5]
    assert "content_ops_button_prompt_receipts" in sqls[6]
    assert "register_content_ops_button_edit_prompt" in sqls[7]
    for sql, params in connection.statements:
        assert "%s" in sql and isinstance(params, (tuple, list))
        for value in (RECEIPT_ID, REVIEW_ID, ACTOR_ID, FINGERPRINT, TEXT): assert value not in sql
    assert OBSERVED in connection.statements[5][1]
    assert int(OBSERVED.timestamp()) not in connection.statements[5][1]


@pytest.mark.parametrize("field,value", [("attempt_id", "bad"),
    ("attempt_id", "00000000-0000-0000-0000-000000000000"),
    ("attempt_id", RECEIPT_ID.replace("-", "")), ("bindings", None),
    ("bot_id", True), ("chat_id", True), ("chat_id", 202), ("human_id", BOT),
    ("thread_id", True), ("observed_at", None), ("observed_at", OBSERVED.replace(tzinfo=None)),
    ("observed_at", "2026-09-14T12:00:10Z")])
def test_invalid_caller_input_refused_before_connect(field, value):
    calls = []
    with pytest.raises(PromptReceiptError, match=UNKNOWN):
        call(PostgresPromptReceiptOwner(lambda: calls.append(1)), **{field: value})
    assert calls == []


@pytest.mark.parametrize("autocommit", [True, 1, None, "false"])
def test_autocommit_connection_rejected_before_sql(autocommit):
    connection = Connection(); connection.autocommit = autocommit
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(lambda: connection))
    assert not connection.statements


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_missing_durable_lineage_refuses_without_receipt_insert(index):
    connection = Connection(); connection.rows[index] = None
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(lambda: connection))
    assert len(connection.statements) == index+1 and connection.events[-1] == "rollback"


@pytest.mark.parametrize("index,field,value", [
    (1, "content_item_id", uid(99)), (1, "state", "active"), (1, "epoch", True),
    (2, "active", False), (2, "review_id", uid(99)), (2, "epoch", 2),
    (3, "id", uid(99)), (3, "card_id", uid(99)), (3, "review_id", uid(99)),
    (3, "epoch", True), (3, "version_fingerprint", "e"*64),
    (3, "bot_binding", "e"*64), (3, "room_binding", "e"*64),
    (3, "human_binding", "e"*64), (3, "parent_binding_sha256", "e"*64),
    (3, "expected_text_sha256", "e"*64), (3, "thread_id", 5),
    (3, "started_at", (OBSERVED+timedelta(seconds=1)).isoformat()),
    (3, "expires_at", OBSERVED.isoformat())])
def test_stored_mismatch_never_inserts_receipt(index, field, value):
    connection = Connection(); connection.rows[index][0][field] = value
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(lambda: connection))
    assert len(connection.statements) <= 5 and connection.events[-1] == "rollback"


@pytest.mark.parametrize("field,value", [("status", "created"), ("attempt_id", uid(99)),
    ("reused", False), ("reused", 1), ("execution_authorized", True), ("extra", "hidden")])
def test_reservation_must_revalidate_same_existing_attempt(field, value):
    connection = Connection(); connection.rows[4][0][field] = value
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(lambda: connection))
    assert len(connection.statements) == 5 and connection.events[-1] == "rollback"


@pytest.mark.parametrize("changes", [dict(text=TEXT+" changed"), dict(message_id=14), dict(message_id=13),
    dict(date=int(OBSERVED.timestamp())+1), dict(date=int(STARTED.timestamp())-1),
    dict(chat=dict(id=CHAT-1, type="supergroup")), dict(message_thread_id=5),
    {"from": dict(id=BOT+1, is_bot=True)}, dict(reply_markup={})])
def test_wrong_response_or_recycled_message_rejected(changes):
    connection = Connection()
    with pytest.raises(PromptReceiptError, match=UNKNOWN):
        call(PostgresPromptReceiptOwner(lambda: connection), raw_response=response(**changes))
    assert len(connection.statements) <= 5 and connection.events[-1] == "rollback"


@pytest.mark.parametrize("row", [None, (), (False,), (1,), (None,), (True, True)])
def test_receipt_conflict_rolls_back_without_registration(row):
    connection = Connection(); connection.rows[6] = row
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(lambda: connection))
    assert len(connection.statements) == 7 and connection.events[-1] == "rollback"


@pytest.mark.parametrize("field,value", [("status", "sent"), ("prompt_id", "bad"),
    ("prompt_id", "00000000-0000-0000-0000-000000000000"), ("prompt_id", PROMPT_ID.replace("-", "")),
    ("reused", 1), ("reused", None), ("execution_authorized", True),
    ("execution_authorized", 0), ("extra", "private")])
def test_bad_registration_receipt_validated_inside_transaction(field, value):
    connection = Connection(); connection.rows[7][0][field] = value
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(lambda: connection))
    assert len(connection.statements) == 8 and connection.events[-1] == "rollback"


@pytest.mark.parametrize("row", [None, (), (None,), ([],), (True,), (registered(), "extra")])
def test_malformed_registration_row_causes_rollback(row):
    connection = Connection(); connection.rows[7] = row
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(lambda: connection))
    assert connection.events[-1] == "rollback"


@pytest.mark.parametrize("kind,index", [("execute_error", 1), ("execute_error", 5),
    ("execute_error", 6), ("execute_error", 8), ("fetch_error", 4), ("fetch_error", 5),
    ("fetch_error", 7), ("fetch_error", 8)])
def test_driver_errors_rollback_never_retry_or_leak(kind, index):
    connection = Connection(**{kind: index}); calls = []
    def factory(): calls.append(1); return connection
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(factory))
    assert calls == [1] and len(connection.statements) == index
    assert connection.events[-1] == "rollback"


def test_lost_commit_ack_unknown_without_retry():
    connection = Connection(commit_error=True); calls = []
    def factory(): calls.append(1); return connection
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(factory))
    assert calls == [1] and len(connection.statements) == 8
    assert connection.exits == [None] and connection.events[-1] == "commit"


def test_factory_failure_fixed_unknown_without_retry():
    calls = []
    def factory(): calls.append(1); raise RuntimeError("synthetic-private-connect-detail")
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(factory))
    assert calls == [1]


@pytest.mark.parametrize("field,value", [("http_status", 500), ("http_status", True),
    ("raw_response", b'{"ok":false,"description":"synthetic-private-provider-detail"}'),
    ("raw_response", b'{"ok":true,"ok":true,"result":{}}'),
    ("raw_response", b"not-json"), ("raw_response", "not-bytes")])
def test_unconfirmed_response_never_reaches_receipt_insert(field, value):
    connection = Connection()
    with pytest.raises(PromptReceiptError, match=UNKNOWN):
        call(PostgresPromptReceiptOwner(lambda: connection), **{field: value})
    assert len(connection.statements) <= 5


@pytest.mark.parametrize("db_now", [None, OBSERVED.replace(tzinfo=None),
    OBSERVED-timedelta(microseconds=1), STARTED+timedelta(minutes=10)])
def test_database_clock_must_follow_original_observation_and_precede_expiry(db_now):
    connection = Connection(); connection.rows[4] = (reserved(), db_now)
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(lambda: connection))
    assert len(connection.statements) == 5 and connection.events[-1] == "rollback"


@pytest.mark.parametrize("row", [None, (), (reserved(),), (reserved(), OBSERVED, "extra")])
def test_malformed_reservation_readback_never_inserts(row):
    connection = Connection(); connection.rows[4] = row
    with pytest.raises(PromptReceiptError, match=UNKNOWN): call(PostgresPromptReceiptOwner(lambda: connection))
    assert len(connection.statements) == 5 and connection.events[-1] == "rollback"


def test_equivalent_timezone_observation_preserves_exact_instant_in_insert():
    observed = OBSERVED.astimezone(timezone(timedelta(hours=9)))
    connection = Connection()
    assert call(PostgresPromptReceiptOwner(lambda: connection), observed_at=observed) == registered()
    assert observed in connection.statements[5][1]


def test_replayed_receipt_with_changed_observation_time_is_conflict_not_freshened():
    connection = Connection(); connection.rows[6] = (False,)
    changed = OBSERVED+timedelta(microseconds=1)
    with pytest.raises(PromptReceiptError, match=UNKNOWN):
        call(PostgresPromptReceiptOwner(lambda: connection), observed_at=changed)
    assert changed in connection.statements[5][1]
    assert changed in connection.statements[6][1]
    assert len(connection.statements) == 7 and connection.events[-1] == "rollback"


def test_precise_reserved_expiry_is_bound_to_insert_and_readback_without_rounding():
    connection = Connection()
    expiry = (STARTED+timedelta(minutes=9, microseconds=654321)).astimezone(
        timezone(timedelta(hours=9)))
    connection.rows[3][0]["expires_at"] = expiry.isoformat()
    assert call(PostgresPromptReceiptOwner(lambda: connection)) == registered()
    insert_sql, insert_params = connection.statements[5]
    readback_sql, readback_params = connection.statements[6]
    assert "reservation_expires_at" in insert_sql
    assert "reservation_expires_at=%s" in readback_sql
    assert insert_params[-1] == expiry and type(insert_params[-1]) is datetime
    assert readback_params[-2] == expiry and type(readback_params[-2]) is datetime
    assert insert_params[-1].microsecond == readback_params[-2].microsecond == 654321
    assert insert_params[-1].utcoffset() == readback_params[-2].utcoffset() == expiry.utcoffset()


def test_replayed_receipt_with_different_reserved_expiry_is_not_registered():
    connection = Connection()
    expiry = STARTED+timedelta(minutes=9, microseconds=123456)
    connection.rows[3][0]["expires_at"] = expiry.isoformat()
    # Emulate owner SQL equality returning false because the existing receipt
    # is tied to a different expiry; this is never an overwrite or renewal.
    connection.rows[6] = (False,)
    with pytest.raises(PromptReceiptError, match=UNKNOWN):
        call(PostgresPromptReceiptOwner(lambda: connection))
    assert connection.statements[5][1][-1] == expiry
    assert connection.statements[6][1][-2] == expiry
    assert len(connection.statements) == 7 and connection.events[-1] == "rollback"
