from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core.batch.dispatcher import build_batch_dispatcher
from core.batch.repository import SupabaseBatchRepository
from core.batch.settings import BatchSettings


_KST = ZoneInfo("Asia/Seoul")


def _experiment_phase(settings: BatchSettings, now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("Batch experiment clock must be timezone-aware")
    if (
        settings.experiment_start_at is None
        or settings.experiment_end_at is None
    ):
        raise ValueError("live Batch experiment window is incomplete")
    if now < settings.experiment_start_at:
        return "not_started"
    if now >= settings.experiment_end_at:
        return "expired"
    return "active"


async def _run_live(settings: BatchSettings) -> dict[str, object]:
    if (
        settings.supabase_url is None
        or settings.supabase_service_role_key is None
        or settings.workspace_id is None
        or settings.openai_api_key is None
    ):
        raise ValueError("live Batch settings are incomplete")
    now = datetime.now(timezone.utc)
    phase = _experiment_phase(settings, now)
    if phase == "not_started":
        return {
            "ok": True,
            "mode": "live",
            "experiment_phase": phase,
            "submissions_enabled": False,
            "provider_calls": False,
            "detail": "batch_experiment_not_started",
        }
    repository = SupabaseBatchRepository(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        workspace_id=settings.workspace_id,
    )
    dispatcher = build_batch_dispatcher(
        repository=repository,
        api_key=settings.openai_api_key,
        allowed_clients=settings.allowed_clients,
        worker_id=f"batch:{uuid.uuid4()}",
        max_claims=settings.max_claims,
        max_requests_per_batch=settings.max_requests_per_batch,
    )
    if phase == "expired":
        summary = await dispatcher.poll_once()
        await dispatcher.cleanup_expired_once(summary=summary)
        result = summary.as_dict()
        result.update({
            "mode": "live",
            "experiment_phase": phase,
            "submissions_enabled": False,
            "detail": "batch_experiment_expired_poll_only",
        })
        return result

    kst_now = now.astimezone(_KST)
    window_start_kst = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end_kst = window_start_kst + timedelta(days=1)
    budget_key = f"batch-general:{window_start_kst.date().isoformat()}"
    await repository.configure_daily_budget(
        budget_key=budget_key,
        window_start=window_start_kst.astimezone(timezone.utc),
        window_end=window_end_kst.astimezone(timezone.utc),
        limit_usd=settings.daily_cap_usd,
    )
    summary = await dispatcher.run_once()
    result = summary.as_dict()
    result["mode"] = "live"
    result["experiment_phase"] = phase
    result["submissions_enabled"] = True
    result["budget_key"] = budget_key
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dispatch review-only CoinEasy work through OpenAI Batch.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate policy and configuration without DB or provider calls.",
    )
    args = parser.parse_args()
    try:
        settings = BatchSettings.from_env(force_dry_run=args.dry_run)
        if settings.mode != "live":
            result = {
                "ok": True,
                **settings.public_summary(),
                "detail": (
                    "batch_experiment_disabled"
                    if settings.mode == "off"
                    else "batch_experiment_validated_no_external_calls"
                ),
            }
        else:
            result = asyncio.run(_run_live(settings))
    except (RuntimeError, ValueError):
        print(json.dumps(
            {"ok": False, "error": "batch_configuration_invalid"},
            separators=(",", ":"),
        ))
        return 2

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
