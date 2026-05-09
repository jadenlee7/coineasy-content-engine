import asyncio
import os
from typing import Any, Optional

import httpx

from core.publishers.base import Publisher

TYPEFULLY_API_BASE = "https://api.typefully.com/v2"
X_POST_LIMIT = 280
DEFAULT_TIMEOUT = 30.0
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


def _normalize_newlines(s: str) -> str:
    """Replace literal '\\n' from upstream LLM output with real newlines."""
    if not s:
        return ""
    return s.replace("\\n", "\n")


def compose_x_post(payload: dict[str, Any]) -> str:
    """Compose a single X post body.

    Format:
        {headline}

        {summary}

        {hashtags}

    URLs are intentionally excluded to avoid X policy issues with auto-published links.
    Truncates to 280 chars by trimming summary first, ending with an ellipsis.
    """
    headline = _normalize_newlines((payload.get("headline") or "").strip())
    summary = _normalize_newlines((payload.get("summary") or "").strip())
    hashtags_list = payload.get("hashtags") or []
    hashtags = " ".join(h.strip() for h in hashtags_list if h and h.strip())

    parts = [p for p in [headline, summary, hashtags] if p]
    text = "\n\n".join(parts)
    if len(text) <= X_POST_LIMIT:
        return text

    # Trim summary first while keeping headline + hashtags intact.
    fixed_overhead = len(headline) + len(hashtags)
    separators = "\n\n" * (1 if headline else 0) + "\n\n" * (1 if hashtags else 0)
    budget = X_POST_LIMIT - fixed_overhead - len(separators) - 1  # 1 for ellipsis
    if budget < 0:
        # Headline + hashtags alone exceed limit — fall back to hard truncate.
        return text[: X_POST_LIMIT - 1] + "…"

    trimmed_summary = summary[:budget].rstrip() + "…"
    parts = [p for p in [headline, trimmed_summary, hashtags] if p]
    return "\n\n".join(parts)


class TypefullyPublisher(Publisher):
    name = "typefully"

    def __init__(self, social_set_id: int, api_key: Optional[str] = None):
        self.social_set_id = int(social_set_id)
        self.api_key = api_key or os.getenv("TYPEFULLY_API_KEY", "")

    async def publish(
        self,
        payload: dict[str, Any],
        dry_run: bool,
        publish_at: Optional[str] = None,
    ) -> dict[str, Any]:
        text = compose_x_post(payload)

        if dry_run:
            return {
                "ok": True,
                "channel": self.name,
                "dry_run": True,
                "would_post": text,
                "would_target_social_set_id": self.social_set_id,
                "response": None,
                "error": None,
                "skipped_reason": None,
            }

        if not self.api_key:
            return {
                "ok": False,
                "channel": self.name,
                "dry_run": False,
                "response": None,
                "error": "TYPEFULLY_API_KEY env var not set",
                "skipped_reason": None,
            }

        url = f"{TYPEFULLY_API_BASE}/social-sets/{self.social_set_id}/drafts"
        body = {
            "platforms": {
                "x": {
                    "enabled": True,
                    "posts": [{"text": text}],
                }
            },
        }
        if publish_at:
            body["publish_at"] = publish_at

        return await self._post_with_retry(url, body)

    async def _post_with_retry(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_status: Optional[int] = None
        last_error: Optional[str] = None
        last_response_text: Optional[str] = None

        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    r = await client.post(url, headers=headers, json=body)
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
