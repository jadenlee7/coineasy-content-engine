import base64
from io import BytesIO

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

import core.sources.source_text_cleanup as source_text_cleanup
from core.sources.source_image import prepare_source_image_bytes
from core.sources.source_text_cleanup import (
    _MaskCandidate,
    SourceTextCleanupError,
    _canonicalize_consensus_mask,
    _detect_region_text_mask,
    _detect_text_mask,
    _unique_pairwise_compatible_clique,
    _validate_expected_line_structure,
    clean_source_text,
    crop_confirmed_lower_caption,
    probe_source_text,
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


def _scaled_caption_source(scale):
    base = _caption_source()
    image = Image.open(BytesIO(base64.b64decode(base.base64_data))).convert("RGB")
    image = image.resize(
        (image.width * scale, image.height * scale),
        Image.Resampling.LANCZOS,
    )
    output = BytesIO()
    image.save(output, format="PNG")
    return prepare_source_image_bytes(output.getvalue())


def _squid_style_caption_source(width):
    """Build the chillin'-style outlined caption without a binary fixture."""
    image = Image.new("RGB", (480, 320), (210, 214, 199))
    draw = ImageDraw.Draw(image)
    draw.ellipse((80, 20, 380, 310), fill=(238, 220, 50))
    draw.rectangle((0, 260, 480, 320), fill=(170, 125, 75))
    draw.text(
        (195, 267),
        "chillin’",
        font=ImageFont.load_default(size=29),
        fill=(255, 230, 60),
        stroke_width=3,
        stroke_fill=(0, 0, 0),
    )
    image = image.resize(
        (width, round(width * 2 / 3)),
        Image.Resampling.LANCZOS,
    )
    output = BytesIO()
    image.save(output, format="JPEG", quality=95)
    return prepare_source_image_bytes(output.getvalue())


def _two_line_outlined_caption_source():
    image = Image.new("RGB", (480, 320), (210, 214, 199))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=29)
    draw.text(
        (125, 230),
        "stack is love,",
        font=font,
        fill="white",
        stroke_width=3,
        stroke_fill="black",
    )
    draw.text(
        (130, 265),
        "stack is life.",
        font=font,
        fill="white",
        stroke_width=3,
        stroke_fill="black",
    )
    output = BytesIO()
    image.save(output, format="JPEG", quality=95)
    return prepare_source_image_bytes(output.getvalue())


def _bright_outline_on_dark_source():
    """Reproduce a bright subtitle whose outline joins a dark scene object."""
    image = Image.new("RGB", (720, 480), (220, 220, 210))
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 180, 620, 455), fill=(18, 18, 18))
    draw.rectangle((0, 455, 720, 479), fill=(184, 132, 72))
    draw.text(
        (300, 405),
        "voila",
        font=ImageFont.load_default(size=43),
        fill=(255, 235, 60),
        stroke_width=5,
        stroke_fill=(0, 0, 0),
    )
    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    return prepare_source_image_bytes(output.getvalue())


def _bright_decorative_dots_on_dark_source():
    image = Image.new("RGB", (720, 480), (220, 220, 210))
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 180, 620, 455), fill=(18, 18, 18))
    draw.rectangle((0, 455, 720, 479), fill=(184, 132, 72))
    for x in (310, 335, 360, 385, 410):
        draw.ellipse((x - 8, 416, x + 8, 432), fill=(255, 235, 60))
    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    return prepare_source_image_bytes(output.getvalue())


def _bright_outline_crossing_scene_seam_source():
    image = Image.new("RGB", (720, 480), (220, 220, 210))
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 180, 620, 455), fill=(18, 18, 18))
    draw.rectangle((0, 450, 720, 479), fill=(184, 132, 72))
    draw.text(
        (300, 405),
        "voila",
        font=ImageFont.load_default(size=43),
        fill=(255, 235, 60),
        stroke_width=5,
        stroke_fill=(0, 0, 0),
    )
    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    return prepare_source_image_bytes(output.getvalue())


def _bright_outline_audited_region(*, source_text="voila", extra=None):
    protections = [
        {
            "kind": "source_text",
            "source_index": 0,
            "x": 42,
            "y": 84,
            "width": 16,
            "height": 8,
        },
        {
            "kind": "product",
            "x": 10,
            "y": 37,
            "width": 80,
            "height": 58,
        },
    ]
    protections.extend(extra or [])
    return {
        "source_text": source_text,
        "text": "짜잔",
        "source_x": 40,
        "source_y": 82,
        "source_width": 20,
        "source_height": 10,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": protections,
    }


def _checker_source(width=200, height=100):
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for y in range(0, height, 8):
        for x in range(0, width, 8):
            if (x // 8 + y // 8) % 2:
                draw.rectangle((x, y, x + 7, y + 7), fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    return prepare_source_image_bytes(output.getvalue())


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


def test_bounded_consensus_recovers_when_the_seed_clips_the_same_caption(
    monkeypatch,
):
    source = _caption_source()
    calls = 0

    def count_detection(gray):
        nonlocal calls
        calls += 1
        return _detect_text_mask(gray)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_text_mask",
        count_detection,
    )
    cleaned = clean_source_text(source, [{
        "source_x": 37,
        "source_y": 68,
        "source_width": 32,
        "source_height": 16,
    }])

    detected = cleaned.detected_regions[0]
    assert 3 <= calls <= 16
    assert detected["x"] == pytest.approx(30.833, abs=0.01)
    assert detected["y"] == pytest.approx(72.5, abs=0.01)
    assert detected["width"] == pytest.approx(38.75, abs=0.01)
    assert detected["height"] == pytest.approx(19.375, abs=0.01)


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
    with pytest.raises(SourceTextCleanupError, match="consensus|ambiguous|unsupported"):
        _detect_region_text_mask(
            np.zeros((100, 100, 3), dtype=np.uint8),
            {
                "source_x": 30,
                "source_y": 30,
                "source_width": 20,
                "source_height": 10,
            },
        )
    assert 2 <= calls <= 16


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


def test_bounded_consensus_rejects_a_different_component(monkeypatch):
    calls = 0

    def disconnected_extended_mask(gray):
        nonlocal calls
        calls += 1
        mask = np.zeros(gray.shape, dtype=np.uint8)
        if calls == 1:
            mask[2:8, 0:12] = 255
        elif calls == 2:
            mask[2:8, 0:16] = 255
        else:
            mask[3:9, 20:26] = 255
        return mask

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_text_mask",
        disconnected_extended_mask,
    )
    with pytest.raises(SourceTextCleanupError, match="consensus|ambiguous|unsupported"):
        _detect_region_text_mask(
            np.zeros((100, 100, 3), dtype=np.uint8),
            {
                "source_x": 30,
                "source_y": 30,
                "source_width": 20,
                "source_height": 10,
            },
        )
    assert 2 <= calls <= 16


