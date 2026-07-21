"""Conservative raster cleanup for localized text in official source creatives.

The source lettering is already baked into the X image.  A transparent CSS
layer cannot remove those pixels, so Squid localization first detects the
lettering inside the final audited phrase box and reconstructs only that tight
mask.  If a reliable text-shaped mask cannot be found, cleanup fails closed and
the official creative is left untouched.
"""
from __future__ import annotations

import base64
import math
from dataclasses import dataclass

import cv2
import numpy as np

from core.sources.source_image import PreparedSourceImage


MAX_CLEANUP_REGIONS = 4
MAX_SINGLE_REGION_FRACTION = 0.16
MAX_TOTAL_MASK_FRACTION = 0.12
MAX_INPAINT_SCORE = 180.0


class SourceTextCleanupError(ValueError):
    """Raised when source lettering cannot be removed conservatively."""


@dataclass(frozen=True)
class SourceTextCleanupResult:
    image: PreparedSourceImage
    masked_pixels: int
    detected_regions: tuple[dict[str, float], ...]


def _odd(value: int) -> int:
    value = max(1, value)
    return value if value % 2 else value + 1


def _percent_box(
    region: object,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    if not isinstance(region, dict):
        raise SourceTextCleanupError("cleanup region must be an object")
    values: dict[str, float] = {}
    for key in ("source_x", "source_y", "source_width", "source_height"):
        raw = region.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise SourceTextCleanupError("cleanup region coordinates are invalid")
        number = float(raw)
        if not math.isfinite(number):
            raise SourceTextCleanupError("cleanup region coordinates are invalid")
        values[key] = number

    x = values["source_x"]
    y = values["source_y"]
    width = values["source_width"]
    height = values["source_height"]
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > 100
        or y + height > 100
        or width * height / 10_000 > MAX_SINGLE_REGION_FRACTION
    ):
        raise SourceTextCleanupError("cleanup region is outside conservative bounds")

    left = max(0, int(math.floor(image_width * x / 100)))
    top = max(0, int(math.floor(image_height * y / 100)))
    right = min(image_width, int(math.ceil(image_width * (x + width) / 100)))
    bottom = min(image_height, int(math.ceil(image_height * (y + height) / 100)))
    if right - left < 6 or bottom - top < 4:
        raise SourceTextCleanupError("cleanup region is too small")
    return left, top, right, bottom


