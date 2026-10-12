-- Synthetic natural generation -> current version -> existing initial outbox.
begin;
select set_config('request.jwt.claim.role','service_role',true);
-- The harness derives this value from the actual Python renderer POLICY.
select set_config('coineasy.test_quality_banner_policy', :'quality_banner_policy', true);
do $$
declare w uuid:=gen_random_uuid(); j uuid; s uuid; f uuid; r uuid; v uuid; a uuid;
    c text; handle text; source_url text; card jsonb; token uuid; seen text[]:='{}';
    body text:='Synthetic official source'; q jsonb; slot_index integer:=0; scenario integer;
    active_policy text:=current_setting('coineasy.test_quality_banner_policy'); fixture_policy text;
begin
    if active_policy='official-reference-sunburst-max@1' then raise exception 'obsolete_test_policy'; end if;
    foreach fixture_policy in array array['official-reference-sunburst-max@1',active_policy] loop
    w:=gen_random_uuid();slot_index:=0;seen:='{}';
    insert into public.workspaces(id,name,slug) values(w,'Synthetic YBO','ybo-'||w::text);
    foreach c in array array['yellow','babylon','origintrail','squid'] loop
        slot_index:=slot_index+1;
        j:=gen_random_uuid();s:=gen_random_uuid();f:=gen_random_uuid();r:=gen_random_uuid();v:=gen_random_uuid();a:=gen_random_uuid();
        handle:=case c when 'yellow' then '@Yellow' when 'babylon' then '@babylonlabs_io'
            when 'origintrail' then '@origin_trail' else '@SquidRouter' end;
        source_url:='https://x.com/'||substring(handle from 2)||'/status/123456789';
        insert into public.workspace_clients(workspace_id,client_id,display_name) values(w,c,'Synthetic client');
        insert into public.source_feeds(id,workspace_id,client_id,provider,name,handle,poll_interval_minutes,last_polled_at,active)
            values(f,w,c,'x','Synthetic feed',handle,15,statement_timestamp(),true);
        insert into public.source_items(id,workspace_id,client_id,source_feed_id,external_id,source_type,
            canonical_url,author_handle,published_at,body,source_hash,media)
            values(s,w,c,f,'123456789','tweet',source_url,handle,statement_timestamp()-interval '1 hour',body,repeat('a',64),
                '[{"type":"photo","url":"https://pbs.twimg.com/media/synthetic.jpg?name=orig"}]');
        insert into public.jobs(id,workspace_id,client_id,job_kind,status,attempts,max_attempts,
            locked_by,locked_at,lease_expires_at,input,output)
            values(j,w,c,'generate','running',1,3,'synthetic-worker',statement_timestamp(),statement_timestamp()+interval '15 minutes',
                jsonb_build_object('workflow','official_x_review_draft_v1','content_kind','daily_news','manual_only',false,
                    'kst_date',timezone('Asia/Seoul',statement_timestamp())::date,'request_id',r::text,
                    'source_item_ids',jsonb_build_array(s::text),'source_content',body,'source_url',source_url,
                    'source_image_url','https://pbs.twimg.com/media/synthetic.jpg?name=orig'),'{"execution_plane":"studio_sync"}');
        insert into private.official_x_daily_slots(workspace_id,kst_date,client_id,slot,job_id)
            values(w,timezone('Asia/Seoul',statement_timestamp())::date,c,slot_index,j);
        insert into private.official_x_source_state(workspace_id,client_id,source_item_id,queued_job_id,queued_at)
            values(w,c,s,j,statement_timestamp());
        insert into private.official_x_style_reference_packs(workspace_id,client_id,request_id,primary_source_item_id,style_references,reference_pack_hash)
            values(w,c,r,s,'[]',repeat('b',32));
        q:=jsonb_build_object('policy',fixture_policy,'request_sha256',repeat('c',64),
            'png_sha256',repeat('d',64),'width',2880,'height',2880);
        insert into public.content_items(id,workspace_id,client_id,content_kind,title,status)
            values(r,w,c,'daily_news','한국어 공식 공지','needs_review');
        insert into public.content_versions(id,workspace_id,content_item_id,version_number,prompt_version,title,
            content,generation_meta,deliverables,channel_copy)
            values(v,w,r,1,fixture_policy,'한국어 공식 공지',jsonb_build_object('spec',jsonb_build_object('quality_banner',q)),
                jsonb_build_object('request_id',r::text,'mock_mode',false,'renderer','higgsfield-sunburst-max','quality_banner',q),
                jsonb_build_object('primary_asset_id',a::text),'{"telegram":"한국어 공지 전문","x":"한국어 X 전문"}');
        update public.content_items set current_version_id=v where id=r;
        insert into public.assets(id,workspace_id,content_item_id,content_version_id,asset_kind,storage_bucket,storage_path,
            mime_type,byte_size,sha256,width,height,metadata)
            values(a,w,r,v,'png','content-studio',w::text||'/'||c||'/'||a::text||'/news-card.png',
                'image/png',5000000,repeat('d',64),2880,2880,'{"filename":"news-card.png"}');
        insert into storage.buckets(id,name) values('content-studio','content-studio') on conflict do nothing;
        insert into storage.objects(bucket_id,name) values('content-studio',w::text||'/'||c||'/'||a::text||'/news-card.png');
        if public.content_ops_reconcile_daily_ybo(w)<>0 then raise exception 'unfinished_job_admitted'; end if;
        perform public.complete_review_draft_job(j,'synthetic-worker',r,v);
        if c<>'squid' then
            if fixture_policy=active_policy then
                if public.content_ops_reconcile_daily_ybo(w)<>1 then raise exception 'natural_fixture_not_eligible'; end if;
            else
                if public.content_ops_reconcile_daily_ybo(w)<>0 then raise exception 'obsolete_policy_reconciled'; end if;
                -- Even an already queued legacy card must not be claimed by the new scope.
                if public.content_ops_reconcile_daily(w,v)<>1 then raise exception 'legacy_fixture_not_eligible'; end if;
            end if;
        end if;
    end loop;
    -- Legacy reconciler can enqueue Squid, but the scoped claimant must never consume it.
    perform public.content_ops_reconcile_daily(w,v);
    if public.content_ops_reconcile_daily_ybo(w)<>0 then raise exception 'duplicate_daily_outbox'; end if;
    if fixture_policy<>active_policy then
        if public.content_ops_claim_daily_ybo(w,gen_random_uuid()) is not null
            or (select count(*) from private.content_ops_review_outbox where workspace_id=w and status='pending')<>4 then
            raise exception 'obsolete_policy_claimed_or_mutated'; end if;
        continue;
    end if;
    for scenario in 1..4 loop
        begin
            case scenario
                when 1 then update public.source_items set published_at=statement_timestamp()-interval '25 hours'
                    where workspace_id=w and client_id<>'squid';
                when 2 then update public.assets set width=2879 where workspace_id=w and content_item_id in (
                    select id from public.content_items where workspace_id=w and client_id<>'squid');
                when 3 then update public.assets set sha256=repeat('e',64) where workspace_id=w and content_item_id in (
                    select id from public.content_items where workspace_id=w and client_id<>'squid');
                when 4 then update public.jobs set status='failed' where workspace_id=w and client_id<>'squid';
            end case;
            if public.content_ops_claim_daily_ybo(w,gen_random_uuid()) is not null then
                raise exception 'invalid_quality_or_natural_claim: %',scenario; end if;
            raise exception 'rollback_case' using errcode='P7701';
        exception when sqlstate 'P7701' then null;end;
    end loop;
    for i in 1..3 loop
        token:=gen_random_uuid();card:=public.content_ops_claim_daily_ybo(w,token);
        if card is null or card->>'client_id'='squid' or card->>'client_id'=any(seen) then
            raise exception 'wrong_or_duplicate_client_claim'; end if;
        seen:=array_append(seen,card->>'client_id');
        if public.content_ops_claim_daily_ybo(w,token) is not null then raise exception 'reused_token_replayed'; end if;
    end loop;
    if public.content_ops_claim_daily_ybo(w,gen_random_uuid()) is not null
        or public.content_ops_reconcile_daily_ybo(w)<>0 then raise exception 'restart_resends_or_requeues'; end if;
    if not exists(select 1 from private.content_ops_review_outbox where workspace_id=w and client_id='squid' and status='pending') then
        raise exception 'squid_touched_by_ybo'; end if;
    if (select count(*) from public.jobs where workspace_id=w)<>4
        or exists(select 1 from public.approvals where workspace_id=w)
        or exists(select 1 from public.publications where workspace_id=w) then raise exception 'unexpected_mutation'; end if;
    if private.content_ops_ybo_quality_version(w,v) then raise exception 'squid_quality_scope_admitted'; end if;
    if has_function_privilege('anon','public.content_ops_reconcile_daily_ybo(uuid)','EXECUTE')
        or has_function_privilege('authenticated','public.content_ops_claim_daily_ybo(uuid,uuid)','EXECUTE')
        or has_function_privilege('service_role','private.content_ops_ybo_quality_version(uuid,uuid)','EXECUTE')
        or not has_function_privilege('service_role','public.content_ops_claim_daily_ybo(uuid,uuid)','EXECUTE') then
        raise exception 'ybo_acl_invalid'; end if;
    perform set_config('request.jwt.claim.role','authenticated',true);
    begin perform public.content_ops_reconcile_daily_ybo(w);raise exception 'untrusted_reconcile';
    exception when insufficient_privilege then null;end;
    begin perform public.content_ops_claim_daily_ybo(w,gen_random_uuid());raise exception 'untrusted_claim';
    exception when insufficient_privilege then null;end;
    end loop;
end $$;
rollback;