def test_bounded_consensus_never_accepts_multiply_clipped_candidates(monkeypatch):
    calls = 0

    def multiply_clipped_masks(gray):
        nonlocal calls
        calls += 1
        mask = np.zeros(gray.shape, dtype=np.uint8)
        if calls == 1:
            mask[0:6, 0:12] = 255
        else:
            mask[0:4, 0:16] = 255
        return mask

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_text_mask",
        multiply_clipped_masks,
    )
    with pytest.raises(SourceTextCleanupError, match="consensus|ambiguous|unsupported"):
        _detect_region_text_mask(
            np.zeros((100, 100, 3), dtype=np.uint8),
            {
                "source_x": 30,
                "source_y": 30,
                "source_width": 20,
                "source_height": 10,
            },
        )
    assert 2 <= calls <= 16


def test_complete_link_consensus_rejects_a_transitive_candidate_chain():
    # 0 agrees with 1 and 1 agrees with 2, but 0 and 2 disagree. A connected
    # component would accept all three; complete-link consensus must reject the
    # two competing maximal cliques {0, 1} and {1, 2}.
    adjacency = [{1}, {0, 2}, {1}]

    with pytest.raises(SourceTextCleanupError, match="ambiguous"):
        _unique_pairwise_compatible_clique(adjacency)


def test_complete_link_consensus_accepts_one_pairwise_clique():
    adjacency = [{1, 2}, {0, 2}, {0, 1}, set()]

    assert _unique_pairwise_compatible_clique(adjacency) == frozenset({0, 1, 2})


def test_coordinate_jitter_converges_to_identical_cleanup_bytes():
    source = _caption_source()
    outputs = []
    detections = []
    for x, y, width, height in (
        (35, 68, 35, 17),
        (37, 68, 32, 16),
        (36, 69, 33, 16),
    ):
        cleaned = clean_source_text(source, [{
            "source_x": x,
            "source_y": y,
            "source_width": width,
            "source_height": height,
        }])
        outputs.append(cleaned.image.base64_data)
        detections.append(cleaned.detected_regions)

    assert outputs[0] == outputs[1] == outputs[2]
    assert detections[0] == detections[1] == detections[2]


def test_detection_geometry_is_stable_across_source_resolutions(monkeypatch):
    # This test isolates detection geometry; the independent textured-ring
    # safety gate has dedicated coverage below.
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._validate_region_reconstruction_complexity",
        lambda _image, _mask: None,
    )
    results = []
    for scale in (1, 2, 4):
        source = _scaled_caption_source(scale)
        result = probe_source_text(source, [{
            "source_x": 35,
            "source_y": 68,
            "source_width": 35,
            "source_height": 17,
        }])
        results.append((
            result.detected_regions[0],
            result.masked_pixels / (source.width * source.height),
        ))

    baseline_box, baseline_fraction = results[0]
    for detected, mask_fraction in results[1:]:
        for key in ("x", "y", "width", "height"):
            assert detected[key] == pytest.approx(baseline_box[key], abs=0.75)
        assert mask_fraction == pytest.approx(baseline_fraction, rel=0.15)


def test_squid_style_tight_audit_consensus_is_resolution_invariant():
    audits = (
        (47, 82, 17, 8),
        (44, 81, 22, 9),
        (46, 84.5, 19, 7.5),
    )
    detections = []
    for width in (480, 900, 1200, 1500, 1800):
        source = _squid_style_caption_source(width)
        resolution_detections = []
        resolution_masked_pixels = []
        for x, y, region_width, region_height in audits:
            result = probe_source_text(source, [{
                "source_x": x,
                "source_y": y,
                "source_width": region_width,
                "source_height": region_height,
            }])
            resolution_detections.append(result.detected_regions[0])
            resolution_masked_pixels.append(result.masked_pixels)
        assert (
            resolution_detections[0]
            == resolution_detections[1]
            == resolution_detections[2]
        )
        assert (
            resolution_masked_pixels[0]
            == resolution_masked_pixels[1]
            == resolution_masked_pixels[2]
        )
        detections.append(resolution_detections[0])

    baseline = detections[0]
    for detected in detections[1:]:
        # All sources are normalized to the same 900px working scale. Integer
        # resampling at a few source widths can move an edge by one working
        # pixel (0.12 percentage point), but never enough to alter placement.
        for key in ("x", "y", "width", "height"):
            assert detected[key] == pytest.approx(baseline[key], abs=0.15)


def test_bright_lower_caption_fallback_uses_dominant_twenty_crop_consensus(
    monkeypatch,
):
    source = _bright_outline_on_dark_source()
    region = _bright_outline_audited_region()
    recoveries = 0
    original_recovery = source_text_cleanup._recover_light_lower_caption_mask

    def count_recovery(image, audited_region):
        nonlocal recoveries
        recoveries += 1
        return original_recovery(image, audited_region)

    monkeypatch.setattr(
        source_text_cleanup,
        "_recover_light_lower_caption_mask",
        count_recovery,
    )
    first = probe_source_text(source, [region])
    second = probe_source_text(source, [region])

    assert recoveries == 2
    assert first == second
    assert first.masked_pixels == 4_123
    assert first.detected_regions[0] == pytest.approx({
        "x": 41.7777777778,
        "y": 86.5,
        "width": 12.2222222222,
        "height": 6.6666666667,
    })

    cleaned = clean_source_text(source, [region])
    before = _decode(source)
    after = _decode(cleaned.image)
    caption = np.s_[390:460, 270:450]
    before_hsv = cv2.cvtColor(before[caption], cv2.COLOR_BGR2HSV)
    after_hsv = cv2.cvtColor(after[caption], cv2.COLOR_BGR2HSV)
    before_yellow = np.count_nonzero(
        (before_hsv[:, :, 0] >= 18)
        & (before_hsv[:, :, 0] <= 40)
        & (before_hsv[:, :, 1] >= 100)
        & (before_hsv[:, :, 2] >= 150)
    )
    after_yellow = np.count_nonzero(
        (after_hsv[:, :, 0] >= 18)
        & (after_hsv[:, :, 0] <= 40)
        & (after_hsv[:, :, 1] >= 100)
        & (after_hsv[:, :, 2] >= 150)
    )
    assert after_yellow < before_yellow * 0.05
    outside = np.ones(before.shape[:2], dtype=bool)
    outside[390:460, 270:450] = False
    outside_delta = np.abs(before.astype(np.int16) - after.astype(np.int16))[outside]
    assert float(np.mean(outside_delta)) < 0.2


