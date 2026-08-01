# ADR-008: Attributing Studio review decisions to a named reviewer

Status: Proposed. The approval screen and its state machine already ship; this
ADR covers only the identity that the recorded decision is missing.
Date: 2026-08-01

## Context

`README.md` still lists "approval/publishing screens" as a later delivery phase.
For approval that is no longer true, and planning against that sentence produces
a rebuild of something that already works. What ships today, verified in code:

| Capability | Where |
|---|---|
| Approve / request-changes buttons, 8 allowlisted reason codes, free-text note | `web/console/index.html:1813-1856` |
| Decision endpoint, session-gated, POST-only | `netlify/functions/library-review.mts:30-36`, `:139-141` |
| Decision validated (`approved`/`rejected`, ≤5 unique codes, ≤1000 chars, rejection requires a reason) | `netlify/functions/library-review.mts:72-82` |
| Stale-version guard returning `409` | `netlify/functions/library-review.mts:87-89` |
| Idempotency via required UUID header + partial unique index | `netlify/functions/library-review.mts:41-44`, migration `20260730202817:37-44` |
| `needs_review` precondition, mock content cannot be approved | migration `20260730202817:177-185` |
| Immutable `approvals` row, `content_items.status` transition, `event_log` entry, one transaction | migration `20260730202817:187-235` |
| Rejection reason codes fed back as bounded brand-learning signal | `get_brand_review_guidance`, migration `20260730202817:289+` |

The decision path is therefore complete, transactional, idempotent, and
auditable in every dimension except one.

**Every approval in the system is anonymous.** `record_studio_content_review`
inserts `reviewer_id => null` with `reviewer_source => 'studio_session'`
(migration `20260730202817:203-204`). It has to: the Studio authenticates one
shared `STUDIO_ACCESS_TOKEN` against a single access code
(`netlify/functions/studio-session.mts:99-107`), and the session cookie carries
only version, timestamps, and a nonce
(`netlify/functions/_shared/studio-session.mts:51-59`). Nothing in the request
distinguishes one reviewer from another, so nothing in the record can.

The original schema expected otherwise — `approvals.reviewer_id` was
`not null references auth.users(id)`
(`20260722115931_content_studio_foundation.sql:317-339`), with an index on
`(reviewer_id, created_at desc)` that today indexes a column that is always
null. Migration `20260730202817:7-17` relaxed it deliberately and encoded the
intended end state in a constraint:

```sql
(reviewer_source = 'supabase_auth'  and reviewer_id is not null)
or (reviewer_source = 'studio_session' and reviewer_id is null)
```

So the gap is known and the seam is already cut. What is missing is the smallest
thing that makes an approval answer "who".

This matters now for a concrete reason: content approved in the Studio becomes
brand-learning input for later generations. When a rejection reason later proves
wrong, there is currently no way to ask the person who filed it what they saw.

## Decision

Add **named reviewer codes**: replace the single shared access code with a small
set of per-reviewer codes, carry the reviewer's slug inside the signed session
cookie, and require it on every recorded decision.

Three changes, no new service and no new runtime dependency.

**1. Per-reviewer access codes.** `STUDIO_REVIEWER_CODES` holds a JSON object of
`{"<slug>": "<code>"}` — slug matching `^[a-z][a-z0-9_]{1,30}$`. Login iterates
the configured entries with the existing constant-time comparison and resolves
the slug of the matching code. `STUDIO_ACCESS_TOKEN` stays supported as the
implicit slug `shared`, so nothing breaks before the codes are distributed.

**2. `v2` session payload.** The cookie payload gains a slug segment:

```text
v1.<issuedAt>.<expiresAt>.<nonce>.<sig>              # existing, still verified
v2.<issuedAt>.<expiresAt>.<nonce>.<slug>.<sig>       # new
```

The HMAC key is **that reviewer's own code**, not a shared secret, so a slug
cannot be swapped without the corresponding code. Verification reads the slug,
looks up exactly one code, and verifies one signature — no iteration, no change
to the timing profile. `v1` cookies keep verifying against `STUDIO_ACCESS_TOKEN`
for the remainder of their 4-hour TTL, so the rollout logs nobody out.

**3. `approvals.reviewer_label`.** A new nullable `text` column, written from
the session slug. `record_studio_content_review` takes
`review_reviewer_label text` and rejects a `studio_session` decision without
one. `reviewer_source` stays `'studio_session'` and `reviewer_id` stays null —
this ADR does not introduce `auth.users` rows.

### What this buys, stated honestly

