from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping
from urllib.parse import urlsplit

import httpx


CONTENT_SIGNALS_SCHEMA_VERSION = "1.0"
CONTENT_SIGNALS_URL = (
    "https://jlxbywqofrltyttklcqy.supabase.co"
    "/functions/v1/content-signals-api"
)
_ALLOWED_CLIENTS = frozenset({"yellow", "origintrail", "squid", "babylon"})
_ALLOWED_SOURCES = frozenset({"community", "telegram_content", "local_x"})
_AVAILABILITY_VALUES = frozenset({"ok", "unavailable"})
_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_DEMAND_TERMS = 20
_MAX_TERM_LENGTH = 80
_MAX_SAFE_COUNT = 9_007_199_254_740_991
_MAX_SIGNAL_AGE = timedelta(hours=24)
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_WALLET_PATTERN = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
_BASE58_WALLET_PATTERN = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_BECH32_LIKE_PATTERN = re.compile(
    r"\b[a-z0-9]{1,20}1[ac-hj-np-z02-9]{20,71}\b",
    re.IGNORECASE,
)
_HIGH_ENTROPY_TOKEN_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9_-]{48,80}|"
    r"(?=[A-Za-z0-9_-]{32,80}$)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]+)$"
)
_PROHIBITED_KEYS = frozenset({
    "btc_address",
    "chat_id",
    "group_id",
    "operator_key",
    "questions",
    "raw_message",
    "raw_messages",
    "service_role_key",
    "summaries",
    "summary",
    "telegram_id",
    "telegram_username",
    "themes",
    "user_id",
    "username",
    "usernames",
    "wallet",
    "wallet_address",
})


class ContentSignalsError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class DemandTerm:
    term: str
    weight: float
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContentSignalsSnapshot:
    schema_version: str
    client_id: str
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    demand_terms: tuple[DemandTerm, ...]


def validate_content_signals_url(value: str) -> str:
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "EASYFARM_CONTENT_SIGNALS_URL is invalid"
        ) from exc
    if (
        normalized != CONTENT_SIGNALS_URL
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "jlxbywqofrltyttklcqy.supabase.co"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/functions/v1/content-signals-api"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "EASYFARM_CONTENT_SIGNALS_URL is outside the automation allowlist"
        )
    return normalized


def _rfc3339(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not 20 <= len(value) <= 40:
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    return parsed.astimezone(timezone.utc)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    return value


def _exact_keys(
    value: Mapping[str, object],
    expected: frozenset[str],
) -> None:
    if frozenset(value.keys()) != expected:
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )


def _reject_prohibited_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in _PROHIBITED_KEYS:
                raise ContentSignalsError(
                    "content_signals_privacy_violation", retryable=False
                )
            _reject_prohibited_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_prohibited_fields(item)


def _array(value: object, maximum: int) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    return value


def _count(value: object, *, maximum: int = _MAX_SAFE_COUNT) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    return value


