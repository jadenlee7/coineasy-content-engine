"""Unmounted, default-OFF cancellation boundary. No transport or credentials.

Card revocation is one-way and intrinsically idempotent; callback IDs are not
delivery receipts. A fresh transaction and current reviewer authorization are
required even for repeats. Never retry an uncertain commit automatically.
"""
from dataclasses import dataclass, astuple
from datetime import datetime
import base64
import hashlib
import hmac
import json
import re
from uuid import UUID

from core.content_ops.prompt_receipt import canonical_uuid, digest
from core.content_ops.prompt_reservation import card_parent_binding, _time
from core.content_ops.review_edit_ingress import EditBindings, _UNSUPPORTED
from core.content_ops.review_ingress import ReviewIngressError, _parse_callback, _positive_int


class CancellationError(ReviewIngressError):
    """Only fixed, sanitized errors cross this boundary."""


def _require(ok):
    if not ok:
        raise CancellationError('review_cancellation_invalid')


@dataclass(frozen=True, repr=False)
class CancellationTarget:
    card_id: str
    review_id: str
    version_fingerprint: str
    epoch: int
    bot_binding: str
    room_binding: str
    message_binding: str
    thread_id: int | None
    parent_binding_sha256: str


@dataclass(frozen=True, repr=False)
class VerifiedCancellation:
    card_id: str
    actor_id: str
    bot_binding: str
    room_binding: str
    message_binding: str
    human_binding: str
    thread_id: int | None
    token: str


def _target_valid(target):
    _require(type(target) is CancellationTarget)
    _require(canonical_uuid(target.card_id) and canonical_uuid(target.review_id))
    _require(type(target.epoch) is int and 0 <= target.epoch < 2**63)
    _require(all(digest(v) for v in (target.version_fingerprint, target.bot_binding,
        target.room_binding, target.message_binding, target.parent_binding_sha256)))
    _require(target.thread_id is None or _positive_int(target.thread_id))


def cancellation_target(review, card, bindings):
    """Use stored original review/card lineage, including historical epochs."""
    try:
        parent = card_parent_binding(bindings, review, card)
        b = card['bindings']
        _require(type(card['active']) is bool and hmac.compare_digest(parent, b['parent_binding']))
        target = CancellationTarget(card['id'], review['id'], card['version_fingerprint'],
            card['epoch'], b['bot'], b['room'], b['message'], b['thread_id'], parent)
        _target_valid(target)
        return target
    except Exception:
        raise CancellationError('review_cancellation_invalid') from None


def _second(now):
    _require(type(now) is int and 0 < now < 2**32)
    return now


def _db_time(value):
    _require(type(value) is datetime and value.utcoffset() is not None)
    return _time(value)


def _envelope(token, now):
    _second(now)
    _require(type(token) is str and re.fullmatch(r'[A-Za-z0-9_-]{51}', token) is not None)
    raw = base64.urlsafe_b64decode(token + '=')
    _require(len(raw) == 38 and base64.urlsafe_b64encode(raw).decode().rstrip('=') == token)
    _require(raw[0] == 2 and raw[21:22] == b'r')
    card_id, expiry = str(UUID(bytes=raw[1:17])), int.from_bytes(raw[17:21], 'big')
    _require(canonical_uuid(card_id) and now < expiry <= now + 1800)
    return card_id, raw


class CancellationSigner:
    def __init__(self, key):
        _require(type(key) is bytes and len(key) >= 32)
        self._key = key

    def _mac(self, body, target):
        _target_valid(target)
        payload = json.dumps(['card-revocation@1', *astuple(target)],
                             separators=(',', ':'), ensure_ascii=True).encode()
        return hmac.new(self._key, body + b'\x00' + payload, hashlib.sha256).digest()[:16]

    def issue(self, target, *, now, expires_at):
        _target_valid(target)
        _second(now)
        _second(expires_at)
        _require(now < expires_at <= now + 1800)
        body = b'\x02' + UUID(target.card_id).bytes + expires_at.to_bytes(4, 'big') + b'r'
        return base64.urlsafe_b64encode(body + self._mac(body, target)).decode().rstrip('=')

    def verify(self, token, target, *, now):
        card_id, raw = _envelope(token, now)
        _target_valid(target)
        _require(card_id == target.card_id and hmac.compare_digest(raw[22:], self._mac(raw[:22], target)))
        return card_id


def cancellation_button(target, signer, *, now, expires_at):
    """Request-shaped fragment only, not an installed or delivered button."""
    _require(type(signer) is CancellationSigner)
    return {'text': '⛔ 이 검수 취소',
            'callback_data': signer.issue(target, now=now, expires_at=expires_at)}


def _result(result, card_id):
    _require(type(result) is dict and set(result) == {
        'status', 'card_id', 'reused', 'execution_authorized'})
    _require(result['status'] == 'card_revoked' and result['card_id'] == card_id
             and type(result['reused']) is bool and result['execution_authorized'] is False)
    return dict(result)


