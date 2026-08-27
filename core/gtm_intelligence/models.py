from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Iterable, Literal, Optional, Union
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    UUID4,
    field_validator,
    model_validator,
)

GTM_CLIENT_IDS = ("babylon", "origintrail", "squid", "yellow")
GtmClientId = Literal["babylon", "origintrail", "squid", "yellow"]

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{1,79}$")
_REF_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{7,199}$")
_X_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_URL_PATTERN = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|www\.|t\.me/|telegram\.me/)",
    re.IGNORECASE,
)
_BARE_DOMAIN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9-])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![A-Za-z0-9-])"
)
_HANDLE_PATTERN = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]{2,}")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?\d[\d -]{8,}\d)(?!\d)")
_KOREAN_PHONE_PATTERN = re.compile(
    r"(?<!\d)01[016789][ .-]?\d{3,4}[ .-]?\d{4}(?!\d)"
)
_HEX_WALLET_PATTERN = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
_BASE58_PATTERN = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_BITCOIN_WALLET_PATTERN = re.compile(
    r"\b(?:[13][a-km-zA-HJ-NP-Z1-9]{25,34}|(?:bc1|tb1)[ac-hj-np-z02-9]{11,71})\b",
    re.IGNORECASE,
)
_BECH32_WALLET_PATTERN = re.compile(
    r"\b(?:cosmos|osmo|inj|bnb|sei|celestia)1[ac-hj-np-z02-9]{20,90}\b",
    re.IGNORECASE,
)
_IPV4_PATTERN = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")
_IPV6_PATTERN = re.compile(
    r"(?<![A-Fa-f0-9:])(?:[A-Fa-f0-9]{0,4}:){2,7}"
    r"[A-Fa-f0-9]{0,4}(?![A-Fa-f0-9:])"
)
_SESSION_PATTERN = re.compile(
    r"\b(?:cookie|session(?:_id)?|sid)\s*[:=]",
    re.IGNORECASE,
)
_TELEGRAM_IDENTIFIER_PATTERN = re.compile(r"(?<!\d)-?\d{6,19}(?!\d)")
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_STRUCTURE_PATTERN = re.compile(r"[\r\n\t]")
_X_STATUS_PATH_PATTERN = re.compile(
    r"^/([A-Za-z0-9_]{1,15})/status/([1-9][0-9]{5,24})$"
)
_PROMPT_INJECTION_PATTERNS = (
    re.compile(
        r"\bignore\s+(?:all\s+|any\s+|the\s+)?"
        r"(?:previous|prior|above|system|developer)\s+instructions?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:system prompt|developer message|reveal the prompt)\b", re.I),
    re.compile(r"\b(?:call|invoke)\s+(?:a\s+|the\s+)?tool\b", re.I),
    re.compile(r"\bexecute\s+(?:a\s+|the\s+)?command\b", re.I),
    re.compile(r"이전.{0,20}(?:지침|명령).{0,10}무시", re.I),
    re.compile(r"(?:시스템\s*프롬프트|개발자\s*메시지)", re.I),
    re.compile(r"(?:도구\s*호출|명령\s*실행|비밀.{0,10}출력)", re.I),
)
_SECRET_PATTERNS = (
    re.compile(
        r"(?<![A-Za-z0-9])(?:sk|xai)-[A-Za-z0-9_-]{20,}"
        r"(?![A-Za-z0-9])"
    ),
    re.compile(
        r"(?<![A-Za-z0-9])(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}"
        r"(?![A-Za-z0-9])"
    ),
    re.compile(
        r"(?<![A-Za-z0-9])sb_(?:secret|publishable)_[A-Za-z0-9_-]{20,}"
        r"(?![A-Za-z0-9])"
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}\b", re.I),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\."
        r"[A-Za-z0-9_-]{10,}\b"
    ),
    re.compile(r"\b[0-9]{6,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"(?<![A-Fa-f0-9])(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})"
        r"(?![A-Fa-f0-9])"
    ),
)
_TOKEN_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9_+=]{32,}(?![A-Za-z0-9])"
)
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}
_MAX_OBSERVATION_AGE = timedelta(hours=24)
_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
_PHASE0_CLIENT_ID = "squid"
_SQUID_OFFICIAL_X_HANDLE = "SquidRouter"


class GtmDomain(str, Enum):
    OPS = "ops"
    TELEGRAM_TRIAGE = "telegram_triage"
    X_NARRATIVE_QA = "x_narrative_qa"


class GtmStatus(str, Enum):
    INFO = "info"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    UNOBSERVED = "unobserved"


class GtmPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class GtmEvidenceKind(str, Enum):
    OFFICIAL_URL = "official_url"
    CONTENT_HASH = "content_hash"
    BANNER_HASH = "banner_hash"
    QA_RECEIPT = "qa_receipt"
    RUNTIME_RECEIPT = "runtime_receipt"
    QUESTION_DIGEST = "question_digest"
    FAQ_RECEIPT = "faq_receipt"
    AGGREGATE = "aggregate"


