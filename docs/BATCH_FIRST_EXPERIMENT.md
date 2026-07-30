# Batch-first agent experiment

CoinEasy's first company-agent experiment routes every eligible, non-urgent
model task through OpenAI Batch. It is review-only: the worker cannot approve,
publish, send outreach, deploy, or roll back anything.

## Pilot boundary

The default configuration is deliberately narrow:

| Setting | Pilot value |
|---|---|
| Client | Squid only |
| Duration | 14 days |
| Mode | `draft_only` |
| Daily hard cap | `$0.50` canary; `$6.00` only after promotion review |
| Models | `gpt-5.6-luna` for S, `gpt-5.6-terra` for M |
| Provider endpoint | `/v1/responses` through `/v1/batches` |
| Completion window | `24h` |
| Recovery-safe envelope | one request per provider Batch |
| Sync fallback | manual only |
| Automatic external effects | none |

The shared bridge already supports these Batch workloads for later expansion:

- Yellow, Squid, OriginTrail, and Babylon non-urgent content packs and reports
- Naver SEO articles, refreshes, metadata, and FAQ
- partnership screens and deep briefs
- analytics narratives
- visual copy and asset QA
- non-blocking release changelogs

Adding a client to `BATCH_ALLOWED_CLIENTS` is the only routing expansion. Keep
the four client evidence packets and Batch files separate.

This build connects the existing Official X daily runner's Squid claim path to
`BatchQueueBridge` after its immutable style pack is pinned and before Studio
generation. Completed `needs_review` results are exposed through a GET-only
Batch source in the signed-session Content Studio review inbox. The Batch
detail remains read-only: it cannot approve, reject, publish, or export a
result, and it does not turn the result into a durable Content Studio item.
The Netlify adapter accepts exactly `headline_ko`, `body_ko`, `x_copy_ko`, and
`telegram_copy_ko` with a provider-supported regex plus independent server-side
`1..maximum` bounds; missing, empty, whitespace-only, additional,
nested, or secret-like fields fail closed before rendering.

## Route contract

A work item is sent to Batch only when all of these are true:

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
- no path to an approval, publication, email, DM, or deployment.

## Local validation

No credentials or provider calls are needed:

```bash
PYTHONPATH=. .venv/bin/python -m scripts.run_batch_dispatcher --dry-run
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_batch_policy.py \
  tests/test_batch_openai_client.py \
  tests/test_batch_settings.py \
  tests/test_batch_repository.py \
  tests/test_batch_dispatcher.py \
  tests/test_batch_deployment.py \
  tests/test_shared_supabase_migrations.py
npm run test:functions
```

The dry run must report:

```json
{
  "mode": "dry_run",
  "allowed_clients": ["squid"],
  "daily_cap_usd": "0.50",
  "sync_fallback": "manual_only",
  "auto_publish": false,
  "provider_calls": false
}
```

## Live setup

1. Apply every Supabase migration in timestamp order to a disposable staging
   database and run the Batch ledger security smoke test.
2. Create a separate OpenAI project key for the Batch worker. Do not reuse a
   ChatGPT credential or give it to Netlify/browser code.
3. Configure the Railway cron from `railway.batch-dispatcher.json` with only:

```text
BATCH_EXPERIMENT_MODE=live
BATCH_ALLOWED_CLIENTS=squid
BATCH_DAILY_CAP_USD=0.50
BATCH_MAX_CLAIMS=1
BATCH_MAX_REQUESTS_PER_BATCH=1
BATCH_TIMEZONE=Asia/Seoul
BATCH_EXPERIMENT_START_AT=<exact KST midnight>
BATCH_EXPERIMENT_END_AT=<exact KST midnight, at most 14 days later>
OPENAI_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
CONTENT_STUDIO_WORKSPACE_ID
```

4. Verify the connected Squid Official X producer and the signed-session,
   GET-only Batch review source in staging. Confirm the Batch detail contains no
   approval, rejection, publication, or export control. The producer
   deliberately does not load `OPENAI_API_KEY`; only the isolated dispatcher
   needs that credential.
5. Pause the producer and verify that no pre-canary Squid official-X public job
   remains `queued`, `running`, or `retrying`. A job that began before
   execution-plane binding cannot safely cross the cutover.
6. Queue one immutable Squid test packet through that adapter.
7. Run the cron manually once. Verify a provider Batch is registered and no
   Content Studio publication, approval, or export row appears.
8. Leave the schedule at every ten minutes for provider polling. The process
   exits after each pass and does not hold a 24-hour model session.

Fresh claims submit directly. Only a stale lease from an ambiguous
upload/create/register attempt searches provider history, bounded by that
attempt's original start time. This keeps normal dispatch cost constant as the
OpenAI project accumulates historical Batches.

The live worker rejects an experiment window longer than 14 KST calendar days.
Before the start it makes no database or provider call. At and after the end it
polls and reconciles already-submitted Batches but never claims or submits new
work. Producer deadlines are clamped to the experiment end, so the last 26
hours form a submission drain window: official-source jobs remain review-only
but are not queued to Batch or silently sent to synchronous generation. Start
the canary at `$0.50` per day with one claim, bounding an aligned 14-day run to
`$7`. Only after reviewing that canary should the operator promote the existing
system ceiling to `$6` per day and 100 claims; that promoted 14-day reservation
ceiling is `$84`.

A previously uncertain Squid handoff is still recovered replay-only after the
experiment end; this links the already-durable same-UUID ledger row but cannot
submit a new provider job. A new first-attempt Squid job outside the live window
continues through the pre-existing synchronous workflow.

The expired dispatcher also releases only definitively unsubmitted
`queued`/`retry_wait` jobs. An expired `claimed` row is not released because an
upload/create response may have been commit-unknown; it remains conservatively
held and is reported as `ambiguous_claimed_manual_recovery` for operator
reconciliation. Keep the cron running in expired mode until active Batches and
that alert count are both zero.

Keep `BATCH_EXPERIMENT_MODE=dry_run` until the migration and security smoke pass.
No deployment or billable provider submission is part of the local build.

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

Expand to a second client, then Naver SEO, only when all are true:

- at least 85% of eligible jobs used Batch;
- at least 60% of non-incident model tokens used Batch;
- measured model cost is at least 35% below an all-sync baseline;
- p95 single-stage completion is at most 26 hours;
- structured-output success is at least 95%;
- QA pass rate is within 5 percentage points of the sync baseline;
- budget overruns, duplicate accepted stages, and unapproved effects are zero.

Official references:

- [OpenAI Batch API](https://developers.openai.com/api/reference/resources/batches)
- [OpenAI Files API](https://developers.openai.com/api/reference/resources/files)
- [GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model)
