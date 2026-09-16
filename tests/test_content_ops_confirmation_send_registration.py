"""Synthetic authenticated command only, no live webhook or operator action."""
from dataclasses import astuple, replace
import hashlib
import json

import pytest

from core.content_ops.confirmation_send_registration import (
    COMMAND, operator_command, TelegramConfirmationSendDecision,
    PostgresConfirmationPermissionOwner, ConfirmationPermissionError,
)
from test_content_ops_confirmation_send import permission
from test_content_ops_confirmation_delivery_owner import DELIVERY
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_cancellation_markup_confirmation import target,moment
from test_content_ops_cancellation_control_receipt_owner import ReceiptConnection
from test_content_ops_review_cancellation import NOW,BOT,ROOM,HUMAN,POLICY,HEADERS,BINDINGS,records,uid


def command():
    i=fixture()[0]
    return operator_command(permission_id=permission().permission_id,delivery_id=DELIVERY,
        target=target(),plan=i['plan'],bindings=i['bindings'])


def update():
    return dict(update_id=8989,message=dict(message_id=88,date=NOW,
        chat=dict(id=ROOM,type='supergroup'),**{'from':dict(id=HUMAN,is_bot=False)},
        text=command(),entities=[dict(type='bot_command',offset=0,length=len(COMMAND))]))


def raw(value=None):return json.dumps(update() if value is None else value).encode()


def decision(**changes):
    opts=dict(enabled=True,raw_body=raw(),headers=HEADERS,policy=POLICY,received_at=moment())
    opts.update(changes);return TelegramConfirmationSendDecision(**opts)


def event():
    return (BINDINGS.digest('bot',BOT),BINDINGS.digest('confirmation-send-message@1',BOT,ROOM,88),
        BINDINGS.digest('confirmation-send-update@1',BOT,8989),permission().permission_id,
        hashlib.sha256(raw()).hexdigest(),moment())


class Connection(ReceiptConnection):
    def __init__(self,reused=False,commit_error=False):
        super().__init__(commit_error=commit_error)
        self.rows=[('5000','10000'),(fixture()[1],),records(),(moment(NOW+.1),),astuple(fixture()[2]),
            event() if reused else None]
        if not reused:self.rows.append(event())
        self.rows.extend([(moment(NOW+.1),),astuple(permission()) if reused else None])
        if not reused:self.rows.extend([None,astuple(permission())])
        self.rows.append((moment(NOW+.1),))


def run(conn,auth=None,**changes):
    opts=dict(enabled=True,permission_id=permission().permission_id,delivery_id=DELIVERY,
        target=target(),**fixture()[0]);opts.update(changes)
    return PostgresConfirmationPermissionOwner(lambda:conn,decision=auth or decision()).register(**opts)


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_off_does_not_authenticate_or_open_db(enabled):
    def fail():raise AssertionError()
    assert PostgresConfirmationPermissionOwner(fail).register(enabled=enabled) is None


@pytest.mark.parametrize('reused',[False,True])
def test_event_and_permission_recorded_atomically_no_sends(reused):
    conn=Connection(reused)
    assert run(conn)==dict(status='confirmation_permission_recorded',reused=reused,execution_authorized=False)
    assert conn.exits==[None]
    writes=[q for q,_ in conn.calls if q.strip().startswith('insert')]
    assert len(writes)==(0 if reused else 2)
    assert all('markup_approvals' not in q and 'publications' not in q for q,_ in conn.calls)


@pytest.mark.parametrize('changes',[dict(headers=[]),dict(headers=[('Cookie','shared-studio')]),
    dict(headers=[*HEADERS,HEADERS[-1]]),dict(raw_body=b'{}'),dict(raw_body=b'\xff'),
    dict(policy=None),dict(received_at=moment().replace(tzinfo=None)),dict(enabled=False)])
def test_invalid_authentication_has_zero_db_io(changes):
    conn=Connection()
    with pytest.raises(ConfirmationPermissionError):run(conn,decision(**changes))
    assert not conn.calls


@pytest.mark.parametrize('field,value',[('text','approved'),('message_id',True),('date',NOW-6),
    ('date',NOW+1),('message_thread_id',4),('edit_date',NOW),('forward_origin',{}),
    ('sender_chat',{}),('via_bot',{}),('reply_to_message',{}),('photo',[]),
    ('entities',[dict(type='bot_command',offset=False,length=len(COMMAND))])])
