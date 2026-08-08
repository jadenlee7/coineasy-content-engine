# ADR-013: OriginTrail reviewed-media evidence boundary

- Status: Accepted
- Date: 2026-08-08

## Context

The first OriginTrail Batch/Buzz path admitted only standalone text sources.
Any source with attached X media was routed to synchronous Studio generation,
because neither the public job nor the Batch ledger could prove that a visual
belonged to the exact reviewed post. That protected the canary, but it also
prevented the latest official OriginTrail post from entering the existing
review-only Batch and Buzz flow.

The selected post makes claims about Prime Agent, ARC-AGI-3 performance, and
the OriginTrail adapter. The attached video thumbnail can prove media identity,
but it cannot prove those claims. The official implementation README also
describes only a Stage 1 transport/connection layer, while the benchmark
figures split between a Prime Intellect announcement and a community
leaderboard whose results are self-reported by default. A linked scorecard
commit was unavailable at review time.

## Decision

1. Keep the original text-only eligibility function name as a compatibility
   wrapper, but broaden its implementation to accept either the existing
   standalone text evidence or one exact registry-backed media source.
2. Store the reviewed source in a private, append-only, forced-RLS registry.
   Bind the X status ID and URL, exact source-body SHA-256, provider media key,
   raw media URL, canonical preview URL and SHA-256, media dimensions, and a
   canonical human-qualified fact-evidence envelope.
3. Store the raw provider media URL in the durable public job so it remains
   byte-identical to `source_items.media`. Canonicalize to `?name=orig` only at
   the downstream rendering boundary.
4. Return the evidence envelope only through a lease-fenced RPC available to
   the scoped Batch producer role (and the existing service role). The RPC
   verifies the exact workspace, running job, worker lease owner, and unexpired
   lease.
5. Revalidate the envelope in Python, bind it inside the immutable Batch input,
   and enforce the exact registry/job/input match with an INSERT trigger on the
   private Batch ledger. Unknown, missing, widened, or changed evidence fails
   closed before provider work is queued.
6. Treat media as provenance only (`factual_evidence: false`). The prompt may
   use the reviewed official references and Korean findings to qualify source
   claims, but must not infer facts or describe the visual.
7. Keep the four-field Batch result and Buzz v1 event/message/receipt contracts
   unchanged. Studio may show the sanitized evidence as an optional read-only
   detail section; legacy text-only and archived reviews remain valid.
8. Continue requiring a human double fact-check. This change does not approve,
   publish, post to X or Telegram, create a Buzz action, or enable any automatic
   external publication.

## Options considered

- **Fetch and fact-check the web at Batch runtime.** Rejected because mutable
  network responses would break replay identity and make provider input depend
  on live availability.
- **Trust any media URL already present on the public job.** Rejected because a
  URL alone does not bind the source body, provider media metadata, review
  decision, or supporting claims.
- **Put fact-check fields in the model output or Buzz v1 event.** Rejected
  because it would widen stable downstream fingerprints and let generated
  output impersonate reviewed evidence.
- **Keep every media post on synchronous Studio generation.** Safe, but rejected
  for this explicitly reviewed source because it prevents the evidence-backed
  Batch/Buzz learning loop the team approved.

## Tradeoffs and consequences

- Each admitted media source needs a reviewed migration entry; this is
  intentionally slower than automatically trusting new media.
- The v1 primary key permits only one immutable row per source. Correcting or
  extending it therefore requires a new migration and policy version that
  explicitly introduces supersession/versioning; an in-place update or a
  duplicate row is not possible.
- The same canonical JSON hash is checked in Postgres, Python, and Netlify,
  which adds contract code but makes drift and tampering observable.
- Studio reviewers gain the claim limitations and official references without
  exposing them through the minimal Buzz notification.
- A newly deployed release changes the production Batch release SHA. Provider
  spend remains fail-closed until a separate operator approval receipt is
  issued for that exact deployed SHA.

## Action items

- Apply the evidence migration before enabling this source in production.
- After merge and deployment, verify the exact release SHA and request a new
  Batch canary approval separately; do not reuse an older approval receipt.
- Run one review-only candidate through Studio and inspect the generated Korean
  qualifiers before any human-controlled delivery decision.
- Add future media sources only through new reviewed registry migrations and a
  new policy version whenever the evidence contract changes.
