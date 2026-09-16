"""Owner-only durable dispatch consumption; default OFF, no transport or route.

A committed reservation is not proof that a relay request has not been used.
Consume the distinct dispatch record before entering the final send guard.
Only a fresh, acknowledged commit permits the coordinator to continue. Lost
commit acknowledgements and existing records never permit another attempt.
This is not a remote relay authentication/replay barrier; no endpoint mounts it.
"""
from dataclasses import astuple
from datetime import datetime, timezone
import hashlib

from core.content_ops.cancellation_markup_owner import _inputs, _load_target
from core.content_ops.cancellation_control_receipt_owner import _clock
from core.content_ops.cancellation_markup_confirmation import _target
from core.content_ops.confirmation_delivery_owner import (
    confirmation_send_request, _COLUMNS as DELIVERY_COLUMNS, _same,
)
from core.content_ops.confirmation_send_authority import (
    ExactConfirmationSendAuthority, PostgresConfirmationSendReader,
)
from core.content_ops.prompt_receipt import canonical_uuid


class ConfirmationDispatchError(ValueError):
    pass


_COLUMNS = 'delivery_id::text,permission_id::text,card_id::text,request_sha256,consumed_at'


def require(value):
    if not value:
        raise ConfirmationDispatchError('confirmation_dispatch_unknown')


class PostgresConfirmationDispatchOwner:
    def __init__(self, connection_factory):
        self._factory = connection_factory

    def consume(self, *, enabled=False, target=None, delivery_id=None, **identity):
        if enabled is not True:
            return None
        try:
            _inputs(**identity); _target(target)
            require(canonical_uuid(delivery_id))
            plan, bindings = identity['plan'], identity['bindings']
            request_hash = hashlib.sha256(confirmation_send_request(target, plan, bindings)).hexdigest()
            with self._factory() as connection:
                require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('lock_timeout','5000',true), "
                                   "set_config('statement_timeout','10000',true)")
                    cursor.fetchone()
                    cursor.execute('''select to_jsonb(private.lock_content_ops_button_markup_card(
                        %s::uuid,%s::uuid,%s,%s))''', (plan.card_id, identity['actor_id'],
                        bindings.digest('bot', plan.bot_id),
                        bindings.digest('human', plan.bot_id, identity['human_id'])))
                    locked = cursor.fetchone()
                    require(type(locked) is tuple and len(locked) == 1)
                    _load_target(cursor, plan, bindings, identity['signer'])
                    before = _clock(cursor, plan, bindings)
                    reader = PostgresConfirmationSendReader(enabled=True)
                    evidence, permission = reader(cursor=cursor, card_id=plan.card_id,
                        delivery_id=delivery_id, actor_id=identity['actor_id'])
                    authority = ExactConfirmationSendAuthority(enabled=True, target=target,
                        delivery_id=delivery_id, reader=lambda **_: (evidence, permission))
                    require(authority(cursor=cursor, locked_card=locked[0], now=before, **identity))
                    cursor.execute(f'''select {DELIVERY_COLUMNS}
                        from private.content_ops_button_confirmation_deliveries
                        where delivery_id=%s::uuid or card_id=%s::uuid for update''',
                        (delivery_id, plan.card_id))
                    delivery = cursor.fetchone()
                    require(type(delivery) is tuple and len(delivery) == 12)
                    _same(delivery[:7], (delivery_id, target.approval_id, plan.card_id,
                        identity['actor_id'], bindings.digest('confirmation-delivery-target@1', *astuple(target)),
                        request_hash, datetime.fromtimestamp(plan.expires_at, timezone.utc)))
                    require(type(delivery[7]) is datetime and delivery[7].utcoffset() is not None
                        and permission.authorized_at <= delivery[7] <= before
                        and delivery[8] == 'unknown')
                    _same(delivery[9:], (None, None, None))
                    # A previously captured response, even not yet imported,
                    # must never be followed by a new dispatch.
                    cursor.execute('''select delivery_id::text
                        from private.content_ops_button_confirmation_sources
                        where delivery_id=%s::uuid for share''', (delivery_id,))
                    require(cursor.fetchone() is None)
                    pinned = (delivery_id, permission.permission_id, plan.card_id, request_hash)
                    cursor.execute(f'''select {_COLUMNS}
                        from private.content_ops_button_confirmation_dispatches
                        where delivery_id=%s::uuid or permission_id=%s::uuid or card_id=%s::uuid
                        for update''', pinned[:3])
                    row = cursor.fetchone()
                    fresh = row is None
                    if fresh:
                        cursor.execute(f'''insert into private.content_ops_button_confirmation_dispatches
                            (delivery_id,permission_id,card_id,request_sha256)
                            values(%s::uuid,%s::uuid,%s::uuid,%s) returning {_COLUMNS}''', pinned)
                        row = cursor.fetchone()
                    require(type(row) is tuple and len(row) == 5)
                    _same(row[:4], pinned)
                    require(type(row[4]) is datetime and row[4].utcoffset() is not None)
                    after = _clock(cursor, plan, bindings)
                    require(before <= after and delivery[7] <= row[4] <= after
                        and (before <= row[4] if fresh else row[4] <= before))
                result = dict(status='confirmation_dispatch_consumed', new_dispatch=fresh,
                    execution_authorized=False)
            return result  # Never return a fresh result before commit succeeds.
        except Exception:
            raise ConfirmationDispatchError('confirmation_dispatch_unknown') from None
