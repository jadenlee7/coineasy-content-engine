# New Babylon readiness trial: disposable SQL proof

`supabase/proposals/readiness_babylon_trial.rollback.sql` is a rollback-by-default,
owner-only installation proposal. It creates an **empty** private anchor for
trial `c34dbb8a-ff7c-484e-8e43-757f692ec743` and replaces only the exact
claim and quality-context function bodies. It does not insert readiness, queue
or claim work, enable a worker, spend, deliver, or alter the previous fresh
Babylon anchor `9d7865c1-c13b-4b1b-aba1-83124aa8d2cd` and its row.

The new anchor is restricted to the existing workspace, KST date 2026-10-16,
whole-second server-observed start no earlier than 2026-10-15T15:47:35Z,
and an exact two-hour deadline within that date. One insert is allowed within
60 seconds of the server clock; updates, deletes, and truncation are blocked.
RLS is enabled with no policies, and runtime roles receive no table or guard
function privileges. The previous anchored worker branches are preserved.

Expected installed predecessor `prosrc` SHA-256 values are
`c62675f07e978d23516bfe533f76b340a3edeff710b6c845e40a7c2b599970c2`
for `claim_exact_natural_review_draft_job` and
`559d9b0e2f682b0fae476ad503cb776a481dd642afa01c4693b533241a147873`
for `get_quality_banner_generation_context`. A mismatch aborts atomically.
Every non-body `pg_proc` field, including OID, owner, ACL, definer and config,
is asserted unchanged.

`operator_catalog.sql` is read-only. It explicitly exposes previous fresh
anchor existence and count and includes its structure, security, and fixed row
in the protected pre/post hashes. It excludes only the new anchor from those
existing-state hashes.

Run the local proof with PostgreSQL 17 and installed repo test dependencies:

```sh
EXACT_CLAIM_LOCAL_PYTHON=/path/to/test-python node scripts/verify_ybo_readiness_local.mjs --local-only
```

The wrapper creates its own disposable Unix-socket server, runs prerequisite
historical proofs, then the new positive/negative claim and logo-context tests,
immutability/ACL checks, old-row and old-worker preservation, replay/body-drift
rejection, and zero-other-row checks. The unchanged proposal is first compiled
and rolled back; fixture clocks are substituted only in a second local copy.
Its 0600 receipt is written under a new `/private/tmp/` directory. Neither the
local proof nor CI is deployment, worker activation, delivery, or public-readback
evidence. CI runs the same SQL suite on PostgreSQL 17.6.
