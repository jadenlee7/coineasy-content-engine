# OriginTrail -> Buzz read-only shadow

## Current state

The build provides the first safe seam between CoinEasy's existing
execution plane and Buzz desktop v0.5.4:

```text
OriginTrail Official X
  -> existing collector and immutable evidence
  -> existing Supabase job + Batch ledger
  -> existing OpenAI Batch dispatcher
  -> existing read-only Batch Review RPC
  -> Netlify GET /api/buzz-shadow/origintrail/batch
     (bounded headline + Telegram summary preview)
  -> durable Supabase receipt + scoped Netlify control    [PRODUCTION ACTIVE]
  -> deterministic one-shot delivery worker               [PRODUCTION ACTIVE]
  -> official `buzz messages send` CLI
  -> dedicated #origintrail-shadow channel
```

The read endpoint, authenticated Studio deep link, durable receipt migration,
separate control endpoint, and the delivery worker are all production active.
The Railway service `coineasy-origintrail-buzz-delivery-prod` runs the pinned
official v0.5.4 image hourly (`15 * * * *`, `--send-once`, restart policy
NEVER) with `BUZZ_DELIVERY_ENABLED=true`. The first fenced relay write was
delivered on 2026-08-04T16:16Z: the receipt shows the first attempt failing,
the durable fence authorizing exactly one retry, and the relay event id
recorded on attempt 2 of 3. Channel creation, OpenAI calls, and publication
remain outside this adapter. The template default stays
`BUZZ_DELIVERY_ENABLED=false`, so any new environment still starts in hold.
Preview delivery is gated by `BUZZ_RESULT_PREVIEW_START_AT`: results completed
before that exact timestamp are omitted so historical metadata-only receipts
cannot conflict with the current message fingerprint.

The reviewed-media extension keeps that Buzz contract unchanged. A media-backed
OriginTrail source may enter Batch only when a private append-only registry
matches the exact X body hash, raw provider media identity, canonical preview
hash, standalone-post proof, and a human-qualified fact-evidence envelope. The
media is explicitly non-factual provenance. Studio can show the sanitized
limitations and official references, while Buzz still receives only the same
headline, Telegram summary, source link, review link, and deterministic banner.
Unknown media continues on the existing synchronous path and cannot enter the
Batch/Buzz handoff. See
[`ADR-013`](ADR-013-origintrail-reviewed-media-evidence.md).

## Operations

Every worker run — including idle ones — opens by calling the server-side
`reconcile` transition (limit 25) and reports the result in its JSON output as
`reconcile: {ok, reconciled_count, pending_count, failed_count,
delivery_unknown_count}`. A receipt whose lease expired mid-flight therefore
surfaces in the Railway logs within an hour: an expired `claimed` lease
re-queues (`pending_count`) or exhausts (`failed_count`), and an expired
`attempt_started` lease becomes `delivery_unknown_count`, which is the signal
to inspect the channel manually before any human decision to re-send. A
nonzero `delivery_unknown_count`, or `reconcile.ok=false` on consecutive
runs, is the page-worthy condition; zeros are the normal steady state.

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

The cutover must be an ISO-8601 timestamp set only when a reviewed release is
activated:

```text
BUZZ_RESULT_PREVIEW_START_AT=<deployment timestamp>
```

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
- a same-origin `/?batch=<job_id>` Studio path;
- the bounded Korean headline and Telegram summary from the exact four-field
  review result.

It never returns the long-form body, X copy, raw input/output, prompt, input
hash, provider IDs, token counts, workspace ID, database credential, or a
mutation URL. Preview fields are trimmed, bounded, control-character checked,
and rejected if they contain an `@` mention. A team member must authenticate to
Studio before the deep link opens the full read-only result.

## Buzz v0.5.4 execution choice

For proactive status delivery, use a short-lived deterministic poller and the
official CLI, not a model heartbeat. The CLI's supported outbound boundary is:

```bash
printf '%s' "$CONTENT" |
  buzz messages send --channel "$CHANNEL_UUID" --content -
```

The worker invokes the program with an argv array and passes content through
stdin; it never constructs a shell command. It accepts a success only when the
v0.5.4 JSON response has `accepted=true`, a lowercase 64-hex `event_id`, and an
empty `mention_pubkeys` array. Buzz has no dry-run flag for `messages send`, so
local and staging tests stop before this command.

`buzz-acp` remains useful later for owner-only conversational commands. If it is
introduced, set an explicit non-bypass permission mode, use a dedicated Nostr
keypair and isolated runtime, and provide no provider, publish, outreach,
database-admin, or deploy credentials. ACP heartbeat is not the polling
mechanism because it would spend model tokens merely to discover whether work
exists.

## Durable delivery gate

The implementation now enforces this transition order:

0. reconcile expired receipt leases (best-effort; a fault here never blocks
   or triggers a delivery) and surface the counts in the run output;
1. poll only this endpoint and select at most one event;
2. format a deterministic UTF-8-bounded message containing the reviewed
   headline and summary, with no mentions, replies, files, or broadcast;
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

The first live relay write happened on 2026-08-04 under the decision recorded
in the [`ADR-011` addendum](ADR-011-origintrail-buzz-durable-delivery.md).
Publication, outreach, and OpenAI calls remain outside this adapter. On the
Netlify side, each Buzz function can adopt a scoped Postgres role credential —
`SUPABASE_BUZZ_DELIVERY_KEY` (role `coineasy_buzz_delivery`, the five receipt
RPCs and nothing else) for the control endpoint and `SUPABASE_BUZZ_SHADOW_KEY`
(role `coineasy_batch_reviewer`, read-only) for the shadow read. These custom
JWTs replace only the `Authorization` bearer; `apikey` remains the project API
credential. Rollback is deleting the scoped variable.

Official Buzz references:

- [desktop v0.5.4 release](https://github.com/block/buzz/releases/tag/desktop-v0.5.4)
- [`buzz-acp` configuration](https://github.com/block/buzz/blob/desktop-v0.5.4/crates/buzz-acp/README.md)
- [`buzz` CLI](https://github.com/block/buzz/blob/desktop-v0.5.4/crates/buzz-cli/README.md)
