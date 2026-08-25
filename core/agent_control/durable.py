from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    UUID4,
    field_validator,
    model_validator,
)

from .control_room import AgentRouteProjection
from .models import AgentIdentity, AgentWorkOrder
from .render import render_owner_planning_packet


class AgentDurableState(str, Enum):
    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    AWAITING_REVIEW = "awaiting_review"
    VERIFIED = "verified"
    APPROVED = "approved"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class CostObservation(str, Enum):
    UNOBSERVED = "unobserved"
    OBSERVED = "observed"


class AgentDispatchStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    ATTEMPT_STARTED = "attempt_started"
    DELIVERED = "delivered"
    DELIVERY_UNKNOWN = "delivery_unknown"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _allowed_dispatch_statuses(
    state: AgentDurableState,
    *,
    has_packet: bool,
) -> frozenset[Optional[AgentDispatchStatus]]:
    if state == AgentDurableState.CANCELLED:
        return frozenset({AgentDispatchStatus.CANCELLED} if has_packet else {None})
    return {
        AgentDurableState.PROPOSED: frozenset({None}),
        AgentDurableState.AUTHORIZED: frozenset({AgentDispatchStatus.PENDING}),
        AgentDurableState.CLAIMED: frozenset({AgentDispatchStatus.CLAIMED}),
        AgentDurableState.IN_PROGRESS: frozenset({
            AgentDispatchStatus.ATTEMPT_STARTED,
        }),
        AgentDurableState.AWAITING_REVIEW: frozenset({
            AgentDispatchStatus.DELIVERED,
        }),
        AgentDurableState.VERIFIED: frozenset({AgentDispatchStatus.DELIVERED}),
        AgentDurableState.APPROVED: frozenset({AgentDispatchStatus.DELIVERED}),
        AgentDurableState.COMPLETED: frozenset({AgentDispatchStatus.DELIVERED}),
        AgentDurableState.BLOCKED: frozenset({
            AgentDispatchStatus.CANCELLED,
            AgentDispatchStatus.FAILED,
            AgentDispatchStatus.DELIVERY_UNKNOWN,
        }),
    }[state]


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


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class AgentAuthorizationReceipt(BaseModel):
    """Hash-bound human authorization payload plus immutable record identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-authorization-receipt@1"] = (
        "agent-authorization-receipt@1"
    )
    receipt_id: UUID4
    work_order_id: UUID4
    actor_user_id: UUID4
    decision: Literal["authorized"] = "authorized"
    scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_cost_microusd: Literal[0] = 0
    max_external_actions: Literal[0] = 0
    automatic_publication: Literal[False] = False
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    def canonical_payload(self) -> dict[str, object]:
        # This is byte-for-byte the payload persisted by the ledger migration.
        # receipt_id and payload_sha256 are immutable record metadata, not
        # members of the hashed JSON payload.
        return {
            "actor_user_id": str(self.actor_user_id),
            "automatic_publication": self.automatic_publication,
            "decision": self.decision,
            "max_cost_microusd": self.max_cost_microusd,
            "max_external_actions": self.max_external_actions,
            "schema_version": self.schema_version,
            "scope_sha256": self.scope_sha256,
            "work_order_id": str(self.work_order_id),
        }

    @model_validator(mode="after")
    def validate_payload_digest(self) -> "AgentAuthorizationReceipt":
        expected = hashlib.sha256(
            _canonical_json(self.canonical_payload())
        ).hexdigest()
        if self.payload_sha256 != expected:
            raise ValueError("agent_authorization_payload_digest_invalid")
        return self


class AgentDispatchPacket(BaseModel):
    """Hash-bound no-I/O dispatch packet; it does not prove delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-dispatch-packet@1"] = (
        "agent-dispatch-packet@1"
    )
    work_order_id: UUID4
    scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner: AgentIdentity
    reviewer: AgentIdentity
    repository: str
    base_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    branch_name: str
    max_cost_microusd: Literal[0] = 0
    max_external_actions: Literal[0] = 0
    automatic_publication: Literal[False] = False
    packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    def canonical_packet(self) -> dict[str, object]:
        return {
            "automatic_publication": self.automatic_publication,
            "base_sha": self.base_sha,
            "branch_name": self.branch_name,
            "max_cost_microusd": self.max_cost_microusd,
            "max_external_actions": self.max_external_actions,
            "owner": self.owner.value,
            "repository": self.repository,
            "reviewer": self.reviewer.value,
            "schema_version": self.schema_version,
            "scope_sha256": self.scope_sha256,
            "work_order_id": str(self.work_order_id),
        }

    @model_validator(mode="after")
    def validate_packet_digest(self) -> "AgentDispatchPacket":
        expected = hashlib.sha256(
            _canonical_json(self.canonical_packet())
        ).hexdigest()
        if self.packet_sha256 != expected:
            raise ValueError("agent_dispatch_packet_digest_invalid")
        if self.owner == self.reviewer:
            raise ValueError("agent_dispatch_packet_separation_invalid")
        return self


class AgentWorkResultReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-work-result@1"] = "agent-work-result@1"
    receipt_id: UUID4
    work_order_id: UUID4
    owner: AgentIdentity
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    recorded_at: datetime
    automatic_publication: Literal[False] = False

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_work_result_recorded_at_invalid")


class AgentVerificationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-verification-receipt@1"] = (
        "agent-verification-receipt@1"
    )
    receipt_id: UUID4
    work_order_id: UUID4
    reviewer: AgentIdentity
    result_receipt_id: UUID4
    passed: bool
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    recorded_at: datetime
    automatic_publication: Literal[False] = False

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_verification_recorded_at_invalid")


class OperatorDecisionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["operator-decision@1"] = "operator-decision@1"
    receipt_id: UUID4
    work_order_id: UUID4
    operator: Literal[AgentIdentity.HUMAN_OPERATOR] = AgentIdentity.HUMAN_OPERATOR
    verification_receipt_id: Optional[UUID4] = None
    decision: Literal["approved", "blocked", "cancelled"]
    recorded_at: datetime
    automatic_publication: Literal[False] = False

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "operator_decision_recorded_at_invalid")

    @model_validator(mode="after")
    def validate_decision_binding(self) -> "OperatorDecisionReceipt":
        if self.decision == "approved" and self.verification_receipt_id is None:
            raise ValueError("operator_decision_verification_required")
        return self


class AgentCompletionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-completion-receipt@1"] = (
        "agent-completion-receipt@1"
    )
    receipt_id: UUID4
    work_order_id: UUID4
    recorded_by: Literal[
        AgentIdentity.HUMAN_OPERATOR,
        AgentIdentity.RAILWAY_WORKER,
    ]
    operator_decision_receipt_id: UUID4
    recorded_at: datetime
    automatic_publication: Literal[False] = False

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_completion_recorded_at_invalid")


