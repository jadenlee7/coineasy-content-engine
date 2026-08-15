# Official-X Grok QA dispatcher runbook

The dispatcher is an optional second reviewer for immutable Content Studio
versions. It does not replace the human reviewer and has no approval,
publication, Typefully, Figma, X-monitor, Studio-login, or destination-selection
authority. The official-X worker continues to create review drafts even while
this dispatcher is disabled.

## Data path

```text
official X worker -> needs_review version + event -> private durable outbox
  -> isolated Railway Grok QA worker -> xAI X Search + banner review
  -> staged provider evidence -> fixed private Telegram QA room
  -> human double-fact-check and approval
```

Only the exact current non-mock `needs_review` Daily News version with its
canonical durable PNG from an active official X feed is claimable. Article and
Tutorial remain manual Studio review in this release. The SQL claim applies the configured client allowlist
inside the transaction, so the initial `squid` canary cannot lease another
client's job.

## Components

- Migration: `20260813143000_grok_qa_dispatch_outbox.sql`
- Netlify broker: `POST /api/grok-qa/dispatch`
- Worker image: `Dockerfile.grok-qa`
- Railway config: `railway.grok-qa.json`
- Worker command: `python -m scripts.run_grok_qa_dispatch --run-once`
- Schedule: every five minutes, at most one QA item per run

## Secret placement

Generate separate high-entropy values and never paste them into a ticket, chat,
log, or command history.

| Value | Netlify | Main Railway API | Grok QA worker |
|---|---:|---:|---:|
| `GROK_QA_DISPATCH_TOKEN` | yes | no | yes |
| `GROK_QA_RELAY_TOKEN` | yes | yes | no |
| `XAI_API_KEY` | no | no | yes |

The worker must not contain Studio, Supabase, X API, Telegram, Typefully,
publication, or Figma credentials. Startup rejects secret reuse. The API relay
accepts only the dedicated relay header; broad `API_SECRET` is not valid for
this path.

## Default-off deployment

The relay-header change is an intentional maintenance cutover, not an ordinary
staggered deploy. Old Netlify code sends the legacy header and new Railway code
accepts only the dedicated header, so no manual connector request may be in
flight between the two deploys.

1. Generate and pre-provision the same dedicated `GROK_QA_RELAY_TOKEN` on
   Netlify and the main Railway API, plus the distinct
   `GROK_QA_DISPATCH_TOKEN` on Netlify and the future QA worker. Keep
   `GROK_QA_DISPATCH_ENABLED=false`.
2. Pause the manual Grok connector and confirm that it has no in-flight relay.
   Do not remove Studio authentication or expose the private review package.
3. Apply the additive migration and run its security suite. The exact-version
   canary fence prevents any disabled-period backlog from being drained during
   the first activation.
4. Deploy the main Railway API first and Netlify Functions second. Before
   restoring the connector, send an authenticated, deliberately invalid relay
   request and require a validation error rather than `401`/`403`; this proves
   the new header without sending Telegram. Confirm the configured destination
   is the exact private supergroup, then restore the connector.
5. Deploy the 24-hour official-X schedule only after the relay cutover is
   complete. Monitor X request count, read cost, and `429` responses during the
   first day; the existing KST draft reservations continue to cap writes.
6. Create the isolated Railway cron service using `railway.grok-qa.json`. Keep
   it disabled and do not attach a public domain or shared-variable reference.
   The process refuses to start if it inherits any Studio, Supabase, Telegram,
   X, Typefully, publisher, deploy, or other model-provider credential. Give it
   only its dedicated dispatch token and xAI key, plus the non-secret fences
   below, using the exact production release SHA:

   ```text
   GROK_QA_DISPATCH_ENABLED=false
   GROK_QA_CANARY_MODE=true
   GROK_QA_CANARY_CONTENT_VERSION_ID=<preselected immutable version UUID>
   GROK_QA_DISPATCH_URL=https://coineasy-newscard.netlify.app/api/grok-qa/dispatch
   GROK_QA_ALLOWED_CLIENTS=squid
   GROK_QA_MODEL=grok-4.5
   GROK_QA_EXPECTED_ENVIRONMENT=production
   GROK_QA_RELEASE_SHA=<exact 40-character deployed commit>
   GROK_QA_LEASE_SECONDS=300
   GROK_QA_MAX_TURNS=3
   GROK_QA_X_SEARCH_WINDOW_DAYS=1
   GROK_QA_MAX_OUTPUT_TOKENS=1600
   GROK_QA_MAX_COST_IN_USD_TICKS=500000000
   ```

   The cost value is returned after xAI has processed the request. Treat this
   setting as a post-response rejection and alert threshold, not a guaranteed
   pre-spend budget. The exact-version provider fence and one-item canary are
   the pre-call volume controls; review the first recorded cost before
   expanding the client allowlist.

