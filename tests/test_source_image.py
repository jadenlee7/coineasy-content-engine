import base64
from io import BytesIO

import pytest
from PIL import Image

from core.sources.source_image import (
    MAX_RENDER_DIMENSION,
    SourceImageError,
    prepare_source_image_bytes,
    validate_source_image_url,
)


def _png_bytes(size=(2400, 1200), mode="RGBA") -> bytes:
    image = Image.new(mode, size, (20, 40, 60, 180))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_validate_source_image_url_allows_only_x_image_cdn():
    url = "https://pbs.twimg.com/media/example.jpg?name=orig"
    assert validate_source_image_url(url) == url

    for unsafe in (
        "http://pbs.twimg.com/media/example.jpg",
        "https://pbs.twimg.com.evil.test/media/example.jpg",
        "https://127.0.0.1/image.jpg",
        "file:///etc/passwd",
    ):
        with pytest.raises(SourceImageError):
            validate_source_image_url(unsafe)


def test_prepare_source_image_bounds_and_encodes_for_vision():
    prepared = prepare_source_image_bytes(_png_bytes())
    assert prepared.media_type == "image/jpeg"
    assert max(prepared.width, prepared.height) == MAX_RENDER_DIMENSION
    assert prepared.width == 1800
    assert prepared.height == 900
    assert prepared.data_url.startswith("data:image/jpeg;base64,")

    decoded = base64.b64decode(prepared.base64_data)
    rendered = Image.open(BytesIO(decoded))
    assert rendered.format == "JPEG"
    assert rendered.size == (1800, 900)


def test_prepare_source_image_rejects_malformed_data():
    with pytest.raises(SourceImageError):
        prepare_source_image_bytes(b"not an image")
