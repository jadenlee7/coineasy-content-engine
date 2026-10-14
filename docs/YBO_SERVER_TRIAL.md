# YBO server-hosted one-shot trial

This is a fixed-window private trial, not a recurring daily scheduler. Code and
CI proof do not imply that the trial is deployed, scheduled, enabled or sent.

## Operator boundary

- Earliest work: **2026-10-14 09:00 KST / 00:00 UTC**. Hard latest effect: 02:00 UTC.
- Yellow, Babylon and OriginTrail only, at most one new official source each.
- Existing account-bound persistent image ledger: aggregate **USD 10 estimated**
  daily limit. Actual provider settlement may differ; no second budget or reset.
- Existing private staff destination and bot policy only. No employee access
  changes, callback bot startup, official Telegram/X posts or Typefully publish.
- Repeated deployment/start cannot admit the same durable trial again. An
  uncertain generation/send/acknowledgment is held, never automatically repeated.
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

## Work and shutdown evidence

1. Before 00:00 UTC, validate the exact release and `controller_task_running`
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

The separately authorized exact-claim function was installed once on October 13.
Its body SHA-256 is
`e319cfa49556c7d70837a01693a2a751fd986aea22b81adfdc499ed6784c5005`.
Existing security/dependency catalog readbacks were unchanged. The function was
not called during installation. The local operator receipt is not a generation
or delivery receipt. Do not run a second apply or include this as an automatic
production migration.

Local tests use mocks or explicitly disposable PostgreSQL only. The rollback
SQL proof package is not a production migration. Hosted-version/runtime/image
and actual staff-room delivery require separate live readbacks after rollout.
