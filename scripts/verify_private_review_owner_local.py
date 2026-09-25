"""Called ONLY inside the disposable full-schema driver; synthetic evidence."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

from core.content_ops.polling_review_adapter import PollingReviewAdapter
from core.content_ops.private_review_owner import PostgresPrivateReviewOwner, private_snapshot
from core.content_ops.prompt_reservation import card_parent_binding
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_ingress import IngressPolicy, ReviewIngressError
from core.publications.handoff import CLIENT_TARGETS


def run(connect, card_fixture, record_card, binding):
    bot, room, human = 101, -(10**12 + 7), 201
    signer = ButtonSigner(b'synthetic-private-callback-signing-key-123')
    owner = PostgresPrivateReviewOwner(connect, binding)

    def read(sql, params=()):
        with connect() as c:
            return c.execute(sql, params).fetchone()[0]

    def setup(client='squid', action='s'):
        ctx = card_fixture(client)
        with connect() as c:
            c.execute("update public.source_items set source_type='tweet',canonical_url=%s where id=%s",
                ('https://x.com/'+CLIENT_TARGETS[client][2]+'/status/123', ctx['source']))
            fp = c.execute('select private.content_ops_button_version_fingerprint(%s,%s,%s)',
                (ctx['workspace'],ctx['item'],ctx['version'])).fetchone()[0]
            c.execute('update private.content_ops_button_reviews set version_fingerprint=%s where id=%s',
                (fp,ctx['review']))
            review = c.execute('select to_jsonb(r) from private.content_ops_button_reviews r where id=%s',
                (ctx['review'],)).fetchone()[0]
            v = c.execute('select to_jsonb(v) from public.content_versions v where id=%s', (ctx['version'],)).fetchone()[0]
            s = c.execute('select to_jsonb(s) from public.source_items s where id=%s', (ctx['source'],)).fetchone()[0]
            a = c.execute('select to_jsonb(a) from public.assets a where id=%s', (ctx['asset'],)).fetchone()[0]
        ctx['fingerprint'] = fp
        card = {'id':ctx['card'],'review_id':ctx['review'],'epoch':0,'version_fingerprint':fp,
                'bindings':ctx['card_bindings'],'parts':ctx['card_parts'],
                'delivered_at':ctx['card_delivered'],'expires_at':ctx['card_expires']}
        ctx['card_bindings']['parent_binding'] = card_parent_binding(binding,review,card)
        record_card(ctx)
        ctx['snapshot'] = private_snapshot(review,v,s,a)
        ctx['policy'] = IngressPolicy('fixture_webhook_'+'z'*32,bot,room,'fixture-room',((human,ctx['actor']),))
        now = int(time.time())
        ctx['update'] = {'update_id':77,'callback_query':{'id':'fixture-click',
            'from':{'id':human,'is_bot':False},'data':'ce1:'+signer.issue(ctx['snapshot'],action,
                'fixture-room',now=now,expires_at=now+600),
            'message':{'message_id':ctx['message']+1,'date':now,
                       'from':{'id':bot,'is_bot':True},'chat':{'id':room,'type':'supergroup'}}}}
        return ctx

    def callback(ctx, selected_owner=owner):
        adapter = PollingReviewAdapter(enabled=True,policy=ctx['policy'],signer=signer,
            transactional_review_owner=selected_owner)
        return asyncio.run(adapter.handle_callback(ctx['update'],now=int(time.time())))

    def count(ctx):
        return read('select count(*) from private.content_ops_button_actions where review_id=%s',(ctx['review'],))

    def reject(ctx, selected_owner=owner):
        try:
            callback(ctx,selected_owner)
        except ReviewIngressError:
            pass
        else:
            raise AssertionError('private DB action must fail closed')

    cases = 0
    for client in CLIENT_TARGETS:
        for action in ('s','c','t','x','b','h'):
            ctx = setup(client,action)
            expected = 'edit_requested' if action in ('t','x','b') else 'action_recorded'
            assert callback(ctx) == {'status':expected,'execution_authorized':False}
            assert callback(ctx)['status']==expected
            assert count(ctx)==1
            cases += 1

    ctx = setup()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _:callback(ctx),range(8)))
    assert all(r['execution_authorized'] is False for r in results) and count(ctx)==1

    refusals = 0
    for mutation in ('card','identity','reviewer','client','source','latest','status','fingerprint',
                     'topic','message','signature','public','actor','room'):
        ctx = setup(action='a' if mutation=='public' else 's')
        sql = {
            'card':("update private.content_ops_button_cards set active=false where id=%s",ctx['card']),
            'identity':("update private.content_ops_button_identities set active=false where workspace_id=%s",ctx['workspace']),
            'reviewer':("update private.content_ops_button_reviewers set active=false where workspace_id=%s",ctx['workspace']),
            'client':("update public.workspace_clients set active=false where workspace_id=%s",ctx['workspace']),
            'source':("update public.source_items set published_at=now()-interval '25 hours' where id=%s",ctx['source']),
            'status':("update public.content_items set status='draft' where id=%s",ctx['item']),
            'fingerprint':("update public.source_items set title='Changed source' where id=%s",ctx['source']),
        }.get(mutation)
        if sql:
            with connect() as c: c.execute(sql[0],(sql[1],))
        if mutation=='latest':
            with connect() as c:
                c.execute("""insert into public.source_items(id,workspace_id,client_id,
                    source_feed_id,source_type,body,source_hash,published_at)
                    values(gen_random_uuid(),%s,%s,%s,'tweet','Newer synthetic tweet',
                        repeat('e',64),clock_timestamp())""",
                    (ctx['workspace'],ctx['client'],ctx['feed']))
        q = ctx['update']['callback_query']
        if mutation=='topic': q['message']['message_thread_id']=9
        if mutation=='message': q['message']['message_id']+=10
        if mutation=='signature': q['data']='ce1:'+'A'*51
        if mutation=='actor': q['from']['id']=999
        if mutation=='room': q['message']['chat']['id']=-999
        reject(ctx)
        assert count(ctx)==0
        refusals += 1

    # A per-call connection has no cross-request card/snapshot mutable cache.
    contexts = [setup(client) for client in CLIENT_TARGETS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(callback,contexts))
    assert all(count(c)==1 for c in contexts)

    ctx = setup()
    class LostAck:
        def __init__(self): self.real=connect()
        @property
        def autocommit(self): return self.real.autocommit
        def __enter__(self): self.real.__enter__(); return self
        def cursor(self): return self.real.cursor()
        def __exit__(self,*args):
            self.real.__exit__(*args)
            if args[0] is None: raise RuntimeError('synthetic lost commit acknowledgement')
    reject(ctx,PostgresPrivateReviewOwner(LostAck,binding))
    # Independent owner readback only; do not repeat the callback after UNKNOWN.
    assert count(ctx)==1

    # Inject a bad result AFTER the real SQL write: receipt validation must roll back.
    ctx = setup()
    class BadReceipt:
        def __init__(self): self.real=connect()
        @property
        def autocommit(self): return self.real.autocommit
        def __enter__(self): self.real.__enter__(); return self
        def __exit__(self,*args): return self.real.__exit__(*args)
        def cursor(self):
            real=self.real.cursor()
            class Cursor:
                bad=False
                def __enter__(self): real.__enter__(); return self
                def __exit__(self,*args): return real.__exit__(*args)
                def execute(self,sql,params=()):
                    self.bad='select private.record_content_ops_button_action' in sql
                    return real.execute(sql,params)
                def fetchall(self): return real.fetchall()
                def fetchone(self):
                    row=real.fetchone()
                    if self.bad:
                        row[0]['execution_authorized']=True
                    return row
            return Cursor()
    reject(ctx,PostgresPrivateReviewOwner(BadReceipt,binding))
    assert count(ctx)==0
    assert read('select (select count(*) from public.approvals)+(select count(*) from public.publications)')==0
    return {'privateCallbackClientActionCases':cases,'privateCallbackRefusals':refusals,
            'concurrentSameCallback':8,'concurrentClients':4,'oneDurableActionPerClick':True,
            'badReceiptRollback':True,'commitAckLossReadback':True,
            'productionCalls':0,'providerCalls':0,'publicRows':0,'hostedProof':False}
