# ADR-026: Repository-scoped Railway Infrastructure as Code partial

**Status:** Accepted and production-applied. Future apply, deployment, or
enablement remains separately authorized.

**Date:** 2026-09-03

**Applied:** 2026-09-04

## Context

The repository historically kept an auto-discovered root `railway.json` for
the `coineasy-content-engine` web service and separate named JSON manifests for
specialized workers. Railway applies an auto-discovered root manifest to any
service that deploys this repository unless the service has a different bound
configuration file. A managed-inspect exact-SHA deployment rehearsal therefore
selected the web service's root Dockerfile instead of its isolated Dockerfile.

Binding the new service to another Config-as-Code JSON file would preserve the
immediate separation but adopt a deprecated facility and violate the required
`railwayConfigFile=null` state. A whole-project IaC import is also unsafe: the
Railway project contains resources owned by several repositories, and omitted
resources are delete intent.

## Decision

Replace the root auto-discovered manifest with Railway's TypeScript
Infrastructure as Code and export the stable named partial
`coineasy-content-engine-services`. Railway recommends one file per project;
this partial is a bounded migration bridge justified by the existing
multi-repository project and must not proliferate into overlapping ownership.

The partial owns exactly two existing services:

1. `coineasy-content-engine`, with the root manifest's Dockerfile, start,
   healthcheck, timeout, and restart contract;
2. `coineasy-managed-inspect`, with its isolated Dockerfile, exact watch set,
   disabled-compatible runtime shape, bounded restart policy, and existing
   service domain.

Both resources declare their GitHub source, complete existing variable-name
inventory as `preserve()`, one production-region replica, and existing runtime
defaults. The definition omits `configFile`, so applying it does not bind a
legacy manifest. Secret values are never represented in source. The IaC
program also rejects any context other than the exact production project and
environment IDs and names or any direct authoring-file command other than
`plan`. Any future production apply is restricted to a newly reviewed and
separately approved pinned plan. The managed service's
`MANAGED_INSPECT_ENABLED=false` state and both services'
`autoDeploy=false` state remain owner-system rollout assertions because the IaC
SDK cannot safely prove all of those live values from the repository contract.

The one-time adoption plan was applied from commit
`784249900491a4fb99b152fbcf73e34511c05a93` after its clean source tree,
owner-only artifact SHA-256, and live configuration ETag were approved. Railway
accepted that exact pinned plan once and created a successful exact-commit web
deployment. The managed-inspect deployment did not change and remained
default-OFF.

The post-apply readback exposed a Railway CLI `5.45.5` convergence limitation.
The CLI imports current state from `environment.config`, where restart-policy
fields remain omitted, while the owner `serviceInstance` API returns the
materialized `ON_FAILURE` policies and retry counts. The planner therefore
continues to report those fields as `null -> desired` even after a successful
apply. This is a canonical read-model mismatch, not permission to replay the
plan.

The owner `serviceInstance` API is consequently the convergence authority for
the settings touched by the one-time adoption plan. A checked-in, read-only
GraphQL query and strict validator prove the exact project, environment, two
service identities, `railwayConfigFile=null`, paired `RAILPACK`/Dockerfile
representation, health/start settings, and restart policies without requesting
variables, logs, deployment metadata, resolved file configuration, or secrets.
A valid receipt returns
`owner_settings_converged/one_time_adoption_settings/stop_no_apply`; any
mismatch is `BLOCK` and requires a new code review and explicit production
approval. This receipt does not prove apply provenance or the complete live
IaC state; source, build environment, watch patterns, runtime, replicas,
networking/domain, and variable-name inventory remain offline contracts or
separate owner-system rollout gates.

The existing named `railway.*.json` files remain unchanged for legacy-bound
services and as the managed-inspect review fixture. Their later migration is a
separate decision. CI evaluates the two-service definition and fails if a root
`railway.json`/`railway.toml` returns, resource ownership expands, variable
names drift, or the isolated settings weaken. CI never applies the definition.

## Options considered

