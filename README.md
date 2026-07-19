# CoinEasy Content Engine

> **Multi-tenant Korean content generation for Web3 clients.**  
> One service. N clients. Each with their own brand, glossary, and voice.

Powered by Claude Opus + Playwright. Deployed on Railway.

---

## What it does

Takes an English source (tweet, blog post, article) and produces **Korean education carousels** (3-5 slides) or **news cards** (1 image) ready for X, Telegram, and other social channels.

Each slide is 1080×1080 PNG with client branding automatically applied.

**Current clients:**
- Yellow Network (`yellow`)
- Squid Router (`squid`)
- OriginTrail Korea (`origintrail`) — news_card only; 브랜드 팔레트 확정 대기 (placeholder)

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
python scripts/new_client.py --id squid --name "Squid Router"

# 2. Add logos
cp ~/logos/squid_white.png   clients/squid/assets/logo_dark.png
cp ~/logos/squid_black.png   clients/squid/assets/logo_light.png

# 3. Edit config
vim clients/squid/config.yaml
# → Fill in brand colors, preserve_terms, glossary, Telegram channels, routing signals

# 4. Test
python scripts/generate_cli.py --client squid --mock \
  --source "Squid Router just integrated with..."

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
POST /clients/{client_id}/generate/news-card       # Generate 1080×1080 news card
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

News-card variant (single 1080×1080 card):
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

**Request** (`NewsCardRequest`): `source_content` (required), `source_type` (default `"tweet"`), `source_url` (default `""`), `source_image_url` (optional, restricted to X's image CDN), `mock_mode` (default `false`, skips LLM for smoke), `template_style` (`"remix"` | `"classic"` | `"editorial"` | `"signal"`, default `"classic"`).

The Netlify console accepts a public X status URL by itself. Its server-side function imports the post text and first attached photo, then forwards the extracted content, canonical source URL, and allowlisted image URL to the Railway API. The recommended `remix` style reads visible banner text with Claude vision, preserves the complete original visual, and adds a branded Korean GTM panel. Posts without an available image automatically fall back to `classic`. Article and blog URLs still require pasted source text.

After generation, the console also returns channel-ready copy in `channel_copy`: `telegram` is a Korean GTM announcement with the generated headline and bullets, CTA, canonical original link, and client hashtags; `x` preserves only the imported or pasted source content without adding translated copy, CTA, or hashtags. Both are available as one-click copy blocks below the card preview.

**Response** (`NewsCardResponse`): `client_id`, `content_type` (`"news_card"`), `spec` (`{label, date, headline, body_lines, source_url, theme}`), `png_path` (**str, single card — not a list**), `requested_template_style`, `template_style` (actual style after fallback), `source_image_used`, `manifest_path`, `duration_ms`.

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

---

## Architecture

```
core/                   # Client-agnostic engine
├── client_config.py    # YAML config loader
├── orchestrator.py     # LLM → render → manifest
├── llm/
│   ├── edu_carousel_pipeline.py
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

These live separately in the existing `YellowKR` bot:
- Telegram bot for approval workflow
- `@Yellow__Korea` tweet scraping & community posting
- Twitter API v2 polling / Nitter RSS monitoring

**Plan**: This engine exposes HTTP API; existing `YellowKR` (or other clients' bots) calls it with new sources and handles approval/posting on their end.

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
