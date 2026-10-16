"""One exact private recovery on the existing courier; default OFF, no schedule."""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from core.content_ops.recovery_private_review_runtime import (
    RecoveryCourierSettings, enabled, validate_configuration, run_recovery, result,
)


def run(*, validate_only=False, environ=None, stamp_reader=None, runner=run_recovery):
    env = os.environ if environ is None else environ
    try:
        active = enabled(env)
        if not active and not validate_only:
            return result("disabled")
        if validate_only:
            plan, _, proof = validate_configuration(env, stamp_reader=stamp_reader, validate_only=True)
            return {"ok": True, "mode": "validate_only", "enabled": active, "plan_configured": plan is not None,
                "network_calls": False, "database_calls": False, "telegram_calls": False,
                "public_send_attempted": False, "provenance": proof.receipt()}
        settings = RecoveryCourierSettings.from_env(env, stamp_reader=stamp_reader)
        return asyncio.run(runner(settings))
    except Exception:
        return {"ok": False, "status": "blocked", "error": "private_recovery_failed",
            "invocation_dispatch_enabled": False, "public_send_attempted": False,
            **({"network_calls": False, "database_calls": False, "telegram_calls": False} if validate_only else {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exact-version private recovery; default OFF, never automatic retry.")
    parser.add_argument("--validate-only", action="store_true")
    receipt = run(validate_only=parser.parse_args(argv).validate_only)
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
