"""Offline exact-control markup plan/readback; no sender, DB, or live wiring.

Inputs must come from the courier's authenticated stored card/control receipt,
not a webhook or caller-supplied provider response. A valid plan/DTO is NOT
permission to edit a message. The future courier must persist its attempt before
I/O, recheck current owner state and never retry an uncertain edit automatically.
"""
from dataclasses import dataclass, astuple
import base64
import hashlib
import hmac
import json
import re
from uuid import UUID

from core.content_ops.review_cancellation import (
    CancellationSigner, cancellation_target, cancellation_button, _second,
)
from core.content_ops.review_edit_ingress import EditBindings, _UNSUPPORTED
from core.content_ops.review_ingress import _positive_int, _unique_object, _reject_constant
from core.content_ops.prompt_reservation import _time


class CancellationMarkupError(ValueError):
    pass


def require(ok):
    if not ok:
        raise CancellationMarkupError('cancellation_markup_unconfirmed')


def encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


@dataclass(frozen=True, repr=False)
class CancellationMarkupPlan:
    card_id: str
    bot_id: int
    chat_id: int
    message_id: int
    thread_id: int | None
    message_date: int
    text_sha256: str
    entities_json: str
    started_at: int
    expires_at: int
    request_json: str
    seal: str


def _seal(bindings, plan):
    return bindings.digest('cancellation-markup-plan@1', *astuple(plan)[:-1])


