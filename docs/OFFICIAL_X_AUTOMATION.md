# Official X → review draft automation

The scheduled worker turns new posts from each client's configured official X
account into Korean Content Studio drafts. It never approves or publishes
content. Its only successful terminal state is `needs_review` in the team
library.

## Content decision

| Official source | Scheduled result |
|---|---|
| Short, concrete announcement | Daily News card + Telegram/X copy |
| Complete X Note of at least 300 characters | Article + Telegram/X copy + Markdown |
| Tutorial signal for Yellow or Squid | Article or Daily News draft; reviewer can manually continue it as a Tutorial |
| Low-signal social post, reply, retweet, or configured skip phrase | No draft |

When the optional EasyFarm bridge is configured, bounded aggregate Korean
audience demand terms may reorder otherwise eligible official posts. Those terms
are ranking hints only: they cannot admit a greeting, reply, retweet, skipped
campaign, or unsupported source, and they never enter the generation prompt as
facts or copy.

Tutorial carousel generation remains available in the human Studio UI for
Yellow and Squid. Reviewers can open a stored News or Article draft and use
**이 원문으로 튜토리얼 만들기** to prefill the manual Tutorial form. The
scheduled worker deliberately cannot claim a `manual_only` Tutorial job, so
`AUTOMATION_ENABLE_TUTORIALS` remains `false`; a human must review the source
and explicitly start every carousel generation.

Daily News automation uses the deterministic `classic` card. The source image
is preserved in the source record but is not automatically remixed. This keeps
scheduled drafts independent of the visual subtitle-cleanup path and leaves
brand-sensitive visual localization to a reviewer.

## Reliability and limits

- Every X poll is cursor-checked and bound to an idempotent poll UUID.
- Every cron run refreshes each official X feed even when a pending source
  already exists, the day's draft is reserved, or the local queue limit has
  been reached. A temporary X failure can still recover a previously committed
  pending source.
- The cursor-advancing worker explicitly requires a complete poll of at most
  200 unseen posts. If X still advertises another page, it fails before cursor
  advancement; it never silently skips the truncated source range. Manual
  generation endpoints remain bounded newest-first samples and do not advance
  this cursor.
- Sanitized source items are deduplicated by official feed and X post ID.
- A source committed just before a worker crash remains in the pending-source
  ledger and is recovered on the next run.
- A transaction-protected KST-day ledger reserves at most one draft per client
  and four drafts across the workspace.
- Generation requests use deterministic UUIDs. If Netlify succeeds but the
  worker loses the response, the next attempt reuses the same durable catalog
  version instead of generating a duplicate.
- Jobs use bounded leases and three attempts. A stale worker cannot complete or
  fail a lease owned by another worker.
- One client failure does not stop the other clients.
- A valid EasyFarm signal snapshot is used only after its sanitized
  `term`/`weight` envelope is committed to immutable, service-only Supabase
  ranking evidence. Signal retrieval, validation, freshness, or evidence-write
  failure falls back to the existing official-X-only ranking.
- Provider, database, and generation error bodies are never written to worker
  logs; only bounded internal error codes are emitted.

## Railway cron

The dedicated service uses:

- config path: `/railway.official-x-cron.json`
- image: `Dockerfile.automation`
- command: `python -m scripts.run_official_x_daily`
- schedule: `*/15 23,0-2 * * *`
- restart policy: `NEVER`

Railway evaluates cron expressions in UTC, so this is a KST morning retry
window. The database reservation guarantees that repeated starts do not create
repeated daily drafts. The process exits after each run; Railway skips a new
cron start if the previous execution is still active.

References: [Railway Cron Jobs](https://docs.railway.com/cron-jobs),
[Railway Config as Code](https://docs.railway.com/config-as-code/reference).

## Server-only variables

The cron service needs only:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
CONTENT_STUDIO_WORKSPACE_ID
X_BEARER_TOKEN
STUDIO_BASE_URL=https://coineasy-newscard.netlify.app
STUDIO_AUTOMATION_TOKEN
AUTOMATION_TIMEZONE=Asia/Seoul
AUTOMATION_LOOKBACK_HOURS=30
AUTOMATION_DAILY_DRAFT_LIMIT=4
AUTOMATION_ENABLE_TUTORIALS=false
# Optional; set URL and token together.
EASYFARM_CONTENT_SIGNALS_URL=https://jlxbywqofrltyttklcqy.supabase.co/functions/v1/content-signals-api
EASYFARM_CONTENT_SIGNALS_TOKEN
EASYFARM_CONTENT_SIGNALS_WINDOW_DAYS=7
```

`STUDIO_AUTOMATION_TOKEN` must be the same high-entropy value in Netlify and the
cron service, but it must be different from the human `STUDIO_ACCESS_TOKEN`.
The cron must not receive Telegram, Typefully, Figma, or other publication
credentials.

`EASYFARM_CONTENT_SIGNALS_TOKEN` is a separate high-entropy server-to-server
credential accepted only by the pinned EasyFarm Edge Function. Never substitute
an EasyFarm operator credential or either product's Supabase service-role key.
The response is schema-, privacy-, freshness-, size-, client-, host-, and
path-validated. Freeform themes, questions, summaries, raw messages, user
identifiers, wallets, and chat/group identifiers are rejected.

Netlify accepts the automation token only on the three generation relays. It
does not grant a browser session and cannot open the team library, request an
editable export, review a version, or publish it.

## Safe rollout

1. Apply all Supabase migrations, including
   `20260722150145_official_x_review_draft_worker.sql` and
   `20260727120000_content_signal_ranking_evidence.sql`.
2. Deploy the Netlify generation relays with the dedicated automation token.
3. Configure a separate Railway cron service with the custom config path and
   server-only variables above. Give it no public domain.
4. Run `python -m scripts.run_official_x_daily --dry-run`. This reads candidates
   but creates no source, job, asset, or content rows.
5. Trigger one real run and confirm each result is `needs_review` in the team
   library before enabling the schedule.
6. If enabling EasyFarm signals, deploy its `content-signals-api` first,
   provision the dedicated token in both server environments, and confirm the
   worker reports only `signals_used` with a bounded term count or the safe
   `signals_unavailable` fallback. No raw term text is emitted in telemetry.

Durable Figma links and the internal import plugin remain downstream of
approval and are not exposed by the current shared-session UI.
`record_approved_figma_link` requires a real Supabase Auth user and workspace
membership. The scheduled X worker has no Figma write path or plugin secret.

At `needs_review`, a reviewer can request a local, non-persistent
Figma-editable SVG using the fields shown in the current Daily News detail.
This does not create a durable asset or Figma link and does not change workflow
status. Scheduled drafts use `classic`, so this transient export does not
depend on source-image retention. A historical `remix` whose external source
image or Railway-cleaned Squid visual cannot be loaded fails closed instead of
returning an image-less SVG.
