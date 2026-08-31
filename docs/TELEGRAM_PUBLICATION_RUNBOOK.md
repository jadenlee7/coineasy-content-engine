# Exact Telegram publication runbook

This runbook covers the first public-publication slice only: an approved,
non-mock Squid Daily News version and its one stored PNG. Article, Tutorial, X,
and every other client remain manual.

## Safety boundary

- Approval never publishes by itself. A signed Studio session must confirm the
  exact version again.
- The browser supplies only the version UUID and an idempotency UUID. Supabase
  reloads the approval, caption, asset, and official channel.
- Railway verifies the private PNG and calls Telegram `sendPhoto` once. It does
  not reformat, truncate, use parse mode, call `sendMessage`, or retry after the
  delivery fence.
- The canonical destination is `@squid_kor_update`. An environment-only change
  cannot redirect this production workflow to another channel.
- Both Railway execution planes compare the GitHub-origin runtime commit in
  `RAILWAY_GIT_COMMIT_SHA` with the operator-authorized exact commit in
  `TELEGRAM_PUBLICATION_RELEASE_SHA`. Missing, malformed, uppercase, or
  mismatched values fail before worker or recovery repository construction.
- `API_SECRET`, `STUDIO_ACCESS_TOKEN`, `SUPABASE_SERVICE_ROLE_KEY`, Telegram bot
  tokens, and the publication worker credential are separate secrets and must
  never be reused.

## Required configuration

Apply `supabase/migrations/20260801120000_exact_telegram_publication.sql` before
deploying any application plane.

| Variable | Main Railway API | Publication cron | Netlify |
|---|:---:|:---:|:---:|
| `SUPABASE_URL` | required | required | required |
| `SUPABASE_SERVICE_ROLE_KEY` | required | required | required |
| `CONTENT_STUDIO_WORKSPACE_ID` | required | required | required |
| `TELEGRAM_BOT_TOKEN_SQUID` | required | required | no |
| `TELEGRAM_CHANNEL_SQUID` | required | required | no |
| `PUBLICATION_WORKER_TOKEN` | required | no | required |
| `RAILWAY_API_URL` | no | no | required |
| `TELEGRAM_PUBLICATION_ENABLED=false` | required | required | no |
| `TELEGRAM_PUBLICATION_ALLOWED_CLIENTS=squid` | required | required | no |
| `RAILWAY_GIT_COMMIT_SHA` | Railway-provided | Railway-provided | no |
| `TELEGRAM_PUBLICATION_RELEASE_SHA` | required | required | no |
| `TELEGRAM_PUBLICATION_RECOVERY_LIMIT=100` | optional | optional | no |
| `STUDIO_TELEGRAM_PUBLISH_ENABLED=false` | no | no | required |
| `STUDIO_TELEGRAM_PUBLISH_ALLOWED_CLIENTS=squid` | no | no | required |

Use one newly generated value of at least 32 printable ASCII characters for
`PUBLICATION_WORKER_TOKEN` on both planes. Do not expose it to the browser.

`TELEGRAM_PUBLICATION_RELEASE_SHA` is not a secret. Set it only to the exact
40-character lowercase Git commit explicitly approved by the operator. Never
manually set or override `RAILWAY_GIT_COMMIT_SHA`; Railway must supply it from
a GitHub-origin deployment. A CLI/image deployment without that provenance
correctly fails the release fence.

