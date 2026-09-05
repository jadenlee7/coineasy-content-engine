# ADR-025: Squid Codex Gate Runner v1

**Status:** Proposed; exact SHAs
`a24492147b256785b71bc431e268844587591df1`,
`919d70feb0b778830d8f20f70823c20fcf049f61`,
`91dc0fc6cba7025d8db816f9864dd0a5d89acd3e`, and
`b64b6676d2f8f67690288b82cd319a1d45864fc2` each produced a fail-closed paid
Preview receipt. Exact SHA `0a71391578f0cb4d6490b96326eb016a6a85fb83`
produced a fifth fail-closed invocation before paid child creation. None was a
successful proof. Five invocations occurred, four reached paid-child creation,
and four children were created. All four children and all five scoped PATs were
verified deleted, and neither the durable migration nor runner has been applied
to Production.
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

The new durable Codex gate described here is included as a checked-in migration
and one-shot Preview proof runner. Local PostgreSQL 16 replay passes all nine
allowlisted migrations and all three Harmony security suites, alongside the
64-way convergence checks. The paid `harmony-preview-one-shot-proof@4` execution
at the first exact SHA above failed closed with `preview_migration_apply_failed`.
The paid `harmony-preview-one-shot-proof@5` execution at the second exact SHA
failed closed before SQL with `preview_database_connectivity_failed`. The paid
`harmony-preview-one-shot-proof@6` execution at the third exact SHA failed
closed before SQL with `branch_pooler_default_pool_size_insufficient`. The paid
`harmony-preview-one-shot-proof@7` execution at the fourth exact SHA failed
closed before SQL with `branch_pooler_default_pool_size_unobserved`. The fifth
`harmony-preview-one-shot-proof@8` invocation at exact SHA
`0a71391578f0cb4d6490b96326eb016a6a85fb83` failed before price readback and
child creation with `supabase_billing_addons_preflight_transport_failed`.
Across the five invocations, paid-child attempt and created-child counts are
both four. All four children and all five scoped PATs were deleted and verified
absent; actual billing is `unobserved`. Those files have not been applied to
Production. Neither the local evidence nor any failed receipt substitutes for
a successful, separately approved exact-SHA Supabase Preview proof. This ADR
therefore does not call the durable gate deployed, deployable, or
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

Repository inclusion is not runtime activation. The migration and runner may
be reviewed and tested from this branch, but they do not authorize a Production
migration, a feature-flag change, a live Codex/provider call, Grok or Buzz use,
an approval decision, external messaging, Recap delivery, or publication.
Automatic publication remains OFF.

## Decision

Use a Codex-first, fixed-specialist `Squid Codex Gate Runner v1`. The existing
offline, no-I/O state machine remains the executable reference model. The
Preview DB projection adds append-only source-lineage, request, transition,
claim, attempt, evidence, result, verification, reconciliation, and stage-link
receipts plus one mutable run projection. This is fixed role ownership, not
runtime model assignment and not a super-agent.

### External approval boundary and mechanical cost guard

Every representative approval gate remains a manual operating boundary outside
the one-shot Preview runner. The operator must separately confirm the exact
release SHA, Production parent ref, paid Preview creation, Preview-only
migration and proof scope, immediate deletion, and any later Deploy Preview or
worker step before invoking the corresponding action. An earlier gate does not
authorize a later gate.

The current runner does not authenticate an approver, validate the truth of an
approval receipt, or atomically consume an approval once. Its terminal receipt
therefore cannot be used as proof that representative approval was genuine,
current, or one-time. Long-running or unattended automation would require a
separately designed, non-Production control plane with an authenticated,
expiring one-time grant and atomic consumption. That mechanism is not part of
this decision or the current runner.

