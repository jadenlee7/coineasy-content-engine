"""Unmounted, default-OFF single-attempt coordinator; no concrete transport.

Trusted dependencies, not user-supplied callbacks:
* guard.hold(**identity) serializes participating keyboard writers and card
  revocation. Ledger transactions MUST run outside this context: reserve first,
  then reacquire the guard and revalidate, send, release, and record evidence.
* lease.validate(**identity, now=aware_datetime) must freshly authenticate the
  operator, active card, original control-message receipt and exact sealed plan.
* transport.edit_reply_markup(body=...) must use that authenticated dedicated
  relay, one HTTP attempt, no redirects/retries/fallback, and return raw bytes.

The DB guard is separate and unmounted. A fake guard/transport proves control
flow only, not delivery or authorization. Out-of-band Telegram edits remain
outside database serialization.
"""
from datetime import datetime

from core.content_ops.cancellation_markup import (
    cancellation_markup_request, validate_cancellation_markup_response,
)
from core.content_ops.cancellation_markup_owner import _inputs, _result


def _time(clock):
    now = clock()
    if type(now) is not datetime or now.utcoffset() is None:
        raise ValueError('cancellation_markup_clock_invalid')
    return now


def run_cancellation_markup_once(*, enabled=False, plan=None, attempt_id=None,
        actor_id=None, human_id=None, bindings=None, signer=None,
        ledger=None, guard=None, transport=None, clock=None):
    """Never retries. Any failure after reserve begins is conservatively unknown.

    `response_matched` means the injected ledger confirmed persistence of a
    matched response, NOT independent Telegram readback or public approval.
    Default-OFF short circuits even malformed inputs/dependencies.
    """
    if enabled is not True:
        return None
    status = 'blocked'
    try:
        _inputs(plan, attempt_id, actor_id, human_id, bindings, signer)
        identity = dict(plan=plan, attempt_id=attempt_id, actor_id=actor_id,
                        human_id=human_id, bindings=bindings, signer=signer)
        # Invalid/missing dependencies must not consume a durable attempt.
        if not all(callable(fn) for fn in (
                ledger.reserve, ledger.record_response, guard.hold,
                transport.edit_reply_markup, clock)):
            raise ValueError('cancellation_markup_dependency_invalid')
        with guard.hold(**identity) as lease:
            before = _time(clock)
            cancellation_markup_request(enabled=True, plan=plan, bindings=bindings,
                                        now=int(before.timestamp()))
            if lease.validate(**identity, now=before) is not True:
                raise ValueError('cancellation_markup_guard_refused')
        status = 'unknown'  # Includes lost reservation commit acknowledgement.
        reserved = _result((ledger.reserve(enabled=True, **identity),),
                           plan, attempt_id, True)
        if reserved['new_attempt'] is False:
            status = 'existing_' + reserved['status']
        else:
            # Changes during the unlocked reservation gap must be refused here.
            with guard.hold(**identity) as lease:
                checked = _time(clock)
                if checked < before or lease.validate(**identity, now=checked) is not True:
                    raise ValueError('cancellation_markup_guard_refused')
                sent_at = _time(clock)
                if sent_at < checked:
                    raise ValueError('cancellation_markup_clock_invalid')
                request = cancellation_markup_request(enabled=True, plan=plan,
                    bindings=bindings, now=int(sent_at.timestamp()))
                http_status, raw_response = transport.edit_reply_markup(body=request['body'])
                observed = _time(clock)
                if observed < sent_at:
                    raise ValueError('cancellation_markup_clock_invalid')
                # Do not trust an injected ledger to validate a mismatched body.
                validate_cancellation_markup_response(enabled=True, plan=plan,
                    bindings=bindings, http_status=http_status,
                    raw_response=raw_response, observed_at=int(observed.timestamp()))
            # Historical evidence is valid after revocation; never reactivate.
            recorded = ledger.record_response(enabled=True, **identity,
                http_status=http_status, raw_response=raw_response, observed_at=observed)
            _result((recorded,), plan, attempt_id, False)
            status = 'response_matched'
    except Exception:
        # No raw request, destination, callback, provider body or exception leaks.
        if status.startswith('existing_') or status == 'response_matched':
            status = 'unknown'
    return {'status': status, 'execution_authorized': False}
