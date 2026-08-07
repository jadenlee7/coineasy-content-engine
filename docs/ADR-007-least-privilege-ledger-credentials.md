# ADR-007: Least-privilege credentials for the agent Batch ledger

Status: Proposed. Roles and grants ship as an additive migration; the credential
cutover is deliberately **not** part of this change.
Date: 2026-08-01. Re-measured 2026-08-07 against `main` @ `ce3470b`.

## Context

`docs/BATCH_FIRST_EXPERIMENT.md` closes with an explicit condition:

> The pilot repository currently authenticates its narrow ledger RPC calls with
> the existing Supabase service-role credential. [...] Run it as an isolated
> Railway service and replace it with a dedicated database broker/token before
> promoting the experiment beyond review-only staging.

The OriginTrail canary has now been promoted to production under that shared
credential. This ADR records what the credential actually grants, why the
current shape is wrong for a service that talks to an external model provider,
and the narrowest change that fixes it.

Measured on the production project on 2026-08-01, `service_role` holds:

| Surface | Extent |
|---|---|
| `public` tables reachable | 21 tables, 114 grants |
| Write grants (`INSERT`/`UPDATE`/`DELETE`) | 51 |
| Tables it may `DELETE` from | 14, including `publications`, `jobs`, `assets`, `figma_links`, `workspace_clients`, `workspace_members` |
| `public` routines it may execute | 42 |
| `rolbypassrls` | true |

The Batch dispatcher needs **fifteen** of those routines and **zero** table
grants. It is also the only component in the system holding `OPENAI_API_KEY` and
the only one that opens a connection to a third party. Today a fault or
compromise in that process can delete published content and workspace members.
That gap — not any observed incident — is what this ADR closes.

That routine count moves, and the direction is the argument. Re-measured on a
fresh database at `main` @ `ce3470b`, `service_role` now executes **56** `public`
routines rather than the 42 above — the canary-grant, cost-overage,
production-shadow, fact-check-gate, and Buzz-delivery migrations each granted
themselves to it. Over the same span the dispatcher's genuine need went from 9 to
15, the producer stayed at 16, and the reviewer stayed at 2. The shared
credential grows with every migration that lands; a scoped role grows only when
a component actually calls something new, and only deliberately.

The ledger's own tables are already unreachable directly: the
`20260731120000_agent_batch_ledger` migration revokes direct access to
`agent_runtime` even from `service_role`, and every state transition runs through
a `SECURITY DEFINER` routine. The remaining exposure is therefore entirely
*outside* the ledger, in the rest of the `public` schema that the shared key
carries along with it.

## Measured consumer surface

Three components authenticate with `SUPABASE_SERVICE_ROLE_KEY` today. Their
actual routine usage is disjoint in a useful way.

**Dispatcher** (`scripts/run_batch_dispatcher.py`, Railway, holds
`OPENAI_API_KEY`) — 15 routines, all via `SupabaseBatchRepository._rpc`:

```text
configure_agent_batch_budget   claim_agent_batch_jobs    expire_agent_batch_jobs
register_agent_batch           list_active_agent_batches update_agent_batch_poll
complete_agent_batch_job       fail_agent_batch_job      finalize_agent_batch

configure_origintrail_batch_canary_grant   claim_origintrail_batch_canary_job
peek_origintrail_batch_shadow_candidate    configure_origintrail_batch_shadow_day
authorize_origintrail_batch_provider_create
register_origintrail_batch_provider_create
```

The six in the second block arrived with the canary-grant, cost-overage, and
production-shadow migrations. They are on the live run path, not operator
subcommands: the entrypoint calls the shadow and canary-grant routines while
arming a day, and `core/batch/dispatcher.py` calls the claim, authorize, and
register routines while dispatching a provider batch.

It cannot reach `queue_agent_batch_job` (no call site), the whole
`*_review_draft_*` family, or the two review-inbox readers. It makes no direct
PostgREST table call and no Storage call.

**Producer** (`scripts/run_official_x_daily.py`, Railway, holds X and Studio
credentials) — 14 automation routines plus 2 ledger routines
(`configure_agent_batch_budget`, `queue_agent_batch_job`) reached through
`BatchQueueBridge`. Also no direct table or Storage access. Unchanged across the
same span: `core/automation/repository.py` issues the same RPC names at
`ce3470b` as it did at `566bca0`.

