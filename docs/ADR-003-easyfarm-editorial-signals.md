# ADR-003: EasyFarm editorial signals bridge

**Status:** Accepted for V1 implementation
**Date:** 2026-07-27
**Deciders:** CoinEasy product, community, and content operations leads

## Context

CoinEasy operates four global official-X sources in Content Engine:

- Yellow (`@Yellow`)
- OriginTrail (`@origin_trail`)
- Squid (`@SquidRouter`)
- Babylon (`@babylonlabs_io`)

EasyFarm separately observes the Korean operating surface for those clients. It
already stores aggregate community topics, questions, tone, activity, Telegram
announcement performance, and locally operated Korean-X performance. It does not
collect the four global official accounts and must not become their source of
truth.

The two products use different Supabase projects. EasyFarm also contains Telegram,
wallet, survey, and operator data that Content Engine does not need. Sharing an
operator key or either project's service-role key would create an unnecessary
cross-product privilege boundary.

The product goal is to collect every new official post, create at most the intended
number of Daily News review drafts, and use Korean audience demand and distribution
performance to decide which sourced topics deserve deeper Article or Tutorial work.

## Decision

Content Engine owns global source ingestion, immutable official-X source records,
content generation, and the `needs_review` workflow. EasyFarm owns Korean community
and distribution measurement.

Content Engine pulls a bounded, aggregate-only signal envelope from a dedicated
EasyFarm Edge Function:

```text
global official X ──> Content Engine source ledger
                            │
EasyFarm aggregate signals ─┤ deterministic candidate ranking
                            │
                            └─> Daily News / source-qualified Article
                                      │
                                      └─> needs_review
```

The bridge uses a new high-entropy server-to-server credential that cannot access
the EasyFarm operator API, browser dashboards, raw tables, or Content Studio. The
EasyFarm response excludes raw messages, Telegram IDs, usernames, group IDs,
wallets, surveys, and other user-level data.

EasyFarm signals are editorial selection evidence only. They are never factual
source material. Headlines, explanations, and claims must remain supported by the
official X post or another explicitly linked official source.

Freeform community digest themes, questions, summaries, and raw messages are not
part of the V1 bridge. Even generalized text can contain identifying fragments or
prompt-like content. A future learning-needs contract must first convert it into a
bounded taxonomy with cohort thresholds.

## V1 Contract

The aggregate response is versioned and client-scoped. It contains:

- observation window and metric freshness;
- sentiment index, label, method, and sample size;
- bounded, sanitized aggregate topic labels and counts;
- Telegram post reach, reactions, forwards, clicks, and top public post links;
- locally operated Korean-X reach and engagement totals plus top public posts;
- bounded demand terms with deterministic weights;
- explicit privacy flags confirming that raw messages and user identifiers are
  absent.

Requests are limited to `yellow`, `origintrail`, `squid`, and `babylon`, and to a
maximum 31-day observation window. Unknown fields do not become database queries or
actions. Optional metric-source failures produce empty sections with freshness
metadata; authentication, client, schema-version, and time-window failures are
rejected.

Community data is available only when exactly one explicit client room heartbeat
has been observed in the previous 48 hours. Missing, expired, or conflicting
room mappings fail closed, and every aggregated message must carry the same
client tag.

Content Engine validates the full response before using it. Invalid, stale, or
unavailable signals fail open to the existing official-X ranking so EasyFarm cannot
stop source collection or draft generation.

## Collection and Draft Limits

Source collection and draft reservation are separate concerns.

- Every cron run polls and records unseen official posts even when that client
  already has a Daily News draft reserved for the KST day.
- Each automation poll explicitly requires a complete set of up to 200 unseen
  posts. If X reports another page beyond that bound, the run fails before
  advancing the cursor so an operator can increase or backfill the window
  without silently losing source evidence. Manual generation remains a bounded
  newest-first sample and does not advance this cursor.
- Source cursor compare-and-swap, external X post ID deduplication, and immutable
  source evidence remain authoritative.
- V1 keeps the existing limit of at most one review draft per client per KST day
  and four across the workspace.
- All generated content stops at `needs_review`.

