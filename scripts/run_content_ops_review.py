"""Disabled-by-default internal review relay. Does not activate a schedule."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Callable, Mapping, Sequence

from core.content_ops.worker import ReviewSettings, review_enabled


def _build_worker(settings: ReviewSettings):
    from core.content_ops.worker import build_worker
    return build_worker(settings)


def run(
    *, validate_only: bool = False, environ: Mapping[str, str] | None = None,
    stamp_reader: Callable[[], str] | None = None,
) -> dict[str, object]:
    env = os.environ if environ is None else environ
    mode = "validate_only" if validate_only else "run"
    try:
        enabled = review_enabled(env)
        # No credential reads, client construction, gateway, or Telegram calls
        # when disabled. Validation deliberately checks all inputs, with no I/O.
        if not enabled and not validate_only:
            return {"ok": True, "mode": mode, "enabled": False, "claimed": 0}
        settings = ReviewSettings.from_env(env, stamp_reader=stamp_reader)
        if validate_only:
            return {
                "ok": True, "mode": mode, "enabled": enabled,
                "network_calls": False, "database_calls": False, "telegram_calls": False,
            }
        return {"mode": mode, **asyncio.run(_build_worker(settings).run())}
    except Exception:
        return {
            "ok": False, "mode": mode, "error": "content_ops_review_failed",
            **({"network_calls": False, "database_calls": False, "telegram_calls": False}
               if validate_only else {}),
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deliver isolated internal content review cards in an explicitly configured canary or daily mode.",
    )
    parser.add_argument("--validate-only", action="store_true", help="Validate settings with zero network calls.")
    args = parser.parse_args(argv)
    result = run(validate_only=args.validate_only)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
