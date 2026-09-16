"""Default-OFF pre-send reservation and original-response import. NO sender.

receipt_loader MUST read the existing relay owner's authenticated, immutable
original send response on the supplied cursor, bound to delivery_id and exact
request_sha256. It must not accept caller JSON, a webhook Message or a refreshed
response timestamp. The DTO is not self-authenticating. No live loader, route,
credential lookup, network, human approval or publication is provided.
"""
from dataclasses import astuple, dataclass
from datetime import datetime, timezone
import hashlib
import json

from core.content_ops.cancellation_markup import encode
from core.content_ops.cancellation_markup_owner import _inputs, _load_target
from core.content_ops.cancellation_control_receipt_owner import _clock
from core.content_ops.cancellation_markup_confirmation import (
    MarkupConfirmationTarget, StoredMarkupConfirmation, markup_confirmation_payload,
    _target, _time,
)
from core.content_ops.cancellation_markup_confirmation_owner import _PROMPT_COLUMNS
from core.content_ops.prompt_receipt import canonical_uuid
from core.content_ops.review_ingress import _positive_int, _unique_object, _reject_constant


class ConfirmationDeliveryError(ValueError):
    pass


def require(ok):
    if not ok:
        raise ConfirmationDeliveryError('confirmation_delivery_unknown')


@dataclass(frozen=True, repr=False)
class StoredConfirmationSendReceipt:
    delivery_id: str
    request_sha256: str
    http_status: int
    raw_response: bytes
    observed_at: datetime


_COLUMNS = '''delivery_id::text,approval_id::text,card_id::text,actor_id::text,
    target_binding,request_sha256,expires_at,reserved_at,status,response_sha256,
    observed_at,recorded_approval_id::text'''


def confirmation_send_request(target, plan, bindings):
    """Pure exact request bytes; not permission or an instruction to send."""
    payload = markup_confirmation_payload(target, bindings)
    body = dict(payload, chat_id=plan.chat_id)
    if plan.thread_id is not None:
        body['message_thread_id'] = plan.thread_id
    return encode({'method':'sendMessage', 'body':body}).encode()


def _same(actual, wanted):
    require(type(actual) is tuple and len(actual) == len(wanted)
            and all(type(a) is type(b) and a == b for a,b in zip(actual,wanted)))


def _response(receipt, target, plan, bindings, row):
    require(type(receipt) is StoredConfirmationSendReceipt
            and receipt.delivery_id == row[0] and receipt.request_sha256 == row[5]
            and type(receipt.http_status) is int and receipt.http_status == 200
            and type(receipt.raw_response) is bytes and 0 < len(receipt.raw_response) <= 32768)
    observed = _time(receipt.observed_at)
    require(_time(row[7]) <= observed < target.expires_at)
    data=json.loads(receipt.raw_response.decode(),object_pairs_hook=_unique_object,parse_constant=_reject_constant)
    require(type(data) is dict and set(data)=={'ok','result'} and data['ok'] is True)
    msg=data['result']
    require(type(msg) is dict and set(msg)<= {'message_id','date','chat','from','text',
        'entities','reply_markup','message_thread_id','is_topic_message'})
    chat,sender=msg.get('chat'),msg.get('from')
    require(type(chat) is dict and type(chat.get('id')) is int and chat['id']==plan.chat_id
        and chat.get('type')=='supergroup' and not ({'username','linked_chat_id'} & chat.keys()))
    require(type(sender) is dict and type(sender.get('id')) is int and sender['id']==plan.bot_id
        and sender.get('is_bot') is True)
    require(_positive_int(msg.get('message_id')) and msg['message_id'] != plan.message_id)
    require(type(msg.get('date')) is int and max(target.started_at,int(_time(row[7])))<=msg['date']<=observed)
    if plan.thread_id is None:
        require('message_thread_id' not in msg and 'is_topic_message' not in msg)
    else:
        require(type(msg.get('message_thread_id')) is int and msg['message_thread_id']==plan.thread_id
                and msg.get('is_topic_message') is True)
    payload=markup_confirmation_payload(target,bindings)
    require(msg.get('text')==payload['text'] and msg.get('entities',[])==[]
            and encode(msg.get('reply_markup'))==encode(payload['reply_markup']))
    return StoredMarkupConfirmation(target,plan.bot_id,plan.chat_id,msg['message_id'],
        plan.thread_id,msg['date'],receipt.observed_at,True)


