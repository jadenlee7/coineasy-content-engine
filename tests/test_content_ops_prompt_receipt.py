"""Synthetic transport-contract tests, no actual provider calls."""
from dataclasses import asdict, replace
import hashlib
import json

import pytest

from core.content_ops.prompt_receipt import PromptAttempt, PromptReceiptError, validate_prompt_response
from core.content_ops.review_edit_ingress import EditBindings

NOW = 1800000000
TEXT = '검수 문안을 이 메시지에 답장해주세요.'
BINDINGS = EditBindings(b'synthetic-receipt-key-no-live-secret-123')
ATTEMPT = PromptAttempt('11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222','33333333-3333-4333-8333-333333333333',
    1,'a'*64,'b'*64,'c'*64,hashlib.sha256(TEXT.encode()).hexdigest(),
    101,-(10**12+7),201,NOW-5,NOW+600)


def response():
    return {'ok':True,'result':{'message_id':31,'date':NOW-1,'text':TEXT,
        'chat':{'id':ATTEMPT.chat_id,'type':'supergroup'},'from':{'id':101,'is_bot':True}}}


def validate(raw=None, **kwargs):
    args = dict(enabled=True,attempt=ATTEMPT,http_status=200,
        raw_response=json.dumps(raw or response()).encode(),bindings=BINDINGS,observed_at=NOW)
    args.update(kwargs)
    return validate_prompt_response(**args)


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_disabled_does_not_inspect_inputs(enabled):
    assert validate_prompt_response(enabled=enabled,attempt=object(),raw_response=object()) is None


def test_validated_projection_matches_reply_bindings_without_private_data():
    result = validate()
    assert result.message_binding == BINDINGS.digest('prompt',101,ATTEMPT.chat_id,31)
    assert result.human_binding == BINDINGS.digest('human',101,201)
    assert result.id == ATTEMPT.attempt_id and result.provider_message_date == NOW-1
    rendered = json.dumps(asdict(result)) + repr(result) + repr(ATTEMPT)
    assert TEXT not in rendered and str(ATTEMPT.chat_id) not in rendered
    raw = response(); raw['result']['from']['first_name'] = 'Ignored display name'
    assert validate(raw) == result


@pytest.mark.parametrize('path,value',[
    (('ok',),False),(('result','chat','id'),-9),(('result','chat','type'),'channel'),
    (('result','chat','username'),'public'),(('result','from','id'),102),
    (('result','from','is_bot'),False),(('result','message_id'),True),
    (('result','message_id'),0),(('result','date'),NOW+1),(('result','date'),NOW-6),
    (('result','text'),TEXT+' changed'),(('result','text'),'\ud800'),
    (('result','forward_origin'),{}),(('result','sender_chat'),{}),
    (('result','edit_date'),NOW),(('result','reply_to_message'),{}),
    (('result','reply_markup'),{}),(('result','message_thread_id'),True),
    (('result','entities'),[{'type':'text_link'}]),(('result','photo'),[]),
])
def test_response_mismatch_is_fixed_unconfirmed(path,value):
    raw = response(); part=raw
    for k in path[:-1]: part=part[k]
    part[path[-1]]=value
    with pytest.raises(PromptReceiptError,match='^prompt_receipt_unconfirmed$'): validate(raw)


@pytest.mark.parametrize('body',[b'{"ok":true,"ok":false}',b'{"ok":NaN}',b'\xff',b'[]',b'x'*32769])
def test_malformed_response(body):
    with pytest.raises(PromptReceiptError): validate(raw_response=body)


@pytest.mark.parametrize('status',[True,201,302,400,429,500])
def test_only_exact_success_response(status):
    with pytest.raises(PromptReceiptError): validate(http_status=status)


@pytest.mark.parametrize('changes',[
    {'epoch':True},{'attempt_id':'invalid'},{'packet_receipt_sha256':''},
    {'expires_at':NOW},{'started_at':NOW+1},{'expires_at':NOW+1801},
    {'human_id':101},{'thread_id':True},
])
def test_invalid_or_expired_owner_attempt(changes):
    with pytest.raises(PromptReceiptError): validate(attempt=replace(ATTEMPT,**changes))


def test_version_packet_actor_action_changes_do_not_share_receipt_hash():
    baseline=validate().receipt_sha256
    for changes in ({'version_fingerprint':'d'*64},{'packet_receipt_sha256':'d'*64},
                    {'edit_action_key':'d'*64},{'epoch':2},{'human_id':202}):
        assert validate(attempt=replace(ATTEMPT,**changes)).receipt_sha256 != baseline


def test_forum_topic_exact_match_required():
    raw=response(); raw['result']['message_thread_id']=9
    assert validate(raw,attempt=replace(ATTEMPT,thread_id=9)).outcome=='sent'
    with pytest.raises(PromptReceiptError): validate(raw)
