from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from core.agent_control import AgentWorkOrder, render_devin_task_packet


def _repository_root(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("agent_work_order_repository_root_invalid")
    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("agent_work_order_repository_root_invalid") from exc
    if not root.is_dir():
        raise ValueError("agent_work_order_repository_root_invalid")
    return root


def _evidence_path(root: Path, uri: str) -> Path:
    candidate = root
    for part in PurePosixPath(uri).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("agent_work_order_evidence_invalid")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("agent_work_order_evidence_invalid") from exc
    if not resolved.is_file():
        raise ValueError("agent_work_order_evidence_invalid")
    return resolved


def _read_order(path: Path, repo_root: Path) -> AgentWorkOrder:
    if path.is_symlink() or not path.is_file():
        raise ValueError("agent_work_order_input_invalid")
    try:
        raw_order = path.read_bytes()
        if len(raw_order) > 128 * 1024:
            raise ValueError("agent_work_order_input_invalid")
        payload = json.loads(raw_order.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("agent_work_order_input_invalid") from exc
    order = AgentWorkOrder.model_validate(payload)
    for evidence in order.evidence:
        evidence_path = _evidence_path(repo_root, evidence.uri)
        evidence_bytes = evidence_path.read_bytes()
        if len(evidence_bytes) > 5 * 1024 * 1024:
            raise ValueError("agent_work_order_evidence_invalid")
        digest = hashlib.sha256(evidence_bytes).hexdigest()
        if digest != evidence.sha256:
            raise ValueError("agent_work_order_evidence_invalid")
    return order


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or render one local CoinEasy agent work order.",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--repo-root", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--render-devin", action="store_true")
    mode.add_argument("--print-schema", action="store_true")
    args = parser.parse_args()

    try:
        if args.print_schema:
            print(json.dumps(
                AgentWorkOrder.model_json_schema(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ))
            return 0
        if args.input is None or args.repo_root is None:
            raise ValueError("agent_work_order_input_invalid")
        order = _read_order(args.input, _repository_root(args.repo_root))
        if args.validate_only:
            print(json.dumps({
                "ok": True,
                "mode": "validate_only",
                "schema_version": order.schema_version,
                "work_order_id": str(order.work_order_id),
                "status": "proposed",
                "owner": order.owner.value,
                "scope_sha256": order.scope_sha256,
                "branch_scope_key": order.branch_scope_key,
                "local_evidence_verified": len(order.evidence),
                "planning_only": True,
                "external_calls": False,
                "database_calls": False,
                "publication_calls": False,
                "provider_calls": False,
            }, separators=(",", ":")))
        else:
            print(render_devin_task_packet(order), end="")
        return 0
    except (OSError, ValueError, ValidationError):
        print(json.dumps({
            "ok": False,
            "error": "agent_work_order_invalid",
            "external_calls": False,
            "database_calls": False,
            "publication_calls": False,
            "provider_calls": False,
        }, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
