from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import httpx
import yaml

from core.publications.models import TelegramReceipt
from core.publications.settings import PUBLICATION_TELEGRAM_USERNAMES
from core.publications.telegram_route import telegram_route_binding


TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_CAPTION_LIMIT = 1_024
TELEGRAM_PHOTO_LIMIT = 10 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_BOT_TOKEN_RE = re.compile(r"^[0-9]{6,14}:[A-Za-z0-9_-]{30,100}$")
_PUBLIC_CHANNEL_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")
_NUMERIC_CHAT_RE = re.compile(r"^-?[1-9][0-9]{5,19}$")
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,99}$")


class TelegramExactError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable_before_attempt: bool = False,
    ):
        super().__init__(code)
        self.code = code
        self.retryable_before_attempt = retryable_before_attempt


@dataclass(frozen=True)
class TelegramExactConfig:
    client_id: str
    public_username: str
    chat_id: str
    bot_token: str = field(repr=False)


def load_telegram_exact_config(
    client_id: str,
    *,
    clients_dir: Path = Path("clients"),
    environ: Mapping[str, str] | None = None,
) -> TelegramExactConfig:
    env = os.environ if environ is None else environ
    canonical_username = PUBLICATION_TELEGRAM_USERNAMES.get(client_id)
    if canonical_username is None:
        raise TelegramExactError("telegram_publication_config_invalid")
    path = clients_dir / client_id / "config.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TelegramExactError("telegram_publication_config_invalid") from exc
    publishing = raw.get("publishing") if isinstance(raw, dict) else None
    telegram = publishing.get("telegram") if isinstance(publishing, dict) else None
    if not isinstance(telegram, dict) or telegram.get("active") is not True:
        raise TelegramExactError("telegram_publication_channel_inactive")
    public_channel = telegram.get("public_channel")
    bot_env = telegram.get("bot_env") or telegram.get("bot_token_env")
    channel_env = telegram.get("channel_env")
    if (
        not isinstance(public_channel, str)
        or not _PUBLIC_CHANNEL_RE.fullmatch(public_channel)
        or public_channel[1:].lower() != canonical_username
        or not isinstance(bot_env, str)
        or not _ENV_NAME_RE.fullmatch(bot_env)
        or not isinstance(channel_env, str)
        or not _ENV_NAME_RE.fullmatch(channel_env)
    ):
        raise TelegramExactError("telegram_publication_config_invalid")
    bot_token = env.get(bot_env, "").strip()
    chat_id = env.get(channel_env, "").strip()
    if not _BOT_TOKEN_RE.fullmatch(bot_token):
        raise TelegramExactError("telegram_publication_credentials_invalid")
    if not (_PUBLIC_CHANNEL_RE.fullmatch(chat_id) or _NUMERIC_CHAT_RE.fullmatch(chat_id)):
        raise TelegramExactError("telegram_publication_target_invalid")
    if chat_id.startswith("@") and chat_id.lower() != public_channel.lower():
        raise TelegramExactError("telegram_publication_target_mismatch")
    return TelegramExactConfig(
        client_id=client_id,
        public_username=public_channel[1:].lower(),
        chat_id=chat_id,
        bot_token=bot_token,
    )


def telegram_send_photo_fingerprint(
    *,
    public_username: str,
    caption: str,
    image_bytes: bytes,
) -> str:
    if (
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", public_username)
        or not isinstance(caption, str)
        or not caption.strip()
        or len(caption) > TELEGRAM_CAPTION_LIMIT
        or not isinstance(image_bytes, bytes)
        or not image_bytes.startswith(_PNG_SIGNATURE)
        or len(image_bytes) > TELEGRAM_PHOTO_LIMIT
    ):
        raise ValueError("invalid exact Telegram request")
    digest = hashlib.sha256()
    digest.update(b"exact-telegram-send-photo-v1\0")
    digest.update(public_username.lower().encode("ascii"))
    digest.update(b"\0")
    digest.update(caption.encode("utf-8"))
    digest.update(b"\0")
    digest.update(image_bytes)
    return digest.hexdigest()