def _utc_seconds(value: datetime, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None or value.microsecond != 0:
        raise ValueError(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError(code) from exc


def _utc_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        return _utc_z(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _telegram_faq_binding_sha256(
    *,
    question_ref: str,
    faq_match: str,
    faq_source_sha256: str,
    draft_reply_ko: str,
) -> str:
    """Bind an opaque question, exact FAQ source, and exact draft bytes."""

    payload = {
        "draft_reply_sha256": hashlib.sha256(
            draft_reply_ko.encode("utf-8")
        ).hexdigest(),
        "faq_match": faq_match,
        "faq_source_sha256": faq_source_sha256,
        "question_ref": question_ref,
        "schema_version": "coineasy-telegram-faq-binding@1",
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _qa_receipt_subject_sha256(
    *,
    source_url: str,
    content_item_id: object,
    content_version_id: object,
    content_sha256: str,
    banner_sha256: Optional[str],
    qa_verdict: str,
    issue_codes: Iterable[str],
) -> str:
    """Bind a QA receipt claim to its exact content/version/verdict subject."""

    payload = {
        "banner_sha256": banner_sha256,
        "content_item_id": content_item_id,
        "content_sha256": content_sha256,
        "content_version_id": content_version_id,
        "issue_codes": tuple(issue_codes),
        "qa_verdict": qa_verdict,
        "schema_version": "coineasy-qa-receipt-subject@1",
        "source_url": source_url,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _qa_receipt_sha256(*, subject_sha256: str) -> str:
    """Deterministically bind the GTM QA receipt to one exact subject."""

    payload = {
        "schema_version": "coineasy-qa-receipt-binding@1",
        "subject_sha256": subject_sha256,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS) or bool(
        _TOKEN_CANDIDATE.search(value)
    )


def _safe_text(
    value: str,
    code: str,
    minimum: int,
    maximum: int,
    *,
    single_line: bool = False,
) -> str:
    normalized = value.strip()
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(code) from exc
    if (
        not minimum <= len(encoded) <= maximum
        or _CONTROL_PATTERN.search(normalized)
        or (single_line and _LINE_STRUCTURE_PATTERN.search(normalized))
        or _contains_secret(normalized)
    ):
        raise ValueError(code)
    return normalized


def _safe_single_line(value: str, code: str, minimum: int, maximum: int) -> str:
    return _safe_text(
        value,
        code,
        minimum,
        maximum,
        single_line=True,
    )


def _safe_redacted_text(
    value: str,
    code: str,
    minimum: int,
    maximum: int,
) -> str:
    normalized = _safe_single_line(value, code, minimum, maximum)
    if any(pattern.search(normalized) for pattern in (
        _EMAIL_PATTERN,
        _URL_PATTERN,
        _BARE_DOMAIN_PATTERN,
        _HANDLE_PATTERN,
        _PHONE_PATTERN,
        _KOREAN_PHONE_PATTERN,
        _HEX_WALLET_PATTERN,
        _BASE58_PATTERN,
        _BITCOIN_WALLET_PATTERN,
        _BECH32_WALLET_PATTERN,
        _IPV4_PATTERN,
        _IPV6_PATTERN,
        _SESSION_PATTERN,
        *_PROMPT_INJECTION_PATTERNS,
    )):
        raise ValueError(code)
    return normalized


def _safe_https_url(value: str, code: str) -> str:
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(code) from exc
    if (
        not 1 <= len(normalized) <= 2_048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or _CONTROL_PATTERN.search(normalized)
        or _contains_secret(normalized)
    ):
        raise ValueError(code)
    return normalized


def _x_status_parts(value: str, code: str) -> tuple[str, str, str]:
    normalized = _safe_https_url(value, code)
    parsed = urlsplit(normalized)
    if (parsed.hostname or "").lower() not in {"x.com", "www.x.com"}:
        raise ValueError(code)
    match = _X_STATUS_PATH_PATTERN.fullmatch(parsed.path)
    if match is None:
        raise ValueError(code)
    return normalized, match.group(1), match.group(2)


def _sorted_codes(values: Iterable[str], code: str) -> tuple[str, ...]:
    normalized = tuple(values)
    if (
        len(normalized) != len(set(normalized))
        or normalized != tuple(sorted(normalized))
        or any(not _CODE_PATTERN.fullmatch(value) for value in normalized)
    ):
        raise ValueError(code)
    return normalized


def _require_exact_values(
    value: object,
    expected: dict[str, object],
    code: str,
) -> object:
    if isinstance(value, dict):
        for field_name, exact_value in expected.items():
            if field_name not in value:
                continue
            candidate = value[field_name]
            if type(candidate) is not type(exact_value) or candidate != exact_value:
                raise ValueError(code)
    return value


class GtmEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: GtmEvidenceKind
    uri: Optional[str] = Field(default=None, max_length=2_048)
    sha256: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    observed_at: datetime

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _safe_https_url(value, "gtm_evidence_uri_invalid")

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "gtm_evidence_time_invalid")

    @model_validator(mode="after")
    def validate_evidence(self) -> "GtmEvidence":
        if self.uri is None and self.sha256 is None:
            raise ValueError("gtm_evidence_empty")
        if self.kind == GtmEvidenceKind.OFFICIAL_URL:
            if self.uri is None or self.sha256 is not None:
                raise ValueError("gtm_evidence_official_url_invalid")
            _x_status_parts(self.uri, "gtm_evidence_official_url_invalid")
        elif self.uri is not None or self.sha256 is None:
            raise ValueError("gtm_evidence_digest_invalid")
        return self


class GtmLineage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    correlation_ref: str = Field(min_length=8, max_length=200)
    work_order_id: Optional[UUID4] = None
    content_item_id: Optional[UUID4] = None
    content_version_id: Optional[UUID4] = None
    narrative_candidate_id: Optional[str] = Field(default=None, max_length=200)

    @field_validator("correlation_ref")
    @classmethod
    def validate_correlation_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not _REF_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_lineage_correlation_invalid")
        return normalized

    @field_validator("narrative_candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not _REF_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_lineage_candidate_invalid")
        return normalized


class GtmPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    internal_ops_only: Literal[True] = True
    read_only: Literal[True] = True
    public_eligible: Literal[False] = False
    approval_required: Literal[True] = True
    external_actions_allowed: Literal[0] = 0
    automatic_publication: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def validate_exact_policy_literals(cls, value: object) -> object:
        return _require_exact_values(
            value,
            {
                "internal_ops_only": True,
                "read_only": True,
                "public_eligible": False,
                "approval_required": True,
                "external_actions_allowed": 0,
                "automatic_publication": False,
            },
            "gtm_policy_literal_invalid",
        )


class GtmNextAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal[
        "no_action",
        "review",
        "verify_source",
        "reply_draft",
        "investigate",
    ]
    human_required: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_human_gate(self) -> "GtmNextAction":
        if self.human_required != (self.code != "no_action"):
            raise ValueError("gtm_next_action_human_gate_invalid")
        return self


class OpsDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-ops-detail@1"] = "coineasy-ops-detail@1"
    service_name: str = Field(min_length=2, max_length=80)
    deployment_status: Literal[
        "running", "success", "building", "failed", "crashed", "unobserved"
    ]
    deployed_sha: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    expected_sha: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    sha_matches: Optional[bool] = Field(default=None, strict=True)
    runtime_status: Literal["healthy", "degraded", "failed", "unobserved"]
    schedule_status: Literal[
        "on_time", "late", "missed", "not_scheduled", "unobserved"
    ]
    last_tick_at: Optional[datetime] = None
    next_tick_at: Optional[datetime] = None
    schedule_interval_seconds: Optional[int] = Field(
        default=None,
        strict=True,
        ge=60,
        le=604_800,
    )
    schedule_grace_seconds: Optional[int] = Field(
        default=None,
        strict=True,
        ge=0,
        le=86_400,
    )
    failure_count: Optional[int] = Field(
        default=None,
        strict=True,
        ge=0,
        le=1_000_000,
    )
    failure_codes: tuple[str, ...] = Field(default=(), max_length=8)
    source_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    change_detected: bool = Field(strict=True)
    raw_logs_included: Literal[False] = False
    mutation_capability: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def validate_exact_ops_literals(cls, value: object) -> object:
        return _require_exact_values(
            value,
            {
                "raw_logs_included": False,
                "mutation_capability": False,
            },
            "gtm_ops_literal_invalid",
        )

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        normalized = value.strip()
        if not _CODE_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_ops_service_invalid")
        return normalized

    @field_validator("last_tick_at", "next_tick_at")
    @classmethod
    def validate_tick_time(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return _utc_seconds(value, "gtm_ops_tick_time_invalid")

    @field_validator("failure_codes")
    @classmethod
    def validate_failure_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_codes(values, "gtm_ops_failure_codes_invalid")

    @model_validator(mode="after")
    def validate_state(self) -> "OpsDetails":
        if self.deployment_status in {"running", "success"} and self.deployed_sha is None:
            raise ValueError("gtm_ops_deployed_sha_missing")
        if self.expected_sha is not None and self.deployed_sha is None:
            raise ValueError("gtm_ops_sha_pair_invalid")
        if self.expected_sha is None and self.sha_matches is not None:
            raise ValueError("gtm_ops_sha_match_unbound")
        if self.expected_sha is not None and self.sha_matches != (
            self.expected_sha == self.deployed_sha
        ):
            raise ValueError("gtm_ops_sha_match_invalid")
        if self.sha_matches is False and not self.change_detected:
            raise ValueError("gtm_ops_sha_change_missing")
        if self.deployment_status in {"failed", "crashed"} and (
            self.runtime_status not in {"degraded", "failed"}
        ):
            raise ValueError("gtm_ops_deployment_runtime_mismatch")
        if self.runtime_status in {"degraded", "failed"} and (
            not self.failure_codes or self.failure_count is None or self.failure_count < 1
        ):
            raise ValueError("gtm_ops_failure_evidence_missing")
        if self.runtime_status == "healthy" and (
            self.failure_codes or self.failure_count != 0
        ):
            raise ValueError("gtm_ops_healthy_with_failures")
        if self.runtime_status == "unobserved" and (
            self.failure_codes or self.failure_count is not None
        ):
            raise ValueError("gtm_ops_unobserved_failure_count_invalid")
        is_scheduled = self.schedule_status in {"on_time", "late", "missed"}
        schedule_values = (
            self.last_tick_at,
            self.next_tick_at,
            self.schedule_interval_seconds,
            self.schedule_grace_seconds,
        )
        if is_scheduled and any(value is None for value in schedule_values):
            raise ValueError("gtm_ops_schedule_binding_missing")
        if not is_scheduled and any(value is not None for value in schedule_values):
            raise ValueError("gtm_ops_unscheduled_tick_invalid")
        if is_scheduled:
            assert self.last_tick_at is not None
            assert self.next_tick_at is not None
            assert self.schedule_interval_seconds is not None
            interval = int((self.next_tick_at - self.last_tick_at).total_seconds())
            if interval != self.schedule_interval_seconds:
                raise ValueError("gtm_ops_schedule_interval_invalid")
        return self


class TelegramTriageDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-telegram-triage-detail@1"] = (
        "coineasy-telegram-triage-detail@1"
    )
    question_ref: str = Field(pattern=r"^question:[a-f0-9]{64}$")
    digest_scheme: Literal["hmac-sha256-v1"] = "hmac-sha256-v1"
    topic: Literal[
        "onboarding",
        "product",
        "technical",
        "event",
        "rewards",
        "wallet_security",
        "investment",
        "privacy",
        "other",
    ]
    question_summary_ko: str = Field(min_length=5, max_length=500)
    answer_state: Literal["unanswered", "answered", "resolved"]
    faq_match: Literal["none", "partial", "exact"]
    faq_source_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    faq_binding_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    draft_reply_ko: Optional[str] = Field(default=None, min_length=10, max_length=600)
    next_action_ko: str = Field(min_length=3, max_length=200)
    escalation_codes: tuple[str, ...] = Field(default=(), max_length=8)
    raw_user_identifiers_included: Literal[False] = False
    raw_chat_id_included: Literal[False] = False
    private_link_included: Literal[False] = False
    public_send_allowed: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def validate_exact_telegram_literals(cls, value: object) -> object:
        return _require_exact_values(
            value,
            {
                "raw_user_identifiers_included": False,
                "raw_chat_id_included": False,
                "private_link_included": False,
                "public_send_allowed": False,
            },
            "gtm_telegram_literal_invalid",
        )

    @field_validator("question_summary_ko")
    @classmethod
    def validate_question(cls, value: str) -> str:
        normalized = _safe_redacted_text(
            value,
            "gtm_telegram_question_invalid",
            5,
            500,
        )
        if _TELEGRAM_IDENTIFIER_PATTERN.search(normalized):
            raise ValueError("gtm_telegram_question_invalid")
        return normalized

    @field_validator("draft_reply_ko")
    @classmethod
    def validate_draft_reply(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = _safe_redacted_text(
            value,
            "gtm_telegram_reply_invalid",
            10,
            600,
        )
        if _TELEGRAM_IDENTIFIER_PATTERN.search(normalized):
            raise ValueError("gtm_telegram_reply_invalid")
        return normalized

    @field_validator("next_action_ko")
    @classmethod
    def validate_next_action(cls, value: str) -> str:
        normalized = _safe_redacted_text(
            value,
            "gtm_telegram_next_action_invalid",
            3,
            200,
        )
        if _TELEGRAM_IDENTIFIER_PATTERN.search(normalized):
            raise ValueError("gtm_telegram_next_action_invalid")
        return normalized

    @field_validator("escalation_codes")
    @classmethod
    def validate_escalation_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {
            "investment",
            "legal",
            "privacy",
            "reward_account",
            "security",
            "unknown",
            "wallet_signing",
        }
        normalized = _sorted_codes(values, "gtm_telegram_escalation_invalid")
        if any(value not in allowed for value in normalized):
            raise ValueError("gtm_telegram_escalation_invalid")
        return normalized

    @model_validator(mode="after")
    def validate_triage(self) -> "TelegramTriageDetails":
        has_faq_source = self.faq_source_sha256 is not None
        has_faq_binding = self.faq_binding_sha256 is not None
        if self.faq_match == "none" and any((
            self.draft_reply_ko is not None,
            has_faq_source,
            has_faq_binding,
        )):
            raise ValueError("gtm_telegram_unverified_reply_invalid")
        if self.faq_match != "none" and (
            self.draft_reply_ko is None
            or not has_faq_source
            or not has_faq_binding
        ):
            raise ValueError("gtm_telegram_faq_reply_missing")
        if self.answer_state != "unanswered" and self.draft_reply_ko is not None:
            raise ValueError("gtm_telegram_answered_reply_invalid")
        if self.draft_reply_ko is not None:
            assert self.faq_source_sha256 is not None
            assert self.faq_binding_sha256 is not None
            expected = _telegram_faq_binding_sha256(
                question_ref=self.question_ref,
                faq_match=self.faq_match,
                faq_source_sha256=self.faq_source_sha256,
                draft_reply_ko=self.draft_reply_ko,
            )
            if self.faq_binding_sha256 != expected:
                raise ValueError("gtm_telegram_faq_binding_invalid")
        return self


class NarrativeQaDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-narrative-qa-detail@1"] = (
        "coineasy-narrative-qa-detail@1"
    )
    signal_kind: Literal["official_source", "competitor", "kol", "content_qa"]
    source_url: str = Field(min_length=1, max_length=2_048)
    source_account: str = Field(min_length=1, max_length=15)
    claim_ko: str = Field(min_length=5, max_length=500)
    comparison_ko: Optional[str] = Field(default=None, min_length=5, max_length=500)
    confidence: Literal["high", "medium", "low"]
    content_sha256: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    banner_sha256: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    qa_receipt_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    qa_receipt_subject_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    qa_verdict: Literal["not_applicable", "pending", "pass", "warn", "block"]
    issue_codes: tuple[str, ...] = Field(default=(), max_length=8)
    publication_allowed: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def validate_exact_narrative_literals(cls, value: object) -> object:
        return _require_exact_values(
            value,
            {"publication_allowed": False},
            "gtm_narrative_literal_invalid",
        )

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        normalized, _, _ = _x_status_parts(
            value,
            "gtm_narrative_source_url_invalid",
        )
        return normalized

    @field_validator("source_account")
    @classmethod
    def validate_source_account(cls, value: str) -> str:
        normalized = value.strip()
        if not _X_HANDLE_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_narrative_source_account_invalid")
        return normalized

    @field_validator("claim_ko")
    @classmethod
    def validate_claim(cls, value: str) -> str:
        return _safe_redacted_text(
            value,
            "gtm_narrative_claim_invalid",
            5,
            500,
        )

    @field_validator("comparison_ko")
    @classmethod
    def validate_comparison(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _safe_redacted_text(
            value,
            "gtm_narrative_comparison_invalid",
            5,
            500,
        )

    @field_validator("issue_codes")
    @classmethod
    def validate_issue_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_codes(values, "gtm_narrative_issue_codes_invalid")

    @model_validator(mode="after")
    def validate_qa_state(self) -> "NarrativeQaDetails":
        _, url_handle, _ = _x_status_parts(
            self.source_url,
            "gtm_narrative_source_url_invalid",
        )
        if url_handle.lower() != self.source_account.lower():
            raise ValueError("gtm_narrative_source_account_mismatch")
        is_qa = self.signal_kind == "content_qa"
        if is_qa:
            if self.content_sha256 is None or self.qa_verdict == "not_applicable":
                raise ValueError("gtm_narrative_qa_binding_missing")
            if self.qa_verdict == "pending" and any((
                self.qa_receipt_sha256 is not None,
                self.qa_receipt_subject_sha256 is not None,
            )):
                raise ValueError("gtm_narrative_pending_receipt_invalid")
            if self.qa_verdict != "pending" and (
                self.qa_receipt_sha256 is None
                or self.qa_receipt_subject_sha256 is None
            ):
                raise ValueError("gtm_narrative_qa_receipt_missing")
        elif (
            self.content_sha256 is not None
            or self.banner_sha256 is not None
            or self.qa_receipt_sha256 is not None
            or self.qa_receipt_subject_sha256 is not None
            or self.qa_verdict != "not_applicable"
            or self.issue_codes
        ):
            raise ValueError("gtm_narrative_signal_qa_drift")
        if self.qa_verdict == "pass" and self.issue_codes:
            raise ValueError("gtm_narrative_pass_with_issues")
        if self.qa_verdict in {"warn", "block"} and not self.issue_codes:
            raise ValueError("gtm_narrative_issue_missing")
        return self


class UnobservedDetails(BaseModel):
    """Explicit missing observation. It never fabricates a numeric zero."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-unobserved-detail@1"] = (
        "coineasy-unobserved-detail@1"
    )
    source_domain: GtmDomain
    reason_code: str = Field(min_length=2, max_length=80)
    last_observed_at: Optional[datetime] = None
    observed_count: Literal[None] = None

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        normalized = value.strip()
        if not _CODE_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_unobserved_reason_invalid")
        return normalized

    @field_validator("last_observed_at")
    @classmethod
    def validate_last_observed_at(
        cls,
        value: Optional[datetime],
    ) -> Optional[datetime]:
        if value is None:
            return None
        return _utc_seconds(value, "gtm_unobserved_time_invalid")


GtmDetails = Annotated[
    Union[
        OpsDetails,
        TelegramTriageDetails,
        NarrativeQaDetails,
        UnobservedDetails,
    ],
    Field(discriminator="schema_version"),
]


class GtmOperatorItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal["coineasy-gtm-operator-item@1"] = (
        "coineasy-gtm-operator-item@1"
    )
    ref: str = Field(min_length=8, max_length=200)
    domain: GtmDomain
    event_type: str = Field(min_length=2, max_length=80)
    client_id: GtmClientId
    observed_at: datetime
    status: GtmStatus
    priority: GtmPriority
    title_ko: str = Field(min_length=3, max_length=160)
    summary_ko: str = Field(min_length=5, max_length=600)
    evidence: tuple[GtmEvidence, ...] = Field(default=(), max_length=8)
    lineage: GtmLineage
    policy: GtmPolicy = Field(default_factory=GtmPolicy)
    next_action: GtmNextAction
    details: GtmDetails
    supplied_item_sha256: Optional[str] = Field(
        default=None,
        alias="item_sha256",
        pattern=r"^[a-f0-9]{64}$",
    )

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not _REF_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_item_ref_invalid")
        return normalized

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        normalized = value.strip()
        if not _CODE_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_item_event_type_invalid")
        return normalized

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "gtm_item_observed_at_invalid")

    @field_validator("title_ko")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _safe_redacted_text(value, "gtm_item_title_invalid", 3, 160)

    @field_validator("summary_ko")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _safe_redacted_text(value, "gtm_item_summary_invalid", 5, 600)

    @model_validator(mode="after")
    def validate_domain_contract(self) -> "GtmOperatorItem":
        if self.status == GtmStatus.UNOBSERVED:
            if (
                not isinstance(self.details, UnobservedDetails)
                or self.details.source_domain != self.domain
                or self.evidence
                or any((
                    self.lineage.work_order_id,
                    self.lineage.content_item_id,
                    self.lineage.content_version_id,
                    self.lineage.narrative_candidate_id,
                ))
                or self.next_action.code != "verify_source"
            ):
                raise ValueError("gtm_item_unobserved_contract_invalid")
            return self._validate_supplied_hash()
        if isinstance(self.details, UnobservedDetails):
            raise ValueError("gtm_item_observed_contract_invalid")
        if self.status in {GtmStatus.NEEDS_REVIEW, GtmStatus.BLOCKED} and (
            not self.next_action.human_required
        ):
            raise ValueError("gtm_item_review_action_missing")
        if self.status == GtmStatus.BLOCKED and self.priority != GtmPriority.HIGH:
            raise ValueError("gtm_item_blocked_priority_invalid")
        if self.status == GtmStatus.NEEDS_REVIEW and self.priority == GtmPriority.LOW:
            raise ValueError("gtm_item_review_priority_invalid")
        expected = {
            OpsDetails: GtmDomain.OPS,
            TelegramTriageDetails: GtmDomain.TELEGRAM_TRIAGE,
            NarrativeQaDetails: GtmDomain.X_NARRATIVE_QA,
        }[type(self.details)]
        if self.domain != expected:
            raise ValueError("gtm_item_domain_mismatch")
        if not self.evidence:
            raise ValueError("gtm_item_evidence_missing")
        evidence_keys = [
            (
                item.kind.value,
                item.uri or "",
                item.sha256 or "",
                _utc_z(item.observed_at),
            )
            for item in self.evidence
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("gtm_item_evidence_duplicate")
        if self.domain == GtmDomain.OPS:
            detail = self.details
            assert isinstance(detail, OpsDetails)
            if any(
                item.kind not in {
                    GtmEvidenceKind.RUNTIME_RECEIPT,
                    GtmEvidenceKind.AGGREGATE,
                }
                for item in self.evidence
            ):
                raise ValueError("gtm_ops_evidence_kind_invalid")
            runtime_evidence = [
                item
                for item in self.evidence
                if item.kind == GtmEvidenceKind.RUNTIME_RECEIPT
            ]
            if (
                len(runtime_evidence) != 1
                or runtime_evidence[0].sha256
                != detail.source_receipt_sha256
            ):
                raise ValueError("gtm_ops_runtime_receipt_missing")
            if any((self.lineage.content_item_id, self.lineage.content_version_id)):
                raise ValueError("gtm_ops_content_lineage_invalid")
            critical = (
                detail.deployment_status in {"failed", "crashed"}
                or detail.runtime_status == "failed"
            )
            needs_attention = (
                detail.deployment_status in {"building", "unobserved"}
                or detail.runtime_status in {"degraded", "unobserved"}
                or detail.schedule_status in {"late", "missed", "unobserved"}
                or detail.sha_matches is False
                or detail.change_detected
            )
            expected_status = (
                GtmStatus.BLOCKED
                if critical
                else GtmStatus.NEEDS_REVIEW
                if needs_attention
                else GtmStatus.INFO
            )
            if self.status != expected_status:
                raise ValueError("gtm_ops_item_status_mismatch")
            if expected_status == GtmStatus.INFO:
                if self.next_action.code != "no_action":
                    raise ValueError("gtm_ops_item_action_mismatch")
            elif self.next_action.code != "investigate":
                raise ValueError("gtm_ops_item_action_mismatch")
            if detail.last_tick_at is not None and (
                detail.last_tick_at > self.observed_at + _FUTURE_CLOCK_SKEW
            ):
                raise ValueError("gtm_ops_tick_after_observation")
            if detail.next_tick_at is not None:
                assert detail.schedule_grace_seconds is not None
                grace_limit = detail.next_tick_at + timedelta(
                    seconds=detail.schedule_grace_seconds,
                )
                if (
                    detail.schedule_status == "on_time"
                    and self.observed_at > detail.next_tick_at
                ):
                    raise ValueError("gtm_ops_on_time_stale")
                if detail.schedule_status == "late" and not (
                    detail.next_tick_at < self.observed_at <= grace_limit
                ):
                    raise ValueError("gtm_ops_late_window_invalid")
                if (
                    detail.schedule_status == "missed"
                    and self.observed_at <= grace_limit
                ):
                    raise ValueError("gtm_ops_missed_window_invalid")
        elif self.domain == GtmDomain.TELEGRAM_TRIAGE:
            detail = self.details
            assert isinstance(detail, TelegramTriageDetails)
            if any(
                _TELEGRAM_IDENTIFIER_PATTERN.search(value)
                for value in (self.title_ko, self.summary_ko)
            ):
                raise ValueError("gtm_telegram_item_identifier_invalid")
            if any(
                item.kind not in {
                    GtmEvidenceKind.QUESTION_DIGEST,
                    GtmEvidenceKind.FAQ_RECEIPT,
                    GtmEvidenceKind.AGGREGATE,
                }
                for item in self.evidence
            ):
                raise ValueError("gtm_telegram_evidence_kind_invalid")
            digest = detail.question_ref.removeprefix("question:")
            expected_ref = f"telegram:{self.client_id}:{digest}"
            if self.ref != expected_ref or self.lineage.correlation_ref != expected_ref:
                raise ValueError("gtm_telegram_opaque_ref_invalid")
            question_evidence = [
                item
                for item in self.evidence
                if item.kind == GtmEvidenceKind.QUESTION_DIGEST
            ]
            if (
                len(question_evidence) != 1
                or question_evidence[0].sha256 != digest
            ):
                raise ValueError("gtm_telegram_question_digest_missing")
            faq_evidence = [
                item
                for item in self.evidence
                if item.kind == GtmEvidenceKind.FAQ_RECEIPT
            ]
            if detail.faq_binding_sha256 is not None:
                if (
                    len(faq_evidence) != 1
                    or faq_evidence[0].sha256 != detail.faq_binding_sha256
                ):
                    raise ValueError("gtm_telegram_faq_receipt_missing")
            elif faq_evidence:
                raise ValueError("gtm_telegram_faq_receipt_unbound")
            if any((self.lineage.content_item_id, self.lineage.content_version_id)):
                raise ValueError("gtm_telegram_content_lineage_invalid")
            if detail.escalation_codes:
                expected_status = GtmStatus.BLOCKED
                expected_action = "investigate"
            elif detail.answer_state == "unanswered":
                expected_status = GtmStatus.NEEDS_REVIEW
                expected_action = "reply_draft" if detail.draft_reply_ko else "review"
            else:
                expected_status = GtmStatus.INFO
                expected_action = "no_action"
            if self.status != expected_status or self.next_action.code != expected_action:
                raise ValueError("gtm_telegram_item_state_mismatch")
        else:
            detail = self.details
            assert isinstance(detail, NarrativeQaDetails)
            allowed_evidence_kinds = {
                GtmEvidenceKind.OFFICIAL_URL,
                GtmEvidenceKind.AGGREGATE,
            }
            if detail.signal_kind == "content_qa":
                allowed_evidence_kinds.update({
                    GtmEvidenceKind.CONTENT_HASH,
                    GtmEvidenceKind.BANNER_HASH,
                    GtmEvidenceKind.QA_RECEIPT,
                })
            if any(
                item.kind not in allowed_evidence_kinds
                for item in self.evidence
            ):
                raise ValueError("gtm_narrative_evidence_kind_invalid")
            _, url_handle, _ = _x_status_parts(
                detail.source_url,
                "gtm_narrative_source_url_invalid",
            )
            if url_handle.lower() != detail.source_account.lower():
                raise ValueError("gtm_narrative_source_account_mismatch")
            if (
                self.client_id == _PHASE0_CLIENT_ID
                and detail.signal_kind in {"official_source", "content_qa"}
                and detail.source_account.lower() != _SQUID_OFFICIAL_X_HANDLE.lower()
            ):
                raise ValueError("gtm_narrative_official_account_mismatch")
            official_evidence = [
                item
                for item in self.evidence
                if item.kind == GtmEvidenceKind.OFFICIAL_URL
            ]
            if (
                len(official_evidence) != 1
                or official_evidence[0].uri != detail.source_url
            ):
                raise ValueError("gtm_narrative_source_evidence_missing")
            if detail.signal_kind == "content_qa":
                content_evidence = [
                    item
                    for item in self.evidence
                    if item.kind == GtmEvidenceKind.CONTENT_HASH
                ]
                if (
                    self.lineage.content_item_id is None
                    or self.lineage.content_version_id is None
                    or len(content_evidence) != 1
                    or content_evidence[0].sha256 != detail.content_sha256
                ):
                    raise ValueError("gtm_narrative_qa_lineage_invalid")
                banner_evidence = [
                    item
                    for item in self.evidence
                    if item.kind == GtmEvidenceKind.BANNER_HASH
                ]
                if detail.banner_sha256 is not None and (
                    len(banner_evidence) != 1
                    or banner_evidence[0].sha256 != detail.banner_sha256
                ):
                    raise ValueError("gtm_narrative_banner_hash_missing")
                if detail.banner_sha256 is None and banner_evidence:
                    raise ValueError("gtm_narrative_banner_hash_unbound")
                receipt_evidence_items = [
                    item
                    for item in self.evidence
                    if item.kind == GtmEvidenceKind.QA_RECEIPT
                ]
                if detail.qa_receipt_sha256 is not None and (
                    len(receipt_evidence_items) != 1
                    or receipt_evidence_items[0].sha256
                    != detail.qa_receipt_sha256
                ):
                    raise ValueError("gtm_narrative_qa_receipt_missing")
                if detail.qa_receipt_sha256 is None and receipt_evidence_items:
                    raise ValueError("gtm_narrative_qa_receipt_unbound")
                if detail.qa_verdict != "pending":
                    assert detail.qa_receipt_subject_sha256 is not None
                    expected_subject = _qa_receipt_subject_sha256(
                        source_url=detail.source_url,
                        content_item_id=self.lineage.content_item_id,
                        content_version_id=self.lineage.content_version_id,
                        content_sha256=detail.content_sha256,
                        banner_sha256=detail.banner_sha256,
                        qa_verdict=detail.qa_verdict,
                        issue_codes=detail.issue_codes,
                    )
                    if detail.qa_receipt_subject_sha256 != expected_subject:
                        raise ValueError("gtm_narrative_qa_receipt_subject_mismatch")
                    if detail.qa_receipt_sha256 != _qa_receipt_sha256(
                        subject_sha256=expected_subject,
                    ):
                        raise ValueError("gtm_narrative_qa_receipt_binding_mismatch")
                    receipt_evidence = receipt_evidence_items[0]
                    subject_evidence = [
                        official_evidence[0],
                        content_evidence[0],
                        *banner_evidence,
                    ]
                    if receipt_evidence.observed_at < max(
                        item.observed_at for item in subject_evidence
                    ):
                        raise ValueError("gtm_narrative_qa_receipt_chronology_invalid")
            elif any((self.lineage.content_item_id, self.lineage.content_version_id)):
                raise ValueError("gtm_narrative_signal_content_lineage_invalid")
            if detail.signal_kind == "content_qa":
                expected_status = {
                    "block": GtmStatus.BLOCKED,
                    "warn": GtmStatus.NEEDS_REVIEW,
                    "pending": GtmStatus.NEEDS_REVIEW,
                    "pass": GtmStatus.INFO,
                }[detail.qa_verdict]
                if self.status != expected_status:
                    raise ValueError("gtm_narrative_qa_status_mismatch")
        if any(
            item.observed_at > self.observed_at + _FUTURE_CLOCK_SKEW
            for item in self.evidence
        ):
            raise ValueError("gtm_item_evidence_after_observation")
        return self._validate_supplied_hash()

    def _validate_supplied_hash(self) -> "GtmOperatorItem":
        if (
            self.supplied_item_sha256 is not None
            and self.supplied_item_sha256 != self.item_sha256
        ):
            raise ValueError("gtm_item_sha256_mismatch")
        return self

    def canonical_item(self) -> dict[str, object]:
        payload = _json_value(self.model_dump(
            mode="python",
            exclude={"supplied_item_sha256"},
        ))
        assert isinstance(payload, dict)
        evidence = payload.get("evidence")
        assert isinstance(evidence, list)
        payload["evidence"] = sorted(
            evidence,
            key=lambda item: _canonical_json(item),
        )
        return payload

    @property
    def item_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.canonical_item())).hexdigest()

    def as_payload(self) -> dict[str, object]:
        return {**self.canonical_item(), "item_sha256": self.item_sha256}


class GtmInboxPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal["coineasy-gtm-inbox@1"] = "coineasy-gtm-inbox@1"
    mode: Literal["shadow_read_only"] = "shadow_read_only"
    generated_at: datetime
    items: tuple[GtmOperatorItem, ...] = Field(default=(), max_length=50)
    next_cursor: Optional[str] = Field(default=None, max_length=200)
    read_only_projection: Literal[True] = True
    external_calls: Literal[False] = False
    database_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    automatic_publication: Literal[False] = False
    supplied_snapshot_sha256: Optional[str] = Field(
        default=None,
        alias="snapshot_sha256",
        pattern=r"^[a-f0-9]{64}$",
    )
    supplied_counts: Optional[dict[str, object]] = Field(
        default=None,
        alias="counts",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_exact_page_literals(cls, value: object) -> object:
        return _require_exact_values(
            value,
            {
                "read_only_projection": True,
                "external_calls": False,
                "database_calls": False,
                "provider_calls": False,
                "publication_calls": False,
                "automatic_publication": False,
            },
            "gtm_inbox_literal_invalid",
        )

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "gtm_inbox_generated_at_invalid")

    @field_validator("next_cursor")
    @classmethod
    def validate_cursor(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not _REF_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_inbox_cursor_invalid")
        return normalized

    @model_validator(mode="after")
    def validate_page(self) -> "GtmInboxPage":
        keys = [
            (
                item.client_id,
                _PRIORITY_RANK[item.priority.value],
                item.domain.value,
                item.observed_at,
                item.ref,
            )
            for item in self.items
        ]
        if keys != sorted(keys):
            raise ValueError("gtm_inbox_order_invalid")
        refs = [item.ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("gtm_inbox_item_duplicate")
        future_limit = self.generated_at + _FUTURE_CLOCK_SKEW
        stale_limit = self.generated_at - _MAX_OBSERVATION_AGE
        if any(item.observed_at > future_limit for item in self.items):
            raise ValueError("gtm_inbox_future_observation")
        if any(item.observed_at < stale_limit for item in self.items):
            raise ValueError("gtm_inbox_stale_observation")
        if any(
            evidence.observed_at < stale_limit
            or evidence.observed_at > future_limit
            for item in self.items
            for evidence in item.evidence
        ):
            raise ValueError("gtm_inbox_stale_evidence")
        subject_keys: list[tuple[str, ...]] = []
        for item in self.items:
            if isinstance(item.details, OpsDetails):
                subject_keys.append((
                    item.client_id,
                    item.domain.value,
                    item.details.service_name,
                ))
            elif isinstance(item.details, TelegramTriageDetails):
                subject_keys.append((
                    item.client_id,
                    item.domain.value,
                    item.details.question_ref,
                ))
            elif isinstance(item.details, NarrativeQaDetails):
                subject_keys.append((
                    item.client_id,
                    item.domain.value,
                    str(
                        item.lineage.content_item_id
                        or item.lineage.narrative_candidate_id
                        or item.details.source_url
                    ),
                ))
            else:
                subject_keys.append((item.client_id, item.domain.value, item.ref))
        if len(subject_keys) != len(set(subject_keys)):
            raise ValueError("gtm_inbox_subject_duplicate")
        if self.supplied_counts is not None and (
            _canonical_json(self.supplied_counts) != _canonical_json(self.counts())
        ):
            raise ValueError("gtm_inbox_counts_mismatch")
        if (
            self.supplied_snapshot_sha256 is not None
            and self.supplied_snapshot_sha256 != self.snapshot_sha256
        ):
            raise ValueError("gtm_inbox_sha256_mismatch")
        return self

    def counts(self) -> dict[str, object]:
        return {
            "total": len(self.items),
            "domains": {
                domain.value: sum(item.domain == domain for item in self.items)
                for domain in GtmDomain
            },
            "statuses": {
                status.value: sum(item.status == status for item in self.items)
                for status in GtmStatus
            },
        }

    def canonical_page(self) -> dict[str, object]:
        return {
            "automatic_publication": self.automatic_publication,
            "counts": self.counts(),
            "database_calls": self.database_calls,
            "external_calls": self.external_calls,
            "generated_at": _utc_z(self.generated_at),
            "items": [item.as_payload() for item in self.items],
            "mode": self.mode,
            "next_cursor": self.next_cursor,
            "provider_calls": self.provider_calls,
            "publication_calls": self.publication_calls,
            "read_only_projection": self.read_only_projection,
            "schema_version": self.schema_version,
        }

    @property
    def snapshot_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.canonical_page())).hexdigest()

    def as_payload(self) -> dict[str, object]:
        return {**self.canonical_page(), "snapshot_sha256": self.snapshot_sha256}


def build_gtm_inbox(
    items: Iterable[GtmOperatorItem],
    *,
    generated_at: datetime,
    next_cursor: Optional[str] = None,
) -> GtmInboxPage:
    normalized = tuple(sorted(
        items,
        key=lambda item: (
            item.client_id,
            _PRIORITY_RANK[item.priority.value],
            item.domain.value,
            item.observed_at,
            item.ref,
        ),
    ))
    return GtmInboxPage(
        generated_at=generated_at,
        items=normalized,
        next_cursor=next_cursor,
    )


def validate_squid_shadow_page(page: GtmInboxPage) -> GtmInboxPage:
    """Enforce the narrower Phase 0 authority over the reusable base model."""

    if page.next_cursor is not None:
        raise ValueError("gtm_phase0_partial_seed_invalid")
    if any(item.client_id != _PHASE0_CLIENT_ID for item in page.items):
        raise ValueError("gtm_phase0_client_invalid")
    if {item.domain for item in page.items} != set(GtmDomain):
        raise ValueError("gtm_phase0_domain_coverage_invalid")
    return page


def phase0_gtm_json_schema() -> dict[str, object]:
    """Return the base schema with machine-readable Phase 0 scope constraints."""

    schema = GtmInboxPage.model_json_schema()
    item_definition = schema["$defs"]["GtmOperatorItem"]
    item_definition["properties"]["client_id"] = {
        "const": _PHASE0_CLIENT_ID,
        "title": "Client Id",
        "type": "string",
    }
    schema["properties"]["next_cursor"] = {
        "const": None,
        "default": None,
        "title": "Next Cursor",
        "type": "null",
    }
    schema["properties"]["items"]["minItems"] = len(GtmDomain)
    schema["allOf"] = [
        {
            "properties": {
                "items": {
                    "contains": {
                        "properties": {"domain": {"const": domain.value}},
                        "required": ["domain"],
                        "type": "object",
                    },
                    "minContains": 1,
                },
            },
            "required": ["items"],
        }
        for domain in GtmDomain
    ]
    schema["x-coineasy-phase0"] = {
        "client_id": _PHASE0_CLIENT_ID,
        "complete_seed": True,
        "domains": [domain.value for domain in GtmDomain],
        "next_cursor": None,
        "semantic_validator": "validate_squid_shadow_page",
    }
    return schema


def _render_items(page: GtmInboxPage, domain: GtmDomain) -> str:
    selected = [item for item in page.items if item.domain == domain]
    if not selected:
        return "- 관측 항목 없음"
    return "\n".join(
        (
            f"- [{item.priority.value}/{item.status.value}] "
            f"{item.client_id}: {item.title_ko} · {item.summary_ko} "
            f"· 다음 `{item.next_action.code}`"
        )
        for item in selected
    )


def render_gtm_inbox(page: GtmInboxPage) -> str:
    page = validate_squid_shadow_page(page)
    counts = page.counts()
    statuses = counts["statuses"]
    assert isinstance(statuses, dict)
    return f"""# CoinEasy GTM 읽기 전용 Inbox

## 1. 상태

- 기준 시각: `{_utc_z(page.generated_at)}`
- 전체: {counts['total']}건
- 검토 필요: {statuses['needs_review']}건
- 차단: {statuses['blocked']}건
- 미관측: {statuses['unobserved']}건 (0으로 환산하지 않음)

## 2. Railway · 운영 상태

{_render_items(page, GtmDomain.OPS)}

## 3. Telegram 커뮤니티 트리아지

{_render_items(page, GtmDomain.TELEGRAM_TRIAGE)}

## 4. X 내러티브 · 콘텐츠 QA

{_render_items(page, GtmDomain.X_NARRATIVE_QA)}

## 5. 권한 경계

- 이 화면의 외부/DB/provider/publication 호출: `0`
- 공개 발송·게시·배포·승인: `OFF`
- Snapshot SHA-256: `{page.snapshot_sha256}`
"""
