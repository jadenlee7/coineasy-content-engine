import asyncio
import html
import os
from typing import Any, Optional

import httpx

from core.publishers.base import Publisher

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 30.0
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


def _normalize_newlines(s: str) -> str:
    if not s:
        return ""
    return s.replace("\\n", "\n")


def compose_telegram_message(payload: dict[str, Any]) -> str:
    """Compose Telegram message body (HTML parse mode, up to 4096 chars).

    Format:
        <b>{headline}</b>

        {summary}

        {body}

        {hashtags}

        출처: {first source url}
    """
    headline = _normalize_newlines((payload.get("headline") or "").strip())
    summary = _normalize_newlines((payload.get("summary") or "").strip())
    body = _normalize_newlines((payload.get("body") or "").strip())
    hashtags_list = payload.get("hashtags") or []
    hashtags = " ".join(h.strip() for h in hashtags_list if h and h.strip())
    source_urls = payload.get("source_urls") or []

    sections: list[str] = []
    if headline:
        sections.append(f"<b>{html.escape(headline)}</b>")
    if summary:
        sections.append(html.escape(summary))
    if body:
        sections.append(html.escape(body))
    if hashtags:
        sections.append(html.escape(hashtags))
    if source_urls:
        sections.append(f"출처: {html.escape(source_urls[0])}")

    text = "\n\n".join(sections)
    if len(text) > 4096:
        text = text[:4093] + "..."
    return text


class TelegramPublisher(Publisher):
    name = "telegram"

    def __init__(self, bot_env: str, channel_env: str):
        self.bot_env = bot_env
        self.channel_env = channel_env
        self.bot_token = os.getenv(bot_env, "")
        self.chat_id = os.getenv(channel_env, "")

    async def publish(
        self,
        payload: dict[str, Any],
        dry_run: bool,
        publish_at: Optional[str] = None,
    ) -> dict[str, Any]:
        text = compose_telegram_message(payload)

        if dry_run:
            return {
                "ok": True,
                "channel": self.name,
                "dry_run": True,
                "would_send": text,
                "would_chat_id": self.chat_id or f"<{self.channel_env} not set>",
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
                "error": f"{self.bot_env} env var not set",
                "skipped_reason": None,
            }
        if not self.chat_id:
            return {
                "ok": False,
                "channel": self.name,
                "dry_run": False,
                "response": None,
                "error": f"{self.channel_env} env var not set",
                "skipped_reason": None,
            }

        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        body = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        return await self._post_with_retry(url, body)

    async def _post_with_retry(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        last_status: Optional[int] = None
        last_error: Optional[str] = None
        last_response_text: Optional[str] = None

        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    r = await client.post(url, json=body)
                last_status = r.status_code
                last_response_text = r.text
                if r.status_code < 300:
                    try:
                        return {
                            "ok": True,
                            "channel": self.name,
                            "dry_run": False,
                            "response": r.json(),
                            "error": None,
                            "skipped_reason": None,
                        }
                    except ValueError:
                        return {
                            "ok": True,
                            "channel": self.name,
                            "dry_run": False,
                            "response": {"raw": r.text},
                            "error": None,
                            "skipped_reason": None,
                        }
                if r.status_code in {401, 403, 404}:
                    return {
                        "ok": False,
                        "channel": self.name,
                        "dry_run": False,
                        "response": {"status": r.status_code, "body": r.text},
                        "error": f"non_retryable_http_{r.status_code}",
                        "skipped_reason": None,
                    }
                if r.status_code not in RETRY_STATUSES:
                    return {
                        "ok": False,
                        "channel": self.name,
                        "dry_run": False,
                        "response": {"status": r.status_code, "body": r.text},
                        "error": f"http_{r.status_code}",
                        "skipped_reason": None,
                    }
                last_error = f"http_{r.status_code}"
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = f"{type(e).__name__}: {e}"

            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)

        return {
            "ok": False,
            "channel": self.name,
            "dry_run": False,
            "response": {"status": last_status, "body": last_response_text},
            "error": last_error or "exhausted_retries",
            "skipped_reason": None,
        }
