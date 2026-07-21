"""
News Card LLM Pipeline (Multi-tenant)

Takes a client's content source (tweet, blog, announcement) and produces a
single 1080x1080 Korean news card spec: label badge + headline + 1-3 body
bullets + date + source_url + theme.

USAGE:
    from core.llm.news_card_pipeline import generate_news_card_spec

    spec = generate_news_card_spec(
        client_id="yellow",
        source_content="Yellow goes live on Ethereum mainnet...",
        source_type="tweet",
        source_url="https://x.com/Yellow/status/...",
    )
    # → {label, date, headline, body_lines, source_url, theme}
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

from core.brand_voice import build_brand_voice_prompt
from core.client_naming import enforce_client_display_name
from core.client_config import ClientConfig, get_client_config
from core.llm.anthropic_compat import create_message
from core.sources.source_image import PreparedSourceImage
from core.sources.source_text_cleanup import SourceTextCleanupError, probe_source_text


# ────────────────────────────────────────────────────
# System Prompt (client-agnostic)
# ────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the content brain for a Web3 news card system
serving Korean audiences.

Your job: Take an English source (blog post, tweet, or announcement) about a
blockchain / Web3 product, and produce a single Korean news card — a tight
1080x1080 square graphic with: a short label badge, a date, one headline
sentence, and 1-3 body bullet lines.

Return STRICT JSON ONLY. No markdown fences, no prose, no commentary.
Do not use em dashes (—) in any output text values. Use commas or periods instead."""

VISUAL_COPY_DISCOVERY_SYSTEM_PROMPT = """You are a deterministic visual-copy localizer for official Squid social creatives.
Inspect only the attached image pixels. Find complete, meaningful source-language captions or headlines that are intended to be read as creative copy, transcribe them exactly, and translate them into concise natural Korean.
Do not infer text from the post caption. Ignore logos, wordmarks, handles, URLs, watermarks, tiny product UI labels, and decorative letter-like shapes. A short meme phrase or slang caption is meaningful copy.
Return STRICT JSON ONLY in the requested schema. No markdown or commentary."""

VISUAL_PLACEMENT_AUDIT_SYSTEM_PROMPT = """You are the final visual replacement QA for Korean localization of official Squid creatives.
Inspect the attached image composition and precisely map the visible source-language phrase boxes. The renderer will detect and content-aware reconstruct only the original lettering pixels inside each audited box, then place Korean over the cleaned visual with no caption panel.
Confirm only geometry that you can locate confidently and enough clearance for a 1-3 source-pixel cleanup dilation. Never move the Korean to a different part of the creative.
Return STRICT JSON ONLY in the requested schema. No markdown or commentary."""

# Opus 4.8 no longer accepts the legacy temperature control, so using it for
# pixel geometry makes identical images sample slightly different boxes. Keep
# the creative-writing model for copy, but use the existing temperature-capable
# visual QA model for stable placement coordinates.
VISUAL_PLACEMENT_AUDIT_MODEL = os.environ.get(
    "VISUAL_PLACEMENT_AUDIT_MODEL",
    "claude-sonnet-4-5-20250929",
)
# Railway may spend up to 12s fetching the source image before this function,
# then still needs deterministic cleanup and a ~5s Playwright render. Keep the
# LLM stage at 30s so Netlify's 55s Railway timeout retains a real margin.
_SQUID_VISUAL_LLM_BUDGET_SECONDS = 30.0
_SQUID_MAIN_LLM_MAX_SECONDS = 22.0
_SQUID_VISUAL_CALL_MAX_SECONDS = 8.0
_SQUID_VISUAL_CALL_RESERVE_SECONDS = 6.0
_MAX_SQUID_STABLE_VISUAL_CALLS = 3


def _minimum_squid_font_percent(
    source_image_width: int,
    source_image_height: int,
) -> float:
    """Mirror the renderer's max(14px, 2%-of-frame-width) floor."""
    if source_image_width <= 0 or source_image_height <= 0:
        return 2.0
    frame_width = (
        1080.0
        if source_image_width >= source_image_height
        else 1080.0 * source_image_width / source_image_height
    )
    return max(2.0, 14.0 / frame_width * 100.0)


def _remaining_llm_timeout(
    deadline: Optional[float],
    maximum: float,
    *,
    reserve: float = 0.0,
) -> Optional[float]:
    if deadline is None:
        return None
    remaining = deadline - time.monotonic() - reserve
    if remaining < 1.0:
        raise TimeoutError("Squid visual localization time budget is exhausted")
    return max(1.0, min(maximum, remaining))


# ────────────────────────────────────────────────────
# User Prompt Builder (injects client-specific config)
# ────────────────────────────────────────────────────

BASE_USER_PROMPT = """# News Card Pipeline

## 1. Your Output

A single JSON object describing one Korean news card (1080x1080 square).
NOT a carousel. NOT multiple slides. One card.

## 2. Card Design Philosophy

- 한눈에 핵심이 들어오는 뉴스 카드 (스크롤 멈춤형)
- label = 무슨 종류의 뉴스인가 (짧은 분류 배지)
- headline = 한 문장으로 요약된 핵심 (경어체)
- body_lines = 핵심 디테일 1-3줄 (불릿 형태)
- "What changed" + "Why it matters" 두 가지가 카드 안에 다 들어가야 함
- 광고 톤 아닌 뉴스 톤 (홍보가 아니라 사실 전달)
- 한국 현지화: 원문의 주장과 강도를 유지하면서 자연스러운 한국어로 옮기고,
  낯선 용어만 필요한 만큼 짧게 풀어 쓸 것
- 원문에 없는 해석, 효용, 시장 전망, 한국 관점을 새로 추가하지 말 것
- 한국 출시·지원 여부가 원문에 없으면 한국에서 제공된다고 추정하거나 과장하지 말 것

## 3. Output Schema (FIXED — do not add or remove keys)

{{
  "label": "뉴스 분류 배지 (4-15자)",
  "date": "YYYY.MM.DD",
  "headline": "메인 헤드라인 한 문장 (15-40자, 경어체)",
  "body_lines": ["불릿 1 (10-30자)", "불릿 2 (10-30자)", "불릿 3 (옵션)"],
  "source_url": "원본 URL (입력값 그대로)",
  "theme": "dark" | "yellow",
  "source_logo_visible": true | false,
  "source_text_visible": true | false,
  "translation_regions": [
    {{
      "source_text": "이미지에 보이는 원문 문구를 줄바꿈까지 그대로 기록",
      "text": "원본 배너 문구의 자연스러운 한국어 번역",
      "x": 0-100,
      "y": 0-100,
      "width": 1-100,
      "height": 1-100,
      "align": "left" | "center" | "right",
      "font_role": "display" | "body",
      "font_size": 2-12,
      "text_color": "#RRGGBB"
    }}
  ]
}}

Rules:
- label: 짧은 분류 텍스트. 예: "메인넷 라이브", "파트너십", "기능 업데이트", "신규 상장"
- date: YYYY.MM.DD 형식 (점 구분). 본문에 명시된 날짜가 있으면 그것을, 없으면 오늘({today_date}).
- headline: 한 문장. 경어체 (합니다/됩니다/입니다). 주체+동사가 명확해야 함.
- body_lines: 1-3개 배열. 각 줄은 헤드라인을 뒷받침하는 구체 사실. 중복 금지.
- source_url: 입력 source_url을 그대로 옮길 것. 생성/변경 금지 (시스템이 사후 보정).
- theme: 아래 4번 규칙대로.
- source_logo_visible: 첨부 이미지에 현재 Client의 공식 로고 또는 공식 워드마크가 명확히 보이면 true, 아니면 false. 파트너사·거래소·토큰 등 다른 로고는 무시할 것. 이미지가 없으면 false.
- source_text_visible / translation_regions: 아래 Original Visual Localization 규칙대로. Squid 이외 또는 이미지가 없으면 반드시 false와 []로 설정.

## 4. Theme Selection

- **dark** — 기술 업데이트, 파트너십, 통합, 일반 뉴스 · 기본값
- **yellow** — 메인넷 라이브, 신규 상장, 제품 출시, 중요 공지

## 5. Korean Translation Rules

- 경어체 (합니다/습니다), NOT 반말, NOT 하십시오체
- Preserve these terms as-is (keep English):
{preserve_terms_block}

- Glossary translations:
{glossary_block}

- Tone guidance: {tone_guidance}

{brand_voice_block}

- em dash(—) 사용 금지. 쉼표(,)나 마침표(.)로 대체. (label/headline/body_lines 전체 적용)

- Length budgets (Korean chars):
  - label: 4-15 chars
  - headline: 15-40 chars (한 문장)
  - body_lines[i]: 10-30 chars each
  - body_lines 총 개수: 1-3개

## 6. Input

Client: {client_name} ({client_id})
Source type: {source_type}
Source URL: {source_url}
Today: {today_date}

Source content:
<<<
{source_content}
>>>

Original visual context:
{visual_guidance}

Original visual localization:
{visual_localization_rules}

When an original visual is attached:
- Read visible product names, feature labels, token pairs, UI states, and numbers.
- Check whether the current Client's official logo or wordmark is already clearly visible, so the renderer can avoid placing a duplicate logo.
- Use the visual only as factual supporting context; do not invent hidden details.
- Write Korean copy that makes the original visual locally readable without adding a new angle or claim.
- Preserve important brand and product terms visible in the image.

## 7. Output Format (STRICT JSON)

Return JSON only. No markdown. No prose. No code fences.

{{
  "label": "...",
  "date": "YYYY.MM.DD",
  "headline": "...",
  "body_lines": ["...", "..."],
  "source_url": "{source_url}",
  "theme": "dark" | "yellow",
  "source_logo_visible": true | false,
  "source_text_visible": true | false,
  "translation_regions": []
}}

## 8. Now Process This Source

Return JSON only.
"""


