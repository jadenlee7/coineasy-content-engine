"""Synthetic original send receipts; no Telegram or production connections."""
from dataclasses import astuple, replace
import hashlib
import json

import pytest

from core.content_ops.confirmation_delivery_owner import (
    ConfirmationDeliveryError, StoredConfirmationSendReceipt,
    PostgresConfirmationDeliveryOwner, confirmation_send_request, _response,
)
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_cancellation_markup_confirmation import target, callback, moment, stored
from test_content_ops_cancellation_control_receipt_owner import ReceiptConnection
from test_content_ops_review_cancellation import NOW, BINDINGS, uid, records


DELIVERY=uid(808)


def receipt():
    return StoredConfirmationSendReceipt(DELIVERY,
        hashlib.sha256(confirmation_send_request(target(),fixture()[0]['plan'],BINDINGS)).hexdigest(),
        200,json.dumps({'ok':True,'result':callback()['callback_query']['message']}).encode(),moment(NOW+.05))


def attempt(matched=False):
    row=(DELIVERY,target().approval_id,target().card_id,target().actor_id,
        BINDINGS.digest('confirmation-delivery-target@1',*astuple(target())),receipt().request_sha256,
        moment(target().expires_at),moment(), 'unknown',None,None,None)
    if matched:
        row=(*row[:8],'response_matched',hashlib.sha256(receipt().raw_response).hexdigest(),
             receipt().observed_at,target().approval_id)
    return row


def prompt():
    value=replace(stored(),delivered_at=receipt().observed_at)
    return (*astuple(value.target),*astuple(value)[1:])


def verify(value=None, row=None):
    return _response(value or receipt(),target(),fixture()[0]['plan'],BINDINGS,row or attempt())


class Connection(ReceiptConnection):
    def __init__(self, *, ingest=True, reused=False, commit_error=False):
        super().__init__(commit_error=commit_error)
        self.rows=[('5000','10000'),(records()[1],),records(),(moment(NOW+.1),),
                   (target().original_receipt_sha256,),prompt() if ingest and reused else None,
                   attempt(ingest and reused) if ingest or reused else None]
        if ingest and not reused: self.rows += [prompt(),attempt(True)]
        if not ingest and not reused:
            # Reservation timestamp is returned after the pre-insert DB clock.
            self.rows.append((*attempt()[:7],moment(NOW+.15),*attempt()[8:]))
        self.rows.append((moment(NOW+.2),))


def run(conn, *, ingest=True, loader=None, **changes):
    kwargs=dict(enabled=True,target=target(),delivery_id=DELIVERY,**fixture()[0]); kwargs.update(changes)
    calls=[]
    def load(**params): calls.append(params); return receipt()
    owner=PostgresConfirmationDeliveryOwner(lambda:conn,receipt_loader=load if loader is None else loader)
    result=(owner.ingest if ingest else owner.reserve)(**kwargs)
    return result,calls


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_off_has_zero_io(enabled):
    def fail(**kwargs): raise AssertionError('no I/O')
    owner=PostgresConfirmationDeliveryOwner(fail,receipt_loader=fail)
    assert owner.reserve(enabled=enabled) is None
    assert owner.ingest(enabled=enabled) is None


@pytest.mark.parametrize('ingest,reused',[(False,False),(False,True),(True,False),(True,True)])
def test_exact_reservation_and_import_are_committed_not_authorized(ingest,reused):
    conn=Connection(ingest=ingest,reused=reused); result,calls=run(conn,ingest=ingest)
    assert conn.exits==[None]
    assert result==dict(status='confirmation_recorded' if ingest else 'unknown',
        **({'reused':reused} if ingest else {'new_attempt':not reused}),execution_authorized=False)
    assert len(calls)==int(ingest)
    writes=[sql for sql,_ in conn.calls if sql.strip().startswith(('insert','update'))]
    assert len(writes)==(0 if reused else (2 if ingest else 1))
    assert all('markup_approvals' not in sql and 'publications' not in sql for sql,_ in conn.calls)


