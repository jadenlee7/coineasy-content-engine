import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, UndefinedError

from core.orchestrator import (
    NEWS_CARD_TEMPLATES,
    _SQUID_GENERATED_TEMPLATE_VERSION,
    _align_regions_to_detected_text,
    generate_news_card,
)
from core.client_config import get_client_config
from core.llm.news_card_pipeline import _minimum_squid_font_percent
from core.renderers.playwright_renderer import (
    TranslationLayoutError,
    _build_font_head,
    _expects_squid_generated_headline_layout,
    _inject_brand_slots,
)
from core.sources.source_image import PreparedSourceImage, SourceImageError
from core.sources.source_text_cleanup import SourceTextCleanupError


MOCK_SPEC = {
    "label": "업데이트",
    "date": "2026.07.18",
    "headline": "새로운 소식을 전합니다",
    "body_lines": ["첫 번째 핵심 내용", "두 번째 핵심 내용"],
    "source_url": "https://example.com/news",
    "theme": "dark",
}


def test_squid_minimum_font_matches_the_source_native_viewport():
    assert _minimum_squid_font_percent(1600, 900) == 2.0
    assert _minimum_squid_font_percent(480, 320) == pytest.approx(14 / 480 * 100)


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
    assert "window.__squidTranslationLayoutStatus" in override_html
    assert "window.__evaluateSquidTranslationLayout = async () =>" in override_html
    assert "await document.fonts.ready" in override_html
    assert "region.style.fontSize = initialSize" in override_html
    assert "safe: !anyRegionFailed" in override_html
    assert "region.hidden = true" not in override_html
    assert ".28em" not in override_html
    assert "cdn.jsdelivr.net" not in override_html

    renderer_source = Path("core/renderers/playwright_renderer.py").read_text()
    assert "document.fonts.status === 'loaded'" in renderer_source
    assert "window.__evaluateSquidTranslationLayout()" in renderer_source

    squid_classic = Path("clients/squid/overrides/news/news_title_card.html")
    assert squid_classic.is_file()
    classic_html = squid_classic.read_text()
    assert "#E6CCFC" in classic_html
    assert "squid_squib_path" in classic_html
    assert "squid_bubbles_path" in classic_html
    assert "width: 320px" in classic_html
    assert "main-card" not in classic_html
    assert "body-item" not in classic_html
    assert "coineasy" not in classic_html.lower()
    assert "size > minimum" in classic_html
    assert ".canvas--legacy" in classic_html
    assert "linear-gradient(132deg, #C99AF0" in classic_html
    assert "width: 1200px" in classic_html
    assert "top: -40px" in classic_html
    assert "white oval" not in classic_html.lower()
    assert 'class="stage-word stage-word--top"' in classic_html
    assert 'class="stage-word stage-word--bottom"' not in classic_html
    assert "window.__evaluateSquidGeneratedHeadlineLayout = async () =>" in classic_html
    assert "await document.fonts.ready" in classic_html
    assert "if (!generatedFamilies.has(variant))" in classic_html
    assert "headline.style.lineHeight = size >= 150 ? '1.04' : '.82'" in classic_html
    legacy_fit_branch = classic_html.split("if (!generatedFamilies.has(variant))", 1)[1].split(
        "headline.style.fontSize = `${size}px`;", 1
    )[0]
    assert "headline.style.lineHeight" not in legacy_fit_branch

    renderer_source = Path("core/renderers/playwright_renderer.py").read_text()
    assert "window.__evaluateSquidGeneratedHeadlineLayout()" in renderer_source
    assert "Squid generated headline did not pass browser layout" in renderer_source


def test_squid_generated_headline_guard_excludes_source_remix_audit_family():
    assert _expects_squid_generated_headline_layout({
        "creative_family": "product_proof",
        "render_strategy": "source_remix",
    }) is False
    assert _expects_squid_generated_headline_layout({
        "creative_family": "product_proof",
        "render_strategy": "generated_gtm",
    }) is True


def test_squid_generated_template_version_invalidates_the_prior_stage_geometry():
    assert _SQUID_GENERATED_TEMPLATE_VERSION == "squid-generated-gtm@4"

    netlify_source = Path("netlify/functions/news-card.mts").read_text()
    assert '"squid-generated-gtm@4"' in netlify_source
    assert '"squid-generated-gtm@3"' not in netlify_source
    assert "payload.template_version = SQUID_GENERATED_TEMPLATE_VERSION" in netlify_source


