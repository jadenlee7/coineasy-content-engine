from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PendingSource:
    source_item_id: str
    post_id: str
    source_content: str
    source_url: str
    source_image_url: str
    published_at: str
    media: tuple[Mapping[str, object], ...] = ()
    metrics: Mapping[str, object] | None = None
    is_note_tweet: bool = False

    def routing_post(self) -> Mapping[str, object]:
        return {
            "id": self.post_id,
            "text": self.source_content,
            "created_at": self.published_at,
            "is_retweet": False,
            "is_reply": False,
            "is_note_tweet": self.is_note_tweet,
            "metrics": dict(self.metrics or {}),
            "source_image_url": self.source_image_url,
            "media": [dict(item) for item in self.media],
            "source_item_id": self.source_item_id,
            "url": self.source_url,
        }


@dataclass(frozen=True)
class AutomationState:
    last_cursor: str | None
    draft_reserved_today: bool
    pending_sources: tuple[PendingSource, ...]


@dataclass(frozen=True)
class QueueResult:
    job_id: str | None
    status: str
    reused: bool = False


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    client_id: str
    content_kind: str
    request_id: str
    source_content: str
    source_url: str
    source_image_url: str
    manual_only: bool
    attempts: int
    max_attempts: int
    locked_by: str
