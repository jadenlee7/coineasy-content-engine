"""Synthetic runtime configuration only: no secret discovery or network."""
import asyncio
import base64
import json
import subprocess
import sys

import pytest

from core.content_ops import confirmation_runtime as runtime
from scripts import run_content_ops_confirmation as cli
from test_content_ops_worker import BOT_TOKEN,CHAT_ID
from test_content_ops_review_cancellation import uid

SHA='a'*40
P=runtime.PREFIX


def env(**changes):
    value={runtime.FLAG:'true',P+'RELEASE_SHA':SHA,'RAILWAY_GIT_COMMIT_SHA':SHA,
        'CONTENT_OPS_RELAY_OWNER':'confirmation','CONTENT_OPS_REVIEW_ENABLED':'false',
        P+'DISPATCH_TOKEN':'synthetic_dispatch_'+'a'*32,P+'SOURCE_TOKEN':'synthetic_source_'+'b'*32,
        P+'DISPATCH_KEY':base64.urlsafe_b64encode(b'c'*32).decode().rstrip('='),
        P+'RESPONSE_KEY':base64.urlsafe_b64encode(b'd'*32).decode().rstrip('='),
        P+'SOURCE_ORIGIN':'https://owner.example.invalid',
        'TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN':BOT_TOKEN,'TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID':CHAT_ID,
        P+'LEDGER_PATH':runtime.LEDGER_PATH,P+'LEDGER_ID':uid(818)}
    value.update(changes);return value


async def invoke(app,**changes):
    scope=dict(type='http',path='/internal/content-ops/confirmation-dispatch',method='POST',headers=[],query_string=b'')
    scope.update(changes);out=[];reads=[]
    async def receive():reads.append(1);raise AssertionError('no body read')
    async def send(value):out.append(value)
    await app(scope,receive,send)
    return out,reads


class FlagOnly:
    def get(self,key,default=None):
        assert key==runtime.FLAG
        return 'false'
    def __iter__(self):raise AssertionError('no configuration scan')


def test_disabled_factory_reads_only_flag_no_stamp_secrets_or_body():
    def fail():raise AssertionError('no stamp read')
    app=runtime.create_app(environ=FlagOnly(),stamp_reader=fail)
    out,reads=asyncio.run(invoke(app))
    assert out[0]['status']==503 and out[1]['body']==b'Disabled' and not reads
    assert (b'cache-control',b'no-store') in out[0]['headers']


def test_off_factory_imports_only_standard_library_in_clean_process():
    code="""import asyncio,sys
from core.content_ops.confirmation_runtime import create_app
app=create_app(environ={})
assert not any(x in sys.modules for x in ['httpx','uvicorn','sqlite3','core.content_ops.worker'])
print('disabled_zero_dependencies')
"""
    result=subprocess.run([sys.executable,'-S','-c',code],check=True,capture_output=True,text=True,
        env={'PATH':'/usr/bin:/bin'},timeout=10)
    assert result.stdout.strip()=='disabled_zero_dependencies'


@pytest.mark.parametrize('flag',['TRUE','1','',True,None])
def test_invalid_flag_is_disabled_and_validation_fails(flag):
    e=env(**{runtime.FLAG:flag})
    assert runtime.validate_only(environ=e,stamp_reader=lambda:SHA)['ok'] is False
    out,_=asyncio.run(invoke(runtime.create_app(environ=e,stamp_reader=lambda:SHA)))
    assert out[0]['status']==503 and out[1]['body']==b'Disabled'


@pytest.mark.parametrize('stamp',['b'*40,'a'*39,SHA+'\n',None])
def test_stamp_mismatch_never_assembles_enabled_relay(stamp):
    e=env()
    assert runtime.validate_only(environ=e,stamp_reader=lambda:stamp)['ok'] is False
    out,_=asyncio.run(invoke(runtime.create_app(environ=e,stamp_reader=lambda:stamp)))
    assert out[1]['body']==b'Disabled'


@pytest.mark.parametrize('name,value',[
    (P+'RELEASE_SHA','b'*40),('RAILWAY_GIT_COMMIT_SHA','b'*40),('CONTENT_OPS_RELAY_OWNER','daily'),
    ('CONTENT_OPS_REVIEW_ENABLED','true'),('CONTENT_OPS_GATEWAY_TOKEN','old-token'),
    (P+'DISPATCH_TOKEN','short'),(P+'SOURCE_TOKEN','short'),(P+'DISPATCH_KEY','bad'),
    (P+'RESPONSE_KEY','a'*44),(P+'SOURCE_ORIGIN','http://owner.example.invalid'),
    (P+'LEDGER_PATH','/tmp/replay.sqlite'),(P+'LEDGER_PATH','/data/../replay.sqlite'),(P+'LEDGER_ID','bad'),
    ('TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN','bad'),('TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID','bad')])
def test_bad_configuration_fixed_error_and_disabled(name,value):
    e=env(**{name:value});result=runtime.validate_only(environ=e,stamp_reader=lambda:SHA)
    assert result['ok'] is False and result['error']=='confirmation_configuration_invalid'
    assert result['network_calls'] is result['ledger_access'] is False
    assert value not in json.dumps(result) or value in ('true','bad')
    out,_=asyncio.run(invoke(runtime.create_app(environ=e,stamp_reader=lambda:SHA)))
    assert out[1]['body']==b'Disabled'


