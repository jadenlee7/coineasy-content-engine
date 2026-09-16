"""Local synthetic courier orchestration, without HTTP, credentials or DB."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from threading import Lock

import pytest

from core.content_ops.cancellation_markup_courier import run_cancellation_markup_once
from core.content_ops.cancellation_markup_owner import PostgresCancellationMarkupLedger
from test_content_ops_cancellation_markup_owner import args, Connection, matched
from test_content_ops_cancellation_markup import response
from test_content_ops_review_cancellation import NOW


class Rig:
    def __init__(self):
        self.events = []; self.held = False; self.saved = None
        self.valid = [True, True]; self.error = None
        self.existing = False; self.existing_status = 'unknown'
        self.kw = args()
        self.times = [NOW+.1, NOW+.2, NOW+.3, NOW+.4]
        self.reply = (200, json.dumps(response(self.kw['plan'])).encode())

    def clock(self):
        return datetime.fromtimestamp(self.times.pop(0), timezone.utc)

    @contextmanager
    def hold(self, **kwargs):
        assert not self.held
        self.held = True; self.events.append('lock')
        try:
            yield self
            if self.error == 'unlock': raise RuntimeError('private detail')
        finally:
            self.held = False; self.events.append('unlock')

    def validate(self, **kwargs):
        assert self.held
        self.events.append('validate')
        return self.valid.pop(0)

    def reserve(self, **kwargs):
        assert not self.held
        self.events.append('reserve')
        if self.error == 'reserve': raise RuntimeError('private commit detail')
        return dict(status=self.existing_status, attempt_id=self.kw['attempt_id'],
            card_id=self.kw['plan'].card_id, new_attempt=not self.existing,
            execution_authorized=False)

    def edit_reply_markup(self, *, body):
        assert self.held
        self.events.append('transport')
        assert set(body) == {'chat_id', 'message_id', 'reply_markup'}
        if self.error == 'transport': raise TimeoutError('private provider detail')
        return self.reply

    def record_response(self, **kwargs):
        assert not self.held
        self.events.append('record'); self.saved = kwargs
        if self.error == 'record': raise RuntimeError('private commit detail')
        return matched()

    def run(self, **overrides):
        kwargs = dict(self.kw, ledger=self, guard=self, transport=self, clock=self.clock)
        kwargs.update(overrides)
        return run_cancellation_markup_once(**kwargs)


@pytest.mark.parametrize('enabled', [False, None, 1, 'true'])
def test_off_zero_io(enabled):
    rig = Rig()
    assert rig.run(enabled=enabled, plan=object()) is None
    assert rig.events == []


def test_exact_single_attempt_under_guard_and_precise_observation():
    rig = Rig()
    assert rig.run() == {'status':'response_matched', 'execution_authorized':False}
    assert rig.events == ['lock','validate','unlock','reserve','lock','validate','transport','unlock','record']
    assert rig.saved['observed_at'] == datetime.fromtimestamp(NOW+.4, timezone.utc)
    assert rig.saved['raw_response'] == rig.reply[1]


@pytest.mark.parametrize('status', ['unknown', 'response_matched'])
def test_existing_attempt_never_resends(status):
    rig = Rig(); rig.existing = True; rig.existing_status = status
    assert rig.run()['status'] == 'existing_' + status
    assert rig.events == ['lock','validate','unlock','reserve']


@pytest.mark.parametrize('stage', ['reserve','transport','record','unlock'])
def test_faults_redacted_never_retry(stage):
    rig = Rig(); rig.error = stage
    assert rig.run() == {'status':'blocked' if stage == 'unlock' else 'unknown', 'execution_authorized':False}
    assert rig.events.count('reserve') == (0 if stage == 'unlock' else 1)
    assert rig.events.count('transport') <= 1
    assert rig.events.count('record') <= 1
    assert not rig.held


@pytest.mark.parametrize('stage', [0,1])
@pytest.mark.parametrize('value', [False, None, 1, 'true'])
def test_guard_requires_exact_true_before_and_after_commit(stage, value):
    rig = Rig(); rig.valid[stage] = value
    assert rig.run()['status'] == ('blocked' if stage == 0 else 'unknown')
    assert 'transport' not in rig.events
    assert ('reserve' in rig.events) == bool(stage)


@pytest.mark.parametrize('stage', [0,1,2,3])
def test_expiry_at_every_boundary(stage):
    rig = Rig(); rig.times[stage] = NOW+300
    assert rig.run()['status'] == ('blocked' if stage == 0 else 'unknown')
    assert ('transport' in rig.events) == (stage == 3)
    assert 'record' not in rig.events


@pytest.mark.parametrize('stage', [1,2,3])
def test_backwards_clock_cannot_confirm_or_send(stage):
    rig = Rig(); rig.times[stage] = NOW
    assert rig.run()['status'] == 'unknown'
    assert ('transport' in rig.events) == (stage == 3)
    assert 'record' not in rig.events


@pytest.mark.parametrize('reply', [(400,b'{}'), (200,b'{bad'), (200,b'{"ok":true,"result":true}')])
def test_bad_response_leaves_unknown_without_record(reply):
    rig = Rig(); rig.reply = reply
    assert rig.run()['status'] == 'unknown'
    assert rig.events.count('transport') == 1 and 'record' not in rig.events


@pytest.mark.parametrize('dependency', ['ledger','guard','transport','clock'])
def test_missing_dependencies_block_before_reservation(dependency):
    rig = Rig()
    assert rig.run(**{dependency:None})['status'] == 'blocked'
    assert rig.events == []


def test_actual_ledger_adapter_commit_ack_precedes_transport():
    rig = Rig(); connections = [Connection(), Connection(matched())]
    def factory():
        rig.events.append('connect')
        return connections.pop(0)
    reserve, record = connections
    result = rig.run(ledger=PostgresCancellationMarkupLedger(factory))
    assert result['status'] == 'response_matched'
    assert reserve.exits == [None] and record.exits == [None]
    assert len(reserve.calls) == len(record.calls) == 2
    assert rig.events == ['lock','validate','unlock','connect','lock','validate','transport','unlock','connect']


def test_actual_ledger_adapter_lost_commit_ack_blocks_transport():
    rig = Rig(); connection = Connection(commit_error=True)
    result = rig.run(ledger=PostgresCancellationMarkupLedger(lambda:connection))
    assert result['status'] == 'unknown'
    assert connection.exits == [None] and 'transport' not in rig.events


@pytest.mark.parametrize('phase', ['reserve','record'])
@pytest.mark.parametrize('field,value', [('execution_authorized',True),('status','published'),
    ('attempt_id','other'),('card_id','other'),('extra','private')])
def test_malformed_ledger_receipts_never_authorize_more_io(phase,field,value):
    rig = Rig(); original = getattr(rig, 'reserve' if phase == 'reserve' else 'record_response')
    def changed(**kwargs):
        result = original(**kwargs); result[field] = value; return result
    setattr(rig, 'reserve' if phase == 'reserve' else 'record_response', changed)
    assert rig.run()['status'] == 'unknown'
    assert rig.events.count('transport') == (0 if phase == 'reserve' else 1)


@pytest.mark.parametrize('fail_transport', [False, True])
def test_eight_contenders_single_transport_even_after_unknown(fail_transport):
    # In-memory owner contract test, not proof of a production distributed lock.
    rig = Rig(); mutex = Lock(); ledger_mutex = Lock(); attempted = False; status = 'unknown'
    @contextmanager
    def hold(**kwargs):
        with mutex:
            rig.held = True
            try: yield rig
            finally: rig.held = False
    def reserve(**kwargs):
        nonlocal attempted
        with ledger_mutex:
            new = not attempted; attempted = True
            return dict(status=status, attempt_id=rig.kw['attempt_id'],
                card_id=rig.kw['plan'].card_id,new_attempt=new,execution_authorized=False)
    def record(**kwargs):
        nonlocal status
        with ledger_mutex:
            status = 'response_matched'; return matched()
    rig.hold = hold; rig.reserve = reserve; rig.record_response = record
    rig.validate = lambda **kwargs: rig.held
    rig.clock = lambda: datetime.fromtimestamp(NOW+.1,timezone.utc)
    if fail_transport: rig.error = 'transport'
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: rig.run(), range(8)))
    assert rig.events.count('transport') == 1
    final = 'unknown' if fail_transport else 'response_matched'
    assert [r['status'] for r in results].count(final) == 1
    assert sum(r['status'] in ('existing_unknown','existing_response_matched') for r in results) == 7
    assert all(r['execution_authorized'] is False for r in results)


def test_guard_release_failure_after_transport_does_not_record_or_retry():
    rig = Rig(); send = rig.edit_reply_markup
    def send_then_break_exit(**kwargs):
        result = send(**kwargs); rig.error = 'unlock'; return result
    rig.edit_reply_markup = send_then_break_exit
    assert rig.run()['status'] == 'unknown'
    assert rig.events.count('transport') == 1 and 'record' not in rig.events
