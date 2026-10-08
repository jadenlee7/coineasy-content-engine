"""Bounded, read-only Typefully v2 account and media status observations.

Only GET operations are available. No create, upload, schedule or publish
method exists here. Responses are projected to identifiers and status; URLs,
profile metadata, provider error bodies and credentials are never returned.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID

import httpx

from core.publications.handoff import CLIENT_TARGETS


BASE_URL = "https://api.typefully.com/v2"


class TypefullyReadbackError(ValueError):
    """Fixed codes only, with no provider response or account data."""


def _fail(code: str) -> None:
    raise TypefullyReadbackError(code)


def _id(value: object) -> int:
    if type(value) is not int or value <= 0:
        _fail("typefully_social_set_invalid")
    return value


def _media_id(value: object) -> str:
    if type(value) is not str or not re.fullmatch(
        r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}", value
    ) or UUID(value).int == 0:
        _fail("typefully_media_id_invalid")
    return value


def _observed_at(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("typefully_observation_time_invalid")
    return value.astimezone(timezone.utc).isoformat()


def _key(value: object) -> str:
    if (type(value) is not str or not 32 <= len(value) <= 512
        or not value.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        _fail("typefully_read_key_invalid")
    return value


def _account(payload: object, *, social_set_id: int, expected_x_username: str,
             observed_at: str) -> dict[str, object]:
    if not isinstance(payload, dict) or type(payload.get("id")) is not int:
        _fail("typefully_account_readback_invalid")
    username = payload.get("username")
    if type(username) is not str or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
        _fail("typefully_account_readback_invalid")
    if payload["id"] != social_set_id or username.casefold() != expected_x_username.casefold():
        _fail("typefully_x_account_mismatch")
    return {"source": "typefully_social_set_get_v2", "social_set_id": social_set_id,
            "x_username": username, "observed_at": observed_at}


def _media(payload: object, *, social_set_id: int, media_id: str,
           observed_at: str) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("media_id") != media_id:
        _fail("typefully_media_readback_invalid")
    status = payload.get("status")
    if status != "ready":
        _fail("typefully_media_not_ready")
    return {"source": "typefully_media_get_v2", "social_set_id": social_set_id,
            "media_id": media_id, "status": "ready", "observed_at": observed_at}


async def read_typefully_social_set(
    *, social_set_id: int, client_id: str, api_key: str,
    client=None, clock=None,
) -> dict[str, object]:
    """One authenticated GET; refuses wrong X identity without response echoes."""
    social_set_id = _id(social_set_id)
    if type(client_id) is not str or client_id not in CLIENT_TARGETS:
        _fail("typefully_expected_account_invalid")
    expected_x_username = CLIENT_TARGETS[client_id][1]
    key = _key(api_key)
    if clock is not None and not callable(clock):
        _fail("typefully_observation_time_invalid")
    owned_client = client is None
    request_client = client if client is not None else httpx.AsyncClient(timeout=10.0)
    try:
        response = await request_client.get(
            f"{BASE_URL}/social-sets/{social_set_id}/",
            headers={"Authorization": f"Bearer {key}"},
        )
        if response.status_code != 200:
            _fail("typefully_account_readback_unavailable")
        timestamp = _observed_at(clock() if clock is not None else
                                 datetime.now(timezone.utc))
        return _account(response.json(), social_set_id=social_set_id,
                        expected_x_username=expected_x_username, observed_at=timestamp)
    except TypefullyReadbackError:
        raise
    except Exception:
        _fail("typefully_account_readback_unavailable")
    finally:
        if owned_client:
            try:
                await request_client.aclose()
            except Exception:
                _fail("typefully_account_readback_unavailable")


async def read_typefully_media(
    *, social_set_id: int, media_id: str, api_key: str,
    client=None, clock=None,
) -> dict[str, object]:
    """One authenticated GET; this does not prove uploaded bytes or version."""
    social_set_id, media_id = _id(social_set_id), _media_id(media_id)
    key = _key(api_key)
    if clock is not None and not callable(clock):
        _fail("typefully_observation_time_invalid")
    owned_client = client is None
    request_client = client if client is not None else httpx.AsyncClient(timeout=10.0)
    try:
        response = await request_client.get(
            f"{BASE_URL}/social-sets/{social_set_id}/media/{media_id}",
            headers={"Authorization": f"Bearer {key}"},
        )
        if response.status_code != 200:
            _fail("typefully_media_readback_unavailable")
        timestamp = _observed_at(clock() if clock is not None else
                                 datetime.now(timezone.utc))
        return _media(response.json(), social_set_id=social_set_id,
                      media_id=media_id, observed_at=timestamp)
    except TypefullyReadbackError:
        raise
    except Exception:
        _fail("typefully_media_readback_unavailable")
    finally:
        if owned_client:
            try:
                await request_client.aclose()
            except Exception:
                _fail("typefully_media_readback_unavailable")
