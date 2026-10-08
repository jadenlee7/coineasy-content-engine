# Employee approval workflow integration

현재 범위: **로컬 코드 통합·검증 / 운영 미연결**.
Yellow·Babylon·Squid·OriginTrail의 정확한 버전 승인과 Telegram 게시 대기,
Typefully 비공개 X 초안 준비를 연결합니다. 기존 봇 배포·권한·활성화와
실제 전송은 별도이며, 이번 작업의 직원방·공식채널 발송은 0건입니다.

Status: local integration, NOT deployed or enabled. No employee-room,
official Telegram, or X sends were made while building this integration.

The integration reuses Draft PR #193's final card/decision owners and Draft
PR #192's exact Typefully media/draft owners on main `344401928a2d89a18ca85bab347042972b3db317`.
It preserves the newer private-courier and producer fences from main.

## Implemented contracts

1. A newly rendered final card explicitly names the official Telegram
   destination and Typefully X draft account. `decision_mode` is part of the
   snapshot hash, callback MAC, and immutable delivery record. Existing
   `record_only` cards cannot be upgraded into public queue authorization.
   The original review card and final confirmation card have distinct IDs;
   both are hash-bound and the clicked button targets the final card only.
2. The queue-mode card requires a complete Telegram caption within 1,024
   UTF-16 units and X copy within the existing draft owner's 280-character
   limit. It never silently truncates or rewrites approved copy.
3. `PostgresFinalPublicationOwner` invokes one fixed SQL transaction. That
   transaction validates the authenticated actor and exact delivered card,
   records the human decision, attributes the approval to the workspace's
   Telegram principal, creates both channel intents, queues the exact Telegram
   job and reserves the matching Typefully daily slot. Any failure rolls back
   all newly admitted work. A held decision creates no channel work.
4. The Telegram queue pins the decision, destination digest and release in
   both its job and publication. The worker must verify its actual publishing
   bot, channel and posting permission against those pins before a send.
   An explicit client subset prevents the Squid-only legacy worker from
   claiming another client's job.
5. Typefully accepts Telegram-principal provenance only when the immutable
   final decision, exact approval, both handoffs and route bindings agree.
   Active staff identity/reviewer scope and the original review epoch/state
   are checked again before new draft work, matching the Telegram gate.
   Only the paired Telegram publication can coexist. Its output remains an
   **unscheduled private X draft**, not an X publication.
6. An unknown commit/send is not retried with a new key. Read-only terminal
   reconciliation returns the original decision, approval, Telegram job,
   publication and Typefully slot IDs. A queue receipt is not delivery proof.
7. The default-OFF `PollingFinalPublicationAdapter` accepts only `ce2` callbacks
   delivered by the existing authenticated poller. It derives bindings from
   configured numeric identities, rejects forwarded/topic/anonymous/foreign
   updates before DB access, and keeps uncertain or cancelled callbacks from
   being retried in that process. It never starts a second poller or sends an
   acknowledgment. Durable unknown-outcome reconciliation remains the host
   poller's responsibility across restarts.
8. `PostgresFinalSnapshotReader` reconstructs the signed final card with one
   bounded, read-only query. It verifies the current version, exact parent and
   final card, active staff registration, both checks, latest official source,
   canonical PNG and fresh destination bindings. Metadata timestamps/casing
   have a deterministic representation; a mismatched stored digest fails.

All SQL changes in `supabase/proposals/` remain ungranted local proposals.
Applied historical migrations are not edited. Neither tests nor importing a
module grants production access or activates a worker.

## Not yet operational

- The snapshot reader and `ce2` callback adapter must still be packaged and
  mounted in the existing sole bot poller. Automatic final-card preparation
  after both source/claim checks also needs its host trigger. A separately
  provisioned signing key and narrowly scoped owner RPC access are required;
  do not reuse the private `ce1` key or its DB credentials.
- Staff identities/reviewer rows are existing registrations; do not recreate
  users or treat a username as authentication. Live activation must use the
  exact existing principals and client scopes.
- Actual client Telegram IDs, publishing permissions and Typefully social
  sets must be read back and bound to the deployed release. Static handles or
  synthetic fixture digests are not live permission proof.
- Standalone Studio tests and edited versions need an explicit review-revision
  admission path. Do not falsify a natural generation job, daily slot or source
  link to make an edited version pass the original producer-binding gate.
- Production schema compatibility, exact migration manifest/ACLs, image builds,
  exact-SHA deployment and a real private canary remain required. No public
  channel should be activated merely because a local suite is green.
- X direct publication is not implemented or enabled. Staff publishes the
  reviewed Typefully draft until a distinct scheduling/publication owner and
  authorization are available.
- Non-Squid unknown Telegram deliveries remain held. The older automated
  delivery-unknown resolution owner is still Squid-only; no unsafe resends or
  optimistic success inference are introduced for the other clients.

## Verification boundaries

- Current local complete suites: Python **4,902 passed**, JavaScript
  **553 passed / 3 skipped / 0 failed**. `git diff --check` and CI YAML parsing
  also pass. The three skipped JavaScript tests are not counted as proof.
- Disposable PostgreSQL 16: all four clients, 16 admission/rollback scenarios,
  exact replay and wrong-actor/version/release/route refusal. An injected
  Telegram queue or Typefully reservation failure leaves no partial approval
  or newly queued channel work.
- Signup-free fixture: the observed hosted principal foreign-key subset was
  reproduced locally. All four clients pass without creating an Auth user for
  the reviewer; cross-workspace and mismatched numeric identity bindings fail.
  This is not a full hosted PostgreSQL 17 catalog/ACL attestation.
  See [the reproducible local proof](DAILY_APPROVAL_SIGNUP_FREE_LOCAL_PROOF.md).
- True two-session same-card test: both sessions wait on the same item lock;
  exactly one decision/approval/publication/job/slot and two channel handoffs
  remain. The second receipt refers to that original work, not a new send.
- The actual courier-rendered token is tested through the decision handler,
  including final-card ID versus parent-card ID. Provider credentials, actual
  employee delivery and official channel delivery are not part of this proof.
- The snapshot reader's exact SQL was executed on local PG16 in read-only
  mode and missing-card refusal verified. Positive reconstruction/digest
  roundtrips use unit fixtures; a real persisted-card reader roundtrip is not
  yet a verified production or end-to-end database receipt.
- Local Docker CLI exists but its daemon was unavailable. Image build/run
  checks therefore remain unverified locally; CI definitions are not CI
  execution receipts. No hosted CI or deployment success is claimed.
- Existing dependency audit reports a high-severity `sharp` 0.35.4 advisory
  (GHSA-wq5f-xc86-pv6w, fix 0.35.5). This integration does not silently change
  that separate dependency; review it before a production release.

The operator's content approval remains separate from permission to deploy or
enable a service. Keep existing employee and public send flags unchanged until
the corresponding exact production action is authorized.
