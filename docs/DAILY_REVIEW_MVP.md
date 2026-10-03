# Daily private review MVP

Status: local implementation verified; not enabled or delivered in production.
Current no-send readback and remaining rollout gates:
`DAILY_REVIEW_PREFLIGHT_20261003.md` (dated evidence, not activation approval).

직원 운영 목표: 매일 최신 공식 소스 1건씩을 한국어 배너·Telegram 공지안·X
게시안으로 검수하고, 수정/사실 확인 후 사람이 정확한 버전을 승인합니다.
현재 추가한 버튼은 수정·확인·보류용입니다. 공식 채널 자동 게시와 Typefully
발행은 연결하거나 활성화하지 않았습니다.

## Daily-only intake increment, 2026-10-02

The immediate operating target is limited to Yellow, Babylon, Squid and
OriginTrail daily announcements:

Official latest source → Korean Telegram/X copy and banner → private staff
review/edit → human approval of that exact version → separately gated delivery.

This increment implements daily-only intake and bounded private-card composition
locally, not an active recurring staff-room schedule or public publishing.
`AUTOMATION_DAILY_REVIEW_MODE` defaults to `false`, leaving legacy routing
unchanged. With an explicitly approved opt-in:

- `AUTOMATION_DAILY_REVIEW_START_KST` gates new daily reservations and generic
  worker claims. Before that time, existing feed polling/recording continues,
  but generation waits. `09:00` is a provisional default, not an approved
  production schedule or a promise of message delivery at precisely that time.
- New reservations are `daily_news`, including complete official long notes
  within the existing 20,000-character news-card input bound. Copy is not
  truncated to fit. Existing articles and already-consumed daily slots are not
  relabeled, replaced or reopened. Previously owned jobs keep their original
  immutable kind/retry contract and can still drain after the start time.
- Intake must successfully poll X and match the latest observed numeric post ID
  to the feed cursor. Freshness is strictly less than 24 hours; naive/future
  timestamps, missing/owned latest sources, low-signal posts and unsafe visuals
  do not fall back to older news. Freshness is rechecked before reservation.
- Existing per-client source-remix/classic visual rules, one-slot-per-KST-day
  owner dedupe and four-client daily bound remain in force. Missing approved
  classic artwork for Babylon/OriginTrail remains a hold, not a generic banner.
- Selection is only a successful-poll snapshot, not final latest-source proof.
  The review owner must still prove active feed/recent poll/latest source,
  current immutable version, canonical PNG/hash and no approvals/publications
  immediately before sending the private review card.

The existing private-review controls support editing copy/banner, fact-checking
and holding. They do not turn an approval comment into a public post. Edits must
create a new immutable version and invalidate old approval eligibility.
Typefully is draft-only until exact account/copy/media/timing is reviewed.

### Bounded daily button-card composition

`scripts.run_daily_private_review_cards` now reuses the existing exact-version
canary/owner/image/courier contracts for at most one card per eligible client,
four total per invocation. Each card contains the canonical image, Korean
Telegram copy, X copy and a six-button edit/fact-check/hold message. It contains
no public approval/publish button. A fresh sender authenticates the same
operator-pinned existing review bot/private-room policy for each version;
this does not register staff, alter permissions or add an update consumer.

The new packet mode is `daily_button_card_v1`, gated separately by
`CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED` on the courier and
`CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED` on Netlify. Both default to OFF.
`CONTENT_OPS_REVIEW_ENABLED` and `CONTENT_OPS_BUTTON_CARD_ENABLED` must stay
false; daily mode rejects a leftover fixed canary version UUID. The old default
container command and unscheduled/NEVER-restart manifest remain unchanged.
The new command is packaged but not mounted or scheduled anywhere.

Only discovery omits the version header. Each returned owner claim creates a
single-use exact-version session; image, review-owner steps and send-start are
all scoped to that UUID, outbox and claim token. Gateway RPC schemas and grants
are not widened. The persisted owner ledger remains the cross-process dedupe
authority. A lost/malformed claim or uncertain send/owner acknowledgement stops
the batch and cannot be blindly retried; confirmed earlier cards retain their
receipts. A missing candidate or KST date rollover also stops discovery.
`--validate-only` checks scope, exact image/release/runtime SHA and existing bot
policy without constructing network/Telegram clients or executing DB calls.
Missing runtime Git SHA is explicitly distinguished and allowed only for an
OFF validation, never an enabled run.

Still required for real daily operation: current hosted schema/RPC compatibility
(including the existing button-owner/image proposals), exclusive bot ownership,
an accepted private canary and employee edit readback, explicit recurring
timing/activation approval, and verified per-client public delivery adapters.
Do not infer that a proposal RPC is already installed in production. The
intake/composition baseline added no migrations. The October 3 follow-up adds
two local-only corrections described below; neither is applied to production.
This change performs no deployment, production settings change, new Telegram
consumer, real sends or Codex heartbeat. In particular, a locally assembled
button does not prove the existing callback owner can accept it in production.

Verification for the intake-only baseline: Python **4,439 passed** (three deprecation
warnings); JavaScript **526 passed / 3 skipped / 0 failed**. JavaScript local
server tests passed on a credential-free rerun outside the sandbox listen
restriction. Disposable PostgreSQL 16 applied 57 migrations, passed full-schema
ACL/producer binding and eight-way single-winner claim/send-start races, and
proved unknown delivery is not retried after restart. The disposable database
was stopped and removed. `git diff --check` passed. Docker packaging was not
rerun because the local daemon was unavailable; hosted CI/deployment and real
employee button/delivery acceptance are not established by these results.

