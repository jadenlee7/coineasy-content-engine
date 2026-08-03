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
| Duration | exactly 48 hours for the first staging canary |
| Mode | review-only drafts |
| Content | `daily_news` only; quoted posts and articles excluded |
| Durable canary grant | one provider Batch and an internal modeled `$0.05` limit per approved config subject; not a provider-invoice cap |
| Model | S tier only: `gpt-5.6-luna` |
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

Live startup is additionally default-off behind `BATCH_CANARY_ENABLED=true`.
The first profile accepts only staging, OriginTrail, one claim, one request,
an exact 48-hour KST window, and a `$0.05` daily ledger cap. A short-lived
config receipt binds those values, the Supabase target, workspace, and release
SHA. A second short-lived dispatch receipt binds the exact queued job UUID,
immutable input SHA-256, and canonical full-request SHA-256 before provider
submission. These receipts prevent an accidental config expansion; they are
operational approval records, not a cryptographic substitute for the operator's
explicit approval in this task.
They are unsigned JSON environment values: anyone who can change the service
environment can forge or replace them. The release SHA check compares approved
configuration with runtime-provided build metadata; it is not remote
attestation. Environment write access must therefore remain an operator-only
boundary.

The forward migration `20260802130000_origintrail_batch_canary_grant.sql`
persists the exact config/dispatch approvals, job, input, full request, expiry,
and internal `$0.05` limit. Its exact claim RPC irreversibly consumes the one
provider-Batch grant in the same transaction that claims attempt one. Terminal
budget settlement cannot restore it, a replacement dispatch receipt cannot
change the same config subject, and stale attempt-one recovery is lookup-only.
This is a durable internal authorization boundary once the migration is applied;
it still does not authenticate the unsigned receipts or cap the provider's
invoice.

The later forward migration
`20260802140000_batch_cost_overage_incidents.sql` closes the provider-accounting
failure case. If observed usage is above the internal reservation, settlement
does not discard the usage-derived amount or poll forever. It atomically moves
the reserved cap into budget spend, stores the full calculated cost, token usage,
and exact outcome fingerprint in an immutable private incident, quarantines the
job as `batch_cost_cap_breached`, and finalizes it as failed. Exact replay is
idempotent; a changed replay is rejected. Any unresolved incident blocks both
generic and OriginTrail exact claims for that workspace. This first profile has
no automatic incident-clear path: keep live submission stopped until an
operator reconciles the provider invoice and a separately reviewed forward
change authorizes recovery.

The same migration also bridges the database-to-provider transaction gap with
a private OriginTrail provider-create intent. After the input file upload and
immediately before `POST /v1/batches`, the dispatcher must arm the exact intent
under the workspace safety lock. Its fingerprint binds the input file,
`/v1/responses`, the `24h` completion window, all provider metadata, and the
seven-day output expiry, in addition to the exact grant, job, request, worker,
attempt-one lease, and deterministic intent token. Only the first non-replayed
arm receipt permits a create. A lost or replayed response is lookup-only.

An armed intent blocks fresh claims and overage settlement for the workspace.
Its two-minute create window expiring does **not** release that fence: a timed
out HTTPS request may still have reached the provider. Exact metadata lookup
and atomic canary registration are the only automatic close path. There is no
unchecked timeout or service-role resolution RPC in this pilot. If the worker
crashes after arming but before a provider Batch can be found, the workspace
remains fail-closed on manual HOLD until operator investigation and a separately
reviewed forward resolution. Repeated `batch_provider_create_fenced` errors are
therefore an operational alert, not a reason to clear or bypass the intent.

- Creating a disposable Supabase branch, applying a remote migration, creating
  a Railway service, or making an OpenAI API call requires explicit operator
  approval because each can create cost or mutate external state.
- Production schema-only promotion was separately approved and completed on
  2026-08-02. Production cron activation, application deployment, and Batch
  spend remain **HOLD** and require their own promotion decisions.
