"""Default-OFF DB row guard; no routes, transport, credentials or grants.

authorize_plan is a REQUIRED trusted, bounded, non-mutating owner check of the
original control receipt and exact operator action authority. It is not a bot
permission check, not a caller boolean, and must not call a publishing provider.
No production implementation/fallback for this authority is provided here.
"""
from contextlib import contextmanager
from datetime import datetime

from core.content_ops.cancellation_markup import CancellationMarkupError, _check_plan
from core.content_ops.cancellation_markup_owner import _inputs, _load_target


def _require(ok):
    if not ok:
        raise CancellationMarkupError('cancellation_markup_guard_refused')


class _Lease:
    def __init__(self, cursor, identity, authorize_plan, locked_card):
        self._cursor = cursor
        self._identity = identity
        self._authorize_plan = authorize_plan
        self._locked_card = locked_card
        self._open = True

    def validate(self, *, now, **identity):
        _require(self._open and identity == self._identity)
        _require(type(now) is datetime and now.utcoffset() is not None)
        plan, bindings = identity['plan'], identity['bindings']
        _check_plan(plan, bindings, int(now.timestamp()))
        # Approval checks receive DB time and this guard's cursor. A concrete
        # owner reader must lock its approval/receipt rows in this transaction.
        self._cursor.execute('select clock_timestamp()')
        row = self._cursor.fetchone()
        _require(row is not None and len(row) == 1)
        db_now = row[0]
        _require(type(db_now) is datetime and db_now.utcoffset() is not None)
        _require(abs((db_now-now).total_seconds()) <= 5)
        _check_plan(plan, bindings, int(db_now.timestamp()))
        _require(self._authorize_plan(**identity, cursor=self._cursor,
            locked_card=self._locked_card, now=db_now) is True)
        self._cursor.execute('select clock_timestamp()')
        after = self._cursor.fetchone()
        _require(after is not None and len(after) == 1 and type(after[0]) is datetime
                 and after[0].utcoffset() is not None and after[0] >= db_now)
        _require(abs((after[0]-now).total_seconds()) <= 5)
        _check_plan(plan, bindings, int(after[0].timestamp()))
        return True


class PostgresCancellationMarkupGuard:
    """Fresh bounded transactions; item/review/card locks, authority share locks.

    The existing private helper reauthenticates the active identity, actor's
    client-scoped reviewer assignment and client. The same row locks used by
    revocation block it until this context exits. This does not block direct
    Telegram edits or nonparticipating external writers.
    """
    def __init__(self, connection_factory, *, enabled=False, authorize_plan=None):
        self._factory = connection_factory
        self._enabled = enabled
        self._authorize_plan = authorize_plan

    @contextmanager
    def hold(self, **identity):
        lease = None
        try:
            _require(self._enabled is True and callable(self._authorize_plan))
            _inputs(**identity)
            plan, bindings = identity['plan'], identity['bindings']
            with self._factory() as connection:
                _require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('lock_timeout','5000',true), "
                                   "set_config('statement_timeout','10000',true)")
                    cursor.fetchone()
                    cursor.execute('''select to_jsonb(private.lock_content_ops_button_markup_card(
                        %s::uuid,%s::uuid,%s,%s))''',
                        (plan.card_id, identity['actor_id'], bindings.digest('bot',plan.bot_id),
                         bindings.digest('human',plan.bot_id,identity['human_id'])))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and type(row[0]) is dict)
                    _require(row[0].get('id') == plan.card_id and row[0].get('active') is True)
                    _load_target(cursor,plan,bindings,identity['signer'])
                    lease = _Lease(cursor,identity,self._authorize_plan,row[0])
                    try:
                        yield lease
                    finally:
                        lease._open = False
        except Exception:
            raise CancellationMarkupError('cancellation_markup_guard_refused') from None
