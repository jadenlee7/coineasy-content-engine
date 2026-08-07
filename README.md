# CoinEasy Content Engine

> **Multi-tenant Korean content generation for Web3 clients.**  
> One service. N clients. Each with their own brand, glossary, and voice.

Powered by Claude Opus + Playwright. Deployed on Railway.

---

## What it does

Takes an English source (tweet, blog post, article) and produces **Korean education carousels** (3-5 slides), **news cards** (1 image), or a source-locked **long-form article draft** with Telegram/X copy.

Education slides and generated news cards use a square canvas. A Squid official
X `remix` instead keeps the source aspect ratio so the primary banner matches
the original composition rather than becoming a letterboxed square.

**Current clients:**
- Yellow Network (`yellow`)
- Squid (`squid`)
- OriginTrail Korea (`origintrail`) — news card and article
- Babylon Korea (`babylon`) — news card and article

**Cost:** Approximate and model-dependent — budget on the order of a few dollars/month per client.

---

## Quick Start (Local)

```bash
# 1. Clone and install
git clone https://github.com/jadenlee7/coineasy-content-engine.git
cd coineasy-content-engine
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2. Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Generate (mock mode = no API call, for smoke test)
python scripts/generate_cli.py --client yellow --mock \
  --source "Yellow is chain-agnostic by design..."

# 4. Real generation
python scripts/generate_cli.py --client yellow \
  --source-file ./test_inputs/chain_agnostic.txt \
  --source-url "https://x.com/Yellow/status/xxx"

# → Output in ./output/yellow/<timestamp>/lesson_*.png
```

> **Note**: `scripts/generate_cli.py` currently supports **edu-carousel only**. News-card CLI wrapper is planned follow-up work — for now use the `POST /generate/news-card` API route or call `core.orchestrator.generate_news_card()` directly.

---

## Adding a New Client (30 min)

```bash
# 1. Scaffold (creates clients/<id>/ with starter config)
python scripts/new_client.py --id squid --name "Squid"

# 2. Add logos
cp ~/logos/squid_white.png   clients/squid/assets/logo_dark.png
cp ~/logos/squid_black.png   clients/squid/assets/logo_light.png

# 3. Edit config
vim clients/squid/config.yaml
# → Fill in brand colors, preserve_terms, glossary, Telegram channels, routing signals

# 4. Test
python scripts/generate_cli.py --client squid --mock \
  --source "Squid just integrated with..."

# 5. Activate
# Edit config.yaml: active: true
```

---

## API (Server Mode)

```bash
# Run locally
uvicorn api.server:app --reload

# Endpoints
GET  /health                                       # Railway health check
GET  /clients                                      # List all clients
POST /clients/{client_id}/generate/edu-carousel    # Generate carousel
POST /clients/{client_id}/generate/news-card       # Generate one branded news image
POST /clients/{client_id}/generate/article         # Generate source-locked Korean article
GET  /files/{path}                                 # Serve generated PNGs
```

Example call:
```bash
curl -X POST https://coineasy-content-engine.up.railway.app/clients/yellow/generate/edu-carousel \
  -H "X-API-Key: $API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "source_content": "Yellow is chain-agnostic by design...",
    "source_type": "tweet",
    "source_url": "https://x.com/Yellow/status/2046509996834206186"
  }'
```

News-card variant (one card; Squid official remix keeps source aspect ratio):
```bash
curl -X POST https://coineasy-content-engine.up.railway.app/clients/origintrail/generate/news-card \
  -H "X-API-Key: $API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "source_content": "OriginTrail launches Paranet on ...",
    "source_type": "tweet",
    "source_url": "https://x.com/origin_trail/status/xxx",
    "mock_mode": false,
    "template_style": "editorial"
  }'
```

**Request** (`NewsCardRequest`): `source_content` (required), `source_type` (default `"tweet"`), `source_url` (default `""`), `source_image_url` (automation-only for an official Squid X `remix`, restricted to an exact `pbs.twimg.com` media URL and included in the idempotency hash), `mock_mode` (default `false`, skips LLM for smoke), `template_style` (`"remix"` | `"classic"` | `"editorial"` | `"signal"`, default `"classic"`).

