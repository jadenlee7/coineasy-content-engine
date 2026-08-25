from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import (
    Annotated,
    Iterable,
    Literal,
    Mapping,
    Protocol,
    Sequence,
    Union,
)
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    UUID4,
    field_validator,
    model_validator,
)

from core.client_config import list_available_clients, load_client_config

from .models import _contains_secret, _safe_text


HARMONY_CLIENT_IDS = ("babylon", "origintrail", "squid", "yellow")
HarmonyClientId = Literal["babylon", "origintrail", "squid", "yellow"]
HARMONY_TOPIC_CODES = (
    "community_faq",
    "integration_update",
    "launch_status",
    "market_context",
    "official_update",
    "performance_gap",
    "product_mechanics",
    "routing_basics",
    "security_safety",
    "staking_basics",
    "technical_architecture",
    "tutorial_demand",
    "user_guide",
    "wallet_safety",
)

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]{1,30}$")
_PARTICIPANT_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]{1,63}$")
_INSTRUCTION_CODE_PATTERN = re.compile(
    r"(?:^|[_:-])(?:credential|execute|ignore|instruction|prompt|publish|"
    r"secret|send|tool_call)(?:$|[_:-])"
)


def _utc_seconds(value: datetime, code: str) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        raise ValueError(code)
    return value.astimezone(timezone.utc)


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


def harmony_payload_sha256(payload: dict[str, object]) -> str:
    """Hash one canonical signal payload before adding payload_sha256."""
    return hashlib.sha256(_canonical_json({
        key: value for key, value in payload.items() if key != "payload_sha256"
    })).hexdigest()


def bind_harmony_signal_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return a copy carrying the exact canonical payload digest."""
    bound = dict(payload)
    bound["payload_sha256"] = harmony_payload_sha256(bound)
    return bound


def _validated_codes(values: Iterable[str], code: str) -> tuple[str, ...]:
    normalized = tuple(values)
    if (
        not normalized
        or tuple(sorted(normalized)) != normalized
        or len(set(normalized)) != len(normalized)
        or any(
            not _CODE_PATTERN.fullmatch(value) or _contains_secret(value)
            or _INSTRUCTION_CODE_PATTERN.search(value)
            for value in normalized
        )
    ):
        raise ValueError(code)
    return normalized


def _validated_topic_codes(values: Iterable[str]) -> tuple[str, ...]:
    normalized = _validated_codes(
        values,
        "agent_harmony_topic_codes_invalid",
    )
    if any(value not in HARMONY_TOPIC_CODES for value in normalized):
        raise ValueError("agent_harmony_topic_codes_invalid")
    return normalized


class HarmonyLane(str, Enum):
    QUIZ_BOT = "quiz_bot"
    COMMUNITY_OPS = "community_ops"
    CONTENT_SOURCE = "content_source"
    RECAP = "recap"
    COORDINATOR = "coordinator"
    INDEPENDENT_QA = "independent_qa"


class HarmonySignalKind(str, Enum):
    QUIZ_LEARNING = "quiz_learning"
    COMMUNITY_DEMAND = "community_demand"
    OFFICIAL_SOURCE = "official_source"
    RECAP_METRIC = "recap_metric"


class HarmonyRoundStatus(str, Enum):
    WAITING_FOR_ATTESTATION = "waiting_for_attestation"
    WAITING_FOR_SIGNALS = "waiting_for_signals"
    NEEDS_ALIGNMENT = "needs_alignment"
    READY_FOR_HUMAN_SCOPE_REVIEW = "ready_for_human_scope_review"


class HarmonyBlocker(str, Enum):
    ATTESTATION_ASSURANCE_INSUFFICIENT = "attestation_assurance_insufficient"
    ATTESTATION_EXPIRED = "attestation_expired"
    ATTESTATION_NOT_YET_VALID = "attestation_not_yet_valid"
    FUTURE_SIGNAL = "future_signal"
    MISSING_ATTESTATION = "missing_attestation"
    MISSING_QUIZ_LEARNING = "missing_quiz_learning"
    MISSING_COMMUNITY_DEMAND = "missing_community_demand"
    MISSING_OFFICIAL_SOURCE = "missing_official_source"
    MISSING_RECAP_METRIC = "missing_recap_metric"
    STALE_SIGNAL = "stale_signal"
    TOPIC_CONSENSUS_MISSING = "topic_consensus_missing"


class HarmonyTrustMode(str, Enum):
    EMPTY = "empty"
    TEST_FIXTURE = "test_fixture"
    RUNTIME_VERIFIED = "runtime_verified"
    MIXED = "mixed"


class HarmonyMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_code: str
    unit: Literal["count", "basis_points", "microusd", "seconds"]
    observed: bool
    value: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)

    @field_validator("metric_code")
    @classmethod
    def validate_metric_code(cls, value: str) -> str:
        return _validated_codes((value,), "agent_harmony_metric_code_invalid")[0]

    @model_validator(mode="after")
    def validate_observation(self) -> "HarmonyMetric":
        if self.observed != (self.value is not None):
            raise ValueError("agent_harmony_metric_observation_invalid")
        if self.unit == "basis_points" and self.value is not None:
            if self.value > 10_000:
                raise ValueError("agent_harmony_metric_value_invalid")
        return self


class _HarmonySignalBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-harmony-signal@1"] = (
        "agent-harmony-signal@1"
    )
    signal_id: UUID4
    workspace_id: UUID4
    client_id: HarmonyClientId
    source_event_id: UUID4
    producer_principal_id: UUID4
    producer_release_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    upstream_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: datetime
    expires_at: datetime
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    topic_codes: tuple[str, ...] = Field(min_length=1, max_length=12)
    content_factual_authority: bool
    raw_messages_included: Literal[False] = False
    personal_data_included: Literal[False] = False
    instructions_allowed: Literal[False] = False
    advisory_only: Literal[True] = True
    max_cost_microusd: Literal[0] = 0
    max_external_actions: Literal[0] = 0
    automatic_publication: Literal[False] = False
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_harmony_signal_observed_at_invalid")

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_harmony_signal_expires_at_invalid")

    @field_validator("topic_codes")
    @classmethod
    def validate_topic_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validated_topic_codes(values)

    @model_validator(mode="after")
    def validate_signal_envelope(self) -> "_HarmonySignalBase":
        if (
            self.expires_at <= self.observed_at
            or self.expires_at - self.observed_at > timedelta(days=31)
        ):
            raise ValueError("agent_harmony_signal_window_invalid")
        expected = hashlib.sha256(_canonical_json(
            self.model_dump(mode="python", exclude={"payload_sha256"})
        )).hexdigest()
        if self.payload_sha256 != expected:
            raise ValueError("agent_harmony_signal_payload_digest_invalid")
        return self

    @property
    def signal_key_sha256(self) -> str:
        return hashlib.sha256(_canonical_json({
            "client_id": self.client_id,
            "producer_principal_id": str(self.producer_principal_id),
            "schema_version": self.schema_version,
            "signal_kind": self.signal_kind.value,
            "source_event_id": str(self.source_event_id),
            "workspace_id": str(self.workspace_id),
        })).hexdigest()


class QuizLearningSignal(_HarmonySignalBase):
    signal_kind: Literal[HarmonySignalKind.QUIZ_LEARNING] = (
        HarmonySignalKind.QUIZ_LEARNING
    )
    lane: Literal[HarmonyLane.QUIZ_BOT] = HarmonyLane.QUIZ_BOT
    data_classification: Literal["aggregate_anonymous"] = "aggregate_anonymous"
    content_factual_authority: Literal[False] = False
    attempts: int = Field(ge=20, le=9_007_199_254_740_991)
    participants: int = Field(ge=5, le=9_007_199_254_740_991)
    accuracy_basis_points: int = Field(ge=0, le=10_000)
    tutorial_priority_basis_points: int = Field(ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_quiz_cohort(self) -> "QuizLearningSignal":
        if self.participants > self.attempts:
            raise ValueError("agent_harmony_quiz_cohort_invalid")
        return self


class CommunityDemandSignal(_HarmonySignalBase):
    signal_kind: Literal[HarmonySignalKind.COMMUNITY_DEMAND] = (
        HarmonySignalKind.COMMUNITY_DEMAND
    )
    lane: Literal[HarmonyLane.COMMUNITY_OPS] = HarmonyLane.COMMUNITY_OPS
    data_classification: Literal["aggregate_anonymous"] = "aggregate_anonymous"
    content_factual_authority: Literal[False] = False
    room_mapping_count: Literal[1] = 1
    sample_size: int = Field(ge=5, le=9_007_199_254_740_991)
    demand_score_basis_points: int = Field(ge=0, le=10_000)


class OfficialSourceSignal(_HarmonySignalBase):
    signal_kind: Literal[HarmonySignalKind.OFFICIAL_SOURCE] = (
        HarmonySignalKind.OFFICIAL_SOURCE
    )
    lane: Literal[HarmonyLane.CONTENT_SOURCE] = HarmonyLane.CONTENT_SOURCE
    data_classification: Literal["public_official"] = "public_official"
    content_factual_authority: Literal[True] = True
    source_item_id: UUID4
    source_body_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_kind: Literal["x_post_text", "x_article", "official_document"]
    source_verified: Literal[True] = True
    eligible_content_kinds: tuple[
        Literal["article", "daily_news", "tutorial"], ...
    ] = Field(min_length=1, max_length=3)

    @field_validator("eligible_content_kinds")
    @classmethod
    def validate_content_kinds(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError("agent_harmony_content_kinds_invalid")
        return values


class RecapMetricSignal(_HarmonySignalBase):
    signal_kind: Literal[HarmonySignalKind.RECAP_METRIC] = (
        HarmonySignalKind.RECAP_METRIC
    )
    lane: Literal[HarmonyLane.RECAP] = HarmonyLane.RECAP
    data_classification: Literal["aggregate_anonymous"] = "aggregate_anonymous"
    content_factual_authority: Literal[False] = False
    period_start: datetime
    period_end: datetime
    metrics: tuple[HarmonyMetric, ...] = Field(min_length=1, max_length=16)

    @field_validator("period_start")
    @classmethod
    def validate_period_start(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_harmony_recap_period_invalid")

    @field_validator("period_end")
    @classmethod
    def validate_period_end(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_harmony_recap_period_invalid")

    @model_validator(mode="after")
    def validate_recap(self) -> "RecapMetricSignal":
        metric_codes = tuple(metric.metric_code for metric in self.metrics)
        if (
            self.period_end <= self.period_start
            or self.period_end > self.observed_at
            or self.period_end - self.period_start > timedelta(days=31)
            or tuple(sorted(metric_codes)) != metric_codes
            or len(set(metric_codes)) != len(metric_codes)
        ):
            raise ValueError("agent_harmony_recap_invalid")
        return self


HarmonySignal = Annotated[
    Union[
        QuizLearningSignal,
        CommunityDemandSignal,
        OfficialSourceSignal,
        RecapMetricSignal,
    ],
    Field(discriminator="signal_kind"),
]


def harmony_attestation_sha256(payload: dict[str, object]) -> str:
    """Hash one trusted attestation without accepting it from signal JSON."""
    return hashlib.sha256(_canonical_json({
        key: value
        for key, value in payload.items()
        if key != "attestation_sha256"
    })).hexdigest()


def bind_harmony_signal_attestation(
    payload: dict[str, object],
) -> dict[str, object]:
    """Return a copy carrying the exact canonical attestation digest."""
    bound = dict(payload)
    bound.setdefault(
        "schema_version",
        "agent-harmony-signal-attestation@1",
    )
    bound.setdefault("environment", "preview")
    bound.setdefault("issuer", "coineasy_preview_attestation_verifier")
    bound.setdefault("audience", "coineasy_harmony")
    lane = bound.get("lane")
    lane_value = lane.value if isinstance(lane, Enum) else lane
    capability_by_lane = {
        "quiz_bot": "submit_quiz_learning",
        "community_ops": "submit_community_demand",
        "content_source": "submit_official_source",
        "recap": "submit_recap_metric",
    }
    if lane_value in capability_by_lane:
        bound.setdefault("capability", capability_by_lane[lane_value])
    bound["attestation_sha256"] = harmony_attestation_sha256(bound)
    return bound


class HarmonySignalAttestation(BaseModel):
    """Out-of-band proof for caller-supplied Harmony signal claims.

    The signal input cannot contain this object.  A trusted adapter must verify
    a JWT or an immutable database receipt and inject a registry separately.
    ``test_fixture`` exists only for deterministic contract tests and never
    makes a signal eligible for a human handoff.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-harmony-signal-attestation@1"] = (
        "agent-harmony-signal-attestation@1"
    )
    attestation_id: UUID4
    workspace_id: UUID4
    client_id: HarmonyClientId
    signal_id: UUID4
    source_event_id: UUID4
    signal_kind: HarmonySignalKind
    lane: HarmonyLane
    producer_principal_id: UUID4
    producer_release_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    upstream_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment: Literal["preview"] = "preview"
    issuer: Literal["coineasy_preview_attestation_verifier"] = (
        "coineasy_preview_attestation_verifier"
    )
    audience: Literal["coineasy_harmony"] = "coineasy_harmony"
    capability: Literal[
        "submit_community_demand",
        "submit_official_source",
        "submit_quiz_learning",
        "submit_recap_metric",
    ]
    verification_method: Literal["jwt", "database_receipt", "test_fixture"]
    verification_reference_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_at: datetime
    expires_at: datetime
    attestation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("verified_at")
    @classmethod
    def validate_verified_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_harmony_attestation_time_invalid")

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_harmony_attestation_time_invalid")

    @model_validator(mode="after")
    def validate_attestation(self) -> "HarmonySignalAttestation":
        capability_by_lane = {
            HarmonyLane.QUIZ_BOT: "submit_quiz_learning",
            HarmonyLane.COMMUNITY_OPS: "submit_community_demand",
            HarmonyLane.CONTENT_SOURCE: "submit_official_source",
            HarmonyLane.RECAP: "submit_recap_metric",
        }
        if (
            self.expires_at <= self.verified_at
            or self.expires_at - self.verified_at > timedelta(days=31)
            or capability_by_lane.get(self.lane) != self.capability
        ):
            raise ValueError("agent_harmony_attestation_time_invalid")
        expected = hashlib.sha256(_canonical_json(
            self.model_dump(mode="python", exclude={"attestation_sha256"})
        )).hexdigest()
        if self.attestation_sha256 != expected:
            raise ValueError("agent_harmony_attestation_digest_invalid")
        return self

    @property
    def runtime_verified(self) -> bool:
        return self.verification_method in {"jwt", "database_receipt"}


