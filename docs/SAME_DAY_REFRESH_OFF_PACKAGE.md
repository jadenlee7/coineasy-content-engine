# Dedicated same-day refresh OFF package

Status: local/CI packaging only. No service is created or deployed by these files.

## Purpose and boundaries

The same-day refresh ledger and manual CLI were merged in PR #203. Existing
`Dockerfile.automation` starts the natural worker and its profile has a 15-minute
cron; the refresh OFF flag does not disable that natural workflow. The private
card image does not contain the refresh runner. Neither is repurposed here.

`Dockerfile.same-day-refresh` packages only the refresh CLI and its Python core
dependencies. It has no HTTP server, publication route, natural worker script,
or scheduler. The image runs as UID/GID 10001 and defaults to explicit OFF.

`railway.same-day-refresh.json` starts and pre-validates configuration only,
uses restart `NEVER`, and declares no cron. On a future **new dedicated service**,
independently verify `cronSchedule=null` and owner `autoDeploy=false`. The JSON
profile cannot attest those owner settings and does not clear an existing
service's inherited cron. Do not attach it to the natural worker or courier.

## Zero-external-I/O runtime validation

The image's default command is:

```text
python -m scripts.run_official_x_same_day_refresh --validate-runtime-only
```

It accepts no business subcommand or candidate IDs. No manifest clock, DB/HTTP
client, provider, queue, claim, generation, reconciliation, or sender is created.
It validates only:

- `OFFICIAL_X_SAME_DAY_REFRESH_ENABLED` is exactly `false`.
- Configured `OFFICIAL_X_SAME_DAY_REFRESH_RELEASE_SHA`, native
  `RAILWAY_GIT_COMMIT_SHA`, and the image stamp are the same canonical lowercase
  40-character SHA.
- `CONTENT_STUDIO_WORKSPACE_ID` is a canonical non-nil UUID of a supported version.
  This is syntax validation, **not** proof that the workspace exists or is authorized.
- Unexpected bot/provider/cloud/DB/proxy/CA credential names are absent. Only
  the future service connection names `SUPABASE_SERVICE_ROLE_KEY` and
  `STUDIO_AUTOMATION_TOKEN` are allowed; their values are not read by this mode.
  Python base-image `GPG_KEY` metadata is the narrow exception: it must be empty.
  This is not credential validity, provider authentication, or permission proof.

Missing or mismatched configuration exits nonzero with a bounded generic
failure receipt and zero-I/O fields. An unconfigured default startup therefore
fails closed; a bare no-args legacy CLI success is not used as image proof.

## Image and origin evidence

Only native build ARG `RAILWAY_GIT_COMMIT_SHA` may populate
`/app/same-day-refresh-build-sha`. The build rejects missing/malformed values.
The stamp is exactly 40 lowercase hex bytes plus LF, root-owned and non-writable.
The reader uses a fixed path, no-follow open, regular-file/owner/mode checks,
and a bounded read. Caller arguments or environment variables cannot redirect it.

The runtime-only receipt reports `image_stamp_matches=true` but deliberately
keeps `image_attestation=false` and `hosted_provenance_verified=false`. Equality
of local inputs cannot independently prove GitHub origin or deployed image
digest. Future deployment verification must correlate exact owner deployment
ID, GitHub commit metadata/build log, image digest/stamp, and native runtime SHA.
CI uses synthetic stamps and is explicitly not hosted production proof.

The existing `--validate-only` remains candidate-manifest validation with
`image_attestation=false`. Enabled queue/generate/inspect operations now require
the fixed image/config/native fence and credential-name boundary before runner
construction, in addition to the existing exact manifest/day/expiry guards.
They remain separately approved business operations, not smoke tests.

## Rollout and execution are independent

This OFF process exits after bounded validation; it is not an always-running
remote execution service. The profile intentionally cannot run a business
operation just because a flag is toggled. Future one-shot execution needs a
separately reviewed launch/pre-deploy policy and exact IDs; this change does not
add remote orchestration, an HTTP generation endpoint, or permission to run it.

Before any future generation, independently verify authenticated Netlify
`/api/studio-capabilities` reports the same expected release as this runner.
That equality still does not attest the upstream Railway generation API SHA.
Netlify's new build stamp also changes Content Ops release fences: coordinate
any separately approved release-variable changes and report old OFF couriers'
temporary mismatch rather than automatically redeploying them.

Keep unknown acknowledgements non-retryable until durable state is inspected.
No new UUID, restart, or another launch bypasses a consumed refresh claim.
The refresh lane still has **no employee-room courier/outbox/button integration**
and does not permit public Telegram, Typefully, or X publication.

The source migration `20261005030000_official_x_same_day_refresh.sql` was recorded
on production on 2026-10-05 under provider-assigned version `20261005034843`, with
the exact source SHA-256 `6f1f5e8530ab0abc3278d5831385c6ee69292cbc4c3a48151eea4ac39978572a`.
This is a dated application receipt, not a fresh DB observation here. This PR
adds no migration, grants, history repair, or business rows; do not reapply that
same SQL to reconcile the source/provider timestamp difference.

## Verification

- Python guards cover explicit OFF, mode conflicts, missing/mismatched/malformed
  release values, workspace syntax, forbidden credential names without value
  retrieval, fixed/bounded/no-follow stamp reads, and pre-runner business fencing.
- Static package guards check native ARG only, non-root/default validation,
  no scheduled/server entrypoint, and no imaginary autoDeploy JSON field.
- Isolated image CI uses `--network none --read-only --cap-drop ALL` and
  `no-new-privileges`; it checks positive and missing/malformed build provenance,
  stamp ownership/mode, configured OFF success, unconfigured failure, wrong
  native/config SHA, ON refusal, and synthetic forbidden credentials.
- No production variables, deployments, enablement, generation, or sends are
  part of local/CI acceptance.
