# Private review producer binding compatibility

`complete_review_draft_job` records the completed immutable item/version in
`jobs.output` and the requested item in `jobs.input.request_id`. It leaves the
optional `jobs.content_item_id` foreign key null. The first private-review
candidate reader instead required that foreign key, so it rejected naturally
completed jobs. Its original synthetic fixture populated the foreign key and
did not expose this mismatch.

The additive migration `20260916190000_content_ops_review_producer_binding.sql`
replaces only the private candidate helper. It identifies possible producers
using any of the FK/request/output bindings, locks and counts them, and rejects
ambiguity even if the second producer failed or uses a conflicting binding.
An accepted producer must still have matching input request, output item/version,
ordered source IDs, current KST slot and successful completion. If the optional
FK is populated it must match. No production job backfill is needed.

The migration preserves current-version, current-day, latest-source, canonical
PNG, source/poll freshness, no-approval and no-publication requirements. Article
content remains outside this daily-news MVP. It does not invoke any queue/send
RPC, alter settings, enable a service, or change the earlier applied migration.

## Verification

The production-shaped NULL-FK regression failed before the fix. Afterward the
disposable local PostgreSQL runner applied all 57 migrations, passed full-schema
ACL checks, and passed positive/mismatched/duplicate producer tests plus existing
eight-way claim and send-start races, exact-version scoping and unknown-delivery
restart fencing. Synthetic approval/publication rows: 0; production/provider
calls: 0. All owned local databases were removed afterward.

The regression is also wired into the existing disposable PostgreSQL CI job.
The following 2026-09-23 evidence was pre-apply evidence, not a production
migration receipt or a Telegram delivery.
The [2026-09-23 read-only production readback](PRIVATE_REVIEW_CARD_PREAPPLY_RECEIPT_20260923.md)
found the earlier candidate-reader body still hosted and no remote applied
`20260916190000` migration entry. A strengthened function-body gate then
blocked the proposed button-card owner path pending that correction.
The same receipt includes a separate read-only function-plus-history contract
that classified production as `legacy_no_history`. The one-transaction SQL
builder was tested in
disposable databases, and a separately gated one-shot runner was tested with
mocked endpoints. Neither test applied the migration to production.

The [2026-09-23 read-only production check](PRIVATE_REVIEW_PRODUCER_BINDING_READBACK_20260923.md)
classified the hosted helper and migration history as `legacy_no_history`; the
base private button-card owner path was then blocked. The
[one-migration pack](../ops/private-review-producer-binding/README.md) pins
the exact migration bytes, verifies atomic function/history application in
disposable PostgreSQL 16 and 17, and prepares a separate approval-gated
one-shot runner. Its mocked network tests and default-OFF template did not
authorize or perform production application.

## Rollout boundary

The deployed service remains OFF. The helper is resolved at runtime by
existing RPCs; applying the fix did not require a worker redeploy. The
observed production migration list had both local-only and remote-only
entries, so generic `supabase db push` or unrestricted migration-up was not
used. Ambiguous outcomes must be reconciled read-only, not retried.
On 2026-09-25, a separately approved exact one-migration operation applied
`20260916190000` and independent read-only checks returned
`corrected_exact_history` and full button-card pre-apply `pass`; see the
[post-correction receipt](PRIVATE_REVIEW_CARD_PREAPPLY_RECEIPT_20260925.md).
The preceding verification paragraphs describe the historical pre-apply
state, not current production. No button-card owner proposal, deployment or
Telegram send is implied by this correction.

Before any canary, select a newly eligible exact immutable version, configure
the dedicated relay and exclusive ownership, then verify the private message
receipt. Do not widen article/date/source eligibility to force a test card.
