from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, UUID4, field_validator, model_validator

from .harmony import (
    CommunityDemandSignal,
    HarmonyClientId,
    HarmonyLane,
    HarmonySignal,
    HarmonySignalAttestation,
    HarmonySignalKind,
    OfficialSourceSignal,
    QuizLearningSignal,
    RecapMetricSignal,
    bind_harmony_signal_attestation,
)
from .models import _contains_secret, _safe_text


_CONNECTOR_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]{2,63}$")
_SHA256_PATTERN = r"^[a-f0-9]{64}$"


def _utc_seconds(value: datetime, code: str) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        raise ValueError(code)
    return value.astimezone(timezone.utc)


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
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


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


class PreviewHarmonyStage(str, Enum):
    PLAN = "plan"
    PRIVATE_CONTENT = "private_content"
    INDEPENDENT_QA = "independent_qa"
    OPERATOR_INBOX = "operator_inbox"
    RECAP = "recap"


_LANE_CAPABILITY = {
    HarmonyLane.QUIZ_BOT: "harmony_submit_quiz_bot",
    HarmonyLane.COMMUNITY_OPS: "harmony_submit_community_ops",
    HarmonyLane.CONTENT_SOURCE: "harmony_submit_content_source",
    HarmonyLane.RECAP: "harmony_submit_recap",
}

_KIND_LANE = {
    HarmonySignalKind.QUIZ_LEARNING: HarmonyLane.QUIZ_BOT,
    HarmonySignalKind.COMMUNITY_DEMAND: HarmonyLane.COMMUNITY_OPS,
    HarmonySignalKind.OFFICIAL_SOURCE: HarmonyLane.CONTENT_SOURCE,
    HarmonySignalKind.RECAP_METRIC: HarmonyLane.RECAP,
}


def preview_connector_receipt_sha256(payload: dict[str, object]) -> str:
    return _sha256({
        key: value for key, value in payload.items() if key != "payload_sha256"
    })


def bind_preview_connector_receipt(
    payload: dict[str, object],
) -> dict[str, object]:
    bound = dict(payload)
    bound.setdefault(
        "schema_version",
        "harmony-connector-attestation-receipt@1",
    )
    bound.setdefault("environment", "preview")
    bound.setdefault("verification_method", "jwt")
    bound.setdefault("raw_data_included", False)
    bound.setdefault("side_effects_performed", False)
    bound.setdefault("automatic_publication", False)
    bound["payload_sha256"] = preview_connector_receipt_sha256(bound)
    return bound


