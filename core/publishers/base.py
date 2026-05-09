from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class Publisher(ABC):
    """Abstract base for content publishers (Typefully draft, Telegram, etc.)."""

    name: str = ""

    @abstractmethod
    async def publish(
        self,
        payload: dict[str, Any],
        dry_run: bool,
        publish_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """Publish a generated news payload.

        payload keys: headline, summary, body, hashtags, source_urls, source_tweet_ids, ...
        Return shape:
            {
              "ok": bool,
              "channel": str,
              "dry_run": bool,
              "response": dict | None,
              "error": str | None,
              "skipped_reason": str | None,
              ... extra channel-specific fields ...
            }
        """
        ...
