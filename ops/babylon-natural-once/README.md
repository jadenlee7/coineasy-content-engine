# Reusable exact Babylon natural admission — local proof

This change replaces only the bodies of two existing service-role RPCs:

- `public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)`
- `public.get_quality_banner_generation_context(uuid,text,uuid)`

The proposal is `supabase/proposals/babylon_natural_once.rollback.sql`. Its default
transaction always rolls back. It checks both exact previous body hashes, then
asserts every other `pg_proc` catalog field is unchanged. No persistent table,
function identity, grant, role, legacy anchor, old job or budget ledger is added
or changed. The only business write inside the claim is its exact target job's
initial queued/attempts-zero to running/attempts-one transition.

The new worker is `babylon-natural-once:<exact request UUID>`. Admission requires
the existing natural KST slot, source-state, style-pack, input digest, full input
shape, latest/fresh official source, recent active feed and durable deduplication.
The database window is bounded by `job.created_at + 2 hours` and the next KST
midnight. The context checks the exact owner/request and live first-attempt lease
for both attached-media and genuinely empty-media logo paths. The runtime owner
must still enforce the approved shorter window, exact release, substantive latest
announcement, estimated USD 10 cap, one employee-room delivery and final OFF.
These RPCs do not judge editorial substance, generate content or grant delivery.
An ASCII/decorative post cannot qualify just because it passes database shape checks.

## Local commands

```sh
/private/tmp/coineasy-preserved-recovery-qa-20261015/bin/python -m pytest -q tests/test_babylon_natural_sql_contract.py
node scripts/verify_babylon_natural_local.mjs --local-only
```

The second command creates a new disposable PostgreSQL 17 database on a private
Unix socket (no TCP listener or supplied DSN), runs the complete existing exact
claim/recovery/fresh/readiness/standalone proof chain, then this suite, and stops
that temporary server. `EXACT_CLAIM_LOCAL_PYTHON` may point to an existing Python.
Never point any fixture at production. CI uses the existing isolated PostgreSQL
17.6 service and runs `natural_local_unittest.py --local-only` after that same chain.

The proof first compiles the unchanged production proposal with rollback and
checks business rows, dependencies, structure, OIDs, owners and ACLs. A second
disposable copy substitutes a fixture clock only. It exercises the media/input/
source/feed/duplicate matrix, exact-request ownership, simultaneous claims,
replay rejection, expiry during validation, KST midnight, later calendar days,
old-worker regressions and full non-target row preservation (including expired
running leases). No production clock override exists.

## Verification status at preparation

- Static contract tests: **4 passed**.
- `git diff --check`: passed.
- Local PostgreSQL 17 behavior proof: **not executed**. Host IPC slots were full
  (`shmget(size=56): No space left on device`) before `initdb` completed. Existing
  IPC objects and the user's PostgreSQL 16 service were not modified. CI's exact
  PostgreSQL 17.6 proof must pass before merge/apply; static tests are not a
  substitute for it.
- Production apply, generation, activation and delivery: **none by this proof**.

Parent orchestration owns any separately approved production transaction and
receipt. It must compare live function preimages and non-body metadata before
apply and reconcile the outcome before retrying an ambiguous transaction.
