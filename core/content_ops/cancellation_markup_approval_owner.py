"""Local-only exact operator-decision registration, default OFF.

decision_authenticator is mandatory and REQUEST-SCOPED. It must authenticate
the current human, verify that this human explicitly confirmed the exact
immutable action/plan, and lock the original decision against revocation using
the supplied cursor. Reading an arbitrary decision by ID is insufficient.
It returns MarkupExecutionApproval only for that authenticated decision; the
DTO, a caller boolean or an agent's assessment is not proof of consent.

No real authenticator, HTTP/webhook route, credential, grant or transport is
provided. This is only append_cancellation_markup@1 authority, never content
approval, double-fact-check attestation or permission for public publication.
"""
from dataclasses import astuple

from core.content_ops.cancellation_markup import CancellationMarkupError
from core.content_ops.cancellation_markup_owner import _inputs, _load_target
from core.content_ops.cancellation_control_receipt_owner import (
    _COLUMNS as _EVIDENCE_COLUMNS, _clock, _same_row,
)
from core.content_ops.cancellation_markup_authority import (
    OriginalControlEvidence, MarkupExecutionApproval, ExactCancellationMarkupAuthority,
)
from core.content_ops.prompt_receipt import canonical_uuid


_COLUMNS = '''approval_id::text,attempt_id::text,card_id::text,actor_id::text,
    human_binding,plan_seal,original_receipt_sha256,action,approved_at,expires_at,active'''


def _require(ok):
    if not ok:
        raise CancellationMarkupError('markup_approval_registration_unknown')


class PostgresMarkupApprovalOwner:
    """Insert once, exact reuse only. Never reactivate or retry unknown commits.

    The connection factory must supply a fresh non-autocommit owner transaction.
    No role switching or privileged fallback exists. The subsequent courier
    independently rechecks the committed approval; this method sends nothing.
    """
    def __init__(self, connection_factory, *, decision_authenticator=None):
        self._factory = connection_factory
        self._authenticate = decision_authenticator

    def register(self, *, enabled=False, approval_id=None, **identity):
        if enabled is not True:
            return None
        try:
            _inputs(**identity)
            _require(canonical_uuid(approval_id) and callable(self._authenticate))
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
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and type(row[0]) is dict
                             and row[0].get('id') == plan.card_id and row[0].get('active') is True)
                    card = row[0]
                    _load_target(cursor, plan, bindings, identity['signer'])
                    before = _clock(cursor, plan, bindings)
                    cursor.execute(f'''select {_EVIDENCE_COLUMNS}
                        from private.content_ops_button_control_evidence
                        where card_id=%s::uuid for share''', (plan.card_id,))
                    raw_evidence = cursor.fetchone()
                    _require(raw_evidence is not None)
                    evidence = OriginalControlEvidence(*raw_evidence)
                    # Request/session authentication and explicit human decision
                    # must be supplied by a trusted adapter, never invented here.
                    decision = self._authenticate(cursor=cursor, approval_id=approval_id)
                    _require(type(decision) is MarkupExecutionApproval and decision.approval_id == approval_id)
                    after = _clock(cursor, plan, bindings)
                    _require(after >= before)
                    verifier = ExactCancellationMarkupAuthority(
                        lambda **_: (evidence, decision), enabled=True)
                    _require(verifier(cursor=cursor, locked_card=card, now=after, **identity) is True)
                    cursor.execute(f'''select {_COLUMNS}
                        from private.content_ops_button_markup_approvals
                        where card_id=%s::uuid or approval_id=%s::uuid or attempt_id=%s::uuid
                        for update''', (plan.card_id, approval_id, identity['attempt_id']))
                    existing = cursor.fetchone()
                    reused = existing is not None
                    if reused:
                        # Includes active: revoked evidence can never be reused
                        # as active, overwritten or granted a longer lifetime.
                        _same_row(existing, decision)
                    else:
                        # Do not grant new authority to an already consumed or
                        # unknown attempt. The shared card lock serializes this
                        # check with reserve and other participating registrars.
                        cursor.execute('''select id::text from private.content_ops_button_markup_attempts
                            where card_id=%s::uuid or id=%s::uuid for share''',
                            (plan.card_id, identity['attempt_id']))
                        _require(cursor.fetchone() is None)
                        cursor.execute(f'''insert into private.content_ops_button_markup_approvals
                            (approval_id,attempt_id,card_id,actor_id,human_binding,plan_seal,
                             original_receipt_sha256,action,approved_at,expires_at,active)
                            values(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s)
                            returning {_COLUMNS}''', astuple(decision))
                        _same_row(cursor.fetchone(), decision)
                    # Expiry during lock/insert/readback causes rollback, not a
                    # successful receipt for an already-expired registration.
                    final = _clock(cursor, plan, bindings)
                    _require(final >= after)
            return {'status': 'markup_approval_recorded', 'reused': reused,
                    'execution_authorized': False}
        except Exception:
            raise CancellationMarkupError('markup_approval_registration_unknown') from None
