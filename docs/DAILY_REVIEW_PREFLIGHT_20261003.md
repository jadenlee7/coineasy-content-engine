# Daily review: local corrections and no-send production readback

Observation window: 2026-10-03 14:18–14:28 UTC (23:18–23:28 KST).

At the observation window: existing production OFF release checks passed; the
new daily workflow was local, uncommitted and inactive. This is not approval to merge, apply
migrations, deploy, schedule, activate or send a private/public message.

## Existing production release, read-only

- GitHub main: `35d1f3f6867c13c18d9afd4c0e5ab8e047fa36f4`.
- Netlify published production deploy: `6abefc61768cd67918c588fa`, state `ready`,
  same exact commit, published 2026-10-02T00:36:22.129Z.
- Authenticated release GET returned HTTP 200, schema
  `content-ops-release-readback@1`, same exact `netlify_release_sha`, and
  `gateway_enabled=false`, `button_gateway_enabled=false`,
  `public_telegram_publish_enabled=false`. That legacy schema does not attest
  the new daily gateway flag, mutation-path runtime context or callback owner.
- Railway `coineasy-private-review-card` production deployment
  `6d6acf4b-c93a-487f-b39b-993bb28c513a`: GitHub-origin metadata has the same
  exact commit. SUCCESS is a completed one-shot, not a running daily service:
  deployment stopped, instances REMOVED/EXITED, cron null.
- Its existing deployment log contains an OFF `validate_only` receipt with
  build/release verified, runtime SHA `match`, runtime/release verified,
  network/DB/Telegram calls false. The separate OFF run receipt has
  `public_send_attempted=false`. These are the existing deployment's receipts,
  not a validation of today's uncommitted code or a new execution.
- Read-only service configuration shows both legacy courier flags false,
  `canary`/`button_card_v1` mode, pinned existing bot policy present and no fixed
  canary version. The new daily courier flag is not explicitly configured.
  A native runtime Git SHA missing from the configuration API is not a runtime
  mismatch: the actual deployment receipt independently proves `match`.
- No settings, deployment, migration, business rows or sends were changed.

## Hosted DB compatibility findings

Production project `coineasy-meme-engine` was verified by project inventory;
catalog/history/ACL queries ran inside read-only transactions, using an explicit
project ref without changing local link configuration. No reconcile, claim,
owner-step, generation, approval or publication RPC was invoked.

- Owner-ledger migration `20260927130000` is already in hosted history.
  Current owner/image relation/function existence, forced RLS and effective
  minimum ACL checks passed. Do not reapply its historical proposal. This
  existence/ACL pack alone does not pin all hosted owner function bodies.
- The current `content_ops_reconcile_daily(uuid,uuid)` still enumerates via
  `item.id = job.content_item_id`, and does not use request/output bindings.
  Observed canonical function-definition SHA-256:
  `1779c55e833a0a90fe06cdf4183c72424d2300d9dac917e0defa7e76a5fc20e2`.
- The current candidate helper still uses `left(telegram_copy,2000)` and
  `left(x_copy,600)`. These were catalog predicates, not exported draft content.

## Local-only corrections

1. `20261003143000_content_ops_reconcile_producer_binding.sql` binds discovery
   to the natural producer's canonical request/output/current version. A guarded
   UUID cast retains primary-key lookup and rejects malformed input. Existing
   slot ownership, locked eligibility, exact scope, dedupe and ACL stay intact.
   File SHA-256:
   `dda15652649983b5dd1ca612519cf2ef63e9b936d1df83f8b740d00365a0a604`.
2. `20261003143100_content_ops_review_exact_copy.sql` returns complete immutable
   title/Telegram/X copy within existing character bounds 160/3400/1000;
   oversize copy is held, not shortened. Transport UTF-16 and public-post limits
   remain independent gates. File SHA-256:
   `4b988a9cf20a98dcbc2f79b78b64c1cb72615adea2e3756058a18a4379fca917`.
3. Daily intake rechecks KST day/window before every new claim. Already-owned
   work finishes normally; legacy OFF routing stays unchanged.

No already-applied migration was edited; neither new migration was applied
to production. No job backfill or broad privilege was added.

## Verification and limits

- Final Python suite: 4,517 passed, three existing deprecation warnings.
- Final JavaScript suite: 532 passed, 3 skipped, 0 failed (535 total).
- Isolated PostgreSQL 16: all 59 local migrations and full-schema outbox ACL,
  natural NULL-FK discovery 4→0, exact version 1→0, nine invalid producer
  exclusions, complete Telegram 2101/3400 and X 601/1000 claim equality,
  oversize hold, eight-way single-winner claim/begin and unknown-delivery
  restart fencing passed. Production/provider calls 0 in this harness; all
  synthetic rows rolled back and disposable server/directory removed.
- Regression is wired into both the local harness and credential-free CI
  producer-binding fixture. `node --check` and `git diff --check` passed.
- Local HEAD `2e689ca5b1d82fc0f393171e39b16e04bc97b0c4` and verified remote main
  have the same source tree `76068877c740883224e1c22e4ec13706b0d8a6a2` before
  these dirty changes. Local `origin/main` is stale and was not used as current
  release evidence. No commit, push, PR or hosted CI was created during that
  readback window; later PR/CI receipts must be read independently.
- Docker daemon unavailable; no Docker start, image build or container-backed
  PostgreSQL 17 proof was performed. A packaged daily command is not a mounted
  schedule or accepted employee button flow.

## Next controlled increment

Prepare/review the corrective daily-only change against current main and obtain
hosted CI, then separate exact-file migration and OFF-deployment approval.
Before any activation, verify daily flag/readback and mutation-path native
deployment context, exclusive existing bot/callback ownership, current source
eligibility and employee check/hold/edit handling. Authorize one exact-version
private canary and inspect its receipts before approving any recurring timing.
Public Telegram/X/Typefully execution remains separate and unconnected here.

Deploy-checklist and code-review skills kept local fixture proof, existing OFF
runtime proof and actual staff delivery as distinct completion gates.
