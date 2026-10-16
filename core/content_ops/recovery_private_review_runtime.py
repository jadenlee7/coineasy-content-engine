"""Single-use recovery on the existing courier; never generation or reset."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from core.content_ops.private_review_bot_policy import ExistingReviewBotPolicy
from core.content_ops.private_review_card_canary import PrivateCardCanary
from core.content_ops.private_review_card_courier import PrivateCardCourier
from core.content_ops.private_review_card_sender import TelegramPrivateCardSender
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.recovery_courier_adapter import (
    RecoveryPlan, RecoveryStateClient, RecoveryCourierError, build_recovery_session,
    canonical_sha256, require, unique_json, window,
)
from scripts.run_private_review_card_canary import _private_card_identity, _validated_scope_and_provenance

OFF_FLAGS = ("CONTENT_OPS_REVIEW_ENABLED", "CONTENT_OPS_BUTTON_CARD_ENABLED",
    "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED", "CONTENT_OPS_TRIAL_COURIER_ENABLED",
    "CONTENT_OPS_GATEWAY_ENABLED", "CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED",
    "CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED", "CONTENT_OPS_TRIAL_GATEWAY_ENABLED",
    "TELEGRAM_PUBLICATION_ENABLED", "STUDIO_TELEGRAM_PUBLISH_ENABLED")


def enabled(env):
    value = env.get("CONTENT_OPS_RECOVERY_COURIER_ENABLED", "false")
    require(value in {"true", "false"})
    return value == "true"


def validate_configuration(env, *, stamp_reader=None, validate_only=False):
    """Local release/policy/optional OFF plan only; no client construction."""
    active = enabled(env)
    require(all(env.get(key, "false") == "false" for key in OFF_FLAGS))
    # The shared credential boundary rejects every extra TELEGRAM_* value,
    # including this verified OFF flag. Filter only that literal false from
    # its input; keep the original runtime environment and all secret checks.
    provenance_env = {key: value for key, value in env.items()
        if not (key == "TELEGRAM_PUBLICATION_ENABLED" and value == "false")}
    proof = _validated_scope_and_provenance(provenance_env, stamp_reader=stamp_reader, daily=True,
        allow_missing_runtime_sha=validate_only and not active)
    raw = env.get("CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON")
    policy_value = unique_json(raw)
    require(type(policy_value) is dict)
    policy = ExistingReviewBotPolicy.from_json(raw, bot_id=policy_value.get("bot_id"), chat_id=policy_value.get("chat_id"))
    plan_raw, plan_sha = (env.get(key, "") for key in ("CONTENT_OPS_RECOVERY_PLAN_JSON", "CONTENT_OPS_RECOVERY_PLAN_SHA256"))
    if plan_raw == plan_sha == "":
        require(validate_only and not active)
        plan = None
    else:
        require(type(plan_raw) is str and 0 < len(plan_raw.encode("utf8")) <= 16384)
        plan = RecoveryPlan.from_mapping(unique_json(plan_raw), plan_sha)
        require(plan.payload["release_sha"] == proof.release_sha
            and plan.payload["destination_policy_sha256"] == canonical_sha256(policy_value)
            and plan.payload["workspace_id"] == env.get("CONTENT_STUDIO_WORKSPACE_ID"))
    return plan, policy, proof


@dataclass(frozen=True, repr=False)
class RecoveryCourierSettings:
    plan: RecoveryPlan
    gateway_token: str = field(repr=False)
    bot_token: str = field(repr=False)
    bot_id: int
    chat_id: int
    signing_key: bytes = field(repr=False)
    binding_key: bytes = field(repr=False)
    bot_policy: ExistingReviewBotPolicy = field(repr=False)
    provenance: dict

    @classmethod
    def from_env(cls, env, *, stamp_reader=None):
        require(enabled(env))
        plan, policy, proof = validate_configuration(env, stamp_reader=stamp_reader)
        workspace, gateway, bot, bid, room, signing, binding, identity_policy = _private_card_identity(env)
        require(type(plan) is RecoveryPlan and workspace == plan.payload["workspace_id"]
            and type(identity_policy) is ExistingReviewBotPolicy and identity_policy == policy)
        return cls(plan, gateway, bot, bid, room, signing, binding, policy, proof.receipt())


class RecoveryTelegramTransport(httpx.AsyncBaseTransport):
    """Fresh DB authority immediately before every existing-bot HTTP request."""
    def __init__(self, backend, state, settings, clock):
        self.backend, self.state, self.settings, self.clock = backend, state, settings, clock
        self.failed = False

    async def handle_async_request(self, request):
        try:
            s = self.settings
            require(not self.failed and request.method == "POST"
                and str(request.url).rsplit("/", 1)[0] == "https://api.telegram.org/bot" + s.bot_token
                and request.url.path.rsplit("/", 1)[-1] in {"getMe", "getChat", "getChatMember", "sendPhoto", "sendMessage"})
            window(s.plan, self.clock)
            await self.state.require_active()
            window(s.plan, self.clock)
            return await self.backend.handle_async_request(request)
        except Exception:
            self.failed = True
            raise RecoveryCourierError("private_recovery_transport_unknown") from None

    async def aclose(self):
        pass  # The composition owner closes the backend once.


class ExactRecoveryRunner:
    def __init__(self, settings, state, clock, *, backend_factory=None):
        self.settings, self.state, self.clock = settings, state, clock
        self.backend_factory = backend_factory or (lambda: httpx.AsyncHTTPTransport(retries=0))
        self.attempted = False

    async def run(self):
        require(not self.attempted)
        self.attempted = True
        s = self.settings
        await self.state.require_active()
        gateway_backend = telegram_backend = None
        try:
            gateway_backend, telegram_backend = self.backend_factory(), self.backend_factory()
            gateway, owner, _ = build_recovery_session(plan=s.plan, backend=gateway_backend,
                state=self.state, clock=self.clock, gateway_token=s.gateway_token, enabled=True)
            sender = TelegramPrivateCardSender(bot_token=s.bot_token, bot_id=s.bot_id, chat_id=s.chat_id,
                bot_policy=s.bot_policy, clock=self.clock, delivery_only=True,
                transport=RecoveryTelegramTransport(telegram_backend, self.state, s, self.clock))
            signer, bindings = ButtonSigner(s.signing_key), EditBindings(s.binding_key)
            courier = PrivateCardCourier(owner, sender, signer, bindings, clock=lambda: int(self.clock().timestamp()))
            canary = PrivateCardCanary(workspace_id=s.plan.payload["workspace_id"],
                content_version_id=s.plan.payload["content_version_id"], bot_id=s.bot_id, chat_id=s.chat_id,
                gateway=gateway, owner=owner, png_reader=gateway, courier=courier, signer=signer, bindings=bindings,
                clock=self.clock, bot_policy=s.bot_policy, delivery_only=True)
            return await canary.run(enabled=True)
        finally:
            for backend in (gateway_backend, telegram_backend):
                if backend is not None:
                    await backend.aclose()


def result(status, *, attempted=0, cards=0, parts=0):
    return {"ok": status in {"disabled", "not_due", "expired", "completed"}, "status": status,
        "attempted_cards": attempted, "cards_recorded": cards, "confirmed_parts": parts,
        "invocation_dispatch_enabled": False, "public_send_attempted": False}


async def run_recovery(settings, *, clock=None, state_factory=RecoveryStateClient, runner_factory=ExactRecoveryRunner):
    clock = clock or (lambda: datetime.now(timezone.utc))
    state, attempted = None, 0
    try:
        now = clock()
        require(type(now) is datetime and now.utcoffset() is not None)
        if now < settings.plan.not_before:
            return result("not_due")  # No polling/schedule or pre-window I/O.
        if now >= settings.plan.deadline:
            return result("expired")
        state = state_factory(settings.plan, settings.gateway_token, clock=clock)
        await state.require_active()
        attempted = 1
        outcome = await runner_factory(settings, state, clock).run()
        require(type(outcome) is dict and set(outcome) <= {"status", "confirmed_parts", "public_send_attempted"}
            and outcome.get("public_send_attempted") is False)
        parts = outcome.get("confirmed_parts", 0)
        require(type(parts) is int and 0 <= parts <= 4)
        if outcome.get("status") == "card_recorded" and parts == 4:
            return result("completed", attempted=1, cards=1, parts=4)
        return result("delivery_unknown" if outcome.get("status") in {"delivery_unknown", "outbox_unknown"} else "blocked",
            attempted=1, parts=parts)
    except Exception:
        return result("delivery_unknown" if attempted else "blocked", attempted=attempted)
    finally:
        if state is not None:
            await state.aclose()
