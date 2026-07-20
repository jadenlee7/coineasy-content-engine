import json
from types import SimpleNamespace

import anthropic
import pytest

from core.llm.news_card_pipeline import (
    _audit_visual_subtitle_placement,
    _normalize_visual_localization,
    generate_news_card_spec,
)
from core.sources.source_image import PreparedSourceImage


def test_squid_visual_is_sent_to_llm_with_translation_only_guidance(monkeypatch):
    calls = []

    def fake_create_message(client, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
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
                    "x": 30,
                    "y": 82,
                    "width": 40,
                    "height": 13,
                    "align": "center",
                    "font_role": "display",
                    "font_size": 20,
                    "text_color": "#e6fa36",
                }],
            }
        else:
            payload = {
                "safe": True,
                "protected_regions": [
                    {"kind": "source_text", "source_index": 0, "x": 31, "y": 83, "width": 38, "height": 11},
                    {"kind": "character", "x": 34, "y": 18, "width": 62, "height": 45},
                ],
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

    assert len(calls) == 2
    content = calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[0]["source"]["data"] == "aW1hZ2U="
    assert "원문의 주장과 강도를 유지" in content[1]["text"]
    assert "원문에 없는 해석" in content[1]["text"]
    assert "original post image is attached" in content[1]["text"]
    assert "official logo or wordmark" in content[1]["text"]
    assert "official creative as the final composition" in content[1]["text"]
    assert "no meaningful translatable copy" in content[1]["text"]
    assert "single-word slang" in content[1]["text"]
    assert "Never infer image text from the post caption" in content[1]["text"]
    assert "source_text must transcribe the visible source phrase exactly" in content[1]["text"]
    assert "Keep a 1-2 line source at the same line count" in content[1]["text"]
    assert "prefer a 2-5 syllable Korean expression" in content[1]["text"]
    assert "must contain Korean Hangul" in content[1]["text"]
    assert "Never copy the original English sentence" in content[1]["text"]
    assert "opaque dark outline is shaped only around the Korean glyphs" in content[1]["text"]
    assert "without a rectangle, blur, cloned texture, separate footer" in content[1]["text"]
    assert "both the removal box and the final Korean text area" in content[1]["text"]
    assert "Do not move the Korean translation" in content[1]["text"]
    assert "Use one tight region around the actual glyphs" not in content[1]["text"]
    audit_content = calls[1]["messages"][0]["content"]
    assert calls[1]["system"].startswith("You are the final visual replacement QA")
    assert audit_content[0]["source"]["data"] == "aW1hZ2U="
    assert '"source_phrase_box": {"x": 30.0, "y": 82.0, "width": 40.0, "height": 13.0}' in audit_content[1]["text"]
    assert "dark outline shaped only around the Korean glyphs" in audit_content[1]["text"]
    assert "NEVER return pixel coordinates" in audit_content[1]["text"]
    assert "other_visual" in audit_content[1]["text"]
    assert '"protected_regions"' in audit_content[1]["text"]
    assert "patch_regions" not in audit_content[1]["text"]
    assert "sample_x" not in audit_content[1]["text"]
    assert "translation_regions may contain only text visibly present" in content[1]["text"]
    assert "Client: Squid (squid)" in content[1]["text"]
    assert "Squid Router" not in content[1]["text"]
    assert result["source_url"] == "https://x.com/squidrouter/status/123"
    assert result["source_logo_visible"] is True
    assert result["source_text_visible"] is True
    assert result["visual_localization_status"] == "translated"
    assert result["translation_regions"] == [{
        "source_text": "Need XRP anywhere?",
        "text": "어디서나 XRP가 필요하신가요?",
        "x": 31.0,
        "y": 83.0,
        "width": 38.0,
        "height": 11.0,
        "source_x": 31.0,
        "source_y": 83.0,
        "source_width": 38.0,
        "source_height": 11.0,
        "align": "center",
        "font_role": "display",
        "font_size": 12.0,
        "scale_x": 0.9,
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
        elif len(calls) == 2:
            payload = {
                "translations": [{
                    "index": 0,
                    "text": "stack이 곧 사랑,\nstack이 곧 인생.",
                }],
            }
        else:
            payload = {
                "safe": True,
                "protected_regions": [
                    {"kind": "source_text", "source_index": 0, "x": 28, "y": 68, "width": 44, "height": 18},
                    {"kind": "character", "x": 34, "y": 18, "width": 62, "height": 30},
                ],
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

    assert len(calls) == 3
    assert calls[1]["system"].startswith("You are a Korean localization editor")
    assert "stack is love, stack is life." in calls[1]["messages"][0]["content"]
    assert calls[2]["system"].startswith("You are the final visual replacement QA")
    assert "stack이 곧 사랑" in calls[2]["messages"][0]["content"][1]["text"]
    assert result["translation_regions"] == [{
        "source_text": "stack is love,\nstack is life.",
        "text": "stack이 곧 사랑,\nstack이 곧 인생.",
        "x": 28.0,
        "y": 68.0,
        "width": 44.0,
        "height": 18.0,
        "source_x": 28.0,
        "source_y": 68.0,
        "source_width": 44.0,
        "source_height": 18.0,
        "align": "center",
        "font_role": "display",
        "font_size": 5.0,
        "scale_x": 1.35,
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
            "source_x": 20,
            "source_y": 82,
            "source_width": 60,
            "source_height": 14,
            "align": "center",
            "font_role": "display",
            "font_size": 6,
            "text_color": "#FFFFFF",
        }],
    }, "squid", True)

    assert result["translation_regions"][0] == {
        "source_text": "stack is love,\nstack is life.",
        "text": "스택은 사랑,\n스택은 인생.",
        "x": 20.0,
        "y": 82.0,
        "width": 60.0,
        "height": 14.0,
        "source_x": 20.0,
        "source_y": 82.0,
        "source_width": 60.0,
        "source_height": 14.0,
        "align": "center",
        "font_role": "display",
        "font_size": 6.0,
        "scale_x": 1.35,
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
            "source_x": 20,
            "source_y": 67,
            "source_width": 60,
            "source_height": 12,
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
            "width": 5,
            "height": 2,
            "source_x": 4,
            "source_y": 4,
            "source_width": 5,
            "source_height": 2,
        }],
    }, "squid", True)

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_target_and_audited_source_geometry_must_match():
    result = _normalize_visual_localization({
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "A caption",
            "text": "한국어 자막",
            "x": 20,
            "y": 70,
            "width": 30,
            "height": 10,
            "source_x": 21,
            "source_y": 70,
            "source_width": 30,
            "source_height": 10,
        }],
    }, "squid", True)

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_overlapping_subtitle_boxes_preserve_the_original_creative():
    result = _normalize_visual_localization({
        "source_text_visible": True,
        "translation_regions": [
            {
                "source_text": "First", "text": "첫 번째",
                "x": 4, "y": 4, "width": 30, "height": 16,
                "source_x": 4, "source_y": 4, "source_width": 30, "source_height": 16,
            },
            {
                "source_text": "Second", "text": "두 번째",
                "x": 20, "y": 10, "width": 30, "height": 16,
                "source_x": 20, "source_y": 10, "source_width": 30, "source_height": 16,
            },
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
            "source_x": 4,
            "source_y": 4,
            "source_width": 40,
            "source_height": 24,
        }],
    }, "squid", True)

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_subtitle_that_cannot_fit_renderer_minimum_is_dropped_before_status():
    result = _normalize_visual_localization({
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "Short",
            "text": "이 문구는 같은 작은 자막 영역에 절대로 들어갈 수 없습니다",
            "x": 40,
            "y": 80,
            "width": 8,
            "height": 4,
            "source_x": 40,
            "source_y": 80,
            "source_width": 8,
            "source_height": 4,
            "font_size": 5,
        }],
    }, "squid", True, 480, 320)

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def _audit_result(monkeypatch, raw_result, audit_payload):
    def fake_create_message(client, **kwargs):
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(audit_payload, ensure_ascii=False))])

    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)
    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )
    return _audit_visual_subtitle_placement(object(), "test-model", raw_result, image)


