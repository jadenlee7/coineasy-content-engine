# ADR-027: Attribute Telegram final review and hand off each publication channel

**Status:** Proposed; not approved for production
**Date:** 2026-09-25
**Deciders:** CoinEasy operator for production authority and client destination policy

## Context

The four-client private card records two checks for one immutable version. Its
second-stage final confirmation remains local-only in Draft PR #193. A Telegram
button is not a Studio login or an `auth.users` session. The hosted
`public.approvals` constraint currently accepts only `supabase_auth` with a
non-null `reviewer_id` or `studio_session` with a null `reviewer_id`. Calling
`record_studio_content_review_v2` from a Telegram callback would misattribute
the human decision. Calling it and then requesting publication in a separate
transaction would also allow an approval without its intended channel handoff.

The hosted private `content_ops_review_principals` table already gives a
signup-free, workspace-scoped UUID for each verified bot/human binding and has
a unique `(workspace_id, id)` key. The current exact Telegram publication RPC
only accepts Squid daily news. `TypefullyPublisher` can create drafts, but
there is no final-confirmation-to-Typefully outbox owner for all four clients.
The generic `request_content_publication` queue is not proof that a channel
worker can deliver or that it targets the right client account.

## Decision

Extend `public.approvals` with a nullable `review_principal_id` and a third
source, `telegram_principal`, valid only when `reviewer_id` is null and the
principal is non-null. A composite foreign key binds it to the same workspace.
Historical Studio and Supabase Auth approvals remain unchanged. The proposal
adds no runtime grant or callback route.

A future dedicated final-decision owner will lock item, review, card and
principal in a consistent order, recheck the latest official source, immutable
version, canonical PNG, full channel copy, both same-actor checks/epoch,
expiry, existing approvals/publications and channel readiness, then write one
attributed `double-fact-check@1` approval plus two channel-specific durable
handoffs in **one transaction**. On any missing channel owner, it must reject
the entire decision, not create a partial approval or claim a queued result.
Callback authentication and a MAC alone are never DB authority.

The Telegram handoff must use an exact-version, destination-pinned owner for
each client. The Squid-only RPC cannot be silently widened by the UI. The X
handoff initially creates a Typefully draft with `publish_at: null`; explicit
final scheduling/public send requires its own authority and provider receipt.
Every channel keeps a separate unknown-outcome/no-resend ledger and public
readback. No staff-room courier receives public-channel credentials.

## Options considered

| Option | Complexity | Cost | Main consequence |
| --- | --- | --- | --- |
| Reuse `studio_session` approval | Low | Low | False human attribution and split transactions; rejected |
| Require Supabase Auth signup | High | Medium | Correct identity, but conflicts with the approved signup-free staff workflow |
| Workspace-scoped Telegram principal (chosen) | Medium | Low | Honest attribution; needs a narrow schema extension and dedicated owner |

## Consequences

- Existing `require_double_fact_check_approval` can inspect the new approval
  because it already checks policy, checks, exact version and latest sequence;
  it does not authenticate the Telegram principal. The new owner must do that.
- The approval constraint change is small, but is a production migration and
  requires explicit operator authorization, hosted pre/post ACL checks and an
  exact release fence before use.
- A green local card test is not a live publisher. The final button remains OFF
  until the DB owner, all four Telegram destinations, Typefully draft owner,
  callback registration and real private canary are independently verified.
- A lost commit acknowledgement is reconciled read-only by the same callback
  and operation IDs. An unknown provider send is never retried blindly.

## Action items

1. [x] Pin the hosted approvals/principal contracts read-only.
2. [x] Draft the attribution-only SQL proposal; no migration or grant.
3. [x] Prove the proposal on a disposable full schema, including historical
   approvals, cross-workspace FK rejection and least-privilege ACLs.
4. [x] Draft an ungranted, read-only final-card preparation gate with
   same-actor checks and action-time source freshness.
5. [ ] Implement the exact final-card delivery registry and one-transaction
   decision owner; the preparation gate is not a publication authority.
6. [ ] Implement and verify all four exact Telegram channel owners and the
   Typefully draft handoff; hold any unsupported destination.
7. [ ] Obtain separate authorization for production migration, deployment,
   enabling a private canary, and each public channel activation.
