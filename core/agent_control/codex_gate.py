from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import Enum
from threading import RLock
from typing import Literal, Mapping
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

from .models import _utc_seconds
from .preview_collaboration import PreviewHarmonyStage, PreviewHarmonyStageReceipt


_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_RELEASE_SHA_PATTERN = r"^[a-f0-9]{40}$"
_BRANCH_REF_PATTERN = r"^[a-z0-9]{20}$"
_MAX_CLAIM_ATTEMPTS = 3
_MAX_LEASE_DURATION = timedelta(minutes=15)


class SquidCodexGateState(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    ATTEMPT_STARTED = "attempt_started"
    RESULT_SUBMITTED = "result_submitted"
    VERIFIED = "verified"
    OPERATOR_REVIEW_PENDING = "operator_review_pending"
    NEEDS_CHANGES = "needs_changes"
    BLOCKED = "blocked"
    OUTCOME_UNKNOWN = "outcome_unknown"


class SquidCodexGateVerdict(str, Enum):
    PASS = "pass"
    NEEDS_CHANGES = "needs_changes"
    BLOCKED = "blocked"


class SquidCodexGateCostObservation(str, Enum):
    OBSERVED = "observed"
    UNOBSERVED = "unobserved"


class SquidCodexGateTerminalReason(str, Enum):
    CLAIM_LIMIT_EXHAUSTED = "claim_limit_exhausted"
    RESULT_NEEDS_CHANGES = "result_needs_changes"
    RESULT_BLOCKED = "result_blocked"
    RESULT_RECEIPT_MISSING = "result_receipt_missing"


SquidCodexQaFindingCode = Literal[
    "automatic_publication_enabled",
    "evidence_incomplete",
    "external_call_detected",
    "factual_binding_failed",
    "language_or_brand_mismatch",
    "private_boundary_failed",
    "review_execution_blocked",
    "source_version_stale",
    "unsupported_claim",
]


class SquidCodexGateError(ValueError):
    """Stable, non-secret state-machine error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _sha256(value: object) -> str:
    encoded = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _db_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("squid_codex_gate_binding_time_invalid")
    normalized = value
    return normalized.isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def squid_codex_specialist_binding_sha256(
    payload: Mapping[str, object],
) -> str:
    """Mirror harmony_preview_specialist_binding_sha without credentials."""

    expires_at = payload["expires_at"]
    if not isinstance(expires_at, datetime):
        raise ValueError("squid_codex_gate_binding_time_invalid")
    return _sha256({
        "actor": payload["actor"],
        "branch_ref": payload["branch_ref"],
        "capability": payload["capability"],
        "client_id": payload.get("client_id", "squid"),
        "config_sha256": payload["config_sha256"],
        "expires_at": _db_timestamp(expires_at),
        "principal_id": payload["principal_id"],
        "producer_release_sha": payload["producer_release_sha"],
        "role": payload["role_name"],
        "schema_version": "harmony-fixed-specialist-binding@1",
        "specialist_code": payload["specialist_code"],
        "stage": payload["stage"],
        "workspace_id": payload["workspace_id"],
    })


def bind_squid_codex_specialist_binding(
    payload: Mapping[str, object],
) -> dict[str, object]:
    bound = dict(payload)
    bound.setdefault("schema_version", "harmony-fixed-specialist-binding@1")
    bound.setdefault("client_id", "squid")
    bound["binding_sha256"] = squid_codex_specialist_binding_sha256(bound)
    return bound


class SquidCodexSpecialistBinding(BaseModel):
    """Exact fixed-specialist registration projected from Preview DB."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["harmony-fixed-specialist-binding@1"] = (
        "harmony-fixed-specialist-binding@1"
    )
    branch_ref: str = Field(pattern=_BRANCH_REF_PATTERN)
    workspace_id: UUID4
    client_id: Literal["squid"] = "squid"
    stage: Literal["private_content", "independent_qa"]
    specialist_code: Literal[
        "squid_private_content_producer",
        "squid_independent_qa",
    ]
    role_name: Literal["coineasy_harmony_content", "coineasy_harmony_qa"]
    capability: Literal[
        "harmony_prepare_private_content",
        "harmony_independent_qa",
    ]
    actor: Literal["content_engine", "codex"]
    principal_id: UUID4
    producer_release_sha: str = Field(pattern=_RELEASE_SHA_PATTERN)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    created_at: datetime
    expires_at: datetime
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("squid_codex_gate_binding_time_invalid")
        return value

    @model_validator(mode="after")
    def validate_binding(self) -> "SquidCodexSpecialistBinding":
        expected_role = {
            "private_content": (
                "squid_private_content_producer",
                "coineasy_harmony_content",
                "harmony_prepare_private_content",
                "content_engine",
            ),
            "independent_qa": (
                "squid_independent_qa",
                "coineasy_harmony_qa",
                "harmony_independent_qa",
                "codex",
            ),
        }[self.stage]
        if (
            self.specialist_code,
            self.role_name,
            self.capability,
            self.actor,
        ) != expected_role:
            raise ValueError("squid_codex_gate_specialist_role_invalid")
        if (
            self.expires_at <= self.created_at
            or self.expires_at - self.created_at > timedelta(hours=2)
        ):
            raise ValueError("squid_codex_gate_binding_time_invalid")
        expected = squid_codex_specialist_binding_sha256(
            self.model_dump(mode="python", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("squid_codex_gate_specialist_binding_invalid")
        return self


def squid_codex_source_lineage_sha256(
    payload: Mapping[str, object],
) -> str:
    canonical = dict(payload)
    canonical.pop("lineage_sha256", None)
    return _sha256(canonical)


def bind_squid_codex_source_lineage(
    payload: Mapping[str, object],
) -> dict[str, object]:
    bound = dict(payload)
    bound.setdefault(
        "schema_version",
        "squid-codex-source-lineage-receipt@1",
    )
    bound.setdefault("client_id", "squid")
    bound.setdefault("branch_fence_active", True)
    bound.setdefault("status", "needs_review")
    bound.setdefault("private_content_only", True)
    bound.setdefault("automatic_publication", False)
    bound.setdefault("database_currentness_required", True)
    bound["lineage_sha256"] = squid_codex_source_lineage_sha256(bound)
    return bound


class SquidCodexSourceLineageReceipt(BaseModel):
    """Expiring, hash-bound DB projection of the current official source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["squid-codex-source-lineage-receipt@1"] = (
        "squid-codex-source-lineage-receipt@1"
    )
    branch_ref: str = Field(pattern=_BRANCH_REF_PATTERN)
    branch_fence_active: Literal[True] = True
    branch_fence_created_at: datetime
    branch_fence_expires_at: datetime
    observed_at: datetime
    workspace_id: UUID4
    client_id: Literal["squid"] = "squid"
    round_id: UUID4
    plan_id: UUID4
    signal_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    signal_input_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    signal_producer_principal_ids: tuple[UUID4, ...] = Field(
        min_length=4,
        max_length=4,
    )
    source_signal_id: UUID4
    source_signal_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_producer_principal_id: UUID4
    source_signal_expires_at: datetime
    connector_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    upstream_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    official_content_version_id: UUID4
    official_source_item_id: UUID4
    official_source_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    content_snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: Literal["needs_review"] = "needs_review"
    private_content_only: Literal[True] = True
    automatic_publication: Literal[False] = False
    database_currentness_required: Literal[True] = True
    lineage_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "branch_fence_created_at",
        "branch_fence_expires_at",
        "observed_at",
        "source_signal_expires_at",
    )
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "squid_codex_gate_source_time_invalid")

    @model_validator(mode="after")
    def validate_lineage(self) -> "SquidCodexSourceLineageReceipt":
        signal_principals = self.signal_producer_principal_ids
        if not (
            self.branch_fence_created_at
            <= self.observed_at
            < self.branch_fence_expires_at
            and self.observed_at < self.source_signal_expires_at
            <= self.branch_fence_expires_at
            and self.branch_fence_expires_at
            - self.branch_fence_created_at <= timedelta(hours=2)
        ):
            raise ValueError("squid_codex_gate_source_time_invalid")
        if (
            self.upstream_receipt_sha256
            != self.official_source_binding_sha256
        ):
            raise ValueError("squid_codex_gate_source_binding_invalid")
        if (
            tuple(sorted(signal_principals, key=str)) != signal_principals
            or len(set(signal_principals)) != len(signal_principals)
            or self.source_producer_principal_id not in signal_principals
        ):
            raise ValueError("squid_codex_gate_producer_set_invalid")
        expected = squid_codex_source_lineage_sha256(
            self.model_dump(mode="python", exclude={"lineage_sha256"})
        )
        if self.lineage_sha256 != expected:
            raise ValueError("squid_codex_gate_source_lineage_invalid")
        return self


def _nested_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = _json_value(payload[key])
    if not isinstance(value, Mapping):
        raise ValueError("squid_codex_gate_request_shape_invalid")
    return value


def squid_codex_gate_work_key(payload: Mapping[str, object]) -> str:
    """Stable logical QA identity; reviewer assignment never participates."""

    plan = _nested_mapping(payload, "plan_receipt")
    private_content = _nested_mapping(payload, "private_content_receipt")
    source = _nested_mapping(payload, "source_lineage")
    return _sha256({
        "client_id": payload.get("client_id", "squid"),
        "content_snapshot_sha256": source["content_snapshot_sha256"],
        "official_content_version_id": source["official_content_version_id"],
        "official_source_binding_sha256": (
            source["official_source_binding_sha256"]
        ),
        "official_source_item_id": source["official_source_item_id"],
        "plan_id": payload["plan_id"],
        "plan_receipt_sha256": plan["receipt_sha256"],
        "private_content_output_sha256": private_content["output_sha256"],
        "private_content_receipt_sha256": private_content["receipt_sha256"],
        "round_id": payload["round_id"],
        "schema_version": "squid-codex-gate-work@1",
        "signal_input_set_sha256": source["signal_input_set_sha256"],
        "signal_manifest_sha256": source["signal_manifest_sha256"],
        "signal_producer_principal_ids": (
            source["signal_producer_principal_ids"]
        ),
        "stage": "independent_qa",
        "workspace_id": payload["workspace_id"],
    })


def squid_codex_gate_assignment_key(payload: Mapping[str, object]) -> str:
    reviewer = _nested_mapping(payload, "reviewer_binding")
    return _sha256({
        "reviewer_binding_sha256": reviewer["binding_sha256"],
        "schema_version": "squid-codex-gate-assignment@1",
        "work_key": payload["work_key"],
    })


_REQUEST_DEFAULTS: dict[str, object] = {
    "schema_version": "squid-codex-gate-request@1",
    "client_id": "squid",
    "specialist_code": "squid_independent_qa",
    "automatic_publication": False,
    "provider_calls": False,
    "external_calls": False,
    "publication_calls": False,
}


def squid_codex_gate_request_key(payload: Mapping[str, object]) -> str:
    """Hash the immutable execution assignment, never any review result."""

    canonical = dict(_REQUEST_DEFAULTS)
    canonical.update(payload)
    canonical.pop("request_key", None)
    canonical.pop("assignment_key", None)
    canonical.pop("work_key", None)
    return _sha256(canonical)


def bind_squid_codex_gate_request(
    payload: Mapping[str, object],
) -> dict[str, object]:
    bound = dict(_REQUEST_DEFAULTS)
    bound.update(payload)
    bound["work_key"] = squid_codex_gate_work_key(bound)
    bound["assignment_key"] = squid_codex_gate_assignment_key(bound)
    bound["request_key"] = squid_codex_gate_request_key(bound)
    return bound


class SquidCodexGateRequest(BaseModel):
    """Immutable, pre-attempt identity for one Squid QA review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["squid-codex-gate-request@1"] = (
        "squid-codex-gate-request@1"
    )
    work_key: str = Field(pattern=_SHA256_PATTERN)
    assignment_key: str = Field(pattern=_SHA256_PATTERN)
    request_key: str = Field(pattern=_SHA256_PATTERN)
    workspace_id: UUID4
    client_id: Literal["squid"] = "squid"
    round_id: UUID4
    plan_id: UUID4
    plan_receipt: PreviewHarmonyStageReceipt
    private_content_receipt: PreviewHarmonyStageReceipt
    private_content_binding: SquidCodexSpecialistBinding
    source_lineage: SquidCodexSourceLineageReceipt
    reviewer_binding: SquidCodexSpecialistBinding
    signal_producer_principal_ids: tuple[UUID4, ...] = Field(
        min_length=4,
        max_length=4,
    )
    specialist_code: Literal["squid_independent_qa"] = "squid_independent_qa"
    approved_cost_cap_microusd: int = Field(ge=0)
    automatic_publication: StrictBool = False
    provider_calls: StrictBool = False
    external_calls: StrictBool = False
    publication_calls: StrictBool = False

    def canonical_input(self) -> dict[str, object]:
        return self.model_dump(
            mode="python",
            exclude={"assignment_key", "request_key", "work_key"},
        )

    @property
    def reviewer_principal_id(self) -> UUID:
        return self.reviewer_binding.principal_id

    @property
    def reviewer_release_sha(self) -> str:
        return self.reviewer_binding.producer_release_sha

    @property
    def reviewer_config_sha256(self) -> str:
        return self.reviewer_binding.config_sha256

    @property
    def private_content_receipt_id(self) -> UUID:
        return self.private_content_receipt.receipt_id

    @property
    def private_content_receipt_sha256(self) -> str:
        return self.private_content_receipt.receipt_sha256

    @property
    def private_content_producer_principal_id(self) -> UUID:
        return self.private_content_receipt.principal_id

    @property
    def private_content_output_sha256(self) -> str:
        return self.private_content_receipt.output_sha256

    @property
    def effective_expires_at(self) -> datetime:
        return min(
            self.private_content_binding.expires_at,
            self.reviewer_binding.expires_at,
            self.source_lineage.branch_fence_expires_at,
            self.source_lineage.source_signal_expires_at,
        )

    @property
    def logical_scope(self) -> tuple[UUID, str, UUID, str]:
        return (
            self.workspace_id,
            self.client_id,
            self.plan_id,
            "independent_qa",
        )

    @model_validator(mode="after")
    def validate_request(self) -> "SquidCodexGateRequest":
        plan = self.plan_receipt
        private_content = self.private_content_receipt
        source = self.source_lineage
        private_binding = self.private_content_binding
        reviewer_binding = self.reviewer_binding
        scope = (self.workspace_id, self.client_id, self.round_id, self.plan_id)
        if (
            plan.stage != PreviewHarmonyStage.PLAN
            or private_content.stage != PreviewHarmonyStage.PRIVATE_CONTENT
            or (
                plan.workspace_id,
                plan.client_id,
                plan.round_id,
                plan.plan_id,
            ) != scope
            or (
                private_content.workspace_id,
                private_content.client_id,
                private_content.round_id,
                private_content.plan_id,
            ) != scope
            or private_content.previous_receipt_sha256 != plan.receipt_sha256
            or private_content.input_sha256 != plan.output_sha256
            or plan.input_sha256 != source.signal_input_set_sha256
        ):
            raise ValueError("squid_codex_gate_stage_lineage_invalid")
        if (
            private_binding.stage != "private_content"
            or private_binding.branch_ref != source.branch_ref
            or private_binding.workspace_id != self.workspace_id
            or private_binding.principal_id != private_content.principal_id
            or private_binding.producer_release_sha
            != private_content.producer_release_sha
            or private_binding.config_sha256 != private_content.config_sha256
            or private_binding.binding_sha256
            != private_content.specialist_binding_sha256
        ):
            raise ValueError("squid_codex_gate_private_binding_invalid")
        if (
            reviewer_binding.stage != "independent_qa"
            or reviewer_binding.branch_ref != source.branch_ref
            or reviewer_binding.workspace_id != self.workspace_id
        ):
            raise ValueError("squid_codex_gate_reviewer_binding_invalid")
        if (
            source.workspace_id,
            source.client_id,
            source.round_id,
            source.plan_id,
        ) != scope:
            raise ValueError("squid_codex_gate_source_scope_invalid")
        observed_at = source.observed_at
        if not (
            source.branch_fence_created_at
            <= plan.recorded_at
            <= private_content.recorded_at
            <= observed_at
            and source.branch_fence_created_at
            <= private_binding.created_at
            <= private_content.recorded_at
            < private_binding.expires_at
            and private_content.recorded_at <= observed_at
            < private_binding.expires_at
            and source.branch_fence_created_at
            <= reviewer_binding.created_at <= observed_at
            < reviewer_binding.expires_at
            and private_binding.expires_at <= source.branch_fence_expires_at
            and reviewer_binding.expires_at <= source.branch_fence_expires_at
        ):
            raise ValueError("squid_codex_gate_binding_time_invalid")
        signal_principals = self.signal_producer_principal_ids
        if (
            tuple(sorted(signal_principals, key=str)) != signal_principals
            or len(set(signal_principals)) != len(signal_principals)
            or signal_principals != source.signal_producer_principal_ids
        ):
            raise ValueError("squid_codex_gate_producer_set_invalid")
        upstream_principals = {
            plan.principal_id,
            private_content.principal_id,
            *signal_principals,
        }
        if reviewer_binding.principal_id in upstream_principals:
            raise ValueError("squid_codex_gate_self_review_forbidden")
        if any((
            self.automatic_publication,
            self.provider_calls,
            self.external_calls,
            self.publication_calls,
        )):
            raise ValueError("squid_codex_gate_side_effect_forbidden")
        canonical = self.canonical_input()
        expected_work_key = squid_codex_gate_work_key(canonical)
        if self.work_key != expected_work_key:
            raise ValueError("squid_codex_gate_work_key_invalid")
        expected_assignment_key = squid_codex_gate_assignment_key({
            **canonical,
            "work_key": self.work_key,
        })
        if self.assignment_key != expected_assignment_key:
            raise ValueError("squid_codex_gate_assignment_key_invalid")
        if self.request_key != squid_codex_gate_request_key(
            canonical
        ):
            raise ValueError("squid_codex_gate_request_key_invalid")
        return self


class SquidCodexQaCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    automatic_publication_off: StrictBool
    factual_binding: StrictBool
    no_external_calls: StrictBool
    output_contract_valid: StrictBool
    private_boundary_preserved: StrictBool
    source_lineage_complete: StrictBool


def squid_codex_qa_evidence_sha256(
    payload: Mapping[str, object] | BaseModel,
) -> str:
    canonical = (
        payload.model_dump(mode="python")
        if isinstance(payload, BaseModel)
        else dict(payload)
    )
    canonical.pop("evidence_sha256", None)
    return _sha256(canonical)


def bind_squid_codex_qa_evidence(
    payload: Mapping[str, object],
) -> dict[str, object]:
    bound = dict(payload)
    bound.setdefault(
        "schema_version",
        "squid-codex-semantic-qa-evidence@1",
    )
    bound.setdefault("verifier_contract_version", "squid-codex-semantic-qa@1")
    bound.setdefault("raw_private_content_included", False)
    bound.setdefault("credentials_included", False)
    bound.setdefault("automatic_publication", False)
    bound.setdefault("provider_calls", False)
    bound.setdefault("external_calls", False)
    bound.setdefault("publication_calls", False)
    bound["evidence_sha256"] = squid_codex_qa_evidence_sha256(bound)
    return bound


class SquidCodexSemanticQaEvidence(BaseModel):
    """Typed semantic-QA evidence; it never contains content or free text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["squid-codex-semantic-qa-evidence@1"] = (
        "squid-codex-semantic-qa-evidence@1"
    )
    work_key: str = Field(pattern=_SHA256_PATTERN)
    assignment_key: str = Field(pattern=_SHA256_PATTERN)
    request_key: str = Field(pattern=_SHA256_PATTERN)
    attempt_fence_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_lineage_sha256: str = Field(pattern=_SHA256_PATTERN)
    private_content_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewed_output_sha256: str = Field(pattern=_SHA256_PATTERN)
    official_content_version_id: UUID4
    official_source_item_id: UUID4
    official_source_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    content_snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewer_principal_id: UUID4
    reviewer_specialist_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewer_release_sha: str = Field(pattern=_RELEASE_SHA_PATTERN)
    reviewer_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    qa_output_sha256: str = Field(pattern=_SHA256_PATTERN)
    criteria: SquidCodexQaCriteria
    findings: tuple[SquidCodexQaFindingCode, ...] = Field(max_length=9)
    verdict: SquidCodexGateVerdict
    verifier_contract_version: Literal["squid-codex-semantic-qa@1"] = (
        "squid-codex-semantic-qa@1"
    )
    raw_private_content_included: Literal[False] = False
    credentials_included: Literal[False] = False
    automatic_publication: Literal[False] = False
    provider_calls: Literal[False] = False
    external_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_evidence(self) -> "SquidCodexSemanticQaEvidence":
        if (
            tuple(sorted(self.findings)) != self.findings
            or len(set(self.findings)) != len(self.findings)
        ):
            raise ValueError("squid_codex_gate_evidence_findings_invalid")
        criteria_all_true = all(self.criteria.model_dump().values())
        if self.verdict == SquidCodexGateVerdict.PASS:
            if self.findings or not criteria_all_true:
                raise ValueError("squid_codex_gate_evidence_verdict_invalid")
        elif not self.findings:
            raise ValueError("squid_codex_gate_evidence_verdict_invalid")
        expected = squid_codex_qa_evidence_sha256(self)
        if self.evidence_sha256 != expected:
            raise ValueError("squid_codex_gate_evidence_digest_invalid")
        return self

    def assert_request_binding(
        self,
        request: SquidCodexGateRequest,
        attempt_fence_sha256: str,
    ) -> None:
        source = request.source_lineage
        expected = (
            request.work_key,
            request.assignment_key,
            request.request_key,
            attempt_fence_sha256,
            source.lineage_sha256,
            request.private_content_receipt.receipt_sha256,
            request.private_content_receipt.output_sha256,
            source.official_content_version_id,
            source.official_source_item_id,
            source.official_source_binding_sha256,
            source.content_snapshot_sha256,
            request.reviewer_binding.principal_id,
            request.reviewer_binding.binding_sha256,
            request.reviewer_binding.producer_release_sha,
            request.reviewer_binding.config_sha256,
        )
        actual = (
            self.work_key,
            self.assignment_key,
            self.request_key,
            self.attempt_fence_sha256,
            self.source_lineage_sha256,
            self.private_content_receipt_sha256,
            self.reviewed_output_sha256,
            self.official_content_version_id,
            self.official_source_item_id,
            self.official_source_binding_sha256,
            self.content_snapshot_sha256,
            self.reviewer_principal_id,
            self.reviewer_specialist_binding_sha256,
            self.reviewer_release_sha,
            self.reviewer_config_sha256,
        )
        if actual != expected:
            raise SquidCodexGateError(
                "squid_codex_gate_evidence_binding_invalid"
            )


_RECEIPT_DEFAULTS: dict[str, object] = {
    "schema_version": "squid-codex-gate-result@1",
    "client_id": "squid",
    "specialist_code": "squid_independent_qa",
    "automatic_publication": False,
    "provider_calls": False,
    "external_calls": False,
    "publication_calls": False,
}


def squid_codex_gate_receipt_sha256(payload: Mapping[str, object]) -> str:
    canonical = dict(_RECEIPT_DEFAULTS)
    canonical.update(payload)
    canonical.pop("receipt_sha256", None)
    return _sha256(canonical)


def bind_squid_codex_gate_receipt(
    payload: Mapping[str, object],
) -> dict[str, object]:
    bound = dict(_RECEIPT_DEFAULTS)
    bound.update(payload)
    bound["receipt_sha256"] = squid_codex_gate_receipt_sha256(bound)
    return bound


class SquidCodexGateResultReceipt(BaseModel):
    """Hash-bound result metadata; raw content and credentials are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["squid-codex-gate-result@1"] = (
        "squid-codex-gate-result@1"
    )
    receipt_id: UUID4
    work_key: str = Field(pattern=_SHA256_PATTERN)
    assignment_key: str = Field(pattern=_SHA256_PATTERN)
    request_key: str = Field(pattern=_SHA256_PATTERN)
    attempt_fence_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_id: UUID4
    client_id: Literal["squid"] = "squid"
    round_id: UUID4
    plan_id: UUID4
    private_content_receipt_id: UUID4
    private_content_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    private_content_producer_principal_id: UUID4
    private_content_specialist_binding_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    private_content_output_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_lineage_sha256: str = Field(pattern=_SHA256_PATTERN)
    official_content_version_id: UUID4
    official_source_item_id: UUID4
    official_source_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    content_snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewer_principal_id: UUID4
    specialist_code: Literal["squid_independent_qa"] = "squid_independent_qa"
    reviewer_specialist_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewer_release_sha: str = Field(pattern=_RELEASE_SHA_PATTERN)
    reviewer_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    qa_output_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    verdict: SquidCodexGateVerdict
    approved_cost_cap_microusd: int = Field(ge=0)
    cost_observation: SquidCodexGateCostObservation
    observed_cost_microusd: int | None = Field(default=None, ge=0)
    recorded_at: datetime
    automatic_publication: StrictBool = False
    provider_calls: StrictBool = False
    external_calls: StrictBool = False
    publication_calls: StrictBool = False
    receipt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "squid_codex_gate_result_time_invalid")

    @model_validator(mode="after")
    def validate_receipt(self) -> "SquidCodexGateResultReceipt":
        if self.reviewer_principal_id == self.private_content_producer_principal_id:
            raise ValueError("squid_codex_gate_self_review_forbidden")
        if any((
            self.automatic_publication,
            self.provider_calls,
            self.external_calls,
            self.publication_calls,
        )):
            raise ValueError("squid_codex_gate_side_effect_forbidden")
        if self.cost_observation == SquidCodexGateCostObservation.OBSERVED:
            if self.observed_cost_microusd is None:
                raise ValueError("squid_codex_gate_cost_observation_invalid")
            if self.observed_cost_microusd > self.approved_cost_cap_microusd:
                raise ValueError("squid_codex_gate_cost_cap_exceeded")
        elif self.observed_cost_microusd is not None:
            raise ValueError("squid_codex_gate_cost_observation_invalid")
        expected = squid_codex_gate_receipt_sha256(
            self.model_dump(mode="python", exclude={"receipt_sha256"})
        )
        if self.receipt_sha256 != expected:
            raise ValueError("squid_codex_gate_receipt_digest_invalid")
        return self

    def assert_request_binding(
        self,
        request: SquidCodexGateRequest,
        attempt_fence_sha256: str,
    ) -> None:
        source = request.source_lineage
        expected = (
            request.work_key,
            request.assignment_key,
            request.request_key,
            attempt_fence_sha256,
            request.workspace_id,
            request.client_id,
            request.round_id,
            request.plan_id,
            request.private_content_receipt_id,
            request.private_content_receipt_sha256,
            request.private_content_producer_principal_id,
            request.private_content_binding.binding_sha256,
            request.private_content_output_sha256,
            source.lineage_sha256,
            source.official_content_version_id,
            source.official_source_item_id,
            source.official_source_binding_sha256,
            source.content_snapshot_sha256,
            request.reviewer_principal_id,
            request.specialist_code,
            request.reviewer_binding.binding_sha256,
            request.reviewer_release_sha,
            request.reviewer_config_sha256,
            request.approved_cost_cap_microusd,
        )
        actual = (
            self.work_key,
            self.assignment_key,
            self.request_key,
            self.attempt_fence_sha256,
            self.workspace_id,
            self.client_id,
            self.round_id,
            self.plan_id,
            self.private_content_receipt_id,
            self.private_content_receipt_sha256,
            self.private_content_producer_principal_id,
            self.private_content_specialist_binding_sha256,
            self.private_content_output_sha256,
            self.source_lineage_sha256,
            self.official_content_version_id,
            self.official_source_item_id,
            self.official_source_binding_sha256,
            self.content_snapshot_sha256,
            self.reviewer_principal_id,
            self.specialist_code,
            self.reviewer_specialist_binding_sha256,
            self.reviewer_release_sha,
            self.reviewer_config_sha256,
            self.approved_cost_cap_microusd,
        )
        if actual != expected:
            raise SquidCodexGateError("squid_codex_gate_receipt_binding_invalid")


def squid_codex_claim_fence_sha256(
    *,
    request: SquidCodexGateRequest,
    claim_attempts: int,
    claimed_at: datetime,
    claim_principal_id: UUID,
    lease_expires_at: datetime,
) -> str:
    return _sha256({
        "assignment_key": request.assignment_key,
        "claim_attempts": claim_attempts,
        "claimed_at": claimed_at,
        "claim_principal_id": claim_principal_id,
        "lease_expires_at": lease_expires_at,
        "request_key": request.request_key,
        "schema_version": "squid-codex-claim-fence@1",
        "work_key": request.work_key,
    })


def squid_codex_attempt_fence_sha256(
    *,
    request: SquidCodexGateRequest,
    claim_fence_sha256: str,
    attempt_started_at: datetime,
) -> str:
    return _sha256({
        "assignment_key": request.assignment_key,
        "attempt_started_at": attempt_started_at,
        "claim_fence_sha256": claim_fence_sha256,
        "request_key": request.request_key,
        "schema_version": "squid-codex-attempt-fence@1",
        "work_key": request.work_key,
    })


class SquidCodexGateRun(BaseModel):
    """One immutable state snapshot from the local gate runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["squid-codex-gate-run@1"] = (
        "squid-codex-gate-run@1"
    )
    work_key: str = Field(pattern=_SHA256_PATTERN)
    request_key: str = Field(pattern=_SHA256_PATTERN)
    request: SquidCodexGateRequest
    state: SquidCodexGateState
    submitted_at: datetime
    last_transition_at: datetime
    claim_attempts: int = Field(ge=0, le=_MAX_CLAIM_ATTEMPTS)
    claimed_at: datetime | None = None
    claim_principal_id: UUID4 | None = None
    lease_expires_at: datetime | None = None
    claim_fence_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    attempt_started_at: datetime | None = None
    attempt_fence_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    result_receipt: SquidCodexGateResultReceipt | None = None
    result_evidence: SquidCodexSemanticQaEvidence | None = None
    result_submitted_at: datetime | None = None
    terminal_reason: SquidCodexGateTerminalReason | None = None

    @field_validator(
        "submitted_at",
        "last_transition_at",
        "claimed_at",
        "lease_expires_at",
        "result_submitted_at",
    )
    @classmethod
    def validate_lease_expires_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        return _utc_seconds(value, "squid_codex_gate_transition_time_invalid")

    @field_validator("attempt_started_at")
    @classmethod
    def validate_attempt_started_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        return _utc_seconds(value, "squid_codex_gate_attempt_time_invalid")

    @model_validator(mode="after")
    def validate_run(self) -> "SquidCodexGateRun":
        if (
            self.work_key != self.request.work_key
            or self.request_key != self.request.request_key
        ):
            raise ValueError("squid_codex_gate_run_request_binding_invalid")
        if not (
            self.request.source_lineage.observed_at
            <= self.submitted_at
            <= self.last_transition_at
            and self.request.plan_receipt.recorded_at
            <= self.request.private_content_receipt.recorded_at
            <= self.request.source_lineage.observed_at
            < self.request.effective_expires_at
        ):
            raise ValueError("squid_codex_gate_transition_time_invalid")
        claim_fields = (
            self.claimed_at is not None,
            self.claim_principal_id is not None,
            self.lease_expires_at is not None,
            self.claim_fence_sha256 is not None,
        )
        if claim_fields not in {
            (False, False, False, False),
            (True, True, True, True),
        }:
            raise ValueError("squid_codex_gate_claim_binding_invalid")
        has_claim = claim_fields == (True, True, True, True)
        if has_claim and self.claim_principal_id != self.request.reviewer_principal_id:
            raise ValueError("squid_codex_gate_claim_principal_invalid")
        if has_claim and (
            self.claimed_at is None
            or self.lease_expires_at is None
            or not self.submitted_at <= self.claimed_at < self.lease_expires_at
            or self.claimed_at > self.last_transition_at
        ):
            raise ValueError("squid_codex_gate_transition_time_invalid")
        attempt_pair = (
            self.attempt_started_at is not None,
            self.attempt_fence_sha256 is not None,
        )
        if attempt_pair not in {(False, False), (True, True)}:
            raise ValueError("squid_codex_gate_attempt_binding_invalid")
        has_attempt = attempt_pair == (True, True)
        if has_attempt and (
            self.lease_expires_at is None
            or self.claimed_at is None
            or self.attempt_started_at is None
            or not self.claimed_at
            <= self.attempt_started_at < self.lease_expires_at
            or self.attempt_started_at > self.last_transition_at
        ):
            raise ValueError("squid_codex_gate_attempt_time_invalid")
        result_pair = (
            self.result_receipt is not None,
            self.result_evidence is not None,
            self.result_submitted_at is not None,
        )
        if result_pair not in {
            (False, False, False),
            (True, True, True),
        }:
            raise ValueError("squid_codex_gate_result_binding_invalid")
        has_result = result_pair == (True, True, True)
        if has_result:
            assert self.result_receipt is not None
            assert self.result_evidence is not None
            assert self.result_submitted_at is not None
            assert self.attempt_fence_sha256 is not None
            self.result_receipt.assert_request_binding(
                self.request,
                self.attempt_fence_sha256,
            )
            self.result_evidence.assert_request_binding(
                self.request,
                self.attempt_fence_sha256,
            )
            if (
                self.result_receipt.evidence_sha256
                != self.result_evidence.evidence_sha256
                or self.result_receipt.qa_output_sha256
                != self.result_evidence.qa_output_sha256
                or self.result_receipt.verdict != self.result_evidence.verdict
                or self.attempt_started_at is None
                or self.lease_expires_at is None
                or not self.attempt_started_at
                <= self.result_receipt.recorded_at
                <= self.result_submitted_at
                < min(self.lease_expires_at, self.request.effective_expires_at)
                or self.result_submitted_at != self.last_transition_at
            ):
                raise ValueError("squid_codex_gate_result_binding_invalid")

        if self.state == SquidCodexGateState.PENDING:
            if (
                has_claim
                or self.claim_attempts >= _MAX_CLAIM_ATTEMPTS
                or has_attempt
                or has_result
                or self.terminal_reason is not None
            ):
                raise ValueError("squid_codex_gate_state_payload_invalid")
            return self

        if not has_claim or self.claim_attempts == 0:
            raise ValueError("squid_codex_gate_state_payload_invalid")
        if self.state == SquidCodexGateState.CLAIMED:
            if any((
                has_attempt,
                has_result,
                self.terminal_reason is not None,
            )):
                raise ValueError("squid_codex_gate_state_payload_invalid")
            return self

        if self.state == SquidCodexGateState.ATTEMPT_STARTED:
            if (
                not has_attempt
                or has_result
                or self.terminal_reason is not None
            ):
                raise ValueError("squid_codex_gate_state_payload_invalid")
            return self

        if self.state == SquidCodexGateState.OUTCOME_UNKNOWN:
            if (
                not has_attempt
                or has_result
                or self.terminal_reason
                != SquidCodexGateTerminalReason.RESULT_RECEIPT_MISSING
            ):
                raise ValueError("squid_codex_gate_state_payload_invalid")
            return self

        if self.state == SquidCodexGateState.BLOCKED and self.result_receipt is None:
            if (
                self.claim_attempts != _MAX_CLAIM_ATTEMPTS
                or has_attempt
                or self.terminal_reason
                != SquidCodexGateTerminalReason.CLAIM_LIMIT_EXHAUSTED
            ):
                raise ValueError("squid_codex_gate_state_payload_invalid")
            return self

        if not has_attempt or not has_result:
            raise ValueError("squid_codex_gate_state_payload_invalid")
        assert self.result_receipt is not None
        verdict = self.result_receipt.verdict
        expected = {
            SquidCodexGateState.RESULT_SUBMITTED: (None, None),
            SquidCodexGateState.VERIFIED: (
                SquidCodexGateVerdict.PASS,
                None,
            ),
            SquidCodexGateState.OPERATOR_REVIEW_PENDING: (
                SquidCodexGateVerdict.PASS,
                None,
            ),
            SquidCodexGateState.NEEDS_CHANGES: (
                SquidCodexGateVerdict.NEEDS_CHANGES,
                SquidCodexGateTerminalReason.RESULT_NEEDS_CHANGES,
            ),
            SquidCodexGateState.BLOCKED: (
                SquidCodexGateVerdict.BLOCKED,
                SquidCodexGateTerminalReason.RESULT_BLOCKED,
            ),
        }.get(self.state)
        if expected is None:
            raise ValueError("squid_codex_gate_state_payload_invalid")
        expected_verdict, expected_reason = expected
        if (
            (expected_verdict is not None and verdict != expected_verdict)
            or self.terminal_reason != expected_reason
        ):
            raise ValueError("squid_codex_gate_state_payload_invalid")
        return self


class SquidCodexGateTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run: SquidCodexGateRun
    reused: StrictBool = False
    execute_authorized: StrictBool = False

    @model_validator(mode="after")
    def validate_execution_authority(self) -> "SquidCodexGateTransition":
        if self.execute_authorized and (
            self.reused
            or self.run.state != SquidCodexGateState.ATTEMPT_STARTED
        ):
            raise ValueError("squid_codex_gate_execution_authority_invalid")
        return self


def _uuid4(value: UUID | str, code: str) -> UUID:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SquidCodexGateError(code) from exc
    if parsed.version != 4:
        raise SquidCodexGateError(code)
    return parsed


def _updated_run(
    run: SquidCodexGateRun,
    **updates: object,
) -> SquidCodexGateRun:
    payload = run.model_dump(mode="python")
    payload.update(updates)
    return SquidCodexGateRun.model_validate(payload)


class SquidCodexGateRunner:
    """Thread-safe process-local simulator. It performs no external I/O."""

    def __init__(self) -> None:
        self._runs: dict[str, SquidCodexGateRun] = {}
        self._scope_work_keys: dict[
            tuple[UUID, str, UUID, str], str
        ] = {}
        self._lock = RLock()

    def _stored(self, work_key: str) -> SquidCodexGateRun:
        run = self._runs.get(work_key)
        if run is None:
            raise SquidCodexGateError("squid_codex_gate_request_not_found")
        return run

    def _store(
        self,
        run: SquidCodexGateRun,
        *,
        reused: bool = False,
        execute_authorized: bool = False,
    ) -> SquidCodexGateTransition:
        self._runs[run.work_key] = run
        return SquidCodexGateTransition(
            run=run,
            reused=reused,
            execute_authorized=execute_authorized,
        )

    def submit_request(
        self,
        request: SquidCodexGateRequest | Mapping[str, object],
        *,
        now: datetime,
    ) -> SquidCodexGateTransition:
        typed = (
            request
            if isinstance(request, SquidCodexGateRequest)
            else SquidCodexGateRequest.model_validate(request)
        )
        observed_at = _utc_seconds(
            now,
            "squid_codex_gate_request_time_invalid",
        )
        if observed_at < typed.source_lineage.observed_at:
            raise SquidCodexGateError(
                "squid_codex_gate_request_not_yet_valid"
            )
        if observed_at >= typed.effective_expires_at:
            raise SquidCodexGateError("squid_codex_gate_request_expired")
        with self._lock:
            scoped_work_key = self._scope_work_keys.get(typed.logical_scope)
            if scoped_work_key is not None and scoped_work_key != typed.work_key:
                raise SquidCodexGateError("squid_codex_gate_work_conflict")
            existing = self._runs.get(typed.work_key)
            if existing is not None:
                if observed_at < existing.last_transition_at:
                    raise SquidCodexGateError(
                        "squid_codex_gate_transition_time_invalid"
                    )
                if existing.request != typed:
                    raise SquidCodexGateError(
                        "squid_codex_gate_assignment_conflict"
                    )
                return SquidCodexGateTransition(run=existing, reused=True)
            self._scope_work_keys[typed.logical_scope] = typed.work_key
            return self._store(SquidCodexGateRun(
                work_key=typed.work_key,
                request_key=typed.request_key,
                request=typed,
                state=SquidCodexGateState.PENDING,
                submitted_at=observed_at,
                last_transition_at=observed_at,
                claim_attempts=0,
            ))

    def get(self, work_key: str) -> SquidCodexGateRun:
        with self._lock:
            return self._stored(work_key)

    def claim(
        self,
        work_key: str,
        *,
        reviewer_principal_id: UUID | str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> SquidCodexGateTransition:
        observed_at = _utc_seconds(now, "squid_codex_gate_claim_time_invalid")
        expires_at = _utc_seconds(
            lease_expires_at,
            "squid_codex_gate_lease_time_invalid",
        )
        if expires_at <= observed_at:
            raise SquidCodexGateError("squid_codex_gate_lease_time_invalid")
        principal_id = _uuid4(
            reviewer_principal_id,
            "squid_codex_gate_claim_principal_invalid",
        )
        with self._lock:
            run = self._stored(work_key)
            if observed_at < run.last_transition_at:
                raise SquidCodexGateError(
                    "squid_codex_gate_transition_time_invalid"
                )
            if observed_at >= run.request.effective_expires_at:
                raise SquidCodexGateError("squid_codex_gate_request_expired")
            if (
                expires_at - observed_at > _MAX_LEASE_DURATION
                or expires_at > run.request.effective_expires_at
            ):
                raise SquidCodexGateError("squid_codex_gate_lease_time_invalid")
            if principal_id != run.request.reviewer_principal_id:
                raise SquidCodexGateError(
                    "squid_codex_gate_claim_principal_invalid"
                )
            if run.state == SquidCodexGateState.CLAIMED:
                if (
                    run.claimed_at is not None
                    and observed_at >= run.claimed_at
                    and
                    run.claim_principal_id == principal_id
                    and run.lease_expires_at == expires_at
                ):
                    return SquidCodexGateTransition(run=run, reused=True)
                raise SquidCodexGateError("squid_codex_gate_claim_conflict")
            if run.state in {
                SquidCodexGateState.ATTEMPT_STARTED,
                SquidCodexGateState.OUTCOME_UNKNOWN,
            }:
                raise SquidCodexGateError(
                    "squid_codex_gate_automatic_retry_forbidden"
                )
            if run.state != SquidCodexGateState.PENDING:
                raise SquidCodexGateError("squid_codex_gate_state_invalid")
            if run.claim_attempts >= _MAX_CLAIM_ATTEMPTS:
                raise SquidCodexGateError("squid_codex_gate_claim_limit")
            claim_attempts = run.claim_attempts + 1
            claim_fence_sha256 = squid_codex_claim_fence_sha256(
                request=run.request,
                claim_attempts=claim_attempts,
                claimed_at=observed_at,
                claim_principal_id=principal_id,
                lease_expires_at=expires_at,
            )
            return self._store(_updated_run(
                run,
                state=SquidCodexGateState.CLAIMED,
                last_transition_at=observed_at,
                claim_attempts=claim_attempts,
                claimed_at=observed_at,
                claim_principal_id=principal_id,
                lease_expires_at=expires_at,
                claim_fence_sha256=claim_fence_sha256,
            ))

    def reconcile_expired_lease(
        self,
        work_key: str,
        *,
        now: datetime,
    ) -> SquidCodexGateTransition:
        observed_at = _utc_seconds(
            now,
            "squid_codex_gate_reconcile_time_invalid",
        )
        with self._lock:
            run = self._stored(work_key)
            if observed_at < run.last_transition_at:
                raise SquidCodexGateError(
                    "squid_codex_gate_transition_time_invalid"
                )
            if run.state == SquidCodexGateState.OUTCOME_UNKNOWN or (
                run.state == SquidCodexGateState.BLOCKED
                and run.terminal_reason
                == SquidCodexGateTerminalReason.CLAIM_LIMIT_EXHAUSTED
            ):
                return SquidCodexGateTransition(run=run, reused=True)
            if run.state not in {
                SquidCodexGateState.CLAIMED,
                SquidCodexGateState.ATTEMPT_STARTED,
            }:
                raise SquidCodexGateError("squid_codex_gate_state_invalid")
            if run.lease_expires_at is None or observed_at < run.lease_expires_at:
                raise SquidCodexGateError("squid_codex_gate_lease_active")
            if run.state == SquidCodexGateState.ATTEMPT_STARTED:
                return self._store(_updated_run(
                    run,
                    state=SquidCodexGateState.OUTCOME_UNKNOWN,
                    last_transition_at=observed_at,
                    terminal_reason=(
                        SquidCodexGateTerminalReason.RESULT_RECEIPT_MISSING
                    ),
                ))
            if run.claim_attempts == _MAX_CLAIM_ATTEMPTS:
                return self._store(_updated_run(
                    run,
                    state=SquidCodexGateState.BLOCKED,
                    last_transition_at=observed_at,
                    terminal_reason=(
                        SquidCodexGateTerminalReason.CLAIM_LIMIT_EXHAUSTED
                    ),
                ))
            return self._store(_updated_run(
                run,
                state=SquidCodexGateState.PENDING,
                last_transition_at=observed_at,
                claimed_at=None,
                claim_principal_id=None,
                lease_expires_at=None,
                claim_fence_sha256=None,
            ))

    def start_attempt(
        self,
        work_key: str,
        *,
        reviewer_principal_id: UUID | str,
        claim_fence_sha256: str,
        now: datetime,
    ) -> SquidCodexGateTransition:
        observed_at = _utc_seconds(
            now,
            "squid_codex_gate_attempt_time_invalid",
        )
        principal_id = _uuid4(
            reviewer_principal_id,
            "squid_codex_gate_claim_principal_invalid",
        )
        with self._lock:
            run = self._stored(work_key)
            if observed_at < run.last_transition_at:
                raise SquidCodexGateError(
                    "squid_codex_gate_transition_time_invalid"
                )
            if run.state == SquidCodexGateState.ATTEMPT_STARTED:
                if (
                    run.claim_principal_id != principal_id
                    or run.claim_fence_sha256 != claim_fence_sha256
                ):
                    raise SquidCodexGateError(
                        "squid_codex_gate_claim_fence_invalid"
                    )
                return SquidCodexGateTransition(run=run, reused=True)
            if run.state == SquidCodexGateState.OUTCOME_UNKNOWN:
                raise SquidCodexGateError(
                    "squid_codex_gate_automatic_retry_forbidden"
                )
            if run.state != SquidCodexGateState.CLAIMED:
                raise SquidCodexGateError("squid_codex_gate_state_invalid")
            if (
                run.claim_principal_id != principal_id
                or run.claim_fence_sha256 != claim_fence_sha256
            ):
                raise SquidCodexGateError(
                    "squid_codex_gate_claim_fence_invalid"
                )
            if (
                run.lease_expires_at is None
                or observed_at >= run.lease_expires_at
                or observed_at >= run.request.effective_expires_at
            ):
                raise SquidCodexGateError("squid_codex_gate_lease_expired")
            attempt_fence_sha256 = squid_codex_attempt_fence_sha256(
                request=run.request,
                claim_fence_sha256=claim_fence_sha256,
                attempt_started_at=observed_at,
            )
            return self._store(_updated_run(
                run,
                state=SquidCodexGateState.ATTEMPT_STARTED,
                last_transition_at=observed_at,
                attempt_started_at=observed_at,
                attempt_fence_sha256=attempt_fence_sha256,
            ), execute_authorized=True)

    def submit_result(
        self,
        work_key: str,
        receipt: SquidCodexGateResultReceipt | Mapping[str, object],
        evidence: SquidCodexSemanticQaEvidence | Mapping[str, object],
        *,
        now: datetime,
    ) -> SquidCodexGateTransition:
        observed_at = _utc_seconds(
            now,
            "squid_codex_gate_result_time_invalid",
        )
        typed = (
            receipt
            if isinstance(receipt, SquidCodexGateResultReceipt)
            else SquidCodexGateResultReceipt.model_validate(receipt)
        )
        typed_evidence = (
            evidence
            if isinstance(evidence, SquidCodexSemanticQaEvidence)
            else SquidCodexSemanticQaEvidence.model_validate(evidence)
        )
        with self._lock:
            run = self._stored(work_key)
            if observed_at < run.last_transition_at:
                raise SquidCodexGateError(
                    "squid_codex_gate_transition_time_invalid"
                )
            if run.attempt_fence_sha256 is None:
                raise SquidCodexGateError(
                    "squid_codex_gate_attempt_fence_missing"
                )
            typed.assert_request_binding(
                run.request,
                run.attempt_fence_sha256,
            )
            typed_evidence.assert_request_binding(
                run.request,
                run.attempt_fence_sha256,
            )
            if (
                typed.evidence_sha256 != typed_evidence.evidence_sha256
                or typed.qa_output_sha256 != typed_evidence.qa_output_sha256
                or typed.verdict != typed_evidence.verdict
            ):
                raise SquidCodexGateError(
                    "squid_codex_gate_result_evidence_invalid"
                )
            if run.result_receipt is not None:
                if (
                    run.result_receipt != typed
                    or run.result_evidence != typed_evidence
                ):
                    raise SquidCodexGateError(
                        "squid_codex_gate_result_conflict"
                    )
                return SquidCodexGateTransition(run=run, reused=True)
            if run.state == SquidCodexGateState.OUTCOME_UNKNOWN:
                raise SquidCodexGateError(
                    "squid_codex_gate_automatic_retry_forbidden"
                )
            if run.state != SquidCodexGateState.ATTEMPT_STARTED:
                raise SquidCodexGateError("squid_codex_gate_state_invalid")
            if (
                run.attempt_started_at is None
                or run.lease_expires_at is None
                or typed.recorded_at < run.attempt_started_at
                or typed.recorded_at > observed_at
                or observed_at >= run.lease_expires_at
                or observed_at >= run.request.effective_expires_at
            ):
                raise SquidCodexGateError(
                    "squid_codex_gate_result_time_invalid"
                )
            return self._store(_updated_run(
                run,
                state=SquidCodexGateState.RESULT_SUBMITTED,
                last_transition_at=observed_at,
                result_receipt=typed,
                result_evidence=typed_evidence,
                result_submitted_at=observed_at,
            ))

    def verify_result(self, work_key: str) -> SquidCodexGateTransition:
        with self._lock:
            run = self._stored(work_key)
            if run.result_receipt is None:
                raise SquidCodexGateError("squid_codex_gate_receipt_missing")
            verdict = run.result_receipt.verdict
            target = {
                SquidCodexGateVerdict.PASS: SquidCodexGateState.VERIFIED,
                SquidCodexGateVerdict.NEEDS_CHANGES: (
                    SquidCodexGateState.NEEDS_CHANGES
                ),
                SquidCodexGateVerdict.BLOCKED: SquidCodexGateState.BLOCKED,
            }[verdict]
            reusable_states = {target}
            if verdict == SquidCodexGateVerdict.PASS:
                reusable_states.add(
                    SquidCodexGateState.OPERATOR_REVIEW_PENDING
                )
            if run.state in reusable_states:
                return SquidCodexGateTransition(run=run, reused=True)
            if run.state != SquidCodexGateState.RESULT_SUBMITTED:
                raise SquidCodexGateError("squid_codex_gate_state_invalid")
            reason = {
                SquidCodexGateVerdict.PASS: None,
                SquidCodexGateVerdict.NEEDS_CHANGES: (
                    SquidCodexGateTerminalReason.RESULT_NEEDS_CHANGES
                ),
                SquidCodexGateVerdict.BLOCKED: (
                    SquidCodexGateTerminalReason.RESULT_BLOCKED
                ),
            }[verdict]
            return self._store(_updated_run(
                run,
                state=target,
                terminal_reason=reason,
            ))

    def queue_operator_review(
        self,
        work_key: str,
    ) -> SquidCodexGateTransition:
        with self._lock:
            run = self._stored(work_key)
            if run.state == SquidCodexGateState.OPERATOR_REVIEW_PENDING:
                return SquidCodexGateTransition(run=run, reused=True)
            if run.state != SquidCodexGateState.VERIFIED:
                raise SquidCodexGateError("squid_codex_gate_state_invalid")
            return self._store(_updated_run(
                run,
                state=SquidCodexGateState.OPERATOR_REVIEW_PENDING,
            ))
