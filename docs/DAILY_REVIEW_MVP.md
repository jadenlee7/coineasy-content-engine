# Daily private review MVP

Status: local implementation verified; not enabled or delivered in production.

## First usable slice

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

## Not included

- A full copy/photo bundle inside Telegram or in-message editing/approval.
- Four-client official Telegram publishing. Existing public publisher remains
  Squid-only and is not invoked here.
- Typefully execution. The exact draft contract is not an operational sender;
  legacy ambiguous-POST retries must not be reused.
- PR #186 cancellation-confirmation courier. That is a different runtime and
  must not be enabled as a substitute for daily review.

## Verification, 2026-09-16

- Python: 4055 passed; two existing FastAPI deprecation warnings.
- JavaScript: 504 passed, three existing opt-in skips, zero failures.
- Disposable PostgreSQL 16: all 56 migrations and full-schema ACL smoke passed;
  synthetic behavior, eight-way claim/send-start races and unknown-delivery
  restart fencing passed. See DAILY_REVIEW_MVP_ACCEPTANCE.md for limits.
- Local Docker image: default-OFF zero-claim output and synthetic validate-only
  succeeded with runtime networking disabled. Wrong runtime SHA was rejected.
  These are not hosted deployment or actual-delivery proofs.
- npm audit reported one existing high-severity direct sharp vulnerability.
  No dependency or lockfile was changed; dependency remediation remains a
  production-readiness follow-up, not silently ignored by green feature tests.

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
