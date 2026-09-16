"""Pure, fail-closed daily content coverage projection (no I/O or authority).

``build_daily_coverage(observations, now=aware_datetime)`` consumes at most four
sanitized mappings, one per configured client. It does not query or generate.
Names are a frozen copy of the checked-in configs, guarded by a parity test.

Common required evidence: client_id, observed_at (aware ISO timestamp or
datetime), kst_date (YYYY-MM-DD), generation_status. Source absence additionally
requires source_count=0, source_scan_complete=True, draft_reserved=False,
feed_active=True and a feed poll within 15 minutes. generation_status is
none/queued/running/retrying/failed/
succeeded. A reservation alone is never completion.

Ready evidence additionally requires: source_count>=1, source_item_id,
source_published_at (<24 hours old), source_is_latest=True, feed_active=True,
feed_last_polled_at (<=15 minutes old), generate_job_id, content_kind=daily_news,
content_status=needs_review, content_item_id, content_version_id,
current_version=True, mock_mode=False, png_count=1, banner_sha256 and zero
approval_count/publication_count. IDs are UUIDs, hashes are SHA-256, counts are
non-boolean integers. Snapshots must be <=30 minutes old and in the current KST
day. Every provided timestamp must be aware, nonfuture, and no later than the
snapshot. Internal receipt states are none/pending/claimed/sending/sent/rejected/
delivery_unknown/obsolete; missing receipt evidence prevents ready status.

Optional IDs/counts are returned only after validation; unknown keys or invalid
client evidence yield evidence_missing, without echoing untrusted values.
Malformed roots, oversized inputs and unknown clients raise a bounded ValueError.
Duplicate client rows are quarantined, never selected arbitrarily. All four
clients are always represented in canonical automation order for valid roots.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from itertools import islice
from uuid import UUID
from zoneinfo import ZoneInfo


CLIENT_ROSTER = (
    ("yellow", "Yellow Network"),
    ("origintrail", "OriginTrail Korea"),
    ("squid", "Squid"),
    ("babylon", "Babylon Korea"),
)
_CLIENTS = frozenset(client for client, _ in CLIENT_ROSTER)
_KST = ZoneInfo("Asia/Seoul")
_FRESH_SNAPSHOT = timedelta(minutes=30)
_FRESH_FEED = timedelta(minutes=15)
_FRESH_SOURCE = timedelta(hours=24)
_GENERATION = frozenset({"none", "queued", "running", "retrying", "failed", "succeeded"})
_INTERNAL = frozenset({
    "none", "pending", "claimed", "sending", "sent", "rejected",
    "delivery_unknown", "obsolete",
})
_IDS = ("source_item_id", "generate_job_id", "content_item_id", "content_version_id")
_COUNTS = ("source_count", "png_count", "approval_count", "publication_count")
_TIMES = ("observed_at", "feed_last_polled_at", "source_published_at")
_BOOLS = (
    "draft_reserved", "feed_active", "source_is_latest", "source_scan_complete",
    "current_version", "mock_mode",
)
_FIELDS = frozenset({
    "client_id", "kst_date", "generation_status", "content_kind",
    "content_status", "banner_sha256", "internal_review_status",
    *_IDS, *_COUNTS, *_TIMES, *_BOOLS,
})
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, str):
        if not 20 <= len(value) <= 40:
            return None
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _uuid(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    if str(parsed) != value.lower() or parsed.version not in {1, 2, 3, 4, 5}:
        return None
    return str(parsed)


def _count(value: object) -> bool:
    return type(value) is int and 0 <= value <= 1_000_000


def _missing(row: dict[str, object], reason: str) -> dict[str, object]:
    row.update(status="evidence_missing", reason_codes=[reason], review_ready=False)
    return row


def _project(client: str, name: str, values: list[Mapping], now: datetime) -> dict[str, object]:
    row: dict[str, object] = {
        "client_id": client,
        "name": name,
        "status": "evidence_missing",
        "reason_codes": [],
        "qa_mode": "manual_batch_review" if client == "origintrail" else "codex_advisory",
        "review_ready": False,
        "generation_status": None,
        "internal_review_status": None,
        "ids": {},
        "counts": {},
        "banner_sha256": None,
    }
    if not values:
        return _missing(row, "observation_missing")
    if len(values) != 1:
        return _missing(row, "duplicate_client_observations")
    value = values[0]
    if any(not isinstance(key, str) or key not in _FIELDS for key in value):
        return _missing(row, "unexpected_observation_fields")
    # Do not include invalid input, even in errors or ostensibly safe ID fields.
    for key in _IDS:
        if value.get(key) is not None:
            normalized = _uuid(value[key])
            if normalized is None:
                return _missing(row, "invalid_identifier")
            row["ids"][key] = normalized
    for key in _COUNTS:
        if value.get(key) is not None:
            if not _count(value[key]):
                return _missing(row, "invalid_count")
            row["counts"][key] = value[key]
    for key in _BOOLS:
        if value.get(key) is not None and type(value[key]) is not bool:
            return _missing(row, "invalid_boolean")
    digest = value.get("banner_sha256")
    if digest is not None:
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            return _missing(row, "invalid_banner_hash")
        row["banner_sha256"] = digest
    observed = _timestamp(value.get("observed_at"))
    if observed is None or observed > now:
        return _missing(row, "invalid_observation_timestamp")
    if now - observed > _FRESH_SNAPSHOT:
        return _missing(row, "stale_observation")
    if (
        value.get("kst_date") != now.astimezone(_KST).date().isoformat()
        or observed.astimezone(_KST).date() != now.astimezone(_KST).date()
    ):
        return _missing(row, "kst_day_mismatch")
    times: dict[str, datetime] = {}
    for key in _TIMES[1:]:
        if value.get(key) is not None:
            stamp = _timestamp(value[key])
            if stamp is None or stamp > observed:
                return _missing(row, "invalid_evidence_timestamp")
            times[key] = stamp
    generation = value.get("generation_status")
    internal = value.get("internal_review_status")
    if not isinstance(generation, str) or generation not in _GENERATION:
        return _missing(row, "generation_state_missing")
    if internal is not None and (not isinstance(internal, str) or internal not in _INTERNAL):
        return _missing(row, "invalid_internal_review_state")
    row["generation_status"] = generation
    row["internal_review_status"] = internal
    if internal not in {None, "none"} and generation != "succeeded":
        return _missing(row, "internal_review_generation_conflict")
    if generation in {"queued", "running", "retrying", "failed"}:
        if "generate_job_id" not in row["ids"]:
            return _missing(row, "generation_job_identity_missing")
        row["status"] = "generation_queued" if generation == "retrying" else f"generation_{generation}"
        return row
    feed = times.get("feed_last_polled_at")
    feed_fresh = value.get("feed_active") is True and feed is not None and now - feed <= _FRESH_FEED
    if generation == "none":
        if value.get("draft_reserved") is True:
            return _missing(row, "reservation_without_generation_evidence")
        if (
            value.get("source_count") == 0
            and value.get("source_scan_complete") is True
            and value.get("draft_reserved") is False
            and feed_fresh
            and internal == "none"
            and not row["ids"]
            and value.get("source_published_at") is None
            and row["banner_sha256"] is None
            and all(value.get(key) in {None, 0} for key in _COUNTS[1:])
            and value.get("content_kind") is None
            and value.get("content_status") is None
            and value.get("current_version") is not True
        ):
            row["status"] = "no_source"
            return row
        return _missing(row, "source_absence_not_proven")
    if internal not in {None, "none"}:
        # A delivery receipt is historical evidence, not current readiness.
        # Retain an obsolete/unknown receipt even if its source has since aged;
        # it must never become a resend candidate through freshness changes.
        if not all(key in row["ids"] for key in _IDS) or row["banner_sha256"] is None:
            return _missing(row, "internal_review_identity_missing")
        row["status"] = {
            "pending": "internal_review_pending",
            "claimed": "internal_review_pending",
            "sending": "internal_review_pending",
            "sent": "internal_review_sent",
            "rejected": "internal_review_rejected",
            "delivery_unknown": "internal_review_unknown",
            "obsolete": "internal_review_obsolete",
        }[internal]
        return row
    if not feed_fresh:
        return _missing(row, "feed_not_fresh")
    source = times.get("source_published_at")
    if source is None or now - source >= _FRESH_SOURCE:
        return _missing(row, "source_not_fresh")
    if value.get("source_is_latest") is not True:
        return _missing(row, "latest_source_not_proven")
    if not all(key in row["ids"] for key in _IDS):
        return _missing(row, "exact_version_identity_missing")
    if not _count(value.get("source_count")) or value["source_count"] < 1:
        return _missing(row, "source_count_missing")
    if value.get("content_kind") != "daily_news":
        return _missing(row, "content_kind_not_supported")
    if value.get("content_status") != "needs_review" or value.get("current_version") is not True:
        return _missing(row, "current_review_version_not_proven")
    if value.get("mock_mode") is not False:
        return _missing(row, "nonmock_evidence_missing")
    if value.get("png_count") != 1 or row["banner_sha256"] is None:
        return _missing(row, "canonical_png_evidence_missing")
    if value.get("approval_count") != 0 or value.get("publication_count") != 0:
        return _missing(row, "approval_publication_boundary_not_clear")
    if internal is None:
        return _missing(row, "internal_review_receipt_missing")
    row["status"] = "needs_review"
    row["review_ready"] = True
    return row


def build_daily_coverage(observations: Iterable[Mapping], *, now: datetime) -> dict[str, object]:
    """Return four deterministic, sanitized coverage rows; never perform actions."""
    current = _timestamp(now)
    if current is None or not isinstance(now, datetime):
        raise ValueError("coverage_now_must_be_timezone_aware")
    if isinstance(observations, (str, bytes, Mapping)) or not isinstance(observations, Iterable):
        raise ValueError("invalid_coverage_observations")
    values = list(islice(observations, 5))
    if len(values) > 4:
        raise ValueError("coverage_observation_limit_exceeded")
    grouped: dict[str, list[Mapping]] = {client: [] for client in _CLIENTS}
    for value in values:
        if not isinstance(value, Mapping) or len(value) > 64:
            raise ValueError("invalid_coverage_observation")
        client = value.get("client_id")
        if not isinstance(client, str) or client not in _CLIENTS:
            raise ValueError("unknown_coverage_client")
        grouped[client].append(value)
    rows = [_project(client, name, grouped[client], current) for client, name in CLIENT_ROSTER]
    identities: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        for key, identifier in row["ids"].items():
            identities.setdefault((key, identifier), []).append(row)
    for owners in identities.values():
        if len(owners) > 1:
            for row in owners:
                _missing(row, "duplicate_candidate_identity")
    return {
        "schema_version": "daily-content-coverage@1",
        "kst_date": current.astimezone(_KST).date().isoformat(),
        "generated_at": current.isoformat(),
        "automatic_approval": False,
        "automatic_publication": False,
        "client_count": len(rows),
        "ready_count": sum(row["review_ready"] for row in rows),
        "clients": rows,
    }
