from __future__ import annotations

import argparse
import asyncio
import json

from core.grok_qa.broker import HttpGrokQaBroker
from core.grok_qa.settings import (
    GrokQaSettings,
    grok_qa_dispatch_enabled,
    validate_grok_qa_worker_secret_boundary,
)
from core.grok_qa.worker import build_grok_qa_worker


def _hold() -> dict[str, object]:
    return {
        "ok": True,
        "enabled": False,
        "mode": "hold",
        "reason": "grok_qa_dispatch_disabled",
        "provider_calls": False,
        "database_calls": False,
        "relay_calls": False,
        "publication_calls": False,
    }


async def _run_once(
    settings: GrokQaSettings,
    broker: HttpGrokQaBroker,
) -> dict[str, object]:
    await broker.reconcile(limit=10)
    worker = build_grok_qa_worker(
        broker=broker,
        api_key=settings.xai_api_key,
        model=settings.model,
        allowed_clients=settings.allowed_clients,
        lease_seconds=settings.lease_seconds,
        max_source_age_seconds=settings.max_source_age_seconds,
        timeout_seconds=settings.timeout_seconds,
        max_turns=settings.max_turns,
        x_search_window_days=settings.x_search_window_days,
        max_output_tokens=settings.max_output_tokens,
        max_cost_in_usd_ticks=settings.max_cost_in_usd_ticks,
        canary_content_version_id=(
            settings.active_canary_content_version_id
        ),
    )
    result = await worker.run_once()
    payload = result.as_dict()
    payload.update({
        "enabled": True,
        "mode": "run_once",
        "advisory_only": True,
        "public_publish": False,
    })
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one least-privilege official-X Grok QA pass.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate every production fence without network calls.",
    )
    mode.add_argument(
        "--run-once",
        action="store_true",
        help="Reconcile leases and process at most one advisory review.",
    )
    args = parser.parse_args()

    try:
        validate_grok_qa_worker_secret_boundary()
        enabled = grok_qa_dispatch_enabled()
        if not enabled and not args.validate_only:
            print(json.dumps(_hold(), separators=(",", ":")))
            return 0
        settings = (
            GrokQaSettings.from_env_for_validation()
            if args.validate_only
            else GrokQaSettings.from_env()
        )
        broker = HttpGrokQaBroker.from_env()
        if args.validate_only:
            print(json.dumps({
                "ok": True,
                "enabled": enabled,
                "mode": "validate_only",
                "model": settings.model,
                "allowed_clients": list(settings.allowed_clients),
                "canary_mode": settings.canary_mode,
                "canary_target_configured": (
                    settings.canary_content_version_id is not None
                ),
                "max_source_age_seconds": settings.max_source_age_seconds,
                "runtime_environment_verified": True,
                "runtime_release_verified": True,
                "provider_calls": False,
                "database_calls": False,
                "relay_calls": False,
                "publication_calls": False,
            }, separators=(",", ":")))
            return 0
        payload = asyncio.run(_run_once(settings, broker))
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0 if payload.get("ok") is True else 1
    except (RuntimeError, ValueError):
        print(json.dumps({
            "ok": False,
            "error": "grok_qa_dispatch_configuration_invalid",
            "provider_calls": False,
            "publication_calls": False,
        }, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
