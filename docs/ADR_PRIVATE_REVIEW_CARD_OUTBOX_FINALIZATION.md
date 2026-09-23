# ADR: Atomic finalization of the private review card and existing outbox

**Status:** Proposed (local-only, not authorized for production)
**Date:** 2026-09-23
**Deciders:** CoinEasy operator for production cutover; content-ops maintainers for implementation

## Context

The deployed private-review outbox owns one room delivery per exact content
version. The proposed four-part button card uses that same outbox's one-shot
claim/begin and durably reserves each Telegram send. After four direct success
responses, two durable records must agree: an active button card and a terminal
`sent` outbox containing the controls message ID. A lost database acknowledgement
must never authorize another Telegram call. The old link-card worker remains
separate until an explicit, single-owner cutover.

## Decision

In the local SQL proposal, store each directly validated Telegram message ID
beside its existing response hash and opaque message binding. After all four
parts are confirmed, the guarded registration function inserts the exact card
and transitions the same owned outbox to `sent` with the fourth message ID in
one transaction. An error rolls both writes back. The courier gets a bounded
receipt only after the commit acknowledgement; uncertainty stops sending and
requires read-only reconciliation by the original IDs.

The outbox's 120-second lease remains a hard deadline. Expiry or partial sends
are `delivery_unknown`, never automatically retried or converted into a new
card. No callback/approval/publication can be enabled by this proposal.

## Options considered

| Option | Complexity | Operating cost | Familiarity | Failure semantics |
| --- | --- | --- | --- | --- |
| Separate card insert then existing `finish` RPC | Low | Two commits/readbacks | High | A crash between commits leaves card and outbox disagreeing |
| Atomic card registration and outbox finalization (chosen) | Medium | One commit plus readback | Medium | Both records commit or neither does |
| New independent button-card outbox | High | Two delivery owners to monitor | Low | Duplicate staff-room cards remain possible |

All three options have negligible scale impact at the daily-card volume. The
atomic option adds a hosted privilege/compatibility proof but avoids a
cross-system repair procedure for a split terminal state.

## Consequences

- The direct response validator, not a webhook/callback payload, supplies the
  message ID. The ledger binds it to the same confirmed part, payload hash and
  response hash used in card registration.
- The outbox row is locked before registration; concurrent legacy `finish` or
  expiry cleanup cannot race the atomic transition.
- A committed terminal readback may report success after a lost ACK, but must
  never issue another send permission.
- The local proposal needs hosted-version schema, RLS, privilege and race
  checks before it can become a migration. No production grant or cutover is
  implied by this ADR.

## Action items

1. [x] Add exact message IDs and atomic finalization to the local SQL proposal.
2. [x] Prove success, mismatch rollback, duplicate denial and terminal readback
   on disposable PostgreSQL without provider I/O.
3. [ ] Validate the local single claim/begin/review-row runner against the
   hosted schema and complete read-only recovery; ensure the old worker and
   new courier cannot both own one version at runtime. The local runner is
   default-OFF and has no authenticated canonical-PNG reader yet.
4. [ ] Obtain separate authorization before any hosted migration, deployment,
   private-room canary, activation or public publication.
