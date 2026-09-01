# Managed Auth Telegram inspect — disabled review implementation

This independent path removes the need for an operator-held project JWT signing
private key. It does **not** enable production inspection, delivery, or closure.
The existing Studio login, dedicated-JWT CLI, three resolution RPCs, workers,
and their grants are unchanged.

## Authority and isolation

Implementation and its role-boundary changes are merged, while synthetic local
tests and CI remain the only execution scope. Production migration, service
creation/deployment, Auth configuration, account/MFA enrollment,
release/operator bootstrap, real consent, real inspect, approve/resolve, and any
delivery require separate explicit approval.

The application must run on a dedicated origin and runtime. Adding a function
to the existing Netlify site is **not** isolation: that site has unrelated
service-role, worker, and provider secrets. The standalone runtime is not wired
into the current Netlify build or Railway services. Disabled mode must perform
zero Auth, database, or external provider calls.

Only a new dedicated human account whose persistent Auth and JWT role is
`coineasy_managed_inspector`, and which has no general content-write workspace
role, is eligible. The database role has access to exactly the three managed
inspect RPC signatures and no ordinary Studio RPC or business-relation access.
An ordinary `authenticated` token is rejected by both server and database.
Its access token remains in server memory; refresh tokens returned by
Auth are discarded, and the application does not refresh sessions. The browser
receives only an opaque Secure, HttpOnly, SameSite session cookie. After the
short server session expires, login and TOTP are required again. Restarting the server
invalidates local sessions. No Auth browser SDK, localStorage token, token
export, signing-key import, shared Studio fallback, or service-role fallback is
provided. No signup, MFA enrollment, recovery, or general-purpose RPC proxy is
part of this UI.

## Human workflow

1. After a separately approved deployment/bootstrap, the human signs in with a
   newly provisioned, never-ordinary-role dedicated account and verifies its
   already enrolled TOTP factor. Account creation and MFA enrollment are not
   part of this application.
2. The server verifies the JWT and live Auth identity. The database independently
   checks the live session, recent MFA, private operator allowlist, approved
   release, and absence of general content-write membership.
3. The human provides and reviews the exact immutable target and a **real,
   recent human public caption/PNG audit**. An automated text scan or old audit
   must not be relabelled as a new human negative audit.
4. An explicit consent POST writes a separate immutable inspect-consent record,
   bound to the human, session, request/hash, audit/hash, release, and expiry.
   Page loads, login, and MFA do not register consent.
5. A separate inspect action calls only the new inspect RPC with the consent ID.
   The RPC returns the existing bounded subject/hash shape. It writes neither
   original rows nor consent, approval, resolution, or event records.
6. Stop. Any later human approval or non-resend resolution remains a different
   explicitly authorized phase. Nothing here claims, retries, requeues, sends,
   publishes, or changes the original delivery outcome.

Consent registration is a database write. Do not describe the entire workflow
as read-only merely because the final inspect RPC is non-mutating.

## Time, replay, and revocation

Verified `aal2` alone does not prove recent MFA. The gate requires a recent real
TOTP AMR event; token refresh/`iat` is not a new MFA event. Live session deletion,
account suspension, allowlist/release revocation, and consent revocation must
deny the next protected call. Account recovery and MFA reset must invalidate
the binding; tests must use supported Auth metadata, not invented event fields.

Consent expires within ten minutes, bounded further by the request, MFA,
session, token, allowlist, and release deadlines. Audit freshness is independently
rechecked at inspection: an audit can become stale before the stored consent
expiry, in which case inspection is denied. Replaying registration must not
extend or expand consent. Inspection
rechecks the current gates and rejects already approved/resolved subjects.
Historical immutable versions are not rejected solely because the current
pointer moved: the original exact lineage and all existing subject checks must
still pass. No current-pointer repair is permitted.

The browser guard commits an IndexedDB unique-key record **before** POSTing an
inspect attempt. It contains only consent ID, request hash, and attempt time.
The marker survives success, rejection, timeout, crash, and reload. Storage
failure, JavaScript disabled, or a pre-existing marker means no POST. Do not
delete markers or automatically mint new consent to bypass a failed attempt.

Real Chromium fault injection found that the browser transport can replay a
POST on connection reset even when JavaScript calls `fetch` only once. The
guard makes one explicit fetch, not a claim of one physical HTTP request. The
server also sets its per-consent attempt flag synchronously **before** awaiting
the inspect RPC; duplicate HTTP requests in that live session cannot invoke that
inspect RPC again. They can still perform fresh Auth and context checks.
Server restart loses the opaque session and denies it rather than retrying.
This in-memory guard adds no database consumption/receipt. Tests separately
measure browser fetch/transport counts and actual server upstream inspect RPC counts.

