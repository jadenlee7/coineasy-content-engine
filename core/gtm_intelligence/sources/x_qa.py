"""Pure read-only projection from sanitized public-X and QA owner records.

This module deliberately has no source client. It does not read X, call Grok,
query a database, inspect environment variables, submit a verdict, or perform
any external action. The caller must first obtain an already sanitized,
allowlisted record from the authoritative owner projection.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Literal, Mapping, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    UUID4,
    field_validator,
    model_validator,
)

from ..models import (
    GtmDomain,
    GtmEvidence,
    GtmEvidenceKind,
    GtmLineage,
    GtmNextAction,
    GtmOperatorItem,
    GtmPriority,
    GtmStatus,
    NarrativeQaDetails,
    UnobservedDetails,
    _qa_receipt_sha256,
    _qa_receipt_subject_sha256,
    _safe_redacted_text,
)


_SQUID_OFFICIAL_X_HANDLE = "SquidRouter"
_X_STATUS_PATTERN = re.compile(
    r"^https://x\.com/([A-Za-z0-9_]{1,15})/status/([1-9][0-9]{5,24})$"
)
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{1,79}$")
_MISSING_REASON_CODES = (
    "sanitized_source_missing",
    "source_access_denied",
    "source_stale",
)

X_SIGNAL_KINDS = ("official_source", "competitor", "kol", "content_qa")
X_QA_VERDICTS = ("not_applicable", "pending", "pass", "warn", "block")
XQaMissingReasonCode = Literal[
    "sanitized_source_missing",
    "source_access_denied",
    "source_stale",
]


def _utc_seconds(value: datetime, code: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.microsecond != 0
    ):
        raise ValueError(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError(code) from exc


def _require_exact_literals(
    value: object,
    expected: Mapping[str, object],
) -> object:
    if isinstance(value, Mapping):
        for field_name, exact_value in expected.items():
            if field_name not in value:
                continue
            candidate = value[field_name]
            if type(candidate) is not type(exact_value) or candidate != exact_value:
                raise ValueError("gtm_x_qa_owner_authority_invalid")
    return value


class SanitizedXQaOwnerProjection(BaseModel):
    """Closed owner record accepted by the read-only Squid adapter.

    The boolean literals are upstream contract claims, not authentication or
    proof. This adapter checks their exact presence and internal bindings but
    cannot establish that the referenced owner objects still exist or remain
    current.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-sanitized-x-qa-owner-projection@1"]
    client_id: Literal["squid"]
    source_system: Literal["public_x_qa_owner_projection"]
    read_only_projection: Literal[True]
    sanitized_public_data_only: Literal[True]
    account_allowlisted: Literal[True]
    owner_projection_claimed_current: Literal[True]
    raw_post_text_included: Literal[False]
    private_data_included: Literal[False]
    mutation_capability: Literal[False]
    publication_capability: Literal[False]

    observed_at: datetime
    source_observed_at: datetime
    signal_kind: Literal[
        "official_source",
        "competitor",
        "kol",
        "content_qa",
    ]
    source_url: str = Field(min_length=1, max_length=2_048)
    source_account: str = Field(min_length=1, max_length=15)
    title_ko: str = Field(min_length=3, max_length=160)
    summary_ko: str = Field(min_length=5, max_length=600)
    claim_ko: str = Field(min_length=5, max_length=500)
    comparison_ko: Optional[str] = Field(default=None, min_length=5, max_length=500)
    confidence: Literal["high", "medium", "low"]

    work_order_id: Optional[UUID4] = None
    content_item_id: Optional[UUID4] = None
    content_version_id: Optional[UUID4] = None
    content_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    content_observed_at: Optional[datetime] = None
    banner_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    banner_observed_at: Optional[datetime] = None
    qa_receipt_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    qa_receipt_subject_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    qa_receipt_observed_at: Optional[datetime] = None
    qa_verdict: Literal[
        "not_applicable",
        "pending",
        "pass",
        "warn",
        "block",
    ] = "not_applicable"
    issue_codes: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="before")
    @classmethod
    def validate_exact_authority_literals(cls, value: object) -> object:
        return _require_exact_literals(
            value,
            {
                "client_id": "squid",
                "source_system": "public_x_qa_owner_projection",
                "read_only_projection": True,
                "sanitized_public_data_only": True,
                "account_allowlisted": True,
                "owner_projection_claimed_current": True,
                "raw_post_text_included": False,
                "private_data_included": False,
                "mutation_capability": False,
                "publication_capability": False,
            },
        )

    @field_validator(
        "observed_at",
        "source_observed_at",
        "content_observed_at",
        "banner_observed_at",
        "qa_receipt_observed_at",
    )
    @classmethod
    def validate_timestamp(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return _utc_seconds(value, "gtm_x_qa_owner_time_invalid")

    @field_validator("title_ko", "summary_ko", "claim_ko", "comparison_ko")
    @classmethod
    def validate_safe_text(cls, value: Optional[str], info: object) -> Optional[str]:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "text")
        bounds = {
            "title_ko": (3, 160),
            "summary_ko": (5, 600),
            "claim_ko": (5, 500),
            "comparison_ko": (5, 500),
        }
        minimum, maximum = bounds[field_name]
        return _safe_redacted_text(
            value,
            "gtm_x_qa_owner_text_invalid",
            minimum,
            maximum,
        )

    @field_validator("issue_codes")
    @classmethod
    def validate_issue_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if (
            values != tuple(sorted(values))
            or len(values) != len(set(values))
            or any(_CODE_PATTERN.fullmatch(value) is None for value in values)
        ):
            raise ValueError("gtm_x_qa_owner_issue_codes_invalid")
        return values

    @model_validator(mode="after")
    def validate_owner_bindings(self) -> "SanitizedXQaOwnerProjection":
        source_match = _X_STATUS_PATTERN.fullmatch(self.source_url)
        if source_match is None or source_match.group(1) != self.source_account:
            raise ValueError("gtm_x_qa_owner_source_binding_invalid")

        if (
            self.signal_kind in {"official_source", "content_qa"}
            and (
                self.source_account != _SQUID_OFFICIAL_X_HANDLE
                or source_match.group(1) != _SQUID_OFFICIAL_X_HANDLE
            )
        ):
            raise ValueError("gtm_x_qa_owner_official_account_invalid")

        evidence_times = (
            self.source_observed_at,
            self.content_observed_at,
            self.banner_observed_at,
            self.qa_receipt_observed_at,
        )
        if any(
            timestamp is not None and timestamp > self.observed_at
            for timestamp in evidence_times
        ):
            raise ValueError("gtm_x_qa_owner_evidence_after_snapshot")

        is_content_qa = self.signal_kind == "content_qa"
        qa_bound_values = (
            self.content_item_id,
            self.content_version_id,
            self.content_sha256,
            self.content_observed_at,
            self.banner_sha256,
            self.banner_observed_at,
            self.qa_receipt_sha256,
            self.qa_receipt_subject_sha256,
            self.qa_receipt_observed_at,
        )
        if not is_content_qa:
            if (
                any(value is not None for value in qa_bound_values)
                or self.qa_verdict != "not_applicable"
                or self.issue_codes
            ):
                raise ValueError("gtm_x_qa_owner_non_qa_binding_invalid")
            return self

        if (
            self.content_item_id is None
            or self.content_version_id is None
            or self.content_sha256 is None
            or self.content_observed_at is None
            or self.qa_verdict == "not_applicable"
        ):
            raise ValueError("gtm_x_qa_owner_content_binding_missing")
        if (self.banner_sha256 is None) != (self.banner_observed_at is None):
            raise ValueError("gtm_x_qa_owner_banner_binding_invalid")

        has_receipt = self.qa_receipt_sha256 is not None
        has_receipt_subject = self.qa_receipt_subject_sha256 is not None
        if has_receipt != (self.qa_receipt_observed_at is not None):
            raise ValueError("gtm_x_qa_owner_receipt_binding_invalid")
        if self.qa_verdict == "pending" and (has_receipt or has_receipt_subject):
            raise ValueError("gtm_x_qa_owner_pending_receipt_invalid")
        if self.qa_verdict != "pending" and (
            not has_receipt or not has_receipt_subject
        ):
            raise ValueError("gtm_x_qa_owner_receipt_missing")
        if self.qa_verdict != "pending":
            assert self.qa_receipt_observed_at is not None
            assert self.qa_receipt_subject_sha256 is not None
            evidence_inputs = [self.source_observed_at, self.content_observed_at]
            if self.banner_observed_at is not None:
                evidence_inputs.append(self.banner_observed_at)
            if self.qa_receipt_observed_at < max(evidence_inputs):
                raise ValueError("gtm_x_qa_owner_receipt_chronology_invalid")
            expected_subject = _qa_receipt_subject_sha256(
                source_url=self.source_url,
                content_item_id=self.content_item_id,
                content_version_id=self.content_version_id,
                content_sha256=self.content_sha256,
                banner_sha256=self.banner_sha256,
                qa_verdict=self.qa_verdict,
                issue_codes=self.issue_codes,
            )
            if self.qa_receipt_subject_sha256 != expected_subject:
                raise ValueError("gtm_x_qa_owner_receipt_subject_mismatch")
            if self.qa_receipt_sha256 != _qa_receipt_sha256(
                subject_sha256=expected_subject,
            ):
                raise ValueError("gtm_x_qa_owner_receipt_binding_mismatch")
        if self.qa_verdict == "pass" and self.issue_codes:
            raise ValueError("gtm_x_qa_owner_pass_with_issues")
        if self.qa_verdict in {"warn", "block"} and not self.issue_codes:
            raise ValueError("gtm_x_qa_owner_issue_missing")
        return self


