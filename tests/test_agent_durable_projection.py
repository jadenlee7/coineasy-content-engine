from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from core.agent_control import (
    AgentDurableState,
    AgentWorkOrder,
    DurableAgentWorkOrderRow,
    ForbiddenAction,
    build_durable_assignment_projection,
    build_durable_company_snapshot,
    render_durable_company_dashboard,
)


OBSERVED_AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


def _uuid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _canonical_sha256(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def work_order(number: int = 1, **overrides: object) -> AgentWorkOrder:
    values: dict[str, object] = {
        "work_order_id": _uuid(number),
        "objective_id": _uuid(1_000 + number),
        "causation_id": _uuid(2_000 + number),
        "idempotency_key": f"agent-task:company:durable:{number}",
        "created_at": OBSERVED_AT - timedelta(days=1),
        "expires_at": OBSERVED_AT + timedelta(days=7),
        "owner": "devin",
        "reviewer": "codex",
        "work_type": "engineering",
        "risk_tier": "R1",
        "allowed_environment": "local",
        "title": f"Build durable task {number}",
        "objective": f"Build deterministic durable task number {number} locally.",
        "client_id": "origintrail",
        "repository": "jadenlee7/coineasy-content-engine",
        "base_sha": "a" * 40,
        "branch_name": f"agent/durable-task-{number}",
        "allowed_paths": [f"plans/durable-task-{number}.md"],
        "evidence": [{
            "uri": "tests/fixtures/agent-work-order-evidence.txt",
            "sha256": (
                "de5bbcf959f710829bd8242f750c2111869055d8e798268620b125cf5eb81761"
            ),
        }],
        "expected_artifacts": [f"Durable artifact {number}"],
        "acceptance_criteria": ["The result is deterministic and local only."],
        "verification_commands": [
            "PYTHONPATH=. .venv/bin/pytest -q "
            "tests/test_agent_durable_projection.py",
        ],
        "forbidden_actions": [action.value for action in ForbiddenAction],
        "max_runtime_seconds": 1_800,
        "max_handoffs": 1,
        "max_cost_microusd": 0,
        "max_external_actions": 0,
        "automatic_publication": False,
    }
    values.update(overrides)
    return AgentWorkOrder.model_validate(values)


def receipt_chain(order: AgentWorkOrder) -> dict[str, object]:
    base = 10_000 + int(str(order.work_order_id)[-4:])
    result_id = _uuid(base + 1)
    verification_id = _uuid(base + 2)
    decision_id = _uuid(base + 3)
    return {
        "result_receipt": {
            "receipt_id": result_id,
            "work_order_id": str(order.work_order_id),
            "owner": order.owner.value,
            "artifact_sha256": "b" * 64,
            "recorded_at": OBSERVED_AT + timedelta(minutes=1),
            "automatic_publication": False,
        },
        "verification_receipt": {
            "receipt_id": verification_id,
            "work_order_id": str(order.work_order_id),
            "reviewer": order.reviewer.value,
            "result_receipt_id": result_id,
            "passed": True,
            "evidence_sha256": "c" * 64,
            "recorded_at": OBSERVED_AT + timedelta(minutes=2),
            "automatic_publication": False,
        },
        "operator_decision": {
            "receipt_id": decision_id,
            "work_order_id": str(order.work_order_id),
            "operator": "human_operator",
            "verification_receipt_id": verification_id,
            "decision": "approved",
            "recorded_at": OBSERVED_AT + timedelta(minutes=3),
            "automatic_publication": False,
        },
        "completion_receipt": {
            "receipt_id": _uuid(base + 4),
            "work_order_id": str(order.work_order_id),
            "recorded_by": "railway_worker",
            "operator_decision_receipt_id": decision_id,
            "recorded_at": OBSERVED_AT + timedelta(minutes=4),
            "automatic_publication": False,
        },
    }


def authorization_proofs(order: AgentWorkOrder) -> dict[str, object]:
    authorization_payload: dict[str, object] = {
        "actor_user_id": _uuid(30_001),
        "automatic_publication": False,
        "decision": "authorized",
        "max_cost_microusd": 0,
        "max_external_actions": 0,
        "schema_version": "agent-authorization-receipt@1",
        "scope_sha256": order.scope_sha256,
        "work_order_id": str(order.work_order_id),
    }
    dispatch_payload: dict[str, object] = {
        "automatic_publication": False,
        "base_sha": order.base_sha,
        "branch_name": order.branch_name,
        "max_cost_microusd": 0,
        "max_external_actions": 0,
        "owner": order.owner.value,
        "repository": order.repository,
        "reviewer": order.reviewer.value,
        "schema_version": "agent-dispatch-packet@1",
        "scope_sha256": order.scope_sha256,
        "work_order_id": str(order.work_order_id),
    }

    return {
        "authorization_receipt": {
            **authorization_payload,
            "receipt_id": _uuid(30_002),
            "payload_sha256": _canonical_sha256(authorization_payload),
        },
        "dispatch_packet": {
            **dispatch_payload,
            "packet_sha256": _canonical_sha256(dispatch_payload),
        },
    }


def terminal_decision(*, work_order_id: str, decision: str) -> dict[str, object]:
    return {
        "receipt_id": _uuid(20_001),
        "work_order_id": work_order_id,
        "operator": "human_operator",
        "verification_receipt_id": None,
        "decision": decision,
        "recorded_at": OBSERVED_AT + timedelta(minutes=1),
        "automatic_publication": False,
    }


def row_payload(
    state: str = "proposed",
    *,
    number: int = 1,
    order: AgentWorkOrder | None = None,
    observed_cost_microusd: int | None = None,
    cost_observation: str = "unobserved",
) -> dict[str, object]:
    bound_order = order or work_order(number)
    values: dict[str, object] = {
        "work_order_id": str(bound_order.work_order_id),
        "scope_sha256": bound_order.scope_sha256,
        "branch_scope_key": bound_order.branch_scope_key,
        "owner": bound_order.owner.value,
        "reviewer": bound_order.reviewer.value,
        "state": state,
        "work_order": bound_order.model_dump(mode="json"),
        "cost_observation": cost_observation,
        "observed_cost_microusd": observed_cost_microusd,
    }
    proof_required_states = {
        "authorized",
        "claimed",
        "in_progress",
        "awaiting_review",
        "verified",
        "approved",
        "completed",
        "blocked",
    }
    if state in proof_required_states:
        values.update(authorization_proofs(bound_order))
        values["dispatch_status"] = {
            "authorized": "pending",
            "claimed": "claimed",
            "in_progress": "attempt_started",
            "awaiting_review": "delivered",
            "verified": "delivered",
            "approved": "delivered",
            "completed": "delivered",
            "blocked": "delivery_unknown",
        }[state]
    levels = {
        "awaiting_review": 1,
        "verified": 2,
        "approved": 3,
        "completed": 4,
    }
    chain = receipt_chain(bound_order)
    for key in tuple(chain)[:levels.get(state, 0)]:
        values[key] = chain[key]
    return values


def durable_row(state: str = "proposed", **kwargs: object) -> DurableAgentWorkOrderRow:
    return DurableAgentWorkOrderRow.model_validate(row_payload(state, **kwargs))


def test_rpc_row_and_assignment_are_deterministic_and_deeply_immutable():
    row = durable_row("awaiting_review")
    replay = DurableAgentWorkOrderRow.model_validate(row.model_dump(mode="json"))

    projection = build_durable_assignment_projection(row)
    replay_projection = build_durable_assignment_projection(replay)

    assert projection.as_payload() == replay_projection.as_payload()
    assert projection.projection_sha256 == (
        "0554b3398e2753174580323617ab625b326f7947371db40eea5977cc8d9e17a8"
    )
    assert projection.assignment_status == "awaiting_independent_verification"
    assert projection.next_gate == "independent_verification"
    assert projection.owner.value == "devin"
    assert projection.reviewer.value == "codex"
    assert projection.authorization_receipt_present is True
    assert projection.authorization_payload_sha256 is not None
    assert projection.dispatch_packet_present is True
    assert projection.dispatch_packet_sha256 is not None
    assert projection.dispatch_status is not None
    assert projection.dispatch_status.value == "delivered"
    assert projection.result_receipt_present is True
    assert projection.verification_receipt_present is False
    assert projection.projection_authorizes_execution is False
    assert projection.external_calls is False
    assert projection.database_calls is False
    assert projection.provider_calls is False
    assert projection.publication_calls is False
    assert projection.automatic_publication is False
    assert "Execution authorized: `false`" in projection.owner_planning_packet
    with pytest.raises(ValidationError):
        projection.assignment_status = "completed"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["scope_sha256", "branch_scope_key"])
