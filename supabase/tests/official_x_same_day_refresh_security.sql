-- LOCAL SYNTHETIC ONLY. Persistent helper is used by the disposable concurrency
-- harness; the security cases below create no durable data and are rolled back.
create or replace function private.test_same_day_refresh_seed()
returns jsonb language plpgsql set search_path = '' as $$
declare
    w uuid:=gen_random_uuid(); f uuid:=gen_random_uuid(); old_source uuid:=gen_random_uuid();
    fresh_source uuid:=gen_random_uuid(); old_item uuid:=gen_random_uuid(); old_version uuid:=gen_random_uuid();
    old_job uuid:=gen_random_uuid(); day date:=pg_catalog.timezone('Asia/Seoul',statement_timestamp())::date;
begin
    insert into public.workspaces(id,name,slug) values(w,'Synthetic refresh fixture',w::text);
    insert into public.workspace_clients(workspace_id,client_id,display_name,active)
        values(w,'squid','Synthetic Squid',true);
    insert into public.source_feeds(id,workspace_id,client_id,provider,name,source_url,handle,
        poll_interval_minutes,last_polled_at,active)
        values(f,w,'squid','x','Synthetic official X','https://x.com/SquidRouter','@SquidRouter',
            15,statement_timestamp(),true);
    insert into public.source_items(id,workspace_id,client_id,source_feed_id,external_id,source_type,
        canonical_url,author_handle,published_at,body,media,source_hash)
        values(old_source,w,'squid',f,'900000000000000001','tweet',
            'https://x.com/SquidRouter/status/900000000000000001','@SquidRouter',
            statement_timestamp()-interval '3 hours','Synthetic earlier official source.','[]',old_source::text),
        (fresh_source,w,'squid',f,'900000000000000002','tweet',
            'https://x.com/SquidRouter/status/900000000000000002','@SquidRouter',
            statement_timestamp()-interval '1 hour','Synthetic latest official source.',
            '[{"type":"photo","media_key":"synthetic","url":"https://pbs.twimg.com/media/synthetic.png"}]',fresh_source::text);
    insert into public.content_items(id,workspace_id,client_id,content_kind,title,status)
        values(old_item,w,'squid','daily_news','Synthetic earlier card','needs_review');
    insert into public.content_versions(id,workspace_id,content_item_id,version_number,prompt_version,title,
        channel_copy,generation_meta,created_at)
        values(old_version,w,old_item,1,'synthetic@1','Synthetic earlier card',
            '{"telegram":"Synthetic earlier Korean review","x":"Synthetic earlier X review"}',
            jsonb_build_object('request_id',old_item,'mock_mode',false),statement_timestamp());
    update public.content_items set current_version_id=old_version where id=old_item;
    insert into public.jobs(id,workspace_id,client_id,job_kind,status,input,output,attempts,max_attempts,finished_at)
        values(old_job,w,'squid','generate','succeeded',jsonb_build_object(
            'workflow','official_x_review_draft_v1','kst_date',day,'source_item_ids',jsonb_build_array(old_source::text),
            'content_kind','daily_news','request_id',old_item,'source_content','Synthetic earlier official source.',
            'source_url','https://x.com/SquidRouter/status/900000000000000001','source_image_url','',
            'manual_only',false),jsonb_build_object('content_item_id',old_item,'content_version_id',old_version,
            'source_item_ids',jsonb_build_array(old_source::text),'completed_by','synthetic-natural'),1,3,statement_timestamp());
    insert into public.content_source_links(workspace_id,client_id,content_item_id,source_item_id,position)
        values(w,'squid',old_item,old_source,0);
    insert into private.official_x_source_state(workspace_id,client_id,source_item_id,queued_job_id,queued_at)
        values(w,'squid',old_source,old_job,statement_timestamp()),(w,'squid',fresh_source,null,null);
    insert into private.official_x_daily_slots(workspace_id,kst_date,client_id,slot,job_id)
        values(w,day,'squid',1,old_job);
    return jsonb_build_object('workspace_id',w,'client_id','squid','kst_date',day,'predecessor_job_id',old_job,
        'predecessor_content_item_id',old_item,'predecessor_content_version_id',old_version,
        'source_item_id',fresh_source,'release_sha',repeat('a',40));
