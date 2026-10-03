"""Default-OFF daily private cards; validate-only never sends or activates cron."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass

from core.content_ops.daily_private_review import DailyPrivateCardWorker
from core.content_ops.private_review_bot_policy import ExistingReviewBotPolicy
from core.content_ops.private_review_card_canary import PrivateCardCanary
from core.content_ops.private_review_card_courier import PrivateCardCourier
from core.content_ops.private_review_card_gateway import DailyButtonGateway
from core.content_ops.private_review_card_owner_gateway import GatewayPrivateCardOwner
from core.content_ops.private_review_card_sender import TelegramPrivateCardSender
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.worker import APP_ORIGIN
from scripts.run_private_review_card_canary import (
    _private_card_identity, _validated_scope_and_provenance,
)


def _enabled(env):
    value = env.get("CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED", "false")
    if value not in {"true", "false"}:
        raise ValueError("daily_private_card_flag_invalid")
    return value == "true"


@dataclass(frozen=True, repr=False)
class DailyPrivateCardSettings:
    workspace_id: str
    release_sha: str
    gateway_token: str
    bot_token: str
    bot_id: int
    chat_id: int
    signing_key: bytes
    binding_key: bytes
    bot_policy: ExistingReviewBotPolicy
    start_kst: str
    runtime_git_sha_state: str

    @property
    def provenance(self):
        return {"schema_version": "private-card-runtime-provenance@1",
                "build_release_verified": True,
                "runtime_git_sha_state": self.runtime_git_sha_state,
                "runtime_release_verified": self.runtime_git_sha_state == "match"}

    @classmethod
    def from_env(cls, env, *, stamp_reader=None, allow_missing_runtime_sha=False):
        _enabled(env)
        provenance = _validated_scope_and_provenance(env, stamp_reader=stamp_reader,
            allow_missing_runtime_sha=allow_missing_runtime_sha, daily=True)
        workspace, *identity = _private_card_identity(env)
        # Recurring cards must pin the operator-selected existing bot policy.
        if type(identity[-1]) is not ExistingReviewBotPolicy:
            raise ValueError("daily_private_card_existing_policy_required")
        start = env.get("AUTOMATION_DAILY_REVIEW_START_KST", "09:00")
        if type(start) is not str or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", start) is None:
            raise ValueError("daily_private_card_start_invalid")
        return cls(workspace, provenance.release_sha, *identity, start,
                   provenance.runtime_git_sha_state)


def build_runner(settings):
    discovery = DailyButtonGateway(origin=APP_ORIGIN,
        gateway_token=settings.gateway_token, release_sha=settings.release_sha, enabled=True)
    signer, bindings = ButtonSigner(settings.signing_key), EditBindings(settings.binding_key)

    def exact_runner(gateway):
        # Sender replay state is one card only; each version needs a fresh
        # preflight while retaining the same pinned existing bot policy.
        sender = TelegramPrivateCardSender(bot_token=settings.bot_token,
            bot_id=settings.bot_id, chat_id=settings.chat_id, bot_policy=settings.bot_policy)
        owner = GatewayPrivateCardOwner(gateway, enabled=True)
        courier = PrivateCardCourier(owner, sender, signer, bindings)
        return PrivateCardCanary(workspace_id=settings.workspace_id,
            content_version_id=gateway.content_version_id,
            bot_id=settings.bot_id, chat_id=settings.chat_id,
            gateway=gateway, owner=owner, png_reader=gateway, courier=courier,
            signer=signer, bindings=bindings, bot_policy=settings.bot_policy)

    return DailyPrivateCardWorker(gateway=discovery, runner_factory=exact_runner,
                                  start_kst=settings.start_kst)


def run(*, validate_only=False, environ=None, stamp_reader=None, runner_factory=build_runner):
    env = os.environ if environ is None else environ
    mode = "validate_only" if validate_only else "run"
    zero_io = {"network_calls": False, "database_calls": False, "telegram_calls": False}
    try:
        enabled = _enabled(env)
        if not enabled and not validate_only:
            return {"ok": True, "mode": mode, "enabled": False,
                    "public_send_attempted": False}
        settings = DailyPrivateCardSettings.from_env(env, stamp_reader=stamp_reader,
            allow_missing_runtime_sha=validate_only and not enabled)
        if validate_only:
            return {"ok": True, "mode": mode, "enabled": enabled,
                    **zero_io, "public_send_attempted": False,
                    "provenance": settings.provenance}
        outcome = asyncio.run(runner_factory(settings).run(enabled=True))
        keys = {"status", "queued", "claimed", "cards_recorded", "confirmed_parts",
                "public_send_attempted"}
        if (type(outcome) is not dict or set(outcome) != keys
            or outcome["public_send_attempted"] is not False
            or outcome["status"] not in {"completed", "no_candidate", "waiting_for_window",
                "day_rolled_over", "blocked", "outbox_unknown", "delivery_unknown"}
            or any(type(outcome[key]) is not int or not 0 <= outcome[key] <= limit
                   for key, limit in (("queued", 4), ("claimed", 4),
                                      ("cards_recorded", 4), ("confirmed_parts", 16)))
            or outcome["cards_recorded"] > outcome["claimed"]
            or outcome["confirmed_parts"] < outcome["cards_recorded"] * 4):
            raise ValueError("daily_private_card_receipt_invalid")
        return {"ok": outcome["status"] in {
                    "completed", "no_candidate", "waiting_for_window"},
                "mode": mode, "enabled": True, **outcome}
    except Exception:
        return {"ok": False, "mode": mode, "error": "daily_private_card_failed",
                **(zero_io if validate_only else {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded daily private review cards; default OFF.")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    receipt = run(validate_only=args.validate_only)
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
