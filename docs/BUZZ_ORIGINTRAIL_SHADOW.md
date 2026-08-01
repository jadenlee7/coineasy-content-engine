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
  -> future deterministic delivery worker                 [HOLD]
  -> official `buzz messages send` CLI
  -> dedicated #origintrail-shadow channel
```

The endpoint and authenticated Studio deep link are implemented. No Buzz relay
write, new Railway service, new Supabase project, migration, or provider call is
part of this build.

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

The shadow reader gets one credential only:

```text
BUZZ_SHADOW_ACCESS_TOKEN=<new random value, 32..512 characters>
```

It presents that value as `x-coineasy-buzz-key`. The endpoint is GET-only,
`no-store`, exact OriginTrail-only, and fails the whole response closed if an
item or cursor is ambiguous.

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

The future worker must invoke the program with an argv array and pass content
through stdin; it must not construct a shell command. Buzz has no dry-run flag
for `messages send`, so local and staging shadow tests stop before this command.

`buzz-acp` remains useful later for owner-only conversational commands. If it is
introduced, set an explicit non-bypass permission mode, use a dedicated Nostr
keypair and isolated runtime, and provide no provider, publish, outreach,
database-admin, or deploy credentials. ACP heartbeat is not the polling
mechanism because it would spend model tokens merely to discover whether work
exists.

## Delivery gate

Before the first relay write, implement and test a durable delivery receipt so
an unknown CLI outcome cannot create duplicate messages. The worker must:

1. poll only this endpoint with keyset pagination;
2. format a fixed top-level text message with no mentions, replies, files, or
   broadcast;
3. claim one deterministic event through a durable receipt;
4. execute the official Buzz CLI once;
5. store the returned relay event ID or mark an unknown outcome for manual
   reconciliation without blind retry;
6. possess only the shadow-read token, dedicated Buzz key, relay URL, auth tag
   if required, and channel UUID.

The first live relay write requires a separate operator approval. Production
approval, publication, outreach, deployment, and OpenAI calls remain outside
this adapter.

Official Buzz references:

- [desktop v0.5.3 release](https://github.com/block/buzz/releases/tag/desktop-v0.5.3)
- [`buzz-acp` configuration](https://github.com/block/buzz/blob/desktop-v0.5.3/crates/buzz-acp/README.md)
- [`buzz` CLI](https://github.com/block/buzz/blob/desktop-v0.5.3/crates/buzz-cli/README.md)
