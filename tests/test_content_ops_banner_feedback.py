"""Offline authenticated update boundary; no provider, Telegram or DB I/O."""
import json
from uuid import uuid4
import pytest

from core.content_ops.banner_feedback import handle_banner_reply
from core.content_ops.banner_regeneration import BannerError
from core.content_ops.review_ingress import ReviewIngressError
from test_content_ops_review_edit_ingress import update, POLICY, BINDINGS, NOW


class Owner:
    def __init__(self):
        self.events=[]
        self.result={'status':'banner_requested','job_id':str(uuid4()),'request_sha256':'a'*64,
                     'reused':False,'execution_authorized':False}
    def save_banner_feedback(self,event):
        self.events.append(event)
        return self.result


def call(owner,data=None,**overrides):
    args=dict(enabled=True,raw_body=json.dumps(data or update()).encode(),
        headers=[('Content-Type','application/json'),('X-Telegram-Bot-Api-Secret-Token',POLICY.webhook_secret)],
        policy=POLICY,bindings=BINDINGS,owner=owner,now=NOW)
    args.update(overrides)
    return handle_banner_reply(**args)


@pytest.mark.parametrize('enabled',[None,False,1,'true'])
def test_default_off(enabled):
    assert handle_banner_reply(enabled=enabled,owner=object())=={'status':'disabled','execution_authorized':False}


def test_bound_reply_receipt_and_no_execution():
    owner=Owner()
    assert call(owner)==owner.result
    event=owner.events[0]
    assert event.message_date==NOW-1 and event.prompt_date==NOW-5 and event.thread_id is None
    assert event.message_binding==BINDINGS.digest('prompt',POLICY.bot_id,POLICY.chat_id,31)


@pytest.mark.parametrize('value',['x'*601,'sk-'+'x'*32,'api_key=do-not-copy','\ud800',''])
def test_bad_feedback_zero_owner_calls(value):
    owner=Owner(); data=update(); data['message']['text']=value
    with pytest.raises((BannerError,ReviewIngressError)): call(owner,data)
    assert not owner.events


@pytest.mark.parametrize('field,value', [('status','published'),('execution_authorized',True),
    ('job_id','bad'),('request_sha256','g'*64),('reused',1),('extra','private')])
def test_no_optimistic_success_on_invalid_receipt(field,value):
    owner=Owner(); owner.result[field]=value
    with pytest.raises(BannerError,match='^banner_feedback_unknown$'): call(owner)
    assert len(owner.events)==1


def test_unknown_not_retried_and_error_redacted():
    class Unknown(Owner):
        def save_banner_feedback(self,event):
            self.events.append(event)
            raise RuntimeError('private connection material')
    owner=Unknown()
    with pytest.raises(BannerError,match='^banner_feedback_unknown$'): call(owner)
    assert len(owner.events)==1
