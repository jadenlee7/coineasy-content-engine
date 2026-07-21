"""Conservative raster cleanup for localized text in official source creatives.

The source lettering is already baked into the X image.  A transparent CSS
layer cannot remove those pixels, so Squid localization first detects the
lettering inside the final audited phrase box and reconstructs only that tight
mask.  If a reliable text-shaped mask cannot be found, cleanup fails closed and
the official creative is left untouched.
"""
from __future__ import annotations

import base64
import hashlib
import math
from dataclasses import dataclass
from itertools import combinations

import cv2
import numpy as np

from core.sources.source_image import PreparedSourceImage


MAX_CLEANUP_REGIONS = 4
MAX_SINGLE_REGION_FRACTION = 0.16
MAX_TOTAL_MASK_FRACTION = 0.12
MAX_INPAINT_SCORE = 180.0
MAX_COMPLEX_RECONSTRUCTION_BBOX_FRACTION = 0.05
MAX_COMPLEX_RECONSTRUCTION_RING_STD = 48.0
TEXT_DETECTION_WORKING_LONG_EDGE = 900
MIN_TEXT_DETECTION_NORMALIZATION_LONG_EDGE = 480

_PROTECTED_VISUAL_KINDS = {
    "source_text",
    "other_text",
    "logo",
    "character",
    "face",
    "limb",
    "product",
    "product_ui",
    "token_icon",
    "other_visual",
}
_CAPTION_SUBSTRATE_KINDS = {"character", "limb", "product"}
_CAPTION_SUBSTRATE_MIN_SOURCE_MASK_RATIO = 0.50
_RECOVERY_HORIZONTAL_REGION_FRACTION = 0.45
_RECOVERY_HORIZONTAL_IMAGE_FRACTION = 0.08
_RECOVERY_VERTICAL_REGION_FRACTION = 0.75
_RECOVERY_VERTICAL_IMAGE_FRACTION = 0.10


class SourceTextCleanupError(ValueError):
    """Raised when source lettering cannot be removed conservatively."""


@dataclass(frozen=True)
class SourceTextCleanupResult:
    image: PreparedSourceImage
    masked_pixels: int
    detected_regions: tuple[dict[str, float], ...]


@dataclass(frozen=True)
class SourceTextProbeResult:
    masked_pixels: int
    detected_regions: tuple[dict[str, float], ...]
    mask_sha256: str


@dataclass(frozen=True)
class _MaskCandidate:
    mask: np.ndarray
    origin: tuple[int, int]
    detected: tuple[int, int, int, int]
    crop: tuple[int, int, int, int]
    seed_containment: float
    candidate_seed_support: float


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
    connector_width = _odd(max(1, round(height * 0.13)))
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
            area < max(1, round(width * height * 0.008))
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
    """Detect a text silhouette with crop-relative morphology thresholds."""
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
    blur_kernel = _odd(max(3, round(min(gray.shape) * 0.45)))
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


def _bounded_text_search_boxes(
    base: tuple[int, int, int, int],
    active_edges: tuple[bool, bool, bool, bool],
    image_width: int,
    image_height: int,
) -> tuple[tuple[int, int, int, int], ...]:
    """Build a small deterministic crop lattice around one clipped seed."""
    base_left, base_top, base_right, base_bottom = base
    base_width = base_right - base_left
    base_height = base_bottom - base_top
    horizontal_max = min(
        base_width * _RECOVERY_HORIZONTAL_REGION_FRACTION,
        image_width * _RECOVERY_HORIZONTAL_IMAGE_FRACTION,
    )
    vertical_max = min(
        base_height * _RECOVERY_VERTICAL_REGION_FRACTION,
        image_height * _RECOVERY_VERTICAL_IMAGE_FRACTION,
    )
    horizontal_context = base_width * 0.05
    vertical_context = base_height * 0.05
    active = tuple(index for index, touches in enumerate(active_edges) if touches)
    boxes: set[tuple[int, int, int, int]] = set()

    for level in (0.50, 0.95, 1.00):
        variants = [active]
        variants.extend(
            tuple(edge for edge in active if edge != omitted)
            for omitted in active
        )
        for expanded in variants:
            left_delta = (
                horizontal_max * level
                if 0 in expanded
                else horizontal_context if not active_edges[0] else 0.0
            )
            top_delta = (
                vertical_max * level
                if 1 in expanded
                else vertical_context if not active_edges[1] else 0.0
            )
            right_delta = (
                horizontal_max * level
                if 2 in expanded
                else horizontal_context if not active_edges[2] else 0.0
            )
            bottom_delta = (
                vertical_max * level
                if 3 in expanded
                else vertical_context if not active_edges[3] else 0.0
            )
            candidate = (
                int(math.floor(base_left - left_delta)),
                int(math.floor(base_top - top_delta)),
                int(math.ceil(base_right + right_delta)),
                int(math.ceil(base_bottom + bottom_delta)),
            )
            if _valid_search_box(candidate, image_width, image_height):
                candidate_area = (
                    (candidate[2] - candidate[0])
                    * (candidate[3] - candidate[1])
                )
                base_area = base_width * base_height
                if candidate_area <= min(
                    base_area * 3,
                    image_width * image_height * MAX_SINGLE_REGION_FRACTION,
                ):
                    boxes.add(candidate)
    return tuple(sorted(boxes))


def _candidate_masks_are_compatible(
    first: _MaskCandidate,
    second: _MaskCandidate,
    coordinate_tolerance: int,
) -> tuple[bool, float]:
    overlap = _mask_overlap_pixels(
        first.mask,
        first.origin,
        second.mask,
        second.origin,
    )
    first_pixels = int(np.count_nonzero(first.mask))
    second_pixels = int(np.count_nonzero(second.mask))
    first_ratio = overlap / max(1, first_pixels)
    second_ratio = overlap / max(1, second_pixels)
    coordinates_match = all(
        abs(first_value - second_value) <= coordinate_tolerance
        for first_value, second_value in zip(first.detected, second.detected)
    )
    return (
        coordinates_match
        and min(first_ratio, second_ratio) >= 0.80
        and max(first_ratio, second_ratio) >= 0.95,
        min(first_ratio, second_ratio),
    )


