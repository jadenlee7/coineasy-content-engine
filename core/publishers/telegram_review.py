from __future__ import annotations

import asyncio
import base64
import binascii
import html
import os
import re
from dataclasses import dataclass
from typing import Any, Literal, Optional

import httpx


TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT_SECONDS = 15.0
MAX_IMAGE_BYTES = 3_000_000
_BOT_TOKEN_PATTERN = re.compile(r"^[0-9]{6,14}:[A-Za-z0-9_-]{30,100}$")
_CHAT_ID_PATTERN = re.compile(r"^-?[1-9][0-9]{5,19}$")
_PRIVATE_SUPERGROUP_ID_PATTERN = re.compile(r"^-100[1-9][0-9]{6,16}$")
_DATA_URL_PATTERN = re.compile(
    r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+={0,2})$"
)


@dataclass(frozen=True)
class TelegramReviewConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class TelegramContentOpsRelayConfig:
    """Dedicated internal relay with no client-publication authority."""

    bot_token: str
    chat_id: str


def telegram_review_config() -> Optional[TelegramReviewConfig]:
    bot_token = os.environ.get("TELEGRAM_REVIEW_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_REVIEW_CHAT_ID", "").strip()
    if not _BOT_TOKEN_PATTERN.fullmatch(bot_token):
        return None
    if not _CHAT_ID_PATTERN.fullmatch(chat_id):
        return None
    return TelegramReviewConfig(
        bot_token=bot_token,
        chat_id=chat_id,
    )


def telegram_content_ops_relay_config(
    primary: TelegramReviewConfig | None = None,
) -> Optional[TelegramContentOpsRelayConfig]:
    """Load the separate team-room bot; never reuse the personal-review bot."""

    bot_token = os.environ.get(
        "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", ""
    ).strip()
    chat_id = os.environ.get("TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID", "").strip()
    if not _BOT_TOKEN_PATTERN.fullmatch(bot_token):
        return None
    if not _PRIVATE_SUPERGROUP_ID_PATTERN.fullmatch(chat_id):
        return None
    if primary is not None and (
        bot_token == primary.bot_token or chat_id == primary.chat_id
    ):
        return None
    return TelegramContentOpsRelayConfig(bot_token=bot_token, chat_id=chat_id)


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
    config: TelegramReviewConfig | TelegramContentOpsRelayConfig,
    method: str,
    *,
    json_body: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> bool:
    return await _telegram_post_outcome(
        config,
        method,
        json_body=json_body,
        data=data,
        files=files,
    ) == "sent"