def test_no_loader_cannot_connect_or_create_receipt():
    calls=[]
    owner=PostgresConfirmationDeliveryOwner(lambda:calls.append(1))
    with pytest.raises(ConfirmationDeliveryError):
        owner.ingest(enabled=True,target=target(),delivery_id=DELIVERY,**fixture()[0])
    assert not calls


@pytest.mark.parametrize('index,value',[(0,uid(77)),(1,uid(77)),(3,uid(77)),
    (4,'e'*64),(5,'e'*64),(6,moment(NOW+301))])
def test_altered_or_new_attempt_identity_cannot_be_used(index,value):
    conn=Connection(); row=list(conn.rows[6]);row[index]=value;conn.rows[6]=tuple(row)
    with pytest.raises(ConfirmationDeliveryError):run(conn)
    assert conn.exits[0] is not None and not any(s.strip().startswith('insert') for s,_ in conn.calls)


def test_unreserved_send_response_is_refused():
    conn=Connection();conn.rows[6]=None
    with pytest.raises(ConfirmationDeliveryError):run(conn)
    assert not any(s.strip().startswith(('insert','update')) for s,_ in conn.calls)


@pytest.mark.parametrize('field,value', [('delivery_id',uid(77)),('request_sha256','e'*64),
    ('http_status',True),('http_status',500),('raw_response',b'{}'),('raw_response',b'[]'),
    ('raw_response',b'{"ok":true,"ok":false}'),('raw_response',b'x'*32769),
    ('observed_at',moment(NOW-1)),('observed_at',moment(NOW+300))])
def test_wrong_or_uncertain_original_receipt_refused(field,value):
    with pytest.raises((ValueError,TypeError)):
        verify(replace(receipt(),**{field:value}))


@pytest.mark.parametrize('field,value',[('text','approved'),('message_id',True),('message_id',10),
    ('date',NOW-1),('date',NOW+1),('date',True),('reply_markup',{}),
    ('edit_date',NOW),('photo',[]),('forward_origin',{}),('reply_to_message',{}),
    ('message_thread_id',1),('is_topic_message',False),('entities',[{'type':'bold'}])])
def test_changed_copied_or_edited_response_is_not_delivery(field,value):
    data=json.loads(receipt().raw_response);data['result'][field]=value
    with pytest.raises(ValueError):verify(replace(receipt(),raw_response=json.dumps(data).encode()))


@pytest.mark.parametrize('container,field,value',[('chat','id',-99),('chat','type','channel'),
    ('chat','username','public'),('from','id',999),('from','is_bot',False)])
def test_wrong_destination_and_sender_refused(container,field,value):
    data=json.loads(receipt().raw_response);data['result'][container][field]=value
    with pytest.raises(ValueError):verify(replace(receipt(),raw_response=json.dumps(data).encode()))


def test_corrupt_post_update_readback_causes_rollback():
    conn=Connection();row=list(conn.rows[8]);row[9]='e'*64;conn.rows[8]=tuple(row)
    with pytest.raises(ConfirmationDeliveryError):run(conn)
    assert conn.exits[0] is not None


def test_future_observation_causes_rollback_not_receipt():
    conn=Connection();conn.rows[-1]=(moment(NOW+.01),)
    with pytest.raises(ConfirmationDeliveryError):run(conn)
    assert conn.exits[0] is not None


@pytest.mark.parametrize('ingest',[False,True])
def test_lost_commit_ack_never_retries(ingest):
    conn=Connection(ingest=ingest,commit_error=True)
    with pytest.raises(ConfirmationDeliveryError,match='^confirmation_delivery_unknown$'):
        run(conn,ingest=ingest)
    assert len(conn.exits)==1


def test_revoked_prompt_never_reactivated_by_original_receipt_replay():
    conn=Connection(reused=True);row=list(conn.rows[5]);row[-1]=False;conn.rows[5]=tuple(row)
    with pytest.raises(ConfirmationDeliveryError):run(conn)
    assert not any(s.strip().startswith(('insert','update')) for s,_ in conn.calls)
