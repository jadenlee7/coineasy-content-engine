# High-quality banners to initial staff review cards

Status: **LOCAL INTEGRATION — NOT DEPLOYED / ENABLED / SENT**.

On 2026-10-12 the user explicitly approved **USD10/day combined estimated image spend**
for Yellow/Babylon/OriginTrail and daily staff review before official posting.
Actual settlement variance was explicitly accepted. The budget decision and
local authenticated estimate integration are settled; hosted rollout is not.
See [the current rollout record](DAILY_QUALITY_ROLLOUT_20261012.md).

## Connected path

`latest official tweet -> real natural job + exact request ID -> Korean copy
checkpoint -> reference-first max-quality image -> canonical asset/current
version -> real job completion -> existing initial staff-review outbox/card`

Only Yellow, Babylon and OriginTrail enter this quality route. Squid's visual
policy remains unchanged. This connection does not create a new bot, poller,
desktop automation or public publisher.

- Netlify resolves the exact running natural job, current KST slot, source
  state/style pack and newest official source through the proposed read-only
  `get_quality_banner_generation_context` RPC. Browser-supplied context is
  refused. The feed must be active/recent and the source less than 24h old.
- Railway receives the original Idempotency-Key and DB-bound workspace/job/
  request/client/source/text hash. Admin service authentication, explicit flags
  and matching pinned/runtime release are required before quality work.
- A shared immutable request-mode fence prevents a pending/unknown request
  switching between quality and legacy production after a flag/context change.
  Quality resumes retain their own GET-only lifecycle; they do not take a
  second generic generation reservation.
- A private persistent store binds each natural request/source to one copy
  attempt. SDK retries are disabled. Copy and reference inputs are checkpointed
  before one image submission. Changed inputs, new UUIDs and new calendar days
  cannot bypass source deduplication. Unknown copy/submit outcomes remain held.
- Prompt-bound source/style/guidance is rejected if it contains forbidden
  private markers; the source is not silently redacted or sent before checking.
- Higgsfield Sunburst uses max quality, requested 4k and the full original art
  plus registered official logo. Both references are anonymously downloaded,
  decoded and hash-checked before paid image submission. Use immutable public
  references: the provider's later fetch cannot be proven by our earlier GET.
  Large Korean type and spacious leading replace a generic Studio template.
  Policy `official-reference-sunburst-max@2` preserves Yellow's composition,
  reduces OriginTrail headline tracking, and uses a lighter semibold Babylon
  headline. These directions are part of the immutable request digest.
- A pending image resumes only through its recorded provider ID with GET.
  Completed native opaque 2880–4096px square PNG bytes are retained without
  resizing. Restart reuses the copy and PNG; lost output files can be restored
  from those same bytes without another generation. A missing/wrong journal
  volume blocks instead of silently creating a new ledger.
- Netlify verifies the distinct quality policy, full pixel decode, opacity,
  native dimensions, SHA and 10,000,000-byte bound. It uses the existing catalog
  and returns IDs/metadata, not a large base64 automation response. It never
  claims Figma approval or legacy Studio rendering provenance.
- The original worker completes the real job. Existing daily reconciliation
  admits its exact current version once. The existing four-part staff packet
  contains banner, Telegram copy, X copy and signed controls. Approval and
  publication counts remain zero until the separate human decision path.

## Default-OFF runtime configuration

The environment factory requires the explicitly approved estimate policy. The
adapter obtains an authenticated USD estimate for the exact generation payload
with the same provider credentials. A fresh request/account-bound proof reserves
rounded-up micro-USD in the same transaction as the one-shot image intent.
Over-budget forecasts cannot submit an image. This is not a provider-enforced
actual-charge ceiling. Completed images retain provisional estimated cost until
an exact-request final-charge receipt is reconciled; `completed` is not billing.
Pending/unknown liabilities survive KST midnight and are never auto-resubmitted.

The planned gateway/server use `QUALITY_BANNER_ENABLED`; the server additionally requires
`NEWS_CARD_IDEMPOTENCY_ENABLED`, `QUALITY_BANNER_RELEASE_SHA` matching runtime
`RAILWAY_GIT_COMMIT_SHA`, and `NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT` on a private
persistent volume shared by every generation replica.

