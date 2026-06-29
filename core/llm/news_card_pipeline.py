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
import re
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

from core.client_config import ClientConfig, get_client_config


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

## 3. Output Schema (FIXED — do not add or remove keys)

{{
  "label": "뉴스 분류 배지 (4-15자)",
  "date": "YYYY.MM.DD",
  "headline": "메인 헤드라인 한 문장 (15-40자, 경어체)",
  "body_lines": ["불릿 1 (10-30자)", "불릿 2 (10-30자)", "불릿 3 (옵션)"],
  "source_url": "원본 URL (입력값 그대로)",
  "theme": "dark" | "yellow"
}}

Rules:
- label: 짧은 분류 텍스트. 예: "메인넷 라이브", "파트너십", "기능 업데이트", "신규 상장"
- date: YYYY.MM.DD 형식 (점 구분). 본문에 명시된 날짜가 있으면 그것을, 없으면 오늘({today_date}).
- headline: 한 문장. 경어체 (합니다/됩니다/입니다). 주체+동사가 명확해야 함.
- body_lines: 1-3개 배열. 각 줄은 헤드라인을 뒷받침하는 구체 사실. 중복 금지.
- source_url: 입력 source_url을 그대로 옮길 것. 생성/변경 금지 (시스템이 사후 보정).
- theme: 아래 4번 규칙대로.

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

## 7. Output Format (STRICT JSON)

Return JSON only. No markdown. No prose. No code fences.

{{
  "label": "...",
  "date": "YYYY.MM.DD",
  "headline": "...",
  "body_lines": ["...", "..."],
  "source_url": "{source_url}",
  "theme": "dark" | "yellow"
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

    return BASE_USER_PROMPT.format(
        preserve_terms_block=preserve_block,
        glossary_block=glossary_block,
        tone_guidance=tone,
        client_name=config.name,
        client_id=config.client_id,
        source_type=source_type,
        source_url=source_url or "(none)",
        source_content=source_content.strip(),
        today_date=_today_kst_date(),
    )


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
            "theme": "dark" | "yellow"
        }
    """
    if mock_mode:
        return mock_response or _get_default_mock(client_id)

    config = get_client_config(client_id)
    llm_cfg = config.llm.news_card

    prompt = _build_user_prompt(config, source_content, source_type, source_url)

    try:
        from anthropic import Anthropic
    except ImportError:
        raise ImportError("pip install anthropic")

    client = Anthropic()

    response = client.messages.create(
        model=llm_cfg.model,
        max_tokens=1500,
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

    # Force-stamp source_url: LLM occasionally truncates or normalizes URLs;
    # the caller's URL is the source of truth.
    result["source_url"] = source_url

    _validate_result(result)
    return result


# ────────────────────────────────────────────────────
# Validation
# ────────────────────────────────────────────────────

VALID_THEMES = {"dark", "yellow"}
DATE_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
REQUIRED_KEYS = ("label", "date", "headline", "body_lines", "source_url", "theme")


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
    }
