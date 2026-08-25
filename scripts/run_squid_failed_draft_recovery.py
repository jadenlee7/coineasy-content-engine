from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid
from datetime import datetime

from core.automation.daily_runner import build_daily_runner
from core.automation.repository import (
    AutomationRepositoryError,
    SupabaseAutomationRepository,
)
from core.automation.settings import AutomationSettings


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_RELEASE_SHA_RE = re.compile(r"^[a-f0-9]{40}$")


def _uuid(value: str) -> str:
    try:
        parsed = str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a canonical UUID") from exc
    if parsed != value.lower():
        raise argparse.ArgumentTypeError("must be a canonical UUID")
    return parsed


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def _sha256(value: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be a SHA-256 hex digest")
    return value


def _release_sha(value: str) -> str:
    if _RELEASE_SHA_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be an exact 40-character Git SHA")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect, authorize, or run exactly one failed Squid draft "
            "recovery. No command approves or publishes content."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_identity(command: argparse.ArgumentParser) -> None:
        command.add_argument("--job-id", required=True, type=_uuid)
        command.add_argument("--recovery-id", required=True, type=_uuid)
        command.add_argument("--release-sha", required=True, type=_release_sha)

    def add_approval(command: argparse.ArgumentParser) -> None:
        command.add_argument("--approval-id", required=True, type=_uuid)
        command.add_argument("--approved-by", required=True)
        command.add_argument("--approved-at", required=True, type=_timestamp)
        command.add_argument("--expires-at", required=True, type=_timestamp)

    inspect = subcommands.add_parser(
        "inspect",
        help="Read the bounded approval subject without changing state.",
    )
    add_identity(inspect)
    add_approval(inspect)

    authorize = subcommands.add_parser(
        "authorize",
        help="Persist one exact approval grant; do not claim or generate.",
    )
    add_identity(authorize)
    add_approval(authorize)
    authorize.add_argument(
        "--subject-sha256",
        required=True,
        type=_sha256,
    )

    run_once = subcommands.add_parser(
        "run-once",
        help="Consume an existing grant and generate at most once.",
    )
    add_identity(run_once)
    run_once.add_argument(
        "--subject-sha256",
        required=True,
        type=_sha256,
    )
    return parser


def _repository(settings: AutomationSettings) -> SupabaseAutomationRepository:
    return SupabaseAutomationRepository(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )


async def _inspect(
    settings: AutomationSettings,
    args: argparse.Namespace,
) -> dict[str, object]:
    inspection = await _repository(settings).inspect_failed_draft_recovery(
        workspace_id=settings.workspace_id,
        job_id=args.job_id,
        recovery_id=args.recovery_id,
        approval_id=args.approval_id,
        approved_by=args.approved_by,
        approved_at=args.approved_at,
        expires_at=args.expires_at,
        release_sha=args.release_sha,
    )
    return {
        "ok": True,
        "mode": "inspect",
        "authorized": inspection.authorized,
        "recovery_id": inspection.recovery_id,
        "job_id": inspection.job_id,
        "request_id": inspection.request_id,
        "source_item_id": inspection.source_item_id,
        "approval_subject": dict(inspection.approval_subject),
        "approval_subject_sha256": inspection.approval_subject_sha256,
        "claims_allowed": inspection.claims_allowed,
        "claims_consumed": inspection.claims_consumed,
        "expires_at": inspection.expires_at,
        "release_sha": inspection.release_sha,
        "automatic_approval": False,
        "automatic_publication": False,
    }


async def _authorize(
    settings: AutomationSettings,
    args: argparse.Namespace,
) -> dict[str, object]:
    reused = await _repository(settings).authorize_failed_draft_recovery(
        workspace_id=settings.workspace_id,
        job_id=args.job_id,
        recovery_id=args.recovery_id,
        approval_id=args.approval_id,
        approved_by=args.approved_by,
        approved_at=args.approved_at,
        expires_at=args.expires_at,
        release_sha=args.release_sha,
        approval_subject_sha256=args.subject_sha256,
    )
    return {
        "ok": True,
        "mode": "authorize",
        "authorized": True,
        "reused": reused,
        "recovery_id": args.recovery_id,
        "job_id": args.job_id,
        "approval_subject_sha256": args.subject_sha256,
        "release_sha": args.release_sha,
        "automatic_approval": False,
        "automatic_publication": False,
    }


async def _run_once(
    settings: AutomationSettings,
    args: argparse.Namespace,
) -> dict[str, object]:
    summary = await build_daily_runner(settings).recover_failed_draft_once(
        job_id=args.job_id,
        recovery_id=args.recovery_id,
        approval_subject_sha256=args.subject_sha256,
        release_sha=args.release_sha,
    )
    return {
        **summary.as_dict(),
        "mode": "run-once",
        "recovery_id": args.recovery_id,
        "job_id": args.job_id,
        "approval_subject_sha256": args.subject_sha256,
        "release_sha": args.release_sha,
        "automatic_approval": False,
        "automatic_publication": False,
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        settings = AutomationSettings.from_env()
        configured_release_sha = os.environ.get(
            "OFFICIAL_X_RECOVERY_RELEASE_SHA",
            "",
        ).strip()
        deployed_release_sha = os.environ.get(
            "RAILWAY_GIT_COMMIT_SHA",
            "",
        ).strip()
        runtime_environment = os.environ.get(
            "RAILWAY_ENVIRONMENT_NAME",
            "",
        ).strip()
        if args.command in {"authorize", "run-once"} and (
            configured_release_sha != args.release_sha
            or _RELEASE_SHA_RE.fullmatch(configured_release_sha) is None
            or deployed_release_sha != args.release_sha
            or _RELEASE_SHA_RE.fullmatch(deployed_release_sha) is None
            or runtime_environment != "production"
        ):
            raise ValueError("recovery release is not the exact production deploy")
    except (RuntimeError, ValueError):
        print(json.dumps({
            "ok": False,
            "error": "recovery_configuration_invalid",
        }, separators=(",", ":")))
        return 2

    try:
        if args.command == "inspect":
            result = asyncio.run(_inspect(settings, args))
        elif args.command == "authorize":
            result = asyncio.run(_authorize(settings, args))
        else:
            result = asyncio.run(_run_once(settings, args))
    except (AutomationRepositoryError, RuntimeError, ValueError):
        print(json.dumps({
            "ok": False,
            "error": "recovery_operation_failed",
            "mode": args.command,
        }, separators=(",", ":")))
        return 1

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    if args.command == "run-once":
        return 0 if result.get("generated") == 1 and result.get("errors") == 0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
