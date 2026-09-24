"""Default-OFF daily selector for approved Typefully draft preparation.

This coordinator selects at most one immutable approved item per client and
KST day. It can create Typefully media and a private X draft, never schedule or
publish. Every external write remains behind the media/draft DB attempt fences.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Awaitable, Callable, Mapping
from zoneinfo import ZoneInfo

import httpx

from core.publications.handoff import CLIENT_TARGETS
from core.publications.settings import _supabase_url
from core.publications.typefully_draft_once import (
    TypefullyDraftOnceSettings,
    TypefullyDraftOwnerError,
    _HEX40,
    _fail,
    _positive,
    _uuid,
    run_typefully_draft_once,
)
from core.publications.typefully_media_once import (
    TypefullyMediaOnceSettings,
    run_typefully_media_once,
)
from core.publications.typefully_media_upload import TypefullyMediaUploadError
from core.publications.typefully_readback import TypefullyReadbackError, _key


_KST = ZoneInfo("Asia/Seoul")
_ORDER = ("yellow", "babylon", "squid", "origintrail")
_BUILD_SHA_PATH = Path("/app/typefully-daily-build-sha")


def _release_fence(values: Mapping[str, str], *, stamp_reader: Callable[[], str] | None,
                   require_pin: bool) -> tuple[str, str, str]:
    deployed = values.get("RAILWAY_GIT_COMMIT_SHA", "")
    pinned = values.get("TYPEFULLY_DAILY_RELEASE_SHA", "")
    try:
        build_sha = (stamp_reader or _BUILD_SHA_PATH.read_text)().strip()
    except (OSError, TypeError, AttributeError):
        _fail("typefully_daily_release_fence_mismatch")
    if (not _HEX40.fullmatch(deployed) or not _HEX40.fullmatch(build_sha)
        or deployed != build_sha or (require_pin and pinned != deployed)
        or (pinned and (not _HEX40.fullmatch(pinned) or pinned != deployed))):
        _fail("typefully_daily_release_fence_mismatch")
    return deployed, pinned, build_sha


@dataclass(frozen=True)
class TypefullyDailySettings:
    enabled: bool
    supabase_url: str
    service_role_key: str
    workspace_id: str
    api_key: str
    clients: tuple[str, ...]
    social_sets: Mapping[str, int]
    deployed_sha: str
    authorized_sha: str
    build_sha: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *,
                 stamp_reader: Callable[[], str] | None = None) -> "TypefullyDailySettings | None":
        values = os.environ if env is None else env
        flag = values.get("TYPEFULLY_DAILY_ENABLED", "false")
        if flag == "false":
            return None
        if flag != "true":
            _fail("typefully_daily_enable_flag_invalid")
        deployed, pinned, build_sha = _release_fence(
            values, stamp_reader=stamp_reader, require_pin=True,
        )
        raw_clients = values.get("TYPEFULLY_DAILY_CLIENTS", "")
        clients = tuple(raw_clients.split(","))
        if (not clients or any(client not in CLIENT_TARGETS for client in clients)
            or len(set(clients)) != len(clients)
            or clients != tuple(client for client in _ORDER if client in clients)):
            _fail("typefully_daily_clients_invalid")
        try:
            url = _supabase_url(values.get("SUPABASE_URL", ""))
            key = _key(values.get("TYPEFULLY_API_KEY", ""))
            sets = {client: _positive(int(values.get(
                f"TYPEFULLY_SOCIAL_SET_{client.upper()}", "",
            ))) for client in clients}
        except (ValueError, TypefullyDraftOwnerError):
            _fail("typefully_daily_settings_invalid")
        if len(set(sets.values())) != len(sets):
            _fail("typefully_daily_social_sets_duplicate")
        service_key = values.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not 32 <= len(service_key) <= 8192 or any(ord(char) <= 32 for char in service_key):
            _fail("typefully_daily_credentials_invalid")
        return cls(
            enabled=True, supabase_url=url, service_role_key=service_key,
            workspace_id=_uuid(values.get("CONTENT_STUDIO_WORKSPACE_ID", "")),
            api_key=key, clients=clients, social_sets=sets,
            deployed_sha=deployed, authorized_sha=pinned, build_sha=build_sha,
        )


class SupabaseTypefullyDailySelector:
    def __init__(self, settings: TypefullyDailySettings,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.transport = settings, transport

    async def claim(self, client_id: str) -> Mapping | None:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False,
                                         trust_env=False, transport=self.transport) as client:
                response = await client.post(
                    f"{self.settings.supabase_url}/rest/v1/rpc/claim_typefully_daily_slot",
                    headers={"apikey": self.settings.service_role_key,
                             "Authorization": f"Bearer {self.settings.service_role_key}",
                             "Content-Type": "application/json"},
                    json={"target_workspace_id": self.settings.workspace_id,
                          "target_client_id": client_id},
                )
            if response.status_code != 200:
                _fail("typefully_daily_database_unavailable")
            raw = response.json()
        except TypefullyDraftOwnerError:
            raise
        except Exception:
            # The claim may have committed. Rerun only with the same client/day;
            # DB readback returns the existing immutable slot, never a new one.
            _fail("typefully_daily_claim_unknown")
        if raw is None:
            return None
        kst_date = raw.get("kst_date") if isinstance(raw, dict) else None
        try:
            valid_date = (type(kst_date) is str
                          and date.fromisoformat(kst_date).isoformat() == kst_date)
        except ValueError:
            valid_date = False
        if (not isinstance(raw, dict)
            or raw.get("workspace_id") != self.settings.workspace_id
            or raw.get("client_id") != client_id
            or type(raw.get("reused")) is not bool
            or not valid_date):
            _fail("typefully_daily_slot_invalid")
        for name in ("slot_id", "content_item_id", "content_version_id", "approval_id"):
            _uuid(raw.get(name))
        return raw


def _media_settings(settings: TypefullyDailySettings, client_id: str,
                    slot: Mapping) -> TypefullyMediaOnceSettings:
    return TypefullyMediaOnceSettings(
        enabled=True, supabase_url=settings.supabase_url,
        service_role_key=settings.service_role_key,
        workspace_id=settings.workspace_id, client_id=client_id,
        content_item_id=slot["content_item_id"],
        content_version_id=slot["content_version_id"],
        approval_id=slot["approval_id"],
        social_set_id=settings.social_sets[client_id],
        api_key=settings.api_key, deployed_sha=settings.deployed_sha,
        authorized_sha=settings.authorized_sha,
    )


def _draft_settings(settings: TypefullyDailySettings, client_id: str,
                    slot: Mapping) -> TypefullyDraftOnceSettings:
    return TypefullyDraftOnceSettings(
        enabled=True, supabase_url=settings.supabase_url,
        service_role_key=settings.service_role_key,
        workspace_id=settings.workspace_id, client_id=client_id,
        content_item_id=slot["content_item_id"],
        content_version_id=slot["content_version_id"],
        approval_id=slot["approval_id"],
        social_set_id=settings.social_sets[client_id],
        api_key=settings.api_key, deployed_sha=settings.deployed_sha,
        authorized_sha=settings.authorized_sha,
    )


async def run_typefully_daily(
    settings: TypefullyDailySettings, *, selector=None,
    media_runner: Callable[[TypefullyMediaOnceSettings], Awaitable[dict]] = run_typefully_media_once,
    draft_runner: Callable[[TypefullyDraftOnceSettings], Awaitable[dict]] = run_typefully_draft_once,
) -> dict:
    if settings.enabled is not True:
        _fail("typefully_daily_disabled")
    if (not _HEX40.fullmatch(settings.deployed_sha)
        or not _HEX40.fullmatch(settings.build_sha)
        or settings.deployed_sha != settings.authorized_sha
        or settings.deployed_sha != settings.build_sha):
        _fail("typefully_daily_release_fence_mismatch")
    try:
        if _supabase_url(settings.supabase_url) != settings.supabase_url:
            _fail("typefully_daily_settings_invalid")
        _key(settings.api_key)
    except ValueError:
        _fail("typefully_daily_settings_invalid")
    if (type(settings.service_role_key) is not str
        or not 32 <= len(settings.service_role_key) <= 8192
        or any(ord(char) <= 32 for char in settings.service_role_key)):
        _fail("typefully_daily_credentials_invalid")
    _uuid(settings.workspace_id)
    if (not settings.clients or len(set(settings.clients)) != len(settings.clients)
        or settings.clients != tuple(client for client in _ORDER if client in settings.clients)
        or any(client not in settings.social_sets for client in settings.clients)
        or len({settings.social_sets[client] for client in settings.clients}) != len(settings.clients)):
        _fail("typefully_daily_clients_invalid")
    for client in settings.clients:
        _positive(settings.social_sets[client])
    effective_selector = selector if selector is not None else SupabaseTypefullyDailySelector(settings)
    outcomes = []
    for client_id in settings.clients:
        try:
            slot = await effective_selector.claim(client_id)
            if slot is None:
                outcomes.append({"client_id": client_id, "status": "no_candidate"})
                continue
            media = await media_runner(_media_settings(settings, client_id, slot))
            if (media.get("status") == "already_reserved"
                and media.get("attempt_status") != "uploaded"):
                outcomes.append({"client_id": client_id, "status": "media_unknown",
                                 "slot_id": slot["slot_id"]})
                continue
            if media.get("status") not in ("uploaded", "already_reserved"):
                _fail("typefully_daily_media_result_invalid")
            draft = await draft_runner(_draft_settings(settings, client_id, slot))
            if draft.get("status") not in ("draft_created", "already_reserved"):
                _fail("typefully_daily_draft_result_invalid")
            draft_status = draft["status"]
            if draft_status == "already_reserved":
                draft_status = ("already_reserved" if draft.get("attempt_status") == "draft_created"
                                else "draft_unknown")
            outcomes.append({"client_id": client_id, "status": draft_status,
                             "slot_id": slot["slot_id"]})
        except (TypefullyDraftOwnerError, TypefullyMediaUploadError,
                TypefullyReadbackError) as exc:
            # Fixed library codes only; no raw provider or DB bodies.
            outcomes.append({"client_id": client_id, "status": "blocked",
                             "code": str(exc)})
        except Exception:
            outcomes.append({"client_id": client_id, "status": "blocked",
                             "code": "typefully_daily_unknown"})
    return {"kst_date": datetime.now(_KST).date().isoformat(),
            "outcomes": outcomes}


def main() -> int:
    try:
        settings = TypefullyDailySettings.from_env()
        if settings is None:
            print('{"status":"disabled"}')
            return 0
        result = asyncio.run(run_typefully_daily(settings))
        print(json.dumps(result, sort_keys=True))
        return 0 if all(item["status"] in (
            "no_candidate", "draft_created", "already_reserved"
        ) for item in result["outcomes"]) else 2
    except Exception as exc:
        code = str(exc) if isinstance(exc, TypefullyDraftOwnerError) else "typefully_daily_blocked"
        print(json.dumps({"status": "blocked", "code": code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
