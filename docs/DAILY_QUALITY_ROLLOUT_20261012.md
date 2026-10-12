# Daily quality rollout — 2026-10-12

Status: LOCAL IMPLEMENTATION; NOT ACTIVATED. No paid generation or send in this
increment. User authorization does not itself prove deployed functionality.

## Confirmed operating intent

- Yellow, Babylon and OriginTrail; preserve the existing 09:00 KST start as the
  intended daily window, maximum one fresh initial source per client/day.
- Latest official source only; no repeated source just to fill a daily quota.
- Combined estimated image-generation limit USD10 per KST day, integer micro-USD ledger.
  This is not USD10/client and does not authorize unbounded regeneration.
  The user explicitly accepts actual settlement variance. This supersedes the
  earlier strict actual-charge-cap requirement, not any access-change gate.
- Reference-first max-quality Korean banner, Telegram announcement and X copy.
  Keep official art/logo, mobile legibility, natural OriginTrail tracking and
  semibold Babylon headline. No universal Studio-template requirement.
- Existing staff room/bot; employees inspect/edit the current version. Public
  channel work may only follow that version's valid human final confirmation.
  Edits invalidate prior checks/approval; Typefully is an unscheduled private
  draft, not an automatic public X post.

## Live read-only owner audit

Observed 2026-10-12 15:41–15:45 UTC. Saved settings and deployment metadata only;
not fresh in-process image-stamp or destination-arrival attestations.

| Owner | Observed state |
|---|---|
| Engine remote main | `344401928a2d89a18ca85bab347042972b3db317`, CI successful |
| Bot remote main | `8b8eb7ace341eb5b00537357e1e8aa211ec62f56`, CI successful |
| Netlify published | `cbbc73a0b9d4a96a8af0ef782242c948021bdb4f`, ready |
| Railway generator API | `cb9de07b16b6b9c5d42833588b76e02dd46ee8b3`, running |
| Daily producer | Existing legacy path, every 15min, saved start09:00KST/limit4/four clients |
| Private courier | Unscheduled completed canary; saved review/button switches false |
| Bot private callback | Saved private-review flag false; old release running |

The local quality integration is not in these deployed releases. Netlify
authenticated release/flags and hosted proposal/ACL state were not established
by this audit; absence of evidence is not an absent setting/schema.

## Approved estimate-based budget

