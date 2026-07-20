"""
CoinEasy Content Engine · FastAPI Server

Single service serving multiple clients (Yellow, Squid, etc.) via:
    POST /clients/{client_id}/generate

Authentication: X-API-Key header
Deployment: Railway
"""
import os
import time
import traceback
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.client_config import (
    CLIENTS_DIR,
    list_active_clients,
    load_client_config,
    list_available_clients,
)
from core.orchestrator import generate_edu_carousel, generate_news_card
from core.sources.x_client import XClient
from core.generators.daily_news import DailyNewsGenerator
from core.publishers.base import Publisher
from core.publishers.telegram import TelegramPublisher
from core.publishers.typefully import TypefullyPublisher
from api.security import check_any_valid_key, resolve_safe_path, validate_client_scope


# ────────────────────────────────────────────────────
# App setup
# ────────────────────────────────────────────────────

app = FastAPI(
    title="CoinEasy Content Engine",
    description="Multi-tenant Korean content generation for Web3 clients",
    version="1.0.0",
)

API_SECRET = os.environ.get("API_SECRET", "")
OUTPUT_ROOT = Path(os.environ.get("OUTPUT_ROOT", "/tmp/content_engine_output"))


def _check_auth(x_api_key: str):
    check_any_valid_key(x_api_key)


# ────────────────────────────────────────────────────
# Request/Response models
# ────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    source_content: str
    source_type: str = "tweet"  # "tweet" | "blog" | "article"
    source_url: str = ""
    series_number: Optional[str] = None
    mock_llm: bool = False  # for smoke testing


class GenerateResponse(BaseModel):
    client_id: str
    content_type: str
    series: dict
    lesson_count: int
    png_paths: list[str]
    manifest_path: str
    duration_ms: int


class NewsCardRequest(BaseModel):
    source_content: str
    source_type: str = "tweet"  # "tweet" | "blog" | "article"
    source_url: str = ""
    source_image_url: str = ""  # X media URL; validated against pbs.twimg.com before download
    mock_mode: bool = False  # for smoke testing
    template_style: Literal["remix", "classic", "editorial", "signal"] = "classic"


class NewsCardResponse(BaseModel):
    client_id: str
    content_type: str
    spec: dict          # localized card copy + source_logo_visible placement signal
    png_path: str       # single 1080×1080 card, not a list
    template_style: str
    requested_template_style: str
    source_image_used: bool
    source_visual_path: Optional[str] = None
    manifest_path: str
    duration_ms: int


class DailyNewsRequest(BaseModel):
    hours: int = 24
    max_results: int = 30


class DailyNewsResponse(BaseModel):
    client_id: str
    content_type: str
    handle: str
    fetched_count: int
    filtered_count: int
    news: dict
    duration_ms: int


CHANNEL_NAMES = ("typefully", "telegram")


class PublishRequest(BaseModel):
    hours: int = 24
    max_results: int = 30
    dry_run: bool = True
    channels: list[Literal["typefully", "telegram"]] = Field(
        default_factory=lambda: list(CHANNEL_NAMES)
    )
    publish_at: Optional[str] = None  # None | "next-free-slot" | ISO8601


class ClientInfo(BaseModel):
    client_id: str
    name: str
    active: bool
    primary_color: str
    features: dict


# ────────────────────────────────────────────────────
# Routes
# ────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "coineasy-content-engine",
        "status": "ok",
        "clients_loaded": len(list_active_clients()),
    }


@app.get("/health")
async def health():
    """Railway health check endpoint."""
    return {"ok": True, "ts": int(time.time())}


@app.get("/clients", response_model=list[ClientInfo])
async def list_clients(x_api_key: str = Header(default="")):
    _check_auth(x_api_key)
    results = []
    for client_id in list_available_clients():
        try:
            cfg = load_client_config(client_id)
            results.append(ClientInfo(
                client_id=cfg.client_id,
                name=cfg.name,
                active=cfg.active,
                primary_color=cfg.brand.primary_color,
                features={
                    "education_carousel": cfg.feature_flags.education_carousel,
                    "news_card": cfg.feature_flags.news_card,
                },
            ))
        except Exception as e:
            print(f"⚠ Failed to load client '{client_id}': {e}")
    return results


