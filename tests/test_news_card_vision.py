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
                    {"kind": "source_text", "source_index": 0, "x": 28, "y": 78, "width": 44, "height": 20},
                    {"kind": "character", "x": 34, "y": 18, "width": 62, "height": 58},
                ],
                "translation_regions": [{"index": 0, "x": 3, "y": 34, "width": 24, "height": 18}],
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
    assert "must contain Korean Hangul" in content[1]["text"]
    assert "Never copy the original English sentence" in content[1]["text"]
    assert "places audited Korean in a nearby clear area inside the original banner" in content[1]["text"]
    assert "subtitle background stays fully transparent" in content[1]["text"]
    assert "coordinates are a source-detection box" in content[1]["text"]
    assert "separate image-aware safety pass chooses the Korean target box" in content[1]["text"]
    assert "Use one tight region around the actual glyphs" not in content[1]["text"]
    audit_content = calls[1]["messages"][0]["content"]
    assert calls[1]["system"].startswith("You are the final visual placement QA")
    assert audit_content[0]["source"]["data"] == "aW1hZ2U="
    assert '"protected_source_box": {"x": 30.0, "y": 82.0, "width": 40.0, "height": 13.0}' in audit_content[1]["text"]
    assert "never a placement suggestion" in audit_content[1]["text"]
    assert "NEVER return pixel coordinates" in audit_content[1]["text"]
    assert "other_visual" in audit_content[1]["text"]
    assert "top-right (x=68,y=2,w=30,h=12)" in audit_content[1]["text"]
    assert "translation_regions may contain only text visibly present" in content[1]["text"]
    assert "Client: Squid (squid)" in content[1]["text"]
    assert "Squid Router" not in content[1]["text"]
    assert result["source_url"] == "https://x.com/squidrouter/status/123"
    assert result["source_logo_visible"] is True
    assert result["source_text_visible"] is True
    assert result["visual_localization_status"] == "translated"
    assert result["translation_regions"] == [{
        "text": "어디서나 XRP가 필요하신가요?",
        "x": 3.0,
        "y": 34.0,
        "width": 24.0,
        "height": 18.0,
        "align": "left",
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
                    {"kind": "source_text", "source_index": 0, "x": 28, "y": 78, "width": 44, "height": 20},
                    {"kind": "character", "x": 34, "y": 18, "width": 62, "height": 58},
                ],
                "translation_regions": [{"index": 0, "x": 3, "y": 34, "width": 24, "height": 18}],
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
    assert calls[2]["system"].startswith("You are the final visual placement QA")
    assert "stack이 곧 사랑" in calls[2]["messages"][0]["content"][1]["text"]
    assert result["translation_regions"] == [{
        "text": "stack이 곧 사랑,\nstack이 곧 인생.",
        "x": 3.0,
        "y": 34.0,
        "width": 24.0,
        "height": 18.0,
        "align": "left",
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


def test_squid_placement_audit_retries_live_mixed_coordinate_response(monkeypatch):
    calls = []
    malformed_live_audit = {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 33, "y": 82, "width": 34, "height": 10},
            {"kind": "other", "x": 95, "y": 30, "width": 110, "height": 220},
            {"kind": "other", "x": 300, "y": 170, "width": 120, "height": 120},
            {"kind": "other", "x": 0, "y": 230, "width": 200, "height": 90},
        ],
        "translation_regions": [{"index": 0, "x": 68, "y": 3, "width": 30, "height": 14}],
    }
    corrected_audit = {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 33, "y": 82, "width": 34, "height": 10},
            {"kind": "character", "x": 19.8, "y": 9.4, "width": 22.9, "height": 68.7},
            {"kind": "product", "x": 62.5, "y": 53.1, "width": 25, "height": 37.5},
            {"kind": "other_visual", "x": 0, "y": 71.9, "width": 41.6, "height": 28.1},
        ],
        "translation_regions": [{"index": 0, "x": 68, "y": 3, "width": 30, "height": 14}],
    }

    def fake_create_message(client, **kwargs):
        calls.append(kwargs)
        payload = malformed_live_audit if len(calls) == 1 else corrected_audit
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))])

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
    retry_prompt = calls[1]["messages"][0]["content"][1]["text"]
    assert "must use 0-100 percentage coordinates" in retry_prompt
    assert "never pixels" in retry_prompt
    assert "Use other_visual, never other" in retry_prompt
    assert "do not merely mirror it to the other middle side" in retry_prompt
    assert "top-right (x=68,y=2,w=30,h=12)" in retry_prompt
    assert result["source_text_visible"] is True
    assert result["translation_regions"][0]["text"] == "여유롭게"
    assert result["translation_regions"][0]["x"] == 68.0
    assert result["translation_regions"][0]["y"] == 3.0
    assert result["translation_regions"][0]["align"] == "right"


