"""Opt-in observation of the existing relay's original HTTP response.

This is NOT a sender, reservation or authorization service. The existing owner
must independently authorize the private prompt and durably consume a fresh
delivery before using the relay. No production runner installs this observer.
The local one-use latch is defense in depth, NOT cross-process deduplication.

The injected async sink belongs to the scoped gateway boundary, never a DB
connection in the relay. It must persist this exact envelope without exposing
raw responses in status/errors. No live sink, keys or endpoint is provisioned.
"""
import hashlib
import inspect

import httpx

from core.content_ops.cancellation_markup import encode
from core.content_ops.cancellation_markup_owner import _inputs
from core.content_ops.cancellation_markup_confirmation import (
    MarkupConfirmationTarget, _target, _time,
)
from core.content_ops.confirmation_delivery_owner import (
    StoredConfirmationSendReceipt, confirmation_send_request,
)
from core.content_ops.confirmation_response_store import ConfirmationResponseAuthenticator
from core.content_ops.prompt_receipt import canonical_uuid


class ConfirmationCaptureError(ValueError):
    pass


def require(ok):
    if not ok:
        raise ConfirmationCaptureError('confirmation_capture_unconfirmed')


class ConfirmationResponseCapture:
    """One exact request, bounded original bytes, one sink call, no retry.

    A sink acknowledgement means source evidence was recorded, not a matched
    delivery, human confirmation or permission to publish. A failure after
    begin permanently consumes this instance, including task cancellation.
    """
    def __init__(self, *, enabled=False, target=None, delivery_id=None,
                 authenticator=None, sink=None, clock=None, **identity):
        self.enabled = enabled is True
        self._target, self._delivery_id = target, delivery_id
        self._auth, self._sink, self._clock = authenticator, sink, clock
        self._identity = dict(identity)
        self._consumed = False
        self._collecting = False
        self.ack = None

    def begin(self, *, settings, method, body, verified):
        if not self.enabled:
            return
        try:
            require(not self._consumed and verified is True)
            _inputs(**self._identity)
            _target(self._target)
            i, t = self._identity, self._target
            plan, bindings = i['plan'], i['bindings']
            require(t == MarkupConfirmationTarget(t.approval_id, i['attempt_id'],
                plan.card_id, i['actor_id'], bindings.digest('human', plan.bot_id, i['human_id']),
                plan.seal, t.original_receipt_sha256, plan.started_at, plan.expires_at))
            require(canonical_uuid(self._delivery_id)
                and type(self._auth) is ConfirmationResponseAuthenticator
                and inspect.iscoroutinefunction(self._sink) and callable(self._clock))
            require(plan.bot_id == int(settings.relay_bot_token.split(':', 1)[0])
                and plan.chat_id == int(settings.relay_chat_id))
            request = confirmation_send_request(t, plan, bindings)
            require(encode({'method':method, 'body':body}).encode() == request)
            started = self._clock()
            require(t.started_at <= _time(started) < t.expires_at)
            self._request_hash = hashlib.sha256(request).hexdigest()
            self._started = started
            # No await between check and consume; same-instance races cannot
            # make a second request. Durable owner enforcement is still needed.
            self._consumed = True
        except Exception:
            raise ConfirmationCaptureError('confirmation_capture_unconfirmed') from None

    async def collect(self, response):
        if not self.enabled:
            return response
        try:
            require(self._consumed and not self._collecting)
            self._collecting = True  # Also fence a failed/cancelled sink.
            require(isinstance(response, httpx.Response))
            # The relay asks for identity encoding. Reject compressed responses
            # instead of silently sealing decompressed/reconstructed bytes.
            require(response.headers.get('content-encoding', 'identity').lower() == 'identity')
            length = response.headers.get('content-length')
            if length is not None:
                require(length.isascii() and length.isdecimal() and 0 < int(length) <= 32768)
            data = bytearray()
            async for chunk in response.aiter_bytes():
                require(len(data) + len(chunk) <= 32768)
                data.extend(chunk)
            require(0 < len(data) <= 32768 and (length is None or int(length) == len(data)))
            observed = self._clock()  # Original full-response time, before sink.
            require(_time(self._started) <= _time(observed) < self._target.expires_at)
            receipt = StoredConfirmationSendReceipt(self._delivery_id, self._request_hash,
                response.status_code, bytes(data), observed)
            envelope = self._auth.seal(enabled=True, receipt=receipt)
            ack = await self._sink(envelope=envelope)
            require(type(ack) is dict
                and set(ack) == {'status', 'reused', 'execution_authorized'}
                and ack['status'] == 'confirmation_source_recorded'
                and type(ack['reused']) is bool and ack['execution_authorized'] is False)
            self.ack = dict(ack)
            # Existing caller parsing sees the identical bytes; it must still
            # validate provider success, destination and exact message itself.
            return httpx.Response(response.status_code, content=receipt.raw_response,
                headers=response.headers, request=response.request)
        except Exception:
            raise ConfirmationCaptureError('confirmation_capture_unconfirmed') from None


class DispatchResponseCapture(ConfirmationResponseCapture):
    """Capture only the authenticated exact dispatch; no owner binding secrets.

    The relay bridge consumes its persistent replay ledger BEFORE using this.
    This local latch alone is not durable replay protection.
    """
    def __init__(self, *, enabled=False, raw=None, dispatch_auth=None, release_sha=None,
                 authenticator=None, sink=None, clock=None):
        super().__init__(enabled=enabled,authenticator=authenticator,sink=sink,clock=clock)
        self._raw,self._dispatch_auth,self._sha = raw,dispatch_auth,release_sha

    def begin(self, *, settings, method, body, verified):
        if not self.enabled:
            return
        try:
            from core.content_ops.confirmation_dispatch_protocol import DispatchAuthenticator, parse
            require(not self._consumed and verified is True
                and type(self._dispatch_auth) is DispatchAuthenticator
                and type(self._auth) is ConfirmationResponseAuthenticator
                and inspect.iscoroutinefunction(self._sink) and callable(self._clock))
            started=self._clock()
            envelope=self._dispatch_auth.decode(raw=self._raw,now=started,release_sha=self._sha)
            require(settings.release_sha==self._sha
                and envelope.bot_id==int(settings.relay_bot_token.split(':',1)[0])
                and parse(envelope.request)['body']['chat_id']==int(settings.relay_chat_id)
                and encode({'method':method,'body':body}).encode()==envelope.request)
            self._target,self._delivery_id = envelope,envelope.delivery_id
            self._request_hash,self._started = envelope.request_sha256,started
            self._consumed = True
        except Exception:
            raise ConfirmationCaptureError('confirmation_capture_unconfirmed') from None