Sunburst's initial estimate is reconciled to actual token cost. Public request
status does not document a final billing receipt or enforceable maximum.
Sources: [model reference](https://open.higgsfield.ai/models/marketing-studio/image/sunburst/api-reference),
[billing](https://docs.higgsfield.ai/docs/concepts/billing-and-retention),
[status schema](https://docs.higgsfield.ai/docs/api-reference/requests/get-request-status).

The user has explicitly selected authenticated estimates rather than a strict
actual-charge ceiling. The real adapter now POSTs the exact generation body to
`/estimate/marketing-studio/image/sunburst`, using the same authenticated account
as generation. A <=5-minute proof binds the request digest and account. Integer
micro-USD rounding is upward; budget reservation and one-shot submission intent
share one transaction. Static prices, multipliers and balances are not evidence.

Completed images retain provisional forecast cost on their completion KST day.
Unknown/pending requests retain forecast liability across days and cannot be
automatically retried. Only a final-charge receipt bound to the exact provider
request can replace provisional cost with actual cost. `completed` does not
provide that billing receipt. Actual variance is accepted, separately visible,
and may reduce remaining admission when reconciled. A known actual overrun does
not permanently disable unrelated future days. No final-billing API is claimed.

Required configuration: `QUALITY_BANNER_BUDGET_MODE=authenticated_provider_estimate@1`,
trusted `QUALITY_BANNER_PROVIDER_ACCOUNT_SHA256` matching the preprovisioned shared
store, and `QUALITY_BANNER_SPEND_POLICY_SHA256` equal to
`000a4243bf92c082db1120b6ccbd6aa8e2ade95ed355aaef124f79a31c6aa0a4`.
Wrong/old ledger modes fail before paid copy; no runtime reset or silent upgrade.

## Work still needed before daily arrival can be claimed

1. Verify production account/store binding for the now-implemented estimate rule.
2. Package/attest the locally mounted final-card runtime; separately finish
   quality-banner edit/revision admission before offering that action.
3. Compatible Engine/Bot release pair, hosted CI, exact proposal/ACL plan, OFF
   rollout, durable shared stores and immutable public reference manifest.
4. One fresh private canary proving actual banner/card arrival and revised-version
   button behavior; only then bounded daily private scheduling.

No new poller, desktop heartbeat, production setting, key, grant, migration,
deployment, source, content version, approval or public post was created here.

## Completed local increment

- Shared estimate image-budget ledger reserves authenticated forecasts in the SAME
  transaction as one-shot image intent, binds the provider ID with its receipt,
  retains unknown liabilities across KST rollover, and never treats image
  completion as final billing. Completed forecasts become provisional. Historical
  paid/unknown rows block a new empty budget ledger; separate reconciliation is
  required. The strict legacy policy remains distinct, not relabelled.
- Optional human-check hook records a durable final-card preparation intent in
  the same transaction. It is not a content approval or send authorization.
- Engine-side preparation reuses the existing final-card courier and multipart
  ledger. Original release generation and active identity are checked again at
  reservation and each new send admission. Revocation preserves previous send
  receipts but blocks the next part. A release switch away/back cannot revive
  an in-flight old intent. Only the separate ce2 human decision can queue a
  public Telegram post and unscheduled Typefully draft.
- Preparation SQL is a proposal, SECURITY INVOKER, RLS/FORCE enabled and no
  runtime grants. Local bot startup now mounts the optional hook default-OFF;
  the Engine has a bounded dispatcher with canonical PNG/private Storage reads,
  fresh trusted route attestations and private-only ce2 transport. Production
  has not mounted these changes; exact pair registry, narrow ACLs, image and
  compatible vendor closure still require the rollout above.
- Initial daily courier now has an opt-in YBO scope, maximum three, after09:00KST.
  Scope is bound through the gateway and enforced before claim in proposed SQL;
  quality policy/native PNG identity prevents legacy Studio cards entering it.

## Earlier strict-policy verification receipts (historical, not additive)

| Check | Result |
|---|---|
| Parent focused Python seven-file run | 215 passed, three existing deprecation warnings |
| Final budget-only suite after legacy-liability guard | 39 passed |
| Final-card/owner/callback eight-file regression after race fixes | 143 passed |
| Final corrected rollback-boundary assertion | 1 passed; adapter reports safe submit-unknown |
| JavaScript quality gateway/asset suite | 11 passed |
| Disposable PostgreSQL16 preparation fixture | Passed; 62 historical migrations + proposals; stopped/socket removed |
| Independent race-fix review | Both findings resolved; no remaining bounded findings |

These suites overlap; do not sum counts. Fixture SQL exercises state
transitions, not a concurrent-revoker stress run. Tests used synthetic billing
proofs/provider responses/destinations only. Hosted CI, Docker builds, production
schema compatibility, actual banner aesthetics, staff arrival and final-button
end-to-end acceptance are **not established** by these receipts.

## Estimate-policy continuation receipts (not additive)

- Image-budget suite: 93 passed after explicit over-budget error classification.
- Quality service suite: 56 passed, including authenticated same-body estimates,
  all three clients, wrong/old policy refusal before paid copy, unknown no-retry,
  fresh-only new intents and stale known-result GET-only reconciliation. Expired
  results can record provisional completion but cannot return content for use.
- Parent four-file Python run: 162 passed and one budget error-code assertion
  failed before the classification fix. That exact assertion then passed, and
  the entire affected quality-service suite above passed. The unchanged three
  files were not rerun solely to obtain an additive total.
- Combined quality/gateway JavaScript run: 42 passed.
- Initial YBO courier: Python101 and gateway/policy JavaScript32 passed. The
  cross-language check binds actual Python/Netlify policy@2 to SQL and catalog
  persistence; disposable PG16 rejects old@1 completed/pending cards. Its scoped
  SQL/ACL/expected-definition proof passed. These suites overlap the run above.
- Final preparation runtime/packaging: 15 passed; prior related owner/courier
  regression65 passed. Exact runtime discovery SELECT was exercised in PG16,
  including stale-oldest/new-eligible and release-generation filtering before
  LIMIT. Claimed unknown intents are not reopened. Docker build was unavailable;
  local packaging checks are not image-build proof.
- Independent budget integration review: same auth/body, proof freshness,
  atomic reserve/complete and stale reconciliation fix inspected; no remaining
  bounded budget findings. This does not establish provider billing or delivery.
- Existing bot full-suite runs: 102/103 passed twice with one existing 10-second
  subprocess timeout under concurrent load. The exact test passed unchanged
  alone. Do not report full bot acceptance as green until the closure run passes.

Hosted read-only catalog at16:28UTC: the initial-card scope's eleven existing
function definitions/ACLs and five private-table RLS/ACL contracts matched the
local expected inventory. Four new functions from two proposal files were
absent; no existing foundation needs reapplication in that scoped comparison.
See `INITIAL_DAILY_PRIVATE_ROLLOUT.md` for the recomputable exact-byte plan.
It is not an audit of all final-publication objects. No production write,
generation charge, staff send or public send occurred in this continuation.
