"""Unmounted manual-only refresh CLI. OFF and validate-only create no clients."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Mapping

from core.automation.same_day_refresh import (
    SHA40, RefreshQueueManifest, RefreshRunManifest, SameDayRefreshRunner,
    SupabaseSameDayRefreshRepository, aware_time, canonical_day, canonical_uuid,
)
from core.automation.same_day_refresh_runtime import read_image_stamp, validate_runtime


def _parser():
    parser = argparse.ArgumentParser(description="One exact manual latest-source refresh; default OFF.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-runtime-only", action="store_true",
                       help="Check explicit OFF/config/native/image SHA only; no candidate IDs or external I/O.")
    modes.add_argument("--validate-only", action="store_true",
                        help="Validate scalar identities/config only; zero DB/HTTP/provider I/O.")
    commands = parser.add_subparsers(dest="command")
    for command in ("queue-once", "generate-once", "inspect"):
        sub = commands.add_parser(command)
        sub.add_argument("--validate-only", action="store_true", default=argparse.SUPPRESS)
        sub.add_argument("--workspace-id")
        sub.add_argument("--refresh-id")
        sub.add_argument("--release-sha")
        if command == "inspect":
            sub.add_argument("--content-version-id")
            continue
        for name in ("client-id", "kst-date", "request-id", "source-item-id", "expires-at"):
            sub.add_argument("--" + name)
        if command == "queue-once":
            for name in ("predecessor-job-id", "predecessor-content-item-id", "predecessor-content-version-id"):
                sub.add_argument("--" + name)
        else:
            sub.add_argument("--job-id")
    return parser


def _enabled(env: Mapping[str, str]) -> bool:
    value = env.get("OFFICIAL_X_SAME_DAY_REFRESH_ENABLED", "false")
    if value not in {"true", "false"}:
        raise ValueError("same_day_refresh_flag_invalid")
    return value == "true"


def _release(env, expected):
    configured = env.get("OFFICIAL_X_SAME_DAY_REFRESH_RELEASE_SHA", "")
    runtime = env.get("RAILWAY_GIT_COMMIT_SHA", "")
    if (type(expected) is not str or SHA40.fullmatch(expected) is None
        or type(configured) is not str or SHA40.fullmatch(configured) is None
        or type(runtime) is not str or SHA40.fullmatch(runtime) is None
        or not secrets.compare_digest(expected, configured)
        or not secrets.compare_digest(expected, runtime)):
        raise ValueError("same_day_refresh_release_mismatch")


def _manifest(args, now):
    if args.command not in {"queue-once", "generate-once", "inspect"}:
        raise ValueError("same_day_refresh_command_required")
    for value in (args.workspace_id, args.refresh_id):
        canonical_uuid(value)
    if args.command == "inspect":
        canonical_uuid(args.content_version_id)
        return None
    shared = dict(workspace_id=args.workspace_id, client_id=args.client_id,
        kst_date=canonical_day(args.kst_date), refresh_id=args.refresh_id,
        request_id=args.request_id, source_item_id=args.source_item_id,
        release_sha=args.release_sha, expires_at=aware_time(args.expires_at))
    if args.command == "queue-once":
        result = RefreshQueueManifest(**shared, predecessor_job_id=args.predecessor_job_id,
            predecessor_content_item_id=args.predecessor_content_item_id,
            predecessor_content_version_id=args.predecessor_content_version_id)
    else:
        result = RefreshRunManifest(**shared, job_id=args.job_id)
    result.validate(now)
    return result


def build_runner(env, *, workspace_id, operation):
    # Called only after enabled + scalar manifest + native release fence pass.
    repository = SupabaseSameDayRefreshRepository(
        supabase_url=env.get("SUPABASE_URL", ""),
        service_role_key=env.get("SUPABASE_SERVICE_ROLE_KEY", ""))
    generation = None
    if operation == "generate-once":
        from core.automation.generation_client import StudioGenerationClient
        generation = StudioGenerationClient(base_url=env.get("STUDIO_BASE_URL", ""),
            automation_token=env.get("STUDIO_AUTOMATION_TOKEN", ""))
    return SameDayRefreshRunner(repository=repository, generation=generation)


def run(args, *, environ=None, now_factory=lambda: datetime.now(timezone.utc),
        runner_factory=build_runner, stamp_reader=read_image_stamp):
    env = os.environ if environ is None else environ
    mode = ("validate_runtime_only" if args.validate_runtime_only else
            "validate_only" if args.validate_only else (args.command or "off"))
    zero_io = {"network_calls": False, "database_calls": False, "provider_calls": False,
               "private_send_attempted": False, "public_send_attempted": False}
    try:
        if args.validate_runtime_only:
            if args.command is not None or args.validate_only:
                raise ValueError("same_day_refresh_modes_conflict")
            provenance = validate_runtime(env, required_enabled=False, stamp_reader=stamp_reader)
            return {"ok": True, "mode": mode, "enabled": False, **zero_io, **provenance}
        enabled = _enabled(env)
        if not enabled and not args.validate_only:
            return {"ok": True, "mode": mode, "enabled": False, **zero_io}
        manifest = _manifest(args, now_factory())
        if canonical_uuid(env.get("CONTENT_STUDIO_WORKSPACE_ID", "")) != args.workspace_id:
            raise ValueError("same_day_refresh_workspace_mismatch")
        _release(env, args.release_sha)
        if args.validate_only:
            return {"ok": True, "mode": mode, "enabled": enabled, **zero_io,
                    "release_sha": args.release_sha, "native_runtime_sha_matches": True,
                    "image_attestation": False}
        provenance = validate_runtime(env, required_enabled=True, stamp_reader=stamp_reader)
        runner = runner_factory(env, workspace_id=args.workspace_id, operation=args.command)
        if args.command == "queue-once":
            receipt = asyncio.run(runner.queue_once(manifest))
        elif args.command == "generate-once":
            receipt = asyncio.run(runner.generate_once(manifest))
        else:
            receipt = asyncio.run(runner.inspect(workspace_id=args.workspace_id,
                refresh_id=args.refresh_id, content_version_id=args.content_version_id))
            if receipt["release_sha"] != args.release_sha:
                raise ValueError("same_day_refresh_release_mismatch")
            receipt = {"ok": receipt["status"] == "refresh_ready", **receipt}
        return {**receipt, "mode": mode, "enabled": True, **provenance}
    except Exception:
        # No source, credentials, provider body or exception text leaves this CLI.
        return {"ok": False, "mode": mode, "error": "same_day_refresh_failed",
                **({"image_attestation": False, "hosted_provenance_verified": False}
                   if args.validate_runtime_only else {}),
                **(zero_io if args.validate_only or args.validate_runtime_only else {
                    "private_send_attempted": False, "public_send_attempted": False})}


def main(argv=None):
    args = _parser().parse_args(argv)
    receipt = run(args)
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