def test_squid_placement_audit_rejects_pixel_protection_boxes_after_retry(monkeypatch):
    calls = []
    malformed_live_audit = {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 33, "y": 82, "width": 34, "height": 10},
            {"kind": "other", "x": 300, "y": 20, "width": 120, "height": 120},
        ],
    }
    def fake_create_message(client, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(malformed_live_audit, ensure_ascii=False))])

    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "chillin'",
            "text": "여유롭게",
            "x": 33,
            "y": 82,
            "width": 34,
            "height": 10,
            "align": "center",
            "font_role": "display",
            "font_size": 6,
            "text_color": "#FFFFFF",
        }],
    }
    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )

    result = _audit_visual_subtitle_placement(object(), "test-model", raw, image)

    assert len(calls) == 2
    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_placement_audit_accepts_percentage_protection_boxes(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "chillin'",
            "text": "여유롭게",
            "x": 33,
            "y": 84,
            "width": 34,
            "height": 10,
        }],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 33, "y": 84, "width": 34, "height": 10},
            {"kind": "character", "x": 0, "y": 18, "width": 100, "height": 42},
            {"kind": "face", "x": 15, "y": 65, "width": 20, "height": 10},
        ],
    })

    assert result["source_text_visible"] is True
    assert result["translation_regions"][0]["text"] == "여유롭게"
    assert result["translation_regions"][0]["x"] == 33.0
    assert result["translation_regions"][0]["y"] == 84.0
    assert result["translation_regions"][0]["source_y"] == 84.0
    assert not any(key.startswith("sample_") for key in result["translation_regions"][0])


