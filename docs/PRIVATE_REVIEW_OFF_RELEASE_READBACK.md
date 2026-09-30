# Private-review OFF release readback

Status: runtime-context correction prepared for a Draft PR. Local and CI
evidence does not fix or attest production. Merge, another deployment,
configuration changes, enablement, database actions and sends are not included.

## Purpose

The existing review mutation gateway deliberately rejects requests while OFF,
before it authenticates or reports a release. The Studio capabilities route
requires a different automation credential. Neither response proves an
authenticated courier release readback while OFF.

The isolated `content-ops-review-release` function reuses the existing dedicated
`CONTENT_OPS_GATEWAY_TOKEN`. It creates no credential, and does not accept a
Studio key, cookie, Telegram token or another configured principal as a fallback.
The shared authentication helper preserves the mutation gateway's existing
token format, principal-separation checks and timing-safe comparison.

## Request contract

- Method: `GET` only.
- Origin: `https://coineasy-newscard.netlify.app` only.
- Path: `/.netlify/functions/content-ops-review-release` exactly.
- Netlify native handler context: `context.deploy.context === "production"`,
  `context.deploy.published === true` and a nonempty, unpadded deployment ID.
  Preview, branch, unpublished and missing/malformed deployment metadata are
  rejected before credentials, release stamps or flags are read.
- `Authorization`: the existing dedicated gateway bearer token.
- `X-Content-Ops-Expected-Release-Sha`: the separately approved exact lowercase
  40-character SHA, supplied from trusted release evidence.
- No query parameters, request body, action, content ID, workspace or URL.

Never put the bearer token in a URL, browser address bar, shell history, saved
receipt or test fixture. A readback client must not follow redirects or retry
through the mutation gateway. Store only the bounded response and its observed
time in a receipt, not request headers.

After authentication, the caller's expected SHA and
`CONTENT_OPS_REVIEW_RELEASE_SHA` must both equal the immutable generated build
stamp returned by `currentStudioReleaseSha()`. A runtime variable cannot replace
that stamp. Missing, malformed or mismatched stamps fail closed.

All three Netlify flags must be explicitly `false`:

- `CONTENT_OPS_GATEWAY_ENABLED`
- `CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED`
- `STUDIO_TELEGRAM_PUBLISH_ENABLED`

Missing, invalid or ON values are not evidence of OFF and return an error.
No flag is changed by this request.

## Bounded response

The success response contains only:

```json
{
  "schema_version": "content-ops-release-readback@1",
  "ok": true,
  "netlify_release_sha": "<exact lowercase 40-character build SHA>",
  "gateway_enabled": false,
  "button_gateway_enabled": false,
  "public_telegram_publish_enabled": false
}
```

All responses use `no-store` for browser and Netlify/CDN caches, vary on
Authorization, and set `nosniff`. The route sets no cookie or CORS permission.
Unauthenticated requests receive no release or flag values. Unexpected failures
return a fixed redacted error rather than exception/configuration details.

Deployment metadata comes exclusively from Netlify's native second handler
argument. No environment variable, request header, query parameter or forwarded
host can supply or override it. In particular, `CONTEXT` is not consulted:
Netlify does not guarantee that build variable in the Functions runtime.
The wrapper forwards native context without loading an SDK or making a request;
its SDK imports are type-only and erased from the bundle.

References: [Functions context API](https://docs.netlify.com/build/functions/api/)
and [Functions environment variables](https://docs.netlify.com/build/functions/environment-variables/).

The handler has only environment-reader and build-stamp dependencies. It has
no catalog, RPC, database, fetcher, provider or sender dependency. Successful
readback does not grant a claim, begin, owner action, image access or send;
the existing mutation gateway still rejects all of those while OFF.

## Evidence boundaries and tests

This response attests only the contacted Netlify function's build stamp and
three configured OFF flags at request time. It does **not** establish Railway
runtime provenance, callback-owner state, hosted database health, source
freshness, candidate eligibility, active-worker quiescence, end-to-end button
behavior or permission to publish. It is not a distributed runtime lock.

`tests_js/content-ops-review-release.test.mts` covers authentication and token
separation, exact host/path/context, rejected methods and payloads, three-way
SHA equality, explicit OFF flags, redaction/cache headers, dependency isolation
and the unchanged mutation rejection after successful readback. Its adapter
test checks native-context forwarding and the actual generated build stamp.
Regression cases include absent `CONTEXT` with valid published production
metadata, conflicting build env, missing native metadata, unpublished deploys
and caller-forged context/forwarded-host headers. The existing CI build-stamp step
also runs this test with `EXPECTED_STUDIO_RELEASE_SHA` after the offline build.

Local/mock and CI results are not production receipts. A future separately
authorized OFF deployment must be read back using its actual approved merge
SHA and existing gateway credential before claiming authenticated production
release proof. No live request is part of this local implementation.

## Earlier production acceptance — 2026-09-30

The separately authorized OFF deployment of
`b50bc769b9689b7855cab29a092bd517d724cc1c` reached published production, but
release GET probes (valid, absent and invalid credentials) all returned the
same bounded `421 content_ops_production_host_required` error. The owner API
showed no custom `CONTEXT` variable. A network-free reproduction returned 421
with absent `CONTEXT` and 200 with a fixture production value.

Those observations and the documented runtime API support correcting the
unsupported build-env dependency. The live error did not expose which host,
path or runtime-context predicate failed; it is not individual predicate
telemetry. No guard is removed, no `CONTEXT` variable is added, and the existing
mutation gateway, credentials, three-way SHA fence and OFF flags are unchanged.

This patch has not been deployed. The earlier acceptance remains BLOCK until
a separately approved release is deployed and authenticated 200 plus negative
401 readbacks are collected. Do not treat local or CI success as live recovery.

## Runtime-context correction validation — 2026-09-30

- Focused release-readback regression tests: 15 passed.
- Full JavaScript suite: 526 passed, 3 skipped, 0 failed. Local HTTP test
  fixtures required loopback-listener permission; the sandbox-only run's
  `listen EPERM` was an environment restriction, not a passing test result.
- Relevant worker/card gateway/owner/runtime/bot-policy tests: 278 passed.
- Full Python suite in the existing dependency-complete Python 3.12 environment:
  4,384 passed, 2 existing FastAPI deprecation warnings. The minimal test venv
  initially lacked application dependencies; no production configuration or
  repository dependencies were changed to address that local limitation.
- `git diff --check`: passed.

The offline Netlify bundle and post-build generated-stamp checks must also run
on the committed patch head. Their result and GitHub CI/Netlify skip receipts
belong to the Draft PR, not to production release acceptance.

## Prior implementation validation — 2026-09-30

- Full `npm run test:functions`: 523 passed, 3 skipped, 0 failed.
- Existing worker, private-card gateway, owner gateway, runtime and bot-policy
  Python regression tests: 248 passed.
- `netlify build --offline --context production`: passed; the new function was
  included in the function bundle. Nothing was deployed.
- Post-build release-stamp, capabilities and release-readback tests: 16 passed.
- `git diff --check`: passed.

The local Netlify CLI stamped Git HEAD
`67c9e099251011269aa313079d44fb2106d99337`, not the supplied synthetic
`COMMIT_REF`. The initial synthetic expectation was correctly rejected; the
post-build check then verified the generated stamp against that actual HEAD
without changing the assertions. These are uncommitted local changes on that
base, so this local bundle is **not** an exact committed-release or production
provenance receipt. GitHub CI has not been run for this change.
