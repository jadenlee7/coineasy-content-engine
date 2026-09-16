"""Offline stored-reader/event behavior; real transactions use local harness."""
from dataclasses import astuple
import hashlib
import json

import pytest

from core.content_ops.cancellation_markup_confirmation import MarkupConfirmationError
from core.content_ops.cancellation_markup_confirmation_owner import (
    PostgresMarkupConfirmationReader, PostgresTelegramMarkupDecision,
)
from test_content_ops_cancellation_markup_confirmation import stored, callback, moment, target
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_review_cancellation import NOW, BOT, BINDINGS, POLICY, HEADERS


def prompt_row():
    receipt=stored()
    return (*astuple(receipt.target), *astuple(receipt)[1:])


def event_row(data=None):
    value=callback() if data is None else data
    return (BINDINGS.digest('bot',BOT),
        BINDINGS.digest('markup-callback@1',BOT,value['callback_query']['id']),
        BINDINGS.digest('markup-update@1',BOT,value['update_id']),target().approval_id,
        hashlib.sha256(json.dumps(value).encode()).hexdigest(),moment())


class Cursor:
    def __init__(self, reused=False):
        self.calls=[]
        self.rows=[prompt_row(),event_row() if reused else None,(moment(NOW+.1),),(moment(NOW+.2),)]
        if not reused: self.rows.append(event_row())
    def execute(self, sql, params=()): self.calls.append((sql,params))
    def fetchone(self): return self.rows.pop(0)


def owner(data=None, **changes):
    kwargs=dict(enabled=True, raw_body=json.dumps(callback() if data is None else data).encode(),
        headers=HEADERS,policy=POLICY,bindings=BINDINGS,received_at=moment())
    kwargs.update(changes)
    return PostgresTelegramMarkupDecision(**kwargs)


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_off_is_zero_io(enabled):
    cursor=Cursor()
    assert owner(enabled=enabled)(cursor=cursor,approval_id=target().approval_id) is None
    assert PostgresMarkupConfirmationReader(enabled=enabled)(cursor=cursor) is None
    assert not cursor.calls


@pytest.mark.parametrize('reused',[False,True])
def test_exact_event_insert_or_replay_keeps_original_time(reused):
    cursor=Cursor(reused)
    received=moment(NOW+.1) if reused else moment()
    decision=owner(received_at=received)(cursor=cursor,approval_id=target().approval_id)
    assert decision == fixture()[3]
    assert 'for share' in cursor.calls[0][0] and 'for update' in cursor.calls[1][0]
    writes=[(sql,params) for sql,params in cursor.calls if sql.strip().startswith('insert')]
    assert len(writes)==(0 if reused else 1)
    if writes: assert writes[0][1] == event_row()
    assert all(POLICY.webhook_secret not in str(params) for _,params in cursor.calls)


@pytest.mark.parametrize('column,value',[(0,'e'*64),(1,'e'*64),(2,'e'*64),
    (3,'10000000-0000-4000-8000-000000000088'),(4,'e'*64),(5,moment(NOW+1))])
def test_existing_event_mismatch_is_not_overwritten(column,value):
    cursor=Cursor(True); row=list(cursor.rows[1]); row[column]=value; cursor.rows[1]=tuple(row)
    with pytest.raises(MarkupConfirmationError): owner()(cursor=cursor,approval_id=target().approval_id)
    assert not any(sql.strip().startswith(('insert','update','delete')) for sql,_ in cursor.calls)


@pytest.mark.parametrize('kind',['inactive','missing','malformed','wrong_target'])
def test_bad_prompt_never_inserts_event(kind):
    cursor=Cursor(); row=list(prompt_row())
    if kind=='inactive': row[-1]=False
    if kind=='wrong_target': row[5]='e'*64
    cursor.rows[0]=None if kind=='missing' else tuple(row[:3] if kind=='malformed' else row)
    with pytest.raises(MarkupConfirmationError): owner()(cursor=cursor,approval_id=target().approval_id)
    assert not any(sql.strip().startswith('insert') for sql,_ in cursor.calls)


def test_unauthenticated_body_never_queries_storage():
    cursor=Cursor()
    with pytest.raises(MarkupConfirmationError):
        owner(headers=[])(cursor=cursor,approval_id=target().approval_id)
    assert not cursor.calls


def test_corrupt_insert_readback_is_refused_for_registrar_rollback():
    cursor=Cursor(); row=list(event_row()); row[4]='e'*64; cursor.rows[-1]=tuple(row)
    with pytest.raises(MarkupConfirmationError): owner()(cursor=cursor,approval_id=target().approval_id)
    assert sum(sql.strip().startswith('insert') for sql,_ in cursor.calls)==1


def test_new_click_does_not_replace_prior_event():
    cursor=Cursor(True); data=callback(); data['callback_query']['id']='another_click'
    with pytest.raises(MarkupConfirmationError): owner(data)(cursor=cursor,approval_id=target().approval_id)
    assert len(cursor.calls)==2


def test_late_replay_uses_first_timestamp_and_cannot_refresh_approval():
    cursor=Cursor(True); cursor.rows[2]=(moment(NOW+6),)
    with pytest.raises(MarkupConfirmationError):
        owner(received_at=moment(NOW+6))(cursor=cursor,approval_id=target().approval_id)
    assert not any(sql.strip().startswith('insert') for sql,_ in cursor.calls)
