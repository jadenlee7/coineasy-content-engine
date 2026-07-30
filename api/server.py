"""
CoinEasy Content Engine · FastAPI Server

Single service serving multiple clients (Yellow, Squid, etc.) via:
    POST /clients/{client_id}/generate

Authentication: X-API-Key header
Deployment: Railway
"""
import asyncio
import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from core.client_config import (
    CLIENTS_DIR,
    list_active_clients,
    load_client_config,
    list_available_clients,
)
from core.orchestrator import generate_edu_carousel, generate_news_card
from core.llm.article_pipeline import (
    ArticleInputError,
    ArticleOutputError,
    generate_article_spec,
)
from core.sources.x_client import XClient
from core.generators.daily_news import DailyNewsGenerator
from core.publishers.base import Publisher
from core.publishers.telegram import TelegramPublisher
from core.publishers.typefully import TypefullyPublisher
from api.security import check_any_valid_key, resolve_safe_path, validate_client_scope
from api.output_retention import (
    cleanup_generated_runs_best_effort,
    clear_run_active,
    mark_run_active,
)


# ────────────────────────────────────────────────────
# App setup
# ────────────────────────────────────────────────────

app = FastAPI(
    title="CoinEasy Content Engine",
    description="Multi-tenant Korean content generation for Web3 clients",
    version="1.0.0",
)

API_SECRET = os.environ.get("API_SECRET", "")
OUTPUT_ROOT = Path(os.environ.get("OUTPUT_ROOT", "/tmp/content_engine_output")).resolve()


def _finish_output_run(
    output_dir: Path,
    background_tasks: BackgroundTasks,
) -> None:
    """Release one run and enforce retention after its response is sent."""
    clear_run_active(OUTPUT_ROOT, output_dir)
    background_tasks.add_task(
        cleanup_generated_runs_best_effort,
        OUTPUT_ROOT,
        preserve=(output_dir,),
    )


def _check_auth(x_api_key: str):
    return check_any_valid_key(x_api_key)


def _check_client_auth(x_api_key: str, client_id: str) -> str:
    """Allow the admin key or the key assigned to this exact client only."""
    authenticated_client = check_any_valid_key(x_api_key)
    if authenticated_client not in {"admin", client_id}:
        raise HTTPException(status_code=403, detail="client_scope_violation")
    return authenticated_client


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


class StyleReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_item_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    source_url: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{1,19}$",
    )
    text: str = Field(min_length=1, max_length=600)
    published_at: str = Field(min_length=20, max_length=40)

    @field_validator("text")
    @classmethod
    def normalize_reference_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("style reference text is empty")
        return normalized

    @field_validator("published_at")
    @classmethod
    def require_aware_published_at(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("style reference published_at is invalid") from exc
        if parsed.tzinfo is None:
            raise ValueError("style reference published_at must include a timezone")
        return value


class NewsCardRequest(BaseModel):
    source_content: str
    source_type: str = "tweet"  # "tweet" | "blog" | "article"
    source_url: str = ""
    source_image_url: str = ""  # X media URL; validated against pbs.twimg.com before download
    mock_mode: bool = False  # for smoke testing
    template_style: Literal["remix", "classic", "editorial", "signal"] = "classic"
    style_references: list[StyleReferenceRequest] = Field(
        default_factory=list,
        max_length=3,
    )
    style_reference_pack_hash: str = Field(
        default="",
        pattern=r"^(?:|[a-f0-9]{32})$",
    )

    @model_validator(mode="after")
    def require_complete_style_reference_pack(self):
        if self.style_references and not self.style_reference_pack_hash:
            raise ValueError("style reference pack hash is required")
        return self


class NewsCardResponse(BaseModel):
    client_id: str
    content_type: str
    spec: dict          # localized card copy + source_logo_visible placement signal
    png_path: str       # single 1080×1080 card, not a list
    template_style: str
    requested_template_style: str
    source_image_used: bool
    source_visual_path: Optional[str] = None
    figma_template: Optional[dict] = None
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


class ArticleRequest(BaseModel):
    source_content: str = Field(min_length=1, max_length=60_000)
    source_type: Literal["tweet", "blog", "article"] = "article"
    source_url: str = Field(default="", max_length=2_048)
    style_references: list[StyleReferenceRequest] = Field(
        default_factory=list,
        max_length=3,
    )
    style_reference_pack_hash: str = Field(
        default="",
        pattern=r"^(?:|[a-f0-9]{32})$",
    )

    @model_validator(mode="after")
    def require_complete_style_reference_pack(self):
        if self.style_references and not self.style_reference_pack_hash:
            raise ValueError("style reference pack hash is required")
        return self

    @field_validator("source_content")
    @classmethod
    def require_meaningful_source(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 300:
            raise ValueError(
                "아티클 초안을 만들려면 사실 근거가 충분한 원문을 300자 이상 입력해주세요."
            )
        return normalized


class ArticleSectionResponse(BaseModel):
    id: str
    heading: str
    body: str


class ArticleSourceMapResponse(BaseModel):
    source_url: str
    applies_to: list[str]


class ArticleChannelCopyResponse(BaseModel):
    telegram: str
    x: str


class ArticleVisualResponse(BaseModel):
    id: str
    after_section_id: str
    role: Literal["overview", "explainer"]
    motif: Literal["network", "layers", "flow", "signal", "event", "asset"]
    eyebrow: str
    headline: str
    caption: str
    points: list[str]


class ArticleResponse(BaseModel):
    client_id: str
    content_type: Literal["article"]
    title: str
    lead: str
    sections: list[ArticleSectionResponse]
    key_takeaways: list[str]
    visuals: list[ArticleVisualResponse]
    source_map: list[ArticleSourceMapResponse]
    channel_copy: ArticleChannelCopyResponse
    markdown: str
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
    authenticated_client = _check_auth(x_api_key)
    results = []
    for client_id in list_available_clients():
        if authenticated_client != "admin" and client_id != authenticated_client:
            continue
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
                    "article": True,
                },
            ))
        except Exception as e:
            print(f"⚠ Failed to load client '{client_id}': {e}")
    return results