class PreviewHarmonyConnectorAttestationReceipt(BaseModel):
    """Database-recorded proof of a client-scoped Preview connector JWT.

    This receipt is not accepted inside caller signal JSON.  The Preview RPC
    constructs it from verified JWT claims and persists it append-only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-connector-attestation-receipt@1"] = (
        "harmony-connector-attestation-receipt@1"
    )
    receipt_id: UUID4
    workspace_id: UUID4
    client_id: HarmonyClientId
    signal_id: UUID4
    source_event_id: UUID4
    connector_id: str
    producer_principal_id: UUID4
    producer_release_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    signal_kind: HarmonySignalKind
    lane: HarmonyLane
    capability: Literal[
        "harmony_submit_quiz_bot",
        "harmony_submit_community_ops",
        "harmony_submit_content_source",
        "harmony_submit_recap",
    ]
    environment: Literal["preview"] = "preview"
    issuer: Literal["supabase"] = "supabase"
    audience: Literal["authenticated"] = "authenticated"
    verification_method: Literal["jwt"] = "jwt"
    verification_reference_sha256: str = Field(pattern=_SHA256_PATTERN)
    signal_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    upstream_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    verified_at: datetime
    expires_at: datetime
    raw_data_included: Literal[False] = False
    side_effects_performed: Literal[False] = False
    automatic_publication: Literal[False] = False
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("connector_id")
    @classmethod
    def validate_connector_id(cls, value: str) -> str:
        if not _CONNECTOR_PATTERN.fullmatch(value) or _contains_secret(value):
            raise ValueError("harmony_connector_id_invalid")
        return value

    @field_validator("verified_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "harmony_connector_receipt_time_invalid")

    @model_validator(mode="after")
    def validate_receipt(self) -> "PreviewHarmonyConnectorAttestationReceipt":
        if (
            self.lane != _KIND_LANE[self.signal_kind]
            or self.capability != _LANE_CAPABILITY[self.lane]
            or self.expires_at <= self.verified_at
            or self.expires_at - self.verified_at > timedelta(days=31)
        ):
            raise ValueError("harmony_connector_receipt_binding_invalid")
        expected = _sha256(self.model_dump(
            mode="python",
            exclude={"payload_sha256"},
        ))
        if self.payload_sha256 != expected:
            raise ValueError("harmony_connector_receipt_digest_invalid")
        return self

    def bind_signal(
        self,
        signal: HarmonySignal,
    ) -> HarmonySignalAttestation:
        if (
            signal.workspace_id != self.workspace_id
            or signal.client_id != self.client_id
            or signal.signal_id != self.signal_id
            or signal.source_event_id != self.source_event_id
            or signal.producer_principal_id != self.producer_principal_id
            or signal.producer_release_sha != self.producer_release_sha
            or signal.config_sha256 != self.config_sha256
            or signal.signal_kind != self.signal_kind
            or signal.lane != self.lane
            or signal.payload_sha256 != self.signal_payload_sha256
            or signal.upstream_receipt_sha256
            != self.upstream_receipt_sha256
            or signal.evidence_sha256 != self.evidence_sha256
            or self.verified_at < signal.observed_at
        ):
            raise ValueError("harmony_connector_signal_binding_invalid")
        return HarmonySignalAttestation.model_validate(
            bind_harmony_signal_attestation({
                "attestation_id": self.receipt_id,
                "workspace_id": self.workspace_id,
                "client_id": self.client_id,
                "signal_id": self.signal_id,
                "source_event_id": self.source_event_id,
                "signal_kind": self.signal_kind,
                "lane": self.lane,
                "producer_principal_id": self.producer_principal_id,
                "producer_release_sha": self.producer_release_sha,
                "config_sha256": self.config_sha256,
                "upstream_receipt_sha256": self.upstream_receipt_sha256,
                "evidence_sha256": self.evidence_sha256,
                "payload_sha256": self.signal_payload_sha256,
                "verification_method": "database_receipt",
                "verification_reference_sha256": self.payload_sha256,
                "verified_at": self.verified_at,
                "expires_at": min(self.expires_at, signal.expires_at),
            })
        )


class PreviewHarmonyRoundSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: UUID4
    signal_kind: HarmonySignalKind
    lane: HarmonyLane
    signal_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    connector_receipt_id: UUID4
    connector_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    upstream_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    official_content_version_id: UUID4 | None = None
    official_source_binding_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    content_factual_authority: bool

    @model_validator(mode="after")
    def validate_lane(self) -> "PreviewHarmonyRoundSignal":
        if self.lane != _KIND_LANE[self.signal_kind]:
            raise ValueError("harmony_preview_manifest_lane_invalid")
        expected_authority = self.lane == HarmonyLane.CONTENT_SOURCE
        if self.content_factual_authority != expected_authority:
            raise ValueError("harmony_preview_manifest_authority_invalid")
        if expected_authority:
            if (
                self.official_content_version_id is None
                or self.official_source_binding_sha256
                    != self.upstream_receipt_sha256
            ):
                raise ValueError("harmony_preview_manifest_source_binding_invalid")
        elif (
            self.official_content_version_id is not None
            or self.official_source_binding_sha256 is not None
        ):
            raise ValueError("harmony_preview_manifest_source_binding_invalid")
        return self


def preview_stage_receipt_sha256(payload: dict[str, object]) -> str:
    return _sha256({
        key: value for key, value in payload.items() if key != "receipt_sha256"
    })


def bind_preview_stage_receipt(
    payload: dict[str, object],
) -> dict[str, object]:
    bound = dict(payload)
    bound.setdefault("schema_version", "harmony-stage-receipt@1")
    bound.setdefault("synthetic", True)
    bound.setdefault("aggregate_only", True)
    bound.setdefault("external_calls", False)
    bound.setdefault("provider_calls", False)
    bound.setdefault("publication_calls", False)
    bound.setdefault("actual_cost_microusd", 0)
    bound.setdefault("automatic_publication", False)
    bound["receipt_sha256"] = preview_stage_receipt_sha256(bound)
    return bound


class PreviewHarmonyStageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-stage-receipt@1"] = (
        "harmony-stage-receipt@1"
    )
    receipt_id: UUID4
    workspace_id: UUID4
    client_id: Literal["squid"]
    round_id: UUID4
    plan_id: UUID4
    stage: PreviewHarmonyStage
    ordinal: int = Field(ge=1, le=5)
    actor: Literal[
        "grok_bot",
        "content_engine",
        "codex",
        "human_operator_inbox",
        "coineasy_recap",
    ]
    principal_id: UUID4
    producer_release_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    capability: Literal[
        "harmony_plan",
        "harmony_prepare_private_content",
        "harmony_independent_qa",
        "harmony_operator_inbox",
        "harmony_recap",
    ]
    binding_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    verdict: Literal["passed"] | None = None
    reviewer_principal_id: UUID4 | None = None
    previous_receipt_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    input_sha256: str = Field(pattern=_SHA256_PATTERN)
    output_sha256: str = Field(pattern=_SHA256_PATTERN)
    recorded_at: datetime
    synthetic: Literal[True] = True
    aggregate_only: Literal[True] = True
    external_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    actual_cost_microusd: Literal[0] = 0
    automatic_publication: Literal[False] = False
    receipt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "harmony_stage_receipt_time_invalid")

    @model_validator(mode="after")
    def validate_receipt(self) -> "PreviewHarmonyStageReceipt":
        expected_order = {
            PreviewHarmonyStage.PLAN: (1, "grok_bot", "harmony_plan"),
            PreviewHarmonyStage.PRIVATE_CONTENT: (
                2,
                "content_engine",
                "harmony_prepare_private_content",
            ),
            PreviewHarmonyStage.INDEPENDENT_QA: (
                3,
                "codex",
                "harmony_independent_qa",
            ),
            PreviewHarmonyStage.OPERATOR_INBOX: (
                4,
                "human_operator_inbox",
                "harmony_operator_inbox",
            ),
            PreviewHarmonyStage.RECAP: (
                5,
                "coineasy_recap",
                "harmony_recap",
            ),
        }
        if (
            self.ordinal,
            self.actor,
            self.capability,
        ) != expected_order[self.stage]:
            raise ValueError("harmony_stage_receipt_role_invalid")
        is_qa = self.stage == PreviewHarmonyStage.INDEPENDENT_QA
        if is_qa != (self.verdict == "passed") or is_qa != (
            self.reviewer_principal_id is not None
        ):
            raise ValueError("harmony_stage_receipt_qa_verdict_invalid")
        if is_qa and self.reviewer_principal_id != self.principal_id:
            raise ValueError("harmony_stage_receipt_qa_principal_invalid")
        if (self.ordinal == 1) != (self.previous_receipt_sha256 is None):
            raise ValueError("harmony_stage_receipt_chain_invalid")
        expected = _sha256(self.model_dump(
            mode="python",
            exclude={"receipt_sha256"},
        ))
        if self.receipt_sha256 != expected:
            raise ValueError("harmony_stage_receipt_digest_invalid")
        return self


class PreviewHarmonyOperatorInboxItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-operator-inbox@1"] = (
        "harmony-operator-inbox@1"
    )
    inbox_id: UUID4
    workspace_id: UUID4
    client_id: Literal["squid"]
    round_id: UUID4
    plan_id: UUID4
    status: Literal["pending"] = "pending"
    stage_receipt_id: UUID4
    scope_sha256: str = Field(pattern=_SHA256_PATTERN)
    qa_receipt_id: UUID4
    qa_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    qa_output_sha256: str = Field(pattern=_SHA256_PATTERN)
    created_at: datetime
    operator_decision_recorded: Literal[False] = False
    external_delivery_attempted: Literal[False] = False
    automatic_publication: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "harmony_operator_inbox_time_invalid")


class PreviewHarmonyCollaborationRound(BaseModel):
    """Typed result of the one-client, no-I/O Preview vertical slice."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-collaboration-round@1"] = (
        "harmony-collaboration-round@1"
    )
    workspace_id: UUID4
    client_id: Literal["squid"]
    round_id: UUID4
    plan_id: UUID4
    input_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    signal_manifest: tuple[PreviewHarmonyRoundSignal, ...] = Field(
        min_length=4,
        max_length=4,
    )
    connector_receipts: tuple[
        PreviewHarmonyConnectorAttestationReceipt,
        ...,
    ] = Field(min_length=4, max_length=4)
    stage_receipts: tuple[PreviewHarmonyStageReceipt, ...] = Field(
        min_length=5,
        max_length=5,
    )
    operator_inbox: PreviewHarmonyOperatorInboxItem
    status: Literal["operator_review_pending"] = "operator_review_pending"
    synthetic: Literal[True] = True
    aggregate_only: Literal[True] = True
    private_content_only: Literal[True] = True
    operator_decision_recorded: Literal[False] = False
    external_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    actual_cost_microusd: Literal[0] = 0
    automatic_publication: Literal[False] = False
    round_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_round(self) -> "PreviewHarmonyCollaborationRound":
        manifest = tuple(sorted(
            self.signal_manifest,
            key=lambda item: item.lane.value,
        ))
        if self.signal_manifest != manifest:
            raise ValueError("harmony_preview_manifest_order_invalid")
        lanes = tuple(item.lane for item in manifest)
        if set(lanes) != set(_LANE_CAPABILITY) or len(set(lanes)) != 4:
            raise ValueError("harmony_preview_manifest_complete_invalid")
        if len({item.signal_id for item in manifest}) != 4 or len({
            item.signal_payload_sha256 for item in manifest
        }) != 4:
            raise ValueError("harmony_preview_manifest_duplicate")
        receipts = tuple(sorted(
            self.connector_receipts,
            key=lambda item: item.lane.value,
        ))
        if self.connector_receipts != receipts:
            raise ValueError("harmony_preview_connector_order_invalid")
        for entry, receipt in zip(manifest, receipts):
            if (
                receipt.workspace_id != self.workspace_id
                or receipt.client_id != self.client_id
                or receipt.signal_id != entry.signal_id
                or receipt.signal_kind != entry.signal_kind
                or receipt.lane != entry.lane
                or receipt.signal_payload_sha256
                != entry.signal_payload_sha256
                or receipt.upstream_receipt_sha256
                != entry.upstream_receipt_sha256
                or receipt.receipt_id != entry.connector_receipt_id
                or receipt.payload_sha256
                != entry.connector_receipt_sha256
            ):
                raise ValueError("harmony_preview_connector_binding_invalid")
        if self.input_set_sha256 != _sha256([
            item.model_dump(mode="python") for item in manifest
        ]):
            raise ValueError("harmony_preview_input_set_invalid")

        expected_stages = tuple(PreviewHarmonyStage)
        if tuple(item.stage for item in self.stage_receipts) != expected_stages:
            raise ValueError("harmony_preview_stage_order_invalid")
        previous: str | None = None
        previous_output = self.input_set_sha256
        for ordinal, receipt in enumerate(self.stage_receipts, start=1):
            if (
                receipt.workspace_id != self.workspace_id
                or receipt.client_id != self.client_id
                or receipt.round_id != self.round_id
                or receipt.plan_id != self.plan_id
                or receipt.ordinal != ordinal
                or receipt.previous_receipt_sha256 != previous
                or receipt.input_sha256 != previous_output
            ):
                raise ValueError("harmony_preview_stage_binding_invalid")
            previous = receipt.receipt_sha256
            previous_output = receipt.output_sha256
        qa_stage = self.stage_receipts[2]
        if qa_stage.principal_id in {
            self.stage_receipts[0].principal_id,
            self.stage_receipts[1].principal_id,
        }:
            raise ValueError("harmony_preview_qa_separation_invalid")
        inbox_stage = self.stage_receipts[3]
        if (
            self.operator_inbox.workspace_id != self.workspace_id
            or self.operator_inbox.client_id != self.client_id
            or self.operator_inbox.round_id != self.round_id
            or self.operator_inbox.plan_id != self.plan_id
            or self.operator_inbox.stage_receipt_id != inbox_stage.receipt_id
            or self.operator_inbox.scope_sha256 != inbox_stage.output_sha256
            or self.operator_inbox.qa_receipt_id != qa_stage.receipt_id
            or self.operator_inbox.qa_receipt_sha256
            != qa_stage.receipt_sha256
            or self.operator_inbox.qa_output_sha256 != qa_stage.output_sha256
        ):
            raise ValueError("harmony_preview_operator_inbox_binding_invalid")
        expected = _sha256(self.model_dump(
            mode="python",
            exclude={"round_sha256"},
        ))
        if self.round_sha256 != expected:
            raise ValueError("harmony_preview_round_digest_invalid")
        return self

    def as_payload(self) -> dict[str, object]:
        return _json_value(self.model_dump(mode="python"))  # type: ignore[return-value]


