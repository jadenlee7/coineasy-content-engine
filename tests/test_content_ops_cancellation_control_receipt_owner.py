"""Synthetic original receipts only; no live owner loader or provider."""
from dataclasses import astuple, replace
from datetime import datetime, timezone, timedelta
import json

import pytest

from core.content_ops.cancellation_control_receipt_owner import (
    PostgresOriginalControlReceiptOwner, StoredOriginalControlReceipt,
)
from core.content_ops.cancellation_markup import CancellationMarkupError
from test_content_ops_cancellation_markup import response, previous_markup
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_cancellation_markup_owner import Connection
from test_content_ops_review_cancellation import records, NOW, uid


def stored():
    identity, _, evidence, _ = fixture()
    data = response(identity['plan'])
    del data['result']['edit_date']
    data['result']['reply_markup'] = previous_markup()
    return StoredOriginalControlReceipt(evidence.card_id, evidence.receipt_sha256,
        200, json.dumps(data).encode(), evidence.delivered_at)


class ReceiptConnection(Connection):
    def __init__(self, reused=False, commit_error=False):
        super().__init__(commit_error=commit_error)
        _, card, evidence, _ = fixture()
        now = datetime.fromtimestamp(NOW+.1, timezone.utc)
        self.rows = [('5000', '10000'), (card,), records(), (now,),
                     astuple(evidence) if reused else None, (now,)]
        if not reused:
            self.rows.append(astuple(evidence))

    def cursor(self):
        parent = self
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, sql, params=()): parent.calls.append((sql, params))
            def fetchone(self): return parent.rows.pop(0)
        return Cursor()


def run(connection, receipt=None, **changes):
    identity = fixture()[0]
    identity.update(changes)
    calls = []
    def loader(**kwargs):
        assert connection.exits == []
        calls.append(kwargs)
        return stored() if receipt is None else receipt
    result = PostgresOriginalControlReceiptOwner(lambda: connection, receipt_loader=loader).ingest(
        enabled=True, **identity)
    return result, calls


@pytest.mark.parametrize('enabled', [False, None, 1, 'true'])
def test_default_off_never_opens_or_loads(enabled):
    def fail(**kwargs): raise AssertionError('no I/O')
    assert PostgresOriginalControlReceiptOwner(fail, receipt_loader=fail).ingest(
        enabled=enabled, plan=object()) is None


def test_missing_trusted_loader_refuses_before_connecting():
    connections = []
    with pytest.raises(CancellationMarkupError, match='^original_control_receipt_unknown$'):
        PostgresOriginalControlReceiptOwner(lambda: connections.append(1)).ingest(
            enabled=True, **fixture()[0])
    assert connections == []


@pytest.mark.parametrize('reused', [False, True])
def test_minimized_exact_persistence_and_idempotent_readback(reused):
    conn = ReceiptConnection(reused)
    result, calls = run(conn)
    assert result == dict(status='original_control_recorded', reused=reused, execution_authorized=False)
    assert len(calls) == 1 and set(calls[0]) == {'cursor', 'card_id', 'receipt_sha256'}
    assert conn.exits == [None]
    assert len(conn.calls) == (6 if reused else 7)
    writes = [(s, p) for s, p in conn.calls if s.strip().startswith('insert')]
    assert len(writes) == (0 if reused else 1)
    if writes:
        assert writes[0][1] == astuple(fixture()[2])
        assert stored().raw_response not in writes[0][1]
    assert all('markup_approvals' not in sql and 'update ' not in sql and 'delete ' not in sql
               for sql, _ in conn.calls)
    assert stored().receipt_sha256 not in repr(stored())
    assert fixture()[0]['plan'].card_id not in str(result)


@pytest.mark.parametrize('field, value', [
    ('card_id', uid(98)), ('receipt_sha256', '0'*64), ('receipt_sha256', None),
    ('http_status', True), ('http_status', 400), ('raw_response', b'{}'),
    ('raw_response', b'{"ok":true,"ok":true,"result":{}}'),
    ('raw_response', b'{"ok":true,"result":NaN}'),
    ('raw_response', b'x'*32769), ('raw_response', '{}'),
    ('observed_at', datetime.fromtimestamp(NOW)),
    ('observed_at', datetime.fromtimestamp(NOW, timezone.utc)),
])
def test_cross_receipt_malformed_or_changed_observation_cannot_write(field, value):
    conn = ReceiptConnection()
    with pytest.raises(CancellationMarkupError, match='^original_control_receipt_unknown$'):
        run(conn, replace(stored(), **{field: value}))
    assert len(conn.calls) == 4 and conn.exits[0] is not None