def test_squid_remix_preserves_the_full_latest_official_landscape_composition():
    template_dir = Path("clients/squid/overrides/news")
    template = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
    ).get_template("news_remix_card.html")

    html = template.render(
        source_image_data_url="data:image/jpeg;base64,b2ZmaWNpYWw=",
        source_image_width=1200,
        source_image_height=675,
        output_width=1200,
        output_height=675,
        output_policy="official_source_native_v1",
        source_background_color="#B881DF",
        source_text_visible=False,
        translation_regions=[],
        brand_bg_dark="#1A0E2E",
        font_family="Pretendard Variable",
        font_display="",
        brand_font_links="",
    )

    # The latest official @squidrouter poster is 1200x675. The primary X
    # deliverable must keep that native composition instead of shrinking it
    # into a square letterbox or wrapping it in publisher chrome.
    assert "left: 0.000px" in html
    assert "top: 0.000px" in html
    assert "width: 1200.000px" in html
    assert "height: 675.000px" in html
    assert "width: 1200.000px; height: 675.000px" in html
    assert "object-fit: fill" in html
    assert "--source-bg: #B881DF" in html
    assert "background: var(--source-bg)" in html
    assert '<section class="source-frame" aria-label="Squid source creative">' in html
    assert 'class="translation-region"' not in html
    for forbidden in (
        "object-fit: cover",
        "visual-blur",
        "visual-shade",
        "logo-wrap",
        "translation-footer",
        "<h1",
    ):
        assert forbidden not in html


def test_squid_generated_html_routes_the_same_four_public_families_as_editable_svg():
    template = Environment(
        loader=FileSystemLoader("clients/squid/overrides/news"),
        autoescape=False,
    ).get_template("news_title_card.html")
    common = {
        "label": "SQUID UPDATE",
        "headline": "Squid로 더 멀리 연결해요",
        "body_lines": ["공식 원문에서 확인한 소식이에요"],
        "source_url": "https://x.com/squidrouter/status/123",
        "date": "2026.08.02",
        "squid_bubbles_path": "bubbles.png",
        "squid_squib_path": "squib.png",
        "squid_form_language_path": "form.png",
        "logo_light_path": "logo-black.png",
        "logo_dark_path": "logo-white.png",
        "font_display": "",
        "brand_font_links": "",
    }

    safe_families = (
        "editorial_big_type",
        "milestone_metric",
        "status_progress",
        "product_proof",
    )
    for family in safe_families:
        html = template.render(
            **common,
            creative_family=family,
            visual_metric="5m" if family == "milestone_metric" else "",
        )
        assert f"canvas--{family}" in html
        assert f'data-creative-family="{family}"' in html
        assert "COINEASY / KOREA" not in html
        assert "#E6FA36" in html
        assert "#BC8EE4" in html
        assert "linear-gradient(132deg, #C99AF0" in html
        assert "left: -110px" in html
        assert "width: 1200px" in html
        assert "font-size: 168px" in html
        assert (
            '<div class="stage-word stage-word--top" aria-hidden="true">Squid</div>'
            in html
        )
        assert 'stage-word--bottom' not in html
        assert 'class="eyebrow"' not in html
        assert '<section class="support">' not in html
        assert '<footer class="footer">' not in html
        assert 'class="brand-logo' not in html
        assert "background: rgba(255, 255, 255, .97)" not in html
        if family == "product_proof":
            assert "canvas--status_progress" not in html.split("<main", 1)[1].split(">", 1)[0]
            assert 'class="form-language"' in html
        if family == "milestone_metric":
            assert '<div class="metric">5m</div>' in html

    with pytest.raises(UndefinedError):
        template.render(
            **common,
            creative_family="worldbuilding",
        )


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


