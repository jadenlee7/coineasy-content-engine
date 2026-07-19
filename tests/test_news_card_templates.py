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
        mock_response={**MOCK_SPEC, "source_logo_visible": True},
        template_style="remix",
    )

    assert captured["template_path"] == "news/news_remix_card.html"
    assert captured["slots"]["source_image_data_url"].startswith("data:image/jpeg;base64,")
    assert captured["slots"]["source_logo_visible"] is True
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