def test_bright_lower_caption_consensus_survives_jpeg_and_resolution_changes():
    prepared = _bright_outline_on_dark_source()
    base = Image.open(
        BytesIO(base64.b64decode(prepared.base64_data))
    ).convert("RGB")
    region = _bright_outline_audited_region()
    results = []
    for width in (480, 720, 1080):
        resized = base.resize(
            (width, round(width * 2 / 3)),
            Image.Resampling.LANCZOS,
        )
        for quality in (40, 60, 80, 95):
            output = BytesIO()
            resized.save(
                output,
                format="JPEG",
                quality=quality,
                optimize=True,
            )
            source = prepare_source_image_bytes(output.getvalue())
            probe = probe_source_text(source, [region])
            results.append((
                probe.detected_regions[0],
                probe.masked_pixels / (source.width * source.height),
            ))

    baseline_box, baseline_fraction = results[0]
    for detected, mask_fraction in results[1:]:
        for key in ("x", "y", "width", "height"):
            assert detected[key] == pytest.approx(baseline_box[key], abs=0.25)
        assert mask_fraction == pytest.approx(baseline_fraction, rel=0.15)


def test_bright_lower_caption_fallback_rejects_competing_crop_masks(
    monkeypatch,
):
    source = _bright_outline_on_dark_source()
    region = _bright_outline_audited_region()
    calls = 0

    def alternating_core(gray):
        nonlocal calls
        calls += 1
        mask = np.zeros_like(gray, dtype=np.uint8)
        width = round(gray.shape[1] * (0.40 if calls % 2 else 0.34))
        left = (gray.shape[1] - width) // 2
        mask[34:68, left:left + width] = 255
        return mask

    monkeypatch.setattr(
        source_text_cleanup,
        "_detect_light_caption_core",
        alternating_core,
    )
    with pytest.raises(SourceTextCleanupError, match="raster consensus"):
        source_text_cleanup._recover_light_lower_caption_mask(
            source_text_cleanup._text_detection_working_image(_decode(source)),
            region,
        )


