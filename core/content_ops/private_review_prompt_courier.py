"""Default-OFF, one-shot bridge from an edit callback to its reply prompt.

The owner must commit the exact attempt before this module calls Telegram. A
provider timeout or lost receipt commit is terminal; neither the prompt nor a
new attempt is retried here. No poller, credential discovery or public send.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import UUID

from core.content_ops.prompt_attempt import prompt_instruction
from core.content_ops.prompt_receipt import (
    PromptAttempt, digest, validate_prompt_response,
)
from core.content_ops.private_review_card_receipt import ObservedSend
from core.content_ops.private_review_card_sender import (
    PrivateCardSenderError, TelegramPrivateCardSender,
)
from core.content_ops.review_edit_ingress import EditBindings


_CALLBACK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ROOM = re.compile(r"-100[1-9][0-9]{6,12}\Z")


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


@dataclass(frozen=True, repr=False)
class PromptCommand:
    """Identifiers from the authenticated existing poller, never chat text."""

    callback_id: str
    attempt_id: str
    bot_id: int
    chat_id: int
    human_id: int


@dataclass(frozen=True, repr=False)
class PromptReservation:
    attempt: PromptAttempt
    action: str
    receipt: dict


class PromptOwner(Protocol):
    async def reserve_prompt(self, *, command: PromptCommand,
                             action_key: str) -> PromptReservation: ...

    async def confirm_prompt(self, *, attempt_id: str, bot_id: int, chat_id: int,
                             human_id: int, http_status: int, raw_response: bytes,
                             observed_at: datetime) -> dict: ...


class PromptSender(Protocol):
    async def preflight(self, *, bot_id: int, chat_id: int) -> None: ...

    async def send_once(self, text: str, *, bot_id: int,
                        chat_id: int) -> ObservedSend: ...


class TelegramPrivatePromptSender(TelegramPrivateCardSender):
    """Reuse the card sender's exact-bot/private-room read-only preflight."""

    async def send_once(self, text: str, *, bot_id: int,
                        chat_id: int) -> ObservedSend:
        if (not self._verified or self._terminal or self._attempted
            or bot_id != self._bot_id or chat_id != self._chat_id
            or type(text) is not str
            or text not in {prompt_instruction(action) for action in
                            ("edit_telegram", "edit_x", "edit_banner")}):
            raise PrivateCardSenderError("private_prompt_request_invalid")
        self._attempted.add(hashlib.sha256(text.encode()).hexdigest())
        self._terminal = True  # Set before provider I/O, including a timeout.
        response = await self._post("sendMessage", body={
            "chat_id": self._chat_id, "text": text, "protect_content": True,
            "link_preview_options": {"is_disabled": True}})
        observed_at = self._clock()
        if type(observed_at) is not datetime or observed_at.utcoffset() is None:
            raise PrivateCardSenderError("private_prompt_clock_invalid")
        return ObservedSend("sendMessage", response.status_code,
            response.content, observed_at.astimezone(timezone.utc))


