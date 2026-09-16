"""Unmounted, default-OFF private response-evidence gateway and relay sink.

No environment/secret discovery, route registration, send authority or provider
call. The relay has only the dedicated gateway token and response-sealing key;
only the owner-side app receives the existing source-store dependency. HMAC is
relay key possession, not independent provider attestation or human approval.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import re
from urllib.parse import urlsplit

import httpx

from core.content_ops.cancellation_markup import encode
from core.content_ops.confirmation_delivery_owner import StoredConfirmationSendReceipt
from core.content_ops.confirmation_response_store import (
    ConfirmationResponseAuthenticator, PostgresConfirmationSourceStore,
    SealedConfirmationResponse, _receipt,
)
from core.content_ops.prompt_receipt import digest
from core.content_ops.review_ingress import _unique_object, _reject_constant


PATH = '/internal/content-ops/confirmation-source'
SCHEMA = 'confirmation-source@1'
MAX_BODY = 49152
TIMEOUT_SECONDS = 20
_SHA = re.compile(r'[a-f0-9]{40}\Z')
_TOKEN = re.compile(r'[A-Za-z0-9_-]{32,256}\Z')


class ConfirmationGatewayError(ValueError):
    pass


def require(value):
    if not value:
        raise ConfirmationGatewayError('confirmation_gateway_unconfirmed')


def _config(token, release_sha, runtime_sha, authenticator):
    require(type(token) is str and _TOKEN.fullmatch(token)
        and type(release_sha) is str and _SHA.fullmatch(release_sha)
        and type(runtime_sha) is str and hmac.compare_digest(release_sha, runtime_sha)
        and type(authenticator) is ConfirmationResponseAuthenticator)


def encode_envelope(envelope, release_sha):
    """Bounded canonical wire bytes; encoding never signs caller data."""
    try:
        require(type(release_sha) is str and _SHA.fullmatch(release_sha)
            and type(envelope) is SealedConfirmationResponse and digest(envelope.relay_seal))
        r = envelope.receipt
        _receipt(r)
        value = dict(schema=SCHEMA, release_sha=release_sha, delivery_id=r.delivery_id,
            request_sha256=r.request_sha256, http_status=r.http_status,
            raw_response_b64=base64.b64encode(r.raw_response).decode('ascii'),
            observed_at=r.observed_at.astimezone(timezone.utc).isoformat(timespec='microseconds'),
            relay_seal=envelope.relay_seal)
        raw = encode(value).encode('ascii')
        require(len(raw) <= MAX_BODY)
        return raw
    except Exception:
        raise ConfirmationGatewayError('confirmation_gateway_unconfirmed') from None


def decode_envelope(raw, release_sha, authenticator):
    try:
        require(type(raw) is bytes and 0 < len(raw) <= MAX_BODY
            and type(authenticator) is ConfirmationResponseAuthenticator)
        value = json.loads(raw.decode('ascii'), object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
        require(type(value) is dict and set(value) == {'schema', 'release_sha', 'delivery_id',
            'request_sha256', 'http_status', 'raw_response_b64', 'observed_at', 'relay_seal'}
            and value['schema'] == SCHEMA and value['release_sha'] == release_sha
            and type(value['raw_response_b64']) is str and type(value['observed_at']) is str)
        receipt = StoredConfirmationSendReceipt(value['delivery_id'], value['request_sha256'],
            value['http_status'], base64.b64decode(value['raw_response_b64'], validate=True),
            datetime.fromisoformat(value['observed_at']))
        envelope = SealedConfirmationResponse(receipt, value['relay_seal'])
        # Canonical comparison rejects extra whitespace, aliases and padded or
        # noncanonical base64/time spellings as well as duplicate JSON keys.
        require(encode_envelope(envelope, release_sha) == raw)
        authenticator.verify(envelope)  # Before any store/DB access.
        return envelope
    except Exception:
        raise ConfirmationGatewayError('confirmation_gateway_unconfirmed') from None


def _ack(value):
    require(type(value) is dict and set(value) == {'status', 'reused', 'execution_authorized'}
        and value['status'] == 'confirmation_source_recorded'
        and type(value['reused']) is bool and value['execution_authorized'] is False)
    return value


class ConfirmationSourceGateway:
    """ASGI HTTP app, intentionally not mounted in api/server.py.

    Only persists authenticated source evidence against an existing reservation.
    A cancelled/timed-out DB thread may commit: its outcome remains unknown and
    MUST be read-only reconciled. This app never retries or sends Telegram.
    """
    def __init__(self, *, enabled=False, token=None, release_sha=None, runtime_sha=None,
                 authenticator=None, store=None):
        self._enabled = enabled is True
        self._token, self._sha, self._runtime = token, release_sha, runtime_sha
        self._auth, self._store = authenticator, store

    async def __call__(self, scope, receive, send):
        status, result = 503, dict(status='confirmation_gateway_unavailable', execution_authorized=False)
        if scope.get('type') != 'http':
            return
        if self._enabled:
            try:
                _config(self._token, self._sha, self._runtime, self._auth)
                require(type(self._store) is PostgresConfirmationSourceStore)
                status = 400
                require(scope.get('path') == PATH and scope.get('method') == 'POST'
                    and not scope.get('query_string', b''))
                headers = {}
                for key, value in scope.get('headers', []):
                    key = key.lower()
                    require(key not in headers)
                    headers[key] = value
                status = 401
                provided = headers.get(b'authorization', b'')
                require(type(provided) is bytes and len(provided) <= 263
                    and hmac.compare_digest(provided,
                    ('Bearer ' + self._token).encode('ascii')))
                status = 409
                require(headers.get(b'x-content-ops-release-sha') == self._sha.encode('ascii'))
                status = 400
                require(headers.get(b'content-type') == b'application/json'
                    and headers.get(b'content-encoding', b'identity') == b'identity'
                    and b'cookie' not in headers)
                length = headers.get(b'content-length')
                if length is not None:
                    require(length.isdigit() and 0 < int(length) <= MAX_BODY)
                async with asyncio.timeout(TIMEOUT_SECONDS):
                    raw = bytearray()
                    while True:
                        event = await receive()
                        require(event.get('type') == 'http.request'
                            and type(event.get('body', b'')) is bytes)
                        chunk = event.get('body', b'')
                        require(len(raw) + len(chunk) <= MAX_BODY)
                        raw.extend(chunk)
                        if not event.get('more_body', False):
                            break
                    require(length is None or int(length) == len(raw))
                    envelope = decode_envelope(bytes(raw), self._sha, self._auth)
                    status = 503
                    ack = _ack(await asyncio.to_thread(self._store.record, enabled=True, envelope=envelope))
                    result = dict(ack, release_sha=self._sha,
                        envelope_sha256=hashlib.sha256(raw).hexdigest())
                    status = 200
            except Exception:
                result = dict(status='confirmation_gateway_unconfirmed', execution_authorized=False)
        await send(dict(type='http.response.start', status=status, headers=[
            (b'content-type', b'application/json'), (b'cache-control', b'no-store')]))
        await send(dict(type='http.response.body', body=encode(result).encode('ascii')))


class ConfirmationGatewaySink:
    """Dedicated async sink for ConfirmationResponseCapture; one HTTP attempt.

    origin is trusted deployment configuration, never a request/body parameter.
    No production origin, token or key is supplied by this module. Disabled
    sinks return None, which an enabled capture deliberately treats as unknown.
    """
    def __init__(self, *, enabled=False, origin=None, token=None, release_sha=None,
                 runtime_sha=None, authenticator=None, transport=None):
        self._enabled, self._origin = enabled is True, origin
        self._token, self._sha, self._runtime = token, release_sha, runtime_sha
        self._auth, self._transport = authenticator, transport

    def validate_configuration(self):
        """Pure configuration preflight: no request, secret discovery or I/O."""
        if not self._enabled: return False
        try:
            _config(self._token, self._sha, self._runtime, self._auth)
            require(type(self._origin) is str)
            url = urlsplit(self._origin)
            require(url.scheme == 'https' and url.hostname and '.' in url.hostname
                and url.netloc == url.hostname and not (url.path or url.query or url.fragment)
                and re.fullmatch(r'[a-z0-9.-]+', url.hostname))
            try:
                ipaddress.ip_address(url.hostname)
            except ValueError:
                pass
            else:
                raise ConfirmationGatewayError('confirmation_gateway_unconfirmed')
            return True
        except Exception:
            raise ConfirmationGatewayError('confirmation_gateway_unconfirmed') from None

    async def record(self, *, envelope=None):
        if not self._enabled:
            return None
        try:
            require(self.validate_configuration() is True)
            self._auth.verify(envelope)
            raw = encode_envelope(envelope, self._sha)
            async with asyncio.timeout(TIMEOUT_SECONDS):
                async with httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_SECONDS,
                        follow_redirects=False, trust_env=False) as client:
                    async with client.stream('POST', self._origin + PATH, content=raw, headers={
                        'Authorization':'Bearer ' + self._token, 'Content-Type':'application/json',
                        'X-Content-Ops-Release-Sha':self._sha, 'Accept-Encoding':'identity',
                    }) as response:
                        require(response.status_code == 200
                            and response.headers.get('content-type') == 'application/json'
                            and response.headers.get('content-encoding', 'identity') == 'identity')
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            require(len(data) + len(chunk) <= 2048)
                            data.extend(chunk)
                        value = json.loads(data.decode('ascii'), object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
                        require(type(value) is dict and set(value) == {'status', 'reused',
                            'execution_authorized', 'release_sha', 'envelope_sha256'}
                            and value['release_sha'] == self._sha
                            and value['envelope_sha256'] == hashlib.sha256(raw).hexdigest())
                        return dict(_ack({k:value[k] for k in ('status', 'reused', 'execution_authorized')}))
        except Exception:
            raise ConfirmationGatewayError('confirmation_gateway_unconfirmed') from None