- Do not replay or edit an applied migration when local and remote timestamps
  differ. Production records `origintrail_batch_canary_grant` as
  `20260802190633` and `batch_cost_overage_incidents` as `20260802190754`;
  preserve that name/version mapping and use a new forward migration for every
  later schema change.

Production schema receipt: the runner was `dry_run`, had no OpenAI key, and had
no active Batch jobs or provider runs before promotion. Both migrations ran in
their own transactions with a five-second lock timeout and 110-second statement
timeout. Postflight confirmed three forced-RLS tables, four enabled safety
triggers, service-role-only public RPCs, no direct private-table grants, no
invalid indexes, and zero grant/intent/incident rows, jobs, runs, reservations,
spend, or actual cost. No application, worker, provider, relay, publication, or
approval action was part of this schema rollout. One read-only intermediate
catalog query used the wrong function signature and logged SQLSTATE 42883; the
corrected signature check passed, with no migration, lock, or application error.
A 10+ minute post-deploy watch found no later ERROR/FATAL/PANIC, lock timeout,
deadlock, new runtime row, Batch cost, or provider activity; the Railway
deployment and no-OpenAI-key state remained unchanged.

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
- its risk is T0–T2 and, for this first canary, its model tier is exactly S
  (`gpt-5.6-luna`);
- at least 26 hours remain for each Batch stage;
- its 120% cost estimate fits its per-job cap;
- a conservative maximum-cost bound over the serialized input bytes plus
  `max_output_tokens` also fits that cap;
- the database has atomically reserved the full modeled per-job cap below the
  internal daily ledger cap (a deliberately stricter first-pilot rule).

The first canary has one `generate` stage and therefore requires 26 hours. A
future two-stage workflow would require 52 hours, but is not authorized by this
profile. A task that misses any gate is not silently sent to a full-price
synchronous model. It becomes `manual_sync`, `waiting_budget`, `out_of_scope`,
or `rejected`.

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

The `$0.05` checks are internal reservations calculated from the checked-in
price table, a conservative serialized-input token bound, and
`max_output_tokens`. They do not transmit a dollar ceiling with the provider
request and cannot guarantee the final provider invoice. Price changes,
provider-side accounting, or an incorrect token bound can differ from this
model. At the official 2026-08-03 Batch rates, this exact Luna profile can admit
at most 272,000 conservatively bounded input tokens; with its 2,000-token output
cap, the model-token charge is at most `$0.0284`. Crossing the 272,000-token
long-context boundary would exceed the internal `$0.05` gate and is rejected.

