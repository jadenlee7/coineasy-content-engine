"""Unmounted, default-OFF one-shot private review-card canary.

No scheduler, polling, callback consumer, public publisher or database key.
Validate-only performs no network, database or Telegram calls. A real private
send still requires a separately authorized default-OFF deployment and exact
version enablement; the proposed Dockerfile.private-review-card is not deployed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence
from uuid import UUID

from core.content_ops.private_review_card_canary import PrivateCardCanary
from core.content_ops.private_review_card_courier import PrivateCardCourier
from core.content_ops.private_review_card_gateway import ButtonCanaryGateway
from core.content_ops.private_review_card_owner_gateway import GatewayPrivateCardOwner
from core.content_ops.private_review_card_sender import TelegramPrivateCardSender
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.worker import APP_ORIGIN


_SHA40 = re.compile(r"[a-f0-9]{40}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
_BOT = re.compile(r"([1-9][0-9]{4,15}):[A-Za-z0-9_-]{30,100}\Z")
_ROOM = re.compile(r"-100[1-9][0-9]{6,12}\Z")
_STAMP = Path("/app/content-ops-build-sha")
_ALLOWED_SECRETS = {"CONTENT_OPS_GATEWAY_TOKEN", "TELEGRAM_REVIEW_BOT_TOKEN",
                    "CONTENT_OPS_BUTTON_SIGNING_KEY", "CONTENT_OPS_EDIT_BINDING_KEY"}


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def _build_stamp():
    return _STAMP.read_text(encoding="ascii").strip()


def _enabled(env):
    value = env.get("CONTENT_OPS_BUTTON_CARD_ENABLED", "false")
    if value not in {"true", "false"}:
        raise ValueError("private_card_flag_invalid")
    return value == "true"


@dataclass(frozen=True, repr=False)
class PrivateCardRuntimeSettings:
    workspace_id: str
    version_id: str
    release_sha: str
    gateway_token: str = field(repr=False)
    bot_token: str = field(repr=False)
    bot_id: int
    chat_id: int
    signing_key: bytes = field(repr=False)
    binding_key: bytes = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, stamp_reader: Callable[[], str] | None = None):
        _enabled(env)
        if (env.get("CONTENT_OPS_REVIEW_ENABLED", "false") != "false"
            or env.get("CONTENT_OPS_REVIEW_MODE") != "canary"
            or env.get("CONTENT_OPS_REVIEW_PACKET_MODE") != "button_card_v1"
            or env.get("CONTENT_OPS_GATEWAY_URL") != APP_ORIGIN):
            raise ValueError("private_card_scope_invalid")
        for name, value in env.items():
            upper = name.upper()
            if not value or upper in _ALLOWED_SECRETS:
                continue
            if (upper.startswith(("SUPABASE_", "DATABASE_", "POSTGRES_", "TYPEFULLY_",
                                   "XAI_", "OPENAI_", "TWITTER_", "X_BEARER_"))
                or upper in {"API_SECRET", "PUBLICATION_WORKER_TOKEN"}
                or (upper.startswith("TELEGRAM_")
                    and upper not in {"TELEGRAM_REVIEW_CHAT_ID"})
                or (upper.endswith(("_KEY", "_SECRET", "_TOKEN"))
                    and upper not in _ALLOWED_SECRETS)):
                raise ValueError("private_card_credential_boundary_invalid")
        workspace = env.get("CONTENT_STUDIO_WORKSPACE_ID", "")
        version = env.get("CONTENT_OPS_REVIEW_CANARY_VERSION_ID", "")
        release = env.get("CONTENT_OPS_REVIEW_RELEASE_SHA", "")
        runtime = env.get("RAILWAY_GIT_COMMIT_SHA", "")
        build = (stamp_reader or _build_stamp)()
        gateway_token = env.get("CONTENT_OPS_GATEWAY_TOKEN", "")
        bot_token = env.get("TELEGRAM_REVIEW_BOT_TOKEN", "")
        room = env.get("TELEGRAM_REVIEW_CHAT_ID", "")
        signing = env.get("CONTENT_OPS_BUTTON_SIGNING_KEY", "")
        binding = env.get("CONTENT_OPS_EDIT_BINDING_KEY", "")
        bot_match = _BOT.fullmatch(bot_token)
        if (not _uuid(workspace) or not _uuid(version)
            or type(release) is not str or _SHA40.fullmatch(release) is None
            or type(runtime) is not str or _SHA40.fullmatch(runtime) is None
            or type(build) is not str or _SHA40.fullmatch(build) is None
            or not secrets.compare_digest(release, runtime)
            or not secrets.compare_digest(release, build)
            or _TOKEN.fullmatch(gateway_token) is None
            or bot_match is None or _ROOM.fullmatch(room) is None
            or _HEX64.fullmatch(signing) is None or _HEX64.fullmatch(binding) is None
            or len({gateway_token, bot_token, signing, binding}) != 4):
            raise ValueError("private_card_configuration_invalid")
        return cls(workspace, version, release, gateway_token, bot_token,
            int(bot_match.group(1)), int(room), bytes.fromhex(signing),
            bytes.fromhex(binding))


def build_runner(settings: PrivateCardRuntimeSettings):
    gateway = ButtonCanaryGateway(origin=APP_ORIGIN,
        gateway_token=settings.gateway_token, release_sha=settings.release_sha,
        content_version_id=settings.version_id, enabled=True)
    owner = GatewayPrivateCardOwner(gateway, enabled=True)
    sender = TelegramPrivateCardSender(bot_token=settings.bot_token,
        bot_id=settings.bot_id, chat_id=settings.chat_id)
    signer, bindings = ButtonSigner(settings.signing_key), EditBindings(settings.binding_key)
    courier = PrivateCardCourier(owner, sender, signer, bindings)
    return PrivateCardCanary(workspace_id=settings.workspace_id,
        content_version_id=settings.version_id, bot_id=settings.bot_id,
        chat_id=settings.chat_id, gateway=gateway, owner=owner,
        png_reader=gateway, courier=courier, signer=signer, bindings=bindings)


def run(*, validate_only=False, environ: Mapping[str, str] | None = None,
        stamp_reader: Callable[[], str] | None = None,
        runner_factory=build_runner):
    env = os.environ if environ is None else environ
    mode = "validate_only" if validate_only else "run"
    try:
        enabled = _enabled(env)
        if not enabled and not validate_only:
            return {"ok": True, "mode": mode, "enabled": False,
                    "public_send_attempted": False}
        settings = PrivateCardRuntimeSettings.from_env(env, stamp_reader=stamp_reader)
        if validate_only:
            return {"ok": True, "mode": mode, "enabled": enabled,
                    "network_calls": False, "database_calls": False,
                    "telegram_calls": False}
        result = asyncio.run(runner_factory(settings).run(enabled=True))
        if (type(result) is not dict or result.get("public_send_attempted") is not False
            or not set(result) <= {"status", "confirmed_parts", "public_send_attempted"}
            or result.get("status") not in {"card_recorded", "no_candidate", "blocked",
                                            "outbox_unknown", "delivery_unknown"}
            or ("confirmed_parts" in result and (type(result["confirmed_parts"]) is not int
                or not 0 <= result["confirmed_parts"] <= 4))):
            raise ValueError("private_card_outcome_invalid")
        return {"ok": result["status"] in {"card_recorded", "no_candidate"},
                "mode": mode, "enabled": True, **result}
    except Exception:
        return {"ok": False, "mode": mode, "error": "private_card_canary_failed",
                **({"network_calls": False, "database_calls": False,
                    "telegram_calls": False} if validate_only else {})}


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description="One exact-version private review canary; default OFF.")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    result = run(validate_only=args.validate_only)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