def test_squid_renderer_injects_reviewed_official_world_assets():
    slots = _inject_brand_slots({}, get_client_config("squid"), "dark")

    for key in (
        "logo_light_path",
        "squid_form_language_path",
        "squid_squib_path",
        "squid_bubbles_path",
    ):
        assert slots[key].startswith("data:image/png;base64,")


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
@pytest.mark.parametrize("requested_style", ["editorial", "signal"])
async def test_squid_generic_template_requests_use_the_official_classic(
    monkeypatch,
    tmp_path,
    requested_style,
):
    captured = {}

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")
        return kwargs["output_path"]

    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)

    result = await generate_news_card(
        client_id="squid",
        source_content="A substantial official Squid ecosystem announcement with enough detail.",
        output_dir=tmp_path,
        mock_mode=True,
        mock_response=MOCK_SPEC,
        template_style=requested_style,
    )

    assert captured["template_path"] == "news/news_title_card.html"
    assert result.requested_template_style == requested_style
    assert result.template_style == "classic"
    assert result.png_path.endswith("news_card_classic.png")
    assert result.spec["creative_family"] == "editorial_big_type"
    assert result.spec["render_strategy"] == "generated_gtm"
    assert result.spec["channel_profile"] == "x_square"
    assert result.spec["creative_family_policy_version"] == "squid-visual-routing@1"
    assert result.spec["visual_reference_pack_version"] == 2
    assert (
        result.spec["visual_design_profile_id"]
        == "squid/full-bleed-character-type"
    )
    assert result.spec["visual_design_profile_version"] == 1
    assert result.spec["template_version"] == "squid-generated-gtm@4"
    assert result.spec["asset_pack_version"] == "squid-local-approved@1"
    display_font_path = get_client_config("squid").font_display_file_path
    assert result.spec["font_status"] == (
        "bagoss_condensed_licensed"
        if display_font_path is not None and display_font_path.is_file()
        else "pretendard_fallback"
    )
    assert captured["slots"]["creative_family"] == "editorial_big_type"
    assert result.figma_template is None


@pytest.mark.asyncio
async def test_squid_milestone_uses_only_the_metric_copied_from_source(
    monkeypatch,
    tmp_path,
):
    captured = {}

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")

    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)

    result = await generate_news_card(
        client_id="squid",
        source_content="Squid crossed 5M swaps across connected chains.",
        source_url="https://x.com/squidrouter/status/2082889008385044897",
        output_dir=tmp_path,
        mock_mode=True,
        mock_response={
            **MOCK_SPEC,
            "headline": "스왑 500만 건을 넘었어요",
        },
        template_style="classic",
    )

    assert result.spec["creative_family"] == "milestone_metric"
    assert result.spec["visual_metric"] == "5m"
    assert captured["slots"]["visual_metric"] == "5m"
    assert result.figma_template is None


@pytest.mark.asyncio
async def test_squid_text_only_worldbuilding_never_invents_a_generated_scene(
    monkeypatch,
    tmp_path,
):
    rendered = False

    async def fake_render_png(**_kwargs):
        nonlocal rendered
        rendered = True

    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)

    with pytest.raises(ValueError, match="requires approved official media"):
        await generate_news_card(
            client_id="squid",
            source_content="Bouncing through the weekend like SQUIB.",
            source_url="https://x.com/squidrouter/status/2083583547353501977",
            output_dir=tmp_path,
            mock_mode=True,
            mock_response=MOCK_SPEC,
            template_style="classic",
        )

    assert rendered is False


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
            background_color="#B881DF",
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
            "visual_design_profile_id": "rogue/llm-profile",
            "visual_design_profile_version": 999,
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
    assert captured["slots"]["output_width"] == 1080
    assert captured["slots"]["output_height"] == 1080
    assert captured["viewport"] == (1080, 1080)
    assert captured["device_scale_factor"] == 1
    assert captured["slots"]["source_background_color"] == "#B881DF"
    assert result.template_style == "remix"
    assert result.requested_template_style == "remix"
    assert result.source_image_used is True
    assert result.source_image_url == "https://pbs.twimg.com/media/source.jpg?name=orig"
    assert result.source_image_sha256 == hashlib.sha256(b"image").hexdigest()
    assert result.spec["render_strategy"] == "source_remix"
    assert result.spec["channel_profile"] == "source_native"
    assert result.spec["visual_reference_pack_version"] == 2
    assert "visual_design_profile_id" not in result.spec
    assert "visual_design_profile_version" not in result.spec
    assert result.spec["template_version"] == "squid-source-remix@1"
    assert result.spec["asset_pack_version"] == "official-source-media@1"
    assert result.source_visual_path == str(tmp_path / "source_visual_cleaned.jpg")
    assert (tmp_path / "source_visual_cleaned.jpg").read_bytes() == b"cleaned"
    assert result.png_path.endswith("news_card_remix.png")
    manifest = json.loads(Path(result.manifest_path).read_text())
    assert manifest["source_image_url"] == result.source_image_url
    assert manifest["source_image_sha256"] == result.source_image_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_size", "expected_viewport"),
    [((1600, 900), (1200, 675)), ((900, 1600), (675, 1200))],
)
async def test_squid_remix_uses_the_official_source_aspect_for_the_primary_png(
    monkeypatch,
    tmp_path,
    source_size,
    expected_viewport,
):
    captured = {}

    async def fake_fetch_source_image(_url):
        return PreparedSourceImage(
            media_type="image/jpeg",
            base64_data="aW1hZ2U=",
            width=source_size[0],
            height=source_size[1],
            background_color="#B881DF",
        )

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch_source_image)
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)

    result = await generate_news_card(
        client_id="squid",
        source_content="A textless official Squid X creative with a landscape composition.",
        source_url="https://x.com/squidrouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg?name=orig",
        output_dir=tmp_path,
        mock_mode=True,
        mock_response={
            **MOCK_SPEC,
            "source_text_visible": False,
            "translation_regions": [],
        },
        template_style="remix",
    )

    assert captured["viewport"] == expected_viewport
    assert captured["device_scale_factor"] == 1
    assert captured["slots"]["output_width"] == expected_viewport[0]
    assert captured["slots"]["output_height"] == expected_viewport[1]
    assert result.spec["output_width"] == expected_viewport[0]
    assert result.spec["output_height"] == expected_viewport[1]