The runner instead requires explicit `--max-small-hourly-usd` and
`--max-total-cost-usd` values and, before paid child creation, reads the current
usage/hourly `ci_small` price from the parent project's Management API billing
response. It fails closed when that price exceeds the hourly ceiling or when
the admission estimate exceeds the total ceiling. The estimate uses the
checked-in maximum watchdog exit-attempt budget: 110 minutes of sleep, five
minutes of reconciliation, one fixed 20-second LIST allowance, one fixed
30-second DELETE allowance, and conservative process-fence and poll allowances,
for `WATCHDOG_MAX_EXIT_ATTEMPT_SECONDS=6983` (less than two hours). The receipt
records the same value as `watchdog_max_exit_attempt_seconds=6983`; its ceiling
is therefore `billable_hours_estimate=2`, and
`admission_estimate_total_usd = observed_hourly_usd * 2`.

These values and the live readback are mechanical admission guards only. The
receipt records `is_approval_evidence=false`, `server_side_budget_lock=false`,
and `within_estimated_total_cap`; it does not call the estimate an actual charge
or an absolute cap. The guard covers disposable Supabase infrastructure billing
only and does not relax the zero model/provider-cost authority represented by
`max_cost_microusd=0`.

Before creating a scoped PAT, waiting for a public key or token, claiming an
invocation, or starting the paid runner, the operator must run the committed
`scripts/probe_harmony_management_reachability.py` in the same shell, Python
interpreter, and network-permission path. It sends one fixed, unauthenticated,
mutation-free billing add-ons GET with environment proxies disabled and
redirects rejected. Only an
HTTP 401 with `harmony-management-reachability@1`, `category=http_status`, and
`ok=true` permits the credential flow to start. Credential environment
presence and every other HTTP or transport result fail closed before network
or credential use as applicable. The probe records no Authorization header,
body, exception message, or invalid arbitrary input. Transport categories are
limited to `dns`, `tls`, `timeout`, `connect`, `response_io`, `client_value`, and
`unknown`.

The required wrapper order is tokenless probe, scoped PAT creation, public-key
and token wait, invocation claim, then runner process creation. A failed probe
requires zero occurrences of every later step. This ordering remains an
operator-wrapper contract: the probe alone cannot prove that a future wrapper
obeyed it.

The current outer receipt is `harmony-preview-one-shot-proof@10`. The runner
requires an explicit `direct` or `supavisor-session` route before any paid child
creation and records that choice. It never switches routes on failure. The
session route first validates read-only parent pooler access, then binds the
exact child `PRIMARY` pooler row and derives only the documented session port
5432 from its transaction-mode 6543 response. It never parses a returned
connection string. Parent pooler values cannot serve as child endpoint or
capacity evidence. Management API transport failures use the same secret-free
typed categories and the same proxy-disabled, redirect-rejecting network path;
an unclassifiable `URLError` reason retains the legacy generic
`transport_failed` code. HTTP error bodies are closed without being read, and
only allow-listed status-derived codes reach the receipt.

The `@9` invocation at `cc6de5abcbc424075d57e42eef65ce9a4f91eb7a`
failed after two completed migrations, at
`20260825132000_harmony_preview_collaboration.sql`; child deletion and scoped
PAT removal were confirmed. Its receipt does not contain SQLSTATE or an input
line, so it cannot distinguish missing baseline relations, missing columns,
privileges, or another SQL error. Local reproduction does not retrospectively
supply the missing hosted evidence.

`@10` adds optional SQLSTATE and psql input-line metadata only for completed
migration/security script failures with exit status 3. Both values are
allowlisted or bounded by the exact script bytes; verbose output, arbitrary
exception text, and unknown conditions yield no optional detail. Timeout,
connection, interrupt, and cleanup paths retain their existing behavior.
No migration SQL or permission grant is changed. This is diagnostic hardening,
not evidence that the hosted SQL failure has been fixed; a new hosted invocation
still needs its own exact-SHA approval.

