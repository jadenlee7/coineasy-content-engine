from __future__ import annotations

import pytest

from core.llm.news_card_pipeline import (
    NEWS_CARD_KOREAN_LOCALIZATION_ERROR,
    NewsCardKoreanLocalizationError,
    _validate_result,
    generate_news_card_spec,
)


def _valid_spec() -> dict:
    return {
        "label": "$QUID / IBC",
        "date": "2026.08.25",
        "headline": "$QUID로 여러 체인을 연결합니다",
        "body_lines": [
            "IBC routing",
            "한국 사용자에게 핵심 내용을 안내합니다",
        ],
        "source_url": "https://x.com/squidrouter/status/1234567890",
        "theme": "dark",
        "source_logo_visible": False,
        "source_text_visible": False,
        "translation_regions": [],
    }


def test_live_news_card_validation_allows_protected_english_terms() -> None:
    _validate_result(_valid_spec())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "headline",
            "Squid connects every chain",
        ),
        (
            "body_lines",
            ["IBC routing", "Cross-chain liquidity"],
        ),
    ],
)
def test_live_news_card_validation_rejects_english_only_copy(
    field: str,
    value: object,
) -> None:
    spec = _valid_spec()
    spec[field] = value

    with pytest.raises(NewsCardKoreanLocalizationError) as error:
        _validate_result(spec)
    assert str(error.value) == NEWS_CARD_KOREAN_LOCALIZATION_ERROR


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("headline", "Squid connects every chain"),
        ("body_lines", ["IBC routing", "Cross-chain liquidity"]),
    ],
)
def test_mock_news_card_validation_rejects_english_only_custom_copy(
    field: str,
    value: object,
) -> None:
    spec = _valid_spec()
    spec[field] = value

    with pytest.raises(NewsCardKoreanLocalizationError) as error:
        generate_news_card_spec(
            client_id="squid",
            source_content="Mock source content remains test-only.",
            source_url=spec["source_url"],
            mock_mode=True,
            mock_response=spec,
        )
    assert str(error.value) == NEWS_CARD_KOREAN_LOCALIZATION_ERROR


def test_mock_news_card_validation_allows_mixed_protected_terms() -> None:
    result = generate_news_card_spec(
        client_id="squid",
        source_content="Mock source content remains test-only.",
        source_url=_valid_spec()["source_url"],
        mock_mode=True,
        mock_response=_valid_spec(),
    )

    assert result["label"] == "$QUID / IBC"
    assert result["body_lines"][0] == "IBC routing"
