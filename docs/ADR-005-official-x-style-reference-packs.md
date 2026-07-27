# ADR-005: Immutable Official-X Style Reference Packs

## Status

Accepted

## Context

CoinEasy already records each client's official X posts as immutable
`source_items` and creates at most one review draft per client per KST day.
Client YAML files contain curated brand-voice examples, but the scheduled
worker does not learn the recent cadence and structure of each official feed.

Recent posts can help the Korean draft sound current, but they must not become
factual evidence for the selected post. Looking references up at generation
time would also make retries non-deterministic: the same request UUID could
receive a different prompt after another X poll.

## Decision

Before a scheduled draft is queued, the worker creates an immutable style
reference pack keyed by the deterministic generation request UUID.

- The primary source remains the one selected official X `source_item`.
- The pack contains at most three earlier posts from the same active official
  client feed.
- Reference text is bounded to 600 characters per post.
- The pack is append-only and private. Only service-role RPCs can create or
  read it.
- A retry with the same request UUID reuses the exact committed pack.
- Netlify accepts runtime style references only with the separate studio
  automation credential. Browser studio sessions cannot submit them.
- Railway treats the references as untrusted style-only data. They may guide
  cadence, sentence length, and structure, but never facts, entities, numbers,
  URLs, calls to action, or claims.
- Generated content continues to stop at `needs_review`. This feature creates
  no publication, scheduling, or export path.

The catalog stores the pack hash, count, and reference URLs as audit metadata.
The factual `content.source` object and `content_source_links` remain limited
to the selected primary source.

## Options considered

### Rewrite client YAML from the scheduled worker

Rejected. It would require a production Git write credential, create noisy
configuration churn, and make rollback and attribution unclear.

### Query recent posts directly during each generation attempt

Rejected. A retry could change the prompt while retaining the same idempotency
key, producing an unverifiable conflict.

### Add the reference posts to `source_item_ids`

Rejected. That would incorrectly present style examples as factual sources and
allow their claims to enter source maps and review evidence.

### Fine-tune a model continuously

Rejected for this stage. It is slower to audit, harder to reverse, and
unnecessary for learning lightweight channel cadence.

## Consequences

- Queueing fails closed if the reference pack cannot be committed.
- Old queued jobs can create their pack lazily from their pinned primary
  source, then reuse it on subsequent retries.
- Empty packs are valid when no earlier official post exists.
- Prompt and request-hash versions advance for news cards and articles.
- A future scoring system can promote reference candidates, but it must write
  a new immutable pack version rather than mutate an existing request.

## Follow-up actions

1. Monitor reference-pack counts and generation failures in the daily worker.
2. Review output quality per client before changing the three-reference limit.
3. Add performance-based reference selection only after enough approved and
   published content has exact-link outcome evidence.
