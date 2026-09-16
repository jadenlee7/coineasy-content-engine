"""Owner-side exact HTTP handoff. No Telegram credential, no route, no retry.

Requires the coordinator's still-open real send guard and committed dispatch
readback. The restricted relay receives no DB/admin or human-binding secret.
"""
from datetime import datetime
import asyncio
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit

from core.content_ops.cancellation_markup_guard import _Lease
from core.content_ops.cancellation_markup_owner import _inputs
from core.content_ops.cancellation_markup_confirmation import _time
from core.content_ops.confirmation_delivery_owner import confirmation_send_request
from core.content_ops.confirmation_dispatch_protocol import (
    PATH,SHA,TOKEN,DispatchEnvelope,DispatchAuthenticator,ConfirmationBridgeError,require,parse,
)
from core.content_ops.confirmation_send_authority import ExactConfirmationSendAuthority,PostgresConfirmationSendReader
from core.content_ops.prompt_receipt import canonical_uuid


class PostgresCommittedDispatchReader:
    def __init__(self, connection_factory):
        self._factory=connection_factory

    def read(self, *, delivery_id, card_id, request_sha256):
        try:
            require(canonical_uuid(delivery_id) and canonical_uuid(card_id)
                and type(request_sha256) is str and re.fullmatch('[a-f0-9]{64}',request_sha256))
            with self._factory() as connection:
                require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('statement_timeout','3000',true)")
                    cursor.fetchone()
                    cursor.execute('''select d.permission_id::text,d.card_id::text,d.request_sha256,
                        l.expires_at,d.consumed_at,clock_timestamp()
                        from private.content_ops_button_confirmation_dispatches d
                        join private.content_ops_button_confirmation_send_permissions p
                          on p.permission_id=d.permission_id and p.delivery_id=d.delivery_id
                          and p.card_id=d.card_id and p.request_sha256=d.request_sha256
                        join private.content_ops_button_confirmation_deliveries l
                          on l.delivery_id=d.delivery_id and l.card_id=d.card_id
                          and l.request_sha256=d.request_sha256 and l.actor_id=p.actor_id
                          and l.target_binding=p.target_binding and l.expires_at=p.expires_at
                        where d.delivery_id=%s::uuid and d.card_id=%s::uuid and d.request_sha256=%s
                          and p.active and p.action='send_private_confirmation@1'
                          and p.authorized_at<=l.reserved_at and l.reserved_at<=d.consumed_at
                          and d.consumed_at<=clock_timestamp() and clock_timestamp()<l.expires_at
                          and l.status='unknown'
                          and not exists(select 1 from private.content_ops_button_confirmation_sources s
                            where s.delivery_id=d.delivery_id)''',(delivery_id,card_id,request_sha256))
                    row=cursor.fetchone()
                    require(type(row) is tuple and len(row)==6 and canonical_uuid(row[0])
                        and row[1]==card_id and row[2]==request_sha256
                        and all(type(t) is datetime and t.utcoffset() is not None for t in row[3:])
                        and row[4]<=row[5]<row[3])
            return row
        except Exception:
            raise ConfirmationBridgeError('confirmation_bridge_unknown') from None


class ConfirmationDispatchClient:
    def __init__(self, *, enabled=False, origin=None, token=None, release_sha=None,runtime_sha=None,
                 authenticator=None, reader=None, target=None, clock=None, transport=None, **identity):
        self._enabled=enabled is True
        self._origin,self._token,self._sha,self._runtime=origin,token,release_sha,runtime_sha
        self._auth,self._reader,self._target=authenticator,reader,target
        self._clock,self._transport,self._identity=clock,transport,dict(identity)

    def send_confirmation(self, *, delivery_id=None, request=None, lease=None):
        if not self._enabled:
            return None
        try:
            # This synchronous owner coordinator must run outside an ASGI event
            # loop (e.g. its existing owner thread), never nest event loops.
            try:asyncio.get_running_loop()
            except RuntimeError:pass
            else:raise ConfirmationBridgeError('confirmation_bridge_unknown')
            _inputs(**self._identity)
            require(type(self._token) is str and TOKEN.fullmatch(self._token)
                and type(self._sha) is str and SHA.fullmatch(self._sha) and self._runtime==self._sha
                and type(self._auth) is DispatchAuthenticator
                and type(self._reader) is PostgresCommittedDispatchReader and callable(self._clock)
                and canonical_uuid(delivery_id) and type(lease) is _Lease and lease._open
                and type(lease._authorize_plan) is ExactConfirmationSendAuthority
                and type(lease._authorize_plan._reader) is PostgresConfirmationSendReader
                and lease._authorize_plan._target==self._target
                and lease._authorize_plan._delivery==delivery_id)
            require(type(self._origin) is str)
            url=urlsplit(self._origin)
            require(url.scheme=='https' and url.hostname and '.' in url.hostname
                and url.netloc==url.hostname and not (url.path or url.query or url.fragment)
                and re.fullmatch(r'[a-z0-9.-]+',url.hostname))
            try:ipaddress.ip_address(url.hostname)
            except ValueError:pass
            else:raise ConfirmationBridgeError('confirmation_bridge_unknown')
            i=self._identity;plan=i['plan']
            require(type(request) is bytes and request==confirmation_send_request(self._target,plan,i['bindings']))
            require(lease.validate(now=self._clock(),**i) is True)
            row=self._reader.read(delivery_id=delivery_id,card_id=plan.card_id,
                request_sha256=hashlib.sha256(request).hexdigest())
            now=self._clock()
            require(abs(_time(now)-_time(row[5]))<=5 and int(_time(row[3]))==plan.expires_at
                and lease.validate(now=now,**i) is True)
            envelope=DispatchEnvelope(delivery_id,row[0],plan.card_id,plan.bot_id,request,self._sha,
                int(_time(now)),min(plan.expires_at,int(_time(now))+15))
            raw=self._auth.encode(envelope=envelope,now=now)
            return asyncio.run(self._handoff(raw))
        except Exception:
            raise ConfirmationBridgeError('confirmation_bridge_unknown') from None

    async def _handoff(self,raw):
        # Read-only DB validation does not depend on the HTTP runtime package.
        import httpx
        from core.content_ops.confirmation_response_gateway import _ack
        async with asyncio.timeout(12):
            async with httpx.AsyncClient(transport=self._transport,timeout=12,follow_redirects=False,trust_env=False) as client:
                async with client.stream('POST',self._origin+PATH,content=raw,headers={
                    'Authorization':'Bearer '+self._token,'Content-Type':'application/json',
                    'X-Content-Ops-Release-Sha':self._sha,'Accept-Encoding':'identity',
                }) as response:
                    require(response.status_code==200 and response.headers.get('content-type')=='application/json'
                        and response.headers.get('content-encoding','identity')=='identity')
                    data=bytearray()
                    async for chunk in response.aiter_bytes():
                        require(len(data)+len(chunk)<=2048)
                        data.extend(chunk)
                    value=parse(bytes(data))
                    require(type(value) is dict and set(value)=={'status','reused','execution_authorized',
                        'release_sha','dispatch_sha256'} and value['release_sha']==self._sha
                        and value['dispatch_sha256']==hashlib.sha256(raw).hexdigest())
                    return dict(_ack({k:value[k] for k in ('status','reused','execution_authorized')}))