def _unique_pairwise_compatible_clique(
    adjacency: list[set[int]],
) -> frozenset[int]:
    """Return the sole maximal complete-link cluster with at least two masks.

    A connected component is insufficient here: A may resemble B and B may
    resemble C even when A and C describe different pixels. Enumerating the
    bounded candidate lattice (at most 15 entries) lets us require every pair
    in the accepted cluster to agree and reject multiple competing cliques.
    """
    count = len(adjacency)
    maximal_cliques: list[frozenset[int]] = []
    for size in range(count, 1, -1):
        for values in combinations(range(count), size):
            candidate = frozenset(values)
            if any(candidate < existing for existing in maximal_cliques):
                continue
            if all(
                second in adjacency[first]
                for first, second in combinations(values, 2)
            ):
                maximal_cliques.append(candidate)

    if len(maximal_cliques) != 1:
        raise SourceTextCleanupError(
            "source-text recovery is ambiguous or unsupported"
        )
    return maximal_cliques[0]


def _canonical_text_crop(
    detected: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
    minimum_margin: int,
) -> tuple[int, int, int, int]:
    """Build one crop determined only by the consensus glyph bounds.

    Audit jitter can lead the complete-link cluster to select masks produced
    from different crop origins even when their glyph bounds agree. Running
    detection once more in this canonical crop prevents those crop-relative
    morphology differences from leaking into the native cleanup mask.
    """
    left, top, right, bottom = detected
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        raise SourceTextCleanupError("source-text consensus bounds are invalid")

    horizontal_margin = max(minimum_margin + 1, round(width * 0.08))
    vertical_margin = max(minimum_margin + 1, round(height * 0.18))
    crop = (
        left - horizontal_margin,
        top - vertical_margin,
        right + horizontal_margin,
        bottom + vertical_margin,
    )
    if not _valid_search_box(crop, image_width, image_height):
        raise SourceTextCleanupError(
            "no safe canonical source-text crop found"
        )
    return crop


def _canonicalize_consensus_mask(
    image: np.ndarray,
    selected: _MaskCandidate,
    candidates: list[_MaskCandidate],
    cluster: frozenset[int],
    coordinate_tolerance: int,
) -> tuple[np.ndarray, tuple[int, int], tuple[int, int, int, int]]:
    """Re-detect and verify the consensus in one audit-independent crop."""
    image_height, image_width = image.shape[:2]
    crop = _canonical_text_crop(
        selected.detected,
        image_width,
        image_height,
        coordinate_tolerance,
    )
    left, top, right, bottom = crop
    gray = cv2.cvtColor(
        image[top:bottom, left:right],
        cv2.COLOR_BGR2GRAY,
    )
    try:
        mask = _detect_text_mask(gray)
        bounds = _mask_bounds(mask)
    except SourceTextCleanupError as exc:
        raise SourceTextCleanupError(
            "canonical source-text detection failed"
        ) from exc

    crop_width = right - left
    crop_height = bottom - top
    if (
        bounds[0] < coordinate_tolerance + 1
        or bounds[1] < coordinate_tolerance + 1
        or crop_width - bounds[2] < coordinate_tolerance + 1
        or crop_height - bounds[3] < coordinate_tolerance + 1
    ):
        raise SourceTextCleanupError(
            "canonical source-text mask is clipped"
        )

    detected = (
        left + bounds[0],
        top + bounds[1],
        left + bounds[2],
        top + bounds[3],
    )
    canonical = _MaskCandidate(
        mask=mask,
        origin=(left, top),
        detected=detected,
        crop=crop,
        seed_containment=1.0,
        candidate_seed_support=1.0,
    )
    for candidate_index in cluster:
        compatible, _ = _candidate_masks_are_compatible(
            canonical,
            candidates[candidate_index],
            coordinate_tolerance,
        )
        if not compatible:
            raise SourceTextCleanupError(
                "canonical source-text mask disagrees with consensus"
            )
    return canonical.mask, canonical.origin, canonical.detected