class PostgresConfirmationDeliveryOwner:
    """Reserve once BEFORE any future I/O; import only that original response.

    Both calls require current card/reviewer/evidence. Unique card reservation
    prevents a second delivery ID becoming a retry. A reused unknown reservation
    never authorizes a send. Return only after commit; uncertain outcomes are not
    retried. Late/revoked outcomes require separate read-only reconciliation.
    """
    def __init__(self, connection_factory, *, receipt_loader=None):
        self._factory, self._loader = connection_factory, receipt_loader

    def reserve(self, *, enabled=False, target=None, delivery_id=None, **identity):
        if enabled is not True: return None
        return self._run(False, target, delivery_id, identity)

    def ingest(self, *, enabled=False, target=None, delivery_id=None, **identity):
        if enabled is not True: return None
        return self._run(True, target, delivery_id, identity)

    def _run(self, ingest, target, delivery_id, identity):
        try:
            _inputs(**identity); _target(target)
            require(canonical_uuid(delivery_id) and (not ingest or callable(self._loader)))
            plan,b=identity['plan'],identity['bindings']
            expected=MarkupConfirmationTarget(target.approval_id,identity['attempt_id'],plan.card_id,
                identity['actor_id'],b.digest('human',plan.bot_id,identity['human_id']),plan.seal,
                target.original_receipt_sha256,plan.started_at,plan.expires_at)
            require(target==expected)
            request_hash=hashlib.sha256(confirmation_send_request(target,plan,b)).hexdigest()
            pinned=(delivery_id,target.approval_id,plan.card_id,identity['actor_id'],
                b.digest('confirmation-delivery-target@1',*astuple(target)),request_hash,
                datetime.fromtimestamp(plan.expires_at,timezone.utc))
            with self._factory() as connection:
                require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('lock_timeout','5000',true), set_config('statement_timeout','10000',true)")
                    cursor.fetchone()
                    cursor.execute('''select to_jsonb(private.lock_content_ops_button_markup_card(
                        %s::uuid,%s::uuid,%s,%s))''',(plan.card_id,identity['actor_id'],
                        b.digest('bot',plan.bot_id),b.digest('human',plan.bot_id,identity['human_id'])))
                    locked=cursor.fetchone()
                    require(locked and type(locked[0]) is dict and locked[0].get('active') is True
                            and locked[0].get('id')==plan.card_id)
                    _load_target(cursor,plan,b,identity['signer'])
                    before=_clock(cursor,plan,b)
                    cursor.execute('''select receipt_sha256 from private.content_ops_button_control_evidence
                        where card_id=%s::uuid for share''',(plan.card_id,))
                    _same(cursor.fetchone(),(target.original_receipt_sha256,))
                    require(target.original_receipt_sha256==locked[0]['bindings']['card_receipt'])
                    cursor.execute(f'''select {_PROMPT_COLUMNS} from private.content_ops_button_markup_confirmations
                        where approval_id=%s::uuid or card_id=%s::uuid for share''',(target.approval_id,plan.card_id))
                    previous=cursor.fetchone()
                    cursor.execute(f'''select {_COLUMNS} from private.content_ops_button_confirmation_deliveries
                        where delivery_id=%s::uuid or approval_id=%s::uuid or card_id=%s::uuid for update''',
                        (delivery_id,target.approval_id,plan.card_id))
                    row=cursor.fetchone(); new=row is None
                    if new:
                        require(not ingest and previous is None)
                        cursor.execute(f'''insert into private.content_ops_button_confirmation_deliveries
                            (delivery_id,approval_id,card_id,actor_id,target_binding,request_sha256,expires_at)
                            values(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s) returning {_COLUMNS}''',pinned)
                        row=cursor.fetchone()
                    require(type(row) is tuple and len(row)==12)
                    _same(row[:7],pinned)
                    require(before<=row[7] if new else row[7]<=before)
                    require(row[8] in ('unknown','response_matched'))
                    if not ingest:
                        require(previous is None or (previous[-1] is True and row[8]=='response_matched'))
                        if row[8]=='unknown': _same(row[9:],(None,None,None))
                    else:
                        receipt=self._loader(cursor=cursor,delivery_id=delivery_id,request_sha256=request_hash)
                        recorded=_response(receipt,target,plan,b,row)
                        require(b.digest('card-message@2',plan.bot_id,plan.chat_id,recorded.message_id)
                                not in {part['message_binding'] for part in locked[0]['parts']})
                        values=(*astuple(recorded.target),*astuple(recorded)[1:])
                        response_hash=hashlib.sha256(receipt.raw_response).hexdigest()
                        if previous is not None:
                            _same(previous,values)
                            _same(row[8:],('response_matched',response_hash,receipt.observed_at,target.approval_id))
                        else:
                            _same(row[8:],('unknown',None,None,None))
                            cursor.execute(f'''insert into private.content_ops_button_markup_confirmations
                                (approval_id,attempt_id,card_id,actor_id,human_binding,plan_seal,
                                 original_receipt_sha256,started_at,expires_at,bot_id,chat_id,message_id,
                                 thread_id,message_date,delivered_at,active)
                                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                returning {_PROMPT_COLUMNS}''',values)
                            _same(cursor.fetchone(),values)
                            cursor.execute(f'''update private.content_ops_button_confirmation_deliveries
                                set status='response_matched',response_sha256=%s,observed_at=%s,recorded_approval_id=%s::uuid
                                where delivery_id=%s::uuid and status='unknown' returning {_COLUMNS}''',
                                (response_hash,receipt.observed_at,target.approval_id,delivery_id))
                            _same(cursor.fetchone(),(*row[:8],'response_matched',response_hash,receipt.observed_at,target.approval_id))
                    after=_clock(cursor,plan,b)
                    require(after>=before and row[7]<=after)
                    if ingest: require(receipt.observed_at<=after)
            return dict(status='confirmation_recorded' if ingest else row[8],
                **({'reused':previous is not None} if ingest else {'new_attempt':new}),execution_authorized=False)
        except Exception:
            raise ConfirmationDeliveryError('confirmation_delivery_unknown') from None
