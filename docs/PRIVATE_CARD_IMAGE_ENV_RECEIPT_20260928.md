# Private-card OFF deployment and image environment correction

## Observed production result

- Service: `coineasy-private-review-card` in `noble-illumination` production.
- GitHub commit and configured release: `4964205397ab5bc2d5cd52c7c7bae0fb25a40d72`.
- Deployment: `6fdb4c92-9cf2-4cb7-a431-19d3fddcbfda`, created at
  `2026-09-28T10:17:51.806Z`; status `FAILED` in pre-deploy validation.
- The build log shows the exact commit written to `/app/content-ops-build-sha`.
- At `2026-09-28T10:18:23.214437296Z`, the application returned
  `private_card_canary_failed` with `network_calls=false`, `database_calls=false`,
  and `telegram_calls=false`.
- Both review feature flags and auto-deploy were OFF. The service had no cron,
  no public domain, and no bot, gateway, provider, or database credentials.
- One deployment was requested. There was no retry, enablement, or send.

## Reproduced cause

The deployment snapshot contained only the six intended configuration variables,
including the new release SHA and both OFF flags. Runtime Git SHA was absent,
which the merged OFF-only check permits.

The build used `python:3.12-slim` at digest
`sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`.
Its image configuration includes a nonempty `GPG_KEY` containing Python's public
release-signing fingerprint. That inherited variable matches the runtime's
generic `*_KEY` credential-denial rule.

A local replay of the exact deployment snapshot combined with the registry's
image environments reproduced `private_card_credential_boundary_invalid` for
all eight returned platform variants. Clearing only the inherited `GPG_KEY`
made OFF validation pass. This replay is function-level evidence, not an
execution of the production container.

The prior image CI passed a hand-built environment dictionary, so it did not
exercise inherited image variables.

## Correction and verification boundary

The private-card Dockerfile clears the unused inherited `GPG_KEY`. Runtime
credential checks are unchanged: any nonempty value injected at launch is still
rejected in OFF validation and enabled mode.

CI now runs the actual CLI `--validate-only` with the container's environment,
without network access, and starts the other credential-boundary checks from
`os.environ`. It also explicitly rejects an injected `GPG_KEY`.

Local Docker execution was unavailable because the daemon was not running.
The isolated image CI must pass before merge. A production success receipt is
still required after a separately authorized deployment of the corrected commit.