@app.post("/clients/{client_id}/generate/edu-carousel", response_model=GenerateResponse)
async def generate_carousel(
    client_id: str,
    req: GenerateRequest,
    x_api_key: str = Header(default=""),
):
    """Generate an education carousel for a client."""
    _check_auth(x_api_key)

    # Verify client exists
    try:
        load_client_config(client_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Client '{client_id}' not found")

    # Unique output dir per request
    ts = int(time.time())
    output_dir = OUTPUT_ROOT / client_id / f"edu_{ts}"

    try:
        result = await generate_edu_carousel(
            client_id=client_id,
            source_content=req.source_content,
            source_type=req.source_type,
            source_url=req.source_url,
            series_number=req.series_number,
            output_dir=output_dir,
            mock_llm=req.mock_llm,
        )
    except HTTPException:
        raise
    except Exception as e:
        # Surface full traceback to Railway logs for debugging
        traceback.print_exc()
        raise HTTPException(500, f"Generation failed: {type(e).__name__}: {e}")

    return GenerateResponse(
        client_id=result.client_id,
        content_type=result.content_type,
        series=result.series_meta,
        lesson_count=len(result.png_paths),
        png_paths=result.png_paths,
        manifest_path=result.manifest_path,
        duration_ms=result.duration_ms,
    )


@app.post("/clients/{client_id}/generate/news-card", response_model=NewsCardResponse)
async def generate_news(
    client_id: str,
    req: NewsCardRequest,
    x_api_key: str = Header(default=""),
):
    """Generate a single 1080×1080 news card for a client."""
    _check_auth(x_api_key)

    try:
        load_client_config(client_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Client '{client_id}' not found")

    # Nanosecond-resolution directory names keep simultaneous card requests
    # from overwriting each other's cleaned source visual before Netlify fetches it.
    ts = time.time_ns()
    output_dir = OUTPUT_ROOT / client_id / f"news_{ts}"

    try:
        result = await generate_news_card(
            client_id=client_id,
            source_content=req.source_content,
            source_type=req.source_type,
            source_url=req.source_url,
            source_image_url=req.source_image_url,
            output_dir=output_dir,
            mock_mode=req.mock_mode,
            template_style=req.template_style,
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Generation failed: {type(e).__name__}: {e}")

    return NewsCardResponse(
        client_id=result.client_id,
        content_type=result.content_type,
        spec=result.spec,
        png_path=result.png_path,
        template_style=result.template_style,
        requested_template_style=result.requested_template_style,
        source_image_used=result.source_image_used,
        source_visual_path=result.source_visual_path,
        manifest_path=result.manifest_path,
        duration_ms=result.duration_ms,
    )


async def _run_daily_news_generation(client_id: str, hours: int, max_results: int) -> dict:
    """Shared pipeline: load client → fetch tweets → LLM filter → KR translate.

    Raises HTTPException on unrecoverable errors. Returns a dict matching
    DailyNewsResponse shape.
    """
    try:
        config = load_client_config(client_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Client '{client_id}' not found")

    if not config.active:
        raise HTTPException(400, f"Client '{client_id}' is inactive")

    twitter = config.content_sources.twitter
    if not twitter or not twitter.handle:
        raise HTTPException(400, f"Client '{client_id}' has no twitter handle configured")

    start = time.time()

    try:
        x = XClient()
        tweets = await x.get_recent_tweets(
            username=twitter.handle,
            hours=hours,
            max_results=max_results,
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"X API fetch failed: {type(e).__name__}: {e}")

    try:
        gen = DailyNewsGenerator()
        filtered = await gen.filter_tweets(tweets)
        news = await gen.translate(filtered, client_name=config.name)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Daily news generation failed: {type(e).__name__}: {e}")

    duration_ms = int((time.time() - start) * 1000)

    return {
        "client_id": client_id,
        "content_type": "daily_news",
        "handle": twitter.handle,
        "fetched_count": len(tweets),
        "filtered_count": len(filtered),
        "news": news,
        "duration_ms": duration_ms,
    }


@app.post("/clients/{client_id}/generate/daily-news", response_model=DailyNewsResponse)
async def generate_daily_news(
    client_id: str,
    req: DailyNewsRequest,
    x_api_key: str = Header(default=""),
):
    """Fetch last N hours of tweets from the client's X handle, LLM-filter, translate to Korean."""
    _check_auth(x_api_key)
    result = await _run_daily_news_generation(
        client_id=client_id,
        hours=req.hours,
        max_results=req.max_results,
    )
    return DailyNewsResponse(**result)


def _load_publishing_yaml(client_id: str) -> dict[str, Any]:
    """Read the raw `publishing:` section from clients/{id}/config.yaml.

    Reading raw yaml here keeps the typed ClientConfig stable while letting the
    publish endpoint pick up new fields (typefully.social_set_id, telegram.bot_env, ...)
    without dataclass churn.
    """
    config_path = CLIENTS_DIR / client_id / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("publishing", {}) or {}


def _build_publisher(channel: str, ch_cfg: dict[str, Any], client_id: str) -> Publisher:
    if channel == "typefully":
        social_set_id = ch_cfg.get("social_set_id")
        if not social_set_id:
            raise ValueError("typefully.social_set_id missing in client config")
        return TypefullyPublisher(social_set_id=social_set_id, client_id=client_id)
    if channel == "telegram":
        bot_env = ch_cfg.get("bot_env")
        channel_env = ch_cfg.get("channel_env")
        if not bot_env or not channel_env:
            raise ValueError("telegram.bot_env / telegram.channel_env missing in client config")
        return TelegramPublisher(bot_env=bot_env, channel_env=channel_env, client_id=client_id)
    raise ValueError(f"Unknown channel: {channel}")


@app.post("/clients/{client_id}/publish/daily-news")
async def publish_daily_news(
    client_id: str,
    req: PublishRequest,
    x_api_key: str = Header(default=""),
):
    """Generate the daily news brief and publish to selected channels.

    Reuses the existing daily-news generator. If no tweets pass the filter,
    publishing is skipped. Each channel publishes independently — one failure
    does not block other channels.
    """
    _check_auth(x_api_key)

    generation_result = await _run_daily_news_generation(
        client_id=client_id,
        hours=req.hours,
        max_results=req.max_results,
    )

    news = generation_result["news"]
    if news.get("is_empty"):
        return {
            "client_id": client_id,
            "generation": generation_result,
            "publishing": {
                "skipped": True,
                "reason": "no_tweets_in_window",
            },
        }

    pub_config = _load_publishing_yaml(client_id)
    publishing_results: dict[str, dict[str, Any]] = {}

    for channel in req.channels:
        ch_cfg = pub_config.get(channel) or {}
        if not ch_cfg.get("active"):
            publishing_results[channel] = {
                "ok": False,
                "channel": channel,
                "skipped": True,
                "skipped_reason": "channel_inactive",
            }
            continue

        try:
            publisher = _build_publisher(channel, ch_cfg, client_id)
        except Exception as e:
            publishing_results[channel] = {
                "ok": False,
                "channel": channel,
                "error": f"config_error: {e}",
            }
            continue

        try:
            publishing_results[channel] = await publisher.publish(
                news,
                dry_run=req.dry_run,
                publish_at=req.publish_at,
            )
        except Exception as e:
            traceback.print_exc()
            publishing_results[channel] = {
                "ok": False,
                "channel": channel,
                "error": f"{type(e).__name__}: {e}",
            }

    return {
        "client_id": client_id,
        "generation": generation_result,
        "publishing": publishing_results,
    }


@app.get("/files/{path:path}")
async def serve_file(path: str, x_api_key: str = Header(default="")):
    """Serve generated assets with per-client scope validation."""
    safe_path = resolve_safe_path(path)
    validate_client_scope(x_api_key, safe_path)
    if not safe_path.exists() or not safe_path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(safe_path)


# ────────────────────────────────────────────────────
# Startup
# ────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    clients = list_active_clients()
    print(f"✓ Content Engine started with {len(clients)} active clients:")
    for cfg in clients:
        print(f"  - {cfg.client_id:12s} {cfg.name}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
