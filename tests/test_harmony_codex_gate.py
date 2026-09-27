from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID

import pytest
from pydantic import ValidationError

from core.agent_control.codex_gate import (
    SquidCodexGateCostObservation,
    SquidCodexGateError,
    SquidCodexGateRequest,
    SquidCodexGateResultReceipt,
    SquidCodexGateRunner,
    SquidCodexGateState,
    SquidCodexGateTerminalReason,
    SquidCodexSemanticQaEvidence,
    SquidCodexSourceLineageReceipt,
    SquidCodexSpecialistBinding,
    bind_squid_codex_gate_receipt,
    bind_squid_codex_gate_request,
    bind_squid_codex_qa_evidence,
    bind_squid_codex_source_lineage,
    bind_squid_codex_specialist_binding,
)
from core.agent_control.preview_collaboration import (
    PreviewHarmonyStage,
    PreviewHarmonyStageReceipt,
    bind_preview_stage_receipt,
    preview_stage_operation_key_sha256,
)


NOW = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
BRANCH_REF = "a" * 20


def _uuid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _specialist_binding(
    stage: str,
    *,
    principal_id: str,
    config_sha256: str = "c" * 64,
    release_sha: str = "b" * 40,
    branch_ref: str = BRANCH_REF,
    created_at: datetime = NOW - timedelta(minutes=30),
    expires_at: datetime = NOW + timedelta(minutes=60),
) -> SquidCodexSpecialistBinding:
    specialist_code, role_name, capability, actor = {
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
    }[stage]
    return SquidCodexSpecialistBinding.model_validate(
        bind_squid_codex_specialist_binding({
            "branch_ref": branch_ref,
            "workspace_id": _uuid(1),
            "stage": stage,
            "specialist_code": specialist_code,
            "role_name": role_name,
            "capability": capability,
            "actor": actor,
            "principal_id": principal_id,
            "producer_release_sha": release_sha,
            "config_sha256": config_sha256,
            "created_at": created_at,
            "expires_at": expires_at,
        })
    )


def _stage_receipt(
    stage: PreviewHarmonyStage,
    *,
    receipt_id: str,
    principal_id: str,
    specialist_binding_sha256: str,
    input_sha256: str,
    output_sha256: str,
    previous_receipt_sha256: str | None,
    config_sha256: str = "c" * 64,
    release_sha: str = "b" * 40,
    recorded_at: datetime = NOW - timedelta(minutes=10),
) -> PreviewHarmonyStageReceipt:
    ordinal, actor, capability, specialist_code = {
        PreviewHarmonyStage.PLAN: (
            1, "grok_bot", "harmony_plan", "squid_planner",
        ),
        PreviewHarmonyStage.PRIVATE_CONTENT: (
            2, "content_engine", "harmony_prepare_private_content",
            "squid_private_content_producer",
        ),
    }[stage]
    return PreviewHarmonyStageReceipt.model_validate(
        bind_preview_stage_receipt({
            "receipt_id": receipt_id,
            "workspace_id": _uuid(1),
            "client_id": "squid",
            "round_id": _uuid(2),
            "plan_id": _uuid(3),
            "stage": stage,
            "ordinal": ordinal,
            "actor": actor,
            "principal_id": principal_id,
            "producer_release_sha": release_sha,
            "config_sha256": config_sha256,
            "capability": capability,
            "specialist_code": specialist_code,
            "specialist_binding_sha256": specialist_binding_sha256,
            "operation_key_sha256": preview_stage_operation_key_sha256(
                specialist_binding_sha256=specialist_binding_sha256,
                workspace_id=_uuid(1),
                client_id="squid",
                plan_id=_uuid(3),
                stage=stage,
                input_sha256=input_sha256,
                output_sha256=output_sha256,
            ),
            "binding_receipt_sha256": "d" * 64,
            "verdict": None,
            "reviewer_principal_id": None,
            "previous_receipt_sha256": previous_receipt_sha256,
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
            "recorded_at": recorded_at,
        })
    )


def _source_lineage(
    *,
    official_content_version_id: str = _uuid(20),
    official_source_item_id: str = _uuid(21),
    official_source_binding_sha256: str = "3" * 64,
    content_snapshot_sha256: str = "4" * 64,
    source_producer_principal_id: str = _uuid(7),
    signal_manifest_sha256: str = "b" * 64,
    signal_input_set_sha256: str = "0" * 64,
    signal_producer_principal_ids: tuple[str, ...] | None = None,
    branch_fence_created_at: datetime = NOW - timedelta(minutes=30),
    source_signal_expires_at: datetime = NOW + timedelta(minutes=45),
) -> SquidCodexSourceLineageReceipt:
    producers = tuple(sorted(signal_producer_principal_ids or (
        source_producer_principal_id, _uuid(8), _uuid(9), _uuid(10),
    )))
    return SquidCodexSourceLineageReceipt.model_validate(
        bind_squid_codex_source_lineage({
            "branch_ref": BRANCH_REF,
            "branch_fence_created_at": branch_fence_created_at,
            "branch_fence_expires_at": NOW + timedelta(minutes=90),
            "observed_at": NOW,
            "workspace_id": _uuid(1),
            "round_id": _uuid(2),
            "plan_id": _uuid(3),
            "signal_manifest_sha256": signal_manifest_sha256,
            "signal_input_set_sha256": signal_input_set_sha256,
            "signal_producer_principal_ids": producers,
            "source_signal_id": _uuid(22),
            "source_signal_payload_sha256": "5" * 64,
            "source_producer_principal_id": source_producer_principal_id,
            "source_signal_expires_at": source_signal_expires_at,
            "connector_receipt_sha256": "6" * 64,
            "upstream_receipt_sha256": official_source_binding_sha256,
            "official_content_version_id": official_content_version_id,
            "official_source_item_id": official_source_item_id,
            "official_source_binding_sha256": official_source_binding_sha256,
            "content_snapshot_sha256": content_snapshot_sha256,
        })
    )


