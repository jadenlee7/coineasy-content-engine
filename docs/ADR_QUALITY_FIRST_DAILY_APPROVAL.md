# Quality-first daily staff approval

Date: 2026-10-12. Status: local implementation in progress, not operational.

## Context

The operator accepted three reference-first Korean banners and requested daily
latest official-X copy/banner production, private staff delivery and explicit
employee approval before official-channel posting. The user subsequently
explicitly approved **USD10/day combined estimated image spend** on 2026-10-12,
accepting actual settlement variance, not unlimited retries. That supersedes
the earlier unanswered USD3 proposal and the strict-cap interpretation.

The existing natural producer uses the Studio renderer. Simply replacing its
renderer with an asynchronous paid API is unsafe: the gateway does not pass its
durable operation identity across the generation boundary, and a transport
timeout is retryable before a catalog result exists. The review bot also lacked
the final `ce2` path in its actual sole-poller startup.

## Decision

Reuse the existing daily source, immutable-version, staff-review, final-decision
and publication owners. Add reference-first max-quality generation and a
durable single-submit boundary; keep the callback path in the existing bot.
Do not add a second Telegram update consumer or a desktop-dependent schedule.

Target flow:

`latest official source -> source-locked Korean copy + reference-first banner
-> private staff card -> edit / source and claim checks -> exact-version final
approval -> official Telegram queue + unscheduled Typefully X draft`

- First quality scope: Yellow, Babylon, OriginTrail. Existing Squid policy stays
  unchanged. The proposed 09:00 KST start and per-client daily bounds must be
  carried into the exact rollout plan; they are not activation evidence.
- Use the accepted Higgsfield Sunburst max-quality, requested-4k, square route
  with original artwork and verified logo. No generic Studio fallback or
  automatic quality downgrade. Actual pixel size is measured, never inferred
  from the request or filename. Retain large Korean type and spacious leading.
- The owner must reserve a paid attempt durably before POST. A request with an
  uncertain result stays held; only a recorded provider ID permits GET-only
  reconciliation. New IDs or the next day cannot bypass source deduplication.
- The same private persistent journal must be shared across all relevant
  workers. It stores an initial-banner attempt and staged result only, not an
  approval, canonical catalog version or publication.
- Staff-approved content is bound to its exact immutable version, source and
  banner hash. Editing requires a new version and invalidates old checks and
  approval. A callback is not a successful public delivery receipt.
- `ce1` remains private edit/check authority; `ce2` has a separate signing key
  and restricted final-publication DB role. Both run in the same poller. OFF
  startup does not construct provider clients or access DB/Telegram.
- Typefully remains a private unscheduled X draft. This change does not claim
  or authorize direct automatic X publishing.

## Alternatives

Reusing the synchronous Studio retry boundary would be a smaller patch but
does not retain provider-request ownership across uncertain paid calls. A new
review bot/poller would duplicate message ownership and existing staff policy.
A local Codex automation would depend on the desktop and is not the requested
hosted daily product. None is selected.

## Consequences and remaining integration work

Local completed components: max-quality provider adapter, persistent initial
attempt/result/copy journal, opt-in natural gateway/server integration into the
existing initial staff-review outbox, real existing-poller final callback routing
and lazy default-OFF startup composition. The bot vendors the exact committed engine
closure; today's uncommitted image changes are not silently included.

The independent engine/callback release contract is now implemented locally.
The snapshot/HMAC/delivery/decision bind E/B/P; the current pair is selected per
workspace/client in a separately provisioned registry. Switching away and back
cannot revive an old card. Downstream channel workers retain E, while callback
runtime attestation uses B. See [the pair contract](CONTENT_OPS_RELEASE_PAIR.md).

Still required before an operational claim:

1. Deploy/attest the now-local trusted job/request/client/source connection.
   The proposed read-only context RPC, immutable copy checkpoint, pre-submit
   reference-byte verification, persisted provider ID and PNG reuse are wired
   into the existing natural completion contract. No route accepts browser-
   supplied authority or a fabricated succeeded job. Provision the exact private
   volume/reference manifest and prove the hosted canary before claiming it live.
2. Connect the existing authenticated banner-edit owner to this quality route
   and implement review admission for its new immutable revision. The initial
   daily journal is intentionally not a revision authorization service.
3. Mount/attest the now-local trusted final-card preparation trigger after both
   human checks. A same-transaction hook, immutable intent, one-shot Engine
   claim and existing-courier composition are implemented and fixture-tested;
   the live bot/worker/ACL composition is not. Preserve multipart receipts and
   hold unknown sends; do not infer live delivery from a recorded intent.
4. Build and attest the independent engine and callback releases, then obtain
   exact authorization for their compatibility manifest and scoped DB pair
   registration. The local digest is an identity binding, not authorization;
   no registry rows, grants or production settings have been provisioned.
5. Verify hosted schema, exact required migration manifest/ACLs, existing staff
   identities, exclusive bot ownership and actual per-client destination
   permissions. Obtain the exact deployment/activation authorization; local
   tests do not prove any of these production properties.
6. Enforce the now-authorized USD10/KST-day combined **estimate-based** image
   policy without reducing quality. The user explicitly accepts actual
   settlement variance. Authenticated same-payload forecasts are reserved
   atomically; pending/unknown liabilities carry over, completed forecasts
   remain provisional, and final billing is separate. A request count or static
   environment price is not enforcement. No paid recurring worker is enabled
   by this document; the local real-provider factory now supports this policy.
7. Deploy the pinned release OFF and verify it, then run an approved private
   canary covering edit -> revised image -> new checks -> final confirmation.
   Confirm that unapproved, stale, changed and repeated buttons never create
   new public work. Only then enable the authorized daily hosted path.

No production migration, deployment, role/key change, generation charge,
activation, staff send or public send occurred in this local increment.
