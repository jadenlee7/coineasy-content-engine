import base64
import hashlib
from io import BytesIO

import pytest
from PIL import Image

from core.sources.source_image import (
    MAX_RENDER_DIMENSION,
    SourceImageError,
    prepare_source_image_bytes,
    validate_source_image_url,
)
from core.sources.x_media_url import normalize_x_media_url


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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://pbs.twimg.com/media/photo.jpg?name=small",
            "https://pbs.twimg.com/media/photo.jpg?name=orig",
        ),
        (
            "https://pbs.twimg.com/amplify_video_thumb/123/img/poster.jpg?format=jpg&name=small",
            "https://pbs.twimg.com/amplify_video_thumb/123/img/poster.jpg?format=jpg&name=orig",
        ),
        (
            "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/poster.jpg",
            "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/poster.jpg?name=orig",
        ),
        (
            "https://pbs.twimg.com/tweet_video_thumb/animated.jpg",
            "https://pbs.twimg.com/tweet_video_thumb/animated.jpg?name=orig",
        ),
    ],
)
def test_x_media_url_contract_covers_photo_video_and_gif_previews(source, expected):
    assert normalize_x_media_url(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "https://pbs.twimg.com/profile_images/avatar.jpg",
        "https://pbs.twimg.com/media/photo.jpg?redirect=https://example.com",
        "https://pbs.twimg.com/media/photo.jpg?format=svg",
        "https://user@pbs.twimg.com/media/photo.jpg",
    ],
)
def test_x_media_url_contract_rejects_non_media_shapes(source):
    assert normalize_x_media_url(source) == ""


def test_prepare_source_image_bounds_and_encodes_for_vision():
    prepared = prepare_source_image_bytes(_png_bytes())
    assert prepared.media_type == "image/jpeg"
    assert max(prepared.width, prepared.height) == MAX_RENDER_DIMENSION
    assert prepared.width == 1800
    assert prepared.height == 900
    assert prepared.data_url.startswith("data:image/jpeg;base64,")

    decoded = base64.b64decode(prepared.base64_data)
    assert prepared.sha256 == hashlib.sha256(decoded).hexdigest()
    rendered = Image.open(BytesIO(decoded))
    assert rendered.format == "JPEG"
    assert rendered.size == (1800, 900)


def test_prepare_source_image_derives_a_stable_edge_fill_for_letterboxing():
    image = Image.new("RGB", (120, 68), (184, 129, 223))
    for x in range(42, 78):
        for y in range(14, 54):
            image.putpixel((x, y), (239, 255, 90))
    output = BytesIO()
    image.save(output, format="PNG")

    prepared = prepare_source_image_bytes(output.getvalue())

    assert prepared.background_color == "#B881DF"


def test_prepare_source_image_rejects_malformed_data():
    with pytest.raises(SourceImageError):
        prepare_source_image_bytes(b"not an image")
