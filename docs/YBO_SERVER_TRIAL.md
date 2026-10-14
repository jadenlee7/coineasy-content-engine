# YBO server-hosted one-shot trial

This is a fixed-window private trial, not a recurring daily scheduler. Code and
CI proof do not imply that the trial is deployed, scheduled, enabled or sent.

## Operator boundary

- Earliest work: **2026-10-15 00:10:51 KST / 2026-10-14 15:10:51 UTC**. Hard latest effect:
  **2026-10-15 02:10:51 KST / 2026-10-14 17:10:51 UTC**. This operator-approved two-hour rewindow is
  fixed in code, not an arbitrary configurable start or deadline extension.
- Yellow, Babylon and OriginTrail only, at most one new official source each.
- Existing account-bound persistent image ledger: aggregate **USD 10 estimated**
  daily limit. Actual provider settlement may differ; no second budget or reset.
- Existing private staff destination and bot policy only. No employee access
  changes, callback bot startup, official Telegram/X posts or Typefully publish.
- Repeated deployment/start cannot admit the same durable trial again. An
  uncertain generation/send/acknowledgment is held, never automatically repeated.
- Yellow is explicitly skipped before all per-client preflight/intake/phase I/O,
  even though the KST daily slot is now October 15. Its earlier uncertain job
  is not resumed, retried or replaced. The other clients retain the existing
  daily-slot skip and exact-source deduplication guards.
- The earlier Yellow trial and its existing daily slot remain preserved. A fresh
  trial must skip every existing daily slot before polling or reserving work;
  this time-window change does not resume, clear or replace an unknown job,
  owner, provider reservation or delivery receipt.
- The latest source may be skipped if missing, stale, unsupported or already
  reserved. This trial cannot promise three messages regardless of source state.

## Server ownership

The existing API hosts one background task. It waits on its own clock, so the
operator's computer, browser and Codex heartbeat are not execution dependencies.
The API private mounted journal stores the one-shot owner and immutable manifest.
It must use one replica and one persistent volume. No generic job queue is drained.

| Component | Existing credential boundary | Trial responsibility |
| --- | --- | --- |
| API controller | Existing source, database, provider and Studio credentials | Fixed-time admission, exact natural job, budgeted banner/copy, immutable manifest |
| Netlify | Existing automation/gateway auth and API connection | Exact release, source/current-version checks, existing private delivery ledger |
| Isolated courier | Existing gateway token and private bot policy | At most three exact four-part cards, fresh owner check before each Telegram request |

The API does not receive a new Telegram token; the courier does not receive an
API admin key, provider key, database key or Railway control-plane credential.

## Release and activation are separate

New code must first be reviewed/merged under separate merge authorization.
GitHub-origin API/courier images and Netlify published deployment must all use
the **same exact 40-character merge SHA**. Verify native deployment metadata,
immutable image stamp, runtime SHA and authenticated release readback; a saved
environment value alone is not provenance. Keep auto-deploy disabled.

Deploy initially OFF. An explicit trial plan pins a fresh UUID, the release SHA,
the fixed date, the shorter deadline, the three clients and the USD 10 policy.
The canonical sorted compact ASCII JSON SHA is shared by all three services.
Do not replace a partially executed plan or remove its journal to retry.

API trial settings (never log secret values):

- `YBO_TRIAL_CONTROLLER_ENABLED` and `YBO_TRIAL_API_MODE` default `false`.
- `YBO_TRIAL_PLAN_JSON`, `YBO_TRIAL_PLAN_SHA256`, and
  `YBO_TRIAL_DESTINATION_POLICY_SHA256` are exact immutable pins.
- Existing `NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT` must be a private persistent
  directory owned by the runtime. Existing image ledger/store/account binding
  remains mandatory and is checked separately from the trial journal.
- `AUTOMATION_WORKSPACE_ID`, `STUDIO_AUTOMATION_TOKEN` use the already authorized
  connection; do not invent or rotate keys.
- Broad `QUALITY_BANNER_ENABLED`, `NEWS_CARD_IDEMPOTENCY_ENABLED`,
  `TELEGRAM_PUBLICATION_ENABLED`, `CONTENT_OPS_REVIEW_ENABLED` and
  `CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED` stay literally `false`.

Netlify generation uses only `YBO_TRIAL_GENERATION_GATEWAY_ENABLED` plus
`YBO_TRIAL_ID`, `YBO_TRIAL_PLAN_SHA256` and `YBO_TRIAL_RELEASE_SHA`. Its delivery
settings and the isolated courier entrypoint are documented in
[the courier contract](YBO_TRIAL_COURIER_RUNTIME.md). All trial flags default OFF.

Before each client intake, the hosted controller makes one authenticated
`GET /api/ybo-trial-preflight` on the fixed production Studio origin. Existing
`X-Studio-Automation-Key`, `X-Studio-Expected-Release-Sha`, `X-Ybo-Trial-Id`, and
`X-Ybo-Trial-Plan-Sha256` headers must match the published build and exact trial
pins. The endpoint performs no DB, owner, provider or send operation. It
requires the generation trial flag ON and **literal** `false` for
`QUALITY_BANNER_ENABLED`, `NEWS_CARD_IDEMPOTENCY_ENABLED`, all four broad
gateway/review flags, and `STUDIO_TELEGRAM_PUBLISH_ENABLED`. A missing OFF flag
is a failure, not a default. It also checks the fixed API address and presence
and bounded format of the existing API secret.
After those local checks, it authenticates the existing Netlify `API_SECRET`
with one bounded `GET /internal/trial/readiness` on the fixed API origin. The
receipt requires the exact trial UUID, plan hash, immutable/runtime release SHA,
controller ON, broad/public flags OFF, all three active local clients, and a
fresh observation. An old OFF container cannot satisfy it. This reads local
configuration and the build stamp but never opens or mutates an owner. Redirects, errors,
oversized/malformed responses, missing active YBO clients, and configuration or
deadline drift fail closed without retries in that individual request.
The receipt requires `upstream_api_auth_verified=true`;
older config-only receipts are rejected before intake. Preflight success is
not generation, delivery, budget or API-owner proof.

