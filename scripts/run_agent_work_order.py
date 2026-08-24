from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from core.agent_control import AgentWorkOrder, render_devin_task_packet
from core.agent_control.io import (
    load_verified_agent_work_order,
    resolve_repository_root,
)


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
        order = load_verified_agent_work_order(
            args.input,
            resolve_repository_root(args.repo_root),
        )
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
