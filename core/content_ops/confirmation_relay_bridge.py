"""Unmounted/default-OFF endpoint for the EXISTING exclusive private relay.

No new poller/webhook, credentials, routes or deployment are enabled. Only this
handoff may use a separately provisioned, persistent single-volume ledger.
Owner DB/admin credentials are neither accepted nor needed in this process.
"""
import asyncio
import hashlib
import hmac

from core.content_ops.cancellation_markup import encode
from core.content_ops.confirmation_dispatch_protocol import (
    PATH,MAX_BODY,SHA,TOKEN,DispatchAuthenticator,require,parse,
)
from core.content_ops.confirmation_replay_ledger import SQLiteConfirmationReplayLedger
from core.content_ops.confirmation_response_capture import DispatchResponseCapture
from core.content_ops.confirmation_response_gateway import ConfirmationGatewaySink,_ack
from core.content_ops.confirmation_response_store import ConfirmationResponseAuthenticator
from core.content_ops.worker import ReviewSettings,TelegramReviewRelay

RELAY_TIMEOUT_SECONDS = 10


class ConfirmationRelayBridge:
    def __init__(self, *, enabled=False, token=None, release_sha=None, runtime_sha=None,
                 dispatch_auth=None, response_auth=None, ledger=None, sink=None,
                 settings=None, clock=None, provider_transport=None):
        self._enabled = enabled is True
        self._token,self._sha,self._runtime = token,release_sha,runtime_sha
        self._dispatch,self._response = dispatch_auth,response_auth
        self._ledger,self._sink,self._settings = ledger,sink,settings
        self._clock,self._transport = clock,provider_transport

    async def __call__(self, scope, receive, send):
        if scope.get('type')!='http':
            return
        status=503
        result=dict(status='confirmation_bridge_unavailable',execution_authorized=False)
        if self._enabled:
            try:
                require(type(self._token) is str and TOKEN.fullmatch(self._token)
                    and type(self._sha) is str and SHA.fullmatch(self._sha)
                    and self._runtime==self._sha and type(self._dispatch) is DispatchAuthenticator
                    and type(self._response) is ConfirmationResponseAuthenticator
                    and type(self._ledger) is SQLiteConfirmationReplayLedger
                    and type(self._sink) is ConfirmationGatewaySink
                    and type(self._settings) is ReviewSettings
                    and self._settings.release_sha==self._sha and callable(self._clock))
                require(not hmac.compare_digest(self._dispatch._key,self._response._key)
                    and not hmac.compare_digest(self._dispatch._key,self._token.encode())
                    and self._sink.validate_configuration() is True
                    and self._sink._sha==self._sha
                    and hmac.compare_digest(self._sink._auth._key,self._response._key)
                    and not hmac.compare_digest(self._token,self._sink._token)
                    and not hmac.compare_digest(self._token,self._settings.gateway_token))
                status=400
                require(scope.get('method')=='POST' and scope.get('path')==PATH
                    and not scope.get('query_string',b''))
                headers={}
                for key,value in scope.get('headers',[]):
                    key=key.lower();require(key not in headers);headers[key]=value
                status=401
                require(type(headers.get(b'authorization')) is bytes
                    and len(headers[b'authorization'])<=263
                    and hmac.compare_digest(headers[b'authorization'],('Bearer '+self._token).encode()))
                status=409
                require(headers.get(b'x-content-ops-release-sha')==self._sha.encode())
                status=400
                require(headers.get(b'content-type')==b'application/json'
                    and headers.get(b'content-encoding',b'identity')==b'identity'
                    and b'cookie' not in headers)
                length=headers.get(b'content-length')
                require(type(length) is bytes and length.isdigit() and 0<int(length)<=MAX_BODY)
                async with asyncio.timeout(RELAY_TIMEOUT_SECONDS):
                    raw=bytearray()
                    while True:
                        event=await receive()
                        require(event.get('type')=='http.request' and type(event.get('body',b'')) is bytes)
                        raw.extend(event.get('body',b''));require(len(raw)<=MAX_BODY)
                        if not event.get('more_body',False):break
                    require(len(raw)==int(length));raw=bytes(raw)
                    envelope=self._dispatch.decode(raw=raw,now=self._clock(),release_sha=self._sha)
                    body=parse(envelope.request)['body']
                    require(envelope.bot_id==int(self._settings.relay_bot_token.split(':',1)[0])
                        and body['chat_id']==int(self._settings.relay_chat_id))
                    status=503
                    # This may commit even if the request task is cancelled.
                    # No acknowledgement => no send; a retry is already consumed.
                    fresh=await asyncio.to_thread(self._ledger.consume,envelope=envelope)
                    require(fresh is True)
                    capture=DispatchResponseCapture(enabled=True,raw=raw,dispatch_auth=self._dispatch,
                        release_sha=self._sha,authenticator=self._response,sink=self._sink.record,clock=self._clock)
                    relay=TelegramReviewRelay(self._settings,transport=self._transport,response_capture=capture)
                    await relay.preflight()  # Existing identity/private-room/non-admin checks.
                    await relay._post('sendMessage',body)  # Capture rechecks expiry immediately before POST.
                    result=dict(_ack(capture.ack),release_sha=self._sha,
                        dispatch_sha256=hashlib.sha256(raw).hexdigest())
                    status=200
            except Exception:
                result=dict(status='confirmation_bridge_unknown',execution_authorized=False)
        await send(dict(type='http.response.start',status=status,headers=[
            (b'content-type',b'application/json'),(b'cache-control',b'no-store')]))
        await send(dict(type='http.response.body',body=encode(result).encode()))
