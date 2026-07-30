from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Sequence


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
_ASCII_TERM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9 _-]*$")


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
        term = " ".join(raw_term.casefold().split())
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
    skip_patterns: Iterable[str] = (),
    demand_terms: Iterable[tuple[str, float]] = (),
    tutorial_priority: float = 0.0,
) -> Mapping[str, object] | None:
    normalized_skips = tuple(
        pattern.strip().lower()
        for pattern in skip_patterns
        if isinstance(pattern, str) and pattern.strip()
    )
    normalized_demand_terms = tuple(
        (term, float(weight))
        for term, weight in demand_terms
        if isinstance(term, str)
        and term.strip()
        and isinstance(weight, (int, float))
        and not isinstance(weight, bool)
        and math.isfinite(float(weight))
        and float(weight) > 0
    )[:20]
    normalized_tutorial_priority = (
        float(tutorial_priority)
        if isinstance(tutorial_priority, (int, float))
        and not isinstance(tutorial_priority, bool)
        and math.isfinite(float(tutorial_priority))
        and 0 <= float(tutorial_priority) <= 1
        else 0.0
    )
    candidates = [
        post
        for post in posts
        if post.get("is_retweet") is not True
        and post.get("is_reply") is not True
        and isinstance(post.get("id"), str)
        and str(post.get("id")).isdigit()
        and not any(
            pattern in str(post.get("text") or "").lower()
            for pattern in normalized_skips
        )
        and announcement_score(post) > 0.25
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda post: (
            announcement_score(post)
            + _demand_signal_score(post, normalized_demand_terms)
            + _tutorial_learning_score(post, normalized_tutorial_priority),
            _published_timestamp(post),
            int(str(post["id"])),
        ),
    )


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
