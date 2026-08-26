from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Sequence
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    UUID4,
    field_validator,
    model_validator,
)

from .harmony import (
    CommunityDemandSignal,
    HarmonyClientId,
    HarmonyInput,
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
_ATTESTATION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9._:-]{2,127}$")
_PREVIEW_BRANCH_PATTERN = re.compile(r"^[a-z0-9]{20}$")
_SHA256_PATTERN = r"^[a-f0-9]{64}$"


def _utc_seconds(value: datetime, code: str) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        raise ValueError(code)
    return value.astimezone(timezone.utc)


def _utc_timestamp(value: datetime, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(code)
    return value.astimezone(timezone.utc)


def _utc_microseconds_json(value: object, code: str) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(code) from exc
    if not isinstance(value, datetime):
        raise ValueError(code)
    normalized = _utc_timestamp(value, code)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uuid4(value: UUID | str, code: str) -> UUID:
    try:
        parsed = value if isinstance(value, UUID) else UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(code) from exc
    if parsed.version != 4:
        raise ValueError(code)
    return parsed


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


PreviewHarmonyConnectorCapability = Literal[
    "harmony_submit_quiz_bot",
    "harmony_submit_community_ops",
    "harmony_submit_content_source",
    "harmony_submit_recap",
]


def preview_connector_registration_sha256(
    payload: dict[str, object],
) -> str:
    """Hash the exact immutable registration binding used by Preview SQL.

    ``created_at`` is audit metadata, not part of the binding.  A registration
    cannot be renewed by changing it; a different expiry is a new binding.
    """
    return _sha256({
        "attestation_key_id": payload["attestation_key_id"],
        "branch_ref": payload["branch_ref"],
        "capability": payload["capability"],
        "client_id": payload["client_id"],
        "config_sha256": payload["config_sha256"],
        "connector_id": payload["connector_id"],
        "expires_at": _utc_microseconds_json(
            payload["expires_at"],
            "harmony_connector_registration_time_invalid",
        ),
        "lane": payload["lane"],
        "producer_principal_id": payload["producer_principal_id"],
        "producer_release_sha": payload["producer_release_sha"],
        "registration_id": payload["registration_id"],
        "schema_version": "harmony-connector-registration@1",
        "workspace_id": payload["workspace_id"],
    })


def bind_preview_connector_registration(
    payload: dict[str, object],
) -> dict[str, object]:
    bound = dict(payload)
    bound.setdefault("schema_version", "harmony-connector-registration@1")
    bound["registration_sha256"] = preview_connector_registration_sha256(bound)
    return bound


class PreviewHarmonyConnectorRegistration(BaseModel):
    """Non-secret, revocable identity binding for one Preview connector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-connector-registration@1"] = (
        "harmony-connector-registration@1"
    )
    branch_ref: str
    workspace_id: UUID4
    client_id: Literal["squid"]
    registration_id: UUID4
    lane: HarmonyLane
    capability: PreviewHarmonyConnectorCapability
    connector_id: str
    producer_principal_id: UUID4
    producer_release_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    attestation_key_id: str
    expires_at: datetime
    created_at: datetime
    registration_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("branch_ref")
    @classmethod
    def validate_branch_ref(cls, value: str) -> str:
        if not _PREVIEW_BRANCH_PATTERN.fullmatch(value):
            raise ValueError("harmony_connector_registration_branch_invalid")
        return value

    @field_validator("connector_id")
    @classmethod
    def validate_connector_id(cls, value: str) -> str:
        if not _CONNECTOR_PATTERN.fullmatch(value) or _contains_secret(value):
            raise ValueError("harmony_connector_registration_connector_invalid")
        return value

    @field_validator("attestation_key_id")
    @classmethod
    def validate_attestation_key_id(cls, value: str) -> str:
        if (
            not _ATTESTATION_KEY_PATTERN.fullmatch(value)
            or _contains_secret(value)
        ):
            raise ValueError("harmony_connector_registration_key_id_invalid")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "harmony_connector_registration_time_invalid")

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime) -> datetime:
        return _utc_timestamp(value, "harmony_connector_registration_time_invalid")

    @model_validator(mode="after")
    def validate_registration(self) -> "PreviewHarmonyConnectorRegistration":
        if (
            self.capability != _LANE_CAPABILITY[self.lane]
            or self.expires_at <= self.created_at
            or self.expires_at - self.created_at > timedelta(hours=2)
        ):
            raise ValueError("harmony_connector_registration_binding_invalid")
        expected = preview_connector_registration_sha256(
            self.model_dump(mode="python")
        )
        if self.registration_sha256 != expected:
            raise ValueError("harmony_connector_registration_digest_invalid")
        return self


def _validated_request_signal(
    target_signal: HarmonySignal | dict[str, object],
    *,
    workspace_id: UUID | str,
    client_id: str,
) -> tuple[HarmonySignal, str]:
    if isinstance(target_signal, BaseModel):
        signal_payload = target_signal.model_dump(mode="python")
    elif isinstance(target_signal, dict):
        signal_payload = dict(target_signal)
    else:
        raise ValueError("harmony_connector_request_signal_invalid")
    claimed_payload_sha256 = signal_payload.get("payload_sha256")
    signal_body = {
        key: value
        for key, value in signal_payload.items()
        if key != "payload_sha256"
    }
    computed_payload_sha256 = _sha256(signal_body)
    if claimed_payload_sha256 != computed_payload_sha256:
        raise ValueError("harmony_connector_request_signal_digest_invalid")
    try:
        typed_signal = HarmonyInput.model_validate({
            "schema_version": "agent-harmony-input@1",
            "workspace_id": signal_payload["workspace_id"],
            "signals": (signal_payload,),
        }).signals[0]
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("harmony_connector_request_signal_invalid") from exc
    expected_workspace_id = _uuid4(
        workspace_id,
        "harmony_connector_request_scope_invalid",
    )
    if (
        typed_signal.workspace_id != expected_workspace_id
        or typed_signal.client_id != client_id
    ):
        raise ValueError("harmony_connector_request_scope_invalid")
    return typed_signal, computed_payload_sha256


def preview_connector_request_sha256(
    *,
    workspace_id: UUID | str,
    client_id: str,
    registration_id: UUID | str,
    connector_receipt_id: UUID | str,
    target_signal: HarmonySignal | dict[str, object],
) -> str:
    """Reproduce SQL's request digest from the complete claimed signal JSON."""
    typed_signal, computed_payload_sha256 = _validated_request_signal(
        target_signal,
        workspace_id=workspace_id,
        client_id=client_id,
    )
    expected_workspace_id = _uuid4(
        workspace_id,
        "harmony_connector_request_scope_invalid",
    )
    expected_registration_id = _uuid4(
        registration_id,
        "harmony_connector_request_scope_invalid",
    )
    expected_connector_receipt_id = _uuid4(
        connector_receipt_id,
        "harmony_connector_request_scope_invalid",
    )
    return _sha256({
        "client_id": client_id,
        "connector_receipt_id": expected_connector_receipt_id,
        "domain": "coineasy:harmony:preview:connector-request:v1",
        "lane": typed_signal.lane,
        "producer_principal_id": typed_signal.producer_principal_id,
        "registration_id": expected_registration_id,
        "rpc": "public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)",
        "signal_id": typed_signal.signal_id,
        "signal_kind": typed_signal.signal_kind,
        "signal_payload_sha256": computed_payload_sha256,
        "source_event_id": typed_signal.source_event_id,
        "workspace_id": expected_workspace_id,
    })


def preview_connector_request_receipt_sha256(
    payload: dict[str, object],
) -> str:
    return _sha256({
        key: value for key, value in payload.items() if key != "payload_sha256"
    })


def bind_preview_connector_request_receipt(
    payload: dict[str, object],
) -> dict[str, object]:
    bound = dict(payload)
    bound.setdefault(
        "schema_version",
        "harmony-connector-request-receipt@1",
    )
    bound.setdefault("raw_content_included", False)
    bound.setdefault("external_calls", False)
    bound.setdefault("provider_calls", False)
    bound.setdefault("publication_calls", False)
    bound.setdefault("automatic_publication", False)
    bound["payload_sha256"] = preview_connector_request_receipt_sha256(bound)
    return bound


class PreviewHarmonyConnectorRequestReceipt(BaseModel):
    """Append-only proof of one admitted, nonce-bound connector request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-connector-request-receipt@1"] = (
        "harmony-connector-request-receipt@1"
    )
    request_receipt_id: UUID4
    workspace_id: UUID4
    client_id: Literal["squid"]
    registration_id: UUID4
    registration_sha256: str = Field(pattern=_SHA256_PATTERN)
    attestation_key_id: str
    request_nonce: UUID4
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    token_claims_sha256: str = Field(pattern=_SHA256_PATTERN)
    signal_id: UUID4
    signal_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    connector_receipt_id: UUID4
    connector_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    accepted_at: datetime
    expires_at: datetime
    raw_content_included: Literal[False] = False
    external_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    automatic_publication: Literal[False] = False
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("attestation_key_id")
    @classmethod
    def validate_attestation_key_id(cls, value: str) -> str:
        if (
            not _ATTESTATION_KEY_PATTERN.fullmatch(value)
            or _contains_secret(value)
        ):
            raise ValueError("harmony_connector_request_key_id_invalid")
        return value

    @field_validator("accepted_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "harmony_connector_request_time_invalid")

    @model_validator(mode="after")
    def validate_receipt(self) -> "PreviewHarmonyConnectorRequestReceipt":
        if (
            self.expires_at <= self.accepted_at
            or self.expires_at - self.accepted_at > timedelta(days=31)
        ):
            raise ValueError("harmony_connector_request_time_invalid")
        expected = preview_connector_request_receipt_sha256(
            self.model_dump(mode="python")
        )
        if self.payload_sha256 != expected:
            raise ValueError("harmony_connector_request_receipt_digest_invalid")
        return self

    def assert_nonce_identity(self, token_jti: UUID | str) -> None:
        """Require the dedicated request nonce to be the verified JWT jti."""
        if _uuid4(
            token_jti,
            "harmony_connector_request_nonce_invalid",
        ) != self.request_nonce:
            raise ValueError("harmony_connector_request_nonce_invalid")

    def bind_registration(
        self,
        registration: PreviewHarmonyConnectorRegistration,
    ) -> None:
        if (
            registration.workspace_id != self.workspace_id
            or registration.client_id != self.client_id
            or registration.registration_id != self.registration_id
            or registration.registration_sha256 != self.registration_sha256
            or registration.attestation_key_id != self.attestation_key_id
            or registration.created_at > self.accepted_at
            or self.expires_at > registration.expires_at
        ):
            raise ValueError("harmony_connector_request_registration_invalid")

    def bind_connector_receipt(
        self,
        registration: PreviewHarmonyConnectorRegistration,
        connector_receipt: "PreviewHarmonyConnectorAttestationReceipt",
        target_signal: HarmonySignal | dict[str, object],
    ) -> None:
        self.bind_registration(registration)
        if (
            connector_receipt.workspace_id != self.workspace_id
            or connector_receipt.client_id != self.client_id
            or connector_receipt.signal_id != self.signal_id
            or connector_receipt.signal_payload_sha256
            != self.signal_payload_sha256
            or connector_receipt.receipt_id != self.connector_receipt_id
            or connector_receipt.payload_sha256
            != self.connector_receipt_sha256
            or connector_receipt.verification_reference_sha256
            != self.token_claims_sha256
            or connector_receipt.connector_id != registration.connector_id
            or connector_receipt.lane != registration.lane
            or connector_receipt.capability != registration.capability
            or connector_receipt.producer_principal_id
            != registration.producer_principal_id
            or connector_receipt.producer_release_sha
            != registration.producer_release_sha
            or connector_receipt.config_sha256 != registration.config_sha256
            or connector_receipt.verified_at != self.accepted_at
            or connector_receipt.expires_at < self.expires_at
        ):
            raise ValueError("harmony_connector_request_receipt_binding_invalid")
        expected_request = preview_connector_request_sha256(
            workspace_id=self.workspace_id,
            client_id=self.client_id,
            registration_id=self.registration_id,
            connector_receipt_id=self.connector_receipt_id,
            target_signal=target_signal,
        )
        typed_signal, _ = _validated_request_signal(
            target_signal,
            workspace_id=self.workspace_id,
            client_id=self.client_id,
        )
        try:
            connector_receipt.bind_signal(typed_signal)
        except ValueError as exc:
            raise ValueError(
                "harmony_connector_request_receipt_binding_invalid"
            ) from exc
        if self.request_sha256 != expected_request:
            raise ValueError("harmony_connector_request_digest_invalid")


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


def preview_stage_operation_key_sha256(
    *,
    specialist_binding_sha256: str,
    workspace_id: UUID | str,
    client_id: str,
    plan_id: UUID | str,
    stage: PreviewHarmonyStage | str,
    input_sha256: str,
    output_sha256: str,
) -> str:
    """Return the stable logical operation key used by the Preview ledger."""
    return _sha256({
        "client_id": client_id,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "plan_id": plan_id,
        "schema_version": "harmony-stage-operation@1",
        "specialist_binding_sha256": specialist_binding_sha256,
        "stage": stage,
        "workspace_id": workspace_id,
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
    specialist_code: Literal[
        "squid_planner",
        "squid_private_content_producer",
        "squid_independent_qa",
        "coineasy_representative_inbox",
        "squid_recap",
    ]
    specialist_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    operation_key_sha256: str = Field(pattern=_SHA256_PATTERN)
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
            PreviewHarmonyStage.PLAN: (
                1, "grok_bot", "harmony_plan", "squid_planner",
            ),
            PreviewHarmonyStage.PRIVATE_CONTENT: (
                2,
                "content_engine",
                "harmony_prepare_private_content",
                "squid_private_content_producer",
            ),
            PreviewHarmonyStage.INDEPENDENT_QA: (
                3,
                "codex",
                "harmony_independent_qa",
                "squid_independent_qa",
            ),
            PreviewHarmonyStage.OPERATOR_INBOX: (
                4,
                "human_operator_inbox",
                "harmony_operator_inbox",
                "coineasy_representative_inbox",
            ),
            PreviewHarmonyStage.RECAP: (
                5,
                "coineasy_recap",
                "harmony_recap",
                "squid_recap",
            ),
        }
        if (
            self.ordinal,
            self.actor,
            self.capability,
            self.specialist_code,
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
        expected_operation_key = preview_stage_operation_key_sha256(
            specialist_binding_sha256=self.specialist_binding_sha256,
            workspace_id=self.workspace_id,
            client_id=self.client_id,
            plan_id=self.plan_id,
            stage=self.stage,
            input_sha256=self.input_sha256,
            output_sha256=self.output_sha256,
        )
        if self.operation_key_sha256 != expected_operation_key:
            raise ValueError("harmony_stage_operation_key_invalid")
        expected = _sha256(self.model_dump(
            mode="python",
            exclude={"receipt_sha256"},
        ))
        if self.receipt_sha256 != expected:
            raise ValueError("harmony_stage_receipt_digest_invalid")
        return self


PreviewHarmonyQaFindingCode = Literal[
    "automatic_publication_enabled",
    "external_call_detected",
    "factual_binding_failed",
    "private_boundary_failed",
]


class PreviewHarmonyQaCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    automatic_publication: StrictBool
    factual_binding: StrictBool
    no_external_calls: StrictBool
    private_only: StrictBool


class PreviewHarmonyIndependentQaEvidence(BaseModel):
    """Strict deterministic evidence kept beside, but outside, the receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-independent-qa-evidence@1"] = (
        "harmony-independent-qa-evidence@1"
    )
    reviewed_output_sha256: str = Field(pattern=_SHA256_PATTERN)
    criteria: PreviewHarmonyQaCriteria
    findings: tuple[PreviewHarmonyQaFindingCode, ...] = Field(
        min_length=1,
        max_length=4,
    )
    verdict: Literal["failed"] = "failed"
    verifier_version: Literal["harmony-deterministic-qa@1"] = (
        "harmony-deterministic-qa@1"
    )

    @model_validator(mode="after")
    def validate_findings(self) -> "PreviewHarmonyIndependentQaEvidence":
        expected = tuple(sorted((
            *(
                ("automatic_publication_enabled",)
                if self.criteria.automatic_publication else ()
            ),
            *(
                ("factual_binding_failed",)
                if not self.criteria.factual_binding else ()
            ),
            *(
                ("external_call_detected",)
                if not self.criteria.no_external_calls else ()
            ),
            *(
                ("private_boundary_failed",)
                if not self.criteria.private_only else ()
            ),
        )))
        if self.findings != expected:
            raise ValueError("harmony_qa_evidence_findings_invalid")
        return self


