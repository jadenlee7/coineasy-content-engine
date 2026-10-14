# Rollback-only readiness-anchored one-shot proof

These are synthetic disposable-database tests, **not an automatic production
migration or installation command**. The prior exact functions were installed
with a superseded fixed window. This package now proves an immutable,
readiness-anchored two-hour one-shot. Production schema/body application and
later anchor insertion remain separately approved owner operations.

`supabase/proposals/ybo_trial_readiness_anchor.rollback.sql` creates only an
empty private table and its guard. The fixed trial is
`1a2bef47-4827-4bc3-9b4d-5baf2714015d`, workspace
`5cc143cc-b253-4841-8a6c-ab3ac98623a6`. After exact OFF readiness, the owner can
insert one row binding release SHA, plan SHA-256, whole-second start, exact
two-hour deadline and a KST day containing both endpoints. Start must be no
more than 60 seconds old and not future at one real-clock sample. UPDATE,
DELETE and TRUNCATE are blocked; the fixed primary key also serializes concurrent
inserts. Runtime roles receive no table privilege or guard EXECUTE grant. RLS
is enabled without policies. No anchor row, public RPC or activation is added
by the rollback schema proposal.

Schema file SHA-256:
`89aa4936658ad8f8a0c51889e405cfc1c021eb1113e18c06d6e8ee06821165fe`.
Schema transaction payload SHA-256:
`fc1d750ef5fc3d575ecbf8569557de31f0a1851dc740cfd190a32bcf3522fc67`.
Guard prosrc SHA-256:
`a066aa4808cc21d6d5e52d80fdc2251fd406cc93157c891aee0f9895b01497d9`.

`exact-natural-claim.rollback.sql` preserves the installed signature, owner intent,
service-role ACL and identity/freshness/dedupe checks. It requires the exact
`ybo-trial:<fixed trial UUID>` worker, derives window/day from the workspace
anchor and excludes Yellow before locks. Missing anchor is OFF. The
preserved logo-only branch permits only
`source.media=[]` plus explicit `job.input.source_image_url=""`; other missing,
NULL, malformed or nonempty-media combinations are not logo-only admission.
It ends in `ROLLBACK`. Candidate function-body SHA-256:

`b4f4ef59b2cba338c33b22e8263d270dfcfcddbfed1dc84eb7063c6d476ff086`

Expected preceding body SHA-256 (PR211 fixed window; verify live before apply):
`5373553023dbfae5270c631d087811ad40517f693638f71b6c8904b4509b720d`.
The rollback proposal checks that hash and all non-body catalog fields before
and after `CREATE OR REPLACE`; it contains no GRANT/REVOKE statements.
This file is not proof that the candidate has been applied to the hosted RPC.
Existing daily slots, including the prior Yellow unknown trial, stay untouched;
a fresh trial skips them instead of reusing or retrying their jobs.

The separate `supabase/proposals/quality_banner_generation_context_ybo_logo.rollback.sql`
preserves the historical quality-context proposal unchanged. Its guarded
`CREATE OR REPLACE` allows that same explicit empty-media pair only for
Babylon/OriginTrail with the same exact worker/anchor/day and a natural initial
job created inside the window, with exact nine-field input, attempts=1/max=3,
and a lease ending by the deadline. Existing attached-media
admission remains unchanged. It verifies the previous body and byte-equivalent
non-body `pg_proc` catalog (including owner/ACL/OID) before rolling back.
New quality-context function-body SHA-256:
`b475ed241a64827ef7294a48691f1954a9314e2fb22ed57d028f52bb3c56cd19`.
Expected preceding quality-context body SHA-256:
`4ab8c89d807d428e392e2cf63dbf2cfd17125a8e6d3203baf6a4585aba010197`.
Its SQL application also needs separate production approval.

Never run the fixture SQL, fixture clock or test harness against production.
The harness requires an explicit disposable database confirmation plus a local
Unix socket or the fixed loopback CI service. Its filename prevents collection
by the ordinary Python test suite. It rejects alternate service/host routes.

The dedicated `ybo-trial.yml` workflow uses disposable PostgreSQL **17.6**. It
uses exact historical body fixtures pinned to production hashes, not a reverse
text transformation. It compiles the untouched rollback SQL against those
prior functions first, then substitutes clocks only in a second local copy so
anchored-window positive and negative cases
can run without changing the deployed body. It covers concurrent claims,
existing-row invariance, stale source/feed, window expiry after lock/validation,
role/argument checks, Python versus real `jsonb::text` hash equality, strict
empty-media admission/rejections and quality-context catalog/ACL invariance.
The new context has 31 negative cases per allowed client, malformed-media table guards,
expiry rejection, and a preserved existing-media path outside the trial window.
Claim replacement also proves unchanged owner/ACL/OID and rejects a changed
prior body. The claim has 72 negative cases plus Yellow rejection. Anchor cases
cover 20 invalid rows, arbitrary fresh date, midnight rejection, single insertion,
immutability and concurrent insertion. New-day fixtures retain previous-day
input-date and wrong-key rejection. Both proposals preserve all other rows.

`proposal_statements()` strictly removes only the single outer `begin;` and
exact final rollback marker. Atomic owner application order is schema, claim,
context; all old-body and non-body guards remain. Local proof verifies explicit
rollback, forced failure after all candidates, and replay failure with no reset.

`readiness_anchor_catalog.sql` is a small read-only schema receipt.
`readiness_anchor_operator_catalog.sql` works with the table absent or present
and additionally proves existing dependencies/security unchanged. It excludes
only the exact new table, its pkey index and zero-argument guard from the old
security fingerprint; the anchor is separately verified, not ignored.
Before insertion require row_count=0, matching owner, no non-owner/runtime/public
grants, exact seven columns, RLS enabled/not forced, zero policies and exact
constraints/triggers. PostgreSQL 17 definition digests:

- constraints: `4e4774bbc157bed043256408fd383fb119ffe7c8ff014143cd8e877a153873f1`
- triggers: `f5ccbbae626e82438292414559e5377e29cec98dc3656fc9c0cf009f1337758b`

Run only after bootstrapping the disposable dependency schema exactly as the
workflow specifies:

```sh
python ops/ybo-server-trial/exact_claim_local_unittest.py --local-only
```

On macOS with Homebrew PostgreSQL 17, the socket-only bootstrap helper creates
and stops its own fresh disposable database (never accepts a DSN):

```sh
EXACT_CLAIM_LOCAL_PYTHON=/path/to/test-venv/bin/python node scripts/verify_ybo_trial_local.mjs --local-only
```

The workflow has read-only repository permissions and no deployment/provider/
Telegram or production database credentials. CI proof is not hosted runtime or
delivery proof. Local PostgreSQL 17.11 anchor/claim/context and atomic catalog
tests passed during preparation; exact 17.6 CI requires its own successful result.
