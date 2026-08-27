from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from core.gtm_intelligence import (
    build_telegram_v2_intake_receipt,
    project_telegram_v2_delivery,
    read_eligible_telegram_v2_event,
)


_MAX_INPUT_BYTES = 2 * 1024 * 1024
_MAX_LOCK_BYTES = 128 * 1024
_MAX_MANIFEST_BYTES = 128 * 1024
_RESULT_SCHEMA = "coineasy-telegram-v2-shadow-result@1"
_LOCK_SCHEMA = (
    "coineasy-content-engine-coineasydaily-telegram-v2-vendor-lock@1"
)
_LOCK_PRODUCER_BASE_SHA = (
    "6f4a137b889a8d159a64d97924bb0ffef784aae9"
)
_LOCK_REVIEWED_PRODUCER_SHA = (
    "0ffce811d2cad55bc7083d20c055801687927657"
)
_LOCK_RAW_SHA256 = (
    "76547ac2bef33bff97233c191cc8cdcaecae5212cbea8968ecffe19f7d98e178"
)
_FIXTURE_NAMES = (
    "one_emitted",
    "hundred_emitted",
    "hundred_mixed",
)
_SOURCE_CONTRACT_PATHS = (
    "community/gtm_intake_staging.py",
    "community/gtm_projection.py",
    "community/gtm_v2_promoter.py",
)
_SCENARIO_EPOCH = "2026-08-27T00:00:00Z"


@dataclass(frozen=True)
class _VerifiedVendorFixture:
    fixture_name: str
    fixture_raw_sha256: str
    manifest_raw_sha256: str
    lock_raw_sha256: str
    producer_fixture_provenance_verified: bool
    reader_snapshot: dict[str, object]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unique_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("gtm_telegram_v2_shadow_duplicate_key")
        value[key] = item
    return value


def _reject_json_constant(_: str) -> object:
    raise ValueError("gtm_telegram_v2_shadow_constant_invalid")


