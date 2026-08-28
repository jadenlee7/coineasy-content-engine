from __future__ import annotations

import ast
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.gtm_intelligence import GtmInboxPage
from core.gtm_intelligence.sources.telegram import (
    TelegramOwnerProjection,
    project_telegram_triage,
)
from core.gtm_intelligence.sources.telegram_v2 import (
    INTAKE_COMMIT_SUBJECT_SCHEMA,
    INTAKE_LEGACY_SHADOW_DISPATCH_KEY,
    INTAKE_SANITIZED_GATE_ENVELOPE_SCHEMA,
    INTAKE_SANITIZED_GATE_SUBJECT_SCHEMA,
    INTAKE_V2_PROMOTION_DISPATCH_KEY,
    V2_OUTBOX_EVENT_IDENTITY_SCHEMA,
    V2_OUTBOX_EVENT_SCHEMA,
    V2_OUTBOX_NAME,
    V2_OUTBOX_STREAM_KEY,
    V2_PROMOTION_SUBJECT_SCHEMA,
    EligibleTelegramV2Event,
    TelegramV2ReaderIneligible,
    TelegramV2ReaderSnapshot,
    project_telegram_v2_delivery,
    read_eligible_telegram_v2_event,
)
from core.gtm_intelligence.telegram_v2_receipts import (
    InMemoryTelegramV2ReceiptStore,
    TelegramV2IntakeReceipt,
    TelegramV2IntakeReceiptRepository,
    TelegramV2ReceiptConflict,
    TelegramV2ReceiptDisabled,
    TelegramV2ReceiptIndeterminate,
    build_telegram_v2_intake_receipt,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _projection(ordinal: int = 0, **overrides: object) -> dict[str, object]:
    digest = _sha(f"question-{ordinal}")
    values: dict[str, object] = {
        "schema_version": "coineasy-telegram-owner-projection@2",
        "source_system": "coineasydaily.single-consumer",
        "projection_stage": "post-owner-redaction",
        "client_id": "squid",
        "read_only_projection": True,
        "new_telegram_consumer": False,
        "raw_update_included": False,
        "telegram_identifiers_included": False,
        "private_links_included": False,
        "hmac_key_included": False,
        "summary_mode": "owner-redacted-nonverbatim",
        "observed_at": "2026-08-26T10:00:01Z",
        "question_observed_at": "2026-08-26T10:00:00Z",
        "digest_scheme": "hmac-sha256-v2",
        "question_ref": f"question:{digest}",
        "question_hmac_sha256": digest,
        "topic": "product",
        "question_summary_ko": f"제품 이용 절차를 묻는 비식별 질문 번호 {ordinal}입니다.",
        "answer_state": "unanswered",
        "faq_match": "none",
        "faq_source_sha256": None,
        "faq_binding_sha256": None,
        "draft_reply_ko": None,
        "safety_class": "none",
    }
    values.update(overrides)
    return values


def _reader_snapshot(
    *,
    count: int = 1,
    selected_ordinal: int = 0,
    stream_id: str = "1800000000000-1",
    projection_overrides: dict[str, object] | None = None,
    source_update_overrides: dict[int, dict[str, object]] | None = None,
    marker_overrides: dict[str, object] | None = None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    projections = [_projection(index) for index in range(count)]
    if projection_overrides:
        projections[selected_ordinal].update(projection_overrides)
    stage_sha = "e" * 64
    updates: list[dict[str, object]] = []
    for ordinal, projection in enumerate(projections):
        projection_sha = _sha(_canonical(projection))
        update = {
            "ordinal": ordinal,
            "update_ref": f"update:{_sha(f'update-{ordinal}')}",
            "member_binding_sha256": _sha(f"binding-{ordinal}"),
            "projection_state": "emitted",
            "projection_sha256": projection_sha,
            "projection_member": {
                "question_ref": projection["question_ref"],
                "projection_sha256": projection_sha,
                "projection": projection,
            },
            "tombstone_reason": None,
        }
        if source_update_overrides and ordinal in source_update_overrides:
            update.update(source_update_overrides[ordinal])
        updates.append(update)
    gate_subject = {
        "schema_version": INTAKE_SANITIZED_GATE_SUBJECT_SCHEMA,
        "identity_context_ref": "context:" + ("c" * 64),
        "source_epoch_ref": "epoch:" + ("d" * 64),
        "source_batch_ref": "batch:" + ("b" * 64),
        "previous_cursor_sha256": "a" * 64,
        "predecessor_head_ref": "bootstrap:" + ("f" * 64),
        "next_offset": 1_000,
        "staged_at": "2026-08-26T10:05:00Z",
        "transport_updates_observed": count,
        "stage_sha256": stage_sha,
        "updates": updates,
        "raw_update_included": False,
        "private_legacy_payload_included": False,
        "telegram_identifiers_included": False,
        "sanitized_projections_included": True,
    }
    gate_sha = _sha(_canonical(gate_subject))
    marker = {
        "schema_version": INTAKE_COMMIT_SUBJECT_SCHEMA,
        "identity_context_ref": gate_subject["identity_context_ref"],
        "source_epoch_ref": gate_subject["source_epoch_ref"],
        "source_batch_ref": gate_subject["source_batch_ref"],
        "expected_cursor_sha256": gate_subject["previous_cursor_sha256"],
        "predecessor_head_ref": gate_subject["predecessor_head_ref"],
        "next_offset_sha256": _sha(str(gate_subject["next_offset"])),
        "stage_sha256": stage_sha,
        "transport_updates_observed": count,
        "members": [
            {
                "ordinal": update["ordinal"],
                "update_ref": update["update_ref"],
                "member_binding_sha256": update["member_binding_sha256"],
                "legacy_payload_hmac_sha256": _sha(f"legacy-{ordinal}"),
                "projection_sha256": update["projection_sha256"],
                "projection_state": update["projection_state"],
            }
            for ordinal, update in enumerate(updates)
        ],
        "gate_sha256": gate_sha,
        "dispatch_score": "0",
        "dispatch_offset_width": 19,
        "legacy_shadow_dispatch_key": INTAKE_LEGACY_SHADOW_DISPATCH_KEY,
        "v2_promotion_dispatch_key": INTAKE_V2_PROMOTION_DISPATCH_KEY,
        "owner_private_stage": True,
        "legacy_effects_materialized": False,
        "sanitized_outbox_materialized": False,
    }
    if marker_overrides:
        marker.update(marker_overrides)
    marker_text = _canonical(marker)
    commit_ref = f"commit:{_sha(marker_text)}"
    gate = {
        "schema_version": INTAKE_SANITIZED_GATE_ENVELOPE_SCHEMA,
        "commit_ref": commit_ref,
        "gate_sha256": gate_sha,
        "gate_subject": gate_subject,
    }

    events: list[dict[str, object]] = []
    for ordinal, projection in enumerate(projections):
        projection_sha = _sha(_canonical(projection))
        identity = {
            "schema_version": V2_OUTBOX_EVENT_IDENTITY_SCHEMA,
            "source_commit_ref": commit_ref,
            "source_projection_ordinal": ordinal,
            "projection_sha256": projection_sha,
        }
        event_key = _sha(_canonical(identity))
        events.append({
            "schema_version": V2_OUTBOX_EVENT_SCHEMA,
            "outbox_name": V2_OUTBOX_NAME,
            "client_id": "squid",
            "event_ref": f"outbox-v2:{event_key}",
            "source_commit_ref": commit_ref,
            "source_stage_sha256": stage_sha,
            "source_gate_sha256": gate_sha,
            "source_projection_ordinal": ordinal,
            "idempotency_key": event_key,
            "projection_sha256": projection_sha,
            "raw_update_included": False,
            "telegram_identifiers_included": False,
            "owner_private_stage_included": False,
            "automatic_publication": False,
            "projection": projection,
        })
    ordered_members = [
        {
            "source_projection_ordinal": event["source_projection_ordinal"],
            "event_ref": event["event_ref"],
            "idempotency_key": event["idempotency_key"],
            "question_ref": event["projection"]["question_ref"],
            "projection_sha256": event["projection_sha256"],
            "event_sha256": _sha(_canonical(event)),
        }
        for event in events
    ]
    promotion_subject = {
        "schema_version": V2_PROMOTION_SUBJECT_SCHEMA,
        "outbox_name": V2_OUTBOX_NAME,
        "source_commit_ref": commit_ref,
        "source_stage_sha256": stage_sha,
        "source_gate_sha256": gate_sha,
        "source_batch_ref": gate_subject["source_batch_ref"],
        "transport_updates_observed": count,
        "outcome_counts": {
            "emitted": count,
            "tombstoned": 0,
            "not_applicable": 0,
        },
        "sanitized_projection_count": count,
        "ordered_members": ordered_members,
        "complete_source_stage_outcomes": True,
        "raw_update_included": False,
        "telegram_identifiers_included": False,
        "owner_private_stage_included": False,
        "legacy_effects_materialized": False,
        "consumer_delivery_observed": False,
        "automatic_publication": False,
        "production_wiring_observed": False,
    }
    promotion_text = _canonical(promotion_subject)
    promotion_ref = f"promotion:{_sha(promotion_text)}"
    event = events[selected_ordinal]
    event_text = _canonical(event)
    event_sha = _sha(event_text)
    snapshot = {
        "schema_version": "coineasy-telegram-v2-reader-evidence@1",
        "reader_policy": "coineasy-telegram-v2-strict-reader@1",
        "mode": "atomic_read_snapshot",
        "source_stream_key": V2_OUTBOX_STREAM_KEY,
        "stream_id": stream_id,
        "stream_row": {
            "schema_version": V2_OUTBOX_EVENT_SCHEMA,
            "idempotency_key": event["idempotency_key"],
            "event_sha256": event_sha,
            "projection_sha256": event["projection_sha256"],
            "event_json": event_text,
        },
        "current_event_index_value": "|".join((
            "v2",
            event_sha,
            str(event["projection_sha256"]),
            commit_ref,
            str(selected_ordinal),
            promotion_ref,
            stream_id,
        )),
        "source_promotion_index_value": promotion_ref,
        "promotion_marker_json": promotion_text,
        "intake_marker_json": marker_text,
        "sanitized_gate_json": _canonical(gate),
        "atomic_snapshot": True,
        "read_only_projection": True,
        "new_telegram_consumer": False,
        "raw_update_included": False,
        "telegram_identifiers_included": False,
        "owner_private_stage_included": False,
        "external_calls": False,
        "database_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
    }
    return snapshot, events


def test_strict_v2_reader_projects_only_opaque_eligible_event() -> None:
    snapshot, _ = _reader_snapshot()

    eligible = read_eligible_telegram_v2_event(snapshot)
    item = project_telegram_v2_delivery(eligible)

    assert isinstance(eligible, EligibleTelegramV2Event)
    assert eligible.marker_index_eligible is True
    assert eligible.event.projection.digest_scheme == "hmac-sha256-v2"
    assert item.event_type == "telegram.triage.v2"
    assert item.details.schema_version == "coineasy-telegram-triage-detail@2"
    assert item.details.digest_scheme == "hmac-sha256-v2"
    assert item.source_acknowledged is False
    assert item.automatic_publication is False


def test_bare_v2_projection_cannot_bypass_reader_or_saved_page() -> None:
    projection = _projection()
    with pytest.raises(ValidationError):
        TelegramOwnerProjection.model_validate(projection)
    with pytest.raises(ValidationError):
        project_telegram_triage(projection)

    eligible = read_eligible_telegram_v2_event(_reader_snapshot()[0])
    item = project_telegram_v2_delivery(eligible)
    forged_eligible = EligibleTelegramV2Event.model_validate(
        eligible.model_dump(mode="json")
    )
    copied_eligible = eligible.model_copy()
    with pytest.raises(TypeError):
        project_telegram_v2_delivery(forged_eligible)
    with pytest.raises(TypeError):
        project_telegram_v2_delivery(copied_eligible)
    forged_page = {
        "schema_version": "coineasy-gtm-inbox@1",
        "mode": "shadow_read_only",
        "generated_at": "2026-08-26T10:06:00Z",
        "items": [item.model_dump(mode="json")],
        "next_cursor": None,
        "read_only_projection": True,
        "external_calls": False,
        "database_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
    }
    with pytest.raises(ValidationError):
        GtmInboxPage.model_validate(forged_page)


@pytest.mark.parametrize(
    "field",
    (
        "stream_row",
        "current_event_index_value",
        "source_promotion_index_value",
        "promotion_marker_json",
        "intake_marker_json",
        "sanitized_gate_json",
    ),
)
def test_reader_requires_all_six_exact_current_bindings(field: str) -> None:
    snapshot, _ = _reader_snapshot()
    broken = copy.deepcopy(snapshot)
    if field == "stream_row":
        broken[field]["event_sha256"] = "0" * 64
    elif field == "source_promotion_index_value":
        broken[field] = "promotion:" + ("0" * 64)
    else:
        broken[field] += " "

    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(broken)


def test_reader_requires_every_explicit_snapshot_boundary_field() -> None:
    snapshot, _ = _reader_snapshot()
    for field in (
        "schema_version",
        "atomic_snapshot",
        "read_only_projection",
        "raw_update_included",
        "automatic_publication",
    ):
        broken = copy.deepcopy(snapshot)
        del broken[field]
        with pytest.raises(TelegramV2ReaderIneligible):
            read_eligible_telegram_v2_event(broken)


def test_reader_rejects_all_model_instances_and_accepts_exact_dict_only() -> None:
    snapshot, _ = _reader_snapshot()
    validated = TelegramV2ReaderSnapshot.model_validate(snapshot)
    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(validated)  # type: ignore[arg-type]
    with pytest.raises(TelegramV2ReaderIneligible):
        build_telegram_v2_intake_receipt(validated)  # type: ignore[arg-type]

    copied = validated.model_copy(update={"raw_update_included": True})
    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(copied)  # type: ignore[arg-type]
    with pytest.raises(TelegramV2ReaderIneligible):
        build_telegram_v2_intake_receipt(copied)  # type: ignore[arg-type]

    missing = {
        name: getattr(validated, name)
        for name in TelegramV2ReaderSnapshot.model_fields
        if name != "atomic_snapshot"
    }
    constructed = TelegramV2ReaderSnapshot.model_construct(**missing)
    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(constructed)  # type: ignore[arg-type]


def test_reader_rejects_boolean_source_ordinals_and_unhashable_state() -> None:
    boolean_ordinal, _ = _reader_snapshot(
        count=2,
        selected_ordinal=1,
        source_update_overrides={1: {"ordinal": True}},
    )
    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(boolean_ordinal)

    unhashable_state, _ = _reader_snapshot(
        source_update_overrides={0: {"projection_state": ["emitted"]}},
    )
    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(unhashable_state)


@pytest.mark.parametrize(
    "marker_overrides",
    (
        {"transport_updates_observed": True},
        {"transport_updates_observed": 1.0},
        {"dispatch_offset_width": 19.0},
    ),
)
def test_reader_requires_exact_marker_integer_types(
    marker_overrides: dict[str, object],
) -> None:
    snapshot, _ = _reader_snapshot(marker_overrides=marker_overrides)
    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(snapshot)


def test_reader_errors_do_not_chain_sensitive_validation_details() -> None:
    canary = "비공개 문의는 user@example.invalid로 남겨 주세요."
    snapshot, _ = _reader_snapshot(
        projection_overrides={"question_summary_ko": canary}
    )
    with pytest.raises(TelegramV2ReaderIneligible) as captured:
        read_eligible_telegram_v2_event(snapshot)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert canary not in str(captured.value)


def test_reader_normalizes_deep_json_recursion_to_ineligible() -> None:
    snapshot, _ = _reader_snapshot()
    broken = copy.deepcopy(snapshot)
    broken["sanitized_gate_json"] = '{"nested":' + ("[" * 2_000) + (
        "]" * 2_000
    ) + "}"
    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(broken)


@pytest.mark.parametrize(
    "projection_override",
    (
        {"schema_version": "coineasy-telegram-owner-projection@1"},
        {"digest_scheme": "hmac-sha256-v1"},
        {"schema_version": "coineasy-telegram-owner-projection@3"},
        {"raw_update_included": 0},
    ),
)
def test_reader_rejects_v1_v2_confusion_and_bool_coercion(
    projection_override: dict[str, object],
) -> None:
    snapshot, _ = _reader_snapshot(projection_overrides=projection_override)
    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(snapshot)


def test_reader_rejects_duplicate_key_and_noncanonical_event_json() -> None:
    snapshot, _ = _reader_snapshot()
    broken = copy.deepcopy(snapshot)
    original = broken["stream_row"]["event_json"]
    duplicate = (
        '{"schema_version":"coineasy-telegram-owner-outbox-event@2",'
        + original[1:]
    )
    broken["stream_row"]["event_json"] = duplicate
    broken["stream_row"]["event_sha256"] = _sha(duplicate)
    with pytest.raises(TelegramV2ReaderIneligible):
        read_eligible_telegram_v2_event(broken)


def test_reader_validates_complete_100_member_manifest_without_page_truncation() -> None:
    snapshot, _ = _reader_snapshot(count=100, selected_ordinal=99)

    eligible = read_eligible_telegram_v2_event(snapshot)

    assert eligible.transport_updates_observed == 100
    assert eligible.sanitized_projection_count == 100
    assert eligible.outcome_counts.emitted == 100
    assert eligible.event.source_projection_ordinal == 99


def test_privacy_canary_is_rejected_without_echoing_value() -> None:
    canary = "비공개 문의는 user@example.invalid로 남겨 주세요."
    snapshot, _ = _reader_snapshot(
        projection_overrides={"question_summary_ko": canary}
    )
    with pytest.raises(TelegramV2ReaderIneligible) as captured:
        read_eligible_telegram_v2_event(snapshot)
    assert canary not in str(captured.value)


def test_receipt_is_exact_default_disabled_and_replay_safe() -> None:
    snapshot, _ = _reader_snapshot()
    receipt = build_telegram_v2_intake_receipt(snapshot)
    store = InMemoryTelegramV2ReceiptStore()
    disabled = TelegramV2IntakeReceiptRepository(store)

    with pytest.raises(TelegramV2ReceiptDisabled):
        disabled.put(receipt)
    assert store.put_calls == store.get_calls == 0

    repository = TelegramV2IntakeReceiptRepository(store, enabled=True)
    first_created = repository.put(receipt)
    second_created = repository.put(receipt)
    assert first_created is True
    assert second_created is False
    stored = repository.get(receipt.idempotency_key)
    assert stored is not None
    assert stored.model_dump(mode="json") == receipt.model_dump(mode="json")
    with pytest.raises(TypeError):
        repository.put(stored)
    assert receipt.source_acknowledged is False
    assert receipt.public_delivery_observed is False
    assert receipt.provider_persistence_observed is False


def test_receipt_requires_reader_derived_item_and_builder_provenance() -> None:
    snapshot, _ = _reader_snapshot()
    eligible = read_eligible_telegram_v2_event(snapshot)
    item = project_telegram_v2_delivery(eligible)
    forged_item = item.model_copy(update={"title_ko": "검증되지 않은 제목"})
    with pytest.raises(TypeError):
        build_telegram_v2_intake_receipt(eligible, forged_item)

    with pytest.raises(TelegramV2ReaderIneligible):
        build_telegram_v2_intake_receipt(eligible)

    receipt = build_telegram_v2_intake_receipt(snapshot)
    forged_receipt = TelegramV2IntakeReceipt.model_validate(
        receipt.model_dump(mode="json")
    )
    copied_receipt = receipt.model_copy()
    repository = TelegramV2IntakeReceiptRepository(enabled=True)
    with pytest.raises(TypeError):
        repository.put(forged_receipt)
    with pytest.raises(TypeError):
        repository.put(copied_receipt)


def test_same_question_projection_distinct_deliveries_have_distinct_identity() -> None:
    first_snapshot, _ = _reader_snapshot(stream_id="1800000000000-1")
    second_snapshot, _ = _reader_snapshot(stream_id="1800000000000-2")
    first_receipt = build_telegram_v2_intake_receipt(first_snapshot)
    second_receipt = build_telegram_v2_intake_receipt(second_snapshot)
    assert first_receipt.idempotency_key != second_receipt.idempotency_key
    assert first_receipt.receipt_ref != second_receipt.receipt_ref
    repository = TelegramV2IntakeReceiptRepository(enabled=True)
    repository.put(first_receipt)
    repository.put(second_receipt)
    assert len(repository.store._receipts) == 2


def test_append_response_loss_converges_to_one_exact_receipt() -> None:
    receipt = build_telegram_v2_intake_receipt(_reader_snapshot()[0])
    store = InMemoryTelegramV2ReceiptStore()
    original_put = store.put_if_absent
    lose_once = True

    def put_then_lose_once(idempotency_key: str, receipt_json: str) -> bool:
        nonlocal lose_once
        created = original_put(idempotency_key, receipt_json)
        if lose_once:
            lose_once = False
            raise RuntimeError("simulated response loss")
        return created

    store.put_if_absent = put_then_lose_once
    repository = TelegramV2IntakeReceiptRepository(store, enabled=True)

    with pytest.raises(TelegramV2ReceiptIndeterminate):
        repository.put(receipt)
    recovered = repository.put(receipt)

    assert recovered is False
    assert len(store._receipts) == 1


def test_append_rejects_non_boolean_store_result_before_readback() -> None:
    receipt = build_telegram_v2_intake_receipt(_reader_snapshot()[0])
    store = InMemoryTelegramV2ReceiptStore()
    store.put_if_absent = (  # type: ignore[method-assign,return-value]
        lambda _key, _value: "true"
    )
    repository = TelegramV2IntakeReceiptRepository(store, enabled=True)

    with pytest.raises(TelegramV2ReceiptIndeterminate):
        repository.put(receipt)
    assert store.get_calls == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("provider_calls", None),
        ("provider_calls", 0),
        ("intake_validated", 1),
    ),
)
def test_receipt_get_rejects_non_exact_stored_serialization(
    field: str,
    replacement: object,
) -> None:
    receipt = build_telegram_v2_intake_receipt(_reader_snapshot()[0])
    payload = receipt.model_dump(mode="json")
    if replacement is None:
        payload.pop(field)
    else:
        payload[field] = replacement
    tampered = _canonical(payload)
    store = InMemoryTelegramV2ReceiptStore()
    store._receipts[receipt.idempotency_key] = tampered
    repository = TelegramV2IntakeReceiptRepository(store, enabled=True)

    with pytest.raises(TelegramV2ReceiptIndeterminate) as captured:
        repository.get(receipt.idempotency_key)

    assert str(captured.value) == "gtm_telegram_v2_receipt_readback_invalid"
    assert field not in str(captured.value)
    assert tampered not in str(captured.value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("provider_calls", None),
        ("provider_calls", 0),
        ("intake_validated", 1),
    ),
)
def test_receipt_append_rejects_non_exact_readback_serialization(
    field: str,
    replacement: object,
) -> None:
    receipt = build_telegram_v2_intake_receipt(_reader_snapshot()[0])
    store = InMemoryTelegramV2ReceiptStore()
    original_get = store.get

    def get_tampered(idempotency_key: str) -> str | None:
        stored = original_get(idempotency_key)
        assert stored is not None
        payload = json.loads(stored)
        if replacement is None:
            payload.pop(field)
        else:
            payload[field] = replacement
        return _canonical(payload)

    store.get = get_tampered  # type: ignore[method-assign]
    repository = TelegramV2IntakeReceiptRepository(store, enabled=True)

    with pytest.raises(TelegramV2ReceiptConflict) as captured:
        repository.put(receipt)

    assert str(captured.value) == "gtm_telegram_v2_receipt_readback_conflict"
    assert field not in str(captured.value)