def _today_kst_date() -> str:
    """Return today's date in Korea (UTC+9) as YYYY.MM.DD."""
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y.%m.%d")


def _build_user_prompt(
    config: ClientConfig,
    source_content: str,
    source_type: str,
    source_url: str,
    has_source_image: bool = False,
) -> str:
    """Build the client-specific user prompt."""
    llm = config.llm.news_card

    if llm.preserve_terms:
        preserve_block = "\n".join(f"  - {t}" for t in llm.preserve_terms)
    else:
        preserve_block = "  (none specified)"

    if llm.glossary:
        glossary_block = "\n".join(
            f"  - \"{en}\" → \"{ko}\"" for en, ko in llm.glossary.items()
        )
    else:
        glossary_block = "  (use standard crypto Korean terminology)"

    tone = llm.tone_guidance or "professional but approachable, 경어체"
    visual_guidance = (
        "An original post image is attached before this prompt. Analyze its visible text and product UI."
        if has_source_image
        else "No original post image is attached. Use the source text only."
    )
    visual_localization_rules = (
        """Squid official-creative translation mode is active.
- Treat the attached image as the final composition. Preserve its character, product, background, crop, layout, and official logo.
- Detect only meaningful marketing/editorial copy that a Korean reader should read. A logo, wordmark, handle, URL, token symbol, product name, decorative letters, or text inside product UI alone does NOT count.
- A short natural-language punchline still counts. Translate single-word slang, reaction text, and meme captions such as "chillin'" when they are visibly printed in the creative. Never infer image text from the post caption.
- If there is no meaningful translatable copy, set source_text_visible=false and translation_regions=[]. Do not invent a headline, badge, footer, logo, caption, or Korean angle on the image.
- If meaningful copy exists, set source_text_visible=true and return 1-4 translation_regions. Translate only the visible copy into concise, natural Korean. Preserve the original claim strength, humor, capitalization intent, line hierarchy, product names, handles, numbers, and token symbols. Keep a 1-2 line source at the same line count; condense a 3+ line source to at most 2 lines. Keep approximately the same rendered width and retain a short prominent Latin keyword when it is part of the visual rhythm and remains natural in Korean. For a one-word reaction or meme caption, prefer a 2-5 syllable Korean expression instead of expanding it into an explanatory sentence.
- source_text must transcribe the visible source phrase exactly, including its line breaks. x/y/width/height must tightly cover that ORIGINAL source phrase including its outline and shadow. These coordinates are the source-removal box and the final Korean text area. Do not move the Korean translation to another part of the banner.
- Every translation_regions[].text containing meaningful English copy must contain Korean Hangul. Never copy the original English sentence back into text. English may remain only for protected product names, handles, URLs, numbers, token symbols, or a short keyword repeated in the source visual rhythm, inside an otherwise Korean translation.
- Choose display for large headline copy and body for supporting copy. font_size is a percentage of the source image width. Keep translation text to at most 2 lines.
- After this response, a separate visual QA pass tightens the exact source-phrase geometry before rendering.
- The renderer detects the original glyph/outline silhouette inside each audited phrase box, reconstructs only those pixels from the surrounding visual, and places concise Korean directly in the same x/y/width/height area. The Korean layer has a transparent background: never add a rectangle, rounded panel, scrim, tint, gradient, blur patch, separate footer, unrelated headline panel, or duplicate caption area.
- The small 1-3 source-pixel cleanup dilation must not reach any official or partner logo, face, product UI, token icon, unrelated text, or ambiguous other_visual. A character, limb, or product already directly behind the original lettering may remain the caption substrate only inside that existing caption footprint. If the source phrase cannot be tightly isolated, preserve the original creative unchanged.
- If the QA pass cannot confidently locate every source phrase or the Korean cannot fit the same line count and area, it removes every localization layer and preserves the official creative unchanged.
- Never translate from the source caption into the image. translation_regions may contain only text visibly present in the attached creative."""
        if config.client_id == "squid" and has_source_image
        else "This client does not use visual-copy replacement. Set source_text_visible=false and translation_regions=[]."
    )

    return BASE_USER_PROMPT.format(
        preserve_terms_block=preserve_block,
        glossary_block=glossary_block,
        tone_guidance=tone,
        brand_voice_block=build_brand_voice_prompt(config, "news_card"),
        client_name=config.name,
        client_id=config.client_id,
        source_type=source_type,
        source_url=source_url or "(none)",
        source_content=source_content.strip(),
        visual_guidance=visual_guidance,
        visual_localization_rules=visual_localization_rules,
        today_date=_today_kst_date(),
    )


_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_HANGUL = re.compile(r"[가-힣]")
_REGION_ALIGNMENTS = {"left", "center", "right"}
_REGION_FONT_ROLES = {"display", "body"}


