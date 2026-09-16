"""Local-only stored prompt reader and durable callback decision adapter.

Use as the registrar's request-scoped decision_authenticator on its SAME cursor:
card/evidence are already locked; then prompt -> event -> approval. No own
connection/commit, live receipt importer, route, credentials or transport. A
stored row must originate from a separately verified original delivery receipt,
never from callback JSON. Synthetic fixtures are not production proof.
"""
from datetime import datetime
import hashlib
import json

from core.content_ops.cancellation_markup_confirmation import (
    MarkupConfirmationError, MarkupConfirmationTarget, StoredMarkupConfirmation,
    TelegramMarkupConfirmation, _require, _time,
)
from core.content_ops.prompt_receipt import canonical_uuid
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import _parse_callback, _unique_object, _reject_constant


_PROMPT_COLUMNS = '''approval_id::text,attempt_id::text,card_id::text,actor_id::text,
    human_binding,plan_seal,original_receipt_sha256,started_at,expires_at,
    bot_id,chat_id,message_id,thread_id,message_date,delivered_at,active'''
_EVENT_COLUMNS = '''bot_binding,callback_binding,update_binding,approval_id::text,
    payload_sha256,received_at'''


class PostgresMarkupConfirmationReader:
    def __init__(self, *, enabled=False):
        self._enabled = enabled

    def __call__(self, *, cursor=None, approval_id=None):
        if self._enabled is not True:
            return None
        try:
            _require(canonical_uuid(approval_id) and cursor is not None)
            cursor.execute(f'''select {_PROMPT_COLUMNS}
                from private.content_ops_button_markup_confirmations
                where approval_id=%s::uuid for share''', (approval_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            _require(type(row) is tuple and len(row) == 16)
            return StoredMarkupConfirmation(MarkupConfirmationTarget(*row[:9]), *row[9:])
        except Exception:
            raise MarkupConfirmationError('markup_confirmation_refused') from None


class PostgresTelegramMarkupDecision:
    """Default-OFF transaction-bound authentication + insert-once event receipt.

    Exact replays use the first event timestamp, never retry time. Replays older
    than five seconds fail closed; use separate read-only reconciliation after
    an uncertain commit. One callback/update/approval maps to one immutable row.
    Any changed payload, actor, prompt, action or duplicate different click is
    refused. Raw callback bodies, Telegram IDs and secrets are not stored here.
    Caller must preserve the ingress's original received_at before first insert.
    """
    def __init__(self, *, enabled=False, raw_body=None, headers=(), policy=None,
                 bindings=None, received_at=None):
        self._enabled = enabled
        # Reuse request snapshotting and raw validation instead of a second route.
        self._request = TelegramMarkupConfirmation(enabled=True, raw_body=raw_body,
            headers=headers, policy=policy, bindings=bindings, received_at=received_at)

    def __call__(self, *, cursor=None, approval_id=None):
        if self._enabled is not True:
            return None
        try:
            req = self._request
            received = _time(req._received_at)
            query, _actor = _parse_callback(req._raw, req._headers, req._policy, int(received))
            _require(canonical_uuid(approval_id) and type(req._bindings) is EditBindings)
            _require(cursor is not None)
            update = json.loads(req._raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            b, bot = req._bindings, req._policy.bot_id
            fields = (b.digest('bot', bot), b.digest('markup-callback@1', bot, query['id']),
                      b.digest('markup-update@1', bot, update['update_id']), approval_id,
                      hashlib.sha256(req._raw).hexdigest())
            receipt = PostgresMarkupConfirmationReader(enabled=True)(cursor=cursor, approval_id=approval_id)
            _require(type(receipt) is StoredMarkupConfirmation and receipt.active is True)
            cursor.execute(f'''select {_EVENT_COLUMNS}
                from private.content_ops_button_markup_confirmation_events
                where callback_binding=%s or update_binding=%s or approval_id=%s::uuid
                for update''', (fields[1], fields[2], approval_id))
            previous = cursor.fetchone()
            received_at = req._received_at
            if previous is not None:
                _require(type(previous) is tuple and len(previous) == 6 and previous[:5] == fields)
                _require(type(previous[5]) is datetime and _time(previous[5]) <= received)
                received_at = previous[5]
            decision = TelegramMarkupConfirmation(enabled=True, raw_body=req._raw,
                headers=req._headers, policy=req._policy, bindings=b, received_at=received_at,
                receipt_reader=lambda **_: receipt)(cursor=cursor, approval_id=approval_id)
            if previous is None:
                values = (*fields, received_at)
                cursor.execute(f'''insert into private.content_ops_button_markup_confirmation_events
                    (bot_binding,callback_binding,update_binding,approval_id,payload_sha256,received_at)
                    values(%s,%s,%s,%s::uuid,%s,%s) returning {_EVENT_COLUMNS}''', values)
                row = cursor.fetchone()
                _require(type(row) is tuple and row == values
                         and all(type(a) is type(b) for a,b in zip(row,values)))
            return decision
        except Exception:
            raise MarkupConfirmationError('markup_confirmation_refused') from None