7. Review the xAI team's data-retention setting. The worker sends private draft
   copy and banner bytes with `store:false`, which disables retrievable
   stateful Responses history but does not by itself enable Zero Data
   Retention. If the team's policy requires ZDR, enable it in xAI and verify the
   `x-zero-data-retention: true` response header with a non-content API probe
   before the canary.
8. With outbound networking blocked, run `--validate-only`. It must report
   verified environment/release fences and zero provider, database, relay, and
   publication calls.

## One-item Squid canary

Select one already-created immutable Squid `needs_review` version. Set its UUID
as `GROK_QA_CANARY_CONTENT_VERSION_ID`, set `GROK_QA_CANARY_MODE=true`, and only
then set `GROK_QA_DISPATCH_ENABLED=true`. The broker must reject or return no
work for every other version. Do this only after the exact deployed release,
dedicated xAI key, fixed private Telegram supergroup, and dispatch/relay tokens
are verified. Require:

- one outbox row and one provider-attempt reservation;
- one xAI response using `grok-4.5`, `store:false`, required X Search, and the
  exact `@SquidRouter` source post;
- one hash-verified PNG and the `squid/brand-review@1` contract in the review
  input;
- stored provider response ID, input SHA, cost ticks, X Search citation/call
  evidence, verdict, and database-authoritative verdict SHA before relay;
- one advisory verdict in the fixed private room;
- zero approvals, public sends, Typefully drafts, Figma mutations, or status
  changes.

Re-running the same version must not create another provider call or Telegram
message. A revised immutable version may create one new job only after the
canary target is deliberately changed. Disable the dispatcher immediately
after this first run; expansion requires a separate decision.

## Failure policy

- Before the provider reservation, an expired lease may retry within the
  bounded attempt count.
- A lost reservation response means zero provider calls by that worker.
- After reservation, a timeout, crash, or missing result is terminal
  `provider_unknown`; it is never automatically retried.
- A fully staged result may be replayed without another provider call.
- Once private Telegram delivery may have occurred, the job becomes
  `delivery_unknown` and is not retried automatically.

### X Search diagnostics

The worker may report `xai_qa_x_search_failed` while the durable outbox keeps
the terminal `grok_qa_provider_unknown` fence. This safe diagnostic means xAI
returned one or more failed internal X tool attempts, but no successful
server-side X Search usage or exact-post citation. It is not a parser success
and must never be converted to PASS or retried for the same content version.

Keep dispatch disabled and reproduce with public synthetic content only. Check
the ZDR response header, dedicated-key status, remaining API credits, and a
plain Web Search control request. If Web Search succeeds but filtered and
unfiltered X Search both produce failed `x_keyword_search` or
`x_thread_fetch` attempts, treat it as an xAI X Search service or entitlement
issue and escalate to xAI support. Include only the public-probe timestamp,
safe error code, and public-only response ID if the probe captured one. Do not
send private Studio copy, banners, keys, or Telegram data in that report. Do
not substitute Web Search as factual proof for this workflow. A new immutable
canary version may be selected only after the public X Search probe returns a
successful tool-use count and exact X citation.

The Responses API may expose a successful X execution either as the documented
`x_search_call` output item or as a completed provider-owned
`custom_tool_call` whose name is one of `x_user_search`, `x_keyword_search`,
`x_semantic_search`, or `x_thread_fetch`. The latter is accepted only when the
response also reports at least the same number of successful server-side tool
uses and every citation resolves to the exact stored official Post ID. Unknown
tool names, failed calls, missing usage proof, or any citation outside that
single-post boundary remain terminal failures.

## Rollback and expansion

Set `GROK_QA_DISPATCH_ENABLED=false` first. Leave the outbox and evidence rows
intact for audit; official-X drafting and manual Studio review continue. Any
wrong client, wrong source, wrong banner hash, missing X Search proof, duplicate
provider charge, duplicate Telegram message, private URL/secret exposure,
approval, or public send is an immediate rollback condition.

Expand `GROK_QA_ALLOWED_CLIENTS` beyond `squid` only after the canary and failure
injection checks pass. Keep dispatch disabled while removing
`GROK_QA_CANARY_CONTENT_VERSION_ID` and setting `GROK_QA_CANARY_MODE=false` in
the same configuration change; that pair is rejected if only one side changes.
Validate again before enabling normal FIFO. Each review package contains its
own server-owned brand contract so Yellow, OriginTrail, Babylon, and Squid
rules remain separated.
