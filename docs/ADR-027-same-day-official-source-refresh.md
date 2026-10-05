# ADR-027: Isolated same-day official-source refresh

**Status:** Proposed for production; local implementation and tests authorized only

**Date:** 2026-10-05

**Deciders:** CoinEasy operator for each production rollout, execution, and delivery gate

## Context

The natural official-X worker reserves one immutable client/KST-day draft. Intake continues collecting newer official sources, but a reserved slot returns `already_reserved`. A newer source cannot replace the old job input. On 2026-10-05 a fresh Squid source had no draft while the existing succeeded draft's source was older than 24 hours. Resetting the slot or reusing the old version would violate provenance and deduplication.

Production inspection, code implementation, migration apply, release deployment, feature enablement, paid generation, employee identity registration, private delivery, and public publication are separate authorities. This change only implements and verifies the local refresh lane.

## Decision

Add a distinct, exact-ID refresh ledger and default-OFF runner:

1. Pin the existing succeeded natural job and its current immutable item/version plus a different latest official source. Derive body, canonical URL, and any allowlisted image reference from the source store rather than caller-provided claims.
2. Reserve at most one additional refresh per workspace/client/KST day, with unique source, request, job, and refresh IDs. Create a **new** `official_x_review_draft_v1` job, `manual_only=true`, `max_attempts=1`. Preserve the old slot, job, item, version, links, and source ownership. Bind only the fresh source's previously unowned source-state row to the new job.
3. Claim only that exact job once. Recheck source age `<24h`, latest-source identity, recent active official feed polling, exact IDs/release/day, and expiry. Recheck the queue/claim time window after row-lock waits. Bind the complete source snapshot and job input with immutable SHA-256 values. Atomically bind `studio_sync`; do not widen the natural manual-false execution-plane binder or invoke FIFO, Batch, polling, or scheduling.
4. Reuse existing style-reference packs, Studio generation with expected release SHA, and immutable completion. Recheck KST day, expiry, and the conservative 900-second execution budget immediately before the Studio generation POST, including after the internal release GET. Never replay that POST after timeout or ambiguous acknowledgement. A consumed claim stays consumed across process restart. One Studio request is not a claim of one internal model/API call.
5. Inspect a refresh-specific, bounded readiness receipt: exact current version, unique producer/source binding, canonical PNG metadata, bounded complete Telegram/X copy, and zero approval/publication/delivery. Readiness is **not** human fact-check approval, image-byte verification, or delivery authorization.
6. Keep natural candidate/reconcile and the existing daily private outbox unchanged. This MVP does **not** connect refresh drafts to the employee-room courier. A scoped ledger-backed refresh exclusion at the completion-to-Grok enqueue boundary prevents refresh generation from inheriting an automatic QA send; fake markers or unrelated manual jobs must not bypass that boundary.

## Options considered

- **Reset or overwrite the daily slot:** less code, but erases immutable provenance and allows replay. Rejected.
- **Wait for the next natural daily window:** no new lane, but cannot promptly reflect same-day updates; sources may expire before the configured window. Retained as the existing safe path.
- **Isolated bounded refresh lane:** adds a small ledger and operator-only execution path without changing natural scheduling. Selected.

## Consequences

- Staff preparation can eventually use a fresh official source without destroying yesterday's or today's receipts.
- Only one refresh per client/day is supported. After consumption or an unknown outcome, another UUID is not an escape hatch.
- A prior same-day private delivery remains a delivery-integration blocker; existing outbox uniqueness is not weakened.
- Active employee identity/reviewer bindings and callback enablement are separate prerequisites. This change does not register staff or make their buttons live.
- There is no deployment/image attestation or real provider/Telegram acceptance proof in local fixtures. Future production verification must pin exact source, release, job, version, banner bytes, and destination independently.

## Test plan

- Unit/contract: OFF and validate-only zero I/O; strict scalar IDs, timezone/day/expiry/release, queue/claim/readiness response binding; one Studio generation POST; wrong style pack/output rejected; delayed preflight cannot cross the action-time boundary; timeout and completion-ACK ambiguity never retry.
- Disposable Unix-socket-only PostgreSQL: PG16 verifies the full migration chain; PG17 verifies the refresh dependency chain and RPC/ACL fixture only. The unrelated existing managed-inspector PG16/platform-admin guard is preserved, not bypassed or weakened. Both refresh fixtures cover canonical banner/source/copy readiness, stale/nonlatest/poll/rollover/duplicate negatives, immutable predecessor snapshots, manual refresh excluded from natural claim/reconcile, and scoped Grok exclusion with natural/fake-marker regressions. Neither proves the hosted Supabase platform boundary.
- Concurrency: eight different queue operations compete for the same client/day/source; exactly one wins. Eight claim workers compete for that winner; exactly one wins. Restart and expired lease do not reclaim the consumed job. Real feed-lock waits extending beyond queue/claim expiry start no operation.
- Existing Python and JavaScript suites plus `git diff --check`: regressions must remain green. No production, provider, or messaging I/O is part of acceptance.

## Remaining production gates

1. Review and merge the implementation under explicit authority.
2. Approve exact migration bytes and independently verify ACLs.
3. Approve an exact-SHA OFF rollout and verify image/runtime and authenticated Studio provenance.
4. Approve one exact latest-source refresh execution; recheck its full readiness.
5. Design and separately authorize the refresh delivery lane and employee bindings; prove one private card without enabling public publication.