def test_bright_lower_caption_consensus_skips_one_clipped_lattice_vote(
    monkeypatch,
):
    source = _bright_outline_on_dark_source()
    region = _bright_outline_audited_region()
    original_core = source_text_cleanup._detect_light_caption_core
    calls = 0

    def clip_first_vote(gray):
        nonlocal calls
        calls += 1
        mask = original_core(gray).copy()
        if calls == 1:
            mask[gray.shape[0] // 2, 0] = 255
        return mask

    monkeypatch.setattr(
        source_text_cleanup,
        "_detect_light_caption_core",
        clip_first_vote,
    )

    mask, _, _, _ = source_text_cleanup._recover_light_lower_caption_mask(
        source_text_cleanup._text_detection_working_image(_decode(source)),
        region,
    )

    assert calls == 20
    assert np.count_nonzero(mask) > 0


def test_bright_lower_caption_consensus_rejects_too_many_clipped_votes(
    monkeypatch,
):
    source = _bright_outline_on_dark_source()
    region = _bright_outline_audited_region()
    original_core = source_text_cleanup._detect_light_caption_core
    calls = 0

    def clip_five_votes(gray):
        nonlocal calls
        calls += 1
        mask = original_core(gray).copy()
        if calls <= 5:
            mask[gray.shape[0] // 2, 0] = 255
        return mask

    monkeypatch.setattr(
        source_text_cleanup,
        "_detect_light_caption_core",
        clip_five_votes,
    )

    with pytest.raises(SourceTextCleanupError, match="dominant raster consensus"):
        source_text_cleanup._recover_light_lower_caption_mask(
            source_text_cleanup._text_detection_working_image(_decode(source)),
            region,
        )

    assert calls == 20


def test_bright_lower_caption_fallback_rejects_decorative_dot_rows(
    monkeypatch,
):
    source = _bright_decorative_dots_on_dark_source()
    region = _bright_outline_audited_region()

    def fail_standard_detection(_image, _region):
        raise SourceTextCleanupError("standard mask failed")

    monkeypatch.setattr(
        source_text_cleanup,
        "_detect_audited_region_text_mask",
        fail_standard_detection,
    )
    with pytest.raises(SourceTextCleanupError, match="standard mask failed"):
        probe_source_text(source, [region])


@pytest.mark.parametrize(
    "source_text",
    [
        "Squid",
        "Squid!",
        "Squid®",
        "squid router™",
        "@squidrouter",
        "https://squidrouter.com",
        "squidrouter.io",
        "squidrouter.xyz/path",
    ],
)
def test_bright_lower_caption_fallback_never_targets_brand_or_link_copy(
    monkeypatch,
    source_text,
):
    source = _bright_outline_on_dark_source()

    def fail_standard_detection(_image, _region):
        raise SourceTextCleanupError("standard mask failed")

    monkeypatch.setattr(
        source_text_cleanup,
        "_detect_audited_region_text_mask",
        fail_standard_detection,
    )
    with pytest.raises(SourceTextCleanupError, match="standard mask failed"):
        probe_source_text(
            source,
            [_bright_outline_audited_region(source_text=source_text)],
        )


def test_bright_lower_caption_outline_cannot_cross_a_logo_protection():
    source = _bright_outline_on_dark_source()
    region = _bright_outline_audited_region(extra=[{
        "kind": "logo",
        "x": 43,
        "y": 85,
        "width": 8,
        "height": 8,
    }])
    with pytest.raises(SourceTextCleanupError, match="protected logo"):
        probe_source_text(source, [region])


def test_bright_lower_caption_outline_rejects_separate_dark_detail():
    image = np.full((100, 180, 3), 120, dtype=np.uint8)
    core = np.zeros((100, 180), dtype=np.uint8)
    for x in (55, 68, 81, 94, 107):
        core[40:70, x:x + 7] = 255
    provenance = core.copy()
    image[provenance > 0] = 240
    outline = (
        cv2.dilate(
            core,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        ) > 0
    ) & (core == 0)
    image[outline] = 0

    source_text_cleanup._expand_light_caption_outline(
        image,
        core,
        provenance,
    )

    image_with_detail = image.copy()
    image_with_detail[32:36, 82:88] = 0
    with pytest.raises(
        SourceTextCleanupError,
        match="outline lacks local pixel support",
    ):
        source_text_cleanup._expand_light_caption_outline(
            image_with_detail,
            core,
            provenance,
        )


def _crop_safe_bright_outline_region():
    region = _bright_outline_audited_region()
    region["_protected_regions"][1] = {
        "kind": "character",
        "x": 10,
        "y": 8,
        "width": 80,
        "height": 84,
    }
    return region


def _validated_carved_aggregate_region(source):
    from core.llm.news_card_pipeline import _carve_aggregate_bottom_visual_band

    probe = source_text_cleanup.probe_light_lower_caption(
        source,
        [_bright_outline_audited_region()],
    )
    anchor = dict(probe.detected_regions[0])
    carved = _carve_aggregate_bottom_visual_band(
        [
            {"kind": "character", "x": 21, "y": 8, "width": 54, "height": 84},
            {"kind": "product", "x": 30, "y": 45, "width": 42, "height": 47},
            {"kind": "other_visual", "x": 0, "y": 70, "width": 100, "height": 30},
        ],
        anchor,
        source,
    )
    # The placement validator converts its unforgeable in-memory sentinel to
    # this cache-safe private bool. Source cleanup sees only that validated
    # representation.
    for protected in carved:
        if "_aggregate_band_piece" in protected:
            protected["_aggregate_band_piece"] = True
    padding_x = 3 / source.width * 100
    padding_y = 3 / source.height * 100
    return {
        "source_text": "voila",
        "text": "짜잔",
        "x": anchor["x"],
        "y": anchor["y"],
        "width": anchor["width"],
        "height": anchor["height"],
        "source_x": anchor["x"] - padding_x,
        "source_y": anchor["y"] - padding_y,
        "source_width": anchor["width"] + padding_x * 2,
        "source_height": anchor["height"] + padding_y * 2,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [
            {"kind": "source_text", "source_index": 0, **anchor},
            *carved,
        ],
    }


def test_confirmed_bright_lower_caption_can_crop_after_unsafe_inpaint():
    source = _bright_outline_on_dark_source()
    result = crop_confirmed_lower_caption(
        source,
        [_crop_safe_bright_outline_region()],
    )

    assert result.strategy == "lower_crop"
    assert result.image.width == 720
    assert result.image.height == 407
    assert result.masked_pixels == 720 * (480 - 407)
    assert result.detected_regions[0] == pytest.approx({
        "x": 41.7777777778,
        "y": 90.1375921376,
        "width": 12.2222222222,
        "height": 7.8624078624,
    })
    assert (
        result.detected_regions[0]["y"]
        + result.detected_regions[0]["height"]
        == pytest.approx(98.0)
    )
    # The original caption starts below the retained raster; this path never
    # paints over it or exports its pixels into the Figma source layer.
    assert result.image.height < round(source.height * 0.86)


def test_confirmed_lower_crop_accepts_authenticated_four_piece_scene_band():
    source = _bright_outline_on_dark_source()
    region = _validated_carved_aggregate_region(source)

    result = crop_confirmed_lower_caption(source, [region])

    assert result.strategy == "lower_crop"
    assert result.image.height == 407
    assert len([
        protected
        for protected in region["_protected_regions"]
        if protected.get("_aggregate_band_piece") is True
    ]) == 4


def test_normalization_preserves_authenticated_scene_band_partition():
    from core.llm.news_card_pipeline import _normalize_visual_localization

    source = _bright_outline_on_dark_source()
    region = _validated_carved_aggregate_region(source)
    normalized = _normalize_visual_localization(
        {
            "source_text_visible": True,
            "translation_regions": [region],
        },
        "squid",
        True,
        source.width,
        source.height,
        require_audit_metadata=True,
    )

    normalized_region = normalized["translation_regions"][0]
    assert normalized_region["source_x"] == region["source_x"]
    assert normalized_region["source_y"] == region["source_y"]
    assert normalized_region["source_width"] == region["source_width"]
    assert normalized_region["source_height"] == region["source_height"]
    result = crop_confirmed_lower_caption(source, [normalized_region])
    assert result.strategy == "lower_crop"
    assert result.image.height == 407


@pytest.mark.parametrize("mutation", ["missing_marker", "geometry_drift"])
def test_confirmed_lower_crop_rejects_unauthenticated_scene_band_partition(
    mutation,
):
    source = _bright_outline_on_dark_source()
    region = _validated_carved_aggregate_region(source)
    pieces = [
        protected
        for protected in region["_protected_regions"]
        if protected.get("_aggregate_band_piece") is True
    ]
    if mutation == "missing_marker":
        pieces[0].pop("_aggregate_band_piece")
    else:
        pieces[0]["width"] -= 0.01

    with pytest.raises(
        SourceTextCleanupError,
        match="crop intersects protected other_visual",
    ):
        crop_confirmed_lower_caption(source, [region])


def test_confirmed_lower_crop_still_enforces_independent_other_visual():
    source = _bright_outline_on_dark_source()
    region = _validated_carved_aggregate_region(source)
    region["_protected_regions"].append({
        "kind": "other_visual",
        "x": 45,
        "y": 90,
        "width": 10,
        "height": 8,
    })

    with pytest.raises(
        SourceTextCleanupError,
        match="crop intersects protected other_visual",
    ):
        crop_confirmed_lower_caption(source, [region])


def test_multi_substrate_bright_caption_defers_instead_of_smearing():
    source = _bright_outline_crossing_scene_seam_source()
    region = _crop_safe_bright_outline_region()

    with pytest.raises(
        SourceTextCleanupError,
        match="complex for clean lower-caption reconstruction",
    ):
        clean_source_text(source, [region])
    cropped = crop_confirmed_lower_caption(source, [region])
    assert cropped.strategy == "lower_crop"
    assert cropped.image.height < source.height


def test_scene_seam_requires_actual_bright_caption_recovery():
    source = _bright_outline_crossing_scene_seam_source()
    region = _crop_safe_bright_outline_region()
    image, mask, _, used_bright_recovery = (
        source_text_cleanup._prepare_cleanup_mask(source, [region])
    )

    assert used_bright_recovery is True
    assert source_text_cleanup._lower_caption_background_needs_crop(
        image,
        mask,
        [region],
        used_light_caption_recovery=True,
    ) is True
    assert source_text_cleanup._lower_caption_background_needs_crop(
        image,
        mask,
        [region],
        used_light_caption_recovery=False,
    ) is False


def test_lower_caption_crop_rejects_non_substrate_content_in_removed_band():
    source = _bright_outline_on_dark_source()
    region = _crop_safe_bright_outline_region()
    region["_protected_regions"].append({
        "kind": "logo",
        "x": 44,
        "y": 90,
        "width": 12,
        "height": 7,
    })

    with pytest.raises(
        SourceTextCleanupError,
        match="crop intersects protected logo",
    ):
        crop_confirmed_lower_caption(source, [region])


def test_lower_caption_crop_rejects_new_logo_overlap_after_reframing():
    source = _bright_outline_on_dark_source()
    region = _crop_safe_bright_outline_region()
    region["_protected_regions"].append({
        "kind": "logo",
        "x": 41,
        "y": 74,
        "width": 16,
        "height": 9,
    })

    with pytest.raises(
        SourceTextCleanupError,
        match="translation overlaps protected logo",
    ):
        crop_confirmed_lower_caption(source, [region])


def test_lower_caption_crop_allows_a_small_product_tail_within_cap():
    source = _bright_outline_on_dark_source()
    region = _bright_outline_audited_region()

    result = crop_confirmed_lower_caption(source, [region])

    assert result.strategy == "lower_crop"


def test_lower_caption_crop_product_tail_cap_is_exactly_eighteen_percent():
    region = {
        "_source_index": 0,
        "_protected_regions": [
            {
                "kind": "source_text",
                "source_index": 0,
                "x": 40,
                "y": 84,
                "width": 20,
                "height": 8,
            },
            {"kind": "product", "x": 0, "y": 0, "width": 100, "height": 100},
        ],
    }
    source_text_cleanup._validate_lower_caption_crop_protection(
        region,
        100,
        100,
        82,
        (40, 70, 60, 80),
    )

    with pytest.raises(
        SourceTextCleanupError,
        match="crop removes protected product",
    ):
        source_text_cleanup._validate_lower_caption_crop_protection(
            region,
            100,
            100,
            81,
            (40, 70, 60, 80),
        )


def test_lower_caption_crop_is_never_used_for_multiple_regions():
    source = _bright_outline_on_dark_source()
    region = _crop_safe_bright_outline_region()

    with pytest.raises(
        SourceTextCleanupError,
        match="exactly one audited region",
    ):
        crop_confirmed_lower_caption(source, [region, dict(region)])


def test_squid_style_audit_jitter_is_stable_with_production_metadata():
    """Exercise the strict path used by the 480x320 live `chillin'` creative."""
    source = _squid_style_caption_source(480)
    audits = (
        (47, 82, 17, 8),
        (44, 81, 22, 9),
        (46, 84.5, 19, 7.5),
    )
    results = []
    for x, y, width, height in audits:
        results.append(probe_source_text(source, [{
            # Production preserves the broader deterministic discovery phrase
            # as source_* while each independent placement audit supplies the
            # tighter detector/protection row below.
            "source_x": 30,
            "source_y": 82,
            "source_width": 40,
            "source_height": 12,
            "_source_index": 0,
            "_source_line_count": 1,
            "_protected_regions": [
                {
                    "kind": "source_text",
                    "source_index": 0,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                },
                {
                    "kind": "character",
                    "x": 6,
                    "y": 6,
                    "width": 78,
                    "height": 86,
                },
                {"kind": "face", "x": 30, "y": 20, "width": 30, "height": 28},
                {"kind": "logo", "x": 88, "y": 4, "width": 8, "height": 8},
            ],
        }]))

    assert results[0].masked_pixels > 1_000
    assert (
        results[0].masked_pixels
        == results[1].masked_pixels
        == results[2].masked_pixels
    )
    assert (
        results[0].detected_regions
        == results[1].detected_regions
        == results[2].detected_regions
    )
    assert (
        results[0].mask_sha256
        == results[1].mask_sha256
        == results[2].mask_sha256
    )
    assert len(results[0].mask_sha256) == 64
    assert int(results[0].mask_sha256, 16) >= 0


@pytest.mark.parametrize(
    "anchor",
    ((33, 82, 34, 10), (30, 82, 40, 12)),
)
def test_equal_padded_audit_and_discovery_uses_independent_consensus(anchor):
    source = _squid_style_caption_source(480)
    x, y, width, height = anchor
    result = probe_source_text(source, [{
        "source_x": x,
        "source_y": y,
        "source_width": width,
        "source_height": height,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [
            {
                "kind": "source_text",
                "source_index": 0,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            },
            {"kind": "character", "x": 6, "y": 6, "width": 78, "height": 86},
            {"kind": "face", "x": 30, "y": 20, "width": 30, "height": 28},
            {"kind": "logo", "x": 88, "y": 4, "width": 8, "height": 8},
        ],
    }])

    assert result.masked_pixels > 1_000
    assert result.detected_regions[0]["width"] < width


def test_equal_audit_and_discovery_cannot_pass_with_one_crop(monkeypatch):
    source = _checker_source(100, 100)
    successful_calls = 0

    def only_first_crop_succeeds(_image, _region):
        nonlocal successful_calls
        if successful_calls:
            raise SourceTextCleanupError("no second crop")
        successful_calls += 1
        mask = np.zeros((10, 20), dtype=np.uint8)
        mask[2:8, 2:18] = 255
        return mask, (30, 30), (32, 32, 48, 38)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_region_text_mask",
        only_first_crop_succeeds,
    )
    region = {
        "source_x": 30,
        "source_y": 30,
        "source_width": 20,
        "source_height": 10,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [{
            "kind": "source_text",
            "source_index": 0,
            "x": 30,
            "y": 30,
            "width": 20,
            "height": 10,
        }],
    }

    with pytest.raises(
        SourceTextCleanupError,
        match="lacks independent mask consensus",
    ):
        probe_source_text(source, [region])


def test_canonical_consensus_redetection_failure_is_closed(monkeypatch):
    mask = np.zeros((10, 20), dtype=np.uint8)
    mask[2:8, 2:18] = 255
    candidate = _MaskCandidate(
        mask=mask,
        origin=(30, 40),
        detected=(32, 42, 48, 48),
        crop=(30, 40, 50, 50),
        seed_containment=1.0,
        candidate_seed_support=1.0,
    )
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_text_mask",
        lambda _gray: (_ for _ in ()).throw(
            SourceTextCleanupError("no canonical mask")
        ),
    )

    with pytest.raises(SourceTextCleanupError, match="canonical.*failed"):
        _canonicalize_consensus_mask(
            np.zeros((100, 100, 3), dtype=np.uint8),
            candidate,
            [candidate, candidate],
            frozenset({0, 1}),
            2,
        )