def test_receipt_contains_no_raw_or_authority_surface() -> None:
    receipt = build_telegram_v2_intake_receipt(_reader_snapshot()[0])
    payload = _canonical(receipt.model_dump(mode="json"))
    for forbidden in (
        "telegram_update_id",
        "chat_id",
        "user_id",
        "message_id",
        "invite_link",
        "raw_question",
        "private_legacy_payload",
        "hmac_key",
        "bot_token",
    ):
        assert forbidden not in payload


@pytest.mark.parametrize("enabled", ("false", 0, 1, None))
def test_receipt_repository_requires_literal_bool(enabled: object) -> None:
    with pytest.raises(TypeError):
        TelegramV2IntakeReceiptRepository(enabled=enabled)  # type: ignore[arg-type]


def test_receipt_repository_rejects_custom_store() -> None:
    class CustomStore:
        def put_if_absent(self, idempotency_key: str, receipt_json: str) -> bool:
            return True

        def get(self, idempotency_key: str) -> str | None:
            return None

    with pytest.raises(TypeError):
        TelegramV2IntakeReceiptRepository(  # type: ignore[arg-type]
            CustomStore(),
            enabled=True,
        )


def test_in_memory_store_concurrent_different_bytes_never_overwrite() -> None:
    store = InMemoryTelegramV2ReceiptStore()
    barrier = Barrier(2)

    def write(value: str) -> str:
        barrier.wait()
        try:
            return "created" if store.put_if_absent("a" * 64, value) else "reused"
        except TelegramV2ReceiptConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = set(executor.map(write, ("first", "second")))

    assert outcomes == {"created", "conflict"}
    assert store.get("a" * 64) in {"first", "second"}


def test_v2_modules_import_no_io_or_mutating_clients() -> None:
    root = Path(__file__).parents[1] / "core" / "gtm_intelligence"
    paths = (
        root / "sources" / "telegram_v2.py",
        root / "telegram_v2_receipts.py",
    )
    banned = {
        "aiohttp",
        "httpx",
        "os",
        "pathlib",
        "redis",
        "requests",
        "socket",
        "subprocess",
        "supabase",
        "telegram",
        "urllib",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(banned)
        source = path.read_text(encoding="utf-8")
        assert "xack(" not in source.lower()
        assert "xgroup" not in source.lower()