def _request(
    *,
    reviewer_principal_id: str = _uuid(6),
    reviewer_config_sha256: str = "c" * 64,
    reviewer_release_sha: str = "b" * 40,
    private_principal_id: str = _uuid(5),
    plan_principal_id: str = _uuid(4),
    source_producer_principal_id: str = _uuid(7),
    signal_producer_principal_ids: tuple[str, ...] | None = None,
    private_output_sha256: str = "2" * 64,
    private_previous_receipt_sha256: str | None = None,
    private_input_sha256: str | None = None,
    official_content_version_id: str = _uuid(20),
    official_source_item_id: str = _uuid(21),
    official_source_binding_sha256: str = "3" * 64,
    content_snapshot_sha256: str = "4" * 64,
    signal_manifest_sha256: str = "b" * 64,
    plan_recorded_at: datetime = NOW - timedelta(minutes=10),
    private_recorded_at: datetime = NOW - timedelta(minutes=10),
    private_binding_created_at: datetime = NOW - timedelta(minutes=30),
    reviewer_binding_created_at: datetime = NOW - timedelta(minutes=30),
    branch_fence_created_at: datetime = NOW - timedelta(minutes=30),
    source_signal_expires_at: datetime = NOW + timedelta(minutes=45),
    approved_cost_cap_microusd: int = 1_000,
    request_overrides: dict[str, object] | None = None,
) -> SquidCodexGateRequest:
    private_binding = _specialist_binding(
        "private_content",
        principal_id=private_principal_id,
        created_at=private_binding_created_at,
    )
    reviewer_binding = _specialist_binding(
        "independent_qa",
        principal_id=reviewer_principal_id,
        config_sha256=reviewer_config_sha256,
        release_sha=reviewer_release_sha,
        created_at=reviewer_binding_created_at,
    )
    plan = _stage_receipt(
        PreviewHarmonyStage.PLAN,
        receipt_id=_uuid(100),
        principal_id=plan_principal_id,
        specialist_binding_sha256="7" * 64,
        input_sha256="0" * 64,
        output_sha256="1" * 64,
        previous_receipt_sha256=None,
        recorded_at=plan_recorded_at,
    )
    private_content = _stage_receipt(
        PreviewHarmonyStage.PRIVATE_CONTENT,
        receipt_id=_uuid(101),
        principal_id=private_principal_id,
        specialist_binding_sha256=private_binding.binding_sha256,
        input_sha256=private_input_sha256 or plan.output_sha256,
        output_sha256=private_output_sha256,
        previous_receipt_sha256=(
            plan.receipt_sha256
            if private_previous_receipt_sha256 is None
            else private_previous_receipt_sha256
        ),
        recorded_at=private_recorded_at,
    )
    producers = tuple(sorted(signal_producer_principal_ids or (
        source_producer_principal_id, _uuid(8), _uuid(9), _uuid(10),
    )))
    source = _source_lineage(
        official_content_version_id=official_content_version_id,
        official_source_item_id=official_source_item_id,
        official_source_binding_sha256=official_source_binding_sha256,
        content_snapshot_sha256=content_snapshot_sha256,
        source_producer_principal_id=source_producer_principal_id,
        signal_manifest_sha256=signal_manifest_sha256,
        signal_producer_principal_ids=producers,
        branch_fence_created_at=branch_fence_created_at,
        source_signal_expires_at=source_signal_expires_at,
    )
    payload: dict[str, object] = {
        "workspace_id": _uuid(1),
        "round_id": _uuid(2),
        "plan_id": _uuid(3),
        "plan_receipt": plan,
        "private_content_receipt": private_content,
        "private_content_binding": private_binding,
        "source_lineage": source,
        "reviewer_binding": reviewer_binding,
        "signal_producer_principal_ids": producers,
        "approved_cost_cap_microusd": approved_cost_cap_microusd,
    }
    if request_overrides:
        payload.update(request_overrides)
    return SquidCodexGateRequest.model_validate(
        bind_squid_codex_gate_request(payload)
    )