def _number(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text_width_units(text: str) -> float:
    """Approximate the widest rendered line in font-relative units."""
    line_units: list[float] = []
    for line in text.splitlines() or [text]:
        units = 0.0
        for character in line:
            if "가" <= character <= "힣":
                units += 1.0
            elif character.isspace():
                units += 0.35
            elif character.isupper() or character.isdigit():
                units += 0.65
            elif character.isalpha():
                units += 0.55
            else:
                units += 0.45
        line_units.append(units)
    return max(line_units, default=1.0)


def _canonicalize_translation_rows(text: str, row_count: int) -> str:
    """Make Korean line breaks depend only on audited visual rows."""
    plain = re.sub(r"\s+", " ", text).strip()
    if row_count <= 1 or not plain:
        return plain

    tokens = plain.split(" ")
    candidates: list[tuple[tuple[float, float, int], str, str]] = []
    if len(tokens) > 1:
        for index in range(1, len(tokens)):
            left = " ".join(tokens[:index])
            right = " ".join(tokens[index:])
            left_units = _text_width_units(left)
            right_units = _text_width_units(right)
            candidates.append((
                (max(left_units, right_units), abs(left_units - right_units), index),
                left,
                right,
            ))
    elif len(plain) > 1:
        characters = list(plain)
        for index in range(1, len(characters)):
            left = "".join(characters[:index])
            right = "".join(characters[index:])
            left_units = _text_width_units(left)
            right_units = _text_width_units(right)
            candidates.append((
                (max(left_units, right_units), abs(left_units - right_units), index),
                left,
                right,
            ))
    if not candidates:
        return plain
    _, left, right = min(candidates, key=lambda candidate: candidate[0])
    return f"{left}\n{right}"


def _parse_json_response(response: object, purpose: str) -> dict:
    """Parse a JSON object from an Anthropic response, tolerating code fences."""
    try:
        raw_text = response.content[0].text.strip()
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError(f"LLM returned no text for {purpose}") from exc
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON for {purpose}: {raw_text[:500]}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM returned non-object JSON for {purpose}")
    return parsed


def _discover_visual_copy(
    api_client: object,
    model: str,
    result: dict,
    source_image: PreparedSourceImage,
    preserve_terms: list[str],
    *,
    deadline: Optional[float] = None,
) -> tuple[dict, int]:
    """Make image-only discovery authoritative for every uncached creative."""
    protected = ", ".join(preserve_terms) or "Squid"
    prompt = f"""Independently inspect the attached official Squid creative.

Protected terms that may remain Latin: {protected}

Find up to four complete, meaningful source-language captions or headlines.
- Read only visible image pixels. Never infer copy from post context.
- Include short meme/slang captions such as "chillin'".
- Ignore logos, wordmarks, handles, URLs, watermarks, decorative shapes, and tiny product/UI labels.
- Transcribe source_text exactly and translate its meaning into concise natural Korean containing Hangul.
- Use image-relative percentage coordinates around the complete phrase, including every visible row and outline/shadow.
- text must contain at most two non-empty lines. Never add a caption panel.
- Return found=false when no meaningful translatable copy is visibly present.

Return exactly:
{{
  "found": true,
  "regions": [
    {{
      "source_text": "exact visible phrase",
      "text": "자연스러운 한국어",
      "x": 35,
      "y": 80,
      "width": 30,
      "height": 10,
      "align": "center",
      "font_role": "display",
      "font_size": 6,
      "text_color": "#FFFFFF"
    }}
  ]
}}
or:
{{"found":false,"regions":[]}}
"""
    no_text_votes = 0
    calls_used = 0
    for attempt in range(2):
        try:
            timeout = _remaining_llm_timeout(
                deadline,
                _SQUID_VISUAL_CALL_MAX_SECONDS,
                reserve=_SQUID_VISUAL_CALL_RESERVE_SECONDS,
            )
            calls_used += 1
            response = create_message(
                api_client,
                model=model,
                max_tokens=1200,
                temperature=0,
                timeout=timeout,
                system=VISUAL_COPY_DISCOVERY_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": source_image.media_type,
                                "data": source_image.base64_data,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            discovery = _parse_json_response(
                response,
                f"visual copy discovery attempt {attempt + 1}",
            )
            if discovery.get("found") is False:
                no_text_votes += 1
                if no_text_votes == 2:
                    return _clear_visual_localization(result), calls_used
                print("[squid] visual discovery found no copy; confirming once")
                continue
            raw_regions = discovery.get("regions")
            if discovery.get("found") is not True or not isinstance(raw_regions, list):
                raise ValueError("visual copy discovery omitted a boolean found result")
            if not 1 <= len(raw_regions) <= 4:
                raise ValueError("visual copy discovery returned an invalid region count")
            regions: list[dict] = []
            for raw_region in raw_regions:
                if not isinstance(raw_region, dict):
                    raise ValueError("visual copy discovery region must be an object")
                source_text = raw_region.get("source_text")
                text = raw_region.get("text")
                box = _strict_percent_box(
                    raw_region,
                    minimum_width=6.0,
                    minimum_height=3.0,
                )
                if (
                    not isinstance(source_text, str)
                    or not source_text.strip()
                    or not isinstance(text, str)
                    or not text.strip()
                    or not _HANGUL.search(text)
                    or len([line for line in text.splitlines() if line.strip()]) > 2
                    or box is None
                ):
                    raise ValueError("visual copy discovery region is invalid")
                font_size = max(2.8, min(12.0, _number(raw_region.get("font_size"), 5.2)))
                text_color = raw_region.get("text_color")
                regions.append({
                    "source_text": source_text.strip()[:240],
                    "text": text.strip()[:240],
                    **box,
                    "align": raw_region.get("align")
                    if raw_region.get("align") in _REGION_ALIGNMENTS
                    else "center",
                    "font_role": raw_region.get("font_role")
                    if raw_region.get("font_role") in _REGION_FONT_ROLES
                    else "display",
                    "font_size": round(font_size, 2),
                    "text_color": text_color.upper()
                    if isinstance(text_color, str) and _HEX_COLOR.match(text_color)
                    else "#FFFFFF",
                })
            result["source_text_visible"] = True
            result["translation_regions"] = regions
            print(f"[squid] stable visual discovery recovered {len(regions)} phrase(s)")
            return result, calls_used
        except Exception as exc:
            print(
                f"[squid] visual copy discovery attempt {attempt + 1} failed safely: "
                f"{type(exc).__name__}"
            )
    return (
        _clear_visual_localization(result, failure_status="cleanup_failed"),
        calls_used,
    )


_PLACEMENT_PROTECTED_KINDS = {
    "source_text",
    "other_text",
    "logo",
    "character",
    "face",
    "limb",
    "product",
    "product_ui",
    "token_icon",
    "other_visual",
}
_SOURCE_TEXT_CLEANUP_PADDING_PX = 3.0
_SOURCE_TEXT_CLEANUP_SUBSTRATE_KINDS = {"character", "limb", "product"}
_SOURCE_TEXT_CLEANUP_SUBSTRATE_MIN_RATIO = 0.50
_MAX_VISUAL_PLACEMENT_AUDIT_CALLS = 2
_VISUAL_LOCALIZATION_FAILURE_KEY = "_visual_localization_failure"
_VISUAL_AUDIT_PRIVATE_KEYS = (
    "_source_index",
    "_source_line_count",
    "_protected_regions",
)


def _clear_visual_localization(
    result: dict,
    *,
    failure_status: Optional[str] = None,
) -> dict:
    """Fail safe: preserve the official creative without any Korean overlay."""
    result["source_text_visible"] = False
    result["translation_regions"] = []
    if failure_status == "cleanup_failed":
        result[_VISUAL_LOCALIZATION_FAILURE_KEY] = failure_status
    else:
        result.pop(_VISUAL_LOCALIZATION_FAILURE_KEY, None)
    return result


def _strict_percent_box(
    value: object,
    *,
    minimum_width: float,
    minimum_height: float,
) -> Optional[dict[str, float]]:
    """Parse an image-relative box without silently repairing unsafe geometry."""
    if not isinstance(value, dict):
        return None
    numbers: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        parsed = float(raw)
        if not math.isfinite(parsed):
            return None
        numbers[key] = parsed
    if (
        numbers["x"] < 0
        or numbers["y"] < 0
        or numbers["width"] < minimum_width
        or numbers["height"] < minimum_height
        or numbers["x"] + numbers["width"] > 100
        or numbers["y"] + numbers["height"] > 100
    ):
        return None
    return numbers


def _audit_log_payload(audit: dict) -> dict:
    """Return coordinates and kinds only, without source or translated copy."""
    return {
        "safe": audit.get("safe"),
        "protected_regions": [
            {key: item.get(key) for key in ("kind", "source_index", "x", "y", "width", "height")}
            for item in audit.get("protected_regions", [])
            if isinstance(item, dict)
        ][:32] if isinstance(audit.get("protected_regions"), list) else "invalid",
    }


def _schema_valid_retry_protections(
    audit: dict,
    *,
    region_count: int,
) -> Optional[list[dict]]:
    """Return validator-shaped non-source boxes only for a complete valid map.

    A corrective placement pass may tighten source-text geometry, but it must
    never forget a logo, character, product, or other protected visual found by
    the first pass.  If any part of the first map is malformed, there is no safe
    evidence set to carry forward and the caller must not retry.
    """
    if audit.get("safe") is not True:
        return None
    raw_protected = audit.get("protected_regions")
    if (
        not isinstance(raw_protected, list)
        or not raw_protected
        or len(raw_protected) > 32
    ):
        return None

    retained: list[dict] = []
    for raw in raw_protected:
        if not isinstance(raw, dict):
            return None
        raw_kind = raw.get("kind")
        kind = "other_visual" if raw_kind == "other" else raw_kind
        if kind not in _PLACEMENT_PROTECTED_KINDS:
            return None
        box = _strict_percent_box(
            raw,
            minimum_width=0.25,
            minimum_height=0.25,
        )
        if box is None:
            return None
        if kind == "source_text":
            source_index = raw.get("source_index")
            if (
                isinstance(source_index, bool)
                or not isinstance(source_index, int)
                or source_index < 0
                or source_index >= region_count
            ):
                return None
            continue
        retained.append({"kind": kind, **box})
    return retained


def _merge_retry_protections(audit: dict, retained: list[dict]) -> dict:
    """Union first-pass non-source evidence into a corrective audit response."""
    if not retained:
        return audit
    raw_protected = audit.get("protected_regions")
    if not isinstance(raw_protected, list):
        return audit

    merged = copy.deepcopy(raw_protected)
    fingerprints: set[tuple[object, ...]] = set()
    for raw in raw_protected:
        if not isinstance(raw, dict):
            continue
        raw_kind = raw.get("kind")
        kind = "other_visual" if raw_kind == "other" else raw_kind
        if kind == "source_text" or kind not in _PLACEMENT_PROTECTED_KINDS:
            continue
        box = _strict_percent_box(
            raw,
            minimum_width=0.25,
            minimum_height=0.25,
        )
        if box is not None:
            fingerprints.add((
                kind,
                box["x"],
                box["y"],
                box["width"],
                box["height"],
            ))

    for protected in retained:
        fingerprint = (
            protected["kind"],
            protected["x"],
            protected["y"],
            protected["width"],
            protected["height"],
        )
        if fingerprint not in fingerprints:
            merged.append(copy.deepcopy(protected))
            fingerprints.add(fingerprint)
    return {**audit, "protected_regions": merged}


def _source_text_visual_row_count(boxes: list[dict[str, float]]) -> int:
    """Count visual rows without treating same-row phrase fragments as lines."""
    rows: list[list[dict[str, float]]] = []

    def center_y(box: dict[str, float]) -> float:
        return box["y"] + box["height"] / 2.0

    def same_visual_row(
        first: dict[str, float],
        second: dict[str, float],
    ) -> bool:
        first_bottom = first["y"] + first["height"]
        second_bottom = second["y"] + second["height"]
        overlap = max(
            0.0,
            min(first_bottom, second_bottom) - max(first["y"], second["y"]),
        )
        minimum_height = min(first["height"], second["height"])
        center_distance = abs(center_y(first) - center_y(second))
        return (
            overlap / max(0.001, minimum_height) >= 0.35
            or center_distance <= minimum_height * 0.35
        )

    for box in sorted(boxes, key=lambda item: (center_y(item), item["x"])):
        compatible_rows = [
            row
            for row in rows
            if all(same_visual_row(box, existing) for existing in row)
        ]
        if not compatible_rows:
            rows.append([box])
            continue
        selected = min(
            compatible_rows,
            key=lambda row: abs(
                center_y(box)
                - sum(center_y(existing) for existing in row) / len(row)
            ),
        )
        selected.append(box)
    return len(rows)


def _normalized_source_identity(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _validate_audit_source_identities(
    raw_regions: list[dict],
    audit: dict,
) -> str:
    """Bind stable geometry to an independently transcribed source phrase."""
    raw_identities = audit.get("verified_source_texts")
    if not isinstance(raw_identities, list) or len(raw_identities) != len(raw_regions):
        return "verified_source_texts must cover every subtitle exactly once"
    verified: dict[int, str] = {}
    for position, raw_identity in enumerate(raw_identities):
        if not isinstance(raw_identity, dict):
            return f"verified_source_texts[{position}] must be an object"
        source_index = raw_identity.get("source_index")
        text = raw_identity.get("text")
        if (
            isinstance(source_index, bool)
            or not isinstance(source_index, int)
            or source_index < 0
            or source_index >= len(raw_regions)
            or source_index in verified
            or not isinstance(text, str)
            or not text.strip()
        ):
            return f"verified_source_texts[{position}] is invalid"
        verified[source_index] = text
    for index, region in enumerate(raw_regions):
        expected = _normalized_source_identity(region.get("source_text"))
        actual = _normalized_source_identity(verified.get(index))
        if not expected or actual != expected:
            return f"verified source text for subtitle {index} does not match"
    return ""


def _validate_visual_placement_audit(
    raw_regions: list[dict],
    first_pass_source_boxes: list[Optional[dict[str, float]]],
    audit: dict,
    source_image: PreparedSourceImage,
    *,
    require_source_identity: bool = False,
) -> tuple[Optional[list[dict]], str]:
    """Validate source geometry and conservative cleanup clearance."""
    if audit.get("safe") is False:
        return None, "unsafe"
    if audit.get("safe") is not True:
        return None, "safe must be a boolean"
    if require_source_identity:
        identity_error = _validate_audit_source_identities(raw_regions, audit)
        if identity_error:
            return None, identity_error

    raw_protected = audit.get("protected_regions")
    if not isinstance(raw_protected, list) or not raw_protected:
        return None, "protected_regions must be a non-empty array"
    if len(raw_protected) > 32:
        return None, "protected_regions exceeds 32 boxes"

    audited_source_boxes: dict[int, list[dict[str, float]]] = {
        index: [] for index in range(len(raw_regions))
    }
    protected_boxes: list[tuple[str, Optional[int], dict[str, float]]] = []
    normalized_protected_regions: list[dict] = []
    for position, raw in enumerate(raw_protected):
        if not isinstance(raw, dict):
            return None, f"protected_regions[{position}] must be an object"
        raw_kind = raw.get("kind")
        # `other` is accepted only as a conservative alias: it still protects
        # the full box and never becomes a placement target.
        kind = "other_visual" if raw_kind == "other" else raw_kind
        if kind not in _PLACEMENT_PROTECTED_KINDS:
            return None, f"protected_regions[{position}].kind is invalid"
        box = _strict_percent_box(raw, minimum_width=0.25, minimum_height=0.25)
        if box is None:
            return None, f"protected_regions[{position}] must use non-degenerate 0-100 percentage coordinates"
        source_index: Optional[int] = None
        if kind == "source_text":
            raw_source_index = raw.get("source_index")
            if (
                isinstance(raw_source_index, bool)
                or not isinstance(raw_source_index, int)
                or raw_source_index < 0
                or raw_source_index >= len(raw_regions)
            ):
                return None, f"protected_regions[{position}].source_index is invalid"
            source_index = raw_source_index
            audited_source_boxes[source_index].append(box)
        protected_boxes.append((kind, source_index, box))
        normalized_protected = {"kind": kind, **box}
        if source_index is not None:
            normalized_protected["source_index"] = source_index
        normalized_protected_regions.append(normalized_protected)

    if any(not boxes for boxes in audited_source_boxes.values()):
        return None, "every subtitle requires an audited source_text box"

    cleanup_padding_x = _SOURCE_TEXT_CLEANUP_PADDING_PX / source_image.width * 100.0
    cleanup_padding_y = _SOURCE_TEXT_CLEANUP_PADDING_PX / source_image.height * 100.0

    audited_regions: list[dict] = []
    accepted_cleanup_boxes: list[dict[str, float]] = []
    for index, original in enumerate(raw_regions):
        line_boxes = audited_source_boxes[index]
        left = min(box["x"] for box in line_boxes)
        top = min(box["y"] for box in line_boxes)
        right = max(box["x"] + box["width"] for box in line_boxes)
        bottom = max(box["y"] + box["height"] for box in line_boxes)
        source_box = {"x": left, "y": top, "width": right - left, "height": bottom - top}
        source_area = source_box["width"] * source_box["height"]
        # Absolute bounds reject canvas-sized or otherwise implausible text
        # regions before deterministic raster QA.
        if (
            source_box["width"] > 96.0
            or source_box["height"] > 45.0
            or source_area > 4_000.0
        ):
            return None, f"source_text geometry for subtitle {index} is implausibly large"
        first_box = first_pass_source_boxes[index]
        if require_source_identity and first_box is None:
            return None, f"source_text geometry for subtitle {index} lacks a discovery anchor"
        if first_box is not None:
            source_center_x = source_box["x"] + source_box["width"] / 2.0
            source_center_y = source_box["y"] + source_box["height"] / 2.0
            first_center_x = first_box["x"] + first_box["width"] / 2.0
            first_center_y = first_box["y"] + first_box["height"] / 2.0
            if require_source_identity:
                overlap_width = max(
                    0.0,
                    min(
                        source_box["x"] + source_box["width"],
                        first_box["x"] + first_box["width"],
                    ) - max(source_box["x"], first_box["x"]),
                )
                overlap_height = max(
                    0.0,
                    min(
                        source_box["y"] + source_box["height"],
                        first_box["y"] + first_box["height"],
                    ) - max(source_box["y"], first_box["y"]),
                )
                anchor_width_coverage = overlap_width / max(
                    0.001,
                    first_box["width"],
                )
                anchor_height_coverage = overlap_height / max(
                    0.001,
                    first_box["height"],
                )
                anchor_area_coverage = (
                    overlap_width * overlap_height
                    / max(0.001, first_box["width"] * first_box["height"])
                )
                if (
                    overlap_width
                    / max(0.001, min(source_box["width"], first_box["width"]))
                    < 0.60
                    or overlap_height
                    / max(0.001, min(source_box["height"], first_box["height"]))
                    < 0.45
                    # Normalizing only by the smaller proposal lets a tiny,
                    # centered word box look like a perfect overlap with a
                    # complete discovery phrase.  Keep audit tightening, but
                    # require it to retain a material portion of the immutable
                    # image-only anchor on both axes and by area.
                    or anchor_width_coverage < 0.45
                    or anchor_height_coverage < 0.55
                    or anchor_area_coverage < 0.27
                    or abs(source_center_x - first_center_x)
                    > min(8.0, first_box["width"] * 0.30)
                    or abs(source_center_y - first_center_y)
                    > min(6.0, first_box["height"] * 0.60)
                ):
                    return None, f"source_text geometry for subtitle {index} does not match discovery"
            elif (
                abs(source_center_x - first_center_x)
                > max(35.0, first_box["width"] * 2.0)
                or abs(source_center_y - first_center_y)
                > max(30.0, first_box["height"] * 2.5)
            ):
                return None, f"source_text geometry for subtitle {index} lacks broad corroboration"
        cleanup_box = {
            "x": source_box["x"] - cleanup_padding_x,
            "y": source_box["y"] - cleanup_padding_y,
            "width": source_box["width"] + cleanup_padding_x * 2,
            "height": source_box["height"] + cleanup_padding_y * 2,
        }
        if (
            cleanup_box["x"] < 0
            or cleanup_box["y"] < 0
            or cleanup_box["x"] + cleanup_box["width"] > 100
            or cleanup_box["y"] + cleanup_box["height"] > 100
        ):
            return None, f"cleanup mask for subtitle {index} leaves the source frame"
        cleanup_right = cleanup_box["x"] + cleanup_box["width"]
        cleanup_bottom = cleanup_box["y"] + cleanup_box["height"]
        overlaps_existing_cleanup = any(
            cleanup_box["x"] < existing["x"] + existing["width"]
            and cleanup_right > existing["x"]
            and cleanup_box["y"] < existing["y"] + existing["height"]
            and cleanup_bottom > existing["y"]
            for existing in accepted_cleanup_boxes
        )
        if overlaps_existing_cleanup:
            return None, f"cleanup mask for subtitle {index} overlaps another cleanup mask"
        for kind, protected_source_index, protected in protected_boxes:
            if kind == "source_text" and protected_source_index == index:
                continue
            protected_right = protected["x"] + protected["width"]
            protected_bottom = protected["y"] + protected["height"]
            cleanup_overlap_width = max(
                0.0,
                min(cleanup_right, protected_right) - max(cleanup_box["x"], protected["x"]),
            )
            cleanup_overlap_height = max(
                0.0,
                min(cleanup_bottom, protected_bottom) - max(cleanup_box["y"], protected["y"]),
            )
            cleanup_overlap_area = cleanup_overlap_width * cleanup_overlap_height
            source_overlap_width = max(
                0.0,
                min(source_box["x"] + source_box["width"], protected_right)
                - max(source_box["x"], protected["x"]),
            )
            source_overlap_height = max(
                0.0,
                min(source_box["y"] + source_box["height"], protected_bottom)
                - max(source_box["y"], protected["y"]),
            )
            source_overlap_area = source_overlap_width * source_overlap_height
            existing_caption_substrate = (
                kind in _SOURCE_TEXT_CLEANUP_SUBSTRATE_KINDS
                and cleanup_overlap_area > 0
                and source_overlap_area / cleanup_overlap_area
                >= _SOURCE_TEXT_CLEANUP_SUBSTRATE_MIN_RATIO
            )
            if cleanup_overlap_area > 0 and not existing_caption_substrate:
                return None, f"cleanup mask for subtitle {index} overlaps protected {kind}"
        accepted_cleanup_boxes.append(cleanup_box)
        visual_row_count = _source_text_visual_row_count(line_boxes)
        if visual_row_count > 2:
            return None, f"source_text geometry for subtitle {index} exceeds two visual rows"
        # Preserve the authoritative image-only discovery box as the cleanup
        # seed.  The audit owns final Korean placement and the protection map,
        # but a slightly clipped audit box must never make raster cleanup erase
        # only one word and leave the rest of the verified source phrase.
        cleanup_source_box = (
            first_box
            if require_source_identity and first_box is not None
            else source_box
        )
        audited_regions.append({
            **original,
            **source_box,
            "source_x": cleanup_source_box["x"],
            "source_y": cleanup_source_box["y"],
            "source_width": cleanup_source_box["width"],
            "source_height": cleanup_source_box["height"],
            "_source_index": index,
            # Line structure is visual evidence. Free-form OCR can insert or
            # omit newlines for the same pixels, so it must not make cleanup
            # succeed or fail randomly.
            "_source_line_count": min(4, max(1, visual_row_count)),
            "_protected_regions": copy.deepcopy(normalized_protected_regions),
        })
    return audited_regions, ""


def _audit_visual_subtitle_placement(
    api_client: object,
    model: str,
    result: dict,
    source_image: PreparedSourceImage,
    *,
    raster_probe: bool = False,
    max_calls: int = _MAX_VISUAL_PLACEMENT_AUDIT_CALLS,
    deadline: Optional[float] = None,
) -> dict:
    """Tighten source-text geometry and atomically accept in-place replacements."""
    raw_regions = result.get("translation_regions")
    if result.get("source_text_visible") is not True or not isinstance(raw_regions, list) or not raw_regions:
        return result

    if len(raw_regions) > 4:
        return _clear_visual_localization(result)
    raw_regions = copy.deepcopy(raw_regions)

    inputs: list[dict] = []
    first_pass_source_boxes: list[Optional[dict[str, float]]] = []
    for index, region in enumerate(raw_regions):
        if not isinstance(region, dict):
            return _clear_visual_localization(result)
        text = region.get("text")
        if not isinstance(text, str) or not text.strip():
            return _clear_visual_localization(result)
        source_text = region.get("source_text")
        if not isinstance(source_text, str) or not source_text.strip():
            return _clear_visual_localization(result)
        if len([line for line in text.splitlines() if line.strip()]) > 2:
            text = re.sub(r"\s+", " ", text).strip()
            region["text"] = text
        source_box = _strict_percent_box(region, minimum_width=0.25, minimum_height=0.25)
        first_pass_source_boxes.append(source_box)
        inputs.append({
            "index": index,
            "source_text": source_text.strip(),
            "korean_text": text.strip(),
            "source_phrase_box": source_box,
        })

    audit_prompt = f"""Audit in-place source-phrase replacement on the attached official creative.

Each source_phrase_box is an immutable anchor from a separate image-only discovery pass. Independently inspect the attached pixels and tighten it to the actual ORIGINAL lettering, but the final source_text boxes must substantially overlap that anchor and must never jump to another phrase. The renderer will isolate and content-aware reconstruct only the lettering/outline pixels inside the final audited box, then place korean_text directly in that exact phrase area with no caption panel. Korean must not be moved elsewhere.

Subtitles:
{json.dumps(inputs, ensure_ascii=False)}

Rules:
- Every coordinate in protected_regions MUST be an image-relative percentage from 0 to 100. NEVER return pixel coordinates.
- Independently transcribe each complete source phrase into verified_source_texts. After case, whitespace, and punctuation normalization it must still exactly match the supplied source_text for that source_index. Return safe=false if the pixels do not corroborate that phrase.
- Protected kind must be exactly one of: source_text, other_text, logo, character, face, limb, product, product_ui, token_icon, other_visual. Use other_visual for an ambiguous object that still needs protection. Never return kind=other.
- Map one tight protected_regions box per contiguous source-language phrase or important visual element, including its outline/shadow, official or partner logo, character, face, limb, product, product UI, and token icon. Return at most 32 protected boxes. Do not mark ordinary background texture or empty scenery as other_visual.
- protected_regions must include at least one kind=source_text box for each subtitle index, marked with that exact source_index. Every visible text row MUST use its own tight source_text box with the same source_index; never wrap two or more rows in one box. Same-row phrase fragments may use separate boxes. Use kind=other_text for any additional visible phrase that is not represented in Subtitles. Text printed inside a product or block still counts as protected text.
- Tighten each source_text box to the actual visible glyphs including outline and shadow while staying bound to source_phrase_box. Never move it to unrelated copy.
- Confirm korean_text can remain readable in the same line count and exact audited area. Preserve every subtitle index exactly once. Do not translate, rewrite, or reposition korean_text.
- Check the 1-3 source-pixel cleanup dilation. Return safe=false if it would touch any protected logo, face, product UI, token icon, unrelated text, ambiguous other_visual, or a separate important visual.
- A character, limb, or product already directly behind the original source lettering is allowed as the existing caption substrate. Keep safe=true and report it accurately so deterministic validation can confirm that at least 50% of the cleanup/object intersection was already inside the original phrase box. If cleanup would newly reach one that was not behind the lettering, return safe=false. An ambiguous other_visual is never a caption substrate and must remain fully protected.
- Korean must sit on a transparent layer over the reconstructed source visual. Never propose a rectangle, rounded panel, tint, gradient, blur patch, scrim, a second caption panel, or a separate footer.
- If any source phrase cannot be located confidently, cleanup lacks clearance, or Korean cannot fit its same area, return safe=false. Preserving the original creative unchanged is required.

Return exactly:
{{
  "safe": true,
  "verified_source_texts": [
    {{"source_index":0,"text":"the exact visible source phrase"}}
  ],
  "protected_regions": [
    {{"kind":"source_text","source_index":0,"x":35,"y":80,"width":30,"height":10}},
    {{"kind":"other_visual","x":35,"y":25,"width":30,"height":40}}
  ]
}}
or:
{{"safe":false,"verified_source_texts":[],"protected_regions":[]}}
"""

    retry_context = ""
    retained_retry_protections: Optional[list[dict]] = None
    terminal_failure_status: Optional[str] = None
    effective_max_calls = max(
        1,
        min(_MAX_VISUAL_PLACEMENT_AUDIT_CALLS, int(max_calls)),
    )
    for attempt_index in range(effective_max_calls):
        attempt_number = attempt_index + 1
        attempt_prompt = audit_prompt
        if retry_context:
            retry_map_instruction = (
                "The previous protection map was structurally valid. Re-map the "
                "whole image; its valid non-source protections will also be "
                "retained during deterministic validation."
                if retained_retry_protections is not None
                else
                "The previous protection map was missing or malformed and cannot "
                "be reused. Build a complete fresh map from the image while "
                "staying anchored to the same supplied source_phrase_box."
            )
            attempt_prompt = f"""This is the final bounded reinspection of the attached image.
Previous attempt outcome: {retry_context}
{retry_map_instruction}

Return a complete fresh audit. Do not copy the previous coordinates and do not assume the image is safe.
- Re-map every protected region from the image.
- Return safe=false if the source phrase or cleanup clearance remains uncertain.
- A safe=true answer will still be rejected unless every protected box passes deterministic geometry validation and the raster lettering probe.

{audit_prompt}"""

        print(
            "[squid] placement audit attempt "
            f"{attempt_number}/{effective_max_calls} model={model}"
        )
        try:
            timeout = _remaining_llm_timeout(
                deadline,
                _SQUID_VISUAL_CALL_MAX_SECONDS,
            )
            audit_response = create_message(
                api_client,
                model=model,
                max_tokens=1400,
                temperature=0,
                timeout=timeout,
                system=VISUAL_PLACEMENT_AUDIT_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": source_image.media_type,
                                "data": source_image.base64_data,
                            },
                        },
                        {"type": "text", "text": attempt_prompt},
                    ],
                }],
            )
            audit = _parse_json_response(
                audit_response,
                f"visual subtitle placement audit attempt {attempt_number}",
            )
        except Exception as exc:
            print(
                f"[squid] placement audit attempt {attempt_number} failed safely: "
                f"{type(exc).__name__}"
            )
            if attempt_number < effective_max_calls:
                retry_context = (
                    "the previous visual audit request failed before a valid "
                    "inspection could be returned"
                )
                continue
            return _clear_visual_localization(
                result,
                failure_status=terminal_failure_status,
            )

        if audit.get("safe") is False:
            print(
                f"[squid] placement audit attempt {attempt_number} "
                "returned explicit safe=false; stopping"
            )
            break
        if attempt_number > 1 and retained_retry_protections is not None:
            audit = _merge_retry_protections(
                audit,
                retained_retry_protections,
            )

        print(
            f"[squid] placement audit attempt {attempt_number} proposal: "
            f"{json.dumps(_audit_log_payload(audit), ensure_ascii=True)}"
        )
        retry_protections = _schema_valid_retry_protections(
            audit,
            region_count=len(raw_regions),
        )
        audited_regions, rejection = _validate_visual_placement_audit(
            raw_regions,
            first_pass_source_boxes,
            audit,
            source_image,
            require_source_identity=raster_probe,
        )
        if audited_regions is None:
            retry_context = (
                "the independent inspection returned safe=false"
                if rejection == "unsafe"
                else f"deterministic protected-region validation rejected it: {rejection}"
            )
            if attempt_number < effective_max_calls:
                retained_retry_protections = retry_protections
                print(
                    f"[squid] placement audit attempt {attempt_number} rejected: "
                    f"{rejection}; using final bounded "
                    f"{'protected-map' if retry_protections is not None else 'fresh-map'} "
                    "attempt"
                )
                continue
            if rejection != "unsafe":
                print(f"[squid] placement audit rejected safely: {rejection}")
            break

        if raster_probe:
            try:
                probe = probe_source_text(source_image, audited_regions)
                print(
                    "[squid] placement raster probe accepted on attempt "
                    f"{attempt_number}: {probe.masked_pixels} pixels"
                )
            except SourceTextCleanupError as exc:
                terminal_failure_status = "cleanup_failed"
                retry_context = (
                    "deterministic raster probing could not isolate the complete "
                    "source lettering inside the proposed boxes"
                )
                print(
                    "[squid] placement raster probe rejected safely on attempt "
                    f"{attempt_number}: {exc}"
                )
                if (
                    attempt_number < effective_max_calls
                    and retry_protections is not None
                ):
                    retained_retry_protections = retry_protections
                    continue
                break
            except Exception as exc:
                print(
                    "[squid] placement raster probe failed closed on attempt "
                    f"{attempt_number}: {type(exc).__name__}"
                )
                return _clear_visual_localization(
                    result,
                    failure_status="cleanup_failed",
                )

        result.pop(_VISUAL_LOCALIZATION_FAILURE_KEY, None)
        result["translation_regions"] = audited_regions
        result["source_text_visible"] = True
        return result

    return _clear_visual_localization(
        result,
        failure_status=terminal_failure_status,
    )


