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
    PreviewHarmonyConnectorRegistration,
    PreviewHarmonyConnectorRequestReceipt,
    PreviewHarmonyTrustSnapshotCandidate,
    PreviewHarmonyIndependentQaEvidence,
    PreviewHarmonyOperatorInboxItem,
    PreviewHarmonyQaDenialReceipt,
    PreviewHarmonyRoundSignal,
    PreviewHarmonyStage,
    PreviewHarmonyStageReceipt,
    bind_preview_collaboration_round,
    bind_preview_connector_registration,
    bind_preview_connector_request_receipt,
    bind_preview_connector_receipt,
    bind_preview_harmony_trust_snapshot_candidate,
    bind_preview_qa_denial_receipt,
    bind_preview_stage_receipt,
    preview_connector_request_sha256,
    preview_qa_evidence_sha256,
    preview_stage_operation_key_sha256,
    validate_preview_harmony_trust_snapshot_candidate,
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


def _receipt(
    signal,
    index: int,
    *,
    receipt_id: str | None = None,
    verified_at=NOW - timedelta(minutes=10),
    expires_at=NOW + timedelta(hours=1),
):
    capability = {
        "quiz_bot": "harmony_submit_quiz_bot",
        "community_ops": "harmony_submit_community_ops",
        "content_source": "harmony_submit_content_source",
        "recap": "harmony_submit_recap",
    }[signal.lane.value]
    return PreviewHarmonyConnectorAttestationReceipt.model_validate(
        bind_preview_connector_receipt({
            "receipt_id": receipt_id or _uuid(1000 + index),
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


def _registration(
    signal,
    receipt,
    index: int = 0,
    *,
    created_at: datetime | None = None,
):
    return PreviewHarmonyConnectorRegistration.model_validate(
        bind_preview_connector_registration({
            "branch_ref": "a" * 20,
            "workspace_id": signal.workspace_id,
            "client_id": signal.client_id,
            "registration_id": _uuid(8000 + index),
            "lane": signal.lane,
            "capability": receipt.capability,
            "connector_id": receipt.connector_id,
            "producer_principal_id": signal.producer_principal_id,
            "producer_release_sha": signal.producer_release_sha,
            "config_sha256": signal.config_sha256,
            "attestation_key_id": f"squid_preview_key_{index}",
            "expires_at": receipt.expires_at,
            "created_at": (
                created_at
                if created_at is not None
                else receipt.verified_at - timedelta(minutes=5)
            ),
        })
    )


def _request_receipt(signal, connector_receipt, registration, index: int = 0):
    return PreviewHarmonyConnectorRequestReceipt.model_validate(
        bind_preview_connector_request_receipt({
            "request_receipt_id": _uuid(8100 + index),
            "workspace_id": signal.workspace_id,
            "client_id": signal.client_id,
            "registration_id": registration.registration_id,
            "registration_sha256": registration.registration_sha256,
            "attestation_key_id": registration.attestation_key_id,
            "request_nonce": _uuid(8200 + index),
            "request_sha256": preview_connector_request_sha256(
                workspace_id=signal.workspace_id,
                client_id=signal.client_id,
                registration_id=registration.registration_id,
                connector_receipt_id=connector_receipt.receipt_id,
                target_signal=signal,
            ),
            "token_claims_sha256": (
                connector_receipt.verification_reference_sha256
            ),
            "signal_id": signal.signal_id,
            "signal_payload_sha256": signal.payload_sha256,
            "connector_receipt_id": connector_receipt.receipt_id,
            "connector_receipt_sha256": connector_receipt.payload_sha256,
            "accepted_at": connector_receipt.verified_at,
            "expires_at": connector_receipt.expires_at,
        })
    )


def test_connector_registration_and_request_bind_exact_signed_identity() -> None:
    signal = _signals()[0]
    connector_receipt = _receipt(signal, 0)
    registration = _registration(signal, connector_receipt)
    request_receipt = _request_receipt(signal, connector_receipt, registration)

    request_receipt.assert_nonce_identity(str(request_receipt.request_nonce))
    request_receipt.bind_connector_receipt(
        registration,
        connector_receipt,
        signal,
    )
    assert request_receipt.raw_content_included is False
    assert request_receipt.external_calls is False
    assert request_receipt.provider_calls is False
    assert request_receipt.publication_calls is False
    assert request_receipt.automatic_publication is False


def test_connector_registration_rejects_fractional_creation_time() -> None:
    signal = _signals()[0]
    connector_receipt = _receipt(signal, 0)
    with pytest.raises(
        ValidationError,
        match="harmony_connector_registration_time_invalid",
    ):
        _registration(
            signal,
            connector_receipt,
            created_at=(
                connector_receipt.verified_at + timedelta(microseconds=500_000)
            ),
        )


def test_connector_request_rejects_registration_from_later_database_second() -> None:
    signal = _signals()[0]
    connector_receipt = _receipt(signal, 0)
    registration = _registration(
        signal,
        connector_receipt,
        created_at=connector_receipt.verified_at + timedelta(seconds=1),
    )
    request_receipt = _request_receipt(signal, connector_receipt, registration)

    with pytest.raises(
        ValueError,
        match="harmony_connector_request_registration_invalid",
    ):
        request_receipt.bind_registration(registration)


def test_connector_registration_rejects_digest_tamper() -> None:
    signal = _signals()[0]
    connector_receipt = _receipt(signal, 0)
    payload = _registration(signal, connector_receipt).model_dump(mode="python")
    payload["registration_sha256"] = "0" * 64
    with pytest.raises(
        ValidationError,
        match="harmony_connector_registration_digest_invalid",
    ):
        PreviewHarmonyConnectorRegistration.model_validate(payload)


def test_connector_registration_digest_uses_exact_sql_time_shape() -> None:
    signal = _signals()[0]
    connector_receipt = _receipt(signal, 0)
    registration = _registration(signal, connector_receipt)
    assert registration.registration_sha256 == _sha({
        "attestation_key_id": registration.attestation_key_id,
        "branch_ref": registration.branch_ref,
        "capability": registration.capability,
        "client_id": registration.client_id,
        "config_sha256": registration.config_sha256,
        "connector_id": registration.connector_id,
        "expires_at": registration.expires_at.strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        "lane": registration.lane,
        "producer_principal_id": registration.producer_principal_id,
        "producer_release_sha": registration.producer_release_sha,
        "registration_id": registration.registration_id,
        "schema_version": "harmony-connector-registration@1",
        "workspace_id": registration.workspace_id,
    })
    with pytest.raises(ValidationError, match="frozen_instance"):
        setattr(registration, "connector_id", "different_connector")


def test_connector_request_rejects_receipt_digest_tamper() -> None:
    signal = _signals()[0]
    connector_receipt = _receipt(signal, 0)
    registration = _registration(signal, connector_receipt)
    payload = _request_receipt(
        signal,
        connector_receipt,
        registration,
    ).model_dump(mode="python")
    payload["token_claims_sha256"] = "0" * 64
    with pytest.raises(
        ValidationError,
        match="harmony_connector_request_receipt_digest_invalid",
    ):
        PreviewHarmonyConnectorRequestReceipt.model_validate(payload)


def test_connector_request_nonce_must_equal_verified_jti() -> None:
    signal = _signals()[0]
    connector_receipt = _receipt(signal, 0)
    registration = _registration(signal, connector_receipt)
    request_receipt = _request_receipt(signal, connector_receipt, registration)

    with pytest.raises(ValueError, match="harmony_connector_request_nonce_invalid"):
        request_receipt.assert_nonce_identity(_signals()[1].signal_id)


def test_connector_request_rejects_signed_request_digest_drift() -> None:
    signal = _signals()[0]
    connector_receipt = _receipt(signal, 0)
    registration = _registration(signal, connector_receipt)
    payload = _request_receipt(
        signal,
        connector_receipt,
        registration,
    ).model_dump(mode="python")
    payload["request_sha256"] = "0" * 64
    drifted = PreviewHarmonyConnectorRequestReceipt.model_validate(
        bind_preview_connector_request_receipt(payload)
    )
    with pytest.raises(ValueError, match="harmony_connector_request_digest_invalid"):
        drifted.bind_connector_receipt(registration, connector_receipt, signal)


def test_connector_request_rejects_token_claim_reference_drift() -> None:
    signal = _signals()[0]
    connector_receipt = _receipt(signal, 0)
    registration = _registration(signal, connector_receipt)
    payload = _request_receipt(
        signal,
        connector_receipt,
        registration,
    ).model_dump(mode="python")
    payload["token_claims_sha256"] = "0" * 64
    drifted = PreviewHarmonyConnectorRequestReceipt.model_validate(
        bind_preview_connector_request_receipt(payload)
    )
    with pytest.raises(
        ValueError,
        match="harmony_connector_request_receipt_binding_invalid",
    ):
        drifted.bind_connector_receipt(registration, connector_receipt, signal)


def test_connector_request_digest_matches_fixed_sql_vector() -> None:
    signal = _signals()[0]
    expected_body = {
        "client_id": "squid",
        "connector_receipt_id": _uuid(1000),
        "domain": "coineasy:harmony:preview:connector-request:v1",
        "lane": "quiz_bot",
        "producer_principal_id": _uuid(201),
        "registration_id": _uuid(8000),
        "rpc": "public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)",
        "signal_id": _uuid(1),
        "signal_kind": "quiz_learning",
        "signal_payload_sha256": (
            "c1c6da353f5d3b63d0ebd8bf26971ea6"
            "c569f7f771a8fccf88a0ad176e8e5976"
        ),
        "source_event_id": _uuid(101),
        "workspace_id": WORKSPACE_ID,
    }
    fixed_request_sha256 = (
        "4f7fa302deef9191f49ab7a46cf4610b"
        "d1334f609dfc70f5d822096288a56eb6"
    )
    assert signal.payload_sha256 == expected_body["signal_payload_sha256"]
    assert _sha(expected_body) == fixed_request_sha256
    assert preview_connector_request_sha256(
        workspace_id=WORKSPACE_ID,
        client_id="squid",
        registration_id=_uuid(8000),
        connector_receipt_id=_uuid(1000),
        target_signal=signal.model_dump(mode="python"),
    ) == fixed_request_sha256


def test_connector_request_rejects_mismatched_claimed_signal_digest() -> None:
    signal = _signals()[0].model_dump(mode="python")
    signal["attempts"] = 41
    with pytest.raises(
        ValueError,
        match="harmony_connector_request_signal_digest_invalid",
    ):
        preview_connector_request_sha256(
            workspace_id=WORKSPACE_ID,
            client_id="squid",
            registration_id=_uuid(8000),
            connector_receipt_id=_uuid(1000),
            target_signal=signal,
        )


def test_connector_request_retains_whole_second_signal_validation() -> None:
    signal = _signals()[0].model_dump(mode="python")
    signal.pop("payload_sha256")
    signal["observed_at"] = signal["observed_at"] + timedelta(microseconds=1)
    fractional_signal = bind_harmony_signal_payload(signal)
    with pytest.raises(
        ValidationError,
        match="agent_harmony_signal_observed_at_invalid",
    ):
        HarmonyInput.model_validate({
            "schema_version": "agent-harmony-input@1",
            "workspace_id": WORKSPACE_ID,
            "signals": (fractional_signal,),
        })


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
    shared_connector_receipt_id: str | None = None,
):
    signals = _signals()
    receipts = tuple(
        _receipt(
            signal,
            index,
            receipt_id=shared_connector_receipt_id,
        )
        for index, signal in enumerate(signals)
    )
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


def _trust_snapshot_candidate_inputs():
    collaboration_round = PreviewHarmonyCollaborationRound.model_validate(
        _round()
    )
    signals = tuple(sorted(_signals(), key=lambda item: item.lane.value))
    connectors_by_lane = {
        receipt.lane: receipt
        for receipt in collaboration_round.connector_receipts
    }
    registrations = tuple(
        _registration(signal, connectors_by_lane[signal.lane], index)
        for index, signal in enumerate(signals)
    )
    request_receipts = tuple(
        _request_receipt(
            signal,
            connectors_by_lane[signal.lane],
            registration,
            index,
        )
        for index, (signal, registration) in enumerate(zip(
            signals,
            registrations,
        ))
    )
    return {
        "collaboration_round": collaboration_round,
        "signals": signals,
        "registrations": registrations,
        "request_receipts": request_receipts,
        "branch_ref": "a" * 20,
        "branch_fence_active": True,
        "branch_fence_created_at": NOW - timedelta(minutes=30),
        "branch_fence_expires_at": NOW + timedelta(hours=2),
        "observed_at": NOW,
        "revoked_registration_ids": (),
    }


def test_trust_snapshot_candidate_keeps_round_v1_and_binds_four_paths() -> None:
    result = validate_preview_harmony_trust_snapshot_candidate(
        **_trust_snapshot_candidate_inputs()
    )
    assert result.schema_version == "harmony-trust-snapshot-candidate@1"
    assert result.collaboration_round.schema_version == (
        "harmony-collaboration-round@1"
    )
    assert result.database_currentness_required is True
    assert "current" not in type(result).model_fields
    assert "current" not in result.model_dump(mode="python")
    assert len(result.registrations) == 4
    assert len(result.request_receipts) == 4


def test_trust_snapshot_candidate_rejects_authoritative_current_claim() -> None:
    payload = bind_preview_harmony_trust_snapshot_candidate(
        **_trust_snapshot_candidate_inputs()
    )
    payload["current"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PreviewHarmonyTrustSnapshotCandidate.model_validate(payload)


def test_trust_snapshot_candidate_hash_distinguishes_microseconds() -> None:
    first = _trust_snapshot_candidate_inputs()
    first["branch_fence_created_at"] = (
        NOW - timedelta(minutes=30) + timedelta(microseconds=1)
    )
    second = _trust_snapshot_candidate_inputs()
    second["branch_fence_created_at"] = (
        NOW - timedelta(minutes=30) + timedelta(microseconds=2)
    )

    first_result = validate_preview_harmony_trust_snapshot_candidate(**first)
    second_result = validate_preview_harmony_trust_snapshot_candidate(**second)
    assert first_result.trust_snapshot_candidate_sha256 != (
        second_result.trust_snapshot_candidate_sha256
    )


def test_trust_snapshot_candidate_accepts_registration_in_fence_second() -> None:
    inputs = _trust_snapshot_candidate_inputs()
    inputs["branch_fence_created_at"] = (
        inputs["registrations"][0].created_at + timedelta(microseconds=500_000)
    )

    result = validate_preview_harmony_trust_snapshot_candidate(**inputs)

    assert result.registrations[0].created_at == (
        result.branch_fence_created_at.replace(microsecond=0)
    )


@pytest.mark.parametrize("missing", ["registration", "request"])
def test_trust_snapshot_candidate_rejects_missing_trust_path(
    missing: str,
) -> None:
    inputs = _trust_snapshot_candidate_inputs()
    key = "registrations" if missing == "registration" else "request_receipts"
    inputs[key] = inputs[key][:-1]
    with pytest.raises(
        (ValueError, ValidationError),
        match="harmony_preview_trust_chain_complete_invalid|too_short",
    ):
        validate_preview_harmony_trust_snapshot_candidate(**inputs)


def test_trust_snapshot_candidate_rejects_revoked_registration() -> None:
    inputs = _trust_snapshot_candidate_inputs()
    inputs["revoked_registration_ids"] = (
        str(inputs["registrations"][0].registration_id),
    )
    with pytest.raises(
        ValidationError,
        match="harmony_preview_trust_chain_registration_revoked",
    ):
        validate_preview_harmony_trust_snapshot_candidate(**inputs)


def test_trust_snapshot_candidate_rejects_expired_binding() -> None:
    inputs = _trust_snapshot_candidate_inputs()
    inputs["observed_at"] = NOW + timedelta(hours=1, seconds=1)
    with pytest.raises(
        ValidationError,
        match="harmony_preview_trust_chain_binding_invalid",
    ):
        validate_preview_harmony_trust_snapshot_candidate(**inputs)


@pytest.mark.parametrize("failure", ["inactive", "expired"])
def test_trust_snapshot_candidate_rejects_invalid_branch_fence(
    failure: str,
) -> None:
    inputs = _trust_snapshot_candidate_inputs()
    if failure == "inactive":
        inputs["branch_fence_active"] = False
    else:
        inputs["branch_fence_expires_at"] = NOW - timedelta(seconds=1)
    with pytest.raises(
        ValidationError,
        match="harmony_preview_trust_chain_fence_invalid",
    ):
        validate_preview_harmony_trust_snapshot_candidate(**inputs)


def test_trust_snapshot_candidate_rejects_aggregate_digest_tamper() -> None:
    payload = bind_preview_harmony_trust_snapshot_candidate(
        **_trust_snapshot_candidate_inputs()
    )
    payload["trust_snapshot_candidate_sha256"] = "0" * 64
    with pytest.raises(
        ValidationError,
        match="harmony_preview_trust_chain_digest_invalid",
    ):
        PreviewHarmonyTrustSnapshotCandidate.model_validate(payload)


def test_trust_snapshot_candidate_rejects_signal_producer_as_qa() -> None:
    inputs = _trust_snapshot_candidate_inputs()
    qa_principal_id = inputs["collaboration_round"].stage_receipts[2].principal_id
    signals = list(inputs["signals"])
    drifted_signal = signals[0].model_dump(mode="python")
    drifted_signal["producer_principal_id"] = qa_principal_id
    signals[0] = HarmonyInput.model_validate({
        "schema_version": "agent-harmony-input@1",
        "workspace_id": WORKSPACE_ID,
        "signals": (bind_harmony_signal_payload(drifted_signal),),
    }).signals[0]
    inputs["signals"] = tuple(sorted(signals, key=lambda item: item.lane.value))

    with pytest.raises(
        ValidationError,
        match="harmony_preview_qa_separation_invalid",
    ):
        validate_preview_harmony_trust_snapshot_candidate(**inputs)


def test_typed_round_binds_every_stage_and_exact_qa_inbox() -> None:
    result = PreviewHarmonyCollaborationRound.model_validate(_round())
    assert result.status == "operator_review_pending"
    assert result.stage_receipts[2].verdict == "passed"
    assert result.publication_calls is False


def test_typed_round_rejects_fully_bound_duplicate_connector_receipt_ids() -> None:
    payload = _round(shared_connector_receipt_id=_uuid(1000))
    with pytest.raises(
        ValidationError,
        match="harmony_preview_connector_duplicate",
    ):
        PreviewHarmonyCollaborationRound.model_validate(payload)


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


def test_typed_round_rejects_signal_producer_as_qa() -> None:
    with pytest.raises(
        ValidationError,
        match="harmony_preview_qa_separation_invalid",
    ):
        PreviewHarmonyCollaborationRound.model_validate(
            _round(qa_principal=_uuid(201))
        )


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


def _qa_denial(private_content, *, reviewer_principal=_uuid(5003)):
    evidence = PreviewHarmonyIndependentQaEvidence.model_validate({
        "reviewed_output_sha256": private_content.output_sha256,
        "criteria": {
            "automatic_publication": False,
            "factual_binding": False,
            "no_external_calls": True,
            "private_only": True,
        },
        "findings": ("factual_binding_failed",),
    })
    denial = PreviewHarmonyQaDenialReceipt.model_validate(
        bind_preview_qa_denial_receipt({
            "denial_receipt_id": _uuid(9001),
            "workspace_id": private_content.workspace_id,
            "client_id": private_content.client_id,
            "round_id": private_content.round_id,
            "plan_id": private_content.plan_id,
            "private_content_receipt_id": private_content.receipt_id,
            "denied_output_sha256": private_content.output_sha256,
            "reviewer_principal_id": reviewer_principal,
            "reviewer_binding_sha256": "4" * 64,
            "evidence_sha256": preview_qa_evidence_sha256(evidence),
            "finding_codes": evidence.findings,
            "recorded_at": NOW,
        })
    )
    return denial, evidence


def test_qa_denial_binds_exact_private_output_and_no_side_effects() -> None:
    private_content = _round()["stage_receipts"][1]
    denial, evidence = _qa_denial(private_content)

    denial.bind_private_content(private_content)
    assert denial.bind_evidence(evidence) == evidence
    assert denial.verdict == "failed"
    assert denial.aggregate_only is True
    assert denial.raw_content_included is False
    assert denial.external_calls is False
    assert denial.provider_calls is False
    assert denial.publication_calls is False
    assert denial.automatic_publication is False


def test_qa_denial_rejects_receipt_digest_tamper() -> None:
    private_content = _round()["stage_receipts"][1]
    denial, _ = _qa_denial(private_content)
    payload = denial.model_dump(mode="python")
    payload["reviewer_binding_sha256"] = "0" * 64
    with pytest.raises(
        ValidationError,
        match="harmony_qa_denial_receipt_digest_invalid",
    ):
        PreviewHarmonyQaDenialReceipt.model_validate(payload)


@pytest.mark.parametrize(
    "finding_codes",
    [
        ("unknown_finding",),
        ("factual_binding_failed", "external_call_detected"),
        ("factual_binding_failed", "factual_binding_failed"),
    ],
)
def test_qa_denial_finding_codes_are_closed_sorted_and_unique(
    finding_codes,
) -> None:
    private_content = _round()["stage_receipts"][1]
    denial, _ = _qa_denial(private_content)
    payload = denial.model_dump(mode="python")
    payload["finding_codes"] = finding_codes
    with pytest.raises(ValidationError):
        PreviewHarmonyQaDenialReceipt.model_validate(
            bind_preview_qa_denial_receipt(payload)
        )


def test_qa_evidence_findings_are_exactly_derived_from_criteria() -> None:
    with pytest.raises(
        ValidationError,
        match="harmony_qa_evidence_findings_invalid",
    ):
        PreviewHarmonyIndependentQaEvidence.model_validate({
            "reviewed_output_sha256": "1" * 64,
            "criteria": {
                "automatic_publication": True,
                "factual_binding": False,
                "no_external_calls": True,
                "private_only": True,
            },
            "findings": ("factual_binding_failed",),
        })


def test_qa_evidence_criteria_require_json_booleans() -> None:
    with pytest.raises(ValidationError):
        PreviewHarmonyIndependentQaEvidence.model_validate({
            "reviewed_output_sha256": "1" * 64,
            "criteria": {
                "automatic_publication": "false",
                "factual_binding": False,
                "no_external_calls": True,
                "private_only": True,
            },
            "findings": ("factual_binding_failed",),
        })


def test_qa_denial_rejects_private_content_producer_as_reviewer() -> None:
    private_content = _round()["stage_receipts"][1]
    denial, _ = _qa_denial(
        private_content,
        reviewer_principal=private_content.principal_id,
    )
    with pytest.raises(
        ValueError,
        match="harmony_qa_denial_producer_separation_invalid",
    ):
        denial.bind_private_content(private_content)


def test_qa_denial_rejects_any_signal_producer_as_reviewer() -> None:
    private_content = _round()["stage_receipts"][1]
    producer_principal = _signals()[0].producer_principal_id
    denial, _ = _qa_denial(
        private_content,
        reviewer_principal=producer_principal,
    )
    with pytest.raises(
        ValueError,
        match="harmony_qa_denial_producer_separation_invalid",
    ):
        denial.assert_producer_separation(
            tuple(str(signal.producer_principal_id) for signal in _signals())
        )


@pytest.mark.parametrize(
    "producer_principal_id",
    [
        "not-a-uuid",
        "00000000-0000-1000-8000-000000000001",
    ],
)
def test_qa_denial_requires_uuid4_for_string_producer_ids(
    producer_principal_id: str,
) -> None:
    private_content = _round()["stage_receipts"][1]
    denial, _ = _qa_denial(private_content)
    with pytest.raises(
        ValueError,
        match="harmony_qa_denial_producer_separation_invalid",
    ):
        denial.assert_producer_separation((producer_principal_id,))


def test_qa_denial_rejects_private_output_linkage_drift() -> None:
    private_content = _round()["stage_receipts"][1]
    denial, _ = _qa_denial(private_content)
    payload = denial.model_dump(mode="python")
    payload["private_content_receipt_id"] = _uuid(9999)
    drifted = PreviewHarmonyQaDenialReceipt.model_validate(
        bind_preview_qa_denial_receipt(payload)
    )
    with pytest.raises(
        ValueError,
        match="harmony_qa_denial_private_content_binding_invalid",
    ):
        drifted.bind_private_content(private_content)
