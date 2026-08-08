# ADR-011: OriginTrail Buzz durable one-shot delivery

**Status:** Accepted — see the 2026-08-07 addendum for the recurring
scheduler, reconcile wiring, and scoped database credentials.
**Date:** 2026-08-03
**Deciders:** CoinEasy content engine team

## Context

CoinEasy exposes a GET-only, metadata-only OriginTrail Batch review shadow for
Buzz. The endpoint is authenticated and production active, but it deliberately
does not connect to a relay. `buzz messages send` is a non-idempotent external
write and has no dry-run flag. A timeout or lost CLI response after the relay
accepts an event must therefore never cause an automatic resend.

The adapter must preserve the existing split: Supabase remains the durable
state plane, Netlify owns narrow database access, and the Buzz process owns only
scoped adapter credentials plus its dedicated Nostr identity. The process must
not receive a Supabase service-role key, Studio session secret, OpenAI key,
publication token, or deployment credential.

## Decision

Add a private `agent_runtime.buzz_delivery_receipts` ledger and four narrow
service-role RPC transitions: claim, mark-attempt, complete, and fail. Add a
separate authenticated Netlify control endpoint that exposes only those
transitions to the worker.

The one-shot worker follows this exact order:

1. Read at most one event from the existing OriginTrail shadow.
2. Build fixed `origintrail-batch-review-ready@1` text containing metadata and
   canonical links only. Mentions, replies, files, broadcasts, model output,
   prompts, and draft copy are forbidden.
3. Claim the deterministic event through the durable control endpoint.
4. Run a read-only `buzz channels get` preflight for the exact channel.
5. Persist the exact relay/channel/message request SHA-256 as `attempt_started`.
6. Only a fresh `reused=false` attempt response authorizes one
   `buzz messages send --channel <uuid> --content -` process. It is executed as
   an argv array with content on stdin and no shell.
7. Accept completion only when CLI exit is zero and stdout is a bounded JSON
   object with `accepted=true`, a lowercase 64-hex `event_id`, and an empty
   `mention_pubkeys` array.
8. Any error after the attempt marker, including a malformed success response
   or lost completion response, becomes `delivery_unknown`. It is never
   automatically retried. Expired attempt leases reconcile to the same state.

The executable is fail-closed by default. A live call requires both the literal
environment flag `BUZZ_DELIVERY_ENABLED=true` and the explicit command-line
flag `--send-once`. Validation mode constructs no HTTP or subprocess client.

## Options Considered

### A. Retry `buzz messages send` on timeout

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Duplicate risk | Unacceptable |
| Auditability | Low |

**Pros:** Simple worker loop.
**Cons:** A committed relay event with a lost response produces duplicates.

### B. Local SQLite receipt beside the Buzz process

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Credential isolation | High |
| Durability across replacement | Medium |

**Pros:** No remote write API.
**Cons:** Ties safety to one host and volume; replacement and multi-worker
coordination are harder than the existing Supabase execution plane.

### C. Supabase receipt behind a scoped Netlify control endpoint (selected)

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Credential isolation | High |
| Durability and auditability | High |

**Pros:** Reuses the existing state plane, survives worker replacement, keeps
the service-role key out of Buzz, and can atomically fence a provider attempt.
**Cons:** Adds one migration, one Netlify function, and a second scoped adapter
token.

## Consequences

- The read-only shadow token remains read-only. Receipt mutations require the
  distinct `BUZZ_DELIVERY_WORKER_TOKEN`.
- A response loss at the attempt or completion boundary intentionally creates a
  manual reconciliation task rather than an automatic retry.
- Pre-attempt failures may retry after a bounded lease/backoff; the relay write
  itself is authorized at most once per event.
- The first live message, relay/channel provisioning, and production enablement
  remain separate operator approvals.
- This slice is OriginTrail-only and one-event-per-process. Expansion to other
  clients or a recurring scheduler requires a new decision and tests.

## Action Items

1. Add the private receipt ledger, RLS, exact OriginTrail constraints, and
   transactional security smoke.
2. Add the dedicated Netlify transition endpoint and scoped token.
3. Add the one-shot worker, strict v0.5.4 CLI adapter, and no-I/O validation
   mode.
4. Prove fresh-attempt-only send, unknown-outcome hold, and no credential
   leakage with unit and integration tests.
5. Deploy schema and endpoint while `BUZZ_DELIVERY_ENABLED=false`; provision the
   relay identity/channel and request first-write approval separately.

## Addendum — 2026-08-07: production state, scheduler, reconcile, scoped roles

**What is now true in production.** The worker runs as Railway service
`coineasy-origintrail-buzz-delivery-prod` on an hourly cron (`15 * * * *`,
`--send-once`, restart policy NEVER) with `BUZZ_DELIVERY_ENABLED=true`. The
first fenced relay write was delivered on 2026-08-04T16:16Z; its receipt shows
attempt 1 failing, the durable fence authorizing exactly one retry, and the
relay event id recorded on attempt 2 of 3 — the mechanism this ADR specified,
exercised once, end to end. This addendum records the standing hourly schedule
as the "new decision" the Consequences section required for a recurring
scheduler. The one-event-per-process and OriginTrail-only bounds are
unchanged; the cron only bounds how often the one-shot process runs.

**Reconcile now has a caller.** As specified, expired attempt leases reconcile
server-side — but nothing invoked the reconcile transition, so a receipt
orphaned by a crashed worker would have sat in `claimed`/`attempt_started`
past its lease indefinitely, and `delivery_unknown` had no observer. Every
worker run (idle ones included) now opens with a best-effort
`action=reconcile` (limit 25) and reports the transition counts in its JSON
output, which the hourly cron turns into a de facto monitor: a nonzero
`delivery_unknown_count` in the Railway logs is the manual-inspection signal.
A reconcile fault never blocks or triggers a delivery.

**The CLI subprocess dies with its timeout.** The 30-second subprocess guard
previously abandoned the child on timeout, so an orphaned `messages send`
could reach the relay after the worker had already recorded
`delivery_unknown`. The runner now kills and reaps the child before raising.
The recorded state was always safe (never a double-send); this closes the gap
where it could silently diverge from relay reality.

**Scoped database credentials.** The `20260805090000` migration defines
`coineasy_buzz_delivery`, a NOLOGIN PostgREST role granted EXECUTE on exactly
the five receipt RPCs and nothing else, and the two Netlify Buzz functions
read optional per-function credentials: `SUPABASE_BUZZ_DELIVERY_KEY` (role
`coineasy_buzz_delivery`) for the control endpoint and
`SUPABASE_BUZZ_SHADOW_KEY` (role `coineasy_batch_reviewer`, read-only — the
first adoption path for ADR-007's reviewer role) for the shadow read. Unset,
both functions keep the site-wide service-role bearer, so adoption is setting
one variable and rollback is deleting it. The scoped token is sent only as the
`Authorization` bearer; `apikey` remains the site's project API credential.
`tests/test_least_privilege_ledger_roles.py`
fails the build if the adapter's RPC set and the role's grant set drift, and
the shadow token now enforces the same distinctness-from-reserved-secrets
rule the delivery token always had.