end $$;
revoke all on function private.test_same_day_refresh_seed() from public,anon,authenticated,service_role;

begin;
set local request.jwt.claim.role='service_role';

create function private.test_same_day_refresh_queue(seed jsonb,refresh uuid,request uuid,expiry timestamptz)
returns jsonb language sql set search_path = '' as $$
    select public.queue_official_x_same_day_refresh((seed->>'workspace_id')::uuid,seed->>'client_id',
        (seed->>'kst_date')::date,(seed->>'predecessor_job_id')::uuid,
        (seed->>'predecessor_content_item_id')::uuid,(seed->>'predecessor_content_version_id')::uuid,
        (seed->>'source_item_id')::uuid,refresh,request,seed->>'release_sha',expiry)
$$;
create function private.test_same_day_refresh_claim(seed jsonb,q jsonb,worker text default 'synthetic-refresh')
returns jsonb language sql set search_path = '' as $$
    select public.claim_official_x_same_day_refresh((seed->>'workspace_id')::uuid,(q->>'refresh_id')::uuid,
        (q->>'job_id')::uuid,(q->>'request_id')::uuid,(q->>'source_item_id')::uuid,q->>'release_sha',worker,900)
$$;

-- Each case creates a new immutable version BEFORE normal completion. No
-- immutable-version trigger is disabled and no existing version is updated.
create function private.test_same_day_refresh_copy_case(target_case text)
returns jsonb language plpgsql set search_path = '' as $$
declare
    seed jsonb:=private.test_same_day_refresh_seed();
    w uuid:=(seed->>'workspace_id')::uuid;
    source uuid:=(seed->>'source_item_id')::uuid;
    refresh uuid:=gen_random_uuid(); request uuid:=gen_random_uuid();
    version uuid:=gen_random_uuid(); banner uuid:=gen_random_uuid();
    q jsonb; claimed jsonb; pack jsonb; inspected jsonb; job public.jobs%rowtype;
    title text:='Synthetic bounded title'; telegram_copy text:='Synthetic bounded Korean review';
    x_copy text:='Synthetic bounded X review'; pack_hash text; path text;
begin
    if target_case not in ('valid','title','telegram','x','style_pack') then
        raise exception 'synthetic_copy_case_invalid';
    end if;
    q:=private.test_same_day_refresh_queue(seed,refresh,request,statement_timestamp()+interval '30 minutes');
    claimed:=private.test_same_day_refresh_claim(seed,q);
    if claimed is null then raise exception 'synthetic_copy_case_claim_missing'; end if;
    select * into job from public.jobs where id=(q->>'job_id')::uuid;
    pack:=public.get_or_create_official_x_style_reference_pack(w,'squid',request,source,3);
    pack_hash:=pack->>'reference_pack_hash';
    -- Trailing spaces intentionally leave btrim lengths below the limit, so
    -- these cases prove the full immutable text is bounded, not silently trim.
    if target_case='title' then title:=title||repeat(' ',161); end if;
    if target_case='telegram' then telegram_copy:=telegram_copy||repeat(' ',3401); end if;
    if target_case='x' then x_copy:=x_copy||repeat(' ',1001); end if;
    if target_case='style_pack' then
        pack_hash:=case when pack_hash=repeat('f',32) then repeat('e',32) else repeat('f',32) end;
    end if;
    insert into public.content_items(id,workspace_id,client_id,content_kind,title,status)
        values(request,w,'squid','daily_news',title,'needs_review');
    insert into public.content_versions(id,workspace_id,content_item_id,version_number,prompt_version,title,
        content,channel_copy,generation_meta,deliverables,created_at)
        values(version,w,request,1,'synthetic@1',title,
            jsonb_build_object('source',jsonb_build_object('submitted_content',job.input->>'source_content',
                'url',job.input->>'source_url','type','tweet')),
            jsonb_build_object('telegram',telegram_copy,'x',x_copy),
            jsonb_build_object('request_id',request,'mock_mode',false,'style_reference_pack_hash',pack_hash),
            jsonb_build_object('primary_asset_id',banner),statement_timestamp());
    update public.content_items set current_version_id=version where id=request;
    path:=w::text||'/squid/'||banner::text||'/news-card.png';
    insert into public.assets(id,workspace_id,content_item_id,content_version_id,asset_kind,storage_bucket,
        storage_path,mime_type,byte_size,sha256,width,height,metadata)
        values(banner,w,request,version,'png','content-studio',path,'image/png',1024,repeat('c',64),1080,1080,
            '{"filename":"news-card.png"}');
    insert into storage.objects(bucket_id,name) values('content-studio',path);
    perform public.complete_review_draft_job(job.id,'synthetic-refresh',request,version);
    inspected:=public.inspect_official_x_same_day_refresh(w,refresh,version);
    if exists(select 1 from private.grok_qa_dispatch_outbox where workspace_id=w)
       or exists(select 1 from private.content_ops_review_outbox where workspace_id=w)
       or exists(select 1 from public.approvals where workspace_id=w)
       or exists(select 1 from public.publications where workspace_id=w) then
        raise exception 'synthetic_copy_case_created_outbox_or_authority';
    end if;
    return inspected;
