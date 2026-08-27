# GTM owner-reader readiness snapshot — 2026-08-25

## Outcome

The local Squid GTM inbox can now compose strict sanitized owner records for
Railway ops, Telegram triage, and public-X/content QA. It is ready for a local
shadow pilot, but it is **not live-connected**: no authenticated owner reader,
Telegram FAQ producer, X/QA join, Grok connection, schedule, or deployment has
been installed.

This snapshot records a read-only audit at approximately
`2026-08-25T19:32:34Z`. The audit read Railway deployment metadata only. It did
not read raw logs, environment variables, request bodies, private payloads, or
credentials and did not mutate or deploy anything.

## Readiness matrix

| Domain | Safe owner data observed | Missing production component | Current gate |
| --- | --- | --- | --- |
| Railway ops | Current service/deployment status, source repo, commit SHA, cron expression, next cron time, and instance state can be read without logs | Authenticated sanitizer that emits the closed GTM receipt; cron execution receipt for last-run success/failure | Local adapter ready; live reader not installed |
| Telegram triage | `coineasydaily` is running and its existing process remains the sole update consumer | Owner-side post-redaction FAQ/question projection with HMAC, original question time, explicit safety class, and canonical FAQ binding | Do not connect until owner producer exists |
| X narrative/QA | Content engine and Grok QA deployments expose current commit metadata; existing code has official-X state and exact QA contracts | Authenticated public-X/QA owner projection that joins exact public URL, current content/version, QA subject, and deterministic receipt | Local adapter ready; live reader not installed |

## Read-only Railway observation

The relevant production metadata observed during this audit was:

| Service | Observed state | Commit/source | Safe interpretation |
| --- | --- | --- | --- |
| `coineasy-content-engine` | deployment `SUCCESS`, instance `RUNNING` | `a0c60dbad30ad0c05d13dc12421ecbb7129cf94d`, matching local `origin/main` | Suitable primary ops subject after an authenticated sanitizer is added |
| `coineasy-grok-qa` | deployment `SUCCESS`, cron `*/5 * * * *`, instance `EXITED`, next cron advanced to `2026-08-25T19:35:00Z` | same `a0c60d...` SHA and `jadenlee7/coineasy-content-engine` | `EXITED` is compatible with a completed cron process, but metadata alone does not prove the last job succeeded; emit schedule/runtime as `unobserved` until a safe run receipt exists |
| `coineasydaily` | deployment `SUCCESS`, instance `RUNNING` | `d2b9e1489a919d8fe9d71f629a6e220d5b9934fd` | Confirms the owner process is live; it does not provide a safe FAQ/question projection |
| `squid-korea-gtm` | old deployment `FAILED`, no active instance | separate repo `jadenlee7/squid-korea-gtm`, SHA `de52cd22a00b88071e38091cf31bb7e778885305`, start `python alpha_bot.py` | Treat as a separate legacy/owner decision; do not fold it into the new inbox or call it an active incident without operator confirmation |

The GTM ops bundle supports multiple unique Railway service records. This lets
the eventual owner reader include both the content engine and Grok QA service
without collapsing them into one health claim. It must not infer a healthy
cron run from `deployment=SUCCESS`, `instance=EXITED`, or an advancing next-run
timestamp alone.

## Telegram owner gap

The existing Telegram flow owns `getUpdates`, the update offset, and its Redis
buffers. Some current downstream buffers contain raw user/chat-level fields,
and consuming a buffer may clear it. The GTM reader must therefore never call
`getUpdates`, import the owner buffer, call a destructive `take`, copy/advance
the offset, or create a second poller.

The minimum safe owner-side producer is one branch inside the existing owner
process that emits only `coineasy-telegram-owner-projection@1` after redaction.
It must create the question HMAC with an owner-held key, preserve the original
question observation time, require an explicit safety class, and bind any FAQ
draft to the exact FAQ source and draft bytes. No such producer was observed in
the current owner code, so live Telegram triage remains intentionally
`unobserved`.

## X and QA owner gap

The source adapter accepts exact public X status URLs and current content/QA
owner assertions. A completed QA receipt is deterministic over its exact
source/content/version/banner/verdict/issue subject, preventing one receipt
value from being reused for another subject. These are internal consistency
checks, not proof that the post, content version, or owner receipt currently
exists.

The remaining component is an authenticated, read-only owner projection that
produces this compact record from the official-X state and exact QA owner
receipt. Grok QA's current MCP/verdict tools remain unchanged and are not used
as the GTM reader.

## Recommended implementation order

1. Add the owner-side Telegram sanitized projection in the `coineasydaily`
   owner flow, with no new poller and no deploy in the same approval step.
2. Add an authenticated read-only Railway sanitizer for selected services,
   using metadata plus a safe cron run receipt and never raw logs.
3. Add an authenticated public-X/content-QA projection with exact current
   version and deterministic QA receipt binding.
4. Run the local Squid shadow bundle for several observations and compare each
   claim with its owner surface.
5. Request separate approvals for deployment and for any Grok/provider reader
   connection. Sending, publishing, verdict submission, and production
   mutations remain out of scope.

## 2026-08-27 local Telegram v2 delta

The earlier v1-only readiness snapshot remains historical. A later local
contract now validates the owner v2 event only when an atomic evidence snapshot
binds the exact stream row, current event index, source index, promotion
marker, intake marker, and sanitized gate. It derives a separate v2 triage item
and a default-disabled, process-memory-only append receipt. It does not widen
the saved source bundle or Phase 0 page to accept bare v2 JSON.

This closes the local schema/eligibility/receipt design gap, but not the live
reader gap. Production is still blocked on an authenticated atomic read
adapter, Redis ACL/retention, durable append-only receipt storage, zero-event
control records, consumer-group/ACK/quarantine policy, CI, reviewed SHA,
deployment, and service readback. See
`docs/ADR-022-strict-telegram-v2-intake-reader.md`.
