"""Dedicated owner/relay wire authentication, not a human approval credential.

Keys and release identity must be explicitly injected; no discovery or fallback.
The HMAC proves dedicated key possession, not independent owner attestation.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import re
from uuid import UUID

from core.content_ops.cancellation_markup import encode
from core.content_ops.cancellation_markup_confirmation import _time
from core.content_ops.prompt_receipt import canonical_uuid
from core.content_ops.review_ingress import _unique_object, _reject_constant

PATH = '/internal/content-ops/confirmation-dispatch'
MAX_BODY = 8192
SHA = re.compile(r'[a-f0-9]{40}\Z')
TOKEN = re.compile(r'[A-Za-z0-9_-]{32,256}\Z')
PREFIX = '검수 메시지에 취소 버튼만 추가합니다.\n콘텐츠 승인·공개 게시·재전송은 하지 않습니다.\n'


class ConfirmationBridgeError(ValueError):
    pass


def require(value):
    if not value:
        raise ConfirmationBridgeError('confirmation_bridge_unknown')


def parse(raw):
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


@dataclass(frozen=True, repr=False)
class DispatchEnvelope:
    delivery_id: str
    permission_id: str
    card_id: str
    bot_id: int
    request: bytes
    release_sha: str
    issued_at: int
    expires_at: int

    @property
    def request_sha256(self):
        return hashlib.sha256(self.request).hexdigest()


def validate(value, now):
    require(type(value) is DispatchEnvelope and all(canonical_uuid(x) for x in
        (value.delivery_id, value.permission_id, value.card_id)))
    require(type(value.bot_id) is int and 0 < value.bot_id < 2**53
        and type(value.release_sha) is str and SHA.fullmatch(value.release_sha)
        and type(value.issued_at) is int and type(value.expires_at) is int
        and 0 < value.issued_at <= _time(now) < value.expires_at < 2**32
        and value.expires_at-value.issued_at <= 15
        and type(value.request) is bytes and 0 < len(value.request) <= 4096)
    data = parse(value.request)
    require(type(data) is dict and set(data)=={'method','body'} and data['method']=='sendMessage'
        and encode(data).encode()==value.request)
    body = data['body']
    require(type(body) is dict and set(body) in (
        {'chat_id','text','reply_markup'}, {'chat_id','text','reply_markup','message_thread_id'})
        and type(body['chat_id']) is int and -2**53 < body['chat_id'] < 0
        and type(body['text']) is str and len(body['text']) <= 2048
        and body['text'].startswith(PREFIX + '대상: ' + value.card_id + '\n확인: '))
    if 'message_thread_id' in body:
        require(type(body['message_thread_id']) is int and 0 < body['message_thread_id'] < 2**53)
    markup = body['reply_markup']
    require(type(markup) is dict and set(markup)=={'inline_keyboard'})
    rows = markup['inline_keyboard']
    require(type(rows) is list and len(rows)==1 and type(rows[0]) is list and len(rows[0])==1)
    button=rows[0][0]
    require(type(button) is dict and set(button)=={'text','callback_data'}
        and button['text']=='취소 버튼 추가만 확인'
        and type(button['callback_data']) is str
        and re.fullmatch(r'[A-Za-z0-9_-]{51}',button['callback_data']))
    callback = base64.urlsafe_b64decode(button['callback_data']+'=')
    require(callback[0]==3 and callback[21:22]==b'm'
        and value.expires_at <= int.from_bytes(callback[17:21],'big'))
    lines=body['text'].split('\n')
    require(len(lines)==6 and lines[3]=='확인: '+str(UUID(bytes=callback[1:17]))
        and re.fullmatch(r'실행 계획: [a-f0-9]{64}',lines[4])
        and lines[5]=='만료(UTC): '+datetime.fromtimestamp(int.from_bytes(callback[17:21],'big'),timezone.utc).isoformat()
        and base64.urlsafe_b64encode(callback).decode().rstrip('=')==button['callback_data'])
    return body


class DispatchAuthenticator:
    def __init__(self, key):
        require(type(key) is bytes and 32 <= len(key) <= 256)
        self._key = key

    def encode(self, *, envelope, now):
        try:
            validate(envelope, now)
            e=envelope
            payload=dict(schema='confirmation-dispatch@1',delivery_id=e.delivery_id,
                permission_id=e.permission_id,card_id=e.card_id,bot_id=e.bot_id,
                request_b64=base64.b64encode(e.request).decode('ascii'),
                request_sha256=e.request_sha256,release_sha=e.release_sha,
                issued_at=e.issued_at,expires_at=e.expires_at)
            mac=hmac.new(self._key,b'confirmation-dispatch@1\0'+encode(payload).encode(),hashlib.sha256).hexdigest()
            raw=encode(dict(payload,seal=mac)).encode()
            require(len(raw)<=MAX_BODY)
            return raw
        except Exception:
            raise ConfirmationBridgeError('confirmation_bridge_unknown') from None

    def decode(self, *, raw, now, release_sha):
        try:
            require(type(raw) is bytes and 0 < len(raw)<=MAX_BODY)
            p=parse(raw)
            require(type(p) is dict and set(p)=={'schema','delivery_id','permission_id','card_id',
                'bot_id','request_b64','request_sha256','release_sha','issued_at','expires_at','seal'}
                and p['schema']=='confirmation-dispatch@1' and p['release_sha']==release_sha
                and type(p['request_b64']) is str and type(p['seal']) is str)
            e=DispatchEnvelope(p['delivery_id'],p['permission_id'],p['card_id'],p['bot_id'],
                base64.b64decode(p['request_b64'],validate=True),p['release_sha'],p['issued_at'],p['expires_at'])
            require(hmac.compare_digest(self.encode(envelope=e,now=now),raw))
            return e
        except Exception:
            raise ConfirmationBridgeError('confirmation_bridge_unknown') from None
