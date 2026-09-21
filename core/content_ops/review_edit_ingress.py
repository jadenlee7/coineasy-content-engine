"""Default-OFF text-edit boundary for the existing bot owner; not a route.

No polling, webhook installation, secret discovery, provider acknowledgments or
sends. Registration and publishing remain separate authorities. Only a fresh,
direct text reply to a registered bot edit prompt is eligible.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from uuid import UUID

from core.content_ops.review_ingress import (
    ReviewIngressError, _authenticated_update, _positive_int, _require,
)

_UNSUPPORTED = frozenset({
    "forward_origin", "forward_from", "forward_from_chat", "sender_chat",
    "via_bot", "business_connection_id", "is_automatic_forward", "is_from_offline",
    "guest_query_id", "edit_date", "external_reply", "quote", "reply_to_story",
    "photo", "document", "video", "audio", "voice", "caption", "sticker",
})
_PRIVATE = re.compile(
    r"t[.]me/(?:\+|joinchat/)|api[.]telegram[.]org/bot|"
    r"[0-9]{5,16}:[A-Za-z0-9_-]{30,100}|-100[0-9]{6,}", re.I)


class EditBindings:
    """Use a dedicated owner-held key, never an unkeyed hash of numeric IDs.

    Provisioning/rotation is not implemented. Rotation must invalidate or
    reconcile outstanding registrations, not try fallback keys or identities.
    """
    def __init__(self, key: bytes):
        _require(type(key) is bytes and len(key) >= 32, "review_edit_key_invalid")
        self._key = key

    def digest(self, kind, *values):
        encoded = json.dumps(["button-edit-binding@1", kind, *values],
                             separators=(",", ":"), ensure_ascii=True).encode()
        return hmac.new(self._key, encoded, hashlib.sha256).hexdigest()


@dataclass(frozen=True, repr=False)
class VerifiedEditReply:
    bot_binding: str
    room_binding: str
    message_binding: str
    human_binding: str
    actor_id: str
    replacement_text: str
    operation_key: str
    thread_id: int | None = None
    message_date: int | None = None
    prompt_date: int | None = None


def _message(message, policy, now):
    _require(type(message) is dict and not (_UNSUPPORTED & message.keys()),
             "review_edit_message_invalid")
    _require(_positive_int(message.get("message_id"))
             and type(message.get("date")) is int and 0 < message["date"] <= now,
             "review_edit_message_invalid")
    if "message_thread_id" in message:
        _require(_positive_int(message["message_thread_id"]), "review_edit_thread_invalid")
    chat = message.get("chat")
    _require(type(chat) is dict and type(chat.get("id")) is int
             and chat["id"] == policy.chat_id and chat.get("type") == "supergroup"
             and not ({"username", "linked_chat_id"} & chat.keys()),
             "review_edit_room_forbidden")


def _receipt(value):
    _require(type(value) is dict and set(value) == {
        "status", "content_version_id", "reused", "rereview_required", "execution_authorized"
    }, "review_edit_outcome_unknown")
    _require(value["status"] == "revision_saved" and type(value["reused"]) is bool
             and value["rereview_required"] is True and value["execution_authorized"] is False,
             "review_edit_outcome_unknown")
    try:
        _require(type(value["content_version_id"]) is str
                 and str(UUID(value["content_version_id"])) == value["content_version_id"]
                 and UUID(value["content_version_id"]).int != 0,
                 "review_edit_outcome_unknown")
    except (ValueError, AttributeError):
        raise ReviewIngressError("review_edit_outcome_unknown") from None
    return dict(value)


def handle_edit_reply_webhook(*, enabled=False, raw_body=None, headers=(),
                              policy=None, bindings=None, owner=None, now=None):
    if enabled is not True:
        return {"status": "disabled", "public_send_attempted": False}
    event = verified_edit_reply(raw_body=raw_body, headers=headers, policy=policy,
                                bindings=bindings, now=now)
    try:
        return _receipt(owner.save_edit_reply(event))
    except Exception:
        raise ReviewIngressError("review_edit_outcome_unknown") from None


def verified_edit_reply(*, raw_body, headers, policy, bindings, now):
    """Shared authenticated parsing, never action selection or mutation authority."""
    update = _authenticated_update(raw_body, headers, policy, now)
    _require(set(update) == {"update_id", "message"}, "review_edit_update_invalid")
    _require(type(bindings) is EditBindings, "review_edit_key_invalid")
    message = update["message"]
    _message(message, policy, now)
    _require(now - message["date"] <= 1800, "review_edit_expired")
    actor = message.get("from")
    _require(type(actor) is dict and _positive_int(actor.get("id"))
             and actor.get("is_bot") is False and actor["id"] in dict(policy.reviewers),
             "review_edit_actor_forbidden")
    prompt = message.get("reply_to_message")
    _message(prompt, policy, now)
    sender = prompt.get("from")
    _require(type(sender) is dict and _positive_int(sender.get("id"))
             and sender["id"] == policy.bot_id and sender.get("is_bot") is True,
             "review_edit_bot_forbidden")
    _require(prompt["date"] <= message["date"] and now - prompt["date"] <= 1800
             and prompt["message_id"] < message["message_id"], "review_edit_prompt_invalid")
    _require(message.get("message_thread_id") == prompt.get("message_thread_id"),
             "review_edit_thread_invalid")
    text = message.get("text")
    _require(type(text) is str and 0 < len(text) <= 3700 and bool(text.strip())
             and not _PRIVATE.search(text)
             and not any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in text),
             "review_edit_copy_invalid")
    try:
        _require(len(text.encode("utf-16-le")) // 2 <= 3700, "review_edit_copy_invalid")
    except UnicodeError:
        raise ReviewIngressError("review_edit_copy_invalid") from None
    # Entity formatting is not preserved. Reject hidden text-link/mention/media
    # entities rather than silently changing the user's intended rendered text.
    _require(not message.get("entities"), "review_edit_plain_text_required")
    event = VerifiedEditReply(
        bindings.digest("bot", policy.bot_id),
        bindings.digest("room", policy.bot_id, policy.chat_id),
        bindings.digest("prompt", policy.bot_id, policy.chat_id, prompt["message_id"]),
        bindings.digest("human", policy.bot_id, actor["id"]),
        dict(policy.reviewers)[actor["id"]], text,
        bindings.digest("reply", policy.bot_id, policy.chat_id, message["message_id"]),
        message.get("message_thread_id"), message["date"], prompt["date"],
    )
    # Message identity (not update_id) is the replay key. DB checks exact body,
    # prompt, actor, expiry and version under locks. Never retry a lost commit ACK.
    return event


class PostgresEditReplyOwner:
    """Injected psycopg-compatible connection factory; no credentials or I/O here.

    Each call must receive a NEW non-autocommit connection whose context commits
    on success, rolls back on exception and closes. Not wired to runtime. Runtime
    roles still have no grants; do not use an admin credential as a workaround.
    Registration has a separate local-only owner adapter; neither path is wired
    to a live bot. No retry or fallback.
    """
    def __init__(self, connection_factory):
        self._connect = connection_factory

    def save_edit_reply(self, event):
        _require(type(event) is VerifiedEditReply, "review_edit_event_invalid")
        try:
            _require(str(UUID(event.actor_id)) == event.actor_id, "review_edit_actor_invalid")
            values = (event.bot_binding, event.room_binding, event.message_binding,
                      event.human_binding, event.operation_key)
            _require(all(type(v) is str and re.fullmatch(r"[a-f0-9]{64}", v) for v in values),
                     "review_edit_event_invalid")
            with self._connect() as connection:
                _require(connection.autocommit is False, "review_edit_transaction_required")
                with connection.cursor() as cursor:
                    cursor.execute("""
                        select private.save_content_ops_button_edit_reply(
                            (select id from private.content_ops_button_edit_prompts
                             where bot_binding=%s and room_binding=%s and message_binding=%s
                               and actor_id=%s::uuid), %s,%s,%s,%s,%s,%s)
                        """, (event.bot_binding, event.room_binding, event.message_binding,
                              event.actor_id, event.bot_binding, event.room_binding,
                              event.message_binding, event.human_binding,
                              event.replacement_text, event.operation_key))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1, "review_edit_outcome_unknown")
                    receipt = _receipt(row[0])
            # Only return after the connection context has acknowledged commit.
            return receipt
        except Exception:
            raise ReviewIngressError("review_edit_outcome_unknown") from None
