"""Default-OFF, one-shot gateway client for an exact button-card canary.

This client has no scheduler, DB credential, Telegram token or publisher. It
can claim and begin only one existing review outbox; the card owner separately
prepares the review row and atomically finalizes the outbox after four receipts.
An uncertain HTTP acknowledgement is never retried by this client.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from uuid import UUID

import httpx

from core.content_ops.private_review_card_canary import CanonicalPng
from core.content_ops.worker import APP_ORIGIN, GATEWAY_PATH, ReviewClaim


_SHA40 = re.compile(r"[a-f0-9]{40}\Z")
_SHA64 = re.compile(r"[a-f0-9]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
_SCOPE = "button_card_v1"


class PrivateCardGatewayError(RuntimeError):
    """Fixed status code; never include response, draft or credential."""


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


class ButtonCanaryGateway:
    def __init__(self, *, origin, gateway_token, release_sha, content_version_id,
                 enabled=False, transport=None, clock=None):
        if (origin != APP_ORIGIN or type(gateway_token) is not str
            or _TOKEN.fullmatch(gateway_token) is None
            or type(release_sha) is not str or _SHA40.fullmatch(release_sha) is None
            or not _uuid(content_version_id) or type(enabled) is not bool):
            raise PrivateCardGatewayError("private_card_gateway_configuration_invalid")
        self._origin = origin
        self._token = gateway_token
        self._release = release_sha
        self._version = content_version_id
        self._enabled = enabled
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._reconciled = False
        self._claim_attempted = False
        self._claimed = None
        self._image_attempted = False
        self._image_verified = False
        self._begin_attempted = False

    def _headers(self):
        return {"Authorization": "Bearer " + self._token,
                "x-content-ops-expected-release-sha": self._release,
                "x-content-ops-mode": "canary",
                "x-content-ops-version-id": self._version,
                "x-content-ops-packet-mode": _SCOPE,
                "Accept": "application/json"}

    async def _post(self, body, result_key):
        if not self._enabled:
            raise PrivateCardGatewayError("private_card_gateway_disabled")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                    trust_env=False, transport=self._transport) as client:
                response = await client.post(self._origin + GATEWAY_PATH,
                    headers=self._headers(), json=body)
            if (response.status_code != 200 or len(response.content) > 16_384
                or response.headers.get("content-type", "").split(";", 1)[0] != "application/json"):
                raise PrivateCardGatewayError("private_card_gateway_outcome_unknown")
            receipt = response.json()
            if (type(receipt) is not dict
                or set(receipt) != {"ok", "release_sha", "scope", result_key}
                or receipt["ok"] is not True or receipt["release_sha"] != self._release
                or receipt["scope"] != {"mode": "canary",
                    "content_version_id": self._version, "packet_mode": _SCOPE}):
                raise PrivateCardGatewayError("private_card_gateway_outcome_unknown")
            return receipt[result_key]
        except Exception:
            raise PrivateCardGatewayError("private_card_gateway_outcome_unknown") from None

    async def reconcile(self):
        if self._reconciled:
            raise PrivateCardGatewayError("private_card_gateway_replay_denied")
        self._reconciled = True
        queued = await self._post({"action": "reconcile"}, "queued")
        if type(queued) is not int or queued not in (0, 1):
            raise PrivateCardGatewayError("private_card_gateway_outcome_unknown")
        return queued

    async def claim(self, claim_token):
        if not _uuid(claim_token):
            raise PrivateCardGatewayError("private_card_gateway_arguments_invalid")
        if self._claim_attempted or not self._reconciled:
            raise PrivateCardGatewayError("private_card_gateway_replay_denied")
        self._claim_attempted = True
        raw = await self._post({"action": "claim", "claim_token": claim_token}, "claim")
        if raw is None:
            return None
        try:
            now = self._clock()
            claim = ReviewClaim.parse(raw, now)
            if (claim.claim_token != claim_token
                or claim.content_version_id != self._version):
                raise ValueError
        except Exception:
            raise PrivateCardGatewayError("private_card_gateway_outcome_unknown") from None
        self._claimed = claim
        return claim

    async def load_png(self, claim):
        if claim is not self._claimed or claim is None:
            raise PrivateCardGatewayError("private_card_gateway_arguments_invalid")
        if self._image_attempted or self._begin_attempted:
            raise PrivateCardGatewayError("private_card_gateway_replay_denied")
        self._image_attempted = True
        if not self._enabled:
            raise PrivateCardGatewayError("private_card_gateway_disabled")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                    trust_env=False, transport=self._transport) as client:
                async with client.stream("POST", self._origin + GATEWAY_PATH,
                    headers={**self._headers(), "Accept": "image/png",
                             "Accept-Encoding": "identity"},
                    json={"action": "image", "outbox_id": claim.outbox_id,
                          "claim_token": claim.claim_token}) as response:
                    declared = response.headers.get("content-length", "")
                    if (response.status_code != 200 or response.is_redirect
                        or response.headers.get("content-type") != "image/png"
                        or response.headers.get("content-encoding", "identity") != "identity"
                        or "content-range" in response.headers
                        or response.headers.get("x-content-ops-release-sha") != self._release
                        or response.headers.get("x-content-ops-outbox-id") != claim.outbox_id
                        or response.headers.get("x-content-ops-item-id") != claim.content_item_id
                        or response.headers.get("x-content-ops-version-id") != claim.content_version_id
                        or response.headers.get("x-content-ops-banner-sha256") != claim.banner_sha256
                        or not declared.isdecimal() or not 8 < int(declared) <= 10_000_000):
                        raise PrivateCardGatewayError("private_card_gateway_image_unknown")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 10_000_000:
                            raise PrivateCardGatewayError("private_card_gateway_image_unknown")
                        data.extend(chunk)
                    if (len(data) != int(declared)
                        or not data.startswith(b"\x89PNG\r\n\x1a\n")
                        or hashlib.sha256(data).hexdigest() != claim.banner_sha256):
                        raise PrivateCardGatewayError("private_card_gateway_image_unknown")
            self._image_verified = True
            return CanonicalPng(claim.content_item_id, claim.content_version_id,
                                claim.banner_sha256, bytes(data))
        except Exception:
            raise PrivateCardGatewayError("private_card_gateway_image_unknown") from None

    async def begin(self, claim, packet_sha256):
        if (claim is not self._claimed or claim is None
            or type(packet_sha256) is not str or _SHA64.fullmatch(packet_sha256) is None):
            raise PrivateCardGatewayError("private_card_gateway_arguments_invalid")
        if self._begin_attempted or not self._image_verified:
            raise PrivateCardGatewayError("private_card_gateway_replay_denied")
        self._begin_attempted = True
        accepted = await self._post({"action": "begin", "claim_token": claim.claim_token,
            "outbox_id": claim.outbox_id, "packet_sha256": packet_sha256}, "accepted")
        if accepted is not True:
            raise PrivateCardGatewayError("private_card_gateway_begin_denied")
        return {"status": "begun", "outbox_id": claim.outbox_id,
                "execution_authorized": False}
