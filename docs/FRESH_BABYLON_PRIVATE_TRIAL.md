# Fresh Babylon private trial

This is one explicitly approved staff-review experiment, not a recurring or
public publishing release. Production execution is separate from this code and
rollback-only SQL proof.

## Fixed scope

- Trial: `9d7865c1-c13b-4b1b-aba1-83124aa8d2cd`.
- Client: Babylon only; maximum one new daily-news job, version and review card.
- Date: KST 2026-10-16; start at or after `2026-10-15T15:00:00Z`.
- Start is fixed once after authenticated OFF readiness. Deadline is exactly
  two hours later and must be in the same KST day. No extension or second anchor.
- Existing account-bound image forecast ledger, estimate policy and US$10 daily
  limit are retained. This is not a guarantee of provider settlement charges.
- Yellow and OriginTrail are skipped before per-client I/O. Official Telegram,
  Typefully and X publication remain OFF. Existing callback-bot roles, keys,
  staff permissions, schedules and auto-deploy settings are unchanged.

## Preservation and admission

The old Babylon job `e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af`, copy checkpoint,
source binding, daily slot and budget history are not reset or adopted. Its
source is no longer latest; the preserved-job recovery UUID is not fresh-work
authority. Both historical anchor tables remain immutable.

The fresh path uses the existing normal source intake, style pack and new KST
daily slot. A complete live official feed read must establish the latest source
within 24 hours. Empty, incomplete, stale or already-used evidence is not a
reason to fall back to an older post. An occupied daily slot is skipped, not
reassigned. Only the returned exact job is claimed; FIFO is forbidden.

`supabase/proposals/newfresh_babylon_trial.rollback.sql` adds one empty,
owner-only immutable anchor and changes only the two existing exact-claim and
quality-context function bodies. It verifies their prior body hashes and
unchanged OID/signature/owner/ACL/catalog. Runtime roles receive no anchor-table
or trigger-function access. The explicit `media=[]` plus empty image input
allows the already pinned official-logo path; missing, NULL, malformed or
unusable media do not.

## Release sequence

1. Pass Python/JavaScript tests, disposable PostgreSQL 17.6 CI and image proofs;
   inspect the diff and retain exact SHA. Merge with `[skip netlify]`.
2. Apply the separately authorized schema/body transaction once using fresh
   catalog and bounded business-row fingerprints. Unknown result is reconciled
   read-only, never blindly replayed. The schema application inserts no anchor
   or job and enables nothing.
3. Deploy API, private courier and Netlify at the exact merge SHA while OFF.
   Verify GitHub origin, sole deployment, immutable image stamp, authenticated
   Netlify SHA, unchanged volume/forecast ledger and destination policy.
4. After the permitted date opens, recheck latest official source, unused new
   daily slot and zero exact-source job/content/delivery duplication. Insert the
   one readiness anchor and save the exact canonical plan/hash durably.
5. Configure that plan while OFF. Blank the legacy recovery receipt **selector**
   for fresh mode; do not delete or alter any recovery receipt record.
6. Publish exact Netlify trial gates first, then enable/deploy the exact API
   trial owner, verify authenticated preflight, then start the private courier.
   All broad generation/publication flags remain OFF.
7. The hosted controller owns intake, bounded generation and private manifest;
   the courier can dispatch only that owner-authorized immutable version.
   Computer availability is not required for the running server trial.
8. After completion, failure or deadline, effective authority is terminal.
   Restore saved trial flags to OFF, deploy OFF and verify authenticated runtime
   readback. Report API delivery receipts separately from visual room readback.

## Stop conditions

Stop before activation on any release/auth/destination/ledger/schema drift or
missing evidence. After admission, provider/DB/delivery ambiguity is terminal
for this owner: preserve receipts and reconcile the original attempt. Never
change UUIDs, retry an unknown delivery, clear the budget, select an older post,
or enable a public path to make the test pass.

Local and CI tests prove only the implementation. They are not production
migration, deployment, activation, visual approval or delivery evidence.
