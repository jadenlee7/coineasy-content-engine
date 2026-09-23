"""Default-OFF, one-shot private card orchestration for the existing bot.

This module has no token discovery, network client, database connection,
poller or scheduler. Injected owners must durably commit each reservation
before returning ``new_attempt=True``. A lost reservation, send or registration
acknowledgement is terminal/unknown: this worker never retries any of them.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from core.content_ops.private_review_card_receipt import (
    card_registration_evidence, part_payload_sha256, prepare_private_card,
    validate_candidate, validate_part_response,
)
from core.content_ops.review_buttons import ButtonSigner, ReviewSnapshot
from core.content_ops.review_edit_ingress import EditBindings


class CardCourierError(RuntimeError):
    """Fixed, non-sensitive status code only."""


@dataclass(frozen=True, repr=False)
class PreparedCard:
    """Owner-verified exact candidate, not a reservation or delivery receipt."""

    review: dict
    snapshot: ReviewSnapshot
    card_id: str
    png: bytes
    bot_id: int
    chat_id: int
    thread_id: int | None
    room_binding: str
    now: int


class CardOwner(Protocol):
    async def reserve_part(self, *, review_id: str, card_id: str, part_index: int,
                           payload_sha256: str) -> dict: ...

    async def confirm_part(self, *, review_id: str, card_id: str, part_index: int,
                           payload_sha256: str, message_binding: str,
                           response_sha256: str, observed_at: str) -> dict: ...

    async def register_card(self, evidence: dict) -> dict: ...


class CardSender(Protocol):
    async def preflight(self, *, bot_id: int, chat_id: int) -> None: ...

    async def send_once(self, request: dict, *, png: bytes | None): ...


class PrivateCardCourier:
    def __init__(self, owner: CardOwner, sender: CardSender, signer: ButtonSigner,
                 bindings: EditBindings, *, clock: Callable[[], int] | None = None):
        if not isinstance(signer, ButtonSigner) or not isinstance(bindings, EditBindings):
            raise CardCourierError("private_card_configuration_invalid")
        self._owner, self._sender = owner, sender
        self._signer, self._bindings = signer, bindings
        self._clock = clock or (lambda: int(time.time()))
        self._attempted_cards: set[str] = set()

    async def run(self, prepared: PreparedCard, *, enabled=False):
        if enabled is not True:
            return {"status": "disabled", "confirmed_parts": 0,
                    "public_send_attempted": False}
        confirmed_parts = 0
        reservation_called = False
        try:
            if type(prepared) is not PreparedCard:
                raise CardCourierError("private_card_candidate_invalid")
            actual_now = self._clock()
            if type(actual_now) is not int or type(prepared.now) is not int or abs(actual_now - prepared.now) > 5:
                raise CardCourierError("private_card_clock_mismatch")
            if type(prepared.png) is not bytes or not prepared.png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise CardCourierError("private_card_candidate_invalid")
            banner_sha256 = hashlib.sha256(prepared.png).hexdigest()
            if banner_sha256 != prepared.snapshot.banner_sha256:
                raise CardCourierError("private_card_candidate_invalid")
            validate_candidate(prepared.review, prepared.snapshot,
                               prepared.card_id, now=prepared.now)
            if prepared.card_id in self._attempted_cards:
                raise CardCourierError("private_card_replay_denied")
            requests = prepare_private_card(prepared.snapshot, self._signer,
                prepared.room_binding, now=prepared.now)
            # An injected sender must authenticate the existing bot and exact
            # private room before any write or send. No second update consumer.
            await self._sender.preflight(bot_id=prepared.bot_id, chat_id=prepared.chat_id)
            # Process-local defense only; the durable owner reservation is the
            # cross-process authority. Consume before any write or send.
            self._attempted_cards.add(prepared.card_id)
            observations = []
            for index, request in enumerate(requests):
                action_now = self._clock()
                if type(action_now) is not int or not prepared.now <= action_now < prepared.now + 1800:
                    raise CardCourierError("private_card_clock_mismatch")
                validate_candidate(prepared.review, prepared.snapshot,
                                   prepared.card_id, now=action_now)
                payload_sha = part_payload_sha256(request, banner_sha256)
                reservation_called = True
                reservation = await self._owner.reserve_part(
                    review_id=prepared.review["id"], card_id=prepared.card_id,
                    part_index=index, payload_sha256=payload_sha)
                # Reused reservations are readback only. Even an otherwise
                # identical result cannot authorize another provider call.
                if reservation != {"status": "reserved", "new_attempt": True,
                                   "execution_authorized": False}:
                    raise CardCourierError("private_card_reservation_unknown")
                observation = await self._sender.send_once(
                    request, png=prepared.png if index == 0 else None)
                message_id, observed_at, response_sha = validate_part_response(
                    observation, request, bot_id=prepared.bot_id,
                    chat_id=prepared.chat_id, thread_id=prepared.thread_id)
                confirmed = await self._owner.confirm_part(
                    review_id=prepared.review["id"], card_id=prepared.card_id,
                    part_index=index, payload_sha256=payload_sha,
                    message_binding=self._bindings.digest("card-message@2",
                        prepared.bot_id, prepared.chat_id, message_id),
                    response_sha256=response_sha,
                    observed_at=observed_at.isoformat())
                if confirmed != {"status": "confirmed", "new_confirmation": True,
                                  "execution_authorized": False}:
                    raise CardCourierError("private_card_confirmation_unknown")
                observations.append(observation)
                confirmed_parts += 1
            evidence = card_registration_evidence(review=prepared.review,
                snapshot=prepared.snapshot, card_id=prepared.card_id,
                signer=self._signer, bindings=self._bindings,
                room_binding=prepared.room_binding, bot_id=prepared.bot_id,
                chat_id=prepared.chat_id, thread_id=prepared.thread_id,
                now=prepared.now, banner_sha256=banner_sha256,
                observations=observations)
            receipt = await self._owner.register_card(evidence)
            if receipt != {"status": "card_recorded", "card_id": prepared.card_id,
                           "reused": False, "execution_authorized": False}:
                raise CardCourierError("private_card_registration_unknown")
            return {"status": "card_recorded", "confirmed_parts": confirmed_parts,
                    "public_send_attempted": False}
        except Exception:
            # Includes lost DB commit ACK: no automatic retry or new card.
            return {"status": "delivery_unknown" if reservation_called else "blocked",
                    "confirmed_parts": confirmed_parts,
                    "public_send_attempted": False}
