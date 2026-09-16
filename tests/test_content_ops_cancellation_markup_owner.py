"""Offline ledger adapter contracts. Real SQL/concurrency lives in the local harness."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import pytest

from core.content_ops.cancellation_markup import CancellationMarkupError
from core.content_ops.cancellation_markup_owner import PostgresCancellationMarkupLedger
from test_content_ops_cancellation_markup import prepare, response
from test_content_ops_review_cancellation import records, signer, BINDINGS, NOW, HUMAN, ACTOR, uid


class Connection:
    autocommit=False
    def __init__(self, result=None, commit_error=False):
        self.rows=[records(),(result or dict(status='unknown',attempt_id=uid(90),
            card_id=uid(5),new_attempt=True,execution_authorized=False),)]
        self.calls=[]; self.exits=[]; self.commit_error=commit_error
    def __enter__(self): return self
    def __exit__(self,*args):
        self.exits.append(args[0])
        if args[0] is None and self.commit_error: raise RuntimeError('synthetic private commit detail')
    def cursor(self):
        parent=self
        class Cursor:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self,sql,params): parent.calls.append((sql,params))
            def fetchone(self): return parent.rows.pop(0)
        return Cursor()


def args():
    return dict(enabled=True,plan=prepare(),attempt_id=uid(90),actor_id=ACTOR,
                human_id=HUMAN,bindings=BINDINGS,signer=signer())


def record_args():
    result=args()
    result.update(http_status=200,raw_response=json.dumps(response(result['plan'])).encode(),
                  observed_at=datetime.fromtimestamp(NOW+0.5,timezone.utc))
    return result


def matched(reused=False):
    return dict(status='response_matched',attempt_id=uid(90),card_id=uid(5),
                reused=reused,execution_authorized=False)


def test_default_off_does_not_open_connection():
    def factory(): raise AssertionError('must not connect')
    ledger=PostgresCancellationMarkupLedger(factory)
    assert ledger.reserve(plan=object()) is None
    assert ledger.record_response(raw_response=object()) is None


def test_reserve_minimized_parameter_binding_and_committed_readback():
    connection=Connection(); kwargs=args()
    result=PostgresCancellationMarkupLedger(lambda:connection).reserve(**kwargs)
    assert result['status']=='unknown' and result['new_attempt'] is True
    assert result['execution_authorized'] is False
    assert len(connection.calls)==2 and connection.exits==[None]
    params=connection.calls[1][1]
    assert params[:3]==(uid(90),uid(5),ACTOR)
    assert params[4]==BINDINGS.digest('human',kwargs['plan'].bot_id,HUMAN)
    assert params[6]==kwargs['plan'].seal
    assert all(not isinstance(v,dict) for v in params)
    assert kwargs['plan'].request_json not in params


@pytest.mark.parametrize('reused',[False,True])
def test_record_exact_response_keeps_original_precise_observation(reused):
    connection=Connection(matched(reused)); kwargs=record_args()
    assert PostgresCancellationMarkupLedger(lambda:connection).record_response(**kwargs)==matched(reused)
    assert connection.calls[-1][1][-1]==kwargs['observed_at']
    assert connection.exits==[None]


@pytest.mark.parametrize('method',['reserve','record_response'])
@pytest.mark.parametrize('field,value',[('attempt_id','bad'),('actor_id','00000000-0000-0000-0000-000000000000'),
    ('human_id',True),('human_id',101),('signer',None),('bindings',None)])
def test_invalid_inputs_never_connect(method,field,value):
    kwargs=args() if method=='reserve' else record_args(); kwargs[field]=value
    calls=[]
    with pytest.raises(CancellationMarkupError,match='^cancellation_markup_ledger_unknown$'):
        getattr(PostgresCancellationMarkupLedger(lambda:calls.append(1)),method)(**kwargs)
    assert calls==[]


@pytest.mark.parametrize('value',[None,True,NOW,datetime.fromtimestamp(NOW), 'now'])
def test_response_requires_precise_timezone_aware_observation(value):
    kwargs=record_args(); kwargs['observed_at']=value; calls=[]
    with pytest.raises(CancellationMarkupError):
        PostgresCancellationMarkupLedger(lambda:calls.append(1)).record_response(**kwargs)
    assert calls==[]


def test_unmatched_response_leaves_reserved_unknown_without_db_call():
    kwargs=record_args(); kwargs['http_status']=400; calls=[]
    with pytest.raises(CancellationMarkupError):
        PostgresCancellationMarkupLedger(lambda:calls.append(1)).record_response(**kwargs)
    assert calls==[]


@pytest.mark.parametrize('method',['reserve','record_response'])
@pytest.mark.parametrize('field,value',[('attempt_id',uid(99)),('card_id',uid(99)),
    ('execution_authorized',True),('execution_authorized',0),('status','published'),('extra','private')])
def test_bad_result_rollback(method,field,value):
    connection=Connection(matched()) if method=='record_response' else Connection()
    connection.rows[-1][0][field]=value
    with pytest.raises(CancellationMarkupError):
        getattr(PostgresCancellationMarkupLedger(lambda:connection),method)(**(record_args() if method=='record_response' else args()))
    assert len(connection.calls)==2 and connection.exits[0] is not None


@pytest.mark.parametrize('method',['reserve','record_response'])
def test_lost_commit_ack_never_retries(method):
    connection=Connection(matched(),True) if method=='record_response' else Connection(commit_error=True)
    calls=[]
    def factory(): calls.append(1); return connection
    with pytest.raises(CancellationMarkupError,match='^cancellation_markup_ledger_unknown$'):
        getattr(PostgresCancellationMarkupLedger(factory),method)(**(record_args() if method=='record_response' else args()))
    assert calls==[1] and connection.exits==[None]


def test_autocommit_and_changed_plan_rejected():
    connection=Connection(); connection.autocommit=True
    with pytest.raises(CancellationMarkupError): PostgresCancellationMarkupLedger(lambda:connection).reserve(**args())
    assert connection.calls==[]
    kwargs=args(); kwargs['plan']=replace(kwargs['plan'],expires_at=NOW+999)
    with pytest.raises(CancellationMarkupError): PostgresCancellationMarkupLedger(lambda:connection).reserve(**kwargs)
    assert connection.calls==[]


def test_cross_card_database_parent_rejected_before_rpc():
    connection=Connection(); connection.rows[0][1]['bindings']['parent_binding']='0'*64
    with pytest.raises(CancellationMarkupError): PostgresCancellationMarkupLedger(lambda:connection).reserve(**args())
    assert len(connection.calls)==1 and connection.exits[0] is not None


SQL=Path(__file__).resolve().parents[1]/'supabase/proposals/content_ops_button_markup_attempt.sql'


def test_sql_only_minimized_single_card_attempts_without_runtime_access():
    code=re.sub(r'--[^\n]*','',SQL.read_text()).lower()
    assert 'card_id uuid not null unique' in code
    assert "default 'unknown'" in code
    assert 'force row level security' in code
    assert 'from public,anon,authenticated,service_role' in code
    assert not re.search(r'\bgrant\b|security definer|\bcreate policy\b|\brequest_json\b|\braw_response\b',code)
    assert not re.search(r'(insert into|update|delete from)\s+public\.',code)
    assert 'before update or delete' in code
    assert "'execution_authorized',false" in code


def test_sql_lock_order_and_no_expiry_reclaim():
    code=SQL.read_text().lower()
    assert code.index('from public.content_items') < code.index('select * into r') < code.index('select * into c')
    assert code.index('from private.content_ops_button_identities') < code.index('from private.content_ops_button_reviewers')
    assert "if found then" in code and "'new_attempt',false" in code
    assert 'on conflict' not in code and 'skip locked' not in code
    assert "a.observed_at is distinct from observed" in code
    assert 'observed<a.recorded_at or observed>=a.expires_at or observed>db_now' in code


def test_full_harness_runs_both_bounded_phases_before_restart_without_timeout_increase():
    root=SQL.parents[2]
    harness=(root/'scripts/verify_button_review_state_local.mjs').read_text()
    assert harness.index("driverTest('initial')") < harness.index("driverTest('cancellation')") < harness.index("driverTest('restart')")
    assert "'--phase', phase], { encoding: 'utf8', env, timeout: 60000 }" in harness
    assert 'sql(markupProposal)' in harness and "sql(markupProposal, 'synthetic_buttons')" in harness
    driver=(root/'scripts/verify_button_edit_driver_local.py').read_text()
    assert "choices=('initial', 'cancellation', 'guard', 'authority', 'confirmation', 'restart')" in driver
    assert harness.index("driverTest('authority')") < harness.index("driverTest('confirmation')") < harness.index("driverTest('restart')")
    assert harness.index("driverTest('cancellation')") < harness.index("driverTest('guard')") < harness.index("driverTest('restart')")
    assert harness.index("driverTest('guard')") < harness.index("driverTest('authority')") < harness.index("driverTest('restart')")
    assert 'sql(authorityProposal)' in harness and "sql(authorityProposal, 'synthetic_buttons')" in harness
    assert "'markupResponseRollbackAndLostAckVerified':True" in driver