def test_rpc_row_recomputes_and_rejects_digest_drift(field: str):
    payload = row_payload()
    payload[field] = "f" * 64

    with pytest.raises(
        ValidationError,
        match="agent_durable_work_order_binding_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


def test_assignment_projection_rejects_planning_packet_digest_drift():
    projection = build_durable_assignment_projection(durable_row())
    payload = projection.model_dump(mode="json")
    payload["owner_planning_packet"] += "\nforged"

    with pytest.raises(
        ValidationError,
        match="agent_assignment_packet_digest_invalid",
    ):
        type(projection).model_validate(payload)


def test_assignment_projection_rejects_dispatch_status_drift():
    projection = build_durable_assignment_projection(
        durable_row("awaiting_review")
    )
    payload = projection.model_dump(mode="json")
    payload["dispatch_status"] = "pending"

    with pytest.raises(
        ValidationError,
        match="agent_assignment_dispatch_status_progression_invalid",
    ):
        type(projection).model_validate(payload)


@pytest.mark.parametrize(
    "state",
    [
        "authorized",
        "claimed",
        "in_progress",
        "awaiting_review",
        "verified",
        "approved",
        "completed",
        "blocked",
    ],
)
@pytest.mark.parametrize("missing", ["authorization_receipt", "dispatch_packet"])
def test_post_authorization_states_require_both_hash_bound_proofs(
    state: str,
    missing: str,
):
    payload = row_payload(state)
    if state == "blocked":
        payload["operator_decision"] = terminal_decision(
            work_order_id=str(payload["work_order_id"]),
            decision="blocked",
        )
    payload.pop(missing)

    with pytest.raises(
        ValidationError,
        match="agent_durable_authorization_proof_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


@pytest.mark.parametrize(
    ("state", "dispatch_status"),
    [
        ("authorized", "pending"),
        ("claimed", "claimed"),
        ("in_progress", "attempt_started"),
        ("awaiting_review", "delivered"),
        ("verified", "delivered"),
        ("approved", "delivered"),
        ("completed", "delivered"),
    ],
)
def test_dispatch_outbox_status_tracks_the_durable_progression(
    state: str,
    dispatch_status: str,
):
    row = durable_row(state)
    assert row.dispatch_status is not None
    assert row.dispatch_status.value == dispatch_status


def test_dispatch_outbox_status_is_required_iff_a_packet_exists():
    payload = row_payload("authorized")
    payload.pop("dispatch_status")
    with pytest.raises(
        ValidationError,
        match="agent_durable_dispatch_status_presence_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)

    payload = row_payload()
    payload["dispatch_status"] = "pending"
    with pytest.raises(
        ValidationError,
        match="agent_durable_dispatch_status_presence_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


@pytest.mark.parametrize(
    ("state", "tampered_status"),
    [
        ("authorized", "claimed"),
        ("claimed", "pending"),
        ("in_progress", "delivered"),
        ("awaiting_review", "attempt_started"),
        ("verified", "failed"),
        ("approved", "cancelled"),
        ("completed", "delivery_unknown"),
    ],
)
def test_dispatch_outbox_status_tamper_fails_closed(
    state: str,
    tampered_status: str,
):
    payload = row_payload(state)
    payload["dispatch_status"] = tampered_status
    with pytest.raises(
        ValidationError,
        match="agent_durable_dispatch_status_progression_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


@pytest.mark.parametrize(
    "dispatch_status",
    ["cancelled", "failed", "delivery_unknown"],
)
def test_blocked_accepts_only_terminal_outbox_states(dispatch_status: str):
    payload = row_payload("blocked")
    payload["dispatch_status"] = dispatch_status
    payload["operator_decision"] = terminal_decision(
        work_order_id=str(payload["work_order_id"]),
        decision="blocked",
    )
    row = DurableAgentWorkOrderRow.model_validate(payload)
    assert row.dispatch_status is not None
    assert row.dispatch_status.value == dispatch_status


def test_proposed_rejects_authorization_or_dispatch_proof():
    order = work_order()
    proofs = authorization_proofs(order)
    for field in ("authorization_receipt", "dispatch_packet"):
        payload = row_payload(order=order)
        payload[field] = proofs[field]
        with pytest.raises(
            ValidationError,
            match="agent_durable_authorization_proof_invalid",
        ):
            DurableAgentWorkOrderRow.model_validate(payload)


def test_authorization_and_dispatch_recompute_canonical_payload_hashes():
    payload = row_payload("authorized")
    authorization = payload["authorization_receipt"]
    assert isinstance(authorization, dict)
    authorization["payload_sha256"] = "f" * 64
    with pytest.raises(
        ValidationError,
        match="agent_authorization_payload_digest_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)

    payload = row_payload("authorized")
    dispatch = payload["dispatch_packet"]
    assert isinstance(dispatch, dict)
    dispatch["packet_sha256"] = "f" * 64
    with pytest.raises(
        ValidationError,
        match="agent_dispatch_packet_digest_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("work_order_id", _uuid(40_001)),
        ("scope_sha256", "d" * 64),
    ],
)
def test_rehashed_authorization_still_must_bind_the_work_order(
    field: str,
    value: str,
):
    payload = row_payload("authorized")
    authorization = payload["authorization_receipt"]
    assert isinstance(authorization, dict)
    authorization[field] = value
    hashed = {
        key: item
        for key, item in authorization.items()
        if key not in {"receipt_id", "payload_sha256"}
    }
    authorization["payload_sha256"] = _canonical_sha256(hashed)

    with pytest.raises(
        ValidationError,
        match="agent_durable_authorization_binding_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("work_order_id", _uuid(40_002)),
        ("scope_sha256", "d" * 64),
        ("owner", "claude_code"),
        ("reviewer", "devin"),
        ("repository", "jadenlee7/other-repository"),
        ("base_sha", "b" * 40),
        ("branch_name", "agent/different-branch"),
    ],
)
def test_rehashed_dispatch_still_must_bind_every_assignment_field(
    field: str,
    value: str,
):
    payload = row_payload("authorized")
    dispatch = payload["dispatch_packet"]
    assert isinstance(dispatch, dict)
    dispatch[field] = value
    hashed = {
        key: item
        for key, item in dispatch.items()
        if key != "packet_sha256"
    }
    dispatch["packet_sha256"] = _canonical_sha256(hashed)

    expected = (
        "agent_dispatch_packet_separation_invalid"
        if field == "reviewer"
        else "agent_durable_dispatch_binding_invalid"
    )
    with pytest.raises(ValidationError, match=expected):
        DurableAgentWorkOrderRow.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("work_order_id", _uuid(900)),
        ("owner", "claude_code"),
        ("reviewer", "claude_code"),
    ],
)
def test_rpc_row_rejects_stored_identity_drift(field: str, value: str):
    payload = row_payload()
    payload[field] = value

    with pytest.raises(
        ValidationError,
        match="agent_durable_work_order_binding_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


def test_rpc_payload_rejects_self_review():
    payload = row_payload()
    work_order_payload = payload["work_order"]
    assert isinstance(work_order_payload, dict)
    work_order_payload["reviewer"] = work_order_payload["owner"]
    payload["reviewer"] = payload["owner"]

    with pytest.raises(ValidationError, match="agent_work_order_separation_invalid"):
        DurableAgentWorkOrderRow.model_validate(payload)


@pytest.mark.parametrize(
    ("state", "keep_receipts"),
    [
        ("awaiting_review", 0),
        ("verified", 1),
        ("approved", 2),
        ("completed", 3),
    ],
)
def test_receipt_progression_fails_closed_when_required_receipt_is_missing(
    state: str,
    keep_receipts: int,
):
    payload = row_payload(state)
    receipt_fields = (
        "result_receipt",
        "verification_receipt",
        "operator_decision",
        "completion_receipt",
    )
    for field in receipt_fields[keep_receipts:]:
        payload.pop(field, None)

    with pytest.raises(
        ValidationError,
        match="agent_durable_receipt_progression_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


def test_completed_state_requires_a_linked_completion_receipt():
    completed = durable_row(
        "completed",
        cost_observation="observed",
        observed_cost_microusd=0,
    )
    assert completed.completion_receipt is not None
    assert completed.operator_decision is not None

    payload = row_payload("completed")
    payload.pop("completion_receipt")
    with pytest.raises(
        ValidationError,
        match="agent_durable_receipt_progression_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


def test_verification_must_be_independent_and_link_to_the_owner_result():
    payload = row_payload("verified")
    verification = payload["verification_receipt"]
    assert isinstance(verification, dict)
    verification["reviewer"] = payload["owner"]

    with pytest.raises(
        ValidationError,
        match="agent_durable_verification_independence_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)

    payload = row_payload("awaiting_review")
    result = payload["result_receipt"]
    assert isinstance(result, dict)
    result["work_order_id"] = _uuid(998)
    with pytest.raises(
        ValidationError,
        match="agent_durable_receipt_binding_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)

    payload = row_payload("verified")
    verification = payload["verification_receipt"]
    assert isinstance(verification, dict)
    verification["result_receipt_id"] = _uuid(999)
    with pytest.raises(
        ValidationError,
        match="agent_durable_verification_independence_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


def test_receipts_reject_backward_time_and_blocked_approval():
    payload = row_payload("verified")
    verification = payload["verification_receipt"]
    assert isinstance(verification, dict)
    verification["recorded_at"] = OBSERVED_AT
    with pytest.raises(ValidationError, match="agent_durable_receipt_order_invalid"):
        DurableAgentWorkOrderRow.model_validate(payload)

    payload = row_payload("approved")
    decision = payload["operator_decision"]
    assert isinstance(decision, dict)
    decision["decision"] = "blocked"
    with pytest.raises(
        ValidationError,
        match="agent_durable_operator_decision_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


@pytest.mark.parametrize("state", ["blocked", "cancelled"])
def test_terminal_stop_accepts_its_human_decision_without_fake_result_chain(
    state: str,
):
    order = work_order()
    payload = row_payload(state, order=order)
    payload["operator_decision"] = {
        "receipt_id": _uuid(20_001),
        "work_order_id": str(order.work_order_id),
        "operator": "human_operator",
        "verification_receipt_id": None,
        "decision": state,
        "recorded_at": OBSERVED_AT + timedelta(minutes=1),
        "automatic_publication": False,
    }

    row = DurableAgentWorkOrderRow.model_validate(payload)

    assert row.state.value == state
    assert row.operator_decision is not None
    assert row.operator_decision.decision == state
    assert row.completion_receipt is None


def test_terminal_stop_rejects_wrong_decision_or_completion_receipt():
    order = work_order()
    payload = row_payload("blocked", order=order)
    payload["operator_decision"] = {
        "receipt_id": _uuid(20_001),
        "work_order_id": str(order.work_order_id),
        "operator": "human_operator",
        "verification_receipt_id": None,
        "decision": "cancelled",
        "recorded_at": OBSERVED_AT + timedelta(minutes=1),
        "automatic_publication": False,
    }
    with pytest.raises(
        ValidationError,
        match="agent_durable_operator_decision_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)

    payload = row_payload("blocked", order=order)
    payload["completion_receipt"] = receipt_chain(order)["completion_receipt"]
    with pytest.raises(ValidationError):
        DurableAgentWorkOrderRow.model_validate(payload)


@pytest.mark.parametrize("state", ["blocked", "cancelled"])
def test_terminal_stop_requires_matching_human_decision(state: str):
    payload = row_payload(state)

    with pytest.raises(
        ValidationError,
        match="agent_durable_operator_decision_required",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)


def test_cancelled_accepts_no_authorization_proof_or_the_complete_pair_only():
    order = work_order()
    decision = terminal_decision(
        work_order_id=str(order.work_order_id),
        decision="cancelled",
    )
    no_proof = row_payload("cancelled", order=order)
    no_proof["operator_decision"] = decision
    assert DurableAgentWorkOrderRow.model_validate(no_proof).state == (
        AgentDurableState.CANCELLED
    )

    proofs = authorization_proofs(order)
    paired = row_payload("cancelled", order=order)
    paired.update(proofs)
    paired["dispatch_status"] = "cancelled"
    paired["operator_decision"] = decision
    assert DurableAgentWorkOrderRow.model_validate(paired).state == (
        AgentDurableState.CANCELLED
    )

    for missing in ("authorization_receipt", "dispatch_packet"):
        incomplete = row_payload("cancelled", order=order)
        incomplete["operator_decision"] = decision
        incomplete.update(proofs)
        incomplete["dispatch_status"] = "cancelled"
        incomplete.pop(missing)
        with pytest.raises(
            ValidationError,
            match="agent_durable_authorization_proof_invalid",
        ):
            DurableAgentWorkOrderRow.model_validate(incomplete)


def test_unknown_cost_is_not_zero_and_observed_zero_remains_valid():
    unknown = durable_row()
    zero = durable_row(
        number=2,
        cost_observation="observed",
        observed_cost_microusd=0,
    )
    snapshot = build_durable_company_snapshot(
        [zero, unknown],
        observed_at=OBSERVED_AT,
    )
    dashboard = render_durable_company_dashboard(snapshot)

    assert snapshot.counts.observed_cost_rows == 1
    assert snapshot.counts.unobserved_cost_rows == 1
    assert snapshot.counts.observed_cost_microusd == 0
    assert "관측된 비용: 0 microusd (1건)" in dashboard
    assert "비용 미관측: 1건 (0으로 환산하지 않음)" in dashboard

    payload = row_payload(observed_cost_microusd=0)
    with pytest.raises(
        ValidationError,
        match="agent_durable_cost_observation_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)

    payload = row_payload(cost_observation="observed")
    with pytest.raises(
        ValidationError,
        match="agent_durable_cost_observation_invalid",
    ):
        DurableAgentWorkOrderRow.model_validate(payload)

    with pytest.raises(ValidationError):
        DurableAgentWorkOrderRow.model_validate(row_payload(
            cost_observation="observed",
            observed_cost_microusd=-1,
        ))
    with pytest.raises(ValidationError):
        DurableAgentWorkOrderRow.model_validate(row_payload(
            cost_observation="observed",
            observed_cost_microusd=1,
        ))


def test_company_snapshot_and_five_section_dashboard_are_golden_and_order_stable():
    pending = durable_row()
    complete = durable_row(
        "completed",
        number=2,
        order=work_order(2, owner="codex", reviewer="claude_code"),
        cost_observation="observed",
        observed_cost_microusd=0,
    )

    snapshot = build_durable_company_snapshot(
        [complete, pending],
        observed_at=OBSERVED_AT,
    )
    replay = build_durable_company_snapshot(
        [pending, complete],
        observed_at=OBSERVED_AT,
    )
    dashboard = render_durable_company_dashboard(snapshot)

    assert snapshot.as_payload() == replay.as_payload()
    assert snapshot.snapshot_sha256 == (
        "e36728f4865084a85984624e9e10fa3efbde84d1f176ac21673d71623bd09b06"
    )
    assert snapshot.counts.total == 2
    assert snapshot.counts.states[AgentDurableState.PROPOSED] == 1
    assert snapshot.counts.states[AgentDurableState.COMPLETED] == 1
    assert snapshot.counts.completed == 1
    assert snapshot.counts.completion_receipts == 1
    assert dashboard.count("## ") == 5
    assert "## 1. 회사 상태" in dashboard
    assert "## 2. 공통 업무 원장" in dashboard
    assert "## 3. AI 자동 배정" in dashboard
    assert "## 4. 독립 검증 · 대표 승인함" in dashboard
    assert "## 5. 비용 · 완료" in dashboard
    assert "자동 발행: `OFF`" in dashboard
    assert "비용 미관측: 1건 (0으로 환산하지 않음)" in dashboard
    assert "완료 상태: 1건" in dashboard
    assert "완료 영수증: 1건" in dashboard


def test_empty_company_dashboard_is_valid_and_reports_no_assignments():
    snapshot = build_durable_company_snapshot([], observed_at=OBSERVED_AT)
    dashboard = render_durable_company_dashboard(snapshot)

    assert snapshot.counts.total == 0
    assert snapshot.counts.observed_cost_microusd == 0
    assert snapshot.counts.observed_cost_rows == 0
    assert snapshot.counts.unobserved_cost_rows == 0
    assert "배정된 업무 없음" in dashboard
    assert "비용 미관측: 0건 (0으로 환산하지 않음)" in dashboard


def test_rpc_row_rejects_unknown_fields_and_automatic_publication():
    payload = row_payload()
    payload["unexpected"] = "drift"
    with pytest.raises(ValidationError):
        DurableAgentWorkOrderRow.model_validate(payload)

    payload = row_payload("awaiting_review")
    result = payload["result_receipt"]
    assert isinstance(result, dict)
    result["automatic_publication"] = True
    with pytest.raises(ValidationError):
        DurableAgentWorkOrderRow.model_validate(payload)
