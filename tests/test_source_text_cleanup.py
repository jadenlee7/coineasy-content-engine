import base64
from io import BytesIO

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.sources.source_image import prepare_source_image_bytes
from core.sources.source_text_cleanup import (
    SourceTextCleanupError,
    _detect_region_text_mask,
    clean_source_text,
)


def _caption_source():
    width, height = 240, 160
    image = Image.new("RGB", (width, height), "#d8c99f")
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (
                190 + (x * 19 + y * 7) % 35,
                165 + (x * 11 + y * 13) % 40,
                95 + (x * 5 + y * 17) % 45,
            )
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((74, 116, 166, 146), radius=7, fill="#080808")
    draw.text((92, 124), "chillin'", fill="#fff36b")
    output = BytesIO()
    image.save(output, format="JPEG", quality=94)
    return prepare_source_image_bytes(output.getvalue())


def _plain_shape_source(shape):
    image = Image.new("RGB", (240, 160), "#ddddcc")
    draw = ImageDraw.Draw(image)
    if shape == "rectangle":
        draw.rounded_rectangle((80, 62, 160, 100), radius=5, fill="#080808")
    elif shape == "circle":
        draw.ellipse((94, 56, 146, 108), fill="#080808")
    else:
        draw.rectangle((68, 72, 172, 90), fill="#080808")
    output = BytesIO()
    image.save(output, format="JPEG", quality=94)
    return prepare_source_image_bytes(output.getvalue())


def _decode(prepared):
    encoded = np.frombuffer(base64.b64decode(prepared.base64_data), dtype=np.uint8)
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)


def test_removes_a_text_shaped_caption_without_changing_the_whole_audit_box():
    source = _caption_source()
    cleaned = clean_source_text(source, [{
        "source_x": 25,
        "source_y": 68,
        "source_width": 50,
        "source_height": 28,
    }])

    before = _decode(source)
    after = _decode(cleaned.image)
    caption = np.s_[108:154, 60:180]
    before_dark = np.count_nonzero(cv2.cvtColor(before[caption], cv2.COLOR_BGR2GRAY) < 30)
    after_dark = np.count_nonzero(cv2.cvtColor(after[caption], cv2.COLOR_BGR2GRAY) < 30)
    assert cleaned.masked_pixels > 1_000
    assert after_dark < before_dark * 0.08

    outside = np.ones(before.shape[:2], dtype=bool)
    outside[105:156, 57:183] = False
    outside_delta = np.abs(before.astype(np.int16) - after.astype(np.int16))[outside]
    assert float(np.mean(outside_delta)) < 3.5


def test_recovers_a_complete_caption_when_the_audited_box_clips_its_top_left():
    source = _caption_source()
    cleaned = clean_source_text(source, [{
        # Deliberately shifted above/right of the actual 30.8%,72.5% caption.
        "source_x": 35,
        "source_y": 68,
        "source_width": 35,
        "source_height": 17,
    }])

    detected = cleaned.detected_regions[0]
    assert detected["x"] == pytest.approx(30.833, abs=0.01)
    assert detected["y"] == pytest.approx(72.5, abs=0.01)
    assert detected["width"] == pytest.approx(38.75, abs=0.01)
    assert detected["height"] == pytest.approx(19.375, abs=0.01)

    before = _decode(source)
    after = _decode(cleaned.image)
    caption = np.s_[112:153, 70:170]
    before_dark = np.count_nonzero(cv2.cvtColor(before[caption], cv2.COLOR_BGR2GRAY) < 30)
    after_dark = np.count_nonzero(cv2.cvtColor(after[caption], cv2.COLOR_BGR2GRAY) < 30)
    assert after_dark < before_dark * 0.08


def test_recovers_an_opaque_caption_panel_that_exactly_fills_the_audit_box():
    source = _caption_source()
    cleaned = clean_source_text(source, [{
        "source_x": 30.8333333333,
        "source_y": 72.5,
        "source_width": 38.75,
        "source_height": 19.375,
    }])

    detected = cleaned.detected_regions[0]
    assert detected["x"] == pytest.approx(30.833, abs=0.01)
    assert detected["y"] == pytest.approx(72.5, abs=0.01)
    assert detected["width"] == pytest.approx(38.75, abs=0.01)
    assert detected["height"] == pytest.approx(19.375, abs=0.01)

    before = _decode(source)
    after = _decode(cleaned.image)
    caption = np.s_[112:153, 70:170]
    before_dark = np.count_nonzero(cv2.cvtColor(before[caption], cv2.COLOR_BGR2GRAY) < 30)
    after_dark = np.count_nonzero(cv2.cvtColor(after[caption], cv2.COLOR_BGR2GRAY) < 30)
    assert cleaned.masked_pixels > 1_000
    assert after_dark < before_dark * 0.08