class DurableAgentWorkOrderRow(BaseModel):
    """Strict local validation of one untrusted durable-ledger RPC row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-work-order-row@1"] = "agent-work-order-row@1"
    work_order_id: UUID4
    scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    branch_scope_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner: AgentIdentity
    reviewer: AgentIdentity
    state: AgentDurableState
    work_order: AgentWorkOrder
    cost_observation: CostObservation
    observed_cost_microusd: Optional[Literal[0]] = None
    authorization_receipt: Optional[AgentAuthorizationReceipt] = None
    dispatch_packet: Optional[AgentDispatchPacket] = None
    dispatch_status: Optional[AgentDispatchStatus] = None
    result_receipt: Optional[AgentWorkResultReceipt] = None
    verification_receipt: Optional[AgentVerificationReceipt] = None
    operator_decision: Optional[OperatorDecisionReceipt] = None
    completion_receipt: Optional[AgentCompletionReceipt] = None

    @model_validator(mode="after")
    def validate_durable_binding(self) -> "DurableAgentWorkOrderRow":
        order = self.work_order
        if (
            self.work_order_id != order.work_order_id
            or self.scope_sha256 != order.scope_sha256
            or self.branch_scope_key != order.branch_scope_key
            or self.owner != order.owner
            or self.reviewer != order.reviewer
        ):
            raise ValueError("agent_durable_work_order_binding_invalid")
        if self.owner == self.reviewer or order.automatic_publication is not False:
            raise ValueError("agent_durable_separation_invalid")

        if self.cost_observation == CostObservation.UNOBSERVED:
            if self.observed_cost_microusd is not None:
                raise ValueError("agent_durable_cost_observation_invalid")
        elif self.observed_cost_microusd is None:
            raise ValueError("agent_durable_cost_observation_invalid")

        authorization = self.authorization_receipt
        dispatch = self.dispatch_packet
        proof_pair = (authorization is not None, dispatch is not None)
        proof_required = self.state in {
            AgentDurableState.AUTHORIZED,
            AgentDurableState.CLAIMED,
            AgentDurableState.IN_PROGRESS,
            AgentDurableState.AWAITING_REVIEW,
            AgentDurableState.VERIFIED,
            AgentDurableState.APPROVED,
            AgentDurableState.COMPLETED,
            AgentDurableState.BLOCKED,
        }
        if self.state == AgentDurableState.PROPOSED:
            if proof_pair != (False, False):
                raise ValueError("agent_durable_authorization_proof_invalid")
        elif self.state == AgentDurableState.CANCELLED:
            if proof_pair not in {(False, False), (True, True)}:
                raise ValueError("agent_durable_authorization_proof_invalid")
        elif proof_required and proof_pair != (True, True):
            raise ValueError("agent_durable_authorization_proof_invalid")
        if (dispatch is None) != (self.dispatch_status is None):
            raise ValueError("agent_durable_dispatch_status_presence_invalid")

        expected_dispatch_statuses = _allowed_dispatch_statuses(
            self.state,
            has_packet=dispatch is not None,
        )
        if self.dispatch_status not in expected_dispatch_statuses:
            raise ValueError("agent_durable_dispatch_status_progression_invalid")

        if authorization is not None and (
            authorization.work_order_id != self.work_order_id
            or authorization.scope_sha256 != self.scope_sha256
        ):
            raise ValueError("agent_durable_authorization_binding_invalid")
        if dispatch is not None and (
            dispatch.work_order_id != self.work_order_id
            or dispatch.scope_sha256 != self.scope_sha256
            or dispatch.owner != self.owner
            or dispatch.reviewer != self.reviewer
            or dispatch.repository != order.repository
            or dispatch.base_sha != order.base_sha
            or dispatch.branch_name != order.branch_name
        ):
            raise ValueError("agent_durable_dispatch_binding_invalid")

        receipts = (
            self.result_receipt,
            self.verification_receipt,
            self.operator_decision,
            self.completion_receipt,
        )
        result = self.result_receipt
        verification = self.verification_receipt
        decision = self.operator_decision
        completion = self.completion_receipt
        present = tuple(receipt is not None for receipt in receipts)
        is_terminal_stop = self.state in {
            AgentDurableState.BLOCKED,
            AgentDurableState.CANCELLED,
        }
        if not is_terminal_stop and present != tuple(
            sorted(present, reverse=True)
        ):
            raise ValueError("agent_durable_receipt_gap")
        if is_terminal_stop and verification is not None and result is None:
            raise ValueError("agent_durable_receipt_gap")

        expected_level = {
            AgentDurableState.PROPOSED: 0,
            AgentDurableState.AUTHORIZED: 0,
            AgentDurableState.CLAIMED: 0,
            AgentDurableState.IN_PROGRESS: 0,
            AgentDurableState.AWAITING_REVIEW: 1,
            AgentDurableState.VERIFIED: 2,
            AgentDurableState.APPROVED: 3,
            AgentDurableState.COMPLETED: 4,
        }.get(self.state)
        receipt_level = sum(present)
        if expected_level is not None and receipt_level != expected_level:
            raise ValueError("agent_durable_receipt_progression_invalid")
        if is_terminal_stop:
            if self.completion_receipt is not None:
                raise ValueError("agent_durable_receipt_progression_invalid")
            if decision is None:
                raise ValueError("agent_durable_operator_decision_required")
            if decision.decision != self.state.value:
                raise ValueError("agent_durable_operator_decision_invalid")

        receipt_ids = [
            str(receipt.receipt_id) for receipt in receipts if receipt is not None
        ]
        if len(receipt_ids) != len(set(receipt_ids)):
            raise ValueError("agent_durable_receipt_id_duplicate")
        if any(
            receipt is not None and receipt.work_order_id != self.work_order_id
            for receipt in receipts
        ):
            raise ValueError("agent_durable_receipt_binding_invalid")

        if result is not None and result.owner != self.owner:
            raise ValueError("agent_durable_result_owner_invalid")
        if verification is not None:
            if (
                result is None
                or verification.reviewer != self.reviewer
                or verification.reviewer == self.owner
                or verification.result_receipt_id != result.receipt_id
            ):
                raise ValueError("agent_durable_verification_independence_invalid")
            if self.state not in {
                AgentDurableState.BLOCKED,
                AgentDurableState.CANCELLED,
            } and not verification.passed:
                raise ValueError("agent_durable_verification_failed")
        if decision is not None:
            if decision.decision == "approved":
                if (
                    verification is None
                    or decision.verification_receipt_id
                        != verification.receipt_id
                    or self.state not in {
                        AgentDurableState.APPROVED,
                        AgentDurableState.COMPLETED,
                    }
                ):
                    raise ValueError(
                        "agent_durable_operator_decision_binding_invalid"
                    )
            else:
                if (
                    not is_terminal_stop
                    or decision.decision != self.state.value
                    or (
                        decision.verification_receipt_id is not None
                        and (
                            verification is None
                            or decision.verification_receipt_id
                                != verification.receipt_id
                        )
                    )
                ):
                    raise ValueError("agent_durable_operator_decision_invalid")
        if completion is not None and (
            decision is None
            or decision.decision != "approved"
            or completion.operator_decision_receipt_id != decision.receipt_id
        ):
            raise ValueError("agent_durable_completion_binding_invalid")

        timestamps = [
            receipt.recorded_at for receipt in receipts if receipt is not None
        ]
        if timestamps != sorted(timestamps):
            raise ValueError("agent_durable_receipt_order_invalid")
        return self


class DurableAssignmentProjection(BaseModel):
    """Deterministic assignment data; it is not an execution grant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-assignment-projection@1"] = (
        "agent-assignment-projection@1"
    )
    work_order_id: UUID4
    scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    branch_scope_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str
    owner: AgentIdentity
    reviewer: AgentIdentity
    state: AgentDurableState
    assignment_status: Literal[
        "awaiting_human_authorization",
        "ready_for_owner_claim",
        "owner_claimed",
        "owner_working",
        "awaiting_independent_verification",
        "awaiting_human_approval",
        "ready_for_completion",
        "completed",
        "blocked",
        "cancelled",
    ]
    next_gate: Literal[
        "human_authorization",
        "owner_claim",
        "owner_start",
        "owner_result",
        "independent_verification",
        "human_approval",
        "completion_receipt",
        "none",
        "resolve_blocker",
    ]
    owner_planning_packet: str
    owner_planning_packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cost_observation: CostObservation
    observed_cost_microusd: Optional[Literal[0]] = None
    authorization_receipt_present: bool
    authorization_payload_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    dispatch_packet_present: bool
    dispatch_packet_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    dispatch_status: Optional[AgentDispatchStatus] = None
    result_receipt_present: bool
    verification_receipt_present: bool
    operator_decision_present: bool
    completion_receipt_present: bool
    projection_authorizes_execution: Literal[False] = False
    external_calls: Literal[False] = False
    database_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    automatic_publication: Literal[False] = False

    @model_validator(mode="after")
    def validate_packet_digest(self) -> "DurableAssignmentProjection":
        expected = hashlib.sha256(
            self.owner_planning_packet.encode("utf-8")
        ).hexdigest()
        if expected != self.owner_planning_packet_sha256:
            raise ValueError("agent_assignment_packet_digest_invalid")
        if self.owner == self.reviewer:
            raise ValueError("agent_assignment_separation_invalid")
        if self.cost_observation == CostObservation.UNOBSERVED:
            if self.observed_cost_microusd is not None:
                raise ValueError("agent_assignment_cost_invalid")
        elif self.observed_cost_microusd is None:
            raise ValueError("agent_assignment_cost_invalid")
        if self.authorization_receipt_present != (
            self.authorization_payload_sha256 is not None
        ):
            raise ValueError("agent_assignment_authorization_proof_invalid")
        if self.dispatch_packet_present != (
            self.dispatch_packet_sha256 is not None
        ):
            raise ValueError("agent_assignment_dispatch_proof_invalid")
        if self.dispatch_packet_present != (self.dispatch_status is not None):
            raise ValueError("agent_assignment_dispatch_status_invalid")
        if self.dispatch_status not in _allowed_dispatch_statuses(
            self.state,
            has_packet=self.dispatch_packet_present,
        ):
            raise ValueError("agent_assignment_dispatch_status_progression_invalid")
        return self

    def canonical_projection(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def projection_sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.canonical_projection())
        ).hexdigest()

    def as_payload(self) -> dict[str, object]:
        return {
            **self.canonical_projection(),
            "projection_sha256": self.projection_sha256,
        }


