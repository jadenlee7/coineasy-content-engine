# Private-card OFF runtime provenance receipt

Code-preparation contract, 2026-10-01 KST. This change does not deploy, enable,
select a canary version, modify production settings or send anything.

## Evidence gap

The OFF `--validate-only` path permits a missing `RAILWAY_GIT_COMMIT_SHA` as long
as the immutable image stamp matches `CONTENT_OPS_REVIEW_RELEASE_SHA` and the
private scope/credential boundary passes. Its previous success output could
not distinguish that bootstrap result from verified runtime Git provenance.
Owner configuration, a deployment snapshot, GitHub metadata and build logs are
not observations of the actual process environment. A stopped one-shot
container cannot supply a new live process readback without a separately
authorized execution/deployment.

## Bounded OFF success output

The existing success and zero-I/O fields are preserved. One nested `provenance`
object is added only to successful OFF validate-only output:

```json
{
  "ok": true,
  "mode": "validate_only",
  "enabled": false,
  "network_calls": false,
  "database_calls": false,
  "telegram_calls": false,
  "provenance": {
    "schema_version": "private-card-runtime-provenance@1",
    "build_release_verified": true,
    "runtime_git_sha_state": "missing",
    "runtime_release_verified": false
  }
}
```

- `missing`: the process native Git variable is absent/empty. OFF preflight
  success remains permitted, but independent runtime provenance is unverified.
- `match`: the process native Git variable is a lowercase exact 40-character
  SHA matching the configured release and immutable stamp. Only this state sets
  `runtime_release_verified=true`; the overall feature is still OFF.
- Malformed/non-string/stale runtime SHA, a bad/unreadable stamp, release
  mismatch, invalid scope or forbidden credentials still produce the existing
  redacted failure with no provenance success object. Failure status/exit codes
  are unchanged; no raw malformed values or exception details are emitted.

The receipt is derived from the same single stamp/runtime observation as the
validation, not a second read. Existing SHA-only validator consumers are
compatible. No raw SHA, token, key, private identity, policy, content, URL or
whole environment is added to the output. There is no image-ENV/manual/legacy
SHA fallback and no new flag, dependency, credential or client construction.

## Unchanged gates and rollout boundary

Disabled normal execution still reads only the OFF flag and exits without
credentials or provenance reads. Enabled settings still require independent
matching runtime Git SHA plus an exact immutable canary version, valid private
destination and distinct keys/tokens. Enabled validation and normal-run output
contracts are unchanged. All validate-only paths remain free of application
network, DB and Telegram calls.

Tests and network-isolated image CI distinguish actual synthetic container
runtime `missing`/`match` receipts and reject bad provenance. They do not prove
Railway production provenance. A future production receipt requires separate
merge/deployment authorization, an exact approved release and process-generated
readback. Even a matching production provenance receipt is not authorization
to claim, enable, dispatch or publish; current-version/source/asset/deduplication
checks and an exact-version private canary authorization remain separate gates.
