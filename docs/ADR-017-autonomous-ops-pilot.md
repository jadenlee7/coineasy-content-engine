# ADR-017: Autonomous Ops Pilot

- Status: Proposed; local implementation only
- Date: 2026-08-13
- Owners: CoinEasy Content Engine
- Related: ADR-016

## Outcome

CoinEasy needs an agent that notices work without waiting for a Buzz command.
This pilot adds the first autonomous loop:

`Observe -> Prioritize -> Propose -> Verify -> Report later`

It is deliberately not an autonomous executor yet. The agent can read a
bounded OriginTrail operational snapshot and create or reuse one durable
`proposed` task. It cannot modify code, open a pull request, deploy, call a
model, submit a Batch, send a Buzz message, approve content, or publish.

## Observation contract

The scoped Supabase RPC returns only aggregate operational signals for the
configured workspace:

- failed or stale OriginTrail Batch jobs;
- unresolved Batch cost-overage evidence;
- failed or delivery-unknown Buzz receipts;
- delivery-unknown review ACK or Operations responses;
- same-KST-day OriginTrail publications while automatic publication is OFF;
- nonterminal Batch count and cumulative actual cost.

The snapshot is hashed from an exact protocol, workspace, KST date, and sorted
metric set. A healthy snapshot causes no database write.

## Planner

The v1 planner is deterministic. Containment risks outrank availability risks:

1. unexpected publication;
2. Batch cost overage;
3. failed or stale Batch;
4. Buzz delivery unknown or failed;
5. review/Operations acknowledgement unknown.

At most one plan is selected per cycle. The incident key is stable for a
workspace, category, and KST day, so a 15-minute cron reuses the existing task
instead of producing noise. Each plan contains fixed Korean steps and is
structurally constrained to `execution_mode=propose_only`,
`automatic_execution=false`, `automatic_publication=false`, and
`external_writes=false`.

## Security boundary

- All three runtime gates default to literal `false`:
  `AUTONOMOUS_OPS_ENABLED`, `AUTONOMOUS_OPS_RECORD_ENABLED`, and Netlify's
  `AUTONOMOUS_OPS_LEDGER_ENABLED`.
- Enabled execution is restricted to staging and an exact release SHA.
- The Railway image has only the dedicated Netlify adapter token. It has no
  Supabase, OpenAI, Batch, Buzz signing, publication, GitHub, Railway, or
  Netlify credential.
- The Netlify adapter holds an optional scoped JWT for a NOLOGIN,
  NOBYPASSRLS role with exactly two RPC grants and zero table grants.
- Observation and task tables are FORCE RLS and immutable.
- No automatic retry can create an external effect because this pilot has no
  external-effect capability.

## Staging acceptance test

Use a disposable Supabase Preview branch and one synthetic failed-job fixture.
The worker must create exactly one proposed task on the first run and return
the same task with `reused=true` on the second. Publication, approval, Batch
member/run/provider intent, OpenAI, relay, and deployment deltas must stay zero.
Then disable all flags and delete the Preview branch.

## Next increment

After this ledger is proven, connect a separately approved model planner that
may enrich only the proposal text. The following increment may create a local
code patch and Draft PR in staging, but Production merge/deploy and publishing
remain human-approved. Proactive Buzz reporting also needs a durable outbox;
it must not be implemented as a blind relay send.