All remote database subprocesses use `verify-full` with the checked-in
`certs/supabase-prod-ca-2021.crt` (`Supabase Root 2021 CA`) bytes bound to the
exact release SHA and fixed SHA-256
`700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7`.
The runner writes those bytes to an anonymous pipe, closes the writer, validates
the macOS unlinked FIFO or the Linux `/proc/self/fd/N = pipe:[inode]` identity
plus the read-only descriptor, and passes only that descriptor via
`pass_fds` and `/dev/fd/<n>`. Each `psql` process receives a fresh anonymous
pipe; no named CA file or directory is created. The child rechecks the exact
digest and the runner rejects system or caller-supplied trust and weaker SSL
modes. The receipt binds the artifact under `proof_artifact_sha256` and retains
the existing `cleanup.ssl_root_cert_removed=true` field to mean that no named
path was created and every owned CA descriptor was closed. Cleanup failure
makes the outer receipt fail.

The outer receipt uses the route-neutral `database_concurrency` result and
`database_client_race_64_way` step; it has no legacy `direct_database` key.
It accepts only `harmony-preview-concurrency-proof@5` and
`harmony-preview-postgrest-proof@3`, rebuilding their TLS and server-overlap
objects from exact allowlists rather than forwarding arbitrary child JSON.
`database_pooler_capacity` is null for direct. For a session route with verified
numeric configuration it contains `default_pool_size`, `max_client_conn`,
`max_client_at_least_64`, and `backend_concurrency_target`. When the exact child
`default_pool_size` is numeric, it must be at least two so the row-lock holder and
observer cannot self-deadlock; `backend_concurrency_target=min(default_pool_size,64)`.
A numeric `max_client_conn` below 64 fails closed.

The Management API permits a nullable `default_pool_size`. A null value is not
configured capacity evidence. After validating the exact-child pooler structure
and identity, the runner does not spend the remaining branch-readiness window
polling for a number. It loads the exact child secrets and runs the existing
64-client database advisory-latch and signed PostgREST blocker-graph probes with
a runtime lower-bound target of two. It does not extend a timeout, substitute
parent capacity, or fall back to direct. An integer one remains a terminal
insufficient-capacity failure and is not reinterpreted as provisioning lag.
Both capacity keys remain required; a missing key is schema drift and is
rejected rather than treated as explicit JSON null.

`database_backend_target_selection` is null for direct and remains null when a
session attempt fails before a valid exact-child target is selected. After that
selection, an integer `default_pool_size>=2` produces
`{source:"management_api_default_pool_size",target:min(default_pool_size,64),
runtime_verified:<bool>}` and the configured values remain in
`database_pooler_capacity`. For `default_pool_size=null`, it is
`{source:"runtime_lower_bound_required",target:2,runtime_verified:<bool>}` while
`database_pooler_capacity` remains null. `runtime_verified` equals the final
outer `ok` bool and becomes true only after both nested live proofs pass outer
contract validation. A null `max_client_conn` remains unobserved and success
still requires measured 64-client ingress; a numeric value below 64 fails
closed. The separate secret-free `database_pooler_readiness` field records only
the bounded read count and last nullable numeric observation plus its state. No
raw pooler response, endpoint, principal, connection string, or error text
enters these fields.

After loading the exact child credential and selected route, the runner runs a
secret-free `SELECT 1` connectivity preflight before applying SQL. A typed or
ambiguous migration/security `psql` apply failure may expose only the
allowlisted diagnostic
`sql_failure={phase,ordinal,filename,sha256,completed_count}`: the filename is a
fixed basename, the digest binds the exact payload, and `completed_count` must
equal `ordinal - 1`. Operator interrupts and unrelated cleanup failures leave
this field null. Raw stdout/stderr, SQL text, connection strings, secrets, and
arbitrary exception text are prohibited from the receipt.

One invocation may create at most one uniquely named, non-persistent,
no-data child. It never repairs or replaces that child. Any new invocation,
including a retry after failure, requires a fresh external representative
approval and a newly scoped PAT. The runner removes the PAT from its process
environment and temporary credential homes, but it does not prove that the PAT
was freshly issued or revoked server-side.

