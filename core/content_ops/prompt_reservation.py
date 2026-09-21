"""Typed local reservation readback. No connection, credentials or transport.

Parent @2 pins UTC microseconds. Legacy @1 is never silently upgraded.
This verifies keyed owner evidence, not that a provider actually sent a packet.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import math

from core.content_ops.prompt_attempt import prompt_instruction
from core.content_ops.prompt_receipt import PromptAttempt, PromptReceiptError, canonical_uuid, digest
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import _positive_int


def _require(value):
    if not value:
        raise PromptReceiptError('prompt_registration_unknown')


def _time(value):
    if type(value) is str:
        value = datetime.fromisoformat(value)
    _require(type(value) is datetime and value.utcoffset() is not None)
    value = value.astimezone(timezone.utc)
    _require(0 < value.timestamp() < 2**32)
    return value


def canonical_timestamp(value):
    return _time(value).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def card_parent_binding(bindings, review, card):
    """Hash the stored typed card, not a provider/webhook-supplied projection."""
    _require(type(bindings) is EditBindings and type(review) is dict and type(card) is dict)
    _require(all(canonical_uuid(v) for v in (card['id'], card['review_id'], review['id'],
        review['workspace_id'], review['content_item_id'], review['content_version_id'])))
    _require(review['client_id'] in ('yellow', 'babylon', 'squid', 'origintrail'))
    _require(card['review_id'] == review['id'] and card['version_fingerprint'] == review['version_fingerprint'])
    _require(type(card['epoch']) is int and card['epoch'] >= 0)
    b = card['bindings']
    _require(type(b) is dict and set(b) == {'bot','room','message','packet_receipt',
                                         'card_receipt','parent_binding','thread_id'})
    _require(all(digest(b[k]) for k in b if k != 'thread_id') and digest(review['version_fingerprint']))
    _require(b['thread_id'] is None or _positive_int(b['thread_id']))
    _require(type(card['parts']) is list and len(card['parts']) == 3)
    parts = []
    for p, kind in zip(card['parts'], ('image', 'telegram', 'x')):
        _require(type(p) is dict and set(p) == {'kind','message_binding','payload_sha256','outcome'})
        _require(p['kind'] == kind and p['outcome'] == 'sent'
                 and digest(p['message_binding']) and digest(p['payload_sha256']))
        parts.append((kind, p['message_binding'], p['payload_sha256']))
    _require(len({b['message'], *(p[1] for p in parts)}) == 4)
    delivered, expires = _time(card['delivered_at']), _time(card['expires_at'])
    _require(delivered < expires <= delivered + timedelta(minutes=30))
    return bindings.digest('prompt-parent@2', card['id'], b['packet_receipt'], b['card_receipt'],
        card['epoch'], review['workspace_id'], review['client_id'], review['content_item_id'],
        review['content_version_id'], review['version_fingerprint'], review['id'], b['bot'],
        b['room'], b['message'], parts, b['thread_id'], canonical_timestamp(delivered),
        canonical_timestamp(expires))


def load_reserved_attempt(review, card, attempt, *, bindings, bot_id, chat_id,
                          human_id, thread_id, observed_at, db_now):
    """Validate precise original times before conservative provider-second mapping."""
    try:
        _require(type(attempt) is dict and type(bindings) is EditBindings)
        _require(_positive_int(bot_id) and _positive_int(human_id) and bot_id != human_id)
        _require(type(chat_id) is int and -(2**52) < chat_id < 0)
        _require(thread_id is None or _positive_int(thread_id))
        _require(card['active'] is True and review['state'] == 'edit_requested')
        _require(all(canonical_uuid(attempt[k]) for k in ('id','card_id','review_id','actor_id')))
        _require(type(attempt['epoch']) is int and type(review['epoch']) is int
                 and attempt['epoch'] == review['epoch'] == card['epoch'] + 1)
        _require(attempt['card_id'] == card['id'] and attempt['review_id'] == review['id']
                 and attempt['version_fingerprint'] == review['version_fingerprint'])
        _require(all(digest(attempt[k]) for k in ('edit_action_key','version_fingerprint',
            'bot_binding','room_binding','human_binding','parent_binding_sha256','expected_text_sha256')))
        b = card['bindings']
        _require(attempt['bot_binding'] == b['bot'] == bindings.digest('bot', bot_id)
                 and attempt['room_binding'] == b['room'] == bindings.digest('room', bot_id, chat_id)
                 and attempt['human_binding'] == bindings.digest('human', bot_id, human_id))
        _require(attempt['thread_id'] is None or _positive_int(attempt['thread_id']))
        _require(attempt['thread_id'] == b['thread_id'] == thread_id)
        parent = card_parent_binding(bindings, review, card)
        _require(parent == b['parent_binding'] == attempt['parent_binding_sha256'])
        _require(attempt['expected_text_sha256'] in {
            hashlib.sha256(prompt_instruction(action).encode()).hexdigest()
            for action in ('edit_telegram', 'edit_x', 'edit_banner')})
        started, expires = _time(attempt['started_at']), _time(attempt['expires_at'])
        observed, now = _time(observed_at), _time(db_now)
        _require(_time(card['delivered_at']) <= started <= observed < expires <= _time(card['expires_at'])
                 and observed <= now < expires and expires <= started + timedelta(minutes=30))
        # Never round an early provider date up or renew the persisted lifetime.
        start_second, expiry_second = math.ceil(started.timestamp()), math.floor(expires.timestamp())
        _require(start_second <= math.floor(observed.timestamp()) < expiry_second)
        return PromptAttempt(attempt['id'], review['id'], attempt['actor_id'], attempt['epoch'],
            attempt['edit_action_key'], attempt['version_fingerprint'], parent,
            attempt['expected_text_sha256'], bot_id, chat_id, human_id,
            start_second, expiry_second, thread_id)
    except Exception:
        raise PromptReceiptError('prompt_registration_unknown') from None
