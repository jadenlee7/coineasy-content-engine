"""Real disposable PostgreSQL + fake object storage/provider, never production."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import json
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from PIL import Image
from psycopg.types.json import Jsonb
from core.content_ops.banner_regeneration import BannerError, BannerRequest, BannerJournal, run_banner_once, sha
from core.content_ops.banner_revision_owner import PostgresBannerOwner
from core.content_ops.private_review_owner import private_snapshot
from core.content_ops.prompt_reservation import card_parent_binding
from core.content_ops.banner_feedback import PostgresBannerFeedbackOwner, handle_banner_reply
from core.content_ops.review_ingress import IngressPolicy
from core.content_ops.review_edit_ingress import PostgresEditReplyOwner, verified_edit_reply
from core.content_ops.private_reply_owner import PostgresPrivateReplyOwner
from core.content_ops.polling_review_adapter import PollingReviewAdapter
from core.publications.handoff import CLIENT_TARGETS


def run(connect,card_fixture,record_card,binding,reserve_attempt,validate_prompt_fixture,persist_prompt):
    def png(size):
        out=io.BytesIO(); Image.new('RGB',size,'purple').save(out,format='PNG'); return out.getvalue()
    image,logo=png((1536,1024)),png((64,64))
    class Storage:
        def __init__(self): self.objects={}; self.calls=0; self.bad=False
        def put_and_verify(self, *, path, png):
            self.calls+=1
            assert path not in self.objects,'must not repeat an object creation'
            self.objects[path]=png
            return {'storage_bucket':'content-studio','storage_path':path,
                    'sha256':('0'*64 if self.bad else sha(png)),'byte_size':len(png)}
    storage=Storage(); owner=PostgresBannerOwner(connect,bindings=binding,storage=storage)

    def read(sql,params=()):
        with connect() as c: return c.execute(sql,params).fetchone()[0]

    def setup(client='squid', *, register_request=True):
        ctx=card_fixture(client)
        with connect() as c:
            c.execute("update public.source_items set source_type='tweet',canonical_url=%s,published_at=now()-interval '1 minute' where id=%s",
                ('https://x.com/'+CLIENT_TARGETS[client][2]+'/status/123',ctx['source']))
            fp=c.execute('select private.content_ops_button_version_fingerprint(%s,%s,%s)',
                (ctx['workspace'],ctx['item'],ctx['version'])).fetchone()[0]
            c.execute('update private.content_ops_button_reviews set version_fingerprint=%s where id=%s',(fp,ctx['review']))
            review=c.execute('select to_jsonb(r) from private.content_ops_button_reviews r where id=%s',(ctx['review'],)).fetchone()[0]
            old=c.execute('select to_jsonb(v) from public.content_versions v where id=%s',(ctx['version'],)).fetchone()[0]
            source=c.execute('select to_jsonb(s) from public.source_items s where id=%s',(ctx['source'],)).fetchone()[0]
            asset=c.execute('select to_jsonb(a) from public.assets a where id=%s',(ctx['asset'],)).fetchone()[0]
        ctx['fingerprint']=fp
        card={'id':ctx['card'],'review_id':ctx['review'],'epoch':0,'version_fingerprint':fp,
              'bindings':ctx['card_bindings'],'parts':ctx['card_parts'],
              'delivered_at':ctx['card_delivered'],'expires_at':ctx['card_expires']}
        ctx['card_bindings']['parent_binding']=card_parent_binding(binding,review,card)
        record_card(ctx)
        read('select private.record_content_ops_button_action(%s,%s,%s,%s,%s)',
            (ctx['review'],ctx['actor'],fp,'edit_banner','e'*64))
        request=BannerRequest(str(uuid4()),sha(uuid4().bytes),private_snapshot(review,old,source,asset),
            '공식 업데이트','검토용 배너','제목을 크게',sha(logo))
        if register_request:
            with connect() as c:
                c.execute('''insert into private.content_ops_banner_requests(job_id,card_id,review_id,actor_id,
                    human_binding,action_key,request_text,request_sha256) values(%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (request.job_id,ctx['card'],ctx['review'],ctx['actor'],binding.digest('human',101,201),
                     'e'*64,request.encoded(),request.digest()))
        return ctx,request,old

    def result_count(request):
        return read('select count(*) from private.content_ops_banner_results where job_id=%s',(request.job_id,))

    def fail(request,selected_owner=owner):
        try: selected_owner.save_banner_revision(request,image)
        except BannerError as exc: assert str(exc)=='banner_commit_unknown'
        else: raise AssertionError('must fail closed')

    for client in CLIENT_TARGETS:
        ctx,request,old=setup(client)
        assert owner.is_current_edit(request) is True
        before=storage.calls
        receipt=owner.save_banner_revision(request,image)
        assert storage.calls==before+1 and receipt['execution_authorized'] is False and receipt['rereview_required'] is True
        new=read('select to_jsonb(v) from public.content_versions v where id=%s',(receipt['content_version_id'],))
        assert new['channel_copy']==old['channel_copy'] and new['id']!=old['id']
        assert new['generation_meta']['fact_check']['status']=='needs_review'
        assert new['generation_meta']['brand_qa']['status']=='needs_review'
        assert read('select to_jsonb(v) from public.content_versions v where id=%s',(old['id'],))==old
        assert read('select current_version_id::text from public.content_items where id=%s',(ctx['item'],))==new['id']
        assert read('select active from private.content_ops_button_cards where id=%s',(ctx['card'],)) is False
        assert owner.save_banner_revision(request,image)==receipt and storage.calls==before+1
        assert owner.is_current_edit(request) is False
        assert result_count(request)==1

    ctx,request,_=setup()
    before=storage.calls
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts=list(pool.map(lambda _:owner.save_banner_revision(request,image),range(8)))
    assert len({r['content_version_id'] for r in receipts})==1 and storage.calls==before+1

    # Reject before upload when registration, identity, card, source or version changed.
    for mutation in ('request','unregistered','identity','card','source','status','action'):
        ctx,request,_=setup(); before=storage.calls
        if mutation=='request': request=replace(request,instruction='변경된 요청')
        if mutation=='unregistered': request=replace(request,job_id=str(uuid4()))
        updates={
            'identity':('update private.content_ops_button_identities set active=false where workspace_id=%s',ctx['workspace']),
            'card':('update private.content_ops_button_cards set active=false where id=%s',ctx['card']),
            'source':("update public.source_items set published_at=now()-interval '25 hours' where id=%s",ctx['source']),
            'status':("update public.content_items set status='draft' where id=%s",ctx['item']),
            'action':("update private.content_ops_button_actions set action='edit_x' where review_id=%s",ctx['review']),
        }
        if mutation in updates:
            sql,param=updates[mutation]
            with connect() as c: c.execute(sql,(param,))
        assert owner.is_current_edit(request) is False
        fail(request)
        assert storage.calls==before and result_count(request)==0

    class FaultConnection:
        def __init__(self, lost_ack=False, fail_sql='insert into private.content_ops_banner_results'):
            self.real=connect(); self.lost_ack=lost_ack; self.fail_sql=fail_sql
        @property
        def autocommit(self): return self.real.autocommit
        def __enter__(self): self.real.__enter__(); return self
        def __exit__(self,*args):
            self.real.__exit__(*args)
            if self.lost_ack and args[0] is None: raise RuntimeError('synthetic lost ACK')
        def cursor(self):
            real=self.real.cursor(); lost=self.lost_ack; fail_sql=self.fail_sql
            class Cursor:
                def __enter__(self): real.__enter__(); return self
                def __exit__(self,*args): return real.__exit__(*args)
                def execute(self,sql,params=()):
                    result=real.execute(sql,params)
                    if not lost and fail_sql in sql:
                        raise RuntimeError('synthetic post-insert rollback')
                    return result
                def fetchone(self): return real.fetchone()
                def fetchall(self): return real.fetchall()
                @property
                def rowcount(self): return real.rowcount
            return Cursor()
    ctx,request,_=setup(); before=storage.calls
    fail(request,PostgresBannerOwner(FaultConnection,bindings=binding,storage=storage))
    assert storage.calls==before+1 and result_count(request)==0
    assert read('select current_version_id::text from public.content_items where id=%s',(ctx['item'],))==request.snapshot.content_version_id
    assert read('select active from private.content_ops_button_cards where id=%s',(ctx['card'],)) is True
    assert read('select count(*) from public.content_versions where content_item_id=%s',(ctx['item'],))==1
    # Orphan retained, not deleted or uploaded again.
    ctx,request,_=setup(); before=storage.calls
    fail(request,PostgresBannerOwner(lambda:FaultConnection(True),bindings=binding,storage=storage))
    assert result_count(request)==1 and storage.calls==before+1
    # Read-only reconciliation, no repeated save after uncertain acknowledgement.
    committed=read('select content_version_id::text from private.content_ops_banner_results where job_id=%s',(request.job_id,))
    assert read('select current_version_id::text from public.content_items where id=%s',(ctx['item'],))==committed

    class Provider:
        calls=0
        async def edit(self,request,logo): self.calls+=1; return image
    ctx,request,_=setup(); provider=Provider()
    with tempfile.TemporaryDirectory() as directory:
        journal=BannerJournal(Path(directory)/'journal.sqlite3')
        journal.enqueue(request,logo,now=int(time.time()))
        assert asyncio.run(run_banner_once(enabled=True,journal=journal,owner=owner,provider=provider))['status']=='result_ready'
        assert asyncio.run(run_banner_once(enabled=True,journal=journal,owner=owner,provider=provider))['status']=='revision_saved'
        assert provider.calls==1 and result_count(request)==1
    assert read('select (select count(*) from public.approvals)+(select count(*) from public.publications)')==0
    feedback=PostgresBannerFeedbackOwner(connect,bindings=binding)
    def feedback_setup(client='squid', *, brief=True):
        ctx,template,_=setup(client,register_request=False)
        ctx['channel']='banner'
        reserve_attempt(ctx); validate_prompt_fixture(ctx)
        ctx['prompt']=persist_prompt(ctx)['prompt_id']
        if brief:
            with connect() as c:
                c.execute('''insert into private.content_ops_banner_briefs values(%s,%s,%s,%s,%s)''',
                    (ctx['version'],ctx['fingerprint'],template.headline,template.subtitle,template.logo_sha256))
        policy=IngressPolicy('synthetic-feedback-'+'k'*32,101,-(10**12+7),'fixture-room',((201,ctx['actor']),))
        prompt=dict(ctx['provider_response']['result'])
        update={'update_id':1,'message':{'message_id':ctx['message']+10,'date':int(time.time()),
            'chat':dict(prompt['chat']),'from':{'id':201,'is_bot':False},
            'text':'제목을 더 크게, 여백을 넓게','reply_to_message':prompt}}
        args=dict(enabled=True,raw_body=json.dumps(update).encode(),
            headers=[('Content-Type','application/json'),('X-Telegram-Bot-Api-Secret-Token',policy.webhook_secret)],
            policy=policy,bindings=binding,owner=feedback,now=int(time.time()))
        return ctx,args
    for client in CLIENT_TARGETS:
        ctx,args=feedback_setup(client)
        receipt=handle_banner_reply(**args)
        assert receipt['status']=='banner_requested' and receipt['reused'] is False
        replay=handle_banner_reply(**args)
        assert replay==dict(receipt,reused=True)
        adapter=PollingReviewAdapter(enabled=True,policy=args['policy'],edit_bindings=binding,
            transactional_reply_owner=PostgresPrivateReplyOwner(connect,bindings=binding))
        assert asyncio.run(adapter.handle_edit_reply(json.loads(args['raw_body']),now=args['now']))==replay
        raw=read('select request_text from private.content_ops_banner_requests where job_id=%s',(receipt['job_id'],))
        requested=BannerRequest.decode(raw)
        assert requested.headline=='공식 업데이트' and requested.subtitle=='검토용 배너'
        assert requested.instruction=='제목을 더 크게, 여백을 넓게' and owner.is_current_edit(requested)
        # Real result owner can consume the authenticated registered request.
        assert owner.save_banner_revision(requested,image)['rereview_required'] is True

    ctx,args=feedback_setup()
    with ThreadPoolExecutor(max_workers=8) as pool:
        feedback_receipts=list(pool.map(lambda _:handle_banner_reply(**args),range(8)))
    assert len({r['job_id'] for r in feedback_receipts})==1
    assert sum(not r['reused'] for r in feedback_receipts)==1
    for field,value in [('text','다른 지시'),('message_id',ctx['message']+11)]:
        changed=json.loads(args['raw_body']); changed['message'][field]=value
        try: handle_banner_reply(**dict(args,raw_body=json.dumps(changed).encode()))
        except BannerError: pass
        else: raise AssertionError('feedback conflict accepted')
    # The TG/X owner must NOT consume a banner reply as copy.
    event=verified_edit_reply(**{k:v for k,v in args.items() if k not in {'enabled','owner'}})
    try: PostgresEditReplyOwner(connect).save_edit_reply(event)
    except Exception: pass
    else: raise AssertionError('banner reply treated as copy')
    for mutation in ('brief','human','topic','card','source','action','identity','status'):
        ctx,args=feedback_setup(brief=mutation!='brief')
        event=verified_edit_reply(**{k:v for k,v in args.items() if k not in {'enabled','owner'}})
        if mutation=='human': event=replace(event,human_binding=binding.digest('human',101,999))
        if mutation=='topic': event=replace(event,thread_id=7)
        changes={
            'card':('update private.content_ops_button_cards set active=false where id=%s',ctx['card']),
            'source':("update public.source_items set published_at=now()-interval '25 hours' where id=%s",ctx['source']),
            'action':("update private.content_ops_button_actions set action='edit_x' where review_id=%s",ctx['review']),
            'identity':('update private.content_ops_button_identities set active=false where workspace_id=%s',ctx['workspace']),
            'status':("update public.content_items set status='draft' where id=%s",ctx['item']),
        }
        if mutation in changes:
            sql,param=changes[mutation]
            with connect() as c: c.execute(sql,(param,))
        try: feedback.save_banner_feedback(event)
        except BannerError as exc: assert str(exc)=='banner_feedback_unknown'
        else: raise AssertionError('ineligible feedback accepted')
        assert read('select count(*) from private.content_ops_banner_requests where review_id=%s',(ctx['review'],))==0
        assert read('select count(*) from private.content_ops_banner_feedback where prompt_id=%s',(ctx['prompt'],))==0
    for lost_ack in (False,True):
        ctx,args=feedback_setup()
        faulty=PostgresBannerFeedbackOwner(
            lambda:FaultConnection(lost_ack,'insert into private.content_ops_banner_feedback'),bindings=binding)
        try: handle_banner_reply(**dict(args,owner=faulty))
        except BannerError as exc: assert str(exc)=='banner_feedback_unknown'
        else: raise AssertionError('unknown feedback ACK reported successful')
        # Independent read-only receipt reconciliation, NEVER retry this write.
        assert read('select count(*) from private.content_ops_banner_requests where review_id=%s',(ctx['review'],))==int(lost_ack)
        assert read('select count(*) from private.content_ops_banner_feedback where prompt_id=%s',(ctx['prompt'],))==int(lost_ack)
    return {'bannerClients':4,'bannerPreUploadRefusals':7,'concurrentBannerCommits':8,
            'authenticatedFeedbackClients':4,'concurrentFeedbackRegistrations':8,'feedbackRefusals':8,
            'feedbackThroughPollingAdapter':True,'feedbackRollbackAndLostAck':True,
            'newVersionAndUpload':1,'oldCopyAndVersionPreserved':True,'oldCardInvalidated':True,
            'postInsertRollback':True,'orphanRetained':True,'lostAckReadOnlyReconciled':True,
            'journalToRealDatabase':True,'productionCalls':0,'realProviderCalls':0,'realStorageCalls':0,'publicRows':0}