| Option | Result |
| --- | --- |
| Keep the root `railway.json` | Rejected: it can be auto-discovered by another service sourced from this repository |
| Bind every service to a named JSON manifest | Rejected: retains deprecated Config as Code and conflicts with `railwayConfigFile=null` |
| Import and own the full Railway project | Rejected: crosses repository ownership and makes omissions destructive |
| Use one stable named IaC partial for two services | Chosen: removes root fallback while bounding ownership to this repository |
| Deploy with an uploaded local source archive | Rejected: it would not prove GitHub-origin exact-SHA provenance |

## Consequences

- Root manifest auto-discovery can no longer select the web Dockerfile for the
  managed-inspect service.
- Railway configuration changes are typed, executable, and reviewable. Every
  future material change still requires a new production plan and approval.
- Every existing variable name for an owned service must remain represented as
  `preserve()` or a future plan can propose deletion. Adding or removing a
  variable therefore requires deliberate code review.
- The stable partial name is production identity and must not be renamed.
- Manual deployment was blocked between removal of the root manifest and the
  one-time partial apply plus materialized-settings readback.
- Automatic deployment and disabled-mode state still require Railway metadata,
  runtime, image-stamp, HTTP 503, zero-I/O, and forbidden-secret readbacks after
  a separately approved exact-SHA deployment.
- The managed-inspect image stamp is sourced only from Railway's
  `RAILWAY_GIT_COMMIT_SHA` Docker build argument. Railway supplies Git variables
  only to GitHub-triggered deployments, so a CLI-origin source upload fails
  closed before producing an image. The preserved historical
  `MANAGED_INSPECT_SOURCE_SHA` service variable is not provenance and can be
  removed only through a separately reviewed production-variable change.
- A plan is no longer an idempotence oracle under CLI `5.45.5`. The executable
  gate accepts only a true zero-change plan. The historical adoption diff and
  the known restart-policy residual are both rejected so neither can authorize
  a second apply.
- The scoped owner receipt is fail-closed: an exact match means
  `OWNER_SETTINGS_CONVERGED/STOP_NO_APPLY`; a mismatch or unreadable response
  means `BLOCK`; there is no automatic `UNAPPLIED -> APPLY` transition.

## Verification and rollout gates

1. [x] Add an offline test for exact project, partial, two-service ownership,
   sources, variable names, build/deploy settings, and absence of root config.
2. [x] Add the pinned IaC authoring SDK and run the contract in CI.
3. [x] Ran a read-only production design plan: it reported no diagnostics,
   deletion, destructive change, addition, or unrelated-service change and
   only the three expected safe service-setting updates. The historical
   adoption gate rejected any other summary, detail, transition, order, or
   count; the post-apply gate now rejects every non-empty change set.
4. [x] Reviewed a fresh pinned production `railway config plan` at apply time
   and blocked on any diagnostic, destructive change, addition, deletion, or
   third-service change. Bound approval to the clean commit SHA, `.railway`
   source-tree SHA, owner-only plan artifact SHA-256, and live `configEtag`.
5. [x] Obtained separate approvals for merge and the one-time production apply.
   The exact approved pinned artifact was applied once and then deleted.
6. [x] Immediately before merge and apply, read back `autoDeploy=false` for
   both owned services and all five legacy-bound services sourced from this
   repository; all seven source triggers remained disabled.
7. [x] After deployment, proved GitHub-origin SHA, successful deployment and
   health, `railwayConfigFile=null`, `autoDeploy=false`,
   `MANAGED_INSPECT_ENABLED=false`, HTTP 503, and zero I/O. The apply did not
   read or mutate secret values.
8. [x] Added a strict owner-receipt validator and changed the plan gate to accept
   only zero changes. A valid owner receipt stops; every other state blocks.

## References

- [Railway Infrastructure as Code](https://docs.railway.com/infrastructure-as-code)
- [Railway IaC reference](https://docs.railway.com/infrastructure-as-code/reference)
- [Railway Config as Code deprecation](https://docs.railway.com/config-as-code)
