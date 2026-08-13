from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from core.autonomous_ops_settings import (
    AutonomousOpsSettings,
    autonomous_ops_enabled,
)


_REQUIRED = (
    "AUTONOMOUS_OPS_ENABLED",
    "AUTONOMOUS_OPS_RECORD_ENABLED",
    "AUTONOMOUS_OPS_ALLOWED_CLIENTS",
    "AUTONOMOUS_OPS_URL",
    "AUTONOMOUS_OPS_WORKER_TOKEN",
    "AUTONOMOUS_OPS_EXPECTED_ENVIRONMENT",
    "AUTONOMOUS_OPS_RELEASE_SHA",
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_GIT_COMMIT_SHA",
)


def _hold(reason: str) -> dict[str, object]:
    return {
        "ok": True,
        "mode": "hold",
        "enabled": False,
        "reason": reason,
        "database_calls": False,
        "relay_calls": False,
        "openai_calls": False,
        "batch_calls": False,
        "publication_calls": False,
        "deployment_calls": False,
    }


def _validate() -> dict[str, object]:
    if any(name not in os.environ for name in _REQUIRED):
        raise ValueError("Autonomous Ops validation input is incomplete")
    settings = AutonomousOpsSettings.from_env_for_validation()
    return {
        "ok": True,
        "mode": "validate_only",
        "enabled": autonomous_ops_enabled(),
        "environment": settings.environment,
        "release_sha": settings.release_sha,
        "execution_mode": "propose_only",
        "database_calls": False,
        "relay_calls": False,
        "openai_calls": False,
        "batch_calls": False,
        "publication_calls": False,
        "deployment_calls": False,
    }


async def _run(settings: AutonomousOpsSettings) -> dict[str, object]:
    from core.autonomous_ops_worker import (
        AutonomousOpsControlClient,
        OriginTrailAutonomousOpsWorker,
    )
    control = AutonomousOpsControlClient(
        url=settings.control_url, token=settings.control_token
    )
    return (await OriginTrailAutonomousOpsWorker(control).run_once()).as_dict()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded OriginTrail autonomous observation cycle."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--run-once", action="store_true")
    args = parser.parse_args(argv)
    if args.validate_only:
        try:
            result = _validate()
        except (RuntimeError, ValueError):
            result = {
                **_hold("autonomous_ops_validation_failed"),
                "ok": False, "mode": "validate_only",
            }
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result["ok"] is True else 1
    if not args.run_once:
        result = _hold("autonomous_ops_not_requested")
        print(json.dumps(result, separators=(",", ":")))
        return 0
    try:
        enabled = autonomous_ops_enabled()
    except ValueError:
        enabled = None
    if enabled is False:
        result = _hold("autonomous_ops_disabled")
        print(json.dumps(result, separators=(",", ":")))
        return 0
    try:
        result = asyncio.run(_run(AutonomousOpsSettings.from_env()))
    except (RuntimeError, ValueError):
        result = {
            "ok": False, "status": "failed",
            "error": "autonomous_ops_worker_unavailable",
        }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
