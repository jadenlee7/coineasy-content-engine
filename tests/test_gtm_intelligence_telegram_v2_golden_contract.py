from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Callable

import pytest

from scripts.run_gtm_telegram_v2_shadow import (
    _validate_fixture_provenance,
    _validate_lock,
    _validate_manifest,
    main,
)


ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = (
    ROOT
    / "tests"
    / "fixtures"
    / "vendor"
    / "coineasydaily"
    / "telegram_v2"
    / "v1"
)
LOCK_PATH = VENDOR_DIR / "LOCK.json"
FIXTURE_NAMES = (
    "one_emitted",
    "hundred_emitted",
    "hundred_mixed",
)
FIXTURE_SHA256 = {
    "one_emitted": (
        "25da0ff96764ac5040a14f1d25b15e12dbb6bc4680fe8260815b6f3c13fb07cf"
    ),
    "hundred_emitted": (
        "8241fae57a5c4efa985a08b69f52da7287f0964a4651a6372bd39dff29d59e45"
    ),
    "hundred_mixed": (
        "056b9c35daabb205c6fa4fa42d74529d5d4dd4668a08bc44bbea172a973dc32b"
    ),
}
MANIFEST_SHA256 = (
    "8f683690a9e11ae0d0f9a83a44dc58a48620216b11bb6c7d9a7d8edf824231ab"
)
LOCK_SHA256 = (
    "76547ac2bef33bff97233c191cc8cdcaecae5212cbea8968ecffe19f7d98e178"
)
EXPECTED = {
    "one_emitted": (1, 1, {"emitted": 1, "tombstoned": 0, "not_applicable": 0}),
    "hundred_emitted": (
        100,
        100,
        {"emitted": 100, "tombstoned": 0, "not_applicable": 0},
    ),
    "hundred_mixed": (
        100,
        34,
        {"emitted": 34, "tombstoned": 33, "not_applicable": 33},
    ),
}


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _run_fixture_cli(
    fixture_path: Path,
    lock_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, dict[str, object]]:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_gtm_telegram_v2_shadow",
            "--fixture",
            str(fixture_path),
            "--lock",
            str(lock_path),
        ],
    )
    exit_code = main()
    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert type(parsed) is dict
    return exit_code, output, parsed


def _copy_vendor(tmp_path: Path) -> Path:
    copied = tmp_path / "vendor"
    shutil.copytree(VENDOR_DIR, copied)
    return copied


def _mutate_and_rebind(
    vendor_dir: Path,
    fixture_name: str,
    mutate: Callable[[dict[str, object]], None],
) -> Path:
    fixture_path = vendor_dir / f"{fixture_name}.json"
    fixture = _load(fixture_path)
    mutate(fixture)
    fixture_raw = _canonical_bytes(fixture)
    fixture_path.write_bytes(fixture_raw)

    manifest_path = vendor_dir / "manifest.json"
    manifest = _load(manifest_path)
    manifest_records = manifest["fixtures"]
    assert type(manifest_records) is list
    manifest_record = next(
        item
        for item in manifest_records
        if item["fixture_name"] == fixture_name
    )
    manifest_record["bytes"] = len(fixture_raw)
    manifest_record["sha256"] = _sha(fixture_raw)
    manifest_raw = _canonical_bytes(manifest)
    manifest_path.write_bytes(manifest_raw)

    lock_path = vendor_dir / "LOCK.json"
    lock = _load(lock_path)
    lock_records = lock["fixtures"]
    assert type(lock_records) is list
    lock_record = next(
        item
        for item in lock_records
        if item["path"] == f"{fixture_name}.json"
    )
    lock_record["bytes"] = len(fixture_raw)
    lock_record["sha256"] = _sha(fixture_raw)
    lock["manifest"]["bytes"] = len(manifest_raw)
    lock["manifest"]["sha256"] = _sha(manifest_raw)
    lock_path.write_bytes(_canonical_bytes(lock))
    return fixture_path


