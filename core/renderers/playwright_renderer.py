"""
Playwright Renderer (ASYNC)

Renders an HTML template (resolved per-client) into a PNG.
Automatically injects client branding (logo paths, colors) as template variables.

USAGE:
    from core.renderers.playwright_renderer import render_png
    png_path = await render_png(
        client_id="yellow",
        template_path="edu/edu_p1_3card.html",
        slots={"lesson_title_kr": "...", ...},
        output_path=Path("./output/lesson_01.png"),
        theme="dark",
    )
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader

from core.client_config import ClientConfig, get_client_config
from core.renderers.template_resolver import resolve_template

# Viewport defaults
EDU_CAROUSEL_SIZE = (1080, 1080)     # square for Instagram/X carousel
NEWS_CARD_16x9 = (1200, 675)         # 16:9 for X, Telegram (reserved, unused)
NEWS_CARD_1x1 = (1080, 1080)         # square — primary news card viewport


async def render_png(
    client_id: str,
    template_path: str,
    slots: dict[str, Any],
    output_path: Path,
    theme: str = "dark",
    viewport: tuple[int, int] = EDU_CAROUSEL_SIZE,
    wait_ms: int = 1200,
    device_scale_factor: int = 2,
) -> Path:
    """
    Render an HTML template to PNG with client branding automatically applied.
    Must be awaited inside an asyncio event loop (e.g. FastAPI async endpoint).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError("pip install playwright; playwright install chromium")

    config = get_client_config(client_id)

    # Resolve template (checks override first, then core)
    template_file = resolve_template(client_id, template_path)

    # Build Jinja environment from the template's parent directory
    env = Environment(
        loader=FileSystemLoader(str(template_file.parent)),
        autoescape=False,  # we trust our own slots; templates handle escaping explicitly
    )
    template = env.get_template(template_file.name)

    # Automatically inject client branding into slots
    enhanced_slots = _inject_brand_slots(slots, config, theme)

    # Render HTML
    html = template.render(**enhanced_slots)

    # Ensure output dir exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Playwright screenshot (ASYNC)
    width, height = viewport

    async def capture() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    device_scale_factor=device_scale_factor,
                )
                if client_id == "squid":
                    # Squid's Korean body face is embedded below. Do not let a
                    # remote stylesheet/network-idle wait turn an otherwise
                    # completed Railway generation into a random Netlify 504.
                    await page.set_content(html, wait_until="load", timeout=5_000)
                    await page.wait_for_function(
                        "document.fonts.status === 'loaded'",
                        timeout=2_500,
                    )
                else:
                    await page.set_content(html, wait_until="networkidle")
                await page.wait_for_timeout(wait_ms)
                await page.screenshot(
                    path=str(output_path),
                    clip={"x": 0, "y": 0, "width": width, "height": height},
                )
            finally:
                await browser.close()

    if client_id == "squid":
        # Includes Chromium launch, local font readiness, fixed layout settle,
        # capture, and close. The LLM stage has its own 30s budget; this bound
        # preserves the upstream 55s end-to-end timeout margin.
        await asyncio.wait_for(capture(), timeout=8.0)
    else:
        await capture()

    return output_path


# ────────────────────────────────────────────────────
# Brand Injection
# ────────────────────────────────────────────────────

# Extra web-font <link> tags per brand font. Every template already loads
# Pretendard, so Pretendard/unknown → no extra links. Latin-only fonts (Inter)
# rely on the always-present Pretendard to cover Hangul via the fallback stack.
_FONT_CSS_LINKS: dict[str, str] = {
    "Gmarket Sans": (
        '<link rel="stylesheet" '
        'href="https://cdn.jsdelivr.net/gh/fonts-archive/GmarketSans/GmarketSans.css">'
    ),
    "Inter": (
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" '
        'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">'
    ),
}


