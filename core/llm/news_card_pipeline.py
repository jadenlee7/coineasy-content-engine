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

import json
import math
import re
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

from core.brand_voice import build_brand_voice_prompt
from core.client_naming import enforce_client_display_name
from core.client_config import ClientConfig, get_client_config
from core.llm.anthropic_compat import create_message
from core.sources.source_image import PreparedSourceImage


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

VISUAL_TRANSLATION_REPAIR_SYSTEM_PROMPT = """You are a Korean localization editor for Squid social banners.
Translate only the supplied visible English marketing copy into concise, natural Korean.
Keep product names, token symbols, handles, URLs, and protected terms unchanged.
Preserve the original humor, claim strength, and short cadence. Do not add facts.
Return STRICT JSON ONLY in the requested schema. No markdown or commentary."""

VISUAL_PLACEMENT_AUDIT_SYSTEM_PROMPT = """You are the final visual placement QA for transparent Korean subtitles on official Squid creatives.
Inspect the attached image composition. Existing image pixels are never removed, so Korean subtitles must sit only in genuinely empty negative space.
Map every protected visual first, then place the supplied Korean subtitles without covering any original lettering, logo, character, face, limb, product, product UI, or token icon.
Return STRICT JSON ONLY in the requested schema. No markdown or commentary."""


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
- If meaningful copy exists, set source_text_visible=true and return 1-4 translation_regions. Translate only the visible copy into concise, natural Korean. Preserve the original claim strength, humor, capitalization intent, line hierarchy, product names, handles, numbers, and token symbols. Keep a 1-2 line source at the same line count; condense a 3+ line source to at most 2 lines. Keep approximately the same rendered width and retain a short prominent Latin keyword when it is part of the visual rhythm and remains natural in Korean.
- source_text must transcribe the visible source phrase exactly, including its line breaks. x/y/width/height must tightly cover that ORIGINAL source phrase and its outline or shadow. These coordinates are a source-detection box, not the final Korean subtitle placement. A separate image-aware safety pass chooses the Korean target box.
- Every translation_regions[].text containing meaningful English copy must contain Korean Hangul. Never copy the original English sentence back into text. English may remain only for protected product names, handles, URLs, numbers, token symbols, or a short keyword repeated in the source visual rhythm, inside an otherwise Korean translation.
- Choose display for large headline copy and body for supporting copy. font_size is a percentage of the source image width. Keep translation text to at most 2 lines.
- After this response, a separate visual safety audit preserves each source-detection box as protected, maps the remaining logo, character, product, and UI areas, and moves Korean into a clear target box of at least 24% x 12% with a 3% safety gap.
- The renderer preserves the full source crop and places audited Korean in a nearby clear area inside the original banner without covering source lettering. The subtitle background stays fully transparent with only a thin readability outline. Never request or imply a separate footer, Squid-colored caption area, gradient, solid caption box, blurred patch, thick text outline, panel, or chip.
- If the safety audit cannot place every translation, it removes every Korean overlay and preserves the official creative unchanged.
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
_ASCII_WORD = re.compile(r"[A-Za-z]{2,}")
_IDENTIFIER_TOKEN = re.compile(r"[A-Z0-9]{2,6}")
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


def _has_untranslated_english(text: str, preserve_terms: list[str]) -> bool:
    """Return true for English prose, including partial Korean translations."""
    tokens = re.findall(r"[A-Za-z0-9@#.$:/_-]+", text)
    if not tokens:
        return False
    protected = {term.strip().lower() for term in preserve_terms if term.strip()}
    for token in tokens:
        normalized = token.strip(".,:;!?()[]{}\"'")
        if not normalized:
            continue
        if normalized.lower() in protected:
            continue
        if normalized.startswith(("@", "#")) or "://" in normalized:
            continue
        if _IDENTIFIER_TOKEN.fullmatch(normalized):
            continue
        return True
    return not _HANGUL.search(text)


def _untranslated_region_indexes(result: dict, preserve_terms: list[str]) -> list[int]:
    """Find visual regions with untranslated or partially translated English."""
    if result.get("source_text_visible") is not True:
        return []
    regions = result.get("translation_regions")
    if not isinstance(regions, list):
        return []
    indexes: list[int] = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict) or not isinstance(region.get("text"), str):
            continue
        text = region["text"]
        if not _ASCII_WORD.search(text):
            continue
        source_keyword = _repeated_leading_keyword(region.get("source_text"))
        allowed_terms = [*preserve_terms, *([source_keyword] if source_keyword else [])]
        if _has_untranslated_english(text, allowed_terms):
            indexes.append(index)
    return indexes