def test_canonical_consensus_must_agree_with_every_clique_mask(monkeypatch):
    consensus_mask = np.zeros((10, 20), dtype=np.uint8)
    consensus_mask[2:8, 2:18] = 255
    candidate = _MaskCandidate(
        mask=consensus_mask,
        origin=(30, 40),
        detected=(32, 42, 48, 48),
        crop=(30, 40, 50, 50),
        seed_containment=1.0,
        candidate_seed_support=1.0,
    )
    incompatible = np.zeros((12, 22), dtype=np.uint8)
    incompatible[3, 3:19] = 255
    incompatible[8, 3:19] = 255
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_text_mask",
        lambda _gray: incompatible,
    )

    with pytest.raises(SourceTextCleanupError, match="disagrees with consensus"):
        _canonicalize_consensus_mask(
            np.zeros((100, 100, 3), dtype=np.uint8),
            candidate,
            [candidate, candidate],
            frozenset({0, 1}),
            2,
        )


def test_private_source_line_count_validates_visual_rows_without_ocr_newlines():
    two_lines = np.zeros((30, 60), dtype=np.uint8)
    two_lines[2:8, 5:55] = 255
    two_lines[19:25, 5:55] = 255

    _validate_expected_line_structure(two_lines, {
        "source_text": "OCR omitted the visual line break",
        "_source_line_count": 2,
    })

    with pytest.raises(SourceTextCleanupError, match="line count"):
        _validate_expected_line_structure(two_lines, {
            "source_text": "OCR formatting must not collapse two visual rows",
            "_source_line_count": 1,
        })

    one_line = np.zeros((30, 60), dtype=np.uint8)
    one_line[10:18, 5:55] = 255
    with pytest.raises(SourceTextCleanupError, match="line count"):
        _validate_expected_line_structure(one_line, {
            "source_text": "OCR omitted the visual line break",
            "_source_line_count": 2,
        })