_ASSIGNMENT_ROUTE = {
    AgentDurableState.PROPOSED: (
        "awaiting_human_authorization",
        "human_authorization",
    ),
    AgentDurableState.AUTHORIZED: ("ready_for_owner_claim", "owner_claim"),
    AgentDurableState.CLAIMED: ("owner_claimed", "owner_start"),
    AgentDurableState.IN_PROGRESS: ("owner_working", "owner_result"),
    AgentDurableState.AWAITING_REVIEW: (
        "awaiting_independent_verification",
        "independent_verification",
    ),
    AgentDurableState.VERIFIED: ("awaiting_human_approval", "human_approval"),
    AgentDurableState.APPROVED: ("ready_for_completion", "completion_receipt"),
    AgentDurableState.COMPLETED: ("completed", "none"),
    AgentDurableState.BLOCKED: ("blocked", "resolve_blocker"),
    AgentDurableState.CANCELLED: ("cancelled", "none"),
}


def build_durable_assignment_projection(
    row: DurableAgentWorkOrderRow,
) -> DurableAssignmentProjection:
    """Build one no-I/O owner projection from a fully validated RPC row."""
    route = AgentRouteProjection(
        work_order_id=row.work_order_id,
        scope_sha256=row.scope_sha256,
        branch_scope_key=row.branch_scope_key,
        idempotency_key=row.work_order.idempotency_key,
        title=row.work_order.title,
        client_id=row.work_order.client_id,
        repository=row.work_order.repository,
        branch_name=row.work_order.branch_name,
        expires_at=row.work_order.expires_at,
        owner=row.owner,
        reviewer=row.reviewer,
        status="ready_for_scope_review",
        blocker_codes=(),
        next_gate="human_scope_review",
    )
    packet = render_owner_planning_packet(row.work_order, route)
    assignment_status, next_gate = _ASSIGNMENT_ROUTE[row.state]
    return DurableAssignmentProjection(
        work_order_id=row.work_order_id,
        scope_sha256=row.scope_sha256,
        branch_scope_key=row.branch_scope_key,
        title=row.work_order.title,
        owner=row.owner,
        reviewer=row.reviewer,
        state=row.state,
        assignment_status=assignment_status,
        next_gate=next_gate,
        owner_planning_packet=packet,
        owner_planning_packet_sha256=hashlib.sha256(
            packet.encode("utf-8")
        ).hexdigest(),
        cost_observation=row.cost_observation,
        observed_cost_microusd=row.observed_cost_microusd,
        authorization_receipt_present=row.authorization_receipt is not None,
        authorization_payload_sha256=(
            row.authorization_receipt.payload_sha256
            if row.authorization_receipt is not None
            else None
        ),
        dispatch_packet_present=row.dispatch_packet is not None,
        dispatch_packet_sha256=(
            row.dispatch_packet.packet_sha256
            if row.dispatch_packet is not None
            else None
        ),
        dispatch_status=row.dispatch_status,
        result_receipt_present=row.result_receipt is not None,
        verification_receipt_present=row.verification_receipt is not None,
        operator_decision_present=row.operator_decision is not None,
        completion_receipt_present=row.completion_receipt is not None,
    )


class DurableCompanyCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(ge=0, le=1_000)
    states: dict[AgentDurableState, int]
    awaiting_independent_verification: int = Field(ge=0, le=1_000)
    awaiting_human_approval: int = Field(ge=0, le=1_000)
    completed: int = Field(ge=0, le=1_000)
    completion_receipts: int = Field(ge=0, le=1_000)
    observed_cost_rows: int = Field(ge=0, le=1_000)
    unobserved_cost_rows: int = Field(ge=0, le=1_000)
    observed_cost_microusd: int = Field(ge=0)


class DurableCompanySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-company-dashboard@1"] = (
        "agent-company-dashboard@1"
    )
    observed_at: datetime
    assignments: tuple[DurableAssignmentProjection, ...] = Field(
        default=(),
        max_length=1_000,
    )
    counts: DurableCompanyCounts
    read_only_projection: Literal[True] = True
    external_calls: Literal[False] = False
    database_calls: Literal[False] = False
    provider_calls: Literal[False] = False
    publication_calls: Literal[False] = False
    automatic_publication: Literal[False] = False

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _utc_seconds(value, "agent_company_observed_at_invalid")

    @model_validator(mode="after")
    def validate_counts(self) -> "DurableCompanySnapshot":
        ids = [str(item.work_order_id) for item in self.assignments]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("agent_company_assignment_order_invalid")
        state_counts = {state: 0 for state in AgentDurableState}
        for item in self.assignments:
            state_counts[item.state] += 1
        observed = [
            item
            for item in self.assignments
            if item.cost_observation == CostObservation.OBSERVED
        ]
        expected = DurableCompanyCounts(
            total=len(self.assignments),
            states=state_counts,
            awaiting_independent_verification=state_counts[
                AgentDurableState.AWAITING_REVIEW
            ],
            awaiting_human_approval=state_counts[AgentDurableState.VERIFIED],
            completed=state_counts[AgentDurableState.COMPLETED],
            completion_receipts=sum(
                item.completion_receipt_present for item in self.assignments
            ),
            observed_cost_rows=len(observed),
            unobserved_cost_rows=len(self.assignments) - len(observed),
            observed_cost_microusd=sum(
                item.observed_cost_microusd or 0 for item in observed
            ),
        )
        if self.counts != expected:
            raise ValueError("agent_company_counts_invalid")
        return self

    def canonical_snapshot(self) -> dict[str, object]:
        return {
            "assignments": [item.as_payload() for item in self.assignments],
            "automatic_publication": self.automatic_publication,
            "counts": self.counts.model_dump(mode="json"),
            "database_calls": self.database_calls,
            "external_calls": self.external_calls,
            "observed_at": _utc_z(self.observed_at),
            "provider_calls": self.provider_calls,
            "publication_calls": self.publication_calls,
            "read_only_projection": self.read_only_projection,
            "schema_version": self.schema_version,
        }

    @property
    def snapshot_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.canonical_snapshot())).hexdigest()

    def as_payload(self) -> dict[str, object]:
        return {
            **self.canonical_snapshot(),
            "snapshot_sha256": self.snapshot_sha256,
        }


