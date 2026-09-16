"""Synthetic authenticated-decision seam; no actual operator session or send."""
from dataclasses import astuple, replace
from datetime import datetime, timezone, timedelta

import pytest

from core.content_ops.cancellation_markup import CancellationMarkupError
from core.content_ops.cancellation_markup_approval_owner import PostgresMarkupApprovalOwner
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_cancellation_control_receipt_owner import ReceiptConnection
from test_content_ops_review_cancellation import records, NOW, uid


class ApprovalConnection(ReceiptConnection):
    def __init__(self, reused=False, commit_error=False):
        super().__init__(commit_error=commit_error)
        _, card, evidence, approval = fixture()
        now = datetime.fromtimestamp(NOW+.1, timezone.utc)
        self.rows = [('5000', '10000'), (card,), records(), (now,), astuple(evidence),
                     (now,), astuple(approval) if reused else None]
        if not reused:
            self.rows += [None, astuple(approval)]
        self.rows.append((now,))


def run(conn, decision=None, **changes):
    identity, _, _, approved = fixture()
    identity.update(changes)
    calls = []
    def authenticate(**kwargs):
        assert conn.exits == []
        calls.append(kwargs)
        return approved if decision is None else decision
    result = PostgresMarkupApprovalOwner(lambda: conn, decision_authenticator=authenticate).register(
        enabled=True, approval_id=approved.approval_id, **identity)
    return result, calls


@pytest.mark.parametrize('enabled', [False, None, 1, 'true'])
def test_off_has_zero_io(enabled):
    def fail(**kwargs): raise AssertionError('no I/O')
    assert PostgresMarkupApprovalOwner(fail, decision_authenticator=fail).register(enabled=enabled) is None


@pytest.mark.parametrize('approval_id', [None, 'invalid', '00000000-0000-0000-0000-000000000000'])
def test_bad_id_and_missing_authenticator_never_connect(approval_id):
    calls = []
    with pytest.raises(CancellationMarkupError, match='^markup_approval_registration_unknown$'):
        PostgresMarkupApprovalOwner(lambda: calls.append(1), decision_authenticator=lambda **_: True).register(
            enabled=True, approval_id=approval_id, **fixture()[0])
    assert calls == []
    with pytest.raises(CancellationMarkupError):
        PostgresMarkupApprovalOwner(lambda: calls.append(1)).register(
            enabled=True, approval_id=fixture()[3].approval_id, **fixture()[0])
    assert calls == []


@pytest.mark.parametrize('reused', [False, True])
def test_exact_registration_is_committed_and_never_sends(reused):
    conn = ApprovalConnection(reused)
    result, calls = run(conn)
    assert result == dict(status='markup_approval_recorded', reused=reused, execution_authorized=False)
    assert conn.exits == [None] and len(conn.calls) == (8 if reused else 10)
    assert len(calls) == 1 and set(calls[0]) == {'cursor', 'approval_id'}
    writes = [(s, p) for s, p in conn.calls if s.strip().startswith('insert')]
    assert len(writes) == (0 if reused else 1)
    if writes:
        assert writes[0][1] == astuple(fixture()[3])
        assert 'insert into private.content_ops_button_markup_approvals' in writes[0][0]
    assert all(not sql.strip().startswith(('update', 'delete')) for sql, _ in conn.calls)
    assert fixture()[3].approval_id not in str(result)


@pytest.mark.parametrize('field, value', [
    ('approval_id', uid(86)), ('card_id', uid(86)), ('attempt_id', uid(86)),
    ('actor_id', uid(86)), ('human_binding', '0'*64), ('plan_seal', '0'*64),
    ('original_receipt_sha256', '0'*64), ('action', 'publish'), ('action', 'approve'),
    ('active', False), ('active', 1),
    ('approved_at', datetime.fromtimestamp(NOW+1, timezone.utc)),
    ('approved_at', datetime.fromtimestamp(NOW-1, timezone.utc)),
    ('approved_at', datetime.fromtimestamp(NOW)),
    ('expires_at', datetime.fromtimestamp(NOW+299, timezone.utc)),
    ('expires_at', datetime.fromtimestamp(NOW+301, timezone.utc)),
])
def test_different_human_action_plan_or_window_refused(field, value):
    conn = ApprovalConnection()
    with pytest.raises(CancellationMarkupError, match='^markup_approval_registration_unknown$'):
        run(conn, replace(fixture()[3], **{field: value}))
    assert conn.exits[0] is not None and not any(s.strip().startswith('insert') for s, _ in conn.calls)


