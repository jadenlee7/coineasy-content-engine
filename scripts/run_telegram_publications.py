from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Mapping, Sequence

from core.publications.settings import (
    PublicationRecoverySettings,
    PublicationSettings,
    telegram_publication_enabled,
)


_VALIDATE_REQUIRED_ENV = (
    "TELEGRAM_PUBLICATION_ENABLED",
    "TELEGRAM_PUBLICATION_ALLOWED_CLIENTS",
    "TELEGRAM_PUBLICATION_LEASE_SECONDS",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "CONTENT_STUDIO_WORKSPACE_ID",
    "TELEGRAM_BOT_TOKEN_SQUID",
    "TELEGRAM_CHANNEL_SQUID",
)


def _build_worker(settings: PublicationSettings):
    # Keep database repository construction out of the validate-only process.
    from core.publications.worker import build_exact_telegram_publication_worker

    return build_exact_telegram_publication_worker(settings)


def _build_recovery_repository(
    settings: PublicationRecoverySettings,
    *,
    transport=None,
):
    from core.publications.repository import SupabasePublicationRecoveryRepository

    return SupabasePublicationRecoveryRepository(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        workspace_id=settings.workspace_id,
        transport=transport,
    )


async def _run(settings: PublicationSettings) -> dict[str, object]:
    worker = _build_worker(settings)
    results: list[dict[str, object]] = []
    for _ in range(settings.max_claims):
        result = await worker.run_once()
        results.append(result.as_dict())
        if not result.claimed:
            break
    failures = sum(1 for result in results if result.get("ok") is not True)
    return {
        "ok": failures == 0,
        "enabled": True,
        "processed": sum(1 for result in results if result.get("claimed") is True),
        "failures": failures,
        "results": results,
    }


async def _recover(
    settings: PublicationRecoverySettings,
    *,
    transport=None,
) -> dict[str, object]:
    repository = _build_recovery_repository(settings, transport=transport)
    summary = await repository.reconcile_expired_leases(
        limit=settings.recovery_limit,
    )
    return {
        "ok": True,
        "mode": "recovery_only",
        "status": "idle" if summary.reconciled_count == 0 else "reconciled",
        "provider_calls": False,
        "reconciled_count": summary.reconciled_count,
        "retrying_count": summary.retrying_count,
        "failed_count": summary.failed_count,
        "delivery_unknown_count": summary.delivery_unknown_count,
    }


def _validate_only(
    environ: Mapping[str, str] | None = None,
    *,
    clients_dir: Path = Path("clients"),
) -> dict[str, object]:
    """Validate deployment inputs without constructing any I/O client."""
    env = os.environ if environ is None else environ
    if any(name not in env for name in _VALIDATE_REQUIRED_ENV):
        raise ValueError("exact Telegram publication validation input is incomplete")
    enabled = telegram_publication_enabled(env)
    settings = PublicationSettings.from_env_for_validation(
        env,
        clients_dir=clients_dir,
    )
    from core.publishers.telegram_exact import load_telegram_exact_config

    telegram = load_telegram_exact_config(
        "squid",
        clients_dir=settings.clients_dir,
        environ=env,
    )
    return {
        "ok": True,
        "mode": "validate_only",
        "enabled": enabled,
        "client_id": telegram.client_id,
        "public_username": telegram.public_username,
        "provider_calls": False,
        "database_calls": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run or validate the exact Telegram publication worker.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate all worker settings without provider or database calls.",
    )
    modes.add_argument(
        "--recovery-only",
        action="store_true",
        help="Reconcile expired leases without claiming or calling Telegram.",
    )
    args = parser.parse_args(argv)
    if args.validate_only:
        try:
            result = _validate_only()
        except (RuntimeError, ValueError):
            result = {
                "ok": False,
                "mode": "validate_only",
                "error": "telegram_publication_validation_failed",
                "provider_calls": False,
                "database_calls": False,
            }
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result.get("ok") is True else 1

    if args.recovery_only:
        try:
            result = asyncio.run(_recover(PublicationRecoverySettings.from_env()))
        except (RuntimeError, ValueError):
            result = {
                "ok": False,
                "mode": "recovery_only",
                "status": "failed",
                "error": "telegram_publication_recovery_failed",
                "provider_calls": False,
            }
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result.get("ok") is True else 1

    try:
        if not telegram_publication_enabled():
            print(json.dumps({
                "ok": True,
                "enabled": False,
                "processed": 0,
                "failures": 0,
                "results": [],
            }, separators=(",", ":")))
            return 0
        result = asyncio.run(_run(PublicationSettings.from_env()))
    except (RuntimeError, ValueError):
        result = {
            "ok": False,
            "enabled": True,
            "error": "telegram_publication_worker_unavailable",
        }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
