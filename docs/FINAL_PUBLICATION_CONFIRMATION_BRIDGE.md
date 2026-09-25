# Exact-version final publication confirmation (local proposal)

Status: **local-only, unmounted, default OFF; no approval or public send**.
This is the second stage after the separate private source and copy/banner
checks. It is not the cancellation-button `confirmation_*` flow.

`core/content_ops/final_publication_confirmation.py` produces an exact-version
four-part private-room packet shape: canonical banner identity (SHA-256; a
future courier must obtain and verify the actual PNG bytes), full Telegram
copy, full X copy, and a final control card showing the client Telegram
destination and Typefully target. The Typefully target is an account/social-set label, not an
asserted public X handle. Buttons use a dedicated `ce2:` namespace and a
15-minute MAC over the current version, DB-issued fingerprint, review/card,
same-reviewer checks/epoch, full copy, banner hash, and trusted room. A `ce1:`
private-card check or legacy approval token cannot be accepted as a `ce2:`
decision. The callback result is at most an owner-queued intent, not a Telegram
or X delivery receipt.

The packet currently has **no delivery owner, DB-backed final-card registry,
restricted callback route, approval transaction, or publication queue adapter**.
It is intentionally not part of the live bot. A signer key must be separate
from the private-card key and all publishing credentials; no key is provisioned
by this proposal. The existing bot remains the sole update consumer.

The trusted final decision owner, when implemented, must atomically re-read
and lock all of the following on the exact callback message and version:

1. Complete registered final card and authenticated same-room actor; active
   per-client reviewer grant, no bot actor, unexpired card, non-reused decision.
2. Current immutable `needs_review` daily-news version, exact DB fingerprint,
   canonical PNG bytes/hash, unchanged Telegram/X copy and destination policy.
3. Two private checks by **this same actor and epoch**, no intervening edit,
   hold, newer version, stale source, inactive feed, or changed official latest
   tweet; current Grok/QA and policy gates must not be inferred from the card.
4. Zero earlier approvals/publications and no pending, failed or unknown
   channel attempt that could be duplicated. One atomic approval plus distinct
   Telegram and Typefully/X outbox entries, or no write. A lost commit ACK is
   reconciled read-only by the original callback/operation IDs, never retried
   with a new ID.

Only separately deployed channel owners may send, and each channel needs its
own provider receipt and public destination readback. Typefully may remain
draft-only until its scheduling/send authority is explicitly configured. The
private review courier must not hold public-channel credentials. No production
migration, deploy, enablement, Telegram or X call is authorized by this file.