def preview_qa_evidence_sha256(
    evidence: PreviewHarmonyIndependentQaEvidence | dict[str, object],
) -> str:
    return _sha256(evidence)


def preview_qa_denial_receipt_sha256(payload: dict[str, object]) -> str:
    return _sha256({
        key: value for key, value in payload.items() if key != "payload_sha256"
    })


def bind_preview_qa_denial_receipt(
    payload: dict[str, object],
) -> dict[str, object]:
    bound = dict(payload)
    bound.setdefault("schema_version", "harmony-qa-denial-receipt@1")
    bound.setdefault("verifier_version", "harmony-deterministic-qa@1")
    bound.setdefault("verdict", "failed")
    bound.setdefault("aggregate_only", True)
    bound.setdefault("raw_content_included", False)
    bound.setdefault("external_calls", False)
    bound.setdefault("provider_calls", False)
    bound.setdefault("publication_calls", False)
    bound.setdefault("automatic_publication", False)
    bound["payload_sha256"] = preview_qa_denial_receipt_sha256(bound)
    return bound


class PreviewHarmonyQaDenialReceipt(BaseModel):
    """Append-only failed-QA receipt that cannot authorize an inbox stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-qa-denial-receipt@1"] = (
        "harmony-qa-denial-receipt@1"
    )
    denial_receipt_id: UUID4
    workspace_id: UUID4
    client_id: Literal["squid"]
    round_id: UUID4
    plan_id: UUID4
    private_content_receipt_id: UUID4
    denied_output_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewer_principal_id: UUID4
    reviewer_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    finding_codes: tuple[PreviewHarmonyQaFindingCode, ...] = Field(
        min_length=1,
        max_length=4,
    )
    verifier_version: Literal["harmony-deterministic-qa@1"] = (
        "harmony-deterministic-qa@1"
    )
    verdict: Literal["failed"] = "failed"
    aggregate_only: Literal[True] = True
    raw_content_included: Literal[False] = False
    external_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    automatic_publication: Literal[False] = False
    recorded_at: datetime
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "harmony_qa_denial_time_invalid")

    @field_validator("finding_codes")
    @classmethod
    def validate_finding_codes(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError("harmony_qa_denial_findings_invalid")
        return values

    @model_validator(mode="after")
    def validate_receipt(self) -> "PreviewHarmonyQaDenialReceipt":
        expected = preview_qa_denial_receipt_sha256(
            self.model_dump(mode="python")
        )
        if self.payload_sha256 != expected:
            raise ValueError("harmony_qa_denial_receipt_digest_invalid")
        return self

    def bind_evidence(
        self,
        evidence: PreviewHarmonyIndependentQaEvidence | dict[str, object],
    ) -> PreviewHarmonyIndependentQaEvidence:
        typed = (
            evidence
            if isinstance(evidence, PreviewHarmonyIndependentQaEvidence)
            else PreviewHarmonyIndependentQaEvidence.model_validate(evidence)
        )
        if (
            typed.reviewed_output_sha256 != self.denied_output_sha256
            or typed.findings != self.finding_codes
            or typed.verifier_version != self.verifier_version
            or preview_qa_evidence_sha256(typed) != self.evidence_sha256
        ):
            raise ValueError("harmony_qa_denial_evidence_binding_invalid")
        return typed

    def bind_private_content(
        self,
        receipt: PreviewHarmonyStageReceipt,
    ) -> None:
        if (
            receipt.stage != PreviewHarmonyStage.PRIVATE_CONTENT
            or receipt.workspace_id != self.workspace_id
            or receipt.client_id != self.client_id
            or receipt.round_id != self.round_id
            or receipt.plan_id != self.plan_id
            or receipt.receipt_id != self.private_content_receipt_id
            or receipt.output_sha256 != self.denied_output_sha256
        ):
            raise ValueError("harmony_qa_denial_private_content_binding_invalid")
        if receipt.principal_id == self.reviewer_principal_id:
            raise ValueError("harmony_qa_denial_producer_separation_invalid")

    def assert_producer_separation(
        self,
        producer_principal_ids: Sequence[UUID | str],
    ) -> None:
        normalized = {
            _uuid4(
                principal_id,
                "harmony_qa_denial_producer_separation_invalid",
            )
            for principal_id in producer_principal_ids
        }
        if self.reviewer_principal_id in normalized:
            raise ValueError("harmony_qa_denial_producer_separation_invalid")


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
        min_length=4,
        max_length=5,
    )
    stage_receipt_count: Literal[4, 5]
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
        if (
            len({item.signal_id for item in manifest}) != 4
            or len({item.signal_payload_sha256 for item in manifest}) != 4
        ):
            raise ValueError("harmony_preview_manifest_duplicate")
        receipts = tuple(sorted(
            self.connector_receipts,
            key=lambda item: item.lane.value,
        ))
        if self.connector_receipts != receipts:
            raise ValueError("harmony_preview_connector_order_invalid")
        if (
            len({item.connector_receipt_id for item in manifest}) != 4
            or len({item.receipt_id for item in receipts}) != 4
        ):
            raise ValueError("harmony_preview_connector_duplicate")
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

        expected_stages = tuple(PreviewHarmonyStage)[:len(self.stage_receipts)]
        if (
            tuple(item.stage for item in self.stage_receipts) != expected_stages
            or self.stage_receipt_count != len(self.stage_receipts)
        ):
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
            *(receipt.producer_principal_id for receipt in receipts),
        }:
            raise ValueError("harmony_preview_qa_separation_invalid")
        if len({receipt.principal_id for receipt in self.stage_receipts}) != len(
            self.stage_receipts
        ):
            raise ValueError("harmony_preview_specialist_separation_invalid")
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
    stage_receipts = bound.get("stage_receipts")
    if isinstance(stage_receipts, (list, tuple)):
        bound.setdefault("stage_receipt_count", len(stage_receipts))
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


def preview_harmony_revocation_set_sha256(
    registration_ids: Sequence[UUID | str],
) -> str:
    normalized = tuple(sorted(
        (
            _uuid4(value, "harmony_preview_trust_chain_revocation_invalid")
            for value in registration_ids
        ),
        key=str,
    ))
    if len(set(normalized)) != len(normalized):
        raise ValueError("harmony_preview_trust_chain_revocation_invalid")
    return _sha256({
        "domain": "coineasy:harmony:preview:revocation-set:v1",
        "registration_ids": normalized,
    })


def _trust_snapshot_microsecond_json_value(value: object) -> object:
    """Canonicalize candidate snapshots without truncating timestamps."""
    if isinstance(value, BaseModel):
        return _trust_snapshot_microsecond_json_value(
            value.model_dump(mode="python")
        )
    if isinstance(value, datetime):
        normalized = _utc_timestamp(
            value,
            "harmony_preview_trust_snapshot_candidate_time_invalid",
        )
        return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _trust_snapshot_microsecond_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _trust_snapshot_microsecond_json_value(item) for item in value
        ]
    return value


def preview_harmony_trust_snapshot_candidate_sha256(
    payload: dict[str, object],
) -> str:
    canonical = json.dumps(
        _trust_snapshot_microsecond_json_value({
            key: value
            for key, value in payload.items()
            if key != "trust_snapshot_candidate_sha256"
        }),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class PreviewHarmonyTrustSnapshotCandidate(BaseModel):
    """As-of structural candidate; only a DB check may establish currentness."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-trust-snapshot-candidate@1"] = (
        "harmony-trust-snapshot-candidate@1"
    )
    collaboration_round: PreviewHarmonyCollaborationRound
    signals: tuple[HarmonySignal, ...] = Field(min_length=4, max_length=4)
    registrations: tuple[PreviewHarmonyConnectorRegistration, ...] = Field(
        min_length=4,
        max_length=4,
    )
    request_receipts: tuple[PreviewHarmonyConnectorRequestReceipt, ...] = Field(
        min_length=4,
        max_length=4,
    )
    branch_ref: str
    branch_fence_active: StrictBool
    branch_fence_created_at: datetime
    branch_fence_expires_at: datetime
    observed_at: datetime
    revoked_registration_ids: tuple[UUID4, ...]
    revocation_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    database_currentness_required: Literal[True] = True
    trust_snapshot_candidate_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("branch_ref")
    @classmethod
    def validate_branch_ref(cls, value: str) -> str:
        if not _PREVIEW_BRANCH_PATTERN.fullmatch(value):
            raise ValueError("harmony_preview_trust_chain_fence_invalid")
        return value

    @field_validator(
        "branch_fence_created_at",
        "branch_fence_expires_at",
        "observed_at",
    )
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _utc_timestamp(value, "harmony_preview_trust_chain_time_invalid")

    @field_validator("revoked_registration_ids")
    @classmethod
    def validate_revoked_registration_ids(
        cls,
        values: tuple[UUID, ...],
    ) -> tuple[UUID, ...]:
        if (
            tuple(sorted(values, key=str)) != values
            or len(set(values)) != len(values)
        ):
            raise ValueError("harmony_preview_trust_chain_revocation_invalid")
        return values

    @model_validator(mode="after")
    def validate_snapshot_candidate(
        self,
    ) -> "PreviewHarmonyTrustSnapshotCandidate":
        round_result = self.collaboration_round
        if round_result.workspace_id != self.registrations[0].workspace_id:
            raise ValueError("harmony_preview_trust_chain_scope_invalid")
        if not self.branch_fence_active or not (
            self.branch_fence_created_at
            <= self.observed_at
            < self.branch_fence_expires_at
        ):
            raise ValueError("harmony_preview_trust_chain_fence_invalid")

        registrations = tuple(sorted(
            self.registrations,
            key=lambda item: item.lane.value,
        ))
        signals = tuple(sorted(self.signals, key=lambda item: item.lane.value))
        qa_principal_id = round_result.stage_receipts[2].principal_id
        if qa_principal_id in {
            signal.producer_principal_id for signal in signals
        }:
            raise ValueError("harmony_preview_qa_separation_invalid")
        if self.registrations != registrations or self.signals != signals:
            raise ValueError("harmony_preview_trust_chain_order_invalid")
        if (
            len({item.registration_id for item in registrations}) != 4
            or {item.lane for item in registrations} != set(_LANE_CAPABILITY)
            or len({item.signal_id for item in signals}) != 4
            or {item.lane for item in signals} != set(_LANE_CAPABILITY)
        ):
            raise ValueError("harmony_preview_trust_chain_complete_invalid")

        registration_ids = {item.registration_id for item in registrations}
        revoked = set(self.revoked_registration_ids)
        if not revoked.issubset(registration_ids):
            raise ValueError("harmony_preview_trust_chain_revocation_invalid")
        if revoked:
            raise ValueError("harmony_preview_trust_chain_registration_revoked")
        if self.revocation_set_sha256 != preview_harmony_revocation_set_sha256(
            self.revoked_registration_ids
        ):
            raise ValueError("harmony_preview_trust_chain_revocation_invalid")

        requests = self.request_receipts
        if (
            len({item.request_receipt_id for item in requests}) != 4
            or len({item.registration_id for item in requests}) != 4
        ):
            raise ValueError("harmony_preview_trust_chain_complete_invalid")
        manifest = round_result.signal_manifest
        connector_receipts = round_result.connector_receipts
        if not (
            round_result.workspace_id == registrations[0].workspace_id
            and round_result.client_id == registrations[0].client_id
        ):
            raise ValueError("harmony_preview_trust_chain_scope_invalid")

        for signal, manifest_entry, registration, request, connector in zip(
            signals,
            manifest,
            registrations,
            requests,
            connector_receipts,
        ):
            if (
                registration.workspace_id != round_result.workspace_id
                or registration.client_id != round_result.client_id
                or registration.branch_ref != self.branch_ref
                or registration.created_at
                < self.branch_fence_created_at.replace(microsecond=0)
                or registration.expires_at > self.branch_fence_expires_at
                or not registration.created_at
                <= self.observed_at
                < registration.expires_at
                or request.registration_id != registration.registration_id
                or not request.accepted_at
                <= self.observed_at
                < request.expires_at
                or signal.signal_id != manifest_entry.signal_id
                or signal.payload_sha256
                != manifest_entry.signal_payload_sha256
                or signal.lane != registration.lane
                or connector.lane != registration.lane
            ):
                raise ValueError("harmony_preview_trust_chain_binding_invalid")
            request.bind_connector_receipt(registration, connector, signal)

        validate_squid_preview_signal_set(
            signals,
            connector_receipts,
            observed_at=self.observed_at,
        )
        expected = preview_harmony_trust_snapshot_candidate_sha256(
            self.model_dump(mode="python")
        )
        if self.trust_snapshot_candidate_sha256 != expected:
            raise ValueError("harmony_preview_trust_chain_digest_invalid")
        return self


