"""Unmounted final review-card courier contract; no credentials or runtime route.

The injected owner must COMMIT each ledger transition before returning. This
module never retries an uncertain reservation, provider response, or receipt.
It cannot approve or publish and is default OFF even when imported.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from uuid import UUID

from core.content_ops.final_publication_confirmation import (
    FinalConfirmationSigner, FinalConfirmationSnapshot,
    final_confirmation_messages,
)
from core.content_ops.private_review_card_receipt import (
    part_payload_sha256, validate_part_response,
)
from core.content_ops.review_edit_ingress import EditBindings


_SHA = re.compile(r"[a-f0-9]{64}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")
_PART_KINDS = ("image", "telegram", "x", "controls")


class FinalCardCourierError(RuntimeError):
    """Fixed, non-sensitive error code only."""


def _require(condition: bool) -> None:
    if not condition:
        raise FinalCardCourierError("final_card_candidate_invalid")


def _uuid(value: str) -> str:
    try:
        _require(type(value) is str and str(UUID(value)) == value and UUID(value).int != 0)
    except (ValueError, AttributeError):
        raise FinalCardCourierError("final_card_candidate_invalid") from None
    return value


def _utc(value: str) -> datetime:
    _require(type(value) is str)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise FinalCardCourierError("final_card_candidate_invalid") from None
    _require(parsed.utcoffset() is not None)
    return parsed.astimezone(timezone.utc)


def _fresh_source(snapshot: FinalConfirmationSnapshot, now: int) -> None:
    published = _utc(snapshot.review.source_published_at)
    age = datetime.fromtimestamp(now, timezone.utc) - published
    _require(timedelta(0) <= age < timedelta(hours=24))


def _text_fits(text: str, limit: int) -> bool:
    try:
        return type(text) is str and 0 < len(text.encode("utf-16-le")) // 2 <= limit
    except UnicodeError:
        return False


def prepare_final_card(snapshot: FinalConfirmationSnapshot,
                       signer: FinalConfirmationSigner, room_binding: str,
                       *, now: int) -> tuple[dict, ...]:
    """Render four immutable requests, without constructing a network client."""
    _require(type(snapshot) is FinalConfirmationSnapshot
             and type(signer) is FinalConfirmationSigner)
    packet = final_confirmation_messages(snapshot, signer, room_binding, now=now)
    caption = (f"{snapshot.review.client_id.upper()} · 최종 게시 확인 (2차)\n"
               f"버전: {snapshot.review.content_version_id}\n"
               "배너·Telegram·X 전문과 마지막 승인 버튼을 확인해주세요.")
    requests = (
        {"kind": "image", "method": "sendPhoto", "text": caption},
        {"kind": "telegram", "method": "sendMessage",
         "text": packet["telegram"]["text"]},
        {"kind": "x", "method": "sendMessage", "text": packet["x"]["text"]},
        {"kind": "controls", "method": "sendMessage",
         "text": packet["controls"]["text"],
         "reply_markup": packet["controls"]["reply_markup"]},
    )
    _require(_text_fits(caption, 1024) and all(
        _text_fits(request["text"], 4096) for request in requests[1:]))
    return requests


def final_card_packet_sha256(requests: tuple[dict, ...], *, banner_sha256: str,
                             snapshot_sha256: str, delivery_id: str,
                             review_id: str, parent_card_id: str,
                             release_sha: str) -> str:
    """Bind the ledger reservation to the exact four rendered private parts."""
    for value in (delivery_id, review_id, parent_card_id):
        _uuid(value)
    _require(type(requests) is tuple and len(requests) == 4
             and tuple(request.get("kind") if type(request) is dict else None
                       for request in requests) == _PART_KINDS
             and type(banner_sha256) is str and bool(_SHA.fullmatch(banner_sha256))
             and type(snapshot_sha256) is str and bool(_SHA.fullmatch(snapshot_sha256))
             and type(release_sha) is str and bool(_RELEASE.fullmatch(release_sha)))
    payload = {"format": "final-card-4@1", "delivery_id": delivery_id,
               "review_id": review_id, "parent_card_id": parent_card_id,
               "snapshot_sha256": snapshot_sha256, "release_sha": release_sha,
               "parts": [part_payload_sha256(request, banner_sha256)
                         for request in requests]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, repr=False)
class PreparedFinalCard:
    """Trusted owner projection; never parsed from a Telegram message."""

    snapshot: FinalConfirmationSnapshot
    delivery_id: str
    png: bytes
    bot_id: int
    chat_id: int
    human_id: int
    thread_id: int | None
    now: int


class FinalCardOwner(Protocol):
    async def reserve_delivery(self, **values) -> dict: ...

    async def begin_part(self, **values) -> dict: ...

    async def confirm_part(self, **values) -> dict: ...

    async def register_card(self, *, delivery_id: str) -> dict: ...

    async def read_registered(self, *, delivery_id: str) -> dict: ...


class FinalCardSender(Protocol):
    async def preflight(self, *, bot_id: int, chat_id: int) -> None: ...

    async def send_once(self, request: dict, *, png: bytes | None): ...


class FinalCardCourier:
    def __init__(self, owner: FinalCardOwner, sender: FinalCardSender,
                 signer: FinalConfirmationSigner, bindings: EditBindings,
                 *, runtime_release_sha: str,
                 clock: Callable[[], int] | None = None):
        if (type(signer) is not FinalConfirmationSigner
            or type(bindings) is not EditBindings
            or signer._key == bindings._key
            or type(runtime_release_sha) is not str
            or not _RELEASE.fullmatch(runtime_release_sha)):
            raise FinalCardCourierError("final_card_configuration_invalid")
        self._owner, self._sender = owner, sender
        self._signer, self._bindings = signer, bindings
        self._release_sha = runtime_release_sha
        self._clock = clock or (lambda: int(time.time()))
        self._attempted: set[str] = set()

    async def run(self, prepared: PreparedFinalCard, *, enabled=False) -> dict:
        if enabled is not True:
            return {"status": "disabled", "confirmed_parts": 0,
                    "public_send_attempted": False}
        confirmed_parts = 0
        reservation_called = False
        try:
            _require(type(prepared) is PreparedFinalCard)
            snapshot = prepared.snapshot
            _require(type(snapshot) is FinalConfirmationSnapshot)
            snapshot.validate()
            _uuid(prepared.delivery_id)
            _require(snapshot.release_sha == self._release_sha
                     and type(prepared.bot_id) is int and prepared.bot_id > 0
                     and type(prepared.chat_id) is int and -(2**52) < prepared.chat_id < 0
                     and type(prepared.human_id) is int and prepared.human_id > 0
                     and prepared.thread_id is None
                     and type(prepared.now) is int and prepared.now > 0
                     and type(prepared.png) is bytes
                     and prepared.png.startswith(b"\x89PNG\r\n\x1a\n"))
            actual_now = self._clock()
            _require(type(actual_now) is int
                     and 0 <= actual_now - prepared.now <= 60)
            _fresh_source(snapshot, actual_now)
            banner_sha = hashlib.sha256(prepared.png).hexdigest()
            _require(banner_sha == snapshot.review.banner_sha256)
            room = self._bindings.digest("room", prepared.bot_id, prepared.chat_id)
            bot = self._bindings.digest("bot", prepared.bot_id)
            human = self._bindings.digest("human", prepared.bot_id, prepared.human_id)
            requests = prepare_final_card(snapshot, self._signer, room,
                                          now=prepared.now)
            snapshot_sha = snapshot.digest()
            packet_sha = final_card_packet_sha256(requests,
                banner_sha256=banner_sha, snapshot_sha256=snapshot_sha,
                delivery_id=prepared.delivery_id, review_id=snapshot.review_id,
                parent_card_id=snapshot.card_id, release_sha=self._release_sha)
            if prepared.delivery_id in self._attempted:
                raise FinalCardCourierError("final_card_replay_denied")
            await self._sender.preflight(bot_id=prepared.bot_id,
                                         chat_id=prepared.chat_id)
            self._attempted.add(prepared.delivery_id)
            reservation_called = True
            reservation = await self._owner.reserve_delivery(
                delivery_id=prepared.delivery_id, review_id=snapshot.review_id,
                parent_card_id=snapshot.card_id, actor_id=snapshot.reviewer_id,
                version_fingerprint=snapshot.version_fingerprint,
                bot_binding=bot, room_binding=room, human_binding=human,
                snapshot_sha256=snapshot_sha, packet_sha256=packet_sha,
                release_sha=self._release_sha,
                telegram_route_binding=snapshot.telegram_route_binding,
                typefully_route_binding=snapshot.typefully_route_binding)
            if (type(reservation) is not dict
                or set(reservation) != {"status", "delivery_id", "expires_at",
                                        "execution_authorized"}
                or reservation["status"] != "delivery_reserved"
                or reservation["delivery_id"] != prepared.delivery_id
                or reservation["execution_authorized"] is not False):
                raise FinalCardCourierError("final_card_reservation_unknown")
            expires_at = _utc(reservation["expires_at"])
            _require(datetime.fromtimestamp(actual_now, timezone.utc) < expires_at
                     <= datetime.fromtimestamp(actual_now + 901, timezone.utc))
            previous_message_id = 0
            previous_observed_at = datetime.fromtimestamp(prepared.now, timezone.utc)
            for index, request in enumerate(requests):
                action_now = self._clock()
                _require(type(action_now) is int and prepared.now <= action_now
                         and datetime.fromtimestamp(action_now, timezone.utc) < expires_at)
                _fresh_source(snapshot, action_now)
                payload_sha = part_payload_sha256(request, banner_sha)
                begin = await self._owner.begin_part(delivery_id=prepared.delivery_id,
                    part_index=index, payload_sha256=payload_sha)
                if begin != {"status": "attempt_recorded", "reused": False,
                             "execution_authorized": False}:
                    raise FinalCardCourierError("final_card_attempt_unknown")
                observed = await self._sender.send_once(request,
                    png=prepared.png if index == 0 else None)
                message_id, observed_at, response_sha = validate_part_response(observed, request,
                    bot_id=prepared.bot_id, chat_id=prepared.chat_id,
                    thread_id=prepared.thread_id)
                _require(message_id > previous_message_id
                         and previous_observed_at <= observed_at < expires_at)
                receipt = await self._owner.confirm_part(
                    delivery_id=prepared.delivery_id, part_index=index,
                    payload_sha256=payload_sha,
                    message_binding=self._bindings.digest("final-card-message@1",
                        prepared.bot_id, prepared.chat_id, message_id),
                    response_sha256=response_sha)
                if receipt != {"status": "confirmed", "reused": False,
                               "execution_authorized": False}:
                    raise FinalCardCourierError("final_card_receipt_unknown")
                confirmed_parts += 1
                previous_message_id, previous_observed_at = message_id, observed_at
            try:
                registered = await self._owner.register_card(
                    delivery_id=prepared.delivery_id)
            except Exception:
                registered = None
            if registered is not None and registered != {
                    "status": "card_registered", "card_id": prepared.delivery_id,
                    "reused": False, "execution_authorized": False}:
                raise FinalCardCourierError("final_card_registration_unknown")
            terminal = await self._owner.read_registered(
                delivery_id=prepared.delivery_id)
            if terminal != {"status": "card_registered",
                            "card_id": prepared.delivery_id,
                            "execution_authorized": False}:
                raise FinalCardCourierError("final_card_terminal_unknown")
            return {"status": "card_registered", "confirmed_parts": 4,
                    "public_send_attempted": False}
        except Exception:
            return {"status": "delivery_unknown" if reservation_called else "blocked",
                    "confirmed_parts": confirmed_parts,
                    "public_send_attempted": False}
