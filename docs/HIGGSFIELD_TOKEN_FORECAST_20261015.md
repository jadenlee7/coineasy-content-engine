# Explicit local token-rate forecast and same-store transition

The operator approved the explicit forecast policy, a history-preserving
same-store transition, CI-passing `[skip netlify]` squash merge, and a separately
bounded staff-only trial. This code does not itself migrate a live ledger,
deploy, enable generation, send a staff card, or publish publicly. Default-OFF
remains zero-I/O; production actions require their own exact receipts.

## Why the existing request stopped

The authenticated, non-generation estimate response for the selected model was
HTTP 200 with string fields `type` and `pricing_description`, not a numeric
`usd` field. The current quote adapter correctly rejects that shape. A synthetic
regression fixture preserves the observed field names/types, not private API
data or an assumed value for `type`. It still cannot silently activate a local
forecast when a provider quote is absent or malformed.

## Evidence versus assumptions

Public model-specific source:
[Sunburst API reference](https://open.higgsfield.ai/models/marketing-studio/image/sunburst/api-reference).
Read 2026-10-15T09:15:36.269Z, HTTP 200; document SHA-256:
`3ce095632b8178cc4edeee0a4fc3fe76b90e3f741e5d305bb9e229a3bd271869`.

The documented uncached rates are USD5/text-input, USD10/text-output,
USD8/image-input and USD30/image-output per million tokens. Cached-input
discounts are **not** assumed. Final cost depends on actual usage and credit
rounding; a generation completion is not a billing receipt. The generic
[billing example](https://docs.higgsfield.ai/docs/concepts/billing-and-retention)
is not a quote for this max-quality request.

The reference does **not** establish maximum token counts for our request. The
following allocations are an approved conservative **operator budgeting policy**,
not provider limits, measured usage, a tokenizer model, or a guaranteed upper
bound. They have not been calibrated against live Sunburst billing yet.

| Component | Assumed tokens | USD per 1M | Forecast USD, two references |
|---|---:|---:|---:|
| Text input | 16,384 | 5 | 0.081920 |
| Image input | 16,384 per reference | 8 | 0.262144 |
| Text output | 8,192 | 10 | 0.081920 |
| Image output | 65,536 | 30 | 1.966080 |
| Subtotal | | | 2.392064 |
| With 50% contingency | | | **3.588096** |

Logo-only forecast: **USD3.391488**. The existing quality remains `max`, requested
`4k`, square, prompt enhancement off. No provider token-limit parameter is added.
Prompts exceeding 16,384 UTF-8 bytes are rejected, rather than truncated. This
byte guard is a request envelope, not a claim about actual tokenization.

The pinned rate evidence requires review after 30 days. This is an operator
freshness limit, not a claim that provider prices cannot change within it.
Any changed rates/allocations/settings need a new policy digest and review.

**USD10 is an estimated image-spend admission limit, not an actual-charge cap.**
Separate text-copy generation and other service fees are not counted by this
image ledger. Actual charges can exceed forecasts, as under the existing
estimate policy; genuine billing receipts replace provisional amounts.

Three full forecasts would exceed USD10, so the initial un-settled allowance
admits at most two such images in total, not three guaranteed images. A third
may fit only if verified settlements reduce the total sufficiently. Each client
still has at most one initial request per KST day; no smaller estimate, cached
discount, quality downgrade or ledger reset is used to force admission.

## Implementation and boundaries

- New explicit mode: `local_token_rate_forecast@1`. A distinct
  `TokenRateForecastProof` cannot be used in strict-ceiling or authenticated-USD
  quote mode, and mixed proof types fail closed.
- Forecast policy SHA-256:
  `2f71160c21ebb2b40d000ccf6cf6d36553c0039766416d52e89d3ab0af653982`.
  This is not a production approval or a trial-plan digest.
- `FORECAST_POLICY_SHA256` binds the evidence, rates, allocations, margin,
  model/settings, daily limit, timezone and one-per-client scope. Calculation
  uses integer micro-dollars with upward rounding, never binary floats.
- The proof includes request/account/payload digests and a sanitized canonical
  calculation record, not copy, media URLs, credentials, or provider bodies.
  A digest is not provider authentication. The evidence is honestly local.
- Integration uses **no estimate HTTP request** and has no quote-error fallback.
  The environment factory accepts only the exact mode/digest pair and an
  already transitioned, account-bound store. Existing quote-mode stores must
  also match their selected mode. Startup never provisions or migrates a store.
- A read-only budget precheck prevents a knowingly underfunded new paid-copy
  attempt. The existing authoritative reservation plus `submit_started` commit
  still occurs atomically before the image POST, protecting concurrent workers.
- Reservation, sanitized forecast evidence and submit intent commit or roll back
  together. Unknown liabilities survive restarts and KST midnight. Existing
  request/source uniqueness and checkpoint/release/policy binding are unchanged.
- No existing store is re-provisioned or implicitly migrated. Paid unknown work
  cannot be retried with a new estimate, UUID, source, owner or new policy.

## Offline verification

The tests exercise the observed missing-USD response, no fallback, exact math,
rate freshness, quality/request envelope, proof binding, combined USD10 limit,
concurrency, rollback, midnight/restart liability, settlement and environment
selection/rejection. Provider and source calls in integration tests use `httpx.MockTransport`.
Fixture banners are synthetic PNGs, not evidence of live creative quality.

No fresh provider estimate, paid generation, Telegram or public posting request
is used to validate this change. Production state/source checks are read-only
and separate from these fixture tests.

Verification observed by 2026-10-15T09:27:42Z:

- Full Python suite: **5,614 passed**, five dependency/lifecycle deprecation
  warnings (local Python 3.11; hosted CI uses Python 3.12 and was not invoked).
- Relevant JavaScript: **92 passed**, zero failures/skips (quality-banner,
  brand-quality, news-card-trial, ybo-trial-preflight).
- `git diff --check`: passed.
- Independent code review: no blocking finding; forecast-versus-cap caveat
  retained. Live quality, billing, deployment and delivery are **not** established
  by these local tests. No commit, push, merge or production change performed.

Updated implementation verification on 2026-10-15 (factory + transition +
operator included): **5,708 Python tests passed**, five existing deprecation
warnings; full JavaScript **735 passed / 3 skipped / 0 failed**. The initial
sandboxed JavaScript run could not bind localhost test servers; the rerun with
local-server permission passed. Independent operator safety review found no
blocking defect. These are local receipts; hosted CI and production evidence
must be checked separately after the PR is created.

## Same-store transition and remaining production work

`core/content_ops/token_forecast_transition.py` plans without writes, then
requires a caller-owned transaction and exact fresh state/plan digest. It changes
only the policy selector, creates empty forecast evidence, and appends an audit.
All existing reservations, pending liability, copy checkpoints, image attempts
and optional tables are hash-bound and preserved. Schema/row drift, wrong
account, orphan paid intent and reapplication fail closed. A new empty ledger
is not an acceptable transition.

The old consumed one-shot trial remains terminal; policy work does not revive
it. On the 2026-10-15 read-only reconciliation, Babylon's latest source still
belongs to the previous expired running job and its immutable copy checkpoint;
OriginTrail's newest source is older than 24 hours. A new UUID/window alone
cannot resume that Babylon job. Any exact-job handoff must separately preserve
and validate its checkpoint, request, source and delivery history; no reset,
new request ID, or unknown paid/send retry is permitted by this transition.

Only after that plan, CI and explicit rollout authority may production mount
the new policy and authorize a new bounded test. Keep official publication off;
staff review and version-bound human approval remain separate gates.

The operator command is `python -m scripts.transition_quality_forecast` with
`plan`, `apply`, or `readback`, an exact `--release-sha`, and (apply only) the
top-level `--plan-sha256` returned by plan. CLI paths are fixed to the existing
private mounted volume. It verifies the immutable image stamp, runtime SHA,
OFF flags, store/account identity, and every untouched sibling file. Apply
uses `BEGIN IMMEDIATE`, rechecks the plan, verifies historical rows/schema and
unchanged budget before commit. Readback constructs the forecast factory using
an in-memory validation environment while the actual runtime stays OFF; a
scoped audit hook forbids network use. The operator does not enable or retry
anything, and its output contains hashes/counts rather than private copy.