def bind_preview_harmony_trust_snapshot_candidate(
    *,
    collaboration_round: PreviewHarmonyCollaborationRound,
    signals: Sequence[HarmonySignal],
    registrations: Sequence[PreviewHarmonyConnectorRegistration],
    request_receipts: Sequence[PreviewHarmonyConnectorRequestReceipt],
    branch_ref: str,
    branch_fence_active: bool,
    branch_fence_created_at: datetime,
    branch_fence_expires_at: datetime,
    observed_at: datetime,
    revoked_registration_ids: Sequence[UUID | str],
) -> dict[str, object]:
    ordered_registrations = tuple(sorted(
        registrations,
        key=lambda item: item.lane.value,
    ))
    requests_by_registration = {
        receipt.registration_id: receipt for receipt in request_receipts
    }
    if len(requests_by_registration) != len(request_receipts):
        raise ValueError("harmony_preview_trust_chain_complete_invalid")
    try:
        ordered_requests = tuple(
            requests_by_registration[item.registration_id]
            for item in ordered_registrations
        )
    except KeyError as exc:
        raise ValueError("harmony_preview_trust_chain_complete_invalid") from exc
    if len(ordered_requests) != len(request_receipts):
        raise ValueError("harmony_preview_trust_chain_complete_invalid")
    normalized_revocations = tuple(sorted(
        (
            _uuid4(value, "harmony_preview_trust_chain_revocation_invalid")
            for value in revoked_registration_ids
        ),
        key=str,
    ))
    payload: dict[str, object] = {
        "schema_version": "harmony-trust-snapshot-candidate@1",
        "collaboration_round": collaboration_round,
        "signals": tuple(sorted(signals, key=lambda item: item.lane.value)),
        "registrations": ordered_registrations,
        "request_receipts": ordered_requests,
        "branch_ref": branch_ref,
        "branch_fence_active": branch_fence_active,
        "branch_fence_created_at": branch_fence_created_at,
        "branch_fence_expires_at": branch_fence_expires_at,
        "observed_at": observed_at,
        "revoked_registration_ids": normalized_revocations,
        "revocation_set_sha256": preview_harmony_revocation_set_sha256(
            normalized_revocations
        ),
        "database_currentness_required": True,
    }
    payload["trust_snapshot_candidate_sha256"] = (
        preview_harmony_trust_snapshot_candidate_sha256(payload)
    )
    return payload