@pytest.mark.parametrize('decision', [True, False, {}, 'approved', object()])
def test_caller_boolean_or_approval_text_is_not_consent(decision):
    conn = ApprovalConnection()
    with pytest.raises(CancellationMarkupError): run(conn, decision)
    assert len(conn.calls) == 5


@pytest.mark.parametrize('column, value', [(0, uid(86)), (1, '0'*64), (2, '0'*64),
    (3, True), (5, 99), (9, '0'*64), (11, '{"inline_keyboard":[]}')])
def test_changed_original_receipt_cannot_authorize(column, value):
    conn = ApprovalConnection(); row = list(conn.rows[4]); row[column] = value; conn.rows[4] = tuple(row)
    with pytest.raises(CancellationMarkupError): run(conn)
    assert not any(s.strip().startswith('insert') for s, _ in conn.calls)


def test_missing_evidence_never_calls_authenticator():
    conn = ApprovalConnection(); conn.rows[4] = None; calls = []
    with pytest.raises(CancellationMarkupError):
        PostgresMarkupApprovalOwner(lambda: conn, decision_authenticator=lambda **_: calls.append(1)).register(
            enabled=True, approval_id=fixture()[3].approval_id, **fixture()[0])
    assert calls == [] and len(conn.calls) == 5


@pytest.mark.parametrize('reused', [False, True])
@pytest.mark.parametrize('column, value', [(0, uid(87)), (1, uid(87)), (4, '0'*64), (10, False), (10, 1)])
def test_revoked_conflicting_or_bad_readback_never_overwrites(reused, column, value):
    conn = ApprovalConnection(reused); index = 6 if reused else 8
    row = list(conn.rows[index]); row[column] = value; conn.rows[index] = tuple(row)
    with pytest.raises(CancellationMarkupError): run(conn)
    assert conn.exits[0] is not None
    if reused:
        assert not any(s.strip().startswith('insert') for s, _ in conn.calls)


def test_existing_consumed_attempt_cannot_get_new_approval():
    conn = ApprovalConnection(); conn.rows[7] = (uid(90),)
    with pytest.raises(CancellationMarkupError): run(conn)
    assert len(conn.calls) == 8 and conn.exits[0] is not None


@pytest.mark.parametrize('index', [3, 5, 9])
@pytest.mark.parametrize('value', [datetime.fromtimestamp(NOW+300, timezone.utc),
    datetime.fromtimestamp(NOW-1, timezone.utc), datetime.fromtimestamp(NOW), None])
def test_expiry_including_after_insert_rolls_back(index, value):
    conn = ApprovalConnection(); conn.rows[index] = (value,)
    with pytest.raises(CancellationMarkupError): run(conn)
    assert conn.exits[0] is not None


def test_clock_regression_after_write_refused():
    conn = ApprovalConnection(); conn.rows[-1] = (conn.rows[5][0]-timedelta(microseconds=1),)
    with pytest.raises(CancellationMarkupError): run(conn)
    assert conn.exits[0] is not None


def test_autocommit_inactive_parent_and_auth_errors_are_bounded():
    conn = ApprovalConnection(); conn.autocommit = True
    with pytest.raises(CancellationMarkupError): run(conn)
    assert conn.calls == []
    conn = ApprovalConnection(); conn.rows[1][0]['active'] = False
    with pytest.raises(CancellationMarkupError): run(conn)
    assert len(conn.calls) == 2
    conn = ApprovalConnection(); conn.rows[2][1]['bindings']['parent_binding'] = '0'*64
    with pytest.raises(CancellationMarkupError): run(conn)
    assert len(conn.calls) == 3
    conn = ApprovalConnection(); calls = []
    def failed(**kwargs): calls.append(1); raise ValueError('private session secret')
    with pytest.raises(CancellationMarkupError, match='^markup_approval_registration_unknown$'):
        PostgresMarkupApprovalOwner(lambda: conn, decision_authenticator=failed).register(
            enabled=True, approval_id=fixture()[3].approval_id, **fixture()[0])
    assert calls == [1] and len(conn.calls) == 5 and conn.exits[0] is not None


def test_unknown_commit_does_not_repeat_registration():
    conn = ApprovalConnection(commit_error=True); calls = []
    def factory(): calls.append(1); return conn
    with pytest.raises(CancellationMarkupError, match='^markup_approval_registration_unknown$'):
        PostgresMarkupApprovalOwner(factory, decision_authenticator=lambda **_: fixture()[3]).register(
            enabled=True, approval_id=fixture()[3].approval_id, **fixture()[0])
    assert calls == [1] and conn.exits == [None]
