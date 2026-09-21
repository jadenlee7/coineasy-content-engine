"""A stored action selects exactly ONE owner; failure never falls back."""
from unittest.mock import Mock
import pytest
from core.content_ops.private_reply_owner import PostgresPrivateReplyOwner
from core.content_ops.review_ingress import ReviewIngressError
from test_content_ops_review_edit_ingress import BINDINGS, receipt
from test_content_ops_banner_feedback import Owner as BannerFixture
import json
from test_content_ops_review_edit_ingress import update, POLICY, NOW


class DB:
    autocommit=False
    def __init__(self,rows): self.rows=rows; self.reads=0
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def cursor(self): return self
    def execute(self,*args): self.reads+=1
    def fetchall(self): return self.rows


def run(owner,**overrides):
    args=dict(enabled=True,raw_body=json.dumps(update()).encode(),
        headers=[('Content-Type','application/json'),('X-Telegram-Bot-Api-Secret-Token',POLICY.webhook_secret)],
        policy=POLICY,bindings=BINDINGS,now=NOW)
    args.update(overrides)
    return owner.handle_update(**args)


@pytest.mark.parametrize('action',['edit_telegram','edit_x','edit_banner'])
def test_only_registered_action_owner_called(action):
    db=DB([(action,)])
    owner=PostgresPrivateReplyOwner(lambda:db,bindings=BINDINGS)
    owner._copy=Mock(); owner._copy.save_edit_reply.return_value=receipt()
    owner._banner=Mock(); owner._banner.save_banner_feedback.return_value=BannerFixture().result
    result=run(owner)
    assert result['execution_authorized'] is False and db.reads==1
    assert owner._copy.save_edit_reply.call_count==(action!='edit_banner')
    assert owner._banner.save_banner_feedback.call_count==(action=='edit_banner')


@pytest.mark.parametrize('action',['edit_x','edit_banner'])
def test_unknown_selected_owner_no_fallback_or_retry(action):
    owner=PostgresPrivateReplyOwner(lambda:DB([(action,)]),bindings=BINDINGS)
    owner._copy=Mock(); owner._banner=Mock()
    owner._copy.save_edit_reply.side_effect=RuntimeError('private')
    owner._banner.save_banner_feedback.side_effect=RuntimeError('private')
    with pytest.raises(ReviewIngressError,match='^review_edit_outcome_unknown$'): run(owner)
    assert owner._copy.save_edit_reply.call_count+owner._banner.save_banner_feedback.call_count==1


@pytest.mark.parametrize('rows',[[],[('edit_x',),('edit_banner',)],[('approve',)]])
def test_missing_ambiguous_public_action_never_mutates(rows):
    owner=PostgresPrivateReplyOwner(lambda:DB(rows),bindings=BINDINGS)
    owner._copy=Mock(); owner._banner=Mock()
    with pytest.raises(ReviewIngressError): run(owner)
    assert not owner._copy.mock_calls and not owner._banner.mock_calls


def test_off_and_authentication_failure_no_database_reads():
    factory=Mock(side_effect=AssertionError('must not connect'))
    owner=PostgresPrivateReplyOwner(factory,bindings=BINDINGS)
    assert run(owner,enabled=False)['status']=='disabled'
    with pytest.raises(ReviewIngressError): run(owner,headers=[])
    factory.assert_not_called()