def _normalize_visual_audit_metadata(
    raw: dict,
    *,
    region_index: int,
    region_count: int,
) -> Optional[dict]:
    """Copy only complete validator-produced metadata into render/cache regions."""
    metadata_present = any(key in raw for key in _VISUAL_AUDIT_PRIVATE_KEYS)
    if not metadata_present:
        return None
    raw_source_index = raw.get("_source_index")
    raw_line_count = raw.get("_source_line_count")
    raw_protected = raw.get("_protected_regions")
    if (
        isinstance(raw_source_index, bool)
        or not isinstance(raw_source_index, int)
        or raw_source_index != region_index
        or isinstance(raw_line_count, bool)
        or not isinstance(raw_line_count, int)
        or raw_line_count < 1
        or raw_line_count > 4
        or not isinstance(raw_protected, list)
        or not raw_protected
        or len(raw_protected) > 32
    ):
        return None

    protected_regions: list[dict] = []
    protected_source_indexes: set[int] = set()
    for raw_region in raw_protected:
        if not isinstance(raw_region, dict):
            return None
        kind = raw_region.get("kind")
        if kind not in _PLACEMENT_PROTECTED_KINDS:
            return None
        box = _strict_percent_box(
            raw_region,
            minimum_width=0.25,
            minimum_height=0.25,
        )
        if box is None:
            return None
        protected_region = {"kind": kind, **box}
        if kind == "source_text":
            protected_source_index = raw_region.get("source_index")
            if (
                isinstance(protected_source_index, bool)
                or not isinstance(protected_source_index, int)
                or protected_source_index < 0
                or protected_source_index >= region_count
            ):
                return None
            protected_region["source_index"] = protected_source_index
            protected_source_indexes.add(protected_source_index)
        protected_regions.append(protected_region)

    if protected_source_indexes != set(range(region_count)):
        return None
    return {
        "_source_index": raw_source_index,
        "_source_line_count": raw_line_count,
        "_protected_regions": protected_regions,
    }


