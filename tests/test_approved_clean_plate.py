import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
from PIL import Image

import core.sources.approved_clean_plate as clean_plate_registry
from core.llm.news_card_pipeline import generate_news_card_spec
from core.orchestrator import generate_news_card
from core.sources.approved_clean_plate import (
    APPROVED_CLEAN_PLATE_CACHE_NAMESPACE,
    APPROVED_CLEAN_PLATE_METHOD,
    APPROVED_CLEAN_PLATE_REGISTRY_VERSION,
    ApprovedCleanPlate,
    ApprovedCleanPlateEntry,
    ApprovedCleanPlateError,
    ApprovedTranslationRegion,
    resolve_approved_clean_plate,
)
from core.sources.source_image import PreparedSourceImage, prepare_source_image_bytes


STATUS_URL = "https://x.com/squidrouter/status/123"
MEDIA_URL = "https://pbs.twimg.com/media/source.jpg?name=orig"


def _jpeg_bytes(color: tuple[int, int, int], size: tuple[int, int] = (96, 64)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="JPEG", quality=92)
    return output.getvalue()


def _region() -> ApprovedTranslationRegion:
    return ApprovedTranslationRegion(
        source_text="OFFICIAL COPY",
        text="승인된 한국어",
        x=10,
        y=12,
        width=80,
        height=18,
        font_size=8,
        text_color="#000000",
    )


def _install_synthetic_entry(
    monkeypatch,
    tmp_path,
    *,
    plate_sha256=None,
    region=None,
):
    source = prepare_source_image_bytes(_jpeg_bytes((190, 145, 224)))
    clean_plate_raw = _jpeg_bytes((194, 151, 227), (source.width, source.height))
    relative_path = Path("clients/squid/assets/approved-clean-plates/synthetic.jpg")
    plate_path = tmp_path / relative_path
    plate_path.parent.mkdir(parents=True)
    plate_path.write_bytes(clean_plate_raw)
    entry = ApprovedCleanPlateEntry(
        client_id="squid",
        source_url=STATUS_URL,
        source_image_url=MEDIA_URL,
        source_sha256=source.sha256,
        width=source.width,
        height=source.height,
        clean_plate_relative_path=str(relative_path),
        clean_plate_sha256=plate_sha256 or hashlib.sha256(clean_plate_raw).hexdigest(),
        approval_version="synthetic-review@1",
        translation_regions=(region or _region(),),
    )
    monkeypatch.setattr(clean_plate_registry, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        clean_plate_registry,
        "_APPROVED_CLEAN_PLATES",
        {("squid", STATUS_URL, MEDIA_URL): entry},
    )
    return source, clean_plate_raw, plate_path


def test_approved_clean_plate_requires_exact_source_and_plate_digests(monkeypatch, tmp_path):
    source, clean_plate_raw, _ = _install_synthetic_entry(monkeypatch, tmp_path)

    resolved = resolve_approved_clean_plate(
        "squid",
        STATUS_URL,
        MEDIA_URL,
        source,
    )

    assert resolved is not None
    assert base64.b64decode(resolved.image.base64_data) == clean_plate_raw
    assert resolved.source_sha256 == source.sha256
    assert resolved.clean_plate_sha256 == hashlib.sha256(clean_plate_raw).hexdigest()
    assert resolved.method == APPROVED_CLEAN_PLATE_METHOD
    assert resolved.registry_version == APPROVED_CLEAN_PLATE_REGISTRY_VERSION
    assert resolved.cache_namespace == APPROVED_CLEAN_PLATE_CACHE_NAMESPACE
    assert resolved.approval_version == "synthetic-review@1"
    assert resolved.render_regions()[0]["text"] == "승인된 한국어"


def test_registered_identity_is_canonicalized_before_lookup(monkeypatch, tmp_path):
    source, _clean_plate_raw, _ = _install_synthetic_entry(monkeypatch, tmp_path)

    resolved = resolve_approved_clean_plate(
        "squid",
        "https://x.com/SquidRouter/status/123/",
        "https://pbs.twimg.com/media/source.jpg?name=small",
        source,
    )

    assert resolved is not None
    assert resolved.source_sha256 == source.sha256


def test_unregistered_identity_does_not_read_registered_asset(
    monkeypatch,
    tmp_path,
):
    _source, _clean_plate_raw, plate_path = _install_synthetic_entry(monkeypatch, tmp_path)
    plate_path.unlink()
    unreadable_unregistered_source = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="not-base64!",
        width=1,
        height=1,
    )

    assert resolve_approved_clean_plate(
        "squid",
        "https://x.com/squidrouter/status/999",
        "https://pbs.twimg.com/media/other.jpg?name=orig",
        unreadable_unregistered_source,
    ) is None


def test_registered_identity_source_digest_mismatch_fails_closed(monkeypatch, tmp_path):
    _source, _clean_plate_raw, _ = _install_synthetic_entry(monkeypatch, tmp_path)
    changed_source = prepare_source_image_bytes(_jpeg_bytes((30, 30, 30)))

    with pytest.raises(ApprovedCleanPlateError, match="source SHA-256"):
        resolve_approved_clean_plate(
            "squid",
            STATUS_URL,
            MEDIA_URL,
            changed_source,
        )