def _repeated_leading_keyword(source_text: object) -> str | None:
    """Return a short Latin keyword that leads every line of a visual phrase."""
    if not isinstance(source_text, str):
        return None
    lines = [line.strip() for line in source_text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    matches = [re.match(r"^([A-Za-z][A-Za-z0-9_-]{1,23})\b", line) for line in lines]
    if any(match is None for match in matches):
        return None
    keywords = [match.group(1) for match in matches if match is not None]
    return keywords[0] if all(keyword.lower() == keywords[0].lower() for keyword in keywords) else None


def _is_protected_identifier_only(text: str, preserve_terms: list[str]) -> bool:
    """Allow a region to be removed when it contains identifiers, not prose."""
    tokens = re.findall(r"[A-Za-z0-9@#.$:/_-]+", text)
    if not tokens:
        return False
    protected = {term.strip().lower() for term in preserve_terms if term.strip()}
    for token in tokens:
        normalized = token.strip(".,:;!?()[]{}\"'")
        if not normalized:
            continue
        if normalized.lower() in protected:
            continue
        if normalized.startswith(("@", "#")) or "://" in normalized:
            continue
        if _IDENTIFIER_TOKEN.fullmatch(normalized):
            continue
        return False
    return True


def _repair_untranslated_visual_copy(
    api_client: object,
    model: str,
    result: dict,
    preserve_terms: list[str],
    source_content: str,
) -> dict:
    """Repair untranslated copy while asking the model to keep visual rhythm."""
    indexes = _untranslated_region_indexes(result, preserve_terms)
    if not indexes:
        return result

    regions = result.get("translation_regions")
    assert isinstance(regions, list)
    inputs = [{
        "index": index,
        "source_text": regions[index].get("source_text", ""),
        "text": regions[index]["text"],
    } for index in indexes]
    protected = ", ".join(preserve_terms) or "Squid"
    repair_prompt = f"""Correct untranslated text in a Squid banner.

Protected terms: {protected}
Source caption context (facts only):
<<<
{source_content.strip()[:2000]}
>>>

Translate every natural-language phrase below into brief, playful Korean suitable for the same banner position.
- Preserve the punchline and line hierarchy.
- Keep a 1-2 line source at the same non-empty line count; condense a 3+ line source to at most 2 lines. Keep approximately the same rendered width as source_text.
- When the same short Latin keyword leads multiple source_text lines, keep that keyword unchanged at the start of each corresponding Korean line.
- Keep protected terms, product names, token symbols, handles, URLs, and numbers unchanged.
- Each translated text must contain at least one Korean Hangul syllable.
- If an input is only a protected identifier or token pair with no natural-language copy, return an empty text for that index.
- Do not change, combine, omit, or reorder indexes.

Inputs:
{json.dumps(inputs, ensure_ascii=False)}

Return exactly:
{{"translations":[{{"index":0,"text":"한국어 번역"}}]}}
"""
    repair_response = create_message(
        api_client,
        model=model,
        max_tokens=600,
        temperature=0,
        system=VISUAL_TRANSLATION_REPAIR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": repair_prompt}],
    )
    repair = _parse_json_response(repair_response, "visual translation repair")
    translations = repair.get("translations")
    if not isinstance(translations, list):
        raise ValueError("LLM visual translation repair omitted translations")

    replacements: dict[int, str] = {}
    for item in translations:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        text = item.get("text")
        if isinstance(index, int) and not isinstance(index, bool) and index in indexes and isinstance(text, str):
            replacements[index] = text.strip()[:240]

    for index in indexes:
        original = regions[index]["text"]
        replacement = replacements.get(index, "")
        if replacement and _HANGUL.search(replacement):
            regions[index]["text"] = replacement
            continue
        if not replacement and _is_protected_identifier_only(original, preserve_terms):
            regions[index]["text"] = ""
            continue
        raise ValueError(f"LLM left visual copy untranslated at region {index}")

    result["translation_regions"] = [
        region for region in regions
        if isinstance(region, dict) and isinstance(region.get("text"), str) and region["text"].strip()
    ]
    result["source_text_visible"] = bool(result["translation_regions"])
    return result


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


def _clear_visual_localization(result: dict) -> dict:
    """Fail safe: preserve the official creative without any Korean overlay."""
    result["source_text_visible"] = False
    result["translation_regions"] = []
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


def _percent_boxes_overlap(
    first: dict[str, float],
    second: dict[str, float],
    *,
    margin: float = 0.0,
) -> bool:
    """Return whether two image-relative boxes overlap after expanding the second."""
    return (
        first["x"] < second["x"] + second["width"] + margin
        and first["x"] + first["width"] > second["x"] - margin
        and first["y"] < second["y"] + second["height"] + margin
        and first["y"] + first["height"] > second["y"] - margin
    )


def _audit_log_payload(audit: dict) -> dict:
    """Return coordinates and kinds only, without source or translated copy."""
    return {
        "safe": audit.get("safe"),
        "protected_regions": [
            {key: item.get(key) for key in ("kind", "source_index", "x", "y", "width", "height")}
            for item in audit.get("protected_regions", [])
            if isinstance(item, dict)
        ][:32] if isinstance(audit.get("protected_regions"), list) else "invalid",
        "translation_regions": [
            {key: item.get(key) for key in ("index", "x", "y", "width", "height")}
            for item in audit.get("translation_regions", [])
            if isinstance(item, dict)
        ][:4] if isinstance(audit.get("translation_regions"), list) else "invalid",
    }


def _validate_visual_placement_audit(
    raw_regions: list[dict],
    first_pass_source_boxes: list[dict[str, float]],
    audit: dict,
) -> tuple[Optional[list[dict]], str]:
    """Validate an audit without repairing geometry or weakening overlap rules."""
    if audit.get("safe") is False:
        return None, "unsafe"
    if audit.get("safe") is not True:
        return None, "safe must be a boolean"

    raw_protected = audit.get("protected_regions")
    raw_placements = audit.get("translation_regions")
    if not isinstance(raw_protected, list) or not raw_protected:
        return None, "protected_regions must be a non-empty array"
    if len(raw_protected) > 32:
        return None, "protected_regions exceeds 32 boxes"
    if not isinstance(raw_placements, list) or len(raw_placements) != len(raw_regions):
        return None, "translation_regions must contain every subtitle exactly once"

    protected: list[tuple[str, Optional[int], dict[str, float]]] = [
        ("source_text", index, box)
        for index, box in enumerate(first_pass_source_boxes)
    ]
    audited_source_counts = {index: 0 for index in range(len(raw_regions))}
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
            return None, f"protected_regions[{position}] must use 0-100 percentage coordinates"
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
            audited_source_counts[source_index] += 1
        protected.append((kind, source_index, box))
    if any(count < 1 for count in audited_source_counts.values()):
        return None, "every subtitle requires an audited source_text box"

    placements: dict[int, tuple[dict, dict[str, float]]] = {}
    for position, raw in enumerate(raw_placements):
        if not isinstance(raw, dict):
            return None, f"translation_regions[{position}] must be an object"
        index = raw.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index in placements:
            return None, f"translation_regions[{position}].index is invalid or duplicated"
        if index < 0 or index >= len(raw_regions):
            return None, f"translation_regions[{position}].index is out of range"
        box = _strict_percent_box(raw, minimum_width=24.0, minimum_height=12.0)
        if box is None:
            return None, f"translation_regions[{position}] must use valid 0-100 percentage coordinates"
        if box["x"] < 2.0 or box["y"] < 2.0 or box["x"] + box["width"] > 98.0 or box["y"] + box["height"] > 98.0:
            return None, f"translation_regions[{position}] violates the 2% canvas inset"
        if any(_percent_boxes_overlap(box, protected_box, margin=3.0) for _, _, protected_box in protected):
            return None, f"translation_regions[{position}] overlaps protected geometry"
        placements[index] = (raw, box)
    if set(placements) != set(range(len(raw_regions))):
        return None, "translation_regions omitted a subtitle index"

    accepted_boxes = [placements[index][1] for index in range(len(raw_regions))]
    for index, box in enumerate(accepted_boxes):
        if any(
            _percent_boxes_overlap(box, other, margin=3.0)
            for other in accepted_boxes[:index]
        ):
            return None, f"translation_regions[{index}] overlaps another subtitle"

    audited_regions: list[dict] = []
    for index, original in enumerate(raw_regions):
        _, box = placements[index]
        box_right = box["x"] + box["width"]
        align = "left" if box_right <= 50.0 else "right" if box["x"] >= 50.0 else "center"
        audited_regions.append({
            **original,
            **box,
            "align": align,
        })
    return audited_regions, ""


def _audit_visual_subtitle_placement(
    api_client: object,
    model: str,
    result: dict,
    source_image: PreparedSourceImage,
) -> dict:
    """Run a fresh image-aware placement pass and atomically accept only safe boxes."""
    raw_regions = result.get("translation_regions")
    if result.get("source_text_visible") is not True or not isinstance(raw_regions, list) or not raw_regions:
        return result

    if len(raw_regions) > 4:
        return _clear_visual_localization(result)

    inputs: list[dict] = []
    first_pass_source_boxes: list[dict[str, float]] = []
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
            return _clear_visual_localization(result)
        source_box = _strict_percent_box(region, minimum_width=0.25, minimum_height=0.25)
        if source_box is None:
            return _clear_visual_localization(result)
        first_pass_source_boxes.append(source_box)
        inputs.append({
            "index": index,
            "source_text": source_text.strip(),
            "korean_text": text.strip(),
            "protected_source_box": source_box,
        })

    audit_prompt = f"""Audit fresh subtitle placement on the attached official creative.

The original pixels remain visible. Korean is an additional subtitle, NOT replacement text. protected_source_box is the first vision pass's detected ORIGINAL lettering box. It is mandatory protected geometry, never a placement suggestion. Scan the remaining image from scratch for a new target.

Subtitles:
{json.dumps(inputs, ensure_ascii=False)}

Rules:
- Every x, y, width, and height in both protected_regions and translation_regions MUST be an image-relative percentage from 0 to 100. NEVER return pixel coordinates. Ensure x + width <= 100 and y + height <= 100.
- Protected kind must be exactly one of: source_text, other_text, logo, character, face, limb, product, product_ui, token_icon, other_visual. Use other_visual for an ambiguous object that still needs protection. Never return kind=other.
- First map one tight protected_regions box per contiguous source-language phrase or visual element, including its outline/shadow, official or partner logo, character, face, limb, product, product UI, and token icon. Return at most 32 protected boxes.
- protected_regions must include at least one kind=source_text box for each subtitle index, marked with that exact source_index. Separate lines may use separate boxes with the same source_index. Use kind=other_text for any additional visible phrase that is not represented in Subtitles. Text printed inside a product or block still counts as protected text.
- Then find genuinely blank or low-detail negative space for every Korean subtitle. Prefer a uniform left or right background away from the central subject.
- Inspect these common target shapes first, then adjust only when needed: left-middle (x=3,y=34,w=24,h=20), right-middle (x=73,y=34,w=24,h=20), top-center (x=30,y=3,w=40,h=14), left-upper (x=3,y=16,w=24,h=16), right-upper (x=73,y=16,w=24,h=16). Use one only when its actual pixels satisfy every safety rule.
- A translation box must be at least 24% wide and 12% high, fit at most 2 lines, stay at least 2% inside every image edge, and keep at least a 3% gap from every protected box and protected_source_box.
- Translation boxes must keep at least a 3% gap from one another.
- Never place Korean over the source phrase it translates. This would show English and Korean on top of each other.
- Never use a lower caption band merely because the original English is there. Never cover a logo, character, product, product UI, or icon.
- Preserve every subtitle index exactly once. Do not translate or rewrite korean_text.
- If the image has no safe negative-space box for every subtitle, return safe=false and empty translation_regions. Preserving the original creative unchanged is required.

Return exactly:
{{
  "safe": true,
  "protected_regions": [
    {{"kind":"source_text","source_index":0,"x":35,"y":80,"width":30,"height":10}},
    {{"kind":"other_visual","x":35,"y":25,"width":30,"height":40}}
  ],
  "translation_regions": [
    {{"index":0,"x":3,"y":3,"width":24,"height":12}}
  ]
}}
or:
{{"safe":false,"protected_regions":[],"translation_regions":[]}}
"""

    try:
        audit_response = create_message(
            api_client,
            model=model,
            max_tokens=1400,
            temperature=0,
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
                    {"type": "text", "text": audit_prompt},
                ],
            }],
        )
        audit = _parse_json_response(audit_response, "visual subtitle placement audit")
    except Exception as exc:
        print(f"[squid] placement audit failed safely: {type(exc).__name__}")
        return _clear_visual_localization(result)

    print(f"[squid] placement audit proposal: {json.dumps(_audit_log_payload(audit), ensure_ascii=True)}")
    audited_regions, rejection = _validate_visual_placement_audit(
        raw_regions,
        first_pass_source_boxes,
        audit,
    )

    if audited_regions is None and rejection != "unsafe":
        print(f"[squid] placement audit rejected: {rejection}; retrying once")
        correction_prompt = f"""Your previous placement audit failed deterministic validation:
{rejection}

Return a complete fresh audit after reinspecting the attached image. Do not reuse malformed coordinates.
- All coordinates must be image-relative percentages from 0 to 100, never pixels.
- Allowed protected kinds are exactly: source_text, other_text, logo, character, face, limb, product, product_ui, token_icon, other_visual.
- Use other_visual, never other, when an object is ambiguous.
- Return safe=false if you cannot satisfy the schema and every clearance rule.

{audit_prompt}"""
        try:
            correction_response = create_message(
                api_client,
                model=model,
                max_tokens=1400,
                temperature=0,
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
                        {"type": "text", "text": correction_prompt},
                    ],
                }],
            )
            corrected_audit = _parse_json_response(
                correction_response,
                "visual subtitle placement audit correction",
            )
            print(
                "[squid] placement audit correction: "
                f"{json.dumps(_audit_log_payload(corrected_audit), ensure_ascii=True)}"
            )
            audited_regions, rejection = _validate_visual_placement_audit(
                raw_regions,
                first_pass_source_boxes,
                corrected_audit,
            )
        except Exception as exc:
            print(f"[squid] placement audit correction failed safely: {type(exc).__name__}")
            return _clear_visual_localization(result)

    if audited_regions is None:
        if rejection != "unsafe":
            print(f"[squid] placement audit correction rejected safely: {rejection}")
        return _clear_visual_localization(result)

    result["translation_regions"] = audited_regions
    result["source_text_visible"] = True
    return result


