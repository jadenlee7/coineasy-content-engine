"""Exact-manifest trial gateway transport; default OFF, no credential discovery.

Reuses the existing immutable canary and private-owner protocol. Runtime
composition must independently attest image/release and fresh owner admission.
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
CLIENTS = frozenset({"yellow", "babylon", "origintrail"})
START = datetime(2026, 10, 14, tzinfo=timezone.utc)
END = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
IDENTITY_HEADERS = {"trial_id": "x-content-ops-trial-id",
                    "plan_sha256": "x-content-ops-trial-plan-sha256",
                    "manifest_sha256": "x-content-ops-trial-manifest-sha256"}
ENTRY_KEYS = frozenset({"client_id", "job_id", "request_id", "source_item_id",
    "source_sha256", "content_item_id", "content_version_id", "banner_sha256",
    "telegram_copy_sha256", "x_copy_sha256"})


class TrialCourierError(ValueError):
    """Bounded non-secret error. Unknown network outcomes are never retried."""


def _require(value):
    if not value:
        raise TrialCourierError("trial_courier_contract_blocked")


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _sha(value, length=64):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value) is not None


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, TypeError, AttributeError):
        return False


async def _bounded_json_bytes(response):
    data = bytearray()
    try:
        _require(response.status_code == 200
            and response.headers.get("content-type", "").split(";", 1)[0] == "application/json")
        async for chunk in response.aiter_bytes():
            _require(len(data) + len(chunk) <= 16384)
            data.extend(chunk)
        return bytes(data)
    finally:
        await response.aclose()


@dataclass(frozen=True, repr=False)
class TrialManifest:
    """Validated pure value, NOT server registration or execution authority."""
    _json: str = field(repr=False)
    sha256: str

    @classmethod
    def from_mapping(cls, value, expected_sha256):
        try:
            _require(type(value) is dict and set(value) == {"contract", "trial_id", "plan_sha256",
                "release_sha", "not_before", "deadline", "destination_policy_sha256", "entries"})
            _require(value["contract"] == "ybo-trial-courier-manifest@1"
                and _uuid(value["trial_id"]) and _sha(value["plan_sha256"])
                and _sha(value["release_sha"], 40) and _sha(value["destination_policy_sha256"])
                and value["not_before"] == "2026-10-14T00:00:00Z")
            end = datetime.fromisoformat(value["deadline"].replace("Z", "+00:00"))
            _require(value["deadline"] == end.isoformat(timespec="seconds").replace("+00:00", "Z")
                and end.utcoffset() is not None and START < end <= END)
            entries = value["entries"]
            _require(type(entries) is list and 1 <= len(entries) <= 3)
            clients = set()
            identities = set()
            for entry in entries:
                _require(type(entry) is dict and set(entry) == ENTRY_KEYS
                    and entry["client_id"] in CLIENTS and entry["client_id"] not in clients)
                clients.add(entry["client_id"])
                ids = [entry[k] for k in ("job_id", "request_id", "source_item_id", "content_version_id")]
                _require(entry["request_id"] == entry["content_item_id"]
                    and all(_uuid(v) for v in ids) and len(set(ids)) == 4
                    and not identities.intersection(ids))
                identities.update(ids)
                _require(all(_sha(entry[k]) for k in ("source_sha256", "banner_sha256",
                    "telegram_copy_sha256", "x_copy_sha256")))
            _require(_sha(expected_sha256) and canonical_sha256(value) == expected_sha256)
            return cls(json.dumps(value, sort_keys=True, separators=(",", ":")), expected_sha256)
        except Exception:
            raise TrialCourierError("trial_courier_manifest_invalid") from None

    @property
    def payload(self):
        return json.loads(self._json)

    def entry(self, version):
        matches = [x for x in self.payload["entries"] if x["content_version_id"] == version]
        _require(len(matches) == 1)
        return matches[0]


class DeadlineTransport(httpx.AsyncBaseTransport):
    """A supplied no-retry backend; invokes owner gate before EACH HTTP call.

    Suitable for a separately composed exact-policy Telegram sender too. The
    owner gate checks the persistent admission; this local clock is additional.
    The backend itself must not retry internally. There is no default backend.
    """
    def __init__(self, *, backend, manifest, before_side_effect, clock, enabled=False):
        _require(isinstance(backend, httpx.AsyncBaseTransport) and type(manifest) is TrialManifest
            and callable(before_side_effect) and callable(clock) and type(enabled) is bool)
        self.backend, self.manifest = backend, manifest
        self.before_side_effect, self.clock, self.enabled = before_side_effect, clock, enabled
        self._failed = False

    def gate(self):
        now = self.clock()
        end = datetime.fromisoformat(self.manifest.payload["deadline"].replace("Z", "+00:00"))
        _require(self.enabled and not self._failed and type(now) is datetime
            and now.utcoffset() is not None and START <= now < end)
        # Callback failure means no dispatch; never interpret a false return as
        # permission. The trusted orchestrator gate returns an aware datetime.
        observed = self.before_side_effect()
        _require(type(observed) is datetime and observed.utcoffset() is not None
            and START <= observed < end)

    async def handle_async_request(self, request):
        try:
            self.gate()
            return await self.backend.handle_async_request(request)
        except Exception:
            self._failed = True
            raise TrialCourierError("trial_courier_transport_unknown") from None

    async def aclose(self):
        # Engine creates an AsyncClient per operation; a shared one-shot
        # backend is closed explicitly by its composition owner, not per call.
        pass


class TrialGatewayTransport(DeadlineTransport):
    """Explicit wire compatibility for one immutable manifest entry."""
    def __init__(self, *, content_version_id, **kwargs):
        super().__init__(**kwargs)
        self.entry = self.manifest.entry(content_version_id)
        self.version = content_version_id
        p = self.manifest.payload
        self.identity = {"trial_id": p["trial_id"], "plan_sha256": p["plan_sha256"],
                         "manifest_sha256": self.manifest.sha256}
        self.internal_scope = {"mode": "canary", "content_version_id": self.version,
                               "packet_mode": "button_card_v1"}
        self.wire_scope = {**self.internal_scope, "mode": "trial", "packet_mode": "trial_delivery_only_v1", **self.identity}
        self.terminal_receipt = None

    async def handle_async_request(self, request):
        try:
            self.gate()
            _require(request.method == "POST" and str(request.url) == ORIGIN + PATH
                and request.headers.get("x-content-ops-mode") == "canary"
                and request.headers.get("x-content-ops-version-id") == self.version
                and request.headers.get("x-content-ops-packet-mode") == "button_card_v1"
                and request.headers.get("x-content-ops-expected-release-sha") == self.manifest.payload["release_sha"]
                and "x-content-ops-daily-scope" not in request.headers)
            data = await request.aread()
            _require(len(data) <= 8192)
            body = json.loads(data)
            _require(type(body) is dict and body.get("action") in {"reconcile", "claim", "image", "begin", "owner"})
            headers = dict(request.headers)
            headers["x-content-ops-mode"] = "trial"
            headers["x-content-ops-packet-mode"] = "trial_delivery_only_v1"
            headers.update({header: self.identity[key] for key, header in IDENTITY_HEADERS.items()})
            wire = httpx.Request("POST", ORIGIN + PATH, headers=headers, content=data)
            self.gate()
            response = await self.backend.handle_async_request(wire)
            if body["action"] == "image":
                # Existing gateway still streams and validates PNG length/hash.
                if not (response.status_code == 200
                        and response.headers.get("x-content-ops-mode") == "trial"
                        and response.headers.get("x-content-ops-packet-mode") == "trial_delivery_only_v1"
                        and all(response.headers.get(h) == self.identity[k]
                        for k, h in IDENTITY_HEADERS.items())):
                    await response.aclose()
                    raise TrialCourierError("trial_courier_gateway_unknown")
                return response
            raw = await _bounded_json_bytes(response)
            value = json.loads(raw)
            key = {"reconcile": "queued", "claim": "claim", "begin": "accepted", "owner": "owner"}[body["action"]]
            _require(type(value) is dict and set(value) == {"ok", "release_sha", "scope", key}
                and value["ok"] is True and value["release_sha"] == self.manifest.payload["release_sha"]
                and value["scope"] == self.wire_scope)
            if body["action"] == "claim" and value[key] is not None:
                self._bind_claim(value[key])
            if body["action"] == "owner" and body.get("step") == "terminal":
                self.terminal_receipt = value[key]
            return httpx.Response(200, json={**value, "scope": self.internal_scope})
        except Exception:
            self._failed = True
            raise TrialCourierError("trial_courier_gateway_unknown") from None

    def _bind_claim(self, claim):
        _require(type(claim) is dict)
        for source, target in (("client_id", "client_id"), ("generate_job_id", "job_id"),
            ("source_item_id", "source_item_id"), ("content_item_id", "content_item_id"),
            ("content_version_id", "content_version_id"), ("banner_sha256", "banner_sha256")):
            _require(claim.get(source) == self.entry[target])
        for text_key in ("telegram_copy", "x_copy"):
            _require(type(claim.get(text_key)) is str and hashlib.sha256(claim[text_key].encode()).hexdigest()
                == self.entry[text_key + "_sha256"])
        # request_id/source_sha are absent from legacy claim projection. The new
        # server must prove both against durable trial ownership before ACK.


def build_exact_session(*, manifest, version, backend, before_side_effect, clock,
                        gateway_token, runtime_release_sha, enabled=False):
    """Compose REAL existing Engine gateway + owner; no I/O during composition.

    Engine import must resolve to an independently verified exact checkout/image.
    This helper neither supplies runtime provenance nor constructs a sender.
    """
    _require(type(manifest) is TrialManifest and runtime_release_sha == manifest.payload["release_sha"])
    from core.content_ops.private_review_card_gateway import ButtonCanaryGateway
    from core.content_ops.private_review_card_owner_gateway import GatewayPrivateCardOwner
    transport = TrialGatewayTransport(content_version_id=version, backend=backend,
        manifest=manifest, before_side_effect=before_side_effect, clock=clock, enabled=enabled)
    gateway = ButtonCanaryGateway(origin=ORIGIN, gateway_token=gateway_token,
        release_sha=runtime_release_sha, content_version_id=version, enabled=enabled,
        transport=transport, clock=clock)
    return gateway, GatewayPrivateCardOwner(gateway, enabled=enabled), transport
