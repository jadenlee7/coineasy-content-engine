"""Default-OFF Railway entrypoint for one private Typefully draft per client/day."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Callable, Mapping, Sequence

from core.publications.typefully_daily import (
    TypefullyDailySettings,
    _release_fence,
    run_typefully_daily,
)


def run(*, validate_only: bool = False, environ: Mapping[str, str] | None = None,
        stamp_reader: Callable[[], str] | None = None,
        daily_runner=run_typefully_daily) -> dict[str, object]:
    values = os.environ if environ is None else environ
    mode = "validate_only" if validate_only else "run"
    try:
        flag = values.get("TYPEFULLY_DAILY_ENABLED", "false")
        if flag not in ("true", "false"):
            raise ValueError("typefully_daily_flag_invalid")
        if flag == "false" and not validate_only:
            return {"ok": True, "mode": mode, "enabled": False,
                    "network_calls": False, "database_calls": False,
                    "provider_calls": False}
        if validate_only:
            _release_fence(values, stamp_reader=stamp_reader, require_pin=flag == "true")
        settings = TypefullyDailySettings.from_env(values, stamp_reader=stamp_reader)
        if validate_only:
            return {"ok": True, "mode": mode, "enabled": flag == "true",
                    "network_calls": False, "database_calls": False,
                    "provider_calls": False}
        if settings is None:
            raise ValueError("typefully_daily_settings_missing")
        result = asyncio.run(daily_runner(settings))
        ok = all(item["status"] in ("no_candidate", "draft_created", "already_reserved")
                 for item in result["outcomes"])
        return {"ok": ok, "mode": mode, "enabled": True, **result}
    except Exception:
        return {"ok": False, "mode": mode, "enabled": values.get("TYPEFULLY_DAILY_ENABLED") == "true",
                "error": "typefully_daily_unavailable",
                **({"network_calls": False, "database_calls": False,
                    "provider_calls": False} if validate_only else {})}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or validate private Typefully daily drafts.")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    result = run(validate_only=args.validate_only)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
