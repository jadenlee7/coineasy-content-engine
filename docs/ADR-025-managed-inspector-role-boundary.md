# ADR-025: Managed inspector dedicated Auth and database role

**Status:** Accepted in merged code and CI; production remains inactive and no
production migration, deployment, account, MFA, release seed, or enablement is
authorized.

**Date:** 2026-09-01

## Context

The managed Telegram delivery-unknown inspector introduced in PR #176 verifies
a real Supabase Auth session, recent TOTP, an exact release, an operator
allowlist, and an immutable consent. It does not expose a service-role key or a
generic REST proxy.

Its first implementation nevertheless accepts a normal `authenticated` JWT.
That database role has accumulated Studio RPC and table capabilities, including
self-service workspace creation. Hiding those routes from the inspector UI
does not remove them from an extracted ordinary bearer token.

The inspector needs only three application RPCs:

- `managed_telegram_inspect_context(uuid,text)`
- `register_managed_telegram_inspect_consent(uuid,jsonb,text)`
- `inspect_managed_telegram_delivery_unknown(uuid)`

## Decision

Use a never-previously-logged-in Auth account whose persistent
`auth.users.role` is created as `coineasy_managed_inspector`. Keep the JWT
audience `authenticated`; audience and database role are separate claims.

Create a database role with the same name and these boundaries:

