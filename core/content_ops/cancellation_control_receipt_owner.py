"""Default-OFF original-control evidence ingestion for the LOCAL proposal.

receipt_loader is a mandatory trusted owner boundary: read the original,
authenticated, immutable send receipt on the supplied cursor. It must bind
the receipt digest, card and precise delivery observation to those stored
bytes, never to request JSON, a webhook, or a fresh provider fetch. This DTO
does not authenticate itself. No production loader, route, credentials,
approval issuer or transport is installed by this module.
"""
from dataclasses import astuple, dataclass
from datetime import datetime
import json

from core.content_ops.cancellation_markup import (
    CancellationMarkupError, _check_plan, encode, prepare_cancellation_markup,
)
from core.content_ops.cancellation_markup_authority import OriginalControlEvidence
from core.content_ops.cancellation_markup_owner import _inputs
from core.content_ops.prompt_receipt import digest
from core.content_ops.prompt_reservation import canonical_timestamp
from core.content_ops.review_edit_ingress import _UNSUPPORTED
from core.content_ops.review_ingress import _unique_object, _reject_constant, _positive_int


@dataclass(frozen=True, repr=False)
class StoredOriginalControlReceipt:
    card_id: str
    receipt_sha256: str
    http_status: int
    raw_response: bytes
    observed_at: datetime


_COLUMNS = '''card_id::text,receipt_sha256,parent_binding_sha256,
    bot_id,chat_id,message_id,thread_id,delivered_at,message_date,text_sha256,
    entities_json,markup_json'''


def _require(ok):
    if not ok:
        raise CancellationMarkupError('original_control_receipt_unknown')


def _evidence(receipt, review, card, identity):
    plan = identity['plan']
    _require(type(receipt) is StoredOriginalControlReceipt)
    _require(receipt.card_id == plan.card_id == card['id'])
    _require(digest(receipt.receipt_sha256)
             and receipt.receipt_sha256 == card['bindings']['card_receipt'])
    _require(type(receipt.observed_at) is datetime and receipt.observed_at.utcoffset() is not None)
    _require(canonical_timestamp(receipt.observed_at) == canonical_timestamp(card['delivered_at']))
    _require(type(receipt.http_status) is int and receipt.http_status == 200)
    _require(type(receipt.raw_response) is bytes and 0 < len(receipt.raw_response) <= 32768)
    data = json.loads(receipt.raw_response.decode(), object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)
    _require(type(data) is dict and set(data) == {'ok', 'result'} and data['ok'] is True)
    message = data['result']
    _require(type(message) is dict and not (_UNSUPPORTED & message.keys()))
    _require(not ({'inline_message_id', 'reply_to_message', 'rich_message'} & message.keys()))
    chat, sender = message.get('chat'), message.get('from')
    _require(type(chat) is dict and type(sender) is dict)
    _require(chat.get('type') == 'supergroup' and not ({'username', 'linked_chat_id'} & chat.keys()))
    _require(sender.get('is_bot') is True)
    _require(type(message.get('entities', [])) is list)
    if 'message_thread_id' in message:
        _require(_positive_int(message['message_thread_id']))
    # Rebuild from the stored original, not from caller-provided text/markup.
    # This checks types, keyed destination bindings, parent@2 and cancellation
    # MAC, and preserves every original row without authorizing those actions.
    rebuilt = prepare_cancellation_markup(enabled=True, review=review, card=card,
        bindings=identity['bindings'], signer=identity['signer'],
        bot_id=sender.get('id'), chat_id=chat.get('id'), message_id=message.get('message_id'),
        thread_id=message.get('message_thread_id'), message_date=message.get('date'),
        control_text=message.get('text'), control_entities=message.get('entities', []),
        existing_markup=message.get('reply_markup'), now=plan.started_at, expires_at=plan.expires_at)
    _require(rebuilt == plan)
    return OriginalControlEvidence(plan.card_id, receipt.receipt_sha256,
        card['bindings']['parent_binding'], plan.bot_id, plan.chat_id, plan.message_id,
        plan.thread_id, receipt.observed_at, plan.message_date, plan.text_sha256,
        plan.entities_json, encode(message['reply_markup']))