def test_squid_placement_audit_uses_audited_source_text_geometry(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "chillin'",
            "text": "여유롭게",
            "x": 41,
            "y": 84,
            "width": 22,
            "height": 8,
            "font_role": "display",
            "font_size": 6,
            "text_color": "#FFFFFF",
        }],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 41, "y": 84, "width": 22, "height": 8},
            {"kind": "character", "x": 6, "y": 18, "width": 80, "height": 48},
        ],
    })

    assert result["source_text_visible"] is True
    assert result["translation_regions"][0]["text"] == "여유롭게"
    assert result["translation_regions"][0]["x"] == 41.0
    assert result["translation_regions"][0]["y"] == 84.0
    assert result["translation_regions"][0]["width"] == 22.0
    assert result["translation_regions"][0]["height"] == 8.0
    assert result["translation_regions"][0]["source_x"] == 41.0
    assert result["translation_regions"][0]["source_y"] == 84.0
    assert not any(key.startswith("sample_") for key in result["translation_regions"][0])


def test_squid_uncovered_zero_size_optional_protection_fails_closed(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "chillin'",
            "text": "여유롭게",
            "x": 41,
            "y": 84,
            "width": 22,
            "height": 8,
        }],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 41, "y": 84, "width": 22, "height": 8},
            {"kind": "face", "x": 68, "y": 15, "width": 0, "height": 0},
        ],
    })

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_source_text_box_cannot_cover_zero_size_visual_anchor(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "chillin'",
            "text": "여유롭게",
            "x": 41,
            "y": 84,
            "width": 22,
            "height": 8,
        }],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 41, "y": 84, "width": 22, "height": 8},
            {"kind": "face", "x": 50, "y": 88, "width": 0, "height": 0},
        ],
    })

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_line_shaped_optional_protection_fails_closed(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "chillin'",
            "text": "여유롭게",
            "x": 41,
            "y": 84,
            "width": 22,
            "height": 8,
        }],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 41, "y": 84, "width": 22, "height": 8},
            {"kind": "other_visual", "x": 0, "y": 72, "width": 100, "height": 28},
            {"kind": "face", "x": 15, "y": 90, "width": 0, "height": 5},
        ],
    })

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_placement_audit_fails_closed_after_invalid_retry(monkeypatch):
    calls = 0
    malformed_audit = {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 33, "y": 82, "width": 34, "height": 10},
            {"kind": "other", "x": 470, "y": 30, "width": 110, "height": 220},
        ],
    }

    def fake_create_message(client, **kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(malformed_audit))])

    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "chillin'",
            "text": "여유롭게",
            "x": 33,
            "y": 82,
            "width": 34,
            "height": 10,
        }],
    }
    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )

    result = _audit_visual_subtitle_placement(object(), "test-model", raw, image)

    assert calls == 2
    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_live_flow_reports_unsafe_status_after_rejected_audit_retry(monkeypatch):
    calls = []
    main_payload = {
        "label": "커뮤니티",
        "date": "2026.07.20",
        "headline": "Squid가 여유로운 주말 분위기를 전합니다",
        "body_lines": ["원본의 짧은 밈 문구를 현지화합니다"],
        "source_url": "ignored",
        "theme": "dark",
        "source_logo_visible": False,
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "chillin'",
            "text": "여유롭게",
            "x": 33,
            "y": 82,
            "width": 34,
            "height": 10,
            "align": "center",
            "font_role": "display",
            "font_size": 6,
            "text_color": "#FFFFFF",
        }],
    }
    malformed_audit = {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 33, "y": 82, "width": 34, "height": 10},
            {"kind": "other", "x": 470, "y": 30, "width": 110, "height": 220},
        ],
    }

    def fake_create_message(client, **kwargs):
        calls.append(kwargs)
        payload = main_payload if len(calls) == 1 else malformed_audit if len(calls) == 2 else {
            "safe": False,
            "protected_regions": [],
        }
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))])

    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)
    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )

    result = generate_news_card_spec(
        client_id="squid",
        source_content="Long week? Sundays are for",
        source_url="https://x.com/squidrouter/status/2078872512038142211",
        source_image=image,
    )

    assert len(calls) == 3
    assert calls[0]["system"].startswith("You are the content brain")
    assert calls[1]["system"].startswith("You are the final visual replacement QA")
    assert calls[2]["system"].startswith("You are the final visual replacement QA")
    assert calls[1]["messages"][0]["content"][0]["source"]["data"] == "aW1hZ2U="
    assert calls[2]["messages"][0]["content"][0]["source"]["data"] == "aW1hZ2U="
    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []
    assert result["visual_localization_status"] == "unsafe_placement"


