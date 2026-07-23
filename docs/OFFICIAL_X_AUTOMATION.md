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
| Tutorial signal for Yellow or Squid | Article plus a Tutorial recommendation |
| Low-signal social post, reply, retweet, or configured skip phrase | No draft |

Tutorial carousel generation remains available in the human Studio UI for
Yellow and Squid. The scheduled worker deliberately cannot claim a
`manual_only` Tutorial job. `AUTOMATION_ENABLE_TUTORIALS` must therefore remain
`false` until a visible manual-review trigger is shipped.

Daily News automation uses the deterministic `classic` card. The source image
is preserved in the source record but is not automatically remixed. This keeps
scheduled drafts independent of the visual subtitle-cleanup path and leaves
brand-sensitive visual localization to a reviewer.

## Reliability and limits

- Every X poll is cursor-checked and bound to an idempotent poll UUID.
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
```

`STUDIO_AUTOMATION_TOKEN` must be the same high-entropy value in Netlify and the
cron service, but it must be different from the human `STUDIO_ACCESS_TOKEN`.
The cron must not receive Telegram, Typefully, Figma, or other publication
credentials.

Netlify accepts the automation token only on the three generation relays. It
does not grant a browser session and cannot open the team library, request an
editable export, review a version, or publish it.

## Safe rollout

1. Apply all Supabase migrations, including
   `20260722150145_official_x_review_draft_worker.sql`.
2. Deploy the Netlify generation relays with the dedicated automation token.
3. Configure a separate Railway cron service with the custom config path and
   server-only variables above. Give it no public domain.
4. Run `python -m scripts.run_official_x_daily --dry-run`. This reads candidates
   but creates no source, job, asset, or content rows.
5. Trigger one real run and confirm each result is `needs_review` in the team
   library before enabling the schedule.

Figma remains downstream of approval. Approved immutable SVG versions can be
linked through `record_approved_figma_link`; the scheduled X worker has no Figma
write path or service-role secret in a plugin.
