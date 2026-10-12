-- Disposable synthetic natural jobs only; all mutations roll back.
begin;
select set_config('request.jwt.claim.role','service_role',true);
do $$
<<fixture>>
declare w uuid; j uuid; s uuid; f uuid; r uuid; v uuid; a uuid;
    c text; handle text; result jsonb; source_url text; body text:='Synthetic official source body';
    media_url text:='https://pbs.twimg.com/media/synthetic.jpg?name=orig';
    scenario integer; context_count bigint; before_jobs bigint; queued integer; replayed integer;
begin
    if public.get_quality_banner_generation_context(gen_random_uuid(),'yellow',gen_random_uuid()) is not null then
        raise exception 'empty_context_should_not_exist'; end if;
    foreach c in array array['yellow','babylon','origintrail'] loop
        w:=gen_random_uuid();j:=gen_random_uuid();s:=gen_random_uuid();f:=gen_random_uuid();
        r:=gen_random_uuid();v:=gen_random_uuid();a:=gen_random_uuid();
        handle:=case c when 'yellow' then '@Yellow' when 'babylon' then '@babylonlabs_io' else '@origin_trail' end;
        source_url:='https://x.com/'||substring(handle from 2)||'/status/123456789';
        insert into public.workspaces(id,name,slug) values(w,'Synthetic quality source','quality-'||w::text);
        insert into public.workspace_clients(workspace_id,client_id,display_name) values(w,c,'Synthetic client');
        insert into public.source_feeds(id,workspace_id,client_id,provider,name,handle,
            poll_interval_minutes,last_polled_at,active) values(f,w,c,'x','Synthetic feed',handle,15,statement_timestamp(),true);
        insert into public.source_items(id,workspace_id,client_id,source_feed_id,external_id,source_type,
            canonical_url,author_handle,published_at,body,source_hash,media)
            values(s,w,c,f,'123456789','tweet',source_url,handle,statement_timestamp()-interval '1 hour',
                body,repeat('a',64),jsonb_build_array(jsonb_build_object('type','photo','url',media_url)));
        insert into public.jobs(id,workspace_id,client_id,job_kind,status,attempts,max_attempts,
            locked_by,locked_at,lease_expires_at,input,output) values(j,w,c,'generate','running',1,3,
            'synthetic-worker',statement_timestamp(),statement_timestamp()+interval '15 minutes',
            jsonb_build_object('workflow','official_x_review_draft_v1','content_kind','daily_news',
                'manual_only',false,'kst_date',timezone('Asia/Seoul',statement_timestamp())::date,
                'request_id',r::text,'source_item_ids',jsonb_build_array(s::text),
                'source_content',body,'source_url',source_url,'source_image_url',media_url),
            jsonb_build_object('execution_plane','studio_sync'));
        insert into private.official_x_daily_slots(workspace_id,kst_date,client_id,slot,job_id)
            values(w,timezone('Asia/Seoul',statement_timestamp())::date,c,1,j);
        insert into private.official_x_source_state(workspace_id,client_id,source_item_id,queued_job_id,queued_at)
            values(w,c,s,j,statement_timestamp());
        insert into private.official_x_style_reference_packs(workspace_id,client_id,request_id,
            primary_source_item_id,style_references,reference_pack_hash) values(w,c,r,s,'[]',repeat('b',32));
        select count(*) into before_jobs from public.jobs where workspace_id=w;
        result:=public.get_quality_banner_generation_context(w,c,r);
        select count(*) into context_count from jsonb_object_keys(result);
        if result is null or context_count<>10 or result->>'request_id'<>r::text
            or result->>'job_id'<>j::text or result->>'workspace_id'<>w::text
            or result->>'client_id'<>c or result->>'source_item_id'<>s::text
            or result->>'source_content'<>body or result->>'source_url'<>source_url
            or result->>'source_image_url'<>media_url
            or result->>'source_sha256'<>encode(extensions.digest(convert_to(body,'UTF8'),'sha256'),'hex') then
            raise exception 'natural_context_identity_invalid'; end if;
        if public.get_quality_banner_generation_context(w,'squid',r) is not null
            or public.get_quality_banner_generation_context(gen_random_uuid(),c,r) is not null
            or public.get_quality_banner_generation_context(w,c,gen_random_uuid()) is not null then
            raise exception 'wrong_scope_context_admitted'; end if;
        for scenario in 1..9 loop
            begin
                case scenario
                    when 1 then update public.source_items set published_at=statement_timestamp()-interval '25 hours' where id=s;
                    when 2 then update public.source_feeds set last_polled_at=statement_timestamp()-interval '31 minutes' where id=f;
                    when 3 then update public.source_feeds set active=false where id=f;
                    when 4 then update public.jobs set lease_expires_at=statement_timestamp()-interval '1 second' where id=j;
                    when 5 then update public.jobs set input=jsonb_set(input,'{manual_only}','true') where id=j;
                    when 6 then update public.jobs set input=jsonb_set(input,'{source_image_url}','"https://pbs.twimg.com/media/not-attached.jpg"') where id=j;
                    when 7 then insert into public.jobs(workspace_id,client_id,job_kind,status,input)
                        select workspace_id,client_id,job_kind,'failed',input from public.jobs where id=j;
                    when 8 then insert into public.source_items(workspace_id,client_id,source_feed_id,external_id,source_type,
                        canonical_url,author_handle,published_at,body,source_hash) values(w,c,f,'987654321','tweet',
                        'https://x.com/'||substring(handle from 2)||'/status/987654321',handle,
                        statement_timestamp()-interval '1 minute','Newer synthetic source',repeat('c',64));
                    when 9 then update public.jobs set input=jsonb_set(input,'{source_content}','"Changed source text"') where id=j;
                end case;
                if public.get_quality_banner_generation_context(w,c,r) is not null then
                    raise exception 'invalid_natural_context_admitted: %',scenario; end if;
                raise exception 'rollback_synthetic_case' using errcode='P7701';
            exception when sqlstate 'P7701' then null;end;
        end loop;
        if (select count(*) from public.jobs where workspace_id=w)<>before_jobs
            or exists(select 1 from public.approvals where workspace_id=w)
            or exists(select 1 from public.publications where workspace_id=w) then
            raise exception 'readonly_context_changed_state'; end if;

        -- Actual completion of the SAME natural job makes the current canonical
        -- quality version eligible for the existing INITIAL review reconciler.
        insert into public.content_items(id,workspace_id,client_id,content_kind,title,status)
            values(r,w,c,'daily_news','한국어 공식 공지','needs_review');
        insert into public.content_versions(id,workspace_id,content_item_id,version_number,prompt_version,
            title,content,generation_meta,deliverables,channel_copy)
            values(v,w,r,1,'official-reference-sunburst-max@1','한국어 공식 공지',
                jsonb_build_object('spec',jsonb_build_object('quality_banner',jsonb_build_object('policy','official-reference-sunburst-max@1'))),
                jsonb_build_object('mock_mode',false,'request_id',r::text),
                jsonb_build_object('primary_asset_id',a::text),jsonb_build_object('telegram','한국어 공지 검수','x','한국어 X 검수'));
        update public.content_items set current_version_id=v where id=r;
        insert into public.assets(id,workspace_id,content_item_id,content_version_id,asset_kind,storage_bucket,
            storage_path,mime_type,byte_size,sha256,width,height,metadata)
            values(a,w,r,v,'png','content-studio',w::text||'/'||c||'/'||a::text||'/news-card.png',
                'image/png',5000000,repeat('d',64),2880,2880,'{"filename":"news-card.png"}');
        insert into storage.buckets(id,name) values('content-studio','content-studio') on conflict do nothing;
        insert into storage.objects(bucket_id,name) values('content-studio',w::text||'/'||c||'/'||a::text||'/news-card.png');
        perform public.complete_review_draft_job(j,'synthetic-worker',r,v);
        queued:=public.content_ops_reconcile_daily(w,v);
        replayed:=public.content_ops_reconcile_daily(w,v);
        if queued<>1 or replayed<>0
            or not exists(select 1 from private.content_ops_review_outbox where workspace_id=w
                and content_item_id=r and content_version_id=v and generate_job_id=j and source_item_id=s) then
            raise exception 'natural_quality_version_not_reconciled_once: queued=%, replayed=%, client=%',queued,replayed,c; end if;
        if (select count(*) from public.jobs where workspace_id=w)<>1
            or exists(select 1 from public.approvals where workspace_id=w)
            or exists(select 1 from public.publications where workspace_id=w) then
            raise exception 'quality_completion_created_extra_job_or_public_action'; end if;
    end loop;
    perform set_config('request.jwt.claim.role','authenticated',true);
    begin perform public.get_quality_banner_generation_context(w,c,r);
        raise exception 'untrusted_role_read_context';
    exception when insufficient_privilege then null;end;
end $$;
rollback;
