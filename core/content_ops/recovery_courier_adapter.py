"""Exact recovery transport for the existing private-card protocol.

A plan is a pure pinned value, not an activation grant. Only fresh database
authority through the existing gateway permits effects. No original outbox,
trial owner, timestamp or artifact is reset by this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID

import httpx

ORIGIN = "https://coineasy-newscard.netlify.app"
PATH = "/.netlify/functions/content-ops-review"
PLAN_KEYS = frozenset({"schema_version", "recovery_id", "workspace_id", "client_id", "outbox_id",
    "content_item_id", "content_version_id", "source_item_id", "generate_job_id", "banner_asset_id",
    "banner_sha256", "telegram_copy_sha256", "x_copy_sha256", "original_outbox_sha256",
    "original_trial_id", "original_plan_sha256", "destination_policy_sha256", "release_sha",
    "not_before", "deadline", "estimated_daily_limit_usd_micros"})
IDENTITY_HEADERS = {"recovery_id": "x-content-ops-recovery-id",
                    "plan_sha256": "x-content-ops-recovery-plan-sha256"}


class RecoveryCourierError(ValueError):
    """Fixed diagnostic only; never retain remote text or credentials."""


def require(value):
    if not value:
        raise RecoveryCourierError("private_recovery_blocked")


def unique_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf8")).hexdigest()


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, TypeError, AttributeError):
        return False


def _sha(value, size=64):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{" + str(size) + "}", value) is not None


def _stamp(value):
    require(type(value) is str and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value))
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True, repr=False)
class RecoveryPlan:
    _json: str = field(repr=False)
    sha256: str

    @classmethod
    def from_mapping(cls, value, expected_sha256):
        try:
            require(type(value) is dict and set(value) == PLAN_KEYS
                and value["schema_version"] == "private-review-recovery@1" and value["client_id"] == "babylon"
                and type(value["estimated_daily_limit_usd_micros"]) is int
                and value["estimated_daily_limit_usd_micros"] == 10_000_000)
            require(all(_uuid(value[k]) for k in PLAN_KEYS if k.endswith("_id") and k != "client_id"))
            require(all(_sha(value[k]) for k in PLAN_KEYS if k.endswith("_sha256"))
                and _sha(value["release_sha"], 40) and _sha(expected_sha256))
            start, end = _stamp(value["not_before"]), _stamp(value["deadline"])
            require(0 < (end - start).total_seconds() <= 7200
                and canonical_sha256(value) == expected_sha256)
            return cls(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False), expected_sha256)
        except Exception:
            raise RecoveryCourierError("private_recovery_plan_invalid") from None

    @property
    def payload(self):
        return json.loads(self._json)

    @property
    def not_before(self):
        return _stamp(self.payload["not_before"])

    @property
    def deadline(self):
        return _stamp(self.payload["deadline"])

    @property
    def scope(self):
        p = self.payload
        return {"mode": "recovery", "packet_mode": "recovery_delivery_only_v1",
            "content_version_id": p["content_version_id"], "recovery_id": p["recovery_id"], "plan_sha256": self.sha256}


def window(plan, clock):
    now = clock()
    require(type(plan) is RecoveryPlan and type(now) is datetime and now.utcoffset() is not None
        and plan.not_before <= now < plan.deadline)
    return now


def recovery_headers(plan, token):
    p = plan.payload
    return {"Authorization": "Bearer " + token, "X-Content-Ops-Expected-Release-Sha": p["release_sha"],
        "X-Content-Ops-Mode": "recovery", "X-Content-Ops-Packet-Mode": "recovery_delivery_only_v1",
        "X-Content-Ops-Version-Id": p["content_version_id"], "X-Content-Ops-Recovery-Id": p["recovery_id"],
        "X-Content-Ops-Recovery-Plan-Sha256": plan.sha256,
        "Accept": "application/json", "Accept-Encoding": "identity"}


async def bounded_json(response):
    try:
        require(response.status_code == 200 and not response.is_redirect
            and response.headers.get("content-type", "").split(";", 1)[0] == "application/json"
            and response.headers.get("content-encoding", "identity") == "identity"
            and "content-range" not in response.headers)
        declared = response.headers.get("content-length")
        require(declared is None or (declared.isdecimal() and 0 < int(declared) <= 16384))
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            require(len(raw) + len(chunk) <= 16384)
            raw.extend(chunk)
        require(declared is None or len(raw) == int(declared))
        return unique_json(raw)
    finally:
        await response.aclose()


def envelope(value, plan, key):
    require(type(value) is dict and set(value) == {"ok", "release_sha", "scope", key}
        and value["ok"] is True and value["release_sha"] == plan.payload["release_sha"]
        and value["scope"] == plan.scope)
    return value[key]


class RecoveryStateClient:
    """Read-only status; a failed read latches closed, never polls/retries."""
    def __init__(self, plan, token, *, transport=None, clock=None):
        require(type(plan) is RecoveryPlan)
        self.plan, self.token = plan, token
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.failed, self.prior = False, None
        self.http = httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False,
            transport=transport or httpx.AsyncHTTPTransport(retries=0))

    async def require_active(self):
        try:
            require(not self.failed)
            before = window(self.plan, self.clock)
            require(self.prior is None or before >= self.prior)
            request = self.http.build_request("POST", ORIGIN + PATH,
                headers=recovery_headers(self.plan, self.token), json={"action": "status"})
            response = await self.http.send(request, stream=True)
            value = envelope(await bounded_json(response), self.plan, "status")
            now = window(self.plan, self.clock)
            require(now >= before and type(value) is dict
                and set(value) == {"active", "observed_at", "plan", "plan_sha256"}
                and value["active"] is True and value["plan"] == self.plan.payload
                and value["plan_sha256"] == self.plan.sha256
                and canonical_sha256(value["plan"]) == self.plan.sha256
                and type(value["observed_at"]) is str
                and re.search(r"(?:Z|[+-]\d{2}:\d{2})$", value["observed_at"]))
            observed = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
            require(observed.utcoffset() is not None and 0 <= (now - observed).total_seconds() <= 5)
            self.prior = now
            return now
        except Exception:
            self.failed = True
            raise RecoveryCourierError("private_recovery_state_unknown") from None

    async def aclose(self):
        await self.http.aclose()


class RecoveryGatewayTransport(httpx.AsyncBaseTransport):
    """Use real canary/owner objects, adapting only their exact wire scope.

    A single terminal read after registration (even a lost ACK) is the sole
    exception to the closed latch/window: it reconciles, never authorizes send.
    """
    def __init__(self, *, plan, backend, state, clock, enabled=False):
        require(type(plan) is RecoveryPlan and isinstance(backend, httpx.AsyncBaseTransport)
            and callable(clock) and type(enabled) is bool)
        self.plan, self.backend, self.state, self.clock, self.enabled = plan, backend, state, clock, enabled
        self.internal_scope = {"mode": "canary", "content_version_id": plan.payload["content_version_id"],
                               "packet_mode": "button_card_v1"}
        self.failed = self.terminal_attempted = False
        self.registration = None

    async def handle_async_request(self, request):
        response = None
        try:
            p = self.plan.payload
            require(self.enabled and request.method == "POST" and str(request.url) == ORIGIN + PATH
                and request.headers.get("x-content-ops-mode") == "canary"
                and request.headers.get("x-content-ops-version-id") == p["content_version_id"]
                and request.headers.get("x-content-ops-packet-mode") == "button_card_v1"
                and request.headers.get("x-content-ops-expected-release-sha") == p["release_sha"]
                and "x-content-ops-daily-scope" not in request.headers
                and not any(k.startswith("x-content-ops-trial-") or k.startswith("x-content-ops-recovery-") for k in request.headers))
            raw = await request.aread()
            require(len(raw) <= 8192)
            body = unique_json(raw)
            require(type(body) is dict and body.get("action") in {"reconcile", "claim", "image", "begin", "owner"})
            terminal = body["action"] == "owner" and body.get("step") == "terminal"
            if terminal:
                require(not self.terminal_attempted and self.registration is not None
                    and set(body) == {"action", "step", "args"} and type(body["args"]) is dict
                    and body["args"] == {**self.registration, "outbox_id": p["outbox_id"]})
                self.terminal_attempted = True
            else:
                require(not self.failed and not self.terminal_attempted and self.registration is None)
                window(self.plan, self.clock)
                await self.state.require_active()
                window(self.plan, self.clock)
            if body["action"] == "owner" and body.get("step") == "register":
                args = body.get("args")
                require(type(args) is dict and _uuid(args.get("review_id")) and _uuid(args.get("card_id")))
                self.registration = {"review_id": args["review_id"], "card_id": args["card_id"]}
            headers = dict(request.headers)
            headers.update({k.lower(): v for k, v in recovery_headers(self.plan, "").items() if k != "Authorization"})
            wire = httpx.Request("POST", ORIGIN + PATH, headers=headers, content=raw)
            response = await self.backend.handle_async_request(wire)
            if not terminal:
                window(self.plan, self.clock)
            if body["action"] == "image":
                require(response.status_code == 200
                    and response.headers.get("x-content-ops-mode") == "recovery"
                    and response.headers.get("x-content-ops-packet-mode") == "recovery_delivery_only_v1"
                    and response.headers.get(IDENTITY_HEADERS["recovery_id"]) == p["recovery_id"]
                    and response.headers.get(IDENTITY_HEADERS["plan_sha256"]) == self.plan.sha256)
                return response  # The real gateway verifies bounded PNG bytes/hash.
            value = await bounded_json(response)
            key = {"reconcile": "queued", "claim": "claim", "begin": "accepted", "owner": "owner"}[body["action"]]
            payload = envelope(value, self.plan, key)
            if body["action"] == "claim" and payload is not None:
                self._bind_claim(payload)
            if terminal:
                require(type(payload) is dict and set(payload) == {"status", "recovery_id", "card_id", "outbox_id",
                    "original_outbox_status", "execution_authorized"}
                    and payload["status"] == "recovery_sent" and payload["recovery_id"] == p["recovery_id"]
                    and payload["card_id"] == self.registration["card_id"] and payload["outbox_id"] == p["outbox_id"]
                    and payload["original_outbox_status"] == "claimed" and payload["execution_authorized"] is False)
                payload = {"status": "sent", "card_id": payload["card_id"], "outbox_id": payload["outbox_id"],
                           "execution_authorized": False}
            return httpx.Response(200, json={**value, "scope": self.internal_scope, key: payload})
        except Exception:
            self.failed = True
            if response is not None:
                await response.aclose()
            raise RecoveryCourierError("private_recovery_gateway_unknown") from None

    def _bind_claim(self, claim):
        p = self.plan.payload
        require(type(claim) is dict and all(claim.get(k) == p[k] for k in (
            "outbox_id", "client_id", "content_item_id", "content_version_id", "source_item_id", "generate_job_id", "banner_sha256")))
        for key in ("telegram_copy", "x_copy"):
            require(type(claim.get(key)) is str
                and hashlib.sha256(claim[key].encode("utf8")).hexdigest() == p[key + "_sha256"])

    async def aclose(self):
        pass  # Per-operation clients do not own the shared one-shot backend.


def build_recovery_session(*, plan, backend, state, clock, gateway_token, enabled=False):
    from core.content_ops.private_review_card_gateway import ButtonCanaryGateway
    from core.content_ops.private_review_card_owner_gateway import GatewayPrivateCardOwner
    transport = RecoveryGatewayTransport(plan=plan, backend=backend, state=state, clock=clock, enabled=enabled)
    gateway = ButtonCanaryGateway(origin=ORIGIN, gateway_token=gateway_token,
        release_sha=plan.payload["release_sha"], content_version_id=plan.payload["content_version_id"],
        enabled=enabled, transport=transport, clock=clock)
    return gateway, GatewayPrivateCardOwner(gateway, enabled=enabled), transport
