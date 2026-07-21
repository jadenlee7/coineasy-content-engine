import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.orchestrator import (
    NEWS_CARD_TEMPLATES,
    _align_regions_to_detected_text,
    generate_news_card,
)
from core.client_config import get_client_config
from core.renderers.playwright_renderer import _build_font_head
from core.sources.source_image import PreparedSourceImage
from core.sources.source_text_cleanup import SourceTextCleanupError


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
    assert 'class="source-text-patch"' not in override_html
    assert 'class="source-clean-patch-image"' not in override_html
    assert "sample_x" not in override_html
    assert "sample_y" not in override_html
    assert "subtitle-scrim" not in override_html
    assert "translation-footer" not in override_html
    assert "source_crop_bottom" not in override_html
    assert "filter:" not in override_html
    assert "feGaussianBlur" not in override_html
    assert "box-shadow" not in override_html
    assert "-webkit-text-stroke: 1px rgba(16, 13, 22, .76)" in override_html
    assert "paint-order: stroke fill" in override_html
    assert "--region-cover-stroke" not in override_html
    assert "text-shadow" not in override_html
    assert "--region-text: {{ region.text_color | default('#FFFFFF') }};" in override_html
    assert ".translation-region::before" not in override_html
    assert "translateY(-0.22em)" not in override_html
    assert "translateY(0.22em) scaleX(var(--region-scale-x))" in override_html
    translation_css = override_html.split(".translation-region {", 1)[1].split("}", 1)[0]
    translation_text_css = override_html.split(".translation-region > span {", 1)[1].split("}", 1)[0]
    assert "background" not in translation_css + translation_text_css
    assert "border" not in translation_css + translation_text_css
    assert "#100D16" not in override_html
    assert "gradient" not in override_html
    assert "overflow: hidden" in override_html
    assert "data-source-line-count" in override_html
    assert "renderedLineCount > allowedLineCount" in override_html
    assert "coverPadding" not in override_html
    assert "staysInsideSourceFrame" in override_html
    assert "overlapsExistingRegion" in override_html
    assert "anyRegionFailed" in override_html
    assert "if (!text)" in override_html
    assert "for (const region of translationRegions) region.hidden = true" in override_html
    assert ".28em" not in override_html
    assert "cdn.jsdelivr.net" not in override_html


def test_squid_renderer_embeds_local_pretendard_without_a_network_dependency():
    config = get_client_config("squid")
    font_head = _build_font_head(config)

    assert "font-family:'Pretendard Variable'" in font_head
    assert "data:font/woff2;base64," in font_head
    assert "https://" not in font_head
    for template_path in Path("core/templates").rglob("*.html"):
        template = template_path.read_text()
        if "pretendardvariable.css" in template:
            assert "{% if client_id != 'squid' %}" in template
            assert "brand_font_links" in template


def test_detected_source_glyphs_recenter_the_transparent_korean_caption():
    aligned = _align_regions_to_detected_text(
        [{
            "text": "칠링",
            "x": 44,
            "y": 81,
            "width": 22,
            "height": 9,
        }],
        ({
            "x": 40.208333,
            "y": 85.625,
            "width": 20,
            "height": 9.6875,
        },),
        480,
        320,
    )

    region = aligned[0]
    assert region["x"] == pytest.approx(39.791666, abs=0.0001)
    assert region["y"] == pytest.approx(85.3125, abs=0.0001)
    assert region["width"] == pytest.approx(20.833333, abs=0.0001)
    assert region["height"] == pytest.approx(10.3125, abs=0.0001)
    assert region["source_x"] == region["x"]
    assert region["source_y"] == region["y"]


def test_detected_geometry_canonicalizes_different_audit_widths():
    detected = ({
        "x": 40.208333,
        "y": 85.625,
        "width": 20,
        "height": 9.6875,
    },)
    aligned = [
        _align_regions_to_detected_text(
            [{
                "text": "여유롭게",
                "font_size": 6,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }],
            detected,
            480,
            320,
        )[0]
        for x, y, width, height in (
            (44, 81, 22, 9),
            (47, 82, 17, 8),
            (46, 84.5, 19, 7.5),
        )
    ]

    for key in ("x", "y", "width", "height", "scale_x"):
        assert aligned[0][key] == aligned[1][key] == aligned[2][key]


def test_detected_geometry_keeps_the_public_figma_minimum_box_contract():
    region = _align_regions_to_detected_text(
        [{
            "text": "요",
            "font_size": 5.2,
            "x": 45,
            "y": 70,
            "width": 10,
            "height": 8,
        }],
        ({"x": 50, "y": 72, "width": 4, "height": 4},),
        480,
        320,
    )[0]

    assert region["width"] >= 6.0
    assert region["height"] >= 3.0
    assert region["source_width"] == region["width"]
    assert region["source_height"] == region["height"]


def test_detected_geometry_rejects_translation_that_no_longer_fits():
    with pytest.raises(SourceTextCleanupError, match="does not fit"):
        _align_regions_to_detected_text(
            [{
                "text": "크로스체인 유동성 업데이트",
                "font_size": 5.2,
                "x": 30,
                "y": 70,
                "width": 40,
                "height": 10,
            }],
            ({
                "x": 42,
                "y": 72,
                "width": 12,
                "height": 6,
            },),
            1080,
            1080,
        )


