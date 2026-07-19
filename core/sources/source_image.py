"""Safe source-image loading for original visual remix cards.

Only X's public image CDN is accepted. Images are decoded with Pillow, bounded,
auto-oriented, and resized before they are sent to the LLM or renderer.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_IMAGE_HOSTS = {"pbs.twimg.com"}
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

    @property
    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.base64_data}"


def validate_source_image_url(value: str) -> str:
    """Return a validated HTTPS X image URL or raise SourceImageError."""
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise SourceImageError("Invalid source image URL") from exc

    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_IMAGE_HOSTS:
        raise SourceImageError("Source image host is not allowed")
    if parsed.username or parsed.password:
        raise SourceImageError("Source image URL credentials are not allowed")
    return value


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

    output = BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True, progressive=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return PreparedSourceImage(
        media_type="image/jpeg",
        base64_data=encoded,
        width=image.width,
        height=image.height,
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