@pytest.mark.parametrize("value", [True, 0, 5, 1.5, "2"])
def test_private_source_line_count_fails_closed_when_malformed(value):
    mask = np.zeros((20, 40), dtype=np.uint8)
    mask[5:12, 5:35] = 255

    with pytest.raises(SourceTextCleanupError, match="metadata"):
        _validate_expected_line_structure(mask, {"_source_line_count": value})


def test_probe_validates_the_same_mask_without_running_inpainting(monkeypatch):
    source = _caption_source()
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._exemplar_inpaint",
        lambda _image, _mask: pytest.fail("probe must not inpaint"),
    )

    result = probe_source_text(source, [{
        "source_x": 37,
        "source_y": 68,
        "source_width": 32,
        "source_height": 16,
    }])

    assert result.masked_pixels > 1_000
    assert result.detected_regions[0]["x"] == pytest.approx(30.833, abs=0.01)


def test_complex_background_guard_is_evaluated_per_distant_region(monkeypatch):
    source = _checker_source()

    def stable_small_mask(image, region):
        image_height, image_width = image.shape[:2]
        left = int(image_width * region["source_x"] / 100)
        top = int(image_height * region["source_y"] / 100)
        right = int(image_width * (
            region["source_x"] + region["source_width"]
        ) / 100)
        bottom = int(image_height * (
            region["source_y"] + region["source_height"]
        ) / 100)
        mask = np.zeros((bottom - top, right - left), dtype=np.uint8)
        mask[3:-3, 3:-3] = 255
        return mask, (left, top), (left + 3, top + 3, right - 3, bottom - 3)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_region_text_mask",
        stable_small_mask,
    )
    result = probe_source_text(source, [
        {"source_x": 5, "source_y": 40, "source_width": 20, "source_height": 20},
        {"source_x": 75, "source_y": 40, "source_width": 20, "source_height": 20},
    ])

    assert len(result.detected_regions) == 2
    assert result.masked_pixels > 0


@pytest.mark.parametrize(
    "protected_kind",
    ["logo", "face", "product_ui", "token_icon", "other_text", "other_visual"],
)
def test_final_dilated_mask_cannot_touch_a_non_substrate_visual(
    monkeypatch,
    protected_kind,
):
    source = _checker_source(100, 100)

    def mask_reaching_audit_edge(_image, _region):
        mask = np.zeros((10, 20), dtype=np.uint8)
        mask[2:8, 2:19] = 255
        return mask, (30, 30), (32, 32, 49, 38)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        mask_reaching_audit_edge,
    )
    region = {
        "source_x": 30,
        "source_y": 30,
        "source_width": 20,
        "source_height": 10,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [
            {
                "kind": "source_text", "source_index": 0,
                "x": 30, "y": 30, "width": 20, "height": 10,
            },
            {
                "kind": protected_kind,
                "x": 49, "y": 30, "width": 5, "height": 10,
            },
        ],
    }

    with pytest.raises(
        SourceTextCleanupError,
        match=f"protected {protected_kind}",
    ):
        probe_source_text(source, [region])


def test_final_dilated_mask_must_stay_inside_bounded_audit_recovery(monkeypatch):
    source = _checker_source(100, 100)

    def mask_escaping_left(_image, _region):
        mask = np.zeros((10, 28), dtype=np.uint8)
        mask[1:9, 1:27] = 255
        return mask, (15, 30), (16, 31, 42, 39)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        mask_escaping_left,
    )
    region = {
        "source_x": 30,
        "source_y": 30,
        "source_width": 20,
        "source_height": 10,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [{
            "kind": "source_text", "source_index": 0,
            "x": 30, "y": 30, "width": 20, "height": 10,
        }],
    }

    with pytest.raises(
        SourceTextCleanupError,
        match="audited cleanup envelope|discovery anchor",
    ):
        probe_source_text(source, [region])


def test_partial_phrase_mask_fails_audited_row_coverage(monkeypatch):
    source = _checker_source(100, 100)

    def partial_mask(_image, _region):
        mask = np.zeros((10, 20), dtype=np.uint8)
        mask[2:8, 1:3] = 255
        mask[2:8, 16:18] = 255
        return mask, (30, 30), (31, 32, 48, 38)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        partial_mask,
    )
    region = {
        "source_x": 30,
        "source_y": 30,
        "source_width": 20,
        "source_height": 10,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [{
            "kind": "source_text", "source_index": 0,
            "x": 30, "y": 30, "width": 20, "height": 10,
        }],
    }

    with pytest.raises(SourceTextCleanupError, match="audited region|audited visual row"):
        probe_source_text(source, [region])


