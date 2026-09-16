"""Synthetic-only button installation contract and edited callback coverage."""
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from uuid import UUID

import pytest

from core.content_ops import cancellation_markup as markup
from core.content_ops import review_cancellation as cancel
from core.content_ops.prompt_reservation import card_parent_binding
from test_content_ops_review_cancellation import (
    records, signer, NOW, BOT, ROOM, MESSAGE, BINDINGS, Owner, webhook, update,
    Connection, revoked,
)


def previous_markup():
    # Existing controller token shape; this helper must NOT bless its MAC.
    body = b'\x01' + UUID(records()[0]['content_version_id']).bytes
    body += (NOW+300).to_bytes(4, 'big') + b'h' + b'0'*16
    return {'inline_keyboard': [[{'text':'보류', 'callback_data':
        base64.urlsafe_b64encode(body).decode().rstrip('=')}]]}


def prepare(**overrides):
    review, card = records()
    args = dict(enabled=True, review=review, card=card, bindings=BINDINGS, signer=signer(),
        bot_id=BOT, chat_id=ROOM, message_id=MESSAGE, thread_id=None,
        control_text='검수 https://example.com', control_entities=[{'type':'url','offset':3,'length':19}],
        message_date=NOW-5, existing_markup=previous_markup(), now=NOW, expires_at=NOW+300)
    args.update(overrides)
    return markup.prepare_cancellation_markup(**args)


def request(plan, now=NOW):
    return markup.cancellation_markup_request(enabled=True, plan=plan, bindings=BINDINGS, now=now)


def response(plan):
    return {'ok':True, 'result':{'message_id':MESSAGE, 'date':NOW-5, 'edit_date':NOW,
        'chat':{'id':ROOM,'type':'supergroup'}, 'from':{'id':BOT,'is_bot':True},
        'text':'검수 https://example.com', 'entities':[{'type':'url','offset':3,'length':19}],
        'reply_markup':request(plan)['body']['reply_markup']}}


def validate(plan, data=None, **overrides):
    args = dict(enabled=True, plan=plan, bindings=BINDINGS, http_status=200,
                raw_response=json.dumps(response(plan) if data is None else data).encode(), observed_at=NOW)
    args.update(overrides)
    return markup.validate_cancellation_markup_response(**args)


def test_off_ignores_all_inputs():
    assert markup.prepare_cancellation_markup(review=object()) is None
    assert markup.cancellation_markup_request(plan=object()) is None
    assert markup.validate_cancellation_markup_response(raw_response=object()) is None


def test_delivery_microseconds_are_preserved_with_second_resolution_observation():
    review, card = records()
    card['delivered_at'] = datetime.fromtimestamp(NOW+0.123456, timezone.utc).isoformat()
    card['bindings']['parent_binding'] = card_parent_binding(BINDINGS, review, card)
    before = deepcopy(card)
    plan = prepare(card=card, message_date=NOW)
    assert plan.message_date == NOW and card == before


def test_exact_topic_response_and_missing_topic_refusal():
    review, card = records(); card['bindings']['thread_id'] = 37
    card['bindings']['parent_binding'] = card_parent_binding(BINDINGS, review, card)
    plan = prepare(card=card, thread_id=37)
    data = response(plan); data['result']['message_thread_id'] = 37
    assert validate(plan,data)['execution_authorized'] is False
    del data['result']['message_thread_id']
    with pytest.raises(markup.CancellationMarkupError): validate(plan,data)


def test_preserves_existing_buttons_immutable_plan_and_exact_target():
    original = previous_markup(); before = deepcopy(original)
    plan = prepare(existing_markup=original)
    req = request(plan)
    assert req['method'] == 'editMessageReplyMarkup'
    assert set(req['body']) == {'chat_id','message_id','reply_markup'}
    rows = req['body']['reply_markup']['inline_keyboard']
    assert rows[:-1] == before['inline_keyboard'] and original == before
    assert rows[-1][0]['text'] == '⛔ 이 검수 취소'
    target = cancel.cancellation_target(*records(), BINDINGS)
    assert signer().verify(rows[-1][0]['callback_data'], target, now=NOW) == plan.card_id
    original['inline_keyboard'].clear(); rows.clear()
    assert len(request(plan)['body']['reply_markup']['inline_keyboard']) == 2
    assert 'example.com' not in repr(plan) and plan.card_id not in repr(plan)


@pytest.mark.parametrize('field,value', [('bot_id',102),('chat_id',-302),('chat_id','-301'),
    ('message_id',11),('message_id',True),('thread_id',True),('thread_id',7),
    ('message_date',NOW+1),('message_date',NOW),('control_text',''),('expires_at',NOW+1801)])
def test_invalid_or_cross_card_preparation(field, value):
    with pytest.raises(markup.CancellationMarkupError, match='^cancellation_markup_unconfirmed$'):
        prepare(**{field:value})


