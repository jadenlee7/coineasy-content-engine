from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.agent_control import AgentWorkOrder
from scripts.run_agent_company_dashboard import main


ROOT = Path(__file__).resolve().parents[1]


def _ledger_snapshot() -> dict[str, object]:
    scope = json.loads(
        (ROOT / "examples/agent-work-order-devin-preview.json").read_text(
            encoding="utf-8",
        )
    )
    order = AgentWorkOrder.model_validate(scope)
    return {
        "schema_version": "agent-company-ledger-snapshot@1",
        "work_orders": [{
            "schema_version": "agent-work-order-row@1",
            "work_order_id": str(order.work_order_id),
            "scope_sha256": order.scope_sha256,
            "branch_scope_key": order.branch_scope_key,
            "owner": order.owner.value,
            "reviewer": order.reviewer.value,
            "state": "proposed",
            "work_order": order.model_dump(mode="json"),
            "cost_observation": "unobserved",
            "observed_cost_microusd": None,
            "authorization_receipt": None,
            "dispatch_packet": None,
            "dispatch_status": None,
            "result_receipt": None,
            "verification_receipt": None,
            "operator_decision": None,
            "completion_receipt": None,
        }],
    }


def test_cli_renders_read_only_snapshot_and_korean_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "ledger.json"
    input_path.write_text(
        json.dumps(_ledger_snapshot(), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", [
        "run_agent_company_dashboard",
        "--input",
        str(input_path),
        "--observed-at",
        "2026-08-25T12:00:00Z",
        "--snapshot-json",
    ])

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["counts"]["total"] == 1
    assert payload["counts"]["unobserved_cost_rows"] == 1
    assert payload["read_only_projection"] is True
    assert payload["external_calls"] is False
    assert payload["database_calls"] is False
    assert payload["provider_calls"] is False
    assert payload["publication_calls"] is False
    assert payload["automatic_publication"] is False

    monkeypatch.setattr(sys, "argv", [
        "run_agent_company_dashboard",
        "--input",
        str(input_path),
        "--observed-at",
        "2026-08-25T12:00:00Z",
        "--dashboard",
    ])
    assert main() == 0
    dashboard = capsys.readouterr().out
    assert dashboard.count("## ") == 5
    assert "## 4. 독립 검증 · 대표 승인함" in dashboard
    assert "비용 미관측: 1건 (0으로 환산하지 않음)" in dashboard
    assert "자동 발행: `OFF`" in dashboard


def test_cli_fails_closed_for_digest_drift_or_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _ledger_snapshot()
    rows = payload["work_orders"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["scope_sha256"] = "f" * 64
    input_path = tmp_path / "drift.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "run_agent_company_dashboard",
        "--input",
        str(input_path),
        "--observed-at",
        "2026-08-25T12:00:00Z",
        "--snapshot-json",
    ])

    assert main() == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure == {
        "automatic_publication": False,
        "database_calls": False,
        "error": "agent_company_dashboard_invalid",
        "external_calls": False,
        "ok": False,
        "provider_calls": False,
        "publication_calls": False,
        "read_only_projection": True,
    }

    target = tmp_path / "target.json"
    target.write_text(json.dumps(_ledger_snapshot()), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    monkeypatch.setattr(sys, "argv", [
        "run_agent_company_dashboard",
        "--input",
        str(link),
        "--observed-at",
        "2026-08-25T12:00:00Z",
        "--snapshot-json",
    ])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