def _evidence(
    request: SquidCodexGateRequest,
    attempt_fence_sha256: str,
    *,
    verdict: str = "pass",
    findings: tuple[str, ...] = (),
    criteria: dict[str, bool] | None = None,
    qa_output_sha256: str = "e" * 64,
    **overrides: object,
) -> SquidCodexSemanticQaEvidence:
    source = request.source_lineage
    payload: dict[str, object] = {
        "work_key": request.work_key,
        "assignment_key": request.assignment_key,
        "request_key": request.request_key,
        "attempt_fence_sha256": attempt_fence_sha256,
        "source_lineage_sha256": source.lineage_sha256,
        "private_content_receipt_sha256": request.private_content_receipt_sha256,
        "reviewed_output_sha256": request.private_content_output_sha256,
        "official_content_version_id": source.official_content_version_id,
        "official_source_item_id": source.official_source_item_id,
        "official_source_binding_sha256": source.official_source_binding_sha256,
        "content_snapshot_sha256": source.content_snapshot_sha256,
        "reviewer_principal_id": request.reviewer_principal_id,
        "reviewer_specialist_binding_sha256": (
            request.reviewer_binding.binding_sha256
        ),
        "reviewer_release_sha": request.reviewer_release_sha,
        "reviewer_config_sha256": request.reviewer_config_sha256,
        "qa_output_sha256": qa_output_sha256,
        "criteria": criteria or {
            "automatic_publication_off": True,
            "factual_binding": True,
            "no_external_calls": True,
            "output_contract_valid": True,
            "private_boundary_preserved": True,
            "source_lineage_complete": True,
        },
        "findings": findings,
        "verdict": verdict,
    }
    payload.update(overrides)
    return SquidCodexSemanticQaEvidence.model_validate(
        bind_squid_codex_qa_evidence(payload)
    )


def _receipt(
    request: SquidCodexGateRequest,
    evidence: SquidCodexSemanticQaEvidence,
    *,
    cost_observation: str = "observed",
    observed_cost_microusd: int | None = 123,
    **overrides: object,
) -> SquidCodexGateResultReceipt:
    source = request.source_lineage
    payload: dict[str, object] = {
        "receipt_id": _uuid(200),
        "work_key": request.work_key,
        "assignment_key": request.assignment_key,
        "request_key": request.request_key,
        "attempt_fence_sha256": evidence.attempt_fence_sha256,
        "workspace_id": request.workspace_id,
        "round_id": request.round_id,
        "plan_id": request.plan_id,
        "private_content_receipt_id": request.private_content_receipt_id,
        "private_content_receipt_sha256": request.private_content_receipt_sha256,
        "private_content_producer_principal_id": (
            request.private_content_producer_principal_id
        ),
        "private_content_specialist_binding_sha256": (
            request.private_content_binding.binding_sha256
        ),
        "private_content_output_sha256": request.private_content_output_sha256,
        "source_lineage_sha256": source.lineage_sha256,
        "official_content_version_id": source.official_content_version_id,
        "official_source_item_id": source.official_source_item_id,
        "official_source_binding_sha256": source.official_source_binding_sha256,
        "content_snapshot_sha256": source.content_snapshot_sha256,
        "reviewer_principal_id": request.reviewer_principal_id,
        "reviewer_specialist_binding_sha256": (
            request.reviewer_binding.binding_sha256
        ),
        "reviewer_release_sha": request.reviewer_release_sha,
        "reviewer_config_sha256": request.reviewer_config_sha256,
        "qa_output_sha256": evidence.qa_output_sha256,
        "evidence_sha256": evidence.evidence_sha256,
        "verdict": evidence.verdict,
        "approved_cost_cap_microusd": request.approved_cost_cap_microusd,
        "cost_observation": cost_observation,
        "observed_cost_microusd": observed_cost_microusd,
        "recorded_at": NOW + timedelta(minutes=2),
    }
    payload.update(overrides)
    return SquidCodexGateResultReceipt.model_validate(
        bind_squid_codex_gate_receipt(payload)
    )


