"""Internal registry for human-approved source-creative clean plates.

Some official creatives place display lettering over a mascot, product, or
other branded object.  Those hidden pixels cannot be recovered safely from the
flattened source image.  This module therefore accepts only a separately
reviewed clean plate whose bytes, dimensions, and source-image binding are
immutable.

The registry is intentionally code-owned.  No request payload or environment
variable can add an entry at runtime, and entries never share the sampled
visual-localization cache used by the generic cleanup path.
"""
from __future__ import annotations

import base64
import hashlib
import math
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from core.sources.source_image import PreparedSourceImage
from core.sources.x_media_url import normalize_x_media_url


APPROVED_CLEAN_PLATE_METHOD = "approved_clean_plate"
APPROVED_CLEAN_PLATE_REGISTRY_VERSION = "approved-clean-plate-registry@1"
APPROVED_CLEAN_PLATE_CACHE_NAMESPACE = "approved-clean-plate@1"
_MAX_APPROVED_CLEAN_PLATE_BYTES = 8 * 1024 * 1024

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._@-]{2,79}$")
_HEX_COLOR = re.compile(r"^#[0-9A-F]{6}$")
_HANGUL = re.compile(r"[가-힣]")
_X_STATUS_PATH = re.compile(
    r"^/([A-Za-z0-9_]{1,15})/status/([0-9]{1,20})/?$"
)
_OFFICIAL_X_HANDLES = {"squid": "squidrouter"}


class ApprovedCleanPlateError(ValueError):
    """Raised when a registered clean plate cannot be verified exactly."""


@dataclass(frozen=True)
class ApprovedTranslationRegion:
    """Immutable, reviewed copy placement stored with a clean plate."""

    source_text: str
    text: str
    x: float
    y: float
    width: float
    height: float
    align: str = "center"
    font_role: str = "display"
    font_size: float = 8.0
    text_color: str = "#000000"
    scale_x: float = 1.0
    source_line_count: int = 1

    def as_render_region(self) -> dict[str, object]:
        """Return a fresh renderer mapping without exposing registry state."""
        return {
            "source_text": self.source_text,
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            # A reviewed plate contains no source lettering.  source_* remains
            # equal to the target box only to satisfy the existing renderer
            # normalization contract; it is never sent to generic cleanup.
            "source_x": self.x,
            "source_y": self.y,
            "source_width": self.width,
            "source_height": self.height,
            "align": self.align,
            "font_role": self.font_role,
            "font_size": self.font_size,
            "text_color": self.text_color,
            "scale_x": self.scale_x,
            "source_line_count": self.source_line_count,
        }


@dataclass(frozen=True)
class ApprovedCleanPlateEntry:
    """One exact source-to-clean-plate approval record."""

    client_id: str
    source_url: str
    source_image_url: str
    source_sha256: str
    width: int
    height: int
    clean_plate_relative_path: str
    clean_plate_sha256: str
    approval_version: str
    translation_regions: tuple[ApprovedTranslationRegion, ...]


@dataclass(frozen=True)
class ApprovedCleanPlate:
    """Verified clean plate ready for the internal render path."""

    image: PreparedSourceImage
    source_sha256: str
    clean_plate_sha256: str
    approval_version: str
    translation_regions: tuple[ApprovedTranslationRegion, ...]
    method: str = APPROVED_CLEAN_PLATE_METHOD
    registry_version: str = APPROVED_CLEAN_PLATE_REGISTRY_VERSION
    cache_namespace: str = APPROVED_CLEAN_PLATE_CACHE_NAMESPACE

    def render_regions(self) -> list[dict[str, object]]:
        return [region.as_render_region() for region in self.translation_regions]


# Production entries are added only after a designer-reviewed clean plate is
# approved for this client, checked into clients/<client>/assets, and its exact
# digest is reviewed in code. Runtime callers cannot select or replace them.
_APPROVED_CLEAN_PLATES: Mapping[
    tuple[str, str, str], ApprovedCleanPlateEntry
] = MappingProxyType({
    (
        "squid",
        "https://x.com/squidrouter/status/2083266484789514640",
        "https://pbs.twimg.com/media/HOk_0-FakAAENyq.jpg?name=orig",
    ): ApprovedCleanPlateEntry(
        client_id="squid",
        source_url="https://x.com/squidrouter/status/2083266484789514640",
        source_image_url="https://pbs.twimg.com/media/HOk_0-FakAAENyq.jpg?name=orig",
        source_sha256="e6f4047e165cf5d72f59ba4676234acafb86b1b61ee0954352bd578fd5ddec1e",
        width=1080,
        height=1080,
        clean_plate_relative_path=(
            "clients/squid/assets/approved-clean-plates/"
            "telegram-launch-textless-v1.jpg"
        ),
        clean_plate_sha256="098218f108c6c669e9bccae7c26c26100cd135f5fb7f49fa6c0bbddd97cfaadd",
        approval_version="squid-telegram-launch-ko@1",
        translation_regions=(
            ApprovedTranslationRegion(
                source_text="SQUID IS",
                text="Squid가",
                x=6.0,
                y=0.0,
                width=88.0,
                height=20.0,
                align="center",
                font_role="display",
                font_size=20.0,
                text_color="#000000",
                scale_x=1.4,
                source_line_count=1,
            ),
            ApprovedTranslationRegion(
                source_text="ON\nTELEGRAM",
                text="텔레그램에\n왔어요",
                x=4.0,
                y=61.0,
                width=92.0,
                height=36.0,
                align="center",
                font_role="display",
                font_size=20.0,
                text_color="#000000",
                scale_x=1.35,
                source_line_count=2,
            ),
        ),
    ),
})