**Review console** (Netlify functions) — the Batch surface uses exactly
`list_agent_batch_review_inbox` and `get_agent_batch_review_item`, both read-only
and GET-only.

## Decision

Introduce three dedicated Postgres roles and grant each only the routines its
component provably calls. PostgREST already switches roles from the JWT `role`
claim, and `authenticator` is the role-switching entry point, so no application
code changes: each component keeps its existing `apikey` + `Authorization`
header shape and only the credential value changes.

```text
coineasy_batch_dispatcher  -> 15 ledger routines
coineasy_batch_producer    -> 14 automation routines + 2 ledger routines
coineasy_batch_reviewer    -> 2 read-only review routines
```

Each role is `NOLOGIN`, is granted to `authenticator`, receives
`USAGE ON SCHEMA public` and nothing else. No table grants, no
`agent_runtime` grants, no `BYPASSRLS`. The `SECURITY DEFINER` routines continue
to run as their owner, so narrowing the caller does not weaken any transition —
it only removes paths that were never used.

`coineasy_batch_reviewer` additionally receives no write routine at all, which
makes the review surface read-only at the database layer rather than only by
convention in the Netlify adapter.

### Why not a broker service

A separate HTTP broker holding `service_role` was considered. It would add a
service to deploy, monitor, and secure, plus a hop in the dispatch path, to
reach the same place that role grants reach with no new runtime. The runbook's
phrase is "broker/token"; this ADR takes the token half deliberately.

### Why not per-workspace tokens

The ledger routines take `target_workspace_id` as a parameter, so a role token
does not by itself bind a caller to one workspace. With a single production
workspace this is not yet a real boundary. When a second workspace exists, the
workspace binding belongs in the routines (derived from the JWT rather than
accepted as an argument), not in the grant layer. Recorded here so the gap is
not mistaken for something this ADR already solved.

## Options considered

1. **Keep `service_role` everywhere.** Zero work, and the runbook explicitly
   rules it out beyond review-only staging. Rejected.
2. **Custom roles + role-claim JWTs (chosen).** No new runtime, no code change,
   removes all table reachability from the dispatcher. Cost: tokens are minted
   from the project JWT secret and rotate manually.
3. **Broker service holding `service_role`.** Strongest audit story, highest
   operational cost. Revisit only if per-call policy or rate limiting is needed
   beyond what the ledger already enforces.
4. **Direct Postgres connections with per-role passwords.** Would replace the
   PostgREST client in both Python repositories. Large change, no additional
   safety over option 2 for this workload. Rejected.

## Consequences

The dispatcher loses the ability to reach `publications`, `jobs`, `assets`,
`figma_links`, `workspace_clients`, and `workspace_members` entirely — not by
policy but because the grant does not exist. The same holds for the 41 routines
it never called.

Three constraints follow from the measured surface and must be respected at
cutover.

**The producer cannot be split without a code change.** `daily_runner.py`
hard-fails unless the Batch settings' Supabase URL, service-role key, and
workspace id are byte-identical to the automation settings'
(`core/automation/daily_runner.py:1054-1060`). The producer therefore takes a
single role covering both its automation and its ledger routines. Splitting them
later means relaxing that equality check, which exists to stop a producer from
queueing into a different project than it reads from — worth keeping until there
is a reason to separate.

**The producer's source-recording routine is chosen at runtime.**
`core/automation/repository.py:487-491` selects
`record_origintrail_nonquote_sources` for `origintrail` and
`record_official_x_sources` otherwise, and `AUTOMATION_CLIENTS` permits four
clients. The producer role is granted both.