def _attempt_started(
    request: SquidCodexGateRequest | None = None,
) -> tuple[SquidCodexGateRunner, SquidCodexGateRequest, SquidCodexSemanticQaEvidence]:
    typed = request or _request()
    runner = SquidCodexGateRunner()
    runner.submit_request(typed, now=NOW)
    claimed = runner.claim(
        typed.work_key,
        reviewer_principal_id=typed.reviewer_principal_id,
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert claimed.run.claim_fence_sha256 is not None
    started = runner.start_attempt(
        typed.work_key,
        reviewer_principal_id=typed.reviewer_principal_id,
        claim_fence_sha256=claimed.run.claim_fence_sha256,
        now=NOW + timedelta(minutes=1),
    )
    assert started.execute_authorized is True
    assert started.run.attempt_fence_sha256 is not None
    return runner, typed, _evidence(typed, started.run.attempt_fence_sha256)


def test_work_key_is_stable_across_reviewer_rotation_and_result_independent() -> None:
    first = _request()
    rotated = _request(
        reviewer_config_sha256="f" * 64,
        reviewer_release_sha="d" * 40,
    )
    assert rotated.work_key == first.work_key
    assert rotated.assignment_key != first.assignment_key
    assert rotated.request_key != first.request_key

    _, request, evidence = _attempt_started(first)
    changed_evidence = _evidence(
        request,
        evidence.attempt_fence_sha256,
        verdict="needs_changes",
        findings=("unsupported_claim",),
        qa_output_sha256="f" * 64,
    )
    assert _receipt(request, evidence).work_key == request.work_key
    assert _receipt(request, changed_evidence).request_key == request.request_key

    reordered = SquidCodexGateRequest.model_validate(
        bind_squid_codex_gate_request(dict(reversed(tuple(
            first.canonical_input().items()
        ))))
    )
    assert reordered.work_key == first.work_key
    assert reordered.request_key == first.request_key


@pytest.mark.parametrize(
    "changed",
    [
        {"private_output_sha256": "8" * 64},
        {"official_content_version_id": _uuid(30)},
        {"official_source_item_id": _uuid(31)},
        {"official_source_binding_sha256": "9" * 64},
        {"content_snapshot_sha256": "a" * 64},
        {"signal_manifest_sha256": "d" * 64},
        {"signal_producer_principal_ids": tuple(sorted((
            _uuid(7), _uuid(8), _uuid(9), _uuid(11),
        )))},
    ],
)
def test_work_key_changes_for_each_logical_input(changed: dict[str, object]) -> None:
    assert _request(**changed).work_key != _request().work_key  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "reviewer_principal_id",
    [_uuid(4), _uuid(5), _uuid(7), _uuid(8), _uuid(9), _uuid(10)],
)
def test_reviewer_cannot_be_any_upstream_producer(
    reviewer_principal_id: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_self_review_forbidden",
    ):
        _request(reviewer_principal_id=reviewer_principal_id)


@pytest.mark.parametrize(
    "field",
    [
        "automatic_publication",
        "provider_calls",
        "external_calls",
        "publication_calls",
    ],
)
def test_request_rejects_every_side_effect(field: str) -> None:
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_side_effect_forbidden",
    ):
        _request(request_overrides={field: True})


def test_request_rejects_broken_plan_private_chain() -> None:
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_stage_lineage_invalid",
    ):
        _request(private_previous_receipt_sha256="f" * 64)
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_stage_lineage_invalid",
    ):
        _request(private_input_sha256="e" * 64)


def test_stage_receipts_must_precede_source_observation() -> None:
    request = _request()
    payload = request.canonical_input()
    private_payload = request.private_content_receipt.model_dump(
        mode="python",
        exclude={"receipt_sha256"},
    )
    private_payload["recorded_at"] = NOW + timedelta(minutes=1)
    payload["private_content_receipt"] = (
        PreviewHarmonyStageReceipt.model_validate(
            bind_preview_stage_receipt(private_payload)
        )
    )
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_binding_time_invalid",
    ):
        SquidCodexGateRequest.model_validate(
            bind_squid_codex_gate_request(payload)
        )


def test_private_receipt_cannot_predate_specialist_registration() -> None:
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_binding_time_invalid",
    ):
        _request(
            private_binding_created_at=NOW - timedelta(minutes=5),
            private_recorded_at=NOW - timedelta(minutes=10),
        )


def test_plan_receipt_cannot_predate_branch_fence() -> None:
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_binding_time_invalid",
    ):
        _request(
            branch_fence_created_at=NOW - timedelta(minutes=5),
            plan_recorded_at=NOW - timedelta(minutes=10),
            private_binding_created_at=NOW - timedelta(minutes=5),
            private_recorded_at=NOW - timedelta(minutes=4),
            reviewer_binding_created_at=NOW - timedelta(minutes=5),
        )


def test_source_lineage_is_hash_bound_current_and_expiring() -> None:
    source = _source_lineage()
    payload = source.model_dump(mode="python")
    payload["content_snapshot_sha256"] = "f" * 64
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_source_lineage_invalid",
    ):
        SquidCodexSourceLineageReceipt.model_validate(payload)
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_source_time_invalid",
    ):
        _source_lineage(source_signal_expires_at=NOW)
    payload = source.model_dump(mode="python", exclude={"lineage_sha256"})
    payload["upstream_receipt_sha256"] = "0" * 64
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_source_binding_invalid",
    ):
        SquidCodexSourceLineageReceipt.model_validate(
            bind_squid_codex_source_lineage(payload)
        )


def test_signal_manifest_producer_set_is_authoritative_and_complete() -> None:
    request = _request()
    payload = request.canonical_input()
    payload["signal_producer_principal_ids"] = tuple(sorted((
        request.source_lineage.source_producer_principal_id,
        UUID(_uuid(8)),
        UUID(_uuid(9)),
        UUID(_uuid(11)),
    ), key=str))
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_producer_set_invalid",
    ):
        SquidCodexGateRequest.model_validate(
            bind_squid_codex_gate_request(payload)
        )

    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_self_review_forbidden",
    ):
        _request(
            reviewer_principal_id=_uuid(11),
            signal_producer_principal_ids=tuple(sorted((
                _uuid(7), _uuid(8), _uuid(9), _uuid(11),
            ))),
        )