def test_does_not_expand_search_when_the_audited_box_has_no_text(monkeypatch):
    calls = []

    def fail_detection(gray):
        calls.append(gray.shape)
        raise SourceTextCleanupError("no reliable source-text mask found")

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_text_mask",
        fail_detection,
    )
    with pytest.raises(SourceTextCleanupError, match="no reliable source-text mask"):
        _detect_region_text_mask(
            np.zeros((100, 100, 3), dtype=np.uint8),
            {
                "source_x": 30,
                "source_y": 30,
                "source_width": 20,
                "source_height": 10,
            },
        )
    assert len(calls) == 1


def test_recovery_must_continue_the_clipped_seed_pixels(monkeypatch):
    calls = 0

    def disconnected_masks(gray):
        nonlocal calls
        calls += 1
        mask = np.zeros(gray.shape, dtype=np.uint8)
        if calls == 1:
            mask[2:8, 10:20] = 255  # clipped at the original right edge
        else:
            mask[2:10, 1:14] = 255  # overlapping bbox, different pixels
        return mask

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_text_mask",
        disconnected_masks,
    )
    with pytest.raises(SourceTextCleanupError, match="audited seed"):
        _detect_region_text_mask(
            np.zeros((100, 100, 3), dtype=np.uint8),
            {
                "source_x": 30,
                "source_y": 30,
                "source_width": 20,
                "source_height": 10,
            },
        )
    assert calls == 2


def test_panel_recovery_must_expand_beyond_the_inner_glyph_mask(monkeypatch):
    calls = 0

    def inner_mask_only(gray):
        nonlocal calls
        calls += 1
        mask = np.zeros(gray.shape, dtype=np.uint8)
        if calls == 1:
            mask[2:8, 5:15] = 255
        else:
            # Same absolute pixels inside the three-pixel recovery halo.
            mask[5:11, 8:18] = 255
        return mask

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_text_mask",
        inner_mask_only,
    )
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._has_nested_dark_caption_panel",
        lambda gray, mask: True,
    )
    with pytest.raises(SourceTextCleanupError, match="audited seed"):
        _detect_region_text_mask(
            np.zeros((100, 100, 3), dtype=np.uint8),
            {
                "source_x": 30,
                "source_y": 30,
                "source_width": 20,
                "source_height": 10,
            },
        )
    assert calls == 2


def test_fails_closed_when_no_reliable_lettering_mask_exists():
    image = Image.new("RGB", (180, 120), "#dddddd")
    output = BytesIO()
    image.save(output, format="JPEG")
    source = prepare_source_image_bytes(output.getvalue())

    with pytest.raises(SourceTextCleanupError, match="no reliable source-text mask"):
        clean_source_text(source, [{
            "source_x": 20,
            "source_y": 20,
            "source_width": 40,
            "source_height": 20,
        }])


def test_rejects_an_overly_large_cleanup_region():
    source = _caption_source()
    with pytest.raises(SourceTextCleanupError, match="outside conservative bounds"):
        clean_source_text(source, [{
            "source_x": 0,
            "source_y": 0,
            "source_width": 80,
            "source_height": 80,
        }])


@pytest.mark.parametrize("shape", ["rectangle", "circle", "bar"])
def test_does_not_erase_a_plain_dark_object_that_is_not_text(shape):
    source = _plain_shape_source(shape)
    with pytest.raises(SourceTextCleanupError, match="no reliable source-text mask"):
        clean_source_text(source, [{
            "source_x": 27.5,
            "source_y": 30,
            "source_width": 45,
            "source_height": 35,
        }])


def test_fails_closed_when_every_background_reconstruction_is_low_quality(monkeypatch):
    source = _caption_source()
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._inpaint_score",
        lambda _image, _mask: 999.0,
    )
    with pytest.raises(SourceTextCleanupError, match="not clean enough"):
        clean_source_text(source, [{
            "source_x": 25,
            "source_y": 68,
            "source_width": 50,
            "source_height": 28,
        }])
