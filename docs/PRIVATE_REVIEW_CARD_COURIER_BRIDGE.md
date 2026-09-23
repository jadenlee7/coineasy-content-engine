# Existing-bot private card courier bridge

Status: **local preparation only; not mounted, not deployed, no send**.

`core/content_ops/private_review_card_receipt.py` renders a private-only
four-part card for Yellow, Babylon, Squid or OriginTrail and validates direct
Telegram success responses before building the argument set for
`private.record_content_ops_button_card`. The card contains the canonical
banner, complete Telegram copy, complete X copy, then six edit/check/hold
buttons. It contains no approval/publish control and no Studio login link.

`core/content_ops/private_review_card_courier.py` now connects that validator
to a default-OFF one-shot sequence through injected `CardOwner` and
`CardSender` contracts. A durable `new_attempt=True` reservation is required
before **each** provider call and a separately committed exact-response
confirmation is required before the next part. A reused/unknown reservation,
provider result, confirmation or registration acknowledgement stops the sequence; controls cannot be sent
after an incomplete image/copy packet. The local guard also rejects a second
run with the same card ID in one process. Neither adapter is implemented or
mounted, so this is not a live delivery path.

The module has no network, database or polling code. Its response parser is
not an authentication boundary: the eventual one-shot courier must own the
review-bot token and the HTTP call, pass its *own* response bytes, and ensure
the exact PNG bytes match the immutable version hash. It must never accept
response bytes from a webhook, staff message or remote caller.

Before enabling or sending even one card, the remaining owner path must:

1. Re-read the exact current version, active official source and fresh poll,
   canonical PNG and Grok QA state; reject previous approvals/publications,
   stale sources, duplicates and changed fingerprints.
2. Implement the `CardOwner` adapter: create one short-lived button-review row,
   **durably reserve each send attempt before** the corresponding Telegram
   call, confirm its direct response before the next call, and register the
   complete card once. Keep this owner's DB authority
   separate from the callback bot's restricted role.
3. Implement `CardSender` using the already-deployed bot token for **send-only** calls. Do not start a
   second `getUpdates` consumer or webhook; keep the existing bot's private
   callback route OFF until a complete card is registered.
4. Send image, Telegram copy and X copy in order, recording each authenticated
   exact-room/bot response. Send controls only after all three are confirmed.
   On rejection, timeout or unknown commit, stop with no automatic retry.
5. Call `record_content_ops_button_card` once with the validated four-response
   evidence, then read the committed exact card and release-fence state. An
   uncertain DB commit is reconciled read-only by the original IDs, never
   retried with a new card or provider send.
6. Only after a default-OFF deployment, validate-only checks and separate
   operator authorization may staff activation and a single private canary be
   considered. This preparation does not authorize public Telegram or X.

The observed 2026-09-23 Babylon content/version IDs in the local readiness
receipt are a snapshot, not a reservation. Re-evaluate source freshness at
action time; do not hard-code them into a worker or production setting.
