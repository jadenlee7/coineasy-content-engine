# ADR-014: Accept immutable standalone text posts as OriginTrail review evidence

**Status:** Proposed
**Date:** 2026-08-10
**Deciders:** CoinEasy release owner

## Context

The first OriginTrail review-pack implementation admits only provider-owned X
Articles. That is the strongest available provenance, but it leaves the agent
idle whenever the official account publishes ordinary statuses or media posts.
The first production result also proved that a URL-only status must remain
quarantined: it has no usable source body and cannot be upgraded safely.

The intake ledger already commits an ordinary status through three matching,
durable records: the normalized `source_items` row, the non-quote standalone
marker, and the exact first poll receipt. A retry with different URL, body,
media, timestamp, or raw payload is rejected by the intake transaction.

## Decision

Keep X Article evidence unchanged and add a shared source-evidence predicate
with a second admissible kind, `x_post_text`. An ordinary status qualifies only
when all of the following hold:

- it belongs to the canonical `origin_trail` feed and URL;
- its standalone marker is non-quote and references the same first poll;
- the poll receipt contains the exact source UUID;
- normalized media is an empty array;
- the body is 10–20,000 characters and contains non-URL text;
- the immutable Batch input URL, body, and SHA-256 match the source row; and
- the existing text-only Batch gate passes.

Both review-pack materialization and Buzz review actions consume the same
predicate. The decision does not enable publication, create a Batch, accept
media, backfill pre-cutover sources, or mutate old results.

## Options considered

### Option A: Continue accepting X Articles only

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost | Lowest |
| Test speed | Poor; depends on rare source format |
| Provenance | Strongest |

This preserves the current boundary but prevents timely end-to-end testing.

### Option B: Reuse the immutable intake ledger for text posts

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Cost | No new provider cost |
| Test speed | Good when a text-only status appears |
| Provenance | Strong; poll- and hash-bound |

This is the selected option. It increases useful coverage without adding a
generic URL fetcher or trusting mutable browser content.

### Option C: Allow media posts or backfill older rows

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Cost | Higher rendering and verification cost |
| Test speed | Immediate |
| Provenance | Requires new media and historical-evidence contracts |

This would broaden the pilot beyond its approved text-only safety boundary.

## Consequences

- The agent can process normal, verified text-only official updates.
- URL-only, media-bearing, quoted, replied, retweeted, and pre-cutover sources
  remain ineligible.
- Article and ordinary-status evidence stay distinguishable in the review DTO
  and generated content metadata.
- A fresh source is still required; the invalid August 4 result remains
  immutable and cannot be upgraded.

## Action items

1. Apply the migration to a disposable Preview branch and run security tests.
2. Verify an X Article remains eligible and malformed evidence stays rejected.
3. Verify one fresh `x_post_text` result can materialize a deterministic PNG.
4. Keep automatic publication disabled during Production Shadow.