def test_squid_live_flow_reports_translated_status_after_source_geometry_audit(monkeypatch):
    calls = []
    main_payload = {
        "label": "커뮤니티",
        "date": "2026.07.20",
        "headline": "Squid가 여유로운 주말 분위기를 전합니다",
        "body_lines": ["원본의 짧은 밈 문구를 현지화합니다"],
        "source_url": "ignored",
        "theme": "dark",
        "source_logo_visible": False,
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "chillin'",
            "text": "여유롭게",
            "x": 33,
            "y": 84,
            "width": 34,
            "height": 10,
            "align": "center",
            "font_role": "display",
            "font_size": 6,
            "text_color": "#FFFFFF",
        }],
    }
    source_geometry_audit = {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 33, "y": 84, "width": 34, "height": 10},
            {"kind": "character", "x": 0, "y": 18, "width": 100, "height": 42},
            {"kind": "face", "x": 15, "y": 65, "width": 20, "height": 10},
        ],
    }

    def fake_create_message(client, **kwargs):
        calls.append(kwargs)
        payload = main_payload if len(calls) == 1 else source_geometry_audit
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))])

    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)
    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )

    result = generate_news_card_spec(
        client_id="squid",
        source_content="Long week? Sundays are for",
        source_url="https://x.com/squidrouter/status/2078872512038142211",
        source_image=image,
    )

    assert len(calls) == 2
    assert calls[1]["messages"][0]["content"][0]["source"]["data"] == "aW1hZ2U="
    assert result["source_text_visible"] is True
    assert result["visual_localization_status"] == "translated"
    assert result["translation_regions"][0]["text"] == "여유롭게"
    assert result["translation_regions"][0]["x"] == 33.0
    assert result["translation_regions"][0]["y"] == 84.0
    assert result["translation_regions"][0]["source_y"] == 84.0
    assert not any(key.startswith("sample_") for key in result["translation_regions"][0])


def test_squid_placement_audit_replaces_copy_in_the_source_text_area(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "stack is love,\nstack is life.",
            "text": "스택은 사랑,\n스택은 인생.",
            "x": 30,
            "y": 82,
            "width": 40,
            "height": 13,
            "align": "center",
            "font_role": "display",
            "font_size": 6,
            "text_color": "#FFFFFF",
        }],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [{
            "kind": "source_text", "source_index": 0,
            "x": 29, "y": 79, "width": 42, "height": 18,
        }],
    })

    assert result["source_text_visible"] is True
    assert result["translation_regions"][0]["x"] == 29.0
    assert result["translation_regions"][0]["y"] == 79.0
    assert result["translation_regions"][0]["source_x"] == 29.0
    assert result["translation_regions"][0]["source_y"] == 79.0


@pytest.mark.parametrize(
    ("audited_x", "expected_visible"),
    [(30.0, True), (34.0, True), (34.01, False)],
)
def test_squid_placement_audit_enforces_source_geometry_overlap(
    monkeypatch,
    audited_x,
    expected_visible,
):
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "Original phrase",
            "text": "한국어 자막",
            "x": 30,
            "y": 20,
            "width": 20,
            "height": 20,
            "align": "center",
            "font_role": "display",
            "font_size": 5,
            "text_color": "#FFFFFF",
        }],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [{
            "kind": "source_text", "source_index": 0,
            "x": audited_x, "y": 20, "width": 20, "height": 20,
        }],
    })

    assert result["source_text_visible"] is expected_visible
    if expected_visible:
        assert result["translation_regions"][0]["text"] == "한국어 자막"
        assert result["translation_regions"][0]["x"] == audited_x
        assert result["translation_regions"][0]["source_x"] == audited_x
        assert result["translation_regions"][0]["align"] == "center"
        assert result["translation_regions"][0]["font_role"] == "display"
        assert result["translation_regions"][0]["font_size"] == 5
    else:
        assert result["translation_regions"] == []