def prepare_cancellation_markup(*, enabled=False, review=None, card=None, bindings=None,
        signer=None, bot_id=None, chat_id=None, message_id=None, thread_id=None,
        control_text=None, control_entities=None, message_date=None, existing_markup=None,
        now=None, expires_at=None):
    """Append one cancel row; never mint/refresh/authorize other review actions.

    existing_markup is the owner's original, stored keyboard. Its other callback
    MACs are intentionally not reissued or considered authorized by this helper.
    Their existing action ingress must still verify them independently.
    """
    if enabled is not True:
        return None
    try:
        require(type(bindings) is EditBindings and type(signer) is CancellationSigner)
        _second(now); _second(expires_at)
        target = cancellation_target(review, card, bindings)
        require(card['active'] is True)
        require(_positive_int(bot_id) and _positive_int(message_id))
        require(type(chat_id) is int and -(2**52) < chat_id < 0)
        require(thread_id is None or _positive_int(thread_id))
        require((target.bot_binding, target.room_binding, target.message_binding, target.thread_id) ==
            (bindings.digest('bot', bot_id), bindings.digest('room', bot_id, chat_id),
             bindings.digest('card-message@2', bot_id, chat_id, message_id), thread_id))
        # Inputs here use provider-second resolution. The original precise
        # delivery timestamp remains pinned by parent@2, never rewritten.
        delivered_second = int(_time(card['delivered_at']).timestamp())
        require(type(message_date) is int and 0 < message_date <= delivered_second <= now)
        require(type(control_text) is str and control_text.strip()
                and len(control_text.encode('utf-16-le')) // 2 <= 4096)
        entities = [] if control_entities is None else control_entities
        require(type(entities) is list and len(entities) <= 100)
        for entity in entities:
            require(type(entity) is dict and set(entity) == {'type','offset','length'})
            require(entity['type'] in ('url','mention','hashtag','cashtag','bot_command',
                'email','phone_number','bold','italic','underline','strikethrough','spoiler',
                'code','blockquote','expandable_blockquote'))
            require(type(entity['offset']) is int and type(entity['length']) is int
                    and entity['offset'] >= 0 and entity['length'] > 0
                    and entity['offset'] + entity['length'] <= len(control_text.encode('utf-16-le')) // 2)
        require(type(existing_markup) is dict and set(existing_markup) == {'inline_keyboard'})
        rows = existing_markup['inline_keyboard']
        require(type(rows) is list and len(rows) <= 8)
        seen = set()
        for row in rows:
            require(type(row) is list and 1 <= len(row) <= 8)
            for b in row:
                require(type(b) is dict and set(b) == {'text', 'callback_data'})
                require(type(b['text']) is str and 0 < len(b['text']) <= 64)
                token = b['callback_data']
                require(type(token) is str and re.fullmatch(r'[A-Za-z0-9_-]{51}', token) is not None)
                raw = base64.urlsafe_b64decode(token + '=')
                require(base64.urlsafe_b64encode(raw).decode().rstrip('=') == token
                        and raw[0] == 1 and raw[21:22] in (b't',b'x',b'b',b'h',b's',b'c',b'a')
                        and str(UUID(bytes=raw[1:17])) == review['content_version_id']
                        and token not in seen)
                seen.add(token)
        # Deeply immutable serialized payload; mutating caller dictionaries later
        # cannot change the prepared edit or its expected response.
        new_rows = json.loads(encode(rows))
        new_rows.append([cancellation_button(target, signer, now=now, expires_at=expires_at)])
        request = encode({'chat_id': chat_id, 'message_id': message_id,
                          'reply_markup': {'inline_keyboard': new_rows}})
        values = (target.card_id, bot_id, chat_id, message_id, thread_id, message_date,
                  hashlib.sha256(control_text.encode()).hexdigest(), encode(entities), now, expires_at, request)
        plan = CancellationMarkupPlan(*values, '')
        return CancellationMarkupPlan(*values, _seal(bindings, plan))
    except Exception:
        raise CancellationMarkupError('cancellation_markup_unconfirmed') from None


def _check_plan(plan, bindings, now):
    require(type(plan) is CancellationMarkupPlan and type(bindings) is EditBindings)
    _second(now)
    require(type(plan.seal) is str and hmac.compare_digest(plan.seal, _seal(bindings, plan)))
    require(plan.started_at <= now < plan.expires_at <= plan.started_at + 1800)


def cancellation_markup_request(*, enabled=False, plan=None, bindings=None, now=None):
    """Sensitive request body for an authorized future courier, never log it."""
    if enabled is not True:
        return None
    try:
        _check_plan(plan, bindings, now)
        return {'method': 'editMessageReplyMarkup', 'body': json.loads(plan.request_json)}
    except Exception:
        raise CancellationMarkupError('cancellation_markup_unconfirmed') from None


def validate_cancellation_markup_response(*, enabled=False, plan=None, bindings=None,
        http_status=None, raw_response=None, observed_at=None):
    """Shape/identity match only; authenticated transport provenance is external.

    True, 400/not-modified, timeout and mismatches are unconfirmed, not success
    or permission to resend. No receipt is persisted and no callback is run.
    """
    if enabled is not True:
        return None
    try:
        _check_plan(plan, bindings, observed_at)
        require(type(http_status) is int and http_status == 200)
        require(type(raw_response) is bytes and 0 < len(raw_response) <= 32768)
        data = json.loads(raw_response.decode(), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
        require(type(data) is dict and set(data) == {'ok','result'} and data['ok'] is True)
        result = data['result']
        require(type(result) is dict and not ((_UNSUPPORTED - {'edit_date'}) & result.keys()))
        require(not ({'inline_message_id','reply_to_message','rich_message'} & result.keys()))
        require(encode(result.get('entities', [])) == plan.entities_json)
        chat, sender = result.get('chat'), result.get('from')
        require(type(chat) is dict and type(sender) is dict)
        require(type(chat.get('id')) is int and chat['id'] == plan.chat_id
                and chat.get('type') == 'supergroup' and not ({'username','linked_chat_id'} & chat.keys()))
        require(type(sender.get('id')) is int and sender['id'] == plan.bot_id and sender.get('is_bot') is True)
        require(type(result.get('message_id')) is int and result['message_id'] == plan.message_id)
        require(type(result.get('date')) is int and result['date'] == plan.message_date)
        require(result.get('message_thread_id') == plan.thread_id)
        if 'message_thread_id' in result:
            require(_positive_int(result['message_thread_id']))
        if 'edit_date' in result:
            require(type(result['edit_date']) is int and plan.started_at <= result['edit_date'] <= observed_at)
        text = result.get('text')
        require(type(text) is str and hashlib.sha256(text.encode()).hexdigest() == plan.text_sha256)
        require(encode(result.get('reply_markup')) == encode(json.loads(plan.request_json)['reply_markup']))
        return {'status': 'markup_response_matched', 'card_id': plan.card_id,
                'receipt_sha256': bindings.digest('cancellation-markup-response@1', plan.seal),
                'execution_authorized': False}
    except Exception:
        raise CancellationMarkupError('cancellation_markup_unconfirmed') from None
