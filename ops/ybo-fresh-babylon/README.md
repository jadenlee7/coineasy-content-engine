# Fresh Babylon: disposable SQL proof

This is an owner-reviewed rollback proposal, not a production migration or an
automatic apply command. It creates one **empty** private anchor and replaces
exactly two existing function bodies. It does not insert an anchor, queue or
claim a job, enable generation, spend money, or deliver a message by installation.

The fixed trial is `9d7865c1-c13b-4b1b-aba1-83124aa8d2cd`, workspace
`5cc143cc-b253-4841-8a6c-ab3ac98623a6`, Babylon only, KST date `2026-10-16`.
The owner may insert one whole-second ready-time anchor after
`2026-10-15T15:00:00Z`, no more than 60 seconds old. The exact two-hour window
must remain in that KST date. The fixed primary key serializes concurrent
insertion; UPDATE/DELETE/TRUNCATE are rejected. RLS has no policies and runtime
roles have neither table privileges nor guard EXECUTE. There is no runtime
anchor-registration RPC.

The existing natural queue remains unchanged: a new KST date obtains a new
daily slot. Previous-day jobs, slots, immutable content, checkpoints, receipt
rows and the persistent budget remain intact. No same-day-refresh exception or
FIFO claim is introduced. The exact claim admits only the supplied new initial
natural job, source, request, input digest and style pack during the immutable
window. It rechecks freshness after lock waits and immediately before its one
job update. Old anchored worker behavior is retained, not widened.

Only explicit `source.media=[]` with JSON string `source_image_url=""` can use
the official logo path. Missing, SQL NULL, JSON null, malformed/nonempty media,
wrong client, wrong worker, stale/non-latest source, stale/inactive feed,
duplicate producer, existing content/link and replay fail closed. The original
attached-media quality-context path stays unchanged.

Required existing prosrc SHA-256:

- `claim_exact_natural_review_draft_job`:
  `b4f4ef59b2cba338c33b22e8263d270dfcfcddbfed1dc84eb7063c6d476ff086`
- `get_quality_banner_generation_context`:
  `b475ed241a64827ef7294a48691f1954a9314e2fb22ed57d028f52bb3c56cd19`

`newfresh_babylon_trial.rollback.sql` checks these bodies and every non-body
`pg_proc` field, including signature/OID/owner/ACL, in its one rollback envelope.
It does not grant or revoke permissions on either existing function.

`operator_catalog.sql` works before and after installation. Its bounded business
digest covers the exact old Babylon job/request/source, related artifacts and
both historical anchors/recovery receipts—not unrelated client/auth traffic.
It separately fingerprints other security/structural state and the target
non-body catalogs. Only `cli_login_postgres.rolvaliduntil` is normalized because
the Management API refreshes this ephemeral expiry per request; every other
role field, membership option, default ACL and policy remains protected. It
returns hashes and schema metadata, never source/draft bodies or secrets.

Run local proof only:

```sh
EXACT_CLAIM_LOCAL_PYTHON=/path/to/test-python node scripts/verify_ybo_fresh_local.mjs --local-only
```

The wrapper starts/stops only its own fresh Unix-socket PostgreSQL 17, installs
the reviewed dependency migrations, runs the original claim/logo proof and
preserved-recovery proof, then executes this fresh proof against their retained
historical records. It rejects remote connection routes. The untouched candidate
must compile and roll back before fixture-clock substitution. It checks forced
transaction rollback, body-drift rejection, preserved non-body catalog/ACLs,
missing-anchor OFF, earliest/day/worker/client guards, strict media handling,
one concurrent winner, replay rejection and all-other-row invariance.

A bounded `fresh-proof.json` is written exclusively with mode 0600 inside the
new disposable `/private/tmp/` directory. It contains the exact original
candidate schema/function metadata for operator verification, not runtime or
delivery evidence. CI runs the same harness on PostgreSQL 17.6. No production,
provider, Telegram, deployment or public-publication call is part of these tests.
