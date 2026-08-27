# ADR-023: Producer-owned Telegram v2 golden fixture and offline shadow

**Status:** Accepted and implemented for local contract validation only. The
producer contract was reviewed and merged in PR #261 at merge commit
`0ffce811d2cad55bc7083d20c055801687927657`; this consumer change remains a
separate review. No live reader, Redis access, Telegram access, ACK, durable
receipt, deployment, or publication is authorized.

**Date:** 2026-08-27

## Context

ADR-022 defines the strict six-object Telegram v2 eligibility reader. Its
initial tests built snapshots inside the consumer repository. Those tests are
useful for isolated mutations, but an independently constructed consumer
snapshot cannot prove compatibility with the producer's current projection,
source validation, event, and promotion contracts.

A normal consumer test must also remain hermetic. Requiring a sibling
`coineasydaily` checkout would make CI depend on an ambient working tree and
could silently test a different producer revision. At the initial decision
point, both repositories' Telegram v2 contract files were untracked. The
producer files have since been reviewed and merged without changing their
fixture, manifest, generator, or source-contract bytes.

## Decision

`coineasydaily` owns the golden fixture bytes. Its offline generator calls the
actual v2 projection builder, source validator, event builder, and promotion
builder with deterministic synthetic inputs. It emits canonical newline JSON,
a manifest that binds every fixture byte hash and source-contract file hash,
and no live-data or delivery receipt claim.

The consumer vendors the producer's manifest and fixture files byte for byte
under a versioned test-only path. A consumer `LOCK.json` records:

- the upstream repository and fixture path;
- producer PR base `6f4a137b889a8d159a64d97924bb0ffef784aae9`;
- reviewed producer merge commit
  `0ffce811d2cad55bc7083d20c055801687927657`;
- `contract_source_state=merged_reviewed`;
- the raw byte length and SHA-256 of the upstream manifest and each imported
  fixture;
- the source-contract paths and hashes copied from the producer manifest;
- the local-only, no-live-I/O authority boundary.

Normal consumer tests use only these vendored bytes and the lock. They do not
import the producer package or inspect a sibling checkout. A future sync job
may compare an immutable reviewed producer revision, but it is separate from
the hermetic consumer suite.

### Golden scenarios

The producer owns three positive scenarios:

1. one transport update and one emitted projection;
2. 100 transport updates and 100 emitted projections, selecting ordinal 99;
3. 100 transport updates with 34 emitted, 33 tombstoned, and 33 not
   applicable outcomes, selecting emitted ordinal 99.

The fixtures bind the full ordered source and promotion manifests. The
consumer separately derives mutation cases for byte drift, six-object
tampering, cross-fixture splicing, ordered-member reorder or truncation,
v1/v2 confusion, and privacy canaries. Tampered bytes are never checked in as
additional golden fixtures.

Zero emitted projections are not a stream-row fixture. They remain
`unobserved` until the producer defines a separately ordered source-terminal
record and receipt.

### Local shadow modes

The offline shadow CLI retains two explicit input classes:

- an asserted naked snapshot, which can exercise the reader but has no
  producer-fixture provenance;
- a vendored fixture plus lock, which verifies the fixture, producer manifest,
  and vendor byte bindings before extracting the reader snapshot.

Both modes produce only a deterministic Korean triage projection and a
prepared, unpersisted receipt. For vendored fixtures, a verified lock proves
that the local bytes match the hashes recorded at reviewed producer merge
commit `0ffce811d2cad55bc7083d20c055801687927657`, so
`producer_fixture_provenance_verified=true`. This is commit provenance only.
The fixture's internal `atomic_snapshot=true` is a synthetic reader-contract
assertion; `live_atomic_redis_snapshot_observed` remains false.

The producer `source_commit_ref=commit:<64 hex>` is a content identity inside
the intake contract. It is not a Git commit SHA and must not be presented as
one.

## Alternatives considered

### Construct the same fixture independently in both repositories

Rejected. Two generators can agree with themselves while drifting from each
other, which defeats a cross-repository contract test.

### Import the producer from a sibling checkout in normal CI

Rejected. It makes results depend on ambient filesystem state and does not pin
the reviewed producer bytes.

### Treat fixture validation as live-source evidence

Rejected. Synthetic canonical bytes prove parser and contract compatibility,
not Redis atomicity, currentness, source ACK, production wiring, or delivery.

## Consequences

- Producer changes that alter contract bytes must intentionally regenerate the
  fixtures and manifest.
- Consumer updates must intentionally vendor the exact new bytes and update
  the lock in the same review.
- Raw-byte drift fails before semantic projection, while semantic mutations
  still fail in the strict reader.
- The consumer suite remains offline and deterministic.
- The fixture and lock add review overhead, but make producer ownership and
  source state explicit.

## Production blockers and next gate

This decision does not resolve ADR-022's live adapter, atomic Redis read,
consumer-group, pending recovery, ACK, durable receipt, ACL, retention,
deployment, or service-readback blockers. The producer contract review is
complete; the consumer contract and lock must still pass their separate PR
review before any later production proposal.

This consumer review authorizes no deployment, production configuration,
Redis call, Telegram call, source ACK, provider call, database write, send, or
publication.
