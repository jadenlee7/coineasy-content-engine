"""Synthetic raw Telegram callbacks; no live receipt, secret or transport."""
from dataclasses import astuple, replace
from datetime import datetime, timezone
import base64
import json

import pytest

from core.content_ops.cancellation_markup_confirmation import (
    MarkupConfirmationError, MarkupConfirmationTarget, StoredMarkupConfirmation,
    TelegramMarkupConfirmation, markup_confirmation_payload,
)
from core.content_ops.cancellation_markup import CancellationMarkupError
from core.content_ops.cancellation_markup_approval_owner import PostgresMarkupApprovalOwner
from core.content_ops.review_edit_ingress import EditBindings
from test_content_ops_cancellation_markup_approval_owner import ApprovalConnection
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_review_cancellation import (
    NOW, BOT, ROOM, HUMAN, BINDINGS, POLICY, HEADERS, uid, update, token,
)


def moment(seconds=NOW):
    return datetime.fromtimestamp(seconds, timezone.utc)


def target():
    identity, _, _, approval = fixture()
    return MarkupConfirmationTarget(*astuple(approval)[:7],
        identity['plan'].started_at, identity['plan'].expires_at)


def stored():
    return StoredMarkupConfirmation(target(), BOT, ROOM, 80, None, NOW, moment(), True)


def callback():
    value = update()
    payload = markup_confirmation_payload(target(), BINDINGS)
    value['callback_query']['data'] = payload['reply_markup']['inline_keyboard'][0][0]['callback_data']
    value['callback_query']['message'].update(payload, message_id=80, date=NOW)
    return value


class Cursor:
    def __init__(self, times=(NOW+.1, NOW+.2)):
        self.rows = [(moment(t),) for t in times]
        self.calls = []
    def execute(self, sql): self.calls.append(sql)
    def fetchone(self): return self.rows.pop(0)


def adapter(value=None, receipt=None, calls=None, **kwargs):
    def reader(**params):
        if calls is not None: calls.append(params)
        return stored() if receipt is None else receipt
    args = dict(raw_body=json.dumps(callback() if value is None else value).encode(),
        headers=HEADERS, policy=POLICY, bindings=BINDINGS, received_at=moment(),
        receipt_reader=reader, enabled=True)
    args.update(kwargs)
    return TelegramMarkupConfirmation(**args)


def invoke(auth, cursor=None):
    return auth(cursor=cursor or Cursor(), approval_id=target().approval_id)


@pytest.mark.parametrize('enabled', [False, None, 1, 'true'])
def test_off_has_no_reader_or_cursor_io(enabled):
    calls = []; cursor = Cursor()
    assert invoke(adapter(calls=calls, enabled=enabled), cursor) is None
    assert not calls and not cursor.calls


def test_explicit_callback_yields_only_exact_markup_decision():
    calls = []; cursor = Cursor()
    decision = invoke(adapter(calls=calls), cursor)
    assert decision == fixture()[3]
    assert calls == [dict(cursor=cursor, approval_id=target().approval_id)]
    assert cursor.calls == ['select clock_timestamp()'] * 2
    assert decision.action == 'append_cancellation_markup@1'
    assert uid(99) not in repr(decision) and str(ROOM) not in repr(stored())


def test_payload_is_separate_action_domain_and_explicit_not_content_approval():
    payload = markup_confirmation_payload(target(), BINDINGS)
    data = payload['reply_markup']['inline_keyboard'][0][0]['callback_data']
    assert len(data) == 51
    assert base64.urlsafe_b64decode(data+'=')[0] == 3
    assert base64.urlsafe_b64decode(data+'=')[21:22] == b'm'
    assert '공개 게시·재전송은 하지 않습니다' in payload['text']
    assert target().plan_seal in payload['text']
    assert POLICY.webhook_secret not in json.dumps(payload)


@pytest.mark.parametrize('field,value', [
    ('approval_id', 'bad'), ('attempt_id', None), ('actor_id', 'ABCDEF00-0000-4000-8000-000000000000'),
    ('card_id', '00000000-0000-0000-0000-000000000000'),
    ('human_binding', 'g'*64), ('plan_seal', 'F'*64), ('original_receipt_sha256', ''),
    ('started_at', True), ('expires_at', NOW), ('expires_at', NOW+1801),
])
def test_malformed_targets_cannot_prepare_confirmation(field, value):
    with pytest.raises(MarkupConfirmationError):
        markup_confirmation_payload(replace(target(), **{field: value}), BINDINGS)


