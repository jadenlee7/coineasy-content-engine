"""Unmounted operator-command permission registration, never a sender.

Reuses the existing raw Telegram webhook authentication and immutable reviewer
mapping. No shared Studio/admin credential or supplied actor assertion substitutes
for a mapped human command. The existing ingress must preserve received_at and
own this adapter; no second webhook, poller, live route or credentials are added.
"""
from dataclasses import astuple
from datetime import datetime, timezone
import hashlib

from core.content_ops.cancellation_markup_owner import _inputs, _load_target
from core.content_ops.cancellation_markup import encode
from core.content_ops.cancellation_control_receipt_owner import _clock, _COLUMNS as _EVIDENCE_COLUMNS, _same_row
from core.content_ops.cancellation_markup_authority import OriginalControlEvidence
from core.content_ops.cancellation_markup_confirmation import _target, _time
from core.content_ops.confirmation_delivery_owner import confirmation_send_request
from core.content_ops.confirmation_send_authority import ConfirmationSendPermission, ExactConfirmationSendAuthority
from core.content_ops.prompt_receipt import canonical_uuid
from core.content_ops.review_ingress import _authenticated_update, _positive_int


COMMAND = '/request_confirmation'
_EVENT = 'bot_binding,message_binding,update_binding,permission_id::text,payload_sha256,received_at'
_PERMISSION = '''permission_id::text,delivery_id::text,card_id::text,actor_id::text,
    human_binding,target_binding,request_sha256,action,authorized_at,expires_at,active'''


class ConfirmationPermissionError(ValueError):
    pass


def require(ok):
    if not ok: raise ConfirmationPermissionError('confirmation_permission_unconfirmed')


def operator_command(*, permission_id, delivery_id, target, plan, bindings):
    """Pure exact command text for a separately reviewed local plan; never send."""
    require(canonical_uuid(permission_id) and canonical_uuid(delivery_id))
    request_hash = hashlib.sha256(confirmation_send_request(target,plan,bindings)).hexdigest()
    return f'{COMMAND} {permission_id} {delivery_id} {request_hash}'


class TelegramConfirmationSendDecision:
    def __init__(self, *, enabled=False, raw_body=None, headers=(), policy=None, received_at=None):
        self._enabled, self._raw = enabled is True, raw_body
        self._headers = tuple(tuple(x) for x in headers)
        self._policy, self._received = policy, received_at

    def authenticate(self, *, permission_id, delivery_id, target, **identity):
        """Authentication, exact text and identity checks BEFORE registrar DB I/O."""
        try:
            require(self._enabled)
            _inputs(**identity); _target(target)
            received = _time(self._received)
            value = _authenticated_update(self._raw,self._headers,self._policy,int(received))
            require(set(value)=={'update_id','message'})
            msg = value['message']; policy = self._policy; plan = identity['plan']
            require(type(msg) is dict and set(msg)<= {'message_id','date','chat','from','text',
                'entities','message_thread_id','is_topic_message'})
            human,chat = msg.get('from'),msg.get('chat')
            require(type(human) is dict and human.get('is_bot') is False
                and _positive_int(human.get('id')) and human['id']==identity['human_id']
                and dict(policy.reviewers).get(human['id'])==identity['actor_id'])
            require(type(chat) is dict and type(chat.get('id')) is int
                and chat['id']==policy.chat_id==plan.chat_id and policy.bot_id==plan.bot_id
                and chat.get('type')=='supergroup'
                and not ({'username','active_usernames','linked_chat_id'} & chat.keys()))
            require(_positive_int(msg.get('message_id')) and type(msg.get('date')) is int
                and 0<=received-msg['date']<=5 and plan.started_at<=msg['date'])
            if plan.thread_id is None:
                require('message_thread_id' not in msg and 'is_topic_message' not in msg)
            else:
                require(type(msg.get('message_thread_id')) is int
                    and msg['message_thread_id']==plan.thread_id and msg.get('is_topic_message') is True)
            expected = operator_command(permission_id=permission_id,delivery_id=delivery_id,
                target=target,plan=plan,bindings=identity['bindings'])
            require(msg.get('text')==expected and encode(msg.get('entities'))==encode([
                {'type':'bot_command','offset':0,'length':len(COMMAND)}]))
            return value
        except Exception:
            raise ConfirmationPermissionError('confirmation_permission_unconfirmed') from None

    def decide(self, *, cursor, permission_id, delivery_id, target, now, **identity):
        value = self.authenticate(permission_id=permission_id,delivery_id=delivery_id,target=target,**identity)
        plan,b = identity['plan'],identity['bindings']
        fields = (b.digest('bot',plan.bot_id),
            b.digest('confirmation-send-message@1',plan.bot_id,plan.chat_id,value['message']['message_id']),
            b.digest('confirmation-send-update@1',plan.bot_id,value['update_id']),
            permission_id,hashlib.sha256(self._raw).hexdigest())
        cursor.execute(f'''select {_EVENT} from private.content_ops_button_confirmation_send_events
            where message_binding=%s or update_binding=%s or permission_id=%s::uuid for update''',
            (fields[1],fields[2],permission_id))
        previous = cursor.fetchone()
        received = self._received
        if previous is not None:
            require(type(previous) is tuple and len(previous)==6 and previous[:5]==fields
                and _time(previous[5])<=_time(received))
            received = previous[5]  # A replay cannot refresh the decision time.
        require(0<=(_time(now)-_time(received))<=5)
        require(0<=_time(received)-value['message']['date']<=5)
        if previous is None:
            values = (*fields,received)
            cursor.execute(f'''insert into private.content_ops_button_confirmation_send_events
                (bot_binding,message_binding,update_binding,permission_id,payload_sha256,received_at)
                values(%s,%s,%s,%s::uuid,%s,%s) returning {_EVENT}''',values)
            row = cursor.fetchone()
            require(type(row) is tuple and row==values
                and all(type(a) is type(z) for a,z in zip(row,values)))
        return ConfirmationSendPermission(permission_id,delivery_id,plan.card_id,identity['actor_id'],
            b.digest('human',plan.bot_id,identity['human_id']),
            b.digest('confirmation-delivery-target@1',*astuple(target)),
            hashlib.sha256(confirmation_send_request(target,plan,b)).hexdigest(),
            'send_private_confirmation@1',received,datetime.fromtimestamp(plan.expires_at,timezone.utc),True)