@pytest.mark.parametrize('name',['SUPABASE_SERVICE_ROLE_KEY','DATABASE_URL','PGHOST','PGPASSWORD',
    'TYPEFULLY_API_KEY','OPENAI_API_KEY','X_BEARER_TOKEN','STUDIO_ACCESS_TOKEN',
    'ADMIN_TOKEN','AUTH_SECRET','OTHER_REVIEWER_KEY','TELEGRAM_OTHER_BOT_TOKEN'])
def test_forbidden_or_unrelated_credentials_block_enablement(name):
    result=runtime.validate_only(environ=env(**{name:'synthetic-sensitive-value'}),stamp_reader=lambda:SHA)
    assert result['ok'] is False and 'synthetic-sensitive-value' not in repr(result)


@pytest.mark.parametrize('a,b',[(P+'DISPATCH_KEY',P+'RESPONSE_KEY'),(P+'DISPATCH_TOKEN',P+'SOURCE_TOKEN')])
def test_credential_aliases_are_rejected(a,b):
    e=env();e[a]=e[b]
    assert runtime.validate_only(environ=e,stamp_reader=lambda:SHA)['ok'] is False


def test_binary_key_alias_of_bearer_is_rejected():
    e=env();e[P+'DISPATCH_TOKEN']='d'*32
    assert runtime.validate_only(environ=e,stamp_reader=lambda:SHA)['ok'] is False


def test_validate_only_never_opens_ledger_or_constructs_network_clients(monkeypatch):
    from core.content_ops.confirmation_replay_ledger import SQLiteConfirmationReplayLedger
    import httpx
    def fail(*a,**k):raise AssertionError('no I/O')
    monkeypatch.setattr(SQLiteConfirmationReplayLedger,'consume',fail)
    monkeypatch.setattr(httpx,'AsyncClient',fail)
    monkeypatch.setattr(httpx,'Client',fail)
    result=runtime.validate_only(environ=env(),stamp_reader=lambda:SHA)
    assert result==dict(ok=True,mode='validate_only',network_calls=False,database_calls=False,
        telegram_calls=False,ledger_access=False,execution_authorized=False,enabled=True,
        configuration_scope='enabled',ledger_verified=False)
    assert BOT_TOKEN not in repr(runtime.settings(env(),lambda:SHA))
    app=runtime.create_app(environ=env(),stamp_reader=lambda:SHA)
    out,reads=asyncio.run(invoke(app))
    assert out[0]['status']==401 and not reads


def test_disabled_validation_checks_sha_but_does_not_require_secrets_or_volume():
    result=runtime.validate_only(environ={P+'RELEASE_SHA':SHA,'RAILWAY_GIT_COMMIT_SHA':SHA},stamp_reader=lambda:SHA)
    assert result['ok'] and result['enabled'] is False and result['ledger_verified'] is False
    assert runtime.validate_only(environ={},stamp_reader=lambda:SHA)['ok'] is False


def test_lifespan_is_side_effect_free_in_both_modes():
    async def run(app):
        events=iter([{'type':'lifespan.startup'},{'type':'lifespan.shutdown'}]);out=[]
        async def receive():return next(events)
        async def send(x):out.append(x)
        await app({'type':'lifespan'},receive,send)
        assert out==[{'type':'lifespan.startup.complete'},{'type':'lifespan.shutdown.complete'}]
    asyncio.run(run(runtime.create_app(environ={})))
    asyncio.run(run(runtime.create_app(environ=env(),stamp_reader=lambda:SHA)))


def test_cli_validation_does_not_start_server(monkeypatch,capsys):
    def validate():return {'ok':True,'network_calls':False}
    def fail():raise AssertionError('no server')
    monkeypatch.setattr(cli,'validate_only',validate);monkeypatch.setattr(cli,'create_app',fail)
    assert cli.main(['--validate-only'])==0
    assert json.loads(capsys.readouterr().out)=={'ok':True,'network_calls':False}


@pytest.mark.parametrize('port',['0','65536','-1','abc'])
def test_cli_invalid_port_does_not_construct_server(monkeypatch,capsys,port):
    monkeypatch.setenv('PORT',port)
    def fail():raise AssertionError('no app')
    monkeypatch.setattr(cli,'create_app',fail)
    assert cli.main([])==1 and 'configuration_invalid' in capsys.readouterr().out


def test_cli_server_single_worker_access_log_off(monkeypatch):
    import uvicorn
    calls=[];sentinel=object()
    monkeypatch.setattr(cli,'create_app',lambda:sentinel)
    monkeypatch.setenv('PORT','8080')
    monkeypatch.setattr(uvicorn,'run',lambda *a,**k:calls.append((a,k)))
    assert cli.main([])==0 and calls==[((sentinel,),dict(host='0.0.0.0',port=8080,
        workers=1,access_log=False,log_level='warning',proxy_headers=False))]
