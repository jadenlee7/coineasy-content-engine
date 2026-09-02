# Managed inspector production activation pack

The manifest and three SQL readbacks in this directory are validate-only. The
separate `production-apply.mjs` runner is default-off and can apply only the
closed canonical pair after an exact, short-lived approval packet is supplied.
Nothing in this directory creates an Auth account, deploys or enables a
runtime, calls an inspector RPC, or contacts Telegram or another provider.

## Canonical migration identity

`manifest.json` fixes the only allowed order, paths and SHA-256 values. The
validator additionally requires a regular non-symlink file, exact byte length,
valid UTF-8, LF-only lines and a final newline. The manifest itself must survive
a deterministic sorted-key JSON round trip byte-for-byte, which also rejects
duplicate JSON keys.

The canonical set digest is SHA-256 over this domain-separated payload:

```text
coineasy-managed-inspector-migration-set@1\n
<version>\t<path>\t<byteLength>\t<sha256>\n
```

`canonicalSetSha256` proves only that ordered migration-byte set. It is neither
the manifest SHA nor an application release/deployment SHA.

Superseded migration digests are explicitly rejected:

- `a82b9a279b36a535ebdf771b1a183e42d239116e51398bbd6c3b6832d102daf2`
  granted the managed RPCs to `authenticated` during the gap between
  separately committed migrations.
- `aecc7af2abf58c00402c026cbf90dabe077a68379ae17bc307a2ed137759a4ed`
  removed that grant but did not normalize arbitrary creator default-ACL
  grantees before the first migration committed.
- `ac5538098b0ce71f1a4f24c15478456354fc30b29814e9bc4c9a9fb6d8ff83ad`
  checked recursive membership by terminal role and path length, which did not
  distinguish the observed hosted CLI path from a rogue intermediate path.

None of these superseded byte sets may be applied.

Run the offline validator from any directory:

```sh
node ops/managed-inspector-activation/validate-pack.mjs
node --test \
  ops/managed-inspector-activation/pack.test.mjs \
  ops/managed-inspector-activation/production-apply.test.mjs
```

## Production readback boundary

Submit these SQL files only through Supabase's official read-only SQL endpoint.
All three queries require `current_user=supabase_read_only_user` and
`transaction_read_only=on`; a console, direct database connection or other
executor is intentionally a BLOCK even if its account happens to be privileged.

Run `preflight.sql` before either migration. It accepts only the clean state in
which both migration-history rows, the dedicated role and every managed target
object are absent. It also checks the PostgreSQL 17 boundary, required roles,
base objects and Auth columns. Auth types use an explicit compatibility set:
identifiers are UUID, timestamps are `timestamptz`, booleans are `bool`, text
fields are `text`/`varchar`, and the three Auth state columns additionally allow
their canonical Auth enum UDTs. A present column with any other UDT is a BLOCK.
A production-observed PostgreSQL 17 catalog must also have exactly the canonical
`authenticator -> postgres` and `authenticator -> supabase_storage_admin`
administrative edges, including their grantors, membership options and complete
role capability attributes. The platform-managed `cli_login_postgres` role is
optional. When it exists, its only accepted reachable path is exactly
`authenticator -> postgres -> cli_login_postgres`, with the observed
`supabase_admin` grantor, `admin=false`, `inherit=false`, `set=true`, and exact
LOGIN-role capability booleans. Password and `VALID UNTIL` are intentionally not
compared because the Management API rotates them. Any similar name, altered
edge, rogue intermediate or additional recursive path is a BLOCK.
A partially applied or already applied state is a BLOCK, not a retry signal.

`preflight.sql` also fixes the hosted readback capability required later:
`supabase_read_only_user` must retain `BYPASSRLS` and effective membership in
`pg_read_all_data`. That combination was observed in production and is required
to count the four owner-only, forced-RLS tables after the first migration.

Run `intermediate.sql` only after the first raw migration and its guarded
history registration have both returned successfully. It requires the first
history row to contain exactly one statement whose full SHA-256 equals the
canonical first migration, requires the second row and dedicated role to be
absent, and verifies the four empty tables, eight functions, owners, RLS, ACLs,
triggers and absence of entry-RPC execution by ordinary roles. It is the gate
between the two separately committed migrations.

Run `postflight.sql` only after both independently approved raw migrations and
their guarded history registrations.
It recomputes effective privileges, including null-ACL defaults, from the
PostgreSQL catalogs. In particular, the dedicated role must have exactly three
executable functions across the observed exposed schemas (`public` and
`graphql_public`), no executable function in `private`, no effective relation,
column or sequence privilege reachable through schema `USAGE`, no owned object,
and no unexpected schema privilege. Its direct members are exactly
`authenticator` plus the PostgreSQL 17 auto-edge to `postgres`. The four
required descendant paths are the canonical routes to `authenticator`,
`postgres` and `supabase_storage_admin`; when the optional exact
`cli_login_postgres` platform edge exists, two additional full paths reach it.
Every ordered path, grantor, membership option and complete platform-role
capability tuple is compared as an exact multiset. The three target RPCs remain
unavailable to PUBLIC and ordinary application roles.

