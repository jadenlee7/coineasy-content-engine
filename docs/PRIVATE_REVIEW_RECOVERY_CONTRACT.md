# Exact private review recovery (OFF until separately activated)

The operator authorized code/DB repair, verification and OFF deployment on
2026-10-16. This is not authorization to activate or send. No original outbox,
trial owner, generation job, asset, budget entry or staff policy is reset.

## Shared implementation contract

- Reuse the existing private courier and Netlify endpoint. No new service,
  provider generation, scheduler, callback handler or public publisher.
- `mode=recovery`, `packet_mode=recovery_delivery_only_v1`. The ordinary
  gateway/courier/trial flags must stay false. New flags default false:
  `CONTENT_OPS_RECOVERY_GATEWAY_ENABLED`, `CONTENT_OPS_RECOVERY_COURIER_ENABLED`.
- Exact operator plan is provided as `CONTENT_OPS_RECOVERY_PLAN_JSON` plus
  `CONTENT_OPS_RECOVERY_PLAN_SHA256`. Canonical UTF-8 sorted-key JSON SHA-256.
  SQL installs its own `private.content_ops_recovery_json_sha256(jsonb)` helper;
  the optional agent-work-order schema is not a hosted dependency.
- Plan keys (all required, no extras): `schema_version`, `recovery_id`,
  `workspace_id`, `client_id`, `outbox_id`, `content_item_id`,
  `content_version_id`, `source_item_id`, `generate_job_id`, `banner_asset_id`,
  `banner_sha256`, `telegram_copy_sha256`, `x_copy_sha256`,
  `original_outbox_sha256`, `original_trial_id`, `original_plan_sha256`,
  `destination_policy_sha256`, `release_sha`, `not_before`, `deadline`,
  `estimated_daily_limit_usd_micros`.
- Schema value `private-review-recovery@1`, client `babylon`, daily estimate
  ceiling 10000000, UTC timezone-aware timestamps, positive window at most
  two hours. Release must equal build/runtime/pinned SHA. Existing validated
  private destination policy must match its plan hash. No raw destination/key.
- Wire scope is exactly `{mode,packet_mode,content_version_id,recovery_id,
  plan_sha256}`. Additional headers `X-Content-Ops-Recovery-Id` and
  `X-Content-Ops-Recovery-Plan-Sha256`. Existing exact release/version headers
  remain mandatory. No wildcard or other delivery mode.

## SQL and gateway seam

One public service-role-only RPC `content_ops_recovery_step` receives:
`target_workspace_id uuid, target_content_version_id uuid,
target_recovery_id uuid, target_plan_sha256 text, target_release_sha text,
target_action text, target_args jsonb`.

`status` with empty args returns exactly `{active:true,observed_at,plan,
plan_sha256}` for the immutable active capability; otherwise null. It never
claims, reserves or changes state. All actions recheck the exact frozen plan,
deadline, original outbox identity/state and eligible current candidate.
Existing source latest/<24h/feed poll/today KST guards remain mandatory.

Existing wire body actions remain reconcile/claim/image/begin/owner; the
gateway maps owner steps to the same RPC action names. `reconcile` with empty
args is read-only availability (0 or 1), not ordinary queue reconciliation.
`claim`, `image`, `begin` and owner steps retain existing argument names and
return shapes. `claim` returns original outbox identity with a NEW unique
recovery claim token. One consumed recovery attempt cannot be reclaimed.

`terminal` uses the explicit recovery receipt:
`{status:recovery_sent,recovery_id,card_id,outbox_id,
original_outbox_status:claimed,execution_authorized:false}`;
not-confirmed is `{status:not_confirmed,recovery_id,card_id:null,
outbox_id:null,original_outbox_status:claimed,execution_authorized:false}`.
Only the transport adapter may map verified recovery terminal evidence to
the existing courier's internal `sent` shape. The original outbox is never
reported or updated as sent.

## Durable authority and attempts

- Operator-only immutable grant, unique on original outbox and version; empty
  after migration. No service-role insert/update/delete permissions or public
  authorization RPC. No grant or start timestamp is installed in an OFF release.
- Append-only claim/begin/recovery terminal records; preserve the original
  expired claimed outbox and original API trial SQLite owner exactly.
- Grant/claim must prove original expired claim, no packet/send-start/message/
  finish, no review/cards/part attempts or competing recovery. Recheck original
  row snapshot and exact canonical PNG/copy before each meaningful operation.
- Reuse existing review/card state and four-part reserve/confirm ledger.
  Extend ownership narrowly for an exact recovery-bound review; no ordinary
  outbox duplicate, lease reset, timestamp rewrite or new generation.
- Reserve before each provider POST. Any uncertain result is reconcile-only;
  never retry claim, begin or a reserved provider part.
- Original source becoming stale/non-latest or a stale feed means BLOCK,
  not an implicit historical-artifact exception.

## Runtime and release

OFF returns before any credentials/files/network construction. Validate-only
may read local image stamp/plan/policy but makes zero network/DB/Telegram calls.
Enabled execution is single-use and checks current authority before each
Telegram request. Use existing delivery-only packet and sender validators;
no executable approval buttons/public paths are added by this recovery.

Tests must cover original-record preservation, ACL, no grant default, replay,
concurrent claim/reserve, unknown acknowledgements, copy/PNG/policy/source/day
drift, expiry, terminal distinction and OFF zero-I/O. Production schema and
prior function bodies must be preflighted, with an atomic apply/readback receipt.
