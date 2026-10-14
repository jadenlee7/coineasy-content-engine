# Rollback-only exact natural claim proof

These are synthetic disposable-database tests, **not an automatic production
migration or installation command**. The prior exact function was installed once
with the superseded window. This package now proves a window-bounded logo-only
admission candidate. This is a behavioral SQL change, **not merely a rewindow**;
it requires separate production SQL approval before hosted replacement.

`exact-natural-claim.rollback.sql` preserves the installed signature, owner intent,
service-role ACL and identity/freshness/dedupe checks, with the fixed window
**2026-10-14T12:44:51Z–2026-10-14T14:44:51Z**. It additionally permits only
`source.media=[]` plus explicit `job.input.source_image_url=""`; other missing,
NULL, malformed or nonempty-media combinations are not logo-only admission.
It ends in `ROLLBACK`. Candidate function-body SHA-256:

`6c306a539f0476eb7acb0cf4430d96d2a2783919a725de17c181fef5d3f1e477`

Expected preceding body SHA-256 (06:22:17–08:22:17 UTC; verify live before apply):
`04524f35cbb1496dc08282e1548a4acc9b23b32ba288863b001cb7a792fe28c3`.
This file is not proof that the candidate has been applied to the hosted RPC.
Existing daily slots, including the prior Yellow unknown trial, stay untouched;
a fresh trial skips them instead of reusing or retrying their jobs.

The separate `supabase/proposals/quality_banner_generation_context_ybo_logo.rollback.sql`
preserves the historical quality-context proposal unchanged. Its guarded
`CREATE OR REPLACE` allows that same explicit empty-media pair only inside the
fixed window for a natural initial job created inside it, with exact nine-field
input, attempts=1/max=3, and a lease ending by the deadline. Existing media
admission remains unchanged. It verifies the previous body and byte-equivalent
non-body `pg_proc` catalog (including owner/ACL/OID) before rolling back.
New quality-context function-body SHA-256:
`52bee7b65bf683b958b38a8017cc14faea2246e301ac957de548692c99a5746e`.
Its SQL application also needs separate production approval.

Never run the fixture SQL, fixture clock or test harness against production.
The harness requires an explicit disposable database confirmation plus a local
Unix socket or the fixed loopback CI service. Its filename prevents collection
by the ordinary Python test suite. It rejects alternate service/host routes.

The dedicated `ybo-trial.yml` workflow uses disposable PostgreSQL **17.6**. It
compiles the untouched rollback SQL first, then substitutes only the three clock
calls in a second local-only copy so future-window positive and negative cases
can run without changing the deployed body. It covers concurrent claims,
existing-row invariance, stale source/feed, window expiry after lock/validation,
role/argument checks, Python versus real `jsonb::text` hash equality, strict
empty-media admission/rejections and quality-context catalog/ACL invariance.
The new context has 29 negative cases per client, malformed-media table guards,
expiry rejection, and a preserved existing-media path outside the trial window.

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
delivery proof. Local PostgreSQL 16.13/17.11 claim tests passed during preparation;
the new digest comparison and exact 17.6 CI require their own successful result.