async def _telegram_post_outcome(
    config: TelegramReviewConfig | TelegramContentOpsRelayConfig,
    method: str,
    *,
    json_body: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> Literal["sent", "rejected", "delivery_unknown"]:
    try:
        async with httpx.AsyncClient(timeout=TELEGRAM_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{TELEGRAM_API_BASE}/bot{config.bot_token}/{method}",
                json=json_body,
                data=data,
                files=files,
            )
    except (httpx.TimeoutException, httpx.TransportError):
        return "delivery_unknown"
    except Exception:
        return "delivery_unknown"
    if response.status_code >= 500:
        return "delivery_unknown"
    if not 200 <= response.status_code < 300:
        return "rejected"
    try:
        result = response.json()
    except Exception:
        return "delivery_unknown"
    return (
        "sent"
        if isinstance(result, dict) and result.get("ok") is True
        else "rejected"
    )


async def send_telegram_review(
    *,
    config: TelegramReviewConfig,
    collaboration_config: TelegramContentOpsRelayConfig | None = None,
    caption_html: str,
    message_html: str,
    review_url: str,
    image_url: str = "",
    image_data_url: str = "",
) -> dict[str, bool]:
    async def send_to_chat(
        destination: TelegramReviewConfig | TelegramContentOpsRelayConfig,
    ) -> tuple[bool, bool]:
        photo_sent = False
        if image_data_url:
            decoded = decode_review_image_data_url(image_data_url)
            if decoded is None:
                return False, False
            mime_type, image_bytes = decoded
            extension = {
                "image/png": "png",
                "image/jpeg": "jpg",
                "image/webp": "webp",
            }[mime_type]
            photo_sent = await _telegram_post(
                destination,
                "sendPhoto",
                data={
                    "chat_id": destination.chat_id,
                    "caption": caption_html,
                    "parse_mode": "HTML",
                },
                files={
                    "photo": (
                        f"review-banner.{extension}",
                        image_bytes,
                        mime_type,
                    ),
                },
            )
        elif image_url:
            photo_sent = await _telegram_post(
                destination,
                "sendPhoto",
                json_body={
                    "chat_id": destination.chat_id,
                    "photo": image_url,
                    "caption": caption_html,
                    "parse_mode": "HTML",
                },
            )

        text_sent = await _telegram_post(
            destination,
            "sendMessage",
            json_body={
                "chat_id": destination.chat_id,
                "text": message_html,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
                "reply_markup": {
                    "inline_keyboard": [[{
                        # Opening the Studio is not itself an approval action.
                        # Keep the CTA factual for smoke packets and real
                        # review items alike; the authenticated Studio screen
                        # owns the separate approve/request-changes action.
                        "text": "콘텐츠 스튜디오 열기",
                        "url": review_url,
                    }]],
                },
            },
        )
        return photo_sent, text_sent

    collaboration_configured = collaboration_config is not None
    collaboration_photo_sent = False
    collaboration_text_sent = False
    if collaboration_config is not None:
        primary_result, collaboration_result = await asyncio.gather(
            send_to_chat(config),
            send_to_chat(collaboration_config),
        )
        photo_sent, text_sent = primary_result
        collaboration_photo_sent, collaboration_text_sent = collaboration_result
    else:
        photo_sent, text_sent = await send_to_chat(config)
    return {
        "sent": text_sent,
        "photo_sent": photo_sent,
        "text_sent": text_sent,
        "collaboration_configured": collaboration_configured,
        "collaboration_sent": collaboration_text_sent,
        "collaboration_photo_sent": collaboration_photo_sent,
    }


def build_telegram_grok_qa_message(
    *,
    client_id: str,
    content_kind: str,
    title: str,
    content_item_id: str,
    content_version_id: str,
    decision: str,
    summary: str,
    fact_status: str,
    fact_checks: list[str],
    source_urls: list[str],
    brand_status: str,
    brand_checks: list[str],
    issues: list[dict[str, str]],
    next_action: str,
) -> str:
    """Build a bounded private-room verdict from validated structured fields."""

    client_names = {
        "yellow": "Yellow",
        "origintrail": "OriginTrail",
        "squid": "Squid",
        "babylon": "Babylon",
    }
    kind_names = {
        "daily_news": "데일리 뉴스",
        "article": "아티클",
        "tutorial": "튜토리얼",
    }

    def escaped(value: str, maximum: int) -> str:
        return html.escape(value.strip()[:maximum], quote=False)

    fact_lines = "\n".join(
        f"• {escaped(check, 180)}" for check in fact_checks[:3]
    )
    source_lines = "\n".join(
        f"• {escaped(source_url, 500)}"
        if len(source_url) <= 500
        else "• 긴 근거 URL — Content Studio에서 확인"
        for source_url in source_urls[:2]
    ) or "• 확인 가능한 URL 없음"
    brand_lines = "\n".join(
        f"• {escaped(check, 180)}" for check in brand_checks[:3]
    )
    issue_lines = "\n".join(
        f"• [{escaped(issue['severity'], 8)}] "
        f"{escaped(issue['code'], 48)} — {escaped(issue['message'], 240)}"
        for issue in issues[:3]
    ) or "• 없음"
    message = "\n".join([
        "🤖 <b>CoinEasy Grok QA · 자문 판정</b>",
        "⚠️ <b>비공개 검수용 · 자동 승인/발행 아님</b>",
        "",
        f"<b>{escaped(client_names[client_id], 40)} · "
        f"{escaped(kind_names[content_kind], 40)}</b>",
        f"<b>{escaped(title, 200)}</b>",
        f"결과: <b>{escaped(decision, 8)}</b>",
        escaped(summary, 500),
        "",
        f"<b>사실 점검 · {escaped(fact_status, 8)}</b>",
        fact_lines,
        "<b>확인한 공식 근거</b>",
        source_lines,
        "",
        f"<b>브랜드 점검 · {escaped(brand_status, 8)}</b>",
        brand_lines,
        "",
        "<b>핵심 이슈</b>",
        issue_lines,
        "",
        f"다음 조치: <b>{escaped(next_action, 40)}</b>",
        f"Item {escaped(content_item_id, 36)}",
        f"Version {escaped(content_version_id, 36)}",
        "",
        "<i>최종 승인과 공개 발행은 사람이 Content Studio에서 별도로 수행합니다.</i>",
    ])
    if len(message) > 4_096:
        raise ValueError("grok_qa_message_too_large")
    return message


async def send_telegram_grok_qa_verdict(
    *,
    config: TelegramContentOpsRelayConfig,
    message_html: str,
    review_url: str,
    image_data_url: str,
) -> Literal["sent", "rejected", "delivery_unknown"]:
    """Send the verified banner and full verdict only to the private relay."""

    decoded = decode_review_image_data_url(image_data_url)
    if decoded is None:
        return "rejected"
    mime_type, image_bytes = decoded
    extension = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
    }[mime_type]
    photo_outcome = await _telegram_post_outcome(
        config,
        "sendPhoto",
        data={
            "chat_id": config.chat_id,
            "caption": (
                "🤖 <b>CoinEasy Grok QA · 검수 배너</b>\n"
                "<i>자문 전용 · 자동 승인/발행 아님</i>"
            ),
            "parse_mode": "HTML",
        },
        files={
            "photo": (
                f"grok-qa-banner.{extension}",
                image_bytes,
                mime_type,
            ),
        },
    )
    if photo_outcome != "sent":
        return photo_outcome

    text_outcome = await _telegram_post_outcome(
        config,
        "sendMessage",
        json_body={
            "chat_id": config.chat_id,
            "text": message_html,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "reply_markup": {
                "inline_keyboard": [[{
                    "text": "콘텐츠 스튜디오 열기",
                    "url": review_url,
                }]],
            },
        },
    )
    if text_outcome == "sent":
        return "sent"
    # The banner is already visible. Treat every later failure as ambiguous so
    # an automated retry cannot duplicate the private-room review packet.
    return "delivery_unknown"
