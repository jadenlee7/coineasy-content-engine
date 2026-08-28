from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


TUTORIAL_CLIENTS = frozenset({"yellow", "squid"})
_TUTORIAL_SIGNALS = (
    "how to",
    "guide",
    "tutorial",
    "step-by-step",
    "getting started",
    "documentation",
    "docs.",
    "learn how",
)
_ANNOUNCEMENT_SIGNALS = (
    "launch",
    "live",
    "release",
    "update",
    "introduc",
    "announc",
    "integrat",
    "partner",
    "mainnet",
    "testnet",
    "upgrade",
    "support",
    "available",
    "proposal",
    "milestone",
)
_LOW_SIGNAL_PATTERN = re.compile(
    r"^(gm|gn|hello|happy\s+\w+day|long week\??|weekend\??|chillin['’]?)[.!?\s]*$",
    re.IGNORECASE,
)
_DEMAND_SIGNAL_BONUS_CAP = 3.0
_TUTORIAL_LEARNING_BONUS_CAP = 2.0
_FRESH_OFFICIAL_SOURCE_CLIENTS = frozenset({
    "yellow",
    "origintrail",
    "squid",
    "babylon",
})
_OFFICIAL_SOURCE_FRESHNESS_WINDOW = timedelta(hours=24)
_NEXT_KST_SLOT_EXPIRY_RESCUE_CLIENTS = frozenset({"yellow", "babylon"})
_OFFICIAL_SOURCE_CRON_INTERVAL = timedelta(minutes=15)
_KST = ZoneInfo("Asia/Seoul")
_X_LINK_METADATA_MARKER = "[X-provided link metadata]"
_ASCII_TERM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9 _-]*$")
_TEMPORAL_DEMAND_PATTERN = re.compile(
    r"^(?:20\d{2}|\d{1,2}월|(?:첫째|둘째|셋째|넷째|다섯째)주|"
    r"(?:이번|지난|다음)(?:주|주간|달|월|분기|연도))$",
    re.IGNORECASE,
)
_ASCII_WITH_KOREAN_PARTICLE_PATTERN = re.compile(
    r"^([a-z0-9][a-z0-9 _-]{1,70})(은|는|이|가|을|를|의|에|와|과|도|로)$",
    re.IGNORECASE,
)
_GENERIC_DEMAND_TERMS = frozenset({
    "channel",
    "official",
    "project",
    "update",
    "공식",
    "기다려온",
    "소식",
    "업데이트",
    "채널",
    "프로젝트",
})


@dataclass(frozen=True)
class ModeDecision:
    content_kind: str
    automatic: bool
    recommendation: str | None = None
    reason: str = ""


def _published_timestamp(post: Mapping[str, object]) -> float:
    raw = post.get("created_at")
    if not isinstance(raw, str):
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _engagement_score(post: Mapping[str, object]) -> float:
    metrics = post.get("metrics")
    if not isinstance(metrics, Mapping):
        return 0.0
    total = 0
    for name in ("like_count", "retweet_count", "reply_count", "quote_count"):
        value = metrics.get(name)
        if isinstance(value, int) and value > 0:
            total += value
    return math.log1p(total)


def _without_x_link_metadata(post: Mapping[str, object]) -> Mapping[str, object]:
    """Return a ranking-only view without provider-enriched link copy."""
    text = str(post.get("text") or "")
    marker_index = text.casefold().find(_X_LINK_METADATA_MARKER.casefold())
    if marker_index < 0:
        return post
    ranking_post = dict(post)
    ranking_post["text"] = text[:marker_index].strip()
    return ranking_post


def announcement_score(post: Mapping[str, object]) -> float:
    text = str(post.get("text") or "").strip()
    lowered = text.lower()
    if not text or _LOW_SIGNAL_PATTERN.fullmatch(text):
        return float("-inf")
    score = min(len(text), 600) / 120
    score += sum(2.0 for signal in _ANNOUNCEMENT_SIGNALS if signal in lowered)
    score += sum(0.8 for signal in _TUTORIAL_SIGNALS if signal in lowered)
    if post.get("is_note_tweet") is True:
        score += 2.5
    score += min(_engagement_score(post), 4.0) * 0.25
    return score


