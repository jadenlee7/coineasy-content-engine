"""
CoinEasy Content Engine · FastAPI Server

Single service serving multiple clients (Yellow, Squid, etc.) via:
    POST /clients/{client_id}/generate

Authentication: X-API-Key header
Deployment: Railway
"""
import asyncio
import os
import re
import secrets
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import parse_qs, urlparse

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Header, Request
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
from core.publishers.telegram_review import (
    build_telegram_grok_qa_message,
    decode_review_image_data_url,
    send_telegram_grok_qa_verdict,
    send_telegram_review,
    telegram_content_ops_relay_config,
    telegram_review_config,
)
from core.publishers.typefully import TypefullyPublisher
from core.publications.repository import PublicationRepositoryError
from core.publications.settings import (
    PublicationSettings,
    publication_worker_token,
    telegram_publication_enabled,
)
from core.publications.worker import build_exact_telegram_publication_worker
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

class BrandReviewExampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_item_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    content_version_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    content_kind: Literal["daily_news", "article", "tutorial"]
    text: str = Field(min_length=1, max_length=1200)
    approved_at: str = Field(min_length=20, max_length=40)

    @field_validator("text")
    @classmethod
    def normalize_approved_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("approved brand review example is empty")
        return normalized

    @field_validator("approved_at")
    @classmethod
    def require_aware_approved_at(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("approved_at is invalid") from exc
        if parsed.tzinfo is None:
            raise ValueError("approved_at must include a timezone")
        return value


class BrandReviewReasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "off_brand_tone",
        "unsupported_claim",
        "awkward_korean",
        "visual_brand_mismatch",
        "duplicate_logo",
        "source_fidelity",
        "channel_fit",
        "other",
    ]
    count: int = Field(ge=1, le=1_000_000)


class BrandReviewGuidanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: Literal["brand-review-learning@1"]
    approved_examples: list[BrandReviewExampleRequest] = Field(
        default_factory=list,
        max_length=3,
    )
    avoid_reason_codes: list[BrandReviewReasonRequest] = Field(
        default_factory=list,
        max_length=5,
    )

    @model_validator(mode="after")
    def require_unique_review_guidance(self):
        item_ids = [item.content_item_id.lower() for item in self.approved_examples]
        reason_codes = [item.code for item in self.avoid_reason_codes]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("approved brand review examples must be unique")
        if len(reason_codes) != len(set(reason_codes)):
            raise ValueError("brand review reason codes must be unique")
        return self


def _require_brand_review_kind(
    guidance: Optional[BrandReviewGuidanceRequest],
    expected_kind: Literal["daily_news", "article", "tutorial"],
) -> None:
    if guidance is not None and any(
        example.content_kind != expected_kind
        for example in guidance.approved_examples
    ):
        raise HTTPException(422, "brand_review_guidance_kind_mismatch")


class GenerateRequest(BaseModel):
    source_content: str
    source_type: str = "tweet"  # "tweet" | "blog" | "article"
    source_url: str = ""
    series_number: Optional[str] = None
    mock_llm: bool = False  # for smoke testing
    brand_review_guidance: Optional[BrandReviewGuidanceRequest] = None


class GenerateResponse(BaseModel):
    client_id: str
    content_type: str
    series: dict
    lessons: list[dict]
    lesson_count: int
    png_paths: list[str]
    manifest_path: str
    duration_ms: int


def _public_tutorial_claim_value(value: Any) -> Any:
    """Remove renderer-only SVG/private fields from the reviewable lesson spec."""
    if isinstance(value, dict):
        return {
            key: _public_tutorial_claim_value(item)
            for key, item in value.items()
            if isinstance(key, str)
            and not key.startswith("_")
            and not key.endswith("_svg")
        }
    if isinstance(value, list):
        return [_public_tutorial_claim_value(item) for item in value]
    return value


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
    brand_review_guidance: Optional[BrandReviewGuidanceRequest] = None

    @model_validator(mode="after")
    def require_complete_style_reference_pack(self):
        if self.style_references and not self.style_reference_pack_hash:
            raise ValueError("style reference pack hash is required")
        return self


