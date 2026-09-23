"""One Typefully media allocation and raw PNG upload; no draft or publication.

The caller must obtain canonical Storage bytes itself and durably persist the
allocated media ID through the exact-version owner RPC before the raw PUT.
An upload response is
not a ready-media result: use an authenticated GET before draft reservation.
This module never retries an uncertain POST or PUT and never returns/logs the
presigned URL or a provider response body.
"""

from __future__ import annotations

import hashlib
import re
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

import httpx

from core.publications.typefully_readback import BASE_URL, _id, _key, _media_id


_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_S3_HOST = re.compile(
    r"^(?:[a-z0-9][a-z0-9.-]*\.)?s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com$"
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_PNG_BYTES = 10 * 1024 * 1024


class TypefullyMediaUploadError(ValueError):
    """A fixed error code, never a provider body, key, or signed URL."""

    def __init__(self, code: str, *, media_id: str | None = None):
        super().__init__(code)
        # If the PUT response is lost, a later authenticated GET can reconcile
        # this allocated ID. It does not authorize a second upload or draft.
        self.media_id = media_id


def _fail(code: str, *, media_id: str | None = None) -> None:
    raise TypefullyMediaUploadError(code, media_id=media_id)


def _safe_upload_url(value: object) -> str:
    if type(value) is not str or len(value) > 8192:
        _fail("typefully_upload_url_invalid")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username is not None
        or parsed.password is not None or parsed.port not in (None, 443)
        or parsed.fragment or not parsed.path.startswith("/")
        or not _S3_HOST.fullmatch(host)):
        _fail("typefully_upload_url_invalid")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (query.get("X-Amz-Algorithm") != ["AWS4-HMAC-SHA256"]
        or len(query.get("X-Amz-Credential", [])) != 1
        or not query["X-Amz-Credential"][0]
        or len(query.get("X-Amz-Signature", [])) != 1
        or not re.fullmatch(r"[a-f0-9]{64}", query["X-Amz-Signature"][0])):
        _fail("typefully_upload_url_invalid")
    return value


async def upload_typefully_png_once(
    *, social_set_id: int, api_key: str, png_bytes: bytes,
    expected_sha256: str,
    persist_upload_intent: Callable[[str], Awaitable[None]],
    api_transport: httpx.AsyncBaseTransport | None = None,
    upload_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    """Allocate one media ID then PUT exact bytes once to an allowlisted S3 URL.

    The caller must persist upload intent before PUT. No automatic retry
    follows an ambiguous response. A later ready-media GET and the durable
    owner receipt are required before any draft reservation.
    """
    try:
        set_id, key = _id(social_set_id), _key(api_key)
    except ValueError:
        _fail("typefully_media_upload_input_invalid")
    if not callable(persist_upload_intent):
        _fail("typefully_media_intent_required")
    if (type(png_bytes) is not bytes or not 24 <= len(png_bytes) <= _MAX_PNG_BYTES
        or not png_bytes.startswith(_PNG_SIGNATURE)
        or png_bytes[12:16] != b"IHDR"
        or type(expected_sha256) is not str
        or not _SHA256.fullmatch(expected_sha256)
        or hashlib.sha256(png_bytes).hexdigest() != expected_sha256):
        _fail("typefully_canonical_png_invalid")
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False,
                                     trust_env=False, transport=api_transport) as client:
            response = await client.post(
                f"{BASE_URL}/social-sets/{set_id}/media/upload",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"file_name": "news-card.png"},
            )
        if response.status_code != 201:
            _fail("typefully_media_allocation_unavailable")
        payload = response.json()
        if not isinstance(payload, dict):
            _fail("typefully_media_allocation_invalid")
        try:
            media_id = _media_id(payload.get("media_id"))
        except ValueError:
            _fail("typefully_media_allocation_invalid")
        upload_url = _safe_upload_url(payload.get("upload_url"))
    except TypefullyMediaUploadError:
        raise
    except Exception:
        _fail("typefully_media_allocation_unavailable")
    try:
        # The caller must durably mark this exact media ID as upload-unknown
        # before the raw PUT. A lost acknowledgement forbids the PUT.
        await persist_upload_intent(media_id)
    except Exception:
        _fail("typefully_media_intent_unknown", media_id=media_id)
    try:
        # A separate client ensures the Typefully Bearer token is never sent to
        # the presigned S3 endpoint. Do not add Content-Type to the raw PUT.
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False,
                                     trust_env=False, transport=upload_transport) as client:
            response = await client.put(upload_url, content=png_bytes)
        if response.status_code not in (200, 204):
            _fail("typefully_media_put_unknown", media_id=media_id)
    except TypefullyMediaUploadError:
        raise
    except Exception:
        _fail("typefully_media_put_unknown", media_id=media_id)
    return {
        "source": "typefully_media_upload_v2",
        "social_set_id": set_id,
        "media_id": media_id,
        "uploaded_bytes_sha256": expected_sha256,
    }
