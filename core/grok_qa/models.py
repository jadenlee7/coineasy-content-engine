from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal, Mapping, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


GROK_QA_CLIENTS = ("yellow", "origintrail", "squid", "babylon")
OFFICIAL_X_HANDLES: Mapping[str, str] = {
    "yellow": "Yellow",
    "origintrail": "origin_trail",
    "squid": "SquidRouter",
    "babylon": "babylonlabs_io",
}
MAX_INLINE_PNG_BYTES = 3_000_000

_UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_ISSUE_CODE_PATTERN = r"^[a-z][a-z0-9_]{2,47}$"
_PROVIDER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}$"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PRIVATE_PROMPT_MARKERS = (
    "/storage/v1/object/sign/",
    "api_secret",
    "studio_access_token",
    "supabase_service_role_key",
    "authorization\":\"bearer",
    "raw_source",
    "request_headers",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _credential_free_https_url(value: str, code: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError(code) from exc
    if (
        len(value) > 2_048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(code)
    return value


def official_x_status_url(client_id: str, value: str) -> str:
    handle = OFFICIAL_X_HANDLES.get(client_id)
    if handle is None:
        raise ValueError("grok_qa_client_invalid")
    expected = re.fullmatch(
        rf"https://x\.com/{re.escape(handle)}/status/([1-9][0-9]{{0,18}})",
        value,
        flags=re.IGNORECASE,
    )
    if expected is None:
        raise ValueError("grok_qa_source_url_invalid")
    return value


def provider_x_citation_matches(
    client_id: str,
    source_url: str,
    citation_url: str,
) -> bool:
    try:
        official_x_status_url(client_id, source_url)
        source = urlsplit(source_url)
        citation = urlsplit(citation_url)
        citation_port = citation.port
    except ValueError:
        return False
    if (
        citation.scheme != "https"
        or (citation.hostname or "").lower() != "x.com"
        or citation.username is not None
        or citation.password is not None
        or citation_port is not None
        or citation.query
        or citation.fragment
    ):
        return False
    source_match = re.fullmatch(
        r"/([A-Za-z0-9_]{1,15})/status/([1-9][0-9]{0,18})",
        source.path.rstrip("/"),
    )
    candidate_match = re.fullmatch(
        r"/([A-Za-z0-9_]{1,15})/status/([1-9][0-9]{0,18})",
        citation.path.rstrip("/"),
    )
    canonical_match = re.fullmatch(
        r"/i/status/([1-9][0-9]{0,18})",
        citation.path.rstrip("/"),
        flags=re.IGNORECASE,
    )
    if source_match is None:
        return False
    return bool(
        (
            candidate_match is not None
            and candidate_match.group(1).lower()
            == OFFICIAL_X_HANDLES[client_id].lower()
            and candidate_match.group(2) == source_match.group(2)
        )
        or (
            canonical_match is not None
            and canonical_match.group(1) == source_match.group(2)
        )
    )


class GrokQaCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["PASS", "WARN", "BLOCK"]
    checks: list[str] = Field(min_length=1, max_length=6)

    @field_validator("checks")
    @classmethod
    def normalize_checks(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not 3 <= len(value) <= 300 for value in normalized):
            raise ValueError("grok_qa_check_invalid")
        return normalized


class GrokQaFactCheck(GrokQaCheck):
    source_urls: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("grok_qa_source_duplicate")
        return [
            _credential_free_https_url(value, "grok_qa_source_url_invalid")
            for value in values
        ]


class GrokQaIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: Literal["WARN", "BLOCK"]
    code: str = Field(pattern=_ISSUE_CODE_PATTERN)
    message: str = Field(min_length=3, max_length=500)
    evidence_url: Optional[str] = Field(default=None, max_length=2_048)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("grok_qa_issue_message_invalid")
        return normalized

    @field_validator("evidence_url")
    @classmethod
    def validate_evidence_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _credential_free_https_url(value, "grok_qa_evidence_url_invalid")


class GrokQaVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["PASS", "WARN", "BLOCK"]
    summary: str = Field(min_length=10, max_length=800)
    fact_check: GrokQaFactCheck
    brand_check: GrokQaCheck
    issues: list[GrokQaIssue] = Field(default_factory=list, max_length=3)
    next_action: Literal[
        "ready_for_human_approval",
        "human_review",
        "verify_source",
        "revise_copy",
        "revise_banner",
    ]

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        normalized = value.strip()
        if not 10 <= len(normalized) <= 800:
            raise ValueError("grok_qa_summary_invalid")
        return normalized

    @model_validator(mode="after")
    def validate_consistency(self) -> "GrokQaVerdict":
        if self.decision == "PASS" and (
            self.fact_check.status != "PASS"
            or self.brand_check.status != "PASS"
            or self.issues
            or not self.fact_check.source_urls
            or self.next_action != "ready_for_human_approval"
        ):
            raise ValueError("grok_qa_pass_evidence_incomplete")
        if (
            self.decision != "PASS"
            and self.next_action == "ready_for_human_approval"
        ):
            raise ValueError("grok_qa_next_action_invalid")
        if self.decision == "BLOCK" and (
            self.fact_check.status != "BLOCK"
            and self.brand_check.status != "BLOCK"
            and not any(issue.severity == "BLOCK" for issue in self.issues)
        ):
            raise ValueError("grok_qa_block_evidence_incomplete")
        sources = set(self.fact_check.source_urls)
        if any(
            issue.evidence_url is not None
            and issue.evidence_url not in sources
            for issue in self.issues
        ):
            raise ValueError("grok_qa_issue_source_mismatch")
        return self

    def validate_source_boundary(self, source_url: str) -> None:
        sources = self.fact_check.source_urls
        if sources != [source_url]:
            raise ValueError("grok_qa_source_mismatch")


# This schema deliberately mirrors the MCP Zod contract. Semantic relationships
# (for example, PASS requiring two PASS checks) are revalidated by GrokQaVerdict.
GROK_QA_VERDICT_JSON_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["PASS", "WARN", "BLOCK"]},
        "summary": {"type": "string", "minLength": 10, "maxLength": 800},
        "fact_check": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["PASS", "WARN", "BLOCK"],
                },
                "checks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "items": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 300,
                    },
                },
                "source_urls": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "string",
                        "format": "uri",
                        "maxLength": 2_048,
                    },
                },
            },
            "required": ["status", "checks", "source_urls"],
        },
        "brand_check": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["PASS", "WARN", "BLOCK"],
                },
                "checks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "items": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 300,
                    },
                },
            },
            "required": ["status", "checks"],
        },
        "issues": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["WARN", "BLOCK"],
                    },
                    "code": {
                        "type": "string",
                        "pattern": _ISSUE_CODE_PATTERN,
                    },
                    "message": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 500,
                    },
                    "evidence_url": {
                        "type": "string",
                        "format": "uri",
                        "maxLength": 2_048,
                    },
                },
                "required": ["severity", "code", "message"],
            },
        },
        "next_action": {
            "type": "string",
            "enum": [
                "ready_for_human_approval",
                "human_review",
                "verify_source",
                "revise_copy",
                "revise_banner",
            ],
        },
    },
    "required": [
        "decision",
        "summary",
        "fact_check",
        "brand_check",
        "issues",
        "next_action",
    ],
}


class GrokQaModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_response_id: str = Field(pattern=_PROVIDER_ID_PATTERN)
    model: Literal["grok-4.5"]
    cost_in_usd_ticks: int = Field(strict=True, ge=0)
    input_sha256: str = Field(pattern=_SHA256_PATTERN)
    x_search_performed: Literal[True]
    x_search_citations: list[str] = Field(min_length=1, max_length=8)
    x_search_calls: int = Field(strict=True, ge=1, le=3)
    verdict: GrokQaVerdict

    @field_validator("x_search_citations")
    @classmethod
    def validate_x_search_citations(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("grok_qa_x_search_citation_duplicate")
        return [
            _credential_free_https_url(
                value,
                "grok_qa_x_search_citation_invalid",
            )
            for value in values
        ]

    @property
    def result_sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.model_dump(mode="json"))
        ).hexdigest()


class GrokQaWorkClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_item_id: str = Field(pattern=_UUID_PATTERN)
    content_version_id: str = Field(pattern=_UUID_PATTERN)
    client_id: Literal["yellow", "origintrail", "squid", "babylon"]
    content_kind: Literal["daily_news"]
    title: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=20, max_length=2_048)
    source_published_at: datetime
    review_text: str = Field(min_length=20, max_length=100_000)
    image_png: bytes
    image_sha256: str = Field(pattern=_SHA256_PATTERN)
    attempt: int = Field(strict=True, ge=1, le=5)
    max_attempts: int = Field(strict=True, ge=1, le=5)
    provider_call_required: bool = True
    staged_result: Optional[GrokQaModelResult] = None
    staged_verdict_sha256: Optional[str] = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    staged_prompt_version: Optional[Literal["official-x-grok-qa@1"]] = None

    @field_validator("source_published_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("grok_qa_source_timestamp_invalid")
        return value

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or _CONTROL_RE.search(normalized):
            raise ValueError("grok_qa_title_invalid")
        return normalized

    @field_validator("review_text")
    @classmethod
    def validate_review_text(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not 20 <= len(normalized) <= 100_000:
            raise ValueError("grok_qa_review_text_invalid")
        lowered = re.sub(r"\s+", "", normalized.lower())
        if any(marker in lowered for marker in _PRIVATE_PROMPT_MARKERS):
            raise ValueError("grok_qa_review_text_private_data")
        return normalized

    @field_validator("image_png", mode="before")
    @classmethod
    def require_png_bytes(cls, value: object) -> bytes:
        if not isinstance(value, (bytes, bytearray)):
            raise ValueError("grok_qa_image_invalid")
        return bytes(value)

    @model_validator(mode="after")
    def validate_claim(self) -> "GrokQaWorkClaim":
        official_x_status_url(self.client_id, self.source_url)
        if self.attempt > self.max_attempts:
            raise ValueError("grok_qa_attempt_invalid")
        staged_values = (
            self.staged_result,
            self.staged_verdict_sha256,
            self.staged_prompt_version,
        )
        if any(value is not None for value in staged_values) and not all(
            value is not None for value in staged_values
        ):
            raise ValueError("grok_qa_staged_result_incomplete")
        if self.provider_call_required != (self.staged_result is None):
            raise ValueError("grok_qa_provider_call_state_invalid")
        if (
            not len(_PNG_SIGNATURE) < len(self.image_png) <= MAX_INLINE_PNG_BYTES
            or not self.image_png.startswith(_PNG_SIGNATURE)
            or hashlib.sha256(self.image_png).hexdigest() != self.image_sha256
        ):
            raise ValueError("grok_qa_image_invalid")
        if self.staged_result is not None:
            self.staged_result.verdict.validate_source_boundary(self.source_url)
            if not all(
                provider_x_citation_matches(
                    self.client_id,
                    self.source_url,
                    citation,
                )
                for citation in self.staged_result.x_search_citations
            ):
                raise ValueError("grok_qa_staged_citation_mismatch")
            if self.staged_result.input_sha256 != self.input_sha256:
                raise ValueError("grok_qa_staged_input_mismatch")
        return self

    @property
    def input_sha256(self) -> str:
        subject = {
            "schema": "coineasy.grok_qa.review_input.v1",
            "content_item_id": self.content_item_id,
            "content_version_id": self.content_version_id,
            "client_id": self.client_id,
            "content_kind": self.content_kind,
            "title": self.title,
            "source_url": self.source_url,
            "source_published_at": self.source_published_at.isoformat(),
            "review_text": self.review_text,
            "image_sha256": self.image_sha256,
        }
        return hashlib.sha256(_canonical_json(subject)).hexdigest()


class GrokQaDeliveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: Literal[True]
    duplicate: bool
    delivery_status: Literal["sent", "duplicate"]

    @model_validator(mode="after")
    def validate_delivery(self) -> "GrokQaDeliveryResult":
        if self.duplicate != (self.delivery_status == "duplicate"):
            raise ValueError("grok_qa_delivery_result_invalid")
        return self


def verdict_payload_sha256(verdict: GrokQaVerdict) -> str:
    # Stable local hint only. The broker must stage the verdict and use the
    # database-returned hash as the authoritative delivery fence.
    return hashlib.sha256(
        json.dumps(
            verdict.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(", ", ": "),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "GROK_QA_CLIENTS",
    "GROK_QA_VERDICT_JSON_SCHEMA",
    "MAX_INLINE_PNG_BYTES",
    "OFFICIAL_X_HANDLES",
    "GrokQaCheck",
    "GrokQaDeliveryResult",
    "GrokQaFactCheck",
    "GrokQaIssue",
    "GrokQaModelResult",
    "GrokQaVerdict",
    "GrokQaWorkClaim",
    "official_x_status_url",
    "provider_x_citation_matches",
    "verdict_payload_sha256",
]