The quality service also requires `QUALITY_BANNER_STORE_ID`,
`QUALITY_BANNER_SPEND_POLICY_SHA256`, `QUALITY_BANNER_BUDGET_MODE` equal to
`authenticated_provider_estimate@1`, `QUALITY_BANNER_PROVIDER_ACCOUNT_SHA256`,
`QUALITY_BANNER_DAILY_REQUEST_LIMIT` (1–3
aggregate initial attempts; at most one/client/KST day),
`QUALITY_BANNER_LOGO_REFERENCES` (trusted client-to-public-URL/SHA manifest),
`QUALITY_BANNER_REFERENCE_HOSTS`, `QUALITY_BANNER_OUTPUT_HOSTS`, and dedicated
`HIGGSFIELD_API_KEY_ID` / `HIGGSFIELD_API_KEY_SECRET`. Never print these secrets.

The approved estimate-policy SHA-256 is
`000a4243bf92c082db1120b6ccbd6aa8e2ade95ed355aaef124f79a31c6aa0a4`.
It does not authorize a different provider account or replacement ledger.

`provision_quality_news_store(root, workspace_id=..., store_id=...,
provider_account_sha256=...)` is an
explicit one-time provisioning helper, never called by HTTP startup; an existing
path is refused. Both quality and legacy requests also require a preprovisioned
v2 shared request-mode journal. Runtime never silently upgrades old schema or
constructs an empty replacement. Existing v1 journals require separate offline
reconciliation, not a fresh ledger that discards their attempted requests.
Production store provisioning needs separate authorization.
UUID outputs are excluded from old timestamp-directory cleanup; a journal-aware
GC/backup policy remains required before claiming durable hosted operation.

The gateway always forwards the natural request UUID. If server idempotency is
OFF, a request carrying any Idempotency-Key is held before journal/provider I/O;
it cannot silently fall back to untracked legacy generation. Only headerless
legacy callers retain the old OFF behavior. Roll out/provision the shared v2
journal and enable its guard before resuming the hosted natural workflow.

Private courier flags, staff roles, destination policy and schedule are
unchanged. Enabling generation does not enable staff delivery or public sending.

## Remaining operational gates

1. Review the exact SQL proposal/ACL, immutable public logo/reference mappings
   and persistent-volume plan; build/deploy compatible releases OFF under
   explicit authorization. No historical migration was rewritten/applied here.
2. Attest the implemented USD10 aggregate KST-day estimate ledger. The existing
   09:00 KST daily timing is retained as the rollout target, at most one initial
   source/client/day. Request limits and a spend-plan digest are not dollar
   enforcement. Production must use authenticated same-payload estimates and
   the shared persistent ledger, not a static environment price. Actual charges
   may differ as accepted by the user; report estimated/provisional and actual
   amounts separately. Old strict/v1 ledgers require offline reconciliation.
3. Run one authorized private canary and read back actual arrival/controls.
   Fixture success is not proof of live image quality, staff registration,
   permissions, deployment or delivery.
4. Complete distinct quality regeneration/revision admission after staff edits
   and deploy the locally composed final-card preparation runtime before
   enabling those paths. Its human-check intent and Engine worker are tested
   locally, not attested in the hosted runtime. This initial card connection
   does not finish them. Typefully remains private draft-only.

The initial route requires an official image; text-only posts do not fall back
to invented artwork. Existing natural retries are bounded. If a provider stays
pending past that budget, reconcile its recorded request instead of inventing
a new natural job. Mutable brand guidance can cause a safe input-conflict hold
on retry; only the original checkpoint may be resumed, never regenerated.

## Offline proof

- Python service tests: all three clients, restart with no HTTP, pending GET
  recovery, lost/wrong volume, input drift, unknown outcomes, pre-copy freshness,
  mocked SDK exactly-one-POST timeout, and native banner -> existing four-part
  private card with synthetic owner receipts.
- Immutable quality/legacy mode pinning, opposite-mode concurrency, disabled
  guard refusal, and private source/style/guidance rejection before copy work.
- Netlify tests: full mocked handler and catalog replay, exact identity,
  real opaque native2880 PNG larger than5MB, no base64 response, bad output
  rejected before persistence, server-only context, legacy compatibility.
- `node scripts/verify_quality_banner_context_local.mjs --local-only` creates
  a disposable socket-only PostgreSQL16 fixture. Three clients × nine rejection
  cases, actual natural job completion and existing review-queue reconciliation
  produce one review row with no extra jobs/approvals/publications. It stops its
  server/removes its socket. It never accepts a production DSN or credentials.

No live provider, Telegram, Typefully, production DB or deployment call is part
of these tests. A queue receipt is not a delivery receipt.
