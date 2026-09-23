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
run with the same card ID in one process. The orchestration is not mounted,
so this is not a live delivery path.

`core/content_ops/private_review_card_sender.py` now implements the isolated
send-only Telegram side. Its mock-HTTP tests cover the existing bot username,
exact private supergroup/member preflight, four bounded card sends, duplicate
send denial, strict part order and provider-error redaction. It targets the
private room's main timeline; topic/thread delivery remains unsupported. It
does **not** call `getUpdates`,
install a webhook or discover a token. It is not configured or deployed.
The durable DB owner is not deployed or mounted, so the courier cannot run live.

`supabase/proposals/content_ops_button_card_send_ledger.sql` and
`core/content_ops/private_review_card_owner.py` now sketch the missing durable
side: default-OFF, one DB transaction per outbox binding/reservation/confirmation, and a
guarded registration wrapper comparing all four payload, message and response
hashes. This SQL is deliberately a **proposal**, not an applied migration; the
owner has no runtime connection, grant or mounted entrypoint. Static SQL tests
and fake-transaction tests do not establish hosted PostgreSQL compatibility.
The disposable, network-isolated PostgreSQL 16 verifier in
`scripts/verify_private_card_ledger_docker_local.mjs --local-only` additionally
checks the old outbox's exclusive claim/begin, exact-version binding,
four-part reservation/confirmation, duplicate rejection, terminal-outbox
denial, rollback and runtime-role ACL denial. It is still synthetic local evidence, not production
schema compatibility or a delivery receipt.

The local ledger proposal now requires an exact `sending` row in the existing
`content_ops_review_outbox`, its claim token, packet hash and unexpired lease
before a button-card part can be reserved. The existing claim/begin transition
is one-shot, so the old link-card path cannot begin the same outbox twice.
The courier requires a committed binding receipt before the first provider
call and checks that the existing outbox's packet SHA-256 covers the exact
four rendered payload hashes, review ID and card ID. This is **not** a live ownership switch: no production migration, runtime
role/grant, claim/begin caller, review-row creator, finish/readback bridge or
entrypoint exists. The current deployed worker must not be run alongside a
new button-card dispatcher until a single owner is selected for that run.

The pure receipt module has no network, database or polling code. Its response parser is
not an authentication boundary: the eventual one-shot courier must own the
review-bot token and the HTTP call, pass its *own* response bytes, and ensure
the exact PNG bytes match the immutable version hash. It must never accept
response bytes from a webhook, staff message or remote caller.

Before enabling or sending even one card, the remaining owner path must:

1. Re-read the exact current version, active official source and fresh poll,
   canonical PNG and Grok QA state; reject previous approvals/publications,
   stale sources, duplicates and changed fingerprints.
2. Complete the exclusive existing-outbox claim/begin/finish integration and
   live-validate the `CardOwner` path: create one short-lived
   button-review row, **durably reserve each send attempt before** the corresponding Telegram
   call, confirm its direct response before the next call, and register the
   complete card once. Finish the same outbox using the verified controls
   message ID. Keep this owner's DB authority
   separate from the callback bot's restricted role.
3. Package and validate the `CardSender` with the already-deployed bot token
   for **send-only** calls. Do not start a second `getUpdates` consumer or
   webhook; keep the existing bot's private callback route OFF until a
   complete card is registered.
4. Send image, Telegram copy and X copy in order, recording each authenticated
   exact-room/bot response. Send controls only after all three are confirmed.
   On rejection, timeout or unknown commit, stop with no automatic retry.
5. Call the guarded card-registration wrapper once with the validated
   four-response evidence, then read the committed exact card and release-fence state. An
   uncertain DB commit is reconciled read-only by the original IDs, never
   retried with a new card or provider send.
6. Only after a default-OFF deployment, validate-only checks and separate
   operator authorization may staff activation and a single private canary be
   considered. This preparation does not authorize public Telegram or X.

The observed 2026-09-23 Babylon content/version IDs in the local readiness
receipt are a snapshot, not a reservation. Re-evaluate source freshness at
action time; do not hard-code them into a worker or production setting.