def test_detected_source_glyphs_fail_closed_instead_of_clamping_at_the_edge():
    with pytest.raises(SourceTextCleanupError, match="cannot stay centered"):
        _align_regions_to_detected_text(
            [{"text": "번역", "x": 0, "y": 20, "width": 20, "height": 10}],
            ({"x": 0, "y": 20, "width": 4, "height": 8},),
            480,
            320,
        )


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
    monkeypatch.setattr(
        "core.orchestrator.clean_source_text",
        lambda image, regions: SimpleNamespace(
            image=PreparedSourceImage(
                media_type="image/jpeg",
                base64_data="Y2xlYW5lZA==",
                width=image.width,
                height=image.height,
            ),
            masked_pixels=48,
            detected_regions=({
                "x": 20,
                "y": 20,
                "width": 30,
                "height": 10,
            },),
        ),
    )

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
                "source_text": "Use XRP anywhere",
                "text": "어디서나 XRP를 사용하세요",
                "x": 8,
                "y": 12,
                "width": 52,
                "height": 16,
                "source_x": 8,
                "source_y": 12,
                "source_width": 52,
                "source_height": 16,
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
    assert captured["slots"]["translation_regions"][0]["x"] == pytest.approx(19.8148, abs=0.001)
    assert captured["slots"]["translation_regions"][0]["y"] == pytest.approx(19.9074, abs=0.001)
    assert captured["slots"]["translation_regions"][0]["source_y"] == pytest.approx(19.9074, abs=0.001)
    assert "sample_y" not in captured["slots"]["translation_regions"][0]
    assert captured["slots"]["source_crop_bottom"] == 100.0
    assert captured["slots"]["source_image_width"] == 1080
    assert captured["slots"]["source_image_height"] == 1080
    assert result.template_style == "remix"
    assert result.requested_template_style == "remix"
    assert result.source_image_used is True
    assert result.source_visual_path == str(tmp_path / "source_visual_cleaned.jpg")
    assert (tmp_path / "source_visual_cleaned.jpg").read_bytes() == b"cleaned"
    assert result.png_path.endswith("news_card_remix.png")


@pytest.mark.asyncio
async def test_remix_cleanup_failure_atomically_preserves_the_original(monkeypatch, tmp_path):
    captured = {}
    source = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")

    async def fake_fetch_source_image(_url):
        return source

    def fail_cleanup(_image, _regions):
        raise SourceTextCleanupError("no reliable source-text mask found")

    region = {
        "source_text": "Original caption",
        "text": "원문 자막",
        "x": 30,
        "y": 80,
        "width": 40,
        "height": 10,
        "source_x": 30,
        "source_y": 80,
        "source_width": 40,
        "source_height": 10,
        "align": "center",
        "font_role": "display",
        "font_size": 5,
    }
    discarded = []

    def fake_generate_news_card_spec(**kwargs):
        assert kwargs["cached_visual_localization"] == [region]
        return {
            **MOCK_SPEC,
            "source_text_visible": True,
            "translation_regions": [dict(region)],
            "_visual_localization_cache_hit": True,
        }

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch_source_image)
    monkeypatch.setattr("core.orchestrator.clean_source_text", fail_cleanup)
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)
    monkeypatch.setattr(
        "core.orchestrator.make_visual_localization_cache_key",
        lambda **kwargs: "cache-key",
    )
    monkeypatch.setattr(
        "core.orchestrator.get_visual_localization",
        lambda _key: [region],
    )
    monkeypatch.setattr(
        "core.orchestrator.generate_news_card_spec",
        fake_generate_news_card_spec,
    )
    monkeypatch.setattr(
        "core.orchestrator.discard_visual_localization",
        lambda key: discarded.append(key),
    )

    result = await generate_news_card(
        client_id="squid",
        source_content="A source with a caption that cannot be cleaned safely.",
        source_url="https://x.com/squidrouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg?name=orig",
        output_dir=tmp_path,
        template_style="remix",
    )

    assert captured["slots"]["source_image_data_url"] == source.data_url
    assert captured["slots"]["source_text_visible"] is False
    assert captured["slots"]["translation_regions"] == []
    assert result.spec["visual_localization_status"] == "cleanup_failed"
    assert result.source_visual_path is None
    assert not (tmp_path / "source_visual_cleaned.jpg").exists()
    assert discarded == ["cache-key"]


