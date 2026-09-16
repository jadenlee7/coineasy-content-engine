"""Dispatch consumption tests use synthetic permissions; never live authority."""
from dataclasses import astuple, replace

import pytest

from core.content_ops.confirmation_dispatch_owner import (
    PostgresConfirmationDispatchOwner, ConfirmationDispatchError,
)
from test_content_ops_cancellation_control_receipt_owner import ReceiptConnection
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_cancellation_markup_confirmation import target, moment
from test_content_ops_confirmation_delivery_owner import DELIVERY, attempt
from test_content_ops_confirmation_send import permission
from test_content_ops_review_cancellation import NOW, records, uid


def dispatched(when=NOW+.15):
    p=permission()
    return (DELIVERY,p.permission_id,p.card_id,p.request_sha256,moment(when))


class Connection(ReceiptConnection):
    def __init__(self,reused=False,commit_error=False):
        super().__init__(commit_error=commit_error)
        self.rows=[('5000','10000'),(fixture()[1],),records(),(moment(NOW+.1),),
            astuple(fixture()[2]),astuple(permission()),attempt(),None,
            dispatched(NOW+.05) if reused else None]
        if not reused:self.rows.append(dispatched())
        self.rows.append((moment(NOW+.2),))


def run(conn,**changes):
    args=dict(enabled=True,target=target(),delivery_id=DELIVERY,**fixture()[0]);args.update(changes)
    return PostgresConfirmationDispatchOwner(lambda:conn).consume(**args)


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_disabled_zero_io(enabled):
    def fail():raise AssertionError('no DB')
    assert PostgresConfirmationDispatchOwner(fail).consume(enabled=enabled) is None


@pytest.mark.parametrize('reused',[False,True])
def test_exact_record_returns_only_after_commit_and_reuse_never_grants(reused):
    c=Connection(reused)
    assert run(c)==dict(status='confirmation_dispatch_consumed',new_dispatch=not reused,execution_authorized=False)
    assert c.exits==[None] and not c.rows
    writes=[s for s,_ in c.calls if s.strip().startswith(('insert','update','delete'))]
    assert len(writes)==int(not reused)
    assert all('insert into private.content_ops_button_confirmation_dispatches' in s for s in writes)


@pytest.mark.parametrize('field,value',[('active',False),('active',1),('delivery_id',uid(88)),
    ('action','append_cancellation_markup@1'),('request_sha256','e'*64),
    ('expires_at',moment(NOW+.1)),('authorized_at',moment(NOW+.09))])
def test_revoked_changed_or_post_reservation_permission_cannot_consume(field,value):
    c=Connection();c.rows[5]=astuple(replace(permission(),**{field:value}))
    with pytest.raises(ConfirmationDispatchError):run(c)
    assert c.exits[0] is not None and not any(s.strip().startswith('insert') for s,_ in c.calls)


@pytest.mark.parametrize('index,value',[(0,uid(88)),(1,uid(88)),(2,uid(88)),(3,uid(88)),
    (4,'e'*64),(5,'e'*64),(6,moment(NOW+301)),(7,moment(NOW+1)),
    (8,'response_matched'),(9,'e'*64),(10,moment()),(11,uid(88))])
def test_exact_unfinished_reservation_required(index,value):
    c=Connection();row=list(c.rows[6]);row[index]=value;c.rows[6]=tuple(row)
    with pytest.raises(ConfirmationDispatchError):run(c)
    assert not any(s.strip().startswith('insert') for s,_ in c.calls)


@pytest.mark.parametrize('index,value',[(1,({'id':target().card_id,'active':False},)),
    (4,None),(5,None),(6,None),(7,(DELIVERY,)),(9,None),(10,(moment(NOW+.14),))])
def test_missing_evidence_existing_source_corrupt_readback_and_clock_fail_closed(index,value):
    c=Connection();c.rows[index]=value
    with pytest.raises(ConfirmationDispatchError,match='^confirmation_dispatch_unknown$'):run(c)
    assert c.exits[0] is not None


@pytest.mark.parametrize('reused',[False,True])
@pytest.mark.parametrize('index,value',[(0,uid(88)),(1,uid(88)),(2,uid(88)),
    (3,'e'*64),(4,moment(NOW+1)),(4,moment(NOW-1)),(4,None)])
def test_existing_and_inserted_dispatch_must_match_exactly(reused,index,value):
    c=Connection(reused);at=8 if reused else 9
    row=list(c.rows[at]);row[index]=value;c.rows[at]=tuple(row)
    with pytest.raises(ConfirmationDispatchError):run(c)
    assert c.exits[0] is not None


def test_lost_fresh_commit_ack_returns_no_dispatch_and_no_retry():
    c=Connection(commit_error=True)
    with pytest.raises(ConfirmationDispatchError):run(c)
    assert len(c.exits)==1
    assert sum(s.strip().startswith('insert') for s,_ in c.calls)==1


def test_bad_identity_fails_before_connection():
    calls=[]
    owner=PostgresConfirmationDispatchOwner(lambda:calls.append(1))
    with pytest.raises(ConfirmationDispatchError):
        owner.consume(enabled=True,target=target(),delivery_id='bad',**fixture()[0])
    assert not calls