def _normalize_visual_localization(
    result: dict,
    client_id: str,
    has_source_image: bool,
    source_image_width: int = 1080,
    source_image_height: int = 1080,
    *,
    require_audit_metadata: bool = False,
) -> dict:
    """Keep Squid visual translation regions bounded and renderer-safe."""
    enabled = (
        client_id == "squid"
        and has_source_image
        and result.get("source_text_visible") is True
    )
    normalized_regions: list[dict] = []
    invalid_regions = False
    raw_regions = result.get("translation_regions")
    if enabled and isinstance(raw_regions, list):
        if len(raw_regions) > 4:
            invalid_regions = True
        for region_index, raw in enumerate(raw_regions):
            if invalid_regions:
                break
            if not isinstance(raw, dict):
                invalid_regions = True
                break
            text = raw.get("text")
            if not isinstance(text, str) or not text.strip():
                invalid_regions = True
                break
            source_text = raw.get("source_text")
            if not isinstance(source_text, str) or not source_text.strip():
                invalid_regions = True
                break
            source_text = source_text.strip()
            source_lines = [line.strip() for line in source_text.splitlines() if line.strip()]
            translation_lines = [line.strip() for line in text.splitlines() if line.strip()]
            audit_metadata = _normalize_visual_audit_metadata(
                raw,
                region_index=region_index,
                region_count=len(raw_regions),
            )
            if require_audit_metadata and audit_metadata is None:
                invalid_regions = True
                break
            if audit_metadata is not None:
                # OCR/newline formatting can vary for identical pixels. Reflow
                # Korean deterministically from the audited visual row count
                # before renderer and Figma fit checks.
                text = _canonicalize_translation_rows(
                    text,
                    audit_metadata["_source_line_count"],
                )
                translation_lines = [
                    line.strip() for line in text.splitlines() if line.strip()
                ]
            if (
                not source_lines
                or not translation_lines
                or len(translation_lines) > 2
                or (
                    audit_metadata is None
                    and len(source_lines) <= 2
                    and len(translation_lines) != len(source_lines)
                )
            ):
                invalid_regions = True
                break

            target_box = _strict_percent_box(raw, minimum_width=6.0, minimum_height=3.0)
            source_box = _strict_percent_box({
                "x": raw.get("source_x"),
                "y": raw.get("source_y"),
                "width": raw.get("source_width"),
                "height": raw.get("source_height"),
            }, minimum_width=6.0, minimum_height=3.0)
            if target_box is None or source_box is None:
                invalid_regions = True
                break
            if audit_metadata is None:
                if any(
                    abs(target_box[key] - source_box[key]) > 0.01
                    for key in ("x", "y", "width", "height")
                ):
                    invalid_regions = True
                    break
            else:
                # In the production audited path, source_* deliberately keeps
                # the full image-only discovery phrase for raster cleanup while
                # x/y/width/height keeps the tighter final Korean placement.
                overlap_width = max(
                    0.0,
                    min(
                        target_box["x"] + target_box["width"],
                        source_box["x"] + source_box["width"],
                    ) - max(target_box["x"], source_box["x"]),
                )
                overlap_height = max(
                    0.0,
                    min(
                        target_box["y"] + target_box["height"],
                        source_box["y"] + source_box["height"],
                    ) - max(target_box["y"], source_box["y"]),
                )
                if (
                    overlap_width / max(0.001, min(target_box["width"], source_box["width"])) < 0.60
                    or overlap_height / max(0.001, min(target_box["height"], source_box["height"])) < 0.45
                    or overlap_width / max(0.001, source_box["width"]) < 0.45
                    or overlap_height / max(0.001, source_box["height"]) < 0.55
                    or (
                        overlap_width * overlap_height
                        / max(0.001, source_box["width"] * source_box["height"])
                    ) < 0.27
                ):
                    invalid_regions = True
                    break

            raw_x = target_box["x"]
            raw_y = target_box["y"]
            font_size = max(2.8, min(12.0, _number(raw.get("font_size"), 5.2)))
            raw_width = target_box["width"]
            raw_height = target_box["height"]
            align = raw.get("align") if raw.get("align") in _REGION_ALIGNMENTS else "left"
            scale_x = 1.0
            translation_units = _text_width_units(text.strip())
            if translation_units > 0:
                estimated_width = max(0.1, translation_units * font_size * 0.60)
                scale_x = max(0.9, min(1.35, raw_width * 0.96 / estimated_width))

            # Match the renderer's 2%-of-image-width minimum font and reject
            # regions that could still disappear after its deterministic shrink.
            minimum_css_font_percent = _minimum_squid_font_percent(
                source_image_width,
                source_image_height,
            )
            widest_line_units = max((_text_width_units(line) for line in translation_lines), default=0.0)
            minimum_rendered_width = widest_line_units * minimum_css_font_percent * 1.05 * scale_x
            source_ratio = source_image_width / max(1, source_image_height)
            minimum_rendered_height = (
                len(translation_lines) * minimum_css_font_percent * 1.02 * source_ratio
            )
            if (
                minimum_rendered_width > raw_width * 0.98
                or minimum_rendered_height > raw_height * 0.98
            ):
                invalid_regions = True
                break

            font_role = raw.get("font_role") if raw.get("font_role") in _REGION_FONT_ROLES else "display"
            text_color = raw.get("text_color")

            candidate = {
                "source_text": source_text[:240],
                "text": text.strip()[:240],
                "x": round(raw_x, 2),
                "y": round(raw_y, 2),
                "width": round(raw_width, 2),
                "height": round(raw_height, 2),
                "source_x": round(source_box["x"], 2),
                "source_y": round(source_box["y"], 2),
                "source_width": round(source_box["width"], 2),
                "source_height": round(source_box["height"], 2),
                "align": align,
                "font_role": font_role,
                "font_size": round(font_size, 2),
                "scale_x": round(scale_x, 2),
                "text_color": text_color.upper() if isinstance(text_color, str) and _HEX_COLOR.match(text_color) else "#FFFFFF",
                "source_line_count": (
                    audit_metadata["_source_line_count"]
                    if audit_metadata is not None
                    else min(2, len(source_lines))
                ),
            }
            if audit_metadata is not None:
                candidate.update(audit_metadata)
            overlaps_existing = any(
                candidate["x"] < existing["x"] + existing["width"]
                and candidate["x"] + candidate["width"] > existing["x"]
                and candidate["y"] < existing["y"] + existing["height"]
                and candidate["y"] + candidate["height"] > existing["y"]
                for existing in normalized_regions
            )
            if overlaps_existing:
                invalid_regions = True
                break
            normalized_regions.append(candidate)

    if invalid_regions:
        normalized_regions = []

    result["translation_regions"] = normalized_regions
    result["source_text_visible"] = bool(normalized_regions)
    result["source_crop_bottom"] = 100.0
    return result


