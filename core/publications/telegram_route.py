"""Opaque exact-route binding shared by the channel owner and final review.

Contains no credentials and does not construct a provider client.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from core.publications.settings import PUBLICATION_TELEGRAM_USERNAMES


def telegram_route_binding(*, workspace_id: str, client_id: str,
                           release_sha: str, channel_id: int, bot_id: int,
                           username: str) -> str:
    try:
        canonical_workspace = str(uuid.UUID(workspace_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid Telegram route binding") from exc
    if (canonical_workspace != workspace_id
        or not re.fullmatch(r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}", workspace_id)
        or not isinstance(release_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", release_sha)
        or username != PUBLICATION_TELEGRAM_USERNAMES.get(client_id)
        or isinstance(channel_id, bool) or not isinstance(channel_id, int)
        or not -9_223_372_036_854_775_808 <= channel_id < 0
        or isinstance(bot_id, bool) or not isinstance(bot_id, int)
        or not 0 < bot_id <= 9_223_372_036_854_775_807):
        raise ValueError("invalid Telegram route binding")
    payload = {"schema": "content-ops-publication-route@1",
               "workspace_id": workspace_id, "client_id": client_id,
               "release_sha": release_sha, "provider": "telegram",
               "action": "official_channel_post", "channel_id": channel_id,
               "bot_id": bot_id, "username": username}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
