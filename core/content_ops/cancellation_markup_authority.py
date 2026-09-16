"""Exact stored-evidence verifier, not an approval issuer or receipt importer.

owner_reader MUST read authenticated owner records using the supplied guarded
cursor, with approval/receipt rows locked against concurrent replacement or
revocation until the guard exits. Never build these records from request JSON,
chat text, environment booleans or a provider-shaped body supplied by a caller.
No production reader, storage schema or permissive fallback is provided here.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from core.content_ops.cancellation_markup import _check_plan, encode
from core.content_ops.cancellation_markup_owner import _inputs
from core.content_ops.prompt_receipt import canonical_uuid, digest
from core.content_ops.prompt_reservation import canonical_timestamp
from core.content_ops.review_ingress import _unique_object, _reject_constant


@dataclass(frozen=True, repr=False)
class OriginalControlEvidence:
    card_id: str
    receipt_sha256: str
    parent_binding_sha256: str
    bot_id: int
    chat_id: int
    message_id: int
    thread_id: int | None
    delivered_at: datetime
    message_date: int
    text_sha256: str
    entities_json: str
    markup_json: str


@dataclass(frozen=True, repr=False)
class MarkupExecutionApproval:
    approval_id: str
    attempt_id: str
    card_id: str
    actor_id: str
    human_binding: str
    plan_seal: str
    original_receipt_sha256: str
    action: str
    approved_at: datetime
    expires_at: datetime
    active: bool


def _require(value):
    if not value:
        raise ValueError('cancellation_markup_authority_refused')


def _json(value):
    _require(type(value) is str and 0 < len(value.encode()) <= 32768)
    parsed = json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    _require(encode(parsed) == value)
    return parsed


def verify_control_evidence(evidence, plan, bindings, locked_card):
    """Shared original-message checks only; grants no action authority."""
    _require(type(evidence) is OriginalControlEvidence)
    b = locked_card['bindings']
    _require(evidence.card_id == plan.card_id)
    _require(digest(evidence.receipt_sha256) and digest(evidence.parent_binding_sha256))
    _require(evidence.receipt_sha256 == b['card_receipt']
             and evidence.parent_binding_sha256 == b['parent_binding'])
    for field in ('bot_id','chat_id','message_id','message_date'):
        _require(type(getattr(evidence,field)) is int and getattr(evidence,field) == getattr(plan,field))
    _require(evidence.thread_id is None or type(evidence.thread_id) is int)
    _require(evidence.thread_id == plan.thread_id == b['thread_id'])
    _require(b['bot'] == bindings.digest('bot',evidence.bot_id)
             and b['room'] == bindings.digest('room',evidence.bot_id,evidence.chat_id)
             and b['message'] == bindings.digest('card-message@2',evidence.bot_id,
                                                evidence.chat_id,evidence.message_id))
    _require(type(evidence.delivered_at) is datetime and evidence.delivered_at.utcoffset() is not None)
    _require(canonical_timestamp(evidence.delivered_at) == canonical_timestamp(locked_card['delivered_at']))
    _require(evidence.message_date <= int(evidence.delivered_at.timestamp()) <= plan.started_at)
    _require(digest(evidence.text_sha256) and evidence.text_sha256 == plan.text_sha256)
    _require(evidence.entities_json == plan.entities_json and type(_json(evidence.entities_json)) is list)
    original = _json(evidence.markup_json)
    _require(type(original) is dict and set(original) == {'inline_keyboard'}
             and type(original['inline_keyboard']) is list)
    proposed = json.loads(plan.request_json)['reply_markup']['inline_keyboard']
    _require(proposed[:-1] == original['inline_keyboard'])


class ExactCancellationMarkupAuthority:
    """Default OFF, fresh owner read per check; errors become False, no cache.

    Approval expiry MUST equal the sealed plan expiry: the guard's DB clock
    check after this call cannot accidentally extend a shorter approval window.
    A different approval window needs a new plan and renewed exact approval.
    """
    def __init__(self, owner_reader, *, enabled=False):
        self._reader = owner_reader
        self._enabled = enabled

    def __call__(self, *, cursor=None, locked_card=None, now=None, **identity):
        if self._enabled is not True:
            return False
        try:
            _inputs(**identity)
            _require(cursor is not None and callable(self._reader))
            _require(type(now) is datetime and now.utcoffset() is not None)
            plan, bindings = identity['plan'], identity['bindings']
            _check_plan(plan, bindings, int(now.timestamp()))
            _require(type(locked_card) is dict and locked_card.get('id') == plan.card_id
                     and locked_card.get('active') is True)
            evidence, approval = self._reader(cursor=cursor, card_id=plan.card_id,
                attempt_id=identity['attempt_id'], actor_id=identity['actor_id'])
            _require(type(evidence) is OriginalControlEvidence
                     and type(approval) is MarkupExecutionApproval)
            verify_control_evidence(evidence,plan,bindings,locked_card)
            _require(approval.card_id == plan.card_id
                     and approval.original_receipt_sha256 == evidence.receipt_sha256)
            # The DB guard separately verifies the final cancellation row's MAC.
            _require(all(canonical_uuid(v) for v in (approval.approval_id,approval.attempt_id,
                                                    approval.card_id,approval.actor_id)))
            _require(approval.attempt_id == identity['attempt_id'] and approval.actor_id == identity['actor_id'])
            _require(approval.human_binding == bindings.digest('human',plan.bot_id,identity['human_id']))
            _require(approval.plan_seal == plan.seal and approval.action == 'append_cancellation_markup@1'
                     and approval.active is True)
            _require(all(type(v) is datetime and v.utcoffset() is not None
                         for v in (approval.approved_at,approval.expires_at)))
            _require(datetime.fromtimestamp(plan.started_at,timezone.utc) <= approval.approved_at <= now
                     < approval.expires_at == datetime.fromtimestamp(plan.expires_at,timezone.utc))
            return True
        except Exception:
            return False
