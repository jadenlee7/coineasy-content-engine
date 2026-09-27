"""Default-OFF webhook boundary for the existing button-review controller.

Library only: no route, webhook registration, polling, credentials lookup,
Telegram acknowledgment, database adapter, or provider send. An existing ingress
owner may call this after a separately authorized integration. A polling owner
must NOT install a second webhook just to use this library.
"""
from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass
from typing import Protocol

from core.content_ops.review_buttons import (
    ButtonReviewError, VerifiedCallback, handle_review_callback,
)

MAX_BODY_BYTES = 32_768
SECRET_HEADER = "x-telegram-bot-api-secret-token"


class ReviewIngressError(ValueError):
    """Only bounded, fixed codes; never reflect incoming data or owner errors."""


def _require(ok, code="review_ingress_invalid"):
    if not ok:
        raise ReviewIngressError(code)


def _positive_int(value):
    return type(value) is int and 0 < value < 2**52


def _opaque(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None


@dataclass(frozen=True, repr=False)
class IngressPolicy:
    # All values come from trusted server configuration, never from the update.
    webhook_secret: str
    bot_id: int
    chat_id: int
    room_binding: str
    # Immutable mapping from Telegram user ID to the existing owner's actor ID.
    reviewers: tuple[tuple[int, str], ...]

    def validate(self):
        _require(type(self.webhook_secret) is str and re.fullmatch(
            r"[A-Za-z0-9_-]{32,256}", self.webhook_secret) is not None,
            "review_ingress_policy_invalid")
        _require(_positive_int(self.bot_id) and type(self.chat_id) is int
                 and -(2**52) < self.chat_id < 0 and _opaque(self.room_binding),
                 "review_ingress_policy_invalid")
        _require(type(self.reviewers) is tuple and 1 <= len(self.reviewers) <= 100,
                 "review_ingress_policy_invalid")
        ids, actors = set(), set()
        for pair in self.reviewers:
            _require(type(pair) is tuple and len(pair) == 2,
                     "review_ingress_policy_invalid")
            user_id, actor = pair
            _require(_positive_int(user_id) and user_id != self.bot_id and _opaque(actor)
                     and user_id not in ids and actor not in actors,
                     "review_ingress_policy_invalid")
            ids.add(user_id)
            actors.add(actor)


class RegisteredReviewOwner(Protocol):
    def resolve_review_message(self, *, bot_id: int, chat_id: int,
                               message_id: int, room_binding: str) -> str:
        """Read only. Return an opaque binding for a fully delivered control card.

        Must match exact bot/room/message and all packet receipts in the existing
        owner ledger. Unknown, partial, revoked or unregistered cards must fail.
        read_review/apply_review_action then recheck this registration under the
        existing transaction contract. This is NOT a new approval authority.
        """


def _headers(pairs):
    # Preserve repeated headers at the HTTP boundary; a pre-flattened dict could
    # hide conflicting authentication values, so it is intentionally rejected.
    _require(type(pairs) in (list, tuple) and len(pairs) <= 64)
    result = {}
    for pair in pairs:
        _require(type(pair) in (list, tuple) and len(pair) == 2)
        name, value = pair
        _require(type(name) is str and type(value) is str
                 and re.fullmatch(r"[A-Za-z0-9-]{1,128}", name) is not None
                 and len(value) <= 4096 and "\r" not in value and "\n" not in value)
        name = name.lower()
        if name in {SECRET_HEADER, "content-type", "content-encoding", "content-length"}:
            _require(name not in result, "review_ingress_duplicate_header")
            result[name] = value
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "review_ingress_duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ReviewIngressError("review_ingress_invalid_json")


def _authenticated_update(raw_body, headers, policy, now):
    """Shared raw HTTP authentication/JSON boundary; no owner I/O."""
    _require(type(policy) is IngressPolicy, "review_ingress_policy_invalid")
    policy.validate()
    hs = _headers(headers)
    supplied = hs.get(SECRET_HEADER, "")
    # Authentication precedes JSON parsing, message resolution and owner I/O.
    _require(supplied.isascii() and hmac.compare_digest(supplied, policy.webhook_secret),
             "review_ingress_unauthorized")
    _require(type(raw_body) is bytes and 0 < len(raw_body) <= MAX_BODY_BYTES,
             "review_ingress_body_invalid")
    _require(hs.get("content-type", "").lower() in {
        "application/json", "application/json; charset=utf-8"}, "review_ingress_media_invalid")
    _require("content-encoding" not in hs, "review_ingress_media_invalid")
    if "content-length" in hs:
        _require(hs["content-length"].isdigit() and len(hs["content-length"]) <= 6
                 and int(hs["content-length"]) == len(raw_body), "review_ingress_body_invalid")
    _require(type(now) is int and 0 < now < 2**32)
    try:
        update = json.loads(raw_body.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ReviewIngressError("review_ingress_invalid_json") from None
    _require(type(update) is dict and "update_id" in update)
    _require(type(update["update_id"]) is int and 0 <= update["update_id"] < 2**52)
    return update


def _parse_callback(raw_body, headers, policy, now, *, private_only=False):
    update = _authenticated_update(raw_body, headers, policy, now)
    _require(set(update) == {"update_id", "callback_query"})
    query = update["callback_query"]
    _require(type(query) is dict and not ({"inline_message_id", "game_short_name"} & query.keys()))
    if private_only:
        _require(type(query.get("data")) is str
                 and query["data"].startswith("ce1:"),
                 "review_ingress_private_namespace_required")
        query["data"] = query["data"][4:]
    actor, message = query.get("from"), query.get("message")
    _require(type(actor) is dict and type(message) is dict)
    _require(_positive_int(actor.get("id")) and actor.get("is_bot") is False
             and actor["id"] in dict(policy.reviewers), "review_ingress_actor_forbidden")
    chat, sender = message.get("chat"), message.get("from")
    _require(type(chat) is dict and type(sender) is dict)
    _require(type(chat.get("id")) is int and chat["id"] == policy.chat_id
             and chat.get("type") == "supergroup"
             and not ({"username", "linked_chat_id"} & chat.keys()),
             "review_ingress_room_forbidden")
    _require(_positive_int(sender.get("id")) and sender["id"] == policy.bot_id
             and sender.get("is_bot") is True, "review_ingress_bot_forbidden")
    _require(_positive_int(message.get("message_id"))
             and type(message.get("date")) is int and 0 < message["date"] <= now,
             "review_ingress_message_invalid")
    # Inline, business, forwarded and anonymous channel-origin copies are not
    # eligible control cards, even when their text resembles a real review.
    _require(not ({"forward_origin", "forward_from", "forward_from_chat", "sender_chat",
                   "via_bot", "business_connection_id", "is_automatic_forward",
                   "is_from_offline", "guest_query_id"} & message.keys()),
             "review_ingress_message_invalid")
    _require(_opaque(query.get("id")) and type(query.get("data")) is str
             and re.fullmatch(r"[A-Za-z0-9_-]{51}", query["data"]) is not None)
    return query, dict(policy.reviewers)[actor["id"]]


def handle_review_webhook(*, enabled=False, raw_body=None, headers=(), policy=None,
                          signer=None, owner=None, now=None, private_only=False):
    """Validate an update and delegate one action; never send/acknowledge here.

    The existing owner must persist idempotency by callback query ID in the same
    transaction as the action. No in-memory dedupe or second polling offset is
    introduced. Owner exceptions become UNKNOWN; callers must reconcile rather
    than try another owner or invent a successful receipt. HTTP response/ack
    policy and Telegram answerCallbackQuery remain the ingress owner's job.
    """
    if enabled is not True:
        return {"status": "disabled", "public_send_attempted": False}
    _require(type(private_only) is bool, "review_ingress_policy_invalid")
    query, actor_id = _parse_callback(raw_body, headers, policy, now, private_only=private_only)
    try:
        binding = owner.resolve_review_message(bot_id=policy.bot_id, chat_id=policy.chat_id,
            message_id=query["message"]["message_id"], room_binding=policy.room_binding)
    except Exception:
        raise ReviewIngressError("review_ingress_registration_unknown") from None
    _require(_opaque(binding), "review_ingress_registration_unknown")
    event = VerifiedCallback(query["id"], actor_id, False, policy.room_binding,
                             binding, query["data"])
    try:
        return handle_review_callback(event, enabled=True, signer=signer, owner=owner,
            allowed_reviewers=frozenset(actor for _, actor in policy.reviewers),
            room_binding=policy.room_binding, now=now, private_only=private_only)
    except ButtonReviewError:
        # Includes stale buttons and potentially lost/invalid owner readback.
        # Conservatively avoid claiming that no owner mutation occurred.
        raise ReviewIngressError("review_ingress_action_unconfirmed") from None
    except Exception:
        raise ReviewIngressError("review_ingress_owner_outcome_unknown") from None