def _read_raw(path: Path, *, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("gtm_telegram_v2_shadow_input_invalid")
    try:
        if path.stat().st_size > maximum:
            raise ValueError("gtm_telegram_v2_shadow_input_invalid")
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("gtm_telegram_v2_shadow_input_invalid") from exc
    if len(raw) > maximum:
        raise ValueError("gtm_telegram_v2_shadow_input_invalid")
    return raw


def _parse_json_object(
    raw: bytes,
    *,
    canonical_file: bool,
) -> dict[str, object]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("gtm_telegram_v2_shadow_input_invalid") from exc
    if type(payload) is not dict:
        raise ValueError("gtm_telegram_v2_shadow_input_invalid")
    if canonical_file and raw != (_canonical_json(payload) + "\n").encode("utf-8"):
        raise ValueError("gtm_telegram_v2_shadow_input_invalid")
    return payload


def _read_snapshot(path: Path) -> dict[str, object]:
    return _parse_json_object(
        _read_raw(path, maximum=_MAX_INPUT_BYTES),
        canonical_file=False,
    )


def _require_exact_keys(
    value: object,
    expected: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != expected:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    return value


def _strict_contract_equal(value: object, expected: object) -> bool:
    """Compare JSON contracts without Python bool/int coercion."""

    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        if frozenset(value) != frozenset(expected):
            return False
        return all(
            _strict_contract_equal(value[key], expected[key])
            for key in expected
        )
    if type(expected) is list:
        return len(value) == len(expected) and all(
            _strict_contract_equal(item, expected_item)
            for item, expected_item in zip(value, expected)
        )
    return value == expected


def _require_sha256(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    return value


def _require_bytes(value: object, *, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    return value


def _validate_file_record(
    value: object,
    *,
    expected_path: str,
    maximum: int,
) -> dict[str, object]:
    record = _require_exact_keys(
        value,
        frozenset({"path", "sha256", "bytes"}),
    )
    if type(record["path"]) is not str or record["path"] != expected_path:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    _require_sha256(record["sha256"])
    _require_bytes(record["bytes"], maximum=maximum)
    return record


def _require_ref(value: object, *, prefix: str) -> str:
    if type(value) is not str or not value.startswith(prefix):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    _require_sha256(value.removeprefix(prefix))
    return value


def _validate_lock(payload: dict[str, object]) -> dict[str, object]:
    lock = _require_exact_keys(
        payload,
        frozenset({
            "boundary",
            "fixtures",
            "manifest",
            "producer_fixture_generator",
            "schema_version",
            "source_contract_files",
            "upstream",
        }),
    )
    if lock["schema_version"] != _LOCK_SCHEMA:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    upstream = _require_exact_keys(
        lock["upstream"],
        frozenset({
            "base_head_sha",
            "contract_source_state",
            "fixture_directory",
            "repository",
            "reviewed_producer_commit_sha",
        }),
    )
    if not _strict_contract_equal(upstream, {
        "base_head_sha": _LOCK_PRODUCER_BASE_SHA,
        "contract_source_state": "merged_reviewed",
        "fixture_directory": "tests/fixtures/gtm_v2_golden",
        "repository": "coineasydaily",
        "reviewed_producer_commit_sha": _LOCK_REVIEWED_PRODUCER_SHA,
    }):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    boundary = _require_exact_keys(
        lock["boundary"],
        frozenset({
            "live_atomic_redis_snapshot_observed",
            "network_reads_required",
            "producer_fixture_provenance_verified",
            "sibling_repository_reads_required",
            "vendored_byte_for_byte",
        }),
    )
    if not _strict_contract_equal(boundary, {
        "live_atomic_redis_snapshot_observed": False,
        "network_reads_required": False,
        "producer_fixture_provenance_verified": True,
        "sibling_repository_reads_required": False,
        "vendored_byte_for_byte": True,
    }):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    manifest = _validate_file_record(
        lock["manifest"],
        expected_path="manifest.json",
        maximum=_MAX_MANIFEST_BYTES,
    )
    if type(lock["fixtures"]) is not list or len(lock["fixtures"]) != 3:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    fixture_records = [
        _validate_file_record(
            record,
            expected_path=f"{name}.json",
            maximum=_MAX_INPUT_BYTES,
        )
        for name, record in zip(_FIXTURE_NAMES, lock["fixtures"])
    ]
    if len({record["sha256"] for record in fixture_records}) != 3:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    if (
        type(lock["source_contract_files"]) is not list
        or len(lock["source_contract_files"]) != 3
    ):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    source_records = [
        _validate_file_record(
            record,
            expected_path=path,
            maximum=_MAX_INPUT_BYTES,
        )
        for path, record in zip(
            _SOURCE_CONTRACT_PATHS,
            lock["source_contract_files"],
        )
    ]
    _validate_file_record(
        lock["producer_fixture_generator"],
        expected_path="community/gtm_v2_golden.py",
        maximum=_MAX_INPUT_BYTES,
    )
    if manifest["sha256"] in {
        record["sha256"] for record in (*fixture_records, *source_records)
    }:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    return lock


def _validate_outcome_counts(value: object) -> dict[str, object]:
    counts = _require_exact_keys(
        value,
        frozenset({"emitted", "tombstoned", "not_applicable"}),
    )
    for item in counts.values():
        if type(item) is not int or item < 0 or item > 100:
            raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    return counts


def _validate_manifest(
    payload: dict[str, object],
    *,
    lock: dict[str, object],
) -> dict[str, object]:
    manifest = _require_exact_keys(
        payload,
        frozenset({
            "boundary",
            "contracts",
            "fixtures",
            "generated_by",
            "purpose",
            "scenario_epoch",
            "schema_version",
            "source_files",
        }),
    )
    if (
        manifest["schema_version"]
        != "coineasy-telegram-v2-golden-manifest@1"
        or manifest["generated_by"] != "community.gtm_v2_golden@1"
        or manifest["purpose"]
        != "offline_cross_repository_consumer_contract"
        or manifest["scenario_epoch"] != _SCENARIO_EPOCH
    ):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    contracts = _require_exact_keys(
        manifest["contracts"],
        frozenset({
            "digest_scheme",
            "event_schema_version",
            "outbox_name",
            "projection_schema_version",
            "promotion_schema_version",
            "reader_evidence_schema_version",
            "reader_policy",
        }),
    )
    if not _strict_contract_equal(contracts, {
        "digest_scheme": "hmac-sha256-v2",
        "event_schema_version": "coineasy-telegram-owner-outbox-event@2",
        "outbox_name": "squid.telegram.owner_projection.v2",
        "projection_schema_version": "coineasy-telegram-owner-projection@2",
        "promotion_schema_version": (
            "coineasy-telegram-owner-sanitized-promotion-subject@1"
        ),
        "reader_evidence_schema_version": (
            "coineasy-telegram-v2-reader-evidence@1"
        ),
        "reader_policy": "coineasy-telegram-v2-strict-reader@1",
    }):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    boundary = _require_exact_keys(
        manifest["boundary"],
        frozenset({
            "contract_validation_only",
            "credentials_included",
            "deterministic",
            "environment_reads",
            "external_calls",
            "generated_from_live_data",
            "live_atomic_redis_snapshot_observed",
            "live_redis_readback_observed",
            "production_receipt",
            "production_wiring_observed",
            "publication_calls",
            "raw_update_included",
            "redis_server_calls",
            "source_acknowledged",
            "synthetic_inputs_only",
            "telegram_identifiers_included",
        }),
    )
    if not _strict_contract_equal(boundary, {
        "contract_validation_only": True,
        "credentials_included": False,
        "deterministic": True,
        "environment_reads": False,
        "external_calls": False,
        "generated_from_live_data": False,
        "live_atomic_redis_snapshot_observed": False,
        "live_redis_readback_observed": False,
        "production_receipt": False,
        "production_wiring_observed": False,
        "publication_calls": False,
        "raw_update_included": False,
        "redis_server_calls": False,
        "source_acknowledged": False,
        "synthetic_inputs_only": True,
        "telegram_identifiers_included": False,
    }):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    if manifest["source_files"] != lock["source_contract_files"]:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    if type(manifest["fixtures"]) is not list or len(manifest["fixtures"]) != 3:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    for name, record, lock_record in zip(
        _FIXTURE_NAMES,
        manifest["fixtures"],
        lock["fixtures"],
    ):
        item = _require_exact_keys(
            record,
            frozenset({
                "bytes",
                "fixture_name",
                "outcome_counts",
                "path",
                "promotion_ref",
                "sanitized_projection_count",
                "sha256",
                "source_commit_ref",
                "transport_updates_observed",
            }),
        )
        if (
            item["fixture_name"] != name
            or item["path"] != f"{name}.json"
            or {
                "bytes": item["bytes"],
                "path": item["path"],
                "sha256": item["sha256"],
            }
            != lock_record
        ):
            raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
        _require_bytes(item["bytes"], maximum=_MAX_INPUT_BYTES)
        _require_sha256(item["sha256"])
        _require_ref(item["promotion_ref"], prefix="promotion:")
        _require_ref(item["source_commit_ref"], prefix="commit:")
        counts = _validate_outcome_counts(item["outcome_counts"])
        observed = item["transport_updates_observed"]
        emitted = item["sanitized_projection_count"]
        if (
            type(observed) is not int
            or not 1 <= observed <= 100
            or type(emitted) is not int
            or not 1 <= emitted <= 100
            or sum(counts.values()) != observed
            or counts["emitted"] != emitted
        ):
            raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    return manifest


def _validate_fixture_provenance(value: object) -> None:
    provenance = _require_exact_keys(
        value,
        frozenset({
            "canonical_json",
            "contract_validation_only",
            "credentials_included",
            "digest_scheme",
            "environment_reads",
            "event_builder",
            "event_schema_version",
            "external_calls",
            "generated_from_live_data",
            "live_atomic_redis_snapshot_observed",
            "live_redis_readback_observed",
            "production_receipt",
            "production_wiring_observed",
            "projection_builder",
            "projection_schema_version",
            "promotion_builder",
            "promotion_schema_version",
            "publication_calls",
            "raw_update_included",
            "redis_server_calls",
            "source_acknowledged",
            "source_validator",
            "synthetic_inputs_only",
            "telegram_identifiers_included",
        }),
    )
    if not _strict_contract_equal(provenance, {
        "canonical_json": "utf8-sort-keys-compact-ensure-ascii-false",
        "contract_validation_only": True,
        "credentials_included": False,
        "digest_scheme": "hmac-sha256-v2",
        "environment_reads": False,
        "event_builder": "community.gtm_v2_promoter.build_v2_outbox_event",
        "event_schema_version": "coineasy-telegram-owner-outbox-event@2",
        "external_calls": False,
        "generated_from_live_data": False,
        "live_atomic_redis_snapshot_observed": False,
        "live_redis_readback_observed": False,
        "production_receipt": False,
        "production_wiring_observed": False,
        "projection_builder": (
            "community.gtm_projection.build_squid_telegram_owner_projection_v2"
        ),
        "projection_schema_version": "coineasy-telegram-owner-projection@2",
        "promotion_builder": "community.gtm_v2_promoter._promotion_subject",
        "promotion_schema_version": (
            "coineasy-telegram-owner-sanitized-promotion-subject@1"
        ),
        "publication_calls": False,
        "raw_update_included": False,
        "redis_server_calls": False,
        "source_acknowledged": False,
        "source_validator": "community.gtm_v2_promoter._validate_source_contract",
        "synthetic_inputs_only": True,
        "telegram_identifiers_included": False,
    }):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")


def _validate_fixture(
    payload: dict[str, object],
    *,
    fixture_name: str,
    manifest_record: dict[str, object],
) -> dict[str, object]:
    fixture = _require_exact_keys(
        payload,
        frozenset({
            "expected",
            "fixture_name",
            "generated_by",
            "provenance",
            "purpose",
            "reader_snapshot",
            "scenario_epoch",
            "schema_version",
        }),
    )
    if (
        fixture["schema_version"] != "coineasy-telegram-v2-golden-fixture@1"
        or fixture["fixture_name"] != fixture_name
        or fixture["generated_by"] != "community.gtm_v2_golden@1"
        or fixture["purpose"] != "offline_consumer_contract_regression"
        or fixture["scenario_epoch"] != _SCENARIO_EPOCH
    ):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    _validate_fixture_provenance(fixture["provenance"])

    expected = _require_exact_keys(
        fixture["expected"],
        frozenset({
            "evidence_sha256",
            "outcome_counts",
            "promotion_ref",
            "sanitized_projection_count",
            "selected_event_ref",
            "selected_event_sha256",
            "selected_projection_sha256",
            "selected_question_ref",
            "selected_source_projection_ordinal",
            "selected_stream_id",
            "source_commit_ref",
            "source_gate_sha256",
            "source_stage_sha256",
            "transport_updates_observed",
        }),
    )
    snapshot = _require_exact_keys(
        fixture["reader_snapshot"],
        frozenset({
            "atomic_snapshot",
            "automatic_publication",
            "current_event_index_value",
            "database_calls",
            "external_calls",
            "intake_marker_json",
            "mode",
            "new_telegram_consumer",
            "owner_private_stage_included",
            "promotion_marker_json",
            "provider_calls",
            "publication_calls",
            "raw_update_included",
            "read_only_projection",
            "reader_policy",
            "sanitized_gate_json",
            "schema_version",
            "source_promotion_index_value",
            "source_stream_key",
            "stream_id",
            "stream_row",
            "telegram_identifiers_included",
        }),
    )
    eligible = read_eligible_telegram_v2_event(snapshot)
    event = eligible.event
    counts = _validate_outcome_counts(expected["outcome_counts"])
    evidence = _require_exact_keys(
        expected["evidence_sha256"],
        frozenset({
            "current_event_index_value",
            "intake_marker_json",
            "promotion_marker_json",
            "sanitized_gate_json",
            "source_promotion_index_value",
            "stream_row_event_json",
        }),
    )
    stream_row = _require_exact_keys(
        snapshot["stream_row"],
        frozenset({
            "event_json",
            "event_sha256",
            "idempotency_key",
            "projection_sha256",
            "schema_version",
        }),
    )
    expected_evidence = {
        "current_event_index_value": _sha256_text(
            snapshot["current_event_index_value"]
        ),
        "intake_marker_json": _sha256_text(snapshot["intake_marker_json"]),
        "promotion_marker_json": _sha256_text(
            snapshot["promotion_marker_json"]
        ),
        "sanitized_gate_json": _sha256_text(snapshot["sanitized_gate_json"]),
        "source_promotion_index_value": _sha256_text(
            snapshot["source_promotion_index_value"]
        ),
        "stream_row_event_json": _sha256_text(stream_row["event_json"]),
    }
    if evidence != expected_evidence:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    expected_projection = {
        "outcome_counts": eligible.outcome_counts.model_dump(mode="json"),
        "promotion_ref": eligible.promotion_ref,
        "sanitized_projection_count": eligible.sanitized_projection_count,
        "selected_event_ref": event.event_ref,
        "selected_event_sha256": eligible.event_sha256,
        "selected_projection_sha256": event.projection_sha256,
        "selected_question_ref": event.projection.question_ref,
        "selected_source_projection_ordinal": event.source_projection_ordinal,
        "selected_stream_id": eligible.stream_id,
        "source_commit_ref": event.source_commit_ref,
        "source_gate_sha256": event.source_gate_sha256,
        "source_stage_sha256": event.source_stage_sha256,
        "transport_updates_observed": eligible.transport_updates_observed,
    }
    comparable_expected = {
        key: value
        for key, value in expected.items()
        if key != "evidence_sha256"
    }
    if comparable_expected != expected_projection or counts != expected_projection[
        "outcome_counts"
    ]:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    manifest_projection = {
        "outcome_counts": manifest_record["outcome_counts"],
        "promotion_ref": manifest_record["promotion_ref"],
        "sanitized_projection_count": manifest_record[
            "sanitized_projection_count"
        ],
        "source_commit_ref": manifest_record["source_commit_ref"],
        "transport_updates_observed": manifest_record[
            "transport_updates_observed"
        ],
    }
    if not _strict_contract_equal(manifest_projection, {
        key: expected_projection[key]
        for key in manifest_projection
    }):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    return snapshot


def _verify_vendor_fixture(
    fixture_path: Path,
    lock_path: Path,
) -> _VerifiedVendorFixture:
    if fixture_path.is_symlink() or lock_path.is_symlink():
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    if lock_path.name != "LOCK.json":
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    if fixture_path.parent.resolve() != lock_path.parent.resolve():
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")

    lock_raw = _read_raw(lock_path, maximum=_MAX_LOCK_BYTES)
    lock_raw_sha256 = hashlib.sha256(lock_raw).hexdigest()
    if lock_raw_sha256 != _LOCK_RAW_SHA256:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    lock = _validate_lock(_parse_json_object(lock_raw, canonical_file=True))
    manifest_record = lock["manifest"]
    manifest_path = lock_path.parent / manifest_record["path"]
    manifest_raw = _read_raw(manifest_path, maximum=_MAX_MANIFEST_BYTES)
    if (
        len(manifest_raw) != manifest_record["bytes"]
        or _sha256_text(manifest_raw.decode("utf-8"))
        != manifest_record["sha256"]
    ):
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    manifest = _validate_manifest(
        _parse_json_object(manifest_raw, canonical_file=True),
        lock=lock,
    )

    selected: tuple[str, str, dict[str, object]] | None = None
    for fixture_name, lock_record, upstream_record in zip(
        _FIXTURE_NAMES,
        lock["fixtures"],
        manifest["fixtures"],
    ):
        path = lock_path.parent / lock_record["path"]
        raw = _read_raw(path, maximum=_MAX_INPUT_BYTES)
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        if (
            len(raw) != lock_record["bytes"]
            or raw_sha256 != lock_record["sha256"]
        ):
            raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
        payload = _parse_json_object(raw, canonical_file=True)
        snapshot = _validate_fixture(
            payload,
            fixture_name=fixture_name,
            manifest_record=upstream_record,
        )
        if path.resolve() == fixture_path.resolve():
            selected = (fixture_name, raw_sha256, snapshot)
    if selected is None:
        raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
    return _VerifiedVendorFixture(
        fixture_name=selected[0],
        fixture_raw_sha256=selected[1],
        manifest_raw_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        lock_raw_sha256=lock_raw_sha256,
        producer_fixture_provenance_verified=(
            lock["boundary"]["producer_fixture_provenance_verified"] is True
        ),
        reader_snapshot=selected[2],
    )


def build_telegram_v2_shadow_result(
    snapshot: dict[str, object],
) -> dict[str, object]:
    """Derive one deterministic, non-persisted local shadow result."""

    return _build_telegram_v2_shadow_result(snapshot, vendor_fixture=None)


def _build_telegram_v2_shadow_result(
    snapshot: dict[str, object],
    *,
    vendor_fixture: _VerifiedVendorFixture | None,
) -> dict[str, object]:

    if type(snapshot) is not dict:
        raise TypeError("gtm_telegram_v2_shadow_snapshot_dict_required")
    eligible = read_eligible_telegram_v2_event(snapshot)
    triage_item = project_telegram_v2_delivery(eligible)
    prepared_receipt = build_telegram_v2_intake_receipt(snapshot)
    if (
        prepared_receipt.subject.item_ref != triage_item.ref
        or prepared_receipt.subject.item_sha256 != triage_item.item_sha256
        or prepared_receipt.subject.eligibility_sha256
        != triage_item.eligibility_sha256
    ):
        raise ValueError("gtm_telegram_v2_shadow_binding_invalid")

    body: dict[str, object] = {
        "schema_version": _RESULT_SCHEMA,
        "ok": True,
        "mode": "shadow_read_only",
        "client_id": "squid",
        "reader_policy": "coineasy-telegram-v2-strict-reader@1",
        "input_kind": (
            "locked_vendor_fixture"
            if vendor_fixture is not None
            else "asserted_local_v2_snapshot"
        ),
        "input_snapshot_sha256": _sha256_text(_canonical_json(snapshot)),
        "input_fixture_raw_sha256": (
            vendor_fixture.fixture_raw_sha256
            if vendor_fixture is not None
            else None
        ),
        "vendor_fixture_name": (
            vendor_fixture.fixture_name if vendor_fixture is not None else None
        ),
        "vendor_manifest_sha256": (
            vendor_fixture.manifest_raw_sha256
            if vendor_fixture is not None
            else None
        ),
        "vendor_lock_sha256": (
            vendor_fixture.lock_raw_sha256
            if vendor_fixture is not None
            else None
        ),
        "vendor_lock_verified": vendor_fixture is not None,
        "producer_fixture_provenance_verified": (
            vendor_fixture.producer_fixture_provenance_verified
            if vendor_fixture is not None
            else False
        ),
        "live_atomic_redis_snapshot_observed": False,
        "triage_item": triage_item.model_dump(mode="json"),
        "prepared_receipt": prepared_receipt.model_dump(mode="json"),
        "receipt_prepared": True,
        "receipt_persisted": False,
        "exact_readback_observed": False,
        "read_only_projection": True,
        "new_telegram_consumer": False,
        "source_acknowledged": False,
        "external_calls": False,
        "network_calls": False,
        "database_calls": False,
        "redis_calls": False,
        "telegram_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
        "production_wiring_observed": False,
    }
    return {
        **body,
        "shadow_result_sha256": _sha256_text(_canonical_json(body)),
    }


def _failure() -> dict[str, object]:
    return {
        "schema_version": _RESULT_SCHEMA,
        "ok": False,
        "error": "gtm_telegram_v2_shadow_invalid",
        "mode": "shadow_read_only",
        "vendor_lock_verified": False,
        "producer_fixture_provenance_verified": False,
        "live_atomic_redis_snapshot_observed": False,
        "receipt_prepared": False,
        "receipt_persisted": False,
        "exact_readback_observed": False,
        "read_only_projection": True,
        "new_telegram_consumer": False,
        "source_acknowledged": False,
        "external_calls": False,
        "network_calls": False,
        "database_calls": False,
        "redis_calls": False,
        "telegram_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
        "production_wiring_observed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one exact local Telegram v2 reader snapshot and print "
            "a deterministic Korean triage item plus a prepared, unpersisted "
            "intake receipt. No Redis, Telegram, database, network, provider, "
            "publication, or ACK operation is performed."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input",
        type=Path,
        help="Path to one asserted local v2 reader snapshot JSON file.",
    )
    source.add_argument(
        "--fixture",
        type=Path,
        help="Path to one byte-locked vendored golden fixture.",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        help="Required LOCK.json for --fixture; forbidden with --input.",
    )
    args = parser.parse_args()

    try:
        if args.input is not None:
            if args.lock is not None:
                raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
            result = build_telegram_v2_shadow_result(_read_snapshot(args.input))
        else:
            if args.fixture is None or args.lock is None:
                raise ValueError("gtm_telegram_v2_shadow_contract_invalid")
            verified = _verify_vendor_fixture(args.fixture, args.lock)
            result = _build_telegram_v2_shadow_result(
                verified.reader_snapshot,
                vendor_fixture=verified,
            )
        print(_canonical_json(result))
        return 0
    except (OSError, TypeError, ValueError):
        print(_canonical_json(_failure()))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
