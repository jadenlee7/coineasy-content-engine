# Official X → review draft automation

The scheduled worker turns new posts from each client's configured official X
account into Korean Content Studio drafts. It never approves or publishes
content. Its only successful terminal state is `needs_review` in the team
library.

## Dynamic style references

Scheduled review drafts freeze up to three earlier posts from the same active
official client feed into an immutable reference pack before queueing. The pack
is keyed by the deterministic request UUID, so retries receive the same prompt.

The references are style-only: they can guide cadence, sentence length, and
structure, but they are never added to `content_source_links` and cannot supply
facts, entities, dates, numbers, URLs, or calls to action. Netlify accepts these
runtime references only from the separate studio automation credential.
Generated work still stops at `needs_review`; the worker has no publish path.
For Squid, these text references are separate from the versioned visual-family
registry. A recent post cannot silently become a visual reference merely by
appearing in this cadence pack.

See `docs/ADR-005-official-x-style-reference-packs.md` for the data and security
contract.

## Content decision

| Official source | Scheduled result |
|---|---|
| Short, concrete announcement | Daily News card + Telegram/X copy |
| Complete X Note of at least 300 characters | Article + Telegram/X copy + Markdown |
| Tutorial signal for Yellow or Squid | Article or Daily News draft; no Tutorial is generated automatically |
| Low-signal social post, reply, retweet, or configured skip phrase | No draft |

When the optional EasyFarm bridge is configured, bounded aggregate Korean
audience demand terms may reorder otherwise eligible official posts. Schema
`1.2` also adds a thresholded quiz-learning priority: after immutable evidence
is recorded, it may lift an already eligible official guide/documentation post.
Those signals are ranking hints only: they cannot admit a greeting, reply,
retweet, skipped campaign, or unsupported source, and they never enter the
generation prompt as facts or copy. Quiz aggregates require at least 20 attempts
and 5 participants globally and per returned category; no question, answer, or
audience identifier is exported.

Tutorial carousel generation remains available in the human Studio UI for
Yellow and Squid. The scheduled worker deliberately cannot claim a
`manual_only` Tutorial job, so `AUTOMATION_ENABLE_TUTORIALS` remains `false`.
The library exposes a Tutorial continuation only after an exact linked
publication receives a qualified performance recommendation and at least 300
characters of pinned official source evidence are available. A human must
review that source and explicitly start every carousel generation.

Daily News automation uses a client-specific visual policy:

- A Yellow Daily News source with verified official X media uses the framed
  `remix` automatically. The full official composition, co-brand lockup, type
  hierarchy, and Yellow-highlighted message remain dominant; compact Korean
  context is confined to the brand-native lower panel. A text-only Yellow
  source continues to use the approved `yellow-news-classic@2` card.
- OriginTrail and Babylon Daily News sources with verified official X media use
  a source-heavy framed `remix`. The first-party square poster receives 780px
  of the 1080px canvas; its marks, contrast, typography, Bitcoin/provenance
  motif, and proof hierarchy stay intact while Korean context remains in a
  compact lower panel. Text-only sources continue to use each deterministic
  `classic` card.
- A Squid Daily News source with an official X photo or canonical video poster
  uses `remix` automatically. The complete official crop remains authoritative
  and its native aspect ratio becomes the primary X deliverable; only audited
  meaningful copy may be localized in place. No square letterbox, new panel,
  footer, CTA, duplicate logo, or crop-based cleanup fallback is added.
- A Squid source without a usable static image enters the server-owned
  `squid-visual-routing@1` policy. Announcements, verified metrics, and bounded
  status/product updates route to the matching generated GTM family. A
  text-only mascot, mood, or meme source stops at
  `manual_visual_review_required`; it is never expanded into invented 3D art.
  Once an image-backed Squid job requests `remix`, an unavailable or unsafe
  source image fails closed for review/retry instead of silently replacing the
  official campaign creative with a generic card.
- Squid replies and retweets are filtered before the generic durable-intake RPC,
  so one timeline reply cannot reject the whole poll or become a draft source;
  same-account quote posts remain eligible for audited source remix. The visual
  policy version is bound only to Daily News request UUIDs and Netlify request
  hashes, preventing an older family render from being reused as current. An
  exact stored pre-policy result remains replayable only when its immutable spec
  has no family/policy fields and its original request hash matches.

## Performance recommendation handoff

After manually publishing a stored Daily News item, a team member can paste its
public X status URL or public Telegram message URL into the library detail. The
server records an idempotent publication observation for the current immutable
version. It does not send, edit, approve, schedule, or delete a post.

The next successful EasyFarm schema `1.1` snapshot may match that canonical URL
to a same-client/channel performance candidate. Content Engine recomputes the
score and stores immutable evidence before recording a review recommendation.
Article needs `0.75`; Tutorial needs `0.80`, a Yellow or Squid client, an
aggregate learning signal, and a matching official how-to/documentation source.
Both remain manual. If pinned official sources total fewer than 300 characters,
the recommendation is visible as the read-only **공식 원문 보강 필요** state
and no generation CTA is enabled. There is no source-enrichment or rebinding
flow in the current product, so that immutable recommendation cannot later
become actionable. A later valid performance snapshot is retained as a new
immutable history row; the library shows only the newest recommendation per
publication, target kind, and policy version whose evidence is still within the
latest 24 hours.