This is at-most-one attempt for the normal client in one browser storage
profile, **not global exactly-once**. A different device, deleted browser
storage, or a direct API client is outside that local guarantee. The server
still checks exact identity and consent on every request; inspection deliberately
does not consume consent or write an execution receipt. Server-wide one-use
semantics would require a separately approved write design.

## Dedicated role boundary — production activation remains blocked

A normal Supabase `authenticated` JWT is **not an inspect-only bearer token**.
The accumulated foundation schema grants authenticated users workspace INSERT;
`workspaces_insert_self` permits a user to create their own workspace, whose
owner membership is established by the existing bootstrap trigger. This is
different from permission to modify the target workspace, and is also different
from the new private allowlist, which users cannot self-enroll in.

The follow-up role boundary does not give the managed account that ordinary
role. Its persistent `auth.users.role`, signed JWT role, live `/user` role, and
database gate must all equal `coineasy_managed_inspector`; the JWT audience
remains `authenticated`. The role has effective execute on only the three
managed RPC signatures. It has no business table/column/sequence rights, schema
creation, object ownership, or membership in ordinary/worker/phase roles.
On the production-observed PostgreSQL 17 image, the role has exactly two direct
members: `authenticator` and the platform-created `postgres` administrative
edge. `postgres` and `supabase_storage_admin` also reach it through the two
pre-existing canonical `authenticator` administration edges. Grantor, option,
attribute and path drift is rejected; no ordinary role is accepted.

`NOINHERIT` does not subtract PUBLIC grants, and a null function ACL can still
mean PUBLIC execute. Cumulative tests therefore inspect effective schema,
function, table, column, sequence, ownership, and membership rights. They also
insert deliberately unsafe PUBLIC, schema, relation/column/sequence, ownership,
and membership fixtures and assert that the same effective-privilege predicates
observe every exposure. PUBLIC object ACLs block when the target can reach the
object through schema `USAGE`; raw PUBLIC ACLs in an inaccessible schema do not.
The known `extensions.pg_stat_statements*` ACLs are compatible only while
`extensions` remains inaccessible, and a negative fixture proves that granting
schema `USAGE` immediately blocks. Target table/function ACLs remain raw exact
hard fences. The pack never performs an automatic global revoke that might
break unrelated applications.

The dedicated server still does not expose a generic database route or return
the bearer token. No Auth Admin credential exists in the runtime. The synthetic
local harness uses its own short-lived service JWT only to atomically create a
never-logged-in disposable custom-role user. A future real account and its TOTP
remain a separate operator-controlled provisioning step.

Changing an existing account's role does not revoke access JWTs already issued
with `authenticated`. Existing employee accounts are therefore not converted.
Likewise, an Auth/Hook control-plane error that issues an ordinary token can
leave authority on other existing APIs even when this managed gate rejects it.
Production activation remains **BLOCKED** until the hosted Auth behavior,
existing Hook behavior, exposed schemas, cumulative effective ACL, exact
release, and a new-account ceremony receive separate readback and approval.
The local production-observed-version replay does not cross this gate. See
[ADR-025](./ADR-025-managed-inspector-role-boundary.md).

## Release and operational gates

New private release/operator records start empty. Browser-supplied SHA, arbitrary
environment metadata, and a green PR are not deployment proof. Activation needs
separate exact-SHA build, migration, runtime, and readback evidence, plus an
approved immutable release fence. The database trusts its approved policy/code,
not an assertion about the binary used by a direct API caller.

Before any production step, separately review:

- Actual Auth + PostgREST signed-token integration results, not only SQL claims
  fixtures; recovery/MFA/session behavior and accumulated grants.
- Dedicated runtime environment contains no shared service-role, Telegram,
  worker, provider, or project JWT signing secret.
- Approved human UUID/workspace/actions/expiry and release/migration/readback
  hashes; no automatic enrollment or seed data.
- Recent genuine human audit and exact immutable target; no positive canonical
  public observation and no existing approval/resolution.
- Bounded, redacted diagnostics; no credentials, private captions, media URLs,
  provider responses, cookies, or TOTP values in logs or artifacts.

## Test contract

