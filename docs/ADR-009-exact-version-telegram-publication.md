# ADR-009: Publish the exact approved Daily News version to Telegram

Status: Accepted for a Squid-only canary behind disabled-by-default flags

Date: 2026-08-01
Deciders: CoinEasy Content Studio operators

## Context

Content Studio already stores immutable generated versions and records an
idempotent review decision. It does not publish those versions. The older
`/clients/{client_id}/publish/daily-news` route starts a new X/LLM generation
and then publishes that unrelated result. It therefore cannot be connected to
the Studio approval button.

Telegram's Bot API has no caller-supplied idempotency key. If a request times
out after Telegram accepts it, automatically repeating the request can create a
duplicate public post. The first publication slice must prefer a visible manual
reconciliation state over an automatic duplicate.

Daily News is the only current content kind with one version-bound PNG in
private Storage. Article banners are reproducibly rendered but are not stored
as immutable assets, and Tutorial has multiple pages. The first slice is
therefore limited to Daily News.

## Decision

Add a separate exact-version Telegram publication path with these boundaries:

1. A signed human Studio session may request publication only after an explicit
   confirmation. Approval never triggers publication automatically.
2. A service-role RPC atomically rechecks the current approved, non-mock Daily
   News version, its exact approval row, Telegram copy of at most 1,024
   characters, and its private PNG before creating one publication and one
   publish job.
3. The job pins the approval, immutable content version, and asset IDs. The
   caption is read only from that version. A worker never reads a newer current
   version and never accepts copy, image URLs, or channel targets from the
   browser.
4. Before the Telegram request, the worker downloads and verifies the private
   PNG, confirms the configured public channel with `getChat`, hashes the exact
   outbound request, and commits a durable attempt marker.
5. The worker performs one multipart `sendPhoto` request with the stored
   `channel_copy.telegram` as plain caption. It does not reformat, truncate,
   enable parse mode, call `sendMessage`, redirect, or retry.
6. A verified receipt atomically marks the publication `published`, the job
   `succeeded`, and the item `published`. A known pre-delivery failure may be
   retried within the job budget. Once the attempt marker exists, a timeout,
   transport failure, invalid response, or expired lease becomes
   `delivery_unknown` and is never sent automatically again.
7. Immediate processing may be kicked through a dedicated internal worker
   credential. A bounded Railway worker drains jobs as a backstop. Neither path
   accepts an item, version, asset, caption, or URL from its caller.
8. Both Netlify and Railway feature flags default to off. The first live
   allowlist contains only `squid`; public canary activation is a separate
   operational step.
9. Rollback reconciliation is a separate recovery-only process. It is allowed
   only while Railway publication is disabled and can call only the bounded
   expired-lease RPC; it has no claim or Telegram publisher capability.
10. Both the cron and API execution planes, including recovery, require the
    Railway GitHub-origin runtime commit to exactly match the operator-approved
    `TELEGRAM_PUBLICATION_RELEASE_SHA`. The comparison accepts only lowercase
    40-hex values and fails before constructing a worker or repository.

## Options considered

### Reuse the legacy publish route

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Source fidelity | Unacceptable; it regenerates content |
| Duplicate protection | None |
| Auditability | Not connected to Studio versions |

Rejected because the posted content would not be the approved version.

### Publish synchronously from Netlify

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Latency | Low |
| Failure recovery | Weak across the Netlify/Telegram boundary |
| Credential isolation | Telegram credentials would move to Netlify |

Rejected because a lost function response cannot safely distinguish a failed
request from an accepted Telegram post.

### Durable exact-version job with a delivery fence

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Source fidelity | Exact immutable version |
| Duplicate protection | Fail-closed after delivery starts |
| Auditability | Publication, job, receipt, and event are version-bound |

Accepted. It adds more state transitions but makes the unsafe ambiguity visible
instead of hiding it behind retries.

## Consequences

- Squid Daily News can move from `approved` to a version-specific Telegram
  publication without another model call.
- `delivery_unknown` requires a human to inspect the public channel. If the
  exact message exists, its canonical public URL can be recorded through the
  existing observation form; that records evidence and never sends another
  message. This is deliberate because strict exactly-once delivery is not
  available from the Telegram Bot API.
- Article and Tutorial remain manual until their complete publishable visual
  packages are immutable assets.
- The existing legacy route remains only for dry-run previews. All live calls,
  regardless of client or channel, are rejected because the route regenerates
  and publishes without an immutable, double-fact-checked Studio version.
- Netlify keeps only the Studio, database, Railway URL, and internal kick
  credentials. Public Telegram bot and channel credentials stay on Railway.

## Verification and rollout

1. Apply the migration and run the transactional SQL security smoke.
2. Run Python and Netlify tests with all publication flags disabled by default.
3. Deploy the worker with `squid` allowlisted and both feature flags still off.
   Validate the disabled container result and the explicit `--validate-only`
   result from a GitHub-origin deployment. The latter must report only
   `runtime_release_verified:true` for the exact approved commit and no provider
   or database calls. Neither mode can claim a database job or call Telegram.
4. Deploy the Studio API/UI with publication still disabled and verify it cannot
   queue work.
5. The production path intentionally pins `@squid_kor_update`; changing only an
   environment variable cannot redirect it to a private channel. A real
   non-production canary therefore requires an isolated workspace/deployment
   whose database and code explicitly allow a disposable public test channel.
6. Compare the stored version, PNG hash, caption, simulated provider receipt,
   and publication row in automated tests. Run the read-only active-queue gate
   from the publication runbook before every activation. Only after those tests
   and any isolated canary pass may an operator explicitly authorize the first
   official Squid post and enable both Railway execution planes before Netlify.
   Yellow is a later code and database allowlist expansion, not an
   environment-only change.