def _font_face_style(family: str, path: Path) -> Optional[str]:
    """Inline <style> @font-face embedding a self-hosted font file as a data URL.
    Returns None if the file can't be read, so callers degrade to a fallback
    font rather than aborting the render."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    ext = path.suffix.lower().lstrip(".")
    fmt = {"woff2": "woff2", "woff": "woff", "otf": "opentype", "ttf": "truetype"}.get(ext, "woff2")
    mime = {"woff2": "font/woff2", "woff": "font/woff", "otf": "font/otf", "ttf": "font/ttf"}.get(ext, "font/woff2")
    data = base64.b64encode(raw).decode("ascii")
    return (
        f"<style>@font-face{{font-family:'{family}';"
        f"src:url(data:{mime};base64,{data}) format('{fmt}');"
        f"font-weight:100 900;font-display:swap;}}</style>"
    )


def _build_font_head(config: ClientConfig) -> str:
    """Extra <head> markup to load a client's brand fonts: CDN links for known
    web fonts (body + display), plus an inline @font-face for a self-hosted
    display font when its file is present. Pretendard ships in every template,
    so it needs nothing here."""
    parts: list[str] = []
    seen: set[str] = set()

    def add_cdn(fam: Optional[str]) -> None:
        if fam and fam not in seen and fam in _FONT_CSS_LINKS:
            parts.append(_FONT_CSS_LINKS[fam])
            seen.add(fam)

    body_family = config.brand.font_family
    body_font_path = config.client_dir / "assets" / "PretendardVariable.woff2"
    body_face = (
        _font_face_style(body_family, body_font_path)
        if body_family == "Pretendard Variable" and body_font_path.is_file()
        else None
    )
    if body_face:
        parts.append(body_face)
        seen.add(body_family)
    else:
        add_cdn(body_family)

    display = config.brand.font_display
    if display and display not in seen:
        font_path = config.font_display_file_path
        # .is_file() (not .exists()) so a directory routes to the fallback;
        # _font_face_style returns None on any read error (perms / TOCTOU race).
        face = _font_face_style(display, font_path) if (font_path and font_path.is_file()) else None
        if face:
            parts.append(face)
            seen.add(display)
        else:
            # no readable self-hosted file — display font might be a known CDN web font
            add_cdn(display)
    return "".join(parts)


def _inject_brand_slots(
    slots: dict[str, Any],
    config: ClientConfig,
    theme: str,
) -> dict[str, Any]:
    """
    Add client-specific branding variables to the Jinja slots.
    """
    enhanced = dict(slots)  # shallow copy

    enhanced.setdefault("theme", theme)

    enhanced["logo_dark_path"] = _logo_to_data_url(config.logo_dark_path)
    enhanced["logo_light_path"] = _logo_to_data_url(config.logo_light_path)

    enhanced["brand_primary_color"] = config.brand.primary_color
    enhanced["brand_bg_dark"] = config.brand.bg_dark
    enhanced["brand_bg_yellow"] = config.brand.bg_yellow
    enhanced["brand_text_primary"] = config.brand.text_primary
    enhanced["brand_text_body"] = config.brand.text_body
    enhanced["font_family"] = config.brand.font_family
    enhanced["font_display"] = config.brand.font_display or ""
    enhanced["brand_font_links"] = _build_font_head(config)

    enhanced["client_id"] = config.client_id
    enhanced["client_name"] = config.name

    return enhanced


_TRANSPARENT_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _logo_to_data_url(logo_path: Path) -> str:
    """Convert a logo file to a base64 data URL for embedding in HTML.
    Falls back to a 1x1 transparent PNG when the file is absent, is a
    directory, or can't be read (perms / TOCTOU race) — never crashes render."""
    if not logo_path.is_file():
        return _TRANSPARENT_PNG

    try:
        raw = logo_path.read_bytes()
    except OSError:
        return _TRANSPARENT_PNG

    ext = logo_path.suffix.lower().lstrip(".")
    mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "svg": "image/svg+xml",
        "webp": "image/webp",
    }.get(ext, "image/png")

    b64 = base64.b64encode(raw).decode()
    return f"data:{mime};base64,{b64}"


# ────────────────────────────────────────────────────
# Batch rendering (for carousels) — ASYNC
# ────────────────────────────────────────────────────

async def render_carousel(
    client_id: str,
    lessons: list[dict],
    output_dir: Path,
    size: tuple[int, int] = EDU_CAROUSEL_SIZE,
) -> list[Path]:
    """
    Render a multi-slide carousel. Must be awaited.
    """
    from core.orchestrator import LAYOUT_TEMPLATES  # avoid circular import

    output_dir.mkdir(parents=True, exist_ok=True)
    png_paths: list[Path] = []

    for lesson in lessons:
        layout = lesson["layout"]
        template_file = LAYOUT_TEMPLATES.get(layout)
        if not template_file:
            print(f"⚠ Unknown layout: {layout}")
            continue

        output_png = output_dir / f"lesson_{lesson['lesson_number']:02d}.png"
        await render_png(
            client_id=client_id,
            template_path=f"edu/{template_file}",
            slots=lesson["slots"],
            output_path=output_png,
            theme=lesson.get("theme", "dark"),
            viewport=size,
        )
        png_paths.append(output_png)

    return png_paths
