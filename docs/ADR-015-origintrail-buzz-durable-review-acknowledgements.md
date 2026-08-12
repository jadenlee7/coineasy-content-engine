# ADR-015: Durable OriginTrail Buzz review acknowledgements

**Status:** Proposed — local implementation only; no Production activation
**Date:** 2026-08-11

## Context

ADR-013 records a human review decision before sending a visible Buzz reply.
That ordering avoids displaying success for a decision that did not persist,
but a process crash after the database commit can permanently lose the reply.
Blindly retrying is not safe: Buzz desktop v0.5.4 does not accept a caller
idempotency key, fixed event timestamp, or precomputed event ID.

## Decision

Store a separate acknowledgement receipt in the same transaction as a fresh
review decision. The receipt is private, FORCE-RLS, and has one row per
`(workspace_id, job_id)`. It stores the fixed service reply and its SHA-256;
the reviewer's reason is never copied into the service reply.

The worker uses this state machine:

```text
pending -> claimed -> attempt_started -> delivered
              |               |
              +-> pending      +-> delivery_unknown
              +-> failed
```

Only failures before `attempt_started` may return to `pending`. A fresh,
durable attempt marker with `authorized_once=true` is the sole authority for
one `buzz messages send --reply-to` call. A timeout, malformed response, or
lost completion response after that marker becomes `delivery_unknown` and is
never automatically resent.

Every run reconciles expired leases before claiming work. An expired
pre-attempt claim becomes pending (or failed after the bounded attempt limit),
while an expired provider attempt becomes `delivery_unknown`. A read-only
thread reconciliation may promote an unknown receipt to delivered only when
it finds exactly one kind-9 event from the configured service key with the
exact fixed content, channel tag, direct reply tag, and bounded timestamp.
Zero or multiple matches remain unknown and authorize no write.

The message request fingerprint binds the acknowledgement template version,
pinned Buzz CLI release, relay origin, channel, service pubkey, reviewer
decision event, and message SHA-256. The worker sends the stored outbox message
rather than regenerating it after a deploy.

## Safety gates

The existing `BUZZ_REVIEW_ACK_ENABLED` remains staging-only. A second literal
gate, `BUZZ_REVIEW_DURABLE_ACK_ENABLED`, must match it. Netlify independently
requires `BUZZ_REVIEW_ACK_OUTBOX_ENABLED=true` before it accepts the atomic
record-with-ack or acknowledgement lifecycle actions. All three default to
false. A normal decision-only record remains the rollback path.

The scoped review database role receives EXECUTE only on the existing review
RPCs and the bounded acknowledgement lifecycle RPCs. It receives no table
privileges and no Batch, OpenAI, approval, publication, deployment, or general
Buzz delivery capability. The Railway scanner receives no Supabase or
publication credential.

Automatic publication and regeneration remain OFF. An acknowledgement proves
only that the service recorded the review command; it cannot approve content,
create a publication, spend model budget, or mutate a Batch result.

## Rollout

1. Merge and deploy with all acknowledgement gates false.
2. Apply the additive migrations and verify FORCE-RLS, exact RPC grants, and
   zero table privileges on a disposable Preview branch.
3. In isolated staging, enable the Netlify outbox and both Railway gates for
   one prebuilt result. Verify one decision, one receipt, one relay attempt,
   one delivered acknowledgement, and zero publications/provider calls.
4. Inject failures before and after the attempt marker. Confirm that only
   pre-attempt failures retry and that post-attempt uncertainty never emits a
   second Buzz reply.
5. Disable all gates and delete the disposable Preview branch.

Production activation requires a separate approval bound to the exact release,
environment, channel, service identity, cutoff, time window, and a durable
maximum of one provider create. This ADR does not authorize that activation.

## Residual limitation

Without caller-controlled event identity, the relay write itself cannot be
exactly-once. This design instead guarantees at most one automated provider
call and uses read-only evidence to resolve a successful-but-unrecorded call.
An unresolved `delivery_unknown` requires operator review and must never be
requeued.