The watchdog never treats a DELETE exit code as authoritative absence. Whether
DELETE succeeds, returns nonzero, or times out, it may retry deletion of the
same exact child only when a subsequent authoritative LIST still shows that
child, and only inside the bounded reconciliation window. This also covers
eventual-consistency lag after a successful DELETE. It is a cleanup retry, not
child repair or replacement.

For Supabase CLI 2.116, an authoritative LIST is the successful exact
`{"branches": [...], "message": ""}` response produced by
`--output-format json`. Every row must bind to the exact
Production parent through `parent_project_ref`, must not be the parent/default
row, and must have a unique valid child identity. An empty validated response is
authoritative only after the exact-parent Management API billing preflight.
Malformed, wrong-parent, default, equal-parent, or duplicate rows cannot
authorize a subsequent create or delete. If first observed after CREATE, the
foreground fails closed and the scoped cleanup/watchdog remains responsible for
the exact-name child. Neither component accepts the legacy `-o json` bare array
or expects a fabricated main row in this child-only contract.

The 6,983-second estimate is not a server-side budget lock. If the Management
API, Supabase CLI, process fence, immediate deletion, or required absence checks
do not complete as bounded, the runner cannot guarantee an absolute total-cost
ceiling. The operator must immediately perform manual cleanup, revoke the
scoped PAT, and obtain a new approval before any new invocation.

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

The approved 64-way proof contract is exact, not statistical. It separates 64
simultaneously held TLS client sessions and 64 authenticated client calls from
the PostgreSQL backend overlap readback. Direct requires a server peak of 64;
the session route requires `database_backend_target_selection.target` for all
twelve DB races. The signed PostgREST proof separately holds 64 HTTPS TLS
sessions, executes 64 signed requests, and observes at least
`min(database_backend_target_selection.target,8)` matching RPC backends in the
registration row-lock blocker graph. It does not claim 64 concurrent PostgREST
DB backends.

The logical convergence contract is:

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

The durable contract is the included, unapplied-to-Production migration
`20260827220000_harmony_preview_codex_gate_durable.sql`, applied ninth after the
eight existing Harmony Preview migrations. It may be verified only from a
clean exact Git SHA on one separately approved non-persistent disposable
Preview child. Success and failure both require immediate child deletion;
the estimated two billing hours are only a pre-create admission calculation,
not a TTL or server-side budget lock. Preview proof does not authorize a
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
| Duplicate-call safety | Local durable DB proof passed; successful Supabase Preview proof pending |
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
- The same local checkpoint passed the then-current isolated SQL
  least-privilege/security checks, Squid recovery concurrency check, and full
  Python and Netlify function suites. Exact counts are intentionally omitted
  because they change as the repository evolves; the current exact SHA must be
  re-tested before any Preview execution.
- The current gate SQL was also exercised against PostgreSQL 16.13 with real
  sessions. The direct advisory latch observed 64 distinct backend PIDs and a
  server peak of 64. A 64-caller registration-row lock simulation reached the
  PostgREST blocker threshold at 57, then observed all 64 blocked callers,
  released the holder, completed every caller, and dropped the latch. This
  validates the PostgreSQL lock/readback mechanism and the migration's matching
  `FOR UPDATE` path; it does not substitute for live PostgREST/JWT or Supavisor
  evidence.
- Exact SHA `a24492147b256785b71bc431e268844587591df1` produced one paid
  `harmony-preview-one-shot-proof@4` receipt that failed closed at
  `preview_migration_apply_failed`. The exact child and scoped PAT cleanup were
  verified; actual billing remains `unobserved`. This is not a successful proof.
- The `@4` receipt could not distinguish direct database connectivity failure
  from failure against the hosted baseline because it had neither the separate
  connectivity preflight nor the allowlisted ordinal SQL diagnostic.
