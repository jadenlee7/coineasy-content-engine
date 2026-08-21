from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from core.agent_control import (
    build_control_room_snapshot,
    load_verified_agent_work_order,
    render_buzz_dry_run_receipt,
    render_operator_dashboard,
    render_owner_planning_packet,
    render_reviewer_planning_packet,
    resolve_repository_root,
)


def _observed_at(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("agent_control_room_observed_at_invalid") from exc


def _failure() -> dict[str, object]:
    return {
        "ok": False,
        "error": "agent_control_room_invalid",
        "planning_only": True,
        "dry_run": True,
        "execution_authorized": False,
        "external_calls": False,
        "database_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render one local-only CoinEasy control-room projection.",
    )
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument(
        "--observed-at",
        required=True,
        help="An explicit timezone-aware, whole-second ISO-8601 timestamp.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot-json", action="store_true")
    mode.add_argument("--dashboard", action="store_true")
    mode.add_argument("--packets", action="store_true")
    args = parser.parse_args()

    try:
        if not 1 <= len(args.input) <= 32:
            raise ValueError("agent_control_room_order_count_invalid")
        root = resolve_repository_root(args.repo_root)
        orders = [
            load_verified_agent_work_order(path, root)
            for path in args.input
        ]
        snapshot = build_control_room_snapshot(
            orders,
            observed_at=_observed_at(args.observed_at),
        )
        if args.snapshot_json:
            print(json.dumps(
                {"ok": True, **snapshot.as_payload()},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ))
        elif args.dashboard:
            print(render_operator_dashboard(snapshot), end="")
        else:
            routes = {
                str(route.work_order_id): route for route in snapshot.routes
            }
            packets = []
            for order in sorted(orders, key=lambda item: str(item.work_order_id)):
                route = routes[str(order.work_order_id)]
                packets.append({
                    "work_order_id": str(order.work_order_id),
                    "scope_sha256": order.scope_sha256,
                    "status": route.status,
                    "dispatch_performed": False,
                    "owner": order.owner.value,
                    "owner_packet": render_owner_planning_packet(order, route),
                    "reviewer": order.reviewer.value,
                    "reviewer_packet": render_reviewer_planning_packet(
                        order,
                        route,
                    ),
                })
            print(json.dumps(
                {
                    "ok": True,
                    "mode": "planning_packets",
                    "planning_only": True,
                    "dry_run": True,
                    "snapshot_sha256": snapshot.snapshot_sha256,
                    "packets": packets,
                    "grok_operator_dashboard": render_operator_dashboard(snapshot),
                    "buzz_receipt_preview": json.loads(
                        render_buzz_dry_run_receipt(snapshot)
                    ),
                    "execution_authorized": False,
                    "external_calls": False,
                    "database_calls": False,
                    "provider_calls": False,
                    "publication_calls": False,
                    "automatic_publication": False,
                },
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