def test_complementary_partial_rows_fail_actual_row_mask_coverage(monkeypatch):
    source = _checker_source(100, 100)

    def complementary_halves(_image, _region):
        mask = np.zeros((20, 20), dtype=np.uint8)
        # The combined bounds span nearly the whole audited phrase, but neither
        # visible row contains a complete mask. A global-bbox check accepts
        # this shape and leaves half of each English line untouched.
        mask[1:6, 1:11] = 255
        mask[13:18, 9:19] = 255
        return mask, (30, 30), (31, 31, 49, 48)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        complementary_halves,
    )
    region = {
        "source_x": 30,
        "source_y": 30,
        "source_width": 20,
        "source_height": 20,
        "_source_index": 0,
        "_source_line_count": 2,
        "_protected_regions": [
            {
                "kind": "source_text", "source_index": 0,
                "x": 30, "y": 30, "width": 20, "height": 8,
            },
            {
                "kind": "source_text", "source_index": 0,
                "x": 30, "y": 42, "width": 20, "height": 8,
            },
        ],
    }

    with pytest.raises(SourceTextCleanupError, match="audited visual row"):
        probe_source_text(source, [region])


def test_two_line_audited_rows_are_detected_atomically_and_deterministically():
    source = _two_line_outlined_caption_source()
    audit_variants = (
        ((24.5, 72.5, 36.0, 11.5), (25.5, 83.2, 33.5, 10.5)),
        ((24.0, 72.0, 37.0, 12.0), (25.0, 83.0, 34.5, 11.0)),
        ((25.0, 73.0, 35.0, 10.5), (26.0, 83.5, 32.5, 10.0)),
    )
    results = []
    for rows in audit_variants:
        region = {
            # One broader discovery phrase remains fixed while two independent
            # row audits jitter inside it.
            "source_x": 20,
            "source_y": 70,
            "source_width": 45,
            "source_height": 25,
            "_source_index": 0,
            "_source_line_count": 2,
            "_protected_regions": [
                {
                    "kind": "source_text",
                    "source_index": 0,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                }
                for x, y, width, height in rows
            ],
        }
        results.append(probe_source_text(source, [region]))

    assert results[0].masked_pixels > 1_000
    assert (
        results[0].masked_pixels
        == results[1].masked_pixels
        == results[2].masked_pixels
    )
    assert (
        results[0].detected_regions
        == results[1].detected_regions
        == results[2].detected_regions
    )


@pytest.mark.parametrize("width", [480, 720, 900, 1200])
@pytest.mark.parametrize("quality", [75, 85, 95])
def test_large_flat_two_line_caption_is_stable_across_resolution_and_jpeg(
    width,
    quality,
):
    base = _two_line_outlined_caption_source()
    image = Image.open(
        BytesIO(base64.b64decode(base.base64_data))
    ).convert("RGB")
    image = image.resize(
        (width, round(width * 2 / 3)),
        Image.Resampling.LANCZOS,
    )
    output = BytesIO()
    image.save(output, format="JPEG", quality=quality)
    source = prepare_source_image_bytes(output.getvalue())
    region = {
        "source_x": 20,
        "source_y": 70,
        "source_width": 45,
        "source_height": 25,
        "_source_index": 0,
        "_source_line_count": 2,
        "_protected_regions": [
            {
                "kind": "source_text", "source_index": 0,
                "x": 24.5, "y": 72.5, "width": 36.0, "height": 11.5,
            },
            {
                "kind": "source_text", "source_index": 0,
                "x": 25.5, "y": 83.2, "width": 33.5, "height": 10.5,
            },
        ],
    }

    assert probe_source_text(source, [region]).masked_pixels > 1_000


@pytest.mark.parametrize("protected_kind", ["character", "limb", "product"])
def test_caption_substrate_requires_verified_source_pixels(
    monkeypatch,
    protected_kind,
):
    source = _checker_source(100, 100)
    region = {
        "source_x": 30,
        "source_y": 30,
        "source_width": 20,
        "source_height": 10,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [
            {
                "kind": "source_text", "_source_index": 0,
                "x": 30, "y": 30, "width": 20, "height": 10,
            },
                {
                    "kind": protected_kind,
                    "x": 49, "y": 30, "width": 6, "height": 10,
                },
        ],
    }

    def contained_mask(_image, _region):
        mask = np.zeros((10, 20), dtype=np.uint8)
        mask[2:8, 2:18] = 255
        return mask, (30, 30), (32, 32, 48, 38)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        contained_mask,
    )
    assert probe_source_text(source, [region]).masked_pixels > 0

    # The protected object is already behind these verified source pixels, and
    # dilation adds only a narrow edge, so it remains a valid caption substrate.
    def existing_substrate_mask(_image, _region):
        mask = np.zeros((10, 24), dtype=np.uint8)
        mask[2:8, 4:24] = 255
        return mask, (28, 30), (32, 32, 52, 38)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        existing_substrate_mask,
    )
    assert probe_source_text(source, [region]).masked_pixels > 0

    # Here only the newly dilated edge reaches the object; none of the verified
    # source mask was printed over it, so cleanup must still fail closed.
    def newly_reaching_mask(_image, _region):
        mask = np.zeros((10, 20), dtype=np.uint8)
        mask[2:8, 2:19] = 255
        return mask, (30, 30), (32, 32, 49, 38)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        newly_reaching_mask,
    )
    with pytest.raises(
        SourceTextCleanupError,
        match=f"audited {protected_kind} substrate",
    ):
        probe_source_text(source, [region])


@pytest.mark.parametrize("protected_kind", ["character", "limb", "product"])
def test_large_display_mask_cannot_reconstruct_a_branded_substrate(
    monkeypatch,
    protected_kind,
):
    source = _checker_source(100, 100)
    region = {
        "source_x": 30,
        "source_y": 30,
        "source_width": 30,
        "source_height": 20,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [
            {
                "kind": "source_text", "source_index": 0,
                "x": 30, "y": 30, "width": 30, "height": 20,
            },
            {
                "kind": protected_kind,
                "x": 30, "y": 30, "width": 30, "height": 20,
            },
        ],
    }

    def large_display_mask(_image, _region):
        mask = np.full((20, 30), 255, dtype=np.uint8)
        return mask, (30, 30), (30, 30, 60, 50)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        large_display_mask,
    )

    with pytest.raises(
        SourceTextCleanupError,
        match="too large for protected caption substrate",
    ):
        probe_source_text(source, [region])


