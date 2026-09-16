"""Synthetic independent prompt permissions; no actual operator approval."""
from contextlib import contextmanager
from dataclasses import astuple, replace
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
import hashlib

import pytest

from core.content_ops.confirmation_send_authority import (
    ConfirmationSendPermission, ExactConfirmationSendAuthority, PostgresConfirmationSendReader,
)
from core.content_ops.confirmation_send_courier import run_confirmation_send_once
from core.content_ops.confirmation_delivery_owner import confirmation_send_request
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_cancellation_markup_confirmation import target, moment
from test_content_ops_confirmation_delivery_owner import DELIVERY
from test_content_ops_review_cancellation import NOW, uid


def permission():
    i = fixture()[0]; t = target()
    return ConfirmationSendPermission(uid(909),DELIVERY,t.card_id,t.actor_id,t.human_binding,
        i['bindings'].digest('confirmation-delivery-target@1',*astuple(t)),
        hashlib.sha256(confirmation_send_request(t,i['plan'],i['bindings'])).hexdigest(),
        'send_private_confirmation@1',moment(),moment(t.expires_at),True)


def verify(p=None,e=None,**changes):
    i,card,evidence,_ = fixture(); calls = []
    def reader(**kw): calls.append(kw); return e if e is not None else evidence,p if p is not None else permission()
    opts = dict(cursor=object(),locked_card=card,now=moment(NOW+.1),**i); opts.update(changes)
    auth = ExactConfirmationSendAuthority(enabled=True,target=target(),delivery_id=DELIVERY,reader=reader)
    return auth(**opts),calls


def test_independent_send_action_exact_request_and_target():
    ok,calls = verify()
    assert ok and len(calls)==1 and calls[0]['delivery_id']==DELIVERY
    assert permission().permission_id not in repr(permission())


@pytest.mark.parametrize('field,value', [('permission_id','bad'),('delivery_id',uid(99)),
    ('card_id',uid(99)),('actor_id',uid(99)),('human_binding','e'*64),
    ('target_binding','e'*64),('request_sha256','e'*64),('action','append_cancellation_markup@1'),
    ('action','publish'),('action','approve'),('active',False),('active',1),
    ('authorized_at',moment(NOW-1)),('authorized_at',moment(NOW+1)),
    ('authorized_at',moment().replace(tzinfo=None)),('expires_at',moment(NOW+301)),
    ('expires_at',moment(NOW+299)),('expires_at',None)])
def test_permission_cannot_change_scope_or_extend_window(field,value):
    assert verify(p=replace(permission(),**{field:value}))[0] is False


@pytest.mark.parametrize('field,value', [('receipt_sha256','e'*64),('parent_binding_sha256','e'*64),
    ('bot_id',999),('chat_id',-999),('message_id',999),('thread_id',1),
    ('text_sha256','e'*64),('entities_json','[{}]'),('markup_json','{}')])
def test_original_control_receipt_is_independently_required(field,value):
    assert verify(e=replace(fixture()[2],**{field:value}))[0] is False


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_off_has_no_authority_read(enabled):
    def fail(**kw): raise AssertionError()
    assert ExactConfirmationSendAuthority(enabled=enabled,reader=fail)() is False
    assert PostgresConfirmationSendReader(enabled=enabled)(cursor=None,card_id=None,delivery_id=None,actor_id=None)==(None,None)


def test_markup_approval_is_not_prompt_permission_and_revocation_not_cached():
    assert verify(p=fixture()[3])[0] is False
    assert verify()[0] is True
    assert verify(p=replace(permission(),active=False))[0] is False
    assert verify(now=moment(NOW+300))[0] is False
    card = dict(fixture()[1],active=False)
    assert verify(locked_card=card)[0] is False


class Cursor:
    def __init__(self,rows): self.rows,self.calls = rows,[]
    def execute(self,*args): self.calls.append(args)
    def fetchone(self): return self.rows.pop(0)


def test_reader_uses_only_guard_cursor_and_share_locks():
    cursor = Cursor([astuple(fixture()[2]),astuple(permission())])
    result = PostgresConfirmationSendReader(enabled=True)(cursor=cursor,
        card_id=target().card_id,delivery_id=DELIVERY,actor_id=target().actor_id)
    assert result==(fixture()[2],permission()) and len(cursor.calls)==2
    assert all('for share' in sql for sql,_ in cursor.calls)
    assert not any('markup_approvals' in sql for sql,_ in cursor.calls)


class Durable:
    """Thread-safe synthetic durable owner state shared across courier instances."""
    def __init__(self): self.lock,self.saved = Lock(),False


