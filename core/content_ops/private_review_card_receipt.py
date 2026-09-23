"""Prepare a private card and validate four owner-observed Telegram receipts.

This is deliberately not a sender, DB writer, poller or approval route. A
separate, one-shot courier must reserve delivery durably before any network I/O
and pass its *direct* Telegram responses here in order. Unknown delivery is
never converted into a card or retried. The callback owner uses the resulting
bindings only after its separate card-registration transaction commits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from core.content_ops.prompt_reservation import card_parent_binding, canonical_timestamp
from core.content_ops.review_buttons import ButtonSigner, ReviewSnapshot, review_messages
from core.content_ops.review_edit_ingress import EditBindings


class CardReceiptError(ValueError):
    """Fixed non-sensitive failure code only."""


def _require(value):
    if not value:
        raise CardReceiptError("private_card_receipt_unknown")


def _uuid(value):
    try:
        _require(type(value) is str and str(UUID(value)) == value and UUID(value).int != 0)
    except (ValueError, AttributeError):
        raise CardReceiptError("private_card_receipt_unknown") from None
    return value


def _utc(value):
    _require(type(value) is datetime and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def part_payload_sha256(request, banner_sha256):
    """One canonical payload hash for reservation, confirmation and card row."""
    _require(type(request) is dict and request.get("kind") in
             ("image", "telegram", "x", "controls"))
    payload = {"kind": request["kind"], "method": request["method"],
               "text": request["text"], "reply_markup": request.get("reply_markup"),
               "banner_sha256": banner_sha256 if request["kind"] == "image" else None}
    return _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


@dataclass(frozen=True, repr=False)
class ObservedSend:
    """A direct, authenticated transport observation; never a webhook body."""

    method: str
    http_status: int
    raw_response: bytes
    observed_at: datetime


def prepare_private_card(snapshot, signer, room_binding, *, now):
    """Render all four immutable requests without sending any of them."""
    _require(type(snapshot) is ReviewSnapshot and type(signer) is ButtonSigner)
    _require(type(now) is int and now > 0 and snapshot.eligibility == "blocked")
    messages = review_messages(snapshot, signer, room_binding, now=now, private_only=True)
    caption = (f"{snapshot.client_id.upper()} · 비공개 검수용 (공개 게시 아님)\n"
               f"버전: {snapshot.content_version_id}\n"
               "이미지·Telegram·X 전문과 검수 버튼을 모두 확인해주세요.")
    _require(len(caption.encode("utf-16-le")) // 2 <= 1024)
    return (
        {"kind": "image", "method": "sendPhoto", "text": caption},
        {"kind": "telegram", "method": "sendMessage", "text": messages["telegram"]["text"]},
        {"kind": "x", "method": "sendMessage", "text": messages["x"]["text"]},
        {"kind": "controls", "method": "sendMessage", "text": messages["controls"]["text"],
         "reply_markup": messages["controls"]["reply_markup"]},
    )


def _validated_response(observation, expected, *, bot_id, chat_id, thread_id):
    _require(type(observation) is ObservedSend and observation.method == expected["method"]
             and type(observation.http_status) is int and observation.http_status == 200
             and type(observation.raw_response) is bytes
             and 0 < len(observation.raw_response) <= 65536)
    observed_at = _utc(observation.observed_at)
    try:
        body = json.loads(observation.raw_response.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, TypeError):
        raise CardReceiptError("private_card_receipt_unknown") from None
    _require(type(body) is dict and body.get("ok") is True and type(body.get("result")) is dict)
    message = body["result"]
    chat, sender = message.get("chat"), message.get("from")
    mid, sent_at = message.get("message_id"), message.get("date")
    _require(type(chat) is dict and type(sender) is dict
             and type(chat.get("id")) is int and chat["id"] == chat_id
             and chat.get("type") == "supergroup"
             and not any(k in chat for k in ("username", "active_usernames", "linked_chat_id"))
             and type(sender.get("id")) is int and sender["id"] == bot_id
             and sender.get("is_bot") is True
             and type(mid) is int and 0 < mid < 2**53
             and type(sent_at) is int and 0 < sent_at <= int(observed_at.timestamp())
             and int(observed_at.timestamp()) - sent_at <= 60
             and message.get("message_thread_id") == thread_id
             and not any(k in message for k in ("sender_chat", "forward_origin", "edit_date")))
    if expected["kind"] == "image":
        photos = message.get("photo")
        _require(message.get("caption") == expected["text"]
                 and type(photos) is list and bool(photos)
                 and all(type(photo) is dict and type(photo.get("file_id")) is str
                         and bool(photo["file_id"]) for photo in photos))
    else:
        _require(message.get("text") == expected["text"])
    if expected["kind"] == "controls":
        _require(message.get("reply_markup") == expected["reply_markup"])
    else:
        _require("reply_markup" not in message)
    return mid, observed_at, _sha(observation.raw_response)


def validate_part_response(observation, expected, *, bot_id, chat_id, thread_id):
    """Stop the courier after any uncertain part; never advance to controls."""
    try:
        return _validated_response(observation, expected, bot_id=bot_id,
                                   chat_id=chat_id, thread_id=thread_id)
    except Exception:
        raise CardReceiptError("private_card_receipt_unknown") from None


def validate_candidate(review, snapshot, card_id, *, now):
    """Reject stale/unbound source or review before any reservation or send."""
    try:
        _validate_candidate(review, snapshot, card_id, now=now)
    except Exception:
        raise CardReceiptError("private_card_candidate_invalid") from None


def _validate_candidate(review, snapshot, card_id, *, now):
    _require(type(review) is dict and type(snapshot) is ReviewSnapshot)
    snapshot.validate()
    for field in ("id", "workspace_id", "content_item_id", "content_version_id"):
        _uuid(review.get(field))
    _uuid(card_id)
    _require(review.get("workspace_id") == snapshot.workspace_id
             and review.get("client_id") == snapshot.client_id
             and review.get("content_item_id") == snapshot.content_item_id
             and review.get("content_version_id") == snapshot.content_version_id
             and type(review.get("version_fingerprint")) is str
             and len(review["version_fingerprint"]) == 64
             and all(c in "0123456789abcdef" for c in review["version_fingerprint"])
             and type(review.get("epoch")) is int and review["epoch"] >= 0
             and review.get("state") == "active"
             and snapshot.eligibility == "blocked")
    _require(type(now) is int and 0 < now < 2**32)
    current = datetime.fromtimestamp(now, timezone.utc)
    source_at = _utc(datetime.fromisoformat(snapshot.source_published_at.replace("Z", "+00:00")))
    expires = _utc(datetime.fromisoformat(review["expires_at"].replace("Z", "+00:00")))
    _require(timedelta(0) <= current - source_at < timedelta(hours=24)
             and current < expires)


def card_registration_evidence(*, review, snapshot, card_id, signer, bindings,
                               room_binding, bot_id, chat_id, thread_id, now,
                               banner_sha256, observations):
    """Build exact registration evidence for the guarded DB wrapper.

    The caller must prove the review was owner-created, source/current version
    remain eligible, the image bytes match ``banner_sha256``, all four sends
    were durably reserved, and each response came from its own Telegram POST.
    This pure validator cannot establish any of those transport/DB facts.
    Response hashes and the fourth controls payload hash are included so the
    wrapper can match all four durable send confirmations before recording.
    """
    try:
        return _card_registration_evidence(review=review, snapshot=snapshot, card_id=card_id,
            signer=signer, bindings=bindings, room_binding=room_binding, bot_id=bot_id,
            chat_id=chat_id, thread_id=thread_id, now=now,
            banner_sha256=banner_sha256, observations=observations)
    except Exception:
        # Including malformed JSON, dates and DB projections: no private body,
        # identity or transport response may escape through an error message.
        raise CardReceiptError("private_card_receipt_unknown") from None


def _card_registration_evidence(*, review, snapshot, card_id, signer, bindings,
                                room_binding, bot_id, chat_id, thread_id, now,
                                banner_sha256, observations):
    _require(type(review) is dict and type(snapshot) is ReviewSnapshot
             and type(bindings) is EditBindings and type(signer) is ButtonSigner
             and type(bot_id) is int and bot_id > 0
             and type(chat_id) is int and -(2**52) < chat_id < 0
             and (thread_id is None or type(thread_id) is int and 0 < thread_id < 2**52)
             and type(observations) in (list, tuple) and len(observations) == 4
             and banner_sha256 == snapshot.banner_sha256)
    _validate_candidate(review, snapshot, card_id, now=now)
    packet = prepare_private_card(snapshot, signer, room_binding, now=now)
    validated = tuple(_validated_response(o, p, bot_id=bot_id, chat_id=chat_id,
                                          thread_id=thread_id)
                      for p, o in zip(packet, observations))
    ids = [item[0] for item in validated]
    _require(ids == sorted(set(ids)))
    times = [item[1] for item in validated]
    _require(times == sorted(times) and times[0] >= datetime.fromtimestamp(now, timezone.utc)
             and times[-1] - times[0] < timedelta(minutes=30))
    delivered = times[-1]
    source_at = _utc(datetime.fromisoformat(snapshot.source_published_at.replace("Z", "+00:00")))
    _require(timedelta(0) <= delivered - source_at < timedelta(hours=24))
    review_expiry = _utc(datetime.fromisoformat(review["expires_at"].replace("Z", "+00:00")))
    expiry = min(review_expiry, datetime.fromtimestamp(now + 1800, timezone.utc))
    _require(delivered < expiry)
    part_names = ("image", "telegram", "x")
    parts = []
    for index, kind in enumerate(part_names):
        parts.append({"kind": kind, "outcome": "sent",
                      "message_binding": bindings.digest("card-message@2", bot_id, chat_id, ids[index]),
                      "payload_sha256": part_payload_sha256(packet[index], banner_sha256)})
    packet_receipt = bindings.digest("card-packet-receipt@2", card_id,
        [(p["message_binding"], item[2]) for p, item in zip(parts, validated[:3])])
    card_receipt = bindings.digest("card-controls-receipt@2", card_id, ids[3], validated[3][2])
    target_bindings = {
        "bot": bindings.digest("bot", bot_id),
        "room": bindings.digest("room", bot_id, chat_id),
        "message": bindings.digest("card-message@2", bot_id, chat_id, ids[3]),
        "packet_receipt": packet_receipt,
        "card_receipt": card_receipt,
        "parent_binding": "0" * 64,
        "thread_id": thread_id,
    }
    card = {"id": card_id, "review_id": review["id"], "epoch": review["epoch"],
            "version_fingerprint": review["version_fingerprint"], "bindings": target_bindings,
            "parts": parts, "delivered_at": canonical_timestamp(delivered),
            "expires_at": canonical_timestamp(expiry)}
    target_bindings["parent_binding"] = card_parent_binding(bindings, review, card)
    return {"target_review_id": review["id"], "target_card_id": card_id,
            "expected_fingerprint": review["version_fingerprint"],
            "target_epoch": review["epoch"], "target_bindings": target_bindings,
            "target_parts": parts, "delivered": card["delivered_at"],
            "expires": card["expires_at"],
            "controls_payload_sha256": part_payload_sha256(packet[3], banner_sha256),
            "response_sha256s": [item[2] for item in validated]}
