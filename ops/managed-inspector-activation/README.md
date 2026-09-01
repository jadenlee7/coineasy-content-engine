# Managed inspector production activation pack

This directory is a validate-only gate. It does not apply migrations, change
roles or ACLs, create an Auth account, deploy a service, or call an inspector
RPC.

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
node --test ops/managed-inspector-activation/pack.test.mjs
```

## Production readback boundary

Submit these SQL files only through Supabase's official read-only SQL endpoint.
Both queries require `current_user=supabase_read_only_user` and
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

Run `postflight.sql` only after an independently approved migration operation.
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

Both files are a single `WITH ... SELECT` statement. Treat any returned
`passed=false` row, query error or missing row set as BLOCK. Save the complete
result as the immutable pre/post readback evidence; neither query proves that a
migration was authorized or that a runtime may be enabled.

Observed text is capped at 4096 bytes. Each row includes the original observed
byte length and a SHA-256 over the full uncapped value; an oversized value makes
that row fail and replaces the displayed value with an omission marker.

Every result row also states `generic_db_push_allowed=false` and
`full_history_not_reconciled=true`. The pack validates only the two target
migrations fixed by `manifest.json`; known remote/local history divergence is
outside this approval. Generic `supabase db push` is prohibited.

Postflight proves only target version/name presence plus the resulting catalog,
role and ACL state. It deliberately returns `exact_migration_bytes_proven=false`.
Only a separately captured custom-apply receipt tied to both raw file hashes may
prove which bytes were executed; `custom_apply_receipt_required=true` remains a
hard activation gate.