def test_inactive_unbound_card_and_duplicate_cancel_refuse():
    review, card = records(); card['active'] = False
    with pytest.raises(markup.CancellationMarkupError): prepare(card=card)
    card['active'] = True; card['bindings']['parent_binding'] = '0'*64
    with pytest.raises(markup.CancellationMarkupError): prepare(card=card)
    existing = request(prepare())['body']['reply_markup']
    with pytest.raises(markup.CancellationMarkupError): prepare(existing_markup=existing)


@pytest.mark.parametrize('bad', [None, {}, {'inline_keyboard':{}}, {'inline_keyboard':[[]]},
    {'inline_keyboard':[[{'text':'link','url':'https://example.com'}]]},
    {'inline_keyboard':[[{'text':'bad','callback_data':'a'*51}]]}])
def test_untrusted_markup_shapes_refuse(bad):
    with pytest.raises(markup.CancellationMarkupError): prepare(existing_markup=bad)


@pytest.mark.parametrize('field,value', [('chat_id',-400),('message_id',99),('started_at',NOW-1),
    ('expires_at',NOW+400),('text_sha256','0'*64),('entities_json','[]'),('request_json','{}')])
def test_modified_plan_cannot_render_or_match(field, value):
    plan = replace(prepare(), **{field:value})
    with pytest.raises(markup.CancellationMarkupError): request(plan)


def test_no_freshening_expired_plan_and_unknown_not_modified():
    plan = prepare()
    with pytest.raises(markup.CancellationMarkupError): request(plan, NOW+300)
    with pytest.raises(markup.CancellationMarkupError): validate(plan, http_status=400)
    with pytest.raises(markup.CancellationMarkupError): validate(plan, {'ok':True,'result':True})
    with pytest.raises(markup.CancellationMarkupError): validate(plan, observed_at=NOW+300)


def test_exact_response_is_only_matched_not_installed_or_authorized():
    plan = prepare(); result = validate(plan)
    assert set(result) == {'status','card_id','receipt_sha256','execution_authorized'}
    assert result['status'] == 'markup_response_matched' and result['execution_authorized'] is False
    assert len(result['receipt_sha256']) == 64


@pytest.mark.parametrize('field,value', [('message_id',11),('message_id',True),('date',NOW),
    ('edit_date',True),('edit_date',NOW-1),('edit_date',NOW+1),('message_thread_id',True),
    ('message_thread_id',7),('text','changed'),('reply_markup',{}),('entities',[]),
    ('forward_origin',{}),('rich_message',{}),('business_connection_id','x')])
def test_response_mismatch_never_matches(field,value):
    plan = prepare(); data = response(plan); data['result'][field] = value
    with pytest.raises(markup.CancellationMarkupError): validate(plan,data)


@pytest.mark.parametrize('path,value', [('chat',{'id':ROOM,'type':'channel'}),
    ('chat',{'id':ROOM,'type':'supergroup','username':'public'}),
    ('from',{'id':BOT+1,'is_bot':True}), ('from',{'id':BOT,'is_bot':False})])
def test_response_identity_mismatch(path,value):
    plan = prepare(); data = response(plan); data['result'][path] = value
    with pytest.raises(markup.CancellationMarkupError): validate(plan,data)


def test_response_duplicate_keys_and_private_error_are_sanitized():
    plan = prepare()
    with pytest.raises(markup.CancellationMarkupError, match='^cancellation_markup_unconfirmed$'):
        validate(plan, raw_response=b'{"ok":true,"ok":false,"private":"secret"}')


def test_installed_shape_click_reaches_database_adapter_without_send():
    plan = prepare(); data = response(plan)
    assert validate(plan,data)['execution_authorized'] is False
    callback = update(); callback['callback_query']['message'] = data['result']
    callback['callback_query']['data'] = data['result']['reply_markup']['inline_keyboard'][-1][0]['callback_data']
    connection = Connection()
    assert webhook(cancel.PostgresCardCancellationOwner(lambda: connection), callback) == revoked()
    assert len(connection.statements) == 5 and connection.exits == [None]


@pytest.mark.parametrize('edited', [True,None,0,NOW-6,NOW+1,'now'])
def test_malformed_edit_date_refused_before_owner(edited):
    data = update(); data['callback_query']['message']['edit_date'] = edited
    owner = Owner()
    with pytest.raises(cancel.CancellationError, match='^review_cancellation_invalid$'): webhook(owner,data)
    assert owner.calls == []


def test_edited_wrong_card_message_still_refused():
    callback = update(); callback['callback_query']['message'].update(edit_date=NOW, message_id=MESSAGE+1)
    connection = Connection()
    with pytest.raises(cancel.CancellationError, match='^review_cancellation_outcome_unknown$'):
        webhook(cancel.PostgresCardCancellationOwner(lambda: connection), callback)
    assert connection.exits[0] is not None