class NewsCardResponse(BaseModel):
    client_id: str
    content_type: str
    spec: dict          # localized card copy + source_logo_visible placement signal
    png_path: str       # one card; Squid official remix keeps source aspect ratio
    template_style: str
    requested_template_style: str
    source_image_used: bool
    source_image_url: str
    source_image_sha256: str = Field(pattern=r"^(?:|[a-f0-9]{64})$")
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
    brand_review_guidance: Optional[BrandReviewGuidanceRequest] = None

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


_TELEGRAM_HTML_TAG_PATTERN = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)[^>]*>")


class TelegramReviewNotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_item_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    content_version_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    client_id: Literal["yellow", "origintrail", "squid", "babylon"]
    content_kind: Literal["daily_news", "article", "tutorial"]
    caption_html: str = Field(min_length=1, max_length=1_024)
    message_html: str = Field(min_length=1, max_length=4_096)
    review_url: str = Field(min_length=20, max_length=500)
    image_url: str = Field(default="", max_length=2_500)
    image_data_url: str = Field(default="", max_length=4_100_000)

    @field_validator("caption_html", "message_html")
    @classmethod
    def require_safe_telegram_html(cls, value: str) -> str:
        normalized = value.strip()
        if any(
            tag.lower() not in {"b", "i"}
            for tag in _TELEGRAM_HTML_TAG_PATTERN.findall(normalized)
        ):
            raise ValueError("telegram_review_html_tag_not_allowed")
        return normalized

    @model_validator(mode="after")
    def validate_review_targets(self):
        review_url = urlparse(self.review_url)
        review_query = parse_qs(review_url.query, keep_blank_values=True)
        if (
            review_url.scheme != "https"
            or review_url.hostname != "coineasy-newscard.netlify.app"
            or review_url.port is not None
            or review_url.username is not None
            or review_url.password is not None
            or review_url.path != "/"
            or review_url.fragment
            or set(review_query) != {"view", "content"}
            or review_query.get("view") != ["library"]
            or review_query.get("content") != [self.content_item_id.lower()]
        ):
            raise ValueError("telegram_review_url_not_allowed")
        if self.image_url and self.image_data_url:
            raise ValueError("telegram_review_image_is_ambiguous")
        if self.image_url:
            image_url = urlparse(self.image_url)
            if (
                image_url.scheme != "https"
                or not image_url.hostname
                or not image_url.hostname.endswith(".supabase.co")
                or not image_url.path.startswith(
                    "/storage/v1/object/sign/content-studio/"
                )
                or image_url.username is not None
                or image_url.password is not None
                or image_url.fragment
            ):
                raise ValueError("telegram_review_image_url_not_allowed")
        if (
            self.image_data_url
            and decode_review_image_data_url(self.image_data_url) is None
        ):
            raise ValueError("telegram_review_image_data_invalid")
        return self


class GrokQaCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PASS", "WARN", "BLOCK"]
    checks: list[str] = Field(min_length=1, max_length=6)

    @field_validator("checks")
    @classmethod
    def validate_checks(cls, values: list[str]) -> list[str]:
        if any(not 3 <= len(value.strip()) <= 300 for value in values):
            raise ValueError("grok_qa_check_invalid")
        return [value.strip() for value in values]


class GrokQaFactCheck(GrokQaCheck):
    source_urls: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("grok_qa_source_duplicate")
        for value in values:
            parsed = urlparse(value)
            if (
                len(value) > 2_048
                or parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError("grok_qa_source_url_invalid")
        return values


class GrokQaIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["WARN", "BLOCK"]
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,47}$")
    message: str = Field(min_length=3, max_length=500)
    evidence_url: str | None = Field(default=None, max_length=2_048)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("grok_qa_issue_message_invalid")
        return normalized

    @field_validator("evidence_url")
    @classmethod
    def validate_evidence_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("grok_qa_evidence_url_invalid")
        return value


class GrokQaVerdictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_item_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    content_version_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    client_id: Literal["yellow", "origintrail", "squid", "babylon"]
    content_kind: Literal["daily_news", "article", "tutorial"]
    title: str = Field(min_length=1, max_length=200)
    decision: Literal["PASS", "WARN", "BLOCK"]
    summary: str = Field(min_length=10, max_length=800)
    fact_check: GrokQaFactCheck
    brand_check: GrokQaCheck
    issues: list[GrokQaIssue] = Field(default_factory=list, max_length=3)
    next_action: Literal[
        "ready_for_human_approval",
        "human_review",
        "verify_source",
        "revise_copy",
        "revise_banner",
    ]
    review_url: str = Field(min_length=20, max_length=500)

    @field_validator("title", "summary")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_verdict(self):
        review_url = urlparse(self.review_url)
        review_query = parse_qs(review_url.query, keep_blank_values=True)
        if (
            review_url.scheme != "https"
            or review_url.hostname != "coineasy-newscard.netlify.app"
            or review_url.port is not None
            or review_url.username is not None
            or review_url.password is not None
            or review_url.path != "/"
            or review_url.fragment
            or set(review_query) != {"view", "content"}
            or review_query.get("view") != ["library"]
            or review_query.get("content") != [self.content_item_id.lower()]
        ):
            raise ValueError("grok_qa_review_url_not_allowed")
        if self.decision == "PASS" and (
            self.fact_check.status != "PASS"
            or self.brand_check.status != "PASS"
            or self.issues
            or not self.fact_check.source_urls
            or self.next_action != "ready_for_human_approval"
        ):
            raise ValueError("grok_qa_pass_evidence_incomplete")
        if (
            self.decision != "PASS"
            and self.next_action == "ready_for_human_approval"
        ):
            raise ValueError("grok_qa_next_action_invalid")
        if self.decision == "BLOCK" and (
            self.fact_check.status != "BLOCK"
            and self.brand_check.status != "BLOCK"
            and not any(issue.severity == "BLOCK" for issue in self.issues)
        ):
            raise ValueError("grok_qa_block_evidence_incomplete")
        source_urls = set(self.fact_check.source_urls)
        if any(
            issue.evidence_url is not None
            and issue.evidence_url not in source_urls
            for issue in self.issues
        ):
            raise ValueError("grok_qa_issue_source_mismatch")
        return self


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


@app.post("/review-notifications/telegram")
async def notify_telegram_review(
    req: TelegramReviewNotificationRequest,
    x_api_key: str = Header(default=""),
):
    if _check_auth(x_api_key) != "admin":
        raise HTTPException(403, "telegram_review_requires_admin_key")
    config = telegram_review_config()
    if config is None:
        raise HTTPException(503, "telegram_review_not_configured")
    collaboration_config = telegram_content_ops_relay_config(config)
    result = await send_telegram_review(
        config=config,
        collaboration_config=collaboration_config,
        caption_html=req.caption_html,
        message_html=req.message_html,
        review_url=req.review_url,
        image_url=req.image_url,
        image_data_url=req.image_data_url,
    )
    if not result["text_sent"]:
        raise HTTPException(502, "telegram_review_send_failed")
    return result


@app.post("/internal/grok-qa-verdict")
async def notify_grok_qa_verdict(
    req: GrokQaVerdictRequest,
    x_api_key: str = Header(default=""),
):
    if _check_auth(x_api_key) != "admin":
        raise HTTPException(403, "grok_qa_verdict_requires_admin_key")
    config = telegram_content_ops_relay_config(telegram_review_config())
    if config is None:
        raise HTTPException(503, "telegram_content_ops_relay_not_configured")
    message = build_telegram_grok_qa_message(
        client_id=req.client_id,
        content_kind=req.content_kind,
        title=req.title,
        content_item_id=req.content_item_id.lower(),
        content_version_id=req.content_version_id.lower(),
        decision=req.decision,
        summary=req.summary,
        fact_status=req.fact_check.status,
        fact_checks=req.fact_check.checks,
        source_urls=req.fact_check.source_urls,
        brand_status=req.brand_check.status,
        brand_checks=req.brand_check.checks,
        issues=[issue.model_dump(exclude_none=True) for issue in req.issues],
        next_action=req.next_action,
    )
    sent = await send_telegram_grok_qa_verdict(
        config=config,
        message_html=message,
        review_url=req.review_url,
    )
    if not sent:
        raise HTTPException(502, "grok_qa_private_relay_failed")
    return {
        "sent": True,
        "advisory_only": True,
        "public_publish": False,
    }


