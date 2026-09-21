"""Real PostgreSQL banner-result owner, not a feedback registrar or publisher.

Requires a trusted pre-registered exact request and complete original review
card. The feedback-registration authority is deliberately not fabricated here.
Storage is create-only, verified before the atomic version/asset/CAS transaction
commits. A failed/uncertain commit may leave an orphan object: never delete or
retry automatically. Runtime roles remain ungranted; no production wiring.
"""
from contextlib import contextmanager, nullcontext
from datetime import timedelta

from core.content_ops.banner_regeneration import BannerError, BannerRequest, MODEL, inspect_png, require, sha
from core.content_ops.private_review_owner import private_snapshot
from core.content_ops.prompt_reservation import _time, card_parent_binding
from core.content_ops.review_edit_ingress import EditBindings


def eligible(ok):
    require(ok,'banner_revision_ineligible')


class PostgresBannerOwner:
    def __init__(self, connection_factory, *, bindings, storage):
        require(callable(connection_factory) and type(bindings) is EditBindings
                and callable(getattr(storage,'put_and_verify',None)))
        self._connect,self._bindings,self._storage=connection_factory,bindings,storage

    @contextmanager
    def _locked(self, request, *, connection=None):
        require(type(request) is BannerRequest)
        request.validate()
        s=request.snapshot
        # Internal feedback registration shares the SAME transaction. Never
        # commit or close an injected connection here; its caller owns the ACK.
        with (self._connect() if connection is None else nullcontext(connection)) as connection:
            require(connection.autocommit is False,'banner_transaction_required')
            with connection.cursor() as cur:
                cur.execute('''select to_jsonb(i) from public.content_items i
                    where workspace_id=%s::uuid and id=%s::uuid and client_id=%s for update''',
                    (s.workspace_id,s.content_item_id,s.client_id))
                row=cur.fetchone(); eligible(row is not None); item=row[0]
                cur.execute('''select to_jsonb(q) from private.content_ops_banner_requests q
                    where job_id=%s::uuid for share''',(request.job_id,))
                row=cur.fetchone(); eligible(row is not None); registered=row[0]
                eligible(registered['request_text']==request.encoded()
                         and registered['request_sha256']==request.digest())
                cur.execute('''select to_jsonb(r) from private.content_ops_button_reviews r
                    where id=%s::uuid and workspace_id=%s::uuid and content_item_id=%s::uuid
                      and content_version_id=%s::uuid and client_id=%s for update''',
                    (registered['review_id'],s.workspace_id,s.content_item_id,s.content_version_id,s.client_id))
                row=cur.fetchone(); eligible(row is not None); review=row[0]
                cur.execute('''select to_jsonb(c) from private.content_ops_button_cards c
                    where id=%s::uuid and review_id=%s::uuid for share''',
                    (registered['card_id'],review['id']))
                row=cur.fetchone(); eligible(row is not None); card=row[0]
                eligible(card_parent_binding(self._bindings,review,card)==card['bindings']['parent_binding'])
                actor=registered['actor_id']
                cur.execute('''select actor_id from private.content_ops_button_identities
                    where workspace_id=%s::uuid and bot_binding=%s and human_binding=%s
                      and actor_id=%s::uuid and active for share''',
                    (s.workspace_id,card['bindings']['bot'],registered['human_binding'],actor))
                eligible(cur.fetchone() is not None)
                cur.execute('''select actor_id from private.content_ops_button_reviewers
                    where workspace_id=%s::uuid and client_id=%s and actor_id=%s::uuid
                      and active for share''',(s.workspace_id,s.client_id,actor))
                eligible(cur.fetchone() is not None)
                cur.execute('''select client_id from public.workspace_clients
                    where workspace_id=%s::uuid and client_id=%s and active for share''',(s.workspace_id,s.client_id))
                eligible(cur.fetchone() is not None)
                cur.execute('''select exists(select 1 from public.approvals where workspace_id=%s::uuid and content_item_id=%s::uuid)
                    or exists(select 1 from public.publications where workspace_id=%s::uuid and content_item_id=%s::uuid)''',
                    (s.workspace_id,s.content_item_id,s.workspace_id,s.content_item_id))
                eligible(cur.fetchone()[0] is False)
                cur.execute('select to_jsonb(t) from private.content_ops_banner_results t where job_id=%s::uuid',
                            (request.job_id,))
                row=cur.fetchone(); result=row[0] if row else None
                if result is not None:
                    eligible(result['content_version_id']==registered['result_version_id']==item['current_version_id']
                             and result['asset_id']==registered['result_asset_id'] and item['status']=='needs_review')
                    yield cur,registered,review,card,None,None,result
                    return
                eligible(item['current_version_id']==s.content_version_id and item['status']=='needs_review'
                         and card['active'] is True and review['state']=='edit_requested'
                         and review['epoch']==card['epoch']+1)
                cur.execute('''select to_jsonb(a) from private.content_ops_button_actions a
                    where review_id=%s::uuid and idempotency_key=%s for share''',
                    (review['id'],registered['action_key']))
                row=cur.fetchone(); eligible(row is not None); action=row[0]
                eligible(action['actor_id']==actor and action['epoch']==review['epoch']
                         and action['action']=='edit_banner' and action['result_status']=='edit_requested'
                         and action['version_fingerprint']==review['version_fingerprint']
                         and _time(card['delivered_at'])<=_time(action['created_at'])<=_time(registered['registered_at']))
                cur.execute('''select to_jsonb(v),
                        jsonb_build_object('canonical_url',s.canonical_url,'published_at',s.published_at),
                        jsonb_build_object('sha256',a.sha256)
                    from public.content_versions v
                    join public.content_source_links l on l.workspace_id=v.workspace_id
                      and l.content_item_id=v.content_item_id and l.position=0 and l.client_id=%s
                    join public.source_items s on s.workspace_id=l.workspace_id and s.client_id=l.client_id
                      and s.id=l.source_item_id and s.source_type='tweet'
                    join public.assets a on a.workspace_id=v.workspace_id and a.content_item_id=v.content_item_id
                      and a.content_version_id=v.id and a.id::text=v.deliverables->>'primary_asset_id'
                    where v.workspace_id=%s::uuid and v.content_item_id=%s::uuid and v.id=%s::uuid
                      and a.asset_kind='png' and a.mime_type='image/png' for share of v,l,s,a''',
                    (s.client_id,s.workspace_id,s.content_item_id,s.content_version_id))
                rows=cur.fetchall(); eligible(len(rows)==1)
                old,source,asset=rows[0]
                eligible(private_snapshot(review,old,source,asset)==s)
                cur.execute('select private.content_ops_button_version_fingerprint(%s::uuid,%s::uuid,%s::uuid)',
                    (s.workspace_id,s.content_item_id,s.content_version_id))
                eligible(cur.fetchone()[0]==review['version_fingerprint'])
                self._clock(cur,registered,review,card,source)
                yield cur,registered,review,card,old,source,None

    def _clock(self,cur,registered,review,card,source):
        cur.execute('select clock_timestamp()'); now=_time(cur.fetchone()[0])
        eligible(_time(registered['registered_at'])<=now<min(_time(review['expires_at']),_time(card['expires_at']))
                 and now-timedelta(hours=24)<_time(source['published_at'])<=now)

    def is_current_edit(self, request):
        try:
            with self._locked(request) as values:
                return values[-1] is None
        except BannerError as exc:
            if str(exc)=='banner_revision_ineligible': return False
            raise BannerError('banner_owner_check_unknown') from None
        except Exception:
            raise BannerError('banner_owner_check_unknown') from None

    def save_banner_revision(self, request, png):
        require(inspect_png(png,opaque=True)==(1536,1024),'banner_image_invalid')
        result_hash=sha(png)
        try:
            from psycopg.types.json import Jsonb
            with self._locked(request) as (cur,registered,review,card,old,source,prior):
                if prior is not None:
                    eligible(prior['banner_sha256']==result_hash and prior['byte_size']==len(png))
                    receipt=self._receipt(prior['content_version_id'],result_hash)
                else:
                    s=request.snapshot
                    # Existing catalog/proxy contract: workspace/client/asset/name.
                    path=f"{s.workspace_id}/{s.client_id}/{registered['result_asset_id']}/news-card.png"
                    expected={'storage_bucket':'content-studio','storage_path':path,'sha256':result_hash,'byte_size':len(png)}
                    stored=self._storage.put_and_verify(path=path,png=png)
                    require(type(stored) is dict and stored==expected,'banner_storage_unknown')
                    # Remote I/O elapsed time cannot extend review/source expiry.
                    self._clock(cur,registered,review,card,source)
                    cur.execute('select coalesce(max(version_number),0)+1 from public.content_versions where workspace_id=%s::uuid and content_item_id=%s::uuid',
                        (s.workspace_id,s.content_item_id))
                    number=cur.fetchone()[0]
                    version,asset=registered['result_version_id'],registered['result_asset_id']
                    metadata={'mock_mode':old['generation_meta'].get('mock_mode',True),
                        'fact_check':{'status':'needs_review'},'brand_qa':{'status':'needs_review'},
                        'review_revision':{'schema_version':'private-banner@1','previous_version_id':s.content_version_id,
                            'job_id':request.job_id,'request_sha256':request.digest(),'model':MODEL}}
                    content={k:v for k,v in old['content'].items() if k not in {'render','spec'}}
                    cur.execute('''insert into public.content_versions(id,workspace_id,content_item_id,version_number,
                        prompt_version,locale,title,content,channel_copy,deliverables,qa,generation_meta,created_by)
                        values(%s::uuid,%s::uuid,%s::uuid,%s,'private-banner@1',%s,%s,%s,%s,%s,%s,%s,%s::uuid)''',
                        (version,s.workspace_id,s.content_item_id,number,old['locale'],old['title'],Jsonb(content),
                         Jsonb(old['channel_copy']),Jsonb({'primary_asset_id':asset}),
                         Jsonb({'manual_review_required':True,'source_fidelity':'needs_review','brand_alignment':'needs_review'}),
                         Jsonb(metadata),registered['actor_id']))
                    cur.execute('''insert into public.assets(id,workspace_id,content_item_id,content_version_id,
                        asset_kind,storage_bucket,storage_path,mime_type,byte_size,sha256,width,height,metadata,created_by)
                        values(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'png','content-studio',%s,'image/png',%s,%s,1536,1024,%s,%s::uuid)''',
                        (asset,s.workspace_id,s.content_item_id,version,path,len(png),result_hash,
                         Jsonb({'schema_version':'private-banner@1','job_id':request.job_id,'request_sha256':request.digest()}),registered['actor_id']))
                    cur.execute("update public.content_items set current_version_id=%s::uuid,status='needs_review',scheduled_for=null where id=%s::uuid and workspace_id=%s::uuid and current_version_id=%s::uuid",
                        (version,s.content_item_id,s.workspace_id,s.content_version_id))
                    eligible(cur.rowcount==1)
                    cur.execute("update private.content_ops_button_reviews set state='held',epoch=epoch+1 where id=%s::uuid",(review['id'],))
                    cur.execute('''update private.content_ops_button_cards c set active=false
                        from private.content_ops_button_reviews r where c.review_id=r.id and r.workspace_id=%s::uuid
                          and r.content_item_id=%s::uuid and c.active''',(s.workspace_id,s.content_item_id))
                    cur.execute('''insert into private.content_ops_banner_results(job_id,content_version_id,asset_id,banner_sha256,byte_size)
                        values(%s::uuid,%s::uuid,%s::uuid,%s,%s)''',(request.job_id,version,asset,result_hash,len(png)))
                    self._clock(cur,registered,review,card,source)
                    receipt=self._receipt(version,result_hash)
            return receipt  # commit acknowledged, not a delivery receipt
        except Exception:
            raise BannerError('banner_commit_unknown') from None

    @staticmethod
    def _receipt(version,result_hash):
        return {'content_version_id':version,'banner_sha256':result_hash,
                'rereview_required':True,'execution_authorized':False}