The Netlify console accepts a public X status URL by itself. Its server-side function imports the post text and first attached photo, then forwards the extracted content, canonical source URL, and allowlisted image URL to the Railway API. The recommended `remix` style reads visible banner text with Claude vision, preserves the complete source visual, and adds localized Korean copy without an agency mark. Squid uses a stricter official-creative translation mode: only meaningful copy already visible inside the source creative is replaced with natural Korean in the same approximate region, hierarchy, alignment, and color treatment. It never adds a separate headline panel, badge, footer, CTA, or logo; a character-only or otherwise textless creative is preserved without generated visual copy. Other clients retain the standard logo-safe-area behavior and may fall back to `classic` when an image is unavailable. A photo-backed Squid `remix` fails closed if its official image cannot be loaded, so a generic card cannot silently replace the source campaign creative. Article and blog URLs still require pasted source text.

After generation, the console returns review-only Korean copy in `channel_copy`. `telegram` is a full announcement with the generated headline and bullets, CTA, canonical original link, and client hashtags. `x` is source-locked Korean localization: it follows each client's observed cadence, preserves source handles/cashtags/hashtags, and does not automatically add a generic CTA, link, or campaign hashtags. Copy controls remain disabled until the exact version passes the two human fact-check attestations.

Each client config also contains a `brand_voice` profile derived from recent official posts: voice constants, anti-patterns, observed writing structures, channel-specific source-fidelity targets, and reference examples. These rules are injected into both news-card and education prompts. Reference examples control cadence only; their facts are explicitly barred from leaking into the current source. The shared enforcement guide lives at `.claude/brand-voice-guidelines.md`.

The console provides a stored PNG review asset and a non-persistent Figma-editable SVG reference. Generated card families remain square; Squid official-source remixes keep the verified source aspect ratio in both formats. The SVG uses named native text, shape, logo, and source-image layers rather than `foreignObject`. Any SVG or transient Article-visual edit is a new, unapproved derivative and must be imported as a new version and reviewed before publication. Designers need the configured brand fonts installed for exact typography.

## Team Content Studio

The Netlify console now has three team-facing modes:

- **Daily News** — one source into a branded PNG, editable SVG reference, and review-only Telegram/X text.
- **Article** — at least 300 pasted source characters into a source-locked Korean draft, Markdown, takeaways, and channel copy.
- **Tutorial** — Yellow or Squid source material into a multi-page PNG carousel.

The page uses a short-lived signed, HttpOnly team session. Daily News, Article, and Tutorial requests use a browser-stable idempotency UUID bound to the normalized request payload and atomically record an immutable `needs_review` version before returning. News and Tutorial also copy every validated Railway PNG to a private Supabase Storage bucket; Article records the source-locked draft without an image asset. Retrying the exact request checks the catalog first instead of generating a duplicate, while reusing its UUID for changed input returns a conflict.

The reviewed schema is applied to the existing `coineasy-meme-engine` Supabase project. Its legacy `daily_memes` data remains intact and server-only, while Content Studio uses a separate RLS-protected workspace and private bucket in the same project. The console's **보관함** view lists the three content types, filters by client/type/status, and opens the current immutable version with short-lived asset links, source evidence, and review-only channel text. A version can be approved only after its valid `double-fact-check@1` baseline is shown and the reviewer separately confirms source facts and final output claims. From a Daily News detail, the browser can request a local, non-persistent SVG; that file and every edit remain explicitly unapproved until imported as a new version and reviewed again. Each non-mock generation can also send the private reviewer a Telegram DM containing an explicitly unapproved representative banner, stored Telegram copy, and an authenticated deep link to that exact library item. The DM itself cannot approve or publish. Manually recorded public links pass the same approval gate before entering performance/KPI data. A disabled-by-default Squid-only slice can instead queue the exact approved Daily News version for one fenced Telegram `sendPhoto`; it posts the stored PNG and stored caption without regeneration or post-delivery retry. `delivery_unknown` remains visible for manual channel inspection. See `docs/ADR-009-exact-version-telegram-publication.md`, `docs/ADR-010-double-fact-check-publication-gate.md`, and `docs/TELEGRAM_PUBLICATION_RUNBOOK.md`. A later EasyFarm aggregate snapshot may create an Article or Yellow/Squid Tutorial recommendation for that exact linked post; the form is prefilled only when the pinned official source is at least 300 characters. Historical `remix` exports fail closed when their external source image is unavailable instead of returning a visually incomplete SVG.

