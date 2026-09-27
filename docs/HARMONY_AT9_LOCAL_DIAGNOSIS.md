# Harmony @9 local diagnosis — 2026-09-05

The hosted failure is **not yet diagnosed conclusively**. This change fixes the
missing diagnostic detail, not a proven migration defect. It does not authorize
or execute a new hosted proof.

## Established hosted evidence

The one authorized `@9` invocation at
`cc6de5abcbc424075d57e42eef65ce9a4f91eb7a` passed database connectivity and two
migrations, then failed with `preview_migration_apply_failed` on
`20260825132000_harmony_preview_collaboration.sql` (SHA-256
`dffdaa5b35e7fb369c3aa767f0c7a6854508e8b4c5b8969ab17508adc95ab22d`).
Security suites and concurrency proofs did not run. The original receipt contains
no SQLSTATE or SQL input line. Child absence and scoped PAT removal were confirmed
in the separate execution closeout; this local diagnosis does not refresh those
owner-system observations.

## Local isolation matrix

Every run used the exact nine migration files from that SHA, unchanged, after
40 earlier repository migrations. PGlite 0.5.8 / PostgreSQL 18.3 ran in separate
in-memory databases. Existing local databases were not modified. This environment
is not hosted Supabase and is not a PostgreSQL 16 replay.

| Fixture | Migration result | Security result |
| --- | --- | --- |
| Complete repository baseline, superuser | 9/9 passed | 3/3 passed |
| Complete baseline, non-superuser with CREATEROLE/BYPASSRLS | 9/9 passed | First suite rejects the deliberately different creator name; later suites not run |
| Missing `private.grok_qa_dispatch_outbox` | First 2 pass; #3 fails `42P01` | Not run |
| Missing outbox `source_event_type` column | First 2 pass; #3 fails `42703` | Not run |
| Outbox owned by another principal, no access for migration owner | First 2 pass; #3 fails `42501` | Not run |

The restricted fixture uses `harmony_test_owner` instead of the managed
`postgres` principal. The security suite correctly rejects its unapproved
membership edge. No exception was added to accommodate that fixture.

These three negative fixtures all match the hosted failure ordinal. The ordinal
therefore cannot identify the hosted root cause. In particular, a missing table
is a hypothesis, not evidence that Production needs a new migration. The first
new baseline-sensitive SQL function is at lines 482–549 of migration #3; its
outbox references and subsequent foreign keys explain this dependency boundary.
The usual PostgreSQL CI job applies all migrations as a superuser, so its success
does not alone establish the inherited hosted baseline or restricted-owner path.

## Diagnostic change

The `@10` runner requests SQLSTATE-only output and suppresses error context for
migration/security stdin scripts. Only a completed exit 3 may contribute optional
`sqlstate` and `psql_input_line` fields to `sql_failure`. SQLSTATE must belong to a
fixed enum; the line must be a strict integer within the exact input payload.
Unknown codes, mixed or verbose output, malformed lines, duplicate errors and
oversized stderr omit the optional fields. Receipt construction revalidates them.

No raw stderr, SQL body, connection text or arbitrary exception value is copied
into receipts. Timeouts, connection errors, interrupted commands and cleanup
failures retain their existing behavior. All nine migrations and all permissions,
one-shot limits, price caps, transports and cleanup paths remain unchanged.

## Verification and next boundary

- Full Python suite: 2,621 passed; two existing FastAPI deprecation warnings.
- Runner suite independently: 289 passed.
- Relevant Harmony JavaScript tests: 20 passed.
- Native local PostgreSQL 16.13 with the real ProcessRunner: three synthetic
  error cases passed, including notice plus error and PL/pgSQL assertion.
- Native mixed-output fixture was rejected as intended.
- Independent static security review: no actionable finding; diff check clean.

No new PAT, hosted child, hosted retry, Production connection or modification,
provider call, worker activation, merge, deployment or publication was performed
for this diagnosis. Before another paid attempt, prefer a separately scoped
read-only baseline inspection where authorized; otherwise the new diagnostic
candidate still requires fresh exact-SHA action-time approval. Do not repair a
baseline, widen permissions, or replay the consumed `@9` approval based on these
local hypotheses.
