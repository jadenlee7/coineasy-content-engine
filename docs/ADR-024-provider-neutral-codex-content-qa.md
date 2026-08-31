# ADR-024: Provider-neutral Codex content QA

**Status:** Proposed

**Date:** 2026-08-30

**Deciders:** CoinEasy Content Engine maintainers and production owner

## Context

The current optional second-review path is coupled to the Grok provider and a
private Telegram relay. Its durable claim and receipt fences are valuable, but
the active path requires xAI, dispatch, and relay credentials and names its
database contract after one provider. The official-X pipeline does not depend
on this reviewer: it already ingests official sources and creates immutable
`needs_review` content versions while the Grok dispatcher is disabled.

CoinEasy wants Codex to perform the private QA step without giving a model
executor approval, publication, generation, or destination-selection
authority. Replacing the model must not weaken the existing identity boundary.
A verdict is valid only for the exact current immutable `needs_review` version,
its position-zero official source item, and its canonical PNG banner hash. A
task transcript or model response alone is not a durable receipt, and an
unbounded Supabase credential is not an acceptable substitute for a narrow
recording contract.

The first execution surface will be a Codex desktop heartbeat used as a shadow
reviewer. That surface is useful for proving the input and verdict contracts,
but it depends on a local host and task lifecycle and therefore is not the
final 24/7 executor.

## Decision

Adopt a provider-neutral private content-QA contract with Codex as the first
executor.

The following boundaries are part of the decision:

1. Keep `GROK_QA_DISPATCH_ENABLED=false`. Do not invoke xAI or the Grok
   provider, and do not relay QA results to Telegram.
2. Leave official-X ingestion and generation unchanged. The generation
   principal may create the existing immutable draft, source link, asset, and
   QA eligibility state, but it may not execute or record a QA verdict.
3. The QA reader may read only an eligible exact tuple consisting of:
   - the current content item in `needs_review`;
   - its exact current immutable content version;
   - the single natural official-X generation job for that version;
   - the position-zero source link and exact active official-X source item ID,
     canonical URL, and published timestamp;
   - the canonical PNG asset and its exact banner SHA-256;
   - the zero-approval and zero-publication preconditions needed to keep the
     review advisory.
   The first MCP surface is limited to Yellow, Babylon, and Squid natural
   `official_x_review_draft_v1` jobs. OriginTrail's distinct Batch provenance
   remains out of scope until it has an equivalent provider-neutral contract.
4. Introduce the provider-neutral private ledger
   `private.content_qa_jobs`. Codex-specific MCP tooling may write QA state only
   through the bounded, database-only, exactly-once
   `public.record_content_qa_verdict` RPC. It receives no direct table-write
   capability and no generation, approval, publication, or relay capability.
5. The record RPC must atomically revalidate the exact tuple and fail closed if
   the item is no longer current or `needs_review`, the source or banner hash
   differs, or the approval/publication boundary has changed. A unique
   database identity for the content version and QA contract makes replay
   idempotent. The durable row binds the executor and contract version, exact
   IDs, input SHA-256, verdict SHA-256, bounded verdict, and execution release
   evidence. Raw provider responses, secrets, private destination data, and
   publication authority are outside the contract.
6. Keep generation and QA as separate principals. The draft MCP has a
   dedicated external bearer and uses a publishable project key plus a scoped
   `SUPABASE_CONTENT_QA_KEY` JWT whose PostgREST role is
   `coineasy_content_qa`. That role can execute only QA-specific list, package,
   readiness, receipt-read, and verdict-record contracts. Its workspace claim
   is checked again inside each security-definer boundary. The JWT also binds
   `sub=codex:content-qa`, `capability=content_qa_review`, the production
   environment, and the exact deployed release SHA; the write RPC requires
   that claim SHA to equal the durable reviewer release. Its Storage policy
   permits reads only below that exact workspace path. It receives no direct
   content-table access, generation RPC, approval, publication, relay, or
   service-role credential.
   A production QA executor must also run in an isolated process or service
   whose environment does not contain a site-wide Supabase service-role
   credential. Removing the code dependency alone is not process-level secret
   isolation, so deploying this Draft function on the existing shared Netlify
   runtime remains blocked.