def _recover_clipped_text_mask(
    image: np.ndarray,
    base: tuple[int, int, int, int],
    seed_mask: np.ndarray,
    seed_bounds: tuple[int, int, int, int],
) -> tuple[np.ndarray, tuple[int, int], tuple[int, int, int, int]]:
    """Accept only a unique text mask repeated by independent bounded crops."""
    image_height, image_width = image.shape[:2]
    base_left, base_top, base_right, base_bottom = base
    base_width = base_right - base_left
    base_height = base_bottom - base_top
    base_area = base_width * base_height
    active_edges = _mask_crop_edges(seed_bounds, base_width, base_height)
    search_boxes = _bounded_text_search_boxes(
        base,
        active_edges,
        image_width,
        image_height,
    )
    seed_pixels = int(np.count_nonzero(seed_mask))
    dilation_radius = max(
        1,
        min(3, round(max(image_width, image_height) / 900)),
    )
    minimum_margin = dilation_radius + 1
    candidates: list[_MaskCandidate] = []

    for left, top, right, bottom in search_boxes:
        gray = cv2.cvtColor(
            image[top:bottom, left:right],
            cv2.COLOR_BGR2GRAY,
        )
        try:
            mask = _detect_text_mask(gray)
            bounds = _mask_bounds(mask)
        except SourceTextCleanupError:
            continue
        crop_width = right - left
        crop_height = bottom - top
        if (
            bounds[0] < minimum_margin
            or bounds[1] < minimum_margin
            or crop_width - bounds[2] < minimum_margin
            or crop_height - bounds[3] < minimum_margin
        ):
            continue

        detected = (
            left + bounds[0],
            top + bounds[1],
            left + bounds[2],
            top + bounds[3],
        )
        detected_area = (
            (detected[2] - detected[0])
            * (detected[3] - detected[1])
        )
        box_overlap = _intersection_area(base, detected)
        detected_area_ratio = detected_area / max(1, base_area)
        seed_overlap = _mask_overlap_pixels(
            seed_mask,
            (base_left, base_top),
            mask,
            (left, top),
        )
        candidate_pixels = int(np.count_nonzero(mask))
        seed_containment = seed_overlap / max(1, seed_pixels)
        candidate_seed_support = seed_overlap / max(1, candidate_pixels)
        if (
            seed_containment < 0.45
            or candidate_seed_support < 0.22
            or box_overlap / max(1, detected_area) < 0.25
            or box_overlap / max(1, base_area) < 0.20
            or not 0.50 <= detected_area_ratio <= 1.60
        ):
            continue
        candidates.append(_MaskCandidate(
            mask=mask,
            origin=(left, top),
            detected=detected,
            crop=(left, top, right, bottom),
            seed_containment=seed_containment,
            candidate_seed_support=candidate_seed_support,
        ))

    if len(candidates) < 2:
        raise SourceTextCleanupError(
            "source-text recovery lacks independent mask consensus"
        )

    adjacency: list[set[int]] = [set() for _ in candidates]
    pair_quality: dict[tuple[int, int], float] = {}
    for first_index, first in enumerate(candidates):
        for second_index in range(first_index + 1, len(candidates)):
            compatible, quality = _candidate_masks_are_compatible(
                first,
                candidates[second_index],
                minimum_margin,
            )
            if compatible:
                adjacency[first_index].add(second_index)
                adjacency[second_index].add(first_index)
                pair_quality[(first_index, second_index)] = quality

    cluster = _unique_pairwise_compatible_clique(adjacency)
    ranked: list[tuple[tuple[float, ...], int]] = []
    for index in sorted(cluster):
        peers = sorted(adjacency[index] & cluster)
        qualities = [
            pair_quality[tuple(sorted((index, peer)))]
            for peer in peers
        ]
        crop = candidates[index].crop
        crop_area = (crop[2] - crop[0]) * (crop[3] - crop[1])
        candidate = candidates[index]
        ranked.append((
            (
                float(len(peers)),
                min(qualities, default=0.0),
                candidate.seed_containment,
                candidate.candidate_seed_support,
                float(-crop_area),
                float(-crop[0]),
                float(-crop[1]),
                float(-crop[2]),
                float(-crop[3]),
            ),
            index,
        ))
    selected = candidates[max(ranked)[1]]
    return _canonicalize_consensus_mask(
        image,
        selected,
        candidates,
        cluster,
        minimum_margin,
    )


