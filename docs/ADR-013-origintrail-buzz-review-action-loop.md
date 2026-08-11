# ADR-013: OriginTrail Buzz publish-intent review decision loop

**Status:** Proposed — implemented and locally verified; external staging
application requires a separate approval.
**Date:** 2026-08-08

## Context

ADR-011 and ADR-012 deliver one bounded OriginTrail Batch result and its banner
into a private Buzz channel, but the operator's response is not yet durable. A
full agent company needs a fast human-in-the-loop boundary that can prove who
recorded a decision about which immutable review package without allowing a
chat message to publish, spend model budget, mutate a Batch result, or trigger
deployment.

Buzz desktop v0.5.4 can read a signed private thread with `buzz messages
thread`. Its normalized CLI output deliberately strips the Nostr signature.
The relay verifies an event before returning it, but the scanner cannot
independently re-verify the signature or bind the normalized object to the raw
signed event. This residual boundary is acceptable only for a decision record.
It is not sufficient authority for publication.

## Decision

Add a separate, disabled-by-default `origintrail-buzz-review@2` scanner with
only two commands:

- trimmed exact `게시 승인: 원문·최종물 확인`
- trimmed exact `수정 요청: <reason>`, where the reason is one line and at
  most 500 characters

The approval text is intentionally explicit. It records the reviewer's claim
that the source facts, final copy, and attached artifact were checked, plus an
intent to publish later. Short forms such as `승인`, `게시 승인`, and
`승인합니다` are invalid. Even the exact v2 command remains a decision-only
signal; it does not create a `public.approvals` row or a publication job.

The scanner lists at most one delivered OriginTrail result without a decision,
reads its exact root thread, and accepts only a kind-9 direct reply that:

- is in the configured channel and references only the delivered root event;
- is authored by one of 1–5 configured lowercase 64-hex reviewer pubkeys;
- is not older than the delivery receipt or more than five minutes in the
  future; and
- matches one of the two command forms exactly.

The earliest valid `(created_at, event_id)` wins. The worker hashes the full
workspace/job/delivery/channel/root/decision-event/reviewer/decision/reason/time
tuple. Netlify independently recomputes that hash and checks the same reviewer
allowlist before calling one of two RPCs. Supabase stores the first decision in
the FORCE-RLS `agent_runtime.buzz_review_decisions` table. An exact replay
returns `reused=true`; every different second decision conflicts. Updates and
deletes are rejected by an immutable trigger.

For the isolated staging proof, a fresh (`reused=false`) decision also receives
one deterministic, text-only Buzz reply from the service identity. The reply is
attached to the reviewer's exact command event and says either `게시 승인 접수`
or `수정 요청 접수`. It always exposes that automatic publication is OFF (and,
for a change request, that automatic regeneration is also OFF). The adapter
rejects mentions, files, broadcasts, invalid reply IDs, and messages larger
than 1024 UTF-8 bytes before invoking the Buzz CLI. It never reflects the
reviewer's reason into the service reply; the reason remains in the original
reply and immutable decision, preventing `@` or `nostr:npub1` text from being
resolved into an unintended Buzz mention.

This write path has a second default-false gate,
`BUZZ_REVIEW_ACK_ENABLED`. The setting is rejected unless the environment
identity fence is exactly `staging`; enabling the decision scanner alone still
performs no relay write. Production cannot activate this first acknowledgement
implementation through configuration.

The immutable decision is committed before the acknowledgement is sent. This
prevents a visible success receipt for a decision that failed to persist, but
it also means the first staging version is intentionally best-effort: if the
process dies or the relay result is unknown after the commit, the scanner does
not retry and risk duplicate replies. Production promotion requires a durable
acknowledgement outbox/receipt that can reconcile this commit-unknown window.

## Protocol cutoff and evidence gates

`BUZZ_REVIEW_PROTOCOL_START_EPOCH` is a required integer Unix timestamp shared
by the Railway scanner and Netlify review function. It is the forward-only v2
cutover: a delivery receipt and a command event earlier than the cutoff are not
eligible. Operations must choose it after the v2 review-message deployment,
store the same value in both services, and never move it backwards to admit a
legacy `승인` reply. There is no default cutoff in the image.

Before a target can be listed or a decision stored, the database rechecks:

- the exact delivered receipt tuple: workspace, OriginTrail job, delivery
  event, private channel, root relay event, message SHA-256, delivered status,
  client, agent, workflow, and v2 cutoff;
- a completed and settled Batch job with `needs_review`, immutable input, a
  complete source snapshot, and the exact four bounded Korean output fields;
- the pending public review handoff job, canonical OriginTrail X URL, and the
  same request ID, source URL, source body, and source-content SHA-256 as the
  immutable Batch input; and
- exactly one standalone, non-quote official X Article source with matching
  stored evidence and an approved X API retrieval method; and
- an immutable review pack that binds that verified source, the exact Batch
  result SHA-256, one Content Studio version, and the deterministic 1200x630
  PNG SHA-256 to the delivery receipt's `attachment_sha256`.