def _unobserved_item(
    *,
    observed_at: datetime,
    last_observed_at: Optional[datetime],
    reason_code: XQaMissingReasonCode,
) -> GtmOperatorItem:
    observed = _utc_seconds(observed_at, "gtm_x_qa_unobserved_time_invalid")
    last_observed = (
        None
        if last_observed_at is None
        else _utc_seconds(
            last_observed_at,
            "gtm_x_qa_unobserved_last_time_invalid",
        )
    )
    if last_observed is not None and last_observed > observed:
        raise ValueError("gtm_x_qa_unobserved_last_time_invalid")
    if reason_code not in _MISSING_REASON_CODES:
        raise ValueError("gtm_x_qa_unobserved_reason_invalid")

    ref = "xqa:squid:unobserved"
    return GtmOperatorItem(
        ref=ref,
        domain=GtmDomain.X_NARRATIVE_QA,
        event_type="x.qa.unobserved",
        client_id="squid",
        observed_at=observed,
        status=GtmStatus.UNOBSERVED,
        priority=GtmPriority.HIGH,
        title_ko="X 및 콘텐츠 QA 소스 미관측",
        summary_ko=(
            "안전하게 정리된 공개 X 및 QA 원천 정보를 "
            "현재 확인할 수 없습니다."
        ),
        evidence=(),
        lineage=GtmLineage(correlation_ref=ref),
        next_action=GtmNextAction(
            code="verify_source",
            human_required=True,
        ),
        details=UnobservedDetails(
            source_domain=GtmDomain.X_NARRATIVE_QA,
            reason_code=reason_code,
            last_observed_at=last_observed,
            observed_count=None,
        ),
    )


