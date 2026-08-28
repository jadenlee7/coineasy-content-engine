"""Pure Squid ops projection from an authorized, sanitized owner receipt.

This module intentionally has no source reader.  It accepts an already
authorized record as a dependency and only validates and projects that record.
It does not read the environment, open files, run commands, access Railway,
query a database, inspect logs, make network calls, or expose a mutation method.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import (
    GtmDomain,
    GtmEvidence,
    GtmEvidenceKind,
    GtmLineage,
    GtmNextAction,
    GtmOperatorItem,
    GtmPriority,
    GtmStatus,
    OpsDetails,
    UnobservedDetails,
)

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{1,79}$")
_MISSING_REASON_CODES = (
    "sanitized_source_missing",
    "source_access_denied",
    "source_stale",
)
MissingReasonCode = Literal[
    "sanitized_source_missing",
    "source_access_denied",
    "source_stale",
]


def _utc_seconds(value: datetime, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None or value.microsecond != 0:
        raise ValueError(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError(code) from exc


class SanitizedRailwayRuntimeReceipt(BaseModel):
    """Closed, secret-free runtime facts emitted by the authorized owner.

    The canonical digest of this object binds the exact deployment SHA,
    expected SHA, runtime state, schedule window, and bounded failure evidence.
    It is an internal-consistency receipt; it does not independently prove that
    Railway currently has the represented state.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["coineasy-sanitized-railway-runtime@1"]
    client_id: Literal["squid"]
    service_name: str = Field(min_length=2, max_length=80)
    observed_at: datetime
    deployment_status: Literal[
        "running", "success", "building", "failed", "crashed"
    ]
    deployed_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    expected_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    runtime_status: Literal["healthy", "degraded", "failed", "unobserved"]
    schedule_status: Literal[
        "on_time", "late", "missed", "not_scheduled", "unobserved"
    ]
    last_tick_at: Optional[datetime] = None
    next_tick_at: Optional[datetime] = None
    schedule_interval_seconds: Optional[int] = Field(
        default=None,
        ge=60,
        le=604_800,
    )
    schedule_grace_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        le=86_400,
    )
    failure_count: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    failure_codes: tuple[str, ...] = Field(default=(), max_length=8)
    change_detected: bool

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        normalized = value.strip()
        if not _CODE_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_ops_source_service_invalid")
        return normalized

    @field_validator("observed_at", "last_tick_at", "next_tick_at")
    @classmethod
    def validate_timestamp(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return _utc_seconds(value, "gtm_ops_source_time_invalid")

    @field_validator("failure_codes")
    @classmethod
    def validate_failure_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if (
            values != tuple(sorted(values))
            or len(values) != len(set(values))
            or any(not _CODE_PATTERN.fullmatch(value) for value in values)
        ):
            raise ValueError("gtm_ops_source_failure_codes_invalid")
        return values

    @model_validator(mode="after")
    def validate_ops_contract(self) -> "SanitizedRailwayRuntimeReceipt":
        # Reuse the public detail contract so source and inbox schedule/state
        # semantics cannot drift.  The placeholder hash is not emitted.
        OpsDetails(
            service_name=self.service_name,
            deployment_status=self.deployment_status,
            deployed_sha=self.deployed_sha,
            expected_sha=self.expected_sha,
            sha_matches=self.deployed_sha == self.expected_sha,
            runtime_status=self.runtime_status,
            schedule_status=self.schedule_status,
            last_tick_at=self.last_tick_at,
            next_tick_at=self.next_tick_at,
            schedule_interval_seconds=self.schedule_interval_seconds,
            schedule_grace_seconds=self.schedule_grace_seconds,
            failure_count=self.failure_count,
            failure_codes=self.failure_codes,
            source_receipt_sha256="0" * 64,
            change_detected=self.change_detected,
            raw_logs_included=False,
            mutation_capability=False,
        )
        return self

    @property
    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class AuthorizedSanitizedRailwayOpsRecord(BaseModel):
    """Pre-authorized envelope accepted by the pure projection boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["coineasy-authorized-railway-ops@1"]
    source_system: Literal["railway"]
    owner_projection: Literal["sanitized_runtime_owner"]
    authorization_scope: Literal["squid:ops:read_only"]
    sanitized: Literal[True]
    read_only: Literal[True]
    raw_logs_included: Literal[False]
    environment_values_included: Literal[False]
    provider_payload_included: Literal[False]
    mutation_capability: Literal[False]
    receipt: SanitizedRailwayRuntimeReceipt
    source_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_receipt_binding(self) -> "AuthorizedSanitizedRailwayOpsRecord":
        if self.source_receipt_sha256 != self.receipt.canonical_sha256:
            raise ValueError("gtm_ops_source_receipt_sha256_mismatch")
        return self


def _observed_status(
    details: OpsDetails,
) -> tuple[GtmStatus, GtmPriority, GtmNextAction, str, str]:
    critical = (
        details.deployment_status in {"failed", "crashed"}
        or details.runtime_status == "failed"
    )
    needs_attention = (
        details.deployment_status == "building"
        or details.runtime_status in {"degraded", "unobserved"}
        or details.schedule_status in {"late", "missed", "unobserved"}
        or details.sha_matches is False
        or details.change_detected
    )
    if critical:
        return (
            GtmStatus.BLOCKED,
            GtmPriority.HIGH,
            GtmNextAction(code="investigate", human_required=True),
            "Railway 운영 장애 확인 필요",
            (
                "정제된 운영 receipt에서 실패 상태가 확인돼 "
                "운영자 조사가 필요합니다."
            ),
        )
    if needs_attention:
        return (
            GtmStatus.NEEDS_REVIEW,
            GtmPriority.HIGH,
            GtmNextAction(code="investigate", human_required=True),
            "Railway 운영 상태 확인 필요",
            (
                "정제된 운영 receipt에서 사람이 확인할 "
                "상태 변화가 감지됐습니다."
            ),
        )
    return (
        GtmStatus.INFO,
        GtmPriority.NORMAL,
        GtmNextAction(code="no_action", human_required=False),
        "Railway 운영 상태 정상",
        "배포 SHA와 런타임 및 스케줄 receipt가 서로 일치합니다.",
    )


def _unobserved_item(
    *,
    observed_at: datetime,
    last_observed_at: Optional[datetime],
    reason_code: MissingReasonCode,
) -> GtmOperatorItem:
    normalized_observed_at = _utc_seconds(
        observed_at,
        "gtm_ops_source_observed_at_invalid",
    )
    normalized_last_observed_at = (
        _utc_seconds(last_observed_at, "gtm_ops_source_last_observed_at_invalid")
        if last_observed_at is not None
        else None
    )
    if (
        normalized_last_observed_at is not None
        and normalized_last_observed_at > normalized_observed_at
    ):
        raise ValueError("gtm_ops_source_last_observed_after_observation")
    ref = "ops:squid:railway-runtime:unobserved"
    return GtmOperatorItem(
        ref=ref,
        domain=GtmDomain.OPS,
        event_type="railway.workflow.unobserved",
        client_id="squid",
        observed_at=normalized_observed_at,
        status=GtmStatus.UNOBSERVED,
        priority=GtmPriority.HIGH,
        title_ko="Railway 운영 상태 미관측",
        summary_ko=(
            "승인된 정제 운영 receipt를 받지 못해 "
            "현재 상태를 확인할 수 없습니다."
        ),
        evidence=(),
        lineage=GtmLineage(correlation_ref=ref),
        next_action=GtmNextAction(code="verify_source", human_required=True),
        details=UnobservedDetails(
            source_domain=GtmDomain.OPS,
            reason_code=reason_code,
            last_observed_at=normalized_last_observed_at,
            observed_count=None,
        ),
    )


def project_squid_railway_ops(
    record: Optional[AuthorizedSanitizedRailwayOpsRecord],
    *,
    observed_at: datetime,
    last_observed_at: Optional[datetime] = None,
    missing_reason_code: MissingReasonCode = "sanitized_source_missing",
) -> GtmOperatorItem:
    """Project one injected owner record, or an explicit unobserved item.

    ``observed_at`` is caller-injected so this module never reads a clock.  For
    an observed record it must exactly equal the timestamp inside the signed
    receipt.  Missing input is represented as ``unobserved`` and never as a
    healthy state or a numeric zero.
    """

    if missing_reason_code not in _MISSING_REASON_CODES:
        raise ValueError("gtm_ops_source_missing_reason_invalid")
    if record is None:
        return _unobserved_item(
            observed_at=observed_at,
            last_observed_at=last_observed_at,
            reason_code=missing_reason_code,
        )
    if not isinstance(record, AuthorizedSanitizedRailwayOpsRecord):
        raise TypeError("gtm_ops_source_record_invalid")
    if last_observed_at is not None:
        raise ValueError("gtm_ops_source_last_observed_with_record")

    normalized_observed_at = _utc_seconds(
        observed_at,
        "gtm_ops_source_observed_at_invalid",
    )
    receipt = record.receipt
    if normalized_observed_at != receipt.observed_at:
        raise ValueError("gtm_ops_source_observation_binding_invalid")

    details = OpsDetails(
        service_name=receipt.service_name,
        deployment_status=receipt.deployment_status,
        deployed_sha=receipt.deployed_sha,
        expected_sha=receipt.expected_sha,
        sha_matches=receipt.deployed_sha == receipt.expected_sha,
        runtime_status=receipt.runtime_status,
        schedule_status=receipt.schedule_status,
        last_tick_at=receipt.last_tick_at,
        next_tick_at=receipt.next_tick_at,
        schedule_interval_seconds=receipt.schedule_interval_seconds,
        schedule_grace_seconds=receipt.schedule_grace_seconds,
        failure_count=receipt.failure_count,
        failure_codes=receipt.failure_codes,
        source_receipt_sha256=record.source_receipt_sha256,
        change_detected=receipt.change_detected,
        raw_logs_included=False,
        mutation_capability=False,
    )
    status, priority, next_action, title_ko, summary_ko = _observed_status(details)
    ref = (
        f"ops:squid:{receipt.service_name}:"
        f"{record.source_receipt_sha256}"
    )
    return GtmOperatorItem(
        ref=ref,
        domain=GtmDomain.OPS,
        event_type="railway.workflow.observed",
        client_id="squid",
        observed_at=receipt.observed_at,
        status=status,
        priority=priority,
        title_ko=title_ko,
        summary_ko=summary_ko,
        evidence=(
            GtmEvidence(
                kind=GtmEvidenceKind.RUNTIME_RECEIPT,
                uri=None,
                sha256=record.source_receipt_sha256,
                observed_at=receipt.observed_at,
            ),
        ),
        lineage=GtmLineage(correlation_ref=ref),
        next_action=next_action,
        details=details,
    )


__all__ = [
    "AuthorizedSanitizedRailwayOpsRecord",
    "MissingReasonCode",
    "SanitizedRailwayRuntimeReceipt",
    "project_squid_railway_ops",
]