def test_split_substrate_boxes_cannot_bypass_the_total_source_mask_cap(
    monkeypatch,
):
    source = _checker_source(100, 100)
    region = {
        "source_x": 30,
        "source_y": 30,
        "source_width": 24,
        "source_height": 10,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [
            {
                "kind": "source_text", "source_index": 0,
                "x": 30, "y": 30, "width": 24, "height": 10,
            },
            {"kind": "character", "x": 30, "y": 30, "width": 12, "height": 10},
            {"kind": "limb", "x": 42, "y": 30, "width": 12, "height": 10},
        ],
    }

    def split_display_mask(_image, _region):
        mask = np.full((10, 24), 255, dtype=np.uint8)
        return mask, (30, 30), (30, 30, 54, 40)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        split_display_mask,
    )

    with pytest.raises(
        SourceTextCleanupError,
        match="too large for protected caption substrate",
    ):
        probe_source_text(source, [region])


def test_large_display_mask_fails_without_a_mascot_protection_box(monkeypatch):
    source = _checker_source(100, 100)
    region = {
        # The immutable discovery box is deliberately wider than the real
        # lettering. A model-controlled denominator must not lower the mask
        # density enough to bypass the protection-independent gate.
        "source_x": 20,
        "source_y": 30,
        "source_width": 50,
        "source_height": 20,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [{
            "kind": "source_text", "source_index": 0,
            "x": 30, "y": 30, "width": 30, "height": 20,
        }],
    }

    def unprotected_display_mask(_image, _region):
        mask = np.full((20, 30), 255, dtype=np.uint8)
        return mask, (30, 30), (30, 30, 60, 50)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        unprotected_display_mask,
    )

    with pytest.raises(
        SourceTextCleanupError,
        match="too large for deterministic reconstruction",
    ):
        probe_source_text(source, [region])


def test_large_sparse_mask_fails_on_a_complex_unprotected_substrate(monkeypatch):
    source = _checker_source(100, 100)
    region = {
        "source_x": 30,
        "source_y": 30,
        "source_width": 30,
        "source_height": 20,
        "_source_index": 0,
        "_source_line_count": 1,
        "_protected_regions": [{
            "kind": "source_text", "source_index": 0,
            "x": 30, "y": 30, "width": 30, "height": 20,
        }],
    }

    def sparse_display_mask(_image, _region):
        mask = np.zeros((20, 30), dtype=np.uint8)
        # A thin display face can stay below the density guard while still
        # erasing more than 2% of a branded object. The colour-aware ring must
        # independently reject its complex substrate.
        mask[:, ::3] = 255
        mask[:, -1] = 255
        return mask, (30, 30), (30, 30, 60, 50)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        sparse_display_mask,
    )

    with pytest.raises(
        SourceTextCleanupError,
        match="too large for deterministic reconstruction",
    ):
        probe_source_text(source, [region])


def test_split_unprotected_display_regions_cannot_bypass_total_mask_cap(
    monkeypatch,
):
    source = _checker_source(100, 100)

    def region(index, x):
        return {
            "source_x": x,
            "source_y": 30,
            "source_width": 15,
            "source_height": 10,
            "_source_index": index,
            "_source_line_count": 1,
            "_protected_regions": [{
                "kind": "source_text", "source_index": index,
                "x": x, "y": 30, "width": 15, "height": 10,
            }],
        }

    def regional_display_mask(_image, value):
        x = int(value["source_x"])
        mask = np.full((10, 15), 255, dtype=np.uint8)
        return mask, (x, 30), (x, 30, x + 15, 40)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        regional_display_mask,
    )
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._validate_region_reconstruction_complexity",
        lambda *_args: None,
    )

    with pytest.raises(
        SourceTextCleanupError,
        match="too large for deterministic reconstruction",
    ):
        probe_source_text(source, [region(0, 20), region(1, 55)])


def test_multiple_regions_cannot_bypass_the_total_substrate_mask_cap(
    monkeypatch,
):
    source = _checker_source(100, 100)

    def region(index, x):
        return {
            "source_x": x,
            "source_y": 30,
            "source_width": 15,
            "source_height": 10,
            "_source_index": index,
            "_source_line_count": 1,
            "_protected_regions": [
                {
                    "kind": "source_text", "source_index": index,
                    "x": x, "y": 30, "width": 15, "height": 10,
                },
                {
                    "kind": "character",
                    "x": x, "y": 30, "width": 15, "height": 10,
                },
            ],
        }

    def regional_display_mask(_image, value):
        x = int(value["source_x"])
        mask = np.full((10, 15), 255, dtype=np.uint8)
        return mask, (x, 30), (x, 30, x + 15, 40)

    monkeypatch.setattr(
        "core.sources.source_text_cleanup._detect_audited_region_text_mask",
        regional_display_mask,
    )
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._validate_region_reconstruction_complexity",
        lambda *_args: None,
    )

    with pytest.raises(
        SourceTextCleanupError,
        match="too large for protected caption substrate",
    ):
        probe_source_text(source, [region(0, 20), region(1, 55)])


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


def test_one_reconstruction_failure_does_not_discard_other_candidates(monkeypatch):
    source = _caption_source()
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._exemplar_inpaint",
        lambda _image, _mask: (_ for _ in ()).throw(
            SourceTextCleanupError("no safe exemplar donor")
        ),
    )

    cleaned = clean_source_text(source, [{
        "source_x": 25,
        "source_y": 68,
        "source_width": 50,
        "source_height": 28,
    }])

    assert cleaned.masked_pixels > 1_000


def test_fails_closed_when_no_reconstruction_candidate_succeeds(monkeypatch):
    source = _caption_source()
    monkeypatch.setattr(
        "core.sources.source_text_cleanup.cv2.inpaint",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            cv2.error("inpaint failed")
        ),
    )
    monkeypatch.setattr(
        "core.sources.source_text_cleanup._exemplar_inpaint",
        lambda _image, _mask: (_ for _ in ()).throw(
            SourceTextCleanupError("no safe exemplar donor")
        ),
    )

    with pytest.raises(SourceTextCleanupError, match="no source background.*succeeded"):
        clean_source_text(source, [{
            "source_x": 25,
            "source_y": 68,
            "source_width": 50,
            "source_height": 28,
        }])
