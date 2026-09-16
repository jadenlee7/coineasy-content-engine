"""Explicit exclusive-relay assembly. Default OFF, no provisioning or polling.

Disabled startup reads only its enable flag. validate-only may read the local
image stamp/configuration, never the ledger or any external system. Runtime
construction itself performs no HTTP/DB/ledger I/O; the authenticated bridge
checks the already-provisioned ledger when a request arrives.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import os
from pathlib import Path
import re

FLAG='CONTENT_OPS_CONFIRMATION_ENABLED'
PREFIX='CONTENT_OPS_CONFIRMATION_'
STAMP=Path('/app/content-ops-confirmation-build-sha')
LEDGER_PATH='/data/content-ops-confirmation/replay.sqlite'


class ConfirmationRuntimeError(ValueError):
    pass


def require(value):
    if not value:raise ConfirmationRuntimeError('confirmation_configuration_invalid')


def enabled(env):
    value=env.get(FLAG,'false')
    require(type(value) is str and value in ('true','false'))
    return value=='true'


async def disabled_app(scope,receive,send):
    # No body read, configuration lookup, dependency import or owner call.
    if scope.get('type')=='lifespan':
        while True:
            event=await receive()
            if event['type']=='lifespan.startup':await send({'type':'lifespan.startup.complete'})
            elif event['type']=='lifespan.shutdown':
                await send({'type':'lifespan.shutdown.complete'});return
    if scope.get('type')=='http':
        await send(dict(type='http.response.start',status=503,headers=[
            (b'content-type',b'text/plain; charset=utf-8'),(b'cache-control',b'no-store')]))
        await send(dict(type='http.response.body',body=b'Disabled'))


def _release(env,stamp_reader):
    values=(env.get(PREFIX+'RELEASE_SHA',''),env.get('RAILWAY_GIT_COMMIT_SHA',''),
        (stamp_reader or (lambda:STAMP.read_text(encoding='ascii')))())
    require(all(type(v) is str and re.fullmatch('[a-f0-9]{40}',v) for v in values)
        and len(set(values))==1)
    return values[0]


@dataclass(frozen=True,repr=False)
class ConfirmationRuntimeSettings:
    release_sha:str
    dispatch_token:str
    dispatch_key:bytes
    source_token:str
    response_key:bytes
    source_origin:str
    bot_token:str
    chat_id:str
    ledger_path:str
    ledger_id:str


def settings(env,stamp_reader=None):
    from core.content_ops.worker import _validate_secret_boundary,_BOT_TOKEN,_CHAT_ID
    from core.content_ops.confirmation_dispatch_protocol import TOKEN
    from core.content_ops.confirmation_response_gateway import ConfirmationGatewaySink
    from core.content_ops.confirmation_response_store import ConfirmationResponseAuthenticator
    from core.content_ops.prompt_receipt import canonical_uuid
    require(all(type(k) is str and type(v) is str for k,v in env.items()))
    _validate_secret_boundary(env)
    require(env.get('CONTENT_OPS_RELAY_OWNER')=='confirmation'
        and env.get('CONTENT_OPS_REVIEW_ENABLED','false')=='false'
        and not env.get('CONTENT_OPS_GATEWAY_TOKEN'))
    sha=_release(env,stamp_reader)
    def value(name):return env.get(PREFIX+name,'')
    def key(name):
        text=value(name)
        require(re.fullmatch(r'[A-Za-z0-9_-]{43}',text))
        raw=base64.urlsafe_b64decode(text+'=')
        require(len(raw)==32 and base64.urlsafe_b64encode(raw).decode().rstrip('=')==text)
        return raw
    dispatch_token,source_token=value('DISPATCH_TOKEN'),value('SOURCE_TOKEN')
    require(TOKEN.fullmatch(dispatch_token) and TOKEN.fullmatch(source_token))
    dispatch_key,response_key=key('DISPATCH_KEY'),key('RESPONSE_KEY')
    bot_token,chat_id=env.get('TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN',''),env.get('TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID','')
    require(_BOT_TOKEN.fullmatch(bot_token) and _CHAT_ID.fullmatch(chat_id))
    secret_names={PREFIX+'DISPATCH_TOKEN',PREFIX+'SOURCE_TOKEN',PREFIX+'DISPATCH_KEY',PREFIX+'RESPONSE_KEY',
        'TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN'}
    raw_secrets=[dispatch_token.encode(),source_token.encode(),dispatch_key,response_key,bot_token.encode()]
    require(len(set(raw_secrets))==len(raw_secrets)
        and len({env[name] for name in secret_names})==len(secret_names))
    for name,val in env.items():
        if not val:continue
        upper=name.upper()
        if upper.startswith(('PG','ADMIN_','STUDIO_','AUTH_')):raise ConfirmationRuntimeError()
        if name not in secret_names and upper!='GPG_KEY' and any(x in upper for x in ('TOKEN','SECRET','PASSWORD','KEY')):
            raise ConfirmationRuntimeError()
        if name!='TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID' and ('CHAT_ID' in upper or 'CHANNEL' in upper):
            require(val!=chat_id)
    path=value('LEDGER_PATH');ledger_id=value('LEDGER_ID')
    require(path==LEDGER_PATH and canonical_uuid(ledger_id))
    source=ConfirmationGatewaySink(enabled=True,origin=value('SOURCE_ORIGIN'),token=source_token,
        release_sha=sha,runtime_sha=sha,authenticator=ConfirmationResponseAuthenticator(response_key))
    require(source.validate_configuration() is True)
    return ConfirmationRuntimeSettings(sha,dispatch_token,dispatch_key,source_token,response_key,
        value('SOURCE_ORIGIN'),bot_token,chat_id,path,ledger_id)


def validate_only(*,environ=None,stamp_reader=None):
    env=os.environ if environ is None else environ
    result=dict(ok=False,mode='validate_only',network_calls=False,database_calls=False,
        telegram_calls=False,ledger_access=False,execution_authorized=False)
    try:
        active=enabled(env)
        if active:
            settings(env,stamp_reader)
        else:
            require(env.get('CONTENT_OPS_REVIEW_ENABLED','false')=='false')
            _release(env,stamp_reader)
        return dict(result,ok=True,enabled=active,configuration_scope='enabled' if active else 'disabled',
            ledger_verified=False)
    except Exception:
        return dict(result,error='confirmation_configuration_invalid')


def create_app(*,environ=None,stamp_reader=None):
    env=os.environ if environ is None else environ
    try:
        if not enabled(env):return disabled_app
        config=settings(env,stamp_reader)
    except Exception:
        return disabled_app
    from core.content_ops.worker import ReviewSettings
    from core.content_ops.confirmation_dispatch_protocol import DispatchAuthenticator
    from core.content_ops.confirmation_replay_ledger import SQLiteConfirmationReplayLedger
    from core.content_ops.confirmation_response_store import ConfirmationResponseAuthenticator
    from core.content_ops.confirmation_response_gateway import ConfirmationGatewaySink
    from core.content_ops.confirmation_relay_bridge import ConfirmationRelayBridge
    auth=ConfirmationResponseAuthenticator(config.response_key)
    sink=ConfirmationGatewaySink(enabled=True,origin=config.source_origin,token=config.source_token,
        release_sha=config.release_sha,runtime_sha=config.release_sha,authenticator=auth)
    # Compatibility carrier for existing bot preflight only. No old gateway
    # credential, polling worker or Studio/publication dependency is constructed.
    relay=ReviewSettings(gateway_origin='',gateway_token='',release_sha=config.release_sha,
        studio_origin='',relay_bot_token=config.bot_token,relay_chat_id=config.chat_id)
    bridge=ConfirmationRelayBridge(enabled=True,token=config.dispatch_token,
        release_sha=config.release_sha,runtime_sha=config.release_sha,
        dispatch_auth=DispatchAuthenticator(config.dispatch_key),response_auth=auth,
        ledger=SQLiteConfirmationReplayLedger(enabled=True,path=config.ledger_path,ledger_id=config.ledger_id),
        sink=sink,settings=relay,clock=lambda:datetime.now(timezone.utc))
    async def app(scope,receive,send):
        if scope.get('type')=='lifespan':await disabled_app(scope,receive,send)
        else:await bridge(scope,receive,send)
    return app
