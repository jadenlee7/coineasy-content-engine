from core.brand_voice import build_brand_voice_prompt
from core.client_config import load_client_config
from core.llm.article_pipeline import _build_user_prompt as _build_article_user_prompt
from core.llm.news_card_pipeline import _build_user_prompt


CLIENTS = ("yellow", "origintrail", "squid", "babylon")


def test_every_client_has_source_locked_voice_references():
    for client_id in CLIENTS:
        config = load_client_config(client_id)
        voice = config.brand_voice

        assert len(voice.identity) >= 4
        assert len(voice.avoid) >= 4
        assert len(voice.writing_patterns) >= 4
        assert voice.source_fidelity["x"] >= 90
        assert voice.source_fidelity["telegram"] >= 80
        assert voice.source_fidelity["banner"] >= 90
        assert voice.source_fidelity["article"] >= 90
        assert len(voice.channel_guidance["article"]) >= 160
        assert "visual" in voice.channel_guidance["article"].lower()
        assert len(voice.reference_examples) >= 3
        assert all(item.get("url", "").startswith("https://x.com/") for item in voice.reference_examples)


def test_official_source_handles_are_configured_for_all_clients():
    expected = {
        "yellow": "@Yellow",
        "origintrail": "@origin_trail",
        "squid": "@SquidRouter",
        "babylon": "@babylonlabs_io",
    }
    for client_id, handle in expected.items():
        config = load_client_config(client_id)
        assert config.content_sources.twitter is not None
        assert config.content_sources.twitter.handle == handle


def test_news_card_prompt_includes_official_voice_lock_and_factual_boundary():
    config = load_client_config("squid")
    prompt = _build_user_prompt(
        config,
        "Need XRP anywhere? We got you.",
        "tweet",
        "https://x.com/squidrouter/status/123",
    )

    assert "Official Brand Voice Lock" in prompt
    assert "Source fidelity target for this output: 92%" in prompt
    assert "do not add benefits" in prompt.lower()
    assert "Mirror the original post's brevity" in build_brand_voice_prompt(config, "x")
    assert "Need XRP anywhere?" in prompt
    assert "Squid Router" not in prompt


def test_runtime_references_are_explicitly_style_only_and_delimited():
    config = load_client_config("squid")
    prompt = build_brand_voice_prompt(config, "article", [{
        "source_url": "https://x.com/SquidRouter/status/123",
        "text": "A prior post claims a launch on a specific date.",
    }])

    assert "Runtime Official X Style References" in prompt
    assert "never factual source material" in prompt
    assert "Never reuse or infer their claims" in prompt
    assert "REFERENCE_1_START" in prompt
    assert "REFERENCE_1_END" in prompt
    assert "A prior post claims a launch" in prompt


def test_human_review_guidance_is_style_only_and_excludes_free_form_comments():
    config = load_client_config("squid")
    guidance = {
        "policy_version": "brand-review-learning@1",
        "approved_examples": [{
            "content_item_id": "22222222-2222-4222-8222-222222222222",
            "content_version_id": "33333333-3333-4333-8333-333333333333",
            "content_kind": "article",
            "text": "승인된 한국어 문장 리듬 예시입니다. 내일 한국 출시됩니다.",
            "approved_at": "2026-07-31T09:00:00.000Z",
        }],
        "avoid_reason_codes": [{
            "code": "unsupported_claim",
            "count": 2,
        }],
    }

    prompt = build_brand_voice_prompt(
        config,
        "article",
        brand_review_guidance=guidance,
    )

    assert "Human-Reviewed Korean Brand Guidance" in prompt
    assert "never factual source material" in prompt
    assert "current source remains the only factual boundary" in prompt
    assert "APPROVED_LOCALIZATION_1_START" in prompt
    assert "Remove any claim" in prompt
    assert "comment" not in prompt.lower()


def test_article_prompt_uses_distinct_client_editorial_and_visual_direction():
    expected_direction = {
        "yellow": ("measured infrastructure thesis", "system-led"),
        "origintrail": ("problem-to-proof narrative", "provenance legible"),
        "squid": ("recognizably compact and human", "punchy and sparse"),
        "babylon": ("verified product state", "Bitcoin-native"),
    }

    for client_id, expected_phrases in expected_direction.items():
        prompt = _build_article_user_prompt(
            load_client_config(client_id),
            "A source-supported factual article body.",
            "article",
            "https://example.com/source",
        )

        assert "Channel rule (article):" in prompt
        assert "Source fidelity target for this output: 92%" in prompt
        assert all(phrase in prompt for phrase in expected_phrases)


def test_generation_fails_closed_when_channel_brand_direction_is_missing():
    config = load_client_config("yellow")
    config.brand_voice.channel_guidance.pop("article")

    try:
        build_brand_voice_prompt(config, "article")
    except ValueError as exc:
        assert "channel_guidance.article" in str(exc)
    else:
        raise AssertionError("missing article brand direction must fail closed")