def _canonical_official_x_status_url(value: object, client_id: str) -> str:
    """Return one canonical official-client X status URL or an empty string."""
    if not isinstance(value, str) or not value or len(value) > 2_048:
        return ""
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return ""
    match = _X_STATUS_PATH.fullmatch(parsed.path)
    expected_handle = _OFFICIAL_X_HANDLES.get(client_id)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "x.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or match is None
        or expected_handle is None
        or match.group(1).lower() != expected_handle
    ):
        return ""
    return f"https://x.com/{expected_handle}/status/{match.group(2)}"


def _validate_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ApprovedCleanPlateError(f"{label} SHA-256 is invalid")


def _validate_entry(entry: ApprovedCleanPlateEntry, client_id: str) -> None:
    if entry.client_id != client_id or not re.fullmatch(r"[a-z0-9_-]{2,32}", client_id):
        raise ApprovedCleanPlateError("clean-plate client binding is invalid")
    if (
        _canonical_official_x_status_url(entry.source_url, client_id)
        != entry.source_url
    ):
        raise ApprovedCleanPlateError("clean-plate source URL binding is invalid")
    if normalize_x_media_url(entry.source_image_url) != entry.source_image_url:
        raise ApprovedCleanPlateError("clean-plate media URL binding is invalid")
    _validate_sha256(entry.source_sha256, "source")
    _validate_sha256(entry.clean_plate_sha256, "clean plate")
    if (
        isinstance(entry.width, bool)
        or isinstance(entry.height, bool)
        or not isinstance(entry.width, int)
        or not isinstance(entry.height, int)
        or entry.width <= 0
        or entry.height <= 0
        or entry.width * entry.height > 36_000_000
    ):
        raise ApprovedCleanPlateError("clean-plate dimensions are invalid")
    if _VERSION.fullmatch(entry.approval_version) is None:
        raise ApprovedCleanPlateError("clean-plate approval version is invalid")
    if not 1 <= len(entry.translation_regions) <= 4:
        raise ApprovedCleanPlateError("clean-plate translation regions are invalid")

    occupied: list[tuple[float, float, float, float]] = []
    for region in entry.translation_regions:
        if not isinstance(region, ApprovedTranslationRegion):
            raise ApprovedCleanPlateError("clean-plate translation region is invalid")
        source_text = region.source_text.strip()
        text = region.text.strip()
        source_lines = [line for line in source_text.splitlines() if line.strip()]
        translation_lines = [line for line in text.splitlines() if line.strip()]
        if (
            not source_text
            or not text
            or _HANGUL.search(text) is None
            or len(source_text) > 240
            or len(text) > 240
            or not 1 <= len(source_lines) <= 2
            or not 1 <= len(translation_lines) <= 2
            or len(source_lines) != region.source_line_count
            or len(translation_lines) != region.source_line_count
        ):
            raise ApprovedCleanPlateError("clean-plate copy is invalid")
        values = (region.x, region.y, region.width, region.height, region.font_size, region.scale_x)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise ApprovedCleanPlateError("clean-plate region geometry is invalid")
        numbers = tuple(float(value) for value in values)
        if any(not math.isfinite(value) for value in numbers):
            raise ApprovedCleanPlateError("clean-plate region geometry is invalid")
        x, y, width, height, font_size, scale_x = numbers
        if (
            x < 0
            or y < 0
            or width < 6
            or height < 3
            or x + width > 100
            or y + height > 100
            or not 2 <= font_size <= 20
            or not 0.8 <= scale_x <= 1.4
            or region.align not in {"left", "center", "right"}
            or region.font_role not in {"display", "body"}
            or _HEX_COLOR.fullmatch(region.text_color) is None
            or isinstance(region.source_line_count, bool)
            or region.source_line_count not in {1, 2}
        ):
            raise ApprovedCleanPlateError("clean-plate region geometry is invalid")
        bounds = (x, y, x + width, y + height)
        if any(
            bounds[0] < previous[2]
            and bounds[2] > previous[0]
            and bounds[1] < previous[3]
            and bounds[3] > previous[1]
            for previous in occupied
        ):
            raise ApprovedCleanPlateError("clean-plate translation regions overlap")
        occupied.append(bounds)