@pytest.mark.parametrize('field, value', [
    ('message_id', True), ('message_id', 999), ('date', NOW), ('text', 'changed'),
    ('entities', []), ('entities', None), ('edit_date', NOW), ('forward_origin', {}),
    ('message_thread_id', None), ('message_thread_id', True), ('message_thread_id', 7),
    ('reply_to_message', {}), ('rich_message', {}), ('photo', []),
    ('reply_markup', {'inline_keyboard': []}), ('reply_markup', None),
    ('chat', {'id': -301, 'type': 'channel'}),
    ('chat', {'id': -301, 'type': 'supergroup', 'username': 'public'}),
    ('from', {'id': 101, 'is_bot': False}), ('from', {'id': True, 'is_bot': True}),
])
def test_original_message_must_match_the_full_plan(field, value):
    receipt = stored(); data = json.loads(receipt.raw_response)
    data['result'][field] = value
    conn = ReceiptConnection()
    with pytest.raises(CancellationMarkupError):
        run(conn, replace(receipt, raw_response=json.dumps(data).encode()))
    assert not any(sql.strip().startswith('insert') for sql, _ in conn.calls)


def test_already_edited_response_is_not_an_original_receipt():
    receipt = replace(stored(), raw_response=json.dumps(response(fixture()[0]['plan'])).encode())
    with pytest.raises(CancellationMarkupError): run(ReceiptConnection(), receipt)


@pytest.mark.parametrize('reused', [False, True])
@pytest.mark.parametrize('column, value', [(0, uid(99)), (1, 'f'*64), (3, True), (11, '{}')])
def test_changed_existing_or_returned_record_rolls_back(reused, column, value):
    conn = ReceiptConnection(reused)
    index = 4 if reused else 6
    row = list(conn.rows[index]); row[column] = value; conn.rows[index] = tuple(row)
    with pytest.raises(CancellationMarkupError): run(conn)
    assert conn.exits[0] is not None
    if reused:
        assert not any(sql.strip().startswith('insert') for sql, _ in conn.calls)


@pytest.mark.parametrize('index', [3, 5])
@pytest.mark.parametrize('value', [datetime.fromtimestamp(NOW+300, timezone.utc),
    datetime.fromtimestamp(NOW-1, timezone.utc), datetime.fromtimestamp(NOW), None])
def test_database_clock_boundaries_prevent_insert(index, value):
    conn = ReceiptConnection(); conn.rows[index] = (value,)
    with pytest.raises(CancellationMarkupError): run(conn)
    assert not any(sql.strip().startswith('insert') for sql, _ in conn.calls)


def test_backward_clock_inside_window_refused():
    conn = ReceiptConnection(); conn.rows[5] = (conn.rows[3][0] - timedelta(microseconds=1),)
    with pytest.raises(CancellationMarkupError): run(conn)


def test_autocommit_inactive_card_and_bad_parent_refuse():
    conn = ReceiptConnection(); conn.autocommit = True
    with pytest.raises(CancellationMarkupError): run(conn)
    assert conn.calls == []
    conn = ReceiptConnection(); conn.rows[1][0]['active'] = False
    with pytest.raises(CancellationMarkupError): run(conn)
    assert len(conn.calls) == 2
    conn = ReceiptConnection()
    for card in (conn.rows[1][0], conn.rows[2][1]): card['bindings']['parent_binding'] = '0'*64
    with pytest.raises(CancellationMarkupError): run(conn)
    assert len(conn.calls) == 4


def test_unknown_commit_and_loader_failure_sanitized_without_retry():
    conn = ReceiptConnection(commit_error=True); connections = []; loads = []
    def factory(): connections.append(1); return conn
    def loader(**kwargs): loads.append(1); return stored()
    owner = PostgresOriginalControlReceiptOwner(factory, receipt_loader=loader)
    with pytest.raises(CancellationMarkupError, match='^original_control_receipt_unknown$'):
        owner.ingest(enabled=True, **fixture()[0])
    assert connections == [1] and loads == [1] and conn.exits == [None]
    conn = ReceiptConnection()
    def failed(**kwargs): raise RuntimeError('private original receipt details')
    with pytest.raises(CancellationMarkupError, match='^original_control_receipt_unknown$'):
        PostgresOriginalControlReceiptOwner(lambda: conn, receipt_loader=failed).ingest(
            enabled=True, **fixture()[0])
    assert len(conn.calls) == 4 and conn.exits[0] is not None
