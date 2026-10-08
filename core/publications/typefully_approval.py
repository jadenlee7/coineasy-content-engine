"""Pure validation of owner-derived human approval and exact X draft routes.

This validates a bounded database projection, not arbitrary Telegram text or a
human identity assertion. It neither grants approval nor makes any I/O.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from uuid import UUID

from core.publications.handoff import CLIENT_TARGETS

_KEYS = {"schema_version", "decision_id", "review_principal_id", "release_sha",
         "version_fingerprint", "snapshot_sha256", "banner_sha256",
         "x_copy_sha256", "telegram_copy_sha256", "typefully_route_binding",
         "telegram_route_binding", "route_verified_at"}


def typefully_route_binding(*, workspace_id, client_id, release_sha,
                           social_set_id, x_username):
    payload = {"schema": "content-ops-publication-route@1", "workspace_id": workspace_id,
               "client_id": client_id, "release_sha": release_sha,
               "provider": "typefully_x", "action": "x_draft_only",
               "social_set_id": social_set_id, "username": x_username.lower()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_telegram_principal_approval(proof: Mapping, *, workspace_id, client_id,
        banner_sha256, x_copy, telegram_copy=None, release_sha=None,
        social_set_id=None, now=None):
    """Require full, hash-bound final-decision evidence; a source string is not proof.

    The trusted owner must join approval, immutable final decision, both channel
    handoffs, and active routes. UUIDs are internal principals, never Auth users.
    Runtime callers MUST supply their pinned release and actual social set.
    """
    def reject():
        raise ValueError("typefully_final_approval_invalid")

    if not isinstance(proof, Mapping) or set(proof) != _KEYS:
        reject()
    if proof["schema_version"] != "content-ops-final-approval@1" or client_id not in CLIENT_TARGETS:
        reject()
    for key in ("decision_id", "review_principal_id"):
        try:
            value = proof[key]
            if type(value) is not str or str(UUID(value)) != value or UUID(value).int == 0:
                reject()
        except (TypeError, ValueError):
            reject()
    if type(proof["release_sha"]) is not str or not re.fullmatch(r"[a-f0-9]{40}", proof["release_sha"]):
        reject()
    for key in _KEYS - {"schema_version", "decision_id", "review_principal_id",
                        "release_sha", "route_verified_at"}:
        if type(proof[key]) is not str or not re.fullmatch(r"[a-f0-9]{64}", proof[key]):
            reject()
    if (proof["banner_sha256"] != banner_sha256 or type(x_copy) is not str
            or proof["x_copy_sha256"] != hashlib.sha256(x_copy.encode("utf-8")).hexdigest()):
        reject()
    if telegram_copy is not None and (type(telegram_copy) is not str
            or proof["telegram_copy_sha256"] != hashlib.sha256(telegram_copy.encode("utf-8")).hexdigest()):
        reject()
    try:
        raw = proof["route_verified_at"]
        if type(raw) is not str:
            reject()
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if observed.utcoffset() is None:
            reject()
        current = datetime.now(timezone.utc) if now is None else now
        if not isinstance(current, datetime) or current.utcoffset() is None:
            reject()
        if not timedelta(0) <= current-observed <= timedelta(minutes=15):
            reject()
    except (TypeError, ValueError, OverflowError):
        reject()
    if release_sha is not None and proof["release_sha"] != release_sha:
        reject()
    if social_set_id is not None:
        if type(social_set_id) is not int or not 0 < social_set_id <= 9_007_199_254_740_991:
            reject()
        expected = typefully_route_binding(workspace_id=workspace_id, client_id=client_id,
            release_sha=proof["release_sha"], social_set_id=social_set_id,
            x_username=CLIENT_TARGETS[client_id][1])
        if proof["typefully_route_binding"] != expected:
            reject()
    return dict(proof)
