from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StoredPng:
    asset_id: str
    storage_bucket: str
    storage_path: str
    mime_type: str
    byte_size: int
    sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class ClaimedTelegramPublication:
    job_id: str
    publication_id: str
    content_item_id: str
    content_version_id: str
    approval_id: str
    client_id: str
    attempts: int
    max_attempts: int
    locked_by: str
    lease_expires_at: datetime
    telegram_text: str
    asset: StoredPng


@dataclass(frozen=True)
class TelegramReceipt:
    message_id: int
    chat_username: str
    provider_date: datetime


@dataclass(frozen=True)
class PublicationRecoverySummary:
    workspace_id: str
    reconciled_count: int
    retrying_count: int
    failed_count: int
    delivery_unknown_count: int


@dataclass(frozen=True)
class PublicationRunResult:
    ok: bool
    claimed: bool
    status: str
    publication_id: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": self.ok,
            "claimed": self.claimed,
            "status": self.status,
        }
        if self.publication_id is not None:
            result["publication_id"] = self.publication_id
        if self.error is not None:
            result["error"] = self.error
        return result
