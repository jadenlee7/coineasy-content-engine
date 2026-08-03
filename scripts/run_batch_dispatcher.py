from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core.batch.canary import canonical_sha256, dispatch_subject
from core.batch.dispatcher import build_batch_dispatcher
from core.batch.repository import SupabaseBatchRepository
from core.batch.settings import BatchSettings


_KST = ZoneInfo("Asia/Seoul")


def _experiment_phase(settings: BatchSettings, now: datetime) -> str:
    return settings.experiment_window_phase(now)


async def _run_live(
    settings: BatchSettings,
    *,
    poll_only: bool = True,
) -> dict[str, object]:
    if (
        settings.supabase_url is None
        or settings.supabase_service_role_key is None
        or settings.workspace_id is None
        or settings.openai_api_key is None
    ):
        raise ValueError("live Batch settings are incomplete")
    started_at = datetime.now(timezone.utc)
    phase = _experiment_phase(settings, started_at)
    if phase == "not_started":
        return {
            "ok": True,
            "mode": "live",
            "experiment_phase": phase,
            "submissions_enabled": False,
            "provider_calls": False,
            "detail": "batch_experiment_not_started",
        }
    settings.assert_canary_config_authorized()
    repository = SupabaseBatchRepository(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
        workspace_id=settings.workspace_id,
    )
    dispatch_approval = settings.canary_dispatch_approval
    dispatcher_options: dict[str, object] = {}
    if dispatch_approval is not None:
        dispatcher_options.update({
            "canary_config_subject_sha256": (
                settings.canary_subject_sha256
            ),
            "canary_config_approval_id": settings.canary_approval.approval_id,
            "canary_dispatch_subject_sha256": (
                dispatch_approval.subject_sha256
            ),
            "canary_dispatch_approval_id": (
                dispatch_approval.dispatch_approval_id
            ),
            "canary_job_id": dispatch_approval.job_id,
            "canary_input_sha256": dispatch_approval.input_sha256,
            "canary_request_sha256": dispatch_approval.request_sha256,
            "canary_not_after": settings.submission_not_after(),
        })
    dispatcher = build_batch_dispatcher(
        repository=repository,
        api_key=settings.openai_api_key,
        allowed_clients=settings.allowed_clients,
        worker_id=f"batch:{uuid.uuid4()}",
        max_claims=settings.max_claims,
        max_requests_per_batch=settings.max_requests_per_batch,
        **dispatcher_options,
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

    summary = await dispatcher.poll_once()
    after_poll = datetime.now(timezone.utc)
    dispatch_phase = settings.dispatch_phase(after_poll)
    deadline_safe = (
        settings.experiment_end_at is not None
        and settings.experiment_end_at - after_poll >= timedelta(hours=26)
    )
    if (
        poll_only
        or summary.errors != 0
        or dispatch_phase != "active"
        or not deadline_safe
    ):
        result = summary.as_dict()
        result.update({
            "mode": "live",
            "experiment_phase": phase,
            "dispatch_phase": dispatch_phase,
            "submissions_enabled": False,
            "detail": (
                "batch_poll_only_requested"
                if poll_only
                else (
                    "batch_poll_failed_submission_blocked"
                    if summary.errors != 0
                    else (
                        "batch_dispatch_not_authorized_poll_only"
                        if dispatch_phase != "active"
                        else "batch_submission_drain_poll_only"
                    )
                )
            ),
        })
        return result

    if dispatch_approval is None:
        raise ValueError("live Batch dispatch approval is incomplete")
    not_after = settings.submission_not_after()
    kst_now = after_poll.astimezone(_KST)
    window_start_kst = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end_kst = window_start_kst + timedelta(days=1)
    budget_key = f"batch-general:{window_start_kst.date().isoformat()}"
    await repository.configure_daily_budget(
        budget_key=budget_key,
        window_start=window_start_kst.astimezone(timezone.utc),
        window_end=window_end_kst.astimezone(timezone.utc),
        limit_usd=settings.daily_cap_usd,
    )
    await repository.configure_canary_grant(
        config_subject_sha256=settings.canary_subject_sha256,
        config_approval_id=settings.canary_approval.approval_id,
        dispatch_subject_sha256=dispatch_approval.subject_sha256,
        dispatch_approval_id=dispatch_approval.dispatch_approval_id,
        job_id=dispatch_approval.job_id,
        input_sha256=dispatch_approval.input_sha256,
        request_sha256=dispatch_approval.request_sha256,
        expires_at=not_after,
        hard_limit_usd=settings.daily_cap_usd,
    )
    before_claim = datetime.now(timezone.utc)
    if (
        settings.dispatch_phase(before_claim) != "active"
        or before_claim >= not_after
    ):
        result = summary.as_dict()
        result.update({
            "mode": "live",
            "experiment_phase": phase,
            "dispatch_phase": settings.dispatch_phase(before_claim),
            "submissions_enabled": False,
            "budget_key": budget_key,
            "detail": "batch_authorization_changed_before_claim",
        })
        return result
    await dispatcher.submit_once(summary=summary)
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate policy and configuration without DB or provider calls.",
    )
    mode.add_argument(
        "--approval-subject",
        action="store_true",
        help="Print the nonsecret live config subject without external calls.",
    )
    mode.add_argument(
        "--preflight-live",
        action="store_true",
        help="Validate live receipts without DB or provider calls.",
    )
    mode.add_argument(
        "--dispatch-subject",
        nargs=3,
        metavar=("JOB_ID", "INPUT_SHA256", "REQUEST_SHA256"),
        help=(
            "Print the exact-job and exact-request dispatch subject without "
            "external calls."
        ),
    )
    mode.add_argument(
        "--poll-only",
        action="store_true",
        help="Poll registered Batches without claiming or submitting work.",
    )
    mode.add_argument(
        "--submit-once",
        action="store_true",
        help=(
            "Allow one exact receipt-bound claim/submission pass. Live "
            "defaults to poll-only without this explicit flag."
        ),
    )
    args = parser.parse_args()
    try:
        if args.approval_subject:
            subject, subject_sha256 = BatchSettings.canary_subject_from_env()
            result = {
                "ok": True,
                "mode": "hold",
                "subject": subject,
                "subject_sha256": subject_sha256,
                "database_calls": False,
                "provider_calls": False,
                "submissions_enabled": False,
                "detail": "approval_subject_only_not_authorization",
            }
            print(json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
            ))
            return 0
        if args.dispatch_subject:
            settings = BatchSettings.from_env(require_openai_api_key=False)
            settings.assert_canary_config_authorized()
            job_id, input_sha256, request_sha256 = args.dispatch_subject
            subject = dispatch_subject(
                config_subject_sha256=settings.canary_subject_sha256,
                config_approval_id=settings.canary_approval.approval_id,
                job_id=job_id,
                input_sha256=input_sha256,
                request_sha256=request_sha256,
            )
            result = {
                "ok": True,
                "mode": "hold",
                "subject": subject,
                "subject_sha256": canonical_sha256(subject),
                "database_calls": False,
                "provider_calls": False,
                "submissions_enabled": False,
                "detail": "dispatch_subject_only_not_authorization",
            }
            print(json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
            ))
            return 0
        settings = BatchSettings.from_env(force_dry_run=args.dry_run)
        if args.preflight_live:
            if settings.mode != "live":
                raise ValueError("live Batch settings are required")
            now = datetime.now(timezone.utc)
            experiment_phase = settings.experiment_window_phase(now)
            config_phase = settings.canary_approval.phase(now)
            dispatch_phase = settings.dispatch_phase(now)
            deadline_safe = (
                settings.experiment_end_at is not None
                and settings.experiment_end_at - now >= timedelta(hours=26)
            )
            result = {
                "ok": True,
                **settings.public_summary(),
                "experiment_phase": experiment_phase,
                "config_approval_phase": config_phase,
                "dispatch_phase": dispatch_phase,
                "submission_deadline_safe": deadline_safe,
                "ready_to_submit": (
                    experiment_phase == "active"
                    and dispatch_phase == "active"
                    and deadline_safe
                ),
                "database_calls": False,
                "provider_calls": False,
                "submissions_enabled": False,
                "detail": "live_config_validated_no_external_calls",
            }
            if settings.canary_dispatch_approval is not None:
                result["canary_dispatch"] = (
                    settings.canary_dispatch_approval.public_summary()
                )
            print(json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
            ))
            return 0
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
            result = asyncio.run(_run_live(
                settings,
                poll_only=(args.poll_only or not args.submit_once),
            ))
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