def _detect_region_text_mask(
    image: np.ndarray,
    region: object,
) -> tuple[np.ndarray, tuple[int, int], tuple[int, int, int, int]]:
    """Detect one complete phrase with bounded consensus for clipped seeds."""
    image_height, image_width = image.shape[:2]
    base = _percent_box(region, image_width, image_height)
    base_left, base_top, base_right, base_bottom = base
    base_width = base_right - base_left
    base_height = base_bottom - base_top
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
        return _recover_clipped_text_mask(
            image,
            base,
            seed_mask,
            seed_bounds,
        )
    if _has_nested_dark_caption_panel(gray_crop, seed_mask):
        # The placement audit already reserves three source pixels around text.
        # Use exactly that halo to expose an opaque panel's real outer edge.
        recovery = panel_recovery
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
    panel_recovery_is_too_small = recovered_pixels / max(1, seed_pixels) < 2.0
    if (
        overlap / max(1, detected_area) < 0.25
        or overlap / max(1, base_area) < 0.20
        or pixel_overlap / max(1, seed_pixels) < 0.90
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
        raise SourceTextCleanupError(
            "source-text mask is clipped by its panel recovery box"
        )

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


def _audited_source_row_regions(region: object) -> list[dict[str, float]]:
    """Return the validator-authenticated source boxes for one subtitle.

    The placement audit emits one tight source-text box per visible row (and
    may emit more than one box for separated fragments on the same row).  A
    single connected-component search over their union tends to select only
    one row.  Keeping the original percentage boxes lets each fragment be
    detected independently before the result is combined atomically.
    """
    if not isinstance(region, dict) or "_protected_regions" not in region:
        return []
    source_index = region.get("_source_index")
    protected_regions = region.get("_protected_regions")
    if (
        isinstance(source_index, bool)
        or not isinstance(source_index, int)
        or not isinstance(protected_regions, list)
    ):
        raise SourceTextCleanupError("protected visual metadata is invalid")

    rows: list[dict[str, float]] = []
    seen: set[tuple[float, float, float, float]] = set()
    for protected in protected_regions:
        if not isinstance(protected, dict):
            raise SourceTextCleanupError("protected visual metadata is invalid")
        if protected.get("kind") != "source_text":
            continue
        protected_source_index = protected.get(
            "source_index",
            protected.get("_source_index"),
        )
        if protected_source_index != source_index:
            continue
        values: dict[str, float] = {}
        for key in ("x", "y", "width", "height"):
            raw = protected.get(key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise SourceTextCleanupError("protected visual coordinates are invalid")
            value = float(raw)
            if not math.isfinite(value):
                raise SourceTextCleanupError("protected visual coordinates are invalid")
            values[key] = value
        fingerprint = (
            values["x"],
            values["y"],
            values["width"],
            values["height"],
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        rows.append({
            "source_x": values["x"],
            "source_y": values["y"],
            "source_width": values["width"],
            "source_height": values["height"],
        })
    return sorted(
        rows,
        key=lambda item: (
            item["source_y"],
            item["source_x"],
            item["source_height"],
            item["source_width"],
        ),
    )


def _detect_audited_region_text_mask(
    image: np.ndarray,
    region: object,
) -> tuple[np.ndarray, tuple[int, int], tuple[int, int, int, int]]:
    """Detect every audited row/fragment, or reject the subtitle atomically."""
    source_rows = _audited_source_row_regions(region)
    if not source_rows:
        return _detect_region_text_mask(image, region)
    image_height, image_width = image.shape[:2]
    discovery_anchor = _percent_box(region, image_width, image_height)

    def detect_row_with_anchor_consensus(
        source_row: dict[str, float],
    ) -> tuple[np.ndarray, tuple[int, int], tuple[int, int, int, int]]:
        row_box = _percent_box(source_row, image_width, image_height)
        coordinate_tolerance = max(
            1,
            min(3, round(max(image_width, image_height) / 900)),
        ) + 1

        horizontal_anchor_delta = max(
            0,
            row_box[0] - discovery_anchor[0],
            discovery_anchor[2] - row_box[2],
        )
        if (
            len(source_rows) > 1
            and horizontal_anchor_delta <= coordinate_tolerance
        ):
            return _detect_region_text_mask(image, source_row)

        # A broad discovery box is authoritative for phrase identity but can be
        # too padded for morphology. Detect from the tight audit plus two fixed
        # interpolations toward the anchor. At least two independent crops must
        # converge on the same glyph pixels, so a central word subset cannot be
        # accepted merely because one sampled box happened to isolate it.
        row_left, row_top, row_right, row_bottom = row_box
        anchor_left, anchor_top, anchor_right, anchor_bottom = discovery_anchor
        row_width = row_right - row_left
        row_height = row_bottom - row_top
        candidates: list[_MaskCandidate] = []
        seen_boxes: set[tuple[int, int, int, int]] = set()
        if row_box == discovery_anchor:
            # An audit can independently return the same padded box as visual
            # discovery. Do not treat that agreement as pixel consensus. Probe a
            # small, fixed crop lattice inside/around the shared box and require
            # two distinct seeds to converge before any cleanup is authorized.
            variant_boxes = (
                (row_left, row_top, row_right, row_bottom),
                (
                    row_left - row_width * 0.20,
                    row_top,
                    row_right + row_width * 0.20,
                    row_bottom,
                ),
                (
                    row_left + row_width * 0.10,
                    row_top,
                    row_right - row_width * 0.10,
                    row_bottom,
                ),
                (
                    row_left + row_width * 0.20,
                    row_top,
                    row_right - row_width * 0.20,
                    row_bottom,
                ),
                (
                    row_left,
                    row_top,
                    row_right,
                    row_bottom + row_height * 0.25,
                ),
                (
                    row_left + row_width * 0.10,
                    row_top,
                    row_right - row_width * 0.10,
                    row_bottom + row_height * 0.25,
                ),
            )
        else:
            variant_boxes = tuple(
                (
                    row_left + (anchor_left - row_left) * level,
                    (
                        row_top + (anchor_top - row_top) * level
                        if len(source_rows) == 1
                        else float(row_top)
                    ),
                    row_right + (anchor_right - row_right) * level,
                    (
                        row_bottom + (anchor_bottom - row_bottom) * level
                        if len(source_rows) == 1
                        else float(row_bottom)
                    ),
                )
                for level in (0.0, 0.25, 0.50)
            )

        for left, top, right, bottom in variant_boxes:
            variant = {
                "source_x": left / image_width * 100.0,
                "source_y": top / image_height * 100.0,
                "source_width": (right - left) / image_width * 100.0,
                "source_height": (bottom - top) / image_height * 100.0,
            }
            try:
                variant_box = _percent_box(variant, image_width, image_height)
            except SourceTextCleanupError:
                continue
            if variant_box in seen_boxes:
                continue
            seen_boxes.add(variant_box)
            try:
                mask, origin, detected = _detect_region_text_mask(image, variant)
            except SourceTextCleanupError:
                continue
            candidates.append(_MaskCandidate(
                mask=mask,
                origin=origin,
                detected=detected,
                crop=(
                    origin[0],
                    origin[1],
                    origin[0] + mask.shape[1],
                    origin[1] + mask.shape[0],
                ),
                seed_containment=1.0,
                candidate_seed_support=1.0,
            ))

        if len(candidates) < 2:
            raise SourceTextCleanupError(
                "source-text anchor recovery lacks independent mask consensus"
            )
        adjacency: list[set[int]] = [set() for _ in candidates]
        for first_index, first in enumerate(candidates):
            for second_index in range(first_index + 1, len(candidates)):
                compatible, _ = _candidate_masks_are_compatible(
                    first,
                    candidates[second_index],
                    coordinate_tolerance,
                )
                if compatible:
                    adjacency[first_index].add(second_index)
                    adjacency[second_index].add(first_index)
        cluster = _unique_pairwise_compatible_clique(adjacency)
        selected_index = min(cluster)
        return _canonicalize_consensus_mask(
            image,
            candidates[selected_index],
            candidates,
            cluster,
            coordinate_tolerance,
        )

    if len(source_rows) == 1:
        # source_* preserves the broader authoritative discovery phrase. The
        # independently audited row is the precise detector seed; completeness
        # is checked against both boxes below after canonical redetection.
        return detect_row_with_anchor_consensus(source_rows[0])

    combined = np.zeros((image_height, image_width), dtype=np.uint8)
    for source_row in source_rows:
        row_mask, row_origin, row_detected = detect_row_with_anchor_consensus(source_row)
        coordinate_tolerance = max(
            1,
            min(3, round(max(image_width, image_height) / 900)),
        ) + 1
        row_candidate = _MaskCandidate(
            mask=row_mask,
            origin=row_origin,
            detected=row_detected,
            crop=(
                row_origin[0],
                row_origin[1],
                row_origin[0] + row_mask.shape[1],
                row_origin[1] + row_mask.shape[0],
            ),
            seed_containment=1.0,
            candidate_seed_support=1.0,
        )
        row_mask, (left, top), _ = _canonicalize_consensus_mask(
            image,
            row_candidate,
            [row_candidate],
            frozenset({0}),
            coordinate_tolerance,
        )
        bottom = top + row_mask.shape[0]
        right = left + row_mask.shape[1]
        if (
            left < 0
            or top < 0
            or right > image_width
            or bottom > image_height
        ):
            raise SourceTextCleanupError("source-text row mask leaves the image")
        combined[top:bottom, left:right] = cv2.bitwise_or(
            combined[top:bottom, left:right],
            row_mask,
        )

    detected = _mask_bounds(combined)
    left, top, right, bottom = detected
    return combined[top:bottom, left:right].copy(), (left, top), detected


def _validate_expected_line_structure(mask: np.ndarray, region: object) -> None:
    """Ensure a recovered group still contains every visible source line."""
    if not isinstance(region, dict):
        return
    metadata_line_count = region.get("_source_line_count")
    if metadata_line_count is not None:
        if (
            isinstance(metadata_line_count, bool)
            or not isinstance(metadata_line_count, int)
            or not 1 <= metadata_line_count <= 4
        ):
            raise SourceTextCleanupError("source caption line metadata is invalid")
        expected_lines = metadata_line_count
    else:
        # Backwards-compatible fallback for mock and legacy callers. Production
        # localization carries the line count from the protected visual audit,
        # rather than trusting punctuation/newlines in free-form OCR text.
        source_text = region.get("source_text")
        if not isinstance(source_text, str):
            return
        expected_lines = len([
            line for line in source_text.splitlines() if line.strip()
        ])
    if expected_lines < 1:
        return

    left, top, right, bottom = _mask_bounds(mask)
    projection = np.count_nonzero(mask[top:bottom, left:right] > 0, axis=1).astype(np.float32)
    if len(projection) < expected_lines * 4:
        raise SourceTextCleanupError("source caption line structure is incomplete")
    smoothed = np.convolve(projection, np.array([0.25, 0.5, 0.25]), mode="same")
    threshold = max(2.0, float(np.max(smoothed)) * 0.25)
    active_rows = smoothed >= threshold
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate(active_rows):
        if active and start is None:
            start = index
        elif not active and start is not None:
            if index - start >= 2:
                bands.append((start, index))
            start = None
    if start is not None and len(active_rows) - start >= 2:
        bands.append((start, len(active_rows)))

    # Merge only hairline gaps within one glyph row. A real line break retains
    # a deeper valley after smoothing.
    merged: list[tuple[int, int]] = []
    for band in bands:
        if merged and band[0] - merged[-1][1] <= 1:
            merged[-1] = (merged[-1][0], band[1])
        else:
            merged.append(band)
    if len(merged) != expected_lines:
        raise SourceTextCleanupError("source caption line count is ambiguous")

    total_pixels = int(np.count_nonzero(mask))
    for start, end in merged:
        line_pixels = int(np.count_nonzero(mask[top + start:top + end, left:right]))
        if line_pixels / max(1, total_pixels) < 0.10:
            raise SourceTextCleanupError("source caption line mask is incomplete")


def _protected_percent_box(
    value: object,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    """Parse one already-audited protected percentage box without repair."""
    if not isinstance(value, dict):
        raise SourceTextCleanupError("protected visual region must be an object")
    numbers: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise SourceTextCleanupError("protected visual coordinates are invalid")
        number = float(raw)
        if not math.isfinite(number):
            raise SourceTextCleanupError("protected visual coordinates are invalid")
        numbers[key] = number
    if (
        numbers["x"] < 0
        or numbers["y"] < 0
        or numbers["width"] <= 0
        or numbers["height"] <= 0
        or numbers["x"] + numbers["width"] > 100
        or numbers["y"] + numbers["height"] > 100
    ):
        raise SourceTextCleanupError("protected visual coordinates are invalid")
    left = max(0, int(math.floor(image_width * numbers["x"] / 100)))
    top = max(0, int(math.floor(image_height * numbers["y"] / 100)))
    right = min(
        image_width,
        int(math.ceil(image_width * (numbers["x"] + numbers["width"]) / 100)),
    )
    bottom = min(
        image_height,
        int(math.ceil(image_height * (numbers["y"] + numbers["height"]) / 100)),
    )
    if right <= left or bottom <= top:
        raise SourceTextCleanupError("protected visual coordinates are invalid")
    return left, top, right, bottom


def _audited_cleanup_envelopes(
    region: object,
    image_width: int,
    image_height: int,
) -> tuple[tuple[int, int, int, int], ...]:
    """Return the bounded area in which audited text recovery may edit pixels.

    A placement box is a seed, not necessarily the complete glyph bounds.  The
    detector may recover a clipped phrase only through the deterministic search
    lattice in :func:`_bounded_text_search_boxes`.  Mirror that lattice's maximum
    expansion here, per audited row, then add the audit-reserved three-pixel
    cleanup halo.  This absorbs model coordinate jitter without turning the
    union of several rows into one large editable rectangle.
    """
    source_rows = _audited_source_row_regions(region)
    if not source_rows:
        source_rows = [region] if isinstance(region, dict) else []
    if not source_rows:
        raise SourceTextCleanupError("protected visual metadata is invalid")
    discovery_anchor = _percent_box(region, image_width, image_height)

    envelopes: list[tuple[int, int, int, int]] = []
    for source_row in source_rows:
        left, top, right, bottom = _percent_box(
            source_row,
            image_width,
            image_height,
        )
        width = right - left
        height = bottom - top
        horizontal_recovery = math.ceil(min(
            width * _RECOVERY_HORIZONTAL_REGION_FRACTION,
            image_width * _RECOVERY_HORIZONTAL_IMAGE_FRACTION,
        ))
        vertical_recovery = math.ceil(min(
            height * _RECOVERY_VERTICAL_REGION_FRACTION,
            image_height * _RECOVERY_VERTICAL_IMAGE_FRACTION,
        ))
        envelope_top = top - vertical_recovery
        envelope_bottom = bottom + vertical_recovery
        if len(source_rows) == 1:
            envelope_top = min(envelope_top, discovery_anchor[1])
            envelope_bottom = max(envelope_bottom, discovery_anchor[3])
        envelopes.append((
            max(0, min(left - horizontal_recovery, discovery_anchor[0]) - 3),
            max(0, envelope_top - 3),
            min(image_width, max(right + horizontal_recovery, discovery_anchor[2]) + 3),
            min(image_height, envelope_bottom + 3),
        ))
    return tuple(envelopes)


def _validate_dilated_mask_protection(
    mask: np.ndarray,
    source_mask: np.ndarray,
    region: object,
) -> None:
    """Reject final cleanup pixels that leave the visual audit's safe area."""
    # Production Squid cleanup always carries the validator-produced protected
    # map.  Legacy/direct utility callers do not, so retain their historical
    # behavior while enforcing the strict envelope on the audited path.
    if not isinstance(region, dict) or "_protected_regions" not in region:
        return
    image_height, image_width = mask.shape
    if source_mask.shape != mask.shape:
        raise SourceTextCleanupError("source-text mask metadata is invalid")
    allowed_envelope = np.zeros_like(mask)
    for envelope_left, envelope_top, envelope_right, envelope_bottom in (
        _audited_cleanup_envelopes(region, image_width, image_height)
    ):
        allowed_envelope[
            envelope_top:envelope_bottom,
            envelope_left:envelope_right,
        ] = 255
    outside_envelope = np.where(allowed_envelope > 0, 0, mask).astype(np.uint8)
    if np.count_nonzero(outside_envelope):
        raise SourceTextCleanupError(
            "source-text mask leaves its audited cleanup envelope"
        )

    protected_regions = region.get("_protected_regions")
    source_index = region.get("_source_index")
    if (
        isinstance(source_index, bool)
        or not isinstance(source_index, int)
        or not isinstance(protected_regions, list)
        or not protected_regions
        or len(protected_regions) > 32
    ):
        raise SourceTextCleanupError("protected visual metadata is invalid")

    for protected in protected_regions:
        if not isinstance(protected, dict):
            raise SourceTextCleanupError("protected visual metadata is invalid")
        kind = protected.get("kind")
        if kind not in _PROTECTED_VISUAL_KINDS:
            raise SourceTextCleanupError("protected visual kind is invalid")
        left, top, right, bottom = _protected_percent_box(
            protected,
            image_width,
            image_height,
        )

        protected_source_index = protected.get(
            "_source_index",
            protected.get("source_index"),
        )
        if kind == "source_text":
            if (
                isinstance(protected_source_index, bool)
                or not isinstance(protected_source_index, int)
            ):
                raise SourceTextCleanupError("protected source-text metadata is invalid")
            if protected_source_index == source_index:
                continue

        overlap_y, overlap_x = np.where(mask[top:bottom, left:right] > 0)
        if not len(overlap_x):
            continue
        if kind not in _CAPTION_SUBSTRATE_KINDS:
            raise SourceTextCleanupError(
                f"source-text mask overlaps protected {kind}"
            )

        source_overlap_pixels = int(np.count_nonzero(
            source_mask[top:bottom, left:right] > 0
        ))
        source_mask_overlap_ratio = (
            source_overlap_pixels / max(1, len(overlap_x))
        )
        if (
            source_mask_overlap_ratio
            < _CAPTION_SUBSTRATE_MIN_SOURCE_MASK_RATIO
        ):
            raise SourceTextCleanupError(
                f"source-text mask leaves its audited {kind} substrate"
            )


def _validate_region_reconstruction_complexity(
    image: np.ndarray,
    region_mask: np.ndarray,
) -> None:
    """Apply the textured-background gate independently to one caption."""
    image_height, image_width = region_mask.shape
    mask_left, mask_top, mask_right, mask_bottom = _mask_bounds(region_mask)
    mask_box_fraction = (
        (mask_right - mask_left)
        * (mask_bottom - mask_top)
        / (image_width * image_height)
    )
    if mask_box_fraction <= MAX_COMPLEX_RECONSTRUCTION_BBOX_FRACTION:
        return
    context_radius = max(
        2,
        min(8, round(max(image_width, image_height) / 225)),
    )
    context_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (context_radius * 2 + 1, context_radius * 2 + 1),
    )
    context_ring = (
        (cv2.dilate(region_mask, context_kernel) > 0)
        & (region_mask == 0)
    )
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    context_std = (
        float(np.std(gray[context_ring]))
        if np.any(context_ring)
        else float("inf")
    )
    if context_std > MAX_COMPLEX_RECONSTRUCTION_RING_STD:
        raise SourceTextCleanupError(
            "source background is too complex for clean reconstruction"
        )


def _text_detection_working_image(image: np.ndarray) -> np.ndarray:
    """Normalize morphology to one resolution before candidate consensus.

    Percentage audit boxes land on different integer crop sizes at different
    source resolutions. Running morphology on those native crops changes the
    kernel lattice and can create a one-row spur in only one otherwise
    identical candidate. A fixed whole-image coordinate system keeps crop
    quantization, kernels, margins, and complete-link comparison deterministic.
    Final cleanup safety checks still run on the native-resolution mask.
    """
    image_height, image_width = image.shape[:2]
    long_edge = max(image_width, image_height)
    # Very small images contain no additional glyph detail to recover by
    # upscaling. Keeping them native also prevents interpolation from turning
    # a simple bar or product edge into a synthetic text-shaped contour.
    if long_edge < MIN_TEXT_DETECTION_NORMALIZATION_LONG_EDGE:
        return image.copy()
    scale = TEXT_DETECTION_WORKING_LONG_EDGE / long_edge
    working_width = max(1, round(image_width * scale))
    working_height = max(1, round(image_height * scale))
    if (working_width, working_height) == (image_width, image_height):
        return image.copy()
    return cv2.resize(
        image,
        (working_width, working_height),
        interpolation=cv2.INTER_LANCZOS4,
    )


def _project_working_mask(
    region_mask: np.ndarray,
    origin: tuple[int, int],
    working_shape: tuple[int, int],
    native_shape: tuple[int, int],
) -> np.ndarray:
    """Project one accepted raw detection mask back to the native canvas."""
    working_height, working_width = working_shape
    native_height, native_width = native_shape
    left, top = origin
    bottom = top + region_mask.shape[0]
    right = left + region_mask.shape[1]
    if (
        left < 0
        or top < 0
        or right > working_width
        or bottom > working_height
    ):
        raise SourceTextCleanupError("source-text mask leaves the image")
    working_mask = np.zeros((working_height, working_width), dtype=np.uint8)
    working_mask[top:bottom, left:right] = region_mask
    if (working_width, working_height) == (native_width, native_height):
        return working_mask
    return cv2.resize(
        working_mask,
        (native_width, native_height),
        interpolation=cv2.INTER_NEAREST,
    )


def _prepare_cleanup_mask(
    source_image: PreparedSourceImage,
    translation_regions: object,
) -> tuple[np.ndarray, np.ndarray, tuple[dict[str, float], ...]]:
    """Decode the source and deterministically validate every cleanup mask."""
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
    detection_image = _text_detection_working_image(image)
    detection_height, detection_width = detection_image.shape[:2]

    dilation_radius = max(1, min(3, round(max(image_width, image_height) / 900)))
    if (detection_width, detection_height) != (image_width, image_height):
        # Nearest-neighbour projection preserves the accepted silhouette but
        # can leave a one-native-pixel antialias/shadow fringe at its edge.
        # The placement audit reserves up to three source pixels, and the
        # final native mask is still checked against every protected region.
        dilation_radius = max(2, dilation_radius)
    dilation_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (dilation_radius * 2 + 1, dilation_radius * 2 + 1),
    )
    full_mask = np.zeros((image_height, image_width), dtype=np.uint8)
    detected_regions: list[dict[str, float]] = []
    for region in translation_regions:
        region_mask, origin, detected = _detect_audited_region_text_mask(
            detection_image,
            region,
        )
        _validate_expected_line_structure(region_mask, region)
        discovery_anchor = _percent_box(
            region,
            detection_width,
            detection_height,
        )
        if isinstance(region, dict) and "_protected_regions" in region:
            audited_source_rows = _audited_source_row_regions(region)
            if not audited_source_rows:
                raise SourceTextCleanupError("source-text row audit is incomplete")
            audited_row_boxes = [
                _percent_box(row, detection_width, detection_height)
                for row in audited_source_rows
            ]
            detection_audited = (
                min(box[0] for box in audited_row_boxes),
                min(box[1] for box in audited_row_boxes),
                max(box[2] for box in audited_row_boxes),
                max(box[3] for box in audited_row_boxes),
            )
            audit_matches_discovery = (
                len(audited_row_boxes) == 1
                and detection_audited == discovery_anchor
            )
            audited_width = detection_audited[2] - detection_audited[0]
            audited_height = detection_audited[3] - detection_audited[1]
            audited_area = audited_width * audited_height
            detected_width = detected[2] - detected[0]
            detected_height = detected[3] - detected[1]
            detected_area = detected_width * detected_height
            detected_overlap = _intersection_area(detection_audited, detected)
            detected_area_ratio = detected_area / max(1, audited_area)
            if (
                detected_area_ratio > 1.45
                or detected_overlap / max(1, detected_area) < 0.25
                or (
                    not audit_matches_discovery
                    and (
                        detected_area_ratio < 0.35
                        or detected_width / max(1, audited_width) < 0.70
                        or detected_height / max(1, audited_height) < 0.55
                        or detected_overlap / max(1, audited_area) < 0.30
                    )
                )
            ):
                raise SourceTextCleanupError(
                    "detected source-text mask left its audited region"
                )

            # The tight audit drives pixel detection, while source_* is the
            # immutable image-only phrase anchor. A partial word on one edge
            # must not pass merely because it fits the audit crop; canonical
            # detected pixels must still explain a material, centered portion
            # of the complete discovered phrase.
            anchor_width = discovery_anchor[2] - discovery_anchor[0]
            anchor_height = discovery_anchor[3] - discovery_anchor[1]
            anchor_overlap = _intersection_area(discovery_anchor, detected)
            anchor_overlap_width = max(
                0,
                min(discovery_anchor[2], detected[2])
                - max(discovery_anchor[0], detected[0]),
            )
            anchor_overlap_height = max(
                0,
                min(discovery_anchor[3], detected[3])
                - max(discovery_anchor[1], detected[1]),
            )
            anchor_center_x = (discovery_anchor[0] + discovery_anchor[2]) / 2.0
            anchor_center_y = (discovery_anchor[1] + discovery_anchor[3]) / 2.0
            detected_center_x = (detected[0] + detected[2]) / 2.0
            detected_center_y = (detected[1] + detected[3]) / 2.0
            if (
                anchor_overlap_width / max(1, anchor_width) < 0.45
                or anchor_overlap_height / max(1, anchor_height) < 0.55
                or anchor_overlap / max(1, anchor_width * anchor_height) < 0.27
                or abs(detected_center_x - anchor_center_x)
                > min(detection_width * 0.08, anchor_width * 0.30)
                or abs(detected_center_y - anchor_center_y)
                > min(detection_height * 0.06, anchor_height * 0.60)
            ):
                raise SourceTextCleanupError(
                    "detected source-text mask does not explain its discovery anchor"
                )
            source_index = region.get("_source_index")
            protected_regions = region.get("_protected_regions")
            if (
                isinstance(source_index, bool)
                or not isinstance(source_index, int)
                or not isinstance(protected_regions, list)
            ):
                raise SourceTextCleanupError("protected visual metadata is invalid")
            audited_rows = [
                _protected_percent_box(
                    protected,
                    detection_width,
                    detection_height,
                )
                for protected in protected_regions
                if (
                    isinstance(protected, dict)
                    and protected.get("kind") == "source_text"
                    and protected.get(
                        "source_index",
                        protected.get("_source_index"),
                    ) == source_index
                )
            ]
            if not audited_rows:
                raise SourceTextCleanupError("source-text row audit is incomplete")
            for audited_row in audited_rows:
                row_width = audited_row[2] - audited_row[0]
                row_height = audited_row[3] - audited_row[1]
                mask_left, mask_top = origin
                mask_right = mask_left + region_mask.shape[1]
                mask_bottom = mask_top + region_mask.shape[0]
                overlap_left = max(audited_row[0], mask_left)
                overlap_top = max(audited_row[1], mask_top)
                overlap_right = min(audited_row[2], mask_right)
                overlap_bottom = min(audited_row[3], mask_bottom)
                if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
                    raise SourceTextCleanupError(
                        "detected source-text mask misses an audited visual row"
                    )
                row_mask = region_mask[
                    overlap_top - mask_top:overlap_bottom - mask_top,
                    overlap_left - mask_left:overlap_right - mask_left,
                ]
                row_y, row_x = np.where(row_mask > 0)
                if not len(row_x):
                    raise SourceTextCleanupError(
                        "detected source-text mask misses an audited visual row"
                    )
                pixel_span_width = int(row_x.max() - row_x.min() + 1)
                pixel_span_height = int(row_y.max() - row_y.min() + 1)
                active_columns = int(np.count_nonzero(np.any(row_mask > 0, axis=0)))
                active_rows = int(np.count_nonzero(np.any(row_mask > 0, axis=1)))
                minimum_span_width = 0.45 if audit_matches_discovery else 0.55
                if (
                    pixel_span_width / max(1, row_width) < minimum_span_width
                    or pixel_span_height / max(1, row_height) < 0.45
                    or active_columns / max(1, row_width) < 0.25
                    or active_rows / max(1, row_height) < 0.30
                ):
                    raise SourceTextCleanupError(
                        "detected source-text mask misses an audited visual row"
                    )
        native_region_mask = _project_working_mask(
            region_mask,
            origin,
            (detection_height, detection_width),
            (image_height, image_width),
        )
        dilated_region_mask = cv2.dilate(
            native_region_mask,
            dilation_kernel,
        )
        _validate_dilated_mask_protection(
            dilated_region_mask,
            native_region_mask,
            region,
        )
        _validate_region_reconstruction_complexity(
            image,
            dilated_region_mask,
        )
        if np.count_nonzero(
            (full_mask > 0) & (dilated_region_mask > 0)
        ):
            raise SourceTextCleanupError("cleanup regions resolve to overlapping text")
        full_mask = cv2.bitwise_or(full_mask, dilated_region_mask)
        detected_regions.append({
            "x": detected[0] / detection_width * 100.0,
            "y": detected[1] / detection_height * 100.0,
            "width": (detected[2] - detected[0]) / detection_width * 100.0,
            "height": (detected[3] - detected[1]) / detection_height * 100.0,
        })

    masked_pixels = int(np.count_nonzero(full_mask))
    if (
        masked_pixels < 12
        or masked_pixels / (image_width * image_height) > MAX_TOTAL_MASK_FRACTION
    ):
        raise SourceTextCleanupError("source-text mask is outside conservative bounds")

    return image, full_mask, tuple(detected_regions)


def probe_source_text(
    source_image: PreparedSourceImage,
    translation_regions: object,
) -> SourceTextProbeResult:
    """Validate detection without running any inpainting candidates."""
    _, full_mask, detected_regions = _prepare_cleanup_mask(
        source_image,
        translation_regions,
    )
    return SourceTextProbeResult(
        masked_pixels=int(np.count_nonzero(full_mask)),
        detected_regions=detected_regions,
        mask_sha256=hashlib.sha256(full_mask.tobytes()).hexdigest(),
    )


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
    image, full_mask, detected_regions = _prepare_cleanup_mask(
        source_image,
        translation_regions,
    )
    image_height, image_width = image.shape[:2]
    masked_pixels = int(np.count_nonzero(full_mask))

    radius = max(2, min(7, round(max(image_width, image_height) / 360)))
    candidate_builders = (
        lambda: cv2.inpaint(image, full_mask, radius, cv2.INPAINT_TELEA),
        lambda: cv2.inpaint(image, full_mask, radius, cv2.INPAINT_NS),
        lambda: _exemplar_inpaint(image, full_mask),
    )
    scored_candidates: list[tuple[float, np.ndarray]] = []
    for build_candidate in candidate_builders:
        try:
            candidate = build_candidate()
            if (
                not isinstance(candidate, np.ndarray)
                or candidate.shape != image.shape
                or candidate.dtype != image.dtype
            ):
                raise SourceTextCleanupError(
                    "source background reconstruction returned an invalid image"
                )
            score = _inpaint_score(candidate, full_mask)
        except Exception:
            # Reconstruction methods are independent alternatives. One method
            # may not have a usable donor patch for a particular visual; that
            # must not discard a clean result produced by another method.
            continue
        if math.isfinite(score):
            scored_candidates.append((score, candidate))

    if not scored_candidates:
        raise SourceTextCleanupError(
            "no source background reconstruction candidate succeeded"
        )
    best_score, cleaned = min(scored_candidates, key=lambda item: item[0])
    if best_score > MAX_INPAINT_SCORE:
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
        detected_regions=detected_regions,
    )