@app.post("/clients/{client_id}/generate/edu-carousel", response_model=GenerateResponse)
async def generate_carousel(
    client_id: str,
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str = Header(default=""),
):
    """Generate an education carousel for a client."""
    _check_client_auth(x_api_key, client_id)

    # Verify client exists
    try:
        load_client_config(client_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Client '{client_id}' not found")

    # Nanosecond-resolution directory names prevent same-client tutorial requests
    # from overwriting each other's lesson files before Netlify downloads them.
    ts = time.time_ns()
    output_dir = OUTPUT_ROOT / client_id / f"edu_{ts}"
    mark_run_active(OUTPUT_ROOT, output_dir)

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
    finally:
        _finish_output_run(output_dir, background_tasks)

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
    background_tasks: BackgroundTasks,
    x_api_key: str = Header(default=""),
):
    """Generate a single 1080×1080 news card for a client."""
    authenticated_client = _check_client_auth(x_api_key, client_id)
    if (
        (req.style_references or req.style_reference_pack_hash)
        and authenticated_client != "admin"
    ):
        raise HTTPException(403, "style_references_require_admin_key")

    try:
        load_client_config(client_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Client '{client_id}' not found")

    # Nanosecond-resolution directory names keep simultaneous card requests
    # from overwriting each other's cleaned source visual before Netlify fetches it.
    ts = time.time_ns()
    output_dir = OUTPUT_ROOT / client_id / f"news_{ts}"
    mark_run_active(OUTPUT_ROOT, output_dir)

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
            style_references=[
                reference.model_dump()
                for reference in req.style_references
            ],
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Generation failed: {type(e).__name__}: {e}")
    finally:
        _finish_output_run(output_dir, background_tasks)

    return NewsCardResponse(
        client_id=result.client_id,
        content_type=result.content_type,
        spec=result.spec,
        png_path=result.png_path,
        template_style=result.template_style,
        requested_template_style=result.requested_template_style,
        source_image_used=result.source_image_used,
        source_visual_path=result.source_visual_path,
        figma_template=result.figma_template,
        manifest_path=result.manifest_path,
        duration_ms=result.duration_ms,
    )


@app.post("/clients/{client_id}/generate/article", response_model=ArticleResponse)
async def generate_article(
    client_id: str,
    req: ArticleRequest,
    x_api_key: str = Header(default=""),
):
    """Generate a source-locked Korean article from pasted source text."""
    authenticated_client = _check_client_auth(x_api_key, client_id)
    if (
        (req.style_references or req.style_reference_pack_hash)
        and authenticated_client != "admin"
    ):
        raise HTTPException(403, "style_references_require_admin_key")

    try:
        load_client_config(client_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Client '{client_id}' not found")

    started = time.monotonic()
    try:
        article = await asyncio.to_thread(
            generate_article_spec,
            client_id=client_id,
            source_content=req.source_content,
            source_type=req.source_type,
            source_url=req.source_url,
            style_references=[
                reference.model_dump()
                for reference in req.style_references
            ],
        )
    except ArticleInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ArticleOutputError as exc:
        traceback.print_exc()
        raise HTTPException(502, f"Article generation returned invalid output: {exc}") from exc
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            500,
            f"Article generation failed: {type(exc).__name__}: {exc}",
        ) from exc

    return ArticleResponse(
        client_id=client_id,
        content_type="article",
        **article,
        duration_ms=int((time.monotonic() - started) * 1000),
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
    _check_client_auth(x_api_key, client_id)
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
    _check_client_auth(x_api_key, client_id)

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
    await asyncio.to_thread(cleanup_generated_runs_best_effort, OUTPUT_ROOT)
    clients = list_active_clients()
    print(f"✓ Content Engine started with {len(clients)} active clients:")
    for cfg in clients:
        print(f"  - {cfg.client_id:12s} {cfg.name}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
