"""Assemble an exact private card from a claimed outbox and owner receipt.

Pure preparation only. The DB owner independently rechecks the current source,
assets, approvals and outbox claim; the caller must obtain the canonical PNG
from a separately authenticated immutable-asset reader. This module neither
begins the outbox nor sends a card.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from uuid import UUID

from core.content_ops.private_review_card_courier import PreparedCard
from core.content_ops.private_review_card_receipt import (
    prepare_private_card, private_card_packet_sha256, validate_candidate,
)
from core.content_ops.prompt_reservation import canonical_timestamp
from core.content_ops.review_buttons import ButtonSigner, ReviewSnapshot
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.worker import ReviewClaim


_SHA = re.compile(r"[a-f0-9]{64}\Z")
_ROOM = re.compile(r"-100[1-9][0-9]{6,12}\Z")
_PNG = b"\x89PNG\r\n\x1a\n"


class PrivateCardCandidateError(ValueError):
    """Fixed status code only; no draft, image or credential in failures."""


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def build_prepared_card(*, claim, review_receipt, workspace_id, card_id,
                        png, signer, bindings, bot_id, chat_id, now):
    """Return a packet-hash-bound `PreparedCard`, never send permission."""
    try:
        if (type(claim) is not ReviewClaim or not _uuid(workspace_id)
            or not _uuid(card_id) or type(signer) is not ButtonSigner
            or type(bindings) is not EditBindings
            or type(bot_id) is not int or bot_id <= 0
            or type(chat_id) is not int or _ROOM.fullmatch(str(chat_id)) is None
            or type(now) is not int or not 0 < now < 2**32
            or type(png) is not bytes or not _PNG == png[:8]
            or not 8 < len(png) <= 10_000_000
            or hashlib.sha256(png).hexdigest() != claim.banner_sha256
            or type(review_receipt) is not dict
            or set(review_receipt) != {"status", "review_id",
                "version_fingerprint", "epoch", "state", "expires_at",
                "execution_authorized"}
            or review_receipt["status"] != "review_prepared"
            or not _uuid(review_receipt["review_id"])
            or type(review_receipt["version_fingerprint"]) is not str
            or _SHA.fullmatch(review_receipt["version_fingerprint"]) is None
            or type(review_receipt["epoch"]) is not int
            or review_receipt["epoch"] != 0
            or review_receipt["state"] != "active"
            or review_receipt["execution_authorized"] is not False
            or type(review_receipt["expires_at"]) is not str):
            raise ValueError
        published = claim.source_published_at
        if (type(published) is not datetime or published.utcoffset() is None
            or not 0 <= now - published.timestamp() < 86_400):
            raise ValueError
        snapshot = ReviewSnapshot(
            workspace_id=workspace_id, client_id=claim.client_id,
            content_item_id=claim.content_item_id,
            content_version_id=claim.content_version_id,
            source_url=claim.source_url,
            source_published_at=canonical_timestamp(published),
            telegram_copy=claim.telegram_copy, x_copy=claim.x_copy,
            banner_sha256=claim.banner_sha256, eligibility="blocked",
        )
        review = {"id": review_receipt["review_id"],
                  "workspace_id": workspace_id, "client_id": claim.client_id,
                  "content_item_id": claim.content_item_id,
                  "content_version_id": claim.content_version_id,
                  "version_fingerprint": review_receipt["version_fingerprint"],
                  "epoch": review_receipt["epoch"],
                  "state": review_receipt["state"],
                  "expires_at": review_receipt["expires_at"]}
        validate_candidate(review, snapshot, card_id, now=now)
        room_binding = bindings.digest("room", bot_id, chat_id)
        requests = prepare_private_card(snapshot, signer, room_binding, now=now)
        packet_sha = private_card_packet_sha256(requests, claim.banner_sha256,
            review["id"], card_id)
        return PreparedCard(review, snapshot, card_id, png, bot_id, chat_id,
            None, room_binding, now, claim.outbox_id, claim.claim_token,
            packet_sha)
    except Exception:
        raise PrivateCardCandidateError("private_card_candidate_invalid") from None
