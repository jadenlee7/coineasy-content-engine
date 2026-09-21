"""Authenticated design feedback -> immutable request, never provider execution.

Uses the existing reserved/receipted prompt, not a second Telegram listener.
No runtime grants or startup wiring. Missing pinned brief fails closed. The
brief preparation and private card re-delivery owners remain separate work.
"""
from uuid import UUID, uuid4
import re

from core.content_ops.banner_regeneration import BannerError, BannerRequest, require, sha, text
from core.content_ops.banner_revision_owner import PostgresBannerOwner
from core.content_ops.private_review_owner import private_snapshot
from core.content_ops.prompt_reservation import _time
from core.content_ops.review_edit_ingress import VerifiedEditReply, verified_edit_reply


class _NoStorage:
    def put_and_verify(self, **kwargs):
        raise BannerError('banner_feedback_storage_forbidden')


def feedback_receipt(value):
    require(type(value) is dict and set(value)=={
        'status','job_id','request_sha256','reused','execution_authorized'}, 'banner_feedback_unknown')
    require(value['status']=='banner_requested' and type(value['reused']) is bool
            and value['execution_authorized'] is False, 'banner_feedback_unknown')
    require(type(value['job_id']) is str and str(UUID(value['job_id']))==value['job_id']
            and UUID(value['job_id']).int!=0, 'banner_feedback_unknown')
    require(type(value['request_sha256']) is str
            and re.fullmatch('[a-f0-9]{64}',value['request_sha256']), 'banner_feedback_unknown')
    return dict(value)


def handle_banner_reply(*, enabled=False, raw_body=None, headers=(), policy=None,
                        bindings=None, owner=None, now=None):
    if enabled is not True:
        return {'status':'disabled','execution_authorized':False}
    event=verified_edit_reply(raw_body=raw_body,headers=headers,policy=policy,bindings=bindings,now=now)
    # Validate bounded design text BEFORE any DB or provider I/O.
    text(event.replacement_text,600)
    try:
        return feedback_receipt(owner.save_banner_feedback(event))
    except Exception:
        raise BannerError('banner_feedback_unknown') from None


