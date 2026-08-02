"""Safe source-image loading for original visual remix cards.

Only X's public image CDN is accepted. Images are decoded with Pillow, bounded,
auto-oriented, and resized before they are sent to the LLM or renderer.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from io import BytesIO

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from core.sources.x_media_url import normalize_x_media_url


MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_SOURCE_PIXELS = 36_000_000
MAX_RENDER_DIMENSION = 1800


class SourceImageError(ValueError):
    """Raised when a source image is unsafe, unavailable, or malformed."""


@dataclass(frozen=True)
class PreparedSourceImage:
    media_type: str
    base64_data: str
    width: int
    height: int
    background_color: str = ""

    @property
    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.base64_data}"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(base64.b64decode(self.base64_data, validate=True)).hexdigest()


def _edge_background_color(image: Image.Image) -> str:
    """Return a stable solid fill sampled only from the creative's outer edge."""
    sample = image.copy()
    sample.thumbnail((64, 64), Image.Resampling.LANCZOS)
    width, height = sample.size
    border = max(1, min(width, height) // 16)
    pixels = [
        sample.getpixel((x, y))
        for y in range(height)
        for x in range(width)
        if x < border or x >= width - border or y < border or y >= height - border
    ]
    channels = [sorted(pixel[index] for pixel in pixels) for index in range(3)]
    middle = len(pixels) // 2
    red, green, blue = (channel[middle] for channel in channels)
    return f"#{red:02X}{green:02X}{blue:02X}"


def validate_source_image_url(value: str) -> str:
    """Return a validated HTTPS X image URL or raise SourceImageError."""
    normalized = normalize_x_media_url(value)
    if not normalized:
        raise SourceImageError("Source image URL is not allowed")
    return normalized


def prepare_source_image_bytes(raw: bytes) -> PreparedSourceImage:
    """Decode, bound, orient, and JPEG-encode an image for vision + rendering."""
    if not raw or len(raw) > MAX_DOWNLOAD_BYTES:
        raise SourceImageError("Source image is empty or too large")

    try:
        image = Image.open(BytesIO(raw))
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > MAX_SOURCE_PIXELS:
            raise SourceImageError("Source image dimensions are not allowed")
        image = ImageOps.exif_transpose(image)
        image.load()
    except SourceImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise SourceImageError("Source image could not be decoded") from exc

    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")

    image.thumbnail(
        (MAX_RENDER_DIMENSION, MAX_RENDER_DIMENSION),
        Image.Resampling.LANCZOS,
    )
    background_color = _edge_background_color(image)

    output = BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True, progressive=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return PreparedSourceImage(
        media_type="image/jpeg",
        base64_data=encoded,
        width=image.width,
        height=image.height,
        background_color=background_color,
    )


async def fetch_source_image(value: str) -> PreparedSourceImage:
    """Download a validated X image with strict type and size limits."""
    url = validate_source_image_url(value)
    timeout = httpx.Timeout(12.0, connect=5.0)
    chunks: list[bytes] = []
    total = 0

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                url,
                headers={"Accept": "image/*", "User-Agent": "CoinEasyContentEngine/1.0"},
            ) as response:
                if response.status_code != 200:
                    raise SourceImageError(f"Source image returned HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if not content_type.startswith("image/"):
                    raise SourceImageError("Source URL did not return an image")

                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                    raise SourceImageError("Source image is too large")

                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise SourceImageError("Source image is too large")
                    chunks.append(chunk)
    except SourceImageError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceImageError("Source image download failed") from exc

    return prepare_source_image_bytes(b"".join(chunks))
