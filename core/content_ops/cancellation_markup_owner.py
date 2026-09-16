"""Local-only injected ledger adapter. No sender, credentials or mounted route.

The trusted courier supplies its authenticated actor/human and original sealed
plan. Neither Python arguments nor matched response shapes prove transport
origin. Runtime roles have no access; no privileged fallback is installed.
"""
from datetime import datetime, timezone
import json

from core.content_ops.cancellation_markup import (
    CancellationMarkupError, CancellationMarkupPlan, _check_plan,
    validate_cancellation_markup_response,
)
from core.content_ops.review_cancellation import CancellationSigner, cancellation_target
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import _positive_int
from core.content_ops.prompt_receipt import canonical_uuid


def require(ok):
    if not ok:
        raise CancellationMarkupError('cancellation_markup_ledger_unknown')


def _inputs(plan, attempt_id, actor_id, human_id, bindings, signer):
    require(type(plan) is CancellationMarkupPlan and type(bindings) is EditBindings
            and type(signer) is CancellationSigner)
    require(canonical_uuid(attempt_id) and canonical_uuid(actor_id)
            and _positive_int(human_id) and human_id != plan.bot_id)
    # Historical exact readback is allowed after expiration; only the SQL
    # first-reservation branch may create an attempt and enforces DB time.
    _check_plan(plan, bindings, plan.started_at)


def _load_target(cursor, plan, bindings, signer):
    cursor.execute('''select to_jsonb(r),to_jsonb(c)
        from private.content_ops_button_cards c
        join private.content_ops_button_reviews r on r.id=c.review_id where c.id=%s::uuid''',
        (plan.card_id,))
    row = cursor.fetchone()
    require(row is not None and len(row) == 2)
    target = cancellation_target(row[0], row[1], bindings)
    require((target.bot_binding, target.room_binding, target.message_binding, target.thread_id) ==
            (bindings.digest('bot',plan.bot_id),bindings.digest('room',plan.bot_id,plan.chat_id),
             bindings.digest('card-message@2',plan.bot_id,plan.chat_id,plan.message_id),plan.thread_id))
    token = json.loads(plan.request_json)['reply_markup']['inline_keyboard'][-1][0]['callback_data']
    require(signer.verify(token,target,now=plan.started_at) == plan.card_id)
    return target


def _result(row, plan, attempt_id, reserve):
    require(row is not None and len(row) == 1 and type(row[0]) is dict)
    result = row[0]
    flag = 'new_attempt' if reserve else 'reused'
    require(set(result) == {'status','attempt_id','card_id',flag,'execution_authorized'})
    require(result['attempt_id'] == attempt_id and result['card_id'] == plan.card_id
            and type(result[flag]) is bool and result['execution_authorized'] is False)
    require(result['status'] in (('unknown','response_matched') if reserve else ('response_matched',)))
    require(not reserve or not result['new_attempt'] or result['status'] == 'unknown')
    return dict(result)


class PostgresCancellationMarkupLedger:
    """Fresh non-autocommit connections. Unknown commit -> reconcile, no retry."""
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def reserve(self, *, enabled=False, plan=None, attempt_id=None, actor_id=None,
                human_id=None, bindings=None, signer=None):
        if enabled is not True:
            return None
        try:
            _inputs(plan,attempt_id,actor_id,human_id,bindings,signer)
            with self._connection_factory() as connection:
                require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    target = _load_target(cursor,plan,bindings,signer)
                    cursor.execute('''select private.reserve_content_ops_button_markup_attempt(
                        %s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s)''',
                        (attempt_id,plan.card_id,actor_id,target.bot_binding,
                         bindings.digest('human',plan.bot_id,human_id),target.parent_binding_sha256,
                         plan.seal,bindings.digest('cancellation-markup-response@1',plan.seal),
                         datetime.fromtimestamp(plan.started_at,timezone.utc),
                         datetime.fromtimestamp(plan.expires_at,timezone.utc)))
                    result = _result(cursor.fetchone(),plan,attempt_id,True)
            return result
        except Exception:
            raise CancellationMarkupError('cancellation_markup_ledger_unknown') from None

    def record_response(self, *, enabled=False, plan=None, attempt_id=None, actor_id=None,
            human_id=None, bindings=None, signer=None, http_status=None, raw_response=None,
            observed_at=None):
        if enabled is not True:
            return None
        try:
            _inputs(plan,attempt_id,actor_id,human_id,bindings,signer)
            require(type(observed_at) is datetime and observed_at.utcoffset() is not None)
            receipt = validate_cancellation_markup_response(enabled=True,plan=plan,bindings=bindings,
                http_status=http_status,raw_response=raw_response,observed_at=int(observed_at.timestamp()))
            with self._connection_factory() as connection:
                require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    target = _load_target(cursor,plan,bindings,signer)
                    cursor.execute('''select private.record_content_ops_button_markup_response(
                        %s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s)''',
                        (attempt_id,plan.card_id,actor_id,target.bot_binding,
                         bindings.digest('human',plan.bot_id,human_id),plan.seal,
                         receipt['receipt_sha256'],observed_at))
                    result = _result(cursor.fetchone(),plan,attempt_id,False)
            return result
        except Exception:
            raise CancellationMarkupError('cancellation_markup_ledger_unknown') from None
