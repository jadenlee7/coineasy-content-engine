"""Default-OFF, exact-version, one-attempt Typefully X draft worker.

This worker never schedules or publishes. It requires an existing durable
media-upload receipt; media upload itself is a separate operator step until a
one-shot media-allocation fence exists. The DB reservation commits UNKNOWN
before this worker makes its sole create-draft POST. An uncertain response is
never retried. No copy, provider body, signed URL, or credential is logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import quote
from uuid import UUID

import httpx

from core.publications.handoff import CLIENT_TARGETS
from core.publications.settings import _supabase_url
from core.publications.typefully_readback import (
    BASE_URL,
    _id as _social_set_id,
    _key,
    _media_id,
    read_typefully_media,
    read_typefully_social_set,
)


_HEX40 = re.compile(r"^[a-f0-9]{40}$")
_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_PNG = b"\x89PNG\r\n\x1a\n"


class TypefullyDraftOwnerError(ValueError):
    """Fixed error codes only."""


def _fail(code: str) -> None:
    raise TypefullyDraftOwnerError(code)


def _uuid(value: object) -> str:
    if type(value) is not str:
        _fail("typefully_identifier_invalid")
    try:
        parsed = UUID(value)
    except ValueError:
        _fail("typefully_identifier_invalid")
    if parsed.int == 0 or str(parsed) != value:
        _fail("typefully_identifier_invalid")
    return value


def _positive(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 9_007_199_254_740_991:
        _fail("typefully_number_invalid")
    return value


def _stamp(value: object) -> str:
    if type(value) is not str:
        _fail("typefully_time_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("typefully_time_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("typefully_time_invalid")
    return value


@dataclass(frozen=True)
class TypefullyDraftOnceSettings:
    enabled: bool
    supabase_url: str
    service_role_key: str
    workspace_id: str
    client_id: str
    content_item_id: str
    content_version_id: str
    approval_id: str
    social_set_id: int
    api_key: str
    deployed_sha: str
    authorized_sha: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TypefullyDraftOnceSettings | None":
        values = os.environ if env is None else env
        flag = values.get("TYPEFULLY_DRAFT_ENABLED", "false")
        if flag == "false":
            return None
        if flag != "true":
            _fail("typefully_enable_flag_invalid")
        deployed = values.get("RAILWAY_GIT_COMMIT_SHA", "")
        pinned = values.get("TYPEFULLY_DRAFT_RELEASE_SHA", "")
        if not _HEX40.fullmatch(deployed) or deployed != pinned:
            _fail("typefully_release_fence_mismatch")
        client_id = values.get("TYPEFULLY_DRAFT_CLIENT_ID", "")
        if client_id not in CLIENT_TARGETS:
            _fail("typefully_client_invalid")
        try:
            url = _supabase_url(values.get("SUPABASE_URL", ""))
            social_set_id = _positive(int(values.get("TYPEFULLY_DRAFT_SOCIAL_SET_ID", "")))
        except (ValueError, TypefullyDraftOwnerError):
            _fail("typefully_settings_invalid")
        service_key = values.get("SUPABASE_SERVICE_ROLE_KEY", "")
        api_key = values.get("TYPEFULLY_API_KEY", "")
        if (not 32 <= len(service_key) <= 8192
            or not 32 <= len(api_key) <= 512
            or not api_key.isascii() or any(ord(char) <= 32 for char in api_key)):
            _fail("typefully_credentials_invalid")
        return cls(
            enabled=True, supabase_url=url, service_role_key=service_key,
            workspace_id=_uuid(values.get("CONTENT_STUDIO_WORKSPACE_ID", "")),
            client_id=client_id,
            content_item_id=_uuid(values.get("TYPEFULLY_DRAFT_CONTENT_ITEM_ID", "")),
            content_version_id=_uuid(values.get("TYPEFULLY_DRAFT_CONTENT_VERSION_ID", "")),
            approval_id=_uuid(values.get("TYPEFULLY_DRAFT_APPROVAL_ID", "")),
            social_set_id=social_set_id, api_key=api_key,
            deployed_sha=deployed, authorized_sha=pinned,
        )


class SupabaseTypefullyDraftOwner:
    def __init__(self, settings: TypefullyDraftOnceSettings,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.transport = settings, transport

    def _headers(self) -> dict[str, str]:
        key = self.settings.service_role_key
        return {"apikey": key, "Authorization": f"Bearer {key}"}

    async def _rpc(self, name: str, data: Mapping[str, object]) -> object:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False,
                                         trust_env=False, transport=self.transport) as client:
                response = await client.post(
                    f"{self.settings.supabase_url}/rest/v1/rpc/{name}",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=dict(data),
                )
            if response.status_code != 200:
                _fail("typefully_database_unavailable")
            return response.json()
        except TypefullyDraftOwnerError:
            raise
        except Exception:
            # A timeout after a mutating RPC may mean it committed. Never
            # repeat record/reserve/confirm blindly with another identity.
            _fail("typefully_database_unknown")

    async def existing_attempt(self) -> Mapping | None:
        raw = await self._rpc("get_typefully_draft_attempt", {
            "target_workspace_id": self.settings.workspace_id,
            "target_content_item_id": self.settings.content_item_id,
        })
        if raw is None:
            return None
        if not isinstance(raw, dict) or raw.get("status") not in (
            "delivery_unknown", "draft_created"
        ) or raw.get("content_version_id") != self.settings.content_version_id:
            _fail("typefully_attempt_readback_invalid")
        _uuid(raw.get("attempt_id"))
        return raw

    async def candidate(self) -> Mapping:
        raw = await self._rpc("get_typefully_draft_candidate", {
            "target_workspace_id": self.settings.workspace_id,
            "target_content_item_id": self.settings.content_item_id,
            "target_content_version_id": self.settings.content_version_id,
            "target_approval_id": self.settings.approval_id,
        })
        if not isinstance(raw, dict):
            _fail("typefully_candidate_unavailable")
        for key, expected in (
            ("workspace_id", self.settings.workspace_id),
            ("client_id", self.settings.client_id),
            ("content_item_id", self.settings.content_item_id),
            ("content_version_id", self.settings.content_version_id),
            ("approval_id", self.settings.approval_id),
        ):
            if raw.get(key) != expected:
                _fail("typefully_candidate_mismatch")
        asset_id = _uuid(raw.get("asset_id"))
        expected_path = (f"{self.settings.workspace_id}/{self.settings.client_id}/"
                         f"{asset_id}/news-card.png")
        if (raw.get("storage_bucket") != "content-studio"
            or raw.get("storage_path") != expected_path
            or type(raw.get("asset_sha256")) is not str
            or not _HEX64.fullmatch(raw["asset_sha256"])
            or type(raw.get("x_copy")) is not str
            or not 1 <= len(raw["x_copy"]) <= 280
            or not raw["x_copy"].strip()
            or not 24 <= _positive(raw.get("asset_byte_size")) <= 10 * 1024 * 1024
            or not 1 <= _positive(raw.get("asset_width")) <= 10000
            or not 1 <= _positive(raw.get("asset_height")) <= 10000):
            _fail("typefully_candidate_invalid")
        _stamp(raw.get("version_created_at"))
        return raw

    async def media_receipt(self) -> Mapping:
        raw = await self._rpc("get_typefully_media_upload_receipt", {
            "target_workspace_id": self.settings.workspace_id,
            "target_content_item_id": self.settings.content_item_id,
            "target_social_set_id": self.settings.social_set_id,
        })
        if not isinstance(raw, dict):
            _fail("typefully_media_receipt_required")
        _uuid(raw.get("media_receipt_id"))
        _uuid(raw.get("media_id"))
        return raw

    async def download_png(self, candidate: Mapping) -> bytes:
        encoded = "/".join(quote(part, safe="") for part in candidate["storage_path"].split("/"))
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False,
                                         trust_env=False, transport=self.transport) as client:
                async with client.stream(
                    "GET",
                    f"{self.settings.supabase_url}/storage/v1/object/content-studio/{encoded}",
                    headers=self._headers(),
                ) as response:
                    if response.status_code != 200 or response.headers.get(
                        "content-type", ""
                    ).split(";", 1)[0].lower() != "image/png":
                        _fail("typefully_asset_unavailable")
                    output = bytearray()
                    async for chunk in response.aiter_bytes():
                        output.extend(chunk)
                        if len(output) > 10 * 1024 * 1024:
                            _fail("typefully_asset_invalid")
            data = bytes(output)
        except TypefullyDraftOwnerError:
            raise
        except Exception:
            _fail("typefully_asset_unavailable")
        if (len(data) != candidate["asset_byte_size"]
            or len(data) > 10 * 1024 * 1024
            or not data.startswith(_PNG) or data[12:16] != b"IHDR"
            or struct.unpack(">II", data[16:24]) != (
                candidate["asset_width"], candidate["asset_height"])
            or hashlib.sha256(data).hexdigest() != candidate["asset_sha256"]):
            _fail("typefully_asset_invalid")
        return data

    async def reserve(self, candidate: Mapping, media: Mapping,
                      account: Mapping, ready: Mapping) -> Mapping:
        raw = await self._rpc("reserve_typefully_draft_once", {
            "target_workspace_id": self.settings.workspace_id,
            "target_content_item_id": self.settings.content_item_id,
            "target_content_version_id": self.settings.content_version_id,
            "target_approval_id": self.settings.approval_id,
            "target_media_receipt_id": media["media_receipt_id"],
            "expected_social_set_id": self.settings.social_set_id,
            "observed_x_username": account["x_username"].removeprefix("@").lower(),
            "account_observed_at": account["observed_at"],
            "media_observed_at": ready["observed_at"],
            "observed_media_status": ready["status"],
        })
        expected_body = {
            "platforms": {"x": {"enabled": True, "posts": [{
                "text": candidate["x_copy"], "media_ids": [media["media_id"]],
            }]}},
            "draft_title": (f"CoinEasy {self.settings.client_id} "
                            f"{self.settings.content_version_id}"),
            "publish_at": None,
        }
        if (not isinstance(raw, dict) or raw.get("status") != "delivery_unknown"
            or raw.get("content_version_id") != self.settings.content_version_id
            or raw.get("approval_id") != self.settings.approval_id
            or raw.get("social_set_id") != self.settings.social_set_id
            or raw.get("request_body") != expected_body):
            # Reservation may already be durable. A mismatched response never
            # authorizes POST; readback/reconciliation is the only next action.
            _fail("typefully_reservation_mismatch")
        _uuid(raw.get("attempt_id"))
        return raw

    async def confirm(self, attempt_id: str, draft_id: int) -> Mapping:
        raw = await self._rpc("confirm_typefully_draft_once", {
            "target_attempt_id": _uuid(attempt_id),
            "observed_social_set_id": self.settings.social_set_id,
            "observed_provider_draft_id": _positive(draft_id),
            "observed_status": "draft",
        })
        if (not isinstance(raw, dict) or raw.get("attempt_id") != attempt_id
            or raw.get("status") != "draft_created"
            or raw.get("social_set_id") != self.settings.social_set_id
            or raw.get("provider_draft_id") != draft_id):
            _fail("typefully_confirmation_unknown")
        return raw


async def create_draft_once(*, social_set_id: int, api_key: str,
                            body: Mapping, transport=None) -> int:
    try:
        _social_set_id(social_set_id)
        _key(api_key)
    except ValueError:
        _fail("typefully_draft_request_invalid")
    if (not isinstance(body, Mapping)
        or body.get("publish_at", "missing") is not None or "plan_at" in body
        or set(body) != {"platforms", "draft_title", "publish_at"}
        or not isinstance(body["platforms"], Mapping)
        or set(body["platforms"]) != {"x"}):
        _fail("typefully_draft_only")
    x = body["platforms"]["x"]
    if (not isinstance(x, Mapping) or set(x) != {"enabled", "posts"}
        or x["enabled"] is not True or not isinstance(x["posts"], list)
        or len(x["posts"]) != 1 or not isinstance(x["posts"][0], Mapping)
        or set(x["posts"][0]) != {"text", "media_ids"}
        or type(x["posts"][0]["text"]) is not str
        or not 1 <= len(x["posts"][0]["text"]) <= 280
        or not x["posts"][0]["text"].strip()
        or not isinstance(x["posts"][0]["media_ids"], list)
        or len(x["posts"][0]["media_ids"]) != 1
        or type(body["draft_title"]) is not str
        or not re.fullmatch(r"CoinEasy (yellow|origintrail|squid|babylon) "
                            r"[a-f0-9]{8}-[a-f0-9-]{27,36}", body["draft_title"])):
        _fail("typefully_draft_request_invalid")
    try:
        _media_id(x["posts"][0]["media_ids"][0])
    except ValueError:
        _fail("typefully_draft_request_invalid")
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False,
                                     trust_env=False, transport=transport) as client:
            response = await client.post(
                f"{BASE_URL}/social-sets/{social_set_id}/drafts",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=dict(body),
            )
        if response.status_code != 201:
            _fail("typefully_draft_create_unknown")
        payload = response.json()
        if (not isinstance(payload, dict) or payload.get("status") != "draft"
            or payload.get("social_set_id") != social_set_id):
            _fail("typefully_draft_create_unknown")
        return _positive(payload.get("id"))
    except TypefullyDraftOwnerError:
        raise
    except Exception:
        _fail("typefully_draft_create_unknown")


async def run_typefully_draft_once(settings: TypefullyDraftOnceSettings,
                                  *, repository=None, typefully_transport=None) -> dict:
    """Run one exact approved version; every failure is terminal for this run."""
    if settings.enabled is not True:
        _fail("typefully_disabled")
    if (not _HEX40.fullmatch(settings.deployed_sha)
        or settings.deployed_sha != settings.authorized_sha):
        _fail("typefully_release_fence_mismatch")
    if settings.client_id not in CLIENT_TARGETS:
        _fail("typefully_client_invalid")
    for identifier in (settings.workspace_id, settings.content_item_id,
                       settings.content_version_id, settings.approval_id):
        _uuid(identifier)
    _positive(settings.social_set_id)
    repo = repository if repository is not None else SupabaseTypefullyDraftOwner(settings)
    existing = await repo.existing_attempt()
    if existing is not None:
        return {"status": "already_reserved", "attempt_id": existing["attempt_id"],
                "attempt_status": existing["status"]}
    candidate = await repo.candidate()
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False,
                                 trust_env=False, transport=typefully_transport) as client:
        account = await read_typefully_social_set(
            social_set_id=settings.social_set_id, client_id=settings.client_id,
            api_key=settings.api_key, client=client,
        )
        media = await repo.media_receipt()
        if (media.get("content_version_id") != settings.content_version_id
            or media.get("asset_id") != candidate["asset_id"]
            or media.get("asset_sha256") != candidate["asset_sha256"]
            or media.get("uploaded_bytes_sha256") != candidate["asset_sha256"]
            or media.get("social_set_id") != settings.social_set_id):
            _fail("typefully_media_receipt_mismatch")
        await repo.download_png(candidate)
        ready = await read_typefully_media(
            social_set_id=settings.social_set_id, media_id=media["media_id"],
            api_key=settings.api_key, client=client,
        )
    reserved = await repo.reserve(candidate, media, account, ready)
    draft_id = await create_draft_once(
        social_set_id=settings.social_set_id, api_key=settings.api_key,
        body=reserved["request_body"], transport=typefully_transport,
    )
    confirmed = await repo.confirm(reserved["attempt_id"], draft_id)
    return {"status": "draft_created", "attempt_id": confirmed["attempt_id"],
            "provider_draft_id": draft_id, "social_set_id": settings.social_set_id}


def main() -> int:
    try:
        settings = TypefullyDraftOnceSettings.from_env()
        if settings is None:
            print('{"status":"disabled"}')
            return 0
        result = asyncio.run(run_typefully_draft_once(settings))
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        code = str(exc)
        # Never print provider/DB exception text or caller-supplied values.
        if not isinstance(exc, TypefullyDraftOwnerError):
            code = "typefully_worker_blocked"
        print(json.dumps({"status": "blocked", "code": code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
