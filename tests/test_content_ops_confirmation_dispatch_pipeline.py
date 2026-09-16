"""Owner-to-existing-relay seam, entirely synthetic and in process.

The transport below is ONLY a test adapter, not a cross-process authenticated
bridge. No production runner imports it and MockTransport prevents networking.
"""
import asyncio

import pytest

from core.content_ops.confirmation_dispatch_owner import PostgresConfirmationDispatchOwner
from core.content_ops.confirmation_delivery_owner import _response
from test_content_ops_confirmation_dispatch_owner import Connection
from test_content_ops_confirmation_response_capture import Rig as CaptureRig, ACK
from test_content_ops_confirmation_response_store import auth
from test_content_ops_confirmation_send import Rig as OwnerRig
from test_content_ops_confirmation_delivery_owner import attempt
from test_content_ops_cancellation_markup_confirmation import moment
from test_content_ops_review_cancellation import NOW


def pipeline(*, commit_error=False, status=200, sink=None, valid=True):
    owner=OwnerRig();conn=Connection(commit_error=commit_error)
    owner.valid[1]=valid
    relay=CaptureRig(status=status,sink=sink,times=[moment(NOW+.3),moment(NOW+.4)])
    class TestOnlyTransport:
        def send_confirmation(self,*,delivery_id,request,lease):
            assert lease is owner
            assert conn.exits==[None] and not conn.rows  # Commit precedes ANY provider call.
            assert owner.held
            asyncio.run(relay.post())
            return dict(ACK)
    def ingest(**kw):
        assert not owner.held and len(relay.envelopes)==1
        received=auth().verify(relay.envelopes[0])
        _response(received,owner.target,owner.identity['plan'],owner.identity['bindings'],attempt())
        return dict(status='confirmation_recorded',reused=False,execution_authorized=False)
    owner.ingest=ingest
    result=owner.run(dispatch_owner=PostgresConfirmationDispatchOwner(lambda:conn),
        transport=TestOnlyTransport())
    return result,owner,relay,conn


def test_committed_permission_and_dispatch_then_existing_relay_capture_then_import():
    result,owner,relay,conn=pipeline()
    assert result['status']=='confirmation_recorded'
    assert len(relay.requests)==4 and len(relay.writes())==1 and len(relay.envelopes)==1
    assert conn.exits==[None] and not owner.held


@pytest.mark.parametrize('commit_error,valid',[(True,True),(False,False)])
def test_uncertain_commit_or_final_revocation_has_zero_provider_calls(commit_error,valid):
    result,owner,relay,conn=pipeline(commit_error=commit_error,valid=valid)
    assert result['status']=='unknown' and not relay.requests
    # A fresh coordinator still sees the earlier reservation as consumed.
    restarted=OwnerRig(owner.durable)
    assert restarted.run()['status']=='existing_unknown' and 'send' not in restarted.events


def test_provider_failure_is_captured_but_never_mistaken_for_delivery_or_retried():
    result,owner,relay,_=pipeline(status=500)
    assert result['status']=='unknown' and len(relay.writes())==len(relay.envelopes)==1
    restarted=OwnerRig(owner.durable)
    assert restarted.run()['status']=='existing_unknown' and 'send' not in restarted.events


def test_source_persistence_failure_never_retries_provider():
    calls=[]
    async def sink(**kw):
        calls.append(1)
        raise TimeoutError('synthetic private error')
    result,owner,relay,_=pipeline(sink=sink)
    assert result==dict(status='unknown',execution_authorized=False)
    assert len(relay.writes())==len(calls)==1
    restarted=OwnerRig(owner.durable)
    assert restarted.run()['status']=='existing_unknown' and 'send' not in restarted.events