class Rig:
    def __init__(self,durable=None):
        self.target,self.delivery_id = target(),DELIVERY
        self.durable = durable or Durable()
        self.identity = fixture()[0]
        self.events,self.valid,self.held = [],[True,True],False
        self.error,self.changed,self.times = None,None,iter([moment(NOW+.1),moment(NOW+.2)])
        self.reserve_reply = self.import_reply = None

    @contextmanager
    def hold(self,**identity):
        assert not self.held
        self.held = True; self.events.append('lock')
        try:
            yield self
            if self.error=='unlock': raise RuntimeError('private error')
        finally:
            self.held = False; self.events.append('unlock')

    def validate(self,**kw):
        assert self.held
        self.events.append('validate'); return self.valid.pop(0)

    def reserve(self,**kw):
        assert not self.held
        self.events.append('reserve')
        with self.durable.lock:
            new = not self.durable.saved
            self.durable.saved = True
        if self.error=='reserve': raise RuntimeError('lost commit ack')
        return self.reserve_reply or dict(status='unknown',new_attempt=new,execution_authorized=False)

    def send_confirmation(self,*,delivery_id,request,lease):
        assert lease is self
        assert self.held and delivery_id==DELIVERY
        assert request==confirmation_send_request(self.target,self.identity['plan'],self.identity['bindings'])
        self.events.append('send')
        if self.error=='send': raise TimeoutError('private provider text')
        return dict(status='confirmation_source_recorded',reused=False,execution_authorized=False)

    def consume(self,**kw):
        assert not self.held
        self.events.append('consume')
        if self.error=='consume': raise RuntimeError('lost dispatch commit ack')
        return dict(status='confirmation_dispatch_consumed',new_dispatch=True,execution_authorized=False)

    def ingest(self,**kw):
        assert not self.held
        self.events.append('ingest')
        if self.error=='ingest': raise RuntimeError('private db error')
        return self.import_reply or dict(status='confirmation_recorded',reused=False,execution_authorized=False)

    def run(self,**changes):
        opts = dict(enabled=True,target=self.target,delivery_id=self.delivery_id,
            guard=self,owner=self,dispatch_owner=self,transport=self,clock=lambda:next(self.times),**self.identity)
        opts.update(changes)
        return run_confirmation_send_once(**opts)


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_courier_off_has_zero_io(enabled):
    r = Rig(); assert r.run(enabled=enabled,plan=object()) is None and not r.events


def test_guard_reserve_revalidate_send_release_import_order():
    r = Rig()
    assert r.run()==dict(status='confirmation_recorded',execution_authorized=False)
    assert r.events==['lock','validate','unlock','reserve','consume','lock','validate','send','unlock','ingest']


@pytest.mark.parametrize('index',[0,1])
def test_revoked_permission_at_either_check_prevents_send(index):
    r = Rig(); r.valid[index] = False
    assert r.run()['status']==('blocked' if index==0 else 'unknown')
    assert 'send' not in r.events


@pytest.mark.parametrize('error',['reserve','consume','send','ingest','unlock'])
def test_uncertain_outcome_never_retries(error):
    r = Rig(); r.error=error
    assert r.run()['status'] in ('blocked','unknown')
    assert r.events.count('send')<=1 and r.events.count('reserve')<=1
    if r.durable.saved:
        restarted = Rig(r.durable)
        assert restarted.run()['status']=='existing_unknown'
        assert 'send' not in restarted.events


def test_eight_restarted_couriers_share_one_durable_attempt():
    durable = Durable(); rigs = [Rig(durable) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(lambda r:r.run(),rigs))
    assert sum(x['status']=='confirmation_recorded' for x in results)==1
    assert sum(x['status']=='existing_unknown' for x in results)==7
    assert sum(r.events.count('send') for r in rigs)==1


@pytest.mark.parametrize('value',[{},True,dict(status='unknown',new_attempt=1,execution_authorized=False),
    dict(status='response_matched',new_attempt=True,execution_authorized=False),
    dict(status='unknown',new_attempt=True,execution_authorized=True)])
def test_malformed_or_authorizing_reservation_does_not_send(value):
    r=Rig(); r.reserve_reply=value
    # Empty/bool receipts need a direct callable to avoid Rig's fixture default.
    r.reserve=lambda **kw:value
    assert r.run()['status']=='unknown' and 'send' not in r.events


def test_changed_target_or_delivery_guard_cannot_be_used():
    r=Rig()
    assert r.run(delivery_id=uid(77))['status']=='blocked' and not r.events
    assert r.run(target=replace(target(),approval_id=uid(77)))['status']=='blocked' and not r.events


@pytest.mark.parametrize('value', [None,{},True,
    dict(status='confirmation_dispatch_consumed',new_dispatch=1,execution_authorized=False),
    dict(status='confirmation_dispatch_consumed',new_dispatch=True,execution_authorized=True)])
def test_missing_or_uncertain_dispatch_commit_never_sends(value):
    r=Rig(); r.consume=lambda **kw:value
    assert r.run()['status']=='unknown' and 'send' not in r.events


def test_existing_dispatch_cannot_be_replayed_even_with_fresh_reservation_reply():
    r=Rig(); r.consume=lambda **kw:dict(status='confirmation_dispatch_consumed',
        new_dispatch=False,execution_authorized=False)
    assert r.run()['status']=='existing_dispatch_consumed' and 'send' not in r.events


def test_no_dispatch_dependency_fails_closed_before_io():
    r=Rig()
    assert r.run(dispatch_owner=None)['status']=='blocked' and not r.events
