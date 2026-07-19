from pathlib import Path

import pytest

from core.orchestrator import NEWS_CARD_TEMPLATES, generate_news_card
from core.sources.source_image import PreparedSourceImage


MOCK_SPEC = {
    "label": "업데이트",
    "date": "2026.07.18",
    "headline": "새로운 소식을 전합니다",
    "body_lines": ["첫 번째 핵심 내용", "두 번째 핵심 내용"],
    "source_url": "https://example.com/news",
    "theme": "dark",
}


def test_news_card_templates_are_allowlisted_and_present():
    assert set(NEWS_CARD_TEMPLATES) == {"remix", "classic", "editorial", "signal"}
    for relative_path in NEWS_CARD_TEMPLATES.values():
        template_path = Path("core/templates") / relative_path
        assert template_path.is_file()
        assert "coineasy" not in template_path.read_text().lower()
    squid_override = Path("clients/squid/overrides/news/news_remix_card.html")
    assert squid_override.is_file()
    override_html = squid_override.read_text()
    assert "translation_regions" in override_html
    assert "headline | safe" not in override_html
    assert "logo_dark_path" not in override_html
    assert "translation-region::after" not in override_html
    assert "--region-tint" not in override_html
    assert 'class="translation-region"' in override_html
    assert "subtitle-scrim" not in override_html
    assert "translation-footer" not in override_html
    assert "source_crop_bottom" not in override_html
    assert "filter: blur" not in override_html
    assert "-webkit-text-stroke: 2px" in override_html
    assert "text-shadow:" not in override_html
    assert "overflow: hidden" in override_html
    assert "renderedLineCount > 2" in override_html
    assert "anyRegionFailed" in override_html
    assert "for (const region of translationRegions) region.hidden = true" in override_html
    assert ".28em" not in override_html


@pytest.mark.asyncio
async def test_selected_template_is_used(monkeypatch, tmp_path):
    captured = {}

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")
        return kwargs["output_path"]

    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)

    result = await generate_news_card(
        client_id="yellow",
        source_content="A long enough source for a smoke test.",
        source_url="https://example.com/news",
        output_dir=tmp_path,
        mock_mode=True,
        mock_response=MOCK_SPEC,
        template_style="signal",
    )

    assert captured["template_path"] == "news/news_signal_card.html"
    assert result.template_style == "signal"
    assert result.png_path.endswith("news_card_signal.png")


@pytest.mark.asyncio
async def test_remix_uses_prepared_source_visual(monkeypatch, tmp_path):
    captured = {}

    async def fake_fetch_source_image(url):
        assert url == "https://pbs.twimg.com/media/source.jpg?name=orig"
        return PreparedSourceImage(
            media_type="image/jpeg",
            base64_data="aW1hZ2U=",
            width=1080,
            height=1080,
        )

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")
        return kwargs["output_path"]

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch_source_image)
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)

    result = await generate_news_card(
        client_id="squid",
        source_content="A long enough source for the original visual remix test.",
        source_url="https://x.com/squidrouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg?name=orig",
        output_dir=tmp_path,
        mock_mode=True,
        mock_response={
            **MOCK_SPEC,
            "source_logo_visible": True,
            "source_text_visible": True,
            "translation_regions": [{
                "text": "어디서나 XRP를 사용하세요",
                "x": 8,
                "y": 12,
                "width": 52,
                "height": 16,
                "align": "left",
                "font_role": "display",
                "font_size": 6,
                "text_color": "#FFFFFF",
            }],
        },
        template_style="remix",
    )

    assert captured["template_path"] == "news/news_remix_card.html"
    assert captured["slots"]["source_image_data_url"].startswith("data:image/jpeg;base64,")
    assert captured["slots"]["source_logo_visible"] is True
    assert captured["slots"]["source_text_visible"] is True
    assert captured["slots"]["translation_regions"][0]["text"] == "어디서나 XRP를 사용하세요"
    assert captured["slots"]["source_crop_bottom"] == 100.0
    assert captured["slots"]["source_image_width"] == 1080
    assert captured["slots"]["source_image_height"] == 1080
    assert result.template_style == "remix"
    assert result.requested_template_style == "remix"
    assert result.source_image_used is True
    assert result.png_path.endswith("news_card_remix.png")


@pytest.mark.asyncio
async def test_remix_without_visual_falls_back_to_classic(monkeypatch, tmp_path):
    captured = {}

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")
        return kwargs["output_path"]

    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)
    result = await generate_news_card(
        client_id="yellow",
        source_content="A long enough source for a fallback smoke test.",
        output_dir=tmp_path,
        mock_mode=True,
        mock_response=MOCK_SPEC,
        template_style="remix",
    )

    assert captured["template_path"] == "news/news_title_card.html"
    assert result.template_style == "classic"
    assert result.requested_template_style == "remix"
    assert result.source_image_used is False


@pytest.mark.asyncio
async def test_unknown_template_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown news card template"):
        await generate_news_card(
            client_id="yellow",
            source_content="A long enough source for a smoke test.",
            output_dir=tmp_path,
            mock_mode=True,
            mock_response=MOCK_SPEC,
            template_style="unknown",
        )
