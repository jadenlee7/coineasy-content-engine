from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from core.buzz.settings import BuzzDeliverySettings, buzz_delivery_enabled


_VALIDATE_REQUIRED_ENV = (
    "BUZZ_DELIVERY_ENABLED",
    "BUZZ_DELIVERY_ALLOWED_CLIENTS",
    "BUZZ_SHADOW_URL",
    "BUZZ_DELIVERY_URL",
    "BUZZ_SHADOW_ACCESS_TOKEN",
    "BUZZ_DELIVERY_WORKER_TOKEN",
    "BUZZ_RELAY_URL",
    "BUZZ_PRIVATE_KEY",
    "BUZZ_CHANNEL_ID",
    "BUZZ_CLI_PATH",
)


def _build_worker(settings: BuzzDeliverySettings):
    from core.buzz.worker import build_origintrail_buzz_delivery_worker

    return build_origintrail_buzz_delivery_worker(settings)


def _validate_only() -> dict[str, object]:
    if any(name not in os.environ for name in _VALIDATE_REQUIRED_ENV):
        raise ValueError("Buzz delivery validation input is incomplete")
    enabled = buzz_delivery_enabled()
    settings = BuzzDeliverySettings.from_env_for_validation()
    return {
        "ok": True,
        "mode": "validate_only",
        "enabled": enabled,
        "client_id": "origintrail",
        "channel_id": settings.channel_id,
        "provider_calls": False,
        "database_calls": False,
        "shadow_calls": False,
    }


async def _send_once(settings: BuzzDeliverySettings) -> dict[str, object]:
    result = await _build_worker(settings).run_once()
    return result.as_dict()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate or run one OriginTrail Buzz delivery.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument(
        "--send-once",
        action="store_true",
        help="Attempt at most one durably fenced Buzz relay write.",
    )
    args = parser.parse_args(argv)

    if args.validate_only:
        try:
            result = _validate_only()
        except (RuntimeError, ValueError):
            result = {
                "ok": False,
                "mode": "validate_only",
                "error": "buzz_delivery_validation_failed",
                "provider_calls": False,
                "database_calls": False,
                "shadow_calls": False,
            }
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result.get("ok") is True else 1

    if not args.send_once:
        print(json.dumps({
            "ok": True,
            "enabled": False,
            "mode": "hold",
            "provider_calls": False,
            "database_calls": False,
            "shadow_calls": False,
        }, separators=(",", ":")))
        return 0

    try:
        enabled = buzz_delivery_enabled()
    except ValueError:
        enabled = None
    if enabled is False:
        print(json.dumps({
            "ok": True,
            "enabled": False,
            "mode": "hold",
            "reason": "buzz_delivery_disabled",
            "provider_calls": False,
            "database_calls": False,
            "shadow_calls": False,
        }, separators=(",", ":")))
        return 0

    try:
        result = asyncio.run(_send_once(BuzzDeliverySettings.from_env()))
    except (RuntimeError, ValueError):
        result = {
            "ok": False,
            "mode": "send_once",
            "error": "buzz_delivery_worker_unavailable",
        }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