def handle_cancellation_webhook(*, enabled=False, raw_body=None, headers=(), policy=None,
                                bindings=None, signer=None, owner=None, now=None):
    if enabled is not True:
        return {'status': 'disabled', 'public_send_attempted': False}
    try:
        query, actor_id = _parse_callback(raw_body, headers, policy, now)
        _require(type(bindings) is EditBindings and type(signer) is CancellationSigner)
        _require(canonical_uuid(actor_id))
        message = query['message']
        topic = message.get('message_thread_id')
        _require(topic is None or _positive_int(topic))
        # Installing reply markup can mark the original control message edited.
        # This timestamp is not authority: exact DB message/target/MAC checks
        # remain mandatory. Do not relax the separate text-edit ingress.
        _require(not ((_UNSUPPORTED - {'edit_date'}) & message.keys()))
        if 'edit_date' in message:
            _require(type(message['edit_date']) is int
                     and message['date'] <= message['edit_date'] <= now)
        card_id, _ = _envelope(query['data'], now)
        event = VerifiedCancellation(card_id, actor_id, bindings.digest('bot', policy.bot_id),
            bindings.digest('room', policy.bot_id, policy.chat_id),
            bindings.digest('card-message@2', policy.bot_id, policy.chat_id, message['message_id']),
            bindings.digest('human', policy.bot_id, query['from']['id']), topic, query['data'])
    except Exception:
        raise CancellationError('review_cancellation_invalid') from None
    try:
        return _result(owner.cancel_card(event, enabled=True, signer=signer,
                                        bindings=bindings, now=now), card_id)
    except Exception:
        raise CancellationError('review_cancellation_outcome_unknown') from None


class PostgresCardCancellationOwner:
    """Injected NEW non-autocommit connection; not wired to any runtime role.

    Item -> original review -> card order matches the private RPC. Verify the
    token with DB time both before and after RPC identity/permission lock waits.
    Result checks remain inside the transaction, including the final clock.
    """
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def cancel_card(self, event=None, *, enabled=False, signer=None, bindings=None, now=None):
        if enabled is not True:
            return {'status': 'disabled', 'public_send_attempted': False}
        try:
            _require(type(event) is VerifiedCancellation and type(signer) is CancellationSigner
                     and type(bindings) is EditBindings)
            _require(canonical_uuid(event.card_id) and canonical_uuid(event.actor_id))
            _require(all(digest(v) for v in (event.bot_binding, event.room_binding,
                                          event.message_binding, event.human_binding)))
            _require(event.thread_id is None or _positive_int(event.thread_id))
            _require(_envelope(event.token, now)[0] == event.card_id)
            with self._connection_factory() as connection:
                _require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    cursor.execute('''
                        select i.id from public.content_items i
                        join private.content_ops_button_reviews r
                          on r.content_item_id=i.id and r.workspace_id=i.workspace_id
                        join private.content_ops_button_cards c on c.review_id=r.id
                        where c.id=%s::uuid and c.bindings->>'bot'=%s
                          and c.bindings->>'room'=%s and c.bindings->>'message'=%s
                        for update of i
                    ''', (event.card_id, event.bot_binding, event.room_binding, event.message_binding))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and canonical_uuid(str(row[0])))
                    item_id = str(row[0])
                    cursor.execute('''
                        select to_jsonb(r) from private.content_ops_button_reviews r
                        join private.content_ops_button_cards c on c.review_id=r.id
                        join public.content_items i on i.id=r.content_item_id and i.workspace_id=r.workspace_id
                        where c.id=%s::uuid and r.content_item_id=%s::uuid for update of r
                    ''', (event.card_id, item_id))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and type(row[0]) is dict)
                    review = row[0]
                    _require(review['content_item_id'] == item_id and canonical_uuid(review['id']))
                    cursor.execute('''select to_jsonb(c) from private.content_ops_button_cards c
                        where c.id=%s::uuid and c.review_id=%s::uuid for update of c''',
                        (event.card_id, review['id']))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1 and type(row[0]) is dict)
                    target = cancellation_target(review, row[0], bindings)
                    _require((target.card_id, target.bot_binding, target.room_binding,
                              target.message_binding, target.thread_id) ==
                             (event.card_id, event.bot_binding, event.room_binding,
                              event.message_binding, event.thread_id))
                    cursor.execute('select clock_timestamp()')
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 1)
                    before = _db_time(row[0])
                    signer.verify(event.token, target, now=int(before.timestamp()))
                    cursor.execute('''with revoked as materialized (
                        select private.revoke_content_ops_button_card(
                            %s::uuid,%s::uuid,%s,%s::uuid,%s,%s) as result
                        ) select result,clock_timestamp() from revoked''',
                        (target.card_id, target.review_id, target.version_fingerprint,
                         event.actor_id, event.bot_binding, event.human_binding))
                    row = cursor.fetchone()
                    _require(row is not None and len(row) == 2)
                    result = _result(row[0], event.card_id)
                    after = _db_time(row[1])
                    _require(after >= before)
                    signer.verify(event.token, target, now=int(after.timestamp()))
            return result
        except Exception:
            raise CancellationError('review_cancellation_outcome_unknown') from None