def _component_candidate(
    binary: np.ndarray,
    *,
    threshold_rank: int,
) -> tuple[float, np.ndarray] | None:
    """Return the best text-like connected group inside one audited crop."""
    height, width = binary.shape
    connector_width = _odd(max(3, round(height * 0.13)))
    connector_height = _odd(max(1, round(height * 0.05)))
    connector = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (connector_width, connector_height),
    )
    grouped = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, connector)
    count, labels, stats, centers = cv2.connectedComponentsWithStats(grouped, 8)
    best: tuple[float, np.ndarray] | None = None

    for index in range(1, count):
        x, y, component_width, component_height, area = (
            int(value) for value in stats[index]
        )
        width_ratio = component_width / width
        height_ratio = component_height / height
        box_fraction = component_width * component_height / (width * height)
        if (
            area < max(8, width * height * 0.008)
            or width_ratio < 0.16
            or height_ratio < 0.18
            or box_fraction > 0.985
            or component_width / max(1, component_height) < 1.15
        ):
            continue

        center_x, center_y = centers[index]
        center_distance = (
            abs(center_x - width / 2) / width
            + abs(center_y - height / 2) / height
        )
        if center_distance > 0.55:
            continue

        component = np.where(labels == index, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(
            component,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        silhouette = np.zeros_like(component)
        cv2.drawContours(silhouette, contours, -1, 255, cv2.FILLED)
        silhouette_pixels = int(np.count_nonzero(silhouette))
        silhouette_fraction = silhouette_pixels / (width * height)
        if not 0.012 <= silhouette_fraction <= 0.78:
            continue
        raw_occupancy = (
            np.count_nonzero((binary > 0) & (silhouette > 0))
            / max(1, silhouette_pixels)
        )
        # A plain dark product, circle, or banner block is not source copy.
        # Real glyph groups have counters/gaps; an opaque caption panel is
        # accepted only when visible lettering cuts enough structure into it.
        if raw_occupancy > 0.965:
            continue

        # The final vision audit is centered on the phrase. Prefer a compact,
        # horizontally coherent group and the first (most selective) threshold
        # that can explain it.
        score = (
            center_distance * 3.0
            + abs(width_ratio - 0.58) * 0.35
            + abs(height_ratio - 0.70) * 0.12
            + threshold_rank * 0.035
        )
        if best is None or score < best[0]:
            best = score, silhouette
    return best


def _detect_text_mask(gray: np.ndarray) -> np.ndarray:
    """Detect a text/outline silhouette without masking the whole audit box."""
    candidates: list[tuple[float, np.ndarray]] = []

    # Most social captions use a near-black outline or backing shape. Starting
    # at a strict threshold prevents nearby coins, characters, and products from
    # joining the lettering before a viable component is found.
    for rank, threshold in enumerate((20, 28, 36, 44, 56, 72, 92, 112)):
        binary = np.where(gray <= threshold, 255, 0).astype(np.uint8)
        candidate = _component_candidate(binary, threshold_rank=rank)
        if candidate is not None:
            candidates.append(candidate)

    # White lettering on a dark visual may have no connected dark outline.
    # Require strong local contrast so a bright character/background is not
    # mistaken for copy.
    blur_kernel = _odd(max(7, round(min(gray.shape) * 0.45)))
    local_background = cv2.medianBlur(gray, blur_kernel)
    for rank, threshold in enumerate((248, 240, 228, 212, 192)):
        binary = np.where(
            (gray >= threshold) & (gray.astype(np.int16) - local_background.astype(np.int16) >= 18),
            255,
            0,
        ).astype(np.uint8)
        candidate = _component_candidate(binary, threshold_rank=rank + 2)
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        raise SourceTextCleanupError("no reliable source-text mask found")
    score, mask = min(candidates, key=lambda item: item[0])
    if score > 1.9:
        raise SourceTextCleanupError("source-text mask confidence is too low")
    return mask


def _mask_bounds(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask > 0)
    if not len(xs):
        raise SourceTextCleanupError("source-text mask is empty")
    return (
        int(xs.min()),
        int(ys.min()),
        int(xs.max()) + 1,
        int(ys.max()) + 1,
    )


def _mask_touches_crop_edge(
    bounds: tuple[int, int, int, int],
    crop_width: int,
    crop_height: int,
) -> bool:
    return any(_mask_crop_edges(bounds, crop_width, crop_height))


def _mask_crop_edges(
    bounds: tuple[int, int, int, int],
    crop_width: int,
    crop_height: int,
) -> tuple[bool, bool, bool, bool]:
    """Return left, top, right, and bottom crop-edge contact."""
    left, top, right, bottom = bounds
    return (
        left <= 0,
        top <= 0,
        right >= crop_width,
        bottom >= crop_height,
    )


def _has_nested_dark_caption_panel(gray: np.ndarray, selected_mask: np.ndarray) -> bool:
    """Reject an inner-glyph mask when a dark panel fills the audited crop."""
    binary = np.where(gray <= 44, 255, 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    crop_height, crop_width = gray.shape
    crop_area = crop_width * crop_height
    selected_pixels = int(np.count_nonzero(selected_mask))
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        touches_opposite_edges = (
            (x <= 0 and x + width >= crop_width)
            or (y <= 0 and y + height >= crop_height)
        )
        component = np.where(labels == index, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(
            component,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        silhouette = np.zeros_like(component)
        cv2.drawContours(silhouette, contours, -1, 255, cv2.FILLED)
        selected_containment = int(
            np.count_nonzero((selected_mask > 0) & (silhouette > 0))
        ) / max(1, selected_pixels)
        if (
            touches_opposite_edges
            and area / max(1, crop_area) >= 0.55
            and area / max(1, selected_pixels) >= 2.0
            and selected_containment >= 0.90
        ):
            return True
    return False


def _valid_search_box(
    box: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
) -> bool:
    left, top, right, bottom = box
    return (
        left >= 0
        and top >= 0
        and right <= image_width
        and bottom <= image_height
        and right - left >= 6
        and bottom - top >= 4
        and (right - left) * (bottom - top)
        <= image_width * image_height * MAX_SINGLE_REGION_FRACTION
    )


def _intersection_area(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> int:
    return max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0,
        min(first[3], second[3]) - max(first[1], second[1]),
    )


def _mask_overlap_pixels(
    first_mask: np.ndarray,
    first_origin: tuple[int, int],
    second_mask: np.ndarray,
    second_origin: tuple[int, int],
) -> int:
    left = max(first_origin[0], second_origin[0])
    top = max(first_origin[1], second_origin[1])
    right = min(
        first_origin[0] + first_mask.shape[1],
        second_origin[0] + second_mask.shape[1],
    )
    bottom = min(
        first_origin[1] + first_mask.shape[0],
        second_origin[1] + second_mask.shape[0],
    )
    if right <= left or bottom <= top:
        return 0
    first_view = first_mask[
        top - first_origin[1]:bottom - first_origin[1],
        left - first_origin[0]:right - first_origin[0],
    ]
    second_view = second_mask[
        top - second_origin[1]:bottom - second_origin[1],
        left - second_origin[0]:right - second_origin[0],
    ]
    return int(np.count_nonzero((first_view > 0) & (second_view > 0)))


def _detect_region_text_mask(
    image: np.ndarray,
    region: object,
) -> tuple[np.ndarray, tuple[int, int], tuple[int, int, int, int]]:
    """Detect one complete phrase, retrying one conservative lower search box.

    Vision coordinates can clip a caption even after the placement audit. A
    clipped detection always touches the crop boundary. In that case, retry a
    single, modest box biased down and slightly left (the common social-caption
    baseline error), then require the complete mask to sit inside that retry and
    still materially overlap the audited box. We never accept a free-ranging
    search elsewhere in the creative.
    """
    image_height, image_width = image.shape[:2]
    base = _percent_box(region, image_width, image_height)
    base_left, base_top, base_right, base_bottom = base
    base_width = base_right - base_left
    base_height = base_bottom - base_top
    clipped_recovery = (
        int(math.floor(base_left - base_width * 0.20)),
        int(math.floor(base_top + base_height * 0.20)),
        int(math.ceil(base_right + base_width * 0.05)),
        int(math.ceil(base_bottom + base_height * 0.70)),
    )
    extended_left_recovery = (
        int(math.floor(base_left - base_width * 0.40)),
        clipped_recovery[1],
        clipped_recovery[2],
        clipped_recovery[3],
    )
    panel_recovery = (
        base_left - 3,
        base_top - 3,
        base_right + 3,
        base_bottom + 3,
    )

    gray_crop = cv2.cvtColor(
        image[base_top:base_bottom, base_left:base_right],
        cv2.COLOR_BGR2GRAY,
    )
    seed_mask = _detect_text_mask(gray_crop)
    seed_bounds = _mask_bounds(seed_mask)
    if _mask_touches_crop_edge(seed_bounds, base_width, base_height):
        recovery = clipped_recovery
        recovery_kind = "clipped"
    elif _has_nested_dark_caption_panel(gray_crop, seed_mask):
        # The placement audit already reserves three source pixels around text.
        # Use exactly that halo to expose an opaque panel's real outer edge.
        recovery = panel_recovery
        recovery_kind = "panel"
    else:
        detected = (
            base_left + seed_bounds[0],
            base_top + seed_bounds[1],
            base_left + seed_bounds[2],
            base_top + seed_bounds[3],
        )
        return seed_mask, (base_left, base_top), detected

    if not _valid_search_box(recovery, image_width, image_height):
        raise SourceTextCleanupError("no safe source-text recovery box found")
    left, top, right, bottom = recovery
    recovery_gray = cv2.cvtColor(
        image[top:bottom, left:right],
        cv2.COLOR_BGR2GRAY,
    )
    mask = _detect_text_mask(recovery_gray)
    local_bounds = _mask_bounds(mask)

    detected = (
        left + local_bounds[0],
        top + local_bounds[1],
        left + local_bounds[2],
        top + local_bounds[3],
    )
    overlap = _intersection_area(base, detected)
    detected_area = (detected[2] - detected[0]) * (detected[3] - detected[1])
    base_area = base_width * base_height
    pixel_overlap = _mask_overlap_pixels(
        seed_mask,
        (base_left, base_top),
        mask,
        (left, top),
    )
    seed_pixels = int(np.count_nonzero(seed_mask))
    recovered_pixels = int(np.count_nonzero(mask))
    minimum_seed_overlap = 0.90 if recovery_kind == "panel" else 0.40
    recovered_overlap_is_too_small = (
        recovery_kind == "clipped"
        and pixel_overlap / max(1, recovered_pixels) < 0.25
    )
    panel_recovery_is_too_small = (
        recovery_kind == "panel"
        and recovered_pixels / max(1, seed_pixels) < 2.0
    )
    if (
        overlap / max(1, detected_area) < 0.25
        or overlap / max(1, base_area) < 0.20
        or pixel_overlap / max(1, seed_pixels) < minimum_seed_overlap
        or recovered_overlap_is_too_small
        or panel_recovery_is_too_small
    ):
        raise SourceTextCleanupError(
            "recovered source-text mask left its audited seed"
        )

    recovery_edges = _mask_crop_edges(
        local_bounds,
        right - left,
        bottom - top,
    )
    if any(recovery_edges):
        if recovery_kind != "clipped" or recovery_edges != (True, False, False, False):
            raise SourceTextCleanupError(
                "source-text mask is clipped by its recovery box"
            )
        if not _valid_search_box(
            extended_left_recovery,
            image_width,
            image_height,
        ):
            raise SourceTextCleanupError("no safe extended source-text recovery box found")

        extended_left, extended_top, extended_right, extended_bottom = (
            extended_left_recovery
        )
        extended_gray = cv2.cvtColor(
            image[extended_top:extended_bottom, extended_left:extended_right],
            cv2.COLOR_BGR2GRAY,
        )
        extended_mask = _detect_text_mask(extended_gray)
        extended_bounds = _mask_bounds(extended_mask)
        if _mask_touches_crop_edge(
            extended_bounds,
            extended_right - extended_left,
            extended_bottom - extended_top,
        ):
            raise SourceTextCleanupError(
                "source-text mask is clipped by its extended recovery box"
            )

        extended_overlap = _mask_overlap_pixels(
            mask,
            (left, top),
            extended_mask,
            (extended_left, extended_top),
        )
        extended_pixels = int(np.count_nonzero(extended_mask))
        if (
            extended_overlap / max(1, recovered_pixels) < 0.95
            or extended_overlap / max(1, extended_pixels) < 0.80
        ):
            raise SourceTextCleanupError(
                "extended source-text mask left its recovery seed"
            )

        extended_detected = (
            extended_left + extended_bounds[0],
            extended_top + extended_bounds[1],
            extended_left + extended_bounds[2],
            extended_top + extended_bounds[3],
        )
        extended_detected_area = (
            (extended_detected[2] - extended_detected[0])
            * (extended_detected[3] - extended_detected[1])
        )
        extended_base_overlap = _intersection_area(base, extended_detected)
        if (
            extended_base_overlap / max(1, extended_detected_area) < 0.25
            or extended_base_overlap / max(1, base_area) < 0.20
        ):
            raise SourceTextCleanupError(
                "extended source-text mask left its audited box"
            )
        return extended_mask, (extended_left, extended_top), extended_detected

    if recovery_kind == "panel":
        seed_top = base_top - top
        seed_left = base_left - left
        seed_bottom = seed_top + seed_mask.shape[0]
        seed_right = seed_left + seed_mask.shape[1]
        mask[seed_top:seed_bottom, seed_left:seed_right] = cv2.bitwise_or(
            mask[seed_top:seed_bottom, seed_left:seed_right],
            seed_mask,
        )
        local_bounds = _mask_bounds(mask)
        detected = (
            left + local_bounds[0],
            top + local_bounds[1],
            left + local_bounds[2],
            top + local_bounds[3],
        )
    return mask, (left, top), detected


def _exemplar_inpaint(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Rebuild textured caption areas from nearby source patches.

    Fluid inpainting is appropriate for a flat wall or sky, but it visibly
    smears coins, fabric, and product photography. This compact exemplar pass
    works only on a padded local crop and copies the best matching known patch
    inward from the mask boundary. Large crops are bounded to keep Railway
    latency predictable.
    """
    ys, xs = np.where(mask > 0)
    if not len(xs):
        raise SourceTextCleanupError("source-text mask is empty")
    image_height, image_width = mask.shape
    mask_width = int(xs.max() - xs.min() + 1)
    mask_height = int(ys.max() - ys.min() + 1)
    padding_x = max(48, mask_width * 2)
    padding_y = max(48, mask_height * 4)
    left = max(0, int(xs.min()) - padding_x)
    right = min(image_width, int(xs.max()) + padding_x + 1)
    top = max(0, int(ys.min()) - padding_y)
    bottom = min(image_height, int(ys.max()) + padding_y + 1)

    crop = image[top:bottom, left:right].copy()
    crop_mask = mask[top:bottom, left:right].copy()
    scale = min(1.0, 640 / max(crop.shape[:2]))
    if scale < 1:
        scaled_size = (
            max(1, round(crop.shape[1] * scale)),
            max(1, round(crop.shape[0] * scale)),
        )
        work = cv2.resize(crop, scaled_size, interpolation=cv2.INTER_AREA)
        work_mask = cv2.resize(crop_mask, scaled_size, interpolation=cv2.INTER_NEAREST)
    else:
        work = crop.copy()
        work_mask = crop_mask.copy()

    unknown = work_mask > 0
    output = work.copy()
    height, width = unknown.shape
    work_dimension = max(height, width)
    patch_radius = 3 if work_dimension <= 520 else min(8, round(work_dimension / 128))
    patch_size = patch_radius * 2 + 1
    boundary_kernel = np.ones((3, 3), dtype=np.uint8)

    for _ in range(700):
        if not np.any(unknown):
            break
        known = (~unknown).astype(np.uint8)
        boundary = unknown & (cv2.dilate(known, boundary_kernel) > 0)
        boundary_y, boundary_x = np.where(boundary)
        if not len(boundary_x):
            raise SourceTextCleanupError("textured cleanup lost its fill boundary")
        known_counts = cv2.boxFilter(
            known.astype(np.float32),
            -1,
            (patch_size, patch_size),
            normalize=False,
            borderType=cv2.BORDER_CONSTANT,
        )
        order = np.argsort(known_counts[boundary_y, boundary_x])[::-1]
        filled = False

        for order_index in order[: min(64, len(order))]:
            center_y = int(boundary_y[order_index])
            center_x = int(boundary_x[order_index])
            patch_top = center_y - patch_radius
            patch_bottom = center_y + patch_radius + 1
            patch_left = center_x - patch_radius
            patch_right = center_x + patch_radius + 1
            if (
                patch_top < 0
                or patch_left < 0
                or patch_bottom > height
                or patch_right > width
            ):
                continue

            target = output[patch_top:patch_bottom, patch_left:patch_right]
            known_patch = (~unknown[
                patch_top:patch_bottom,
                patch_left:patch_right,
            ]).astype(np.uint8) * 255
            if np.count_nonzero(known_patch) < patch_size * patch_size * 0.18:
                continue
            color_mask = np.repeat(known_patch[:, :, None], 3, axis=2)
            scores = cv2.matchTemplate(
                output,
                target,
                cv2.TM_SQDIFF,
                mask=color_mask,
            )
            invalid = cv2.matchTemplate(
                unknown.astype(np.uint8),
                np.ones((patch_size, patch_size), dtype=np.uint8),
                cv2.TM_CCORR,
            ) > 0
            scores[invalid] = np.inf
            scores[~np.isfinite(scores)] = np.inf
            candidate_y, candidate_x = np.indices(scores.shape)
            candidate_center_y = candidate_y + patch_radius
            candidate_center_x = candidate_x + patch_radius
            spatial_penalty = (
                (candidate_center_x - center_x) ** 2
                + (candidate_center_y - center_y) ** 2
            ).astype(np.float32) * 20.0
            scores += spatial_penalty
            if not np.isfinite(scores).any():
                continue

            source_y, source_x = np.unravel_index(np.argmin(scores), scores.shape)
            donor = output[
                source_y:source_y + patch_size,
                source_x:source_x + patch_size,
            ]
            needs_fill = unknown[
                patch_top:patch_bottom,
                patch_left:patch_right,
            ]
            target_view = output[
                patch_top:patch_bottom,
                patch_left:patch_right,
            ]
            target_view[needs_fill] = donor[needs_fill]
            needs_fill[:] = False
            filled = True
            break

        if not filled:
            raise SourceTextCleanupError("no safe nearby texture patch was found")
    if np.any(unknown):
        raise SourceTextCleanupError("textured cleanup exceeded its iteration bound")

    if scale < 1:
        output = cv2.resize(output, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_CUBIC)
    result = image.copy()
    local_target = result[top:bottom, left:right]
    local_target[crop_mask > 0] = output[crop_mask > 0]
    return result


def _inpaint_score(image: np.ndarray, mask: np.ndarray) -> float:
    """Balance seam continuity with preservation of surrounding texture."""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    outer = cv2.dilate(mask, kernel) > 0
    inner = cv2.erode(mask, kernel) > 0
    boundary = outer & ~inner
    if not np.any(boundary):
        return float("inf")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(gradient_x, gradient_y)
    laplacian = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    filled = mask > 0
    surrounding = outer & ~filled
    filled_texture = float(np.mean(laplacian[filled])) if np.any(filled) else 0.0
    surrounding_texture = float(np.mean(laplacian[surrounding])) if np.any(surrounding) else 0.0
    texture_mismatch = abs(math.log((filled_texture + 1) / (surrounding_texture + 1)))
    return float(np.mean(gradient[boundary])) + texture_mismatch * 50.0


def clean_source_text(
    source_image: PreparedSourceImage,
    translation_regions: object,
) -> SourceTextCleanupResult:
    """Erase audited source lettering and return a cleaned JPEG.

    Cleanup is atomic: every region must yield a conservative text-shaped mask.
    The caller must hide all Korean layers if this function raises.
    """
    if not isinstance(translation_regions, list) or not translation_regions:
        raise SourceTextCleanupError("cleanup requires at least one translation region")
    if len(translation_regions) > MAX_CLEANUP_REGIONS:
        raise SourceTextCleanupError("too many cleanup regions")

    try:
        raw = base64.b64decode(source_image.base64_data, validate=True)
    except (ValueError, TypeError) as exc:
        raise SourceTextCleanupError("source image data is invalid") from exc
    encoded = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise SourceTextCleanupError("source image could not be decoded")
    image_height, image_width = image.shape[:2]

    full_mask = np.zeros((image_height, image_width), dtype=np.uint8)
    detected_regions: list[dict[str, float]] = []
    for region in translation_regions:
        region_mask, (left, top), detected = _detect_region_text_mask(image, region)
        bottom = top + region_mask.shape[0]
        right = left + region_mask.shape[1]
        full_mask[top:bottom, left:right] = cv2.bitwise_or(
            full_mask[top:bottom, left:right],
            region_mask,
        )
        detected_regions.append({
            "x": detected[0] / image_width * 100.0,
            "y": detected[1] / image_height * 100.0,
            "width": (detected[2] - detected[0]) / image_width * 100.0,
            "height": (detected[3] - detected[1]) / image_height * 100.0,
        })

    dilation_radius = max(1, min(3, round(max(image_width, image_height) / 900)))
    dilation_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (dilation_radius * 2 + 1, dilation_radius * 2 + 1),
    )
    full_mask = cv2.dilate(full_mask, dilation_kernel)
    masked_pixels = int(np.count_nonzero(full_mask))
    if (
        masked_pixels < 12
        or masked_pixels / (image_width * image_height) > MAX_TOTAL_MASK_FRACTION
    ):
        raise SourceTextCleanupError("source-text mask is outside conservative bounds")

    radius = max(2, min(7, round(max(image_width, image_height) / 360)))
    telea = cv2.inpaint(image, full_mask, radius, cv2.INPAINT_TELEA)
    navier_stokes = cv2.inpaint(image, full_mask, radius, cv2.INPAINT_NS)
    exemplar = _exemplar_inpaint(image, full_mask)
    scored_candidates = [
        (_inpaint_score(candidate, full_mask), candidate)
        for candidate in (telea, navier_stokes, exemplar)
    ]
    best_score, cleaned = min(scored_candidates, key=lambda item: item[0])
    if not math.isfinite(best_score) or best_score > MAX_INPAINT_SCORE:
        raise SourceTextCleanupError("source background reconstruction is not clean enough")

    ok, cleaned_jpeg = cv2.imencode(
        ".jpg",
        cleaned,
        [cv2.IMWRITE_JPEG_QUALITY, 90, cv2.IMWRITE_JPEG_OPTIMIZE, 1],
    )
    if not ok:
        raise SourceTextCleanupError("cleaned source image could not be encoded")
    return SourceTextCleanupResult(
        image=PreparedSourceImage(
            media_type="image/jpeg",
            base64_data=base64.b64encode(cleaned_jpeg.tobytes()).decode("ascii"),
            width=image_width,
            height=image_height,
        ),
        masked_pixels=masked_pixels,
        detected_regions=tuple(detected_regions),
    )