def build_durable_company_snapshot(
    rows: Iterable[DurableAgentWorkOrderRow],
    *,
    observed_at: datetime,
) -> DurableCompanySnapshot:
    normalized_observed_at = _utc_seconds(
        observed_at,
        "agent_company_observed_at_invalid",
    )
    assignments = tuple(sorted(
        (build_durable_assignment_projection(row) for row in rows),
        key=lambda item: str(item.work_order_id),
    ))
    if len(assignments) > 1_000:
        raise ValueError("agent_company_assignment_count_invalid")
    ids = [str(item.work_order_id) for item in assignments]
    if len(ids) != len(set(ids)):
        raise ValueError("agent_company_work_order_duplicate")
    state_counts = {state: 0 for state in AgentDurableState}
    for item in assignments:
        state_counts[item.state] += 1
    observed = [
        item
        for item in assignments
        if item.cost_observation == CostObservation.OBSERVED
    ]
    return DurableCompanySnapshot(
        observed_at=normalized_observed_at,
        assignments=assignments,
        counts=DurableCompanyCounts(
            total=len(assignments),
            states=state_counts,
            awaiting_independent_verification=state_counts[
                AgentDurableState.AWAITING_REVIEW
            ],
            awaiting_human_approval=state_counts[AgentDurableState.VERIFIED],
            completed=state_counts[AgentDurableState.COMPLETED],
            completion_receipts=sum(
                item.completion_receipt_present for item in assignments
            ),
            observed_cost_rows=len(observed),
            unobserved_cost_rows=len(assignments) - len(observed),
            observed_cost_microusd=sum(
                item.observed_cost_microusd or 0 for item in observed
            ),
        ),
    )