def test_squid_placement_audit_accepts_top_right_space_above_central_character(monkeypatch):
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
            {"kind": "character", "x": 6, "y": 18, "width": 80, "height": 58},
            {"kind": "other_visual", "x": 0, "y": 72, "width": 100, "height": 28},
        ],
        "translation_regions": [{"index": 0, "x": 68, "y": 2, "width": 30, "height": 12}],
    })

    assert result["source_text_visible"] is True
    assert result["translation_regions"][0]["text"] == "여유롭게"
    assert result["translation_regions"][0]["x"] == 68.0
    assert result["translation_regions"][0]["y"] == 2.0
    assert result["translation_regions"][0]["width"] == 30.0
    assert result["translation_regions"][0]["height"] == 12.0
    assert result["translation_regions"][0]["align"] == "right"


def test_squid_placement_audit_fails_closed_after_invalid_retry(monkeypatch):
    calls = 0
    malformed_audit = {
        "safe": True,
        "protected_regions": [
            {"kind": "source_text", "source_index": 0, "x": 33, "y": 82, "width": 34, "height": 10},
            {"kind": "other", "x": 95, "y": 30, "width": 110, "height": 220},
        ],
        "translation_regions": [{"index": 0, "x": 68, "y": 3, "width": 30, "height": 14}],
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
            {"kind": "other", "x": 95, "y": 30, "width": 110, "height": 220},
        ],
        "translation_regions": [{"index": 0, "x": 68, "y": 3, "width": 30, "height": 14}],
    }

    def fake_create_message(client, **kwargs):
        calls.append(kwargs)
        payload = main_payload if len(calls) == 1 else malformed_audit if len(calls) == 2 else {
            "safe": False,
            "protected_regions": [],
            "translation_regions": [],
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
    assert calls[1]["system"].startswith("You are the final visual placement QA")
    assert calls[2]["system"].startswith("You are the final visual placement QA")
    assert calls[1]["messages"][0]["content"][0]["source"]["data"] == "aW1hZ2U="
    assert calls[2]["messages"][0]["content"][0]["source"]["data"] == "aW1hZ2U="
    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []
    assert result["visual_localization_status"] == "unsafe_placement"


def test_squid_placement_audit_rejects_live_source_text_overlap(monkeypatch):
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
        "translation_regions": [{"index": 0, "x": 30, "y": 82, "width": 40, "height": 13}],
    })

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


@pytest.mark.parametrize(
    ("target_width", "expected_visible"),
    [(24.0, True), (24.01, False), (23.99, False)],
)
def test_squid_placement_audit_enforces_clearance_and_minimum_size(
    monkeypatch,
    target_width,
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
            "x": 30, "y": 20, "width": 20, "height": 20,
        }],
        "translation_regions": [{
            "index": 0, "x": 3, "y": 20, "width": target_width, "height": 12,
        }],
    })

    assert result["source_text_visible"] is expected_visible
    if expected_visible:
        assert result["translation_regions"][0]["text"] == "한국어 자막"
        assert result["translation_regions"][0]["x"] == 3.0
        assert result["translation_regions"][0]["align"] == "left"
        assert result["translation_regions"][0]["font_role"] == "display"
        assert result["translation_regions"][0]["font_size"] == 5
    else:
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
        "translation_regions": [
            {"index": 0, "x": 3, "y": 3, "width": 24, "height": 12},
            {"index": 1, "x": 73, "y": 3, "width": 24, "height": 12},
        ],
    })

    assert result["source_text_visible"] is False
    assert result["translation_regions"] == []


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
            {"kind": "source_text", "source_index": 1, "x": 60, "y": 70, "width": 20, "height": 10},
        ],
        "translation_regions": [
            {"index": 0, "x": 3, "y": 3, "width": 24, "height": 12},
            {"index": 1, "x": 58, "y": 68, "width": 24, "height": 12},
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