@pytest.mark.parametrize("fixture_name", FIXTURE_NAMES)
def test_locked_producer_fixture_replays_deterministically(
    fixture_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture_path = VENDOR_DIR / f"{fixture_name}.json"
    first_code, first_output, result = _run_fixture_cli(
        fixture_path,
        LOCK_PATH,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    second_code, second_output, replay = _run_fixture_cli(
        fixture_path,
        LOCK_PATH,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert first_code == second_code == 0
    assert first_output == second_output
    assert result == replay
    assert result["ok"] is True
    assert result["input_kind"] == "locked_vendor_fixture"
    assert result["vendor_fixture_name"] == fixture_name
    assert result["input_fixture_raw_sha256"] == FIXTURE_SHA256[fixture_name]
    assert result["vendor_manifest_sha256"] == MANIFEST_SHA256
    assert result["vendor_lock_verified"] is True
    assert result["producer_fixture_provenance_verified"] is True
    assert result["live_atomic_redis_snapshot_observed"] is False
    assert result["receipt_prepared"] is True
    assert result["receipt_persisted"] is False
    assert result["exact_readback_observed"] is False
    assert result["source_acknowledged"] is False
    assert result["production_wiring_observed"] is False

    observed, emitted, counts = EXPECTED[fixture_name]
    subject = result["prepared_receipt"]["subject"]
    assert subject["transport_updates_observed"] == observed
    assert subject["sanitized_projection_count"] == emitted
    assert subject["outcome_counts"] == counts
    assert subject["source_projection_ordinal"] == (
        0 if fixture_name == "one_emitted" else 99
    )


def test_vendor_lock_and_manifest_bind_exact_checked_in_bytes() -> None:
    lock_raw = LOCK_PATH.read_bytes()
    lock = _load(LOCK_PATH)
    assert lock_raw == _canonical_bytes(lock)
    assert _sha(lock_raw) == LOCK_SHA256
    assert lock["upstream"] == {
        "base_head_sha": "6f4a137b889a8d159a64d97924bb0ffef784aae9",
        "contract_source_state": "merged_reviewed",
        "fixture_directory": "tests/fixtures/gtm_v2_golden",
        "repository": "coineasydaily",
        "reviewed_producer_commit_sha": (
            "0ffce811d2cad55bc7083d20c055801687927657"
        ),
    }

    manifest_path = VENDOR_DIR / "manifest.json"
    manifest_raw = manifest_path.read_bytes()
    manifest = _load(manifest_path)
    assert manifest_raw == _canonical_bytes(manifest)
    assert _sha(manifest_raw) == MANIFEST_SHA256
    assert lock["manifest"] == {
        "bytes": len(manifest_raw),
        "path": "manifest.json",
        "sha256": MANIFEST_SHA256,
    }
    assert manifest["source_files"] == lock["source_contract_files"]

    for fixture_name, lock_record, manifest_record in zip(
        FIXTURE_NAMES,
        lock["fixtures"],
        manifest["fixtures"],
    ):
        raw = (VENDOR_DIR / f"{fixture_name}.json").read_bytes()
        assert raw == _canonical_bytes(json.loads(raw))
        assert lock_record == {
            "bytes": len(raw),
            "path": f"{fixture_name}.json",
            "sha256": FIXTURE_SHA256[fixture_name],
        }
        assert manifest_record["bytes"] == len(raw)
        assert manifest_record["sha256"] == FIXTURE_SHA256[fixture_name]


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    (
        (
            "upstream",
            "base_head_sha",
            "c72daad6b30c64eaeea17dc706c32a40f812a124",
        ),
        ("upstream", "contract_source_state", "dirty_uncommitted"),
        ("upstream", "reviewed_producer_commit_sha", None),
        (
            "upstream",
            "reviewed_producer_commit_sha",
            "8667eb3f6d3e5ecc024c3caa3cf7ad5ed8428265",
        ),
        ("boundary", "producer_fixture_provenance_verified", False),
        ("boundary", "network_reads_required", 0),
        ("producer_fixture_generator", "sha256", "0" * 64),
    ),
)
def test_vendor_lock_rejects_unreviewed_or_unpinned_provenance(
    section: str,
    field: str,
    replacement: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vendor_dir = _copy_vendor(tmp_path)
    lock_path = vendor_dir / "LOCK.json"
    lock = _load(lock_path)
    lock[section][field] = replacement
    lock_path.write_bytes(_canonical_bytes(lock))

    code, output, result = _run_fixture_cli(
        vendor_dir / "one_emitted.json",
        lock_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert code == 2
    assert result["vendor_lock_verified"] is False
    assert result["producer_fixture_provenance_verified"] is False
    assert str(replacement) not in output


@pytest.mark.parametrize("layer", ("lock", "manifest", "fixture"))
def test_contract_boolean_literals_reject_numeric_coercion(layer: str) -> None:
    lock = _load(LOCK_PATH)
    if layer == "lock":
        lock["boundary"]["network_reads_required"] = 0
        with pytest.raises(ValueError):
            _validate_lock(lock)
        return

    manifest = _load(VENDOR_DIR / "manifest.json")
    if layer == "manifest":
        manifest["boundary"]["external_calls"] = 0
        with pytest.raises(ValueError):
            _validate_manifest(manifest, lock=lock)
        return

    fixture = _load(VENDOR_DIR / "one_emitted.json")
    fixture["provenance"]["external_calls"] = 0
    with pytest.raises(ValueError):
        _validate_fixture_provenance(fixture["provenance"])


def test_one_byte_vendor_drift_fails_before_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vendor_dir = _copy_vendor(tmp_path)
    fixture_path = vendor_dir / "one_emitted.json"
    fixture_path.write_bytes(fixture_path.read_bytes()[:-1] + b" \n")

    code, output, result = _run_fixture_cli(
        fixture_path,
        vendor_dir / "LOCK.json",
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert code == 2
    assert result["ok"] is False
    assert result["vendor_lock_verified"] is False
    assert "question:" not in output
    assert "promotion:" not in output


def test_manifest_lock_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vendor_dir = _copy_vendor(tmp_path)
    lock_path = vendor_dir / "LOCK.json"
    lock = _load(lock_path)
    lock["manifest"]["sha256"] = "0" * 64
    lock_path.write_bytes(_canonical_bytes(lock))

    code, _, result = _run_fixture_cli(
        vendor_dir / "one_emitted.json",
        lock_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert code == 2
    assert result["vendor_lock_verified"] is False


def test_cross_fixture_snapshot_splice_fails_after_hash_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vendor_dir = _copy_vendor(tmp_path)
    foreign_snapshot = copy.deepcopy(
        _load(vendor_dir / "hundred_mixed.json")["reader_snapshot"]
    )
    fixture_path = _mutate_and_rebind(
        vendor_dir,
        "one_emitted",
        lambda fixture: fixture.__setitem__(
            "reader_snapshot",
            foreign_snapshot,
        ),
    )

    code, output, result = _run_fixture_cli(
        fixture_path,
        vendor_dir / "LOCK.json",
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert code == 2
    assert result["vendor_lock_verified"] is False
    assert "question:" not in output


@pytest.mark.parametrize("mutation", ("reorder", "truncate"))
def test_hundred_member_manifest_mutation_fails_after_hash_rebinding(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vendor_dir = _copy_vendor(tmp_path)

    def mutate(fixture: dict[str, object]) -> None:
        snapshot = fixture["reader_snapshot"]
        marker = json.loads(snapshot["promotion_marker_json"])
        members = marker["ordered_members"]
        if mutation == "reorder":
            members[0], members[1] = members[1], members[0]
        else:
            members.pop()
        snapshot["promotion_marker_json"] = json.dumps(
            marker,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    fixture_path = _mutate_and_rebind(
        vendor_dir,
        "hundred_emitted",
        mutate,
    )
    code, _, result = _run_fixture_cli(
        fixture_path,
        vendor_dir / "LOCK.json",
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert code == 2
    assert result["vendor_lock_verified"] is False


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
def test_each_golden_eligibility_object_fails_when_tampered_and_rebound(
    field: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vendor_dir = _copy_vendor(tmp_path)

    def mutate(fixture: dict[str, object]) -> None:
        snapshot = fixture["reader_snapshot"]
        if field == "stream_row":
            snapshot[field]["event_sha256"] = "0" * 64
        else:
            snapshot[field] += " "

    fixture_path = _mutate_and_rebind(
        vendor_dir,
        "one_emitted",
        mutate,
    )
    code, _, result = _run_fixture_cli(
        fixture_path,
        vendor_dir / "LOCK.json",
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert code == 2
    assert result["vendor_lock_verified"] is False


def test_v1_confusion_and_privacy_canary_fail_without_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vendor_dir = _copy_vendor(tmp_path)
    canary = "비공개 문의는 user@example.invalid로 남겨 주세요."

    def mutate(fixture: dict[str, object]) -> None:
        snapshot = fixture["reader_snapshot"]
        event = json.loads(snapshot["stream_row"]["event_json"])
        event["projection"]["schema_version"] = (
            "coineasy-telegram-owner-projection@1"
        )
        event["projection"]["question_summary_ko"] = canary
        snapshot["stream_row"]["event_json"] = json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    fixture_path = _mutate_and_rebind(
        vendor_dir,
        "one_emitted",
        mutate,
    )
    code, output, result = _run_fixture_cli(
        fixture_path,
        vendor_dir / "LOCK.json",
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert code == 2
    assert result["vendor_lock_verified"] is False
    assert canary not in output
    assert "example.invalid" not in output


def test_vendored_bytes_are_synthetic_private_safe_and_hermetic() -> None:
    forbidden = (
        '"telegram_update_id"',
        '"chat_id"',
        '"user_id"',
        '"message_id"',
        '"bot_token"',
        '"raw_text"',
        '"private_payload"',
        "TELEGRAM_BOT_TOKEN",
        "github_pat_",
        "Bearer ",
        "/Users/seunghyunlee/coineasydaily",
    )
    for path in sorted(VENDOR_DIR.glob("*.json")):
        raw = path.read_text(encoding="utf-8")
        assert all(candidate not in raw for candidate in forbidden)

    script = (
        ROOT / "scripts" / "run_gtm_telegram_v2_shadow.py"
    ).read_text(encoding="utf-8")
    assert "/Users/seunghyunlee/coineasydaily" not in script
    assert "from community.gtm_v2_golden" not in script
    assert "import community.gtm_v2_golden" not in script
