"""Entire bridge uses synthetic keys, local SQLite and in-process HTTP only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import astuple,replace
import hashlib
import json
import sqlite3
import subprocess
import sys

import httpx
import pytest

from core.content_ops.cancellation_markup import encode
from core.content_ops.confirmation_dispatch_protocol import (
    PATH,MAX_BODY,DispatchAuthenticator,DispatchEnvelope,ConfirmationBridgeError,
)
from core.content_ops.confirmation_replay_ledger import LEDGER_DDL,SQLiteConfirmationReplayLedger
from core.content_ops.confirmation_relay_bridge import ConfirmationRelayBridge
from core.content_ops.confirmation_dispatch_client import ConfirmationDispatchClient,PostgresCommittedDispatchReader
from core.content_ops.confirmation_send_authority import PostgresConfirmationSendGuard
from core.content_ops.confirmation_delivery_owner import confirmation_send_request
from core.content_ops.confirmation_response_gateway import ConfirmationGatewaySink,ConfirmationSourceGateway
from core.content_ops.confirmation_response_store import PostgresConfirmationSourceStore
from test_content_ops_cancellation_control_receipt_owner import ReceiptConnection
from test_content_ops_cancellation_markup_authority import fixture
from test_content_ops_cancellation_markup_confirmation import target,moment
from test_content_ops_confirmation_delivery_owner import DELIVERY,receipt
from test_content_ops_confirmation_response_store import auth,Connection as SourceConnection
from test_content_ops_confirmation_send import permission
from test_content_ops_worker import settings
from test_content_ops_review_cancellation import BOT,ROOM,NOW,uid,records

SHA='a'*40
TOKEN='synthetic_dispatch_token_'+'b'*32
SOURCE_TOKEN='synthetic_source_token_'+'c'*32
LEDGER_ID=uid(818)


def dispatch_auth():return DispatchAuthenticator(b'synthetic-dispatch-key-only'*2)
def envelope():
    i=fixture()[0]
    return DispatchEnvelope(DELIVERY,permission().permission_id,target().card_id,BOT,
        confirmation_send_request(target(),i['plan'],i['bindings']),SHA,NOW,NOW+15)
def wire(e=None):return dispatch_auth().encode(envelope=e or envelope(),now=moment(NOW+.05))


def provision(tmp_path):
    path=tmp_path.resolve()/'relay-ledger.sqlite'
    with sqlite3.connect(path) as c:
        c.executescript(LEDGER_DDL);c.execute('insert into identity values(?,1)',(LEDGER_ID,))
    path.chmod(0o600)
    return path
def ledger(path,**changes):
    opts=dict(enabled=True,path=str(path),ledger_id=LEDGER_ID);opts.update(changes)
    return SQLiteConfirmationReplayLedger(**opts)
def count(path):
    with sqlite3.connect(path) as c:return c.execute('select count(*) from consumed').fetchone()[0]


class Rig:
    def __init__(self,path,**changes):
        self.path=path;self.calls=[];self.source_calls=[]
        self.clock=lambda:moment(NOW+.05)
        def connect():self.source_calls.append(1);return SourceConnection()
        source=ConfirmationSourceGateway(enabled=True,token=SOURCE_TOKEN,release_sha=SHA,runtime_sha=SHA,
            authenticator=auth(),store=PostgresConfirmationSourceStore(connect,authenticator=auth()))
        sink=ConfirmationGatewaySink(enabled=True,origin='https://owner.example.invalid',token=SOURCE_TOKEN,
            release_sha=SHA,runtime_sha=SHA,authenticator=auth(),transport=httpx.ASGITransport(app=source))
        async def provider(request):
            # Even preflight is forbidden until the durable commit succeeds.
            assert count(path)==1
            method=request.url.path.rsplit('/',1)[-1];self.calls.append(method)
            if method=='getMe':result=dict(id=BOT,is_bot=True)
            elif method=='getChat':result=dict(id=ROOM,type='supergroup')
            elif method=='getChatMember':result=dict(status='member',user=dict(id=BOT,is_bot=True))
            else:return httpx.Response(200,content=receipt().raw_response)
            return httpx.Response(200,json=dict(ok=True,result=result))
        self.provider=provider
        opts=dict(enabled=True,token=TOKEN,release_sha=SHA,runtime_sha=SHA,dispatch_auth=dispatch_auth(),
            response_auth=auth(),ledger=ledger(path),sink=sink,
            settings=replace(settings(),release_sha=SHA,relay_bot_token=f'{BOT}:'+'a'*35,relay_chat_id=str(ROOM)),
            clock=lambda:self.clock(),provider_transport=httpx.MockTransport(lambda r:self.provider(r)))
        opts.update(changes);self.app=ConfirmationRelayBridge(**opts)

    async def invoke(self,raw=None,headers=None,**scope_changes):
        raw=wire() if raw is None else raw
        h=[(b'authorization',('Bearer '+TOKEN).encode()),(b'x-content-ops-release-sha',SHA.encode()),
            (b'content-type',b'application/json'),(b'content-length',str(len(raw)).encode())]
        scope=dict(type='http',method='POST',path=PATH,query_string=b'',headers=h if headers is None else headers)
        scope.update(scope_changes);out=[];reads=[]
        async def receive():reads.append(1);return dict(type='http.request',body=raw)
        async def send(x):out.append(x)
        await self.app(scope,receive,send)
        return out[0]['status'],json.loads(out[1]['body']),reads


def test_codec_exact_roundtrip_and_redacted_repr():
    e=envelope()
    assert dispatch_auth().decode(raw=wire(),now=moment(NOW+.1),release_sha=SHA)==e
    assert DELIVERY not in repr(e) and str(ROOM) not in repr(e)


@pytest.mark.parametrize('field,value',[('delivery_id','bad'),('permission_id','bad'),('card_id','bad'),
    ('bot_id',True),('bot_id',0),('release_sha','a'*39),('issued_at',True),('issued_at',NOW+1),
    ('expires_at',NOW+16),('expires_at',NOW),('request',b'{}'),('request',b'x'*4097)])
def test_invalid_envelope_cannot_be_signed(field,value):
    with pytest.raises(ConfirmationBridgeError):wire(replace(envelope(),**{field:value}))


@pytest.mark.parametrize('change',['copy','method','extra','chat','topic','keyboard','callback','confirmation'])
def test_scope_cannot_be_broadened_even_by_codec_caller(change):
    data=json.loads(envelope().request);b=data['body']
    if change=='copy':b['text']+=' extra'
    if change=='method':data['method']='sendPhoto'
    if change=='extra':b['parse_mode']='HTML'
    if change=='chat':b['chat_id']=True
    if change=='topic':b['message_thread_id']=True
    if change=='keyboard':b['reply_markup']['inline_keyboard'].append([])
    if change=='callback':b['reply_markup']['inline_keyboard'][0][0]['callback_data']='a'*51
    if change=='confirmation':b['text']=b['text'].replace(target().approval_id,uid(999))
    with pytest.raises(ConfirmationBridgeError):wire(replace(envelope(),request=encode(data).encode()))


@pytest.mark.parametrize('field',list(json.loads(wire())))
def test_every_wire_field_is_authenticated(field):
    p=json.loads(wire());p[field]=None
    with pytest.raises(ConfirmationBridgeError):
        dispatch_auth().decode(raw=encode(p).encode(),now=moment(),release_sha=SHA)


def test_noncanonical_duplicate_json_wrong_key_and_expiry_rejected():
    for raw in (b' '+wire(),wire()[:-1]+b',"seal":"x"}',b'x'*(MAX_BODY+1)):
        with pytest.raises(ConfirmationBridgeError):dispatch_auth().decode(raw=raw,now=moment(),release_sha=SHA)
    with pytest.raises(ConfirmationBridgeError):
        DispatchAuthenticator(b'wrong-key'*8).decode(raw=wire(),now=moment(),release_sha=SHA)
    with pytest.raises(ConfirmationBridgeError):dispatch_auth().decode(raw=wire(),now=moment(NOW+15),release_sha=SHA)


def test_shared_ledger_concurrency_new_instances_and_changed_ids_cannot_retry(tmp_path):
    p=provision(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:ledger(p).consume(envelope=envelope()),range(8)))
    assert results.count(True)==1 and count(p)==1
    assert ledger(p).consume(envelope=envelope()) is False
    for change in (dict(delivery_id=uid(900)),dict(permission_id=uid(900)),dict(card_id=uid(900)),
                   dict(delivery_id=uid(900),permission_id=uid(901))):
        assert ledger(p).consume(envelope=replace(envelope(),**change)) is False


@pytest.mark.parametrize('change',['missing','schema','permissions','symlink','identity','corrupt'])
def test_ledger_invalid_never_recreated(tmp_path,change):
    p=provision(tmp_path)
    if change=='missing':p.unlink()
    if change=='schema':
        with sqlite3.connect(p) as c:c.execute('drop trigger consumed_no_delete')
    if change=='permissions':p.chmod(0o644)
    if change=='symlink':
        link=p.parent/'alias.sqlite';link.symlink_to(p);p=link
    if change=='identity':
        with sqlite3.connect(p) as c:c.execute('update identity set id=?',(uid(999),))
    if change=='corrupt':p.write_bytes(b'not sqlite')
    with pytest.raises(ConfirmationBridgeError):ledger(p).consume(envelope=envelope())
    if change=='missing':assert not p.exists()


def test_success_reuses_existing_relay_and_persists_source_before_ack(tmp_path):
    p=provision(tmp_path);r=Rig(p)
    status,result,_=asyncio.run(r.invoke())
    assert status==200 and result==dict(status='confirmation_source_recorded',reused=False,
        execution_authorized=False,release_sha=SHA,dispatch_sha256=hashlib.sha256(wire()).hexdigest())
    assert r.calls==['getMe','getChat','getChatMember','sendMessage'] and r.source_calls==[1]
    restarted=Rig(p)
    assert asyncio.run(restarted.invoke())[0]==503
    assert not restarted.calls and not restarted.source_calls


def test_concurrent_http_handoffs_only_one_provider_post(tmp_path):
    p=provision(tmp_path);rigs=[Rig(p) for _ in range(8)]
    async def run():return await asyncio.gather(*(r.invoke() for r in rigs))
    results=asyncio.run(run())
    assert sum(result[0]==200 for result in results)==1 and count(p)==1
    assert sum(r.calls.count('sendMessage') for r in rigs)==1


@pytest.mark.parametrize('change',[dict(enabled=False),dict(enabled=1),dict(token=None),
    dict(runtime_sha='b'*40),dict(dispatch_auth=None),dict(response_auth=None),dict(ledger=None),
    dict(sink=None),dict(settings=None),dict(clock=None),dict(dispatch_auth=DispatchAuthenticator(auth()._key))])
def test_invalid_config_or_disabled_has_zero_io(tmp_path,change):
    p=provision(tmp_path);r=Rig(p,**change)
    result=asyncio.run(r.invoke())
    assert result[0]==503 and not result[2] and count(p)==0 and not r.calls


@pytest.mark.parametrize('headers', [[],[(b'authorization',b'Bearer wrong')],
    [(b'authorization',('Bearer '+TOKEN).encode()),(b'Authorization',b'wrong')]])
def test_bad_http_auth_refuses_before_read_or_ledger(tmp_path,headers):
    p=provision(tmp_path);r=Rig(p)
    _,_,reads=asyncio.run(r.invoke(headers=headers))
    assert not reads and count(p)==0 and not r.calls


@pytest.mark.parametrize('change',[dict(method='GET'),dict(path=PATH+'/other'),dict(query_string=b'x=1')])
def test_wrong_route_zero_io(tmp_path,change):
    p=provision(tmp_path);r=Rig(p)
    assert asyncio.run(r.invoke(**change))[0]==400 and count(p)==0 and not r.calls


def test_bad_signature_wrong_bot_destination_and_late_expiry_have_no_post(tmp_path):
    p=provision(tmp_path);r=Rig(p)
    wrong=json.loads(wire());wrong['seal']='0'*64
    assert asyncio.run(r.invoke(raw=encode(wrong).encode()))[0]==400
    assert count(p)==0 and not r.calls
    assert asyncio.run(r.invoke(raw=wire(replace(envelope(),bot_id=BOT+1))))[0]==400
    original=r.provider
    async def provider(req):
        response=await original(req)
        if req.url.path.endswith('getChatMember'):r.clock=lambda:moment(NOW+15)
        return response
    r.provider=provider
    assert asyncio.run(r.invoke())[0]==503
    assert count(p)==1 and 'sendMessage' not in r.calls


@pytest.mark.parametrize('failure',['preflight','provider','source','lost_ack'])
def test_uncertain_failure_burns_ledger_and_never_retries(tmp_path,failure):
    p=provision(tmp_path);r=Rig(p);original=r.provider
    async def provider(req):
        method=req.url.path.rsplit('/',1)[-1]
        if (failure=='preflight' and method=='getMe') or (failure=='provider' and method=='sendMessage'):
            r.calls.append(method);raise httpx.ReadTimeout('private synthetic data')
        return await original(req)
    r.provider=provider
    if failure=='source':r.app._sink._transport=httpx.MockTransport(lambda _:httpx.Response(503))
    result=asyncio.run(r.invoke())  # lost_ack: deliberately discard successful reply.
    assert result[0]==(200 if failure=='lost_ack' else 503) and count(p)==1
    again=Rig(p)
    assert asyncio.run(again.invoke())[0]==503 and not again.calls


def proof_connection():
    c=ReceiptConnection();p=permission()
    c.rows=[('3000',),(p.permission_id,p.card_id,p.request_sha256,p.expires_at,moment(),moment(NOW+.05))]
    return c


def send_guard():
    c=ReceiptConnection();c.rows=[('5000','10000'),(fixture()[1],),records()]
    for _ in range(2):c.rows.extend([(moment(NOW+.05),),astuple(fixture()[2]),astuple(permission()),(moment(NOW+.05),)])
    return PostgresConfirmationSendGuard(lambda:c,enabled=True,target=target(),delivery_id=DELIVERY),c


def owner_client(transport,conn=None,**changes):
    opts=dict(enabled=True,origin='https://relay.example.invalid',token=TOKEN,release_sha=SHA,runtime_sha=SHA,
        authenticator=dispatch_auth(),reader=PostgresCommittedDispatchReader(lambda:conn or proof_connection()),
        target=target(),clock=lambda:moment(NOW+.05),transport=transport,**fixture()[0])
    opts.update(changes);return ConfirmationDispatchClient(**opts)


def test_owner_client_real_guard_to_http_relay_to_source_gateway(tmp_path):
    p=provision(tmp_path);r=Rig(p);requests=[]
    async def remote(request):
        requests.append(request)
        result=await r.invoke(raw=request.content,headers=request.headers.raw)
        return httpx.Response(result[0],json=result[1],headers={'content-type':'application/json'})
    guard,c=send_guard();proof=proof_connection()
    with guard.hold(**fixture()[0]) as lease:
        result=owner_client(httpx.MockTransport(remote),proof,**lease._identity).send_confirmation(
            delivery_id=DELIVERY,request=envelope().request,lease=lease)
    assert result==dict(status='confirmation_source_recorded',reused=False,execution_authorized=False)
    assert len(requests)==1 and len(r.calls)==4 and proof.exits==[None] and not c.rows


@pytest.mark.parametrize('origin',['http://relay.example.invalid','https://127.0.0.1',
    'https://relay.example.invalid/path','https://user@relay.example.invalid','https://relay.example.invalid:443'])
def test_owner_unsafe_origin_has_zero_network(origin):
    def fail(_):raise AssertionError('no network')
    guard,_=send_guard()
    with guard.hold(**fixture()[0]) as lease:
        with pytest.raises(ConfirmationBridgeError):owner_client(httpx.MockTransport(fail),origin=origin).send_confirmation(
            delivery_id=DELIVERY,request=envelope().request,lease=lease)


def test_owner_no_open_real_guard_or_disabled_cannot_send():
    def fail(_):raise AssertionError('no network')
    c=owner_client(httpx.MockTransport(fail))
    with pytest.raises(ConfirmationBridgeError):c.send_confirmation(delivery_id=DELIVERY,request=envelope().request)
    assert owner_client(httpx.MockTransport(fail),enabled=False).send_confirmation() is None


@pytest.mark.parametrize('status,body',[(302,{}),(503,{}),(200,{}),(200,{'status':'approved'})])
def test_owner_bad_reply_is_unknown_and_one_http_attempt(status,body):
    calls=[]
    def remote(req):calls.append(req);return httpx.Response(status,json=body)
    guard,_=send_guard()
    with guard.hold(**fixture()[0]) as lease:
        with pytest.raises(ConfirmationBridgeError):owner_client(httpx.MockTransport(remote),**lease._identity).send_confirmation(
            delivery_id=DELIVERY,request=envelope().request,lease=lease)
    assert len(calls)==1


def test_durable_ledger_survives_new_python_process(tmp_path):
    p=provision(tmp_path)
    assert ledger(p).consume(envelope=envelope()) is True
    code="import sys; sys.path.insert(0,'tests'); from test_content_ops_confirmation_relay_bridge import ledger,envelope; print(ledger(sys.argv[1]).consume(envelope=envelope()))"
    result=subprocess.run([sys.executable,'-c',code,str(p)],capture_output=True,text=True,
        check=True,timeout=10,env={'PATH':'/usr/bin:/bin'})
    assert result.stdout.strip()=='False' and count(p)==1


def test_real_sqlite_commit_ack_loss_is_consumed_not_retried(tmp_path,monkeypatch):
    p=provision(tmp_path);real=sqlite3.connect
    class LostAck:
        def __init__(self,connection):self.c=connection
        def __getattr__(self,key):return getattr(self.c,key)
        def commit(self):self.c.commit();raise OSError('synthetic commit acknowledgement lost')
    def connect(*args,**kw):
        c=real(*args,**kw)
        return LostAck(c) if kw.get('uri') else c
    with monkeypatch.context() as m:
        m.setattr(sqlite3,'connect',connect)
        with pytest.raises(ConfirmationBridgeError):ledger(p).consume(envelope=envelope())
    assert count(p)==1 and ledger(p).consume(envelope=envelope()) is False


def test_ledger_mutation_is_denied(tmp_path):
    p=provision(tmp_path);ledger(p).consume(envelope=envelope())
    with sqlite3.connect(p) as c:
        for sql in ('delete from consumed',"update consumed set release_sha='changed'"):
            with pytest.raises(sqlite3.IntegrityError):c.execute(sql)
    assert count(p)==1


@pytest.mark.parametrize('change',['cookie','encoding','length','release','content_type','extra_header'])
def test_http_metadata_tampering_never_consumes(tmp_path,change):
    p=provision(tmp_path);r=Rig(p)
    headers=[(b'authorization',('Bearer '+TOKEN).encode()),(b'x-content-ops-release-sha',SHA.encode()),
        (b'content-type',b'application/json'),(b'content-length',str(len(wire())).encode())]
    if change=='cookie':headers.append((b'cookie',b'shared=bad'))
    if change=='encoding':headers.append((b'content-encoding',b'gzip'))
    if change=='length':headers[-1]=(b'content-length',b'9999999')
    if change=='release':headers[1]=(b'x-content-ops-release-sha',b'b'*40)
    if change=='content_type':headers[2]=(b'content-type',b'text/plain')
    if change=='extra_header':headers.extend([(b'x-test',b'a'),(b'X-Test',b'b')])
    assert asyncio.run(r.invoke(headers=headers))[0] in (400,409)
    assert count(p)==0 and not r.calls


def test_relay_total_timeout_during_provider_consumes_without_retry(tmp_path,monkeypatch):
    from core.content_ops import confirmation_relay_bridge as module
    p=provision(tmp_path);r=Rig(p)
    async def slow(req):await asyncio.sleep(1);raise AssertionError('must time out')
    r.provider=slow;monkeypatch.setattr(module,'RELAY_TIMEOUT_SECONDS',.03)
    assert asyncio.run(r.invoke())[0]==503 and count(p)==1
    again=Rig(p)
    assert asyncio.run(again.invoke())[0]==503 and not again.calls


@pytest.mark.parametrize('row',[None,(),('bad',),
    (uid(1),uid(2),'e'*64,moment(NOW+300),moment(),moment()),
    (uid(1),target().card_id,envelope().request_sha256,moment(NOW-1),moment(),moment())])
def test_committed_reader_malformed_or_missing_row_blocks_http(row):
    conn=proof_connection();conn.rows[-1]=row
    with pytest.raises(ConfirmationBridgeError):PostgresCommittedDispatchReader(lambda:conn).read(
        delivery_id=DELIVERY,card_id=target().card_id,request_sha256=envelope().request_sha256)


def test_owner_uncommitted_readback_ack_loss_never_reaches_http():
    conn=proof_connection();conn.commit_error=True;calls=[]
    guard,_=send_guard()
    with guard.hold(**fixture()[0]) as lease:
        with pytest.raises(ConfirmationBridgeError):
            owner_client(httpx.MockTransport(lambda req:calls.append(1)),conn,**lease._identity).send_confirmation(
                delivery_id=DELIVERY,request=envelope().request,lease=lease)
    assert not calls