def _number(
    value: object,
    *,
    minimum: float = 0,
    maximum: float = float(_MAX_SAFE_COUNT),
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    return float(value)


def _public_text(
    value: object,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    normalized = " ".join(value.split())
    if (
        value != normalized
        or len(value) > maximum
        or (not allow_empty and not value)
        or _CONTROL_PATTERN.search(value)
        or "http://" in value.casefold()
        or "https://" in value.casefold()
        or "@" in value
        or _WALLET_PATTERN.search(value)
        or _BASE58_WALLET_PATTERN.search(value)
        or _BECH32_LIKE_PATTERN.search(value)
        or _HIGH_ENTROPY_TOKEN_PATTERN.fullmatch(value)
    ):
        raise ContentSignalsError(
            "content_signals_privacy_violation", retryable=False
        )
    return value


def _public_url(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not 1 <= len(value) <= 500:
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
        or _WALLET_PATTERN.search(value)
        or _BASE58_WALLET_PATTERN.search(value)
        or _BECH32_LIKE_PATTERN.search(value)
        or _HIGH_ENTROPY_TOKEN_PATTERN.search(value)
    ):
        raise ContentSignalsError(
            "content_signals_privacy_violation", retryable=False
        )


def _nullable_datetime(value: object, field: str) -> None:
    if value is not None:
        _datetime(value, field)


def _validate_community(value: Mapping[str, object]) -> None:
    _exact_keys(value, frozenset({
        "method", "suppressed", "sample_size", "sentiment", "topics",
    }))
    method = _mapping(value.get("method"))
    _exact_keys(method, frozenset({"aggregation", "sentiment"}))
    if (
        method.get("aggregation")
        != "content_signals_community-v1-explicit-room"
        or method.get("sentiment") != "aggregate-tone-balance-v1"
        or not isinstance(value.get("suppressed"), bool)
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )

    sample = _mapping(value.get("sample_size"))
    _exact_keys(sample, frozenset({
        "meets_threshold",
        "meaningful_messages",
        "active_users",
        "tone_total",
        "minimum_meaningful_messages",
        "minimum_active_users",
    }))
    meets = sample.get("meets_threshold")
    if (
        not isinstance(meets, bool)
        or sample.get("minimum_meaningful_messages") != 20
        or sample.get("minimum_active_users") != 5
        or value.get("suppressed") is meets
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )

    if meets:
        messages = _count(sample.get("meaningful_messages"))
        active_users = _count(sample.get("active_users"))
        tone_total = _count(sample.get("tone_total"))
        if messages < 20 or active_users < 5 or tone_total > messages:
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
        sentiment = _mapping(value.get("sentiment"))
        _exact_keys(sentiment, frozenset({"index", "label", "tone", "method"}))
        if sentiment.get("method") != "aggregate-tone-balance-v1":
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
        _number(sentiment.get("index"), maximum=100)
        _public_text(sentiment.get("label"), maximum=24)
        tone = _mapping(sentiment.get("tone"))
        _exact_keys(
            tone,
            frozenset({"positive", "question", "negative", "neutral"}),
        )
        if sum(_count(tone.get(name)) for name in tone) != tone_total:
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
    elif (
        sample.get("meaningful_messages") is not None
        or sample.get("active_users") is not None
        or sample.get("tone_total") is not None
        or value.get("sentiment") is not None
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )

    topics = _array(value.get("topics"), 12)
    if not meets and topics:
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    for raw in topics:
        topic = _mapping(raw)
        _exact_keys(topic, frozenset({"label", "mentions", "distinct_users"}))
        _public_text(topic.get("label"), maximum=80)
        if (
            _count(topic.get("mentions")) < 10
            or _count(topic.get("distinct_users")) < 5
        ):
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )


def _validate_telegram(value: Mapping[str, object]) -> None:
    _exact_keys(value, frozenset({
        "method",
        "sample_size",
        "by_kind",
        "metrics",
        "top_posts",
        "top_links",
        "daily",
    }))
    if value.get("method") != "telethon-channel-metrics-v1":
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    _count(value.get("sample_size"))
    for raw in _array(value.get("by_kind"), 10):
        item = _mapping(raw)
        _exact_keys(item, frozenset({"kind", "count"}))
        _public_text(item.get("kind"), maximum=40)
        _count(item.get("count"))

    metrics = _mapping(value.get("metrics"))
    metric_keys = frozenset({
        "views_total",
        "views_avg",
        "forwards_total",
        "reactions_total",
        "clicks_total",
    })
    _exact_keys(metrics, metric_keys)
    for name in metric_keys:
        _count(metrics.get(name))

    for raw in _array(value.get("top_posts"), 5):
        item = _mapping(raw)
        _exact_keys(item, frozenset({
            "kind", "title", "url", "day", "views", "forwards", "reactions",
        }))
        _public_text(item.get("kind"), maximum=40)
        _public_text(item.get("title"), maximum=120, allow_empty=True)
        _public_text(item.get("day"), maximum=10, allow_empty=True)
        _public_url(item.get("url"))
        for name in ("views", "forwards", "reactions"):
            _count(item.get(name))

    for raw in _array(value.get("top_links"), 5):
        item = _mapping(raw)
        _exact_keys(item, frozenset({"label", "url", "clicks"}))
        _public_text(item.get("label"), maximum=80, allow_empty=True)
        _public_url(item.get("url"))
        _count(item.get("clicks"))

    for raw in _array(value.get("daily"), 31):
        item = _mapping(raw)
        _exact_keys(item, frozenset({"day", "posts", "views"}))
        _public_text(item.get("day"), maximum=10, allow_empty=True)
        _count(item.get("posts"))
        _count(item.get("views"))


def _validate_local_x(value: Mapping[str, object]) -> None:
    _exact_keys(value, frozenset({
        "method",
        "sample_size",
        "metrics",
        "followers",
        "by_type",
        "top_posts",
    }))
    if value.get("method") != "typefully-analytics-v2":
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    sample = _mapping(value.get("sample_size"))
    _exact_keys(sample, frozenset({"returned", "total", "truncated"}))
    returned = _count(sample.get("returned"), maximum=500)
    total = _count(sample.get("total"))
    if (
        total < returned
        or not isinstance(sample.get("truncated"), bool)
        or sample.get("truncated") is not (total > returned)
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )

    metrics = _mapping(value.get("metrics"))
    metric_keys = frozenset({
        "impressions",
        "engagement_total",
        "likes",
        "comments",
        "shares",
        "quotes",
        "saves",
        "link_clicks",
        "link_clicks_available_posts",
        "engagement_rate_pct",
    })
    _exact_keys(metrics, metric_keys)
    for name in metric_keys - {"engagement_rate_pct"}:
        _count(metrics.get(name))
    _number(metrics.get("engagement_rate_pct"), maximum=100)

    followers = _mapping(value.get("followers"))
    _exact_keys(followers, frozenset({"current", "delta", "as_of"}))
    if followers.get("current") is not None:
        _count(followers.get("current"))
    delta = followers.get("delta")
    if (
        delta is not None
        and (
            isinstance(delta, bool)
            or not isinstance(delta, int)
            or abs(delta) > _MAX_SAFE_COUNT
        )
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    _nullable_datetime(followers.get("as_of"), "local_x.followers.as_of")

    for raw in _array(value.get("by_type"), 20):
        item = _mapping(raw)
        _exact_keys(item, frozenset({"content_type", "posts"}))
        _public_text(item.get("content_type"), maximum=48)
        _count(item.get("posts"))

    top_metric_keys = frozenset({
        "impressions",
        "engagement_total",
        "likes",
        "comments",
        "shares",
        "quotes",
        "saves",
        "link_clicks",
    })
    for raw in _array(value.get("top_posts"), 10):
        item = _mapping(raw)
        _exact_keys(item, frozenset({
            "url", "preview", "posted_at", "content_type", "metrics",
        }))
        _public_url(item.get("url"))
        _public_text(item.get("preview"), maximum=280, allow_empty=True)
        _public_text(item.get("content_type"), maximum=48)
        _nullable_datetime(item.get("posted_at"), "local_x.posted_at")
        item_metrics = _mapping(item.get("metrics"))
        _exact_keys(item_metrics, top_metric_keys)
        for name in top_metric_keys - {"link_clicks"}:
            _count(item_metrics.get(name))
        if item_metrics.get("link_clicks") is not None:
            _count(item_metrics.get("link_clicks"))


def _demand_terms(value: object) -> tuple[DemandTerm, ...]:
    if not isinstance(value, list) or len(value) > _MAX_DEMAND_TERMS:
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    terms: list[DemandTerm] = []
    seen: set[str] = set()
    for raw in value:
        item = _mapping(raw)
        if not {"term", "weight"} <= set(item) or not set(item) <= {
            "term",
            "weight",
            "sources",
        }:
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
        raw_term = item.get("term")
        raw_weight = item.get("weight")
        if not isinstance(raw_term, str):
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
        term = " ".join(raw_term.split())
        normalized = term.casefold()
        if (
            not 2 <= len(term) <= _MAX_TERM_LENGTH
            or normalized in seen
            or "http://" in normalized
            or "https://" in normalized
            or "@" in term
            or _WALLET_PATTERN.search(term)
            or _BASE58_WALLET_PATTERN.search(term)
            or _BECH32_LIKE_PATTERN.search(term)
            or _HIGH_ENTROPY_TOKEN_PATTERN.fullmatch(term)
            or raw_term != term
            or isinstance(raw_weight, bool)
            or not isinstance(raw_weight, (int, float))
            or not math.isfinite(float(raw_weight))
            or not 0 <= float(raw_weight) <= 1
        ):
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
        raw_sources = item.get("sources", [])
        if (
            not isinstance(raw_sources, list)
            or not 1 <= len(raw_sources) <= len(_ALLOWED_SOURCES)
            or any(
                not isinstance(source, str) or source not in _ALLOWED_SOURCES
                for source in raw_sources
            )
            or len(set(raw_sources)) != len(raw_sources)
        ):
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
        seen.add(normalized)
        terms.append(DemandTerm(
            term=term,
            weight=float(raw_weight),
            sources=tuple(raw_sources),
        ))
    return tuple(terms)


def _snapshot(
    value: object,
    *,
    expected_client_id: str,
    expected_start: datetime,
    expected_end: datetime,
    now: datetime,
) -> ContentSignalsSnapshot:
    body = _mapping(value)
    _exact_keys(body, frozenset({
        "ok",
        "schema_version",
        "generated_at",
        "client_id",
        "window",
        "freshness",
        "availability",
        "community",
        "telegram_content",
        "local_x",
        "demand_terms",
        "demand_terms_meta",
        "privacy",
    }))
    _reject_prohibited_fields(body)
    if (
        body.get("ok") is not True
        or body.get("schema_version") != CONTENT_SIGNALS_SCHEMA_VERSION
        or body.get("client_id") != expected_client_id
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )

    window = _mapping(body.get("window"))
    _exact_keys(window, frozenset({"start", "end", "days", "timezone"}))
    window_start = _datetime(window.get("start"), "window.start")
    window_end = _datetime(window.get("end"), "window.end")
    days = window.get("days")
    if (
        window_start != expected_start
        or window_end != expected_end
        or window_end <= window_start
        or isinstance(days, bool)
        or not isinstance(days, int)
        or not 1 <= days <= 31
        or window_end - window_start != timedelta(days=days)
        or window.get("timezone") != "Asia/Seoul"
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )

    generated_at = _datetime(body.get("generated_at"), "generated_at")
    normalized_now = now.astimezone(timezone.utc)
    if (
        generated_at > normalized_now + _MAX_CLOCK_SKEW
        or generated_at < normalized_now - _MAX_SIGNAL_AGE
    ):
        raise ContentSignalsError(
            "content_signals_stale_response", retryable=False
        )

    availability = _mapping(body.get("availability"))
    _exact_keys(
        availability,
        frozenset({"community", "telegram_content", "local_x"}),
    )
    if any(value not in _AVAILABILITY_VALUES for value in availability.values()):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )

    freshness = _mapping(body.get("freshness"))
    _exact_keys(
        freshness,
        frozenset({"community", "telegram_content", "local_x"}),
    )
    freshness_rules = {
        "community": (48, "metrics_event"),
        "telegram_content": (24, "metrics_update"),
        "local_x": (24, "metrics_fetch"),
    }
    stale_sources: set[str] = set()
    for source, (stale_after_hours, basis) in freshness_rules.items():
        item = _mapping(freshness.get(source))
        _exact_keys(
            item,
            frozenset({"as_of", "stale", "stale_after_hours", "basis"}),
        )
        as_of = item.get("as_of")
        stale = item.get("stale")
        if (
            not isinstance(stale, bool)
            or item.get("stale_after_hours") != stale_after_hours
            or item.get("basis") != basis
            or (as_of is None and stale is not True)
        ):
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
        parsed_as_of = None
        if as_of is not None:
            parsed_as_of = _datetime(as_of, f"freshness.{source}.as_of")
            if (
                parsed_as_of > normalized_now + _MAX_CLOCK_SKEW
                or parsed_as_of > generated_at + _MAX_CLOCK_SKEW
            ):
                raise ContentSignalsError(
                    "content_signals_invalid_response", retryable=False
                )
        locally_stale = (
            parsed_as_of is None
            or generated_at - parsed_as_of
            > timedelta(hours=stale_after_hours)
            or normalized_now - window_end > timedelta(hours=48)
        )
        if locally_stale and stale is not True:
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
        if stale:
            stale_sources.add(source)

    section_validators = {
        "community": _validate_community,
        "telegram_content": _validate_telegram,
        "local_x": _validate_local_x,
    }
    for section, validator in section_validators.items():
        section_value = body.get(section)
        if availability[section] == "ok":
            validator(_mapping(section_value))
        if availability[section] == "unavailable":
            section_freshness = _mapping(freshness.get(section))
            if (
                section_value is not None
                or section_freshness.get("as_of") is not None
                or section_freshness.get("stale") is not True
            ):
                raise ContentSignalsError(
                    "content_signals_invalid_response", retryable=False
                )

    demand_terms = _demand_terms(body.get("demand_terms"))
    if any(
        source in stale_sources or availability[source] != "ok"
        for term in demand_terms
        for source in term.sources
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )
    demand_terms_meta = _mapping(body.get("demand_terms_meta"))
    _exact_keys(demand_terms_meta, frozenset({
        "method",
        "max_items",
        "stale_sources_excluded",
        "factual_evidence",
        "translation",
    }))
    if (
        demand_terms_meta.get("method") != "deterministic-aggregate-public-v1"
        or demand_terms_meta.get("max_items") != _MAX_DEMAND_TERMS
        or demand_terms_meta.get("stale_sources_excluded") is not True
        or demand_terms_meta.get("factual_evidence") is not False
        or demand_terms_meta.get("translation") is not False
    ):
        raise ContentSignalsError(
            "content_signals_invalid_response", retryable=False
        )

    privacy = _mapping(body.get("privacy"))
    _exact_keys(
        privacy,
        frozenset({"raw_messages_included", "user_identifiers_included"}),
    )
    if (
        privacy.get("raw_messages_included") is not False
        or privacy.get("user_identifiers_included") is not False
    ):
        raise ContentSignalsError(
            "content_signals_privacy_violation", retryable=False
        )

    return ContentSignalsSnapshot(
        schema_version=CONTENT_SIGNALS_SCHEMA_VERSION,
        client_id=expected_client_id,
        generated_at=generated_at,
        window_start=window_start,
        window_end=window_end,
        demand_terms=demand_terms,
    )


class EasyFarmContentSignalsClient:
    def __init__(
        self,
        *,
        endpoint_url: str,
        token: str,
        window_days: int = 7,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.endpoint_url = validate_content_signals_url(endpoint_url)
        self.token = token.strip()
        if not 32 <= len(self.token) <= 512:
            raise ValueError(
                "EASYFARM_CONTENT_SIGNALS_TOKEN must contain 32 to 512 characters"
            )
        if isinstance(window_days, bool) or not isinstance(window_days, int):
            raise ValueError(
                "EASYFARM_CONTENT_SIGNALS_WINDOW_DAYS must be an integer"
            )
        if not 1 <= window_days <= 31:
            raise ValueError(
                "EASYFARM_CONTENT_SIGNALS_WINDOW_DAYS must be between 1 and 31"
            )
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("content signals timeout must be between 1 and 30 seconds")
        self.window_days = window_days
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch(
        self,
        *,
        client_id: str,
        now: datetime,
    ) -> ContentSignalsSnapshot:
        if client_id not in _ALLOWED_CLIENTS:
            raise ValueError("unsupported content signals client")
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("content signals clock must be timezone-aware")
        end = (
            now.astimezone(timezone.utc)
            .replace(microsecond=0)
        )
        start = end - timedelta(days=self.window_days)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.endpoint_url,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "schema_version": CONTENT_SIGNALS_SCHEMA_VERSION,
                        "client_id": client_id,
                        "start": _rfc3339(start),
                        "end": _rfc3339(end),
                    },
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ContentSignalsError(
                "content_signals_unavailable", retryable=True
            ) from exc

        if response.status_code != 200:
            raise ContentSignalsError(
                "content_signals_request_rejected",
                retryable=response.status_code in {
                    408,
                    425,
                    429,
                    500,
                    502,
                    503,
                    504,
                },
            )
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ContentSignalsError(
                "content_signals_response_too_large", retryable=False
            )
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type.strip().lower() != "application/json":
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ContentSignalsError(
                "content_signals_invalid_response", retryable=False
            ) from exc
        return _snapshot(
            body,
            expected_client_id=client_id,
            expected_start=start,
            expected_end=end,
            now=end,
        )
