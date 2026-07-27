# ADR-004: Exact-link content performance recommendations

**Status:** Accepted
**Date:** 2026-07-27
**Deciders:** CoinEasy product, community, content, and engineering leads

## Context

Phase 1 uses aggregate EasyFarm demand terms to rank new official-X sources.
Those terms describe Korean audience interest, but they do not identify which
Content Studio version produced a particular local X or Telegram post.

Matching by copy similarity, client, or posting time can assign another
operator's post to the wrong content version. That would make a “this Daily News
performed well” recommendation untrustworthy.

Content Studio already has a version-specific `publications` record with a
channel and public external URL. EasyFarm already observes the same public URLs
when it collects local X and Telegram metrics. The two systems can therefore
join on a canonical public URL without sharing database credentials, raw
community messages, user identifiers, or platform-native private IDs.

## Decision

Phase 2 uses an exact, one-way aggregate pull:

```text
Content Studio version
  └─ human records the already-public X or Telegram URL
       └─ Content Studio publication row
            └─ exact canonical URL match
                 └─ EasyFarm aggregate performance candidate
                      └─ immutable evidence + review recommendation
```

Recording a link never publishes, edits, schedules, approves, or exports
content. It only records that an already-public post belongs to the selected
stored version.

EasyFarm schema `1.1` keeps the complete `1.0` response and adds at most five
performance candidates. Each candidate contains:

- a stable SHA-256 candidate ID;
- `x` or `telegram`;
- a canonical public URL and publication timestamp;
- same-client, same-channel reach and interaction percentiles;
- a bounded community-alignment count;
- a deterministic score and fixed reason codes;
- Article and, when qualified, Tutorial format hints.

Candidates contain no raw community copy, user identifiers, private Telegram
room URL, Typefully draft ID, native database ID, wallet, or factual source
text. EasyFarm signals remain editorial evidence, not factual evidence.

## Eligibility and score

A channel is eligible only when:

- its metrics are no more than 24 hours stale;
- the post is between 12 and 72 hours old;
- the same-client, same-channel cohort has at least five posts;
- the X scan is complete and the post has at least 100 impressions, or the
  Telegram post has at least 50 views;
- the URL is a canonical public `x.com/.../status/...` or
  `t.me/<public-channel>/<message-id>` URL.

The score is:

```text
0.55 × reach percentile
+ 0.35 × interaction percentile
+ 0.10 × min(community matches, 3) / 3
```

Candidates below `0.70` are omitted. Tutorial hints require `0.80`, a bounded
how-to/documentation signal, community alignment, and a Yellow or Squid client.
Raw counts are never compared across clients or channels. Within a cohort,
unique observations retain their deterministic rank, tied observations use
their average occupied rank, and zero interaction contributes a zero
interaction percentile.

## Content Engine boundary

Content Engine validates the entire `1.1` envelope, recomputes the score, and
stores immutable evidence before creating a recommendation.

A candidate creates a recommendation only when its canonical URL matches
exactly one publication in the same workspace, client, and channel. Missing or
ambiguous matches fail closed. Each accepted performance-evidence snapshot can
create at most one immutable recommendation per target kind. A later fresh
snapshot creates a new history row rather than mutating the old one; the team
UI collapses that history to the newest unexpired recommendation for each
publication, target kind, and policy version.

Recommendations may be stored even when the attached official source is too
short for deeper content. The team UI then shows `source_ready=false` as a
visible, read-only explanation with no generation CTA. The current product has
no source-enrichment or source-rebinding flow, so that immutable recommendation
cannot later become actionable. Article or Tutorial input is enabled only on a
separate recommendation that was created with at least 300 characters of pinned
official source material.

Every accepted fresh snapshot remains an immutable history row, including a
later snapshot for the same publication and target kind. History retention is
distinct from team-UI visibility: the UI selects the newest recommendation per
publication, target kind, and policy version only while its evidence is within
the latest 24 hours. An older row remains auditable but is neither displayed as
current nor made actionable by a newer metric snapshot.

All recommendations are manual review aids:

- no generation job is queued by the performance recorder;
- no approval or publication status is changed;
- no Telegram or X post is sent;
- no Figma file or link is created;
- Tutorial remains limited to Yellow and Squid.

## Failure behavior

- EasyFarm `1.0` remains backward compatible.
- Unsupported, stale, truncated, private, under-age, over-age, or small-cohort
  data produces no candidate.
- An invalid `1.1` response is discarded in full.
- Signal or evidence persistence failure does not stop official-X collection or
  the existing Daily News workflow.
- The library remains available when recommendation retrieval is unavailable.

## Consequences

- The team must paste the public post URL after a manual publication until a
  future publisher writes it automatically.
- A recommendation can be traced to one stored version and one public post.
- Recommendation history remains immutable while the review UI shows only the
  newest recommendation backed by evidence generated within the latest 24
  hours. Expired history is retained but is not a current recommendation.
- Exact attribution is available without a second cross-product write API.
- Full auto-generation remains intentionally out of scope until approval,
  publication, and longer official-source workflows are live.

## Local channel allowlist

The link recorder and EasyFarm candidate producer both enforce the same
client/channel account mapping:

| Client | X | Telegram |
|---|---|---|
| Yellow | `yellow__korea` | `yellowkorea_ann` |
| OriginTrail | `origin_trail_kr` | `origintrailkr` |
| Squid | `squidkorea` | `squid_kor_update` |
| Babylon | `babylonkorean` | `babylonbtc` |

Share parameters and fragments may be removed before submission, but the
stored URL is always canonical. Private Telegram `/c/` links and any URL from a
different account fail closed.

For Telegram, the `channels.announce` values in EasyFarm
`clients/<client>.json` are the canonical performance-attribution allowlist;
they normalize to the usernames in this table and are mirrored by Content
Engine's public-channel configuration. Telegram publishing still targets the
server-only environment channel ID, not this public URL. Neither the publisher
ID nor another publisher setting may widen or replace the shared performance
allowlist. The local X mapping is likewise fixed by the shared performance
policy rather than inferred from a publisher target.