7. Start with a read-only Codex desktop heartbeat shadow against one exact
   eligible version. After the contract is reviewed and a separately approved
   migration exists, exercise one exactly-once private record canary. The
   heartbeat must not be treated as a 24/7 service guarantee.
8. A later provider-neutral Railway executor may implement the same read and
   record contracts for unattended 24/7 operation. It must use an isolated
   least-privilege principal and exact deployed-release fence; changing the
   executor must not change the database identity or QA contract.
9. Retain the existing Grok code path and its evidence in the OFF state as a
   rollback option. Decommissioning it requires a separate decision and
   approval after the provider-neutral path has passed canary and failure
   tests. This is a path-level rollback for versions that have not crossed
   either review boundary. When a Content QA receipt first wins for an exact
   version, the same transaction makes any pristine matching Grok outbox row
   `obsolete`; Grok must never replay that exact version.

This ADR authorizes architecture and local contract work only. Production
migration, deployment, configuration changes, secret creation or removal,
Grok-path decommissioning, approval, publication, and any public or private
message send remain separate approval gates.

## Options Considered

### Option A: Provider-neutral ledger with staged Codex executors

| Dimension | Assessment |
|---|---|
| Complexity | Medium: new bounded ledger and RPC, existing pipeline unchanged |
| Cost | Low during desktop shadow; executor-dependent for 24/7 operation |
| Scalability | High: one contract supports desktop and Railway executors |
| Team familiarity | Medium: reuses existing exact-version and receipt patterns |

**Pros:**

- Removes xAI and Telegram from the new QA execution path.
- Preserves database-authoritative exactly-once evidence.
- Keeps provider and execution surface replaceable without changing content
  generation.
- Allows a low-risk read-only shadow before any production write.

**Cons:**

- Requires an additive schema and a narrowly scoped MCP/RPC contract.
- Desktop shadow execution is not sufficient for a 24/7 service objective.
- The old and new paths coexist until a separately approved decommission.

### Option B: Keep the Grok dispatcher as the active reviewer

| Dimension | Assessment |
|---|---|
| Complexity | Low: already implemented |
| Cost | Ongoing xAI, relay, and operational-secret overhead |
| Scalability | Medium: durable worker, but provider- and relay-specific |
| Team familiarity | High: existing runbook and canary evidence |

**Pros:**

- Retains the currently implemented claim, evidence, and failure fences.
- Requires no new QA ledger before another Grok run.

**Cons:**

- Keeps QA coupled to xAI-specific behavior and Telegram delivery.
- Maintains additional secrets and cross-service failure modes.
- Does not meet the provider-neutral Codex direction.

This option is retained only as an OFF rollback path.

### Option C: Use the Codex desktop heartbeat as the permanent executor

| Dimension | Assessment |
|---|---|
| Complexity | Low initially |
| Cost | Low infrastructure overhead |
| Scalability | Low: local host and task lifecycle are execution dependencies |
| Team familiarity | High for supervised operations |

**Pros:**

- Fastest way to prove the QA prompt, image review, and bounded verdict.
- Keeps results visible to the human reviewer without Telegram.

**Cons:**

- Does not provide a production-grade 24/7 availability guarantee.
- A task transcript is not an exactly-once database receipt.
- Retries and concurrent wakes remain unsafe without the database RPC and
  unique identity selected in Option A.

### Option D: Let Codex write existing tables directly

| Dimension | Assessment |
|---|---|
| Complexity | Low in code, high in operational risk |
| Cost | Low implementation cost |
| Scalability | Poor: every caller must reproduce database invariants |
| Team familiarity | Superficially familiar, but inconsistent with current fences |

