# Batch-first agent experiment

CoinEasy's first company-agent experiment routes eligible, non-urgent,
standalone, text-only OriginTrail Official X daily-news copy through OpenAI Batch. It is
review-only: the worker cannot approve, publish, send outreach, deploy, or roll
back anything.

## Pilot boundary

The default configuration is deliberately narrow:

| Setting | Pilot value |
|---|---|
| Client | OriginTrail only (`origintrail_client_agent`) |
| Duration | 14 days |
| Mode | review-only drafts |
| Content | `daily_news` only; quoted posts and articles excluded |
| Daily hard cap | `$0.50` canary; `$6.00` only after promotion review |
| Models | `gpt-5.6-luna` for S, `gpt-5.6-terra` for M |
| Provider endpoint | `/v1/responses` through `/v1/batches` |
| Completion window | `24h` |
| Prompt cache | disabled for the canary; no cache-write charge accepted |
| Recovery-safe envelope | one request per provider Batch |
| Post-bind sync fallback | none; manual recovery only |
| Automatic external effects | none |

## Activation status: HOLD

The checked-in build and local validation do not authorize a live deployment
or a billable provider call. Keep both services at
`BATCH_EXPERIMENT_MODE=dry_run` until the staging approval gate below is
complete.

- Creating a disposable Supabase branch, applying a remote migration, creating
  a Railway service, or making an OpenAI API call requires explicit operator
  approval because each can create cost or mutate external state.
- Production migration, production cron activation, and production Batch spend
  remain **HOLD** even after a staging smoke test. Production requires a
  separate promotion approval.
- Do not push this migration directly to a production database with unresolved
  local/remote migration-history drift. If the Batch ledger migration has
  already been applied in an environment, use a new forward migration instead
  of editing the applied migration in place.

The shared data model and bridge recognize these workloads for later expansion:

- Yellow, Squid, OriginTrail, and Babylon non-urgent content packs and reports
- Naver SEO articles, refreshes, metadata, and FAQ
- partnership screens and deep briefs
- analytics narratives
- visual copy and asset QA
- non-blocking release changelogs

Recognition is not authorization. Changing `BATCH_ALLOWED_CLIENTS` alone does
not connect another client and must not be treated as a promotion. Producer
routing, database handoff and result guards, the Netlify review adapter, the
console, and their tests all enforce the single canary identity. Follow the
multi-layer promotion checklist below, and keep every client's evidence packet,
ledger identity, and provider Batch file separate.

This build connects the existing Official X daily runner's OriginTrail claim
path to `BatchQueueBridge` after its immutable style pack is pinned and before
Studio generation. Only a standalone `daily_news` claim whose pinned source
rows have empty media arrays is eligible for this first Batch canary. A fresh
photo, video, or animated-GIF preview is pinned into `source_image_url`, so the
claim is bound to the existing `studio_sync` path before Batch admission.
Articles also remain on Studio. Never blank or discard a real source media URL
to force a job into Batch.

Fresh quoted OriginTrail posts are excluded before durable intake because the
quoted post's full evidence is not pinned. The cursor advances only to the
newest source item actually committed; a quote-only poll deliberately leaves
the cursor unchanged and may be fetched again. Other clients retain their
existing quote behavior. OriginTrail intake calls a dedicated wrapper that
requires `is_quote`, `is_retweet`, and `is_reply` to be exact JSON booleans set
to `false`, commits through the existing intake RPC, and atomically records
each returned source ID in the private standalone-source allowlist. Malformed
reference evidence or unresolved, duplicated, unsupported, or untrusted media
evidence fails the poll before persistence or cursor advancement. Historical
rows have no membership and therefore receive
`origintrail_batch_eligible: false` when claimed; they remain processable on
`studio_sync` but cannot bind or hand off to Batch. SQL
independently revalidates allowlist membership, client, identity, `daily_news`,
source ownership, and empty media at bind and handoff time.