def validate_preview_harmony_trust_snapshot_candidate(
    **kwargs: object,
) -> PreviewHarmonyTrustSnapshotCandidate:
    return PreviewHarmonyTrustSnapshotCandidate.model_validate(
        bind_preview_harmony_trust_snapshot_candidate(  # type: ignore[arg-type]
            **kwargs
        )
    )


__all__ = [
    "PreviewHarmonyCollaborationRound",
    "PreviewHarmonyConnectorAttestationReceipt",
    "PreviewHarmonyConnectorCapability",
    "PreviewHarmonyConnectorRegistration",
    "PreviewHarmonyConnectorRequestReceipt",
    "PreviewHarmonyTrustSnapshotCandidate",
    "PreviewHarmonyIndependentQaEvidence",
    "PreviewHarmonyOperatorInboxItem",
    "PreviewHarmonyQaCriteria",
    "PreviewHarmonyQaDenialReceipt",
    "PreviewHarmonyQaFindingCode",
    "PreviewHarmonyRoundSignal",
    "PreviewHarmonyStage",
    "PreviewHarmonyStageReceipt",
    "bind_preview_collaboration_round",
    "bind_preview_connector_registration",
    "bind_preview_connector_request_receipt",
    "bind_preview_connector_receipt",
    "bind_preview_harmony_trust_snapshot_candidate",
    "bind_preview_qa_denial_receipt",
    "bind_preview_stage_receipt",
    "preview_collaboration_round_sha256",
    "preview_connector_registration_sha256",
    "preview_connector_request_receipt_sha256",
    "preview_connector_request_sha256",
    "preview_connector_receipt_sha256",
    "preview_harmony_trust_snapshot_candidate_sha256",
    "preview_harmony_revocation_set_sha256",
    "preview_qa_denial_receipt_sha256",
    "preview_qa_evidence_sha256",
    "preview_stage_operation_key_sha256",
    "preview_stage_receipt_sha256",
    "validate_preview_harmony_trust_snapshot_candidate",
    "validate_squid_preview_signal_set",
]
