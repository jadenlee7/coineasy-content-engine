from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx


TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT_SECONDS = 15.0
MAX_IMAGE_BYTES = 3_000_000
_BOT_TOKEN_PATTERN = re.compile(r"^[0-9]{6,14}:[A-Za-z0-9_-]{30,100}$")
_CHAT_ID_PATTERN = re.compile(r"^-?[1-9][0-9]{5,19}$")
_DATA_URL_PATTERN = re.compile(
    r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+={0,2})$"
)


@dataclass(frozen=True)
class TelegramReviewConfig:
    bot_token: str
    chat_id: str


def telegram_review_config() -> Optional[TelegramReviewConfig]:
    bot_token = os.environ.get("TELEGRAM_REVIEW_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_REVIEW_CHAT_ID", "").strip()
    if not _BOT_TOKEN_PATTERN.fullmatch(bot_token):
        return None
    if not _CHAT_ID_PATTERN.fullmatch(chat_id):
        return None
    return TelegramReviewConfig(bot_token=bot_token, chat_id=chat_id)


def _valid_image_bytes(mime_type: str, image_bytes: bytes) -> bool:
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        return False
    if mime_type == "image/png":
        return image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return image_bytes.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return (
            len(image_bytes) >= 12
            and image_bytes[:4] == b"RIFF"
            and image_bytes[8:12] == b"WEBP"
        )
    return False


def decode_review_image_data_url(value: str) -> tuple[str, bytes] | None:
    match = _DATA_URL_PATTERN.fullmatch(value)
    if not match:
        return None
    try:
        image_bytes = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        return None
    mime_type = match.group(1)
    if not _valid_image_bytes(mime_type, image_bytes):
        return None
    return mime_type, image_bytes


async def _telegram_post(
    config: TelegramReviewConfig,
    method: str,
    *,
    json_body: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> bool:
    try:
        async with httpx.AsyncClient(timeout=TELEGRAM_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{TELEGRAM_API_BASE}/bot{config.bot_token}/{method}",
                json=json_body,
                data=data,
                files=files,
            )
    except (httpx.TimeoutException, httpx.TransportError):
        return False
    except Exception:
        return False
    if not 200 <= response.status_code < 300:
        return False
    try:
        result = response.json()
    except Exception:
        return False
    return isinstance(result, dict) and result.get("ok") is True


async def send_telegram_review(
    *,
    config: TelegramReviewConfig,
    caption_html: str,
    message_html: str,
    review_url: str,
    image_url: str = "",
    image_data_url: str = "",
) -> dict[str, bool]:
    photo_sent = False
    if image_data_url:
        decoded = decode_review_image_data_url(image_data_url)
        if decoded is None:
            return {"sent": False, "photo_sent": False, "text_sent": False}
        mime_type, image_bytes = decoded
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[
            mime_type
        ]
        photo_sent = await _telegram_post(
            config,
            "sendPhoto",
            data={
                "chat_id": config.chat_id,
                "caption": caption_html,
                "parse_mode": "HTML",
            },
            files={
                "photo": (f"review-banner.{extension}", image_bytes, mime_type),
            },
        )
    elif image_url:
        photo_sent = await _telegram_post(
            config,
            "sendPhoto",
            json_body={
                "chat_id": config.chat_id,
                "photo": image_url,
                "caption": caption_html,
                "parse_mode": "HTML",
            },
        )

    text_sent = await _telegram_post(
        config,
        "sendMessage",
        json_body={
            "chat_id": config.chat_id,
            "text": message_html,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "reply_markup": {
                "inline_keyboard": [[{
                    "text": "✅ 승인·수정 확인",
                    "url": review_url,
                }]],
            },
        },
    )
    return {
        "sent": text_sent,
        "photo_sent": photo_sent,
        "text_sent": text_sent,
    }
