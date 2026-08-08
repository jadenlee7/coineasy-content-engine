"""Fail-closed parser for reviewed OriginTrail media evidence.

The database owns the append-only evidence registry.  The automation worker
still validates the narrow RPC response before binding it into an immutable
Batch request, so malformed or widened evidence can never silently reach the
provider.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from urllib.parse import urlsplit

from core.sources.x_media_url import normalize_x_media_url


ORIGINTRAIL_MEDIA_EVIDENCE_SCHEMA_VERSION = "1.0"
ORIGINTRAIL_MEDIA_EVIDENCE_POLICY_VERSION = (
    "origintrail-media-fact-evidence@1"
)

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MEDIA_KEY_RE = re.compile(r"^[0-9]+_[0-9]+$")
_EVIDENCE_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_SOURCE_URL_RE = re.compile(
    r"^https://x\.com/origin_trail/status/[0-9]{1,19}$"
)
_PAYLOAD_KEYS = frozenset({
    "schema_version",
    "policy_version",
    "review_status",
    "human_review_required",
    "verified_at",
    "source_url",
    "source_content_sha256",
    "media",
    "review_notes_ko",
    "official_references",
})
_MEDIA_KEYS = frozenset({
    "type",
    "media_key",
    "recorded_url",
    "preview_url",
    "preview_url_sha256",
    "width",
    "height",
    "factual_evidence",
})
_REFERENCE_KEYS = frozenset({
    "kind",
    "label_ko",
    "url",
    "observed_at",
    "snapshot_sha256",
    "availability",
    "finding_ko",
})
_REFERENCE_KINDS = frozenset({
    "origintrail_implementation",
    "prime_intellect_announcement",
    "prime_agent_release",
    "arc_community_leaderboard",
    "arc_methodology",
    "scorecard_source",
})


@dataclass(frozen=True)
class OriginTrailFactEvidence:
    """One immutable, human-qualified evidence envelope."""

    canonical_json: str
    evidence_sha256: str
    source_url: str
    source_content_sha256: str
    recorded_media_url: str
    preview_media_url: str

    def batch_envelope(self) -> dict[str, object]:
        """Return a fresh JSON-compatible copy for immutable Batch input."""
        return {
            "payload": json.loads(self.canonical_json),
            "evidence_sha256": self.evidence_sha256,
        }


def _exact_keys(value: Mapping[str, object], expected: frozenset[str]) -> bool:
    return frozenset(value) == expected


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or _EVIDENCE_DATE_RE.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _bounded_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 1 <= len(value) <= maximum
    )


def _safe_reference_url(kind: str, value: object) -> bool:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value) > 2_048
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    ):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
        or parsed.netloc != (parsed.hostname or "")
        or parsed.geturl() != value
    ):
        return False
    host = (parsed.hostname or "").lower()
    if kind == "origintrail_implementation":
        return (
            host == "github.com"
            and re.fullmatch(
                r"/OriginTrail/dkg/blob/[a-f0-9]{40}/"
                r"packages/adapter-prime-agent/README\.md",
                parsed.path,
            ) is not None
            and not parsed.query
        )
    if kind == "prime_intellect_announcement":
        return (
            host == "www.primeintellect.ai"
            and parsed.path == "/blog/prime-agent"
            and not parsed.query
        )
    if kind == "prime_agent_release":
        return (
            host == "github.com"
            and re.fullmatch(
                r"/PrimeIntellect-ai/prime-agent/(?:releases/tag/v[0-9.]+|"
                r"commit/[a-f0-9]{40})",
                parsed.path,
            ) is not None
            and not parsed.query
        )
    if kind == "arc_community_leaderboard":
        return (
            host == "arcprize.org"
            and parsed.path == "/api/leaderboards"
            and not parsed.query
        )
    if kind == "arc_methodology":
        return (
            host == "arcprize.org"
            and parsed.path == "/media/ARC_AGI_3_Technical_Report.pdf"
            and not parsed.query
        )
    if kind == "scorecard_source":
        return (
            host == "github.com"
            and re.fullmatch(
                r"/PrimeIntellect-ai/arc-agi-3-prime-agent-scorecard/"
                r"commit/[a-f0-9]{40}",
                parsed.path,
            ) is not None
            and not parsed.query
        )
    return False


def _safe_recorded_media_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value) > 2_048
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    ):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc == "pbs.twimg.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.query
        and not parsed.fragment
        and parsed.geturl() == value
        and bool(normalize_x_media_url(value))
    )


def parse_origintrail_fact_evidence(value: object) -> OriginTrailFactEvidence:
    """Validate and canonicalize the lease-fenced registry RPC response."""
    if not isinstance(value, Mapping) or frozenset(value) != {
        "payload",
        "evidence_sha256",
    }:
        raise ValueError("invalid OriginTrail fact evidence envelope")
    payload = value.get("payload")
    evidence_sha256 = value.get("evidence_sha256")
    if (
        not isinstance(payload, Mapping)
        or not _exact_keys(payload, _PAYLOAD_KEYS)
        or not isinstance(evidence_sha256, str)
        or _SHA256_RE.fullmatch(evidence_sha256) is None
        or payload.get("schema_version")
            != ORIGINTRAIL_MEDIA_EVIDENCE_SCHEMA_VERSION
        or payload.get("policy_version")
            != ORIGINTRAIL_MEDIA_EVIDENCE_POLICY_VERSION
        or payload.get("review_status") != "qualified"
        or payload.get("human_review_required") is not True
        or not _valid_timestamp(payload.get("verified_at"))
    ):
        raise ValueError("invalid OriginTrail fact evidence payload")

    source_url = payload.get("source_url")
    source_content_sha256 = payload.get("source_content_sha256")
    if (
        not isinstance(source_url, str)
        or _SOURCE_URL_RE.fullmatch(source_url) is None
        or not isinstance(source_content_sha256, str)
        or _SHA256_RE.fullmatch(source_content_sha256) is None
    ):
        raise ValueError("invalid OriginTrail fact evidence source")

    media = payload.get("media")
    if not isinstance(media, Mapping) or not _exact_keys(media, _MEDIA_KEYS):
        raise ValueError("invalid OriginTrail fact evidence media")
    recorded_url = media.get("recorded_url")
    preview_url = media.get("preview_url")
    preview_url_sha256 = media.get("preview_url_sha256")
    width = media.get("width")
    height = media.get("height")
    if (
        media.get("type") not in {"photo", "video", "animated_gif"}
        or not isinstance(media.get("media_key"), str)
        or len(media["media_key"]) > 128
        or _MEDIA_KEY_RE.fullmatch(media["media_key"]) is None
        or not _safe_recorded_media_url(recorded_url)
        or not isinstance(preview_url, str)
        or normalize_x_media_url(recorded_url) != preview_url
        or not isinstance(preview_url_sha256, str)
        or _SHA256_RE.fullmatch(preview_url_sha256) is None
        or hashlib.sha256(preview_url.encode("utf-8")).hexdigest()
            != preview_url_sha256
        or not isinstance(width, int)
        or isinstance(width, bool)
        or not 1 <= width <= 8_192
        or not isinstance(height, int)
        or isinstance(height, bool)
        or not 1 <= height <= 8_192
        or media.get("factual_evidence") is not False
    ):
        raise ValueError("invalid OriginTrail fact evidence media")

    notes = payload.get("review_notes_ko")
    if (
        not isinstance(notes, list)
        or not 1 <= len(notes) <= 8
        or any(not _bounded_text(note, 600) for note in notes)
    ):
        raise ValueError("invalid OriginTrail fact evidence notes")

    references = payload.get("official_references")
    if not isinstance(references, list) or not 1 <= len(references) <= 8:
        raise ValueError("invalid OriginTrail fact evidence references")
    seen_kinds: set[str] = set()
    for reference in references:
        if not isinstance(reference, Mapping) or not _exact_keys(
            reference,
            _REFERENCE_KEYS,
        ):
            raise ValueError("invalid OriginTrail fact evidence reference")
        kind = reference.get("kind")
        snapshot_sha256 = reference.get("snapshot_sha256")
        if (
            not isinstance(kind, str)
            or kind not in _REFERENCE_KINDS
            or kind in seen_kinds
            or not _bounded_text(reference.get("label_ko"), 160)
            or not _safe_reference_url(kind, reference.get("url"))
            or not _valid_timestamp(reference.get("observed_at"))
            or (
                snapshot_sha256 is not None
                and (
                    not isinstance(snapshot_sha256, str)
                    or _SHA256_RE.fullmatch(snapshot_sha256) is None
                )
            )
            or reference.get("availability") not in {"available", "unavailable"}
            or not _bounded_text(reference.get("finding_ko"), 600)
        ):
            raise ValueError("invalid OriginTrail fact evidence reference")
        seen_kinds.add(kind)
    if seen_kinds != _REFERENCE_KINDS:
        raise ValueError("OriginTrail fact evidence references are incomplete")

    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if hashlib.sha256(canonical_json.encode("utf-8")).hexdigest() != evidence_sha256:
        raise ValueError("OriginTrail fact evidence hash mismatch")
    return OriginTrailFactEvidence(
        canonical_json=canonical_json,
        evidence_sha256=evidence_sha256,
        source_url=source_url,
        source_content_sha256=source_content_sha256,
        recorded_media_url=recorded_url,
        preview_media_url=preview_url,
    )