def test_squid_placement_audit_rejects_whole_canvas_source_box(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "Original phrase", "text": "한국어 자막",
            "x": 30, "y": 70, "width": 20, "height": 10,
        }],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [{
            "kind": "source_text", "source_index": 0,
            "x": 0, "y": 0, "width": 100, "height": 100,
        }],
    })

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_placement_audit_rejects_distant_split_source_boxes(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "Line one\nLine two", "text": "첫째 줄\n둘째 줄",
            "x": 30, "y": 70, "width": 20, "height": 12,
        }],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 31, "y": 71, "width": 18, "height": 4},
            {"kind": "source_text", "source_index": 0, "x": 70, "y": 20, "width": 18, "height": 4},
        ],
    })

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_placement_audit_requires_source_box_for_every_index(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [
            {"source_text": "First", "text": "첫 번째", "x": 30, "y": 70, "width": 20, "height": 10},
            {"source_text": "Second", "text": "두 번째", "x": 60, "y": 70, "width": 20, "height": 10},
        ],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [{
            "kind": "source_text", "source_index": 0,
            "x": 30, "y": 70, "width": 20, "height": 10,
        }],
    })

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_placement_audit_preserves_every_source_index(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [
            {"source_text": "First", "text": "첫 번째", "x": 20, "y": 70, "width": 20, "height": 10},
            {"source_text": "Second", "text": "두 번째", "x": 60, "y": 70, "width": 20, "height": 10},
        ],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 21, "y": 71, "width": 18, "height": 8},
            {"kind": "source_text", "source_index": 1, "x": 61, "y": 71, "width": 18, "height": 8},
        ],
    })

    assert result["source_text_visible"] is True
    assert [region["text"] for region in result["translation_regions"]] == ["첫 번째", "두 번째"]
    assert [region["source_x"] for region in result["translation_regions"]] == [21.0, 61.0]


def test_squid_placement_audit_rejects_one_unsafe_region_atomically(monkeypatch):
    raw = {
        "source_text_visible": True,
        "translation_regions": [
            {"source_text": "First", "text": "첫 번째", "x": 30, "y": 70, "width": 20, "height": 10},
            {"source_text": "Second", "text": "두 번째", "x": 60, "y": 70, "width": 20, "height": 10},
        ],
    }
    result = _audit_result(monkeypatch, raw, {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 30, "y": 70, "width": 20, "height": 10},
            {"kind": "source_text", "source_index": 1, "x": 85, "y": 70, "width": 15, "height": 10},
        ],
    })

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


def test_squid_placement_audit_failure_preserves_original_creative(monkeypatch):
    def fail_create_message(client, **kwargs):
        raise RuntimeError("temporary audit failure")

    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fail_create_message)
    raw = {
        "source_text_visible": True,
        "translation_regions": [{
            "source_text": "Original phrase",
            "text": "한국어 자막",
            "x": 30,
            "y": 70,
            "width": 30,
            "height": 12,
        }],
    }
    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )

    result = _audit_visual_subtitle_placement(object(), "test-model", raw, image)

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
    assert result["visual_localization_status"] == "no_text"


def test_squid_rejected_localization_reports_unsafe_placement():
    image = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )
    result = generate_news_card_spec(
        client_id="squid",
        source_content="A visible caption with no validated placement.",
        source_image=image,
        mock_mode=True,
        mock_response={
            "label": "커뮤니티",
            "date": "2026.07.20",
            "headline": "Squid 커뮤니티 소식을 전합니다",
            "body_lines": ["원본 비주얼의 문구를 현지화합니다"],
            "source_url": "ignored",
            "theme": "dark",
            "source_logo_visible": True,
            "source_text_visible": True,
            "translation_regions": [{
                "source_text": "chillin'",
                "text": "여유롭게",
                "x": 4,
                "y": 4,
                "width": 20,
                "height": 8,
            }],
        },
    )

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []
    assert result["visual_localization_status"] == "unsafe_placement"
