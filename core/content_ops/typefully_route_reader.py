"""Unmounted, default-OFF Typefully social-set detail reader.

The caller owns a separately provisioned READ-capable credential. This module
never reads the environment or creates a draft, schedule, media upload or post.
Only the fixed GET detail endpoint is reachable; identity checks remain in
``publication_route_verification``.
"""

from __future__ import annotations

import json

import httpx


_MAX_RESPONSE_BYTES = 32768


class TypefullyRouteReaderError(RuntimeError):
    """Fixed error code only; never includes credential or provider response."""


class TypefullySocialSetDetailReader:
    """Read one exact social-set detail using an injected credential."""

    def __init__(self, *, bearer_token: str, enabled: bool = False,
            transport: httpx.AsyncBaseTransport | None = None):
        self._bearer_token = bearer_token
        self._enabled = enabled
        self._transport = transport

    async def __call__(self, social_set_id: int) -> dict:
        if self._enabled is not True:
            raise TypefullyRouteReaderError("typefully_route_reader_disabled")
        token = self._bearer_token
        if (type(token) is not str or not 1 <= len(token) <= 512
            or not token.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in token)
            or type(social_set_id) is not int or social_set_id <= 0):
            raise TypefullyRouteReaderError("typefully_route_reader_configuration_invalid")
        url = f"https://api.typefully.com/v2/social-sets/{social_set_id}/"
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False,
                                         trust_env=False, transport=self._transport) as client:
                async with client.stream("GET", url,
                        headers={"Authorization": "Bearer " + token,
                                 "Accept": "application/json",
                                 "Accept-Encoding": "identity"}) as response:
                    if response.status_code != 200 or response.is_redirect:
                        raise TypefullyRouteReaderError("typefully_route_reader_unavailable")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > _MAX_RESPONSE_BYTES:
                            raise TypefullyRouteReaderError("typefully_route_reader_unavailable")
            result = json.loads(raw)
            if (type(result) is not dict or type(result.get("id")) is not int
                or result["id"] != social_set_id):
                raise TypefullyRouteReaderError("typefully_route_reader_unverified")
            return result
        except TypefullyRouteReaderError:
            raise
        except Exception:
            raise TypefullyRouteReaderError("typefully_route_reader_unavailable") from None