end $$;

do $acl$
declare role_name text; routine text;
begin
    foreach role_name in array array['anon','authenticated','service_role'] loop
        if has_table_privilege(role_name,'private.official_x_same_day_refresh_requests','SELECT,INSERT,UPDATE,DELETE') then
            raise exception 'refresh_private_ledger_acl_leak';
        end if;
        foreach routine in array array[
            'public.queue_official_x_same_day_refresh(uuid,text,date,uuid,uuid,uuid,uuid,uuid,uuid,text,timestamp with time zone)',
            'public.claim_official_x_same_day_refresh(uuid,uuid,uuid,uuid,uuid,text,text,integer)',
            'public.inspect_official_x_same_day_refresh(uuid,uuid,uuid)'] loop
            if has_function_privilege(role_name,routine,'EXECUTE')<>(role_name='service_role') then
                raise exception 'refresh_rpc_acl_mismatch';
            end if;
        end loop;
        foreach routine in array array[
            'private.guard_official_x_same_day_refresh_request()',
            'private.official_x_same_day_refresh_source_valid(uuid,text,uuid,timestamp with time zone)',
            'private.official_x_same_day_refresh_predecessor_valid(uuid,text,date,uuid,uuid,uuid,uuid)',
            'private.official_x_same_day_refresh_undelivered(uuid,uuid)',
            'private.official_x_same_day_refresh_receipt(uuid,boolean)',
            'private.official_x_same_day_refresh_source_sha256(uuid)',
            'private.official_x_same_day_refresh_input_valid(uuid)'] loop
            if has_function_privilege(role_name,routine,'EXECUTE') then
                raise exception 'refresh_private_helper_acl_leak';
            end if;
        end loop;
    end loop;
    if not exists(select 1 from pg_class where oid='private.official_x_same_day_refresh_requests'::regclass
        and relrowsecurity and relforcerowsecurity) then raise exception 'refresh_rls_missing'; end if;
end $acl$;

do $security$
declare
    seed jsonb:=private.test_same_day_refresh_seed(); q jsonb; replay jsonb; claimed jsonb; inspected jsonb;
    w uuid:=(seed->>'workspace_id')::uuid; s uuid:=(seed->>'source_item_id')::uuid;
    old_item uuid:=(seed->>'predecessor_content_item_id')::uuid; old_job uuid:=(seed->>'predecessor_job_id')::uuid;
    old_version uuid:=(seed->>'predecessor_content_version_id')::uuid;
    refresh uuid:=gen_random_uuid(); request uuid:=gen_random_uuid(); version uuid:=gen_random_uuid();
    banner uuid:=gen_random_uuid(); expiry timestamptz:=statement_timestamp()+interval '30 minutes';
    original jsonb; current_rows jsonb; source_record public.source_items%rowtype;
    job public.jobs%rowtype; pack jsonb; path text; rejected boolean;
    second_seed jsonb; second_q jsonb; fake_seed jsonb; fake_source uuid;