class ExactTelegramPublisher:
    def __init__(
        self,
        config: TelegramExactConfig,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._content_ops_channel_id: int | None = None

    def _url(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self.config.bot_token}/{method}"

    @staticmethod
    def _result(response: httpx.Response) -> Mapping[str, object]:
        try:
            body = response.json()
        except ValueError as exc:
            raise TelegramExactError("telegram_response_invalid") from exc
        if not isinstance(body, Mapping) or body.get("ok") is not True:
            raise TelegramExactError("telegram_response_invalid")
        result = body.get("result")
        if not isinstance(result, Mapping):
            raise TelegramExactError("telegram_response_invalid")
        return result

    async def preflight(self) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self._url("getChat"),
                    json={"chat_id": self.config.chat_id},
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise TelegramExactError(
                "telegram_preflight_unavailable", retryable_before_attempt=True
            ) from None
        if not 200 <= response.status_code < 300:
            raise TelegramExactError(
                "telegram_preflight_rejected",
                retryable_before_attempt=response.status_code in {408, 425, 429, 500, 502, 503, 504},
            )
        result = self._result(response)
        username = result.get("username")
        if (
            result.get("type") != "channel"
            or not isinstance(username, str)
            or username.lower() != self.config.public_username
        ):
            raise TelegramExactError("telegram_publication_target_mismatch")

    async def verify_content_ops_route(self, *, workspace_id: str, release_sha: str) -> str:
        """Read provider identity/rights and pin a numeric destination, never send.

        Used only for attributed final-review handoffs. The old Studio canary
        retains its existing preflight contract.
        """
        self._content_ops_channel_id = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds,
                follow_redirects=False, trust_env=False, transport=self.transport) as client:
                async def read(method: str, payload: dict[str, object]):
                    response = await client.post(self._url(method), json=payload)
                    if not 200 <= response.status_code < 300:
                        raise TelegramExactError("telegram_publication_route_mismatch")
                    return self._result(response)

                me = await read("getMe", {})
                bot_id = me.get("id")
                if (isinstance(bot_id, bool) or not isinstance(bot_id, int)
                    or bot_id <= 0 or me.get("is_bot") is not True
                    or not _BOT_TOKEN_RE.fullmatch(self.config.bot_token)
                    or int(self.config.bot_token.split(":", 1)[0]) != bot_id):
                    raise TelegramExactError("telegram_publication_route_mismatch")
                chat = await read("getChat", {"chat_id": self.config.chat_id})
                channel_id = chat.get("id")
                if (isinstance(channel_id, bool) or not isinstance(channel_id, int)
                    or channel_id >= 0 or chat.get("type") != "channel"
                    or not isinstance(chat.get("username"), str)
                    or chat["username"].lower() != self.config.public_username
                    or self.config.public_username != PUBLICATION_TELEGRAM_USERNAMES.get(self.config.client_id)
                    or (not self.config.chat_id.startswith("@") and str(channel_id) != self.config.chat_id)
                    or (self.config.chat_id.startswith("@") and self.config.chat_id.lower() != "@" + self.config.public_username)):
                    raise TelegramExactError("telegram_publication_route_mismatch")
                member = await read("getChatMember", {"chat_id": channel_id, "user_id": bot_id})
                user = member.get("user")
                if (member.get("status") != "administrator"
                    or member.get("can_post_messages") is not True
                    or not isinstance(user, Mapping) or user.get("id") != bot_id
                    or user.get("is_bot") is not True):
                    raise TelegramExactError("telegram_publication_route_mismatch")
                binding = telegram_route_binding(workspace_id=workspace_id,
                    client_id=self.config.client_id, release_sha=release_sha,
                    channel_id=channel_id, bot_id=bot_id, username=self.config.public_username)
        except (httpx.TimeoutException, httpx.TransportError):
            raise TelegramExactError("telegram_preflight_unavailable",
                retryable_before_attempt=True) from None
        except TelegramExactError:
            raise
        except Exception:
            raise TelegramExactError("telegram_publication_route_mismatch") from None
        self._content_ops_channel_id = channel_id
        return binding

    async def send_photo_once(
        self,
        *,
        image_bytes: bytes,
        caption: str,
    ) -> TelegramReceipt:
        # All validation happens before the one provider call. This method has
        # deliberately no retry loop: Telegram offers no idempotency key.
        telegram_send_photo_fingerprint(
            public_username=self.config.public_username,
            caption=caption,
            image_bytes=image_bytes,
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=self._content_ops_channel_id is None,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self._url("sendPhoto"),
                    data={
                        "chat_id": str(self._content_ops_channel_id) if self._content_ops_channel_id is not None else self.config.chat_id,
                        "caption": caption,
                    },
                    files={
                        "photo": ("news-card.png", image_bytes, "image/png"),
                    },
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise TelegramExactError("telegram_delivery_unknown") from None
        except Exception:
            raise TelegramExactError("telegram_delivery_unknown") from None
        if not 200 <= response.status_code < 300:
            raise TelegramExactError("telegram_delivery_unknown")
        try:
            result = self._result(response)
        except TelegramExactError as exc:
            raise TelegramExactError("telegram_delivery_unknown") from exc
        message_id = result.get("message_id")
        provider_date = result.get("date")
        chat = result.get("chat")
        username = chat.get("username") if isinstance(chat, Mapping) else None
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or not 1 <= message_id <= 9_223_372_036_854_775_807
            or isinstance(provider_date, bool)
            or not isinstance(provider_date, int)
            or not 1 <= provider_date <= 4_102_444_800
            or not isinstance(username, str)
            or username.lower() != self.config.public_username
            or chat.get("type") != "channel"
            or (self._content_ops_channel_id is not None and (
                chat.get("id") != self._content_ops_channel_id
                or result.get("caption") != caption
            ))
        ):
            raise TelegramExactError("telegram_delivery_unknown")
        return TelegramReceipt(
            message_id=message_id,
            chat_username=username.lower(),
            provider_date=datetime.fromtimestamp(provider_date, tz=timezone.utc),
        )


__all__ = [
    "ExactTelegramPublisher",
    "TelegramExactConfig",
    "TelegramExactError",
    "load_telegram_exact_config",
    "telegram_send_photo_fingerprint",
]