def test_signal_input_set_must_equal_plan_input() -> None:
    request = _request()
    payload = request.canonical_input()
    source_payload = request.source_lineage.model_dump(
        mode="python",
        exclude={"lineage_sha256"},
    )
    source_payload["signal_input_set_sha256"] = "f" * 64
    payload["source_lineage"] = SquidCodexSourceLineageReceipt.model_validate(
        bind_squid_codex_source_lineage(source_payload)
    )
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_stage_lineage_invalid",
    ):
        SquidCodexGateRequest.model_validate(
            bind_squid_codex_gate_request(payload)
        )


def test_request_rejects_stale_keys_and_raw_content() -> None:
    request = _request()
    payload = bind_squid_codex_gate_request(request.canonical_input())
    payload["work_key"] = "0" * 64
    with pytest.raises(ValidationError, match="squid_codex_gate_work_key_invalid"):
        SquidCodexGateRequest.model_validate(payload)
    payload = bind_squid_codex_gate_request(request.canonical_input())
    payload["raw_private_content"] = "forbidden"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SquidCodexGateRequest.model_validate(payload)


def test_happy_path_stops_at_operator_review_pending() -> None:
    runner, request, evidence = _attempt_started()
    result = runner.submit_result(
        request.work_key,
        _receipt(request, evidence),
        evidence,
        now=NOW + timedelta(minutes=2),
    )
    assert result.run.state == SquidCodexGateState.RESULT_SUBMITTED
    assert runner.verify_result(request.work_key).run.state == (
        SquidCodexGateState.VERIFIED
    )
    queued = runner.queue_operator_review(request.work_key)
    assert queued.run.state == SquidCodexGateState.OPERATOR_REVIEW_PENDING
    assert queued.run.result_receipt is not None
    assert queued.run.result_evidence is not None
    assert queued.run.result_receipt.observed_cost_microusd == 123
    assert queued.run.result_receipt.automatic_publication is False
    assert queued.run.result_evidence.raw_private_content_included is False
    assert runner.queue_operator_review(request.work_key).reused is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("work_key", "0" * 64),
        ("assignment_key", "0" * 64),
        ("request_key", "0" * 64),
        ("attempt_fence_sha256", "0" * 64),
        ("private_content_receipt_sha256", "0" * 64),
        ("private_content_output_sha256", "0" * 64),
        ("source_lineage_sha256", "0" * 64),
        ("official_content_version_id", _uuid(105)),
        ("official_source_item_id", _uuid(106)),
        ("official_source_binding_sha256", "0" * 64),
        ("content_snapshot_sha256", "0" * 64),
        ("reviewer_specialist_binding_sha256", "0" * 64),
        ("reviewer_release_sha", "0" * 40),
        ("reviewer_config_sha256", "0" * 64),
        ("approved_cost_cap_microusd", 999),
    ],
)
def test_result_receipt_binds_work_source_and_assignment(
    field: str,
    value: object,
) -> None:
    runner, request, evidence = _attempt_started()
    receipt = _receipt(request, evidence, **{field: value})
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_receipt_binding_invalid",
    ):
        runner.submit_result(
            request.work_key, receipt, evidence,
            now=NOW + timedelta(minutes=2),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewed_output_sha256", "0" * 64),
        ("official_content_version_id", _uuid(301)),
        ("official_source_item_id", _uuid(302)),
        ("official_source_binding_sha256", "0" * 64),
        ("content_snapshot_sha256", "0" * 64),
        ("source_lineage_sha256", "0" * 64),
        ("reviewer_specialist_binding_sha256", "0" * 64),
    ],
)
def test_typed_evidence_binds_input_source_and_reviewer(
    field: str,
    value: object,
) -> None:
    runner, request, evidence = _attempt_started()
    wrong = _evidence(
        request,
        evidence.attempt_fence_sha256,
        **{field: value},
    )
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_evidence_binding_invalid",
    ):
        runner.submit_result(
            request.work_key, _receipt(request, wrong), wrong,
            now=NOW + timedelta(minutes=2),
        )


def test_evidence_digest_verdict_findings_and_extra_fail_closed() -> None:
    _, request, evidence = _attempt_started()
    payload = evidence.model_dump(mode="python")
    payload["qa_output_sha256"] = "0" * 64
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_evidence_digest_invalid",
    ):
        SquidCodexSemanticQaEvidence.model_validate(payload)
    bad_criteria = evidence.criteria.model_dump()
    bad_criteria["factual_binding"] = False
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_evidence_verdict_invalid",
    ):
        _evidence(
            request,
            evidence.attempt_fence_sha256,
            criteria=bad_criteria,
        )
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_evidence_verdict_invalid",
    ):
        _evidence(
            request,
            evidence.attempt_fence_sha256,
            verdict="needs_changes",
        )
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_evidence_findings_invalid",
    ):
        _evidence(
            request,
            evidence.attempt_fence_sha256,
            verdict="needs_changes",
            findings=("unsupported_claim", "unsupported_claim"),
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _evidence(
            request,
            evidence.attempt_fence_sha256,
            raw_finding_text="forbidden",
        )


def test_result_and_evidence_verdict_mismatch_is_rejected() -> None:
    runner, request, evidence = _attempt_started()
    receipt = _receipt(request, evidence, verdict="needs_changes")
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_result_evidence_invalid",
    ):
        runner.submit_result(
            request.work_key, receipt, evidence,
            now=NOW + timedelta(minutes=2),
        )