def test_changed_copied_or_edited_message_cannot_register(field,value):
    v=update();v['message'][field]=value;conn=Connection()
    with pytest.raises(ConfirmationPermissionError):run(conn,decision(raw_body=raw(v)))
    assert not conn.calls


@pytest.mark.parametrize('container,field,value',[('from','id',999),('from','is_bot',True),
    ('chat','id',ROOM-1),('chat','type','channel'),('chat','username','public'),
    ('chat','active_usernames',[])])
def test_only_exact_mapped_human_and_private_room_allowed(container,field,value):
    v=update();v['message'][container][field]=value;conn=Connection()
    with pytest.raises(ConfirmationPermissionError):run(conn,decision(raw_body=raw(v)))
    assert not conn.calls


@pytest.mark.parametrize('change',[dict(permission_id=uid(777)),dict(delivery_id=uid(777)),
    dict(actor_id=uid(777)),dict(human_id=999)])
def test_wrong_actor_or_command_scope_refused_before_db(change):
    conn=Connection()
    with pytest.raises(ConfirmationPermissionError):run(conn,**change)
    assert not conn.calls


def test_no_generic_boolean_or_markup_decision_fallback():
    calls=[]
    with pytest.raises(ConfirmationPermissionError):
        PostgresConfirmationPermissionOwner(lambda:calls.append(1),decision=lambda **kw:True).register(
            enabled=True,**fixture()[0])
    assert not calls


@pytest.mark.parametrize('index',range(5))
def test_changed_command_replay_refused(index):
    conn=Connection(True);row=list(event());row[index]='e'*64;conn.rows[5]=tuple(row)
    with pytest.raises(ConfirmationPermissionError):run(conn)
    assert conn.exits[0] is not None and not any(q.strip().startswith('insert') for q,_ in conn.calls)


def test_replay_uses_original_time_not_retry_received_at():
    conn=Connection(True)
    assert run(conn,decision(received_at=moment(NOW+.2)))['reused'] is True
    conn=Connection(True);conn.rows[3]=(moment(NOW+5.1),)
    with pytest.raises(ConfirmationPermissionError):run(conn,decision(received_at=moment(NOW+.2)))
    assert conn.exits[0] is not None


@pytest.mark.parametrize('field,value',[('active',False),('expires_at',moment(NOW+301)),
    ('request_sha256','e'*64),('authorized_at',moment(NOW+.1))])
def test_revoked_or_changed_permission_cannot_be_reissued(field,value):
    conn=Connection(True);conn.rows[7]=astuple(replace(permission(),**{field:value}))
    with pytest.raises(ConfirmationPermissionError):run(conn)
    assert not any(q.strip().startswith('insert') for q,_ in conn.calls)


def test_consumed_attempt_prevents_new_permission_and_rolls_back_event():
    conn=Connection();conn.rows[9]=(DELIVERY,)
    with pytest.raises(ConfirmationPermissionError):run(conn)
    assert conn.exits[0] is not None
    assert sum(q.strip().startswith('insert') for q,_ in conn.calls)==1


@pytest.mark.parametrize('index',[6,10])
def test_corrupt_event_or_permission_readback_rolls_back(index):
    conn=Connection();conn.rows[index]=None
    with pytest.raises(ConfirmationPermissionError):run(conn)
    assert conn.exits[0] is not None


def test_expiry_during_registration_rolls_back_and_lost_commit_ack_never_retries():
    conn=Connection();conn.rows[-1]=(moment(NOW+300),)
    with pytest.raises(ConfirmationPermissionError):run(conn)
    assert conn.exits[0] is not None
    conn=Connection(commit_error=True)
    with pytest.raises(ConfirmationPermissionError,match='^confirmation_permission_unconfirmed$'):run(conn)
    assert len(conn.exits)==1 and sum(q.strip().startswith('insert') for q,_ in conn.calls)==2


def test_operator_preview_command_is_pure_and_bound_to_payload():
    text=command()
    assert text.startswith(COMMAND+' ') and text.split()[-1]==permission().request_sha256
    assert str(ROOM) not in text and POLICY.webhook_secret not in text