Official-X scheduled source collection is live and stops every generated item at `needs_review`. It refreshes all four official feeds on every cron run even after a daily draft is reserved. An optional, aggregate-only EasyFarm bridge can reorder eligible official posts using bounded Korean demand terms; schema `1.2` preserves same-client/channel performance candidates and adds thresholded quiz-learning priority for already eligible official guide/documentation posts. Every signal is stored as immutable evidence and never becomes factual source copy. Recommendations never enqueue generation, approval, publication, or Figma work. Yellow Daily News and legacy Squid classic results can link to their explicitly approved CoinEasy Management frames. New Squid purpose-routed families deliberately store no Figma approval until each family receives its own reviewed registry node; Babylon/OriginTrail remain unregistered until canonical frames are approved. Netlify keeps `STUDIO_ACCESS_TOKEN`, `CONTENT_KPI_SYNC_TOKEN`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_URL`, and `CONTENT_STUDIO_WORKSPACE_ID` server-side. Review and approval screens are live: from a 보관함 detail the team records an approve or request-changes decision on the current version, with allowlisted rejection reason codes, an optional note, a required idempotency key, a stale-version guard, and a block on approving mock output. The decision writes the `approvals` row, the `content_items` status transition, and the `event_log` entry in one transaction, and rejection reason codes feed bounded brand guidance back into later generations. Approvals are attributed to the shared Studio session rather than to a person — see `docs/ADR-008-studio-review-attribution.md`. Exact Telegram publication is implemented only for approved Squid Daily News and remains behind two off-by-default flags. Publication for other clients/content kinds, X publication, per-user Supabase Auth, durable approved-SVG handoff, and the internal Figma plugin remain later delivery phases.

The first Buzz integration is a separate GET-only OriginTrail shadow projection at `/api/buzz-shadow/origintrail/batch`. It reuses the Batch review RPC but returns only a deterministic event ID, exact agent/workflow identity, review status, model tier, measured cost, official X URL, and an authenticated Studio deep-link path. Draft copy, prompts, provider IDs, token counts, Supabase credentials, and every approve/publish/deploy capability stay behind the existing execution plane. The production reader is protected by its own `BUZZ_SHADOW_ACCESS_TOKEN`, the durable receipt and `/api/buzz-delivery/origintrail` transition endpoint by a distinct `BUZZ_DELIVERY_WORKER_TOKEN`. The isolated one-shot worker fences the pinned official v0.5.4 CLI, is live on an hourly Railway cron with `--send-once`, and delivered the first fenced relay write on 2026-08-04; every run now also reconciles expired receipt leases and reports the transition counts in its output, so a `delivery_unknown` surfaces in the service logs within an hour. Both Netlify Buzz functions can swap their Supabase credential to a scoped role key (`SUPABASE_BUZZ_DELIVERY_KEY` / `SUPABASE_BUZZ_SHADOW_KEY`) with no code change. See [`docs/BUZZ_ORIGINTRAIL_SHADOW.md`](docs/BUZZ_ORIGINTRAIL_SHADOW.md) and [`docs/ADR-011-origintrail-buzz-durable-delivery.md`](docs/ADR-011-origintrail-buzz-durable-delivery.md).

**Response** (`NewsCardResponse`): `client_id`, `content_type` (`"news_card"`), `spec` (`{label, date, headline, body_lines, source_url, theme, source_logo_visible, source_text_visible, translation_regions}`), `png_path` (**str, single card — not a list**), `requested_template_style`, `template_style` (actual style after fallback), `source_image_used`, `manifest_path`, `duration_ms`.

---

## Deployment (Railway)

```bash
# First time
railway login
railway init coineasy-content-engine

# Env vars
railway variables set ANTHROPIC_API_KEY=sk-ant-...
railway variables set API_SECRET=$(openssl rand -hex 32)
railway variables set TELEGRAM_BOT_TOKEN_YELLOW=xxx  # per-client bot tokens