@pytest.mark.parametrize(
    "field",
    [
        "automatic_publication",
        "provider_calls",
        "external_calls",
        "publication_calls",
    ],
)
def test_result_and_evidence_reject_every_side_effect(field: str) -> None:
    _, request, evidence = _attempt_started()
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_side_effect_forbidden",
    ):
        _receipt(request, evidence, **{field: True})
    with pytest.raises(ValidationError):
        _evidence(
            request,
            evidence.attempt_fence_sha256,
            **{field: True},
        )


def test_result_receipt_digest_is_fail_closed() -> None:
    _, request, evidence = _attempt_started()
    payload = _receipt(request, evidence).model_dump(mode="python")
    payload["qa_output_sha256"] = "0" * 64
    with pytest.raises(
        ValidationError,
        match="squid_codex_gate_receipt_digest_invalid",
    ):
        SquidCodexGateResultReceipt.model_validate(payload)


@pytest.mark.parametrize(
    ("observation", "cost", "error"),
    [
        ("observed", None, "squid_codex_gate_cost_observation_invalid"),
        ("unobserved", 1, "squid_codex_gate_cost_observation_invalid"),
        ("observed", 1_001, "squid_codex_gate_cost_cap_exceeded"),
    ],
)
def test_result_cost_observation_and_cap_are_fail_closed(
    observation: str,
    cost: int | None,
    error: str,
) -> None:
    _, request, evidence = _attempt_started()
    with pytest.raises(ValidationError, match=error):
        _receipt(
            request,
            evidence,
            cost_observation=observation,
            observed_cost_microusd=cost,
        )


def test_unobserved_cost_remains_unobserved() -> None:
    runner, request, evidence = _attempt_started()
    runner.submit_result(
        request.work_key,
        _receipt(
            request,
            evidence,
            cost_observation="unobserved",
            observed_cost_microusd=None,
        ),
        evidence,
        now=NOW + timedelta(minutes=2),
    )
    runner.verify_result(request.work_key)
    queued = runner.queue_operator_review(request.work_key)
    assert queued.run.result_receipt is not None
    assert queued.run.result_receipt.cost_observation == (
        SquidCodexGateCostObservation.UNOBSERVED
    )
    assert queued.run.result_receipt.observed_cost_microusd is None


@pytest.mark.parametrize(
    ("verdict", "finding", "expected_state", "expected_reason"),
    [
        (
            "needs_changes", "unsupported_claim",
            SquidCodexGateState.NEEDS_CHANGES,
            SquidCodexGateTerminalReason.RESULT_NEEDS_CHANGES,
        ),
        (
            "blocked", "review_execution_blocked",
            SquidCodexGateState.BLOCKED,
            SquidCodexGateTerminalReason.RESULT_BLOCKED,
        ),
    ],
)
def test_non_pass_never_reaches_operator_inbox(
    verdict: str,
    finding: str,
    expected_state: SquidCodexGateState,
    expected_reason: SquidCodexGateTerminalReason,
) -> None:
    runner, request, initial = _attempt_started()
    evidence = _evidence(
        request,
        initial.attempt_fence_sha256,
        verdict=verdict,
        findings=(finding,),
    )
    runner.submit_result(
        request.work_key, _receipt(request, evidence), evidence,
        now=NOW + timedelta(minutes=2),
    )
    terminal = runner.verify_result(request.work_key)
    assert terminal.run.state == expected_state
    assert terminal.run.terminal_reason == expected_reason
    with pytest.raises(SquidCodexGateError, match="squid_codex_gate_state_invalid"):
        runner.queue_operator_review(request.work_key)


def test_lease_is_bounded_and_request_expiry_is_fail_closed() -> None:
    request = _request()
    runner = SquidCodexGateRunner()
    runner.submit_request(request, now=NOW)
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_lease_time_invalid",
    ):
        runner.claim(
            request.work_key,
            reviewer_principal_id=request.reviewer_principal_id,
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=16),
        )
    stale = _request(source_signal_expires_at=NOW + timedelta(minutes=1))
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_request_expired",
    ):
        SquidCodexGateRunner().submit_request(
            stale,
            now=NOW + timedelta(minutes=1),
        )