The exact local-account allowlist is defined in
[ADR-004](ADR-004-content-performance-promotions.md). In particular, the
`channels.announce` URL in each EasyFarm `clients/<client>.json` is canonical
for Telegram performance attribution and is mirrored by Content Engine's
public-channel configuration. Actual Telegram publishing uses a server-only
environment channel ID; that ID and other publisher settings must not be used
to widen or replace the shared performance allowlist.

See [ADR-004](ADR-004-content-performance-promotions.md) for the exact-link,
privacy, and failure contract.

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
- EasyFarm schema `1.2` preserves schema `1.1` performance candidates, which are
  matched only to the exact
  canonical public URL recorded for a Content Studio publication in the same
  client and channel. Missing or ambiguous links create no recommendation.
- Performance candidates are 12–72 hours old, use fresh same-client/channel
  cohorts, and are persisted as immutable evidence. They never queue a
  generation job or change approval, publication, or Figma state.
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
AUTOMATION_ALLOWED_CLIENTS=yellow,origintrail,squid,babylon
AUTOMATION_LOOKBACK_HOURS=30
AUTOMATION_DAILY_DRAFT_LIMIT=4
AUTOMATION_ENABLE_TUTORIALS=false
# Optional; set URL and token together.
EASYFARM_CONTENT_SIGNALS_URL=https://jlxbywqofrltyttklcqy.supabase.co/functions/v1/content-signals-api
EASYFARM_CONTENT_SIGNALS_TOKEN
EASYFARM_CONTENT_SIGNALS_WINDOW_DAYS=7
```

`AUTOMATION_ALLOWED_CLIENTS` is a fail-closed intake scope. A dedicated
OriginTrail worker may set it to `origintrail`; the canonical four-client
value preserves the normal daily worker behavior. The setting changes which
official clients are inspected and cannot grant generation or publication
authority.

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

1. Deploy the EasyFarm `content-signals-api` provider first. Verify a schema
   `1.0` request still returns the backward-compatible V1 envelope, then verify a
   schema `1.1` request adds only bounded aggregate candidates and metadata. Do
   not deploy the consumer while either contract check fails.
2. Apply the Content Engine Supabase migrations in order, including
   `20260722150145_official_x_review_draft_worker.sql`,
   `20260727120000_content_signal_ranking_evidence.sql`, and
   `20260727143000_content_performance_promotions.sql`. Run
   `supabase/tests/content_performance_promotions_security.sql` against the
   target schema and require its exact-version, exact-URL, account-allowlist,
   idempotency, immutability, and no-side-effect checks to pass.
3. Deploy the Content Engine Railway worker. Configure the separate cron service
   with the custom config path and server-only variables above, with no public
   domain. Run `python -m scripts.run_official_x_daily --dry-run`; it reads
   candidates but creates no source, job, asset, or content rows.
4. Deploy the Netlify functions and console with the dedicated automation token
   only after the migrations and Railway checks pass. Trigger one real worker
   run and confirm every generated result remains `needs_review` before enabling
   the schedule.
5. Provision the EasyFarm bridge token in both server environments and confirm
   the worker reports only `signals_used` with bounded term/candidate counts or
   the safe `signals_unavailable` fallback. No raw term text, public URL, or
   score is emitted in telemetry.
6. Perform an exact-version smoke test: in the library, record one
   already-public allowlisted local X or Telegram URL on the current stored
   Daily News version, run the next snapshot, and verify that only that exact
   version receives a review recommendation. Verify a stale version, another
   account, another channel, and another URL fail closed. Do not enable any
   publisher or Figma credential for this smoke test.

Rollback is consumer-first and leaves official-X drafting available. Disable or
remove `EASYFARM_CONTENT_SIGNALS_URL` and
`EASYFARM_CONTENT_SIGNALS_TOKEN` from the Railway worker, then roll back the
Netlify recommendation controls and the Railway consumer. The worker must report
the safe `signals_unavailable` path and continue official-X-only ranking,
collection, and `needs_review` draft generation. Do not roll back by granting
publisher credentials, deleting immutable evidence/history, or writing into
EasyFarm; the provider can remain on backward-compatible schemas `1.0` and
`1.1` while the consumer is disabled.

Durable Figma links and the internal import plugin remain downstream of
approval and are not exposed by the current shared-session UI.
`record_approved_figma_link` requires a real Supabase Auth user and workspace
membership. The scheduled X worker has no Figma write path or plugin secret.

At `needs_review`, a reviewer can request a local, non-persistent
Figma-editable SVG using the fields shown in the current Daily News detail.
This does not create a durable asset or Figma link and does not change workflow
status. Every scheduled client draft with pinned official media uses `remix`;
text-only drafts use that client's `classic` treatment. A historical `remix`
whose external source image or Railway-cleaned Squid visual cannot be loaded
fails closed instead of returning an image-less SVG.

Article jobs also store a source-locked visual story: one `1200x630` hero and
two `1200x675` inline editorial visuals. The Studio library regenerates them
from the immutable article version. See
[`ARTICLE_VISUAL_STORY.md`](./ARTICLE_VISUAL_STORY.md).