def _stamp_visual_localization_status(
    result: dict,
    client_id: str,
    has_source_image: bool,
    had_detected_copy: bool,
) -> dict:
    """Tell the console whether copy was absent or rejected by placement QA."""
    failure_status = result.pop(_VISUAL_LOCALIZATION_FAILURE_KEY, None)
    if client_id == "squid" and has_source_image:
        if result.get("source_text_visible") is True:
            result["visual_localization_status"] = "translated"
        elif failure_status == "cleanup_failed":
            result["visual_localization_status"] = "cleanup_failed"
        elif had_detected_copy:
            result["visual_localization_status"] = "unsafe_placement"
        else:
            result["visual_localization_status"] = "no_text"
    return result


# ────────────────────────────────────────────────────
# Main Pipeline Function
# ────────────────────────────────────────────────────

def generate_news_card_spec(
    client_id: str,
    source_content: str,
    source_type: Literal["tweet", "blog", "article"] = "tweet",
    source_url: str = "",
    mock_mode: bool = False,
    mock_response: Optional[dict] = None,
    source_image: Optional[PreparedSourceImage] = None,
    cached_visual_localization: Optional[list[dict]] = None,
) -> dict:
    """
    Generate a news card spec for a given client.

    Uses the client's LLM config (model, temperature, preserve_terms, glossary, tone).

    Returns:
        {
            "label": str,
            "date": str,           # YYYY.MM.DD
            "headline": str,
            "body_lines": list[str],  # 1-3 items
            "source_url": str,
            "theme": "dark" | "yellow",
            "source_logo_visible": bool
        }
    """
    if mock_mode:
        result = dict(mock_response or _get_default_mock(client_id))
        result.pop(_VISUAL_LOCALIZATION_FAILURE_KEY, None)
        had_detected_copy = (
            result.get("source_text_visible") is True
            and isinstance(result.get("translation_regions"), list)
            and bool(result["translation_regions"])
        )
        result = _normalize_visual_localization(
            result,
            client_id,
            source_image is not None,
            source_image.width if source_image is not None else 1080,
            source_image.height if source_image is not None else 1080,
        )
        result = _stamp_visual_localization_status(
            result,
            client_id,
            source_image is not None,
            had_detected_copy,
        )
        result = enforce_client_display_name(client_id, result)
        result["source_logo_visible"] = (
            result.get("source_logo_visible") is True
            if source_image is not None
            else False
        )
        return result

    config = get_client_config(client_id)
    llm_cfg = config.llm.news_card

    prompt = _build_user_prompt(
        config,
        source_content,
        source_type,
        source_url,
        has_source_image=source_image is not None,
    )

    try:
        from anthropic import Anthropic
    except ImportError:
        raise ImportError("pip install anthropic")

    client = Anthropic()
    # The SDK retries timeouts by default, which can silently multiply an 8s
    # visual call past Netlify's 55s upstream limit. Visual discovery/audit has
    # its own bounded retry policy, so keep each SDK request single-attempt.
    if client_id == "squid" and source_image is not None and hasattr(client, "with_options"):
        client = client.with_options(max_retries=0)
    visual_deadline = (
        time.monotonic() + _SQUID_VISUAL_LLM_BUDGET_SECONDS
        if client_id == "squid" and source_image is not None
        else None
    )

    message_content: str | list[dict] = prompt
    if source_image is not None:
        message_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": source_image.media_type,
                    "data": source_image.base64_data,
                },
            },
            {"type": "text", "text": prompt},
        ]

    response = create_message(
        client,
        model=llm_cfg.model,
        max_tokens=1500,
        temperature=llm_cfg.temperature,
        timeout=_remaining_llm_timeout(
            visual_deadline,
            _SQUID_MAIN_LLM_MAX_SECONDS,
            reserve=_SQUID_VISUAL_CALL_RESERVE_SECONDS * 2,
        ),
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": message_content}],
    )

    result = _parse_json_response(response, "news card generation")
    result.pop(_VISUAL_LOCALIZATION_FAILURE_KEY, None)
    had_detected_copy = (
        result.get("source_text_visible") is True
        and isinstance(result.get("translation_regions"), list)
        and bool(result["translation_regions"])
    )

    cache_hit = False
    if (
        client_id == "squid"
        and source_image is not None
        and isinstance(cached_visual_localization, list)
        and cached_visual_localization
    ):
        cached = _normalize_visual_localization(
            {
                "source_text_visible": True,
                "translation_regions": copy.deepcopy(cached_visual_localization),
            },
            client_id,
            True,
            source_image.width,
            source_image.height,
            require_audit_metadata=True,
        )
        if cached.get("source_text_visible") is True:
            result["source_text_visible"] = True
            result["translation_regions"] = cached["translation_regions"]
            result["_visual_localization_cache_hit"] = True
            had_detected_copy = True
            cache_hit = True
            print("[squid] validated visual localization cache hit; placement audit skipped")

    if client_id == "squid" and source_image is not None and not cache_hit:
        # The sampled creative-writing model never owns destructive image
        # geometry. A temperature-zero, image-only pass always replaces its
        # visual OCR so a valid-but-partial first answer cannot make cold
        # requests alternate between translated and unchanged.
        result, discovery_calls = _discover_visual_copy(
            client,
            VISUAL_PLACEMENT_AUDIT_MODEL,
            result,
            source_image,
            llm_cfg.preserve_terms,
            deadline=visual_deadline,
        )
        had_detected_copy = (
            result.get("source_text_visible") is True
            and isinstance(result.get("translation_regions"), list)
            and bool(result["translation_regions"])
        )
        if had_detected_copy:
            result = _audit_visual_subtitle_placement(
                client,
                VISUAL_PLACEMENT_AUDIT_MODEL,
                result,
                source_image,
                raster_probe=True,
                max_calls=max(
                    1,
                    _MAX_SQUID_STABLE_VISUAL_CALLS - discovery_calls,
                ),
                deadline=visual_deadline,
            )

    # Force-stamp source_url: LLM occasionally truncates or normalizes URLs;
    # the caller's URL is the source of truth.
    result["source_url"] = source_url
    result = _normalize_visual_localization(
        result,
        client_id,
        source_image is not None,
        source_image.width if source_image is not None else 1080,
        source_image.height if source_image is not None else 1080,
        require_audit_metadata=(client_id == "squid" and source_image is not None),
    )
    result = _stamp_visual_localization_status(
        result,
        client_id,
        source_image is not None,
        had_detected_copy,
    )
    result = enforce_client_display_name(client_id, result)
    result["source_logo_visible"] = (
        result.get("source_logo_visible") is True
        if source_image is not None
        else False
    )

    _validate_result(result)
    return result


