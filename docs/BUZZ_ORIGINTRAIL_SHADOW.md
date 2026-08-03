# OriginTrail -> Buzz read-only shadow

## Current state

The local build provides the first safe seam between CoinEasy's existing
execution plane and Buzz desktop v0.5.3:

```text
OriginTrail Official X
  -> existing collector and immutable evidence
  -> existing Supabase job + Batch ledger
  -> existing OpenAI Batch dispatcher
  -> existing read-only Batch Review RPC
  -> Netlify GET /api/buzz-shadow/origintrail/batch
  -> durable Supabase receipt + scoped Netlify control    [BUILT / NOT DEPLOYED]
  -> deterministic one-shot delivery worker               [BUILT / HOLD]
  -> official `buzz messages send` CLI
  -> dedicated #origintrail-shadow channel
```

The read endpoint and authenticated Studio deep link are production active. The
durable receipt migration, separate control endpoint, and one-shot worker are
implemented on the feature branch but are not applied or deployed. No Buzz
relay write, channel creation, OpenAI call, or publication is part of this
build.

## Cost boundary

The operator confirmed on 2026-08-01 that the Supabase organization is already
Pro, so this pilot has no `$25` plan-upgrade delta. The local build costs `$0`.
Before any external action, the infrastructure gate is capped at `$3.00 + tax`:
at most 48 hours of a Preview branch (about `$0.65` at the published
`$0.01344/hour` minimum), at most `$1.00` of staging Railway usage, and a
`$1.35` buffer. One live OpenAI Batch request is a separate approval capped at
`$0.05`; production and the first Buzz relay write remain unapproved.

Supabase Preview branch usage is not covered by its Spend Cap. Delete the
disposable branch after the smoke test rather than leaving it running. See the
[Supabase branching usage documentation](https://supabase.com/docs/guides/platform/manage-your-usage/branching).

## Why this boundary

Buzz is the command, conversation, and mobile visibility plane. Supabase,
Railway, OpenAI Batch, and Content Studio remain the execution, state, budget,
and review plane. A Buzz process therefore receives neither
`SUPABASE_SERVICE_ROLE_KEY` nor `STUDIO_ACCESS_TOKEN`.

The read-only projection uses one credential:

```text
BUZZ_SHADOW_ACCESS_TOKEN=<new random value, 32..512 characters>
```

It presents that value as `x-coineasy-buzz-key`. The endpoint is GET-only,
`no-store`, exact OriginTrail-only, and fails the whole response closed if an
item or cursor is ambiguous.

The future delivery process uses a second, distinct scoped credential:

```text
BUZZ_DELIVERY_WORKER_TOKEN=<different random value, 32..512 characters>
```

It presents that token only to
`POST /api/buzz-delivery/origintrail`. Netlify translates each bounded action to
one service-role RPC, so the worker never receives the service-role key. The
read token cannot mutate receipts and the delivery token cannot read Batch
drafts.

## Response contract

The response contains `schema_version: "1.0"`, `mode: "shadow_read_only"`, a
bounded `events` array, and the existing keyset `next_cursor`. Each event has
only:

- deterministic `event_id` and fixed event type;
- job/review reference and exact OriginTrail agent/workflow identity;
- `needs_review`, model tier, settled cost, and completion time;
- the canonical `https://x.com/origin_trail/status/<id>` source;
- a same-origin `/?batch=<job_id>` Studio path.

It never returns the generated title or draft, channel copy, raw input/output,
prompt, input hash, provider IDs, token counts, workspace ID, database
credential, or a mutation URL. A team member must authenticate to Studio before
the deep link opens the read-only result.

## Buzz v0.5.3 execution choice

For proactive status delivery, use a short-lived deterministic poller and the
official CLI, not a model heartbeat. The CLI's supported outbound boundary is:

```bash
printf '%s' "$CONTENT" |
  buzz messages send --channel "$CHANNEL_UUID" --content -
```

The worker invokes the program with an argv array and passes content through
stdin; it never constructs a shell command. It accepts a success only when the
v0.5.3 JSON response has `accepted=true`, a lowercase 64-hex `event_id`, and an
empty `mention_pubkeys` array. Buzz has no dry-run flag for `messages send`, so
local and staging tests stop before this command.

`buzz-acp` remains useful later for owner-only conversational commands. If it is
introduced, set an explicit non-bypass permission mode, use a dedicated Nostr
keypair and isolated runtime, and provide no provider, publish, outreach,
database-admin, or deploy credentials. ACP heartbeat is not the polling
mechanism because it would spend model tokens merely to discover whether work
exists.

## Durable delivery gate

The local implementation now enforces this transition order:

1. poll only this endpoint and select at most one event;
2. format a fixed top-level text message with no mentions, replies, files, or
   broadcast;
3. claim one deterministic event through a durable receipt;
4. preflight the exact channel, then durably mark the request fingerprint;
5. authorize `buzz messages send` only for a fresh `reused=false` marker;
6. store the returned relay event ID or mark an unknown outcome for manual
   reconciliation without blind retry;
7. pass only the two scoped adapter tokens, dedicated Buzz key, relay URL, auth
   tag if required, channel UUID, and CLI path to the isolated process.

The process defaults to a structural hold:

```bash
python -m scripts.run_origintrail_buzz_delivery
python -m scripts.run_origintrail_buzz_delivery --validate-only
```

The first command performs no I/O. Validation constructs no HTTP, database, or
subprocess client. A relay call is reachable only when the environment contains
the literal `BUZZ_DELIVERY_ENABLED=true` and the operator also supplies
`--send-once`.

The first live relay write requires a separate operator approval. Production
schema/control deployment, relay identity and channel provisioning, publication,
outreach, and OpenAI calls remain outside this adapter. See
[`ADR-011`](ADR-011-origintrail-buzz-durable-delivery.md).

Official Buzz references:

- [desktop v0.5.3 release](https://github.com/block/buzz/releases/tag/desktop-v0.5.3)
- [`buzz-acp` configuration](https://github.com/block/buzz/blob/desktop-v0.5.3/crates/buzz-acp/README.md)
- [`buzz` CLI](https://github.com/block/buzz/blob/desktop-v0.5.3/crates/buzz-cli/README.md)
