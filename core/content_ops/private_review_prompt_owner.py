"""Unmounted DB owner for one edit-prompt reservation and receipt.

The injected connection factory must use a dedicated least-privilege role.
This module never discovers a DSN, grants a role, starts a poller or sends a
message. The hosted schema has the underlying reservation/registration
functions, but the restricted runtime role cannot execute them. This owner
calls two proposed narrow capability wrappers that are not hosted yet; their
exact compatibility with the hosted functions is not established.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
from datetime import timedelta

from core.content_ops.private_review_prompt_courier import (
    PromptCommand, PromptReservation,
)
from core.content_ops.prompt_attempt import prompt_instruction
from core.content_ops.prompt_receipt import PromptAttempt, canonical_uuid, digest
from core.content_ops.prompt_receipt_owner import PostgresPromptReceiptOwner
from core.content_ops.prompt_reservation import card_parent_binding, _time
from core.content_ops.review_edit_ingress import EditBindings


class PromptOwnerError(RuntimeError):
    """Fixed non-sensitive error code; never expose SQL or employee data."""


def _require(condition):
    if not condition:
        raise PromptOwnerError("private_prompt_owner_unknown")


def _project_attempt(row, review, card, action, *, command, action_key,
                     expected_actor_id, bindings, db_now):
    """Accept only the exact committed owner row, not callback-derived text."""
    _require(all(type(value) is dict for value in (row, review, card)))
    _require(action in {"edit_telegram", "edit_x", "edit_banner"})
    _require(all(canonical_uuid(value) for value in
                 (row.get("id"), row.get("review_id"), row.get("card_id"),
                  row.get("actor_id"), review.get("id"), card.get("id"))))
    _require(row["id"] == command.attempt_id
             and row["actor_id"] == expected_actor_id
             and row["review_id"] == review["id"] == card.get("review_id")
             and row["card_id"] == card["id"]
             and type(row.get("epoch")) is int
             and row["epoch"] == review.get("epoch") == card.get("epoch") + 1
             and review.get("state") == "edit_requested"
             and card.get("active") is True
             and row.get("edit_action_key") == action_key
             and row.get("version_fingerprint") == review.get("version_fingerprint")
             == card.get("version_fingerprint")
             and digest(row["version_fingerprint"]))
    bot = bindings.digest("bot", command.bot_id)
    room = bindings.digest("room", command.bot_id, command.chat_id)
    human = bindings.digest("human", command.bot_id, command.human_id)
    card_bindings = card.get("bindings")
    _require(type(card_bindings) is dict and card_bindings.get("thread_id") is None
             and row.get("thread_id") is None
             and row.get("bot_binding") == card_bindings.get("bot") == bot
             and row.get("room_binding") == card_bindings.get("room") == room
             and row.get("human_binding") == human
             and row.get("parent_binding_sha256") ==
                 card_bindings.get("parent_binding") ==
                 card_parent_binding(bindings, review, card))
    text_hash = hashlib.sha256(prompt_instruction(action).encode()).hexdigest()
    _require(row.get("expected_text_sha256") == text_hash)
    started, expires, current = (_time(row.get("started_at")),
                                 _time(row.get("expires_at")), _time(db_now))
    _require(started <= current < expires
             and expires <= started + timedelta(minutes=30)
             and expires <= _time(card.get("expires_at"))
             and expires <= _time(review.get("expires_at")))
    start_second, expiry_second = math.ceil(started.timestamp()), math.floor(expires.timestamp())
    _require(0 < start_second < expiry_second < 2**32)
    return PromptAttempt(row["id"], review["id"], row["actor_id"], row["epoch"],
        action_key, row["version_fingerprint"], row["parent_binding_sha256"],
        text_hash, command.bot_id, command.chat_id, command.human_id,
        start_second, expiry_second, None)


class PostgresPrivatePromptOwner:
    """Fresh transaction per call; reservation commits before caller may send."""

    def __init__(self, connection_factory, bindings: EditBindings):
        _require(callable(connection_factory) and type(bindings) is EditBindings)
        self._connect, self._bindings = connection_factory, bindings
        self._receipt = PostgresPromptReceiptOwner(connection_factory)
        self._reserve_attempted = False
        self._confirm_attempted = False
        self._reserved_command = None

    async def reserve_prompt(self, *, command: PromptCommand, action_key: str):
        if self._reserve_attempted:
            raise PromptOwnerError("private_prompt_owner_replay_denied")
        self._reserve_attempted = True
        result = await asyncio.to_thread(self._reserve_sync, command, action_key)
        self._reserved_command = command
        return result

    def _reserve_sync(self, command, action_key):
        try:
            _require(type(command) is PromptCommand and canonical_uuid(command.attempt_id)
                     and type(command.callback_id) is str
                     and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", command.callback_id)
                     and digest(action_key)
                     and hashlib.sha256(command.callback_id.encode()).hexdigest() == action_key)
            bot = self._bindings.digest("bot", command.bot_id)
            room = self._bindings.digest("room", command.bot_id, command.chat_id)
            human = self._bindings.digest("human", command.bot_id, command.human_id)
            with self._connect() as connection:
                _require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    # Lookup only. The RPC below rechecks identity, card,
                    # action, version and expiry under item/review locks.
                    cursor.execute("""select a.actor_id::text,r.id::text,c.id::text,a.action
                        from private.content_ops_button_actions a
                        join private.content_ops_button_reviews r on r.id=a.review_id
                        join private.content_ops_button_cards c on c.review_id=r.id
                          and c.epoch=r.epoch-1 and c.active
                        join private.content_ops_button_identities i
                          on i.workspace_id=r.workspace_id and i.actor_id=a.actor_id
                          and i.bot_binding=c.bindings->>'bot'
                          and i.human_binding=%s and i.active
                        where a.idempotency_key=%s and a.epoch=r.epoch
                          and a.result_status='edit_requested'
                          and a.action in ('edit_telegram','edit_x','edit_banner')
                          and c.bindings->>'bot'=%s and c.bindings->>'room'=%s
                          and c.bindings->'thread_id'='null'::jsonb""",
                        (human, action_key, bot, room))
                    rows = cursor.fetchall()
                    _require(len(rows) == 1 and len(rows[0]) == 4)
                    actor_id, review_id, card_id, action = rows[0]
                    _require(all(canonical_uuid(v) for v in (actor_id, review_id, card_id)))
                    cursor.execute("""with reserved as materialized (
                        select private.reserve_content_ops_button_prompt_for_runtime(
                            %s::uuid,%s::uuid,%s::uuid,%s,%s) as result
                    ) select result,clock_timestamp() from reserved""",
                        (card_id, command.attempt_id, actor_id, human, action_key))
                    result = cursor.fetchone()
                    _require(result is not None and len(result) == 2
                             and result[0] == {"status": "attempt_recorded",
                                "attempt_id": command.attempt_id, "reused": False,
                                "execution_authorized": False})
                    cursor.execute("""select to_jsonb(a),to_jsonb(r),to_jsonb(c)
                        from private.content_ops_button_prompt_attempts a
                        join private.content_ops_button_reviews r on r.id=a.review_id
                        join private.content_ops_button_cards c on c.id=a.card_id
                        where a.id=%s::uuid and a.review_id=%s::uuid
                          and a.card_id=%s::uuid for share of a,r,c""",
                        (command.attempt_id, review_id, card_id))
                    records = cursor.fetchall()
                    _require(len(records) == 1 and len(records[0]) == 3)
                    attempt = _project_attempt(*records[0], action,
                        command=command, action_key=action_key,
                        expected_actor_id=actor_id,
                        bindings=self._bindings, db_now=result[1])
            return PromptReservation(attempt, action, result[0])
        except Exception:
            # The commit ACK may be lost. Never retry this reservation with a
            # new ID or proceed to Telegram on an uncertain result.
            raise PromptOwnerError("private_prompt_owner_unknown") from None

    async def confirm_prompt(self, *, attempt_id, bot_id, chat_id, human_id,
                             http_status, raw_response, observed_at):
        if (self._confirm_attempted or self._reserved_command is None
            or (attempt_id, bot_id, chat_id, human_id) !=
               (self._reserved_command.attempt_id, self._reserved_command.bot_id,
                self._reserved_command.chat_id, self._reserved_command.human_id)):
            raise PromptOwnerError("private_prompt_owner_replay_denied")
        self._confirm_attempted = True
        try:
            return await asyncio.to_thread(self._receipt.record_prompt_response,
                enabled=True, attempt_id=attempt_id, bindings=self._bindings,
                bot_id=bot_id, chat_id=chat_id, human_id=human_id, thread_id=None,
                http_status=http_status, raw_response=raw_response,
                observed_at=observed_at)
        except Exception:
            raise PromptOwnerError("private_prompt_owner_unknown") from None