class HarmonyAttestationRegistry(Protocol):
    """Pure lookup boundary; implementations must perform no I/O in a build."""

    @property
    def trust_mode(self) -> HarmonyTrustMode: ...

    def resolve(self, payload_sha256: str) -> HarmonySignalAttestation | None: ...


@dataclass(frozen=True, slots=True)
class EmptyHarmonyAttestationRegistry:
    """Default CLI registry.  Caller JSON can never populate it."""

    trust_mode = HarmonyTrustMode.EMPTY

    def resolve(self, payload_sha256: str) -> None:
        del payload_sha256
        return None


@dataclass(frozen=True, slots=True, init=False)
class FrozenHarmonyAttestationRegistry:
    """Immutable, side-effect-free registry produced by a trusted verifier."""

    _by_payload: Mapping[str, HarmonySignalAttestation]
    _attestations: tuple[HarmonySignalAttestation, ...]
    _trust_mode: HarmonyTrustMode

    def __init__(
        self,
        attestations: Iterable[HarmonySignalAttestation],
    ) -> None:
        by_payload: dict[str, HarmonySignalAttestation] = {}
        by_id: dict[UUID, HarmonySignalAttestation] = {}
        for attestation in attestations:
            prior_payload = by_payload.get(attestation.payload_sha256)
            prior_id = by_id.get(attestation.attestation_id)
            if (
                prior_payload is not None
                and prior_payload.attestation_sha256
                    != attestation.attestation_sha256
            ) or (
                prior_id is not None
                and prior_id.attestation_sha256 != attestation.attestation_sha256
            ):
                raise ValueError("agent_harmony_attestation_registry_conflict")
            by_payload[attestation.payload_sha256] = attestation
            by_id[attestation.attestation_id] = attestation
        object.__setattr__(
            self,
            "_by_payload",
            MappingProxyType(dict(by_payload)),
        )
        object.__setattr__(self, "_attestations", tuple(sorted(
            by_payload.values(),
            key=lambda item: item.attestation_sha256,
        )))
        methods = {item.verification_method for item in self._attestations}
        if not methods:
            trust_mode = HarmonyTrustMode.EMPTY
        elif methods == {"test_fixture"}:
            trust_mode = HarmonyTrustMode.TEST_FIXTURE
        elif "test_fixture" not in methods:
            trust_mode = HarmonyTrustMode.RUNTIME_VERIFIED
        else:
            trust_mode = HarmonyTrustMode.MIXED
        object.__setattr__(self, "_trust_mode", trust_mode)

    @property
    def attestations(self) -> tuple[HarmonySignalAttestation, ...]:
        return self._attestations

    @property
    def trust_mode(self) -> HarmonyTrustMode:
        return self._trust_mode

    def resolve(self, payload_sha256: str) -> HarmonySignalAttestation | None:
        return self._by_payload.get(payload_sha256)


@dataclass(frozen=True)
class _AttestedHarmonySignal:
    signal: HarmonySignal
    attestation: HarmonySignalAttestation


@dataclass(frozen=True)
class _HarmonySignalTrustIssue:
    signal: HarmonySignal
    reason: Literal[
        "attestation_expired",
        "attestation_not_yet_valid",
        "test_fixture",
        "unattested",
    ]


class HarmonyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-harmony-input@1"] = "agent-harmony-input@1"
    workspace_id: UUID4
    signals: tuple[HarmonySignal, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def validate_workspace_binding(self) -> "HarmonyInput":
        if any(signal.workspace_id != self.workspace_id for signal in self.signals):
            raise ValueError("agent_harmony_workspace_binding_invalid")
        return self


class HarmonyClientProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: HarmonyClientId
    display_name: str
    locale: Literal["ko-KR"] = "ko-KR"
    supported_content_kinds: tuple[
        Literal["article", "daily_news", "tutorial"], ...
    ]
    official_source_configured: Literal[True] = True
    quiz_contract_available: Literal[True] = True
    community_contract_available: Literal[True] = True
    recap_contract_available: Literal[True] = True
    live_harmony_adapter_connected: Literal[False] = False
    automatic_publication: Literal[False] = False

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return _safe_text(
            value,
            "agent_harmony_client_name_invalid",
            2,
            80,
            single_line=True,
        )

    @field_validator("supported_content_kinds")
    @classmethod
    def validate_supported_content_kinds(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError("agent_harmony_content_kinds_invalid")
        return values


def load_harmony_client_profiles(
    clients_dir: Path = Path("clients"),
) -> tuple[HarmonyClientProfile, ...]:
    available = tuple(list_available_clients(clients_dir))
    if available != HARMONY_CLIENT_IDS:
        raise ValueError("agent_harmony_connector_registry_incomplete")
    profiles: list[HarmonyClientProfile] = []
    for client_id in available:
        config = load_client_config(client_id, clients_dir=clients_dir)
        if (
            not config.active
            or config.locale != "ko-KR"
            or config.content_sources.twitter is None
            or not config.content_sources.twitter.handle
            or not config.brand_voice.identity
            or not config.brand_voice.channel_guidance
        ):
            raise ValueError("agent_harmony_client_contract_incomplete")
        content_kinds = {"article"}
        if config.feature_flags.news_card:
            content_kinds.add("daily_news")
        if config.feature_flags.education_carousel:
            content_kinds.add("tutorial")
        profiles.append(HarmonyClientProfile(
            client_id=client_id,
            display_name=config.name,
            supported_content_kinds=tuple(sorted(content_kinds)),
        ))
    return tuple(profiles)


class HarmonyParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    participant_id: str
    participant_kind: Literal[
        "audit",
        "builder",
        "client_quiz_bot",
        "community_ops",
        "content_engine",
        "coordinator",
        "human_gate",
        "recap",
        "reviewer",
    ]
    client_id: HarmonyClientId | None = None
    connection_state: Literal[
        "contract_only",
        "human_gate",
        "local_config",
        "planned_adapter",
        "sanitized_snapshot",
    ]
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=8)
    can_approve_scope: bool = False
    can_access_raw_community: Literal[False] = False
    can_change_production: Literal[False] = False
    can_publish: Literal[False] = False

    @field_validator("participant_id")
    @classmethod
    def validate_participant_id(cls, value: str) -> str:
        if not _PARTICIPANT_PATTERN.fullmatch(value) or _contains_secret(value):
            raise ValueError("agent_harmony_participant_invalid")
        return value

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validated_codes(values, "agent_harmony_capability_invalid")

    @model_validator(mode="after")
    def validate_scope(self) -> "HarmonyParticipant":
        if self.participant_kind == "client_quiz_bot":
            if self.client_id is None or self.connection_state != "contract_only":
                raise ValueError("agent_harmony_participant_scope_invalid")
        elif self.client_id is not None:
            raise ValueError("agent_harmony_participant_scope_invalid")
        if self.can_approve_scope != (self.participant_kind == "human_gate"):
            raise ValueError("agent_harmony_participant_authority_invalid")
        return self


def harmony_participants() -> tuple[HarmonyParticipant, ...]:
    participants = [
        HarmonyParticipant(
            participant_id=f"{client_id}_quiz_bot",
            participant_kind="client_quiz_bot",
            client_id=client_id,
            connection_state="contract_only",
            capabilities=("aggregate_learning_signal",),
        )
        for client_id in HARMONY_CLIENT_IDS
    ]
    participants.extend((
        HarmonyParticipant(
            participant_id="content_engine",
            participant_kind="content_engine",
            connection_state="local_config",
            capabilities=("official_source_binding", "private_content_brief"),
        ),
        HarmonyParticipant(
            participant_id="easyfarm_community_ops",
            participant_kind="community_ops",
            connection_state="sanitized_snapshot",
            capabilities=("aggregate_demand_signal", "ops_observation"),
        ),
        HarmonyParticipant(
            participant_id="coineasy_recap",
            participant_kind="recap",
            connection_state="sanitized_snapshot",
            capabilities=("completion_recap", "performance_observation"),
        ),
        HarmonyParticipant(
            participant_id="grok_bot",
            participant_kind="coordinator",
            connection_state="planned_adapter",
            capabilities=("bounded_round_synthesis", "operator_inbox"),
        ),
        HarmonyParticipant(
            participant_id="buzz",
            participant_kind="audit",
            connection_state="planned_adapter",
            capabilities=("audit_receipt", "private_status"),
        ),
        HarmonyParticipant(
            participant_id="devin",
            participant_kind="builder",
            connection_state="planned_adapter",
            capabilities=("bounded_implementation",),
        ),
        HarmonyParticipant(
            participant_id="claude_code",
            participant_kind="builder",
            connection_state="planned_adapter",
            capabilities=("bounded_implementation", "independent_review"),
        ),
        HarmonyParticipant(
            participant_id="codex",
            participant_kind="reviewer",
            connection_state="planned_adapter",
            capabilities=("independent_review", "release_verification"),
        ),
        HarmonyParticipant(
            participant_id="grok_build",
            participant_kind="builder",
            connection_state="planned_adapter",
            capabilities=("preview_prototype",),
        ),
        HarmonyParticipant(
            participant_id="human_operator",
            participant_kind="human_gate",
            connection_state="human_gate",
            capabilities=("scope_approval", "strategy_direction"),
            can_approve_scope=True,
        ),
    ))
    return tuple(sorted(participants, key=lambda item: item.participant_id))


class HarmonyTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    client_id: HarmonyClientId
    sequence: int = Field(ge=1, le=6)
    speaker: str
    lane: HarmonyLane
    message_kind: Literal[
        "challenge",
        "evidence",
        "evidence_request",
        "handoff",
        "proposal",
        "support",
        "verification",
    ]
    signal_ids: tuple[UUID4, ...] = Field(default=(), max_length=64)
    attestation_sha256s: tuple[str, ...] = Field(default=(), max_length=64)
    topic_codes: tuple[str, ...] = Field(default=(), max_length=12)
    content_factual_authority: bool
    instructions_accepted: Literal[False] = False
    execution_authorized: Literal[False] = False
    automatic_publication: Literal[False] = False

    @field_validator("speaker")
    @classmethod
    def validate_speaker(cls, value: str) -> str:
        if not _PARTICIPANT_PATTERN.fullmatch(value) or _contains_secret(value):
            raise ValueError("agent_harmony_turn_speaker_invalid")
        return value

    @field_validator("topic_codes")
    @classmethod
    def validate_turn_topic_codes(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not values:
            return values
        return _validated_topic_codes(values)

    @model_validator(mode="after")
    def validate_turn(self) -> "HarmonyTurn":
        if (
            tuple(sorted(self.signal_ids, key=str)) != self.signal_ids
            or tuple(sorted(self.attestation_sha256s))
            != self.attestation_sha256s
            or len(set(self.signal_ids)) != len(self.signal_ids)
            or len(set(self.attestation_sha256s))
            != len(self.attestation_sha256s)
            or len(self.signal_ids) != len(self.attestation_sha256s)
            or any(
                re.fullmatch(r"[a-f0-9]{64}", item) is None
                for item in self.attestation_sha256s
            )
        ):
            raise ValueError("agent_harmony_turn_signal_order_invalid")
        if self.content_factual_authority != (
            self.lane == HarmonyLane.CONTENT_SOURCE
            and self.message_kind == "evidence"
        ):
            raise ValueError("agent_harmony_turn_authority_invalid")
        expected = hashlib.sha256(_canonical_json(
            self.model_dump(mode="python", exclude={"turn_sha256"})
        )).hexdigest()
        if self.turn_sha256 != expected:
            raise ValueError("agent_harmony_turn_digest_invalid")
        return self


class HarmonySignalManifestEntry(BaseModel):
    """Typed signal/attestation pair used by turns and handoffs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: UUID4
    signal_kind: HarmonySignalKind
    lane: HarmonyLane
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    attestation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_factual_authority: bool

    @model_validator(mode="after")
    def validate_lane_and_authority(self) -> "HarmonySignalManifestEntry":
        lane_by_kind = {
            HarmonySignalKind.QUIZ_LEARNING: HarmonyLane.QUIZ_BOT,
            HarmonySignalKind.COMMUNITY_DEMAND: HarmonyLane.COMMUNITY_OPS,
            HarmonySignalKind.OFFICIAL_SOURCE: HarmonyLane.CONTENT_SOURCE,
            HarmonySignalKind.RECAP_METRIC: HarmonyLane.RECAP,
        }
        if self.lane != lane_by_kind[self.signal_kind]:
            raise ValueError("agent_harmony_manifest_lane_invalid")
        expected_authority = (
            self.signal_kind == HarmonySignalKind.OFFICIAL_SOURCE
            and self.lane == HarmonyLane.CONTENT_SOURCE
        )
        if self.content_factual_authority != expected_authority:
            raise ValueError("agent_harmony_manifest_authority_invalid")
        return self


class HarmonySignalTrustIssue(BaseModel):
    """A caller claim that was not eligible for the trusted input set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: UUID4
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lane: HarmonyLane
    reason: Literal[
        "attestation_expired",
        "attestation_not_yet_valid",
        "test_fixture",
        "unattested",
    ]


def _manifest_sort_key(
    item: HarmonySignalManifestEntry,
) -> tuple[str, str, str, str]:
    return (
        item.lane.value,
        str(item.signal_id),
        item.payload_sha256,
        item.attestation_sha256,
    )


def _validate_signal_manifest(
    manifest: Sequence[HarmonySignalManifestEntry],
) -> None:
    if (
        tuple(sorted(manifest, key=_manifest_sort_key)) != tuple(manifest)
        or len({item.signal_id for item in manifest}) != len(manifest)
        or len({item.payload_sha256 for item in manifest}) != len(manifest)
        or len({item.attestation_sha256 for item in manifest}) != len(manifest)
    ):
        raise ValueError("agent_harmony_signal_manifest_invalid")


def _manifest_input_set_sha256(
    manifest: Sequence[HarmonySignalManifestEntry],
) -> str:
    return hashlib.sha256(_canonical_json([
        item.model_dump(mode="python") for item in manifest
    ])).hexdigest()


def _manifest_attestation_set_sha256(
    manifest: Sequence[HarmonySignalManifestEntry],
) -> str:
    return hashlib.sha256(_canonical_json(sorted(
        item.attestation_sha256 for item in manifest
    ))).hexdigest()


class HarmonyHandoffCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-harmony-handoff@1"] = (
        "agent-harmony-handoff@1"
    )
    workspace_id: UUID4
    client_id: HarmonyClientId
    round_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    signal_manifest: tuple[HarmonySignalManifestEntry, ...] = Field(
        min_length=4,
        max_length=256,
    )
    input_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    attestation_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_signal_ids: tuple[UUID4, ...] = Field(min_length=1, max_length=32)
    context_signal_ids: tuple[UUID4, ...] = Field(min_length=3, max_length=96)
    topic_codes: tuple[str, ...] = Field(min_length=1, max_length=12)
    recommended_content_kind: Literal["article", "daily_news", "tutorial"]
    channel_scope: Literal["private_control_room"] = "private_control_room"
    data_classification: Literal["aggregate_plus_public_official"] = (
        "aggregate_plus_public_official"
    )
    requested_capabilities: tuple[
        Literal[
            "independent_qa",
            "prepare_private_content",
            "prepare_private_recap",
        ], ...
    ] = (
        "independent_qa",
        "prepare_private_content",
        "prepare_private_recap",
    )
    next_gate: Literal["human_scope_review"] = "human_scope_review"
    environment: Literal["preview"] = "preview"
    dispatchable: Literal[False] = False
    portable_trust: Literal[False] = False
    attestation_reverification_required: Literal[True] = True
    execution_authorized: Literal[False] = False
    external_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    max_cost_microusd: Literal[0] = 0
    max_external_actions: Literal[0] = 0
    automatic_publication: Literal[False] = False
    scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("topic_codes")
    @classmethod
    def validate_handoff_topics(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _validated_topic_codes(values)

    @model_validator(mode="after")
    def validate_handoff(self) -> "HarmonyHandoffCandidate":
        _validate_signal_manifest(self.signal_manifest)
        manifest_source_ids = tuple(sorted(
            (
                item.signal_id
                for item in self.signal_manifest
                if item.lane == HarmonyLane.CONTENT_SOURCE
            ),
            key=str,
        ))
        manifest_context_ids = tuple(sorted(
            (
                item.signal_id
                for item in self.signal_manifest
                if item.lane != HarmonyLane.CONTENT_SOURCE
            ),
            key=str,
        ))
        if (
            tuple(sorted(self.source_signal_ids, key=str))
            != self.source_signal_ids
            or tuple(sorted(self.context_signal_ids, key=str))
            != self.context_signal_ids
            or set(self.source_signal_ids) & set(self.context_signal_ids)
            or self.requested_capabilities != (
                "independent_qa",
                "prepare_private_content",
                "prepare_private_recap",
            )
            or self.source_signal_ids != manifest_source_ids
            or self.context_signal_ids != manifest_context_ids
            or self.input_set_sha256
            != _manifest_input_set_sha256(self.signal_manifest)
            or self.attestation_set_sha256
            != _manifest_attestation_set_sha256(self.signal_manifest)
        ):
            raise ValueError("agent_harmony_handoff_signal_binding_invalid")
        expected = hashlib.sha256(_canonical_json(
            self.model_dump(mode="python", exclude={"scope_sha256"})
        )).hexdigest()
        if self.scope_sha256 != expected:
            raise ValueError("agent_harmony_handoff_digest_invalid")
        return self


class HarmonyClientRound(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-harmony-round@1"] = "agent-harmony-round@1"
    workspace_id: UUID4
    client_id: HarmonyClientId
    round_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: HarmonyRoundStatus
    active_signal_ids: tuple[UUID4, ...] = Field(default=(), max_length=256)
    stale_signal_ids: tuple[UUID4, ...] = Field(default=(), max_length=256)
    future_signal_ids: tuple[UUID4, ...] = Field(default=(), max_length=256)
    signal_manifest: tuple[HarmonySignalManifestEntry, ...] = Field(
        default=(),
        max_length=256,
    )
    trust_issues: tuple[HarmonySignalTrustIssue, ...] = Field(
        default=(),
        max_length=256,
    )
    consensus_topic_codes: tuple[str, ...] = Field(default=(), max_length=12)
    blockers: tuple[HarmonyBlocker, ...] = Field(default=(), max_length=16)
    turns: tuple[HarmonyTurn, ...] = Field(min_length=6, max_length=6)
    handoff: HarmonyHandoffCandidate | None = None
    rehearsal_only: Literal[True] = True
    execution_authorized: Literal[False] = False
    max_cost_microusd: Literal[0] = 0
    max_external_actions: Literal[0] = 0
    automatic_publication: Literal[False] = False

    @field_validator("consensus_topic_codes")
    @classmethod
    def validate_consensus_topics(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not values:
            return values
        return _validated_topic_codes(values)

    @model_validator(mode="after")
    def validate_round(self) -> "HarmonyClientRound":
        _validate_signal_manifest(self.signal_manifest)
        if tuple(turn.sequence for turn in self.turns) != (1, 2, 3, 4, 5, 6):
            raise ValueError("agent_harmony_turn_sequence_invalid")
        if any(turn.client_id != self.client_id for turn in self.turns):
            raise ValueError("agent_harmony_turn_client_binding_invalid")
        expected_speakers = (
            f"{self.client_id}_quiz_bot",
            "easyfarm_community_ops",
            "content_engine",
            "coineasy_recap",
            "grok_bot",
            "codex",
        )
        expected_lanes = (
            HarmonyLane.QUIZ_BOT,
            HarmonyLane.COMMUNITY_OPS,
            HarmonyLane.CONTENT_SOURCE,
            HarmonyLane.RECAP,
            HarmonyLane.COORDINATOR,
            HarmonyLane.INDEPENDENT_QA,
        )
        if (
            tuple(turn.speaker for turn in self.turns) != expected_speakers
            or tuple(turn.lane for turn in self.turns) != expected_lanes
        ):
            raise ValueError("agent_harmony_turn_role_binding_invalid")
        if (
            tuple(sorted(self.active_signal_ids, key=str))
            != self.active_signal_ids
            or tuple(sorted(self.stale_signal_ids, key=str))
            != self.stale_signal_ids
            or tuple(sorted(self.future_signal_ids, key=str))
            != self.future_signal_ids
            or len(set(self.active_signal_ids)) != len(self.active_signal_ids)
            or len(set(self.stale_signal_ids)) != len(self.stale_signal_ids)
            or len(set(self.future_signal_ids)) != len(self.future_signal_ids)
            or set(self.active_signal_ids) & set(self.stale_signal_ids)
            or set(self.active_signal_ids) & set(self.future_signal_ids)
            or set(self.stale_signal_ids) & set(self.future_signal_ids)
            or tuple(sorted(
                self.trust_issues,
                key=lambda item: (
                    item.lane.value,
                    item.payload_sha256,
                    str(item.signal_id),
                    item.reason,
                ),
            )) != self.trust_issues
            or tuple(sorted(self.blockers, key=lambda item: item.value))
            != self.blockers
        ):
            raise ValueError("agent_harmony_round_order_invalid")
        ready = self.status == HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW
        if ready != (self.handoff is not None):
            raise ValueError("agent_harmony_handoff_state_invalid")
        if ready and self.blockers:
            raise ValueError("agent_harmony_handoff_state_invalid")
        active_set = set(self.active_signal_ids)
        manifest_set = {item.signal_id for item in self.signal_manifest}
        manifest_by_lane = {
            lane: tuple(
                item for item in self.signal_manifest if item.lane == lane
            )
            for lane in (
                HarmonyLane.QUIZ_BOT,
                HarmonyLane.COMMUNITY_OPS,
                HarmonyLane.CONTENT_SOURCE,
                HarmonyLane.RECAP,
            )
        }
        lane_signal_sets = [set(turn.signal_ids) for turn in self.turns[:4]]
        if (
            manifest_set != active_set
            or any(left & right for index, left in enumerate(lane_signal_sets)
                for right in lane_signal_sets[index + 1:])
            or set().union(*lane_signal_sets) != active_set
            or set(self.turns[4].signal_ids) != active_set
            or set(self.turns[5].signal_ids) != active_set
        ):
            raise ValueError("agent_harmony_turn_signal_binding_invalid")
        for turn, lane in zip(
            self.turns[:4],
            (
                HarmonyLane.QUIZ_BOT,
                HarmonyLane.COMMUNITY_OPS,
                HarmonyLane.CONTENT_SOURCE,
                HarmonyLane.RECAP,
            ),
        ):
            lane_manifest = manifest_by_lane[lane]
            if (
                turn.signal_ids
                != tuple(sorted(
                    (item.signal_id for item in lane_manifest),
                    key=str,
                ))
                or turn.attestation_sha256s
                != tuple(sorted(
                    item.attestation_sha256 for item in lane_manifest
                ))
            ):
                raise ValueError("agent_harmony_turn_manifest_binding_invalid")
        all_attestations = tuple(sorted(
            item.attestation_sha256 for item in self.signal_manifest
        ))
        if (
            self.turns[4].attestation_sha256s != all_attestations
            or self.turns[5].attestation_sha256s != all_attestations
        ):
            raise ValueError("agent_harmony_turn_manifest_binding_invalid")
        round_scope = {
            "active_signal_ids": [str(item) for item in self.active_signal_ids],
            "automatic_publication": False,
            "blockers": [item.value for item in self.blockers],
            "client_id": self.client_id,
            "consensus_topic_codes": list(self.consensus_topic_codes),
            "future_signal_ids": [str(item) for item in self.future_signal_ids],
            "schema_version": self.schema_version,
            "signal_manifest": [
                item.model_dump(mode="python") for item in self.signal_manifest
            ],
            "stale_signal_ids": [str(item) for item in self.stale_signal_ids],
            "status": self.status.value,
            "trust_issues": [
                item.model_dump(mode="python") for item in self.trust_issues
            ],
            "workspace_id": str(self.workspace_id),
        }
        if self.round_sha256 != hashlib.sha256(
            _canonical_json(round_scope)
        ).hexdigest():
            raise ValueError("agent_harmony_round_digest_invalid")
        if self.handoff is not None and (
            self.handoff.workspace_id != self.workspace_id
            or self.handoff.client_id != self.client_id
            or self.handoff.round_sha256 != self.round_sha256
            or self.handoff.signal_manifest != self.signal_manifest
            or self.handoff.source_signal_ids != self.turns[2].signal_ids
            or self.handoff.context_signal_ids != tuple(sorted(
                (
                    *self.turns[0].signal_ids,
                    *self.turns[1].signal_ids,
                    *self.turns[3].signal_ids,
                ),
                key=str,
            ))
        ):
            raise ValueError("agent_harmony_handoff_round_binding_invalid")
        return self


class HarmonySharedPattern(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    topic_code: str
    client_ids: tuple[HarmonyClientId, ...] = Field(min_length=2, max_length=4)
    aggregate_signal_ids: tuple[UUID4, ...] = Field(min_length=2, max_length=192)
    reuse_scope: Literal["planning_practice_only"] = "planning_practice_only"
    factual_copy_reuse: Literal[False] = False
    client_asset_reuse: Literal[False] = False
    audience_rank_comparison: Literal[False] = False
    next_gate: Literal["human_strategy_review"] = "human_strategy_review"
    automatic_publication: Literal[False] = False

    @field_validator("topic_code")
    @classmethod
    def validate_topic_code(cls, value: str) -> str:
        return _validated_topic_codes((value,))[0]

    @model_validator(mode="after")
    def validate_pattern(self) -> "HarmonySharedPattern":
        if (
            tuple(sorted(self.client_ids)) != self.client_ids
            or len(set(self.client_ids)) != len(self.client_ids)
            or tuple(sorted(self.aggregate_signal_ids, key=str))
            != self.aggregate_signal_ids
        ):
            raise ValueError("agent_harmony_shared_pattern_order_invalid")
        expected = hashlib.sha256(_canonical_json(
            self.model_dump(mode="python", exclude={"pattern_sha256"})
        )).hexdigest()
        if self.pattern_sha256 != expected:
            raise ValueError("agent_harmony_shared_pattern_digest_invalid")
        return self


class HarmonyCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clients: Literal[4] = 4
    participants: int = Field(ge=1, le=64)
    input_signal_claims: int = Field(ge=0, le=256)
    runtime_attested_signals: int = Field(ge=0, le=256)
    test_fixture_signals: int = Field(ge=0, le=256)
    unattested_signal_claims: int = Field(ge=0, le=256)
    expired_attestations: int = Field(ge=0, le=256)
    not_yet_valid_attestations: int = Field(ge=0, le=256)
    accepted_signals: int = Field(ge=0, le=256)
    replayed_signals: int = Field(ge=0, le=256)
    stale_signals: int = Field(ge=0, le=256)
    future_signals: int = Field(ge=0, le=256)
    ready_for_human_scope_review: int = Field(ge=0, le=4)
    waiting_for_attestation: int = Field(ge=0, le=4)
    waiting_for_signals: int = Field(ge=0, le=4)
    needs_alignment: int = Field(ge=0, le=4)
    shared_patterns: int = Field(ge=0, le=64)
    live_harmony_adapters: Literal[0] = 0


class HarmonySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-harmony-control-room@1"] = (
        "agent-harmony-control-room@1"
    )
    workspace_id: UUID4
    observed_at: datetime
    profiles: tuple[HarmonyClientProfile, ...] = Field(min_length=4, max_length=4)
    participants: tuple[HarmonyParticipant, ...] = Field(min_length=1, max_length=64)
    rounds: tuple[HarmonyClientRound, ...] = Field(min_length=4, max_length=4)
    shared_patterns: tuple[HarmonySharedPattern, ...] = Field(default=(), max_length=64)
    counts: HarmonyCounts
    trust_mode: HarmonyTrustMode
    caller_identity_trusted: Literal[False] = False
    attestation_required_for_handoff: Literal[True] = True
    planning_only: Literal[True] = True
    render_only: Literal[True] = True
    portable_trust: Literal[False] = False
    serialized_snapshot_authoritative: Literal[False] = False
    live_adapters_connected: Literal[False] = False
    execution_authorized: Literal[False] = False
    external_calls: Literal[False] = False
    database_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    max_cost_microusd: Literal[0] = 0
    max_external_actions: Literal[0] = 0
    automatic_publication: Literal[False] = False

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_harmony_observed_at_invalid")

    @model_validator(mode="after")
    def validate_snapshot(self) -> "HarmonySnapshot":
        profile_clients = tuple(profile.client_id for profile in self.profiles)
        round_clients = tuple(round_.client_id for round_ in self.rounds)
        participant_ids = tuple(item.participant_id for item in self.participants)
        if (
            profile_clients != HARMONY_CLIENT_IDS
            or round_clients != HARMONY_CLIENT_IDS
            or participant_ids != tuple(sorted(participant_ids))
            or len(set(participant_ids)) != len(participant_ids)
            or tuple(pattern.pattern_sha256 for pattern in self.shared_patterns)
            != tuple(sorted(
                pattern.pattern_sha256 for pattern in self.shared_patterns
            ))
        ):
            raise ValueError("agent_harmony_snapshot_order_invalid")
        if any(
            round_.workspace_id != self.workspace_id for round_ in self.rounds
        ):
            raise ValueError("agent_harmony_snapshot_workspace_invalid")
        trust_issues = tuple(
            issue for round_ in self.rounds for issue in round_.trust_issues
        )
        runtime_attested_signals = sum(
            len(item.active_signal_ids)
            + len(item.stale_signal_ids)
            + len(item.future_signal_ids)
            for item in self.rounds
        )
        expected_counts = HarmonyCounts(
            participants=len(self.participants),
            input_signal_claims=(
                runtime_attested_signals
                + len(trust_issues)
                + self.counts.replayed_signals
            ),
            runtime_attested_signals=runtime_attested_signals,
            test_fixture_signals=sum(
                item.reason == "test_fixture" for item in trust_issues
            ),
            unattested_signal_claims=sum(
                item.reason == "unattested" for item in trust_issues
            ),
            expired_attestations=sum(
                item.reason == "attestation_expired" for item in trust_issues
            ),
            not_yet_valid_attestations=sum(
                item.reason == "attestation_not_yet_valid"
                for item in trust_issues
            ),
            accepted_signals=sum(len(item.active_signal_ids) for item in self.rounds),
            replayed_signals=self.counts.replayed_signals,
            stale_signals=sum(len(item.stale_signal_ids) for item in self.rounds),
            future_signals=sum(len(item.future_signal_ids) for item in self.rounds),
            ready_for_human_scope_review=sum(
                item.status == HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW
                for item in self.rounds
            ),
            waiting_for_attestation=sum(
                item.status == HarmonyRoundStatus.WAITING_FOR_ATTESTATION
                for item in self.rounds
            ),
            waiting_for_signals=sum(
                item.status == HarmonyRoundStatus.WAITING_FOR_SIGNALS
                for item in self.rounds
            ),
            needs_alignment=sum(
                item.status == HarmonyRoundStatus.NEEDS_ALIGNMENT
                for item in self.rounds
            ),
            shared_patterns=len(self.shared_patterns),
        )
        if self.counts != expected_counts:
            raise ValueError("agent_harmony_counts_invalid")
        if any(round_.handoff is not None for round_ in self.rounds) and (
            self.trust_mode
            not in {HarmonyTrustMode.RUNTIME_VERIFIED, HarmonyTrustMode.MIXED}
        ):
            raise ValueError("agent_harmony_handoff_trust_mode_invalid")
        return self

    def canonical_snapshot(self) -> dict[str, object]:
        return _json_value(self.model_dump(mode="python"))  # type: ignore[return-value]

    @property
    def snapshot_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.canonical_snapshot())).hexdigest()

    def as_payload(self) -> dict[str, object]:
        return {**self.canonical_snapshot(), "snapshot_sha256": self.snapshot_sha256}


def _validate_attestation_binding(
    signal: HarmonySignal,
    attestation: HarmonySignalAttestation,
) -> None:
    if (
        attestation.workspace_id != signal.workspace_id
        or attestation.client_id != signal.client_id
        or attestation.signal_id != signal.signal_id
        or attestation.source_event_id != signal.source_event_id
        or attestation.signal_kind != signal.signal_kind
        or attestation.lane != signal.lane
        or attestation.producer_principal_id != signal.producer_principal_id
        or attestation.producer_release_sha != signal.producer_release_sha
        or attestation.config_sha256 != signal.config_sha256
        or attestation.upstream_receipt_sha256
        != signal.upstream_receipt_sha256
        or attestation.evidence_sha256 != signal.evidence_sha256
        or attestation.payload_sha256 != signal.payload_sha256
        or attestation.verified_at < signal.observed_at
    ):
        raise ValueError("agent_harmony_attestation_binding_invalid")


def _resolve_signal_attestations(
    signals: Sequence[HarmonySignal],
    registry: HarmonyAttestationRegistry,
    observed_at: datetime,
) -> tuple[
    tuple[_AttestedHarmonySignal, ...],
    tuple[_HarmonySignalTrustIssue, ...],
]:
    trusted: list[_AttestedHarmonySignal] = []
    issues: list[_HarmonySignalTrustIssue] = []
    for signal in signals:
        attestation = registry.resolve(signal.payload_sha256)
        if attestation is None:
            issues.append(_HarmonySignalTrustIssue(signal, "unattested"))
            continue
        _validate_attestation_binding(signal, attestation)
        if not attestation.runtime_verified:
            issues.append(_HarmonySignalTrustIssue(signal, "test_fixture"))
            continue
        # A future signal is explicitly classified as future below.  It must
        # never be folded into the stale bucket merely because its matching
        # attestation was also issued in the future.
        if signal.observed_at > observed_at:
            trusted.append(_AttestedHarmonySignal(signal, attestation))
        elif attestation.verified_at > observed_at:
            issues.append(_HarmonySignalTrustIssue(
                signal,
                "attestation_not_yet_valid",
            ))
        elif attestation.expires_at <= observed_at:
            issues.append(_HarmonySignalTrustIssue(
                signal,
                "attestation_expired",
            ))
        else:
            trusted.append(_AttestedHarmonySignal(signal, attestation))
    return tuple(trusted), tuple(issues)


def _deduplicate_attested_signals(
    entries: Sequence[_AttestedHarmonySignal],
) -> tuple[tuple[_AttestedHarmonySignal, ...], int]:
    by_key: dict[str, _AttestedHarmonySignal] = {}
    by_id: dict[str, _AttestedHarmonySignal] = {}
    replays = 0
    for entry in entries:
        signal = entry.signal
        signal_id = str(signal.signal_id)
        previous_id = by_id.get(signal_id)
        if previous_id is not None:
            if (
                previous_id.signal.payload_sha256 != signal.payload_sha256
                or previous_id.attestation.attestation_sha256
                != entry.attestation.attestation_sha256
            ):
                raise ValueError("agent_harmony_signal_id_conflict")
            replays += 1
            continue
        previous_key = by_key.get(signal.signal_key_sha256)
        if previous_key is not None:
            if (
                previous_key.signal.payload_sha256 != signal.payload_sha256
                or previous_key.attestation.attestation_sha256
                != entry.attestation.attestation_sha256
            ):
                raise ValueError("agent_harmony_signal_key_conflict")
            replays += 1
            by_id[signal_id] = entry
            continue
        by_key[signal.signal_key_sha256] = entry
        by_id[signal_id] = entry

    official_sources: dict[tuple[str, str], str] = {}
    for entry in by_key.values():
        signal = entry.signal
        if isinstance(signal, OfficialSourceSignal):
            key = (signal.client_id, str(signal.source_item_id))
            previous_hash = official_sources.get(key)
            if previous_hash is not None and previous_hash != signal.source_body_sha256:
                raise ValueError("agent_harmony_official_source_conflict")
            official_sources[key] = signal.source_body_sha256
    return (
        tuple(sorted(
            by_key.values(),
            key=lambda item: (
                item.signal.client_id,
                item.signal.observed_at,
                item.signal.signal_kind.value,
                str(item.signal.signal_id),
            ),
        )),
        replays,
    )


def _signal_manifest(
    entries: Sequence[_AttestedHarmonySignal],
) -> tuple[HarmonySignalManifestEntry, ...]:
    return tuple(sorted(
        (
            HarmonySignalManifestEntry(
                signal_id=entry.signal.signal_id,
                signal_kind=entry.signal.signal_kind,
                lane=entry.signal.lane,
                payload_sha256=entry.signal.payload_sha256,
                attestation_sha256=entry.attestation.attestation_sha256,
                content_factual_authority=(
                    entry.signal.content_factual_authority
                ),
            )
            for entry in entries
        ),
        key=_manifest_sort_key,
    ))


def _turn(
    *,
    client_id: HarmonyClientId,
    sequence: int,
    speaker: str,
    lane: HarmonyLane,
    message_kind: str,
    signals: Sequence[_AttestedHarmonySignal],
    topic_codes: Sequence[str],
) -> HarmonyTurn:
    body: dict[str, object] = {
        "attestation_sha256s": sorted(
            entry.attestation.attestation_sha256 for entry in signals
        ),
        "automatic_publication": False,
        "client_id": client_id,
        "content_factual_authority": (
            lane == HarmonyLane.CONTENT_SOURCE and message_kind == "evidence"
        ),
        "execution_authorized": False,
        "instructions_accepted": False,
        "lane": lane.value,
        "message_kind": message_kind,
        "sequence": sequence,
        "signal_ids": sorted(str(entry.signal.signal_id) for entry in signals),
        "speaker": speaker,
        "topic_codes": sorted(set(topic_codes)),
    }
    return HarmonyTurn.model_validate({
        **body,
        "turn_sha256": hashlib.sha256(_canonical_json(body)).hexdigest(),
    })


def _round_sha256(
    *,
    workspace_id: UUID,
    client_id: str,
    active: Sequence[_AttestedHarmonySignal],
    stale: Sequence[_AttestedHarmonySignal],
    future: Sequence[_AttestedHarmonySignal],
    manifest: Sequence[HarmonySignalManifestEntry],
    trust_issues: Sequence[HarmonySignalTrustIssue],
    consensus: Sequence[str],
    blockers: Sequence[HarmonyBlocker],
    status: HarmonyRoundStatus,
) -> str:
    return hashlib.sha256(_canonical_json({
        "active_signal_ids": sorted(
            str(entry.signal.signal_id) for entry in active
        ),
        "automatic_publication": False,
        "blockers": [item.value for item in blockers],
        "client_id": client_id,
        "consensus_topic_codes": list(consensus),
        "future_signal_ids": sorted(
            str(entry.signal.signal_id) for entry in future
        ),
        "schema_version": "agent-harmony-round@1",
        "signal_manifest": [
            item.model_dump(mode="python") for item in manifest
        ],
        "stale_signal_ids": sorted(
            str(entry.signal.signal_id) for entry in stale
        ),
        "status": status.value,
        "trust_issues": [
            item.model_dump(mode="python") for item in trust_issues
        ],
        "workspace_id": str(workspace_id),
    })).hexdigest()


def _recommended_content_kind(
    profile: HarmonyClientProfile,
    official: Sequence[OfficialSourceSignal],
    quiz: Sequence[QuizLearningSignal],
) -> str:
    eligible = set(profile.supported_content_kinds)
    eligible.intersection_update(*(
        set(signal.eligible_content_kinds) for signal in official
    ))
    if (
        "tutorial" in eligible
        and max(signal.tutorial_priority_basis_points for signal in quiz) >= 6_000
    ):
        return "tutorial"
    if "article" in eligible and any(
        signal.source_kind in {"x_article", "official_document"}
        for signal in official
    ):
        return "article"
    if "daily_news" in eligible:
        return "daily_news"
    if "article" in eligible:
        return "article"
    raise ValueError("agent_harmony_content_capability_missing")


def _handoff(
    *,
    workspace_id: UUID,
    profile: HarmonyClientProfile,
    round_sha256: str,
    active: Sequence[_AttestedHarmonySignal],
    manifest: Sequence[HarmonySignalManifestEntry],
    consensus: Sequence[str],
) -> HarmonyHandoffCandidate:
    official = tuple(
        entry.signal
        for entry in active
        if isinstance(entry.signal, OfficialSourceSignal)
    )
    quiz = tuple(
        entry.signal
        for entry in active
        if isinstance(entry.signal, QuizLearningSignal)
    )
    source_ids = tuple(sorted(
        (
            item.signal_id
            for item in manifest
            if item.lane == HarmonyLane.CONTENT_SOURCE
        ),
        key=str,
    ))
    context_ids = tuple(sorted(
        (
            item.signal_id
            for item in manifest
            if item.lane != HarmonyLane.CONTENT_SOURCE
        ),
        key=str,
    ))
    body: dict[str, object] = {
        "attestation_set_sha256": _manifest_attestation_set_sha256(manifest),
        "attestation_reverification_required": True,
        "automatic_publication": False,
        "channel_scope": "private_control_room",
        "client_id": profile.client_id,
        "context_signal_ids": [str(item) for item in context_ids],
        "data_classification": "aggregate_plus_public_official",
        "execution_authorized": False,
        "dispatchable": False,
        "environment": "preview",
        "external_calls": False,
        "input_set_sha256": _manifest_input_set_sha256(manifest),
        "max_cost_microusd": 0,
        "max_external_actions": 0,
        "next_gate": "human_scope_review",
        "provider_calls": False,
        "portable_trust": False,
        "publication_calls": False,
        "recommended_content_kind": _recommended_content_kind(
            profile,
            official,
            quiz,
        ),
        "requested_capabilities": [
            "independent_qa",
            "prepare_private_content",
            "prepare_private_recap",
        ],
        "round_sha256": round_sha256,
        "schema_version": "agent-harmony-handoff@1",
        "signal_manifest": [
            item.model_dump(mode="python") for item in manifest
        ],
        "source_signal_ids": [str(item) for item in source_ids],
        "topic_codes": list(consensus),
        "workspace_id": str(workspace_id),
    }
    return HarmonyHandoffCandidate.model_validate({
        **body,
        "scope_sha256": hashlib.sha256(_canonical_json(body)).hexdigest(),
    })


def _client_round(
    *,
    workspace_id: UUID,
    profile: HarmonyClientProfile,
    signals: Sequence[_AttestedHarmonySignal],
    trust_issues: Sequence[_HarmonySignalTrustIssue],
    observed_at: datetime,
) -> HarmonyClientRound:
    client_signals = tuple(
        entry
        for entry in signals
        if entry.signal.client_id == profile.client_id
    )
    active = tuple(
        entry
        for entry in client_signals
        if entry.signal.observed_at <= observed_at < entry.signal.expires_at
    )
    stale = tuple(
        entry
        for entry in client_signals
        if entry.signal.expires_at <= observed_at
    )
    future = tuple(
        entry
        for entry in client_signals
        if entry.signal.observed_at > observed_at
    )
    public_trust_issues = tuple(sorted(
        (
            HarmonySignalTrustIssue(
                signal_id=issue.signal.signal_id,
                payload_sha256=issue.signal.payload_sha256,
                lane=issue.signal.lane,
                reason=issue.reason,
            )
            for issue in trust_issues
            if issue.signal.client_id == profile.client_id
        ),
        key=lambda item: (
            item.lane.value,
            item.payload_sha256,
            str(item.signal_id),
            item.reason,
        ),
    ))
    manifest = _signal_manifest(active)
    required_lanes = (
        HarmonyLane.QUIZ_BOT,
        HarmonyLane.COMMUNITY_OPS,
        HarmonyLane.CONTENT_SOURCE,
        HarmonyLane.RECAP,
    )
    by_lane = {
        lane: tuple(
            entry for entry in active if entry.signal.lane == lane
        )
        for lane in required_lanes
    }
    blockers: list[HarmonyBlocker] = []
    missing = (
        (HarmonyLane.QUIZ_BOT, HarmonyBlocker.MISSING_QUIZ_LEARNING),
        (HarmonyLane.COMMUNITY_OPS, HarmonyBlocker.MISSING_COMMUNITY_DEMAND),
        (HarmonyLane.CONTENT_SOURCE, HarmonyBlocker.MISSING_OFFICIAL_SOURCE),
        (HarmonyLane.RECAP, HarmonyBlocker.MISSING_RECAP_METRIC),
    )
    attestation_blocked = False
    issue_blocker = {
        "attestation_expired": HarmonyBlocker.ATTESTATION_EXPIRED,
        "attestation_not_yet_valid": (
            HarmonyBlocker.ATTESTATION_NOT_YET_VALID
        ),
        "test_fixture": HarmonyBlocker.ATTESTATION_ASSURANCE_INSUFFICIENT,
        "unattested": HarmonyBlocker.MISSING_ATTESTATION,
    }
    for lane, missing_blocker in missing:
        if by_lane[lane]:
            # Extra stale, future, fixture, or unattested claims do not veto a
            # fresh runtime-attested lane.
            continue
        blockers.append(missing_blocker)
        lane_issues = tuple(
            issue for issue in public_trust_issues if issue.lane == lane
        )
        if lane_issues:
            attestation_blocked = True
            blockers.extend(issue_blocker[issue.reason] for issue in lane_issues)
        if any(entry.signal.lane == lane for entry in stale):
            blockers.append(HarmonyBlocker.STALE_SIGNAL)
        if any(entry.signal.lane == lane for entry in future):
            blockers.append(HarmonyBlocker.FUTURE_SIGNAL)

    official_topics = {
        topic
        for entry in by_lane[HarmonyLane.CONTENT_SOURCE]
        for topic in entry.signal.topic_codes
    }
    support_count: dict[str, int] = {}
    for lane in (
        HarmonyLane.QUIZ_BOT,
        HarmonyLane.COMMUNITY_OPS,
        HarmonyLane.RECAP,
    ):
        lane_topics = {
            topic
            for entry in by_lane[lane]
            if not isinstance(entry.signal, RecapMetricSignal)
            or any(metric.observed for metric in entry.signal.metrics)
            for topic in entry.signal.topic_codes
        }
        for topic in lane_topics:
            support_count[topic] = support_count.get(topic, 0) + 1
    consensus = tuple(sorted(
        topic
        for topic in official_topics
        if support_count.get(topic, 0) >= 2
    ))
    if not blockers and not consensus:
        blockers.append(HarmonyBlocker.TOPIC_CONSENSUS_MISSING)

    blockers = sorted(set(blockers), key=lambda item: item.value)
    if attestation_blocked:
        status = HarmonyRoundStatus.WAITING_FOR_ATTESTATION
    elif any(
        blocker.value.startswith("missing_") for blocker in blockers
    ):
        status = HarmonyRoundStatus.WAITING_FOR_SIGNALS
    elif blockers:
        status = HarmonyRoundStatus.NEEDS_ALIGNMENT
    else:
        status = HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW
    round_hash = _round_sha256(
        workspace_id=workspace_id,
        client_id=profile.client_id,
        active=active,
        stale=stale,
        future=future,
        manifest=manifest,
        trust_issues=public_trust_issues,
        consensus=consensus,
        blockers=blockers,
        status=status,
    )

    turns: list[HarmonyTurn] = []
    lane_specs = (
        (HarmonyLane.QUIZ_BOT, f"{profile.client_id}_quiz_bot", "proposal"),
        (HarmonyLane.COMMUNITY_OPS, "easyfarm_community_ops", "support"),
        (HarmonyLane.CONTENT_SOURCE, "content_engine", "evidence"),
        (HarmonyLane.RECAP, "coineasy_recap", "support"),
    )
    for sequence, (lane, speaker, normal_kind) in enumerate(lane_specs, start=1):
        lane_signals = by_lane[lane]
        if not lane_signals:
            message_kind = "evidence_request"
        elif lane == HarmonyLane.CONTENT_SOURCE:
            message_kind = normal_kind
        elif consensus and any(
            set(entry.signal.topic_codes) & set(consensus)
            for entry in lane_signals
        ):
            message_kind = normal_kind
        else:
            message_kind = "challenge"
        turns.append(_turn(
            client_id=profile.client_id,
            sequence=sequence,
            speaker=speaker,
            lane=lane,
            message_kind=message_kind,
            signals=lane_signals,
            topic_codes=sorted({
                topic
                for entry in lane_signals
                for topic in entry.signal.topic_codes
            }),
        ))
    turns.append(_turn(
        client_id=profile.client_id,
        sequence=5,
        speaker="grok_bot",
        lane=HarmonyLane.COORDINATOR,
        message_kind=(
            "handoff"
            if status == HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW
            else "challenge"
        ),
        signals=active,
        topic_codes=consensus,
    ))
    turns.append(_turn(
        client_id=profile.client_id,
        sequence=6,
        speaker="codex",
        lane=HarmonyLane.INDEPENDENT_QA,
        message_kind=(
            "verification"
            if status == HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW
            else "evidence_request"
        ),
        signals=active,
        topic_codes=consensus,
    ))
    handoff = None
    if status == HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW:
        handoff = _handoff(
            workspace_id=workspace_id,
            profile=profile,
            round_sha256=round_hash,
            active=active,
            manifest=manifest,
            consensus=consensus,
        )
    return HarmonyClientRound(
        workspace_id=workspace_id,
        client_id=profile.client_id,
        round_sha256=round_hash,
        status=status,
        active_signal_ids=tuple(sorted(
            (entry.signal.signal_id for entry in active),
            key=str,
        )),
        stale_signal_ids=tuple(sorted(
            (entry.signal.signal_id for entry in stale),
            key=str,
        )),
        future_signal_ids=tuple(sorted(
            (entry.signal.signal_id for entry in future),
            key=str,
        )),
        signal_manifest=manifest,
        trust_issues=public_trust_issues,
        consensus_topic_codes=consensus,
        blockers=tuple(blockers),
        turns=tuple(turns),
        handoff=handoff,
    )


def _shared_patterns(
    signals: Sequence[_AttestedHarmonySignal],
    observed_at: datetime,
) -> tuple[HarmonySharedPattern, ...]:
    active_aggregate = tuple(
        entry.signal
        for entry in signals
        if entry.signal.observed_at <= observed_at < entry.signal.expires_at
        and not isinstance(entry.signal, OfficialSourceSignal)
        and (
            not isinstance(entry.signal, RecapMetricSignal)
            or any(metric.observed for metric in entry.signal.metrics)
        )
    )
    topics: dict[str, list[HarmonySignal]] = {}
    for signal in active_aggregate:
        for topic in signal.topic_codes:
            topics.setdefault(topic, []).append(signal)
    patterns: list[HarmonySharedPattern] = []
    for topic, topic_signals in topics.items():
        clients = tuple(sorted({signal.client_id for signal in topic_signals}))
        if len(clients) < 2:
            continue
        signal_ids = tuple(sorted(
            {signal.signal_id for signal in topic_signals},
            key=str,
        ))
        body: dict[str, object] = {
            "aggregate_signal_ids": [str(item) for item in signal_ids],
            "audience_rank_comparison": False,
            "automatic_publication": False,
            "client_asset_reuse": False,
            "client_ids": list(clients),
            "factual_copy_reuse": False,
            "next_gate": "human_strategy_review",
            "reuse_scope": "planning_practice_only",
            "topic_code": topic,
        }
        patterns.append(HarmonySharedPattern.model_validate({
            **body,
            "pattern_sha256": hashlib.sha256(_canonical_json(body)).hexdigest(),
        }))
    return tuple(sorted(patterns, key=lambda item: item.pattern_sha256))


def build_harmony_snapshot(
    harmony_input: HarmonyInput,
    profiles: Sequence[HarmonyClientProfile],
    *,
    observed_at: datetime,
    attestation_registry: HarmonyAttestationRegistry | None = None,
) -> HarmonySnapshot:
    normalized_observed_at = _utc_seconds(
        observed_at,
        "agent_harmony_observed_at_invalid",
    )
    ordered_profiles = tuple(sorted(profiles, key=lambda item: item.client_id))
    if tuple(profile.client_id for profile in ordered_profiles) != HARMONY_CLIENT_IDS:
        raise ValueError("agent_harmony_connector_registry_incomplete")
    registry = (
        attestation_registry
        if attestation_registry is not None
        else EmptyHarmonyAttestationRegistry()
    )
    resolved, trust_issues = _resolve_signal_attestations(
        harmony_input.signals,
        registry,
        normalized_observed_at,
    )
    signals, replayed = _deduplicate_attested_signals(resolved)
    if any(
        entry.signal.workspace_id != harmony_input.workspace_id
        for entry in signals
    ):
        raise ValueError("agent_harmony_workspace_binding_invalid")
    rounds = tuple(
        _client_round(
            workspace_id=harmony_input.workspace_id,
            profile=profile,
            signals=signals,
            trust_issues=trust_issues,
            observed_at=normalized_observed_at,
        )
        for profile in ordered_profiles
    )
    participants = harmony_participants()
    shared = _shared_patterns(signals, normalized_observed_at)
    public_trust_issues = tuple(
        issue for round_ in rounds for issue in round_.trust_issues
    )
    runtime_attested_signals = sum(
        len(item.active_signal_ids)
        + len(item.stale_signal_ids)
        + len(item.future_signal_ids)
        for item in rounds
    )
    counts = HarmonyCounts(
        participants=len(participants),
        input_signal_claims=len(harmony_input.signals),
        runtime_attested_signals=runtime_attested_signals,
        test_fixture_signals=sum(
            item.reason == "test_fixture" for item in public_trust_issues
        ),
        unattested_signal_claims=sum(
            item.reason == "unattested" for item in public_trust_issues
        ),
        expired_attestations=sum(
            item.reason == "attestation_expired"
            for item in public_trust_issues
        ),
        not_yet_valid_attestations=sum(
            item.reason == "attestation_not_yet_valid"
            for item in public_trust_issues
        ),
        accepted_signals=sum(len(item.active_signal_ids) for item in rounds),
        replayed_signals=replayed,
        stale_signals=sum(len(item.stale_signal_ids) for item in rounds),
        future_signals=sum(len(item.future_signal_ids) for item in rounds),
        ready_for_human_scope_review=sum(
            item.status == HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW
            for item in rounds
        ),
        waiting_for_attestation=sum(
            item.status == HarmonyRoundStatus.WAITING_FOR_ATTESTATION
            for item in rounds
        ),
        waiting_for_signals=sum(
            item.status == HarmonyRoundStatus.WAITING_FOR_SIGNALS
            for item in rounds
        ),
        needs_alignment=sum(
            item.status == HarmonyRoundStatus.NEEDS_ALIGNMENT
            for item in rounds
        ),
        shared_patterns=len(shared),
    )
    return HarmonySnapshot(
        workspace_id=harmony_input.workspace_id,
        observed_at=normalized_observed_at,
        profiles=ordered_profiles,
        participants=participants,
        rounds=rounds,
        shared_patterns=shared,
        counts=counts,
        trust_mode=registry.trust_mode,
    )


_STATUS_KO = {
    HarmonyRoundStatus.WAITING_FOR_ATTESTATION: "신뢰 증명 대기",
    HarmonyRoundStatus.WAITING_FOR_SIGNALS: "신호 대기",
    HarmonyRoundStatus.NEEDS_ALIGNMENT: "의견 조율",
    HarmonyRoundStatus.READY_FOR_HUMAN_SCOPE_REVIEW: "대표 범위 승인 대기",
}


def render_harmony_dashboard(snapshot: HarmonySnapshot) -> str:
    profile_map = {profile.client_id: profile for profile in snapshot.profiles}
    participant_lines = "\n".join(
        f"- `{item.participant_id}`: `{item.participant_kind}` / "
        f"연결 `{item.connection_state}` / 발행 `불가`"
        for item in snapshot.participants
    )
    round_sections: list[str] = []
    for round_ in snapshot.rounds:
        profile = profile_map[round_.client_id]
        turns = "\n".join(
            f"  {turn.sequence}. `{turn.speaker}` → `{turn.message_kind}` "
            f"({', '.join(turn.topic_codes) or '관측 없음'})"
            for turn in round_.turns
        )
        blockers = ", ".join(item.value for item in round_.blockers) or "없음"
        trust_issues = ", ".join(
            item.reason for item in round_.trust_issues
        ) or "없음"
        handoff = (
            f"`{round_.handoff.recommended_content_kind}` / "
            f"scope `{round_.handoff.scope_sha256}` / runtime 증명 "
            f"{len(round_.handoff.signal_manifest)}건"
            if round_.handoff is not None
            else "생성 안 됨 (runtime 증명 없이는 생성 불가)"
        )
        round_sections.append(
            f"### {profile.display_name} (`{round_.client_id}`)\n\n"
            f"- 상태: **{_STATUS_KO[round_.status]}**\n"
            f"- runtime 활성/오래된/미래 신호: "
            f"{len(round_.active_signal_ids)}/"
            f"{len(round_.stale_signal_ids)}/"
            f"{len(round_.future_signal_ids)}\n"
            f"- 신뢰 제외 사유: {trust_issues}\n"
            f"- 합의 주제: {', '.join(round_.consensus_topic_codes) or '없음'}\n"
            f"- 차단 사유: {blockers}\n"
            f"- handoff: {handoff}\n\n"
            f"{turns}"
        )
    shared_lines = "\n".join(
        f"- `{item.topic_code}`: {', '.join(item.client_ids)} / "
        "기획 관행만 공유, 사실·카피·브랜드 자산 공유 금지"
        for item in snapshot.shared_patterns
    ) or "- 교차 고객 공유 후보 없음"
    return f"""# CoinEasy Harmony 운영실

## 1. 오늘의 오케스트라

- 고객: {snapshot.counts.clients}개 (Babylon, OriginTrail, Squid, Yellow)
- 참여 역할: {snapshot.counts.participants}개
- caller 신호 주장: {snapshot.counts.input_signal_claims}건
- runtime 증명 신호: {snapshot.counts.runtime_attested_signals}건 (현재 활성 {snapshot.counts.accepted_signals}건)
- 미증명/test fixture/만료·미래 증명: {snapshot.counts.unattested_signal_claims}/{snapshot.counts.test_fixture_signals}/{snapshot.counts.expired_attestations + snapshot.counts.not_yet_valid_attestations}건
- 검증된 runtime input replay 제거: {snapshot.counts.replayed_signals}건
- trust mode: `{snapshot.trust_mode.value}`
- caller JSON의 client/producer/release/config/receipt 자기진술: `신뢰하지 않음`
- live Harmony adapter: `0` — 로컬 구조 projection, live identity 검증 없음
- snapshot/handoff: `render-only / dispatch 불가 / portable trust 없음`
- 자동 발행/외부 호출/비용: `OFF / 0 / 0`

## 2. 참여자와 연결 상태

{participant_lines}

## 3. 고객별 구조화 대화 · 기획

{chr(10).join(round_sections)}

## 4. 대표 승인함

- 범위 승인 대기: {snapshot.counts.ready_for_human_scope_review}건
- 신뢰 증명 대기: {snapshot.counts.waiting_for_attestation}건
- 신호 대기: {snapshot.counts.waiting_for_signals}건
- 의견 조율: {snapshot.counts.needs_alignment}건

`handoff`는 JWT 또는 immutable database receipt를 검증한 별도 registry가
정확히 결합한 입력만으로 만드는 기획 제안입니다. 기본 CLI registry는
비어 있으므로 caller JSON만으로는 handoff가 생성되지 않습니다. 기존 업무
원장 승인, provider 실행, 메시지, 배포 또는 publication 권한도 부여하지
않습니다. serialized snapshot을 다른 프로세스가 소비하려면 trusted registry로
attestation을 다시 검증해야 합니다. 아래 6턴은 구조화된 계약 projection이며
실제 봇 대화가 아닙니다.

## 5. 교차 고객 학습 · 비용 · 완료

{shared_lines}

- 관측 비용: 0 microusd (이 projection 자체)
- Production/DB/provider/Buzz/publication 호출: 0
- Snapshot SHA-256: `{snapshot.snapshot_sha256}`
"""


__all__ = [
    "CommunityDemandSignal",
    "EmptyHarmonyAttestationRegistry",
    "FrozenHarmonyAttestationRegistry",
    "HARMONY_CLIENT_IDS",
    "HARMONY_TOPIC_CODES",
    "HarmonyAttestationRegistry",
    "HarmonyBlocker",
    "HarmonyClientProfile",
    "HarmonyClientRound",
    "HarmonyCounts",
    "HarmonyHandoffCandidate",
    "HarmonyInput",
    "HarmonyLane",
    "HarmonyMetric",
    "HarmonyParticipant",
    "HarmonyRoundStatus",
    "HarmonySharedPattern",
    "HarmonySignal",
    "HarmonySignalAttestation",
    "HarmonySignalManifestEntry",
    "HarmonySignalTrustIssue",
    "HarmonySignalKind",
    "HarmonySnapshot",
    "HarmonyTrustMode",
    "HarmonyTurn",
    "OfficialSourceSignal",
    "QuizLearningSignal",
    "RecapMetricSignal",
    "bind_harmony_signal_attestation",
    "bind_harmony_signal_payload",
    "build_harmony_snapshot",
    "harmony_participants",
    "harmony_attestation_sha256",
    "harmony_payload_sha256",
    "load_harmony_client_profiles",
    "render_harmony_dashboard",
]