The production-observed image has raw PUBLIC `SELECT` ACLs on
`extensions.pg_stat_statements*`, but PUBLIC and the target role have no
`extensions` schema `USAGE`; those rows are therefore inaccessible and are not
treated as effective relation access. Postflight separately rejects any target
schema `USAGE` that would make them reachable. Raw ACLs on the four target
tables and eight target functions remain complete exact allowlist checks even
when their schema is inaccessible.

Target table and function ACLs are exploded with PostgreSQL's effective default
when an ACL is null, then compared against complete grantee/privilege allowlists.
The eight function signatures also have exact security-definer and per-function
`proconfig` contracts.

Each readback file is a single `WITH ... SELECT` statement. Treat any returned
`passed=false` row, query error or missing row set as BLOCK. Save the complete
result as the bounded pre/intermediate/post readback evidence; no readback by
itself proves authorization or permits runtime activation.

Observed text is capped at 4096 bytes. Each row includes the original observed
byte length and a SHA-256 over the full uncapped value; an oversized value makes
that row fail and replaces the displayed value with an omission marker.

Every result row also states `generic_db_push_allowed=false` and
`full_history_not_reconciled=true`. The pack validates only the two target
migrations fixed by `manifest.json`; known remote/local history divergence is
outside this approval. Generic `supabase db push` is prohibited.

Intermediate and postflight prove that the hosted history rows contain one
exact-source statement with the canonical raw SHA-256, plus the resulting
catalog, role and ACL state. They deliberately return
`exact_migration_bytes_proven=false`: only the custom runner receipt binds the
locally revalidated raw bytes to each official API request.
`custom_apply_receipt_required=true` remains a hard activation gate.

## Default-off production runner

The runner uses only the fixed production project and Supabase Management API
origins. It verifies a clean exact `origin/main` checkout, the manifest, every
raw migration immediately before send, a 26/26 read-only preflight, and the
write endpoint's `postgres` executor before the first mutation. The strict
order is:

1. first raw migration;
2. guarded first history registration containing the raw source as one text
   array element;
3. 22/22 read-only intermediate gate;
4. second raw migration;
5. guarded second exact-source history registration;
6. 39/39 read-only postflight.

Raw migration commit and history registration are separate transactions and
cannot be made atomic without changing the canonical migration bytes. The
runner therefore journals this gap explicitly. Timeout, connection loss,
non-201 status, malformed response, local byte drift, failed readback or receipt
failure stops immediately. It never automatically retries, repairs history,
continues to the next migration, or rolls back a confirmed commit.

Each migration request opens a transaction, acquires one fixed transaction-level
advisory lock and rechecks the migration-specific clean/prerequisite state while
that lock is held. Only then is the exact canonical body included unchanged
exactly once; its own `COMMIT` ends the enclosing transaction and automatically
releases the lock. An error rolls back and releases the lock instead of poisoning
a pooled session. A simultaneous host fails on the lock, while a later host with
a stale preflight fails the in-lock state guard before DDL. The receipt records
both the raw manifest SHA and the fencing-wrapper request SHA. History and
catalog readbacks remain mandatory because the lock cannot span separate
Management API requests.

Because each canonical file intentionally retains its own `BEGIN`, PostgreSQL
may emit the bounded warning `there is already a transaction in progress` after
the wrapper's outer `BEGIN`. That warning is expected; any SQL error or failed
guard remains a terminal BLOCK.

An approval packet is deterministic canonical JSON, bound to the exact release,
canonical migration set, operation UUID, actor, actions and a maximum two-hour
window. Its full bounded subject is recomputed and must match its SHA-256. This
is an operator-review binding, not a detached cryptographic signature or a
substitute for the explicit approval record. Approval expiry is rechecked before
every mutation.

Offline-only commands:

```sh
node ops/managed-inspector-activation/production-apply.mjs --template
node ops/managed-inspector-activation/production-apply.mjs \
  --validate --approval /absolute/canonical-approval.json
```

The `--apply` form is intentionally omitted from copy/paste instructions. It
requires a separately approved exact subject hash, a fresh trusted `main`
readback and a new private receipt directory. Each receipt event is hash-chained,
created with `wx`, fsynced with its parent directory and sealed read-only. The
final digest must be captured outside the runner; local files alone are not an
immutable external approval authority.