@pytest.mark.asyncio
async def test_remix_rejects_dimension_changing_cleanup_and_preserves_original(
    monkeypatch,
    tmp_path,
):
    captured = {}
    source = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="c291cmNl",
        width=480,
        height=320,
        background_color="#B881DF",
    )

    async def fake_fetch_source_image(_url):
        return source

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch_source_image)
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)
    monkeypatch.setattr(
        "core.orchestrator.generate_news_card_spec",
        lambda **_kwargs: {
            **MOCK_SPEC,
            "source_text_visible": True,
            "translation_regions": [{
                "source_text": "Original caption",
                "text": "원문 자막",
                "x": 30,
                "y": 80,
                "width": 40,
                "height": 10,
                "align": "center",
                "font_role": "display",
                "font_size": 5,
            }],
        },
    )
    monkeypatch.setattr(
        "core.orchestrator.clean_source_text",
        lambda _image, _regions: SimpleNamespace(
            image=PreparedSourceImage(
                media_type="image/jpeg",
                base64_data="Y3JvcHBlZA==",
                width=480,
                height=280,
            ),
            masked_pixels=19_200,
            detected_regions=({"x": 30, "y": 80, "width": 40, "height": 10},),
        ),
    )

    result = await generate_news_card(
        client_id="squid",
        source_content="A source whose cleanup implementation attempted to crop.",
        source_url="https://x.com/squidrouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg?name=orig",
        output_dir=tmp_path,
        mock_mode=True,
        mock_response={
            **MOCK_SPEC,
            "source_text_visible": True,
            "translation_regions": [{
                "source_text": "Original caption",
                "text": "원문 자막",
                "x": 30,
                "y": 80,
                "width": 40,
                "height": 10,
                "align": "center",
                "font_role": "display",
                "font_size": 5,
            }],
        },
        template_style="remix",
    )

    assert captured["slots"]["source_image_data_url"] == source.data_url
    assert captured["slots"]["source_background_color"] == "#B881DF"
    assert captured["slots"]["source_text_visible"] is False
    assert captured["slots"]["translation_regions"] == []
    assert result.spec["visual_localization_status"] == "cleanup_failed"
    assert result.source_visual_path is None
    assert not (tmp_path / "source_visual_cleaned.jpg").exists()


