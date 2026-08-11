from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from core.buzz.settings import (
    BuzzReviewSettings,
    buzz_review_ack_enabled,
    buzz_review_enabled,
)


_VALIDATE_REQUIRED_ENV = (
    "BUZZ_REVIEW_ENABLED",
    "BUZZ_REVIEW_ALLOWED_CLIENTS",
    "BUZZ_REVIEW_URL",
    "BUZZ_REVIEW_WORKER_TOKEN",
    "BUZZ_RELAY_URL",
    "BUZZ_PRIVATE_KEY",
    "BUZZ_CHANNEL_ID",
    "BUZZ_CLI_PATH",
    "BUZZ_REVIEWER_PUBKEYS",
    "BUZZ_REVIEW_ACK_ENABLED",
)


def _build_worker(settings: BuzzReviewSettings):
    from core.buzz.review import build_origintrail_buzz_review_worker

    return build_origintrail_buzz_review_worker(settings)


def _validate_only() -> dict[str, object]:
    if any(name not in os.environ for name in _VALIDATE_REQUIRED_ENV):
        raise ValueError("Buzz review validation input is incomplete")
    enabled = buzz_review_enabled()
    settings = BuzzReviewSettings.from_env_for_validation()
    return {
        "ok": True,
        "mode": "validate_only",
        "enabled": enabled,
        "client_id": "origintrail",
        "channel_id": settings.channel_id,
        "reviewer_count": len(settings.reviewer_pubkeys),
        "acknowledgement_enabled": buzz_review_ack_enabled(),
        "provider_calls": False,
        "publication_calls": False,
        "database_calls": False,
        "relay_calls": False,
    }


async def _scan_once(settings: BuzzReviewSettings) -> dict[str, object]:
    return (await _build_worker(settings).run_once()).as_dict()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate or scan one OriginTrail Buzz review thread.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument(
        "--scan-once",
        action="store_true",
        help="Read at most one delivered review thread and record one decision.",
    )
    args = parser.parse_args(argv)

    if args.validate_only:
        try:
            result = _validate_only()
        except (RuntimeError, ValueError):
            result = {
                "ok": False,
                "mode": "validate_only",
                "error": "buzz_review_validation_failed",
                "provider_calls": False,
                "publication_calls": False,
                "database_calls": False,
                "relay_calls": False,
            }
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result.get("ok") is True else 1

    if not args.scan_once:
        print(json.dumps({
            "ok": True,
            "enabled": False,
            "mode": "hold",
            "provider_calls": False,
            "publication_calls": False,
            "database_calls": False,
            "relay_calls": False,
        }, separators=(",", ":")))
        return 0

    try:
        enabled = buzz_review_enabled()
    except ValueError:
        enabled = None
    if enabled is False:
        print(json.dumps({
            "ok": True,
            "enabled": False,
            "mode": "hold",
            "reason": "buzz_review_disabled",
            "provider_calls": False,
            "publication_calls": False,
            "database_calls": False,
            "relay_calls": False,
        }, separators=(",", ":")))
        return 0

    try:
        result = asyncio.run(_scan_once(BuzzReviewSettings.from_env()))
    except (RuntimeError, ValueError):
        result = {
            "ok": False,
            "mode": "scan_once",
            "error": "buzz_review_worker_unavailable",
        }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