**Pros:**

- Avoids designing a new record RPC.

**Cons:**

- Gives the model executor excessive database authority.
- Cannot guarantee atomic revalidation and exactly-once recording.
- Risks recording a verdict for a stale version, source, or banner.

This option is rejected.

## Trade-off Analysis

Option A adds one schema and RPC boundary now in exchange for removing model
and delivery-provider coupling from every later executor. It also places the
exactly-once guarantee where it can be enforced: in the database transaction,
not in a heartbeat prompt or worker retry policy.

Option B is the shortest route to another automated review, but it preserves
the xAI and Telegram dependencies that this decision is intended to remove.
Keeping it disabled provides rollback value without allowing two reviewers to
race for the same content version.

Option C is the fastest validation surface and is therefore the first shadow
step, but it cannot satisfy unattended availability on its own. Moving later
to Railway is safe only if both surfaces implement the same provider-neutral
read and record contracts. Option D is simpler only by moving concurrency,
authorization, and stale-input risks into every caller; it is not acceptable.

## Consequences

- Official-X ingestion and draft generation continue even when every QA
  executor is off.
- Codex can review exact content without xAI or Telegram credentials.
- A QA verdict becomes durable only after the record RPC commits the exact
  database identity and hashes; task output remains shadow evidence otherwise.
- Approval and publication remain separate human-controlled transitions.
- The new ledger and RPC require additive migration, security, replay,
  stale-input, and principal-isolation tests.
- The Draft MCP's external bearer, scoped database JWT, publishable project
  key, and QA-only PostgREST role are separated from generation. Credential
  creation, production injection, and live PostgREST/Storage probes remain
  separate rollout gates; this Draft does not create or install a credential.
- Production rollout also requires an isolated executor runtime that contains
  no site-wide Supabase service-role credential. The shared Netlify site does
  not satisfy that boundary merely because this function never reads the
  credential.
- The desktop heartbeat accelerates supervised rollout but does not satisfy
  the future 24/7 availability target.
- Operating two implemented QA paths increases temporary maintenance cost,
  although the Grok dispatcher remains OFF and the first provider-neutral
  receipt atomically closes any pristine matching Grok row.
- Existing Grok evidence remains auditable and is not relabeled as Codex or
  provider-neutral evidence.
- OriginTrail Batch review remains unchanged and unavailable through the first
  Codex QA MCP surface.

## Action Items

1. [ ] Define the `private.content_qa_jobs` row contract, state transitions,
   unique identity, and bounded verdict schema in a local migration proposal.
2. [ ] Define the database-only exactly-once record RPC and prove that the
   caller's expected generation job, source ID/URL/timestamp, banner SHA-256,
   version, approval, and publication tuple is atomically revalidated, with
   stale input and replay rejected.
3. [x] Replace the Draft MCP's shared Netlify service-role dependency with a
   dedicated Codex QA database/runtime principal that can call only the
   bounded QA read and record contracts and cannot generate, approve, publish,
   relay, inherit generation credentials, or write tables directly.
4. [ ] Place the production QA executor in a separate process or service whose
   environment contains only the publishable key, scoped QA JWT, and its own
   bounded external bearer; prove that no site-wide service-role credential is
   present before deployment approval.
5. [ ] Run one exact-version desktop heartbeat in read-only shadow mode and
   compare its bounded result with the immutable source and banner inputs.
6. [ ] Obtain separate approval before applying any production migration or
   enabling one private exactly-once Codex record canary.
7. [ ] Specify and test the provider-neutral Railway executor, including
   release fencing, crash recovery, replay handling, and secret isolation,
   before requesting 24/7 deployment approval.
8. [ ] Keep the Grok dispatcher OFF and verify that no xAI, Telegram, approval,
   publication, or public-delivery call occurs during the Codex rollout.
9. [ ] Request a separate decommission decision before removing Grok code,
   deployments, credentials, or historical evidence.