[OpenAI project spend limits](https://developers.openai.com/api/docs/guides/spend-limits)
now support both soft alerts and an enforceable monthly hard limit. The hard
limit returns `429` after tracked spend reaches the configured amount, but
enforcement is not instantaneous and recorded spend can slightly exceed it.
Use a dedicated project key, the narrowest model/rate permissions, the lowest
practical project hard limit, lower alert thresholds, and a fresh official-price
check as independent promotion blockers. If approval requires a penny-exact
maximum provider invoice, this canary must remain HOLD.

## Durable flow

```text
immutable evidence packet
  -> lease-fenced execution-plane binding
  -> BatchQueueBridge
  -> idempotent KST daily-budget bootstrap
  -> agent_runtime budget + job ledger
  -> immutable exact-request canary grant
  -> atomic one-shot consumption + exact dispatcher claim
  -> client/model-isolated JSONL
  -> OpenAI input file
  -> durable exact provider-create intent (two-minute create window)
  -> OpenAI Batch
  -> atomic exact Batch registration + intent close
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

The forward migration
`supabase/migrations/20260802130000_origintrail_batch_canary_grant.sql` adds the
private one-shot grant and replaces the generic claimer so it cannot claim any
OriginTrail job. OriginTrail is exclusive to the exact grant RPC during this
profile, closing the queue-to-grant race; other clients retain the generic
claim path.

The ledger provides:

- caller-generated deterministic job IDs and idempotency keys;
- a first-attempt `studio_sync` or `openai_batch` execution-plane binding that
  every retry must reuse across experiment start/end and mode changes;
- unique Batch `custom_id` values (`job_id:stage:attempt`);
- `FOR UPDATE SKIP LOCKED` leases and bounded attempts;
- same-attempt stale-lease recovery and paginated provider lookup before create;
- every same-attempt recovery is lookup-only; a lookup miss never uploads or
  creates another provider Batch;
- one-request Batch envelopes during the first pilot, preventing a changed claim
  group from creating a duplicate billable Batch after an ambiguous response;
- atomic budget reservation before a job can be queued;
- one immutable grant per workspace/config subject, with unique config and
  dispatch approvals, dispatch subject, and exact job;
- atomic, irreversible consumption of exactly one provider-Batch authorization
  with the exact attempt-one claim; terminal reservation release cannot restore
  this authorization;
- provider batch/file IDs stored as opaque references;
- a durable provider-create fence whose expiry forbids late creation but never
  auto-clears external-call ambiguity or lets settlement pass;
- out-of-order and partial result reconciliation by `custom_id`;
- actual token/cost settlement and release of unused reservation;
- immutable overage accounting: full provider-usage-derived cost is retained while
  only the authorized reservation is moved into bounded budget spend; the job
  is quarantined and every later claim is stopped while the incident remains
  unresolved;
- exact cost settlement for billable refusals and invalid outputs; if provider
  usage is missing, the full reservation is conservatively recorded as spent;
- finalization of missing, expired, failed, or cancelled results;
- terminal malformed, duplicate, or unknown result files are quarantined and
  conservatively finalized instead of retaining reservations forever;
- the first successful admission durably binds its KST budget day in the
  ledger. Backlog uses the actual queue-attempt day, while an uncertain replay
  after KST midnight reads the original binding without a second reservation;
- every producer attempt after attempt one uses a server-enforced replay-only
  lookup, regardless of current deadline or budget state. A missing prior row
  cannot become a new admission, and no new daily budget is configured for that
  lookup;
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
  tests/test_batch_bridge.py \
  tests/test_batch_openai_client.py \
  tests/test_batch_settings.py \
  tests/test_batch_canary.py \
  tests/test_batch_canary_preflight.py \
  tests/test_batch_repository.py \
  tests/test_batch_dispatcher.py \
  tests/test_batch_deployment.py \
  tests/test_batch_ledger_migration.py \
  tests/test_origintrail_batch_canary_grant_migration.py \
  tests/test_batch_cost_overage_migration.py \
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
BATCH_DAILY_CAP_USD=0.05
BATCH_MAX_CLAIMS=1
BATCH_MAX_REQUESTS_PER_BATCH=1
BATCH_TIMEZONE=Asia/Seoul
BATCH_EXPERIMENT_START_AT=<exact KST midnight>
BATCH_EXPERIMENT_END_AT=<exactly 48 hours after start>
BATCH_CANARY_ENABLED=false
BATCH_CANARY_ENVIRONMENT=staging
RAILWAY_ENVIRONMENT_NAME=staging
BATCH_CANARY_RELEASE_SHA=<exact deployed 40-hex commit>
RAILWAY_GIT_COMMIT_SHA=<exact deployed 40-hex commit>
BATCH_CANARY_APPROVAL_RECEIPT=
BATCH_CANARY_DISPATCH_RECEIPT=
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
with narrow model/rate permissions, a project hard spend limit, and lower spend
alerts in addition to the database cap. The hard limit blocks later requests
with `429`, but its non-instantaneous enforcement is not a penny-exact invoice
guarantee for this one request.

Do not set only one service to `live`. The config subject must be identical on
producer and dispatcher. The producer never receives the dispatch receipt or
OpenAI key until the exact queued job is known; the dispatcher remains
submission-disabled and poll-only without the exact-job dispatch receipt.

Subject commands are deliberately offline and produce **HOLD**, not approval:

```bash
python -m scripts.run_batch_dispatcher --approval-subject
python -m scripts.run_batch_dispatcher \
  --dispatch-subject \
  <exact-job-uuid> <exact-input-sha256> <exact-request-sha256>
python -m scripts.run_batch_dispatcher --preflight-live
```

`--approval-subject` does not require `OPENAI_API_KEY`. `--preflight-live`
parses the real live configuration and receipts but constructs neither the
Supabase repository nor the OpenAI client; its JSON must say
`database_calls:false`, `provider_calls:false`, and `submissions_enabled:false`.
Only `ready_to_submit:true` proves that the config remains hash-bound and the
window, 26-hour deadline slack, exact-job dispatch receipt, runtime staging
environment, and deployed release SHA are active. Its summary must also say
`runtime_environment_verified:true` and `runtime_release_verified:true`. It
still does not authorize or execute a submission.

The default live command and the Railway cron are poll-only. The only local
process flag that enables the exact claim/create path is billable and remains
HOLD until the separate approval gate:

```bash
python -m scripts.run_batch_dispatcher --submit-once
```

The staging gate is:

1. Obtain explicit approval for the disposable Supabase branch and any
   staging Railway/OpenAI cost.
2. Confirm the target is disposable staging, inspect its migration history,
   apply every migration in order, and run the Batch ledger security smoke.
   Treat this only as evidence for a separately approved production promotion;
   never use staging success as implicit production permission.
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
7. Keep both schedules paused. Stage the exact live values with the kill switch
   on, generate the config subject offline on both services, and compare the
   SHA-256. Verify `RAILWAY_ENVIRONMENT_NAME=staging` exactly equals
   `BATCH_CANARY_ENVIRONMENT=staging`. Verify `RAILWAY_GIT_COMMIT_SHA` is the
   immutable 40-hex SHA of the deployed image and exactly equals
   `BATCH_CANARY_RELEASE_SHA`; an operator-set label alone is not sufficient.
   After explicit config approval, record the same short-lived config receipt
   on both. Do not add a dispatch receipt yet.
8. Run the producer once, then pause it again. From a post-deployment intake,
   queue one standalone, text-only OriginTrail `daily_news` packet and verify
   its source ID has `is_quote is false`. Record the exact Batch job UUID and
   immutable input SHA-256 and full-request SHA-256. While the first `$0.05`
   reservation remains held it
   fills that KST budget, so a second job cannot enter. Confirm the staging
   ledger contains only the exact authorized job. A quote, unknown/pre-cutover
   row, article, photo, video, or animated GIF must stay on `studio_sync` and
   create no Batch handoff.
9. Obtain a separate explicit approval whose intended provider-spend ceiling is
   `$0.05`. This is an authorization limit, not proof that the database can cap
   the provider invoice. Re-verify official model pricing and the serialized
   worst-case token bound immediately before proceeding. Use a dedicated
   project key, narrow model/rate permissions, the lowest practical project hard
   spend limit, and lower spend alerts. Explicitly acknowledge that hard-limit
   enforcement is not instantaneous and may slightly exceed the configured
   amount.
   Generate the exact-job dispatch subject offline, record its two-hour receipt
   on the dispatcher only, and run `--preflight-live`. It must report the exact
   job/input/request binding plus `ready_to_submit:true` with zero external calls.
10. Run the dispatcher once manually with `--submit-once`, never on its
    ten-minute cron. The scheduled/default command is poll-only and cannot
    submit without that explicit process flag.
    Immediately remove the dispatch receipt and use `--poll-only` for later
    reconciliation. The live pass must register the immutable grant and consume
    it atomically with the exact attempt-one claim before any provider call.
    After input upload, require the first non-replayed provider-create intent
    receipt before `POST /v1/batches`, then close it only through exact atomic
    registration. Verify `provider_batches_consumed=1`, intent status
    `registered`, one provider Batch is registered, the
    result appears only in the read-only review inbox, duplicate accepted jobs
    remain zero,
    and no publication, approval, export, outreach, visual, or deploy row
    appears. A stale recovery lookup miss or attempt two is forbidden in this
    canary profile, makes no upload/create call, and requires a separately
    approved future experiment rather than a replacement receipt.
    If the intent is `armed` but exact provider lookup finds no Batch, do not
    clear it, settle around it, or submit again. Keep the workspace on manual
    HOLD until an operator audit and separately reviewed forward resolution.

A successful staging job is evidence for a later production application or
provider decision, not permission to deploy either. The schema-only layer has
been promoted separately; application rollout and billable Batch execution
remain **HOLD** until an operator reviews the staging receipt, spend, duplicate
count, structured-output result, and absence of external side effects and
explicitly approves that specific promotion.

Fresh authorized attempt-one claims submit directly. Only a stale lease from
an ambiguous create/register attempt searches provider history, bounded
by that attempt's original start time. For the first canary, a recovery lookup
miss or attempt two stops before upload/create and cannot be unlocked by merely
replacing the receipt. This keeps an ambiguous response from creating a second
billable Batch; a retry belongs to a separately approved future experiment.
An input-file upload failure is different: it may leave an orphan file but it
cannot create a billable Batch. The one-shot canary therefore settles that job
terminally, releases its reservation, and does not restore the consumed grant;
another provider attempt requires a newly approved experiment.

The first live profile accepts exactly 48 hours between KST midnights. Before
the start it makes no database or provider call. At and after the end it polls
and reconciles already-submitted Batches but never claims or submits new work.
Producer deadlines are clamped to the experiment end, so only the first 22
hours admit work and the last 26 hours form a submission drain window. The
first admitted S-tier job reserves its full internal `$0.05` maximum against
that first KST day, and no second job fits while that reservation remains held.
When the next KST budget day begins, fewer than 26 hours remain, so the deadline
gate prevents another ordinary admission.

The forward migration makes the intended one-shot envelope durable for the
approved config subject: it stores the exact request and internal `$0.05`
limit, then burns its single provider-Batch authorization atomically with the
exact attempt-one claim. Terminal budget release cannot restore that grant,
and the same config subject cannot accept a replacement dispatch binding. The
migration and its security smoke, exact
`RAILWAY_ENVIRONMENT_NAME=staging`, and the runtime/deployed release-SHA match
must pass in disposable staging before live submission; immediate receipt
removal, poll-only follow-up, and the dedicated project key, restrictions, and
project hard spend limit plus lower alerts remain promotion blockers. A new
config subject is a new experiment and requires separate explicit approval.

A broader 14-day profile is not currently accepted by live startup. Enabling
one requires a code change, a new config subject and receipt, reviewed staging
evidence, and a separate spend approval. The generic ledger ceiling of `$6`
per day is defense-in-depth for a future promotion, not permission to use an
`$84` 14-day envelope.

A previously uncertain OriginTrail handoff is still recovered after the
experiment end by consuming the already-durable same-UUID ledger receipt and
completing the idempotent public handoff. Ordinary and max-attempt recovery do
not rebuild current prompt, schema, deadline, or style-pack inputs, reserve
budget, or submit provider work. A new first-attempt OriginTrail job outside
the live window continues through the pre-existing synchronous workflow.

The expired dispatcher also releases only definitively unsubmitted
`queued`/`retry_wait` jobs. An expired `claimed` row is not released because an
create/register response or the terminal upload-failure settlement may have
been commit-unknown; it remains conservatively held and is reported as
`ambiguous_claimed_manual_recovery` for operator reconciliation. Keep the cron
running in expired mode until active Batches and that alert count are both zero.

Keep `BATCH_EXPERIMENT_MODE=dry_run` until the migration and security smoke pass
and the applicable staging approval is recorded. No deployment or billable
provider submission is part of the local build.

For an emergency stop, pause the Official X producer schedule first and remove
the dispatch receipt. Keep the dispatcher on explicit `--poll-only` long enough
to reconcile registered Batches, release only safe expired pre-submit work,
and report any ambiguous claimed row. `BATCH_CANARY_ENABLED=false` is the
default-off startup kill switch, but do not apply it to the reconciler until
polling drains because live config would then fail closed before polling.
Changing the mode cannot move an already-bound public job to the other
execution plane, but turning off the Batch service before its handoffs drain
can leave review work pending manual recovery.

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
