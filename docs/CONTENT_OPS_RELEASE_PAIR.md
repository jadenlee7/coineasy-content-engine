# Independent engine and review-bot releases

Status: local proposal and implementation; no production provisioning or grant.
Date: 2026-10-12.

## Decision

The engine's courier, Telegram worker and Typefully draft worker use one engine
Git SHA **E**. The separate review-bot repository builds an image at Git SHA
**B**. Requiring E = B is not a valid cross-repository deployment contract.
`release_sha` continues to mean E; `callback_release_sha` means B.

The compatibility identity **P** is SHA-256 of these exact ASCII bytes, including
the final newline (both SHAs must be lowercase 40-character hex):

```text
content-ops-release-pair@1
engine=<E>
callback=<B>
```

A digest is not authorization or proof of deployment. A separately authorized
operator must install the immutable E/B/P registry entry and current
workspace/client selection after verifying both actual builds. Runtime roles
receive no pair provisioning authority in this proposal. Neither source code
nor Telegram inputs choose an authorized pair.

Generate the compatibility manifest only after both independent Git commits
exist. The bot vendors the committed engine closure E; the engine must not
embed B back into its own source, creating a circular commit dependency.

## Enforcement

- Final snapshots and their callback HMACs bind E, B and P, along with exact
  content/version/banner/source, checked reviewer, routes and card identities.
- The engine courier checks E. Before any message part is admitted, the DB
  checks the registered current pair. Deliveries and decisions retain E/B/P.
- The bot independently checks B against its immutable image stamp and runtime
  Git metadata, E against the verified vendored package, and P against the
  canonical role-labelled digest. It cannot substitute the engine pin for B.
- The bounded read-only snapshot reader filters by the configured E/B/P and
  scoped current DB selection. The atomic decision repeats mutable checks
  under locks. A DB-maintained selection timestamp blocks cards created before
  the latest selection, including P1 -> P2 -> P1 rollback. Callers cannot backdate
  it; switching takes the same scoped row lock held through queue admission.
  Terminal readback reconciles original receipts without reopening decisions
  or provider attempts.
- Queue-owner RPC argument 10 remains E, with B/P appended in arguments 13/14.
  Telegram job metadata, handoffs and Typefully approval proofs remain pinned
  to E. Only callback attestation uses B.
- A distinct synthetic E/B pair is a positive case; swapped roles, malformed
  or altered digests, mismatched builds and inactive pairs fail closed.

## Compatibility and release boundary

These are staged proposal schemas, not automatic production migrations. Old
single-SHA RPC signatures and card projections are not silently upgraded or
accepted. The exact schema and restricted-role ACL manifest must be reviewed
before any production apply. The new registry is deliberately empty and has
no runtime grants or registered employee/client pair.

Disposable schema load order: apply `content_ops_release_pair_registry.sql`
after publication routes and before final-card delivery, decision and queue
proposals. The CI load list and socket-only local verification script use this
order; neither applies anything to production.

The callback owner, keys and DB identity remain separate from private review,
the courier and public channel workers. There is still only one bot poller.
Typefully remains unscheduled private draft only. Unknown sends/commits retain
their original reconciliation identity and must never be retried automatically.

Local startup/validate-only can prove configuration and zero-I/O behavior. It
explicitly cannot prove DB registration, hosted ACLs, actual deployment, staff
delivery or public publication. No change here enables a feature or scheduler.

## Local verification, 2026-10-12

- Relevant Engine Python regression run: 2,588 passed (before removing seven
  duplicated imported test cases). Pair-specific and snapshot tests were
  rerun after the final selection-generation assertion; no deadline was relaxed.
- Relevant JavaScript review/release/Typefully deployment contracts: 46 passed.
- Fresh socket-only PostgreSQL 16: 62 unchanged historical migrations plus
  staged proposals loaded successfully; registry/ACL, final-card, decision,
  handoff and 23 queue scenarios passed, including signup-free four-client
  approval and rollback cases.
- Two real local sessions: one decision/approval/publication/publish job/slot,
  two channel handoffs; the concurrent pair-selection update waited on the
  admission SHARE lock. Exact terminal replay after a switch retained original
  identities and created no additional work.
- Disposable server stopped and socket removed. Provider/production calls: 0.
- Independent read-only code review found no release-role or stale-card issue.
  Diff whitespace and CI YAML checks passed.

The snapshot query's pair joins and data reconstruction have unit coverage;
an actual persisted-card reader-to-callback roundtrip was not performed in this
increment. PostgreSQL 17 hosted schema/ACL compatibility, built images, hosted
CI, real routing permissions and a private operational canary remain unverified.