def _demand_signal_score(
    post: Mapping[str, object],
    demand_terms: Sequence[tuple[str, float]],
) -> float:
    text = str(post.get("text") or "").casefold()
    total = 0.0
    for raw_term, raw_weight in demand_terms:
        term = _normalize_demand_term(raw_term)
        if not term or raw_weight <= 0:
            continue
        if _ASCII_TERM_PATTERN.fullmatch(term):
            matched = re.search(
                rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                text,
            ) is not None
        else:
            matched = term in text
        if matched:
            total += min(float(raw_weight), 1.0) * _DEMAND_SIGNAL_BONUS_CAP
    return min(total, _DEMAND_SIGNAL_BONUS_CAP)


def _normalize_demand_term(value: str) -> str:
    """Remove date, cadence, and tokenization noise from ranking-only hints."""
    term = " ".join(value.casefold().split())
    if not term or _TEMPORAL_DEMAND_PATTERN.fullmatch(term):
        return ""
    particle_match = _ASCII_WITH_KOREAN_PARTICLE_PATTERN.fullmatch(term)
    if particle_match:
        term = particle_match.group(1).strip()
    if term in _GENERIC_DEMAND_TERMS:
        return ""
    return term


def _normalize_demand_terms(
    demand_terms: Iterable[tuple[str, float]],
) -> tuple[tuple[str, float], ...]:
    normalized: dict[str, float] = {}
    for term, weight in demand_terms:
        if (
            not isinstance(term, str)
            or not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(float(weight))
            or float(weight) <= 0
        ):
            continue
        clean_term = _normalize_demand_term(term)
        if not clean_term:
            continue
        normalized[clean_term] = max(
            normalized.get(clean_term, 0.0),
            float(weight),
        )
        if len(normalized) >= 20:
            break
    return tuple(normalized.items())


def _tutorial_learning_score(
    post: Mapping[str, object],
    tutorial_priority: float,
) -> float:
    text = str(post.get("text") or "").casefold()
    if not any(signal in text for signal in _TUTORIAL_SIGNALS):
        return 0.0
    return min(tutorial_priority, 1.0) * _TUTORIAL_LEARNING_BONUS_CAP