| Layer | Required proof | Failure examples |
| --- | --- | --- |
| Pure/server unit | Strict inputs, signature/issuer/audience, output bounds | Spoofed actor, ambiguous JSON, stale MFA, wrong key |
| HTTP | Cookie/CSRF/Origin/CSP, only named routes, fail-closed transport | Shared cookie, redirect, timeout, unexpected RPC shape |
| Browser guard | Atomic add/commit before one explicit fetch; marker retention | Two tabs, storage failure, crash, reload, browser transport replay |
| Disposable PostgreSQL | Additive dedicated role, effective PUBLIC/null ACL audit, exact binding, immutable rows | Indirect membership, schema/table/column/sequence rights, old/general RPC |
| Real local Auth + REST | Admin-created persistent custom role, password + TOTP, `/user` + DB role, three managed RPCs, logout/refresh/recovery | Ordinary role, role drift, AAL1, expired/tampered JWT, general RPC/workspace/table access |
| Packaging/CI | Disabled zero I/O; isolated files; full regressions | Inherited broad secret, missing build stamp, old path changes |

Coverage results, explicit skips, and limitations belong in the PR evidence.
Synthetic fixtures must never contain production UUIDs, credentials, private
content, or a refreshed fake audit presented as real human evidence.

### Local verification commands

Normal tests do not start Auth or PostgREST:

```sh
npm ci --ignore-scripts
npm run test:functions
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -p no:cacheprovider -q
git diff --check
```

The explicitly named integration harness starts only its own disposable Docker
stack with synthetic accounts/keys, applies migrations only there, tests real
Auth-issued tokens through PostgREST, and removes its own containers/network.
CI runs both the established baseline and the production-observed-version
replay:

```sh
node scripts/test-managed-auth-local.mjs
```

It requires a local Unix-socket Docker engine. The baseline uses GoTrue
v2.189.0, PostgREST v14.12, and PostgreSQL 16.13. The compatibility profile
replays the production-observed GoTrue v2.196.0, PostgREST v14.5, and Supabase
PostgreSQL 17.6.1.127 images locally. The harness verifies its selected image
profile and runtime version readbacks, but makes no hosted request and loads no
production credential. Its newly created internal Docker network has no
external egress or published host ports. A disposable Node helper makes actual
HTTP requests to only that stack's Auth and REST services. It does not load
project dotenv files or accept a remote project/database URL.
SQL-only claims fixtures and actual signed-token integration are separately
labelled. The real Auth/PostgREST adapter test and Chromium guard test are
separate harnesses, not a combined browser-to-real-Auth UI end-to-end test.
Production Auth/gateway/schema compatibility still needs a separately
authorized check before applying the migration or activating the runtime.

### Validate-only production review pack

`ops/managed-inspector-activation/manifest.json` fixes the exact order and
SHA-256 identity of the two migrations. Its `canonicalSetSha256` is the digest
of the documented ordered canonical-line payload; it is not a release SHA.
`preflight.sql` and `postflight.sql` are each one catalog-only `WITH ... SELECT`
statement and are intended for the platform read-only SQL surface. They do not
apply migrations, grant privileges, seed a release, create an account, or
enable the runtime.

The production migration history diverges from this checkout's ordinary local
history, so a generic `supabase db push` is not an allowed activation path.
Any future production operation must use the reviewed canonical pair in strict
order under separate approval, with saved preflight and postflight readbacks.
Any query error, missing result, digest mismatch, partial state, or
`passed=false` is a BLOCK.

```sh
node ops/managed-inspector-activation/validate-pack.mjs
node --test ops/managed-inspector-activation/pack.test.mjs
```

The browser test uses a fresh headless Chromium profile and a loopback-only
synthetic HTTP server. Install Playwright 1.62.1 and its Chromium outside this
repository, then set `MANAGED_INSPECT_BROWSER_TEST=1` and
`MANAGED_INSPECT_PLAYWRIGHT_MODULE` to that installation's absolute
`playwright/index.mjs` path before running
`node --test tests_js/managed-inspect-browser.test.mts`.

The image build requires a 40-character `MANAGED_INSPECT_SOURCE_SHA` build
argument. This writes the read-only build stamp; runtime SHA environment values
cannot replace it. A local smoke-test stamp is not a production release proof.
No deployment manifest or live enable command is supplied here.

## Primary contracts

- [Supabase server-side authentication](https://supabase.com/docs/guides/auth/server-side/creating-a-client)
- [Session verification and logout](https://supabase.com/docs/guides/auth/sessions)
- [MFA and authentication-method claims](https://supabase.com/docs/guides/auth/jwt-fields)
- [Database function security](https://supabase.com/docs/guides/database/functions)
- [JWT signing keys](https://supabase.com/docs/guides/auth/signing-keys)
