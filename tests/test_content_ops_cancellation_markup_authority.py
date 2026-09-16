"""Synthetic owner evidence only. No approval issuing, importing or network."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta

import pytest

from core.content_ops.cancellation_markup import encode
from core.content_ops.cancellation_markup_authority import (
    ExactCancellationMarkupAuthority, OriginalControlEvidence, MarkupExecutionApproval,
)
from core.content_ops.cancellation_markup_guard import PostgresCancellationMarkupGuard
from test_content_ops_cancellation_markup import previous_markup
from test_content_ops_cancellation_markup_owner import args
from test_content_ops_cancellation_markup_guard import GuardConnection
from test_content_ops_cancellation_markup_courier import Rig
from test_content_ops_review_cancellation import records, NOW, uid


def fixture():
    identity=args(); identity.pop('enabled')
    card=records()[1]; plan=identity['plan']
    evidence=OriginalControlEvidence(plan.card_id,card['bindings']['card_receipt'],
        card['bindings']['parent_binding'],plan.bot_id,plan.chat_id,plan.message_id,
        plan.thread_id,datetime.fromisoformat(card['delivered_at']),plan.message_date,
        plan.text_sha256,plan.entities_json,encode(previous_markup()))
    approval=MarkupExecutionApproval(uid(99),identity['attempt_id'],plan.card_id,
        identity['actor_id'],identity['bindings'].digest('human',plan.bot_id,identity['human_id']),
        plan.seal,evidence.receipt_sha256,'append_cancellation_markup@1',
        datetime.fromtimestamp(NOW,timezone.utc),datetime.fromtimestamp(plan.expires_at,timezone.utc),True)
    return identity,card,evidence,approval


def check(evidence=None,approval=None,**changes):
    identity,card,e,a=fixture(); calls=[]
    def reader(**kwargs): calls.append(kwargs); return evidence or e,approval or a
    kwargs=dict(identity,cursor=object(),locked_card=card,now=datetime.fromtimestamp(NOW+.1,timezone.utc))
    kwargs.update(changes)
    return ExactCancellationMarkupAuthority(reader,enabled=True)(**kwargs),calls


def test_exact_evidence_and_action_verified_without_exposing_private_values():
    accepted,calls=check()
    assert accepted is True and len(calls)==1
    assert set(calls[0])=={'cursor','card_id','attempt_id','actor_id'}
    _,_,evidence,approval=fixture()
    assert evidence.receipt_sha256 not in repr(evidence)
    assert approval.approval_id not in repr(approval)


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_off_no_owner_read_even_for_invalid_inputs(enabled):
    calls=[]
    assert ExactCancellationMarkupAuthority(lambda **kw:calls.append(1),enabled=enabled)(plan=object()) is False
    assert calls==[]


@pytest.mark.parametrize('field,value',[
    ('card_id',uid(66)),('receipt_sha256','0'*64),('parent_binding_sha256','0'*64),
    ('bot_id',999),('bot_id',True),('chat_id',-999),('message_id',999),
    ('thread_id',True),('message_date',NOW),('text_sha256','0'*64),
    ('entities_json','[]'),('markup_json','{"inline_keyboard":[]}'),
    ('markup_json','{"inline_keyboard":[],"inline_keyboard":[]}'),
    ('delivered_at',datetime.fromtimestamp(NOW,timezone.utc)),
    ('delivered_at',datetime.fromtimestamp(NOW)),
])
def test_original_message_and_keyboard_changes_refused(field,value):
    _,_,e,_=fixture()
    accepted,calls=check(evidence=replace(e,**{field:value}))
    assert accepted is False and len(calls)==1


@pytest.mark.parametrize('field,value',[
    ('approval_id','00000000-0000-0000-0000-000000000000'),
    ('attempt_id',uid(66)),('card_id',uid(66)),('actor_id',uid(66)),
    ('human_binding','0'*64),('plan_seal','0'*64),('original_receipt_sha256','0'*64),
    ('action','publish'),('action','approve'),('active',False),('active',1),
    ('approved_at',datetime.fromtimestamp(NOW+1,timezone.utc)),
    ('approved_at',datetime.fromtimestamp(NOW-1,timezone.utc)),
    ('approved_at',datetime.fromtimestamp(NOW)),
    ('expires_at',datetime.fromtimestamp(NOW+299,timezone.utc)),
    ('expires_at',datetime.fromtimestamp(NOW+301,timezone.utc)),
])
def test_approval_bound_to_exact_attempt_human_action_and_window(field,value):
    _,_,_,approval=fixture()
    accepted,_=check(approval=replace(approval,**{field:value}))
    assert accepted is False


@pytest.mark.parametrize('now',[datetime.fromtimestamp(NOW+300,timezone.utc),
    datetime.fromtimestamp(NOW),None])
def test_expired_or_invalid_clock_prevents_owner_read(now):
    accepted,calls=check(now=now)
    assert accepted is False and calls==[]


def test_owner_record_absence_or_error_is_not_approval():
    identity,card,_,_=fixture()
    for reader in (lambda **kwargs:(None,None),lambda **kwargs:({},{})):
        assert ExactCancellationMarkupAuthority(reader,enabled=True)(**identity,
            cursor=object(),locked_card=card,now=datetime.fromtimestamp(NOW,timezone.utc)) is False
    def failed(**kwargs): raise RuntimeError('private owner detail')
    assert ExactCancellationMarkupAuthority(failed,enabled=True)(**identity,
        cursor=object(),locked_card=card,now=datetime.fromtimestamp(NOW,timezone.utc)) is False


def test_owner_projection_is_reread_and_revocation_is_not_cached():
    identity,card,evidence,approval=fixture(); state=[approval]; calls=[]
    def reader(**kwargs): calls.append(1); return evidence,state[0]
    authority=ExactCancellationMarkupAuthority(reader,enabled=True)
    kwargs=dict(identity,cursor=object(),locked_card=card,now=datetime.fromtimestamp(NOW,timezone.utc))
    assert authority(**kwargs) is True
    state[0]=replace(approval,active=False)
    assert authority(**kwargs) is False and len(calls)==2


def test_timezone_equivalent_exact_instants_are_accepted():
    _,_,e,a=fixture(); zone=timezone(timedelta(hours=9))
    assert check(evidence=replace(e,delivered_at=e.delivered_at.astimezone(zone)),
        approval=replace(a,approved_at=a.approved_at.astimezone(zone),
                         expires_at=a.expires_at.astimezone(zone)))[0] is True


@pytest.mark.parametrize('revoke_on_second',[False,True])
def test_guard_uses_same_transaction_cursor_and_checks_again_before_transport(revoke_on_second):
    rig=Rig(); _,_,evidence,approval=fixture(); owner_calls=[]; connections=[]
    def reader(**kwargs):
        owner_calls.append(kwargs)
        assert 'clock_timestamp' in connections[-1].calls[-1][0]
        return evidence,replace(approval,active=not(revoke_on_second and len(owner_calls)==2))
    def connection():
        result=GuardConnection(); connections.append(result); return result
    authority=ExactCancellationMarkupAuthority(reader,enabled=True)
    guard=PostgresCancellationMarkupGuard(connection,enabled=True,authorize_plan=authority)
    # Rig's held flag describes its old in-memory guard, not this DB guard.
    def transport(**kwargs):
        rig.events.append('transport')
        assert connections[-1].exits==[]
        return rig.reply
    rig.edit_reply_markup=transport
    result=rig.run(guard=guard)
    assert result['status']==('unknown' if revoke_on_second else 'response_matched')
    assert len(owner_calls)==2
    assert rig.events.count('transport')==(0 if revoke_on_second else 1)
    assert all(call['cursor'] is not None for call in owner_calls)
