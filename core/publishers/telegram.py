from __future__ import annotations

import asyncio
import html
import os
from typing import Any, Optional

import httpx

from core.publishers.base import Publisher


TELEGRAM_API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 30.0
TELEGRAM_TEXT_LIMIT = 4096
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


def _normalize_newlines(text: str) -> str:
    if not text:
        return ""
    return text.replace("\\n", "\n")


def _esc(s: str) -> str:
    """Escape user-provided text for HTML parse_mode (& < >)."""
    if not s:
        return ""
    return html.escape(s, quote=False)


def _build_message(payload: dict[str, Any]) -> str:
    headline = _normalize_newlines((payload.get("headline") or "").strip())
    summary = _normalize_newlines((payload.get("summary") or "").strip())
    body = _normalize_newlines((payload.get("body") or "").strip())
    hashtags_list = payload.get("hashtags") or []
    hashtags = " ".join(h for h in hashtags_list if h).strip()
    source_urls = payload.get("source_urls") or []
    first_url = source_urls[0] if source_urls else ""

    parts: list[str] = []
    if headline:
        parts.append(f"<b>{_esc(headline)}</b>")
    if summary:
        parts.append(_esc(summary))
    if body:
        parts.append(_esc(body))
    if hashtags:
        parts.append(_esc(hashtags))
    if first_url:
        parts.append(f"출처: {_esc(first_url)}")

    text = "\n\n".join(parts)
    if len(text) > TELEGRAM_TEXT_LIMIT:
        text = text[: TELEGRAM_TEXT_LIMIT - 1].rstrip() + "…"
    return text


class TelegramPublisher(Publisher):
    """Sends a message to a Telegram channel using a bot token."""

    name = "telegram"

    def __init__(
        self,
        bot_env: str,
        channel_env: str,
        client_id: str,
    ):
        self.bot_env = bot_env
        self.channel_env = channel_env
        self.client_id = client_id
        self.bot_token = os.environ.get(bot_env, "")
        self.chat_id = os.environ.get(channel_env, "")

    def _build_request_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "text": _build_message(payload),
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

    async def publish(
        self,
        payload: dict[str, Any],
        dry_run: bool,
        publish_at: Optional[str] = None,
    ) -> dict[str, Any]:
        message = _build_message(payload)

        if dry_run:
            return {
                "ok": True,
                "channel": self.name,
                "dry_run": True,
                "would_send": message,
                "would_target_channel_env": self.channel_env,
                "response": None,
                "error": None,
                "skipped_reason": None,
            }

        if not self.bot_token:
            return {
                "ok": False,
                "channel": self.name,
                "dry_run": False,
                "response": None,
                "error": f"{self.bot_env} not set",
                "skipped_reason": None,
            }
        if not self.chat_id:
            return {
                "ok": False,
                "channel": self.name,
                "dry_run": False,
                "response": None,
                "error": f"{self.channel_env} not set",
                "skipped_reason": None,
            }

        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        body = self._build_request_body(payload)

        last_error: Optional[str] = None
        last_status: Optional[int] = None
        last_response_body: Any = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    r = await client.post(url, json=body)
                last_status = r.status_code
                try:
                    last_response_body = r.json()
                except Exception:
                    last_response_body = {"text": r.text[:500]}

                if 200 <= r.status_code < 300:
                    return {
                        "ok": True,
                        "channel": self.name,
                        "dry_run": False,
                        "response": last_response_body,
                        "status_code": r.status_code,
                        "sent_text": message,
                        "error": None,
                        "skipped_reason": None,
                    }

                if r.status_code in {401, 403, 404}:
                    # Strip token from any error body before returning
                    return {
                        "ok": False,
                        "channel": self.name,
                        "dry_run": False,
                        "response": last_response_body,
                        "status_code": r.status_code,
                        "error": f"Telegram {r.status_code}",
                        "skipped_reason": None,
                    }

                if r.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                    last_error = f"Telegram {r.status_code}"
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue

                last_error = f"Telegram {r.status_code}"
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                break

        return {
            "ok": False,
            "channel": self.name,
            "dry_run": False,
            "response": last_response_body,
            "status_code": last_status,
            "error": last_error or "unknown_error",
            "skipped_reason": None,
        }
