"""Unmounted, default-OFF one-shot private card canary coordinator.

The image reader is deliberately injected: no storage credential, discovery,
schedule, bot listener or provider call exists here. The local gateway reader
proposal proves the exact immutable canonical PNG; fake readers are only for
local tests. Neither reader is mounted in a production runtime.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from core.content_ops.private_review_card_candidate import build_prepared_card
from core.content_ops.private_review_card_courier import PrivateCardCourier
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.worker import ReviewClaim


class PrivateCardCanaryError(RuntimeError):
    """Fixed non-sensitive failure code."""


@dataclass(frozen=True, repr=False)
class CanonicalPng:
    content_item_id: str
    content_version_id: str
    sha256: str
    data: bytes


class CanonicalPngReader(Protocol):
    async def load_png(self, claim) -> CanonicalPng: ...


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


class PrivateCardCanary:
    def __init__(self, *, workspace_id, content_version_id, bot_id, chat_id, gateway, owner,
                 png_reader: CanonicalPngReader, courier: PrivateCardCourier,
                 signer: ButtonSigner, bindings: EditBindings,
                 clock=None, uuid_factory=None):
        # The concrete one-shot HTTP gateway must own claim, image and begin
        # together. Synthetic tests may inject separate fake contracts only.
        from core.content_ops.private_review_card_gateway import ButtonCanaryGateway
        from core.content_ops.private_review_card_owner_gateway import GatewayPrivateCardOwner

        if (not _uuid(workspace_id) or not _uuid(content_version_id)
            or type(bot_id) is not int or bot_id <= 0
            or type(chat_id) is not int
            or re.fullmatch(r"-100[1-9][0-9]{6,12}", str(chat_id)) is None
            or type(courier) is not PrivateCardCourier
            or type(signer) is not ButtonSigner
            or type(bindings) is not EditBindings
            or (isinstance(gateway, ButtonCanaryGateway)
                and (png_reader is not gateway
                    or type(owner) is not GatewayPrivateCardOwner
                    or owner.gateway is not gateway
                    or courier._owner is not owner))):
            raise PrivateCardCanaryError("private_card_canary_configuration_invalid")
        self._workspace_id, self._version_id = workspace_id, content_version_id
        self._bot_id, self._chat_id = bot_id, chat_id
        self._gateway, self._owner = gateway, owner
        self._reader, self._courier = png_reader, courier
        self._signer, self._bindings = signer, bindings
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._uuid_factory = uuid_factory or (lambda: str(uuid4()))
        self._attempted = False

    async def run(self, *, enabled=False):
        if enabled is not True:
            return {"status": "disabled", "public_send_attempted": False}
        if self._attempted:
            return {"status": "replay_denied", "public_send_attempted": False}
        self._attempted = True
        begin_attempted = False
        begun = False
        try:
            queued = await self._gateway.reconcile()
            if type(queued) is not int or queued not in (0, 1):
                raise PrivateCardCanaryError("private_card_canary_queue_invalid")
            if queued == 0:
                return {"status": "no_candidate", "public_send_attempted": False}
            claim_token = self._uuid_factory()
            if not _uuid(claim_token):
                raise PrivateCardCanaryError("private_card_canary_id_invalid")
            claim = await self._gateway.claim(claim_token)
            if claim is None:
                return {"status": "no_candidate", "public_send_attempted": False}
            claim_now = self._clock()
            if (type(claim) is not ReviewClaim or claim.claim_token != claim_token
                or claim.content_version_id != self._version_id
                or type(claim_now) is not datetime or claim_now.utcoffset() is None
                or type(claim.source_published_at) is not datetime
                or claim.source_published_at.utcoffset() is None
                or not 0 <= (claim_now - claim.source_published_at).total_seconds() < 86_400):
                raise PrivateCardCanaryError("private_card_canary_claim_invalid")
            image = await self._reader.load_png(claim)
            if (type(image) is not CanonicalPng
                or image.content_item_id != claim.content_item_id
                or image.content_version_id != claim.content_version_id
                or image.sha256 != claim.banner_sha256
                or type(image.data) is not bytes
                or not 8 < len(image.data) <= 10_000_000
                or not image.data.startswith(b"\x89PNG\r\n\x1a\n")
                or hashlib.sha256(image.data).hexdigest() != claim.banner_sha256):
                raise PrivateCardCanaryError("private_card_canary_image_invalid")
            review_id, card_id = self._uuid_factory(), self._uuid_factory()
            if not (_uuid(review_id) and _uuid(card_id)
                    and review_id != card_id and review_id != claim_token
                    and card_id != claim_token):
                raise PrivateCardCanaryError("private_card_canary_id_invalid")
            review_receipt = await self._owner.prepare_review(
                workspace_id=self._workspace_id, outbox_id=claim.outbox_id,
                claim_token=claim.claim_token,
                content_version_id=claim.content_version_id,
                review_id=review_id)
            current = self._clock()
            if type(current) is not datetime or current.utcoffset() is None:
                raise PrivateCardCanaryError("private_card_canary_clock_invalid")
            prepared = build_prepared_card(claim=claim, review_receipt=review_receipt,
                workspace_id=self._workspace_id, card_id=card_id, png=image.data,
                signer=self._signer, bindings=self._bindings,
                bot_id=self._bot_id, chat_id=self._chat_id,
                now=int(current.timestamp()))
            begin_attempted = True
            begin_receipt = await self._gateway.begin(claim, prepared.packet_sha256)
            if begin_receipt != {"status": "begun", "outbox_id": claim.outbox_id,
                                 "execution_authorized": False}:
                raise PrivateCardCanaryError("private_card_canary_begin_unknown")
            begun = True
            outcome = await self._courier.run(prepared, enabled=True)
            if (type(outcome) is not dict or set(outcome) != {
                    "status", "confirmed_parts", "public_send_attempted"}
                or outcome["status"] not in {"card_recorded", "delivery_unknown", "blocked"}
                or type(outcome["confirmed_parts"]) is not int
                or not 0 <= outcome["confirmed_parts"] <= 4
                or outcome["public_send_attempted"] is not False):
                raise PrivateCardCanaryError("private_card_canary_outcome_unknown")
            return outcome
        except Exception:
            return {"status": ("delivery_unknown" if begun else
                               "outbox_unknown" if begin_attempted else "blocked"),
                    "public_send_attempted": False}