def test_source_submit_claim_and_attempt_times_never_move_backward() -> None:
    request = _request()
    runner = SquidCodexGateRunner()
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_request_not_yet_valid",
    ):
        runner.submit_request(request, now=NOW - timedelta(minutes=1))

    submitted = runner.submit_request(request, now=NOW)
    assert submitted.run.submitted_at == NOW
    assert submitted.run.last_transition_at == NOW
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_transition_time_invalid",
    ):
        runner.claim(
            request.work_key,
            reviewer_principal_id=request.reviewer_principal_id,
            now=NOW - timedelta(minutes=1),
            lease_expires_at=NOW + timedelta(minutes=4),
        )

    claimed = runner.claim(
        request.work_key,
        reviewer_principal_id=request.reviewer_principal_id,
        now=NOW + timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert claimed.run.claimed_at == NOW + timedelta(minutes=1)
    assert claimed.run.claim_fence_sha256 is not None
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_transition_time_invalid",
    ):
        runner.start_attempt(
            request.work_key,
            reviewer_principal_id=request.reviewer_principal_id,
            claim_fence_sha256=claimed.run.claim_fence_sha256,
            now=NOW,
        )


def test_result_uses_trusted_submit_time_and_rejects_backdating() -> None:
    runner, request, evidence = _attempt_started()
    receipt = _receipt(request, evidence)
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_result_time_invalid",
    ):
        runner.submit_result(
            request.work_key,
            receipt,
            evidence,
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_result_time_invalid",
    ):
        runner.submit_result(
            request.work_key,
            receipt,
            evidence,
            now=NOW + timedelta(minutes=5),
        )
    assert runner.get(request.work_key).state == (
        SquidCodexGateState.ATTEMPT_STARTED
    )


def test_result_and_reconcile_at_lease_boundary_fail_closed() -> None:
    runner, request, evidence = _attempt_started()
    receipt = _receipt(request, evidence)
    barrier = Barrier(2)

    def submit() -> str:
        barrier.wait()
        try:
            runner.submit_result(
                request.work_key,
                receipt,
                evidence,
                now=NOW + timedelta(minutes=5),
            )
            return "submitted"
        except SquidCodexGateError as exc:
            return exc.code

    def reconcile() -> str:
        barrier.wait()
        runner.reconcile_expired_lease(
            request.work_key,
            now=NOW + timedelta(minutes=5),
        )
        return "reconciled"

    with ThreadPoolExecutor(max_workers=2) as pool:
        submit_future = pool.submit(submit)
        reconcile_future = pool.submit(reconcile)
        outcomes = (submit_future.result(), reconcile_future.result())
    assert "reconciled" in outcomes
    assert set(outcomes) <= {
        "reconciled",
        "squid_codex_gate_result_time_invalid",
        "squid_codex_gate_automatic_retry_forbidden",
    }
    assert runner.get(request.work_key).state == (
        SquidCodexGateState.OUTCOME_UNKNOWN
    )


def test_stale_claim_fence_cannot_start_after_reclaim() -> None:
    request = _request()
    runner = SquidCodexGateRunner()
    runner.submit_request(request, now=NOW)
    first = runner.claim(
        request.work_key,
        reviewer_principal_id=request.reviewer_principal_id,
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    old_fence = first.run.claim_fence_sha256
    assert old_fence is not None
    runner.reconcile_expired_lease(
        request.work_key,
        now=NOW + timedelta(minutes=1),
    )
    second = runner.claim(
        request.work_key,
        reviewer_principal_id=request.reviewer_principal_id,
        now=NOW + timedelta(minutes=2),
        lease_expires_at=NOW + timedelta(minutes=3),
    )
    assert second.run.claim_fence_sha256 != old_fence
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_claim_fence_invalid",
    ):
        runner.start_attempt(
            request.work_key,
            reviewer_principal_id=request.reviewer_principal_id,
            claim_fence_sha256=old_fence,
            now=NOW + timedelta(minutes=2),
        )


def test_only_three_pre_attempt_expiries_are_reclaimable() -> None:
    request = _request()
    runner = SquidCodexGateRunner()
    runner.submit_request(request, now=NOW)
    for attempt in range(1, 4):
        claimed_at = NOW + timedelta(minutes=attempt * 10)
        claimed = runner.claim(
            request.work_key,
            reviewer_principal_id=request.reviewer_principal_id,
            now=claimed_at,
            lease_expires_at=claimed_at + timedelta(minutes=1),
        )
        assert claimed.run.claim_attempts == attempt
        reconciled = runner.reconcile_expired_lease(
            request.work_key,
            now=claimed_at + timedelta(minutes=1),
        )
        if attempt < 3:
            assert reconciled.run.state == SquidCodexGateState.PENDING
        else:
            assert reconciled.run.state == SquidCodexGateState.BLOCKED
            assert reconciled.run.terminal_reason == (
                SquidCodexGateTerminalReason.CLAIM_LIMIT_EXHAUSTED
            )