@pytest.mark.parametrize('field', ['approval_id', 'attempt_id', 'card_id', 'actor_id',
    'human_binding', 'plan_seal', 'original_receipt_sha256', 'started_at', 'expires_at'])
def test_every_target_field_is_bound_to_callback(field):
    changed = uid(88) if field.endswith('_id') else 'e'*64
    if field == 'started_at': changed = NOW-1
    if field == 'expires_at': changed = NOW+299
    receipt = replace(stored(), target=replace(target(), **{field: changed}))
    with pytest.raises(MarkupConfirmationError): invoke(adapter(receipt=receipt))


@pytest.mark.parametrize('change', ['human', 'bot', 'room', 'forward', 'bad_secret',
    'duplicate_header', 'duplicate_json', 'body_type', 'naive_time'])
def test_unauthenticated_requests_do_not_touch_adapter_cursor(change):
    value = callback(); kwargs = {}; calls=[]; cursor=Cursor()
    query = value['callback_query']; message=query['message']
    if change == 'human': query['from']['id'] += 1
    if change == 'bot': message['from']['id'] += 1
    if change == 'room': message['chat']['id'] -= 1
    if change == 'forward': message['forward_origin'] = {}
    if change == 'bad_secret': kwargs['headers'] = HEADERS[:1]
    if change == 'duplicate_header': kwargs['headers'] = HEADERS+HEADERS[1:]
    if change == 'duplicate_json': kwargs['raw_body'] = b'{"update_id":1,"update_id":2}'
    if change == 'body_type': kwargs['raw_body'] = bytearray(b'{}')
    if change == 'naive_time': kwargs['received_at'] = datetime.fromtimestamp(NOW)
    with pytest.raises(MarkupConfirmationError, match='^markup_confirmation_refused$'):
        invoke(adapter(value, calls=calls, **kwargs), cursor)
    assert not calls and not cursor.calls


@pytest.mark.parametrize('field,value', [('message_id', 81), ('text', '승인'),
    ('date', NOW-1), ('edit_date', NOW), ('photo', []), ('caption', 'x'),
    ('reply_markup', {'inline_keyboard': []}), ('entities', [{'type':'bold','offset':0,'length':2}]),
    ('message_thread_id', 2), ('is_topic_message', False), ('external_reply', {})])
def test_copied_changed_or_media_prompt_is_not_confirmation(field, value):
    data=callback(); data['callback_query']['message'][field] = value
    with pytest.raises(MarkupConfirmationError): invoke(adapter(data))


@pytest.mark.parametrize('field,value', [('active', False), ('active', 1),
    ('bot_id', True), ('chat_id', ROOM-1), ('message_id', True), ('message_id', 81),
    ('thread_id', True), ('message_date', True), ('delivered_at', moment(NOW+1))])
def test_stored_receipt_must_be_exact_active_and_delivered(field, value):
    with pytest.raises(MarkupConfirmationError):
        invoke(adapter(receipt=replace(stored(), **{field:value})))


def test_topic_prompt_requires_exact_topic_and_strict_true_flag():
    data=callback(); message=data['callback_query']['message']
    message.update(message_thread_id=42, is_topic_message=True)
    receipt=replace(stored(), thread_id=42)
    assert invoke(adapter(data, receipt)).action == 'append_cancellation_markup@1'
    message['is_topic_message'] = 1
    with pytest.raises(MarkupConfirmationError): invoke(adapter(data, receipt))


@pytest.mark.parametrize('times', [(NOW-1,NOW), (NOW+6,NOW+6),
    (NOW+1,NOW+.9), (NOW+.1,NOW+6)])
def test_future_delayed_or_backward_db_time_is_refused(times):
    with pytest.raises(MarkupConfirmationError): invoke(adapter(), Cursor(times))


