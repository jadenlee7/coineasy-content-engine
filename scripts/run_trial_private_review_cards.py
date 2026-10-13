"""One fixed-window private trial process; no schedule creation or callback bot."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from core.content_ops.trial_private_review_runtime import TrialCourierSettings, enabled, run_trial, result


def run(*, validate_only=False, environ=None, stamp_reader=None, runner=run_trial):
    env = os.environ if environ is None else environ
    try:
        active = enabled(env)
        if not active and not validate_only:
            return result("disabled")
        settings = TrialCourierSettings.from_env(env, stamp_reader=stamp_reader, validate_only=validate_only)
        if validate_only:
            return {"ok": True, "mode": "validate_only", "enabled": active,
                "network_calls": False, "database_calls": False, "telegram_calls": False,
                "public_send_attempted": False, "provenance": settings.provenance}
        return asyncio.run(runner(settings))
    except Exception:
        return {"ok": False, "status": "blocked", "error": "trial_private_courier_failed",
            "invocation_dispatch_enabled": False, "public_send_attempted": False,
            **({"network_calls": False, "database_calls": False, "telegram_calls": False} if validate_only else {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description="One Oct14 YBO private trial; default OFF.")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    receipt = run(validate_only=args.validate_only)
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