def select_official_candidate(
    posts: Iterable[Mapping[str, object]],
    *,
    client_id: str | None = None,
    now: datetime | None = None,
    skip_patterns: Iterable[str] = (),
    demand_terms: Iterable[tuple[str, float]] = (),
    tutorial_priority: float = 0.0,
) -> Mapping[str, object] | None:
    """Select one eligible official post using client-specific routing policy."""
    normalized_skips = tuple(
        pattern.strip().lower()
        for pattern in skip_patterns
        if isinstance(pattern, str) and pattern.strip()
    )
    normalized_demand_terms = _normalize_demand_terms(demand_terms)
    normalized_tutorial_priority = (
        float(tutorial_priority)
        if isinstance(tutorial_priority, (int, float))
        and not isinstance(tutorial_priority, bool)
        and math.isfinite(float(tutorial_priority))
        and 0 <= float(tutorial_priority) <= 1
        else 0.0
    )
    freshness_required = client_id in _FRESH_OFFICIAL_SOURCE_CLIENTS
    candidates: list[
        tuple[Mapping[str, object], Mapping[str, object]]
    ] = []
    for post in posts:
        if (
            post.get("is_retweet") is True
            or post.get("is_reply") is True
            or not isinstance(post.get("id"), str)
            or not str(post.get("id")).isdigit()
        ):
            continue
        # Provider-enriched link cards are immutable source evidence, but
        # their title/description must not promote an otherwise low-signal X
        # post.  Actual X Article copy appears before this marker and remains
        # available to the ranking policy.
        ranking_post = _without_x_link_metadata(post)
        ranking_text = str(ranking_post.get("text") or "").lower()
        if (
            any(pattern in ranking_text for pattern in normalized_skips)
            or announcement_score(ranking_post) <= 0.25
        ):
            continue
        candidates.append((post, ranking_post))
    if not candidates:
        return None

    def relevance_key(
        candidate: tuple[Mapping[str, object], Mapping[str, object]],
    ) -> tuple[float, float, int]:
        post, ranking_post = candidate
        return (
            announcement_score(ranking_post)
            + _demand_signal_score(ranking_post, normalized_demand_terms)
            + _tutorial_learning_score(
                ranking_post,
                normalized_tutorial_priority,
            ),
            _published_timestamp(post),
            int(str(post["id"])),
        )

    if not freshness_required:
        return max(candidates, key=relevance_key)[0]

    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    freshness_cutoff = (
        reference_now.astimezone(timezone.utc)
        - _OFFICIAL_SOURCE_FRESHNESS_WINDOW
    ).timestamp()
    fresh_candidates = [
        candidate
        for candidate in candidates
        if _published_timestamp(candidate[0]) >= freshness_cutoff
    ]
    if fresh_candidates:
        if client_id in _NEXT_KST_SLOT_EXPIRY_RESCUE_CLIENTS:
            local_now = reference_now.astimezone(_KST)
            next_kst_slot = (local_now + timedelta(days=1)).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            next_slot_freshness_cutoff = (
                next_kst_slot.astimezone(timezone.utc)
                + _OFFICIAL_SOURCE_CRON_INTERVAL
                - _OFFICIAL_SOURCE_FRESHNESS_WINDOW
            ).timestamp()
            expiring_candidates = [
                candidate
                for candidate in fresh_candidates
                if _published_timestamp(candidate[0])
                <= next_slot_freshness_cutoff
            ]
            if (
                expiring_candidates
                and len(expiring_candidates) == len(fresh_candidates)
            ):
                # Yellow and Babylon receive one draft per KST day.  When
                # every eligible post reaches the 24-hour boundary by
                # tomorrow's first full cron interval, use latest-first so a
                # relevance-heavy backlog item cannot hide the newest official
                # post.  Mixed buckets
                # retain the existing relevance policy because their safe
                # candidates can still be considered in the next KST slot.
                # Eligibility, skip, reply, and retweet guards have already run.
                return max(
                    expiring_candidates,
                    key=lambda candidate: (
                        _published_timestamp(candidate[0]),
                        relevance_key(candidate)[0],
                        int(str(candidate[0]["id"])),
                    ),
                )[0]
        return max(fresh_candidates, key=relevance_key)[0]
    return None


def choose_content_mode(
    client_id: str,
    post: Mapping[str, object],
    *,
    enable_tutorials: bool = False,
) -> ModeDecision:
    text = str(post.get("text") or "").strip()
    lowered = text.lower()
    is_complete_note = post.get("is_note_tweet") is True and len(text) >= 300
    is_tutorial_source = any(signal in lowered for signal in _TUTORIAL_SIGNALS)

    if is_complete_note and is_tutorial_source and client_id in TUTORIAL_CLIENTS:
        if enable_tutorials:
            return ModeDecision(
                content_kind="tutorial",
                automatic=True,
                reason="complete_official_tutorial_note",
            )
        return ModeDecision(
            content_kind="article",
            automatic=True,
            recommendation="tutorial",
            reason="tutorial_rollout_requires_review",
        )
    if is_complete_note:
        return ModeDecision(
            content_kind="article",
            automatic=True,
            reason="complete_official_note",
        )
    if is_tutorial_source and client_id in TUTORIAL_CLIENTS:
        return ModeDecision(
            content_kind="daily_news",
            automatic=True,
            recommendation="tutorial",
            reason="short_tutorial_source_kept_as_news",
        )
    return ModeDecision(
        content_kind="daily_news",
        automatic=True,
        reason="official_x_announcement",
    )
