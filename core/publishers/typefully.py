from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from core.publishers.base import Publisher


TYPEFULLY_API_BASE = "https://api.typefully.com/v2"
X_POST_LIMIT = 280
TIMEOUT_SECONDS = 30.0


def _normalize_newlines(text: str) -> str:
    """Convert raw '\\n' literals (from JSON-style strings) to real newlines."""
    if not text:
        return ""
    return text.replace("\\n", "\n")


def _build_x_post(payload: dict[str, Any]) -> str:
    """Assemble a single X post (<= 280 chars) from the daily-news payload.

    URLs are intentionally excluded from the body — X policy blocks direct posts
    that include URLs via the API for unverified developer apps. The Typefully
    draft can be edited manually if a URL is desired.
    """
    headline = _normalize_newlines((payload.get("headline") or "").strip())
    summary = _normalize_newlines((payload.get("summary") or "").strip())
    hashtags_list = payload.get("hashtags") or []
    hashtags = " ".join(h for h in hashtags_list if h).strip()

    def assemble(s: str) -> str:
        parts = [headline]
        if s:
            parts.append(s)
        if hashtags:
            parts.append(hashtags)
        return "\n\n".join(p for p in parts if p)

    text = assemble(summary)
    if len(text) <= X_POST_LIMIT:
        return text

    # Trim summary to fit
    fixed_len = len(assemble(""))  # length without summary
    # Account for the extra "\n\n" + summary section we'll re-add
    # assemble("") uses headline + (hashtags). We need room for "\n\n" + trimmed_summary + "…"
    overhead = 2 + 1  # "\n\n" between summary and next block + "…"
    budget = X_POST_LIMIT - fixed_len - overhead
    if budget <= 0:
        # Fall back: hard-truncate the entire assembled text
        return text[: X_POST_LIMIT - 1].rstrip() + "…"
    trimmed = summary[:budget].rstrip() + "…"
    return assemble(trimmed)


class TypefullyPublisher(Publisher):
    """Legacy draft-only Typefully client; never retries a create request."""

    name = "typefully"

    def __init__(
        self,
        social_set_id: int,
        client_id: str,
        api_key: Optional[str] = None,
    ):
        self.social_set_id = int(social_set_id)
        self.client_id = client_id
        self.api_key = api_key or os.environ.get("TYPEFULLY_API_KEY", "")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _draft_title(self, payload: dict[str, Any]) -> str:
        from datetime import datetime, timezone
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"Daily News - {self.client_id} - {date}"

    def _build_request_body(
        self,
        payload: dict[str, Any],
        publish_at: Optional[str],
    ) -> dict[str, Any]:
        if publish_at is not None:
            raise ValueError("typefully_draft_only")
        text = _build_x_post(payload)
        body: dict[str, Any] = {
            "platforms": {
                "x": {
                    "enabled": True,
                    "posts": [{"text": text}],
                }
            },
            "draft_title": self._draft_title(payload),
        }
        return body

    async def publish(
        self,
        payload: dict[str, Any],
        dry_run: bool,
        publish_at: Optional[str] = None,
    ) -> dict[str, Any]:
        text = _build_x_post(payload)

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

        # This legacy publisher has no exact-version owner or durable attempt
        # fence. It may only create an inert draft; scheduling/publication must
        # use a separately approved exact-version path.
        if publish_at is not None:
            return {
                "ok": False,
                "channel": self.name,
                "dry_run": False,
                "response": None,
                "error": "typefully_draft_only",
                "skipped_reason": None,
            }

        if not self.api_key:
            return {
                "ok": False,
                "channel": self.name,
                "dry_run": False,
                "response": None,
                "error": "TYPEFULLY_API_KEY not set",
                "skipped_reason": None,
            }

        url = f"{TYPEFULLY_API_BASE}/social-sets/{self.social_set_id}/drafts"
        body = self._build_request_body(payload, publish_at)

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                r = await client.post(url, headers=self._headers(), json=body)
        except (httpx.TimeoutException, httpx.TransportError):
            # A lost response does not prove that the draft was not created.
            # Reconciliation must use the original attempt, never another POST.
            return {
                "ok": False, "channel": self.name, "dry_run": False,
                "response": None, "error": "typefully_delivery_unknown",
                "skipped_reason": None,
            }
        except Exception:
            return {
                "ok": False, "channel": self.name, "dry_run": False,
                "response": None, "error": "typefully_delivery_unknown",
                "skipped_reason": None,
            }

        if r.status_code == 201:
            try:
                response_body = r.json()
            except Exception:
                response_body = None
            if (
                isinstance(response_body, dict)
                and response_body.get("status") == "draft"
                and type(response_body.get("id")) is int
                and response_body.get("social_set_id") == self.social_set_id
            ):
                return {
                    "ok": True, "channel": self.name, "dry_run": False,
                    "response": {"id": response_body["id"], "status": "draft"},
                    "status_code": 201, "posted_text": text,
                    "error": None, "skipped_reason": None,
                }
            return {
                "ok": False, "channel": self.name, "dry_run": False,
                "response": None, "status_code": 201,
                "error": "typefully_delivery_unknown", "skipped_reason": None,
            }
        # Any non-201 response, including throttling and server errors, is
        # terminal for this attempt. Never re-POST a non-idempotent draft.
        return {
            "ok": False, "channel": self.name, "dry_run": False,
            "response": None, "status_code": r.status_code,
            "error": "typefully_request_rejected" if r.status_code in {400, 401, 402, 403, 404, 422}
                     else "typefully_delivery_unknown",
            "skipped_reason": None,
        }