@pytest.mark.asyncio
async def test_remix_cleaned_file_publish_failure_falls_back_atomically(monkeypatch, tmp_path):
    captured = {}
    source = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")

    async def fake_fetch_source_image(_url):
        return source

    region = {
        "source_text": "Original caption",
        "text": "원문 자막",
        "x": 30,
        "y": 80,
        "width": 40,
        "height": 10,
        "source_x": 30,
        "source_y": 80,
        "source_width": 40,
        "source_height": 10,
        "align": "center",
        "font_role": "display",
        "font_size": 5,
    }
    discarded = []

    def fake_generate_news_card_spec(**kwargs):
        assert kwargs["cached_visual_localization"] == [region]
        return {
            **MOCK_SPEC,
            "source_text_visible": True,
            "translation_regions": [dict(region)],
            "_visual_localization_cache_hit": True,
        }

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch_source_image)
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)
    monkeypatch.setattr(
        "core.orchestrator.make_visual_localization_cache_key",
        lambda **kwargs: "cache-key",
    )
    monkeypatch.setattr(
        "core.orchestrator.get_visual_localization",
        lambda _key: [region],
    )
    monkeypatch.setattr(
        "core.orchestrator.generate_news_card_spec",
        fake_generate_news_card_spec,
    )
    monkeypatch.setattr(
        "core.orchestrator.discard_visual_localization",
        lambda key: discarded.append(key),
    )
    monkeypatch.setattr(
        "core.orchestrator.clean_source_text",
        lambda image, _regions: SimpleNamespace(
            image=PreparedSourceImage(
                media_type="image/jpeg",
                base64_data="Y2xlYW5lZA==",
                width=image.width,
                height=image.height,
            ),
            masked_pixels=48,
        ),
    )
    original_replace = Path.replace

    def fail_cleaned_publish(path, target):
        if path.name.startswith(".source_visual_cleaned.") and path.suffix == ".tmp":
            raise OSError("disk full")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_cleaned_publish)

    result = await generate_news_card(
        client_id="squid",
        source_content="A source whose cleaned asset cannot be published.",
        source_url="https://x.com/squidrouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg?name=orig",
        output_dir=tmp_path,
        template_style="remix",
    )

    assert captured["slots"]["source_image_data_url"] == source.data_url
    assert captured["slots"]["source_text_visible"] is False
    assert captured["slots"]["translation_regions"] == []
    assert result.spec["visual_localization_status"] == "cleanup_failed"
    assert result.source_visual_path is None
    assert not (tmp_path / "source_visual_cleaned.jpg").exists()
    assert list(tmp_path.glob(".source_visual_cleaned.*.tmp")) == []
    assert discarded == []


@pytest.mark.asyncio
async def test_remix_caches_private_audit_evidence_but_never_publishes_it(
    monkeypatch,
    tmp_path,
):
    captured_render = {}
    captured_cache = {}
    source = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=480,
        height=320,
    )
    region = {
        "source_text": "chillin'",
        "text": "여유롭게",
        "x": 40,
        "y": 85,
        "width": 20,
        "height": 10,
        "source_x": 40,
        "source_y": 85,
        "source_width": 20,
        "source_height": 10,
        "align": "center",
        "font_role": "display",
        "font_size": 6,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [{"kind": "character", "x": 0, "y": 0}],
    }

    async def fake_fetch_source_image(_url):
        return source

    async def fake_render_png(**kwargs):
        captured_render.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")

    def fake_make_cache_key(**kwargs):
        assert kwargs["audit_model"]
        return "cache-key"

    def fake_generate_news_card_spec(**kwargs):
        assert kwargs["cached_visual_localization"] == [region]
        return {
            **MOCK_SPEC,
            "source_text_visible": True,
            "translation_regions": [dict(region)],
            "_visual_localization_cache_hit": True,
        }

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch_source_image)
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)
    monkeypatch.setattr(
        "core.orchestrator.make_visual_localization_cache_key",
        fake_make_cache_key,
    )
    monkeypatch.setattr(
        "core.orchestrator.get_visual_localization",
        lambda _key: [region],
    )
    monkeypatch.setattr(
        "core.orchestrator.generate_news_card_spec",
        fake_generate_news_card_spec,
    )
    monkeypatch.setattr(
        "core.orchestrator.clean_source_text",
        lambda image, _regions: SimpleNamespace(
            image=PreparedSourceImage(
                media_type="image/jpeg",
                base64_data="Y2xlYW5lZA==",
                width=image.width,
                height=image.height,
            ),
            masked_pixels=48,
            detected_regions=({
                "x": 40.2,
                "y": 85.6,
                "width": 19.8,
                "height": 9.4,
            },),
        ),
    )
    monkeypatch.setattr(
        "core.orchestrator.put_visual_localization",
        lambda key, regions: captured_cache.update(key=key, regions=regions),
    )

    result = await generate_news_card(
        client_id="squid",
        source_content="A cached source with private visual audit evidence.",
        source_url="https://x.com/squidrouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg?name=orig",
        output_dir=tmp_path,
        template_style="remix",
    )

    private_keys = {"_source_index", "_source_line_count", "_protected_regions"}
    assert captured_cache["key"] == "cache-key"
    assert private_keys <= captured_cache["regions"][0].keys()
    assert private_keys.isdisjoint(
        captured_render["slots"]["translation_regions"][0]
    )
    assert private_keys.isdisjoint(result.spec["translation_regions"][0])
    manifest = json.loads(Path(result.manifest_path).read_text())
    assert manifest["visual_placement_audit_model"]
    assert manifest["visual_localization_cache_hit"] is True
    assert private_keys.isdisjoint(manifest["spec"]["translation_regions"][0])


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
