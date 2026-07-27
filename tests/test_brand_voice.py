from core.brand_voice import build_brand_voice_prompt
from core.client_config import load_client_config
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
