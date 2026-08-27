# ADR-021: Squid Codex Gate Runner v1

**Status:** Proposed; durable DB contract and disposable local PostgreSQL proof
are complete, while the clean exact-SHA disposable Supabase Preview proof
remains pending
**Date:** 2026-08-27
**Deciders:** CoinEasy representative, content, community, security, and
engineering leads

## Context

An earlier Squid Harmony Preview rehearsal proved a tenant-scoped five-stage ledger,
fixed specialists, revocable connector attestations, durable QA denials, and
64-way database convergence. That proof deliberately used deterministic,
synthetic stage artifacts. The `codex` value on the QA receipt is an actor
label; it is not proof that a Codex reviewer performed semantic or factual
review.

The new durable Codex gate described here has passed its disposable local
PostgreSQL migration, security, and 64-way convergence proof. That local proof
does not substitute for the separately approved clean exact-SHA Supabase
Preview proof, so this ADR still does not call the durable gate deployable or
Production-ready.

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

Use a Codex-first, fixed-specialist `Squid Codex Gate Runner v1`. The existing
offline, no-I/O state machine remains the executable reference model. The
Preview DB projection adds append-only source-lineage, request, transition,
claim, attempt, evidence, result, verification, reconciliation, and stage-link
receipts plus one mutable run projection. This is fixed role ownership, not
runtime model assignment and not a super-agent.

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

The public durable path is deliberately narrow:

```text
prepare_preview_harmony_squid_codex_qa
  -> claim_preview_harmony_squid_codex_qa
  -> start_preview_harmony_squid_codex_qa_attempt
  -> submit_preview_harmony_squid_codex_qa_result
  -> verify_preview_harmony_squid_codex_qa_result
```

`coineasy_harmony_qa` alone may execute those five functions and the separate
`reconcile_preview_harmony_squid_codex_qa_lease` function. It retains the
negative-only `record_preview_harmony_squid_qa_denial` RPC, but its execute
privilege on the legacy generic `append_preview_harmony_squid_stage` function
is revoked. `public`, `anon`, `authenticated`, `service_role`, and every other
Harmony role have no execute privilege on the six durable gate RPCs. A table
trigger additionally rejects any positive `independent_qa` insert that is not
backed by a verified durable result.

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
uses the database server's `clock_timestamp()` after acquiring its locks;
client time is never authoritative.

All six durable RPCs first validate the signed zero-authority QA scope without
reading tenant rows, then acquire one canonical workspace/client advisory
transaction lock before any run, request, or round lock. Frozen reviewer
assignment is revalidated after the rows are locked. This common ordering
prevents cross-RPC deadlock cycles and cross-workspace queue probing.

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

The approved 64-way proof contract is exact, not statistical:

- prepare: one new request/run and 63 reuses;
- claim: one claimed winner and 63 non-claiming responses;
- start: one new `execute_authorized=true` attempt and 63 reused,
  non-authorizing responses;
- submit: one result/evidence and 63 reuses;
- verify: one verification, one positive QA stage, one stage link, and 63
  reuses.

All 64 responses in a phase must converge on the same work/request/fence or
receipt identity. A timeout or commit-unknown is a failed proof, not permission
to repeat a provider call.

Only `pass` may produce the `independent_qa` stage. Verification appends the
verification receipt, inserts the positive QA stage, links it to the durable
gate, and advances the run to `operator_review_pending` in the same database
transaction. `needs_changes` and `blocked` are terminal verification outcomes
with no positive stage, inbox, or Recap. An expired pre-attempt claim may be
released within the three-claim cap; an expired post-attempt run without a
result receipt becomes terminal `outcome_unknown` and is never retried
automatically.

The migration and harness keep `automatic_publication`, `external_calls`,
`provider_calls`, and `publication_calls` false. The `execute_authorized=true`
start receipt authorizes at most one future worker invocation; the current
harness does not call Codex, Grok, Buzz, a provider, an approval system, Recap
delivery, or publication.

The durable contract is migration
`20260827220000_harmony_preview_codex_gate_durable.sql`, applied ninth after the
eight existing Harmony Preview migrations. It may be verified only from a
clean exact Git SHA on one separately approved non-persistent disposable
Preview child. Success and failure both require immediate child deletion;
the two-hour TTL is only a safety bound. Preview proof does not authorize a
Production migration, feature-flag activation, representative approval, or
publication.

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
| Duplicate-call safety | Local durable DB proof passed; Supabase Preview proof pending |
| Least privilege | Strong |
| Recovery clarity | Strong |
| Initial implementation cost | Medium |

This preserves fixed ownership while allowing the same Codex frontier to run
separate, non-self-approving contexts.

## Consequences

- The reference model can be adversarially tested without credentials, cost,
  network access, or infrastructure.
- A future live worker must durably commit `attempt_started` before invoking
  Codex and reconcile the exact request key before any recovery action.
- Semantic QA output uses a typed additive evidence contract and does not reuse
  the existing `harmony-deterministic-qa@1` label.
- The durable prepare RPC derives manifest, input set, producer principals,
  source currentness, and minimum trust expiry from locked database rows rather
  than accepting those values from caller JSON. The migration's existence is
  still not proof that those checks hold on a live Preview.
- Append-only DB receipts, uniqueness, and atomic claim/attempt fencing now
  exist in the migration contract. Restart-safe and multi-worker exactly-once
  remain NO-GO until local PostgreSQL and disposable Preview evidence both
  satisfy the 64-way contract above.
- Content production, planning, and Recap can later adopt the same state
  machine, but each remains a distinct role and credential.
- Operator approval and automatic publication remain outside the runtime.

## Verification Status

- Offline reference state machine and its prior adversarial review remain the
  only completed evidence recorded by this ADR.
- Durable migration, static contract tests, SQL security suites, and the
  updated 64-way harness passed on a fresh disposable local PostgreSQL 16 DB.
  Prepare, submit, and verify each converged `1 new / 63 reused`; claim
  converged `1 claimed / 63 not claimed`; start converged
  `1 execute-authorized / 63 replay-non-authorizing`; the positive QA stage and
  durable stage link were each inserted once. A separately isolated source
  version was then superseded after result submission; 64 reconcilers converged
  on `1 result_not_current / 63 no-op` with zero verification, positive QA
  stage, stage link, operator inbox, or Recap rows for that stale plan.
- The same local checkpoint passed 24 isolated SQL least-privilege/security
  suites, the existing Squid recovery concurrency test, 1,970 Python tests,
  and 404 Netlify function tests.
- No exact-SHA disposable Supabase Preview receipt is recorded here yet.
- Therefore the durable gate, live Codex worker, Production migration, and all
  feature-flag activation remain blocked.

## Action Items

1. [x] Implement and adversarially test the offline Squid QA state machine.
2. [x] Independently review request identity, terminal states, replay handling,
       self-review rejection, and publication-off invariants.
3. [x] Implement the ninth Preview migration and harness contract for
       DB-derived source-lineage, server-time validation, append-only receipts,
       closed QA privileges, and atomic positive-stage linking.
4. [x] Pass local PostgreSQL migration, security, and 64-way convergence
       checks; preserve only the non-secret distribution and side-effect
       receipt in this ADR.
5. [ ] Under the separately approved cost and TTL gate, pass the same contract
       on one disposable Supabase Preview and immediately confirm child
       deletion. Do not reuse or repair a failed child.
6. [ ] Under another separate approval, connect one QA-only Codex worker with
       no planning, content, operator, Recap, messaging, or publication token.
7. [ ] Require clean Preview rounds and measured operator value before adding
       the next specialist lane.
