# ADR-022: Strict Telegram v2 eligibility reader and intake receipt

**Status:** Accepted and implemented for local, provider-disconnected contract
validation only; no live owner reader, Redis credential, consumer group, ACK,
durable database, deployment, or publication

**Date:** 2026-08-27

## Context

The existing GTM source bundle deliberately accepts only
`coineasy-telegram-owner-projection@1`. The `coineasydaily` owner now has a
separate local v2 path whose question identity is bound to token lineage,
source epoch, and HMAC-key epoch. It promotes raw-free projections into a v2
stream only after checking an immutable intake marker and sanitized gate.

Accepting a bare v2 projection in the existing source bundle would discard the
promotion eligibility boundary. Accepting a v2 detail in the general saved
`GtmInboxPage` would create a second bypass because arbitrary saved JSON could
look like reader-derived evidence. A stream row alone is also insufficient:
Redis Lua failures can leave an orphan `XADD` row without the indexes and final
promotion marker that make it eligible.

## Decision

Keep every existing v1 source-bundle, saved-page, CLI, broker, fixture, and
projector contract unchanged. Add a separate v2 path with three stages:

```text
atomic six-object read snapshot
  -> EligibleTelegramV2Event
  -> EligibleTelegramV2TriageItem
  -> snapshot-revalidated prepared intake receipt
  -> default-disabled process-memory append + internal exact readback
```

`EligibleTelegramV2TriageItem` is intentionally not a `GtmOperatorItem` and is
not part of the saved-page discriminator. This preserves the v1 page boundary
and prevents a JSON file from claiming v2 provenance.

### Exact version pairing

The v2 reader accepts only this pair:

```text
schema_version = coineasy-telegram-owner-projection@2
digest_scheme  = hmac-sha256-v2
```

The other 22 projection fields retain the v1 privacy, FAQ, timestamp, safety,
and human-review semantics. A v1/v2 mixed pair, unknown version, extra field,
coerced boolean, non-canonical JSON, duplicate JSON key, or changed identity
fails closed.

### Six-object eligibility gate

One read snapshot must contain all six current objects:

1. the exact v2 stream row and canonical `event_json`;
2. the current event idempotency index;
3. the source-commit to promotion index;
4. the canonical immutable promotion marker;
5. the immutable intake commit marker;
6. the raw-free sanitized gate envelope.

The reader recomputes and cross-binds the event SHA, projection SHA, event
identity, source commit/stage/gate, ordinal, full ordered promotion manifest,
source index, event index, and exact source projections. Removing or changing
any one object revokes eligibility. The input attests that these values came
from one atomic read snapshot; an eventual live adapter must enforce that with
a read-only transactional snapshot or equivalent immutable-retention proof.
This repository creates no Redis client or credential.

The returned eligible object contains only sanitized projections and evidence
digests. It does not retain the intake marker or gate JSON and cannot expose
owner-private stage data.

### Intake receipt

`coineasy-telegram-v2-intake-delivery-receipt@1` binds:

- consumer namespace and reader policy;
- stream ID, event ref/idempotency/SHA, question ref, and projection SHA;
- source commit, stage, gate, ordinal, and batch ref;
- promotion ref/manifest, stream row, current event index, source index,
  intake marker, and sanitized gate digests;
- complete promotion outcome counts and ordered-members digest;
- exact eligible triage item ref and item SHA.

The append identity binds the full event delivery: consumer and reader policy,
stream and event identity/SHA, question and projection, source commit,
promotion manifest, and exact item SHA. The same exact delivery and bytes
replay the existing receipt. A different stream/event delivery of the same
question is a distinct receipt. Cross-event question deduplication is a
separate policy index and is not implemented here.

