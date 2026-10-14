# YBO server-hosted one-shot trial

This is a readiness-anchored private trial, not a recurring daily scheduler. Code and
CI proof do not imply that the trial is deployed, scheduled, enabled or sent.

## Operator boundary

- The approved one-shot ID is **1a2bef47-4827-4bc3-9b4d-5baf2714015d**.
  Complete code/CI and exact-SHA OFF deployment before starting its clock.
  The operator then inserts one immutable `private.ybo_trial_readiness_anchor`
  row with a fresh UTC-second start, exactly two-hour deadline, same KST date,
  workspace, release SHA and canonical plan SHA. No runtime write grant exists.
  UPDATE/DELETE/TRUNCATE and a second anchor are refused. A restart never moves
  this clock or grants a second trial. The preceding PR211 fixed window expired
  without admission and is historical evidence only.
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

Deploy initially OFF. The preparation plan is non-authoritative while OFF.
After readiness, the single anchored plan pins the fixed UUID, release SHA,
start/deadline, derived KST date, the three clients and the USD 10 policy.
The canonical sorted compact ASCII JSON SHA is shared by all three services.
Do not replace a partially executed plan or remove its journal to retry.
The runtime journal path stays unchanged and the owner filename is the fixed
trial UUID, never a plan-hash-specific owner path. Local operator preparation
and activation receipt directories may differ; runtime state must not.

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
`YBO_TRIAL_ID`, `YBO_TRIAL_PLAN_SHA256`, `YBO_TRIAL_RELEASE_SHA`,
`YBO_TRIAL_NOT_BEFORE` and `YBO_TRIAL_DEADLINE`. Its delivery
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
Readiness proves the configured immutable plan identity, including exact
`not_before`, `deadline`, and `kst_date`; it does not write the DB anchor.
The operator must compare the durable anchor to the final plan before every
activation. A preparation plan or absent anchor cannot authorize activation.
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

The prior PR #211 exact-claim body SHA-256 is
`5373553023dbfae5270c631d087811ad40517f693638f71b6c8904b4509b720d`.
The readiness-anchored replacement body is
`b4f4ef59b2cba338c33b22e8263d270dfcfcddbfed1dc84eb7063c6d476ff086`.
The separate empty-schema proposal is
`supabase/proposals/ybo_trial_readiness_anchor.rollback.sql`, file SHA-256
`89aa4936658ad8f8a0c51889e405cfc1c021eb1113e18c06d6e8ee06821165fe`.
It creates no anchor row and grants no runtime access. The operator applies
the empty schema and two body replacements atomically after matching the exact
hosted preflight catalog. A different prior body, existing anchor schema,
changed OID/owner/ACL/non-body metadata, or unrelated security/dependency drift
must abort the transaction. Do not replay any earlier apply or install these
rollback-only proposals as automatic migrations.

The exact-claim body reads one immutable anchor for the code-pinned trial and
workspace. It refuses Yellow and any other worker identity before business
locks. All source, request, style-pack, lease, latest-source, freshness, dedupe
and role checks remain. The logo path still requires the exact empty
`source.media=[]` collection paired with the explicit JSON string
`job.input.source_image_url=""`; missing, NULL, non-string, whitespace or
nonempty/malformed media cannot use this path.

The historical `supabase/proposals/quality_banner_generation_context.sql` is
unchanged. A separate rollback-only proposal,
`supabase/proposals/quality_banner_generation_context_ybo_logo.rollback.sql`,
retains its attached-media path and permits the same explicit empty-media pair
only inside the immutable anchored window, for a naturally bound initial job created
inside that window, attempts=1/max=3, exact nine-field input and a lease ending
no later than the trial deadline. It changes no owner, ACL, signature, volatility
or other non-body catalog field; its guard verifies the preceding deployed body SHA
`4ab8c89d807d428e392e2cf63dbf2cfd17125a8e6d3203baf6a4585aba010197`.
The readiness-anchored quality-context body SHA-256 is
`b475ed241a64827ef7294a48691f1954a9314e2fb22ed57d028f52bb3c56cd19`.

**The schema and both behavioral SQL replacements require explicit production approval** and
exact hosted pre/post catalog readbacks. Code/CI/merge authorization is not
production SQL apply authority. Neither rollback file installs its candidate,
and no trial may activate until both hosted bodies and runtime release agree.
Only after exact OFF runtime readiness may the operator insert the anchor once,
with a start no more than 60 seconds old. UPDATE, DELETE and TRUNCATE are denied;
restarts cannot advance the deadline. Every activation checks the full stored
anchor against the final plan, including plan hash and exact release SHA.
An official logo supplies visual identity only; it does not supply source facts
or authorize publication. Attribution/claim review remains mandatory.

Local tests use mocks or explicitly disposable PostgreSQL only. The rollback
SQL proof package is not a production migration. Hosted-version/runtime/image
and actual staff-room delivery require separate live readbacks after rollout.
