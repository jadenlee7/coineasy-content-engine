"""One fixed-window private trial process; no schedule creation or callback bot."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import re
from core.content_ops.private_review_bot_policy import ExistingReviewBotPolicy
from core.content_ops.trial_courier_adapter import canonical_sha256
from core.content_ops.trial_private_review_runtime import (
    TrialCourierSettings, enabled, require, run_trial, result,
)
from scripts.run_private_review_card_canary import (
    _private_card_identity, _validated_scope_and_provenance,
)


def run(*, validate_only=False, environ=None, stamp_reader=None, runner=run_trial):
    env = os.environ if environ is None else environ
    try:
        active = enabled(env)
        if not active and not validate_only:
            return result("disabled")
        if not active:
            # A release-only OFF deployment precedes the real receipt and anchor.
            # Its pre-deploy check must authenticate the existing private route
            # and immutable image without inventing a new trial plan/window.
            require(all(env.get(k, "false") == "false" for k in (
                "CONTENT_OPS_REVIEW_ENABLED", "CONTENT_OPS_BUTTON_CARD_ENABLED",
                "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED",
            )))
            proof = _validated_scope_and_provenance(env, stamp_reader=stamp_reader,
                allow_missing_runtime_sha=True, daily=True)
            identity = _private_card_identity(env)
            policy_sha = env.get("CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256", "")
            require(type(identity[-1]) is ExistingReviewBotPolicy
                and type(policy_sha) is str and re.fullmatch(r"[a-f0-9]{64}", policy_sha)
                and canonical_sha256(json.loads(env["CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON"]))
                    == policy_sha)
            return {"ok": True, "mode": "validate_only", "enabled": False,
                "network_calls": False, "database_calls": False, "telegram_calls": False,
                "public_send_attempted": False, "provenance": proof.receipt()}
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
    parser = argparse.ArgumentParser(description="One readiness-anchored YBO private trial; default OFF.")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    receipt = run(validate_only=args.validate_only)
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
