"""Offline explicit-human confirmation adapter; default OFF, no transport.

The existing ingress owner must preserve its original authenticated received_at
across replay, and supply a trusted stored prompt receipt reader on the registrar
cursor. The reader MUST lock immutable target/receipt and active state until
commit. Never synthesize that receipt from the incoming callback. No production
reader, ingress route, receipt persistence, webhook or acknowledgment is wired.
"""
from dataclasses import astuple, dataclass
from datetime import datetime, timezone
import base64
import hmac
from uuid import UUID

from core.content_ops.cancellation_markup import encode
from core.content_ops.cancellation_markup_authority import MarkupExecutionApproval
from core.content_ops.prompt_receipt import canonical_uuid, digest
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import _parse_callback, _positive_int


class MarkupConfirmationError(ValueError):
    pass


def _require(ok):
    if not ok:
        raise MarkupConfirmationError('markup_confirmation_refused')


@dataclass(frozen=True, repr=False)
class MarkupConfirmationTarget:
    approval_id: str
    attempt_id: str
    card_id: str
    actor_id: str
    human_binding: str
    plan_seal: str
    original_receipt_sha256: str
    started_at: int
    expires_at: int


@dataclass(frozen=True, repr=False)
class StoredMarkupConfirmation:
    target: MarkupConfirmationTarget
    bot_id: int
    chat_id: int
    message_id: int
    thread_id: int | None
    message_date: int
    delivered_at: datetime
    active: bool


def _target(target):
    _require(type(target) is MarkupConfirmationTarget)
    _require(all(canonical_uuid(v) for v in astuple(target)[:4]))
    _require(all(digest(v) for v in astuple(target)[4:7]))
    _require(type(target.started_at) is int and type(target.expires_at) is int
             and 0 < target.started_at < target.expires_at < 2**32
             and target.expires_at - target.started_at <= 1800)


def _time(value):
    _require(type(value) is datetime and value.utcoffset() is not None
             and 0 < value.timestamp() < 2**32)
    return value.timestamp()


def markup_confirmation_payload(target, bindings):
    """Pure preparation only: returned text/keyboard are NOT a sent receipt.

    Version 3/action m is distinct from content-review and cancellation tokens.
    The existing owner binding key is domain-separated; no secret is loaded.
    """
    try:
        _target(target)
        _require(type(bindings) is EditBindings)
        body = (b'\x03' + UUID(target.approval_id).bytes
                + target.expires_at.to_bytes(4, 'big') + b'm')
        mac = bytes.fromhex(bindings.digest('markup-human-confirmation@1',
                                            *astuple(target)))[:16]
        token = base64.urlsafe_b64encode(body + mac).decode().rstrip('=')
        text = ('검수 메시지에 취소 버튼만 추가합니다.\n'
                '콘텐츠 승인·공개 게시·재전송은 하지 않습니다.\n'
                f'대상: {target.card_id}\n확인: {target.approval_id}\n'
                f'실행 계획: {target.plan_seal}\n'
                f'만료(UTC): {datetime.fromtimestamp(target.expires_at, timezone.utc).isoformat()}')
        return {'text': text, 'reply_markup': {'inline_keyboard': [[{
            'text': '취소 버튼 추가만 확인', 'callback_data': token}]]}}
    except Exception:
        raise MarkupConfirmationError('markup_confirmation_refused') from None


class TelegramMarkupConfirmation:
    """Request-scoped decision_authenticator for PostgresMarkupApprovalOwner.

    Auth is checked before THIS adapter accesses the cursor/reader. The calling
    registrar may already hold DB locks. Fresh DB time brackets the locked owner
    read; the original ingress timestamp is never refreshed on retry. A delayed
    (>5s), future or expired request fails closed. This callable never commits,
    executes the edit, acknowledges Telegram or authorizes content publication.
    """
    def __init__(self, *, raw_body=None, headers=(), policy=None, bindings=None,
                 received_at=None, receipt_reader=None, enabled=False):
        self._raw = raw_body
        try:
            self._headers = tuple(tuple(p) for p in headers) if type(headers) in (list, tuple) else None
        except Exception:
            self._headers = None
        self._policy, self._bindings = policy, bindings
        self._received_at, self._reader = received_at, receipt_reader
        self._enabled = enabled

    def __call__(self, *, cursor=None, approval_id=None):
        if self._enabled is not True:
            return None
        try:
            received = _time(self._received_at)
            query, actor = _parse_callback(self._raw, self._headers, self._policy, int(received))
            _require(canonical_uuid(approval_id) and type(self._bindings) is EditBindings
                     and cursor is not None and callable(self._reader))
            # Reject other button domains and mismatched lookup IDs before the
            # stored-receipt read. The complete target MAC is checked below.
            decoded = base64.urlsafe_b64decode(query['data'] + '=')
            _require(len(decoded) == 38 and decoded[:1] == b'\x03'
                     and decoded[1:17] == UUID(approval_id).bytes and decoded[21:22] == b'm'
                     and received < int.from_bytes(decoded[17:21], 'big')
                     and base64.urlsafe_b64encode(decoded).decode().rstrip('=') == query['data'])
            before = self._clock(cursor, received)
            receipt = self._reader(cursor=cursor, approval_id=approval_id)
            _require(type(receipt) is StoredMarkupConfirmation and receipt.active is True)
            target = receipt.target
            _target(target)
            _require(target.approval_id == approval_id and target.actor_id == actor
                     and target.human_binding == self._bindings.digest(
                         'human', self._policy.bot_id, query['from']['id']))
            _require(target.started_at <= received <= before < target.expires_at)
            payload = markup_confirmation_payload(target, self._bindings)
            _require(hmac.compare_digest(query['data'],
                     payload['reply_markup']['inline_keyboard'][0][0]['callback_data']))
            message = query['message']
            _require(set(message) <= {'message_id', 'date', 'chat', 'from', 'text',
                     'entities', 'reply_markup', 'message_thread_id', 'is_topic_message'})
            _require(type(receipt.bot_id) is int and receipt.bot_id == self._policy.bot_id
                     and type(receipt.chat_id) is int and receipt.chat_id == self._policy.chat_id
                     and _positive_int(receipt.message_id) and receipt.message_id == message['message_id'])
            _require(type(receipt.message_date) is int and receipt.message_date == message['date']
                     and target.started_at <= receipt.message_date <= _time(receipt.delivered_at) <= received)
            _require(receipt.thread_id is None or _positive_int(receipt.thread_id))
            if receipt.thread_id is None:
                _require('message_thread_id' not in message and 'is_topic_message' not in message)
            else:
                _require(type(message.get('message_thread_id')) is int
                         and message['message_thread_id'] == receipt.thread_id
                         and message.get('is_topic_message') is True)
            _require(message.get('text') == payload['text']
                     and message.get('entities', []) == []
                     and encode(message.get('reply_markup')) == encode(payload['reply_markup']))
            after = self._clock(cursor, received)
            _require(before <= after < target.expires_at)
            return MarkupExecutionApproval(target.approval_id, target.attempt_id,
                target.card_id, target.actor_id, target.human_binding, target.plan_seal,
                target.original_receipt_sha256, 'append_cancellation_markup@1',
                self._received_at, datetime.fromtimestamp(target.expires_at, timezone.utc), True)
        except Exception:
            raise MarkupConfirmationError('markup_confirmation_refused') from None

    @staticmethod
    def _clock(cursor, received):
        cursor.execute('select clock_timestamp()')
        now = _time(cursor.fetchone()[0])
        _require(received <= now <= received + 5)
        return now