class PostgresBannerFeedbackOwner:
    def __init__(self, connection_factory, *, bindings):
        self._connect=connection_factory
        self._validator=PostgresBannerOwner(connection_factory,bindings=bindings,storage=_NoStorage())

    def save_banner_feedback(self,event):
        try:
            require(type(event) is VerifiedEditReply)
            text(event.replacement_text,600)
            require(type(event.actor_id) is str and str(UUID(event.actor_id))==event.actor_id
                    and UUID(event.actor_id).int!=0)
            require(all(type(v) is str and re.fullmatch('[a-f0-9]{64}',v) for v in (
                event.bot_binding,event.room_binding,event.message_binding,event.human_binding,event.operation_key)))
            require(type(event.message_date) is int and type(event.prompt_date) is int
                    and 0<event.prompt_date<=event.message_date)
            require(event.thread_id is None or (type(event.thread_id) is int and event.thread_id>0))
            with self._connect() as connection:
                require(connection.autocommit is False)
                with connection.cursor() as cur:
                    # Serialize all mutation paths on the same content item FIRST.
                    cur.execute('''select i.id from public.content_items i
                        join private.content_ops_button_reviews r on r.content_item_id=i.id and r.workspace_id=i.workspace_id
                        join private.content_ops_button_edit_prompts p on p.review_id=r.id
                        where p.bot_binding=%s and p.room_binding=%s and p.message_binding=%s
                          and p.actor_id=%s::uuid for update of i''',
                        (event.bot_binding,event.room_binding,event.message_binding,event.actor_id))
                    require(cur.fetchone() is not None)
                    cur.execute('''select to_jsonb(p),to_jsonb(r),to_jsonb(a)
                        from private.content_ops_button_edit_prompts p
                        join private.content_ops_button_reviews r on r.id=p.review_id
                        join private.content_ops_button_prompt_attempts a on a.review_id=r.id and a.epoch=p.epoch
                        where p.bot_binding=%s and p.room_binding=%s and p.message_binding=%s
                          and p.actor_id=%s::uuid for update of r''',
                        (event.bot_binding,event.room_binding,event.message_binding,event.actor_id))
                    rows=cur.fetchall(); require(len(rows)==1); prompt,review,attempt=rows[0]
                    require(attempt['human_binding']==event.human_binding
                            and attempt['actor_id']==event.actor_id and attempt['thread_id']==event.thread_id
                            and prompt['consumed_key'] is None)
                    # No fixture-only fallback: a real reserved attempt was required above.
                    cur.execute('select private.assert_content_ops_button_prompt_card_active(%s::uuid,%s::uuid,%s)',
                                (prompt['id'],review['id'],event.human_binding))
                    cur.execute('select clock_timestamp()'); now=_time(cur.fetchone()[0])
                    require(int(_time(attempt['started_at']).timestamp())<=event.prompt_date
                            <=int(_time(prompt['delivered_at']).timestamp())<=event.message_date<=now.timestamp()
                            and now.timestamp()-event.message_date<=1800)
                    cur.execute('''select action from private.content_ops_button_actions
                        where review_id=%s::uuid and idempotency_key=%s and actor_id=%s::uuid''',
                        (review['id'],prompt['edit_action_key'],event.actor_id))
                    require(cur.fetchone()==('edit_banner',))
                    cur.execute('''select to_jsonb(f),q.request_text from private.content_ops_banner_feedback f
                        join private.content_ops_banner_requests q on q.job_id=f.job_id where f.prompt_id=%s::uuid''',
                        (prompt['id'],))
                    prior=cur.fetchone(); reused=prior is not None
                    if reused:
                        saved,raw=prior
                        require(saved['operation_key']==event.operation_key
                                and saved['reply_sha256']==sha(event.replacement_text.encode()))
                        request=BannerRequest.decode(raw)
                    else:
                        cur.execute('''select to_jsonb(v),to_jsonb(s),to_jsonb(a),to_jsonb(b)
                            from public.content_versions v
                            join private.content_ops_banner_briefs b on b.content_version_id=v.id
                            join public.content_source_links l on l.workspace_id=v.workspace_id and l.content_item_id=v.content_item_id
                              and l.client_id=%s and l.position=0
                            join public.source_items s on s.id=l.source_item_id and s.workspace_id=l.workspace_id and s.client_id=l.client_id
                            join public.assets a on a.id::text=v.deliverables->>'primary_asset_id'
                              and a.workspace_id=v.workspace_id and a.content_item_id=v.content_item_id and a.content_version_id=v.id
                            where v.id=%s::uuid and v.workspace_id=%s::uuid and v.content_item_id=%s::uuid''',
                            (review['client_id'],review['content_version_id'],review['workspace_id'],review['content_item_id']))
                        rows=cur.fetchall(); require(len(rows)==1); version,source,asset,brief=rows[0]
                        require(brief['version_fingerprint']==review['version_fingerprint'])
                        request=BannerRequest(str(uuid4()),event.operation_key,private_snapshot(review,version,source,asset),
                            brief['headline'],brief['subtitle'],event.replacement_text,brief['logo_sha256'])
                        request.validate()
                        cur.execute('''insert into private.content_ops_banner_requests(job_id,card_id,review_id,actor_id,
                            human_binding,action_key,request_text,request_sha256) values(%s,%s,%s,%s,%s,%s,%s,%s)''',
                            (request.job_id,attempt['card_id'],review['id'],event.actor_id,event.human_binding,
                             prompt['edit_action_key'],request.encoded(),request.digest()))
                        cur.execute('''insert into private.content_ops_banner_feedback(prompt_id,operation_key,reply_sha256,job_id)
                            values(%s,%s,%s,%s)''',
                            (prompt['id'],event.operation_key,sha(event.replacement_text.encode()),request.job_id))
                    # In the SAME transaction validate exact source/version/card,
                    # current ACL, human, action, expiry and no approvals/publication.
                    # Any failure rolls back registration + consumption together.
                    with self._validator._locked(request,connection=connection) as locked:
                        require(locked[-1] is None)
                    receipt=feedback_receipt({'status':'banner_requested','job_id':request.job_id,
                        'request_sha256':request.digest(),'reused':reused,'execution_authorized':False})
            return receipt  # Only after commit ACK. Never enqueue/provider/send here.
        except Exception:
            raise BannerError('banner_feedback_unknown') from None
