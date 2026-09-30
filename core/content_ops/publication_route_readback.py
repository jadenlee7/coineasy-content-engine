"""Default-OFF, unmounted read-only provider preflight for public routes.

This is not the private review bot and has no send, draft, update, polling,
database or configuration-write method. The caller must inject an approved
client publishing configuration from the existing publication owner, an
independently attested runtime release SHA, and a fresh official Typefully
detail reader. The config must be loaded through the active-channel validator,
not fabricated from a review card.
No credential or provider response is included in errors or return values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import httpx

from core.content_ops.publication_route_verification import (
    ExpectedPublicationRoutes, PublicationRouteError, VerifiedPublicationRoutes,
    validate_expected_publication_routes, validate_telegram_route_observation,
    verify_publication_routes,
)
from core.publishers.telegram_exact import TelegramExactConfig
from core.content_ops.typefully_route_reader import TypefullyDraftTarget


_TOKEN = re.compile(r"([0-9]{6,14}):[A-Za-z0-9_-]{30,100}\Z")
_MAX_RESPONSE_BYTES = 32768


class PublicationRouteReadbackError(RuntimeError):
    """Fixed error code only; never expose token, destination or response."""


class PublicationRouteReadback:
    """Read getMe/getChat/getChatMember and one X-only social-set detail."""

    def __init__(self, expected: ExpectedPublicationRoutes, *,
            telegram_publisher_config: TelegramExactConfig,
            typefully_publisher_target: TypefullyDraftTarget,
            runtime_release_sha: str,
            typefully_detail_reader: Callable[[int], Awaitable[dict]],
            transport: httpx.AsyncBaseTransport | None = None,
            clock: Callable[[], datetime] | None = None):
        self._expected = expected
        self._publisher_config = telegram_publisher_config
        self._typefully_target = typefully_publisher_target
        self._runtime_release_sha = runtime_release_sha
        self._typefully_detail_reader = typefully_detail_reader
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def run(self, *, enabled: bool = False) -> VerifiedPublicationRoutes:
        if enabled is not True:
            raise PublicationRouteReadbackError("publication_route_readback_disabled")
        try:
            validate_expected_publication_routes(self._expected)
        except PublicationRouteError:
            raise PublicationRouteReadbackError("publication_route_readback_configuration_invalid") from None
        config = self._publisher_config
        typefully_target = self._typefully_target
        if (type(config) is not TelegramExactConfig
            or type(typefully_target) is not TypefullyDraftTarget):
            raise PublicationRouteReadbackError("publication_route_readback_configuration_invalid")
        token = config.bot_token
        match = _TOKEN.fullmatch(token) if type(token) is str else None
        target = config.chat_id.lower() if type(config.chat_id) is str else None
        if (match is None or int(match.group(1)) != self._expected.telegram_bot_id
            or config.client_id != self._expected.client_id
            or type(config.public_username) is not str
            or config.public_username.lower() != self._expected.telegram_username.lower()
            or target not in ("@" + self._expected.telegram_username.lower(),
                              str(self._expected.telegram_channel_id))
            or typefully_target.client_id != self._expected.client_id
            or type(typefully_target.social_set_id) is not int
            or typefully_target.social_set_id != self._expected.typefully_social_set_id
            or type(self._runtime_release_sha) is not str
            or self._runtime_release_sha != self._expected.release_sha
            or not callable(self._typefully_detail_reader)
            or not callable(self._clock)):
            raise PublicationRouteReadbackError("publication_route_readback_configuration_invalid")

        async def read(client: httpx.AsyncClient, method: str, body: dict) -> dict:
            # The method is only selected by the three fixed calls below.
            url = "https://api.telegram.org/bot" + token + "/" + method
            try:
                async with client.stream("POST", url, json=body,
                                         headers={"Accept-Encoding": "identity"}) as response:
                    if response.status_code != 200 or response.is_redirect:
                        raise PublicationRouteReadbackError("publication_route_readback_unavailable")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > _MAX_RESPONSE_BYTES:
                            raise PublicationRouteReadbackError("publication_route_readback_unavailable")
                parsed = json.loads(raw)
                if (type(parsed) is not dict or parsed.get("ok") is not True
                    or type(parsed.get("result")) is not dict):
                    raise PublicationRouteReadbackError("publication_route_readback_unavailable")
                return parsed["result"]
            except Exception:
                raise PublicationRouteReadbackError("publication_route_readback_unavailable") from None

        try:
            observation_started_at = self._clock()
            async with httpx.AsyncClient(timeout=10, follow_redirects=False,
                                         trust_env=False, transport=self._transport) as client:
                bot = await read(client, "getMe", {})
                if (type(bot.get("id")) is not int
                    or bot["id"] != self._expected.telegram_bot_id
                    or bot.get("is_bot") is not True):
                    raise PublicationRouteReadbackError("publication_route_readback_unverified")
                chat_id = self._expected.telegram_channel_id
                chat = await read(client, "getChat", {"chat_id": chat_id})
                member = await read(client, "getChatMember", {
                    "chat_id": chat_id, "user_id": self._expected.telegram_bot_id})
                validate_telegram_route_observation(self._expected,
                    telegram_bot=bot, telegram_channel=chat,
                    telegram_member=member)
            details = await self._typefully_detail_reader(
                self._expected.typefully_social_set_id)
            now = self._clock()
            return verify_publication_routes(self._expected,
                telegram_bot=bot, telegram_channel=chat,
                telegram_member=member, typefully_social_set=details,
                observed_at=observation_started_at, now=now)
        except PublicationRouteReadbackError:
            raise
        except PublicationRouteError:
            raise PublicationRouteReadbackError("publication_route_readback_unverified") from None
        except Exception:
            raise PublicationRouteReadbackError("publication_route_readback_unavailable") from None