The backstop worker is built with `Dockerfile.publication`. Create it as a
separate Railway cron service with no public domain and set its custom config
source to the absolute repository path
`/railway.telegram-publication-worker.json`; Railway does not discover a
non-default filename automatically. See Railway's
[custom config source documentation](https://docs.railway.com/config-as-code).
Its five-minute invocation is mandatory
while publication is enabled and claims at most one durable job by default. Set
`TELEGRAM_PUBLICATION_LEASE_SECONDS` to at least 180 seconds. The main Railway
API also exposes a dedicated, bodyless internal kick endpoint authenticated only
by `X-Publication-Worker-Key`.

## Rollout order

For the `double-fact-check@1` upgrade, this order supersedes the earlier
migration-first rollout. Do not apply
`20260802120000_double_fact_check_approval_gate.sql` while either publication
execution plane or the official-X generation cron can claim new work.

1. Disable new Studio publication requests, pause the official-X generation
   cron, and set `TELEGRAM_PUBLICATION_ENABLED=false` on both the main Railway
   API and the mandatory publication cron. Wait for current leases to expire,
   run the recovery-only command below, and use the read-only queue gate in
   step 8. Do not continue while an exact Telegram job is `running` or a
   publication is `publishing` with a non-null `delivery_started_at`. The
   double-fact-check migration deliberately aborts and rolls back in either
   state.
2. Deploy the main Railway API and mandatory cron worker with
   `TELEGRAM_PUBLICATION_ENABLED=false`. Set the cron service config source to
   `/railway.telegram-publication-worker.json` and confirm it has no public
   domain. Set `TELEGRAM_PUBLICATION_RELEASE_SHA` to the approved Git commit on
   both execution planes and require a GitHub-origin exact-SHA deployment. The
   cron pre-deploy validate-only command must report
   `runtime_release_verified:true`, `provider_calls:false`, and
   `database_calls:false`. Keep the official-X generation cron paused. For the
   double-fact-check upgrade, verify the Railway Tutorial response contract
   exposes bounded `lessons`; do not send a mutating generation request yet.
3. Apply all pending migrations, including the double-fact-check approval gate,
   and run all transactional SQL smoke tests. This creates a brief fail-closed
   Studio review maintenance window until the matching Netlify functions are
   deployed; legacy approval RPCs remain revoked.
4. Set the dedicated shared worker token only on the main API and Netlify, plus
   the service-specific Supabase/Telegram variables from the table above. Run
   the built publication image with `--validate-only`; this mode must report
   `provider_calls:false` and `database_calls:false`.
5. Deploy Netlify with `STUDIO_TELEGRAM_PUBLISH_ENABLED=false`. Existing
   publication status remains readable, but no new request can be queued.
   Verify the authenticated `/api/studio-capabilities` response advertises
   `double-fact-check@1`, generate each supported content kind, record both
   human attestations, and exercise publication in dry-run mode.
6. Run the Python publisher, worker, endpoint, Netlify, and PostgreSQL tests.
   Provider tests use a mock transport and must assert one `sendPhoto` call.
7. If a live non-production canary is required, create an isolated deployment
   and database migration that explicitly pins a disposable public test
   channel. Do not repoint the production Squid environment alone.
8. Before every first activation or reactivation, run this read-only queue gate:

   ```sql
   select
       publication.id as publication_id,
       publication.content_item_id,
       publication.content_version_id,
       publication.status as publication_status,
       publication.delivery_started_at,
       job.id as job_id,
       job.status as job_status,
       job.available_at,
       job.lease_expires_at
   from public.publications as publication
   join public.jobs as job
     on job.workspace_id = publication.workspace_id
    and job.input ->> 'publication_id' = publication.id::text
   where publication.request_payload ->> 'workflow'
            = 'exact_telegram_publication_v1'
     and (
         publication.status in ('queued', 'publishing', 'delivery_unknown')
         or job.status in ('queued', 'running', 'retrying')
     )
   order by publication.created_at, publication.id;
   ```

   A clean first activation returns zero rows. Never enable with
   `publishing`/`delivery_unknown`. A queued or retrying row will be sent as soon
   as Railway is enabled, so proceed only when the operator has explicitly
   re-approved that exact item/version for immediate public delivery. Do not
   edit queue rows directly to make this check pass.
9. Enable the main Railway API and mandatory cron worker first, then enable
   Netlify. Resume official-X generation only after its authenticated capability
   preflight succeeds against the matching Netlify deployment. Keep both
   publication allowlists exactly `squid`.

Turning off the Netlify flag stops new requests without hiding existing state.
Turning off Railway stops new claims. Neither action cancels a provider call
that already crossed the delivery fence.

For rollback, turn off Netlify first and set
`TELEGRAM_PUBLICATION_ENABLED=false` on both Railway execution planes. Read the
latest `lease_expires_at` from the queue gate and wait until it is in the past;
do not guess from wall-clock time. Then run the publication image once with:

```bash
python -m scripts.run_telegram_publications --recovery-only
```

Recovery is accepted only while the execution flag is the literal `false`. It
also requires the same exact deployed-release fence as the live worker, then
calls only the service-role expired-lease reconciliation RPC: it cannot claim a
job, construct a Telegram publisher, or call Telegram. Repeat it until
`reconciled_count` is zero, then run the queue gate again and inspect every
returned row. A pre-fence expiry may now be `retrying`; a post-fence expiry is
`delivery_unknown` and stays non-sendable. Re-enable only under step 7. If an
unexpected queued or retrying job must not be delivered, leave publication
disabled until an audited cancellation path is added; never repair the state
with ad hoc SQL.

Publication state mutations must use the supported single-publication RPCs.
Direct or bulk updates to `public.publications` are not an operational repair
path; trigger serialization is scoped to one immutable publication key.

## `delivery_unknown`

`delivery_unknown` means Telegram may already have accepted the message. Never
reset, requeue, or resend that job automatically.

1. Open `@squid_kor_update` and compare the exact stored caption and PNG around
   `delivery_started_at`.
2. If the message exists, paste its canonical
   `https://t.me/squid_kor_update/<message_id>` URL into the Telegram performance
   observation field. This records the existing post and does not publish.
3. If it does not appear, keep the job unresolved until Telegram delivery can be
   ruled out operationally. A new approved version and a new publication request
   require an explicit human decision because a delayed original message could
   otherwise become a duplicate.

Never put bot tokens, worker tokens, raw provider payloads, or arbitrary error
text into publication rows or operator notes.