The scanner then requires one exact root event whose channel and content hash
match the durable receipt and whose author matches `BUZZ_SERVICE_PUBKEY`, and
accepts only an allowlisted kind-9 direct reply
with one exact channel tag and one root reply tag. The command timestamp must
be at or after both delivery and cutoff, no more than seven days after
delivery, and no more than five minutes in the future. Netlify and Supabase
independently recompute the complete command hash. Exact replay is idempotent;
any different second decision conflicts.

`coineasy_buzz_review_decider` is a NOLOGIN/NOBYPASSRLS PostgREST role with
EXECUTE on exactly the list and record RPCs, no table privileges, and no access
to Batch production, publication, provider, deployment, or delivery routines.
The worker receives no Supabase credential. Netlify can adopt the scoped role
through `SUPABASE_BUZZ_REVIEW_KEY`; deleting that variable retains the existing
service-role fallback during cutover.

The Railway deployment also requires these fail-closed identity fences:

- `BUZZ_SERVICE_PUBKEY`: the Buzz service public key, which must not appear in
  `BUZZ_REVIEWER_PUBKEYS`; this prevents the configured service identity from
  approving its own work but does not replace raw signature verification;
- `RAILWAY_ENVIRONMENT_NAME` exactly equal to
  `BUZZ_REVIEW_EXPECTED_ENVIRONMENT`; and
- `RAILWAY_GIT_COMMIT_SHA` exactly equal to `BUZZ_REVIEW_RELEASE_SHA`.

The image supplies safe, non-secret defaults only for
`BUZZ_REVIEW_ENABLED=false` and
`BUZZ_REVIEW_ALLOWED_CLIENTS=origintrail`. Service identity, reviewer keys,
environment, release SHA, protocol cutoff, endpoint, and credentials have no
image defaults. Railway runs `--validate-only` as a pre-deploy command, so a
missing or mismatched fence prevents the deployment. After validation the cron
still remains a no-I/O hold until the literal enable flag is changed to
`true`.

## Residual trust boundary

The normalized Buzz CLI thread is signature-stripped. Consequently an
`approved` row proves only that CoinEasy's configured relay/CLI path returned a
matching event attributed to an allowlisted public key and that every durable
database evidence gate passed. The review-pack and delivery ledgers bind the
decision target to one exact content version and PNG hash, but the normalized
reply does not independently prove the Nostr event signature or that a human
viewed those bytes. It also does not prove both publication fact-check
attestations or authorization to call Telegram.
The decision-only row cannot authorize publication.

No release manager may translate this row directly into publication. A future
publication transition must use a separately approved, atomic authorization
boundary that binds the immutable content version and attachment hash, proves
both fact-check attestations, and creates the exact publication job. Raw event
signature verification is also required before this Buzz signal can itself be
treated as publication authority.

## Explicit non-goals

An `approved` decision does not change `result_code`, create a Studio approval,
or publish.
A `changes_requested` decision records the reason but does not queue a new
Batch. The staging acknowledgement is only a visible receipt; it makes no
OpenAI call and does not queue regeneration, deployment, or publication. Those
are separate transitions requiring their own durable fences and staging
approval after this decision path is proven.

## Rollout

1. Apply the eight additive migrations from
   `20260808121500_origintrail_batch_telegram_publish_limit.sql` through
   `20260808140000_origintrail_review_pack_roles.sql` in timestamp order, then
   deploy the Netlify endpoints with dedicated worker tokens, scoped role JWTs,
   reviewer pubkey allowlist, and immutable v2 protocol cutoff.
2. Configure the same cutoff plus service identity, environment, and exact
   release fences in Railway. Deploy with `BUZZ_REVIEW_ENABLED=false`; the
   pre-deploy validation must pass and the scheduled command must return a
   disabled hold without relay or database I/O. Keep the independent
   `BUZZ_REVIEW_ACK_ENABLED=false` gate until the isolated acknowledgement
   test is explicitly approved.
3. In isolated staging, enable the five-minute one-shot cron and reply to the
   fixed result with `게시 승인: 원문·최종물 확인`; verify one immutable row
   plus one direct `게시 승인 접수` reply, and an idle next run. Verify that an
   exact database replay emits no duplicate acknowledgement and that legacy
   `승인` remains ignored.
4. Repeat once with a new fixed staging fixture and `수정 요청: ...`.
5. Keep publication disconnected. For one isolated staging result only, enable
   the otherwise-default-false `BUZZ_REVIEW_PACK_MATERIALIZATION_ENABLED` flag
   and verify the source link, exact Batch result, Content Studio version,
   deterministic 1200x630 PNG, matching receipt `attachment_sha256`, and exact
   replay behavior. The V1 delivery claim remains the rollback path.
6. Separately design and approve a raw-signature-capable publication
   authorization boundary before any decision can create a publication
   request. Automatic publication remains OFF.

The five-minute scan uses no model tokens. Its cost is a short Railway process,
one bounded Netlify/Supabase list request, and a Buzz thread read only when a
target exists.
