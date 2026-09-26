"""Pure, unmounted verifier for exact public-channel route evidence.

The caller must obtain expected identities from an independently approved
owner configuration and observations from read-only provider calls. This
module makes no network, credential, database, draft, or publishing call.
Its digests are identity bindings, not evidence of human approval or a send.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from core.publications.handoff import CLIENT_TARGETS


_UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")
_TG_HANDLE = re.compile(r"[A-Za-z0-9_]{5,32}\Z")
_X_HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
_FRESHNESS = timedelta(minutes=15)


class PublicationRouteError(ValueError):
    """Fixed code only: provider responses and account IDs must not leak."""


def _require(condition: bool, code: str = "publication_route_unverified") -> None:
    if not condition:
        raise PublicationRouteError(code)


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True, repr=False)
class ExpectedPublicationRoutes:
    """Exact identities from owner-approved configuration, never from card text."""

    workspace_id: str
    client_id: str
    release_sha: str
    telegram_channel_id: int
    telegram_username: str
    telegram_bot_id: int
    typefully_social_set_id: int
    x_username: str


@dataclass(frozen=True, repr=False)
class VerifiedPublicationRoutes:
    workspace_id: str
    client_id: str
    release_sha: str
    telegram_route_binding: str
    typefully_route_binding: str
    telegram_destination_label: str
    typefully_x_destination_label: str
    verified_at: datetime


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def verify_publication_routes(expected: ExpectedPublicationRoutes, *,
        telegram_bot: dict, telegram_channel: dict, telegram_member: dict,
        typefully_social_set: dict, observed_at: datetime,
        now: datetime) -> VerifiedPublicationRoutes:
    """Verify two read-only observations against one approved, exact policy.

    Telegram evidence is getMe/getChat/getChatMember for the publishing bot.
    Typefully evidence is one social-set detail with exactly one X platform.
    Refreshing a matching observation does not change the identity digest;
    verified_at remains separate so a database gate can enforce freshness.
    """
    _require(type(expected) is ExpectedPublicationRoutes)
    _require(type(expected.workspace_id) is str
             and bool(_UUID.fullmatch(expected.workspace_id))
             and UUID(expected.workspace_id).int != 0
             and expected.client_id in CLIENT_TARGETS
             and type(expected.release_sha) is str
             and bool(_RELEASE.fullmatch(expected.release_sha))
             and _positive_int(expected.telegram_bot_id)
             and type(expected.telegram_channel_id) is int
             and expected.telegram_channel_id < 0
             and _positive_int(expected.typefully_social_set_id)
             and type(expected.telegram_username) is str
             and bool(_TG_HANDLE.fullmatch(expected.telegram_username))
             and type(expected.x_username) is str
             and bool(_X_HANDLE.fullmatch(expected.x_username)),
             "publication_route_policy_invalid")
    _require(type(observed_at) is datetime and type(now) is datetime
             and observed_at.tzinfo is not None and now.tzinfo is not None
             and observed_at.utcoffset() is not None
             and now.utcoffset() is not None
             and timedelta(0) <= now-observed_at <= _FRESHNESS,
             "publication_route_observation_stale")
    _require(all(type(value) is dict for value in (
        telegram_bot, telegram_channel, telegram_member, typefully_social_set)))
    member_user = telegram_member.get("user")
    _require(telegram_bot.get("id") == expected.telegram_bot_id
             and type(telegram_bot.get("id")) is int
             and telegram_bot.get("is_bot") is True
             and telegram_channel.get("id") == expected.telegram_channel_id
             and type(telegram_channel.get("id")) is int
             and telegram_channel.get("type") == "channel"
             and type(telegram_channel.get("username")) is str
             and telegram_channel["username"].lower() == expected.telegram_username.lower()
             and type(member_user) is dict
             and member_user.get("id") == expected.telegram_bot_id
             and type(member_user.get("id")) is int
             and member_user.get("is_bot") is True
             and telegram_member.get("status") == "administrator"
             and telegram_member.get("can_post_messages") is True,
             "publication_route_telegram_unverified")
    platforms = typefully_social_set.get("platforms")
    x = platforms.get("x") if type(platforms) is dict else None
    _require(type(typefully_social_set.get("id")) is int
             and typefully_social_set["id"] == expected.typefully_social_set_id
             and type(platforms) is dict and type(x) is dict
             and set(platforms) == {"x", "linkedin", "mastodon", "threads",
                                   "bluesky", "substack"}
             and all(value is None for key, value in platforms.items() if key != "x")
             and x.get("platform") == "x"
             and type(x.get("username")) is str
             and x["username"].lower() == expected.x_username.lower()
             and x.get("profile_url") == "https://x.com/" + x["username"],
             "publication_route_typefully_unverified")
    common = {"schema": "content-ops-publication-route@1",
              "workspace_id": expected.workspace_id,
              "client_id": expected.client_id,
              "release_sha": expected.release_sha}
    telegram_binding = _digest({**common, "provider": "telegram",
        "action": "official_channel_post", "channel_id": expected.telegram_channel_id,
        "bot_id": expected.telegram_bot_id,
        "username": expected.telegram_username.lower()})
    typefully_binding = _digest({**common, "provider": "typefully_x",
        "action": "x_draft_only", "social_set_id": expected.typefully_social_set_id,
        "username": expected.x_username.lower()})
    return VerifiedPublicationRoutes(expected.workspace_id, expected.client_id,
        expected.release_sha, telegram_binding, typefully_binding,
        "@" + telegram_channel["username"], "@" + x["username"], observed_at)
