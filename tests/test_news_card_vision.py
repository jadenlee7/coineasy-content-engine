import json
from types import SimpleNamespace

import anthropic
import pytest

from core.llm.news_card_pipeline import _normalize_visual_localization, generate_news_card_spec
from core.sources.source_image import PreparedSourceImage


def test_squid_visual_is_sent_to_llm_with_translation_only_guidance(monkeypatch):
    captured = {}

    def fake_create_message(client, **kwargs):
        captured.update(kwargs)
        payload = {
            "label": "기능 업데이트",
            "date": "2026.07.19",
            "headline": "한국 사용자 관점의 핵심 기능을 전합니다",
            "body_lines": ["원본 배너의 제품 정보를 반영합니다"],
            "source_url": "ignored",
            "theme": "dark",
            "source_logo_visible": True,
            "source_text_visible": True,
            "translation_regions": [{
                "source_text": "Need XRP anywhere?",
                "text": "어디서나 XRP가 필요하신가요?",
                "x": 4,
                "y": 15,
                "width": 92,
                "height": 12,
                "align": "center",
                "font_role": "display",
                "font_size": 20,
                "text_color": "#e6fa36",
            }],
        }
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))])

    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)

    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=1080,
        height=1080,
    )
    result = generate_news_card_spec(
        client_id="squid",
        source_content="A product update with enough factual source context.",
        source_url="https://x.com/squidrouter/status/123",
        source_image=image,
    )

    content = captured["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[0]["source"]["data"] == "aW1hZ2U="
    assert "원문의 주장과 강도를 유지" in content[1]["text"]
    assert "원문에 없는 해석" in content[1]["text"]
    assert "original post image is attached" in content[1]["text"]
    assert "official logo or wordmark" in content[1]["text"]
    assert "official creative as the final composition" in content[1]["text"]
    assert "no meaningful translatable copy" in content[1]["text"]
    assert "source_text must transcribe the visible source phrase exactly" in content[1]["text"]
    assert "Keep a 1-2 line source at the same line count" in content[1]["text"]
    assert "must contain Korean Hangul" in content[1]["text"]
    assert "Never copy the original English sentence" in content[1]["text"]
    assert "places Korean in a nearby clear area inside the original banner" in content[1]["text"]
    assert "subtitle background stays fully transparent" in content[1]["text"]
    assert "x/y/width/height define the NEW Korean subtitle placement" in content[1]["text"]
    assert "must not touch or cover the original source lettering" in content[1]["text"]
    assert "do not use the source lettering's position as the Korean subtitle box" in content[1]["text"]
    assert "Use one tight region around the actual glyphs" not in content[1]["text"]
    assert "Every region must be at least 24% wide and 12% high" in content[1]["text"]
    assert "If no non-overlapping clear area" in content[1]["text"]
    assert "translation_regions may contain only text visibly present" in content[1]["text"]
    assert "Client: Squid (squid)" in content[1]["text"]
    assert "Squid Router" not in content[1]["text"]
    assert result["source_url"] == "https://x.com/squidrouter/status/123"
    assert result["source_logo_visible"] is True
    assert result["source_text_visible"] is True
    assert result["translation_regions"] == [{
        "text": "어디서나 XRP가 필요하신가요?",
        "x": 4.0,
        "y": 15.0,
        "width": 92.0,
        "height": 12.0,
        "align": "center",
        "font_role": "display",
        "font_size": 12.0,
        "scale_x": 0.85,
        "text_color": "#E6FA36",
    }]
    assert result["source_crop_bottom"] == 100.0


def test_squid_untranslated_visual_copy_is_repaired_in_korean(monkeypatch):
    calls = []

    def fake_create_message(client, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            payload = {
                "label": "커뮤니티",
                "date": "2026.07.19",
                "headline": "Squid가 스택의 매력을 전합니다",
                "body_lines": ["원문의 짧은 유머를 유지합니다"],
                "source_url": "ignored",
                "theme": "dark",
                "source_logo_visible": True,
                "source_text_visible": True,
                "translation_regions": [{
                    "source_text": "stack is love,\nstack is life.",
                    "text": "stack is love, stack is life.",
                    "x": 30,
                    "y": 70,
                    "width": 40,
                    "height": 14,
                    "align": "center",
                    "font_role": "display",
                    "font_size": 5,
                    "text_color": "#FFFFFF",
                }],
            }
        else:
            payload = {
                "translations": [{
                    "index": 0,
                    "text": "stack이 곧 사랑,\nstack이 곧 인생.",
                }],
            }
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))])

    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)

    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=1080,
        height=1080,
    )
    result = generate_news_card_spec(
        client_id="squid",
        source_content="stack is love, stack is life.",
        source_url="https://x.com/squidrouter/status/789",
        source_image=image,
    )

    assert len(calls) == 2
    assert calls[1]["system"].startswith("You are a Korean localization editor")
    assert "stack is love, stack is life." in calls[1]["messages"][0]["content"]
    assert result["translation_regions"] == [{
        "text": "stack이 곧 사랑,\nstack이 곧 인생.",
        "x": 30.0,
        "y": 70.0,
        "width": 40.0,
        "height": 14.0,
        "align": "center",
        "font_role": "display",
        "font_size": 5.0,
        "scale_x": 0.91,
        "text_color": "#FFFFFF",
    }]
    assert result["source_crop_bottom"] == 100.0