# Deploy
railway up
```

The Dockerfile bakes Playwright + Chromium + Korean fonts. First build ~3 min, subsequent pushes ~30 sec.

Railway-local `edu_<time_ns>` and `news_<time_ns>` run folders are cleaned at
startup and after generation. The default policy retains completed downloads for
24 hours, never removes a run younger than 30 minutes, protects active runs with a
lease marker, and caps eligible output at 2 GiB. Operators can tune
`OUTPUT_RETENTION_TTL_HOURS`, `OUTPUT_RETENTION_MAX_MIB`,
`OUTPUT_RETENTION_MIN_AGE_MINUTES`, and
`OUTPUT_RETENTION_ACTIVE_LEASE_MINUTES`; hard safety floors still prevent a
misconfigured value from deleting a just-returned Netlify download.

---

## Architecture

```
core/                   # Client-agnostic engine
├── client_config.py    # YAML config loader
├── orchestrator.py     # LLM → render → manifest
├── llm/
│   ├── edu_carousel_pipeline.py
│   ├── article_pipeline.py       # source-locked Korean long-form draft
│   └── news_card_pipeline.py    # single-card LLM spec (shared schema)
├── renderers/
│   ├── playwright_renderer.py   # HTML + Jinja → PNG
│   └── template_resolver.py     # override > core precedence
└── templates/
    ├── edu/                     # 8 base layouts (P1-P8)
    └── news/
        └── news_title_card.html # 1080×1080 single news card

clients/                # Per-tenant configs
├── yellow/
│   ├── config.yaml     # brand, LLM tuning, publishing channels
│   ├── assets/         # logos
│   └── overrides/      # (optional) custom templates
└── squid/
    └── ...

api/
└── server.py           # FastAPI multi-tenant endpoints

scripts/
├── new_client.py       # Scaffold new client in 30 sec
├── generate_cli.py     # Local testing/ad-hoc generation
└── migrate_tokens.py   # One-time template migration

Dockerfile              # Production container
railway.json            # Railway deploy config
requirements.txt
```

See `docs/ARCHITECTURE.md` for full design doc.

---

## Layouts

| ID | Name | Use |
|---|---|---|
| P1_3CARD | 3-Card Grid | "What is X?" with 3 components |
| P2_BULLETS | Icon Bullets | 3-4 capabilities/points (most common) |
| P3_BEFORE_AFTER | Comparison | A → B changes |
| P4_SUMMARY | Summary + Keywords | Final slide (always) |
| P5_COVER | Series Intro | Optional cover slide |
| P6_STEP | Sequential Steps | 1→2→3→4 process |
| P7_DEFINITION | Term Deep-Dive | Single concept explained |
| P8_DIAGRAM | Layer Stack | Architectural visualization |

LLM picks appropriate layout per lesson automatically.

---

## What's NOT in this repo (yet)

These are not yet part of the shared Content Studio workflow:
- direct Telegram callback approval (the private DM deep-links to authenticated Studio review)
- per-reviewer identity on an approval — every decision is attributed to the shared Studio session, so `approvals.reviewer_id` is null (`docs/ADR-008-studio-review-attribution.md`)
- a UI for browsing prior versions of an item; versions are recorded immutably, but 보관함 opens only the current one
- exact publication for Yellow, OriginTrail, Babylon, Article, Tutorial, or X; only Squid Daily News has a disabled-by-default Telegram action
- approved-version Figma import/link plugin

Live, and previously listed here in error: scheduled multi-client source collection with per-feed deduplication (unique `(workspace_id, client_id, source_feed_id, external_id)`, one reserved draft per `(workspace_id, kst_date, slot)`), and the review/approval screens described above.

Existing client bots can continue to call the HTTP API. The Supabase foundation in this repository is the planned shared boundary for collection, review, approval, Figma handoff, and publishing history.

---

## Tech Stack

- **Python 3.12**
- **Anthropic Claude Opus 4.8** — content generation
- **Playwright + Chromium** — HTML → PNG rendering
- **Jinja2** — templating
- **FastAPI + Uvicorn** — HTTP server
- **Pretendard Variable** — Korean typography
- **Railway** — deployment

---

## License

Private / internal to CoinEasy.

---

## Contact

Built by Jaden (CoinEasy) for Yellow Korea GTM and scaling to multi-client ops.
