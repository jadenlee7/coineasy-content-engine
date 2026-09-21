"""Pure pre-send planning, not a courier, registration authority or durable log.

Inputs must be read by the existing trusted owner from its registered control
card and reviewer/action records, NEVER supplied by a webhook or client. A
three-part bundle's last X message is NOT a registered interactive control card.
The required live reader and durable pre-send writer are not implemented here.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.content_ops.prompt_receipt import PromptAttempt, canonical_uuid, digest
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import _positive_int
import hashlib


class PromptPlanError(ValueError):
    """Fixed, minimized refusal; no raw owner record or employee identifiers."""


def _require(condition):
    if not condition:
        raise PromptPlanError('prompt_plan_unavailable')


@dataclass(frozen=True, repr=False)
class PromptTarget:
    workspace_id: str
    client_id: str
    content_item_id: str
    content_version_id: str
    version_fingerprint: str


@dataclass(frozen=True, repr=False)
class PromptReviewContext:
    target: PromptTarget
    review_id: str
    card_registration_id: str
    actor_id: str
    human_binding: str
    epoch: int
    edit_action_key: str
    action: str
    requested_at: int
    expires_at: int
    state: str = 'edit_requested'
    actor_active: bool = False


@dataclass(frozen=True, repr=False)
class DeliveredPacketPart:
    kind: str
    message_binding: str
    payload_sha256: str
    outcome: str = 'sent'


@dataclass(frozen=True, repr=False)
class RegisteredControlCard:
    target: PromptTarget
    registration_id: str
    review_id: str
    epoch: int
    bot_binding: str
    room_binding: str
    message_binding: str
    packet_receipt_sha256: str
    card_receipt_sha256: str
    parts: tuple[DeliveredPacketPart, ...]
    delivered_at: int
    expires_at: int
    thread_id: int | None = None
    active: bool = False
    status: str = 'unregistered'


def prompt_instruction(action):
    if type(action) is str and action == 'edit_banner':
        return '배너에서 바꿀 디자인을 이 메시지에 답장해주세요(600자 이내). 원문·로고는 유지하며, 재제작 후 다시 검수합니다. 자동 게시되지 않습니다.'
    """Fixed plain text only; changing a template requires a new pinned attempt."""
    if type(action) is str and action == 'edit_telegram':
        return '수정할 Telegram 공지 전문을 이 메시지에 답장해주세요. 저장 후 다시 검수하며, 자동 게시되지 않습니다.'
    if type(action) is str and action == 'edit_x':
        return '수정할 X 게시글 전문을 이 메시지에 답장해주세요. 저장 후 다시 검수하며, 자동 게시되지 않습니다.'
    raise PromptPlanError('prompt_plan_unavailable')


def _target(value):
    _require(type(value) is PromptTarget)
    _require(all(canonical_uuid(v) for v in
                 (value.workspace_id, value.content_item_id, value.content_version_id)))
    _require(type(value.client_id) is str
             and value.client_id in ('yellow', 'babylon', 'squid', 'origintrail'))
    _require(digest(value.version_fingerprint))


def plan_prompt_attempt(*, enabled=False, review=None, card=None, bindings=None,
                        attempt_id=None, bot_id=None, chat_id=None, human_id=None,
                        thread_id=None, now=None):
    """Return an in-memory plan, not permission to send or evidence of delivery.

    The owner must persist/reconcile the exact attempt BEFORE any future send.
    This function never generates a retry ID, extends card/review expiry, enrolls
    staff or proves transport provenance. Supplied active/registered flags are
    projections of trusted records, not caller authorization.
    """
    if enabled is not True:
        return None
    try:
        _require(type(review) is PromptReviewContext and type(card) is RegisteredControlCard)
        _require(type(bindings) is EditBindings and canonical_uuid(attempt_id))
        _target(review.target); _target(card.target)
        _require(review.target == card.target and review.review_id == card.review_id
                 and review.card_registration_id == card.registration_id)
        _require(all(canonical_uuid(v) for v in
                     (review.review_id, review.card_registration_id, review.actor_id, card.registration_id)))
        _require(type(review.epoch) is int and review.epoch > 0
                 and type(card.epoch) is int and card.epoch >= 0
                 and review.epoch == card.epoch + 1
                 and type(review.state) is str and review.state == 'edit_requested'
                 and review.actor_active is True and card.active is True
                 and type(card.status) is str and card.status == 'registered')
        _require(all(digest(v) for v in (review.edit_action_key, review.human_binding,
            card.bot_binding, card.room_binding, card.message_binding, card.packet_receipt_sha256,
            card.card_receipt_sha256)))
        _require(_positive_int(bot_id) and _positive_int(human_id) and bot_id != human_id)
        _require(type(chat_id) is int and -(2**52) < chat_id < 0)
        _require(thread_id is None or _positive_int(thread_id))
        _require(card.thread_id is None or _positive_int(card.thread_id))
        _require(thread_id == card.thread_id)
        _require(bindings.digest('bot', bot_id) == card.bot_binding
                 and bindings.digest('room', bot_id, chat_id) == card.room_binding
                 and bindings.digest('human', bot_id, human_id) == review.human_binding)
        _require(all(type(v) is int and 0 < v < 2**32 for v in
                     (now, review.requested_at, review.expires_at, card.delivered_at, card.expires_at)))
        _require(card.delivered_at <= review.requested_at <= now < card.expires_at
                 and now < review.expires_at and now-card.delivered_at < 1800)
        _require(type(card.parts) is tuple and len(card.parts) == 3)
        messages = [card.message_binding]
        parent_parts = []
        for part, kind in zip(card.parts, ('image', 'telegram', 'x')):
            _require(type(part) is DeliveredPacketPart and type(part.kind) is str
                     and part.kind == kind and type(part.outcome) is str and part.outcome == 'sent'
                     and digest(part.message_binding) and digest(part.payload_sha256))
            messages.append(part.message_binding)
            parent_parts.append((kind, part.message_binding, part.payload_sha256))
        # A bundle part cannot be recycled as the control card; every part and
        # the separately registered interactive message must have its own ID.
        _require(len(set(messages)) == 4)
        target = review.target
        parent_digest = bindings.digest('prompt-parent@1', card.registration_id,
            card.packet_receipt_sha256, card.card_receipt_sha256, card.epoch,
            target.workspace_id, target.client_id,
            target.content_item_id, target.content_version_id, target.version_fingerprint,
            card.review_id, card.bot_binding, card.room_binding, card.message_binding,
            parent_parts, card.thread_id, card.delivered_at, card.expires_at)
        return PromptAttempt(attempt_id, review.review_id, review.actor_id, review.epoch,
            review.edit_action_key, target.version_fingerprint, parent_digest,
            hashlib.sha256(prompt_instruction(review.action).encode('utf-8')).hexdigest(),
            bot_id, chat_id, human_id, now,
            min(review.expires_at, card.expires_at, card.delivered_at+1800, now+1800), thread_id)
    except Exception:
        raise PromptPlanError('prompt_plan_unavailable') from None
