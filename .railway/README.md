# Railway Infrastructure as Code

`.railway/railway.ts` is the named Railway IaC partial for the two services
this repository owns directly:

- `coineasy-content-engine`
- `coineasy-managed-inspect`

The partial fails closed unless Railway supplies the exact production project
and environment IDs and names recorded in the file and evaluates it as a
`plan`. It owns exactly those two service resources. Do not replace it with a
whole-project import: the Railway project also contains resources owned by
other repositories, and omitted resources can become delete intent.

The one-time adoption plan was applied to production on 2026-09-04 from commit
`784249900491a4fb99b152fbcf73e34511c05a93`. It is complete and must not be
replayed. Merge, future infrastructure changes, deployment, enablement, and
delivery remain independent approval gates.

## Owner-settings convergence authority

Railway CLI `5.45.5` builds the IaC current graph from `environment.config`.
That read model omits the two services' restart-policy fields even though the
owner `serviceInstance` API returns their materialized values. A new plan can
therefore repeat `null -> desired` restart changes after a successful apply.
That repeated diff is not evidence that production is unapplied, and submitting
it again can cause redundant configuration and deployment churn.

Use the owner API receipt as the authority for the current settings touched by
the one-time adoption plan. The checked-in GraphQL document requests only
project/environment identity and allow-listed service settings. It does not
request variables, logs, deployment metadata, resolved file configuration, or
secret values. Pipe the raw response directly to the validator so it is never
printed or stored:

```bash
set -euo pipefail
test "$(railway --version)" = "railway 5.45.5"
railway api --file scripts/railway_iac_owner_receipt.graphql \
  --raw-var projectId=43f15c45-4a5c-4cf9-9400-e462cac46bb1 \
  --raw-var environmentId=5bf47282-1982-4930-95ad-29230ec0429b \
  --raw-var webServiceId=80168b5d-54f5-4684-ab32-d5f3c4f8e483 \
  --raw-var managedServiceId=b1ab4f39-982f-4c33-9402-b0bc843aac2f \
  --compact \
  | node scripts/validate_railway_iac_owner_receipt.mjs
```

The only successful state is `owner_settings_converged`, scoped to
`one_time_adoption_settings`, with action `stop_no_apply`. It proves the exact
web build/health/start/restart settings and managed-service restart settings
touched by that plan, plus both services' config-file/Dockerfile pairing. It is
not provenance for who applied those settings and is not a complete live-state
audit of source, replicas, runtime, watch patterns, networking, domains, or
variable-name inventory. Those remain offline contract checks or separate
owner-system rollout gates. Any command, schema, target, selected setting, or
response-shape drift is `BLOCK`. A failed or unreadable receipt never
authorizes an apply. It requires a new code change, review, and explicit
production approval.

Railway exposes a Dockerfile-backed service as `builder=RAILPACK` together with
the materialized `dockerfilePath`. The validator checks both fields as a pair,
plus the web health/start contract, both restart policies, and
`railwayConfigFile=null`. Do not interpret the builder label alone.

## Additional convergence diagnostic

The plan gate is now post-apply only. It accepts an exact two-resource,
zero-diagnostic, zero-change plan and emits bounded metadata. It rejects every
change, including the historical adoption changes:

```bash
set -o pipefail
test "$(railway --version)" = "railway 5.45.5"
railway config plan --file .railway/railway.ts --json \
  | node scripts/validate_railway_iac_plan.mjs
```

With CLI `5.45.5`, the known restart-policy read-model mismatch makes this
diagnostic fail closed even while the owner receipt passes. That failure must
not be bypassed and must not trigger a second apply. A future CLI version needs
a separate compatibility review before use.

## Repository and CI boundaries

- `configFile` remains omitted so both services retain
  `railwayConfigFile=null` instead of using deprecated Config as Code.
- Existing variable values use `preserve()`; secret values must never be
  pulled, printed, stored, or committed.
- The named `railway.*.json` files remain unchanged for legacy-bound services
  and review fixtures.
- CI evaluates the two-service definition, the zero-change plan gate, and the
  owner-receipt validator offline. CI never authenticates to Railway and never
  mutates infrastructure.
- `autoDeploy=false`, `MANAGED_INSPECT_ENABLED=false`, managed-inspect HTTP 503,
  zero I/O, and runtime health remain separate owner-system readbacks.
- There is intentionally no automatic `unapplied -> apply` path. Any future
  material change needs its own reviewed plan and action-time approval.
