# ADR-006: Figma, aggregate learning, and monthly KPI orchestration

Status: Proposed for staged production rollout  
Date: 2026-07-30

## Decision

CoinEasy Content Engine coordinates four sources without merging their trust
boundaries:

1. Official client X/linked documentation remains the only factual source for
   generated copy.
2. EasyFarm Content Signals schema `1.2` supplies aggregate community sentiment,
   public-channel performance, and thresholded quiz-learning priority.
3. CoinEasy Management Figma supplies explicitly approved visual references
   through `config/figma_templates.json`; “newest frame” alone is not approval.
4. `@CoinEasy_KPI_bot` combines publication-backed Content Engine counts with
   Notion's community-event operating record.

All generated content still stops at `needs_review`. None of these integrations
can approve, publish, mutate Figma, or turn aggregate signals into factual copy.

## Figma contract

Only frames with an explicit approval marker and exact file/node binding enter
the registry. The first registry version contains the approved `[KEEP]`
1080×1080 Daily News frames for Squid and Yellow. Babylon and OriginTrail remain
unregistered until the team approves canonical frames.

The renderer remains deterministic and local. A successful matching render may
return the approved Figma reference and version in its API/catalog metadata so a
reviewer can open the design source. A Figma edit, export, or “latest frame”
scan never happens during generation.

## Learning and sentiment contract

EasyFarm returns no question text, answer choice, quest/session ID, message,
user, Telegram, or wallet identifier. Quiz learning is suppressed unless the
whole cohort has at least 20 attempts and 5 participants. Every returned
category independently meets the same thresholds.

Content Engine validates freshness and records immutable aggregate learning
evidence before use. Tutorial priority can only reorder already eligible
official how-to/documentation posts. It cannot admit replies, retweets,
configured skip phrases, greetings, or unsupported clients. If retrieval,
validation, freshness, or evidence persistence fails, ranking falls back to the
existing official-X-only path.

## Monthly KPI contract

For every active core client:

- Daily News: one published item per KST calendar day.
- Article: two published items per KST month.
- Tutorial: two published items per KST month.
- Community event: two completed events per KST month.

Content Engine counts distinct content items only when a publication record is
exactly `published` with a `published_at` inside the KST month. Drafts,
`needs_review`, approved items, and scheduled items are shown as pipeline
context but never as completed KPI.

Community events remain sourced from the exact active Notion project row.
Content Engine does not invent or overwrite that operational record. The
canonical `Babylon` row wins over the legacy `Bablyon` typo.

The bot reads `/api/kpi/monthly` with the dedicated
`CONTENT_KPI_SYNC_TOKEN`. This token is separate from `API_SECRET`,
`STUDIO_ACCESS_TOKEN`, EasyFarm's content-signals key, and Supabase credentials.
Either data source may degrade independently; the report labels unavailable
sections instead of converting missing data to completion.

## Deployment order

1. Apply EasyFarm `20260730_content_signals_learning_v12` and deploy the
   `content-signals-api` function.
2. Apply Content Engine `20260730123000_content_learning_evidence` and
   `20260730120000_monthly_content_kpi`.
3. Deploy Content Engine and set the same new `CONTENT_KPI_SYNC_TOKEN` in
   Netlify and the KPI bot Railway service.
4. Deploy the KPI bot and verify `/health`, `/status`, then a manual KPI report.
5. Approve Babylon/OriginTrail Figma frames before adding their registry nodes.

Each stage is independently reversible and leaves external publishing under
human review.
