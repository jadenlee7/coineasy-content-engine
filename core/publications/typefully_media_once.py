"""Default-OFF, exact-version, one-attempt Typefully PNG media owner.

The allocation reservation is durable before the provider POST. The allocated
media ID is durable before the raw S3 PUT. An unknown response at either step
is terminal for this run and is never retried automatically. This worker does
not create a draft, schedule a post, or publish to X.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Mapping

import httpx

from core.publications.handoff import CLIENT_TARGETS
from core.publications.settings import _supabase_url
from core.publications.typefully_draft_once import (
    SupabaseTypefullyDraftOwner,
    TypefullyDraftOwnerError,
    _HEX40,
    _fail,
    _positive,
    _uuid,
)
from core.publications.typefully_media_upload import upload_typefully_png_once
from core.publications.typefully_readback import _key, _media_id, read_typefully_social_set


@dataclass(frozen=True)
class TypefullyMediaOnceSettings:
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
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TypefullyMediaOnceSettings | None":
        values = os.environ if env is None else env
        flag = values.get("TYPEFULLY_MEDIA_ENABLED", "false")
        if flag == "false":
            return None
        if flag != "true":
            _fail("typefully_media_enable_flag_invalid")
        deployed = values.get("RAILWAY_GIT_COMMIT_SHA", "")
        pinned = values.get("TYPEFULLY_MEDIA_RELEASE_SHA", "")
        if not _HEX40.fullmatch(deployed) or deployed != pinned:
            _fail("typefully_media_release_fence_mismatch")
        client_id = values.get("TYPEFULLY_MEDIA_CLIENT_ID", "")
        if client_id not in CLIENT_TARGETS:
            _fail("typefully_client_invalid")
        try:
            url = _supabase_url(values.get("SUPABASE_URL", ""))
            set_id = _positive(int(values.get("TYPEFULLY_MEDIA_SOCIAL_SET_ID", "")))
            api_key = _key(values.get("TYPEFULLY_API_KEY", ""))
        except (ValueError, TypefullyDraftOwnerError):
            _fail("typefully_media_settings_invalid")
        service_key = values.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not 32 <= len(service_key) <= 8192 or any(ord(char) <= 32 for char in service_key):
            _fail("typefully_media_credentials_invalid")
        return cls(
            enabled=True, supabase_url=url, service_role_key=service_key,
            workspace_id=_uuid(values.get("CONTENT_STUDIO_WORKSPACE_ID", "")),
            client_id=client_id,
            content_item_id=_uuid(values.get("TYPEFULLY_MEDIA_CONTENT_ITEM_ID", "")),
            content_version_id=_uuid(values.get("TYPEFULLY_MEDIA_CONTENT_VERSION_ID", "")),
            approval_id=_uuid(values.get("TYPEFULLY_MEDIA_APPROVAL_ID", "")),
            social_set_id=set_id, api_key=api_key,
            deployed_sha=deployed, authorized_sha=pinned,
        )


class SupabaseTypefullyMediaOwner(SupabaseTypefullyDraftOwner):
    async def existing_allocation(self) -> Mapping | None:
        raw = await self._rpc("get_typefully_media_allocation_attempt", {
            "target_workspace_id": self.settings.workspace_id,
            "target_content_item_id": self.settings.content_item_id,
        })
        if raw is None:
            return None
        if (not isinstance(raw, dict)
            or raw.get("content_version_id") != self.settings.content_version_id
            or raw.get("status") not in ("allocation_unknown", "upload_unknown", "uploaded")):
            _fail("typefully_allocation_readback_invalid")
        _uuid(raw.get("attempt_id"))
        return raw

    async def reserve_allocation(self, candidate: Mapping, account: Mapping) -> Mapping:
        raw = await self._rpc("reserve_typefully_media_allocation_once", {
            "target_workspace_id": self.settings.workspace_id,
            "target_content_item_id": self.settings.content_item_id,
            "target_content_version_id": self.settings.content_version_id,
            "target_approval_id": self.settings.approval_id,
            "expected_social_set_id": self.settings.social_set_id,
            "observed_x_username": account["x_username"].removeprefix("@").lower(),
            "account_observed_at": account["observed_at"],
        })
        if (not isinstance(raw, dict) or raw.get("status") != "allocation_unknown"
            or raw.get("content_version_id") != self.settings.content_version_id
            or raw.get("approval_id") != self.settings.approval_id
            or raw.get("asset_id") != candidate["asset_id"]
            or raw.get("asset_sha256") != candidate["asset_sha256"]
            or raw.get("social_set_id") != self.settings.social_set_id):
            _fail("typefully_allocation_reservation_mismatch")
        _uuid(raw.get("attempt_id"))
        return raw

    async def mark_upload_intent(self, attempt_id: str, media_id: str) -> None:
        raw = await self._rpc("mark_typefully_media_upload_intent", {
            "target_attempt_id": _uuid(attempt_id),
            "observed_social_set_id": self.settings.social_set_id,
            "observed_media_id": _media_id(media_id),
        })
        if (not isinstance(raw, dict) or raw.get("attempt_id") != attempt_id
            or raw.get("status") != "upload_unknown"
            or raw.get("social_set_id") != self.settings.social_set_id
            or raw.get("media_id") != media_id):
            _fail("typefully_upload_intent_mismatch")

    async def record_upload(self, attempt_id: str, candidate: Mapping,
                            uploaded: Mapping) -> str:
        if (uploaded.get("source") != "typefully_media_upload_v2"
            or uploaded.get("social_set_id") != self.settings.social_set_id
            or uploaded.get("uploaded_bytes_sha256") != candidate["asset_sha256"]):
            _fail("typefully_uploaded_bytes_mismatch")
        media_id = _media_id(uploaded.get("media_id"))
        receipt_id = await self._rpc("record_typefully_media_upload", {
            "target_allocation_attempt_id": _uuid(attempt_id),
            "target_workspace_id": self.settings.workspace_id,
            "target_content_item_id": self.settings.content_item_id,
            "target_content_version_id": self.settings.content_version_id,
            "target_asset_id": candidate["asset_id"],
            "target_uploaded_bytes_sha256": uploaded["uploaded_bytes_sha256"],
            "target_social_set_id": self.settings.social_set_id,
            "target_media_id": _media_id(media_id),
        })
        receipt_id = _uuid(receipt_id)
        allocation = await self.existing_allocation()
        receipt = await self.media_receipt()
        if (allocation is None or allocation.get("attempt_id") != attempt_id
            or allocation.get("status") != "uploaded"
            or allocation.get("media_id") != media_id
            or receipt.get("media_receipt_id") != receipt_id
            or receipt.get("content_version_id") != self.settings.content_version_id
            or receipt.get("asset_id") != candidate["asset_id"]
            or receipt.get("asset_sha256") != candidate["asset_sha256"]
            or receipt.get("uploaded_bytes_sha256") != candidate["asset_sha256"]
            or receipt.get("social_set_id") != self.settings.social_set_id
            or receipt.get("media_id") != media_id):
            _fail("typefully_media_confirmation_unknown")
        return receipt_id


async def run_typefully_media_once(
    settings: TypefullyMediaOnceSettings, *, repository=None,
    typefully_transport=None, upload_transport=None,
) -> dict:
    if settings.enabled is not True:
        _fail("typefully_media_disabled")
    if (not _HEX40.fullmatch(settings.deployed_sha)
        or settings.deployed_sha != settings.authorized_sha):
        _fail("typefully_media_release_fence_mismatch")
    if settings.client_id not in CLIENT_TARGETS:
        _fail("typefully_client_invalid")
    for identifier in (settings.workspace_id, settings.content_item_id,
                       settings.content_version_id, settings.approval_id):
        _uuid(identifier)
    _positive(settings.social_set_id)
    try:
        _key(settings.api_key)
    except ValueError:
        _fail("typefully_media_credentials_invalid")
    repo = repository if repository is not None else SupabaseTypefullyMediaOwner(settings)
    existing = await repo.existing_allocation()
    if existing is not None:
        return {"status": "already_reserved", "attempt_id": existing["attempt_id"],
                "attempt_status": existing["status"]}
    candidate = await repo.candidate()
    png_bytes = await repo.download_png(candidate)
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False,
                                 trust_env=False, transport=typefully_transport) as client:
        account = await read_typefully_social_set(
            social_set_id=settings.social_set_id, client_id=settings.client_id,
            api_key=settings.api_key, client=client,
        )
    allocation = await repo.reserve_allocation(candidate, account)

    async def persist_intent(media_id: str) -> None:
        await repo.mark_upload_intent(allocation["attempt_id"], media_id)

    uploaded = await upload_typefully_png_once(
        social_set_id=settings.social_set_id, api_key=settings.api_key,
        png_bytes=png_bytes, expected_sha256=candidate["asset_sha256"],
        persist_upload_intent=persist_intent,
        api_transport=typefully_transport, upload_transport=upload_transport,
    )
    receipt_id = await repo.record_upload(
        allocation["attempt_id"], candidate, uploaded,
    )
    return {"status": "uploaded", "attempt_id": allocation["attempt_id"],
            "media_receipt_id": receipt_id, "media_id": uploaded["media_id"]}


def main() -> int:
    try:
        settings = TypefullyMediaOnceSettings.from_env()
        if settings is None:
            print('{"status":"disabled"}')
            return 0
        print(json.dumps(asyncio.run(run_typefully_media_once(settings)), sort_keys=True))
        return 0
    except Exception as exc:
        # Never echo a provider response, presigned URL, PNG bytes, or key.
        code = str(exc) if isinstance(exc, TypefullyDraftOwnerError) else "typefully_media_blocked"
        print(json.dumps({"status": "blocked", "code": code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