This pre-bind source guard is different from fallback. After a job is durably
bound to `openai_batch`, a failure, retry, mode change, or experiment-window
change cannot move it to synchronous generation.

Completed `needs_review` results are exposed through a GET-only Batch source in
the signed-session Content Studio review inbox. The Batch detail remains
read-only: it cannot approve, reject, publish, or export a result, and it does
not turn the result into a durable Content Studio item.
The Netlify adapter accepts exactly `headline_ko`, `body_ko`, `x_copy_ko`, and
`telegram_copy_ko` with a provider-supported regex plus independent server-side
`1..maximum` bounds; missing, empty, whitespace-only, additional,
nested, or secret-like fields fail closed before rendering.

A second GET-only projection at `/api/buzz-shadow/origintrail/batch` exposes
only status metadata for the future Buzz bridge. It uses a dedicated
`BUZZ_SHADOW_ACCESS_TOKEN`, while the Supabase service-role key remains inside
Netlify. The projection deliberately omits the generated headline and body,
channel copy, prompts, token counts, provider identifiers, and all mutation
capabilities. Its deterministic event ID is stable across repeated polls, and
its relative `/?batch=<job_id>` path opens the signed-session, read-only Batch
detail. This endpoint does not call the Buzz relay; outbound delivery and its
durable receipt remain **HOLD** until a separate staging gate.

## Route contract

A work item is sent to Batch only when all of these are true:

- it belongs to the OriginTrail Official X producer and
  `origintrail_client_agent`;
- it is `daily_news`, not an article or tutorial;
- it is a standalone post, not a quote;
- every pinned source row is text-only (`media = []` and
  `source_image_url` is empty);
- its evidence packet is complete, immutable, and SHA-256 bound;
- its retry is idempotent;
- it needs neither a live tool nor an external side effect;
- it is not interactive, an incident, or a release blocker;
- its risk is T0–T2 and its model tier is S or M;
- at least 26 hours remain for each Batch stage;
- its 120% cost estimate fits its per-job cap;
- a conservative maximum-cost bound over the serialized input bytes plus
  `max_output_tokens` also fits that cap;
- the database has atomically reserved the full per-job hard cap below the
  daily hard cap (a deliberately stricter first-pilot rule).

Two Batch stages therefore require 52 hours. A task that misses any gate is not
silently sent to a full-price synchronous model. It becomes `manual_sync`,
`waiting_budget`, `out_of_scope`, or `rejected`.

The daily-news and text-only checks happen before execution-plane binding and
are revalidated by SQL at handoff, so visual or article work safely continues
through the existing Studio path. It is not a fallback from an attempted or
failed Batch submission.

Every Responses request also sets
`prompt_cache_options: {"mode":"explicit"}` without a cache breakpoint. This
disables GPT-5.6's implicit prompt-cache write for the first canary. A result
whose usage omits `cache_write_tokens`, reports a non-integer value, or reports
anything other than zero is rejected as unverifiable and settles against the
job's full reserved cap. This keeps the database ceiling conservative instead
of silently ignoring the provider's cache-write charge.

## Durable flow

```text
immutable evidence packet
  -> lease-fenced execution-plane binding
  -> BatchQueueBridge
  -> idempotent KST daily-budget bootstrap
  -> agent_runtime budget + job ledger
  -> short-lived dispatcher claim
  -> client/model-isolated JSONL
  -> OpenAI input file + Batch
  -> 10-minute status reconciliation
  -> structured output validation
  -> needs_review result
  -> signed-session Batch review inbox (read-only)
  -> metadata-only Buzz shadow projection (GET-only; no relay write)
  -> human review handoff
```

The additive migration
`supabase/migrations/20260731120000_agent_batch_ledger.sql` creates a private
`agent_runtime` schema instead of extending the Content Studio `public.jobs`
table. Direct table access is revoked even from `service_role`; narrow
security-definer RPCs own all state transitions.

The ledger provides:

- caller-generated deterministic job IDs and idempotency keys;
- a first-attempt `studio_sync` or `openai_batch` execution-plane binding that
  every retry must reuse across experiment start/end and mode changes;
- unique Batch `custom_id` values (`job_id:stage:attempt`);
- `FOR UPDATE SKIP LOCKED` leases and bounded attempts;
- same-attempt stale-lease recovery and paginated provider lookup before create;
- one-request Batch envelopes during the first pilot, preventing a changed claim
  group from creating a duplicate billable Batch after an ambiguous response;
- atomic budget reservation before a job can be queued;
- provider batch/file IDs stored as opaque references;
- out-of-order and partial result reconciliation by `custom_id`;
- actual token/cost settlement and release of unused reservation;
- exact cost settlement for billable refusals and invalid outputs; if provider
  usage is missing, the full reservation is conservatively recorded as spent;
- finalization of missing, expired, failed, or cancelled results;
- terminal malformed, duplicate, or unknown result files are quarantined and
  conservatively finalized instead of retaining reservations forever;
- the first successful admission durably binds its KST budget day in the
  ledger. Backlog uses the actual queue-attempt day, while an uncertain replay
  after KST midnight reads the original binding without a second reservation;
- a retry that has crossed the deadline safety window uses a server-enforced
  replay-only lookup. A missing prior row cannot become a new admission, and
  no new daily budget is configured for that lookup;
- an ordinary or final-attempt retry can consume only an exact safe OriginTrail
  receipt stored under the public job's same UUID, then complete the idempotent
  public handoff. Recovery never rebuilds the current prompt, schema, deadline,
  or style-reference pack and never admits or reserves budget for new Batch or
  provider work. A final-attempt recovery claim waits for a one-minute cooldown,
  does not increase attempts, and is marked `batch_handoff_recovery_only`;
- no path to an approval, publication, email, DM, or deployment.

## Local validation

No credentials or provider calls are needed:

```bash
PYTHONPATH=. .venv/bin/python -m scripts.run_batch_dispatcher --dry-run
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_automation_daily_runner.py \
  tests/test_automation_repository.py \
  tests/test_batch_policy.py \
  tests/test_batch_openai_client.py \
  tests/test_batch_settings.py \
  tests/test_batch_repository.py \
  tests/test_batch_dispatcher.py \
  tests/test_batch_deployment.py \
  tests/test_batch_ledger_migration.py \
  tests/test_shared_supabase_migrations.py
npm run test:functions
```

The dry run must report:

```json
{
  "mode": "dry_run",
  "allowed_clients": ["origintrail"],
  "daily_cap_usd": "0.50",
  "sync_fallback": "manual_only",
  "auto_publish": false,
  "provider_calls": false
}
```

## Staging setup and approval gate

The Official X producer and the isolated Batch dispatcher are separate
services. They must use the same client, experiment window, budget, workspace,
and ledger credentials. Configure this shared block on **both** services while
they remain in dry-run:

```text
BATCH_EXPERIMENT_MODE=dry_run
BATCH_ALLOWED_CLIENTS=origintrail
BATCH_DAILY_CAP_USD=0.50
BATCH_MAX_CLAIMS=1
BATCH_MAX_REQUESTS_PER_BATCH=1
BATCH_TIMEZONE=Asia/Seoul
BATCH_EXPERIMENT_START_AT=<exact KST midnight>
BATCH_EXPERIMENT_END_AT=<exact KST midnight, at most 14 days later>
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
CONTENT_STUDIO_WORKSPACE_ID
```

Here `dry_run` disables the Batch plane; it does not turn the whole Official X
runner into a no-write dry run. Unless the producer process is invoked with its
own `--dry-run` flag or its schedule is paused, its existing Studio sync path
continues to operate.

The producer keeps its existing Official X and Studio credentials, including
`X_BEARER_TOKEN`, `STUDIO_BASE_URL`, and `STUDIO_AUTOMATION_TOKEN`. Do not copy
those credentials to the dispatcher.

