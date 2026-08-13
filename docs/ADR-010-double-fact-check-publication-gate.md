# ADR-010: Immutable double fact-check approval and publication gate

**Status:** Accepted
**Date:** 2026-08-02
**Deciders:** CoinEasy content engine team

## Context

The generation prompts treat the submitted source text as the factual boundary,
and the existing brand QA checks source presence plus a bounded set of numeric
claims. All four clients' scheduled source-remix requests additionally prove
that the X status, immutable account identity, and selected media belong
together. Those controls do not
prove that manually submitted text matches the linked post, and they do not
require a reviewer to record that every final public claim was compared with
the official source.

All generated content stops at `needs_review`, but the current approval record
only captures an approve/reject decision. An approved version can therefore
reach the exact Telegram queue without durable evidence that source identity
and final claims were checked separately.

The system must not describe a prompt instruction, model opinion, URL, or media
provenance check as a complete fact check. It also must not fetch arbitrary web
pages or silently treat community and performance signals as factual evidence.

## Decision

Adopt `double-fact-check@1` for every newly generated Daily News, article, and
tutorial version.

1. After the final generated copy and channel copy are known, the Netlify
   boundary creates an immutable fact-check baseline in
   `content_versions.generation_meta.fact_check`. It binds the exact source and
   public output with SHA-256 digests and records two distinct automated checks:
   source evidence and output-claim anchors. Stored Daily News and Tutorial PNG
   bytes are included by digest; localized Squid image text and Tutorial lesson
   claims are included in the public-text snapshot.
2. The automated report uses `pass`, `review`, or `blocked`. `pass` means only
   that the deterministic checks found no issue. `review` is expected when a
   human must confirm provided text, a translation, or source authority.
   Neither status claims that an arbitrary statement is independently true.
3. Approval requires two explicit human attestations for the exact current
   version:
   - the reviewer opened and verified the official or primary source, including
     author, URL, date, product names, numbers, and launch or availability state;
   - the reviewer compared every final headline, body, image label, and channel
     post with that source and found no unsupported claim.
4. The approval record stores both attestations and the policy version. Missing
   or malformed automatic evidence, a `blocked` report, either unchecked human
   attestation, a mock result, or a stale version prevents approval.
5. Publication revalidates the same immutable version and its attested approval
   when a request is queued, when a worker claims it, and immediately before an
   irreversible provider call. Legacy approvals without this evidence are not
   publishable until the content is regenerated and reviewed under the current
   policy.
   The generate-and-publish legacy HTTP route remains dry-run-only because it
   has no immutable version or approval boundary.
   Manually observed public URLs pass through the same approval gate before they
   can enter the performance ledger or KPI accounting.
6. EasyFarm signals, historical approved examples, Figma references, community
   sentiment, and model-generated explanations remain editorial or style input.
   They never satisfy either fact-check attestation.
7. Downloaded SVGs, client-side rasterized Article visuals, and any design edits
   are review-only derivatives outside the approved immutable version. They are
   labeled as unapproved and must be imported as a new version, fingerprinted,
   and reviewed again before publication. `double-fact-check@1` does not claim
   to approve a transient or edited visual that was never stored and hashed.

## Options Considered

### A. Prompt-only fact-check instruction

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Latency and cost | Low |
| Auditability | Poor |
| Failure behavior | Open |

**Pros:** No storage or workflow changes.
**Cons:** A model can ignore the instruction, and no durable evidence reaches
approval or publication.

### B. A second synchronous model call only

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Latency and cost | Medium to high |
| Auditability | Medium |
| Failure behavior | Variable |

**Pros:** Can compare paraphrases and translations semantically.
**Cons:** It is nondeterministic, shares the same source limitations, adds time
to already bounded image rendering, and cannot replace source review.

### C. Immutable automated baseline plus two human attestations (selected)

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Latency and cost | Low |
| Auditability | High |
| Failure behavior | Closed at approval and publication |

**Pros:** Binds checks to the exact version, distinguishes provenance from
claim review, works for Korean localization, and is enforceable at the database
boundary.
**Cons:** Approval remains a deliberate human step, and deterministic checks do
not replace source expertise.

## Consequences

- The Studio cannot approve a new version until both fact-check confirmations
  are selected.
- Existing approvals remain readable but are intentionally not publishable
  through the exact workflow without current-policy evidence.
- A successful automatic report is a preflight signal, not a truth guarantee.
- A single authorized Studio session currently records both human attestations;
  the policy requires two distinct checks, not two independent people.
- Official X imports can progressively add canonical-text snapshots and more
  precise claim comparison without changing the approval contract.
- A future independent semantic checker may be added as a third check, but it
  must fail closed and must not weaken the two human attestations.

## Action Items

1. Add and persist the `double-fact-check@1` generation report for all three
   content kinds.
2. Add the two mandatory Studio approval confirmations and durable approval
   fields.
3. Enforce the evidence at approval, publication request, worker claim, and the
   final Telegram attempt fence.
4. Add deterministic, API, UI, migration, and transactional security tests.
5. Extend official-source snapshots and semantic claim comparison separately;
   never label those future checks complete before they are deployed and tested.

## Rollout safety

This change intentionally favors a short fail-closed review maintenance window
over a publication bypass. Production rollout must use this order:

1. Pause the official-X generation cron and both Telegram publication execution
   planes. Verify that no exact Telegram job is `running` and no publication is
   `publishing` with `delivery_started_at`; reconcile any such provider attempt.
   The migration aborts if either active state exists.
2. Deploy the Railway API response contract that exposes bounded Tutorial
   `lessons`, while keeping the workers paused. The generation client performs
   an authenticated `/api/studio-capabilities` preflight before every mutating
   Netlify request, so an old Netlify deployment cannot receive and persist a
   new-worker request.
3. Apply the database migration. Legacy review RPCs are revoked at this point,
   so review is briefly unavailable until the next step; publication is already
   fail-closed.
4. Deploy the matching Netlify functions and console, then smoke-test capability
   preflight, generation, the two review attestations, and a dry-run publication.
5. Resume the official-X and Telegram workers only after those checks pass.

Do not deploy from a feature checkout and do not resume workers between steps.