- Exact SHA `919d70feb0b778830d8f20f70823c20fcf049f61` produced one paid
  `harmony-preview-one-shot-proof@5` receipt that failed closed at
  `preview_database_connectivity_failed`. SQL did not start, migration and
  security completed counts were zero, and `sql_failure` was null. Three exact
  child absence confirmations and scoped PAT deletion were verified; actual
  billing remains `unobserved`. This is not a successful proof.
- The `@5` receipt itself proves only the typed connectivity failure. Separate
  secret-free diagnosis reproduced an exact direct host with AAAA but no A
  record and `No route to host` at IPv6 port 5432. The Seoul shared pooler at
  IPv4 port 5432 completed TLS hostname verification with the pinned Supabase
  CA, while the system CA rejected its self-signed chain. These are transport
  and trust blocker observations, not deleted-child authentication, SQL, or
  proof success.
- Exact SHA `91dc0fc6cba7025d8db816f9864dd0a5d89acd3e` produced one paid
  `harmony-preview-one-shot-proof@6` receipt that failed closed at
  `branch_pooler_default_pool_size_insufficient`. The receipt did not preserve
  whether the nullable Management API value was null or integer one. SQL did
  not start, migration and security completed counts were zero, three exact
  child absence confirmations and scoped PAT deletion were verified, and
  actual billing remains `unobserved`. This is not a successful proof.
- Exact SHA `b64b6676d2f8f67690288b82cd319a1d45864fc2` produced one paid
  `harmony-preview-one-shot-proof@7` receipt that failed closed at
  `branch_pooler_default_pool_size_unobserved` after 165 pooler read attempts
  without sufficient numeric capacity evidence; the final observation was null.
  SQL did not start, migration and security completed counts were zero, three
  exact child absence confirmations and scoped PAT deletion were verified, and
  actual billing remains `unobserved`. The approval and PAT used for this
  invocation are consumed and cannot authorize another paid run.
- Exact SHA `0a71391578f0cb4d6490b96326eb016a6a85fb83` produced one approved
  `harmony-preview-one-shot-proof@8` invocation that failed closed at
  `supabase_billing_addons_preflight_transport_failed`, before price readback
  and before branch creation. The invocation count is one, while paid-child
  attempt and created-child counts are both zero. The scoped PAT was deleted,
  the owner UI showed zero Preview children, and actual billing remains
  `unobserved`. The generic historical receipt does not prove a transport
  subtype, and its approval cannot authorize a retry.
- The current `@9` runner preserves the `@8` explicit route, exact parent and
  child pooler readbacks, pinned CA over anonymous descriptors, route-neutral
  concurrency receipt, 64-client TLS ingress, route-bound server overlap
  readback, pre-SQL `SELECT 1`, and safe SQL diagnostic. It additionally
  keeps integer one terminal while sending a nullable unobserved pool size to
  the existing live probes with a minimum target of two. The receipt separates
  configured capacity from this runtime lower-bound selection and marks it
  verified only when both nested proof contracts and the outer proof succeed,
  without retaining raw command output, SQL, secrets, endpoint identity, or
  exception text. It adds typed, message-free Management API transport failure
  codes while retaining the generic code for an unknown category.
- The durable migration and runner are included but not applied to Production.
  Therefore the exact-SHA Supabase Preview proof, live Codex worker, Production
  migration, and every feature-flag activation remain blocked.

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
5. [ ] The historical `@4`, `@5`, `@6`, `@7`, and `@8` invocations are
       consumed. First pass the tokenless Management API reachability gate in
       the exact future launch context. Only after a fresh external
       representative approval and issuance of a fresh scoped PAT, invoke the
       current `@9` runner once with both required cost ceilings and one
       explicit route, pass the same contract on one disposable Supabase
       Preview, and immediately confirm child deletion. Do not reuse, repair,
       replace, or switch route on the failed child.
6. [ ] Under another separate approval, connect one QA-only Codex worker with
       no planning, content, operator, Recap, messaging, or publication token.
7. [ ] Require clean Preview rounds and measured operator value before adding
       the next specialist lane.
