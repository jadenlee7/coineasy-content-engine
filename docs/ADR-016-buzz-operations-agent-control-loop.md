# ADR-016: Buzz Operations Agent control loop

- Status: Proposed; local implementation only
- Date: 2026-08-13
- Owners: CoinEasy Content Engine
- Related: ADR-013, ADR-015

## Context

The current Buzz integration is a bounded review surface. It can deliver an
OriginTrail review pack, recognize two exact review commands, and durably
record the resulting decision. It does not behave like an operations agent:
general channel messages are ignored, no planning task is created, and a
successful review decision does not start editing, publishing, or analysis.

That boundary was intentional while the delivery and acknowledgement ledgers
were being proven. It is now too narrow for the intended product: a team should
be able to ask the CoinEasy agent what is happening, request today's plan, and
control the next task from Buzz without waiting for an engineer.

The first operations surface must not accidentally turn conversational text
into provider spend or publication. It also must not repeat a visible response
when a relay or database response is lost.

## Decision

Introduce a separate, staging-first `origintrail-buzz-operations@1` control
plane. It is independent from the review-decision worker and accepts only these
Korean commands:

| Command | Placement | Effect |
| --- | --- | --- |
| `상태` | top-level channel message | Return a bounded operational snapshot. |
| `오늘 기획` | top-level channel message | Create or reuse one durable daily-plan task for the KST day. |
| `다음 작업` | top-level channel message | Return the oldest pending operations task, if one exists. |
| `보류` | direct reply to an agent task response | Move the referenced pending task to `held`. |

Whitespace around a command may be trimmed. All other content, including code
fences, prefixes, suffixes, mentions, and arbitrary instructions, is ignored.
Only allowlisted reviewer pubkeys in the configured channel may issue commands.

### Durable state

Two FORCE-RLS tables are used:

1. `agent_runtime.buzz_operations_tasks` stores planning work. The first task
   type is `daily_plan`; it has `pending`, `held`, and `completed` states. One
   task per workspace/KST day is allowed.
2. `agent_runtime.buzz_operations_commands` stores the immutable command
   identity and the mutable response-delivery state. The command, selected task,
   and fixed Korean response are committed atomically before any relay write.

The response state machine is:

`pending -> claimed -> attempt_started -> delivered`

Pre-attempt failures may return a lease to `pending` or terminate as `failed`.
After `attempt_started`, uncertainty becomes `delivery_unknown` and is never
automatically retried. Exact relay-thread reconciliation may promote an unknown
response to `delivered`; it may never create another provider attempt.

Direct table access is denied. A dedicated NOLOGIN/NOBYPASSRLS role receives
EXECUTE only on the bounded operations RPCs. The Netlify adapter holds that
scoped credential and has no Buzz private key. The Railway worker holds the
Buzz key and adapter token but no Supabase, OpenAI, Batch, Studio publication,
or deployment credential.

### Runtime gates

The following flags default to literal `false`:

- `BUZZ_OPERATIONS_ENABLED`
- `BUZZ_OPERATIONS_RESPONSE_ENABLED`
- `BUZZ_OPERATIONS_OUTBOX_ENABLED` (Netlify)

Response sending is restricted to `staging` in this ADR. Production rollout
requires a separate approval, exact release/config binding, a one-response
pilot cap, and an enable-to-disable rollback receipt.

### What `오늘 기획` means in v1

It creates a durable planning task and acknowledges it. It does **not** call a
model yet. A future planner worker may claim these tasks only after a separate
provider-spend design and approval. This separates the always-on interaction
plane from the expensive creative plane.

## Safety invariants

- Automatic publication remains OFF. No operations RPC may insert an approval,
  publication, Batch job/member/run, or provider-create intent.
- No operations module imports or calls OpenAI, the Batch dispatcher, Studio
  publication, or social publishers.
- One command event produces at most one command row and one response attempt.
- A `보류` command can affect only the task bound to the exact agent response it
  directly replies to.
- Unknown or duplicate relay outcomes never cause a blind resend.
- Disabled and validate-only modes perform zero HTTP, database, relay, provider,
  or publication calls.

## Rollout

1. Land code and schema with all flags OFF.
2. Apply migrations to a disposable Supabase Preview branch and verify FORCE
   RLS, exact RPC grants, zero table grants, and empty operations rows.
3. Deploy a Netlify deploy-preview and a Railway staging scanner with all flags
   OFF; validate zero-I/O hold.
4. Enable one bounded staging session. Exercise `상태`, `오늘 기획`, `다음 작업`,
   and `보류`, verifying one response per command and zero publication/provider
   deltas.
5. Disable, delete the Preview branch, and preserve only non-secret receipts.
6. Production remains out of scope until a separate production pilot approval.

## Consequences

Buzz gains a real operational interaction loop without making arbitrary chat a
privileged instruction. The company can progressively attach a planner,
creative generation, review, publication, and analytics workers to the durable
task ledger. The tradeoff is deliberate: v1 feels command-driven rather than
fully conversational, but it is observable, recoverable, and safe enough to
run continuously.