def test_squid_safe_subtitle_box_is_preserved_from_vision():
    result = _normalize_visual_localization({
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "stack is love,\nstack is life.",
            "text": "스택은 사랑,\n스택은 인생.",
            "x": 20,
            "y": 82,
            "width": 60,
            "height": 14,
            "align": "center",
            "font_role": "display",
            "font_size": 6,
            "text_color": "#FFFFFF",
        }],
    }, "squid", True)

    assert result["translation_regions"][0] == {
        "text": "스택은 사랑,\n스택은 인생.",
        "x": 20.0,
        "y": 82.0,
        "width": 60.0,
        "height": 14.0,
        "align": "center",
        "font_role": "display",
        "font_size": 6.0,
        "scale_x": 1.24,
        "text_color": "#FFFFFF",
    }
    assert result["source_crop_bottom"] == 100.0


def test_squid_safe_subtitle_coordinates_are_not_shifted_by_the_renderer():
    result = _normalize_visual_localization({
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "A lower-third caption",
            "text": "하단 자막",
            "x": 20,
            "y": 67,
            "width": 60,
            "height": 12,
            "align": "center",
            "font_role": "display",
            "font_size": 5,
            "text_color": "#FFFFFF",
        }],
    }, "squid", True)

    assert result["translation_regions"][0]["y"] == 67.0
    assert result["source_crop_bottom"] == 100.0


def test_squid_unsafe_small_subtitle_box_is_dropped():
    result = _normalize_visual_localization({
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "A caption",
            "text": "한국어 자막",
            "x": 4,
            "y": 4,
            "width": 20,
            "height": 8,
        }],
    }, "squid", True)

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_overlapping_subtitle_boxes_preserve_the_original_creative():
    result = _normalize_visual_localization({
        "source_text_visible": True,
        "translation_regions": [
            {"source_text": "First", "text": "첫 번째", "x": 4, "y": 4, "width": 30, "height": 16},
            {"source_text": "Second", "text": "두 번째", "x": 20, "y": 10, "width": 30, "height": 16},
        ],
    }, "squid", True)

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_three_line_subtitle_preserves_the_original_creative():
    result = _normalize_visual_localization({
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "First\nSecond\nThird",
            "text": "첫째 줄\n둘째 줄\n셋째 줄",
            "x": 4,
            "y": 4,
            "width": 40,
            "height": 24,
        }],
    }, "squid", True)

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_untranslated_visual_copy_cannot_succeed_after_failed_repair(monkeypatch):
    calls = 0

    def fake_create_message(client, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = {
                "label": "커뮤니티",
                "date": "2026.07.19",
                "headline": "Squid 커뮤니티 소식을 전합니다",
                "body_lines": ["원문 배너를 현지화합니다"],
                "source_url": "ignored",
                "theme": "dark",
                "source_logo_visible": True,
                "source_text_visible": True,
                "translation_regions": [{
                    "text": "stack is love, stack is life.",
                    "x": 30,
                    "y": 70,
                    "width": 40,
                    "height": 10,
                    "align": "center",
                    "font_role": "display",
                    "font_size": 5,
                    "text_color": "#FFFFFF",
                }],
            }
        else:
            payload = {"translations": [{"index": 0, "text": "stack is love"}]}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])

    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)

    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=1080,
        height=1080,
    )
    with pytest.raises(ValueError, match="left visual copy untranslated"):
        generate_news_card_spec(
            client_id="squid",
            source_content="stack is love, stack is life.",
            source_image=image,
        )


def test_source_logo_is_never_reported_without_an_attached_image(monkeypatch):
    captured = {}

    def fake_create_message(client, **kwargs):
        captured.update(kwargs)
        payload = {
            "label": "기능 업데이트",
            "date": "2026.07.19",
            "headline": "한국 사용자 관점의 핵심 기능을 전합니다",
            "body_lines": ["제품 정보를 한국어로 정리합니다"],
            "source_url": "ignored",
            "theme": "dark",
            "source_logo_visible": True,
        }
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))])

    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)

    result = generate_news_card_spec(
        client_id="squid",
        source_content="A product update with enough factual source context.",
        source_url="https://x.com/squidrouter/status/123",
    )

    assert result["source_logo_visible"] is False
    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []
    assert result["source_crop_bottom"] == 100.0


def test_squid_textless_visual_keeps_translation_layer_empty():
    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=1080,
        height=1080,
    )
    result = generate_news_card_spec(
        client_id="squid",
        source_content="A character-only Squid post.",
        source_url="https://x.com/squidrouter/status/456",
        source_image=image,
        mock_mode=True,
        mock_response={
            "label": "커뮤니티",
            "date": "2026.07.19",
            "headline": "Squid 커뮤니티의 오늘을 전합니다",
            "body_lines": ["캐릭터 비주얼 중심의 게시물입니다"],
            "source_url": "ignored",
            "theme": "dark",
            "source_logo_visible": True,
            "source_text_visible": False,
            "translation_regions": [{"text": "새 문구를 추가하면 안 됩니다"}],
        },
    )

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []
    assert result["source_crop_bottom"] == 100.0