**The Netlify credential is shared with non-Batch functions.** The same
`SUPABASE_SERVICE_ROLE_KEY` powers `content-catalog`, `content-promotions`,
`monthly-kpi`, and `tutorial-storage`, which call further routines, read
`workspace_clients` directly, and use Storage. Netlify environment variables are
per-site, so swapping that one variable to a reviewer token would break those
functions. The reviewer role is therefore defined and granted here but **not
adopted** by the review console until either the Batch functions read a separate
variable or the other functions get their own role. This ADR does not pretend
that site-wide swap is ready. The first per-function adoption path now exists
elsewhere: the Buzz shadow function reads its own optional
`SUPABASE_BUZZ_SHADOW_KEY` and can carry a reviewer-role token without touching
the site-wide variable, and the Buzz delivery function likewise adopts the
`coineasy_buzz_delivery` role through `SUPABASE_BUZZ_DELIVERY_KEY` (see the
ADR-011 addendum).

## Cutover plan

The migration in this change is additive: it creates roles and grants and
changes no existing privilege, so applying it alters no behavior. Adoption is a
separate, reversible step per component.

1. Apply the migration. Verify each role holds exactly its expected routine list
   and zero table grants.
2. Mint a role token per component, signed with the project JWT secret, carrying
   `{"iss":"supabase","ref":"<project-ref>","role":"<role>"}`. Confirm the
   project still accepts HS256 legacy tokens before relying on this; a project
   migrated to asymmetric signing keys mints these differently.
3. Cut over the **dispatcher first** — it has the smallest surface, the clearest
   rollback, and the highest value. Swap `SUPABASE_SERVICE_ROLE_KEY` on that
   service only, then confirm a live pass reports `ok: true` and that a budget
   row is written.
4. Roll back by restoring the previous variable value. Nothing in the database
   needs to change to revert.
5. Cut over the producer only outside its cron window
   (`*/15 23,0-2 * * *` UTC), after the dispatcher has run clean for a full day.
6. Leave the reviewer role unadopted until the Netlify credential split above is
   resolved.

Do not cut over during the 14-day OriginTrail canary window or the 7-day
production shadow (`20260804130000_origintrail_7d_production_shadow`) unless the
run is paused: a credential fault mid-window would consume evidence days without
producing evidence. The shadow path is the reason the dispatcher role now spans
15 routines rather than 9, so cutting over mid-shadow would also be the first
time those six grants are exercised in production.

## Verification

`supabase/tests/agent_batch_ledger_least_privilege.sql` asserts, in a
transaction that rolls back, that each role holds `EXECUTE` on exactly its
expected routine set, holds no `public` table privilege, holds no `agent_runtime`
privilege, and is not a member of `service_role`. It also asserts that the
dispatcher role cannot execute `queue_agent_batch_job` or any
`*_review_draft_*` routine, which is the specific separation this ADR buys.

That suite alone is a closed loop: the expected set and the granted set are both
authored here, so a ledger routine added *and* called by the dispatcher keeps it
green while the scoped credential silently lacks the grant. That is exactly what
happened between `566bca0` and `ce3470b` — six routines reached the dispatcher's
call sites with no grant behind them, and every check stayed green.

`tests/test_least_privilege_ledger_roles.py` closes the loop against the call
sites: it parses the routine names out of the migration, parses the RPC names
`SupabaseBatchRepository` issues, and fails when a call site has no grant. It
also pins the dispatcher grant to *exactly* the batch RPCs minus the
producer-only bridge call, and asserts the migration still sorts last. A scoped
role is only as good as the thing that notices when it goes stale.

The migration is deliberately timestamped after every migration that creates a
routine it grants, so its `to_regprocedure` guard validates each signature
against the final definition. A grant issued before a later `create or replace`
survives only while the signature is unchanged; ordering last removes that
dependency, and the ordering assertion above keeps it that way.

## References

- `docs/BATCH_FIRST_EXPERIMENT.md` — the broker/token condition this ADR answers
- `supabase/migrations/20260805090000_least_privilege_ledger_roles.sql` — the
  roles and grants this ADR describes
- `supabase/migrations/20260731120000_agent_batch_ledger.sql` — ledger and its
  existing `SECURITY DEFINER` boundary
- `tests/test_least_privilege_ledger_roles.py` — the call-site drift guard
- [Supabase Postgres roles](https://supabase.com/docs/guides/database/postgres/roles)
- [PostgREST roles and JWT](https://docs.postgrest.org/en/stable/references/auth.html)