class PrivateReviewPromptCourier:
    def __init__(self, owner: PromptOwner, sender: PromptSender, bindings: EditBindings,
                 *, clock: Callable[[], int] | None = None, sleep=None):
        if type(bindings) is not EditBindings:
            raise ValueError("private_prompt_configuration_invalid")
        self._owner, self._sender, self._bindings = owner, sender, bindings
        self._clock = clock or (lambda: int(time.time()))
        self._sleep = sleep or asyncio.sleep
        self._attempted = False

    async def run(self, command: PromptCommand, *, enabled=False):
        if enabled is not True:
            return {"status": "disabled", "private_send_attempts": 0,
                    "public_send_attempted": False}
        if self._attempted:
            return {"status": "replay_denied", "private_send_attempts": 0,
                    "public_send_attempted": False}
        self._attempted = True
        reserved = False
        calls = 0
        try:
            if (type(command) is not PromptCommand
                or type(command.callback_id) is not str
                or _CALLBACK_ID.fullmatch(command.callback_id) is None
                or not _uuid(command.attempt_id)
                or type(command.bot_id) is not int or command.bot_id <= 0
                or type(command.human_id) is not int or command.human_id <= 0
                or command.human_id == command.bot_id
                or type(command.chat_id) is not int
                or _ROOM.fullmatch(str(command.chat_id)) is None):
                raise ValueError("private_prompt_command_invalid")
            action_key = hashlib.sha256(command.callback_id.encode()).hexdigest()
            # Read-only bot/room/member checks precede the durable reservation.
            await self._sender.preflight(bot_id=command.bot_id, chat_id=command.chat_id)
            reservation = await self._owner.reserve_prompt(
                command=command, action_key=action_key)
            if type(reservation) is not PromptReservation:
                raise ValueError("private_prompt_reservation_unknown")
            attempt = reservation.attempt
            text = prompt_instruction(reservation.action)
            now = self._clock()
            # SQL records a precise reservation and its receipt projects the
            # first eligible provider second with ceil(). Wait at most one
            # second; never send with a provider date before that boundary.
            if (type(attempt) is PromptAttempt and type(now) is int
                and type(attempt.started_at) is int and attempt.started_at == now + 1):
                await self._sleep(1)
                now = self._clock()
            if (type(attempt) is not PromptAttempt
                or attempt.attempt_id != command.attempt_id
                or not _uuid(attempt.review_id) or not _uuid(attempt.actor_id)
                or type(attempt.epoch) is not int or attempt.epoch <= 0
                or attempt.edit_action_key != action_key
                or not digest(attempt.version_fingerprint)
                or not digest(attempt.packet_receipt_sha256)
                or (attempt.bot_id, attempt.chat_id, attempt.human_id) !=
                    (command.bot_id, command.chat_id, command.human_id)
                or attempt.thread_id is not None
                or type(now) is not int
                or type(attempt.started_at) is not int
                or type(attempt.expires_at) is not int
                or not 0 < attempt.started_at <= now < attempt.expires_at
                or attempt.expires_at > attempt.started_at + 1800
                or attempt.expected_text_sha256 != hashlib.sha256(text.encode()).hexdigest()
                or reservation.receipt != {"status": "attempt_recorded",
                    "attempt_id": command.attempt_id, "reused": False,
                    "execution_authorized": False}):
                raise ValueError("private_prompt_reservation_unknown")
            reserved = True
            calls = 1  # Mark before possible provider I/O; never retry unknown.
            observation = await self._sender.send_once(text,
                bot_id=command.bot_id, chat_id=command.chat_id)
            if (type(observation) is not ObservedSend
                or observation.method != "sendMessage"
                or type(observation.observed_at) is not datetime
                or observation.observed_at.utcoffset() is None):
                raise ValueError("private_prompt_provider_unknown")
            matched = validate_prompt_response(enabled=True, attempt=attempt,
                bindings=self._bindings, http_status=observation.http_status,
                raw_response=observation.raw_response,
                observed_at=int(observation.observed_at.timestamp()))
            if matched.id != command.attempt_id:
                raise ValueError("private_prompt_provider_unknown")
            receipt = await self._owner.confirm_prompt(attempt_id=command.attempt_id,
                bot_id=command.bot_id, chat_id=command.chat_id,
                human_id=command.human_id, http_status=observation.http_status,
                raw_response=observation.raw_response,
                observed_at=observation.observed_at)
            if receipt != {"status": "prompt_registered",
                           "prompt_id": command.attempt_id, "reused": False,
                           "execution_authorized": False}:
                raise ValueError("private_prompt_confirmation_unknown")
            return {"status": "prompt_registered", "private_send_attempts": calls,
                    "public_send_attempted": False}
        except Exception:
            return {"status": "delivery_unknown" if reserved else "blocked",
                    "private_send_attempts": calls, "public_send_attempted": False}
