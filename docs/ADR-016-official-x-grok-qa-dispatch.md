# ADR-016: Durable official-X Grok QA dispatch

**Status:** Accepted for implementation; production provider activation remains
gated on an exact release and a dedicated xAI credential
**Date:** 2026-08-13

## Context

The official-X producer already polls the four configured client accounts,
stores immutable source evidence, creates one review draft per client and KST
day, and stops at `needs_review`. The manual Grok connector can inspect a
bounded review package and send one advisory verdict to the private operations
room. A recurring bot that scans the generic `needs_review` list is not a safe
production queue: an old item remains visible after its receipt is sent, so a
scanner can repeatedly spend provider budget or race a revised version.

The monitoring, model, Telegram, Studio, and publication authorities must also
not accumulate in one process. A prompt injection in an official post or a
model error must not create approval or public-delivery authority.

## Decision

Use an event-triggered, durable QA outbox plus a separate Railway cron worker.

1. The database enqueues only the exact content version named by a successful
   `official_x_review_draft_completed` event and only when that version is
   `daily_news` with its canonical durable PNG. Article and Tutorial stay in
   manual Studio review until they have the same durable banner contract. The
   existing OriginTrail Batch
   materialization event is included because it creates the same immutable
   `needs_review` review package outside the synchronous producer. There is no
   historical backfill and no generic library scan.
2. One row is unique by `(workspace_id, content_version_id)`. Claims are
   filtered atomically by the worker's validated client allowlist; a Squid-only
   canary cannot lease or fail another client's work. A bounded lease, attempt
   count, and worker fence control claims. Before contacting xAI, the
   worker atomically records the canonical provider-input and banner SHA-256s
   against locked version/asset/source rows and consumes
   the version's single provider-call reservation. The provider result and its
   evidence are staged before any Telegram relay attempt. A lost response after
   the reservation or an uncertain provider outcome therefore closes as
   `provider_unknown`; it never authorizes a second paid model call.
3. The isolated worker holds only `XAI_API_KEY` and a dedicated
   `GROK_QA_DISPATCH_TOKEN`. It receives a sanitized review package and a
   hash-verified inline PNG from a server-only Netlify broker. It never receives
   a Studio password, Supabase key, X bearer token, Telegram token, Typefully
   token, Figma credential, or publication credential.
4. The xAI origin and endpoint are fixed to
   `https://api.x.ai/v1/responses`; the only accepted model is `grok-4.5`.
   The request sets `store:false` so it does not create retrievable stateful
   Responses history, requires X Search constrained to the selected client's
   exact official handle and a bounded date range, and uses a strict verdict
   schema. `store:false` is not a Zero Data Retention guarantee: the xAI team's
   retention setting must be reviewed before private drafts are enabled. It
   requires an `x_search_call`, the exact official
   status URL in provider citations, and the provider-reported
   `usage.cost_in_usd_ticks` before accepting a result. Source and banner
   evidence are treated as untrusted input, never as instructions.
5. Delivery reuses the existing exact-version Grok verdict receipt and the
   fixed private Telegram relay. Before claiming that receipt, the broker
   refetches the current PNG, requires the fenced banner SHA-256, and then
   relays those exact verified bytes. The worker cannot choose a destination. A
   provider or relay uncertainty becomes `delivery_unknown` and is not retried
   automatically.
6. Every verdict is advisory. No outbox or dispatcher routine can approve,
   publish, schedule, call Typefully, change a Studio status, or enable a
   feature flag. Existing human double-fact-check attestations remain mandatory
   before publication.
7. The official-X Railway schedule becomes `*/15 * * * *` UTC. Existing feed
   cursors, source deduplication, KST daily reservations, per-client quota, and
   workspace daily limit remain the cost and volume boundary.

## Security boundaries

- Netlify-to-Railway QA messages use a dedicated `GROK_QA_RELAY_TOKEN`; the
  broad `API_SECRET` is rejected for this path.
- Railway-to-Netlify dispatch uses a separate
  `GROK_QA_DISPATCH_TOKEN`. Reuse with Studio, API, Supabase, publication,
  Telegram, or connector credentials is a configuration error.
- The private relay accepts only the configured negative Telegram supergroup
  identifier and never accepts a destination in a request body.
- The database roles used by anonymous and authenticated callers have no
  outbox transition privileges. The broker performs only the named claim,
  stage, completion, failure, and reconciliation RPCs.
- Logs contain bounded internal codes and opaque IDs only. Provider bodies,
  source text, signed asset URLs, image bytes, secrets, and private room
  identifiers are not logged.

## Failure contract

```text
pending -> claimed -> staged -> sent
              |          |
              +-> pending+-> delivery_unknown
              +-> failed
              +-> obsolete
              +-> provider_unknown
```

An expired pre-provider lease may retry within the attempt cap. Once a model
call is reserved, it is never called again for that version; an interruption
before a validated result is staged becomes terminal `provider_unknown`. Once
a model result is staged, a later worker must reuse that exact result. Once
private delivery may have happened, no automatic retry is permitted. A version
that is no longer the current non-mock `needs_review` version becomes
`obsolete` without a model or relay call.

The first production activation additionally requires an exact immutable
content-version canary fence. Normal FIFO claiming is unavailable while canary
mode is active, so a disabled-worker backlog cannot drain accidentally.

## Rollout

1. Pause the manual Grok connector, pre-provision distinct relay/dispatch
   secrets, and keep `GROK_QA_DISPATCH_ENABLED=false`.
2. Apply the additive migration and run its FORCE-RLS, grants, lease,
   idempotency, immutability, and no-side-effect security suite.
3. Deploy the Railway API first and Netlify Functions second with the same
   dedicated relay token, verify the fixed private room, then restore the
   manual connector. Deploy the 24-hour official-X schedule only after this
   maintenance cutover is complete.
4. Create the isolated worker disabled, pin the exact production commit and
   environment, configure the xAI API key, and validate without I/O.
5. Enable canary mode with one exact Squid content-version ID and run one
   canary. Require one provider reservation, one staged
   result, one private verdict, zero approvals, and zero public sends.
6. Re-run the same version and inject pre/post-delivery failures. Require no
   second provider call and no second Telegram message. A revised version must
   create one new job.
7. After the canary passes, first disable the dispatcher. In one fail-closed
   configuration change, remove `GROK_QA_CANARY_CONTENT_VERSION_ID`, disable
   canary mode, and expand the allowlist to all four configured clients. Then
   validate again before re-enabling. Roll back by setting
   `GROK_QA_DISPATCH_ENABLED=false`; the official-X producer and human Studio
   review continue independently.

## Consequences

The system gains a continuously fed, auditable second review without exposing
the Studio login to Grok or giving an AI agent employee-level publishing
authority. It adds one cron service, one private queue, and one external-model
cost boundary. Human review remains intentional: a Grok `PASS` is evidence for
the reviewer, never an approval.
