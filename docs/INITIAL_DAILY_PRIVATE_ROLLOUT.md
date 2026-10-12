# Initial YBO staff cards: bounded rollout

Local implementation; no deployment, scheduling, paid generation or delivery
performed by this work. This is the initial staff card, not final approval.

## Runtime artifact

`ops/content-ops/railway.daily-private-card.json` reuses the existing private-card
image and switches only its entrypoint to the daily CLI. It has no cron field,
restart is NEVER and predeploy is `--validate-only`. Existing canary manifests
and four-client defaults remain unchanged.

Set `CONTENT_OPS_DAILY_CLIENT_SCOPE=ybo_v1` on BOTH Netlify gateway and courier.
The request/receipt scope must match. SQL selects only Yellow, Babylon and
OriginTrail before reserving a claim; a post-claim client filter alone is not
the security boundary. The courier accepts at most three cards/twelve message
parts per invocation; SQL's existing unique workspace/client/KST-day outbox
prevents more than one initial card per client/day across restart. Unknown
claims/sends are not retried. The selected scope requires start `09:00` KST.

Only completed natural daily jobs with current immutable versions pass the
existing locked source/job/canonical checks. New scoped discovery additionally
requires the dedicated quality policy, actual PNG hash/dimensions and renderer
receipt; old Studio drafts, Squid and unfinished jobs are not upgraded or sent.
The admission policy is the actual renderer/Netlify `official-reference-sunburst-max@2`;
the SQL fixture derives its positive policy from Python rather than maintaining
a copied value. A cross-language test binds the Python marker, imported Netlify
policy, persisted prompt version and both SQL predicates; old `@1` rows are
explicitly rejected even if the legacy reconciler has already queued them.
The gateway already validates decoded opaque PNG pixels before catalog
admission; this SQL gate compares its persisted receipt, not image aesthetics.

## Minimal order (do not run blindly)

1. Commit/review/CI the actual Engine release E and compatible existing-bot
   release B. Remote main at the latest bounded readback remains
   `344401928a2d89a18ca85bab347042972b3db317`; these local changes are not deployed.
2. Run `node scripts/describe_daily_private_rollout.mjs --local-only` to produce
   SHA-256s in dependency order. Run the read-only catalog inventory with an
   existing authorized DB owner. Compare exact function source hashes, owner,
   search path, RLS and ACL against the disposable expected inventory. An
   installed proposal is NOT permission to rerun CREATE statements; reconcile
   installed/missing/drifted objects first. Historical migrations are comparison
   prerequisites, not a request to replay all history. The 16:28 UTC hosted
   inventory matched eleven existing function definitions/ACLs and five private
   table RLS/ACL contracts exactly: **do not reapply the six foundation/card
   proposals**. No drift was observed in this scoped initial-card inventory.
3. Apply only the resulting exact missing/approved SQL delta: first
   `quality_banner_generation_context.sql`, then `content_ops_daily_private_scope.sql`.
   Their four new functions were absent in the hosted readback; the other six
   proposal dependencies used in the local bootstrap are already present for
   the scoped initial-card contracts. Readback receipt:
   `ops/content-ops/daily-private-hosted-inventory-20261012.json`.
   It does not require final release-pair, final decision/publication queue,
   public channel routes or public-publisher grants. Employee edit/check button
   usability separately requires the existing bot's private callback role pack;
   do not advertise live buttons while that owner is disabled/incompatible.
4. OFF-deploy the quality API, Netlify and existing private courier at E; deploy
   the compatible existing review bot at B with its separate private runtime.
   Generator requires ONE durable absolute persistent SQLite volume shared by
   copy/idempotency/image/budget owners, one replica, immutable trusted official
   reference configuration and the operator's accepted daily budget mode. The
   private courier needs NO volume or provider/DB/public publisher secrets: its
   durable owner remains the Netlify RPC + PostgreSQL ledger. Do not replace
   or widen existing bot keys/policy.

   The accepted budget is an estimate gate, not a strict final-charge cap:
   `QUALITY_BANNER_BUDGET_MODE=authenticated_provider_estimate@1`, policy SHA-256
   `000a4243bf92c082db1120b6ccbd6aa8e2ade95ed355aaef124f79a31c6aa0a4`.
   The trusted `QUALITY_BANNER_PROVIDER_ACCOUNT_SHA256` must bind the same account
   in the persistent ledger. Unknown estimates/charges retain their liability;
   changing a date or request UUID cannot create a fresh spending allowance.
5. Courier OFF config: daily/button/review execution flags false; review mode
   daily, packet `daily_button_card_v1`, canary version empty, selected scope
   `ybo_v1`, start `09:00`, exact release E. Existing pinned bot policy and
   dedicated gateway/signing/edit keys remain identity-bound. Run validate-only
   and authenticated release readback, with no source/claim/Telegram call.
6. Only then enable the intended private generation/discovery/card/callback
   gates and observe one real natural source/version through staff delivery.
   Keep official publication OFF. Arrival requires four exact-part receipts
   and room readback; API success alone is insufficient.
7. Once the one-card receipt and edit/check behavior pass, select the daily
   manifest on the existing private service, set cron `*/15 * * * *`; no burst
   fill/retry. The application gates all work before 09:00 KST. Producer allowlist
   must also be YBO with daily limit3; the current producer's legacy limit4 is
   not the desired new setting. No desktop heartbeat is needed for hosted cron.

Rollback stops new cron/feature admissions, preserves durable unknown/send and
budget records, and reconciles exact attempts. Never delete rows or reset a
claim to resend after an ambiguous response.

## Deliberately separate final publication boundary

The initial card contains edit/check controls, not an automatic public approve
button. The final-preparation worker, E/B release registry, exact final card,
human decision and publication handoff remain a separate conditional path.
Initial private readiness does not establish public-post readiness.

## Evidence limits

The live 2026-10-12 read-only owner audit showed old Netlify/generator releases,
an unscheduled OFF canary courier and disabled private bot callback. Latest
GitHub main/CI recheck is unchanged and successful. Hosted PostgreSQL 17.6
initial-card catalog comparison is recorded above; new SQL behavior was tested
on disposable PostgreSQL 16.13, not on hosted production. Fresh process image
stamp, runtime readiness and destination arrival remain unobserved. The catalog
inventory never reads source text, identities, credentials or content.

Local proof commands:

```sh
python -m pytest -q tests/test_content_ops_daily_private_review.py tests/test_content_ops_daily_private_review_runtime.py tests/test_content_ops_private_review_card_gateway.py
node --experimental-strip-types --test tests_js/content-ops-review.test.mts
node --experimental-strip-types --test tests_js/daily-private-policy-contract.test.mts
node scripts/verify_daily_private_scope_local.mjs --local-only
node scripts/describe_daily_private_rollout.mjs --local-only
```
