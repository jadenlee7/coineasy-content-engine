# Exact preserved Babylon lease handoff

This is a **rollback-only proposal**, not a production migration runner. The
single authorized job is `e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af`. No general
recovery, retry, requeue, new generation job, approval or public send is added.
The old trial anchor, old claim/context RPCs, ledger and source/style/request
rows are untouched. New tables are empty after schema installation.

## Immutable operator receipt

The owner registers one `private.ybo_preserved_babylon_compatibility_receipts`
row only after the separate OFF operator proves the existing copy checkpoint,
news payload, original budget store and old terminal owner. SQL cannot inspect
SQLite or the filesystem. `db_proof_sha256` and `local_proof_sha256` therefore
are **trusted owner attestations**, not cryptographic proof of remote truth.
Runtime cannot insert or modify these rows. The local operator registration
uses the matching column names:

- `receipt_id`, fixed workspace/client/trial/job/request/source IDs;
- old/new release, spend-policy, and copy-input digests;
- `checkpoint_sha256`, `api_payload_sha256`, `input_sha256` (PostgreSQL JSONB
  text digest), `style_pack_hash`, `db_proof_sha256`, `local_proof_sha256`,
  `provider_account_sha256`, `observed_at`.

No raw copy, media URL, provider response, destination or secret is registered.
Old release and spend policy are fixed to the exact prior trial; new budget
policy is fixed to `local_token_rate_forecast@1`'s policy digest. The source X
post is pinned to `2110380535927427174`, not merely an arbitrary latest ID.
The existing canonical checkpoint and API-payload SHA-256 values are separately
hard-pinned from the live readback, so a valid-shaped but different digest fails.
Source body and URL must match the unchanged job input hash; the API payload
and checkpoint are verified independently by the OFF operator.

Only after readiness does the owner insert one
`private.ybo_recovery_readiness_anchor` row for trial
`d346ceea-4ea9-479f-a5cf-05097b50ce48`. It contains workspace, receipt UUID,
plan digest, exact receipt release SHA, whole-second `not_before`, exactly
two-hour `deadline`, and one KST date containing both endpoints. The receipt
must be no more than 15 minutes old and not future. Anchor insertion requires
start within the previous 60 seconds, never future. All three new relations
reject UPDATE, DELETE and TRUNCATE. Unique trial/job constraints prevent replay.

## Runtime contract

`YBO_PRESERVED_BABYLON_RECEIPT_ID` identifies the exact owner-inserted row.
`get_exact_preserved_babylon_context(workspace UUID, job UUID, receipt UUID)`
returns the original source input and immutable style pack only while the
new window is live, source is latest and under 24 hours, feed is active with a
15-minute interval and a poll under 30 minutes, and there is no prior handoff
or durable content/delivery. The style pack uses the existing `references`
parser format. This read does not take ownership or create a style pack.

`claim_exact_preserved_babylon_job` arguments are workspace, job, request,
source UUIDs; input SHA-256; style hash; receipt UUID; exact new worker ID;
and 60–900 lease seconds (900 default). It locks receipt, anchor, daily slot,
feed, source, job, state, pack and client before repeating all mutable checks.
It resamples the clock after validation scans. The only business-row change
is a compare-and-swap of `locked_by`, `locked_at`, `lease_expires_at` (plus the
existing updated-at trigger). Job identity/input/output, status `running`,
attempt count `1`, request, source, slot, style pack and original start survive.
The existing output must remain exactly `{"execution_plane":"studio_sync"}`;
arbitrary unknown outputs are not treated as an empty or reusable job.
One append-only handoff record is consumed atomically. Repeated or ambiguous
calls are not automatically retried; a committed first call makes a second
return null. Existing completion/binding RPCs remain the next authority.

The preserved source requires attached official media. No logo-only exception
or OriginTrail quote/note relaxation is introduced. No source freshness error
can be bypassed using a cached old proof. Newest-source comparison is serialized
against the existing normal feed-locking recorder, not arbitrary owner SQL.

This successor release performs **only the existing Babylon recovery**.
Yellow remains excluded. OriginTrail is skipped before source/queue/claim I/O:
its ordinary initial-claim and logo-only context remain pinned to the original
trial and are not silently widened. The plan's two-client maximum is an upper
bound, not permission to route an incompatible new job through this recovery.

## Authorized rollout ordering

1. Pass the Python/Node, disposable PostgreSQL 17.6 CI, and OFF-image checks;
   merge with `[skip netlify]`. This proposal itself always rolls back.
2. Install only the independently verified new empty schema/functions. Preserve
   existing function catalogs, anchors, business rows, and staff access.
3. Deploy the exact merge SHA with all execution gates OFF. Verify Git origin,
   image stamp, authenticated release, existing volume/account, and no sends.
4. Refresh the exact official source through the existing recorder. If it is
   no longer latest, expired, or different from the preserved input, stop OFF.
5. Before creating any successor owner, run the network-denied copy operator
   with the full original API payload and a fresh bounded DB proof. Append one
   compatibility receipt; preserve every preexisting row/file and liability.
6. Register that exact SHA-only receipt, then anchor one two-hour same-KST-date
   window only after readiness. Stage the final plan and receipt selector OFF.
7. Enable the bounded generation path, inspect its exact resulting version,
   and enable only the staff-room courier after QA and duplicate checks.
   Finish by restoring saved flags and deployed runtime to OFF.

Every mutation uses a durable intent. An unknown outcome is reconciliation,
not permission to repeat a lease, provider POST, receipt, anchor or send.
The aggregate $10 limit is an estimated-image-price admission ceiling, not a
claim about final provider settlement. Public publishing remains disabled.

## Proofs

Run `scripts/verify_preserved_babylon_local.mjs --local-only` with
`EXACT_CLAIM_LOCAL_PYTHON` pointing to the test virtualenv. It creates a fresh
socket-only PostgreSQL 17 instance and always stops it. No remote DSN, existing
database, provider or Telegram credential is accepted. CI uses its disposable
PostgreSQL 17.6 service and invokes
`python ops/ybo-preserved-recovery/recovery_local_unittest.py --local-only`.

The harness compiles the unchanged rollback proposal, verifies full rollback
and atomic failure, and only then substitutes a synthetic clock in a disposable
copy. It covers default OFF, exact one handoff under concurrent callers,
replay rejection, unchanged old anchor and pre-existing function catalogs,
full-row preservation outside the lease/handoff, stale/latest source and feed,
lease/job/output/slot/input mismatches, expiry before the final write, immutable
receipt/anchor/handoff, actual anonymous/authenticated denial, and private table
ACL isolation. `catalog.sql` is a separate bounded metadata-only readback.
Local/CI proof does not prove a production apply, activation or delivery.