def _state_for_projection(
    projection: SanitizedXQaOwnerProjection,
) -> tuple[GtmStatus, GtmPriority, GtmNextAction]:
    if projection.signal_kind != "content_qa":
        if projection.confidence == "low":
            return (
                GtmStatus.NEEDS_REVIEW,
                GtmPriority.NORMAL,
                GtmNextAction(code="verify_source", human_required=True),
            )
        return (
            GtmStatus.INFO,
            GtmPriority.NORMAL,
            GtmNextAction(code="review", human_required=True),
        )

    state = {
        "pending": (
            GtmStatus.NEEDS_REVIEW,
            GtmPriority.NORMAL,
            GtmNextAction(code="review", human_required=True),
        ),
        "pass": (
            GtmStatus.INFO,
            GtmPriority.NORMAL,
            GtmNextAction(code="review", human_required=True),
        ),
        "warn": (
            GtmStatus.NEEDS_REVIEW,
            GtmPriority.HIGH,
            GtmNextAction(code="verify_source", human_required=True),
        ),
        "block": (
            GtmStatus.BLOCKED,
            GtmPriority.HIGH,
            GtmNextAction(code="investigate", human_required=True),
        ),
    }
    try:
        return state[projection.qa_verdict]
    except KeyError as exc:
        raise ValueError("gtm_x_qa_owner_verdict_invalid") from exc


