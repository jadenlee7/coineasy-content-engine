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
11. Operational closure of `delivery_unknown` is orthogonal to the delivery
    state. The publication remains `delivery_unknown`, its job remains `failed`,
    and the original request, response, attempt and error evidence remain
    immutable.
12. Negative operational closure requires a bounded canonical public-channel
    audit, a non-mutating inspect RPC, a separate approve RPC that appends an
    immutable exact approval receipt and event, and only then a resolve RPC that
    appends one resolution receipt. Not observing a matching public message at
    the checked time is not proof of non-delivery.
13. Inspect, approve and resolve are available only to a dedicated no-login
    `coineasy_telegram_resolution` role through three separate production JWTs
    with `telegram_delivery_unknown_inspect`,
    `telegram_delivery_unknown_approve`, and
    `telegram_delivery_unknown_resolve` capabilities. Their phase and exact
    claims are not interchangeable. None has provider, claim, requeue, resend,
    publication, or job creation authority.
14. A canonical positive manual observation remains separate `published`
    evidence and can also clear the operational activation blocker. The existing
    manual-observation RPC permits this only while the exact target version is
    still current; it is not a historical backfill path after a newer version
    becomes current. The evidence row neither rewrites nor deletes the unknown
    attempt or an earlier resolution receipt.
15. Before activation, the operational queue gate blocks `delivery_unknown`
    attempts that have neither an append-only non-resend resolution receipt nor
    a canonical positive manual-observation row. Either accepted evidence path
    removes that incident through an anti-join; neither authorizes resending the
    old version or publishing a new one.

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

### Mutate `delivery_unknown` to a definitive terminal status

| Dimension | Assessment |
|---|---|
| Operational simplicity | High |
| Forensic fidelity | Unacceptable; observation absence is not delivery proof |
| Late positive evidence | Ambiguous after the original state is overwritten |
| Duplicate protection | Easy to misread as safe-to-resend |

Rejected. `cancelled`, `not_delivered`, or another replacement status would
claim more than the evidence establishes and weaken the irreversible delivery
fence.

### Append an operational resolution receipt

| Dimension | Assessment |
|---|---|
| Operational simplicity | Medium; queue reads require an anti-join |
| Forensic fidelity | Preserves the exact unknown transport state and failed job |
| Late positive evidence | Supported as a separate canonical observation |
| Duplicate protection | Old attempt stays permanently non-claimable and non-resendable |

Accepted. The receipt records an explicitly approved operator disposition,
bounded public audit, immutable hashes, and zero resend authority without
rewriting delivery evidence.

## Consequences

- Squid Daily News can move from `approved` to a version-specific Telegram
  publication without another model call.
- `delivery_unknown` requires a human to inspect the public channel. If the
  exact message exists, its canonical public URL can be recorded through the
  existing observation form; that records evidence and never sends another
  message. This is deliberate because strict exactly-once delivery is not
  available from the Telegram Bot API.
- If no match is observed in a bounded public audit, a dedicated-role inspect
  RPC can derive an exact approval subject. A separate phase JWT and approve RPC
  append an immutable approval receipt and bounded event for that exact subject.
  Only a third resolve JWT can consume the unexpired receipt and append an
  immutable `operator_closed_without_resend` resolution while the publication
  remains `delivery_unknown` and the job remains `failed`. The resolution means
  only that the incident was operationally closed without resend.
- Unknown attempts with a non-resend resolution receipt or a canonical positive
  manual-observation row are excluded from the activation blocker through an
  anti-join. They remain non-claimable and non-resendable. The existing manual
  observation RPC can add that positive evidence only while the target version
  remains current.
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
7. Test the delivery-unknown resolution migration in a disposable database.
   Prove the inspect phase is non-mutating; inspect, approve and resolve JWTs
   cannot cross phase boundaries; approve writes one immutable approval receipt
   and one bounded event; resolve requires that exact unexpired receipt and
   writes only one append-only resolution receipt and bounded event; the
   publication/job transport rows remain unchanged; exact replay is idempotent;
   changed subjects fail; current-version positive manual observation clears the
   activation blocker; non-current historical observation fails closed; and
   anon, authenticated, service-role, worker and browser callers cannot execute
   any of the three RPCs.
   Prove concurrent resolves produce one receipt/event, stale repeatable-read
   snapshots cannot mutate terminal-unknown originals or invoke a resolution
   phase, and all three phases use timezone-independent UTC subject hashes.
8. Merge readiness does not authorize production migration application or
   deployment. Applying the migration, issuing each phase-specific credential,
   inspecting one production tuple, approving its exact subject, resolving it,
   and later activating publication are separate operator gates with independent
   readback.
