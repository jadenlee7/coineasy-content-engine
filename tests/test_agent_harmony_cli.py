from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.agent_control import bind_harmony_signal_payload
from scripts.run_agent_harmony import main


ROOT = Path(__file__).resolve().parents[1]


def _empty_input() -> dict[str, object]:
    return {
        "schema_version": "agent-harmony-input@1",
        "workspace_id": "00000000-0000-4000-8000-000000000001",
        "signals": [],
    }


def _complete_untrusted_squid_input() -> dict[str, object]:
    signals: list[dict[str, object]] = []
    for index, kind in enumerate((
        "quiz_learning",
        "community_demand",
        "official_source",
        "recap_metric",
    ), start=1):
        payload: dict[str, object] = {
            "schema_version": "agent-harmony-signal@1",
            "signal_id": f"00000000-0000-4000-8000-{index:012d}",
            "workspace_id": "00000000-0000-4000-8000-000000000001",
            "client_id": "squid",
            "signal_kind": kind,
            "source_event_id": f"00000000-0000-4000-8000-{100 + index:012d}",
            "producer_principal_id": (
                f"00000000-0000-4000-8000-{200 + index:012d}"
            ),
            "producer_release_sha": "a" * 40,
            "config_sha256": "b" * 64,
            "upstream_receipt_sha256": "c" * 64,
            "observed_at": "2026-08-25T11:00:00Z",
            "expires_at": "2026-08-26T12:00:00Z",
            "evidence_sha256": "d" * 64,
            "topic_codes": ["routing_basics"],
            "raw_messages_included": False,
            "personal_data_included": False,
            "instructions_allowed": False,
            "advisory_only": True,
            "max_cost_microusd": 0,
            "max_external_actions": 0,
            "automatic_publication": False,
        }
        if kind == "quiz_learning":
            payload.update({
                "lane": "quiz_bot",
                "data_classification": "aggregate_anonymous",
                "content_factual_authority": False,
                "attempts": 40,
                "participants": 10,
                "accuracy_basis_points": 4_000,
                "tutorial_priority_basis_points": 8_000,
            })
        elif kind == "community_demand":
            payload.update({
                "lane": "community_ops",
                "data_classification": "aggregate_anonymous",
                "content_factual_authority": False,
                "room_mapping_count": 1,
                "sample_size": 20,
                "demand_score_basis_points": 7_000,
            })
        elif kind == "official_source":
            payload.update({
                "lane": "content_source",
                "data_classification": "public_official",
                "content_factual_authority": True,
                "source_item_id": "00000000-0000-4000-8000-000000000303",
                "source_body_sha256": "e" * 64,
                "source_kind": "x_post_text",
                "source_verified": True,
                "eligible_content_kinds": [
                    "article",
                    "daily_news",
                    "tutorial",
                ],
            })
        else:
            payload.update({
                "lane": "recap",
                "data_classification": "aggregate_anonymous",
                "content_factual_authority": False,
                "period_start": "2026-08-18T11:00:00Z",
                "period_end": "2026-08-25T11:00:00Z",
                "metrics": [{
                    "metric_code": "content_clicks",
                    "unit": "count",
                    "observed": True,
                    "value": 12,
                }],
            })
        signals.append(bind_harmony_signal_payload(payload))
    return {
        "schema_version": "agent-harmony-input@1",
        "workspace_id": "00000000-0000-4000-8000-000000000001",
        "signals": signals,
    }


def test_cli_renders_four_client_snapshot_and_korean_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "harmony.json"
    input_path.write_text(json.dumps(_empty_input()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "run_agent_harmony",
        "--input",
        str(input_path),
        "--clients-dir",
        str(ROOT / "clients"),
        "--observed-at",
        "2026-08-25T12:00:00Z",
        "--snapshot-json",
    ])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["counts"]["clients"] == 4
    assert payload["counts"]["waiting_for_signals"] == 4
    assert payload["counts"]["live_harmony_adapters"] == 0
    assert payload["live_adapters_connected"] is False
    assert payload["execution_authorized"] is False
    assert payload["automatic_publication"] is False

    monkeypatch.setattr(sys, "argv", [
        "run_agent_harmony",
        "--input",
        str(input_path),
        "--clients-dir",
        str(ROOT / "clients"),
        "--observed-at",
        "2026-08-25T12:00:00Z",
        "--dashboard",
    ])
    assert main() == 0
    dashboard = capsys.readouterr().out
    assert sum(line.startswith("## ") for line in dashboard.splitlines()) == 5
    assert "Yellow" in dashboard
    assert "OriginTrail" in dashboard
    assert "Squid" in dashboard
    assert "Babylon" in dashboard
    assert "자동 발행/외부 호출/비용: `OFF / 0 / 0`" in dashboard


def test_cli_rejects_extra_fields_symlinks_and_non_utc_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid = {**_empty_input(), "raw_messages": []}
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")

    def run(path: Path, observed_at: str = "2026-08-25T12:00:00Z") -> dict:
        monkeypatch.setattr(sys, "argv", [
            "run_agent_harmony",
            "--input",
            str(path),
            "--clients-dir",
            str(ROOT / "clients"),
            "--observed-at",
            observed_at,
            "--snapshot-json",
        ])
        assert main() == 2
        return json.loads(capsys.readouterr().out)

    failure = run(invalid_path)
    assert failure["ok"] is False
    assert failure["trust_mode"] == "empty"
    assert failure["caller_identity_trusted"] is False
    assert failure["attestation_required_for_handoff"] is True
    assert failure["runtime_attested_signals"] == 0
    assert failure["handoff_candidates"] == 0
    assert failure["render_only"] is True
    assert failure["portable_trust"] is False
    assert failure["serialized_snapshot_authoritative"] is False
    assert failure["live_adapters_connected"] is False
    assert failure["provider_calls"] is False
    assert failure["publication_calls"] is False
    assert failure["automatic_publication"] is False

    target = tmp_path / "target.json"
    target.write_text(json.dumps(_empty_input()), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    assert run(link)["ok"] is False
    assert run(target, "2026-08-25T16:00:00+04:00")["ok"] is False


def test_cli_complete_claims_still_have_zero_trusted_handoffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "untrusted-complete.json"
    input_path.write_text(
        json.dumps(_complete_untrusted_squid_input()),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", [
        "run_agent_harmony",
        "--input",
        str(input_path),
        "--clients-dir",
        str(ROOT / "clients"),
        "--observed-at",
        "2026-08-25T12:00:00Z",
        "--snapshot-json",
    ])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["trust_mode"] == "empty"
    assert payload["counts"]["input_signal_claims"] == 4
    assert payload["counts"]["runtime_attested_signals"] == 0
    assert payload["counts"]["unattested_signal_claims"] == 4
    assert payload["counts"]["waiting_for_attestation"] == 1
    assert payload["counts"]["ready_for_human_scope_review"] == 0
    squid = next(
        item for item in payload["rounds"] if item["client_id"] == "squid"
    )
    assert squid["handoff"] is None