def test_post_attempt_missing_receipt_is_unknown_and_never_retried() -> None:
    runner, request, evidence = _attempt_started()
    unknown = runner.reconcile_expired_lease(
        request.work_key,
        now=NOW + timedelta(minutes=5),
    )
    assert unknown.run.state == SquidCodexGateState.OUTCOME_UNKNOWN
    assert unknown.run.terminal_reason == (
        SquidCodexGateTerminalReason.RESULT_RECEIPT_MISSING
    )
    assert runner.reconcile_expired_lease(
        request.work_key,
        now=NOW + timedelta(minutes=6),
    ).reused is True
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_automatic_retry_forbidden",
    ):
        runner.claim(
            request.work_key,
            reviewer_principal_id=request.reviewer_principal_id,
            now=NOW + timedelta(minutes=6),
            lease_expires_at=NOW + timedelta(minutes=7),
        )
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_automatic_retry_forbidden",
    ):
        runner.submit_result(
            request.work_key,
            _receipt(request, evidence),
            evidence,
            now=NOW + timedelta(minutes=6),
        )


def test_identical_result_replay_reuses_and_different_payload_conflicts() -> None:
    runner, request, evidence = _attempt_started()
    receipt = _receipt(request, evidence)
    assert runner.submit_result(
        request.work_key, receipt, evidence,
        now=NOW + timedelta(minutes=2),
    ).reused is False
    assert runner.submit_result(
        request.work_key, receipt, evidence,
        now=NOW + timedelta(minutes=3),
    ).reused is True
    conflict_evidence = _evidence(
        request,
        evidence.attempt_fence_sha256,
        qa_output_sha256="f" * 64,
    )
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_result_conflict",
    ):
        runner.submit_result(
            request.work_key,
            _receipt(request, conflict_evidence, receipt_id=_uuid(201)),
            conflict_evidence,
            now=NOW + timedelta(minutes=3),
        )


def test_sixty_four_identical_requests_converge_to_one_work() -> None:
    request = _request()
    runner = SquidCodexGateRunner()
    barrier = Barrier(64)

    def submit(_: int):
        barrier.wait()
        return runner.submit_request(request, now=NOW)

    with ThreadPoolExecutor(max_workers=64) as pool:
        transitions = tuple(pool.map(
            submit,
            range(64),
        ))
    assert {item.run.work_key for item in transitions} == {request.work_key}
    assert sum(item.reused for item in transitions) == 63
    assert runner.get(request.work_key).state == SquidCodexGateState.PENDING


def test_sixty_four_rotated_assignments_create_one_work_and_conflict() -> None:
    requests = tuple(
        _request(reviewer_config_sha256=f"{index:064x}")
        for index in range(1, 65)
    )
    assert len({item.work_key for item in requests}) == 1
    runner = SquidCodexGateRunner()
    barrier = Barrier(64)

    def submit(request: SquidCodexGateRequest) -> str:
        barrier.wait()
        try:
            transition = runner.submit_request(request, now=NOW)
            return "created" if not transition.reused else "reused"
        except SquidCodexGateError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=64) as pool:
        outcomes = tuple(pool.map(submit, requests))
    assert outcomes.count("created") == 1
    assert outcomes.count("squid_codex_gate_assignment_conflict") == 63


def test_same_scope_with_changed_source_is_a_work_conflict() -> None:
    runner = SquidCodexGateRunner()
    first = _request()
    changed = _request(official_content_version_id=_uuid(999))
    runner.submit_request(first, now=NOW)
    with pytest.raises(
        SquidCodexGateError,
        match="squid_codex_gate_work_conflict",
    ):
        runner.submit_request(changed, now=NOW)


def test_sixty_four_starts_authorize_exactly_one_execution() -> None:
    request = _request()
    runner = SquidCodexGateRunner()
    runner.submit_request(request, now=NOW)
    claimed = runner.claim(
        request.work_key,
        reviewer_principal_id=request.reviewer_principal_id,
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    fence = claimed.run.claim_fence_sha256
    assert fence is not None
    barrier = Barrier(64)

    def start(_: int):
        barrier.wait()
        return runner.start_attempt(
            request.work_key,
            reviewer_principal_id=request.reviewer_principal_id,
            claim_fence_sha256=fence,
            now=NOW + timedelta(minutes=1),
        )

    with ThreadPoolExecutor(max_workers=64) as pool:
        transitions = tuple(pool.map(start, range(64)))
    assert sum(item.execute_authorized for item in transitions) == 1
    assert sum(item.reused for item in transitions) == 63
    assert all(
        item.execute_authorized is False
        for item in transitions
        if item.reused
    )


def test_sixty_four_identical_results_converge_to_one_receipt() -> None:
    runner, request, evidence = _attempt_started()
    receipt = _receipt(request, evidence)
    barrier = Barrier(64)

    def submit(_: int):
        barrier.wait()
        return runner.submit_result(
            request.work_key,
            receipt,
            evidence,
            now=NOW + timedelta(minutes=2),
        )

    with ThreadPoolExecutor(max_workers=64) as pool:
        transitions = tuple(pool.map(submit, range(64)))
    assert sum(item.reused for item in transitions) == 63
    assert all(
        item.run.state == SquidCodexGateState.RESULT_SUBMITTED
        for item in transitions
    )
