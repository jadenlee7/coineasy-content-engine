from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from core.agent_control import (
    DurableAgentWorkOrderRow,
    build_durable_company_snapshot,
    render_durable_company_dashboard,
)


_MAX_INPUT_BYTES = 2 * 1024 * 1024


def _observed_at(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("agent_company_observed_at_invalid") from exc


def _load_rows(path: Path) -> list[DurableAgentWorkOrderRow]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("agent_company_input_invalid")
    try:
        if path.stat().st_size > _MAX_INPUT_BYTES:
            raise ValueError("agent_company_input_invalid")
        raw = path.read_bytes()
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("agent_company_input_invalid")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("agent_company_input_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "work_orders",
    }:
        raise ValueError("agent_company_input_invalid")
    if payload["schema_version"] != "agent-company-ledger-snapshot@1":
        raise ValueError("agent_company_input_invalid")
    raw_rows = payload["work_orders"]
    if not isinstance(raw_rows, list) or len(raw_rows) > 1_000:
        raise ValueError("agent_company_input_invalid")
    return [DurableAgentWorkOrderRow.model_validate(row) for row in raw_rows]


def _failure() -> dict[str, object]:
    return {
        "ok": False,
        "error": "agent_company_dashboard_invalid",
        "read_only_projection": True,
        "external_calls": False,
        "database_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a saved, sanitized Agent Work Order adapter snapshot and "
            "render the read-only CoinEasy company dashboard."
        ),
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--observed-at",
        required=True,
        help="An explicit timezone-aware, whole-second ISO-8601 timestamp.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot-json", action="store_true")
    mode.add_argument("--dashboard", action="store_true")
    args = parser.parse_args()

    try:
        snapshot = build_durable_company_snapshot(
            _load_rows(args.input),
            observed_at=_observed_at(args.observed_at),
        )
        if args.dashboard:
            print(render_durable_company_dashboard(snapshot), end="")
        else:
            print(json.dumps(
                {"ok": True, **snapshot.as_payload()},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ))
        return 0
    except (OSError, ValueError, ValidationError):
        print(json.dumps(
            _failure(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
