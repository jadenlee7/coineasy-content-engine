# Private review and banner revision v2

Status: **LOCAL_IMPLEMENTATION / NOT_WIRED / NOT_DEPLOYED / NO_SEND**.

Base: `c7360452143bf29169e6d38b37d013205d7ed94c` (PR #188).
This increment does not enable the existing courier, install a Telegram webhook,
add a database migration, or change publication permissions.

## Intended employee experience

For Yellow, Babylon, Squid and OriginTrail, an exact-version private review card
contains the canonical image, full Korean Telegram copy, full X copy, official
source, publication date and version. Six private controls offer:

- Telegram copy edit
- X copy edit
- Banner regeneration with design feedback
- Hold
- Source verification
- Claims verification

Copy edits and banner revisions require another review. These controls are not
public publication approval. A later, separately authorized publication stage
must show the exact channels and version; Typefully should initially remain
draft-only. No daily posting or publication is enabled by this increment.

## Implemented and locally tested

- `review_messages(..., private_only=True)` produces private controls without
  the public publication action. The corresponding callback and ingress policy
  also reject old signed publication callbacks before any owner mutation.
- `build_client_bundle` renders all four clients without expanding the existing
  Squid-only worker or gateway transport permissions.
- `BannerRequest` binds the exact review snapshot, local official-logo hash,
  Korean headline/subtitle and bounded design feedback to a unique operation.
- The image editor uses the pinned GPT Image 2.5 model, one image-edit request,
  bounded response handling and no automatic retries. Tests mock HTTP; they do
  not prove live provider access, image quality or billing availability.
- Generated outputs must be real opaque PNGs at 1536 x 1024. The worker checks
  source age at dispatch and at result commit, not just enqueue.
- The persistent provider journal prevents concurrent duplicate claims. A
  provider-started or committing operation is never silently reclaimed after
  restart. An unknown outcome blocks subsequent jobs for that item pending
  operator reconciliation, even with a new operation ID.
- New content versions are installed only through the content-owner interface.
  Owner receipts must require re-review and explicitly deny execution authority.

The journal transition is:

`queued -> provider_started -> result_ready -> committing -> complete`

`provider_unknown` and `commit_unknown` are terminal holds, not retry queues.
The journal stores private copy and images: use a restricted persistent volume,
do not expose it as a download, and do not log its rows. All workers must share
the same database file; independent replicas/disks defeat its replay barrier.

## Remaining implementation before a staff pilot

1. Prepare pinned, source-checked headline/subtitle/logo briefs for each exact
   version. The authenticated banner-feedback owner is implemented locally;
   missing briefs refuse requests rather than derive claims from chat text.
2. Package the connected local feedback/result owners with a restricted runtime
   DB role and scoped private Storage access. The current local fixtures are NOT
   production request registrations.
3. Connect the existing one-shot courier, receipt registration and edit ingress
   to the existing polling owner with `private_only=True`. The in-process
   adapter and owner-side routing now have an offline cross-repo test, but real
   callback and text-reply DB adapters now have local integration coverage;
   runtime composition exists but remains uninstalled. Do not infer
   delivery from a rendered card or attach buttons to an incomplete bundle.
4. Extend the passing local PostgreSQL callback/text-edit/banner-feedback/result
   tests to cloud private delivery and final staff-visible re-review receipts.
5. Add separately scoped deployment configuration for the image worker. Keep
   provider credentials out of the review courier and public publisher.
6. Enroll verified staff/approver identities and confirm the private destination.
   Usernames alone are not stable authorization identifiers.
7. Confirm KST schedule and spending policy. Current journal limits are counts
   per client/day and item, **not a US-dollar cap**. A queued request cannot
   start a paid provider call after its reserved KST day; an already generated
   result can still commit after midnight without a new call. Enforce a real
   monetary budget before scheduling paid generation; leave scheduling off meanwhile.
8. Reconcile the existing private activation authorization against the exact
   rollout scope and run a single
   private exact-version pilot with message-ID readback. Public posting and
   Typefully execution require a separate exact-content/destination approval.

Until these steps are complete, staff cannot use these new buttons in Telegram,
and turning the computer off does not make this local code run in the cloud.

## Existing review bot reuse preflight

The operator selected `@coineasy_review_bot`. Read-only `getMe` confirmed its
identity on the existing meme-engine service; no new credential is needed for
identity discovery. No token or private destination was written to this document.

Observed existing owner: `jadenlee7/coineasy-meme-engine`, Railway service
`coineasy-meme-engine`, deployment `fdba40b0-bd3f-47af-8861-608e5e027fe5`, exact
SHA `31fc31dd804335705095d1cc618f047812c7a8a6`, status `SUCCESS`, `stopped=false`.
`getWebhookInfo` reported no configured webhook. The deployed
`pipeline/review_bot.py` registers an unfiltered callback handler and runs
polling. Its legacy `approve` branch directly calls `publish_meme(job)`.
This is source evidence of a publication path, not evidence of a new publication.

Reuse must preserve a single update owner. Do not point a second polling worker
or webhook at this token. The content-ops courier is not that update owner and
must not be started as one. Integration requires explicit private-room/action
routing before the legacy handler, verified identities, and tests proving that
malformed, stale and legacy publication callbacks cannot fall through from the
private workflow. The operator confirmed that legacy meme reviews must continue;
the companion local integration preserves them. No live handler, webhook, configuration,
deployment, staff ACL or existing publication behavior was changed in preflight.

### Single-owner integration increment

Private card callbacks now have the `ce1:` namespace (55 bytes including the
unchanged signed token). The private ingress accepts it; the legacy public
ingress rejects it. `PollingReviewAdapter` reuses signed-card and edit-reply
validation in-process and rejects signed public approval actions.
The companion meme-bot route handles these inputs before legacy video controls
and never falls through after an unknown owner result.

Run the cross-repo offline check with PTB 21.11.1 installed:

```sh
python -m scripts.check_private_review_polling_contract --meme-repo /path/to/coineasy-review-bot-integration
```

It uses actual PTB Updates/handlers, adapter and signed controller, but synthetic
owner fixtures. It is not hosted proof or a real database/Telegram receipt.
Production packaging, restricted runtime grants, identities, pinned briefs and
deployment remain unfinished. The legacy publisher is a Drive/uploader handoff
with separately controlled YouTube upload, not proof of client-channel delivery.

### Transactional callback and enrollment increment

`PostgresPrivateReviewOwner` now resolves the registered card and locks the
existing item/review/card/identity/permissions within ONE transaction per click.
It reads the canonical current PNG and channel copies from the authoritative DB,
checks the signed private snapshot, then calls the existing action function.
No shared mutable last-card cache is used between polling threads. Source age,
card expiry and signature expiry are checked again after SQL/permission waits.
Unknown commit acknowledgement is not retried. This adapter grants no SQL access
and does not use the legacy bot's Supabase admin credential.

The companion bot now has explicit default-OFF composition for the callback and
text-reply adapters. Its optional `/review_register` handler verifies a direct
command, exact private room and fresh Telegram membership before storing only a
PENDING staff candidate on a restricted persistent volume. It never grants an
ACL from a username. Numeric identity mapping to the existing owner actor still
requires operator verification. The production entrypoint is unchanged: neither
the command nor the new route is installed in the live bot.

Local full-schema PostgreSQL receipt: 24 client/action cases, 13 rejected cases,
eight concurrent duplicate clicks -> one durable action, four concurrent clients,
real rollback on malformed receipts, and read-only reconciliation after lost
commit acknowledgement. Existing cancellation/edit/restart/ACL harness passed.
All provider and production calls in this receipt are zero; it is not hosted proof.

## Offline checks

Latest feedback increment: full Python suite **4,185 passed**, with the same
three pre-existing deprecation warnings. New tests include typed-action routing
without fallback and the banner-specific reply limit/receipt boundary.

- Focused banner/private-control tests: 40 passed on Python 3.12.
- Full Python suite after transactional callback integration: 4,122 passed (three existing
  dependency/lifecycle deprecation warnings).
- Cross-repo actual PTB/adapter/signed-controller contract passed with legacy
  calls=0, public outboxes=0 and external sends=0 (offline fixtures).
- Full JavaScript suite: 505 passed / 3 skipped with localhost listeners allowed.
- Companion bot: 26 Python tests and 8 JavaScript tests passed, including pending
  enrollment identity conflicts, membership refusal and existing meme review.
- No live image API calls, production DB mutations, deployments or sends.

`git diff --check` passed. No live end-to-end receipt exists yet.

## Banner-result owner increment

`PostgresBannerOwner` now implements the result side against the authoritative
content tables. The new **local-only** proposal
`supabase/proposals/content_ops_banner_revision.sql` stores immutable exact
requests and result receipts; it grants no runtime role access. A request must
already be registered by the authenticated feedback owner. Do not seed
production rows the way the local test fixture does.

### Authenticated feedback increment

The existing durable prompt reservation/response ledger now supports the banner
instruction, with the same exact actor/card/room/topic/expiry checks as copy
prompts. Banner prompts cannot use the historical unreserved fixture fallback.
TG/X copy-saving SQL still rejects banner actions.

`PostgresBannerFeedbackOwner` authenticates a reply and atomically registers one
immutable request plus a consumed-reply receipt. Headline/subtitle/logo are read
from a separately pinned immutable brief, while reply text is design feedback
only (maximum 600 characters). The result owner's exact-version/official-source,
card HMAC, active identity and no-publication checks run in that same transaction.
An error rolls both inserts back; lost commit acknowledgement stays unknown and
does not retry. Concurrent identical replies reuse one request; changed text or
a second message cannot reuse the consumed prompt.

The existing poller composition uses `PostgresPrivateReplyOwner` to select the
stored prompt action. Neither button labels nor exception fallback choose an
owner. Its `banner_requested` receipt means **saved request, not a worker queue,
image generation, delivered banner, or permission to publish**. Composition is
still default-OFF and is not installed in the production entrypoint.

Local PostgreSQL tests cover all four clients, actual reserved-prompt response
registration, the polling adapter, eight concurrent replies, wrong human/topic,
revocation, stale source, inactive identity, missing brief, changed action,
transaction rollback and independent readback after a lost commit ACK. Providers,
Storage and Telegram responses are synthetic; production calls remain zero.

The owner locks item/review/card/identity/reviewer/client, verifies the request,
current source/version/fingerprint and matching `edit_banner` action, and refuses
any approval/publication. Public eligibility is not required for private image
editing: the real owner rechecks authority and freshness instead. Generated
results remain unapproved and require independent human review.

The local `BannerWorker` wrapper is now an explicit dependency-injected,
one-shot boundary around that journal. Its default-off path returns before
inspecting a journal, owner or provider; its enabled path refuses missing
dependencies before claiming work. It does not read environment variables,
discover credentials, start a scheduler, retry an unknown provider/commit
outcome, upload to Storage or send Telegram. It is still not wired into the
production entrypoint.

`prepare_banner_rereview` accepts only the committed result-owner receipt plus
owner-issued replacement outbox/token values. It preserves client, item, source,
generate-job and both channel copies while changing only the immutable version,
banner hash, outbox and claim token. It is a pure handoff planner for the
existing private review worker; it does not create an outbox, call the image
provider, send a Telegram card or authorize publication.

Storage uses the existing `workspace/client/asset/news-card.png` convention in
the private `content-studio` bucket. `SupabaseBannerStorage` requires explicit
separate project/scoped credentials, verifies the bucket is private, creates the
object without upsert, then reads back and compares the entire bounded PNG.
No redirect, automatic retry, overwrite, cleanup or admin-key fallback exists.

Only after matching storage readback does one DB transaction create a new
immutable version plus canonical asset/hash/provenance, preserve channel copy,
reset QA to needs-review, replace the current version, clear scheduling and
deactivate old cards. Expiry is checked after storage and before commit. Same-job
replays with the same result reuse the existing receipt; changed result bytes
conflict. The worker does not retry a commit with an uncertain acknowledgement.

DB and object Storage do not share a transaction. A DB rollback or uncertain
upload can leave an orphan object; preserve it for read-only reconciliation.
Never delete/re-upload/regenerate automatically to hide this uncertainty.

Local evidence: four clients, eight concurrent saves -> one new version/upload,
seven pre-upload refusals, unchanged old version/copy, deactivated old cards,
post-insert rollback, retained orphan, lost-ACK read-only reconciliation and a
provider-journal -> real PostgreSQL result flow passed. Provider and Storage
transports were fixtures; no real API spend or private/public send occurred.
Storage transport unit tests exercise private-bucket refusal, create-only writes,
exact-byte readback, malformed paths, redirects, timeouts and no retry/cleanup.
Full Python suite before the latest re-review planner: **4,140 passed**, three existing
deprecation warnings. The cross-repository single-poller contract also passed.
