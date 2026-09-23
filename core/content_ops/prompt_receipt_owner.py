"""Internal, unmounted, default-OFF receipt persistence. Never a send endpoint.

The injected owner must authenticate the provider transport. This adapter reads
an existing locked reservation and checks keyed packet/control-card lineage;
neither those hashes nor caller-supplied response bytes prove transport origin.
Those live integrations are NOT implemented here. The restricted runtime role
lacks the proposed prompt capability grants; it must never receive direct
prompt INSERT or an admin/service-role credential as a fallback. No retries,
sends or enrollment.
"""
from __future__ import annotations

from datetime import datetime
import json
import math

from core.content_ops.prompt_receipt import (
    PromptReceiptError, canonical_uuid, validate_prompt_response,
)
from core.content_ops.prompt_reservation import load_reserved_attempt
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import _positive_int


def _require(condition):
    if not condition:
        raise PromptReceiptError('prompt_registration_unknown')


def _registration(value):
    _require(type(value) is dict and set(value) == {
        'status', 'prompt_id', 'reused', 'execution_authorized',
    })
    _require(value['status'] == 'prompt_registered'
             and canonical_uuid(value['prompt_id'])
             and type(value['reused']) is bool
             and value['execution_authorized'] is False)
    return dict(value)


class PostgresPromptReceiptOwner:
    """Use a fresh psycopg-compatible, commit/rollback/close context per call.

    observed_at is the ORIGINAL precise owner observation of the authenticated
    response, not database insertion time or a fresh timestamp on reconciliation.
    provider_message_date only constrains chronology; it does not replace that
    observation. Replays must preserve it exactly. A caller receiving unknown
    must independently reconcile durable state, never retry a send or this write
    automatically. No connection factory is wired into the application.
    """

    def __init__(self, connection_factory):
        self._connect = connection_factory

    def record_prompt_receipt(self, receipt=None, *, enabled=False,
                              observed_at=None, version_fingerprint=None):
        """Legacy preconstructed-receipt path is closed, including for replay."""
        if enabled is not True:
            return {'status': 'disabled', 'execution_authorized': False}
        raise PromptReceiptError('prompt_registration_unknown')

    def record_prompt_response(self, *, enabled=False, attempt_id=None, bindings=None,
                               bot_id=None, chat_id=None, human_id=None, thread_id=None,
                               http_status=None, raw_response=None, observed_at=None):
        """Only an existing reservation may validate an owner-authenticated response.

        Raw responses are internal transport input, never an HTTP request body.
        No route/transport is installed. Reads and registration share one transaction.
        """
        if enabled is not True:
            return {'status': 'disabled', 'execution_authorized': False}
        try:
            _require(canonical_uuid(attempt_id) and type(bindings) is EditBindings)
            _require(_positive_int(bot_id) and _positive_int(human_id) and bot_id != human_id)
            _require(type(chat_id) is int and -(2**52) < chat_id < 0)
            _require(thread_id is None or _positive_int(thread_id))
            _require(type(http_status) is int and http_status == 200)
            _require(type(raw_response) is bytes and 0 < len(raw_response) <= 32768)
            _require(type(observed_at) is datetime and observed_at.utcoffset() is not None)
            with self._connect() as connection:
                _require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    # No reservation creation here: missing attempt fails at the
                    # first read. Item -> review -> card -> attempt, then current
                    # identity/action checks, then receipt/registration.
                    cursor.execute("""
                        select i.id from public.content_items i
                        join private.content_ops_button_reviews r
                          on r.content_item_id=i.id and r.workspace_id=i.workspace_id
                        join private.content_ops_button_prompt_attempts a on a.review_id=r.id
                        where a.id=%s::uuid for update of i
                    """, (attempt_id,))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and row[0] is not None)
                    locked_item_id = row[0]
                    cursor.execute("""
                        select to_jsonb(r) from private.content_ops_button_reviews r
                        join private.content_ops_button_prompt_attempts a on a.review_id=r.id
                        where a.id=%s::uuid
                          and content_item_id=%s::uuid
                          and exists (select 1 from public.content_items i
                              where i.id=r.content_item_id and i.workspace_id=r.workspace_id)
                          and state='edit_requested' for update of r
                    """, (attempt_id, locked_item_id))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and type(row[0]) is dict)
                    review = row[0]
                    cursor.execute("""
                        select to_jsonb(c) from private.content_ops_button_cards c
                        join private.content_ops_button_prompt_attempts a on a.card_id=c.id
                        where a.id=%s::uuid and c.review_id=%s::uuid for share of c
                    """, (attempt_id, review['id']))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and type(row[0]) is dict)
                    card = row[0]
                    cursor.execute("""
                        select to_jsonb(a) from private.content_ops_button_prompt_attempts a
                        where a.id=%s::uuid and a.review_id=%s::uuid and a.card_id=%s::uuid for share
                    """, (attempt_id, review['id'], card['id']))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and type(row[0]) is dict)
                    stored = row[0]
                    _require(stored['id'] == attempt_id)
                    cursor.execute("""
                        with checked as materialized (
                            select private.reserve_content_ops_button_prompt_for_runtime(
                                %s::uuid,%s::uuid,%s::uuid,%s,%s) as result
                        ) select result, clock_timestamp() from checked
                    """, (card['id'], attempt_id, stored['actor_id'],
                          bindings.digest('human', bot_id, human_id), stored['edit_action_key']))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 2 and type(row[0]) is dict
                             and row[0] == {'status':'attempt_recorded','attempt_id':attempt_id,
                                            'reused':True,'execution_authorized':False}
                             and row[0]['reused'] is True and row[0]['execution_authorized'] is False)
                    attempt = load_reserved_attempt(review, card, stored, bindings=bindings,
                        bot_id=bot_id, chat_id=chat_id, human_id=human_id, thread_id=thread_id,
                        observed_at=observed_at, db_now=row[1])
                    receipt = validate_prompt_response(enabled=True, attempt=attempt,
                        bindings=bindings, http_status=http_status, raw_response=raw_response,
                        observed_at=math.floor(observed_at.timestamp()))
                    # @2 cards use one message namespace for card and all parts.
                    # Prompt reply binding remains its separate established domain.
                    message_id = json.loads(raw_response)['result']['message_id']
                    message_binding = bindings.digest('card-message@2', bot_id, chat_id, message_id)
                    _require(message_binding not in {card['bindings']['message'],
                        *(p['message_binding'] for p in card['parts'])})
                    fields = (receipt.id, receipt.review_id, receipt.actor_id, receipt.epoch,
                              receipt.edit_action_key, receipt.bot_binding, receipt.room_binding,
                              receipt.message_binding, receipt.receipt_sha256, observed_at,
                              datetime.fromisoformat(stored['expires_at']))
                    # A narrow security-definer capability atomically writes
                    # the receipt and registers the prompt. The login role
                    # retains no direct INSERT or base-RPC EXECUTE grant.
                    cursor.execute("""
                        select private.register_content_ops_button_prompt_response_for_runtime(
                            %s::uuid,%s,%s,%s,%s,%s)
                    """, (receipt.id, receipt.human_binding, receipt.message_binding,
                          message_binding, receipt.receipt_sha256, observed_at))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1)
                    result = _registration(row[0])
                    # Never overwrite an unknown/rejected/conflicting receipt, or
                    # freshen delivery time. Check every persisted input on replay.
                    cursor.execute("""
                        select review_id=%s::uuid and actor_id=%s::uuid and epoch=%s
                            and edit_action_key=%s and bot_binding=%s and room_binding=%s
                            and message_binding=%s and receipt_sha256=%s
                            and delivered_at=%s and reservation_expires_at=%s and outcome='sent'
                        from private.content_ops_button_prompt_receipts
                        where id=%s::uuid for share
                    """, (*fields[1:], receipt.id))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and row[0] is True)
            # Malformed output rolls back BOTH writes. A failed commit ACK yields
            # unknown even if the server committed. Never return optimistic success.
            return result
        except Exception:
            raise PromptReceiptError('prompt_registration_unknown') from None
