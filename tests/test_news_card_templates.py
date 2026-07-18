from pathlib import Path

import pytest

from core.orchestrator import NEWS_CARD_TEMPLATES, generate_news_card


MOCK_SPEC = {
    "label": "업데이트",
    "date": "2026.07.18",
    "headline": "새로운 소식을 전합니다",
    "body_lines": ["첫 번째 핵심 내용", "두 번째 핵심 내용"],
    "source_url": "https://example.com/news",
    "theme": "dark",
}


def test_news_card_templates_are_allowlisted_and_present():
    assert set(NEWS_CARD_TEMPLATES) == {"classic", "editorial", "signal"}
    for relative_path in NEWS_CARD_TEMPLATES.values():
        assert (Path("core/templates") / relative_path).is_file()


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
