# Reference-first daily banner quality

Status: **LOCAL OPT-IN INTEGRATION / NOT DEPLOYED / NO PROVIDER CALL / NO SEND**.

The natural gateway/server and initial staff-card connection now exists locally.
See [the current integration](QUALITY_NEWS_TO_STAFF_CARD.md).

`core.automation.higgsfield_banner` captures the accepted three-client visual
direction without embedding dated facts, private reference files or staff-room
identifiers. It supports Yellow, Babylon and OriginTrail; Squid's source-native
contract is unchanged. Quality OFF never silently downgrades a pinned quality
attempt. Natural gateway requests require the shared idempotency guard; only
headerless legacy callers retain their old behavior with that guard OFF.

The policy is `official-reference-sunburst-max@2`: Higgsfield
`marketing-studio/image/sunburst`, max quality, requested 4k, square, prompt
enhancement off. Two hash-bound owner-staged **immutable public** references are
required: the current official creative and verified official logo. Do not
publish private staff uploads merely to satisfy the URL interface. Reference
staging must verify bytes, public ownership and immutable storage separately.
The adapter downloads, decodes and verifies both hashes before paid submission;
it cannot prove that the remote provider later read those exact bytes.

The prompt preserves client artwork/logo identity while requiring short Korean
copy, spacious leading, one recognizable focal visual and large mobile text.
Only source-checked headline/subtitle are printable. Feedback is untrusted
design data and cannot add facts. The adapter does not replace source or human
claim checks, and an image returned by an API is not a quality approval.

The 2026-10-12 designer feedback is now a digest-bound client policy: preserve
Yellow artwork and headline leading; use natural compact OriginTrail headline
tracking (not expanded spacing); use semibold rather than extra-bold Babylon
headlines. No new image has been generated or visually accepted by this change.

## Request and output contract

Official documentation checked 2026-10-12:

- [Sunburst API reference](https://open.higgsfield.ai/models/marketing-studio/image/sunburst/api-reference)
- [Quickstart](https://docs.higgsfield.ai/docs/quickstart)
- [Request lifecycle](https://docs.higgsfield.ai/docs/concepts/requests)
- [Polling](https://docs.higgsfield.ai/docs/concepts/polling)

The API supports the selected max/4k/square settings. Submit is an authenticated
POST; completion is asynchronous. The returned status URL is accepted only if
it exactly matches the documented API host and request ID path. No arbitrary
URL receives provider credentials. Output download has no authorization header,
uses an exact trusted host allowlist and refuses redirects. Neither SDK retries
nor environment proxy/endpoint overrides are used.

There is exactly one submit attempt after a durable owner reservation. Unknown
POST or commit acknowledgment is held, never submitted again. The provider
Idempotency-Key is defense-in-depth, not permission for an automatic retry.
`collect()` reads only a previously committed request ID; it makes bounded status
GETs and no submission/cancellation/upload. It can be called again for GET-only
reconciliation. A pending/failed/unknown response is not completion.
Source age is enforced before a paid submit. A source that expires afterward
can still be reconciled using its recorded request ID and staged locally;
staging is not admission. The content owner must check current/latest/fresh
eligibility again before installing or exposing it for approval/publication.

Provider output is bounded and decoded as a single image, not trusted by its
filename or response MIME. A square native result of 2880–4096 pixels is
required. PNG/WebP/JPEG is packaged as opaque RGB PNG without resizing/upscaling
or hiding transparency. The canonical PNG is at most 10 MB, matching the staged
result owner. The request says 4k, but actual output dimensions must
be measured; the previously accepted sample was 2880 square. The durable owner
stages output bytes/hash; this does not install an immutable content version,
approve it or publish it.

## Original integration gap (closed locally, not deployed)

The former Netlify news-card handler checked catalog idempotency before calling
Railway but did not persist a provider intent before that call. It did not
forward the natural request UUID to `api.server.generate_news`; the server
created a fresh time-based output directory. Natural automation treats transport
timeouts and 502/504 errors as retryable, before an immutable catalog version
may exist. Adding an asynchronous paid renderer there would permit another paid
submission after an uncertain first outcome.

The new opt-in path propagates the original job/request/source identity across
that boundary, reserves its durable attempt before provider IO,
stores the provider request ID before polling, and recovers only the same staged
result. Its owner for each unique workspace/client/source must survive restart
and prevent a different operation ID from evading deduplication. Budget policy
and source/reference registrations are trusted-owner inputs, not request JSON.

The local `core.content_ops.higgsfield_banner_journal.HiggsfieldBannerJournal`
implements the injected four-method attempt interface for an initial daily
banner. A trusted composer pins workspace/client/source when constructing it.
The immutable request also contains UUID workspace/source identities; its
digest covers those fields and client identity. The adapter passes that exact
scope to `begin_submit`, which refuses any mismatch before reserving a row or
daily quota. Reconciliation requires the committed digest and owner's fixed
scope, so changing scope cannot retrieve or reuse another request's provider ID.
All workers must use one shared private persistent database, not independent
replica disks. One source remains single-attempt across dates or changed UUIDs;
an explicit daily aggregate request limit is not a dollar spending cap. This
local journal is not a deployed owner, credential configuration, monetary
enforcement or a source/reference-registration authority. The user has now
approved USD10/day combined **on authenticated estimated prices**, explicitly
accepting actual settlement variance. The quality owner reserves same-payload
account-bound estimates atomically in its shared ledger. Completed results
remain provisional until exact final billing is known; unknown requests retain
their estimate across KST days. The environment factory requires this exact
policy and a preprovisioned account-bound store. Hosted activation is separate.
Banner revisions
still require their distinct authenticated edit owner.

The final stored PNG must pass the existing natural completion, source-link,
current-version, canonical Storage path and daily-slot contracts. A manual
banner or edited version needs a separate explicit revision admission path;
do not fake a succeeded natural job. Existing retry defaults are not changed.

## Offline verification

`python -m pytest tests/test_higgsfield_banner.py tests/test_higgsfield_banner_journal.py -q`

Earlier focused receipt (2026-10-12): **55 passed**. This includes concrete journal
integration and explicit scope-mismatch regressions; all provider requests used
mocked HTTP.

Later integration/guard receipts are recorded in
[the validation log](QUALITY_FIRST_VALIDATION_20261012.md).

Tests use synthetic references, an injected owner and mocked HTTP only. They
cover immutable policy/digest, PNG normalization without enlargement, source
age/client restrictions, bounded polling, output credential isolation, invalid
URLs, failed provider states and no repeated POST after unknown outcomes.
Concrete SQLite-owner integration also checks restart-to-GET-only recovery and
the inability to evade source deduplication with a new operation UUID. Scope
mismatch tests require zero provider POSTs and zero consumed reservations;
matching scope still succeeds after a rejected mismatched request.
They do not establish hosted credentials, spending limits, provider quality,
schema compatibility, live delivery or accepted staff approval buttons.
