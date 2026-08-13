from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from core.buzz.settings import (
    BuzzOperationsSettings,
    buzz_operations_enabled,
)


_VALIDATE_REQUIRED_ENV = (
    "BUZZ_OPERATIONS_ENABLED",
    "BUZZ_OPERATIONS_RESPONSE_ENABLED",
    "BUZZ_OPERATIONS_ALLOWED_CLIENTS",
    "BUZZ_OPERATIONS_URL",
    "BUZZ_OPERATIONS_WORKER_TOKEN",
    "BUZZ_OPERATIONS_REVIEWER_PUBKEYS",
    "BUZZ_OPERATIONS_EXPECTED_ENVIRONMENT",
    "BUZZ_OPERATIONS_RELEASE_SHA",
    "BUZZ_OPERATIONS_PROTOCOL_START_EPOCH",
    "BUZZ_OPERATIONS_RESPONSE_LEASE_SECONDS",
    "BUZZ_RELAY_URL",
    "BUZZ_PRIVATE_KEY",
    "BUZZ_CHANNEL_ID",
    "BUZZ_SERVICE_PUBKEY",
    "BUZZ_CLI_PATH",
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_GIT_COMMIT_SHA",
)


def _validate_only() -> dict[str, object]:
    if any(name not in os.environ for name in _VALIDATE_REQUIRED_ENV):
        raise ValueError("Buzz operations validation input is incomplete")
    enabled = buzz_operations_enabled()
    settings = BuzzOperationsSettings.from_env_for_validation()
    return {
        "ok": True,
        "mode": "validate_only",
        "enabled": enabled,
        "response_enabled": settings.response_enabled,
        "client_id": "origintrail",
        "environment": settings.deployment_environment,
        "channel_id": settings.channel_id,
        "reviewer_count": len(settings.reviewer_pubkeys),
        "release_sha": settings.release_sha,
        "database_calls": False,
        "relay_calls": False,
        "openai_calls": False,
        "batch_calls": False,
        "publication_calls": False,
    }


async def _scan_once(settings: BuzzOperationsSettings) -> dict[str, object]:
    from core.buzz.operations_worker import (
        build_origintrail_buzz_operations_worker,
    )
    return (await build_origintrail_buzz_operations_worker(settings).run_once()).as_dict()


def _hold(reason: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "mode": "hold",
        "enabled": False,
        "database_calls": False,
        "relay_calls": False,
        "openai_calls": False,
        "batch_calls": False,
        "publication_calls": False,
    }
    if reason is not None:
        result["reason"] = reason
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate or scan one bounded Buzz operations command.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--scan-once", action="store_true")
    args = parser.parse_args(argv)

    if args.validate_only:
        try:
            result = _validate_only()
        except (RuntimeError, ValueError):
            result = {
                "ok": False,
                "mode": "validate_only",
                "error": "buzz_operations_validation_failed",
                "database_calls": False,
                "relay_calls": False,
                "openai_calls": False,
                "batch_calls": False,
                "publication_calls": False,
            }
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result.get("ok") is True else 1

    if not args.scan_once:
        print(json.dumps(_hold(), separators=(",", ":")))
        return 0
    try:
        enabled = buzz_operations_enabled()
    except ValueError:
        enabled = None
    if enabled is False:
        print(json.dumps(
            _hold("buzz_operations_disabled"), separators=(",", ":")
        ))
        return 0
    try:
        result = asyncio.run(_scan_once(BuzzOperationsSettings.from_env()))
    except (RuntimeError, ValueError):
        result = {
            "ok": False,
            "mode": "scan_once",
            "error": "buzz_operations_worker_unavailable",
        }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