# ────────────────────────────────────────────────────
# Validation
# ────────────────────────────────────────────────────

VALID_THEMES = {"dark", "yellow"}
DATE_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
REQUIRED_KEYS = (
    "label",
    "date",
    "headline",
    "body_lines",
    "source_url",
    "theme",
    "source_logo_visible",
    "source_text_visible",
    "translation_regions",
)


def _validate_result(result: dict):
    for k in REQUIRED_KEYS:
        assert k in result, f"news_card: missing key '{k}'"

    assert isinstance(result["label"], str) and result["label"].strip(), \
        "news_card: 'label' must be non-empty string"
    assert isinstance(result["headline"], str) and result["headline"].strip(), \
        "news_card: 'headline' must be non-empty string"

    assert DATE_PATTERN.match(result["date"]), \
        f"news_card: 'date' must be YYYY.MM.DD, got '{result['date']}'"

    assert result["theme"] in VALID_THEMES, \
        f"news_card: 'theme' must be one of {VALID_THEMES}, got '{result['theme']}'"

    assert isinstance(result["source_logo_visible"], bool), \
        "news_card: 'source_logo_visible' must be bool"

    assert isinstance(result["source_text_visible"], bool), \
        "news_card: 'source_text_visible' must be bool"
    assert isinstance(result["translation_regions"], list), \
        "news_card: 'translation_regions' must be list"
    assert result["source_text_visible"] == bool(result["translation_regions"]), \
        "news_card: source_text_visible must match translation_regions"

    body = result["body_lines"]
    assert isinstance(body, list), "news_card: 'body_lines' must be list"
    assert 1 <= len(body) <= 3, f"news_card: body_lines must have 1-3 items, got {len(body)}"
    for i, line in enumerate(body):
        assert isinstance(line, str) and line.strip(), \
            f"news_card: body_lines[{i}] must be non-empty string"

    assert isinstance(result["source_url"], str), "news_card: 'source_url' must be string"


def _get_default_mock(client_id: str) -> dict:
    return {
        "label": "이더리움 메인넷 라이브",
        "date": "2026.06.23",
        "headline": "Yellow가 이더리움 메인넷에서 라이브됩니다",
        "body_lines": [
            "Nitrolite 상태 채널로 오프체인 정산",
            "모든 EVM 네트워크 지원",
        ],
        "source_url": "https://example.com",
        "theme": "dark",
        "source_logo_visible": False,
        "source_text_visible": False,
        "translation_regions": [],
    }