def project_squid_x_qa(
    record: Optional[
        Union[SanitizedXQaOwnerProjection, Mapping[str, object]]
    ],
    *,
    observed_at: Optional[datetime] = None,
    last_observed_at: Optional[datetime] = None,
    unavailable_reason_code: XQaMissingReasonCode = "sanitized_source_missing",
) -> GtmOperatorItem:
    """Project one owner record, or an explicit unavailable observation.

    ``None`` never means an observed count of zero. It requires the caller's
    explicit observation timestamp and produces ``UnobservedDetails``.
    For an available record, ``observed_at`` may be omitted; if supplied, it
    must equal the owner projection timestamp exactly.
    """

    if record is None:
        if observed_at is None:
            raise ValueError("gtm_x_qa_unobserved_time_missing")
        return _unobserved_item(
            observed_at=observed_at,
            last_observed_at=last_observed_at,
            reason_code=unavailable_reason_code,
        )

    if (
        last_observed_at is not None
        or unavailable_reason_code != "sanitized_source_missing"
    ):
        raise ValueError("gtm_x_qa_unobserved_argument_drift")
    projection = (
        record
        if isinstance(record, SanitizedXQaOwnerProjection)
        else SanitizedXQaOwnerProjection.model_validate(record)
    )
    if observed_at is not None and (
        _utc_seconds(observed_at, "gtm_x_qa_owner_time_invalid")
        != projection.observed_at
    ):
        raise ValueError("gtm_x_qa_owner_observation_mismatch")

    source_match = _X_STATUS_PATTERN.fullmatch(projection.source_url)
    assert source_match is not None
    source_post_id = source_match.group(2)
    is_content_qa = projection.signal_kind == "content_qa"
    if is_content_qa:
        assert projection.content_version_id is not None
        ref = f"xqa:squid:content:{projection.content_version_id}"
        narrative_candidate_id = None
    else:
        ref = f"xqa:squid:{projection.signal_kind}:{source_post_id}"
        narrative_candidate_id = (
            f"xsignal:squid:{projection.signal_kind}:{source_post_id}"
        )

    evidence = [
        GtmEvidence(
            kind=GtmEvidenceKind.OFFICIAL_URL,
            uri=projection.source_url,
            observed_at=projection.source_observed_at,
        )
    ]
    if is_content_qa:
        assert projection.content_sha256 is not None
        assert projection.content_observed_at is not None
        evidence.append(GtmEvidence(
            kind=GtmEvidenceKind.CONTENT_HASH,
            sha256=projection.content_sha256,
            observed_at=projection.content_observed_at,
        ))
        if projection.banner_sha256 is not None:
            assert projection.banner_observed_at is not None
            evidence.append(GtmEvidence(
                kind=GtmEvidenceKind.BANNER_HASH,
                sha256=projection.banner_sha256,
                observed_at=projection.banner_observed_at,
            ))
        if projection.qa_receipt_sha256 is not None:
            assert projection.qa_receipt_observed_at is not None
            evidence.append(GtmEvidence(
                kind=GtmEvidenceKind.QA_RECEIPT,
                sha256=projection.qa_receipt_sha256,
                observed_at=projection.qa_receipt_observed_at,
            ))

    status, priority, next_action = _state_for_projection(projection)
    event_suffix = (
        projection.qa_verdict if is_content_qa else projection.signal_kind
    )
    event_type = (
        f"content.qa.{event_suffix}"
        if is_content_qa
        else f"x.narrative.{event_suffix}"
    )
    return GtmOperatorItem(
        ref=ref,
        domain=GtmDomain.X_NARRATIVE_QA,
        event_type=event_type,
        client_id="squid",
        observed_at=projection.observed_at,
        status=status,
        priority=priority,
        title_ko=projection.title_ko,
        summary_ko=projection.summary_ko,
        evidence=tuple(evidence),
        lineage=GtmLineage(
            correlation_ref=ref,
            work_order_id=projection.work_order_id,
            content_item_id=projection.content_item_id,
            content_version_id=projection.content_version_id,
            narrative_candidate_id=narrative_candidate_id,
        ),
        next_action=next_action,
        details=NarrativeQaDetails(
            signal_kind=projection.signal_kind,
            source_url=projection.source_url,
            source_account=projection.source_account,
            claim_ko=projection.claim_ko,
            comparison_ko=projection.comparison_ko,
            confidence=projection.confidence,
            content_sha256=projection.content_sha256,
            banner_sha256=projection.banner_sha256,
            qa_receipt_sha256=projection.qa_receipt_sha256,
            qa_receipt_subject_sha256=(
                projection.qa_receipt_subject_sha256
            ),
            qa_verdict=projection.qa_verdict,
            issue_codes=projection.issue_codes,
            publication_allowed=False,
        ),
    )


def project_squid_x_qa_records(
    records: Iterable[
        Union[SanitizedXQaOwnerProjection, Mapping[str, object]]
    ],
) -> tuple[GtmOperatorItem, ...]:
    """Project a bounded non-empty owner snapshot without changing its order."""

    if records is None or isinstance(records, (str, bytes, Mapping)):
        raise ValueError("gtm_x_qa_owner_records_invalid")
    try:
        iterator = iter(records)
    except TypeError as exc:
        raise ValueError("gtm_x_qa_owner_records_invalid") from exc
    projected_items: list[GtmOperatorItem] = []
    for index, record in enumerate(iterator):
        if index >= 50:
            raise ValueError("gtm_x_qa_owner_records_count_invalid")
        projected_items.append(project_squid_x_qa(record))
    projected = tuple(projected_items)
    if not projected:
        raise ValueError("gtm_x_qa_owner_records_count_invalid")
    refs = [item.ref for item in projected]
    if len(refs) != len(set(refs)):
        raise ValueError("gtm_x_qa_owner_records_duplicate")
    return projected


__all__ = [
    "SanitizedXQaOwnerProjection",
    "X_QA_VERDICTS",
    "X_SIGNAL_KINDS",
    "XQaMissingReasonCode",
    "project_squid_x_qa",
    "project_squid_x_qa_records",
]
