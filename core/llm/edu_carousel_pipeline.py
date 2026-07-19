"""
Education Carousel LLM Pipeline (Multi-tenant)

Takes a client's content source (tweet, blog, article) and produces a 
3-5 slide Korean education carousel data structure, using client-specific
branding, glossary, and tone guidance.

USAGE:
    from core.llm.edu_carousel_pipeline import generate_carousel_spec
    
    spec = generate_carousel_spec(
        client_id="yellow",
        source_content="Yellow is chain-agnostic...",
        source_type="tweet",
        source_url="https://x.com/Yellow/status/...",
    )
    # → {series: {...}, lessons: [{layout, slots}, ...]}
"""

from __future__ import annotations

import json
import re
from typing import Literal, Optional

from core.client_naming import enforce_client_display_name
from core.client_config import ClientConfig, get_client_config
from core.llm.anthropic_compat import create_message


# ────────────────────────────────────────────────────
# System Prompt (client-agnostic)
# ────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the content brain for a Web3 educational carousel system 
serving Korean audiences.

Your job: Take an English source (blog post, tweet, or article) about a blockchain / 
Web3 product, and produce a Korean educational carousel (3-5 slides) that a complete 
crypto beginner can understand.

Return STRICT JSON ONLY. No markdown fences, no prose, no commentary."""


# ────────────────────────────────────────────────────
# User Prompt Builder (injects client-specific config)
# ────────────────────────────────────────────────────

BASE_USER_PROMPT = """# Education Carousel Pipeline

## 1. Your Output

A JSON document describing 3-5 carousel slides in Korean.
Each slide has a layout pattern + filled slot values.

## 2. Carousel Design Philosophy

- 최대한 짧고 심플하게, 초보자들이 이해 쉽게
- 3-5 slides MAXIMUM (prefer 4)
- Each slide covers ONE concept. Not more.
- Progressive disclosure: slide 1 defines topic, later slides add depth
- Last slide ALWAYS wraps up with P4_SUMMARY (Keyword box) or similar

## 3. Available Layouts

### P1_3CARD — 3개 컨셉 병렬 비교/나열
Use when: "What is X?" with 3 essential components
Slots: lesson_title_kr, lesson_body_kr, cards[3]{{title_kr}}

### P2_BULLETS — 3-4개 아이콘 불릿 리스트 · 가장 많이 쓰는 기본형
Use when: listing features, capabilities, points
Slots: lesson_title_kr, lesson_body_kr, bullets[3-4]{{text_kr}}

### P3_BEFORE_AFTER — 이전/현재 비교
Use when: explaining a CHANGE or comparing two approaches
Slots: lesson_title_kr, lesson_body_kr, 
       before{{state_label, description}},
       after{{state_label, description}},
       conclusion_kr

### P4_SUMMARY — 요약 + Keyword 박스 · 마지막 슬라이드
Use when: final slide wrapping up the series
Slots: lesson_title_kr, 
       summary_style: "bullets" | "numbered",
       summary_items[2-3]{{text_kr}},
       keywords[1-2]{{term, definition_kr}}
RULE: Last slide of 4+ carousel should almost always be P4_SUMMARY

### P5_COVER — 시리즈 커버 슬라이드 (옵션)
Use only for major topic series worth introducing
Slots: eyebrow_kr, series_number, series_title_en, cover_title_kr, summary_kr, hint_kr

### P6_STEP — 1→2→3→4 순차 프로세스
Use when: explaining a sequence or workflow
Slots: lesson_title_kr, lesson_body_kr, steps[3-5]{{title_kr, desc_kr?, is_final?}}

### P7_DEFINITION — 단일 용어 정의 중심
Use when: one term needs detailed explanation
Slots: term_en, term_pos (optional), term_definition_kr,
       details[2-3]{{label, content_kr}}, insight_kr (optional)

### P8_DIAGRAM — Layer 구조 시각화
Use when: showing architectural stack or layered relationship
Slots: lesson_title_kr, lesson_body_kr,
       layers[3-4]{{tag, title_kr, subtitle_kr?, style}},
       caption_kr (optional)
style: "highlight" (yellow) | "emphasis" (black) | "base" (gray)

## 4. Theme Selection

- **dark** — 기술·인프라·프로토콜·구조 설명 · 기본값
- **yellow** — 제품 변화·기능 업데이트·UI 변경·중요 공지

## 5. Korean Translation Rules

- 경어체 (합니다/습니다), NOT 반말, NOT 하십시오체
- Preserve these terms as-is (keep English):
{preserve_terms_block}

- Glossary translations:
{glossary_block}

- Tone guidance: {tone_guidance}

- Length budgets (Korean chars):
  - series_title_en: 20-40 chars (English kept)
  - lesson_title_kr: 10-25 chars (punchy)
  - lesson_body_kr: 40-120 chars (1-3 sentences)
  - bullet text_kr: 8-30 chars
  - card title_kr: 5-18 chars (can use <br> for line break)

## 6. Carousel Structure Recommendation

For most blog/tweet content, use this pattern:
- **Slide 1** (P1/P2): "X는?" — define topic
- **Slide 2** (P2): "무엇을 가능하게?" — capabilities
- **Slide 3** (P3/P6/P8): "어떻게?" — how it works
- **Slide 4** (P4): "한 문장으로 정리하면" — summary + keywords

Adjust to content. Minimum 3 slides, max 5.

## 7. Input

Client: {client_name} ({client_id})
Source type: {source_type}
Source URL: {source_url}

Source content:
<<<
{source_content}
>>>

## 8. Output Format (STRICT JSON)

