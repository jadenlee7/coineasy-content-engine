# ADR: Narrow owner authority for private button-card delivery

**Status:** Proposed (local-only; production cutover not authorized)
**Date:** 2026-09-23
**Deciders:** CoinEasy operator for production authority; content-ops maintainers for implementation

## Context

The one-shot courier needs to prepare an exact review row, bind the already
begun outbox, reserve and confirm four private-room sends, atomically register
the complete card, and read its terminal state. The local owner functions live
in the `private` schema, are `SECURITY INVOKER`, and their FORCE-RLS tables
grant no runtime role access. The courier must not receive a broad Supabase
service-role key or table-level mutation rights. The existing authenticated
Netlify review gateway already holds the database key behind an exact release,
token, production host, default-OFF button mode, and one-version canary scope.

## Decision

Propose one public, service-role-only `content_ops_button_card_owner_step` RPC
as a narrow bridge to six fixed private functions. It rejects all other
actions and extra argument keys, pins every call to a server-supplied workspace
and content version, and delegates each state transition to the existing
claim/outbox/part/card guards. It does not grant any direct table access or
private-function execute right to a courier role. Netlify accepts an `owner`
request only in `button_card_v1`, validates exact argument and receipt shapes,
and makes one bounded RPC call. Provider errors and SQL details are redacted.

The SQL remains in `supabase/proposals/`, not migrations. The existing Python
`PostgresPrivateCardOwner` remains an unmounted local contract; the new
default-OFF `GatewayPrivateCardOwner` uses the same one-shot gateway as claim,
image and begin. A separate default-OFF one-shot runtime and service manifest
are prepared locally but have not been deployed. This decision
does not authorize a hosted grant, deployment, private-room send, approval or
publication.

## Options considered

| Option | Complexity | Authority surface | Failure semantics |
| --- | --- | --- | --- |
| Direct DB login with table policies | High | Multiple FORCE-RLS table grants and private functions | Hard to prove least privilege across all mutations |
| Put service-role key in courier | Low | Broad project database/Storage access in sender runtime | Credential compromise can escape the card scope |
| Reuse fenced gateway with one narrow owner RPC (chosen) | Medium | Existing gateway holds broad key; courier gets only fixed actions | One RPC per step, unknown ACK remains terminal/read-only |

## Trade-off analysis

The chosen route avoids another privileged credential in the Telegram sender,
at the cost of more gateway validation and network round trips. Daily review
volume is low, but the claim's short lease still bounds all four sends; no
HTTP retry is permitted after an unknown response. An owner RPC receipt alone
is not a provider-send permit: only a new durable reservation and the
courier's direct Telegram response validation can advance a part. The public
RPC's security-definer ownership, RLS bypass behavior and hosted function ACL
must be proved on the actual Supabase version before migration.

## Consequences

- The button-card path remains OFF and cannot use the legacy link-card `finish`
  endpoint or publish to a client channel.
- The courier can eventually use one release-pinned gateway client for claim,
  image, begin and owner steps without a DB service-role credential.
- Hosted schema/ACL compatibility, default-OFF deployment, single-owner
  cutover and private-room canary each require separate evidence and authority.
- A lost reserve/confirm/register acknowledgement stops automatically; only
  exact-ID readback may reconcile, never replay a Telegram send.

## Action items

1. [x] Add an unhosted service-role-only owner RPC proposal with six fixed
   actions and no direct table grants.
2. [x] Add bounded Netlify owner routing, exact receipt projection, synthetic
   PostgreSQL/ACL and gateway tests.
3. [x] Add a one-shot Python HTTP owner adapter and test the complete
   claim/image/prepare/begin/reserve/confirm/register/readback chain with
   synthetic database and Telegram responses.
4. [ ] Validate the proposal against hosted schema and ACLs before requesting
   production migration, deployment or a private-room canary.
5. [x] Prepare an unmounted, default-OFF one-shot runtime and validate-only
   service manifest with no cron or automatic retry.