@pytest.mark.parametrize(
    ("status_url", "media_url"),
    [
        ("https://x.com/squidrouter/status/999", MEDIA_URL),
        (STATUS_URL, "https://pbs.twimg.com/media/other.jpg?name=orig"),
        ("https://x.com.attacker.test/squidrouter/status/123", MEDIA_URL),
        (STATUS_URL, "https://pbs.twimg.com.attacker.test/media/source.jpg"),
    ],
)
def test_incorrect_status_or_media_identity_never_resolves_plate(
    monkeypatch,
    tmp_path,
    status_url,
    media_url,
):
    source, _clean_plate_raw, _ = _install_synthetic_entry(monkeypatch, tmp_path)

    assert resolve_approved_clean_plate(
        "squid",
        status_url,
        media_url,
        source,
    ) is None


def test_registered_clean_plate_hash_mismatch_fails_closed(monkeypatch, tmp_path):
    source, _clean_plate_raw, _ = _install_synthetic_entry(
        monkeypatch,
        tmp_path,
        plate_sha256="0" * 64,
    )

    with pytest.raises(ApprovedCleanPlateError, match="SHA-256 does not match"):
        resolve_approved_clean_plate("squid", STATUS_URL, MEDIA_URL, source)


def test_registered_clean_plate_rejects_claimed_source_dimensions(monkeypatch, tmp_path):
    source, _clean_plate_raw, _ = _install_synthetic_entry(monkeypatch, tmp_path)
    forged = PreparedSourceImage(
        media_type=source.media_type,
        base64_data=source.base64_data,
        width=source.width + 1,
        height=source.height,
        background_color=source.background_color,
    )

    with pytest.raises(ApprovedCleanPlateError, match="source dimensions"):
        resolve_approved_clean_plate("squid", STATUS_URL, MEDIA_URL, forged)


def test_registered_clean_plate_rejects_prepared_media_type(monkeypatch, tmp_path):
    source, _clean_plate_raw, _ = _install_synthetic_entry(monkeypatch, tmp_path)
    forged = PreparedSourceImage(
        media_type="image/png",
        base64_data=source.base64_data,
        width=source.width,
        height=source.height,
        background_color=source.background_color,
    )

    with pytest.raises(ApprovedCleanPlateError, match="normalized JPEG"):
        resolve_approved_clean_plate("squid", STATUS_URL, MEDIA_URL, forged)


def test_approved_copy_must_preserve_the_reviewed_visual_line_count(
    monkeypatch,
    tmp_path,
):
    source, _clean_plate_raw, _ = _install_synthetic_entry(
        monkeypatch,
        tmp_path,
        region=ApprovedTranslationRegion(
            source_text="OFFICIAL COPY",
            text="승인된\n한국어",
            x=10,
            y=12,
            width=80,
            height=18,
            font_size=8,
            text_color="#000000",
            source_line_count=1,
        ),
    )

    with pytest.raises(ApprovedCleanPlateError, match="copy is invalid"):
        resolve_approved_clean_plate("squid", STATUS_URL, MEDIA_URL, source)


def test_production_registry_has_no_synthesized_telegram_override():
    assert dict(clean_plate_registry._APPROVED_CLEAN_PLATES) == {}


def test_approved_visual_override_skips_sampled_discovery_and_audit(monkeypatch):
    calls = []

    class FakeAnthropic:
        def with_options(self, **_kwargs):
            return self

    def fake_create_message(_client, **kwargs):
        calls.append(kwargs)
        payload = {
            "label": "공식 업데이트",
            "date": "2026.08.03",
            "headline": "Squid 공식 소식을 전해요",
            "body_lines": ["원문에서 확인한 내용이에요"],
            "source_url": "ignored",
            "theme": "dark",
            "source_logo_visible": True,
            "source_text_visible": False,
            "translation_regions": [],
        }
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))]
        )

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr("core.llm.news_card_pipeline.create_message", fake_create_message)
    monkeypatch.setattr(
        "core.llm.news_card_pipeline._discover_visual_copy",
        lambda *_args, **_kwargs: pytest.fail("approved geometry must skip discovery"),
    )
    monkeypatch.setattr(
        "core.llm.news_card_pipeline._audit_visual_subtitle_placement",
        lambda *_args, **_kwargs: pytest.fail("approved geometry must skip placement audit"),
    )
    source = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data="aW1hZ2U=",
        width=1080,
        height=1080,
    )

    result = generate_news_card_spec(
        client_id="squid",
        source_content="An official source with enough factual context.",
        source_url="https://x.com/squidrouter/status/123",
        source_image=source,
        approved_visual_localization=[_region().as_render_region()],
    )

    assert len(calls) == 1
    assert result["visual_localization_status"] == "translated"
    assert result["source_text_visible"] is True
    assert result["translation_regions"][0]["text"] == "승인된 한국어"


