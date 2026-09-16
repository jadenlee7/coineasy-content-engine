"""Guard contracts with synthetic cursor rows; no production access."""
from datetime import datetime, timezone

import pytest

from core.content_ops.cancellation_markup import CancellationMarkupError
from core.content_ops.cancellation_markup_guard import PostgresCancellationMarkupGuard
from test_content_ops_cancellation_markup_owner import args, Connection
from test_content_ops_review_cancellation import records, NOW


_IDENTITY = args()
_IDENTITY.pop('enabled')


def identity():
    return dict(_IDENTITY)


class GuardConnection(Connection):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rows = [('5000','10000'), (records()[1],), records(),
                     (datetime.fromtimestamp(NOW+.1,timezone.utc),),
                     (datetime.fromtimestamp(NOW+.1,timezone.utc),)]

    def cursor(self):
        original = super().cursor()
        class Cursor:
            def __enter__(self): original.__enter__(); return self
            def __exit__(self,*args): return original.__exit__(*args)
            def execute(self,sql,params=()): return original.execute(sql,params)
            def fetchone(self): return original.fetchone()
        return Cursor()


def guard(connection, **overrides):
    kwargs = dict(enabled=True, authorize_plan=lambda **kwargs: True)
    kwargs.update(overrides)
    return PostgresCancellationMarkupGuard(lambda:connection, **kwargs)


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_disabled_guard_zero_connections(enabled):
    calls=[]
    owner=PostgresCancellationMarkupGuard(lambda:calls.append(1),enabled=enabled)
    with pytest.raises(CancellationMarkupError,match='^cancellation_markup_guard_refused$'):
        with owner.hold(**identity()): pass
    assert calls == []


def test_missing_authority_cannot_open_db():
    calls=[]
    owner=PostgresCancellationMarkupGuard(lambda:calls.append(1),enabled=True)
    with pytest.raises(CancellationMarkupError):
        with owner.hold(**identity()): pass
    assert calls == []


def test_guard_lock_then_lineage_then_authority_and_db_clock():
    conn=GuardConnection(); seen=[]
    def authorize(**kwargs): seen.append(kwargs); return True
    with guard(conn,authorize_plan=authorize).hold(**identity()) as lease:
        assert conn.exits == []
        assert lease.validate(**identity(),now=datetime.fromtimestamp(NOW,timezone.utc)) is True
        assert len(conn.calls) == 5
        assert 'lock_content_ops_button_markup_card' in conn.calls[1][0]
        assert conn.calls[1][1][0:2] == (identity()['plan'].card_id,identity()['actor_id'])
        assert conn.calls[-1][0] == 'select clock_timestamp()'
    assert conn.exits == [None] and len(seen) == 1
    with pytest.raises(CancellationMarkupError):
        lease.validate(**identity(),now=datetime.fromtimestamp(NOW,timezone.utc))
    assert len(conn.calls) == 5


@pytest.mark.parametrize('active',[False,None,1,'true'])
def test_inactive_card_refused_before_lease(active):
    conn=GuardConnection(); conn.rows[1][0]['active']=active
    with pytest.raises(CancellationMarkupError):
        with guard(conn).hold(**identity()): pytest.fail('lease must not be issued')
    assert len(conn.calls) == 2 and conn.exits[0] is not None


@pytest.mark.parametrize('answer',[False,None,1,'true'])
def test_exact_plan_authority_required(answer):
    conn=GuardConnection()
    with pytest.raises(CancellationMarkupError):
        with guard(conn,authorize_plan=lambda **kwargs:answer).hold(**identity()) as lease:
            lease.validate(**identity(),now=datetime.fromtimestamp(NOW,timezone.utc))
    assert len(conn.calls) == 4 and conn.exits[0] is not None


@pytest.mark.parametrize('db_time', [NOW-6,NOW+301])
def test_db_time_stale_or_expired_refused(db_time):
    conn=GuardConnection(); conn.rows[-2]=(datetime.fromtimestamp(db_time,timezone.utc),)
    with pytest.raises(CancellationMarkupError):
        with guard(conn).hold(**identity()) as lease:
            lease.validate(**identity(),now=datetime.fromtimestamp(NOW,timezone.utc))
    assert conn.exits[0] is not None


def test_autocommit_refused_and_ack_loss_redacted():
    conn=GuardConnection(); conn.autocommit=True
    with pytest.raises(CancellationMarkupError):
        with guard(conn).hold(**identity()): pass
    assert conn.calls == []
    conn=GuardConnection(commit_error=True)
    with pytest.raises(CancellationMarkupError,match='^cancellation_markup_guard_refused$'):
        with guard(conn).hold(**identity()) as lease:
            lease.validate(**identity(),now=datetime.fromtimestamp(NOW,timezone.utc))
    assert conn.exits == [None]


@pytest.mark.parametrize('after',[
    datetime.fromtimestamp(NOW+300,timezone.utc),
    datetime.fromtimestamp(NOW-1,timezone.utc),
    datetime.fromtimestamp(NOW+5.1,timezone.utc),
    datetime.fromtimestamp(NOW), None,
])
def test_authority_check_cannot_outlive_plan_or_hide_clock_change(after):
    conn=GuardConnection(); conn.rows[-1]=(after,)
    with pytest.raises(CancellationMarkupError):
        with guard(conn).hold(**identity()) as lease:
            lease.validate(**identity(),now=datetime.fromtimestamp(NOW,timezone.utc))
    assert len(conn.calls)==5 and conn.exits[0] is not None
