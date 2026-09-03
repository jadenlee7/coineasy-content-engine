# ADR-026: Repository-scoped Railway Infrastructure as Code partial

**Status:** Proposed; repository and CI review only. No production apply,
deployment, or enablement is authorized.

**Date:** 2026-09-03

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
`plan`. Production apply is restricted to a separately approved pinned plan.
The managed
service's `MANAGED_INSPECT_ENABLED=false` state and both services'
`autoDeploy=false` state remain owner-system rollout assertions because the IaC
SDK cannot safely prove all of those live values from the repository contract.

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
- Railway configuration changes become typed, executable, and reviewable, but
  adopting the partial still requires an explicit production plan and apply.
- Every existing variable name for an owned service must remain represented as
  `preserve()` or a future plan can propose deletion. Adding or removing a
  variable therefore requires deliberate code review.
- The stable partial name is production identity and must not be renamed.
- Once the root manifest is removed, manual deployment is blocked until the
  named partial has been applied and its materialized settings read back.
- Automatic deployment and disabled-mode state still require Railway metadata,
  runtime, image-stamp, HTTP 503, zero-I/O, and forbidden-secret readbacks after
  a separately approved exact-SHA deployment.

## Verification and rollout gates

1. [x] Add an offline test for exact project, partial, two-service ownership,
   sources, variable names, build/deploy settings, and absence of root config.
2. [x] Add the pinned IaC authoring SDK and run the contract in CI.
3. [x] Run a read-only production design plan: it reported no diagnostics,
   deletion, destructive change, addition, or unrelated-service change and
   only the three expected safe service-setting updates. The checked-in plan
   gate rejects any other summary, detail, transition, order, or count.
4. [ ] Review a fresh pinned production `railway config plan` at apply time and
   block on any diagnostic, destructive change, addition, deletion, or
   third-service change. Bind approval to the clean commit SHA, `.railway`
   source-tree SHA, owner-only plan artifact SHA-256, and live `configEtag`.
5. [ ] Obtain separate approvals for merge, production apply, and exact-SHA
   deployment; none is implied by this ADR. Apply only the approved pinned plan
   artifact and fail on live-state drift.
6. [ ] Immediately before merge, read back `autoDeploy=false` for both owned
   services and all five legacy-bound services sourced from this repository;
   block the merge if any source trigger is enabled. A design-time readback
   observed all seven disabled but is not an action-time receipt.
7. [ ] After deployment, prove GitHub-origin SHA, metadata/build/image stamps,
   `railwayConfigFile=null`, `autoDeploy=false`,
   `MANAGED_INSPECT_ENABLED=false`, HTTP 503, zero I/O, and zero forbidden
   secrets before calling the rollout complete.

## References

- [Railway Infrastructure as Code](https://docs.railway.com/infrastructure-as-code)
- [Railway IaC reference](https://docs.railway.com/infrastructure-as-code/reference)
- [Railway Config as Code deprecation](https://docs.railway.com/config-as-code)
