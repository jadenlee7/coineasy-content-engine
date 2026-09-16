"""Pure edit-prompt response validation; no send, enrollment or DB writer.

The existing courier must load the attempt from its own durable ledger after
checking packet/control-card lineage. Never construct attempts from webhook
payloads or accept caller-supplied HTTP replies as provider authentication.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from uuid import UUID

from core.content_ops.review_edit_ingress import EditBindings, _UNSUPPORTED
from core.content_ops.review_ingress import _positive_int, _unique_object, _reject_constant


class PromptReceiptError(ValueError):
    """Fixed codes only. Failure after send means unknown, never resend."""


def require(condition):
    if not condition:
        raise PromptReceiptError('prompt_receipt_unconfirmed')


def canonical_uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except ValueError:
        return False


def digest(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None


@dataclass(frozen=True, repr=False)
class PromptAttempt:
    # All fields are trusted owner ledger values pinned BEFORE its send.
    attempt_id: str
    review_id: str
    actor_id: str
    epoch: int
    edit_action_key: str
    version_fingerprint: str
    packet_receipt_sha256: str
    expected_text_sha256: str
    bot_id: int
    chat_id: int
    human_id: int
    started_at: int
    expires_at: int
    thread_id: int | None = None


@dataclass(frozen=True, repr=False)
class ValidatedPromptReceipt:
    id: str
    review_id: str
    actor_id: str
    epoch: int
    edit_action_key: str
    bot_binding: str
    room_binding: str
    message_binding: str
    human_binding: str
    receipt_sha256: str
    # Provider second-resolution date is NOT the database owner's precise
    # delivery observation timestamp. The writer must record that separately.
    provider_message_date: int
    # Advisory projection only; constructing this type grants no DB permission.
    outcome: str = 'sent'


def validate_prompt_response(*, enabled=False, attempt=None, http_status=None,
                             raw_response=None, bindings=None, observed_at=None):
    """Return a minimized matched receipt, or raise fixed unconfirmed outcome.

    Only the courier's authenticated, no-redirect sendMessage transport may
    supply a response. This function cannot prove network origin. No private
    text, raw response, room or human identifiers appear in its result/repr.
    Unknown/rejected results do not yield an eligible registration receipt.
    """
    if enabled is not True:
        return None
    try:
        require(type(attempt) is PromptAttempt and type(bindings) is EditBindings)
        require(all(canonical_uuid(v) for v in (attempt.attempt_id, attempt.review_id, attempt.actor_id)))
        require(all(digest(v) for v in (attempt.edit_action_key, attempt.version_fingerprint,
                                      attempt.packet_receipt_sha256, attempt.expected_text_sha256)))
        require(type(attempt.epoch) is int and attempt.epoch > 0)
        require(_positive_int(attempt.bot_id) and _positive_int(attempt.human_id)
                and attempt.bot_id != attempt.human_id)
        require(type(attempt.chat_id) is int and -(2**52) < attempt.chat_id < 0)
        require(attempt.thread_id is None or _positive_int(attempt.thread_id))
        require(all(type(v) is int and 0 < v < 2**32 for v in
                    (attempt.started_at, attempt.expires_at, observed_at)))
        require(attempt.started_at <= observed_at < attempt.expires_at
                <= attempt.started_at + 1800)
        require(type(http_status) is int and http_status == 200)
        require(type(raw_response) is bytes and 0 < len(raw_response) <= 32768)
        raw = json.loads(raw_response.decode('utf-8'), object_pairs_hook=_unique_object,
                         parse_constant=_reject_constant)
        require(type(raw) is dict and set(raw) == {'ok','result'} and raw['ok'] is True)
        result = raw['result']
        require(type(result) is dict and not (_UNSUPPORTED & result.keys()))
        # Plain-text prompt contract; no hidden entities/markup, reply substitution
        # or rich media. Extra harmless Telegram sender/chat fields are ignored.
        require(not result.get('entities') and 'reply_to_message' not in result
                and 'reply_markup' not in result)
        chat, sender = result.get('chat'), result.get('from')
        require(type(chat) is dict and type(sender) is dict)
        require(type(chat.get('id')) is int and chat['id'] == attempt.chat_id
                and chat.get('type') == 'supergroup' and not ({'username','linked_chat_id'} & chat.keys()))
        require(_positive_int(sender.get('id')) and sender['id'] == attempt.bot_id
                and sender.get('is_bot') is True)
        require(_positive_int(result.get('message_id')))
        require(type(result.get('date')) is int
                and attempt.started_at <= result['date'] <= observed_at)
        require(result.get('message_thread_id') == attempt.thread_id)
        if 'message_thread_id' in result:
            require(_positive_int(result['message_thread_id']))
        text = result.get('text')
        require(type(text) is str and text.strip() and len(text.encode('utf-16-le'))//2 <= 4096)
        text_sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
        require(text_sha == attempt.expected_text_sha256)
        bot = bindings.digest('bot',attempt.bot_id)
        room = bindings.digest('room',attempt.bot_id,attempt.chat_id)
        message = bindings.digest('prompt',attempt.bot_id,attempt.chat_id,result['message_id'])
        human = bindings.digest('human',attempt.bot_id,attempt.human_id)
        # Stable selected-field digest ignores provider display names/JSON order.
        sha = bindings.digest('prompt-receipt',attempt.attempt_id,attempt.review_id,
            attempt.actor_id,attempt.epoch,attempt.edit_action_key,attempt.version_fingerprint,
            attempt.packet_receipt_sha256,text_sha,bot,room,message,human,
            result['date'],attempt.thread_id)
        return ValidatedPromptReceipt(attempt.attempt_id,attempt.review_id,attempt.actor_id,
            attempt.epoch,attempt.edit_action_key,bot,room,message,human,sha,result['date'])
    except Exception:
        raise PromptReceiptError('prompt_receipt_unconfirmed') from None
