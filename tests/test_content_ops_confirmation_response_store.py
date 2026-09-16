"""Synthetic relay authentication only; no keys loaded or network calls."""
from dataclasses import replace
import pytest

from core.content_ops.confirmation_response_store import (
    ConfirmationSourceError, ConfirmationResponseAuthenticator,
    SealedConfirmationResponse, PostgresConfirmationSourceStore,
    PostgresConfirmationSourceReader, _values,
)
from test_content_ops_confirmation_delivery_owner import receipt, DELIVERY
from test_content_ops_cancellation_markup_confirmation import moment
from test_content_ops_cancellation_control_receipt_owner import ReceiptConnection
from test_content_ops_review_cancellation import NOW, uid


def auth(): return ConfirmationResponseAuthenticator(b'synthetic-relay-response-key-only'*2)
def sealed(): return auth().seal(enabled=True,receipt=receipt())


class Connection(ReceiptConnection):
    def __init__(self,reused=False,commit_error=False):
        super().__init__(commit_error=commit_error)
        self.rows=[('5000','10000'),(receipt().request_sha256,moment(),moment(NOW+300)),
            (moment(NOW+.1),),_values(sealed()) if reused else None]
        if not reused:self.rows.append(_values(sealed()))


def record(conn,**changes):
    args=dict(enabled=True,envelope=sealed());args.update(changes)
    return PostgresConfirmationSourceStore(lambda:conn,authenticator=auth()).record(**args)


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_off_has_zero_io(enabled):
    def fail(**kwargs):raise AssertionError('no I/O')
    assert PostgresConfirmationSourceStore(fail).record(enabled=enabled) is None
    assert PostgresConfirmationSourceReader(enabled=enabled)(cursor=object()) is None
    assert auth().seal(enabled=enabled) is None


@pytest.mark.parametrize('key',[None,'secret',b'',b'a'*31,bytearray(b'a'*32)])
def test_key_requires_explicit_strict_bytes(key):
    with pytest.raises(ConfirmationSourceError):ConfirmationResponseAuthenticator(key)


@pytest.mark.parametrize('field,value',[('delivery_id',uid(77)),('request_sha256','e'*64),
    ('http_status',500),('raw_response',b'{}'),('observed_at',moment(NOW+.06))])
def test_mac_binds_every_receipt_field(field,value):
    envelope=replace(sealed(),receipt=replace(receipt(),**{field:value}))
    calls=[]
    with pytest.raises(ConfirmationSourceError):
        PostgresConfirmationSourceStore(lambda:calls.append(1),authenticator=auth()).record(
            enabled=True,envelope=envelope)
    assert not calls


@pytest.mark.parametrize('envelope',[True,{},'approved',None])
def test_unsealed_input_cannot_touch_db(envelope):
    calls=[]
    with pytest.raises(ConfirmationSourceError):
        PostgresConfirmationSourceStore(lambda:calls.append(1),authenticator=auth()).record(
            enabled=True,envelope=envelope)
    assert not calls


@pytest.mark.parametrize('field,value',[('delivery_id','bad'),('request_sha256','X'*64),
    ('http_status',True),('http_status',600),('raw_response',b''),
    ('raw_response',b'x'*32769),('raw_response',bytearray(b'{}'))])
def test_sealing_rejects_unbounded_and_invalid_data(field,value):
    with pytest.raises(ConfirmationSourceError):
        auth().seal(enabled=True,receipt=replace(receipt(),**{field:value}))


def test_wrong_key_has_no_fallback_and_repr_contains_no_sensitive_data():
    with pytest.raises(ConfirmationSourceError):
        ConfirmationResponseAuthenticator(b'different-synthetic-key'*2).verify(sealed())
    assert DELIVERY not in repr(sealed()) and 'raw_response' not in repr(sealed())


@pytest.mark.parametrize('reused',[False,True])
def test_exact_insert_reuse_commit_with_minimized_result(reused):
    conn=Connection(reused)
    assert record(conn)==dict(status='confirmation_source_recorded',reused=reused,execution_authorized=False)
    assert conn.exits==[None]
    assert sum(sql.strip().startswith('insert') for sql,_ in conn.calls)==int(not reused)
    assert all(not sql.strip().startswith(('update','delete')) for sql,_ in conn.calls)
    assert 'for update' in conn.calls[1][0]


@pytest.mark.parametrize('attempt',[None,('e'*64,moment(),moment(NOW+300)),
    (receipt().request_sha256,moment(NOW+1),moment(NOW+300)),
    (receipt().request_sha256,moment(),moment(NOW+.01))])
def test_unknown_or_mismatched_reservation_is_not_evidence(attempt):
    conn=Connection();conn.rows[1]=attempt
    with pytest.raises(ConfirmationSourceError):record(conn)
    assert conn.exits[0] is not None
    assert not any(s.strip().startswith('insert') for s,_ in conn.calls)


def test_future_observation_never_inserts():
    conn=Connection();conn.rows[2]=(moment(),)
    with pytest.raises(ConfirmationSourceError):record(conn)
    assert not any(s.strip().startswith('insert') for s,_ in conn.calls)


@pytest.mark.parametrize('index',range(7))
def test_existing_or_returned_record_mismatch_refused(index):
    conn=Connection(True);row=list(conn.rows[3]);row[index]=None;conn.rows[3]=tuple(row)
    with pytest.raises(ConfirmationSourceError):record(conn)
    assert not any(s.strip().startswith('insert') for s,_ in conn.calls)


def test_bad_insert_readback_and_lost_ack_do_not_retry():
    conn=Connection();conn.rows[-1]=None
    with pytest.raises(ConfirmationSourceError):record(conn)
    assert conn.exits[0] is not None
    conn=Connection(commit_error=True)
    with pytest.raises(ConfirmationSourceError,match='^confirmation_source_unconfirmed$'):record(conn)
    assert len(conn.exits)==1 and sum(s.strip().startswith('insert') for s,_ in conn.calls)==1


def read_row(row):
    conn=Connection();conn.rows=[row]
    reader=PostgresConfirmationSourceReader(enabled=True,authenticator=auth())
    with conn.cursor() as cursor:
        return reader(cursor=cursor,delivery_id=DELIVERY,request_sha256=receipt().request_sha256)


def test_reader_returns_original_bytes_and_timestamp_without_new_transaction():
    assert read_row(_values(sealed()))==receipt()


@pytest.mark.parametrize('index',range(7))
def test_reader_revalidates_stored_signature_and_bytes(index):
    row=list(_values(sealed()));row[index]=None
    with pytest.raises(ConfirmationSourceError):read_row(tuple(row))


def test_missing_source_is_not_delivery():
    with pytest.raises(ConfirmationSourceError):read_row(None)


def test_authenticated_failure_response_is_evidence_not_success():
    failed=replace(receipt(),http_status=500,raw_response=b'{"ok":false}')
    envelope=auth().seal(enabled=True,receipt=failed)
    assert auth().verify(envelope)==failed
    # Its shape can be stored; the delivery importer still rejects non-200.
    from test_content_ops_confirmation_delivery_owner import verify
    with pytest.raises(ValueError):verify(failed)
