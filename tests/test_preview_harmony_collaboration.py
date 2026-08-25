from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.agent_control.harmony import (
    FrozenHarmonyAttestationRegistry,
    HarmonyInput,
    HarmonyLane,
    bind_harmony_signal_payload,
    build_harmony_snapshot,
    load_harmony_client_profiles,
)
from core.agent_control.preview_collaboration import (
    PreviewHarmonyCollaborationRound,
    PreviewHarmonyConnectorAttestationReceipt,
    PreviewHarmonyOperatorInboxItem,
    PreviewHarmonyRoundSignal,
    PreviewHarmonyStage,
    PreviewHarmonyStageReceipt,
    bind_preview_collaboration_round,
    bind_preview_connector_receipt,
    bind_preview_stage_receipt,
    preview_stage_operation_key_sha256,
    validate_squid_preview_signal_set,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
WORKSPACE_ID = "00000000-0000-4000-8000-000000000001"


def _uuid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _sha(value: object) -> str:
    def normalize(item: object) -> object:
        if hasattr(item, "model_dump"):
            return normalize(item.model_dump(mode="python"))  # type: ignore[union-attr]
        if isinstance(item, datetime):
            return item.isoformat(timespec="seconds").replace("+00:00", "Z")
        if hasattr(item, "value"):
            return normalize(item.value)  # type: ignore[union-attr]
        if isinstance(item, dict):
            return {str(key): normalize(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(value) for value in item]
        return str(item) if item.__class__.__name__ == "UUID" else item

    encoded = json.dumps(
        normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _signals():
    common = {
        "schema_version": "agent-harmony-signal@1",
        "workspace_id": WORKSPACE_ID,
        "client_id": "squid",
        "producer_release_sha": "a" * 40,
        "config_sha256": "b" * 64,
        "upstream_receipt_sha256": "c" * 64,
        "observed_at": NOW - timedelta(minutes=20),
        "expires_at": NOW + timedelta(hours=2),
        "evidence_sha256": "d" * 64,
        "topic_codes": ("staking_basics",),
        "raw_messages_included": False,
        "personal_data_included": False,
        "instructions_allowed": False,
        "advisory_only": True,
        "max_cost_microusd": 0,
        "max_external_actions": 0,
        "automatic_publication": False,
    }
    rows = []
    for index, kind in enumerate((
        "quiz_learning", "community_demand", "official_source", "recap_metric"
    ), start=1):
        payload = {
            **common,
            "signal_id": _uuid(index),
            "source_event_id": _uuid(100 + index),
            "producer_principal_id": _uuid(200 + index),
        }
        if kind == "quiz_learning":
            payload.update({
                "signal_kind": kind, "lane": "quiz_bot",
                "data_classification": "aggregate_anonymous",
                "content_factual_authority": False,
                "attempts": 40, "participants": 10,
                "accuracy_basis_points": 4000,
                "tutorial_priority_basis_points": 8000,
            })
        elif kind == "community_demand":
            payload.update({
                "signal_kind": kind, "lane": "community_ops",
                "data_classification": "aggregate_anonymous",
                "content_factual_authority": False,
                "room_mapping_count": 1, "sample_size": 20,
                "demand_score_basis_points": 7000,
            })
        elif kind == "official_source":
            payload.update({
                "signal_kind": kind, "lane": "content_source",
                "data_classification": "public_official",
                "content_factual_authority": True,
                "source_item_id": _uuid(300 + index),
                "source_body_sha256": "e" * 64,
                "source_kind": "x_post_text", "source_verified": True,
                "eligible_content_kinds": ("daily_news",),
            })
        else:
            payload.update({
                "signal_kind": kind, "lane": "recap",
                "data_classification": "aggregate_anonymous",
                "content_factual_authority": False,
                "period_start": NOW - timedelta(days=7),
                "period_end": NOW - timedelta(hours=1),
                "metrics": ({
                    "metric_code": "content_clicks", "unit": "count",
                    "observed": True, "value": 12,
                },),
            })
        rows.append(bind_harmony_signal_payload(payload))
    return HarmonyInput.model_validate({
        "schema_version": "agent-harmony-input@1",
        "workspace_id": WORKSPACE_ID,
        "signals": rows,
    }).signals


def _receipt(signal, index: int, *, verified_at=NOW - timedelta(minutes=10),
             expires_at=NOW + timedelta(hours=1)):
    capability = {
        "quiz_bot": "harmony_submit_quiz_bot",
        "community_ops": "harmony_submit_community_ops",
        "content_source": "harmony_submit_content_source",
        "recap": "harmony_submit_recap",
    }[signal.lane.value]
    return PreviewHarmonyConnectorAttestationReceipt.model_validate(
        bind_preview_connector_receipt({
            "receipt_id": _uuid(1000 + index),
            "workspace_id": signal.workspace_id,
            "client_id": signal.client_id,
            "signal_id": signal.signal_id,
            "source_event_id": signal.source_event_id,
            "connector_id": f"squid_{signal.lane.value}",
            "producer_principal_id": signal.producer_principal_id,
            "producer_release_sha": signal.producer_release_sha,
            "config_sha256": signal.config_sha256,
            "signal_kind": signal.signal_kind,
            "lane": signal.lane,
            "capability": capability,
            "issuer": "supabase",
            "audience": "authenticated",
            "verification_reference_sha256": "f" * 64,
            "signal_payload_sha256": signal.payload_sha256,
            "upstream_receipt_sha256": signal.upstream_receipt_sha256,
            "evidence_sha256": signal.evidence_sha256,
            "verified_at": verified_at,
            "expires_at": expires_at,
        })
    )


def test_database_connector_receipts_build_real_harmony_handoff() -> None:
    signals = _signals()
    receipts = tuple(_receipt(signal, index) for index, signal in enumerate(signals))
    attestations = validate_squid_preview_signal_set(
        signals, receipts, observed_at=NOW
    )
    snapshot = build_harmony_snapshot(
        HarmonyInput(
            schema_version="agent-harmony-input@1",
            workspace_id=WORKSPACE_ID,
            signals=signals,
        ),
        load_harmony_client_profiles(ROOT / "clients"),
        observed_at=NOW,
        attestation_registry=FrozenHarmonyAttestationRegistry(attestations),
    )
    squid = next(item for item in snapshot.rounds if item.client_id == "squid")
    assert squid.handoff is not None
    assert squid.handoff.dispatchable is False
    assert squid.handoff.automatic_publication is False
    assert len(squid.handoff.signal_manifest) == 4


@pytest.mark.parametrize("mode", ["future", "expired"])
def test_connector_receipt_must_be_current_at_validation_time(mode: str) -> None:
    signals = _signals()
    receipts = [_receipt(signal, index) for index, signal in enumerate(signals)]
    if mode == "future":
        receipts[0] = _receipt(
            signals[0], 0,
            verified_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(hours=1),
        )
    else:
        receipts[0] = _receipt(
            signals[0], 0,
            verified_at=NOW - timedelta(hours=2),
            expires_at=NOW - timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="harmony_preview_connector_time_invalid"):
        validate_squid_preview_signal_set(signals, receipts, observed_at=NOW)


@pytest.mark.parametrize(
    "topic_code", ["transfer_funds", "delete_account", "unknown_topic"]
)
def test_signal_topic_taxonomy_is_closed(topic_code: str) -> None:
    payload = _signals()[0].model_dump(mode="python")
    payload.pop("payload_sha256")
    payload["topic_codes"] = (topic_code,)
    with pytest.raises(ValidationError):
        HarmonyInput.model_validate({
            "schema_version": "agent-harmony-input@1",
            "workspace_id": WORKSPACE_ID,
            "signals": [bind_harmony_signal_payload(payload)],
        })


def _round(
    *,
    qa_principal: str = _uuid(5003),
    recap_principal: str = _uuid(5005),
    inbox_qa_output: str | None = None,
):
    signals = _signals()
    receipts = tuple(_receipt(signal, index) for index, signal in enumerate(signals))
    sorted_receipts = tuple(sorted(receipts, key=lambda item: item.lane.value))
    manifest = tuple(sorted((
        PreviewHarmonyRoundSignal(
            signal_id=signal.signal_id,
            signal_kind=signal.signal_kind,
            lane=signal.lane,
            signal_payload_sha256=signal.payload_sha256,
            connector_receipt_id=receipt.receipt_id,
            connector_receipt_sha256=receipt.payload_sha256,
            upstream_receipt_sha256=signal.upstream_receipt_sha256,
            official_content_version_id=(
                signal.source_event_id if signal.lane == HarmonyLane.CONTENT_SOURCE else None
            ),
            official_source_binding_sha256=(
                signal.upstream_receipt_sha256
                if signal.lane == HarmonyLane.CONTENT_SOURCE else None
            ),
            content_factual_authority=signal.lane == HarmonyLane.CONTENT_SOURCE,
        ) for signal, receipt in zip(signals, receipts)
    ), key=lambda item: item.lane.value))
    input_sha = _sha([item.model_dump(mode="python") for item in manifest])
    round_id, plan_id = _uuid(4001), _uuid(4002)
    mapping = (
        (
            PreviewHarmonyStage.PLAN, "grok_bot", "harmony_plan",
            "squid_planner", _uuid(5001),
        ),
        (PreviewHarmonyStage.PRIVATE_CONTENT, "content_engine",
            "harmony_prepare_private_content",
            "squid_private_content_producer", _uuid(5002)),
        (PreviewHarmonyStage.INDEPENDENT_QA, "codex",
            "harmony_independent_qa", "squid_independent_qa", qa_principal),
        (PreviewHarmonyStage.OPERATOR_INBOX, "human_operator_inbox",
            "harmony_operator_inbox", "coineasy_representative_inbox",
            _uuid(5004)),
        (PreviewHarmonyStage.RECAP, "coineasy_recap", "harmony_recap",
            "squid_recap", recap_principal),
    )
    stage_receipts = []
    previous_receipt, previous_output = None, input_sha
    for ordinal, (
        stage, actor, capability, specialist_code, principal,
    ) in enumerate(mapping, start=1):
        output = _sha({"stage": stage.value, "input": previous_output})
        specialist_binding = _sha({
            "stage": stage.value,
            "principal_id": principal,
            "specialist_code": specialist_code,
        })
        payload = bind_preview_stage_receipt({
            "receipt_id": _uuid(6000 + ordinal),
            "workspace_id": WORKSPACE_ID, "client_id": "squid",
            "round_id": round_id, "plan_id": plan_id,
            "stage": stage, "ordinal": ordinal, "actor": actor,
            "principal_id": principal, "producer_release_sha": "1" * 40,
            "config_sha256": "2" * 64, "capability": capability,
            "specialist_code": specialist_code,
            "specialist_binding_sha256": specialist_binding,
            "operation_key_sha256": preview_stage_operation_key_sha256(
                specialist_binding_sha256=specialist_binding,
                workspace_id=WORKSPACE_ID,
                client_id="squid",
                plan_id=plan_id,
                stage=stage,
                input_sha256=previous_output,
                output_sha256=output,
            ),
            "binding_receipt_sha256": "3" * 64,
            "verdict": "passed" if stage == PreviewHarmonyStage.INDEPENDENT_QA else None,
            "reviewer_principal_id": (
                principal if stage == PreviewHarmonyStage.INDEPENDENT_QA else None
            ),
            "previous_receipt_sha256": previous_receipt,
            "input_sha256": previous_output, "output_sha256": output,
            "recorded_at": NOW,
        })
        receipt = PreviewHarmonyStageReceipt.model_validate(payload)
        stage_receipts.append(receipt)
        previous_receipt, previous_output = receipt.receipt_sha256, output
    qa = stage_receipts[2]
    operator = stage_receipts[3]
    inbox = PreviewHarmonyOperatorInboxItem(
        inbox_id=_uuid(7001), workspace_id=WORKSPACE_ID, client_id="squid",
        round_id=round_id, plan_id=plan_id, stage_receipt_id=operator.receipt_id,
        scope_sha256=operator.output_sha256, qa_receipt_id=qa.receipt_id,
        qa_receipt_sha256=qa.receipt_sha256,
        qa_output_sha256=inbox_qa_output or qa.output_sha256,
        created_at=NOW,
    )
    return bind_preview_collaboration_round({
        "workspace_id": WORKSPACE_ID, "client_id": "squid",
        "round_id": round_id, "plan_id": plan_id,
        "input_set_sha256": input_sha, "signal_manifest": manifest,
        "connector_receipts": sorted_receipts,
        "stage_receipts": stage_receipts, "operator_inbox": inbox,
    })


def test_typed_round_binds_every_stage_and_exact_qa_inbox() -> None:
    result = PreviewHarmonyCollaborationRound.model_validate(_round())
    assert result.status == "operator_review_pending"
    assert result.stage_receipts[2].verdict == "passed"
    assert result.publication_calls is False


def test_typed_round_is_representative_visible_before_recap() -> None:
    payload = _round()
    payload["stage_receipts"] = payload["stage_receipts"][:4]
    payload["stage_receipt_count"] = 4
    result = PreviewHarmonyCollaborationRound.model_validate(
        bind_preview_collaboration_round(payload)
    )
    assert len(result.stage_receipts) == 4
    assert result.stage_receipt_count == 4


def test_typed_round_rejects_stage_count_mismatch() -> None:
    payload = _round()
    payload["stage_receipt_count"] = 4
    with pytest.raises(ValidationError, match="harmony_preview_stage_order_invalid"):
        PreviewHarmonyCollaborationRound.model_validate(
            bind_preview_collaboration_round(payload)
        )


def test_typed_round_rejects_qa_self_review() -> None:
    with pytest.raises(ValidationError, match="harmony_preview_qa_separation_invalid"):
        PreviewHarmonyCollaborationRound.model_validate(_round(qa_principal=_uuid(5001)))


def test_typed_round_rejects_any_reused_specialist_principal() -> None:
    with pytest.raises(
        ValidationError,
        match="harmony_preview_specialist_separation_invalid",
    ):
        PreviewHarmonyCollaborationRound.model_validate(
            _round(recap_principal=_uuid(5001))
        )


def test_typed_round_rejects_inbox_qa_output_tamper() -> None:
    with pytest.raises(
        ValidationError, match="harmony_preview_operator_inbox_binding_invalid"
    ):
        PreviewHarmonyCollaborationRound.model_validate(
            _round(inbox_qa_output="9" * 64)
        )


def test_typed_round_rejects_stage_input_tamper_even_with_new_digests() -> None:
    payload = _round()
    stages = list(payload["stage_receipts"])
    stage = stages[1].model_dump(mode="python")
    stage["input_sha256"] = "8" * 64
    stage["operation_key_sha256"] = preview_stage_operation_key_sha256(
        specialist_binding_sha256=stage["specialist_binding_sha256"],
        workspace_id=stage["workspace_id"],
        client_id=stage["client_id"],
        plan_id=stage["plan_id"],
        stage=stage["stage"],
        input_sha256=stage["input_sha256"],
        output_sha256=stage["output_sha256"],
    )
    stages[1] = PreviewHarmonyStageReceipt.model_validate(
        bind_preview_stage_receipt(stage)
    )
    payload["stage_receipts"] = stages
    payload = bind_preview_collaboration_round(payload)
    with pytest.raises(ValidationError, match="harmony_preview_stage_binding_invalid"):
        PreviewHarmonyCollaborationRound.model_validate(payload)
