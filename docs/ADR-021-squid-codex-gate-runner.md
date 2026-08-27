# ADR-021: Squid Codex Gate Runner v1

**Status:** Proposed for local and CI verification only
**Date:** 2026-08-27
**Deciders:** CoinEasy representative, content, community, security, and
engineering leads

## Context

The Squid Harmony Preview has already proven a tenant-scoped five-stage ledger,
fixed specialists, revocable connector attestations, durable QA denials, and
64-way database convergence. That proof deliberately used deterministic,
synthetic stage artifacts. The `codex` value on the QA receipt is an actor
label; it is not proof that a Codex reviewer performed semantic or factual
review.

Moving directly from that rehearsal to a live model worker would leave a more
important boundary unproven. The current stage operation key includes the
result hash, so it cannot prevent two model calls from being made for the same
logical input. A timeout after a provider call could therefore create duplicate
cost, divergent outputs, or an unsafe automatic retry.

CoinEasy also chose a Codex-first operating model with fixed specialist roles.
This is not dynamic model assignment and it is not an all-powerful
super-agent. Each worker must have one role, one capability, and no authority
to approve or publish.

## Decision

Add an offline, no-I/O `Squid Codex Gate Runner v1` state machine before any
live Codex connector or database migration.

The first lane is the independently scoped Squid QA specialist. A stable
`work_key` binds the exact plan and private-content stage receipt chain plus
the official content version, source item, source binding, and content
snapshot. Reviewer identity, release, configuration, and registration expiry
are held separately in an `assignment_key`. A changed assignment therefore
cannot create a second logical QA job. Both keys are derived before execution
and never from a provider result.

The DB-projected source-lineage contract also binds the canonical four-signal
manifest SHA, its input-set SHA, and the exact sorted producer-principal set.
The request producer set must exactly equal that projection, and the plan input
must equal the projected input set. This prevents a request author from
omitting an upstream producer to bypass reviewer independence.

The allowed progression is:

```text
pending
  -> claimed
  -> attempt_started
  -> result_submitted
  -> verified
  -> operator_review_pending
```

`needs_changes`, `blocked`, and `outcome_unknown` are terminal. A lease may be
reclaimed at most three times only before `attempt_started`. Once an attempt has
started, an absent or ambiguous result receipt becomes `outcome_unknown` and is
never retried automatically.

Every clock-bearing transition is monotonic:

```text
branch fence <= plan receipt <= private receipt <= source observation
branch fence <= private binding creation <= private receipt < binding expiry
source observation <= request submission <= claim <= attempt start
                   <= result receipt <= result submission
```

The run stores `submitted_at`, `claimed_at`, `result_submitted_at`, and the
latest transition time. Claim fences include the request key and claim time.
Result submission requires a trusted clock input strictly before both lease
and request expiry and at or after the receipt time. The Preview implementation
must replace that input with the database server clock; client time is never
authoritative.

A verified receipt and its typed `squid-codex-semantic-qa-evidence@1` evidence
must bind:

- workspace, client, round, plan, exact plan/private receipt digests, and their
  previous-receipt/input-output chain;
- reviewed private-content output SHA-256 and the expiring DB-projected source
  lineage receipt;
- canonical signal manifest/input-set SHA-256 values and the exact four
  producer principals, with plan-input equality;
- current `needs_review` content version, official source item, official source
  binding, and content snapshot SHA-256;
- fixed private-content and reviewer specialist registrations, branch fence,
  principal separation from every upstream signal/stage producer, and expiry;
- exact release and configuration SHA;
- canonical typed evidence SHA-256, closed finding codes, criteria, and verdict;
- the exact claim fence and attempt fence that authorized execution;
- cost as either observed microusd or explicitly `unobserved`;
- observed cost no greater than the request's approved microusd cap;
- automatic publication, provider-side publication, external messaging, and
  publication calls all false.

The runner enforces one logical work row per the existing Preview uniqueness
scope `(workspace, client, plan, independent_qa)`. A different source
or output under the same stage scope conflicts rather than opening a second
execution. A reviewer rotation under the same work also conflicts in v1; a
future manual reassignment would require a separate operator override receipt.

Only a first, non-reused `start_attempt` transition carries
`execute_authorized=true`. Sixty-four concurrent starts converge to exactly
one such transition. Replays are explicitly non-authorizing. Claims are fenced
with request key, claim time, principal, attempt count, and lease expiry, and
are limited to a fifteen-minute lease, so a worker holding an expired fence
cannot start after a reclaim.

Only `pass` may produce the local `operator_review_pending` transition. This
decision does not implement a representative decision, Recap delivery,
provider call, database write, message, deployment, or publication.

## Options Considered

### Option A: Call Codex directly from the existing stage RPC

| Dimension | Assessment |
|---|---|
| Implementation speed | High |
| Duplicate-call safety | Weak |
| Recovery clarity | Weak |
| Least privilege | Weak |

Rejected because a transaction cannot safely contain an external model call,
and commit-unknown recovery would be ambiguous.

### Option B: One orchestrator holding every specialist credential

| Dimension | Assessment |
|---|---|
| Operational simplicity | Medium |
| Blast radius | High |
| Role independence | Weak |
| Auditability | Medium |

Rejected because it recreates the super-agent design that Harmony was built to
avoid.

### Option C: Fixed-role, receipt-driven gate workers (chosen)

| Dimension | Assessment |
|---|---|
| Duplicate-call safety | Strong inside one runner; durable DB proof pending |
| Least privilege | Strong |
| Recovery clarity | Strong |
| Initial implementation cost | Medium |

This preserves fixed ownership while allowing the same Codex frontier to run
separate, non-self-approving contexts.

## Consequences

- The first implementation can be adversarially tested without credentials,
  cost, network access, or infrastructure.
- A future live worker must durably commit `attempt_started` before invoking
  Codex and reconcile the exact request key before any recovery action.
- Semantic QA output uses a typed additive evidence contract and does not reuse
  the existing `harmony-deterministic-qa@1` label.
- The local source-lineage receipt and its authoritative-manifest fields are a
  contract fixture, not proof of live DB currentness. A future Preview RPC must
  derive the manifest, input set, and producer principals from the current DB
  rows and preserve `database_currentness_required=true`.
- The in-memory lock proves one-process concurrency only. Restart-safe and
  multi-worker exactly-once remain NO-GO until append-only DB rows, uniqueness,
  and atomic claim/attempt fencing exist in a disposable Preview.
- The Preview RPC must derive manifest, producer, currentness, and minimum
  connector-trust expiry from database rows; define the canonical relation
  between manifest and input-set digests; preserve DB timestamp precision; and
  recompute every claim/attempt fence on readback.
- Content production, planning, and Recap can later adopt the same state
  machine, but each remains a distinct role and credential.
- Operator approval and automatic publication remain outside the runtime.

## Action Items

1. [x] Implement and adversarially test the offline Squid QA state machine.
2. [x] Independently review request identity, terminal states, replay handling,
       self-review rejection, and publication-off invariants.
3. [ ] Under a separate approval, add DB-derived source-lineage, server-time
       validation, and append-only request, claim, attempt, result,
       verification, and reconciliation receipts to a disposable Preview.
4. [ ] Under another separate approval, connect one QA-only Codex worker with
       no planning, content, operator, Recap, messaging, or publication token.
5. [ ] Require clean Preview rounds and measured operator value before adding
       the next specialist lane.
