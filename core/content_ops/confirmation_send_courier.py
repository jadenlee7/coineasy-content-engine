"""Unmounted owner-side coordinator. No runtime wiring or credentials.

Runs in the trusted owner, not the restricted relay process. Transport must be
the exclusive bounded relay bridge and must capture/persist the original reply
before returning its minimal source-store ack. The opt-in dispatch client now
implements that bridge, but no runner installs it. The operator permission
registrar remains unmounted and this is not a second sender.
"""
from core.content_ops.cancellation_markup_owner import _inputs
from core.content_ops.cancellation_markup_courier import _time
from core.content_ops.confirmation_delivery_owner import confirmation_send_request
from core.content_ops.confirmation_response_gateway import _ack
from core.content_ops.prompt_receipt import canonical_uuid


def run_confirmation_send_once(*, enabled=False, target=None, delivery_id=None,
        guard=None, owner=None, dispatch_owner=None, transport=None, clock=None, **identity):
    if enabled is not True: return None
    status = 'blocked'
    try:
        def require(value):
            if not value: raise ValueError()
        _inputs(**identity)
        require(canonical_uuid(delivery_id) and guard.target==target and guard.delivery_id==delivery_id)
        require(all(callable(fn) for fn in (guard.hold,owner.reserve,owner.ingest,
            dispatch_owner.consume,transport.send_confirmation,clock)))
        request = confirmation_send_request(target,identity['plan'],identity['bindings'])
        with guard.hold(**identity) as lease:
            before = _time(clock)
            require(lease.validate(now=before,**identity) is True)
        status = 'unknown'  # Reservation commit may be uncertain: never retry.
        reserved = owner.reserve(enabled=True,target=target,delivery_id=delivery_id,**identity)
        require(type(reserved) is dict and set(reserved)=={'status','new_attempt','execution_authorized'}
            and reserved['status'] in ('unknown','response_matched')
            and type(reserved['new_attempt']) is bool and reserved['execution_authorized'] is False)
        if reserved['new_attempt'] is False:
            status = 'existing_' + reserved['status']
        else:
            require(reserved['status']=='unknown')
            consumed = dispatch_owner.consume(enabled=True,target=target,
                delivery_id=delivery_id,**identity)
            require(type(consumed) is dict and set(consumed)=={'status','new_dispatch','execution_authorized'}
                and consumed['status']=='confirmation_dispatch_consumed'
                and type(consumed['new_dispatch']) is bool and consumed['execution_authorized'] is False)
            if consumed['new_dispatch'] is False:
                return dict(status='existing_dispatch_consumed',execution_authorized=False)
            with guard.hold(**identity) as lease:
                now = _time(clock)
                require(now>=before and lease.validate(now=now,**identity) is True)
                # Held card/permission locks serialize participating revocation.
                # Store only takes delivery/source locks; importer runs outside.
                _ack(transport.send_confirmation(delivery_id=delivery_id,request=request,lease=lease))
            result = owner.ingest(enabled=True,target=target,delivery_id=delivery_id,**identity)
            require(type(result) is dict and set(result)=={'status','reused','execution_authorized'}
                and result['status']=='confirmation_recorded' and type(result['reused']) is bool
                and result['execution_authorized'] is False)
            status = 'confirmation_recorded'
    except Exception:
        if status.startswith('existing_'): status = 'unknown'
    return dict(status=status,execution_authorized=False)