def test_expired_original_callback_is_not_refreshed_on_replay():
    with pytest.raises(MarkupConfirmationError):
        invoke(adapter(received_at=moment(NOW+300)), Cursor((NOW+300,NOW+300)))


def test_cancellation_token_or_wrong_binding_key_never_confirms():
    data=callback(); data['callback_query']['data'] = token()
    with pytest.raises(MarkupConfirmationError): invoke(adapter(data))
    with pytest.raises(MarkupConfirmationError):
        invoke(adapter(bindings=EditBindings(b'different-synthetic-key'*3)))


@pytest.mark.parametrize('reused,commit_error', [(False,False),(True,False),(False,True)])
def test_registrar_composition_commits_once_or_reports_unknown_no_retry(reused, commit_error):
    conn=ApprovalConnection(reused=reused, commit_error=commit_error)
    conn.rows[5:5] = [(moment(NOW+.1),), (moment(NOW+.1),)]
    factory_calls=[]; receipt_calls=[]
    def factory(): factory_calls.append(1); return conn
    owner=PostgresMarkupApprovalOwner(factory,
        decision_authenticator=adapter(calls=receipt_calls))
    if commit_error:
        with pytest.raises(CancellationMarkupError, match='^markup_approval_registration_unknown$'):
            owner.register(enabled=True, approval_id=target().approval_id, **fixture()[0])
    else:
        assert owner.register(enabled=True, approval_id=target().approval_id, **fixture()[0]) == dict(
            status='markup_approval_recorded', reused=reused, execution_authorized=False)
    assert factory_calls == [1] and len(receipt_calls) == 1
    assert sum(sql.strip().startswith('insert') for sql,_ in conn.calls) == (0 if reused else 1)
    assert all(not sql.strip().startswith(('update','delete')) for sql,_ in conn.calls)


def test_authenticated_but_wrong_prompt_rolls_back_without_insert():
    conn=ApprovalConnection(); conn.rows[5:5] = [(moment(NOW+.1),)]
    data=callback(); data['callback_query']['message']['message_id'] += 1
    owner=PostgresMarkupApprovalOwner(lambda: conn, decision_authenticator=adapter(data))
    with pytest.raises(CancellationMarkupError):
        owner.register(enabled=True, approval_id=target().approval_id, **fixture()[0])
    assert conn.exits[0] is not None
    assert not any(sql.strip().startswith('insert') for sql,_ in conn.calls)


def test_other_button_domain_is_refused_before_receipt_lookup():
    calls=[]; cursor=Cursor(); data=callback()
    data['callback_query']['data'] = token()
    with pytest.raises(MarkupConfirmationError): invoke(adapter(data, calls=calls), cursor)
    assert not calls and not cursor.calls


@pytest.mark.parametrize('receipt', [True, {}, 'approved', object()])
def test_untrusted_boolean_or_missing_receipt_is_not_human_consent(receipt):
    with pytest.raises(MarkupConfirmationError): invoke(adapter(receipt=receipt))


def test_missing_reader_and_malformed_headers_fail_closed():
    cursor=Cursor()
    with pytest.raises(MarkupConfirmationError): invoke(adapter(receipt_reader=None), cursor)
    with pytest.raises(MarkupConfirmationError): invoke(adapter(headers=[None]), cursor)
    assert not cursor.calls


def test_owner_exception_is_fixed_and_not_reflected():
    def fail(**kwargs): raise RuntimeError('synthetic-private-provider-details')
    with pytest.raises(MarkupConfirmationError, match='^markup_confirmation_refused$'):
        invoke(adapter(receipt_reader=fail))


def test_different_click_timestamp_cannot_overwrite_recorded_approval():
    conn=ApprovalConnection(reused=True)
    conn.rows[5:5] = [(moment(NOW+.1),), (moment(NOW+.1),)]
    owner=PostgresMarkupApprovalOwner(lambda: conn,
        decision_authenticator=adapter(received_at=moment(NOW+.05)))
    with pytest.raises(CancellationMarkupError):
        owner.register(enabled=True, approval_id=target().approval_id, **fixture()[0])
    assert conn.exits[0] is not None
    assert not any(sql.strip().startswith(('insert','update','delete')) for sql,_ in conn.calls)