def _verified_jpeg_dimensions(raw: bytes, label: str) -> tuple[int, int]:
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != "JPEG":
                raise ApprovedCleanPlateError(f"{label} must be an approved JPEG")
            if image.getexif().get(274, 1) != 1:
                raise ApprovedCleanPlateError(f"{label} must not use EXIF rotation")
            dimensions = image.size
            image.load()
    except ApprovedCleanPlateError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ApprovedCleanPlateError(f"{label} could not be decoded") from exc
    return dimensions


def _resolve_clean_plate_path(entry: ApprovedCleanPlateEntry) -> Path:
    relative = Path(entry.clean_plate_relative_path)
    if relative.is_absolute():
        raise ApprovedCleanPlateError("clean-plate path must be repository-relative")
    approved_root = (
        _PROJECT_ROOT
        / "clients"
        / entry.client_id
        / "assets"
        / "approved-clean-plates"
    ).resolve()
    candidate = (_PROJECT_ROOT / relative).resolve()
    try:
        candidate.relative_to(approved_root)
    except ValueError as exc:
        raise ApprovedCleanPlateError("clean-plate path escapes approved client assets") from exc
    if not candidate.is_file():
        raise ApprovedCleanPlateError("approved clean-plate file is missing")
    return candidate


def resolve_approved_clean_plate(
    client_id: str,
    source_url: str,
    source_image_url: str,
    source_image: PreparedSourceImage,
) -> Optional[ApprovedCleanPlate]:
    """Return a byte-for-byte verified clean plate for an exact source image.

    An unregistered canonical status/media identity is a normal non-match and
    never consults the filesystem or source bytes. If a registered identity's
    prepared bytes, dimensions, or approval asset changed, an error is raised
    so the orchestrator preserves the untouched source instead of falling
    through to destructive generic cleanup.
    """
    canonical_source_url = _canonical_official_x_status_url(source_url, client_id)
    canonical_source_image_url = normalize_x_media_url(source_image_url)
    if not canonical_source_url or not canonical_source_image_url:
        return None
    entry = _APPROVED_CLEAN_PLATES.get(
        (client_id, canonical_source_url, canonical_source_image_url)
    )
    if entry is None:
        return None

    _validate_entry(entry, client_id)
    if (
        entry.source_url != canonical_source_url
        or entry.source_image_url != canonical_source_image_url
    ):
        # Defensive check for a malformed/custom Mapping implementation.
        raise ApprovedCleanPlateError("clean-plate identity binding is invalid")
    try:
        source_sha256 = source_image.sha256
    except (ValueError, TypeError) as exc:
        raise ApprovedCleanPlateError("prepared source image is invalid") from exc
    if source_sha256 != entry.source_sha256:
        raise ApprovedCleanPlateError("prepared source SHA-256 does not match approval")
    if (source_image.width, source_image.height) != (entry.width, entry.height):
        raise ApprovedCleanPlateError("prepared source dimensions do not match approval")
    if source_image.media_type != "image/jpeg":
        raise ApprovedCleanPlateError("prepared source must use normalized JPEG bytes")
    try:
        source_raw = base64.b64decode(source_image.base64_data, validate=True)
    except (ValueError, TypeError) as exc:
        raise ApprovedCleanPlateError("prepared source image is invalid") from exc
    if _verified_jpeg_dimensions(source_raw, "prepared source") != (entry.width, entry.height):
        raise ApprovedCleanPlateError("decoded source dimensions do not match approval")

    clean_plate_path = _resolve_clean_plate_path(entry)
    try:
        clean_plate_raw = clean_plate_path.read_bytes()
    except OSError as exc:
        raise ApprovedCleanPlateError("approved clean-plate file is unreadable") from exc
    if not clean_plate_raw or len(clean_plate_raw) > _MAX_APPROVED_CLEAN_PLATE_BYTES:
        raise ApprovedCleanPlateError("approved clean-plate file size is invalid")
    clean_plate_sha256 = hashlib.sha256(clean_plate_raw).hexdigest()
    if clean_plate_sha256 != entry.clean_plate_sha256:
        raise ApprovedCleanPlateError("approved clean-plate SHA-256 does not match")
    if clean_plate_sha256 == source_sha256:
        raise ApprovedCleanPlateError("approved clean plate must differ from source")
    if _verified_jpeg_dimensions(clean_plate_raw, "clean plate") != (entry.width, entry.height):
        raise ApprovedCleanPlateError("clean-plate dimensions do not match approval")

    return ApprovedCleanPlate(
        image=PreparedSourceImage(
            media_type="image/jpeg",
            base64_data=base64.b64encode(clean_plate_raw).decode("ascii"),
            width=entry.width,
            height=entry.height,
            background_color=source_image.background_color,
        ),
        source_sha256=entry.source_sha256,
        clean_plate_sha256=entry.clean_plate_sha256,
        approval_version=entry.approval_version,
        translation_regions=entry.translation_regions,
    )
