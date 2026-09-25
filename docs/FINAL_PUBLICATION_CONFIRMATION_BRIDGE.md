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
decision. The callback result is at most a private decision receipt, not a
public approval, publication request, or Telegram/X delivery receipt.
The snapshot also binds an exact 40-character release SHA. A future owner must
compare that SHA with its own deployed runtime before honoring a decision.

The packet currently has **no live delivery owner, restricted callback route,
public approval transaction, or publication queue adapter**.
It is intentionally not part of the live bot. A signer key must be separate
from the private-card key and all publishing credentials; no key is provisioned
by this proposal. The existing bot remains the sole update consumer.

`supabase/proposals/content_ops_final_card_preflight.sql` now provides a
local-only, ungranted preparation check. It rechecks the parent private card,
the same principal's two checks, current version fingerprint and today's
fresh official source through the existing candidate owner. Its only positive
result is `ready_for_final_card` with `execution_authorized=false`; it does
not register or deliver a final card, record an approval, create an outbox,
or invoke a provider. The hosted catalog preapply is read-only and must be
rerun before any separately authorized installation. Disposable CI proves
normal eligibility and fail-closed missing check, wrong binding, stale source,
stale poll, revoked reviewer, existing approval and revoked parent card.

`supabase/proposals/content_ops_final_card_delivery_ledger.sql` is a separate
local-only, ungranted transport ledger and final-card registry. It reserves one
15-minute attempt for an exact parent review epoch, actor, version fingerprint,
snapshot, packet, and release. A future trusted courier must durably record
each of the four part attempts **before** invoking Telegram, then attach a
provider-verified message/response binding. A repeated attempt without a
receipt returns `delivery_unknown`: it is never permission to resend. Only
four confirmed sequential parts allow registration of the control card.
Synthetic receipts in the disposable test prove state transitions, **not**
real Telegram delivery. A supplied receipt is not independently authenticated
by the ledger; a future courier must verify it and bind the full message to
the expected room, bot, payload, and release. All ledger functions return
`execution_authorized=false` and have no runtime role grants. Registration is
not an approval and creates no publication or public outbox.

`core/content_ops/final_card_courier.py` now defines the matching **unmounted,
default-OFF** courier contract. It renders the exact banner and full Telegram/X
copy, binds four payload hashes to the snapshot, parent card, delivery UUID and
release SHA, checks canonical PNG bytes and 24-hour source age, and accepts
only direct, correctly ordered Telegram response evidence from an injected
sender. It stops after a reused/uncertain reservation or response and never
resends with another delivery ID. The injected DB owner must durably commit
each ledger write before returning; the runtime release SHA must come from
verified deployment provenance, not a callback or user message. Synthetic
unit tests use fakes only. `core/content_ops/final_card_owner.py` is the
matching injected PostgreSQL adapter: it is default OFF, uses fixed SQL
calls and a fresh committed transaction per step, and accepts only bounded
receipts. A lost commit ACK remains unknown. The ledger's ungranted,
read-only terminal function resolves only the original delivery UUID after
an uncertain registration; it cannot approve or send. There is still no
deployed owner, credential loader, route, live send, or real provider delivery
proof.

`supabase/proposals/content_ops_final_decision_ledger.sql` adds a **local-only,
ungranted** decision owner. A trusted ingress must authenticate the Telegram
callback and HMAC, then pass exact actor, four room/message/bot/human bindings,
current version fingerprint, snapshot hash, and deployed release SHA. Under the
content-item lock the owner checks the registered final card, expiry, same-actor
private checks, latest official source, current version, and zero existing
approvals/publications. It records one immutable decision per review. A hold
also invalidates the review epoch. A confirmation returns
`confirmed_pending_publication_owner`; it does **not** insert an approval,
publication, publish job, Typefully draft, or any dispatchable outbox row.
Replay of the original callback key returns the same decision ID, conflicting
keys/actions fail closed, and read-only terminal lookup handles an uncertain
commit. `core/content_ops/final_decision_owner.py` is its default-OFF,
unmounted fixed-SQL adapter with an injected trusted snapshot reader. The
disposable full-schema test uses only synthetic records and proves the absence
of public work. The final-card button text explicitly says a decision is not
queueing or sending.

The later public approval/publication owner must atomically re-read
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

The existing `public.record_studio_content_review_v2` is **not** this owner:
it records `reviewer_source='studio_session'` with no Telegram reviewer ID,
and a later `request_content_publication` is a separate transaction. Calling
it for a bot callback would misattribute the human decision and lose the
atomic approval/outbox guarantee. A dedicated, least-privilege decision RPC
must bind the signup-free private reviewer principal to an auditable approval
source that the existing `double-fact-check@1` publication gate recognizes.
That schema/contract change needs its own hosted compatibility proof and
explicit production authorization.

Only separately deployed channel owners may send, and each channel needs its
own provider receipt and public destination readback. Typefully may remain
draft-only until its scheduling/send authority is explicitly configured. The
private review courier must not hold public-channel credentials. No production
migration, deploy, enablement, Telegram or X call is authorized by this file.