Verification after daily button composition: Python **4,513 passed** (three
existing deprecation warnings); JavaScript **532 passed / 3 skipped / 0 failed**.
Synthetic integration recorded all sixteen private parts across four clients,
six version-bound edit-only controls per card, partial-batch receipts, no later
client after unknown delivery, KST rollover, OFF/window no-I/O, strict policy,
and exact-version image/owner/start fences. These are mocks, not real message
receipts. The expanded image CI validation script also passed locally with a
synthetic stamp and socket/DB audit guards; it is not a built-image receipt.
`git diff --check` passed. Docker remains unavailable, so actual image packaging
and Docker-backed owner-ledger PostgreSQL 16/17 CI were not rerun locally.
No hosted CI, migration, deployment, activation or real Telegram/X send occurred.

### Producer/discovery compatibility follow-up, 2026-10-03

Read-only hosted catalog inspection confirmed that the corrected candidate helper
accepts natural completion `jobs.input/output` bindings, while the existing
reconciliation enumerator still requires the optional `jobs.content_item_id` FK.
The local additive `20261003143000` migration makes discovery use the same
canonical request/output/current-version contract, with a guarded UUID lookup.
It retains daily slot ownership, four-client bounds, the locked eligibility
helper, service-role-only RPC access, exact-version cleanup and terminal unknown
dedupe. It does not backfill jobs or enqueue anything in production.

Hosted inspection also confirmed legacy `left()` truncation in the candidate
helper. Local additive `20261003143100` returns the complete immutable title,
Telegram copy and X copy within existing runtime character bounds (160/3400/1000).
Oversize content is held, not silently shortened. Transport UTF-16/message limits
remain independently enforced before any send. Returning full X review text is
not a claim that it fits every account's public-post limit; publication stays
separately gated.

Daily intake now rechecks the invocation's KST date and daily window before every
new job claim. An already claimed job finishes normally; a midnight rollover
cannot acquire the next day's work. Legacy opt-in-OFF routing is unchanged.

Synthetic regressions cover NULL-FK discovery 4→0, exact-version 1→0, invalid
producer exclusion, complete-copy equality through reconcile→claim, oversize
holds, and generation-time rollover. These are local fixtures, not hosted
enqueue/send proof. The production owner-ledger migration `20260927130000` is
already in hosted history and its current ACL check passed; do not reapply its
historical proposal. Both new corrective migrations still require separate
review, exact-file production apply approval and post-apply readback.

## Original link-card slice (historical scope)

Official-X cron creates a draft using the existing pipeline. The private review
courier sends a link card for each eligible client/version to the configured
staff review destination. The link opens the exact immutable version in Studio,
where staff can inspect copy, banner and source and use existing human review.
A stale version link hides detail and approval controls rather than approving a
different version. Approval itself does not publish.

This PR supplies the missing gateway/outbox, exclusive team-relay ownership,
exact-version UI guard and disposable verification. Existing worker, coverage,
CLI and Docker image are reused from main. The gateway accepts link cards only.
All delivery configuration remains default-OFF; no service is added to active IaC.

## Still not included

- Production recurring copy/photo/button delivery or accepted employee button
  editing. Local composition is implemented above; no hosted acceptance yet.
- Four-client official Telegram publishing. Existing public publisher remains
  Squid-only and is not invoked here.
- Typefully execution. The exact draft contract is not an operational sender;
  legacy ambiguous-POST retries must not be reused.
- PR #186 cancellation-confirmation courier. That is a different runtime and
  must not be enabled as a substitute for daily review.

## Verification, 2026-09-16

- Python: 4055 passed; two existing FastAPI deprecation warnings.
- JavaScript after dependency patch: 505 passed, three existing opt-in skips,
  zero failures. Relevant Python rerun: 36 passed, two existing warnings.
  Initial sandboxed JS run hit localhost listen EPERM; the scoped local-server
  rerun passed. No production or provider endpoints were used.
- Disposable PostgreSQL 16: all 56 migrations and full-schema ACL smoke passed;
  synthetic behavior, eight-way claim/send-start races and unknown-delivery
  restart fencing passed. See DAILY_REVIEW_MVP_ACCEPTANCE.md for limits.
- Local Docker image: default-OFF zero-claim output and synthetic validate-only
  succeeded with runtime networking disabled. Wrong runtime SHA was rejected.
  These are not hosted deployment or actual-delivery proofs.
- Follow-up dependency remediation: sharp minimum/lock updated to 0.35.4,
  including prebuilt libheif 1.23.2. npm audit now reports zero vulnerabilities.
  A regression test checks both locked and actually loaded native versions.
  This patches this branch only, not the currently deployed production image.
  Advisory: https://github.com/advisories/GHSA-rgj7-g3m4-5g8c

## Controlled rollout (separate authority required)

1. Review and merge this isolated change after CI. Merge is not deployment.
2. Verify hosted schema compatibility, dependency risk and exact source SHA;
   obtain narrowly scoped migration/deployment authority, keeping delivery OFF.
3. Read back gateway/worker provenance and exact private destination; assign
   exclusive relay ownership so the legacy staff-room relay cannot duplicate it.
4. Authorize one exact-version private canary, verify its message and authenticated
   Studio link, and verify approvals/publications remain unchanged.
5. Only after acceptance, authorize recurring private review timing. Timing has
   not been chosen or configured by this PR.

Public sends require a separate exact content/banner/destination approval and
verified per-client sender. Unknown delivery is terminal: reconcile receipts,
never blindly retry.
