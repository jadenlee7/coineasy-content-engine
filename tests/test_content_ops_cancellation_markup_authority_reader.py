from dataclasses import astuple, replace
from pathlib import Path
import re

import pytest

from core.content_ops.cancellation_markup_authority_reader import PostgresMarkupAuthorityReader
from test_content_ops_cancellation_markup_authority import fixture
from core.content_ops.cancellation_markup_authority import ExactCancellationMarkupAuthority
from core.content_ops.cancellation_markup_guard import PostgresCancellationMarkupGuard
from test_content_ops_cancellation_markup_guard import GuardConnection
from test_content_ops_cancellation_markup_courier import Rig


class Cursor:
    def __init__(self):
        _,_,e,a=fixture(); self.rows=[astuple(e),astuple(a)]; self.calls=[]
    def execute(self,sql,params): self.calls.append((sql,params))
    def fetchone(self): return self.rows.pop(0)


def call(cursor, **changes):
    identity,_,_,_=fixture()
    kwargs=dict(cursor=cursor,card_id=identity['plan'].card_id,
        attempt_id=identity['attempt_id'],actor_id=identity['actor_id'])
    kwargs.update(changes)
    return PostgresMarkupAuthorityReader(enabled=True)(**kwargs)


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_off_never_touches_cursor(enabled):
    cursor=Cursor()
    assert PostgresMarkupAuthorityReader(enabled=enabled)(cursor=cursor)==(None,None)
    assert cursor.calls==[]


def test_two_parameterized_reads_and_shared_locks_on_supplied_cursor():
    cursor=Cursor(); _,_,e,a=fixture()
    assert call(cursor)==(e,a)
    assert len(cursor.calls)==2
    assert all(sql.strip().startswith('select') and 'for share' in sql for sql,_ in cursor.calls)
    assert cursor.calls[0][1]==(e.card_id,)
    assert cursor.calls[1][1]==(a.card_id,a.attempt_id,a.actor_id)


@pytest.mark.parametrize('field',['card_id','attempt_id','actor_id'])
@pytest.mark.parametrize('value',[None,'invalid','00000000-0000-0000-0000-000000000000'])
def test_invalid_identity_prevents_queries(field,value):
    cursor=Cursor()
    with pytest.raises(ValueError,match='^cancellation_markup_owner_read_refused$'):
        call(cursor,**{field:value})
    assert cursor.calls==[]


@pytest.mark.parametrize('missing',[0,1])
def test_missing_evidence_or_approval_is_not_authority(missing):
    cursor=Cursor(); cursor.rows[missing]=None
    assert call(cursor)==(None,None)
    assert len(cursor.calls)==missing+1


def test_driver_errors_are_fixed_and_not_retried():
    cursor=Cursor()
    def failed(sql,params): cursor.calls.append(1); raise RuntimeError('private connection detail')
    cursor.execute=failed
    with pytest.raises(ValueError,match='^cancellation_markup_owner_read_refused$'): call(cursor)
    assert cursor.calls==[1]


def test_proposal_has_no_issuer_grants_or_public_writes():
    root=Path(__file__).resolve().parents[1]
    code=re.sub(r'--[^\n]*','',(root/'supabase/proposals/content_ops_button_markup_authority.sql').read_text()).lower()
    assert code.count('force row level security')==2
    assert not re.search(r'\bgrant\b|security definer|create policy|insert into|update public\.',code)
    assert "active boolean not null default false" in code
    assert "old.active and not new.active" in code
    assert "(to_jsonb(old)-'active')=(to_jsonb(new)-'active')" in code
    assert code.count('before update or delete')==2
    assert 'card_id uuid not null unique' in code and 'attempt_id uuid not null unique' in code
    assert "action='append_cancellation_markup@1'" in code


@pytest.mark.parametrize('scenario', ['valid','missing_evidence','missing_approval',
    'inactive','changed_plan','revoked_between_checks','changed_original'])
def test_reader_verifier_guard_and_courier_chain(scenario):
    rig=Rig(); _,_,evidence,approval=fixture(); connections=[]
    def connect():
        conn=GuardConnection()
        e,a=evidence,approval
        if scenario=='inactive' or (scenario=='revoked_between_checks' and connections):
            a=replace(a,active=False)
        if scenario=='changed_plan': a=replace(a,plan_seal='0'*64)
        if scenario=='changed_original': e=replace(e,text_sha256='0'*64)
        # Guard reads locks/target/time, then the reader consumes these exact rows
        # on the same cursor, then guard performs its post-authority clock check.
        conn.rows[4:4]=[None if scenario=='missing_evidence' else astuple(e),
                        None if scenario=='missing_approval' else astuple(a)]
        connections.append(conn)
        return conn
    reader=PostgresMarkupAuthorityReader(enabled=True)
    guard=PostgresCancellationMarkupGuard(connect,enabled=True,
        authorize_plan=ExactCancellationMarkupAuthority(reader,enabled=True))
    def transport(*,body):
        assert connections[-1].exits==[]
        rig.events.append('transport')
        return rig.reply
    rig.edit_reply_markup=transport
    result=rig.run(guard=guard)
    expected=('response_matched' if scenario=='valid' else
              'unknown' if scenario=='revoked_between_checks' else 'blocked')
    assert result=={'status':expected,'execution_authorized':False}
    assert rig.events.count('transport')==(1 if scenario=='valid' else 0)
    assert rig.events.count('reserve')==(1 if scenario in ('valid','revoked_between_checks') else 0)
    assert len(connections)==(2 if scenario in ('valid','revoked_between_checks') else 1)
    for conn in connections:
        evidence_reads=[sql for sql,_ in conn.calls if 'from private.content_ops_button_control_evidence' in sql]
        approval_reads=[sql for sql,_ in conn.calls if 'from private.content_ops_button_markup_approvals' in sql]
        assert len(evidence_reads)==1
        assert len(approval_reads)==(0 if scenario=='missing_evidence' else 1)
        assert all('for share' in sql for sql in evidence_reads+approval_reads)
