"""Canonical allowlist for X media URLs used by source-locked generation."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_X_MEDIA_PATH = re.compile(
    r"^/(?:"
    r"media/[A-Za-z0-9._~-]+"
    r"|(?:amplify_video_thumb|ext_tw_video_thumb)/\d+/(?:img|pu/img)/[A-Za-z0-9._~-]+"
    r"|tweet_video_thumb/[A-Za-z0-9._~-]+"
    r")$"
)
_X_MEDIA_FORMAT = re.compile(r"^(?:jpe?g|png|webp)$", re.IGNORECASE)


def normalize_x_media_url(value: object) -> str:
    """Return one canonical pbs.twimg.com media URL or an empty string."""
    if not isinstance(value, str) or not value or len(value) > 2_048:
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != "pbs.twimg.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or not _X_MEDIA_PATH.fullmatch(parsed.path)
        ):
            return ""
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return ""

    if any(key not in {"format", "name"} for key, _value in pairs):
        return ""
    if sum(key == "format" for key, _value in pairs) > 1:
        return ""
    if sum(key == "name" for key, _value in pairs) > 1:
        return ""
    image_format = next((item for key, item in pairs if key == "format"), "")
    if image_format and not _X_MEDIA_FORMAT.fullmatch(image_format):
        return ""

    canonical_query = []
    if image_format:
        canonical_query.append(("format", image_format.lower()))
    canonical_query.append(("name", "orig"))
    return urlunsplit(("https", "pbs.twimg.com", parsed.path, urlencode(canonical_query), ""))


def is_allowed_x_media_url(value: object) -> bool:
    return bool(normalize_x_media_url(value))