def preview_collaboration_round_sha256(payload: dict[str, object]) -> str:
    return _sha256({
        key: value for key, value in payload.items() if key != "round_sha256"
    })


def bind_preview_collaboration_round(
    payload: dict[str, object],
) -> dict[str, object]:
    bound = dict(payload)
    bound.setdefault("schema_version", "harmony-collaboration-round@1")
    bound.setdefault("status", "operator_review_pending")
    bound.setdefault("synthetic", True)
    bound.setdefault("aggregate_only", True)
    bound.setdefault("private_content_only", True)
    bound.setdefault("operator_decision_recorded", False)
    bound.setdefault("external_calls", False)
    bound.setdefault("provider_calls", False)
    bound.setdefault("publication_calls", False)
    bound.setdefault("actual_cost_microusd", 0)
    bound.setdefault("automatic_publication", False)
    bound["round_sha256"] = preview_collaboration_round_sha256(bound)
    return bound


def validate_squid_preview_signal_set(
    signals: Sequence[HarmonySignal],
    receipts: Sequence[PreviewHarmonyConnectorAttestationReceipt],
    *,
    observed_at: datetime,
) -> tuple[HarmonySignalAttestation, ...]:
    """Validate the four inputs before a Preview adapter writes anything."""
    now = _utc_seconds(observed_at, "harmony_preview_observed_at_invalid")
    if (
        len(signals) != 4
        or len(receipts) != 4
        or any(signal.client_id != "squid" for signal in signals)
        or any(receipt.client_id != "squid" for receipt in receipts)
    ):
        raise ValueError("harmony_preview_squid_scope_invalid")
    by_lane = {signal.lane: signal for signal in signals}
    if len(by_lane) != 4 or set(by_lane) != set(_LANE_CAPABILITY):
        raise ValueError("harmony_preview_signal_set_incomplete")
    typed = (
        (HarmonyLane.QUIZ_BOT, QuizLearningSignal),
        (HarmonyLane.COMMUNITY_OPS, CommunityDemandSignal),
        (HarmonyLane.CONTENT_SOURCE, OfficialSourceSignal),
        (HarmonyLane.RECAP, RecapMetricSignal),
    )
    if any(not isinstance(by_lane[lane], expected) for lane, expected in typed):
        raise ValueError("harmony_preview_signal_kind_invalid")
    if any(
        not signal.observed_at <= now < signal.expires_at
        for signal in signals
    ):
        raise ValueError("harmony_preview_signal_time_invalid")
    receipt_by_lane = {receipt.lane: receipt for receipt in receipts}
    if len(receipt_by_lane) != 4:
        raise ValueError("harmony_preview_connector_set_incomplete")
    if any(
        not receipt.verified_at <= now < receipt.expires_at
        for receipt in receipts
    ):
        raise ValueError("harmony_preview_connector_time_invalid")
    return tuple(
        receipt_by_lane[lane].bind_signal(by_lane[lane])
        for lane in sorted(by_lane, key=lambda item: item.value)
    )


__all__ = [
    "PreviewHarmonyCollaborationRound",
    "PreviewHarmonyConnectorAttestationReceipt",
    "PreviewHarmonyOperatorInboxItem",
    "PreviewHarmonyRoundSignal",
    "PreviewHarmonyStage",
    "PreviewHarmonyStageReceipt",
    "bind_preview_collaboration_round",
    "bind_preview_connector_receipt",
    "bind_preview_stage_receipt",
    "preview_collaboration_round_sha256",
    "preview_connector_receipt_sha256",
    "preview_stage_receipt_sha256",
    "validate_squid_preview_signal_set",
]