Before durable owner admission, startup now waits for the same authenticated
read-only preflight to succeed. This avoids depending on the new API public
route before Railway has made it reachable. Only transient transport failures,
502/504, or the exact 503 `trial_preflight_unavailable` envelope may repeat this
no-business GET: at most six attempts, two-second backoff, at most 60 seconds,
and never beyond the fixed deadline. OFF/configuration/authentication failures
and invalid, redirected or stale receipts are terminal. No owner row, slot,
job, budget reservation, provider call or send is created by this readiness
step. Default OFF does not perform readiness I/O.

After readiness, the owner admits exactly once and the existing one-shot
preflight still precedes each client intake. Any failure after admission closes
the owner unknown without retrying intake, generation or delivery. Fixed
allowlisted stage/code/status diagnostics omit response bodies and credentials.
Neither readiness success nor these diagnostics prove generation or delivery.
Never reset an existing owner to repeat. The same preflight succeeds only after
the exact new API ON container is reachable, not merely after Netlify activation.
No eligible fresh source is
not a reason to enable or force a trial; leave every trial flag OFF instead.

## Work and shutdown evidence

1. Before admission, validate the exact release and `controller_task_running`
   through authenticated `/internal/trial/status`. Owner must not be admitted.
   Check courier startup, no cron/restart, destination policy equality, budget
   balance and all broad/public flags OFF. This is the server registration proof.
2. At/after the start, refresh official source and reserve one natural daily slot.
   The installed exact-claim function locks only its bound job. The source, job,
   style pack, immutable request and phase must match before any paid generation.
3. The existing provider ledger submits at most once. Only the exact known
   pending response permits collect-only requests for the persisted provider ID.
   No pending record means no new copy, estimate or image submit on that path.
4. Catalog readback checks the current version, both copies, factual-review
   marker, exact source link and canonical private 2880-square PNG bytes/hash.
   This is structural verification, **not perceptual or human approval**.
5. After all eligible artifacts are ready, publish the immutable manifest once.
   Existing private delivery ledgers reserve each of the four parts before send.
   The trial packet truthfully says `needs_review` and has a source URL button,
   not a functional edit/approve callback.
6. Only a verified four-part terminal DB receipt closes an existing send phase.
   The final receipt makes the owner terminal and effectively OFF immediately;
   failure, cancellation or deadline also closes authority. A request already
   in flight cannot be recalled. Preserve unknown state; do not replay it.

The courier exits after its bounded attempt. Saved trial environment flags can
remain `true` even after effective authority is OFF: report these separately.
Use the already authorized post-trial cleanup to restore saved trial flags to
`false`, then verify runtime status and unchanged public flags. No control-plane
key is granted to the content process just to perform that cleanup.

## SQL and test proof

The preceding **12:44:51–14:44:51 UTC** candidate's body SHA-256 is
`6c306a539f0476eb7acb0cf4430d96d2a2783919a725de17c181fef5d3f1e477`.
Before any separately authorized replacement, read the hosted body and its
security/dependency catalog; require that exact previous body and unchanged
catalog. The local operator receipt is not a generation or delivery receipt.
Do not replay an earlier apply or include this as an automatic production
migration. The current rollback-only claim candidate is a **time/day-only
rewindow** of that deployed body. It retains an exact empty `source.media=[]` collection paired
with the explicit JSON string `job.input.source_image_url=""`. Missing, NULL,
non-string, whitespace or nonempty/malformed media cannot use this logo path.
All existing identity, lease, latest-source, freshness, dedupe, role and fixed
window checks remain. The claim candidate body SHA-256 is
`5373553023dbfae5270c631d087811ad40517f693638f71b6c8904b4509b720d`.

The historical `supabase/proposals/quality_banner_generation_context.sql` is
unchanged. A separate rollback-only proposal,
`supabase/proposals/quality_banner_generation_context_ybo_logo.rollback.sql`,
retains its attached-media path and permits the same explicit empty-media pair
only inside the fixed trial window, for a naturally bound initial job created
inside that window, attempts=1/max=3, exact nine-field input and a lease ending
no later than the trial deadline. It changes no owner, ACL, signature, volatility
or other non-body catalog field; its guard verifies the preceding deployed body SHA
`52bee7b65bf683b958b38a8017cc14faea2246e301ac957de548692c99a5746e`.
The new quality-context candidate body SHA-256 is
`4ab8c89d807d428e392e2cf63dbf2cfd17125a8e6d3203baf6a4585aba010197`.

**Both behavioral SQL replacements require separate production approval** and
exact hosted pre/post catalog readbacks. Code/CI/merge authorization is not
production SQL apply authority. Neither rollback file installs its candidate,
and no trial may activate until both hosted bodies and runtime release agree.
An official logo supplies visual identity only; it does not supply source facts
or authorize publication. Attribution/claim review remains mandatory.

Local tests use mocks or explicitly disposable PostgreSQL only. The rollback
SQL proof package is not a production migration. Hosted-version/runtime/image
and actual staff-room delivery require separate live readbacks after rollout.