- `NOLOGIN`, `NOINHERIT`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`,
  `NOREPLICATION`, and `NOBYPASSRLS`;
- no direct or indirect membership in `authenticated`, `anon`, `service_role`,
  worker, or phase-resolution roles, and no owned objects;
- no schema creation, business table or column access, sequence access, or
  direct private/auth/storage access;
- effective execute access to exactly the three signatures above;
- membership granted from the dedicated role to PostgREST's `authenticator`
  with PostgreSQL 16 `SET TRUE`, `INHERIT FALSE`, and `ADMIN FALSE` options.

The application verifier, `/user` readback, and database identity gate must all
observe the exact dedicated role. Missing, ordinary, altered, or Hook-rewritten
roles fail closed on the managed path. The verifier retains the existing exact
issuer, audience, subject, session, AAL2, recent TOTP, expiry, and bounds.

The build migration creates the three managed RPCs but deliberately leaves them
ungranted. The immediately following role-boundary migration grants only those
three signatures to the dedicated role. This strict pair avoids an ordinary
`authenticated` exposure window between migration-file transactions. Neither
migration has been applied to production, so the pre-activation correction does
not rewrite production history. Old phase JWT RPCs, workers, general Studio
functions, and their grants remain unchanged.

The GoTrue Admin API can persist a top-level user role at creation and the
common token path derives a JWT role from that stored value. CI replays both the
existing baseline stack and production-observed service versions in disposable
local containers. That is version-compatibility evidence only, not hosted
configuration, network, Hook, ACL, account, or activation proof.
[Admin create](https://github.com/supabase/auth/blob/v2.196.0/internal/api/admin.go),
[token issuance](https://github.com/supabase/auth/blob/v2.196.0/internal/tokens/service.go)

## Security qualifications

`NOINHERIT` does not subtract rights granted to `PUBLIC`. A null function ACL
can still mean default PUBLIC execute. Tests therefore inspect effective
schema, function, table, column, sequence, ownership, and membership rights and
include negative fixtures which deliberately expose each class of right. Those
fixtures must be observed by the same effective-privilege predicates used by
the clean inventory. PUBLIC object rights count as effective only when the role
can also use the containing schema. Thus the production-observed raw PUBLIC
`SELECT` ACLs on inaccessible `extensions.pg_stat_statements*` are compatible,
while any `extensions` schema `USAGE` immediately blocks. Raw ACLs on target
tables/functions are still exact hard fences. This change does not silently
revoke shared privileges used by other applications. [PostgreSQL privileges](https://www.postgresql.org/docs/16/ddl-priv.html)

The production-observed PostgreSQL 17 role graph is also explicit: the target's
direct members are `authenticator` and the auto-created `postgres` edge, while
the four required descendant paths include `postgres` both directly and via
`authenticator`, plus `supabase_storage_admin` via `authenticator`. Hosted
projects can also retain Supabase's fixed `cli_login_postgres` role used by the
CLI's Management API temporary-password flow. Its presence is optional; if it
exists, the two additional target paths must end in the exact
`postgres -> cli_login_postgres` edge. Full ordered role paths, grantors,
membership options and every capability boolean are compared as exact
multisets. Password and `VALID UNTIL` are rotating credential state and are not
part of this authorization contract. A similar role name, different
intermediate, altered option or extra path fails closed.

PostgREST selects a role from a verified JWT, but the original login role also
matters for PostgreSQL `SET ROLE`. The boundary is no direct database credential,
no arbitrary SQL or role-switching endpoint, and a fixed PostgREST API surface;
it is not a claim that the underlying authenticator login cannot switch to its
other granted roles. [PostgREST role impersonation](https://docs.postgrest.org/en/v14/references/auth.html#user-impersonation),
[PostgreSQL role membership](https://www.postgresql.org/docs/16/role-membership.html)

Changing a stored user role does not invalidate already-issued access JWTs.
Existing employee accounts are therefore not converted. A control-plane error
that issues an ordinary token can leave authority on other existing APIs even
when the managed gate rejects it. Production provisioning must start with a
new account and must verify existing Auth Hook behavior before activation.

Admin credentials are used only by the isolated synthetic local test and, in a
future separately approved provisioning ceremony, by an operator control
plane. They are never accepted by the inspector runtime, browser, or generic
proxy. The local harness creates ephemeral credentials on an internal Docker
network and must not load project environment files.

## Alternatives considered

| Option | Decision |
| --- | --- |
| Persistent dedicated Auth role plus matching DB role | Chosen, subject to real local proof and later hosted readback |
| Custom Access Token Hook over a stored ordinary role | Rejected for v1: disabling or bypassing the Hook can restore the stored ordinary role |
| User-specific restrictive RLS only | Insufficient alone: it does not remove other callable or SECURITY DEFINER RPC authority |
| Separate Auth project or token broker | Stronger control-plane isolation but disproportionate new infrastructure for this bounded inspector |
| Service-role, admin, or shared Studio runtime identity | Forbidden because its blast radius exceeds inspect |

## Verification contract

The existing normal tests remain fast and the existing opt-in disposable stack
provides the integration proof. Completion requires:

1. clean cumulative PostgreSQL 16 migrations plus a digest-pinned replay of the
   production-observed Supabase PostgreSQL 17.6.1.127, GoTrue v2.196.0 and
   PostgREST v14.5 images, with the exact hosted-version ACL/role graph and only
   the three managed RPCs effective for the dedicated role;
2. negative ACL fixtures for PUBLIC/null ACL, schema creation, relation/column/
   sequence access, ownership, indirect role membership, CLI-edge option or
   capability drift, and a same-depth rogue intermediate path;
3. actual local Admin-created custom-role user: password login, existing TOTP,
   `/user`, database live role, three managed RPCs, refresh, recovery, factor
   reset, logout, ban, expiry, and revocation behavior;
4. an ordinary `authenticated` control user denied on all three managed RPCs;
5. the dedicated user denied on workspace creation, business tables/views,
   the ten ordinary application RPCs, phase-resolution RPCs, private helpers,
   arbitrary SQL, and role switching;
6. existing consent/hash/no-write, browser replay, server concurrency, Python,
   JavaScript, SQL, image-isolation, and packaging regressions;
7. zero production reads or mutations and zero Telegram, Grok, Typefully, X, or
   other provider calls.

Passing local tests and the production-observed-version replay is not hosted
production proof. The canonical two-migration manifest and single-query,
SELECT-only pre/post ACL probes in
`ops/managed-inspector-activation/` prepare a fail-closed review gate; they do
not apply either migration. Hosted Hook state, exposed schemas, cumulative
ACLs, exact release SHA, and the new-account ceremony remain separate approval
gates.

## Rollback

Fail closed: disable the UI/allowlist/release, revoke the exact account/session,
and, if separately approved, revoke the dedicated RPC and authenticator
membership. Preserve immutable consent and audit records. Do not restore the
account to `authenticated`, and do not claim that logout alone instantly
invalidates all stateless access JWTs.