def _normalize_visual_localization(
    result: dict,
    client_id: str,
    has_source_image: bool,
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
        for raw in raw_regions:
            if invalid_regions:
                break
            if not isinstance(raw, dict):
                invalid_regions = True
                break
            text = raw.get("text")
            if not isinstance(text, str) or not text.strip():
                invalid_regions = True
                break
            if len([line for line in text.splitlines() if line.strip()]) > 2:
                invalid_regions = True
                break

            raw_x = max(0.0, min(99.0, _number(raw.get("x"), 8.0)))
            raw_y = max(0.0, min(99.0, _number(raw.get("y"), 8.0)))
            font_size = max(2.0, min(12.0, _number(raw.get("font_size"), 5.2)))
            raw_width = max(1.0, min(100.0 - raw_x, _number(raw.get("width"), 84.0)))
            raw_height = max(1.0, min(100.0 - raw_y, _number(raw.get("height"), 20.0)))
            if raw_width < 24.0 or raw_height < 12.0:
                invalid_regions = True
                break
            align = raw.get("align") if raw.get("align") in _REGION_ALIGNMENTS else "left"
            scale_x = 1.0
            source_text = raw.get("source_text")
            if isinstance(source_text, str) and source_text.strip():
                source_text = source_text.strip()
                translation_units = _text_width_units(text.strip())
                if translation_units > 0:
                    scale_x = max(0.85, min(1.35, _text_width_units(source_text) / translation_units))

            font_role = raw.get("font_role") if raw.get("font_role") in _REGION_FONT_ROLES else "display"
            text_color = raw.get("text_color")

            candidate = {
                "text": text.strip()[:240],
                "x": round(raw_x, 2),
                "y": round(raw_y, 2),
                "width": round(raw_width, 2),
                "height": round(raw_height, 2),
                "align": align,
                "font_role": font_role,
                "font_size": round(font_size, 2),
                "scale_x": round(scale_x, 2),
                "text_color": text_color.upper() if isinstance(text_color, str) and _HEX_COLOR.match(text_color) else "#FFFFFF",
            }
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
    if client_id == "squid" and has_source_image:
        if result.get("source_text_visible") is True:
            result["visual_localization_status"] = "translated"
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
        had_detected_copy = (
            result.get("source_text_visible") is True
            and isinstance(result.get("translation_regions"), list)
            and bool(result["translation_regions"])
        )
        result = _normalize_visual_localization(
            result,
            client_id,
            source_image is not None,
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
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": message_content}],
    )

    result = _parse_json_response(response, "news card generation")
    had_detected_copy = (
        result.get("source_text_visible") is True
        and isinstance(result.get("translation_regions"), list)
        and bool(result["translation_regions"])
    )

    if client_id == "squid" and source_image is not None:
        result = _repair_untranslated_visual_copy(
            client,
            llm_cfg.model,
            result,
            llm_cfg.preserve_terms,
            source_content,
        )
        # A repair may discard an identifier-only region. Status should reflect
        # whether natural-language copy still reached placement QA.
        had_detected_copy = (
            result.get("source_text_visible") is True
            and isinstance(result.get("translation_regions"), list)
            and bool(result["translation_regions"])
        )
        result = _audit_visual_subtitle_placement(
            client,
            llm_cfg.model,
            result,
            source_image,
        )

    # Force-stamp source_url: LLM occasionally truncates or normalizes URLs;
    # the caller's URL is the source of truth.
    result["source_url"] = source_url
    result = _normalize_visual_localization(
        result,
        client_id,
        source_image is not None,
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
