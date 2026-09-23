# ADR: Claim-bound access to the canonical private review-card PNG

**Status:** Proposed (local-only; no hosted change or send authorized)
**Date:** 2026-09-23
**Deciders:** CoinEasy operator for production cutover; content-ops maintainers for implementation

## Context

The private four-part review card must send the exact immutable PNG attached to
the claimed content version. The courier must not hold a broad Supabase
service-role credential or accept an arbitrary image URL, staff-uploaded bytes,
or an asset from another version. A failed, expired or already-begun claim must
not open a new image read. The existing Netlify review gateway already has a
bounded service-role database path behind a release, token and canary fence.

## Decision

Keep the service-role key inside that gateway. A local SQL proposal exposes a
service-role-only locator for the exact claimed outbox, token and version. It
rechecks the current candidate, active source, asset metadata and Storage
object, then returns an internal bucket/path/hash/size but no URL or bytes.
The gateway uses the locator once, reads the private Storage object itself,
checks size, PNG signature and SHA-256, and returns only the verified bytes
with exact release and claim-binding headers. The one-shot Python client
checks those headers, size and hash again before permitting `begin`. An image
failure or unknown acknowledgement is terminal for that attempt. The same
gateway object must own claim, image read and begin; the runner remains OFF.

This is a local implementation proposal only. The SQL has not been applied,
the Netlify handler has not been deployed, and no runtime or sender is wired.

## Options considered

| Option | Complexity | Credential reach | Failure semantics |
| --- | --- | --- | --- |
| Give the courier a service-role key | Low | Broad DB and Storage access in another process | Easier to read an unrelated asset |
| Issue a signed Storage URL | Medium | Temporary bearer URL can escape logs/receipts | URL lifetime can outlive the claim |
| Claim-bound gateway read (chosen) | Medium | Existing gateway holds the only broad key | Exact version/hash checks fail closed before `begin` |

## Trade-off analysis

The chosen path adds one database locator call and one private Storage read
per canary, plus a bounded 10 MB in-memory verifier. At daily-card volume this
is modest, but it increases gateway code and requires hosted-schema, Storage
ACL and runtime tests before use. A claim can expire between locator and
Storage GET; therefore the reader does not grant delivery, and `begin` must
independently revalidate the claim. Neither a successful PNG response nor a
preview is a send authorization.

## Consequences

- No Storage path, signed URL, provider response, token or image bytes are
  included in the review-card receipt or normal error messages.
- Wrong token/version, stale source, missing object, changed bytes, oversize
  body, redirect and transport uncertainty fail closed without a retry.
- The legacy link-card mode and its existing `finish` flow remain unchanged.
- The proposed SQL needs hosted-version/RLS/ACL compatibility proof, and the
  gateway needs a default-OFF exact-release deployment before any canary.

## Action items

1. [x] Add local claim-bound locator, byte-verifying gateway and one-shot
   client with synthetic denied/mismatch tests.
2. [ ] Prove the SQL against the actual hosted schema and service-role/Storage
   privileges without sending, then validate exact release fences.
3. [x] Enforce the same concrete gateway object for claim/read/begin in the
   unmounted canary runner.
4. [ ] Mount one single owner only after separate production authorization and
   a default-OFF rollout.
5. [ ] Obtain distinct operator approval for any private-room canary; public
   Telegram or X publication remains separately gated.