Only the dispatcher configured from `railway.batch-dispatcher.json` receives:

```text
OPENAI_API_KEY
```

Never put `OPENAI_API_KEY` on the Official X producer, in Netlify, in browser
code, or in a client configuration file. Use a dedicated OpenAI project key
with a provider-side spend limit and alert in addition to the database cap.

Do not set only the dispatcher to `live`: a dry-run producer will continue to
choose Studio sync and create no Batch work. Do not set only the producer to
`live`: it can bind and queue work that no active dispatcher submits. A cap or
window mismatch can also fail the immutable database budget contract. Promote
the two services together with exactly the same shared values.

The staging gate is:

1. Obtain explicit approval for the disposable Supabase branch and any
   staging Railway/OpenAI cost.
2. Confirm the target is disposable staging, inspect its migration history,
   apply every migration in order, and run the Batch ledger security smoke.
   Do not apply the Batch migration directly to production.
3. Verify that the staging workspace has an active OriginTrail client and
   official `@origin_trail` source feed. As the migration owner, inventory the
   automatically quarantined pre-cutover pending rows; the result is allowed
   to be nonzero because those rows route Studio only:

   ```sql
   select count(*) as pre_cutover_studio_only_pending
   from private.official_x_source_state as state
   left join private.origintrail_standalone_sources as standalone
     on standalone.source_item_id = state.source_item_id
    and standalone.workspace_id = state.workspace_id
   where state.client_id = 'origintrail'
     and state.queued_job_id is null
     and standalone.source_item_id is null;
   ```
4. Deploy the signed-session, GET-only review adapter, metadata-only Buzz
   shadow endpoint, and console. Set a new `BUZZ_SHADOW_ACCESS_TOKEN` only in
   Netlify and the isolated future bridge; never reuse a Studio, Supabase,
   provider, publish, or deploy credential. Confirm the detail view accepts
   only `origintrail` plus `origintrail_client_agent` and exposes no approve,
   reject, publish, or export control. Confirm the Buzz endpoint itself makes
   no relay call.
5. Deploy the dispatcher as an isolated short-lived service, but keep both
   producer and dispatcher at `dry_run`. Confirm dry-run reports
   `allowed_clients: ["origintrail"]` and `provider_calls: false`.
6. Pause the producer and verify that no pre-canary OriginTrail Official X job
   remains `queued`, `running`, or `retrying`. A job that began before a safe
   cutover must not cross execution-plane binding accidentally.
7. Obtain a second explicit approval for one billable, review-only staging
   Batch. Set both services to `live` with the same exact KST window and shared
   values, then resume the producer.
8. From a post-deployment intake, queue one standalone, text-only OriginTrail
   `daily_news` packet and verify its source ID has a private allowlist row
   with `is_quote is false`. A quote, unknown/pre-cutover row, article, photo,
   video, or animated GIF must stay out of Batch; visual/article/pre-cutover
   work must use `studio_sync` and create no Batch handoff.
9. Run the dispatcher once manually. Verify one provider Batch is registered,
   the result appears only in the read-only review inbox, duplicate accepted
   jobs remain zero, and no Content Studio publication, approval, export, or
   visual row appears.
10. Keep the dispatcher on its ten-minute schedule until registered work and
    any ambiguity alerts drain. The process exits after each pass and does not
    hold a 24-hour model session.

A successful staging job is evidence for a later production decision, not
permission to deploy production. Production remains **HOLD** until an operator
reviews the staging receipt, spend, duplicate count, structured-output result,
and absence of external side effects and explicitly approves promotion.

Fresh claims submit directly. Only a stale lease from an ambiguous
upload/create/register attempt searches provider history, bounded by that
attempt's original start time. This keeps normal dispatch cost constant as the
OpenAI project accumulates historical Batches.

