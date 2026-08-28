from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts.run_gtm_telegram_v2_shadow import (
    build_telegram_v2_shadow_result,
    main,
)
from tests.test_gtm_intelligence_telegram_v2 import _reader_snapshot


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/run_gtm_telegram_v2_shadow.py"


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _run_cli(
    path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, dict[str, object]]:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_gtm_telegram_v2_shadow", "--input", str(path)],
    )
    exit_code = main()
    output = capsys.readouterr().out
    return exit_code, output, json.loads(output)


def test_shadow_cli_prints_deterministic_korean_triage_and_prepared_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot, _ = _reader_snapshot()
    input_path = tmp_path / "snapshot.json"
    input_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    first_code, first_output, result = _run_cli(
        input_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    second_code, second_output, replay = _run_cli(
        input_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert first_code == second_code == 0
    assert first_output == second_output
    assert result == replay
    assert result["schema_version"] == "coineasy-telegram-v2-shadow-result@1"
    assert result["ok"] is True
    assert result["mode"] == "shadow_read_only"
    assert result["input_kind"] == "asserted_local_v2_snapshot"
    assert result["input_snapshot_sha256"] == hashlib.sha256(
        _canonical(snapshot).encode("utf-8")
    ).hexdigest()
    assert result["producer_fixture_provenance_verified"] is False
    assert result["live_atomic_redis_snapshot_observed"] is False
    triage = result["triage_item"]
    receipt = result["prepared_receipt"]
    assert isinstance(triage, dict)
    assert isinstance(receipt, dict)
    assert triage["event_type"] == "telegram.triage.v2"
    assert "커뮤니티 질문" in str(triage["title_ko"])
    assert "운영자" in str(triage["details"]["next_action_ko"])
    assert receipt["subject"]["item_ref"] == triage["ref"]
    assert receipt["subject"]["item_sha256"] == hashlib.sha256(
        _canonical(triage).encode("utf-8")
    ).hexdigest()
    assert receipt["source_acknowledged"] is False
    assert receipt["durability_scope"] == "process_memory_only"

    body = dict(result)
    result_sha = body.pop("shadow_result_sha256")
    assert result_sha == hashlib.sha256(
        _canonical(body).encode("utf-8")
    ).hexdigest()
    assert result["receipt_prepared"] is True
    assert result["receipt_persisted"] is False
    assert result["exact_readback_observed"] is False
    for field in (
        "external_calls",
        "network_calls",
        "database_calls",
        "redis_calls",
        "telegram_calls",
        "provider_calls",
        "publication_calls",
        "automatic_publication",
        "production_wiring_observed",
        "source_acknowledged",
        "new_telegram_consumer",
    ):
        assert result[field] is False


def test_shadow_builder_rejects_tampered_marker_index_binding() -> None:
    snapshot, _ = _reader_snapshot()
    tampered = copy.deepcopy(snapshot)
    tampered["current_event_index_value"] += " "

    with pytest.raises(ValueError):
        build_telegram_v2_shadow_result(tampered)


@pytest.mark.parametrize("invalid_kind", ("tampered", "duplicate", "symlink"))
def test_shadow_cli_fails_closed_without_echoing_input(
    invalid_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot, _ = _reader_snapshot(
        projection_overrides={
            "question_summary_ko": "절대 출력하면 안 되는 실패 경로 카나리입니다.",
        },
    )
    target = tmp_path / "target.json"
    if invalid_kind == "duplicate":
        raw = json.dumps(snapshot, ensure_ascii=False)
        raw = raw.replace(
            '"schema_version":',
            '"schema_version":"duplicate","schema_version":',
            1,
        )
        target.write_text(raw, encoding="utf-8")
        input_path = target
    else:
        if invalid_kind == "tampered":
            snapshot["source_promotion_index_value"] = (
                "promotion:" + ("0" * 64)
            )
        target.write_text(
            json.dumps(snapshot, ensure_ascii=False),
            encoding="utf-8",
        )
        if invalid_kind == "symlink":
            input_path = tmp_path / "linked.json"
            input_path.symlink_to(target)
        else:
            input_path = target

    code, output, failure = _run_cli(
        input_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert code == 2
    assert failure == {
        "schema_version": "coineasy-telegram-v2-shadow-result@1",
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
    assert "절대 출력하면 안 되는" not in output
    assert "promotion:" not in output
    assert "question:" not in output


def test_shadow_cli_has_no_live_adapter_or_write_surface() -> None:
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    called_attributes: set[str] = set()
    imported_symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
            imported_symbols.update(alias.name for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        ):
            called_attributes.add(node.func.attr)

    assert imported.isdisjoint({
        "aiohttp",
        "boto3",
        "httpx",
        "os",
        "psycopg",
        "psycopg2",
        "redis",
        "requests",
        "socket",
        "subprocess",
        "supabase",
        "telegram",
        "urllib",
    })
    assert imported_symbols.isdisjoint({
        "InMemoryTelegramV2ReceiptStore",
        "TelegramV2IntakeReceiptRepository",
    })
    assert called_attributes.isdisjoint({
        "ack",
        "connect",
        "execute",
        "open",
        "publish",
        "put",
        "send",
        "set",
        "write_bytes",
        "write_text",
        "xack",
        "xadd",
    })
