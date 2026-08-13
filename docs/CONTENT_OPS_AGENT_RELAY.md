# CoinEasy content-operations agents and relay

Status: staged rollout  
Date: 2026-08-13

## Why the relay bot is separate

In simple terms, Grok is the content team's brain and the relay bot is its
internal courier. The courier moves a versioned work card between the review
and design rooms; it cannot publish to a client's Korean announcement channel
or use Typefully. If an agent makes a mistake, the mistake stops inside the
team room instead of becoming a public client post.

The existing personal review DM, the new internal relay, each client's Telegram
publisher, and Typefully therefore keep separate credentials and permissions.
No fallback is allowed between those trust boundaries.

## Existing operating pattern

The team already hands off an official X post from CoinEasy Management and
receives a completed banner from EASY Design. Content Engine already provides
official-X intake, client-specific Korean GTM generation, immutable review
versions, banner rendering, double fact-check approval, exact Telegram
publication, Typefully drafts, and KPI evidence. This design joins those pieces
without turning Telegram chat text into an unverified factual source.

## Grok employee roles

One `CoinEasy Content Ops` agent may perform the following repetitive roles.
The independent `CoinEasy Content QA` agent remains separate.

1. **Official-source scout** — inspect configured official client X accounts,
   ignore replies/retweets/low-signal posts, and pin the exact post, media, and
   timestamp. Telegram requests can choose work priority but cannot add facts.
2. **Korean GTM editor** — preserve the official claim boundary while rewriting
   the value, context, and call to action in natural Korean for the selected
   client's own audience and tone.
3. **Design coordinator** — create a versioned design brief containing the
   official reference, required Korean copy, output size, client asset rules,
   and `content_version_id`; request the banner and match the returned file to
   that exact version.
4. **Release-prep operator** — assemble a private review packet with Telegram
   copy, X copy, banner, primary URLs, fact-check evidence, and KPI category.
   After human approval, prepare a Typefully **draft** and an exact Telegram
   publication request. It does not click the final public-send action.
5. **KPI recorder** — after publication, bind the real public URLs to the exact
   version so the monthly Daily News, Article, Tutorial, and Community Event
   counts remain auditable.

`CoinEasy Content QA` independently checks source facts, final Korean claims,
and client branding. Its PASS/WARN/BLOCK verdict is advisory and cannot approve
or publish.

## Work-item state machine

```text
official_source_detected
  -> source_verified
  -> korea_gtm_draft_ready
  -> design_requested
  -> banner_received
  -> needs_review
  -> human_double_fact_check_approved
  -> typefully_draft_ready + telegram_publication_queued
  -> published
  -> kpi_recorded

WARN  -> revision_requested -> korea_gtm_draft_ready
BLOCK -> source_required or brand_fix_required
```

Every transition carries `content_item_id`, `content_version_id`, `client_id`,
`content_kind`, primary source URLs, and asset hashes. A returned banner without
the expected version ID is quarantined for manual matching. A revised version
invalidates earlier review and publication eligibility.

## Telegram and Typefully boundaries

- The internal relay bot may send messages and media only in allowlisted private
  operations rooms. It is never an administrator.
- It receives no client Telegram channel IDs, client bot tokens, Typefully key,
  X bearer token, Figma credential, Supabase service-role key, or publication
  worker token.
- Client Telegram publication stays behind the immutable exact-version queue
  and human `double-fact-check@1` approval.
- Typefully starts in draft-only mode with `publish_at: null`. A human reviews
  the selected social set, final copy, media, and timing before scheduling.
- Agent-to-agent replies use one work-item/version correlation ID and a bounded
  attempt count. Bots do not answer each other indefinitely.

## Dedicated relay configuration

Railway only:

```text
TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN
TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID
```

The relay configuration is ignored unless both values are valid. It is also
ignored if either the bot token or destination duplicates the personal review
delivery. The client publication workers keep their existing, separate
credentials.

## `CoinEasy Content Ops` agent instruction

```text
You are CoinEasy Content Ops. Perform repetitive content-team preparation, not
final approval or public publication.

For each authorized work item:
1. Read AGENTS.md and docs/CONTENT_OPS_AGENT_RELAY.md. Use content_item_id and
   content_version_id as the correlation and deduplication keys.
2. Inspect the selected client's configured official X account and pin the
   exact primary post, media, timestamp, and canonical URL. Telegram messages
   can prioritize a task but are not factual evidence. Never invent or infer a
   claim beyond the primary source.
3. Prepare Korean GTM copy in that client's own tone. Explain why the update
   matters to Korean users, but preserve names, numbers, dates, networks,
   products, links, and calls to action exactly as supported.
4. Produce a design brief for EASY Design: client, content kind, dimensions,
   exact official visual reference, approved assets/template, Korean overlay
   copy, safe area, forbidden changes, content_version_id, and due status.
5. When a banner returns, verify its version, dimensions, official logo,
   typography, spacing, source fidelity, Korean legibility, and asset hash.
   Quarantine an unversioned or mismatched file.
6. Assemble one private review packet for CoinEasy Content QA and the human
   reviewer. Include only primary URLs, final copy, banner, concrete checks,
   and the exact IDs. Do not expose secrets or private chat history.
7. Only after the exact version has a valid human double-fact-check approval,
   prepare a Typefully draft with publish_at null and an exact Telegram
   publication request. Never click public send, schedule, or publish.
8. Record real publication URLs for KPI only after provider confirmation.

Stop with BLOCK when the source, version, banner, account, or approval is
missing or ambiguous. Do not approve your own work. Do not start bot loops.
```

## Rollout and acceptance

1. Create the dedicated bot and add it only to the private operations room.
2. Configure the two Railway relay variables; do not change client publishers.
3. Send one non-mock `needs_review` packet and verify personal DM and the team
   room receive the same exact version through different bots.
4. Run one Squid and one non-Squid canary through source, copy, design brief,
   banner return, independent QA, and human approval. Keep all public sends off.
5. Create Typefully drafts with `publish_at: null`; verify account and copy
   manually. Then allow one explicitly approved Telegram publication.
6. Enable scheduled agent runs only after duplicate suppression, version
   matching, retry limits, and delivery alerts pass.

Revisit direct scheduling only after at least 20 successful version-matched
canaries across all clients with no wrong-brand, unsupported-claim,
wrong-destination, or duplicate-publication incident.