This prevents a morning draft from causing the rest of that day's official posts
to disappear from the editorial inbox.

## Ranking Rules

Candidate ranking combines:

1. official-source completeness and announcement signals;
2. the official post's own public engagement snapshot;
3. bounded term overlap with EasyFarm aggregate demand terms.

The community contribution is capped so it can reorder otherwise valid official
candidates but cannot turn a greeting, reply, retweet, skipped campaign, or
unsupported source into a candidate. Ranking is deterministic and versioned.

Sentiment and attention remain separate:

- high attention is not assumed to be positive;
- negative or high-friction discussion may justify an explanatory Tutorial
  recommendation, not promotional copy;
- raw counts are not compared across clients as though their audience sizes were
  equal.

## Article and Tutorial Boundary

V1 generates an Article automatically only when the selected official source meets
the existing source-lock requirement, such as a complete X Note of at least 300
characters. A popular short post does not justify invented long-form facts.

Yellow and Squid may receive a Tutorial recommendation, but a reviewer must still
start the Tutorial from Content Studio. OriginTrail and Babylon Tutorial generation
remains unsupported. V1 does not weaken `AUTOMATION_ENABLE_TUTORIALS=false`.

Exact reaction-based promotion requires content-level attribution that does not yet
exist across the products. Phase 2 must add a non-PII correlation ID from a Content
Studio version to its Telegram message and Typefully draft/X post, wait for a
12–72-hour observation window, and then create an idempotent promotion reservation.
Until that contract exists, V1 ranks topics and sources but does not claim that a
specific Daily News asset automatically became an Article or Tutorial because of
its measured performance.

## Options Considered

### Option A: Dedicated aggregate pull API (chosen)

| Dimension | Assessment |
|---|---|
| Privilege | Least privilege |
| Privacy | Aggregate only |
| Failure isolation | Strong |
| Implementation cost | Medium |

**Pros:** no database-key sharing, versioned contract, independent deployments,
EasyFarm remains the measurement source of truth.

**Cons:** requires a new Edge Function, a dedicated secret, and freshness handling.

### Option B: Share EasyFarm service-role or operator credentials

| Dimension | Assessment |
|---|---|
| Privilege | Excessive |
| Privacy | Unsafe |
| Failure isolation | Weak |
| Implementation cost | Low |

Rejected because the available credentials can reach unrelated user, wallet,
survey, and operator data.

### Option C: Copy all EasyFarm events into Content Studio

| Dimension | Assessment |
|---|---|
| Privilege | High |
| Privacy | High risk |
| Failure isolation | Weak |
| Implementation cost | High |

Rejected because Content Engine needs editorial aggregates, not a second copy of
the community event ledger.

## Consequences

- Daily source intake becomes complete even after the day's draft is reserved.
- Candidate choice becomes informed by Korean demand without contaminating factual
  source evidence.
- EasyFarm and Content Engine can deploy and fail independently.
- Operators must provision and rotate one new dedicated bridge secret.
- V1 improves Daily News selection and preserves deeper-content recommendations,
  but exact asset-to-performance promotion remains a Phase 2 deliverable.
- Automatic approval, Telegram posting, X publishing, and Figma mutation remain
  outside this bridge.

## V1 Acceptance Criteria

- [ ] All four configured global X handles continue to poll after a daily draft is
      reserved.
- [ ] EasyFarm rejects missing/invalid auth, unknown clients, unsupported schemas,
      and windows over 31 days.
- [ ] The response contains no raw message, Telegram/user/group/wallet identity,
      operator-only field, or freeform community digest text.
- [ ] Content Engine strictly validates and bounds the response.
- [ ] Signal failure leaves the existing official-X candidate path operational.
- [ ] Community demand can reorder valid candidates but cannot admit skipped,
      low-signal, reply, or retweet posts.
- [ ] Generated outputs remain `needs_review`; no publication or Figma job is
      created.
- [ ] Article source-length and Tutorial client/manual-review constraints remain
      enforced.
- [ ] Both repositories have contract, security, failure, and deterministic-ranking
      tests before either production deployment.