begin
    select jsonb_build_object('job',to_jsonb(j),'item',to_jsonb(i),'version',to_jsonb(v),
        'slot',to_jsonb(slot),'state',to_jsonb(state)) into original
        from public.jobs j join public.content_items i on i.id=old_item
        join public.content_versions v on v.id=old_version
        join private.official_x_daily_slots slot on slot.job_id=j.id
        join private.official_x_source_state state on state.queued_job_id=j.id where j.id=old_job;
    -- Denied app identity is checked even when function privileges are bypassed
    -- by the synthetic database owner.
    perform set_config('request.jwt.claim.role','authenticated',true);
    rejected:=false;
    begin perform private.test_same_day_refresh_queue(seed,refresh,request,expiry);
    exception when insufficient_privilege then rejected:=true; end;
    if not rejected then raise exception 'refresh_auth_role_not_enforced'; end if;
    perform set_config('request.jwt.claim.role','service_role',true);

    rejected:=false;
    begin perform private.test_same_day_refresh_queue(seed,refresh,request,statement_timestamp()-interval '1 minute');
    exception when invalid_parameter_value then rejected:=true; end;
    if not rejected then raise exception 'refresh_expired_queue_accepted'; end if;
    rejected:=false;
    begin perform private.test_same_day_refresh_queue(seed,refresh,request,statement_timestamp()+interval '3 hours');
    exception when invalid_parameter_value then rejected:=true; end;
    if not rejected then raise exception 'refresh_overlong_expiry_accepted'; end if;
    rejected:=false;
    begin perform private.test_same_day_refresh_queue(seed||jsonb_build_object('kst_date',
        ((seed->>'kst_date')::date-1)::text),refresh,request,expiry);
    exception when invalid_parameter_value then rejected:=true; end;
    if not rejected then raise exception 'refresh_wrong_kst_date_accepted'; end if;
    -- Boundary/future/poll/body checks are exercised through actual queue RPCs.
    begin
        update public.source_items set published_at=statement_timestamp()-interval '24 hours' where id=s;
        rejected:=false;
        begin perform private.test_same_day_refresh_queue(seed,refresh,request,expiry);
        exception when check_violation then rejected:=true; end;
        if not rejected then raise exception 'refresh_stale_source_accepted'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    begin
        update public.source_items set published_at=statement_timestamp()+interval '1 hour' where id=s;
        rejected:=false;
        begin perform private.test_same_day_refresh_queue(seed,refresh,request,expiry);
        exception when check_violation then rejected:=true; end;
        if not rejected then raise exception 'refresh_future_source_accepted'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    begin
        update public.source_feeds set last_polled_at=statement_timestamp()-interval '16 minutes' where workspace_id=w;
        rejected:=false;
        begin perform private.test_same_day_refresh_queue(seed,refresh,request,expiry);
        exception when check_violation then rejected:=true; end;
        if not rejected then raise exception 'refresh_stale_poll_accepted'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    begin
        update public.source_items set body=repeat('x',20001) where id=s;
        rejected:=false;
        begin perform private.test_same_day_refresh_queue(seed,refresh,request,expiry);
        exception when check_violation then rejected:=true; end;
        if not rejected then raise exception 'refresh_oversize_source_accepted'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;

    q:=private.test_same_day_refresh_queue(seed,refresh,request,expiry);
    replay:=private.test_same_day_refresh_queue(seed,refresh,request,expiry);
    if q->>'status'<>'queued' or q->'reused'<>'false'::jsonb or replay->'reused'<>'true'::jsonb
       or replay->>'job_id'<>q->>'job_id'
       or (select count(*) from jsonb_object_keys(q))<>10
       or q ?| array['input','source_content','source_url','source_image_url'] then
        raise exception 'refresh_queue_receipt_unbounded_or_not_idempotent';
    end if;
    rejected:=false;
    begin perform private.test_same_day_refresh_queue(seed,gen_random_uuid(),gen_random_uuid(),expiry);
    exception when unique_violation then rejected:=true; end;
    if not rejected then raise exception 'refresh_second_same_client_day_accepted'; end if;
    rejected:=false;
    begin perform private.test_same_day_refresh_queue(seed||jsonb_build_object('release_sha',repeat('b',40)),refresh,request,expiry);
    exception when unique_violation then rejected:=true; end;
    if not rejected then raise exception 'refresh_same_id_binding_changed'; end if;
    if public.claim_review_draft_job(w,'synthetic-natural',900) is not null then
        raise exception 'natural_worker_claimed_manual_refresh';
    end if;
    rejected:=false;
    begin perform private.test_same_day_refresh_claim(seed,q||jsonb_build_object('release_sha',repeat('b',40)));
    exception when check_violation then rejected:=true; end;
    if not rejected then raise exception 'refresh_wrong_release_claim_accepted'; end if;
    rejected:=false;
    begin perform private.test_same_day_refresh_claim(seed,q||jsonb_build_object('job_id',gen_random_uuid()));
    exception when check_violation then rejected:=true; end;
    if not rejected then raise exception 'refresh_wrong_job_claim_accepted'; end if;
    begin
        update public.jobs set input=jsonb_set(input,'{source_content}','"Changed body"') where id=(q->>'job_id')::uuid;
        if private.test_same_day_refresh_claim(seed,q) is not null then raise exception 'input_drift_rejected'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    begin
        update public.source_items set body='Changed recorded source body.' where id=s;
        if private.test_same_day_refresh_claim(seed,q) is not null then raise exception 'source_drift_rejected'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    begin
        update public.source_items set media='[]' where id=s;
        if private.test_same_day_refresh_claim(seed,q) is not null then raise exception 'source_media_drift_rejected'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    begin
        update public.content_items set status='archived' where id=old_item;
        if private.test_same_day_refresh_claim(seed,q) is not null then raise exception 'predecessor_drift_rejected'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    begin
        select * into source_record from public.source_items where id=s;
        insert into public.source_items(workspace_id,client_id,source_feed_id,external_id,source_type,
            canonical_url,author_handle,published_at,body,source_hash)
            values(w,'squid',source_record.source_feed_id,'900000000000000003','tweet',
                'https://x.com/SquidRouter/status/900000000000000003','@SquidRouter',
                statement_timestamp()-interval '5 minutes','Synthetic newly arrived source.',gen_random_uuid()::text);
        if private.test_same_day_refresh_claim(seed,q) is not null then raise exception 'newer_source_claim_not_held'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;

    claimed:=private.test_same_day_refresh_claim(seed,q);
    if claimed is null or claimed->>'status'<>'running' or claimed->>'execution_plane'<>'studio_sync'
       or claimed->'input'->'manual_only'<>'true'::jsonb
       or claimed->>'attempts'<>'1' or claimed->>'max_attempts'<>'1'
       or (claimed->>'lease_expires_at')::timestamptz>expiry then
        raise exception 'refresh_exact_claim_contract_invalid';
    end if;
    if private.test_same_day_refresh_claim(seed,q) is not null
       or private.test_same_day_refresh_claim(seed,q,'synthetic-other') is not null then
        raise exception 'consumed_claim_never_replays';
    end if;
    rejected:=false;
    begin update private.official_x_same_day_refresh_requests set claimed_at=null,claimed_by=null where refresh_id=refresh;
    exception when object_not_in_prerequisite_state then rejected:=true; end;
    if not rejected then raise exception 'refresh_receipt_reset_accepted'; end if;
    rejected:=false;
    begin delete from private.official_x_same_day_refresh_requests where refresh_id=refresh;
    exception when object_not_in_prerequisite_state then rejected:=true; end;
    if not rejected then raise exception 'refresh_receipt_deletion_accepted'; end if;

    select * into job from public.jobs where id=(q->>'job_id')::uuid;
    pack:=public.get_or_create_official_x_style_reference_pack(w,'squid',request,s,3);
    insert into public.content_items(id,workspace_id,client_id,content_kind,title,status)
        values(request,w,'squid','daily_news','Synthetic latest Korean card','needs_review');
    insert into public.content_versions(id,workspace_id,content_item_id,version_number,prompt_version,title,
        content,channel_copy,generation_meta,deliverables,created_at)
        values(version,w,request,1,'synthetic@1','Synthetic latest Korean card',
            jsonb_build_object('source',jsonb_build_object('submitted_content',job.input->>'source_content',
                'url',job.input->>'source_url','type','tweet')),
            '{"telegram":"Synthetic latest Korean review","x":"Synthetic latest X review"}',
            jsonb_build_object('request_id',request,'mock_mode',false,
                'style_reference_pack_hash',pack->>'reference_pack_hash'),
            jsonb_build_object('primary_asset_id',banner),statement_timestamp());
    update public.content_items set current_version_id=version where id=request;
    begin
        update public.jobs set input=jsonb_set(input,'{source_content}','"Poisoned after exact claim."') where id=job.id;
        rejected:=false;
        begin perform public.complete_review_draft_job(job.id,'synthetic-refresh',request,version);
        exception when check_violation then rejected:=true; end;
        if not rejected or exists(select 1 from private.grok_qa_dispatch_outbox
            where workspace_id=w and content_item_id=request) then
            raise exception 'known_refresh_poison_did_not_fail_closed';
        end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    perform public.complete_review_draft_job(job.id,'synthetic-refresh',request,version);
    if exists(select 1 from private.grok_qa_dispatch_outbox where workspace_id=w and content_item_id=request) then
        raise exception 'refresh_no_grok_outbox';
    end if;
    if public.inspect_official_x_same_day_refresh(w,refresh,version)->>'status'<>'refresh_not_ready' then
        raise exception 'refresh_readiness_requires_canonical_banner';
    end if;
    path:=w::text||'/squid/'||banner::text||'/news-card.png';
    insert into public.assets(id,workspace_id,content_item_id,content_version_id,asset_kind,storage_bucket,
        storage_path,mime_type,byte_size,sha256,width,height,metadata)
        values(banner,w,request,version,'png','content-studio',path,'image/png',1024,repeat('b',64),1080,1080,
            '{"filename":"news-card.png"}');
    insert into storage.objects(bucket_id,name) values('content-studio',path);
    inspected:=public.inspect_official_x_same_day_refresh(w,refresh,version);
    if inspected->>'status'<>'refresh_ready' or inspected->>'banner_sha256'<>repeat('b',64)
       or inspected->'execution_authorized'<>'false'::jsonb or inspected->'delivery_authorized'<>'false'::jsonb
       or inspected ?| array['input','source_content','source_url','source_image_url','telegram_copy','x_copy','storage_path'] then
        raise exception 'refresh_advisory_readiness_contract_invalid';
    end if;
    -- Valid control has exactly the same source, producer, canonical asset and
    -- completion path as each negative case; only the tested field differs.
    if private.test_same_day_refresh_copy_case('valid')->>'status'<>'refresh_ready' then
        raise exception 'refresh_copy_fixture_valid_control_not_ready';
    end if;
    if private.test_same_day_refresh_copy_case('title')->>'status'<>'refresh_not_ready' then
        raise exception 'refresh_oversized_immutable_title_accepted';
    end if;
    if private.test_same_day_refresh_copy_case('telegram')->>'status'<>'refresh_not_ready' then
        raise exception 'refresh_oversized_immutable_telegram_accepted';
    end if;
    if private.test_same_day_refresh_copy_case('x')->>'status'<>'refresh_not_ready' then
        raise exception 'refresh_oversized_immutable_x_accepted';
    end if;
    if private.test_same_day_refresh_copy_case('style_pack')->>'status'<>'refresh_not_ready' then
        raise exception 'refresh_mismatched_immutable_style_pack_accepted';
    end if;
    if private.content_ops_review_candidate(w,request,version) is not null
       or public.content_ops_reconcile_daily(w,version)<>0
       or exists(select 1 from private.content_ops_review_outbox where workspace_id=w) then
        raise exception 'refresh_entered_natural_delivery_route';
    end if;
    if private.test_same_day_refresh_claim(seed,q) is not null then raise exception 'completed_refresh_reclaimed'; end if;
    begin
        update public.jobs set input=input||'{"unexpected_extra":true}' where id=job.id;
        if public.inspect_official_x_same_day_refresh(w,refresh,version)->>'status'<>'refresh_not_ready' then
            raise exception 'readiness_input_drift_accepted'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    begin
        insert into public.content_source_links(workspace_id,client_id,content_item_id,source_item_id,position)
            select w,'squid',request,source_item_id,0 from public.content_source_links where content_item_id=old_item;
        if public.inspect_official_x_same_day_refresh(w,refresh,version)->>'status'<>'refresh_not_ready' then
            raise exception 'readiness_duplicate_source_link_accepted'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    begin
        update public.assets set storage_path=path||'.extra' where id=banner;
        if public.inspect_official_x_same_day_refresh(w,refresh,version)->>'status'<>'refresh_not_ready' then
            raise exception 'readiness_noncanonical_banner_accepted'; end if;
        raise exception 'probe_rollback' using errcode='PZ001';
    exception when sqlstate 'PZ001' then null; end;
    select jsonb_build_object('job',to_jsonb(j),'item',to_jsonb(i),'version',to_jsonb(v),
        'slot',to_jsonb(slot),'state',to_jsonb(state)) into current_rows
        from public.jobs j join public.content_items i on i.id=old_item
        join public.content_versions v on v.id=old_version
        join private.official_x_daily_slots slot on slot.job_id=j.id
        join private.official_x_source_state state on state.queued_job_id=j.id where j.id=old_job;
    if current_rows is distinct from original then raise exception 'natural_rows_unchanged'; end if;

    -- Natural and fake-marker completions still execute the original QA path.
    insert into public.event_log(workspace_id,entity_type,entity_id,event_type,data)
        select w,'content_item',old_item,'official_x_review_draft_completed',
            jsonb_build_object('job_id',old_job,'content_version_id',old_version,
                'source_item_ids',j.output->'source_item_ids') from public.jobs j where j.id=old_job;
    if (select count(*) from private.grok_qa_dispatch_outbox where workspace_id=w and content_item_id=old_item)<>1 then
        raise exception 'natural_grok_outbox_preserved';
    end if;
    fake_seed:=private.test_same_day_refresh_seed();
    update public.jobs set input=input||jsonb_build_object('manual_only',true,'same_day_refresh_id',gen_random_uuid()),
        attempts=1,max_attempts=1 where id=(fake_seed->>'predecessor_job_id')::uuid;
    insert into public.event_log(workspace_id,entity_type,entity_id,event_type,data)
        select (fake_seed->>'workspace_id')::uuid,'content_item',(fake_seed->>'predecessor_content_item_id')::uuid,
            'official_x_review_draft_completed',jsonb_build_object('job_id',j.id,
                'content_version_id',fake_seed->>'predecessor_content_version_id','source_item_ids',j.output->'source_item_ids')
            from public.jobs j where j.id=(fake_seed->>'predecessor_job_id')::uuid;
    if (select count(*) from private.grok_qa_dispatch_outbox
        where workspace_id=(fake_seed->>'workspace_id')::uuid)<>1 then raise exception 'fake_marker_not_suppressed'; end if;

    second_seed:=private.test_same_day_refresh_seed();
    second_q:=private.test_same_day_refresh_queue(second_seed,gen_random_uuid(),gen_random_uuid(),expiry);
    claimed:=private.test_same_day_refresh_claim(second_seed,second_q);
    perform public.fail_review_draft_job((second_q->>'job_id')::uuid,'synthetic-refresh',
        'synthetic_failure','Synthetic one-shot failure.',false,null);
    if private.test_same_day_refresh_claim(second_seed,second_q) is not null
       or public.claim_review_draft_job((second_seed->>'workspace_id')::uuid,'synthetic-natural',900) is not null
       or (select status from public.jobs where id=(second_q->>'job_id')::uuid)<>'failed' then
        raise exception 'failed_refresh_retried';
    end if;
    if exists(select 1 from public.approvals where workspace_id=w)
       or exists(select 1 from public.publications where workspace_id=w)
       or exists(select 1 from public.jobs where workspace_id=w and job_kind in ('publish','figma_export')) then
        raise exception 'refresh_created_approval_publication_or_send';
    end if;
end $security$;
rollback;
