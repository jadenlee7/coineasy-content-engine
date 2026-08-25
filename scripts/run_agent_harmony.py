from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from core.agent_control import (
    HarmonyInput,
    build_harmony_snapshot,
    load_harmony_client_profiles,
    render_harmony_dashboard,
)


_MAX_INPUT_BYTES = 2 * 1024 * 1024


def _observed_at(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("agent_harmony_observed_at_invalid") from exc


def _load_input(path: Path) -> HarmonyInput:
    if path.is_symlink() or not path.is_file():
        raise ValueError("agent_harmony_input_invalid")
    try:
        if path.stat().st_size > _MAX_INPUT_BYTES:
            raise ValueError("agent_harmony_input_invalid")
        raw = path.read_bytes()
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("agent_harmony_input_invalid")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("agent_harmony_input_invalid") from exc
    return HarmonyInput.model_validate(payload)


def _failure() -> dict[str, object]:
    return {
        "ok": False,
        "error": "agent_harmony_input_invalid",
        "trust_mode": "empty",
        "caller_identity_trusted": False,
        "attestation_required_for_handoff": True,
        "runtime_attested_signals": 0,
        "handoff_candidates": 0,
        "planning_only": True,
        "render_only": True,
        "portable_trust": False,
        "serialized_snapshot_authoritative": False,
        "live_adapters_connected": False,
        "execution_authorized": False,
        "external_calls": False,
        "database_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one sanitized four-client Harmony snapshot and render "
            "the bounded, read-only collaboration dashboard."
        ),
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--clients-dir", type=Path, default=Path("clients"))
    parser.add_argument(
        "--observed-at",
        required=True,
        help="An explicit UTC, whole-second ISO-8601 timestamp.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot-json", action="store_true")
    mode.add_argument("--dashboard", action="store_true")
    args = parser.parse_args()

    try:
        snapshot = build_harmony_snapshot(
            _load_input(args.input),
            load_harmony_client_profiles(args.clients_dir),
            observed_at=_observed_at(args.observed_at),
        )
        if args.dashboard:
            print(render_harmony_dashboard(snapshot), end="")
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