@pytest.mark.asyncio
async def test_orchestrator_uses_approved_plate_without_generic_cleanup_or_cache(
    monkeypatch,
    tmp_path,
):
    source = PreparedSourceImage(
        media_type="image/jpeg",
        base64_data=base64.b64encode(b"source").decode("ascii"),
        width=1080,
        height=1080,
        background_color="#C39CE5",
    )
    clean_bytes = b"human-approved-clean-plate"
    approved = ApprovedCleanPlate(
        image=PreparedSourceImage(
            media_type="image/jpeg",
            base64_data=base64.b64encode(clean_bytes).decode("ascii"),
            width=1080,
            height=1080,
            background_color="#C39CE5",
        ),
        source_sha256=source.sha256,
        clean_plate_sha256=hashlib.sha256(clean_bytes).hexdigest(),
        approval_version="synthetic-review@1",
        translation_regions=(_region(),),
    )
    rendered = {}

    async def fake_fetch(_url):
        return source

    async def fake_render_png(**kwargs):
        rendered.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch)
    monkeypatch.setattr(
        "core.orchestrator.resolve_approved_clean_plate",
        lambda client_id, source_url, source_image_url, image: approved,
    )
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)
    monkeypatch.setattr(
        "core.orchestrator.clean_source_text",
        lambda *_args, **_kwargs: pytest.fail("approved plate must skip generic cleanup"),
    )
    monkeypatch.setattr(
        "core.orchestrator.make_visual_localization_cache_key",
        lambda **_kwargs: pytest.fail("approved plate must use a separate cache namespace"),
    )

    result = await generate_news_card(
        client_id="squid",
        source_content="Squid is live on Telegram.",
        source_url="https://x.com/squidrouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg?name=orig",
        output_dir=tmp_path,
        mock_mode=True,
        mock_response={
            "label": "공식 업데이트",
            "date": "2026.08.03",
            "headline": "Squid 공식 소식을 전해요",
            "body_lines": ["원문에서 확인한 내용이에요"],
            "source_url": "ignored",
            "theme": "dark",
            "source_logo_visible": True,
            "source_text_visible": False,
            "translation_regions": [],
        },
        template_style="remix",
    )

    assert rendered["slots"]["source_image_data_url"] == approved.image.data_url
    assert rendered["slots"]["translation_regions"][0]["text"] == "승인된 한국어"
    assert result.spec["visual_localization_method"] == APPROVED_CLEAN_PLATE_METHOD
    assert result.spec["visual_localization_version"] == "synthetic-review@1"
    assert (
        result.spec["visual_localization_cache_namespace"]
        == APPROVED_CLEAN_PLATE_CACHE_NAMESPACE
    )
    assert result.spec["visual_localization_status"] == "translated"
    assert result.source_visual_path == str(tmp_path / "source_visual_cleaned.jpg")
    assert (tmp_path / "source_visual_cleaned.jpg").read_bytes() == clean_bytes
    assert result.source_image_sha256 == source.sha256


@pytest.mark.asyncio
async def test_registered_identity_digest_mismatch_preserves_original_without_cleanup(
    monkeypatch,
    tmp_path,
):
    _registered_source, _clean_plate_raw, _ = _install_synthetic_entry(
        monkeypatch,
        tmp_path,
    )
    source = prepare_source_image_bytes(_jpeg_bytes((30, 30, 30)))
    rendered = {}

    async def fake_fetch(_url):
        return source

    async def fake_render_png(**kwargs):
        rendered.update(kwargs)
        kwargs["output_path"].write_bytes(b"png")

    monkeypatch.setattr("core.orchestrator.fetch_source_image", fake_fetch)
    monkeypatch.setattr("core.orchestrator.render_png", fake_render_png)
    monkeypatch.setattr(
        "core.orchestrator.clean_source_text",
        lambda *_args, **_kwargs: pytest.fail("broken approval must not use generic cleanup"),
    )
    monkeypatch.setattr(
        "core.orchestrator.make_visual_localization_cache_key",
        lambda **_kwargs: pytest.fail("broken approval must not use generic cache"),
    )

    result = await generate_news_card(
        client_id="squid",
        source_content="Squid is live on Telegram.",
        source_url=STATUS_URL,
        source_image_url=MEDIA_URL,
        output_dir=tmp_path,
        mock_mode=True,
        mock_response={
            "label": "공식 업데이트",
            "date": "2026.08.03",
            "headline": "Squid 공식 소식을 전해요",
            "body_lines": ["원문에서 확인한 내용이에요"],
            "source_url": "ignored",
            "theme": "dark",
            "source_logo_visible": True,
            "source_text_visible": True,
            "translation_regions": [_region().as_render_region()],
        },
        template_style="remix",
    )

    assert rendered["slots"]["source_image_data_url"] == source.data_url
    assert rendered["slots"]["source_text_visible"] is False
    assert rendered["slots"]["translation_regions"] == []
    assert result.spec["visual_localization_status"] == "cleanup_failed"
    assert result.source_visual_path is None
    assert not (tmp_path / "source_visual_cleaned.jpg").exists()
