"""Advisory exact-version Typefully draft request, without provider I/O.

The caller must provide a consistent owner snapshot and normalized, authenticated
readbacks for the Typefully X account and an already uploaded canonical PNG.
This module cannot verify those external readbacks, reserve an attempt, create a
draft, schedule a post, or authorize publication. Unknown fields are rejected.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from uuid import UUID

from core.publications.handoff import CLIENT_TARGETS, _time, build_publication_handoff


class TypefullyDraftPreparationError(ValueError):
    """Fixed error codes only; never contains copy, account data, or media URLs."""


def _reject(code: str) -> None:
    raise TypefullyDraftPreparationError(code)


def _record(value: object, keys: set[str], code: str) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != keys:
        _reject(code)
    return value


def _media_id(value: object) -> str:
    if type(value) is not str or not re.fullmatch(
        r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}", value
    ) or UUID(value).int == 0:
        _reject("typefully_media_invalid")
    return value


def bind_typefully_media(owner_upload: Mapping, provider_media: Mapping) -> dict[str, object]:
    """Compare a trusted owner upload row with a fresh provider GET projection.

    This does not authenticate the owner row; a future durable owner must read
    it transactionally and prove it records the uploaded canonical PNG bytes.
    """
    owner = _record(
        owner_upload,
        {"source", "media_id", "social_set_id", "content_version_id",
         "asset_sha256", "uploaded_bytes_sha256"},
        "typefully_media_owner_invalid",
    )
    provider = _record(
        provider_media,
        {"source", "media_id", "social_set_id", "status", "observed_at"},
        "typefully_media_readback_invalid",
    )
    media_id = _media_id(owner["media_id"])
    if (owner["source"] != "durable_typefully_media_owner_v1"
        or type(owner["social_set_id"]) is not int or owner["social_set_id"] <= 0
        or type(owner["content_version_id"]) is not str
        or not re.fullmatch(
            r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}",
            owner["content_version_id"],
        )
        or type(owner["asset_sha256"]) is not str
        or not re.fullmatch(r"[a-f0-9]{64}", owner["asset_sha256"])
        or owner["uploaded_bytes_sha256"] != owner["asset_sha256"]):
        _reject("typefully_media_owner_invalid")
    if (provider["source"] != "typefully_media_get_v2"
        or provider["media_id"] != media_id
        or type(provider["social_set_id"]) is not int
        or provider["social_set_id"] != owner["social_set_id"]
        or provider["status"] != "ready"):
        _reject("typefully_media_readback_invalid")
    observed = _time(provider["observed_at"])
    return {
        "source": "owner_bound_typefully_media_v2",
        "media_id": media_id,
        "social_set_id": owner["social_set_id"],
        "content_version_id": owner["content_version_id"],
        "asset_sha256": owner["asset_sha256"],
        "status": "ready",
        "observed_at": observed.isoformat(),
    }


def prepare_typefully_draft(
    snapshot: Mapping,
    *,
    social_set: Mapping,
    media: Mapping,
    now: datetime,
) -> dict[str, object]:
    """Bind approved copy and PNG to an inert X draft body for later review.

    A separate exact-version owner must re-read everything, fence one provider
    attempt, and verify the authenticated provider response before any POST.
    """
    handoff = build_publication_handoff(snapshot, now=now)
    packet = handoff["packet"]
    observed_now = _time(now)
    account = _record(
        social_set,
        {"source", "social_set_id", "x_username", "observed_at"},
        "typefully_account_evidence_invalid",
    )
    social_set_id = account["social_set_id"]
    if (account["source"] != "typefully_social_set_get_v2"
        or type(social_set_id) is not int or social_set_id <= 0
        or type(account["x_username"]) is not str):
        _reject("typefully_account_evidence_invalid")
    expected_x = CLIENT_TARGETS[packet["client_id"]][1]
    if account["x_username"].removeprefix("@").casefold() != expected_x.casefold():
        _reject("typefully_x_account_mismatch")
    account_observed = _time(account["observed_at"])
    if not observed_now - timedelta(minutes=15) <= account_observed <= observed_now:
        _reject("typefully_account_readback_stale")

    upload = _record(
        media,
        {"source", "media_id", "social_set_id", "content_version_id",
         "asset_sha256", "status", "observed_at"},
        "typefully_media_evidence_invalid",
    )
    media_id = _media_id(upload["media_id"])
    # Typefully GET proves media readiness, while only the owner can bind the
    # uploaded bytes back to this immutable version and canonical PNG hash.
    if (upload["source"] != "owner_bound_typefully_media_v2"
        or type(upload["social_set_id"]) is not int
        or upload["social_set_id"] != social_set_id
        or upload["content_version_id"] != packet["content_version_id"]
        or upload["asset_sha256"] != packet["asset"]["sha256"]
        or upload["status"] != "ready"):
        _reject("typefully_media_binding_mismatch")
    media_observed = _time(upload["observed_at"])
    if not (max(_time(packet["version_created_at"]),
                observed_now - timedelta(minutes=15)) <= media_observed <= observed_now):
        _reject("typefully_media_evidence_invalid")

    x_copy = packet["channel_copy"]["x"]
    # No rewriting, truncation or thread splitting after human attestation.
    # Typefully performs its own platform-length validation on the later POST.
    if len(x_copy) > 280:
        _reject("typefully_exact_x_copy_requires_revision")
    body = {
        "platforms": {"x": {"enabled": True, "posts": [
            {"text": x_copy, "media_ids": [media_id]},
        ]}},
        "draft_title": f"CoinEasy {packet['client_id']} {packet['content_version_id']}",
        "publish_at": None,
    }
    preparation = {
        "schema_version": "exact-typefully-draft-preparation@1",
        "purpose": "advisory_preparation_only",
        "workspace_id": packet["workspace_id"],
        "client_id": packet["client_id"],
        "content_item_id": packet["content_item_id"],
        "content_version_id": packet["content_version_id"],
        "approval_id": packet["approval"]["approval_id"],
        "source_item_id": packet["source"]["source_item_id"],
        "asset_sha256": packet["asset"]["sha256"],
        "handoff_sha256": handoff["packet_sha256"],
        "social_set_id": social_set_id,
        "x_username": expected_x,
        "account_observed_at": account_observed.isoformat(),
        "media_observed_at": media_observed.isoformat(),
        "request_body": body,
        "execution_authorized": False,
        "provider_attempted": False,
        "durable_attempt_fence_required": True,
        "live_owner_readback_required": True,
    }
    encoded = json.dumps(preparation, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return {"preparation": preparation,
            "preparation_sha256": hashlib.sha256(encoded).hexdigest()}
