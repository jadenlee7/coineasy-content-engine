"""Pure, advisory preparation of an exact human-approved Daily News version.

Input must be a sanitized, trusted caller snapshot from the owner system. This
module cannot authenticate the caller, prove that an approval is latest, read
Storage, recompute existing fact-check fingerprints, or grant execution rights.
It performs no filesystem, environment, credential, network, or provider I/O.
The resulting hash binds a local preparation packet; it is NOT a publication
receipt, human approval, queue command, or reusable authorization token.

``build_publication_handoff(snapshot, now=aware_datetime)`` requires exact fields:
workspace_id, client_id, client_active, content_item_id, content_version_id,
current_version_id, content_kind, content_status, mock_mode, observed_at,
version_created_at, primary_asset_id, existing_publication_count, channel_copy,
latest_approval, fact_check, asset, source. Nested contracts are defined below.
The trusted caller must obtain these fields from one consistent owner-system
readback. Unknown fields (including destination or schedule overrides) reject.

The existing human-approved publication gate has no fixed source-age expiry;
the 24-hour ingestion/courier rule is deliberately NOT added here. Evidence
timestamps must be aware, nonfuture and no later than the snapshot. This does
not assert current freshness or waive a human's final source/timing review.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from types import MappingProxyType
from uuid import UUID


# Public destination policy, copied from the existing DB observation allowlist.
# Typefully account labels/social-set IDs are not verified public X handles.
CLIENT_TARGETS = MappingProxyType({
    "yellow": ("yellowkorea_ann", "yellow__korea", "Yellow"),
    "origintrail": ("origintrailkr", "origin_trail_kr", "origin_trail"),
    "squid": ("squid_kor_update", "squidkorea", "SquidRouter"),
    "babylon": ("babylonbtc", "babylonkorean", "babylonlabs_io"),
})
_ROOT = {
    "workspace_id", "client_id", "client_active", "content_item_id",
    "content_version_id", "current_version_id", "content_kind", "content_status",
    "mock_mode", "observed_at", "version_created_at", "primary_asset_id",
    "existing_publication_count", "channel_copy", "latest_approval", "fact_check",
    "asset", "source",
}
_BINDING = {"workspace_id", "content_item_id", "content_version_id"}
_APPROVAL = _BINDING | {
    "approval_id", "decision", "reviewer_source", "review_sequence", "reviewed_at",
    "fact_check_policy_version", "source_facts_verified", "output_claims_verified",
}
_ASSET = _BINDING | {
    "client_id", "asset_id", "asset_kind", "mime_type", "storage_bucket",
    "storage_path", "filename", "sha256", "byte_size", "width", "height",
    "asset_count", "stored",
}
_SOURCE = {"workspace_id", "client_id", "source_item_id", "source_type", "canonical_url", "published_at", "position"}
_REPORT = {"schema_version", "policy_version", "content_kind", "human_review_required", "status", "input_sha256", "output_sha256", "checks"}
_CHECK = {"id", "status", "label", "detail", "metrics"}
_HASH = re.compile(r"^[a-f0-9]{64}$")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])")
_UUID = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")
_PRIVATE = re.compile(
    r"(?:t\.me/(?:\+|joinchat/)|api\.telegram\.org/bot|/storage/v1/object/sign/|"
    r"(?<!\d)-100[1-9][0-9]{6,12}(?!\d)|[1-9][0-9]{4,15}:[A-Za-z0-9_-]{30,100}|"
    r"(?:authorization|api[_ -]?key|access[_ -]?token|service[_ -]?role[_ -]?key)\s*[:=])",
    re.IGNORECASE,
)


class PublicationHandoffError(ValueError):
    """A fixed error code only; never includes caller input or stored copy."""


def _reject(code: str) -> None:
    raise PublicationHandoffError(code)


def _record(value: object, fields: set[str], code: str) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != fields:
        _reject(code)
    return value


def _id(value: object) -> str:
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        _reject("handoff_identifier_invalid")
    if UUID(value).int == 0:
        _reject("handoff_identifier_invalid")
    return value


def _hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _reject("handoff_hash_invalid")
    return value


def _time(value: object) -> datetime:
    if isinstance(value, str):
        if type(value) is not str or _TIME.fullmatch(value) is None:
            _reject("handoff_timestamp_invalid")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _reject("handoff_timestamp_invalid")
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _reject("handoff_timestamp_invalid")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _reject("handoff_timestamp_invalid")


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _text(value: object, maximum: int) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
        or not value.strip() or _PRIVATE.search(value)
        or any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127
               or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        _reject("handoff_text_invalid")
    return value  # No stripping, normalization, shortening, or recomposition.


def _binding(value: Mapping, snapshot: Mapping) -> None:
    for key in _BINDING:
        if _id(value[key]) != snapshot[key]:
            _reject("handoff_exact_version_mismatch")


def _fact_check(value: object) -> dict[str, object]:
    report = _record(value, _REPORT, "handoff_fact_check_invalid")
    if (report["schema_version"] != "1.0"
        or report["policy_version"] != "double-fact-check@1"
        or report["content_kind"] != "daily_news"
        or report["human_review_required"] is not True
        or report["status"] not in ("pass", "review")
        or not isinstance(report["checks"], list) or len(report["checks"]) != 2):
        _reject("handoff_fact_check_invalid")
    statuses = []
    for expected, raw in zip(("source_evidence", "output_claims"), report["checks"]):
        check = _record(raw, _CHECK, "handoff_fact_check_invalid")
        if check["id"] != expected or check["status"] not in ("pass", "review"):
            _reject("handoff_fact_check_invalid")
        _text(check["label"], 240)
        _text(check["detail"], 2000)
        metrics = check["metrics"]
        if not isinstance(metrics, Mapping) or len(metrics) > 32:
            _reject("handoff_fact_check_invalid")
        for key, metric in metrics.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                _reject("handoff_fact_check_invalid")
            if isinstance(metric, str):
                if metric:
                    _text(metric, 2000)
            elif metric is not None and type(metric) not in {bool, int, float}:
                _reject("handoff_fact_check_invalid")
            elif type(metric) in {int, float} and (abs(metric) > 1e15 or not math.isfinite(metric)):
                _reject("handoff_fact_check_invalid")
        statuses.append(check["status"])
    if report["status"] != ("review" if "review" in statuses else "pass"):
        _reject("handoff_fact_check_invalid")
    return {
        "policy_version": "double-fact-check@1", "status": report["status"],
        "input_sha256": _hash(report["input_sha256"]),
        "output_sha256": _hash(report["output_sha256"]),
        "checks": dict(zip(("source_evidence", "output_claims"), statuses)),
    }


def build_publication_handoff(snapshot: Mapping, *, now: datetime) -> dict[str, object]:
    """Build a non-authorizing preparation packet from a trusted exact snapshot."""
    if not isinstance(now, datetime):
        _reject("handoff_timestamp_invalid")
    decision_now = _time(now)
    data = _record(snapshot, _ROOT, "handoff_snapshot_invalid")
    client = data["client_id"]
    if not isinstance(client, str) or client not in CLIENT_TARGETS:
        _reject("handoff_client_invalid")
    for key in _BINDING | {"current_version_id", "primary_asset_id"}:
        _id(data[key])
    if data["content_version_id"] != data["current_version_id"]:
        _reject("handoff_exact_version_mismatch")
    if (data["client_active"] is not True or data["content_kind"] != "daily_news"
        or data["content_status"] != "approved" or data["mock_mode"] is not False):
        _reject("handoff_approved_nonmock_version_required")
    if not _integer(data["existing_publication_count"], 0, 0):
        _reject("handoff_existing_publication_requires_readback")
    observed, created = _time(data["observed_at"]), _time(data["version_created_at"])
    if not created <= observed <= decision_now:
        _reject("handoff_evidence_time_conflict")
    approval = _record(data["latest_approval"], _APPROVAL, "handoff_approval_invalid")
    _binding(approval, data)
    approval_id = _id(approval["approval_id"])
    reviewed = _time(approval["reviewed_at"])
    if (approval["decision"] != "approved" or approval["reviewer_source"] != "studio_session"
        or approval["fact_check_policy_version"] != "double-fact-check@1"
        or approval["source_facts_verified"] is not True or approval["output_claims_verified"] is not True
        or not _integer(approval["review_sequence"], 1, 2**53 - 1)
        or not created <= reviewed <= observed):
        _reject("handoff_human_attestation_required")
    report = _fact_check(data["fact_check"])
    copies = _record(data["channel_copy"], {"telegram", "x"}, "handoff_channel_copy_invalid")
    telegram, x_copy = _text(copies["telegram"], 16000), _text(copies["x"], 16000)

    asset = _record(data["asset"], _ASSET, "handoff_asset_invalid")
    _binding(asset, data)
    asset_id = _id(asset["asset_id"])
    banner_hash = _hash(asset["sha256"])
    if (asset["client_id"] != client or asset_id != data["primary_asset_id"]
        or asset["asset_kind"] != "png" or asset["mime_type"] != "image/png"
        or asset["storage_bucket"] != "content-studio" or asset["filename"] != "news-card.png"
        or asset["storage_path"] != f"{data['workspace_id']}/{client}/{asset_id}/news-card.png"
        or not _integer(asset["asset_count"], 1, 1) or asset["stored"] is not True
        or not _integer(asset["byte_size"], 8, 10 * 1024 * 1024)
        or not _integer(asset["width"], 1, 10000) or not _integer(asset["height"], 1, 10000)):
        _reject("handoff_canonical_png_required")
    source = _record(data["source"], _SOURCE, "handoff_source_invalid")
    source_id = _id(source["source_item_id"])
    published = _time(source["published_at"])
    telegram_target, x_target, official_handle = CLIENT_TARGETS[client]
    if (source["workspace_id"] != data["workspace_id"] or source["client_id"] != client
        or source["source_type"] != "tweet" or not _integer(source["position"], 0, 0)
        or not isinstance(source["canonical_url"], str)
        or re.fullmatch(rf"https://x\.com/{official_handle}/status/[1-9][0-9]{{0,18}}", source["canonical_url"], re.IGNORECASE) is None
        or published > created):
        _reject("handoff_source_invalid")
    packet = {
        "schema_version": "approved-publication-preparation@1",
        "purpose": "advisory_preparation_only", "client_id": client,
        **{key: data[key] for key in sorted(_BINDING)},
        "observed_at": observed.isoformat(), "version_created_at": created.isoformat(),
        "approval": {
            "approval_id": approval_id, "reviewed_at": reviewed.isoformat(),
            "review_sequence": approval["review_sequence"], "reviewer_source": "studio_session",
            "fact_check_policy_version": "double-fact-check@1",
            "source_facts_verified": True, "output_claims_verified": True,
        },
        "fact_check": report,
        "channel_copy": {"telegram": telegram, "x": x_copy},
        "asset": {key: asset[key] for key in ("asset_id", "sha256", "mime_type", "byte_size", "width", "height")},
        "source": {"source_item_id": source_id, "canonical_url": source["canonical_url"], "published_at": published.isoformat()},
        "targets": {"telegram": telegram_target, "x": x_target},
        "capabilities": {
            "telegram": {
                "adapter": "exact_version_implemented" if client == "squid" else "adapter_missing",
                "payload": "within_caption_limit" if len(telegram) <= 1024 else "caption_limit_exceeded",
                "runtime": "unverified", "execution_authorized": False,
            },
            "typefully": {
                "adapter": "exact_version_adapter_missing", "publish_at": None,
                "account_mapping": "live_verification_required", "execution_authorized": False,
            },
        },
        "safety": {
            "structural_input_only": True, "trusted_owner_snapshot_required": True,
            "live_readback_verified": False, "asset_bytes_verified": False,
            "final_human_publication_approval_required": True,
            "automatic_approval": False, "automatic_publication": False,
            "queue_created": False, "execution_authorized": False,
            "source_freshness_policy": "human_approved_publication_no_fixed_age_limit",
        },
    }
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"packet": packet, "packet_sha256": hashlib.sha256(encoded).hexdigest()}
