"""Compose sanitized owner records into the complete Squid shadow inbox.

This module is deliberately a pure projection.  It receives dependency-
injected Pydantic records, reads no clock or environment, and performs no
filesystem, network, database, provider, Telegram, Railway, or publication
operation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    GtmDomain,
    GtmInboxPage,
    GtmLineage,
    GtmNextAction,
    GtmOperatorItem,
    GtmPriority,
    GtmStatus,
    UnobservedDetails,
    _utc_seconds,
    build_gtm_inbox,
    validate_squid_shadow_page,
)
from .sources import (
    AuthorizedSanitizedRailwayOpsRecord,
    SanitizedXQaOwnerProjection,
    TelegramOwnerProjection,
    project_squid_railway_ops,
    project_squid_x_qa,
    project_squid_x_qa_records,
    project_telegram_triage,
)


SourceUnavailableReasonCode = Literal[
    "sanitized_source_missing",
    "source_access_denied",
    "source_stale",
]


class _SourceState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    availability: Literal["available", "unavailable"]
    observed_at: datetime
    last_observed_at: Optional[datetime] = None
    unavailable_reason_code: Optional[SourceUnavailableReasonCode] = None

    @field_validator("observed_at", "last_observed_at")
    @classmethod
    def validate_timestamp(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return _utc_seconds(value, "gtm_source_bundle_time_invalid")

    def _validate_availability(self, has_records: bool) -> None:
        if self.availability == "available":
            if (
                not has_records
                or self.last_observed_at is not None
                or self.unavailable_reason_code is not None
            ):
                raise ValueError("gtm_source_bundle_available_state_invalid")
            return
        if (
            has_records
            or self.unavailable_reason_code is None
            or (
                self.last_observed_at is not None
                and self.last_observed_at > self.observed_at
            )
        ):
            raise ValueError("gtm_source_bundle_unavailable_state_invalid")


class SquidOpsSourceState(_SourceState):
    schema_version: Literal["coineasy-squid-ops-source-state@1"] = (
        "coineasy-squid-ops-source-state@1"
    )
    source_domain: Literal[GtmDomain.OPS] = GtmDomain.OPS
    records: tuple[AuthorizedSanitizedRailwayOpsRecord, ...] = Field(
        default=(),
        max_length=16,
    )

    @model_validator(mode="after")
    def validate_state(self) -> "SquidOpsSourceState":
        self._validate_availability(bool(self.records))
        if any(
            record.receipt.observed_at > self.observed_at
            for record in self.records
        ):
            raise ValueError("gtm_source_bundle_ops_observation_mismatch")
        service_names = [record.receipt.service_name for record in self.records]
        if len(service_names) != len(set(service_names)):
            raise ValueError("gtm_source_bundle_ops_duplicate")
        return self


class SquidTelegramSourceState(_SourceState):
    schema_version: Literal["coineasy-squid-telegram-source-state@1"] = (
        "coineasy-squid-telegram-source-state@1"
    )
    source_domain: Literal[GtmDomain.TELEGRAM_TRIAGE] = (
        GtmDomain.TELEGRAM_TRIAGE
    )
    records: tuple[TelegramOwnerProjection, ...] = Field(
        default=(),
        max_length=16,
    )

    @model_validator(mode="after")
    def validate_state(self) -> "SquidTelegramSourceState":
        self._validate_availability(bool(self.records))
        if any(record.observed_at > self.observed_at for record in self.records):
            raise ValueError("gtm_source_bundle_telegram_observation_mismatch")
        digests = [record.question_hmac_sha256 for record in self.records]
        if len(digests) != len(set(digests)):
            raise ValueError("gtm_source_bundle_telegram_duplicate")
        return self


class SquidXQaSourceState(_SourceState):
    schema_version: Literal["coineasy-squid-x-qa-source-state@1"] = (
        "coineasy-squid-x-qa-source-state@1"
    )
    source_domain: Literal[GtmDomain.X_NARRATIVE_QA] = (
        GtmDomain.X_NARRATIVE_QA
    )
    records: tuple[SanitizedXQaOwnerProjection, ...] = Field(
        default=(),
        max_length=16,
    )

    @model_validator(mode="after")
    def validate_state(self) -> "SquidXQaSourceState":
        self._validate_availability(bool(self.records))
        if any(record.observed_at > self.observed_at for record in self.records):
            raise ValueError("gtm_source_bundle_x_qa_observation_mismatch")
        return self


class SquidGtmSourceBundle(BaseModel):
    """Complete, sanitized, provider-disconnected source envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coineasy-squid-gtm-source-bundle@1"] = (
        "coineasy-squid-gtm-source-bundle@1"
    )
    client_id: Literal["squid"] = "squid"
    mode: Literal["sanitized_owner_projection"] = "sanitized_owner_projection"
    generated_at: datetime
    read_only_projection: Literal[True] = True
    external_calls: Literal[False] = False
    database_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    automatic_publication: Literal[False] = False
    ops: SquidOpsSourceState
    telegram_triage: SquidTelegramSourceState
    x_narrative_qa: SquidXQaSourceState

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "gtm_source_bundle_generated_at_invalid")

    @field_validator(
        "read_only_projection",
        "external_calls",
        "database_calls",
        "provider_calls",
        "publication_calls",
        "automatic_publication",
        mode="before",
    )
    @classmethod
    def validate_authority_literal_type(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("gtm_source_bundle_authority_type_invalid")
        return value

    @model_validator(mode="after")
    def validate_snapshot_times(self) -> "SquidGtmSourceBundle":
        if any(
            state.observed_at > self.generated_at
            for state in (self.ops, self.telegram_triage, self.x_narrative_qa)
        ):
            raise ValueError("gtm_source_bundle_observation_after_generation")
        return self


def _telegram_unobserved_item(
    state: SquidTelegramSourceState,
) -> GtmOperatorItem:
    assert state.availability == "unavailable"
    assert state.unavailable_reason_code is not None
    ref = "telegram:squid:unobserved"
    return GtmOperatorItem(
        ref=ref,
        domain=GtmDomain.TELEGRAM_TRIAGE,
        event_type="telegram.triage.unobserved",
        client_id="squid",
        observed_at=state.observed_at,
        status=GtmStatus.UNOBSERVED,
        priority=GtmPriority.NORMAL,
        title_ko="Telegram 커뮤니티 질문 미관측",
        summary_ko=(
            "안전하게 비식별화된 커뮤니티 질문 원천을 "
            "현재 확인할 수 없습니다."
        ),
        evidence=(),
        lineage=GtmLineage(correlation_ref=ref),
        next_action=GtmNextAction(code="verify_source", human_required=True),
        details=UnobservedDetails(
            source_domain=GtmDomain.TELEGRAM_TRIAGE,
            reason_code=state.unavailable_reason_code,
            last_observed_at=state.last_observed_at,
            observed_count=None,
        ),
    )


def build_squid_gtm_projection(bundle: SquidGtmSourceBundle) -> GtmInboxPage:
    """Project all three explicit source states into one validated page."""

    if not isinstance(bundle, SquidGtmSourceBundle):
        raise TypeError("gtm_source_bundle_invalid")

    if bundle.ops.availability == "available":
        ops = tuple(
            project_squid_railway_ops(
                record,
                observed_at=record.receipt.observed_at,
            )
            for record in bundle.ops.records
        )
    else:
        ops = (
            project_squid_railway_ops(
                None,
                observed_at=bundle.ops.observed_at,
                last_observed_at=bundle.ops.last_observed_at,
                missing_reason_code=bundle.ops.unavailable_reason_code,
            ),
        )

    if bundle.telegram_triage.availability == "available":
        telegram = tuple(
            project_telegram_triage(record)
            for record in bundle.telegram_triage.records
        )
    else:
        telegram = (_telegram_unobserved_item(bundle.telegram_triage),)

    if bundle.x_narrative_qa.availability == "available":
        x_qa = project_squid_x_qa_records(bundle.x_narrative_qa.records)
    else:
        assert bundle.x_narrative_qa.unavailable_reason_code is not None
        x_qa = (
            project_squid_x_qa(
                None,
                observed_at=bundle.x_narrative_qa.observed_at,
                last_observed_at=bundle.x_narrative_qa.last_observed_at,
                unavailable_reason_code=(
                    bundle.x_narrative_qa.unavailable_reason_code
                ),
            ),
        )

    page = build_gtm_inbox(
        (*ops, *telegram, *x_qa),
        generated_at=bundle.generated_at,
    )
    return validate_squid_shadow_page(page)


def source_bundle_json_schema() -> dict[str, object]:
    return SquidGtmSourceBundle.model_json_schema()


__all__ = [
    "SquidGtmSourceBundle",
    "SquidOpsSourceState",
    "SquidTelegramSourceState",
    "SquidXQaSourceState",
    "build_squid_gtm_projection",
    "source_bundle_json_schema",
]