Attribution, not authentication. Two people who share a code are still
indistinguishable, and a reviewer who hands their code to someone else has
delegated their name along with it. That is the correct trade for a two-person
review team: it removes the "nobody knows who approved this" failure without
introducing per-user auth, session management, and an invite flow to a workspace
that has no second workspace and no external reviewers.

Supabase Auth remains the end state, and the constraint at
`20260730202817:14-17` is what it will switch on. Nothing here blocks it: an
`auth.users`-backed decision writes `reviewer_source => 'supabase_auth'` with a
real `reviewer_id`, and `reviewer_label` becomes redundant metadata rather than
something to unwind.

## Non-goals

Explicitly outside this scope, so the ADR is not later read as covering them:

- **Per-user Supabase Auth**, invites, and roles. Deferred to its own ADR.
- **A publish action.** Approval stays decoupled from publishing; the team
  publishes manually and records the resulting URL. That separation is
  deliberate and this ADR does not touch it.
- **A dedicated review-queue screen.** 보관함 already filters by 검토 필요
  (`web/console/index.html:850`). A default landing filter and an unread badge
  are worth doing and are not needed to make a decision attributable.
- **Backfilling historical approvals.** Decisions recorded before this change
  were genuinely anonymous; labelling them retroactively would fabricate a
  record. They keep `reviewer_label = null`, which reads as "shared-code era".

## Options considered

1. **Keep the shared code (status quo).** Zero work. Leaves an audit trail whose
   actor column is always null and an index on it that can never be used.
   Rejected.
2. **Named reviewer codes (chosen).** ~1 day. No new infrastructure, reversible
   by restoring one environment variable. Buys attribution, not identity.
3. **Full Supabase Auth per reviewer.** The correct end state and what the schema
   was designed for. Requires user provisioning, per-user JWTs through functions
   that currently run with a single server-side key, an invite flow, and RLS
   policy work — a multi-week change for a two-person team. Deferred, not
   rejected.
4. **A reviewer-name dropdown on the approval form.** Cheapest of all and
   worthless: any reviewer can select any name, so the record asserts something
   it cannot support. Rejected — a forgeable audit field is worse than an honest
   null.

## Consequences

**The routine must be dropped and recreated, not replaced.** Postgres keys
functions by argument list, so adding `review_reviewer_label` via
`create or replace` yields a second overload and an ambiguous PostgREST call.
The migration drops the existing signature, creates the new one, and re-grants
`execute` explicitly — a recreated function does not inherit the prior grants.

**No interaction with the PR #91 role grants.** `coineasy_batch_reviewer` holds
only `list_agent_batch_review_inbox` and `get_agent_batch_review_item`;
`record_studio_content_review` is called by the Netlify console under its own
credential and appears in no role in
`20260801090000_least_privilege_ledger_roles.sql`. The least-privilege assertions
in `supabase/tests/agent_batch_ledger_least_privilege.sql` are unaffected.

**The new column cannot be constrained retroactively.** Existing
`studio_session` rows have no label, so the "label required" check ships as
`not valid` and enforcement for new rows lives in the routine. Stated here so a
later reader does not mistake the `not valid` marker for an oversight.

**Rollout is reversible at every step.** Leaving `STUDIO_REVIEWER_CODES` unset
keeps the current single-code behaviour and writes `reviewer_label = 'shared'`;
removing it after rollout restores the prior state without a database change.

**Ordering.** Ship after the OriginTrail canary window (2026-08-01T15:00Z to
2026-08-16) or accept that a login change mid-window costs canary days if the
codes are mis-distributed. The change is small enough that waiting is cheap.

## Verification

A transactional test alongside the existing suite asserts that:

- a `studio_session` decision without `reviewer_label` is rejected;
- a decision with a label writes it verbatim and still leaves `reviewer_id` null
  and `reviewer_source = 'studio_session'`;
- the existing idempotency key path returns the first decision unchanged when
  replayed with a different label, rather than silently re-attributing it;
- the `needs_review` precondition, mock-approval block, and version guard still
  fail exactly as they do today.

Session tests cover: a `v2` cookie signed with reviewer A's code fails
verification when its slug is edited to reviewer B; an unexpired `v1` cookie
still verifies; and an expired `v2` cookie is refused.

## References

- `docs/ADR-007-least-privilege-ledger-credentials.md` — the credential-narrowing
  direction this ADR stays consistent with
- `supabase/migrations/20260730202817_studio_review_learning.sql` — current
  review routine, the relaxed `reviewer_id`, and the `supabase_auth` seam
- `supabase/migrations/20260722115931_content_studio_foundation.sql:317-339` —
  original `approvals` shape
- `netlify/functions/_shared/studio-session.mts` — session payload and signing
- `netlify/functions/library-review.mts` — the decision endpoint this extends
