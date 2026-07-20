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
import json
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.client_config import get_client_config
from core.llm.edu_carousel_pipeline import generate_carousel_spec
from core.llm.news_card_pipeline import generate_news_card_spec
from core.renderers.playwright_renderer import render_png, EDU_CAROUSEL_SIZE, NEWS_CARD_1x1
from core.sources.source_image import SourceImageError, fetch_source_image
from core.sources.source_text_cleanup import SourceTextCleanupError, clean_source_text


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

# OpenCV cleanup is CPU-bound. A dedicated single-worker executor keeps it off
# the event loop, bounds Railway CPU/memory, and stays safe across test/worker
# event loops (unlike a module-global asyncio.Semaphore).
_SOURCE_TEXT_CLEANUP_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="source-text-cleanup",
)


def _decode_base64_strict(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


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
    source_visual_path: Optional[str]
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
) -> NewsCardResult:
    """
    End-to-end async: source → news_card spec → single 1080x1080 PNG + manifest.
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
    source_image = None
    if requested_template_style == "remix":
        if source_image_url:
            try:
                source_image = await fetch_source_image(source_image_url)
                print(
                    f"[{client_id}] Source visual ready: "
                    f"{source_image.width}x{source_image.height}"
                )
            except SourceImageError as exc:
                print(f"[{client_id}] ⚠ Source visual unavailable, falling back to classic: {exc}")
                actual_template_style = "classic"
        else:
            print(f"[{client_id}] ⚠ Remix requested without an image, falling back to classic")
            actual_template_style = "classic"

    template_path = NEWS_CARD_TEMPLATES[actual_template_style]

    if output_dir is None:
        ts = start.strftime("%Y%m%d_%H%M%S")
        output_dir = Path(f"output/{client_id}/news_{ts}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: LLM ──────────────────────────────
    print(f"[{client_id}] Stage 1/2: LLM news card spec")
    try:
        spec = generate_news_card_spec(
            client_id=client_id,
            source_content=source_content,
            source_type=source_type,
            source_url=source_url,
            mock_mode=mock_mode,
            mock_response=mock_response,
            source_image=source_image,
        )
    except Exception as e:
        print(f"[{client_id}] ✗ LLM stage failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise

    print(f"  → label: {spec['label']} · theme: {spec['theme']}")

    # Renderer/editable SVG need the prepared image aspect ratio so LLM-provided
    # translation regions stay image-relative instead of drifting into letterbox space.
    if source_image is not None:
        spec["source_image_width"] = source_image.width
        spec["source_image_height"] = source_image.height

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
        candidate_path = output_dir / "source_visual_cleaned.jpg"
        temporary_path = output_dir / (
            f".source_visual_cleaned.{uuid.uuid4().hex}.tmp"
        )
        try:
            loop = asyncio.get_running_loop()
            cleanup = await loop.run_in_executor(
                _SOURCE_TEXT_CLEANUP_EXECUTOR,
                clean_source_text,
                source_image,
                spec["translation_regions"],
            )
            cleaned_bytes = await asyncio.to_thread(
                _decode_base64_strict,
                cleanup.image.base64_data,
            )
            await asyncio.to_thread(temporary_path.write_bytes, cleaned_bytes)
            await asyncio.to_thread(temporary_path.replace, candidate_path)
            # Commit renderer/result state only after the cleaned asset exists.
            render_source_image = cleanup.image
            source_visual_path = candidate_path
            print(
                f"[{client_id}] Source lettering removed: "
                f"{cleanup.masked_pixels} pixels"
            )
        except SourceTextCleanupError as exc:
            render_source_image = source_image
            source_visual_path = None
            await _unlink_best_effort(temporary_path)
            print(f"[{client_id}] ⚠ Source lettering cleanup failed safely: {exc}")
            spec["source_text_visible"] = False
            spec["translation_regions"] = []
            spec["visual_localization_status"] = "cleanup_failed"
        except Exception as exc:
            render_source_image = source_image
            source_visual_path = None
            await _unlink_best_effort(temporary_path)
            print(
                f"[{client_id}] ⚠ Source lettering cleanup failed safely: "
                f"{type(exc).__name__}"
            )
            spec["source_text_visible"] = False
            spec["translation_regions"] = []
            spec["visual_localization_status"] = "cleanup_failed"

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
            viewport=NEWS_CARD_1x1,
        )
    except Exception as e:
        print(f"  ✗ Render failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise

    print(f"  ✓ {output_png.name}")

    # ── Manifest ──────────────────────────────────
    manifest = {
        "client_id": client_id,
        "content_type": "news_card",
        "model_used": model_used,
        "generated_at": start.isoformat(),
        "source_type": source_type,
        "source_url": source_url,
        "source_image_url": source_image_url,
        "source_image_used": source_image is not None,
        "source_visual_path": str(source_visual_path) if source_visual_path else None,
        "source_content_preview": source_content[:200],
        "spec": spec,
        "requested_template_style": requested_template_style,
        "template_style": actual_template_style,
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
        source_visual_path=str(source_visual_path) if source_visual_path else None,
        manifest_path=str(manifest_path),
        duration_ms=duration,
    )