The reader and public receipt builder accept only an exact plain-dictionary
serialization of the six-object snapshot; Pydantic model instances, copies,
and construct-without-validation objects are not provenance. The builder
invokes the strict reader again and derives the triage item internally. It does
not accept a serialized eligible object or caller-supplied item as provenance.
A prepared receipt is still persistence-unobserved and cannot be appended
after ordinary JSON rehydration.

The repository is `enabled=False` by default, requires a literal boolean, and
accepts only the concrete process-local create-only store. That store uses a
lock around check-and-set so concurrent callers cannot overwrite. An enabled
local append is successful only after exact readback. A lost append response
is indeterminate; retrying the same built receipt converges without
overwriting. The prepared receipt explicitly records:

```text
source_acknowledged=false
public_delivery_observed=false
automatic_publication=false
approval_granted=false
provider/database/network/telegram_calls=false
production_wiring_observed=false
durability_scope=process_memory_only
provider_persistence_observed=false
```

It is an intake-validation receipt, not a Telegram or public delivery receipt.
No public or serializable result object claims exact readback. The repository
method returns only a created/reused boolean after its internal exact readback;
that boolean is operational flow control, not durable evidence. The receipt
therefore remains persistence-unobserved even after a process-memory append.

## Compatibility boundary

- `TelegramOwnerProjection` remains v1-only.
- `SquidTelegramSourceState` remains v1-only and capped at 16 records.
- `GtmInboxPage`, the Phase 0 CLI, fixtures, renderer, and broker remain
  v1-only and byte-stable.
- A bare v2 projection cannot call the v1 projector.
- Only a runtime reader grant may create the separate v2 triage item. The
  receipt builder independently revalidates the complete snapshot and derives
  that item itself.
- The v2 reader validates up to the producer's complete 100-event promotion
  manifest without forcing those events into the 50-item v1 page.

## Failure semantics

Missing, malformed, stale-index, orphan, or mismatched evidence is
`ineligible`. A future live read I/O or atomic-snapshot failure is
`indeterminate`. Neither state permits a receipt or ACK.

Receipt order for a future consumer is fixed:

```text
eligibility -> append receipt -> exact receipt readback -> source ACK
```

This phase implements only the first three local contract steps and has no ACK
method. An ineligible orphan must not be silently acknowledged.

## Known production blockers

1. No authenticated read-only adapter or atomic Redis snapshot exists here.
   The current input is an asserted snapshot and does not yet attest the exact
   hash field selectors or XRANGE-returned row ID used by an adapter.
2. No consumer-group discovery, lease, pending recovery, ACK, or quarantine
   policy is approved.
3. No durable private receipt table, insert-only role, or service readback
   receipt exists; process memory is not production durability.
4. A zero-projection promotion has a marker but no stream row. It requires a
   separate ordered source-control record and source-level terminal receipt;
   an empty stream must remain unobserved, never zero.
5. Cross-event question deduplication/current-state policy is intentionally
   separate from exact delivery idempotency and does not exist yet.
6. A stage-level completion receipt cannot be emitted until every ordered
   member receipt is proven, including the 100-event boundary.
7. Required CI, reviewed commit SHA, deployment SHA, ACL, retention, and live
   service readback are unobserved.

## Verification

The local suite covers valid v2 projection, bare-v2 bypass, six independent
binding failures, v1/v2 confusion, boolean coercion, duplicate/non-canonical
JSON, explicit snapshot-field presence, source ordinal/state type confusion,
deep-JSON recursion, exception-chain privacy, full 100-member validation,
privacy canaries, default-disabled zero calls, exact replay, full delivery
identity, forged-provenance rejection, concurrent append conflict,
append-response loss, and banned I/O or ACK imports.

ADR-023 adds a producer-owned, byte-locked synthetic fixture bundle and an
offline fixture-mode shadow CLI. That verifies cross-repository contract
compatibility without changing this ADR's asserted-snapshot, live-atomicity,
or production blockers.

This ADR authorizes no deploy, production configuration, Telegram send,
publication, provider call, database migration, consumer group, or source ACK.