class PostgresConfirmationPermissionOwner:
    """Atomic original-command event + exact permission; no transport or retries.

    Lock order: current card/evidence -> command event -> permission -> existing
    delivery check. New authority is forbidden once that card has a consumed
    delivery. Exact replay can only reuse an unchanged active permission.
    """
    def __init__(self, factory, *, decision=None): self._factory,self._decision = factory,decision

    def register(self, *, enabled=False, permission_id=None, delivery_id=None, target=None, **identity):
        if enabled is not True: return None
        try:
            require(type(self._decision) is TelegramConfirmationSendDecision)
            self._decision.authenticate(permission_id=permission_id,delivery_id=delivery_id,target=target,**identity)
            plan,b = identity['plan'],identity['bindings']
            with self._factory() as connection:
                require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('lock_timeout','5000',true), set_config('statement_timeout','10000',true)")
                    cursor.fetchone()
                    cursor.execute('''select to_jsonb(private.lock_content_ops_button_markup_card(
                        %s::uuid,%s::uuid,%s,%s))''',(plan.card_id,identity['actor_id'],
                        b.digest('bot',plan.bot_id),b.digest('human',plan.bot_id,identity['human_id'])))
                    row = cursor.fetchone()
                    require(row and type(row[0]) is dict and row[0].get('id')==plan.card_id
                        and row[0].get('active') is True)
                    card = row[0]
                    _load_target(cursor,plan,b,identity['signer'])
                    before = _clock(cursor,plan,b)
                    cursor.execute(f'''select {_EVIDENCE_COLUMNS} from private.content_ops_button_control_evidence
                        where card_id=%s::uuid for share''',(plan.card_id,))
                    evidence = OriginalControlEvidence(*cursor.fetchone())
                    decision = self._decision.decide(cursor=cursor,permission_id=permission_id,
                        delivery_id=delivery_id,target=target,now=before,**identity)
                    after = _clock(cursor,plan,b)
                    require(after>=before and _time(after)-_time(decision.authorized_at)<=5)
                    verifier = ExactConfirmationSendAuthority(enabled=True,target=target,delivery_id=delivery_id,
                        reader=lambda **kw:(evidence,decision))
                    require(verifier(cursor=cursor,locked_card=card,now=after,**identity) is True)
                    cursor.execute(f'''select {_PERMISSION} from private.content_ops_button_confirmation_send_permissions
                        where permission_id=%s::uuid or delivery_id=%s::uuid or card_id=%s::uuid for update''',
                        (permission_id,delivery_id,plan.card_id))
                    previous = cursor.fetchone(); reused = previous is not None
                    if reused:
                        _same_row(previous,decision)
                    else:
                        cursor.execute('''select delivery_id::text from private.content_ops_button_confirmation_deliveries
                            where card_id=%s::uuid or delivery_id=%s::uuid for share''',(plan.card_id,delivery_id))
                        require(cursor.fetchone() is None)
                        cursor.execute(f'''insert into private.content_ops_button_confirmation_send_permissions
                            (permission_id,delivery_id,card_id,actor_id,human_binding,target_binding,request_sha256,
                             action,authorized_at,expires_at,active) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                             returning {_PERMISSION}''',astuple(decision))
                        _same_row(cursor.fetchone(),decision)
                    final = _clock(cursor,plan,b)
                    require(final>=after and _time(final)-_time(decision.authorized_at)<=5)
            return dict(status='confirmation_permission_recorded',reused=reused,execution_authorized=False)
        except Exception:
            raise ConfirmationPermissionError('confirmation_permission_unconfirmed') from None
