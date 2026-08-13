from __future__ import annotations

import argparse
import asyncio
import json

from core.automation.daily_runner import build_daily_runner
from core.automation.settings import AutomationSettings
from core.batch.settings import BatchSettings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create review-only Korean drafts from official X accounts.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and plan candidates without writing or generating content.",
    )
    args = parser.parse_args()

    try:
        settings = AutomationSettings.from_env()
        batch_settings = BatchSettings.from_env(
            force_dry_run=args.dry_run,
            require_openai_api_key=False,
        )
        runner = build_daily_runner(
            settings,
            batch_settings=batch_settings,
        )
    except (RuntimeError, ValueError):
        print(json.dumps({
            "ok": False,
            "error": "automation_configuration_invalid",
        }, separators=(",", ":")))
        return 2

    try:
        summary = asyncio.run(runner.run(dry_run=args.dry_run))
    except (RuntimeError, ValueError):
        print(json.dumps({
            "ok": False,
            "error": "automation_runtime_failed",
        }, separators=(",", ":")))
        return 1

    print(json.dumps(summary.as_dict(), ensure_ascii=False, separators=(",", ":")))
    return 0 if summary.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
