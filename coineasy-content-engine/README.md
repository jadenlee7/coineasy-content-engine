# CoinEasy Content Engine

> **Multi-tenant Korean content generation for Web3 clients.**  
> One service. N clients. Each with their own brand, glossary, and voice.

Powered by Claude Sonnet + Playwright. Deployed on Railway.

---

## What it does

Takes an English source (tweet, blog post, article) and produces **Korean education carousels** (3-5 slides) or **news banners** (1 image) ready for X, Telegram, and other social channels.

Each slide is 1080×1080 PNG with client branding automatically applied.

**Current clients:**
- Yellow Network (`yellow`)
- Squid Router (`squid`)

**Cost:** ~$0.03 per carousel · ~$6/month for 1 client · ~$14/month for 10 clients.

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
│   └── edu_carousel_pipeline.py
├── renderers/
│   ├── playwright_renderer.py   # HTML + Jinja → PNG
│   └── template_resolver.py     # override > core precedence
└── templates/edu/      # 8 base layouts (P1-P8)

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
- **Anthropic Claude Sonnet 4.6** — content generation
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
