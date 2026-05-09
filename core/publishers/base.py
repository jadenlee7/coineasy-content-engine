from abc import ABC, abstractmethod
from typing import Any, Optional


class Publisher(ABC):
    """Channel-agnostic publishing adapter for daily news."""

    name: str  # "typefully" | "telegram"

    @abstractmethod
    async def publish(
        self,
        payload: dict[str, Any],
        dry_run: bool,
        publish_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        payload: { headline, summary, body, hashtags, source_urls, ... }
        return: {
            ok: bool,
            channel: str,
            dry_run: bool,
            response: dict | None,
            error: str | None,
            skipped_reason: str | None,
        }
        """
        ...