The live dispatcher rejects an experiment window longer than 14 KST calendar
days. Before the start it makes no database or provider call. At and after the
end it polls and reconciles already-submitted Batches but never claims or
submits new work. Producer deadlines are clamped to the experiment end, so the
last 26 hours form a submission drain window: official-source jobs remain
review-only but are not queued to Batch or silently sent to synchronous
generation. Start the canary at `$0.50` per day with one claim, bounding an
aligned 14-day run to `$7`. Only after reviewing that canary should the operator
promote the existing system ceiling to `$6` per day and 100 claims; that
promoted 14-day reservation ceiling is `$84`.

A previously uncertain OriginTrail handoff is still recovered after the
experiment end by consuming the already-durable same-UUID ledger receipt and
completing the idempotent public handoff. Ordinary and max-attempt recovery do
not rebuild current prompt, schema, deadline, or style-pack inputs, reserve
budget, or submit provider work. A new first-attempt OriginTrail job outside
the live window continues through the pre-existing synchronous workflow.

The expired dispatcher also releases only definitively unsubmitted
`queued`/`retry_wait` jobs. An expired `claimed` row is not released because an
upload/create response may have been commit-unknown; it remains conservatively
held and is reported as `ambiguous_claimed_manual_recovery` for operator
reconciliation. Keep the cron running in expired mode until active Batches and
that alert count are both zero.

Keep `BATCH_EXPERIMENT_MODE=dry_run` until the migration and security smoke pass
and the applicable staging approval is recorded. No deployment or billable
provider submission is part of the local build.

For an emergency stop, pause the Official X producer schedule first. Keep the
dispatcher running long enough to poll registered Batches, release only safe
expired pre-submit work, and report any ambiguous claimed row. Changing the
mode cannot move an already-bound public job to the other execution plane, but
turning off the Batch service before its handoffs drain can leave review work
pending manual recovery.

The pilot repository currently authenticates its narrow ledger RPC calls with
the existing Supabase service-role credential. The worker image contains no
publish, outreach, or deploy adapter, but that credential still has a broader
project blast radius than the Batch ledger. Run it as an isolated Railway
service and replace it with a dedicated database broker/token before promoting
the experiment beyond review-only staging.

## Promotion gate after 14 days

Expand to a second client, then Naver SEO, only when all outcome thresholds are
true:

- at least 85% of eligible jobs used Batch;
- at least 60% of non-incident model tokens used Batch;
- measured model cost is at least 35% below an all-sync baseline;
- p95 single-stage completion is at most 26 hours;
- structured-output success is at least 95%;
- QA pass rate is within 5 percentage points of the sync baseline;
- budget overruns, duplicate accepted stages, and unapproved effects are zero.

Every additional client also requires a multi-layer implementation and review:

1. Pin that client's official source allowlist, immutable evidence builder,
   style references, terminology, output schema, and dedicated agent identity.
2. Add an explicit producer route. Do not infer eligibility merely because the
   generic Batch model recognizes the client or workflow name.
3. Extend `BATCH_ALLOWED_CLIENTS` on producer and dispatcher together, while
   preserving client-separated budget/job identities and provider files.
4. Extend the database queue, handoff, strict completion, review index/list,
   and detail guards for the exact client, agent, workflow, and stage.
5. Extend the Netlify response validator and console list/detail guards. Keep
   the review surface GET-only and free of approval, publish, export, outreach,
   deploy, or visual side effects.
6. Add Python producer/isolation tests, SQL security smoke coverage, Netlify and
   console tests, and a negative cross-client leakage test. Existing clients
   must remain rejected from another client's review path.
7. Run one text/evidence-complete staging packet for the new client under its
   own approved cap and inspect quality, cost, identity, and audit receipts.
8. Require explicit human approval before changing production allowlists or
   enabling any production schedule. A passing test or staging run never
   promotes a client automatically.

Official references:

- [OpenAI Batch API](https://developers.openai.com/api/reference/resources/batches)
- [OpenAI Files API](https://developers.openai.com/api/reference/resources/files)
- [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model)