def render_durable_company_dashboard(snapshot: DurableCompanySnapshot) -> str:
    state_lines = "\n".join(
        f"- {state.value}: {snapshot.counts.states[state]}건"
        for state in AgentDurableState
    )
    assignment_lines = "\n".join(
        (
            f"- `{item.work_order_id}`: `{item.owner.value}` -> "
            f"`{item.reviewer.value}` / `{item.assignment_status}` / "
            f"다음 `{item.next_gate}`"
        )
        for item in snapshot.assignments
    ) or "- 전담 업무 없음"
    review_lines = "\n".join(
        (
            f"- `{item.work_order_id}`: 독립 검증 "
            f"`{'완료' if item.verification_receipt_present else '대기'}`, "
            "대표 승인 "
            f"`{'완료' if item.operator_decision_present else '대기'}`"
        )
        for item in snapshot.assignments
        if item.state
        in {
            AgentDurableState.AWAITING_REVIEW,
            AgentDurableState.VERIFIED,
            AgentDurableState.APPROVED,
            AgentDurableState.COMPLETED,
        }
    ) or "- 독립 검증 또는 대표 승인 대기 없음"
    cost_lines = "\n".join((
        (
            f"- 관측된 비용: {snapshot.counts.observed_cost_microusd} "
            f"microusd ({snapshot.counts.observed_cost_rows}건)"
        ),
        (
            f"- 비용 미관측: {snapshot.counts.unobserved_cost_rows}건 "
            "(0으로 환산하지 않음)"
        ),
        f"- 완료 상태: {snapshot.counts.completed}건",
        f"- 완료 영수증: {snapshot.counts.completion_receipts}건",
        f"- Snapshot SHA-256: `{snapshot.snapshot_sha256}`",
    ))
    return f"""# CoinEasy AI 회사 운영 대시보드

## 1. 회사 상태

- 기준 시각: `{_utc_z(snapshot.observed_at)}`
- 전체 업무: {snapshot.counts.total}건
- 자동 발행: `OFF`
- 이 화면의 외부/DB/provider/publication 호출: `0`

## 2. 공통 업무 원장

{state_lines}

## 3. 고정 전담 역할

{assignment_lines}

각 owner와 reviewer는 업무 계약에 미리 고정됩니다. 이 투영과 owner
planning packet은 실행 승인서가 아닙니다.

## 4. 독립 검증 · 대표 승인함

- 독립 검증 대기: {snapshot.counts.awaiting_independent_verification}건
- 대표 승인 대기: {snapshot.counts.awaiting_human_approval}건
{review_lines}

## 5. 비용 · 완료

{cost_lines}
"""


__all__ = [
    "AgentAuthorizationReceipt",
    "AgentCompletionReceipt",
    "AgentDispatchPacket",
    "AgentDispatchStatus",
    "AgentDurableState",
    "AgentVerificationReceipt",
    "AgentWorkResultReceipt",
    "CostObservation",
    "DurableAgentWorkOrderRow",
    "DurableAssignmentProjection",
    "DurableCompanyCounts",
    "DurableCompanySnapshot",
    "OperatorDecisionReceipt",
    "build_durable_assignment_projection",
    "build_durable_company_snapshot",
    "render_durable_company_dashboard",
]
