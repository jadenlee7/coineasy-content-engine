"""Fixed-window, once-per-process private YBO courier; no DB/admin/provider key.

The existing database claim/part ledger is the restart duplicate authority.
There is no automatic recovery from an unknown send or owner acknowledgement.
The callback bot is not started here; all packets are delivery-only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
import re

import httpx

from core.content_ops.private_review_bot_policy import ExistingReviewBotPolicy
from core.content_ops.private_review_card_canary import PrivateCardCanary
from core.content_ops.private_review_card_courier import PrivateCardCourier
from core.content_ops.private_review_card_sender import TelegramPrivateCardSender
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.trial_courier_adapter import (
    ORIGIN, TrialManifest, build_exact_session, canonical_sha256,
)
from core.content_ops.trial_window import ELIGIBLE_CLIENTS, TRIAL_ID, trial_window
from scripts.run_private_review_card_canary import _private_card_identity, _validated_scope_and_provenance

STATE_PATH = "/.netlify/functions/content-ops-trial-state"
MAX_STATE_BYTES = 16_384


class TrialRuntimeError(ValueError):
    """Fixed diagnostic only; never include identities, transport URLs or keys."""


def require(value):
    if not value:
        raise TrialRuntimeError("trial_private_courier_blocked")


def enabled(env):
    value = env.get("CONTENT_OPS_TRIAL_COURIER_ENABLED", "false")
    require(value in {"true", "false"})
    return value == "true"


@dataclass(frozen=True, repr=False)
class TrialCourierSettings:
    workspace_id: str
    gateway_token: str
    bot_token: str
    bot_id: int
    chat_id: int
    signing_key: bytes
    binding_key: bytes
    bot_policy: ExistingReviewBotPolicy
    release_sha: str
    trial_id: str
    plan_sha256: str
    policy_sha256: str
    not_before: datetime
    deadline: datetime
    provenance: dict

    @classmethod
    def from_env(cls, env, *, stamp_reader=None, validate_only=False):
        is_enabled = enabled(env)
        require(all(env.get(k, "false") == "false" for k in (
            "CONTENT_OPS_REVIEW_ENABLED", "CONTENT_OPS_BUTTON_CARD_ENABLED", "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED")))
        proof = _validated_scope_and_provenance(env, stamp_reader=stamp_reader,
            allow_missing_runtime_sha=validate_only and not is_enabled, daily=True)
        workspace, gateway, bot, bid, room, signing, binding, policy = _private_card_identity(env)
        require(type(policy) is ExistingReviewBotPolicy)
        trial, plan, policy_sha = (env.get(k, "") for k in (
            "CONTENT_OPS_TRIAL_ID", "CONTENT_OPS_TRIAL_PLAN_SHA256", "CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256"))
        require(trial == TRIAL_ID and all(type(v) is str and re.fullmatch(r"[a-f0-9]{64}", v) for v in (plan, policy_sha)))
        start, end = trial_window(env.get("CONTENT_OPS_TRIAL_NOT_BEFORE"), env.get("CONTENT_OPS_TRIAL_DEADLINE"))
        # Hash the existing validated policy projection, never a newly inferred role.
        require(canonical_sha256(json.loads(env["CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON"])) == policy_sha)
        return cls(workspace, gateway, bot, bid, room, signing, binding, policy,
                   proof.release_sha, trial, plan, policy_sha, start, end, proof.receipt())


def window(clock, *, manifest=None, settings=None):
    now = clock()
    bound = manifest if manifest is not None else settings
    require(bound is not None and type(now) is datetime and now.utcoffset() is not None
            and bound.not_before <= now < bound.deadline)
    return now


class TrialStateClient:
    """Existing gateway token only. Exactly one attempt per read; no fallback."""
    def __init__(self, settings, *, transport=None, clock=None):
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.http = httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False,
            transport=transport or httpx.AsyncHTTPTransport(retries=0))

    async def read(self):
        window(self.clock, settings=self.settings)
        response = None
        try:
            request = self.http.build_request("POST", ORIGIN + STATE_PATH,
                headers={"Authorization": "Bearer " + self.settings.gateway_token,
                    "X-Content-Ops-Expected-Release-Sha": self.settings.release_sha,
                    "Accept-Encoding": "identity"},
                json={"trial_id": self.settings.trial_id, "plan_sha256": self.settings.plan_sha256})
            response = await self.http.send(request, stream=True)
            require(response.status_code in {200, 409} and not response.is_redirect
                and response.headers.get("content-type", "").split(";", 1)[0] == "application/json"
                and response.headers.get("x-content-ops-release-sha") == self.settings.release_sha
                and response.headers.get("content-encoding", "identity") == "identity"
                and "content-range" not in response.headers)
            declared = response.headers.get("content-length")
            require(declared is None or (declared.isdigit() and int(declared) <= MAX_STATE_BYTES))
            data = bytearray()
            async for chunk in response.aiter_bytes():
                require(len(data) + len(chunk) <= MAX_STATE_BYTES)
                data.extend(chunk)
            require(declared is None or int(declared) == len(data))
            observed = window(self.clock, settings=self.settings)
            value = json.loads(data)
            if response.status_code == 409:
                require(value == {"error": "trial_manifest_not_ready"})
                return None
            require(type(value) is dict and set(value) == {"active", "observed_at", "manifest", "manifest_sha256"}
                and value["active"] is True)
            stamp = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
            require(stamp.utcoffset() is not None and 0 <= (observed-stamp).total_seconds() <= 5)
            manifest = TrialManifest.from_mapping(value["manifest"], value["manifest_sha256"])
            p = manifest.payload
            require(p["trial_id"] == self.settings.trial_id and p["plan_sha256"] == self.settings.plan_sha256
                and p["release_sha"] == self.settings.release_sha and p["destination_policy_sha256"] == self.settings.policy_sha256
                and manifest.not_before == self.settings.not_before and manifest.deadline == self.settings.deadline)
            window(self.clock, manifest=manifest)
            return manifest
        except Exception:
            raise TrialRuntimeError("trial_private_state_unknown") from None
        finally:
            if response is not None:
                await response.aclose()

    async def require_same(self, manifest):
        current = await self.read()
        require(type(current) is TrialManifest and current.sha256 == manifest.sha256
                and current.payload == manifest.payload)
        return window(self.clock, manifest=manifest)

    async def aclose(self):
        await self.http.aclose()


class FreshStateTransport(httpx.AsyncBaseTransport):
    """Fresh server admission immediately before each existing bot HTTP call."""
    def __init__(self, backend, state, manifest, settings, clock):
        self.backend, self.state, self.manifest = backend, state, manifest
        self.settings, self.clock, self.failed = settings, clock, False

    async def handle_async_request(self, request):
        try:
            require(not self.failed and request.method == "POST"
                and str(request.url).rsplit("/", 1)[0] == "https://api.telegram.org/bot" + self.settings.bot_token
                and request.url.path.rsplit("/", 1)[-1] in {"getMe", "getChat", "getChatMember", "sendPhoto", "sendMessage"})
            window(self.clock, manifest=self.manifest)
            await self.state.require_same(self.manifest)
            window(self.clock, manifest=self.manifest)
            return await self.backend.handle_async_request(request)
        except Exception:
            self.failed = True
            raise TrialRuntimeError("trial_private_transport_unknown") from None

    async def aclose(self):
        pass  # Explicit runner owner closes shared backend once, never each part.


class ExactTrialRunner:
    def __init__(self, settings, manifest, entry, state, clock, *, backend_factory=None):
        self.settings, self.manifest, self.entry, self.state, self.clock = settings, manifest, entry, state, clock
        self.backend_factory = backend_factory or (lambda: httpx.AsyncHTTPTransport(retries=0))
        self.attempted = False

    async def run(self):
        require(not self.attempted)
        self.attempted = True
        await self.state.require_same(self.manifest)
        s = self.settings
        gateway_backend, telegram_backend = self.backend_factory(), self.backend_factory()
        try:
            gateway, owner, _ = build_exact_session(manifest=self.manifest, version=self.entry["content_version_id"],
                backend=gateway_backend, before_side_effect=lambda: window(self.clock, manifest=self.manifest),
                clock=self.clock, gateway_token=s.gateway_token, runtime_release_sha=s.release_sha, enabled=True)
            transport = FreshStateTransport(telegram_backend, self.state, self.manifest, s, self.clock)
            sender = TelegramPrivateCardSender(bot_token=s.bot_token, bot_id=s.bot_id, chat_id=s.chat_id,
                bot_policy=s.bot_policy, clock=self.clock, transport=transport, delivery_only=True)
            signer, bindings = ButtonSigner(s.signing_key), EditBindings(s.binding_key)
            courier = PrivateCardCourier(owner, sender, signer, bindings,
                clock=lambda: int(self.clock().timestamp()))
            canary = PrivateCardCanary(workspace_id=s.workspace_id, content_version_id=self.entry["content_version_id"],
                bot_id=s.bot_id, chat_id=s.chat_id, gateway=gateway, owner=owner, png_reader=gateway,
                courier=courier, signer=signer, bindings=bindings, clock=self.clock,
                bot_policy=s.bot_policy, delivery_only=True)
            return await canary.run(enabled=True)
        finally:
            await gateway_backend.aclose()
            await telegram_backend.aclose()


async def run_trial(settings, *, clock=None, sleep=asyncio.sleep, state_factory=TrialStateClient,
                    runner_factory=ExactTrialRunner):
    clock = clock or (lambda: datetime.now(timezone.utc))
    state, cards, parts, attempted = None, 0, 0, 0
    status = "blocked"
    try:
        start, end = settings.not_before, settings.deadline
        prior = clock()
        require(type(prior) is datetime and prior.utcoffset() is not None and start-prior <= timedelta(days=1))
        for _ in range(2881):
            now = clock()
            require(now >= prior); prior = now
            if now >= end:
                status = "expired"; return result(status, cards, parts, attempted)
            if now >= start: break
            await sleep(min(30, (start-now).total_seconds()))
        else: raise TrialRuntimeError("trial_private_wait_bound")
        state = state_factory(settings, clock=clock)
        manifest = None
        for _ in range(481):
            now = clock(); require(now >= prior); prior = now
            if now >= end:
                status = "expired"; return result(status, cards, parts, attempted)
            manifest = await state.read()
            if manifest is not None: break
            await sleep(min(15, max(0, (end-clock()).total_seconds())))
        require(type(manifest) is TrialManifest)
        require(manifest.not_before == start and manifest.deadline == end)
        for client in ELIGIBLE_CLIENTS:
            entries = [e for e in manifest.payload["entries"] if e["client_id"] == client]
            if not entries: continue
            window(clock, manifest=manifest)
            await state.require_same(manifest)
            attempted += 1
            outcome = await runner_factory(settings, manifest, entries[0], state, clock).run()
            require(type(outcome) is dict and outcome.get("public_send_attempted") is False)
            count = outcome.get("confirmed_parts", 0)
            require(type(count) is int and 0 <= count <= 4)
            parts += count
            if outcome.get("status") != "card_recorded" or count != 4:
                status = "delivery_unknown" if outcome.get("status") in {"delivery_unknown", "outbox_unknown"} else "blocked"
                return result(status, cards, parts, attempted)
            cards += 1
        return result("completed", cards, parts, attempted)
    except Exception:
        return result("delivery_unknown" if attempted else "blocked", cards, parts, attempted)
    finally:
        if state is not None:
            await state.aclose()


def result(status, cards=0, parts=0, attempted=0):
    return {"ok": status in {"completed", "disabled", "expired"}, "status": status,
        "attempted_cards": attempted, "cards_recorded": cards, "confirmed_parts": parts,
        "invocation_dispatch_enabled": False, "public_send_attempted": False}