{{
  "series": {{
    "series_number": "...",
    "series_title_en": "Short English topic title",
    "series_subtitle_kr": "15-30 char 한글 서브타이틀",
    "theme": "dark" | "yellow"
  }},
  "lessons": [
    {{
      "lesson_number": 1,
      "layout": "P1_3CARD" | "P2_BULLETS" | ...,
      "theme": "dark" | "yellow",
      "slots": {{...layout-specific slots...}}
    }}
  ],
  "reasoning": "1-2 sentences: why these layouts and structure"
}}

DO NOT include icon_svg fields — renderer injects defaults.

## 9. Now Process This Source

Return JSON only. No markdown. No prose. No code fences.
"""


def _build_user_prompt(
    config: ClientConfig,
    source_content: str,
    source_type: str,
    source_url: str,
) -> str:
    """Build the client-specific user prompt."""
    llm = config.llm.edu_carousel
    
    # Preserve terms block
    if llm.preserve_terms:
        preserve_block = "\n".join(f"  - {t}" for t in llm.preserve_terms)
    else:
        preserve_block = "  (none specified)"
    
    # Glossary block
    if llm.glossary:
        glossary_block = "\n".join(
            f"  - \"{en}\" → \"{ko}\"" for en, ko in llm.glossary.items()
        )
    else:
        glossary_block = "  (use standard crypto Korean terminology)"
    
    tone = llm.tone_guidance or "professional but approachable, 경어체"
    
    return BASE_USER_PROMPT.format(
        preserve_terms_block=preserve_block,
        glossary_block=glossary_block,
        tone_guidance=tone,
        client_name=config.name,
        client_id=config.client_id,
        source_type=source_type,
        source_url=source_url or "(none)",
        source_content=source_content.strip(),
    )


# ────────────────────────────────────────────────────
# Main Pipeline Function
# ────────────────────────────────────────────────────

def generate_carousel_spec(
    client_id: str,
    source_content: str,
    source_type: Literal["tweet", "blog", "article"] = "tweet",
    source_url: str = "",
    series_number: Optional[str] = None,
    mock_mode: bool = False,
    mock_response: Optional[dict] = None,
) -> dict:
    """
    Generate a carousel spec (metadata + lessons) for a given client.
    
    Uses the client's LLM config (model, temperature, preserve_terms, glossary, tone).
    
    Returns:
        {
            "series": {series_number, series_title_en, series_subtitle_kr, theme},
            "lessons": [{lesson_number, layout, theme, slots}, ...],
            "reasoning": "..."
        }
    """
    if mock_mode:
        return enforce_client_display_name(
            client_id,
            mock_response or _get_default_mock(client_id),
        )
    
    config = get_client_config(client_id)
    llm_cfg = config.llm.edu_carousel
    
    prompt = _build_user_prompt(config, source_content, source_type, source_url)
    
    try:
        from anthropic import Anthropic
    except ImportError:
        raise ImportError("pip install anthropic")
    
    client = Anthropic()
    
    response = create_message(
        client,
        model=llm_cfg.model,
        max_tokens=4000,
        temperature=llm_cfg.temperature,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    
    raw_text = response.content[0].text.strip()
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)
    
    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {raw_text[:500]}") from e
    
    if series_number:
        result["series"]["series_number"] = series_number

    result = enforce_client_display_name(client_id, result)
    
    _validate_result(result)
    return result


# ────────────────────────────────────────────────────
# Validation
# ────────────────────────────────────────────────────

VALID_LAYOUTS = {
    "P1_3CARD", "P2_BULLETS", "P3_BEFORE_AFTER", "P4_SUMMARY",
    "P5_COVER", "P6_STEP", "P7_DEFINITION", "P8_DIAGRAM",
}
VALID_THEMES = {"dark", "yellow"}

REQUIRED_SLOTS = {
    "P1_3CARD": ["lesson_title_kr", "lesson_body_kr", "cards"],
    "P2_BULLETS": ["lesson_title_kr", "lesson_body_kr", "bullets"],
    "P3_BEFORE_AFTER": ["lesson_title_kr", "before", "after"],
    "P4_SUMMARY": ["lesson_title_kr", "summary_items"],
    "P5_COVER": ["cover_title_kr"],
    "P6_STEP": ["lesson_title_kr", "steps"],
    "P7_DEFINITION": ["term_en", "term_definition_kr"],
    "P8_DIAGRAM": ["lesson_title_kr", "layers"],
}


def _validate_result(result: dict):
    assert "series" in result, "series missing"
    assert "lessons" in result, "lessons missing"
    assert 3 <= len(result["lessons"]) <= 5, f"Expected 3-5 lessons, got {len(result['lessons'])}"
    
    series = result["series"]
    assert series.get("theme") in VALID_THEMES, f"Invalid theme: {series.get('theme')}"
    
    for i, lesson in enumerate(result["lessons"]):
        layout = lesson.get("layout")
        assert layout in VALID_LAYOUTS, f"Lesson {i+1}: invalid layout {layout}"
        slots = lesson.get("slots", {})
        for req in REQUIRED_SLOTS[layout]:
            assert req in slots, f"Lesson {i+1} ({layout}): missing slot '{req}'"


def _get_default_mock(client_id: str) -> dict:
    return {
        "series": {
            "series_number": "99",
            "series_title_en": "Mock Series",
            "series_subtitle_kr": f"{client_id} 테스트 목업",
            "theme": "dark",
        },
        "lessons": [{
            "lesson_number": 1,
            "layout": "P2_BULLETS",
            "theme": "dark",
            "slots": {
                "lesson_title_kr": "목업 레슨",
                "lesson_body_kr": "테스트용 목업입니다.",
                "bullets": [
                    {"text_kr": "포인트 1"},
                    {"text_kr": "포인트 2"},
                    {"text_kr": "포인트 3"},
                ],
            },
        }],
        "reasoning": f"Mock mode for {client_id}",
    }