@app.post("/internal/publications/telegram/run-once")
async def run_exact_telegram_publication_once(
    request: Request,
    x_publication_worker_key: str = Header(
        default="",
        alias="X-Publication-Worker-Key",
    ),
):
    """Claim one due, immutable Telegram publication job.

    The caller cannot select content, a version, a destination, or an asset.
    Those values are pinned by the approved Supabase queue transaction.
    """
    try:
        expected_token = publication_worker_token()
    except ValueError:
        raise HTTPException(503, "telegram_publication_worker_not_configured")
    if not secrets.compare_digest(
        x_publication_worker_key.encode("utf-8"),
        expected_token.encode("ascii"),
    ):
        raise HTTPException(401, "invalid_publication_worker_key")

    declared_length = request.headers.get("content-length")
    if declared_length not in {None, "0"}:
        raise HTTPException(400, "publication_worker_request_body_not_allowed")
    async for chunk in request.stream():
        if chunk:
            raise HTTPException(400, "publication_worker_request_body_not_allowed")

    try:
        if not telegram_publication_enabled():
            raise HTTPException(503, "telegram_publication_worker_disabled")
        settings = PublicationSettings.from_env()
        result = await build_exact_telegram_publication_worker(settings).run_once()
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(503, "telegram_publication_worker_not_configured")
    except PublicationRepositoryError:
        raise HTTPException(503, "telegram_publication_queue_unavailable")
    return result.as_dict()


@app.post("/clients/{client_id}/generate/edu-carousel", response_model=GenerateResponse)
async def generate_carousel(
    client_id: str,
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str = Header(default=""),
):
    """Generate an education carousel for a client."""
    authenticated_client = _check_client_auth(x_api_key, client_id)
    if req.brand_review_guidance is not None and authenticated_client != "admin":
        raise HTTPException(403, "brand_review_guidance_requires_admin_key")
    _require_brand_review_kind(req.brand_review_guidance, "tutorial")

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
            brand_review_guidance=(
                req.brand_review_guidance.model_dump()
                if req.brand_review_guidance is not None
                else None
            ),
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
        lessons=[
            _public_tutorial_claim_value({
                "lesson_number": lesson.get("lesson_number"),
                "layout": lesson.get("layout"),
                "theme": lesson.get("theme", result.series_meta.get("theme")),
                "slots": lesson.get("slots", {}),
            })
            for lesson in result.lessons_data
        ],
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
    """Generate one news image; official Squid remixes keep source aspect."""
    authenticated_client = _check_client_auth(x_api_key, client_id)
    if (
        (req.style_references or req.style_reference_pack_hash)
        and authenticated_client != "admin"
    ):
        raise HTTPException(403, "style_references_require_admin_key")
    if req.brand_review_guidance is not None and authenticated_client != "admin":
        raise HTTPException(403, "brand_review_guidance_requires_admin_key")
    _require_brand_review_kind(req.brand_review_guidance, "daily_news")

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
            brand_review_guidance=(
                req.brand_review_guidance.model_dump()
                if req.brand_review_guidance is not None
                else None
            ),
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
        source_image_url=result.source_image_url,
        source_image_sha256=result.source_image_sha256,
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
    if req.brand_review_guidance is not None and authenticated_client != "admin":
        raise HTTPException(403, "brand_review_guidance_requires_admin_key")
    _require_brand_review_kind(req.brand_review_guidance, "article")

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
            brand_review_guidance=(
                req.brand_review_guidance.model_dump()
                if req.brand_review_guidance is not None
                else None
            ),
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

    # Live delivery must originate from an immutable Studio version carrying a
    # current double-fact-check approval. This legacy endpoint generates and
    # publishes in one request, so it cannot prove either invariant. Keep its
    # deterministic dry-run preview for compatibility, but fail closed before
    # generation for every live client/channel request.
    if not req.dry_run:
        raise HTTPException(409, "studio_double_fact_check_publication_required")

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