@pytest.mark.asyncio
async def test_remix_browser_layout_rejection_rerenders_untouched_original(
    monkeypatch,
    tmp_path,
):
    source = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="c291cmNl",
        width=480,
        height=320,
        background_color="#B881DF",
    )
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
    render_calls = []
    cached = []
    discarded = []

    async def fake_fetch_source_image(_url):
        return source

    async def fake_render_png(**kwargs):
        render_calls.append(kwargs)
        if len(render_calls) == 1:
            raise TranslationLayoutError("replacement overflow")
        kwargs["output_path"].write_bytes(b"original")

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch_source_image)
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)
    monkeypatch.setattr(
        "core.orchestrator.generate_news_card_spec",
        lambda **_kwargs: {
            **MOCK_SPEC,
            "source_text_visible": True,
            "translation_regions": [dict(region)],
        },
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
            detected_regions=({"x": 30, "y": 80, "width": 40, "height": 10},),
        ),
    )
    monkeypatch.setattr(
        "core.orchestrator.make_visual_localization_cache_key",
        lambda **_kwargs: "cache-key",
    )
    monkeypatch.setattr("core.orchestrator.get_visual_localization", lambda _key: None)
    monkeypatch.setattr(
        "core.orchestrator.put_visual_localization",
        lambda key, regions: cached.append((key, regions)),
    )
    monkeypatch.setattr(
        "core.orchestrator.discard_visual_localization",
        lambda key: discarded.append(key),
    )

    result = await generate_news_card(
        client_id="squid",
        source_content="A source whose Korean replacement cannot fit in Chromium.",
        source_url="https://x.com/squidrouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg?name=orig",
        output_dir=tmp_path,
        template_style="remix",
    )

    assert len(render_calls) == 2
    assert render_calls[0]["viewport"] == (480, 320)
    assert render_calls[1]["viewport"] == (480, 320)
    assert render_calls[0]["device_scale_factor"] == 1
    assert render_calls[1]["device_scale_factor"] == 1
    assert render_calls[0]["slots"]["source_image_data_url"].endswith("Y2xlYW5lZA==")
    assert render_calls[0]["slots"]["source_text_visible"] is True
    assert render_calls[1]["slots"]["source_image_data_url"] == source.data_url
    assert render_calls[1]["slots"]["source_text_visible"] is False
    assert render_calls[1]["slots"]["translation_regions"] == []
    assert render_calls[1]["slots"]["source_background_color"] == "#B881DF"
    assert cached and cached[0][0] == "cache-key"
    assert discarded == ["cache-key"]
    assert result.spec["visual_localization_status"] == "unsafe_placement"
    assert result.spec["source_text_visible"] is False
    assert result.spec["translation_regions"] == []
    assert result.source_visual_path is None
    assert Path(result.png_path).read_bytes() == b"original"
    assert not (tmp_path / "source_visual_cleaned.jpg").exists()
    manifest = json.loads(Path(result.manifest_path).read_text())
    assert manifest["source_visual_path"] is None
    assert manifest["spec"]["visual_localization_status"] == "unsafe_placement"
    assert manifest["spec"]["source_text_visible"] is False
    assert manifest["spec"]["translation_regions"] == []


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
async def test_remix_preserves_the_full_source_when_inpainting_is_unsafe(
    monkeypatch,
    tmp_path,
):
    captured = {}
    source = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="c291cmNl",
        width=480,
        height=320,
    )
    region = {
        "source_text": "voila",
        "text": "짜잔",
        "x": 40,
        "y": 82,
        "width": 20,
        "height": 10,
        "source_x": 40,
        "source_y": 82,
        "source_width": 20,
        "source_height": 10,
        "align": "center",
        "font_role": "display",
        "font_size": 5,
    }
    calls = []

    async def fake_fetch_source_image(_url):
        return source

    async def fake_render_png(**kwargs):
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")

    def fail_cleanup(image, regions):
        assert image is source
        assert regions[0]["text"] == "짜잔"
        calls.append("inpaint")
        raise SourceTextCleanupError("background reconstruction is unsafe")

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch_source_image)
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)
    monkeypatch.setattr("core.orchestrator.clean_source_text", fail_cleanup)

    result = await generate_news_card(
        client_id="squid",
        source_content="A lower caption whose textured background is unsafe to inpaint.",
        source_url="https://x.com/squidrouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg?name=orig",
        output_dir=tmp_path,
        mock_mode=True,
        mock_response={
            **MOCK_SPEC,
            "source_logo_visible": True,
            "source_text_visible": True,
            "translation_regions": [region],
        },
        template_style="remix",
    )

    assert calls == ["inpaint"]
    assert captured["slots"]["source_image_data_url"] == source.data_url
    assert captured["slots"]["source_image_width"] == 480
    assert captured["slots"]["source_image_height"] == 320
    assert captured["slots"]["source_text_visible"] is False
    assert captured["slots"]["translation_regions"] == []
    assert result.spec["visual_localization_status"] == "cleanup_failed"
    assert result.source_visual_path is None
    assert not (tmp_path / "source_visual_cleaned.jpg").exists()
    assert result.spec["source_image_width"] == 480
    assert result.spec["source_image_height"] == 320


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
        "_protected_regions": [
            {"kind": "character", "x": 0, "y": 0},
            {
                "kind": "other_visual",
                "x": 0,
                "y": 70,
                "width": 100,
                "height": 10,
                "_aggregate_band_piece": True,
            },
        ],
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
    assert captured_cache["regions"][0]["_protected_regions"][1][
        "_aggregate_band_piece"
    ] is True
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
async def test_squid_remix_without_official_visual_fails_closed(tmp_path):
    with pytest.raises(SourceImageError, match="requires a source image"):
        await generate_news_card(
            client_id="squid",
            source_content="A long enough source for a fail-closed smoke test.",
            output_dir=tmp_path,
            mock_mode=True,
            mock_response=MOCK_SPEC,
            template_style="remix",
        )


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
