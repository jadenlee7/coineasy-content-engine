"""
Multi-Tenant Orchestrator (ASYNC)

Coordinates the full content generation pipeline for any client:
    source → LLM spec → rendered PNGs → manifest

USAGE:
    from core.orchestrator import generate_edu_carousel

    result = await generate_edu_carousel(
        client_id="yellow",
        source_content="Yellow is chain-agnostic...",
        source_type="tweet",
        source_url="https://x.com/Yellow/status/xxx",
        output_dir=Path("./output/yellow_20260421"),
    )
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import math
import re
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence

from core.client_config import get_client_config
from core.figma_template_registry import approved_figma_template
from core.llm.edu_carousel_pipeline import generate_carousel_spec
from core.llm.news_card_pipeline import (
    VISUAL_PLACEMENT_AUDIT_MODEL,
    _minimum_squid_font_percent,
    _text_width_units,
    generate_news_card_spec,
)
from core.renderers.playwright_renderer import (
    EDU_CAROUSEL_SIZE,
    NEWS_CARD_1x1,
    TranslationLayoutError,
    render_png,
)
from core.sources.source_image import (
    SourceImageError,
    fetch_source_image,
    validate_source_image_url,
)
from core.sources.source_text_cleanup import (
    SourceTextCleanupError,
    clean_source_text,
)
from core.squid_visual_style import classify_squid_visual_style
from core.sources.visual_localization_cache import (
    discard_visual_localization,
    get_visual_localization,
    make_visual_localization_cache_key,
    put_visual_localization,
)


# ────────────────────────────────────────────────────
# Layout → Template file mapping
# ────────────────────────────────────────────────────

LAYOUT_TEMPLATES = {
    "P1_3CARD": "edu_p1_3card.html",
    "P2_BULLETS": "edu_p2_bullets.html",
    "P3_BEFORE_AFTER": "edu_p3_before_after.html",
    "P4_SUMMARY": "edu_p4_summary.html",
    "P5_COVER": "edu_p5_cover.html",
    "P6_STEP": "edu_p6_step.html",
    "P7_DEFINITION": "edu_p7_definition.html",
    "P8_DIAGRAM": "edu_p8_diagram.html",
}

NEWS_CARD_TEMPLATES = {
    "remix": "news/news_remix_card.html",
    "classic": "news/news_title_card.html",
    "editorial": "news/news_editorial_card.html",
    "signal": "news/news_signal_card.html",
}

_SQUID_BRAND_TOKENS_VERSION = "squid-brand-tokens@1"
_SQUID_GENERATED_TEMPLATE_VERSION = "squid-generated-gtm@2"
_SQUID_SOURCE_TEMPLATE_VERSION = "squid-source-remix@1"
_SQUID_GENERATED_ASSET_PACK_VERSION = "squid-local-approved@1"
_SQUID_SOURCE_ASSET_PACK_VERSION = "official-source-media@1"

# OpenCV cleanup is CPU-bound. A dedicated single-worker executor keeps it off
# the event loop, bounds Railway CPU/memory, and stays safe across test/worker
# event loops (unlike a module-global asyncio.Semaphore).
_SOURCE_TEXT_CLEANUP_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="source-text-cleanup",
)

_VISUAL_LOCALIZATION_PRIVATE_KEYS = frozenset({
    "_source_index",
    "_source_line_count",
    "_protected_regions",
})


def _decode_base64_strict(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def _align_regions_to_detected_text(
    regions: list[dict],
    detected_regions: object,
    image_width: int,
    image_height: int,
) -> list[dict]:
    """Center Korean copy on the glyph mask that was actually removed."""
    if not detected_regions:
        return regions
    if (
        not isinstance(detected_regions, (list, tuple))
        or len(detected_regions) != len(regions)
    ):
        raise SourceTextCleanupError("detected source-text geometry is incomplete")

    aligned: list[dict] = []
    occupied: list[tuple[float, float, float, float]] = []
    for region, detected in zip(regions, detected_regions):
        if not isinstance(region, dict) or not isinstance(detected, dict):
            raise SourceTextCleanupError("detected source-text geometry is invalid")
        try:
            detected_x = float(detected["x"])
            detected_y = float(detected["y"])
            detected_width = float(detected["width"])
            detected_height = float(detected["height"])
            original_width = float(region["width"])
            original_height = float(region["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceTextCleanupError(
                "detected source-text geometry is invalid"
            ) from exc
        values = (
            detected_x,
            detected_y,
            detected_width,
            detected_height,
            original_width,
            original_height,
        )
        if any(not math.isfinite(value) for value in values):
            raise SourceTextCleanupError("detected source-text geometry is invalid")

        # The audited box varies slightly between otherwise identical vision
        # responses. Once raster consensus finds the real glyphs, use only that
        # canonical geometry so placement and Figma output stay repeatable.
        width = max(6.0, detected_width + 4.0 / image_width * 100.0)
        height = max(3.0, detected_height + 2.0 / image_height * 100.0)
        if width <= 0 or height <= 0 or width > 100 or height > 100:
            raise SourceTextCleanupError("detected source-text geometry is outside the image")
        center_x = detected_x + detected_width / 2.0
        center_y = detected_y + detected_height / 2.0
        x = center_x - width / 2.0
        y = center_y - height / 2.0
        if x < 0 or y < 0 or x + width > 100 or y + height > 100:
            raise SourceTextCleanupError(
                "detected source-text geometry cannot stay centered in the image"
            )
        bounds = (x, y, x + width, y + height)
        if any(
            bounds[0] < existing[2]
            and bounds[2] > existing[0]
            and bounds[1] < existing[3]
            and bounds[3] > existing[1]
            for existing in occupied
        ):
            raise SourceTextCleanupError("detected source-text regions overlap")
        occupied.append(bounds)
        text = region.get("text")
        if not isinstance(text, str):
            raise SourceTextCleanupError("translated source text is invalid")
        translation_lines = [line for line in text.splitlines() if line.strip()]
        if not translation_lines or len(translation_lines) > 2:
            raise SourceTextCleanupError("translated source text line count is invalid")
        try:
            font_size = float(region.get("font_size", 5.2))
        except (TypeError, ValueError) as exc:
            raise SourceTextCleanupError("translated source text font size is invalid") from exc
        if not math.isfinite(font_size):
            raise SourceTextCleanupError("translated source text font size is invalid")

        text_units = _text_width_units(text.strip())
        estimated_width = max(0.1, text_units * font_size * 0.60)
        scale_x = round(
            max(0.9, min(1.35, width * 0.96 / estimated_width)),
            2,
        )

        # Re-run the same minimum-font fit guard used by visual localization
        # normalization. Raster consensus can shrink a sampled audit box to the
        # canonical glyph bounds; publishing that narrower box without another
        # fit check would make the renderer hide every Korean layer.
        minimum_css_font_percent = _minimum_squid_font_percent(
            image_width,
            image_height,
        )
        widest_line_units = max(
            (_text_width_units(line) for line in translation_lines),
            default=0.0,
        )
        minimum_rendered_width = (
            widest_line_units * minimum_css_font_percent * 1.05 * scale_x
        )
        source_ratio = image_width / max(1, image_height)
        minimum_rendered_height = (
            len(translation_lines)
            * minimum_css_font_percent
            * 1.02
            * source_ratio
        )
        if (
            minimum_rendered_width > width * 0.98
            or minimum_rendered_height > height * 0.98
        ):
            raise SourceTextCleanupError(
                "translated source text does not fit its detected glyph region"
            )
        aligned.append({
            **region,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "source_x": x,
            "source_y": y,
            "source_width": width,
            "source_height": height,
            "scale_x": scale_x,
        })
    return aligned


def _strip_visual_localization_private_fields(regions: object) -> object:
    """Return renderer-safe regions without internal audit evidence."""
    if not isinstance(regions, list):
        return regions
    public_regions: list[object] = []
    for region in regions:
        if not isinstance(region, dict):
            public_regions.append(region)
            continue
        public_regions.append({
            key: copy.deepcopy(value)
            for key, value in region.items()
            if key not in _VISUAL_LOCALIZATION_PRIVATE_KEYS
        })
    return public_regions


async def _unlink_best_effort(path: Path) -> None:
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except OSError:
        pass


# ────────────────────────────────────────────────────
# Default icons (injected when LLM omits them)
# ────────────────────────────────────────────────────

DEFAULT_ICONS = {
    "card_1": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><circle cx="4" cy="7" r="2"/><circle cx="20" cy="7" r="2"/><circle cx="4" cy="17" r="2"/><circle cx="20" cy="17" r="2"/></svg>',
    "card_2": '<svg viewBox="0 0 24 24"><path d="M4 20h16v1.5H4zM4 10h2v8H4zM10 10h2v8h-2zM18 10h2v8h-2zM14 10h2v8h-2zM3 8h18L12 3z"/></svg>',
    "card_3": '<svg viewBox="0 0 24 24"><path d="M12 16L3 10.5 12 5l9 5.5-9 5.5zm0 2.5L3 13l9 5.5 9-5.5-9 5.5z"/></svg>',
    "bullet_check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12l5 5L20 7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "bullet_arrow": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "bullet_dot": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/></svg>',
    "before_key": '<svg viewBox="0 0 24 24"><circle cx="7" cy="14" r="3.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M10 11l10-10M17 4l2 2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    "after_wallet": '<svg viewBox="0 0 24 24"><rect x="3" y="7" width="18" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="15" cy="13" r="1.2" fill="currentColor"/></svg>',
}


def _inject_default_icons(layout: str, slots: dict):
    """Inject default icons into slots when LLM omits icon_svg fields."""
    if layout == "P1_3CARD":
        cards = slots.get("cards", [])
        icons = [DEFAULT_ICONS["card_1"], DEFAULT_ICONS["card_2"], DEFAULT_ICONS["card_3"]]
        for i, card in enumerate(cards):
            card.setdefault("icon_svg", icons[i % len(icons)])
    elif layout == "P2_BULLETS":
        bullets = slots.get("bullets", [])
        icon_pool = [DEFAULT_ICONS["bullet_check"], DEFAULT_ICONS["bullet_arrow"], DEFAULT_ICONS["bullet_dot"]]
        for i, bullet in enumerate(bullets):
            bullet.setdefault("icon_svg", icon_pool[i % len(icon_pool)])
    elif layout == "P3_BEFORE_AFTER":
        if "before" in slots:
            slots["before"].setdefault("icon_svg", DEFAULT_ICONS["before_key"])
        if "after" in slots:
            slots["after"].setdefault("icon_svg", DEFAULT_ICONS["after_wallet"])
    elif layout == "P4_SUMMARY":
        items = slots.get("summary_items", [])
        icons = [DEFAULT_ICONS["card_3"], DEFAULT_ICONS["bullet_arrow"], DEFAULT_ICONS["bullet_check"]]
        for i, item in enumerate(items):
            item.setdefault("icon_svg", icons[i % len(icons)])


# ────────────────────────────────────────────────────
# Result type
# ────────────────────────────────────────────────────

@dataclass
class CarouselResult:
    client_id: str
    content_type: str  # "edu_carousel"
    series_meta: dict
    png_paths: list[str]
    lessons_data: list[dict]
    manifest_path: str
    duration_ms: int
    llm_cost_usd: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NewsCardResult:
    client_id: str
    content_type: str  # "news_card"
    spec: dict                  # full card copy + source_logo_visible placement signal
    png_path: str               # 1 card → single PNG, not a list
    template_style: str
    requested_template_style: str
    source_image_used: bool
    source_image_url: str
    source_image_sha256: str
    source_visual_path: Optional[str]
    figma_template: Optional[dict]
    manifest_path: str
    duration_ms: int
    llm_cost_usd: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ────────────────────────────────────────────────────
# Defensive normalization (prevents KeyError on malformed LLM output)
# ────────────────────────────────────────────────────

def _sanitize_spec_series(series: dict) -> dict:
    """Ensure series has required fields with fallbacks, tolerating LLM field-name drift."""
    if not isinstance(series, dict):
        series = {}
    return {
        "series_number": str(series.get("series_number") or series.get("number") or "01"),
        "series_title_en": (
            series.get("series_title_en")
            or series.get("title_en")
            or series.get("title")
            or "Untitled"
        ),
        "series_subtitle_kr": (
            series.get("series_subtitle_kr")
            or series.get("subtitle_kr")
            or series.get("title_kr")  # tolerate LLM drift
            or ""
        ),
        "theme": series.get("theme", "dark"),
    }


# ────────────────────────────────────────────────────
# Main Entry Point (ASYNC)
# ────────────────────────────────────────────────────

async def generate_edu_carousel(
    client_id: str,
    source_content: str,
    source_type: str = "tweet",
    source_url: str = "",
    series_number: Optional[str] = None,
    output_dir: Optional[Path] = None,
    mock_llm: bool = False,
    mock_llm_response: Optional[dict] = None,
    brand_review_guidance: Mapping[str, object] | None = None,
) -> CarouselResult:
    """
    End-to-end async: source content → LLM spec → N PNGs + manifest.
    Must be awaited inside an asyncio event loop (e.g. FastAPI async endpoint).
    """
    start = datetime.now(timezone.utc)

    config = get_client_config(client_id)
    model_used = config.llm.edu_carousel.model
    print(f"[{client_id}] Using model: {model_used}")

    if not config.active:
        raise ValueError(f"Client '{client_id}' is marked inactive in config.yaml")
    if not config.feature_flags.education_carousel:
        raise ValueError(f"Client '{client_id}' has edu_carousel disabled in feature_flags")

    # Default output dir
    if output_dir is None:
        ts = start.strftime("%Y%m%d_%H%M%S")
        output_dir = Path(f"output/{client_id}/{ts}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: LLM ──────────────────────────────
    print(f"[{client_id}] Stage 1/2: LLM spec generation")
    try:
        spec = generate_carousel_spec(
            client_id=client_id,
            source_content=source_content,
            source_type=source_type,
            source_url=source_url,
            series_number=series_number,
            mock_mode=mock_llm,
            mock_response=mock_llm_response,
            brand_review_guidance=brand_review_guidance,
        )
    except Exception as e:
        print(f"[{client_id}] ✗ LLM stage failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise

    # Defensive normalization
    spec["series"] = _sanitize_spec_series(spec.get("series", {}))
    if "lessons" not in spec or not isinstance(spec["lessons"], list):
        raise ValueError(f"LLM spec missing 'lessons' array. Got keys: {list(spec.keys())}")

    print(f"  → Series: {spec['series']['series_number']} · {spec['series']['series_title_en']}")
    print(f"  → {len(spec['lessons'])} lessons")

    # ── Stage 2: Rendering ────────────────────────
    print(f"[{client_id}] Stage 2/2: Rendering {len(spec['lessons'])} PNGs")
    png_paths: list[str] = []

    for lesson in spec["lessons"]:
        layout = lesson.get("layout")
        template_file = LAYOUT_TEMPLATES.get(layout)
        if not template_file:
            print(f"  ⚠ Unknown layout {layout}, skipping lesson {lesson.get('lesson_number', '?')}")
            continue

        slots = lesson.setdefault("slots", {})

        # Inject default icons (LLM doesn't produce them)
        _inject_default_icons(layout, slots)

        # Merge series metadata into lesson slots (templates need them)
        merged_slots = {
            "series_number": spec["series"]["series_number"],
            "series_title_en": spec["series"]["series_title_en"],
            "series_subtitle_kr": spec["series"]["series_subtitle_kr"],
            "lesson_number": lesson.get("lesson_number", 0),
            **slots,
        }

        output_png = output_dir / f"lesson_{lesson.get('lesson_number', 0):02d}.png"
        theme = lesson.get("theme", spec["series"]["theme"])

        try:
            await render_png(
                client_id=client_id,
                template_path=f"edu/{template_file}",
                slots=merged_slots,
                output_path=output_png,
                theme=theme,
                viewport=EDU_CAROUSEL_SIZE,
            )
            png_paths.append(str(output_png))
            print(f"  ✓ Lesson {lesson.get('lesson_number')} ({layout}) → {output_png.name}")
        except Exception as e:
            print(f"  ✗ Lesson {lesson.get('lesson_number')} failed: {type(e).__name__}: {e}")
            traceback.print_exc()

    # ── Manifest ──────────────────────────────────
    manifest = {
        "client_id": client_id,
        "content_type": "edu_carousel",
        "model_used": model_used,
        "generated_at": start.isoformat(),
        "source_type": source_type,
        "source_url": source_url,
        "source_content_preview": source_content[:200],
        "spec": spec,
        "png_paths": png_paths,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    print(f"[{client_id}] Complete ({duration}ms, {len(png_paths)} PNGs)")

    return CarouselResult(
        client_id=client_id,
        content_type="edu_carousel",
        series_meta=spec["series"],
        png_paths=png_paths,
        lessons_data=spec["lessons"],
        manifest_path=str(manifest_path),
        duration_ms=duration,
    )


# ────────────────────────────────────────────────────
# News Card Main Entry Point (ASYNC)
# ────────────────────────────────────────────────────

async def generate_news_card(
    client_id: str,
    source_content: str,
    source_type: str = "tweet",
    source_url: str = "",
    output_dir: Optional[Path] = None,
    mock_mode: bool = False,
    mock_response: Optional[dict] = None,
    template_style: str = "classic",
    source_image_url: str = "",
    style_references: Sequence[Mapping[str, str]] = (),
    brand_review_guidance: Mapping[str, object] | None = None,
) -> NewsCardResult:
    """
    End-to-end async: source → news_card spec → one PNG + manifest.

    Generated cards remain 1080x1080. A Squid official-source remix instead
    keeps the source aspect ratio so an X banner is not reduced to a small
    letterboxed image inside a square canvas.
    Must be awaited inside an asyncio event loop (e.g. FastAPI async endpoint).
    """
    start = datetime.now(timezone.utc)

    config = get_client_config(client_id)
    model_used = config.llm.news_card.model
    print(f"[{client_id}] Using model: {model_used}")

    if not config.active:
        raise ValueError(f"Client '{client_id}' is marked inactive in config.yaml")
    if not config.feature_flags.news_card:
        raise ValueError(f"Client '{client_id}' has news_card disabled in feature_flags")

    if template_style not in NEWS_CARD_TEMPLATES:
        allowed = ", ".join(sorted(NEWS_CARD_TEMPLATES))
        raise ValueError(f"Unknown news card template '{template_style}'. Allowed: {allowed}")

    requested_template_style = template_style
    actual_template_style = template_style
    if client_id == "squid" and requested_template_style in {"editorial", "signal"}:
        # Squid's generated family has one reviewed source-free composition.
        # Keep legacy/API selections readable, but never route them into the
        # shared publisher-looking editorial or signal templates.
        actual_template_style = "classic"
    source_image = None
    source_image_canonical_url = ""
    source_image_sha256 = ""
    if requested_template_style == "remix":
        if source_image_url:
            try:
                canonical_image_url = validate_source_image_url(source_image_url)
                source_image = await fetch_source_image(canonical_image_url)
                source_image_canonical_url = canonical_image_url
                source_image_sha256 = source_image.sha256
                print(
                    f"[{client_id}] Source visual ready: "
                    f"{source_image.width}x{source_image.height}"
                )
            except SourceImageError as exc:
                if client_id == "squid":
                    raise SourceImageError(
                        "Squid original-visual localization requires the official source image"
                    ) from exc
                print(f"[{client_id}] ⚠ Source visual unavailable, falling back to classic: {exc}")
                actual_template_style = "classic"
        else:
            if client_id == "squid":
                raise SourceImageError(
                    "Squid original-visual localization requires a source image"
                )
            print(f"[{client_id}] ⚠ Remix requested without an image, falling back to classic")
            actual_template_style = "classic"

    template_path = NEWS_CARD_TEMPLATES[actual_template_style]

    visual_cache_key: Optional[str] = None
    cached_visual_localization: Optional[list[dict]] = None
    if (
        not mock_mode
        and client_id == "squid"
        and actual_template_style == "remix"
        and source_image is not None
    ):
        visual_config_fingerprint = json.dumps(
            {
                "client_name": config.name,
                "locale": config.locale,
                "news_card": asdict(config.llm.news_card),
                "brand_voice": asdict(config.brand_voice),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        visual_cache_key = make_visual_localization_cache_key(
            client_id=client_id,
            template_style=actual_template_style,
            model=model_used,
            audit_model=VISUAL_PLACEMENT_AUDIT_MODEL,
            config_fingerprint=visual_config_fingerprint,
            source_type=source_type,
            source_url=source_url,
            source_content=source_content,
            source_image=source_image,
        )
        cached_visual_localization = get_visual_localization(visual_cache_key)

    if output_dir is None:
        ts = start.strftime("%Y%m%d_%H%M%S")
        output_dir = Path(f"output/{client_id}/news_{ts}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: LLM ──────────────────────────────
    print(f"[{client_id}] Stage 1/2: LLM news card spec")
    try:
        # Anthropic's sync SDK must not block the FastAPI event loop; one slow
        # visual request should not make unrelated clients time out behind it.
        spec = await asyncio.to_thread(
            generate_news_card_spec,
            client_id=client_id,
            source_content=source_content,
            source_type=source_type,
            source_url=source_url,
            mock_mode=mock_mode,
            mock_response=mock_response,
            source_image=source_image,
            cached_visual_localization=cached_visual_localization,
            style_references=style_references,
            brand_review_guidance=brand_review_guidance,
        )
    except Exception as e:
        print(f"[{client_id}] ✗ LLM stage failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise

    if client_id == "squid":
        # The browser and LLM cannot choose a visual family. Bind it to the
        # immutable source and the already-verified media decision so an exact
        # official creative always wins over generated Korean GTM artwork.
        squid_visual = classify_squid_visual_style(
            source_content,
            source_url=source_url,
            has_official_media=(
                actual_template_style == "remix" and source_image is not None
            ),
        )
        if squid_visual.manual_review_required:
            raise ValueError(
                "Squid text-only worldbuilding requires approved official media"
            )
        expected_strategy = (
            "source_remix" if actual_template_style == "remix" else "generated_gtm"
        )
        if squid_visual.channel != expected_strategy:
            raise ValueError("Squid visual routing is inconsistent with the render path")
        spec.update(squid_visual.as_spec_metadata())
        licensed_display_font = config.font_display_file_path
        spec.update({
            "brand_tokens_version": _SQUID_BRAND_TOKENS_VERSION,
            "template_version": (
                _SQUID_SOURCE_TEMPLATE_VERSION
                if actual_template_style == "remix"
                else _SQUID_GENERATED_TEMPLATE_VERSION
            ),
            "asset_pack_version": (
                _SQUID_SOURCE_ASSET_PACK_VERSION
                if actual_template_style == "remix"
                else _SQUID_GENERATED_ASSET_PACK_VERSION
            ),
            "font_status": (
                "bagoss_condensed_licensed"
                if licensed_display_font is not None
                and licensed_display_font.is_file()
                else "pretendard_fallback"
            ),
        })

    print(f"  → label: {spec['label']} · theme: {spec['theme']}")
    visual_cache_hit = spec.pop("_visual_localization_cache_hit", False) is True

    # Renderer/editable SVG need the prepared image aspect ratio so LLM-provided
    # translation regions stay image-relative instead of drifting into letterbox space.
    spec.pop("source_background_color", None)
    spec.pop("output_policy", None)
    spec.pop("output_width", None)
    spec.pop("output_height", None)
    render_viewport = NEWS_CARD_1x1
    render_scale_factor = 2
    if source_image is not None:
        spec["source_image_width"] = source_image.width
        spec["source_image_height"] = source_image.height
        if re.fullmatch(r"#[0-9A-F]{6}", source_image.background_color or ""):
            spec["source_background_color"] = source_image.background_color
        if client_id == "squid" and actual_template_style == "remix":
            # Prepared X media is already bounded. Keep the complete source
            # composition, reducing only oversized assets to a 1200 px long
            # edge to stay comfortably inside the synchronous PNG budget.
            scale = min(1.0, 1200.0 / max(source_image.width, source_image.height))
            render_viewport = (
                max(1, round(source_image.width * scale)),
                max(1, round(source_image.height * scale)),
            )
            spec["output_width"], spec["output_height"] = render_viewport
            spec["output_policy"] = "official_source_native_v1"
            # The prepared source pixels are already the authoritative X
            # creative. Do not upscale them through the default 2x screenshot.
            render_scale_factor = 1

    # Source copy is baked into the raster. For Squid, remove the audited
    # lettering before either renderer sees the image. A cleanup failure is
    # atomic: preserve the official source and hide every Korean overlay.
    render_source_image = source_image
    source_visual_path: Optional[Path] = None
    if (
        client_id == "squid"
        and actual_template_style == "remix"
        and source_image is not None
        and spec.get("source_text_visible") is True
        and isinstance(spec.get("translation_regions"), list)
        and spec["translation_regions"]
    ):
        audited_regions = copy.deepcopy(spec["translation_regions"])
        candidate_path = output_dir / "source_visual_cleaned.jpg"
        temporary_path = output_dir / (
            f".source_visual_cleaned.{uuid.uuid4().hex}.tmp"
        )
        cleanup = None
        aligned_regions = None
        try:
            loop = asyncio.get_running_loop()
            cleanup = await loop.run_in_executor(
                _SOURCE_TEXT_CLEANUP_EXECUTOR,
                clean_source_text,
                source_image,
                spec["translation_regions"],
            )
            if (
                cleanup.image.width != source_image.width
                or cleanup.image.height != source_image.height
            ):
                raise SourceTextCleanupError(
                    "cleaned source image dimensions must match the original"
                )
            aligned_regions = _align_regions_to_detected_text(
                spec["translation_regions"],
                getattr(cleanup, "detected_regions", ()),
                cleanup.image.width,
                cleanup.image.height,
            )
        except asyncio.CancelledError:
            raise
        except SourceTextCleanupError as exc:
            await _unlink_best_effort(temporary_path)
            if visual_cache_hit and visual_cache_key is not None:
                discard_visual_localization(visual_cache_key)
            print(f"[{client_id}] ⚠ Source lettering cleanup failed safely: {exc}")
            spec["source_text_visible"] = False
            spec["translation_regions"] = []
            spec["visual_localization_status"] = "cleanup_failed"
        except Exception as exc:
            # Executor/runtime failures are not evidence that a validated cache
            # entry is bad. Preserve it for the next request and fail this card
            # closed on the untouched source visual.
            await _unlink_best_effort(temporary_path)
            print(
                f"[{client_id}] ⚠ Source lettering cleanup failed safely: "
                f"{type(exc).__name__}"
            )
            spec["source_text_visible"] = False
            spec["translation_regions"] = []
            spec["visual_localization_status"] = "cleanup_failed"
        else:
            try:
                assert cleanup is not None
                assert aligned_regions is not None
                cleaned_bytes = await asyncio.to_thread(
                    _decode_base64_strict,
                    cleanup.image.base64_data,
                )
                await asyncio.to_thread(temporary_path.write_bytes, cleaned_bytes)
                await asyncio.to_thread(temporary_path.replace, candidate_path)
            except asyncio.CancelledError:
                await _unlink_best_effort(temporary_path)
                raise
            except Exception as exc:
                # Base64/file publication failures are transient and must not
                # poison a localization that already passed cleanup and align.
                await _unlink_best_effort(temporary_path)
                print(
                    f"[{client_id}] ⚠ Source lettering asset publish failed safely: "
                    f"{type(exc).__name__}"
                )
                spec["source_text_visible"] = False
                spec["translation_regions"] = []
                spec["visual_localization_status"] = "cleanup_failed"
            else:
                # Commit renderer/result state only after the cleaned asset exists.
                render_source_image = cleanup.image
                source_visual_path = candidate_path
                spec["source_image_width"] = cleanup.image.width
                spec["source_image_height"] = cleanup.image.height
                spec["translation_regions"] = (
                    _strip_visual_localization_private_fields(aligned_regions)
                )
                if visual_cache_key is not None:
                    try:
                        # Cache the validated pre-strip audit evidence. It is
                        # required by deterministic cleanup on the next request,
                        # but must never leak into render/spec/manifest output.
                        put_visual_localization(visual_cache_key, audited_regions)
                    except Exception as exc:
                        print(
                            f"[{client_id}] ⚠ Visual localization cache write skipped: "
                            f"{type(exc).__name__}"
                        )
                print(
                    f"[{client_id}] Source lettering removed: "
                    f"{cleanup.masked_pixels} pixels"
                )

    # Audit evidence is internal even if a future path bypasses cleanup. Keep
    # every public consumer (PNG, editable SVG, manifest, API result) clean.
    spec.pop("_protected_regions", None)
    spec["translation_regions"] = _strip_visual_localization_private_fields(
        spec.get("translation_regions")
    )

    # ── Stage 2: Rendering ────────────────────────
    print(f"[{client_id}] Stage 2/2: Rendering 1 PNG")
    output_png = output_dir / f"news_card_{actual_template_style}.png"
    render_slots = dict(spec)
    if render_source_image is not None:
        render_slots["source_image_data_url"] = render_source_image.data_url
    try:
        await render_png(
            client_id=client_id,
            template_path=template_path,
            slots=render_slots,
            output_path=output_png,
            theme=spec["theme"],
            viewport=render_viewport,
            device_scale_factor=render_scale_factor,
        )
    except TranslationLayoutError as exc:
        if not (
            client_id == "squid"
            and actual_template_style == "remix"
            and source_image is not None
            and source_visual_path is not None
        ):
            raise
        print(
            f"[{client_id}] ⚠ Korean replacement layout rejected safely: {exc}"
        )
        await _unlink_best_effort(source_visual_path)
        source_visual_path = None
        render_source_image = source_image
        spec["source_text_visible"] = False
        spec["translation_regions"] = []
        spec["visual_localization_status"] = "unsafe_placement"
        spec["source_image_width"] = source_image.width
        spec["source_image_height"] = source_image.height
        if visual_cache_key is not None:
            discard_visual_localization(visual_cache_key)
        fallback_slots = dict(spec)
        fallback_slots["source_image_data_url"] = source_image.data_url
        await render_png(
            client_id=client_id,
            template_path=template_path,
            slots=fallback_slots,
            output_path=output_png,
            theme=spec["theme"],
            viewport=render_viewport,
            device_scale_factor=render_scale_factor,
        )
    except Exception as e:
        print(f"  ✗ Render failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise

    print(f"  ✓ {output_png.name}")

    # ── Manifest ──────────────────────────────────
    # Figma is an explicit approval registry, not a "newest frame wins" source.
    # Only an approved [KEEP] frame whose mapped renderer style was actually
    # used is attached to the immutable generation result.
    figma_template = (
        None
        if client_id == "squid" and "creative_family" in spec
        else approved_figma_template(
            client_id,
            content_kind="daily_news",
            template_style=actual_template_style,
        )
    )
    manifest = {
        "client_id": client_id,
        "content_type": "news_card",
        "model_used": model_used,
        "generated_at": start.isoformat(),
        "source_type": source_type,
        "source_url": source_url,
        "source_image_url": source_image_canonical_url,
        "source_image_sha256": source_image_sha256,
        "source_image_used": source_image is not None,
        "source_visual_path": str(source_visual_path) if source_visual_path else None,
        "source_content_preview": source_content[:200],
        "visual_placement_audit_model": VISUAL_PLACEMENT_AUDIT_MODEL,
        "visual_localization_cache_hit": visual_cache_hit,
        "spec": spec,
        "requested_template_style": requested_template_style,
        "template_style": actual_template_style,
        "figma_template": figma_template,
        "png_path": str(output_png),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    print(f"[{client_id}] Complete ({duration}ms)")

    return NewsCardResult(
        client_id=client_id,
        content_type="news_card",
        spec=spec,
        png_path=str(output_png),
        template_style=actual_template_style,
        requested_template_style=requested_template_style,
        source_image_used=source_image is not None,
        source_image_url=source_image_canonical_url,
        source_image_sha256=source_image_sha256,
        source_visual_path=str(source_visual_path) if source_visual_path else None,
        figma_template=figma_template,
        manifest_path=str(manifest_path),
        duration_ms=duration,
    )