def _same_row(row, evidence):
    expected = astuple(evidence)
    _require(row is not None and len(row) == len(expected))
    _require(all(type(actual) is type(wanted) and actual == wanted
                 for actual, wanted in zip(row, expected)))


def _clock(cursor, plan, bindings):
    cursor.execute('select clock_timestamp()')
    row = cursor.fetchone()
    _require(row is not None and len(row) == 1 and type(row[0]) is datetime
             and row[0].utcoffset() is not None)
    _check_plan(plan, bindings, int(row[0].timestamp()))
    return row[0]


class PostgresOriginalControlReceiptOwner:
    """One transaction, immutable exact dedupe. Unknown commit never retries.

    Only active, currently scoped cards with a live sealed plan can import.
    An expired/revoked unknown outcome requires separate read-only owner
    reconciliation, not a refreshed receipt/plan or automatic ingestion retry.
    """
    def __init__(self, connection_factory, *, receipt_loader=None):
        self._factory = connection_factory
        self._loader = receipt_loader

    def ingest(self, *, enabled=False, **identity):
        if enabled is not True:
            return None
        try:
            _inputs(**identity)
            _require(callable(self._loader))
            plan, bindings = identity['plan'], identity['bindings']
            with self._factory() as connection:
                _require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('lock_timeout','5000',true), "
                                   "set_config('statement_timeout','10000',true)")
                    cursor.fetchone()
                    cursor.execute('''select to_jsonb(private.lock_content_ops_button_markup_card(
                        %s::uuid,%s::uuid,%s,%s))''',
                        (plan.card_id, identity['actor_id'], bindings.digest('bot', plan.bot_id),
                         bindings.digest('human', plan.bot_id, identity['human_id'])))
                    locked = cursor.fetchone()
                    _require(locked is not None and len(locked) == 1 and type(locked[0]) is dict
                             and locked[0].get('id') == plan.card_id and locked[0].get('active') is True)
                    cursor.execute('''select to_jsonb(r),to_jsonb(c)
                        from private.content_ops_button_cards c
                        join private.content_ops_button_reviews r on r.id=c.review_id
                        where c.id=%s::uuid''', (plan.card_id,))
                    records = cursor.fetchone()
                    _require(records is not None and len(records) == 2 and records[1] == locked[0])
                    before = _clock(cursor, plan, bindings)
                    receipt = self._loader(cursor=cursor, card_id=plan.card_id,
                                           receipt_sha256=locked[0]['bindings']['card_receipt'])
                    evidence = _evidence(receipt, records[0], records[1], identity)
                    cursor.execute(f'''select {_COLUMNS}
                        from private.content_ops_button_control_evidence
                        where card_id=%s::uuid for share''', (plan.card_id,))
                    existing = cursor.fetchone()
                    after = _clock(cursor, plan, bindings)
                    _require(after >= before and evidence.delivered_at <= after)
                    reused = existing is not None
                    if reused:
                        _same_row(existing, evidence)
                    else:
                        # Card lock serializes participating importers. No
                        # UPSERT, overwrite or delete path for conflicting rows.
                        cursor.execute(f'''insert into private.content_ops_button_control_evidence
                            (card_id,receipt_sha256,parent_binding_sha256,bot_id,chat_id,message_id,
                             thread_id,delivered_at,message_date,text_sha256,entities_json,markup_json)
                            values(%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            returning {_COLUMNS}''', astuple(evidence))
                        _same_row(cursor.fetchone(), evidence)
            # Return only after commit; no receipt bytes, private IDs or tokens.
            return {'status': 'original_control_recorded', 'reused': reused,
                    'execution_authorized': False}
        except Exception:
            raise CancellationMarkupError('original_control_receipt_unknown') from None
