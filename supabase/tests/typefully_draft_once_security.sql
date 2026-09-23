-- Disposable-schema ACL and fail-closed smoke. No provider I/O or durable data.
begin;
do $test$
declare
    relation_name text;
    function_name text;
    role_name text;
    before_count bigint;
begin
    foreach relation_name in array array[
        'private.typefully_media_upload_receipts',
        'private.typefully_draft_attempts'
    ] loop
        if not exists (select 1 from pg_catalog.pg_class
            where oid = relation_name::regclass
              and relrowsecurity and relforcerowsecurity) then
            raise exception 'typefully_private_rls_missing';
        end if;
        foreach role_name in array array['anon', 'authenticated', 'service_role'] loop
            if has_table_privilege(role_name, relation_name,
                'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') then
                raise exception 'typefully_direct_table_grant';
            end if;
        end loop;
    end loop;
    foreach function_name in array array[
        'private.record_typefully_media_upload(uuid,uuid,uuid,uuid,text,bigint,uuid)',
        'private.reserve_typefully_draft_once(uuid,uuid,uuid,uuid,uuid,bigint,text,timestamptz,timestamptz,text)',
        'private.confirm_typefully_draft_once(uuid,bigint,bigint,text)',
        'public.record_typefully_media_upload(uuid,uuid,uuid,uuid,text,bigint,uuid)',
        'public.reserve_typefully_draft_once(uuid,uuid,uuid,uuid,uuid,bigint,text,timestamptz,timestamptz,text)',
        'public.confirm_typefully_draft_once(uuid,bigint,bigint,text)',
        'public.get_typefully_draft_attempt(uuid,uuid)',
        'public.get_typefully_draft_candidate(uuid,uuid,uuid,uuid)',
        'public.get_typefully_media_upload_receipt(uuid,uuid,bigint)'
    ] loop
        if to_regprocedure(function_name) is null
           or not has_function_privilege('service_role', function_name, 'EXECUTE')
           or has_function_privilege('anon', function_name, 'EXECUTE')
           or has_function_privilege('authenticated', function_name, 'EXECUTE')
           or not exists (select 1 from pg_catalog.pg_proc
               where oid = function_name::regprocedure and prosecdef
                 and proconfig @> array['search_path=""']) then
            raise exception 'typefully_rpc_acl_invalid';
        end if;
    end loop;
    select count(*) into before_count from private.typefully_draft_attempts;
    perform set_config('request.jwt.claim.role', 'anon', true);
    begin
        perform public.reserve_typefully_draft_once(
            gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),
            gen_random_uuid(), gen_random_uuid(), 1, 'squidkorea',
            statement_timestamp(), statement_timestamp(), 'ready');
        raise exception 'typefully_nonservice_invocation_accepted';
    exception when insufficient_privilege then
        if sqlerrm <> 'typefully_service_role_required' then raise; end if;
    end;
    begin
        perform public.get_typefully_draft_attempt(gen_random_uuid(),gen_random_uuid());
        raise exception 'typefully_nonservice_read_accepted';
    exception when insufficient_privilege then
        if sqlerrm <> 'typefully_service_role_required' then raise; end if;
    end;
    perform set_config('request.jwt.claim.role', 'service_role', true);
    begin
        perform public.reserve_typefully_draft_once(
            gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),
            gen_random_uuid(), gen_random_uuid(), 1, 'squidkorea',
            statement_timestamp(), statement_timestamp(), 'ready');
        raise exception 'typefully_missing_item_accepted';
    exception when check_violation then
        if sqlerrm <> 'typefully_current_approval_required' then raise; end if;
    end;
    if (select count(*) from private.typefully_draft_attempts) <> before_count then
        raise exception 'typefully_failed_reservation_wrote_row';
    end if;
    if exists (select 1 from pg_catalog.pg_trigger
        where tgrelid = 'private.typefully_draft_attempts'::regclass
          and not tgisinternal) then
        raise exception 'typefully_automatic_trigger_not_allowed';
    end if;
end;
$test$;

do $behavior$
<<fixture>>
declare
    workspace_id uuid := gen_random_uuid();
    item_id uuid := gen_random_uuid();
    version_id uuid := gen_random_uuid();
    asset_id uuid := gen_random_uuid();
    feed_id uuid := gen_random_uuid();
    source_id uuid := gen_random_uuid();
    newer_source_id uuid := gen_random_uuid();
    approval_id uuid := gen_random_uuid();
    media_id uuid := gen_random_uuid();
    media_receipt_id uuid;
    attempt jsonb;
    attempt_id uuid;
    confirmation jsonb;
    banner_hash text := repeat('a', 64);
    report jsonb;
begin
    perform set_config('request.jwt.claim.role', 'service_role', true);
    report := jsonb_build_object(
        'schema_version', '1.0', 'policy_version', 'double-fact-check@1',
        'content_kind', 'daily_news', 'human_review_required', true,
        'status', 'pass', 'input_sha256', repeat('b',64),
        'output_sha256', repeat('c',64), 'checks', jsonb_build_array(
            jsonb_build_object('id','source_evidence','status','pass',
                'label','source','detail','verified','metrics','{}'::jsonb),
            jsonb_build_object('id','output_claims','status','pass',
                'label','output','detail','verified','metrics','{}'::jsonb)));
    insert into public.workspaces (id,name,slug)
    values (workspace_id,'Synthetic Typefully','synthetic-typefully-' || left(workspace_id::text,8));
    insert into public.workspace_clients (workspace_id,client_id,display_name)
    values (workspace_id,'squid','Synthetic Squid');
    insert into public.content_items (id,workspace_id,client_id,content_kind,title,status)
    values (item_id,workspace_id,'squid','daily_news','Synthetic','draft');
    insert into public.content_versions (
        id,workspace_id,content_item_id,version_number,prompt_version,title,
        channel_copy,deliverables,generation_meta
    ) values (
        version_id,workspace_id,item_id,1,'test','Synthetic',
        jsonb_build_object('telegram','테스트 공지','x','테스트 공지'),
        jsonb_build_object('primary_asset_id',asset_id::text),
        jsonb_build_object('mock_mode',false,'fact_check',report));
    update public.content_items set current_version_id=version_id,status='approved'
    where id=item_id;
    insert into storage.buckets (id,name) values ('content-studio','content-studio')
    on conflict (id) do nothing;
    insert into public.assets (
        id,workspace_id,content_item_id,content_version_id,asset_kind,
        storage_bucket,storage_path,mime_type,byte_size,sha256,width,height,metadata
    ) values (
        asset_id,workspace_id,item_id,version_id,'png','content-studio',
        workspace_id::text || '/squid/' || asset_id::text || '/news-card.png',
        'image/png',2048,banner_hash,1024,1024,'{"filename":"news-card.png"}'::jsonb);
    insert into storage.objects (bucket_id,name) values (
        'content-studio',workspace_id::text || '/squid/' || asset_id::text || '/news-card.png');
    insert into public.source_feeds (
        id,workspace_id,client_id,provider,name,handle,poll_interval_minutes,
        last_polled_at,active
    ) values (feed_id,workspace_id,'squid','x','Synthetic official feed',
        '@SquidRouter',15,statement_timestamp(),true);
    insert into public.source_items (
        id,workspace_id,client_id,source_feed_id,source_type,canonical_url,author_handle,
        published_at,body,source_hash
    ) values (
        source_id,workspace_id,'squid',feed_id,'tweet',
        'https://x.com/SquidRouter/status/123456789','@SquidRouter',
        statement_timestamp()-interval '1 hour','Synthetic',repeat('d',64));
    insert into public.content_source_links (
        workspace_id,client_id,content_item_id,source_item_id,position
    ) values (workspace_id,'squid',item_id,source_id,0);
    insert into public.approvals (
        id,workspace_id,client_id,content_item_id,content_version_id,
        reviewer_source,decision,fact_check_policy_version,
        source_facts_verified,output_claims_verified
    ) values (
        approval_id,workspace_id,'squid',item_id,version_id,
        'studio_session','approved','double-fact-check@1',true,true);
    update public.source_items set published_at=statement_timestamp()-interval '25 hours'
    where id=source_id;
    if public.get_typefully_draft_candidate(workspace_id,item_id,version_id,approval_id)
            is not null then
        raise exception 'typefully_stale_source_candidate_accepted';
    end if;
    update public.source_items set published_at=statement_timestamp()-interval '1 hour'
    where id=source_id;
    insert into public.source_items (
        id,workspace_id,client_id,source_feed_id,source_type,canonical_url,author_handle,
        published_at,body,source_hash
    ) values (
        newer_source_id,workspace_id,'squid',feed_id,'tweet',
        'https://x.com/SquidRouter/status/123456790','@SquidRouter',
        statement_timestamp()-interval '30 minutes','Synthetic newer',repeat('e',64));
    if public.get_typefully_draft_candidate(workspace_id,item_id,version_id,approval_id)
            is not null then
        raise exception 'typefully_nonlatest_source_candidate_accepted';
    end if;
    delete from public.source_items where id=newer_source_id;
    update public.source_feeds set last_polled_at=statement_timestamp()-interval '31 minutes'
    where id=feed_id;
    if public.get_typefully_draft_candidate(workspace_id,item_id,version_id,approval_id)
            is not null then
        raise exception 'typefully_stale_feed_candidate_accepted';
    end if;
    update public.source_feeds set last_polled_at=statement_timestamp()
    where id=feed_id;
    if public.get_typefully_draft_candidate(
        workspace_id,item_id,version_id,approval_id)->>'asset_id' <> asset_id::text
       or public.get_typefully_draft_candidate(
        workspace_id,item_id,version_id,approval_id)->>'x_copy' <> '테스트 공지'
       or public.get_typefully_draft_candidate(
        workspace_id,item_id,version_id,gen_random_uuid()) is not null then
        raise exception 'typefully_candidate_projection_invalid';
    end if;
    begin
        perform public.record_typefully_media_upload(
            workspace_id,item_id,version_id,asset_id,repeat('f',64),1234,media_id);
        raise exception 'typefully_wrong_uploaded_bytes_accepted';
    exception when check_violation then
        if sqlerrm <> 'typefully_canonical_png_mismatch' then raise; end if;
    end;
    media_receipt_id := public.record_typefully_media_upload(
        workspace_id,item_id,version_id,asset_id,banner_hash,1234,media_id);
    if public.get_typefully_media_upload_receipt(workspace_id,item_id,1234)
            ->>'media_receipt_id' <> media_receipt_id::text
       or public.get_typefully_media_upload_receipt(workspace_id,item_id,1235)
            is not null then
        raise exception 'typefully_media_receipt_readback_invalid';
    end if;
    update public.source_items set published_at=statement_timestamp()-interval '25 hours'
    where id=source_id;
    begin
        perform public.reserve_typefully_draft_once(
            workspace_id,item_id,version_id,approval_id,media_receipt_id,
            1234,'squidkorea',statement_timestamp(),statement_timestamp(),'ready');
        raise exception 'typefully_stale_source_reservation_accepted';
    exception when check_violation then
        if sqlerrm <> 'typefully_primary_source_invalid' then raise; end if;
    end;
    update public.source_items set published_at=statement_timestamp()-interval '1 hour'
    where id=source_id;
    insert into public.source_items (
        id,workspace_id,client_id,source_feed_id,source_type,canonical_url,author_handle,
        published_at,body,source_hash
    ) values (
        newer_source_id,workspace_id,'squid',feed_id,'tweet',
        'https://x.com/SquidRouter/status/123456790','@SquidRouter',
        statement_timestamp()-interval '30 minutes','Synthetic newer',repeat('e',64));
    begin
        perform public.reserve_typefully_draft_once(
            workspace_id,item_id,version_id,approval_id,media_receipt_id,
            1234,'squidkorea',statement_timestamp(),statement_timestamp(),'ready');
        raise exception 'typefully_nonlatest_source_reservation_accepted';
    exception when check_violation then
        if sqlerrm <> 'typefully_primary_source_invalid' then raise; end if;
    end;
    delete from public.source_items where id=newer_source_id;
    update public.source_feeds set last_polled_at=statement_timestamp()-interval '31 minutes'
    where id=feed_id;
    begin
        perform public.reserve_typefully_draft_once(
            workspace_id,item_id,version_id,approval_id,media_receipt_id,
            1234,'squidkorea',statement_timestamp(),statement_timestamp(),'ready');
        raise exception 'typefully_stale_feed_reservation_accepted';
    exception when check_violation then
        if sqlerrm <> 'typefully_primary_source_invalid' then raise; end if;
    end;
    update public.source_feeds set last_polled_at=statement_timestamp()
    where id=feed_id;
    begin
        perform public.reserve_typefully_draft_once(
            workspace_id,item_id,version_id,approval_id,media_receipt_id,
            1234,'wrong_account',statement_timestamp(),statement_timestamp(),'ready');
        raise exception 'typefully_wrong_account_accepted';
    exception when check_violation then
        if sqlerrm <> 'typefully_live_readback_required' then raise; end if;
    end;
    attempt := public.reserve_typefully_draft_once(
        workspace_id,item_id,version_id,approval_id,media_receipt_id,
        1234,'squidkorea',statement_timestamp(),statement_timestamp(),'ready');
    attempt_id := (attempt->>'attempt_id')::uuid;
    if public.get_typefully_draft_attempt(workspace_id,item_id)->>'status'
            <> 'delivery_unknown'
       or public.get_typefully_draft_attempt(workspace_id,item_id) ?| array[
            'request_body','x_copy','media_url','upload_url','secret'] then
        raise exception 'typefully_unknown_readback_missing';
    end if;
    if attempt->>'status' <> 'delivery_unknown'
       or attempt->'request_body'->>'publish_at' is not null
       or jsonb_typeof(attempt->'request_body'->'publish_at') <> 'null'
       or attempt->'request_body'->'platforms'->'x'->'posts'->0->>'text' <> '테스트 공지'
       or attempt->'request_body'->'platforms'->'x'->'posts'->0->'media_ids'->>0 <> media_id::text then
        raise exception 'typefully_reserved_draft_body_invalid';
    end if;
    begin
        perform public.reserve_typefully_draft_once(
            workspace_id,item_id,version_id,approval_id,media_receipt_id,
            1234,'squidkorea',statement_timestamp(),statement_timestamp(),'ready');
        raise exception 'typefully_second_attempt_accepted';
    exception when check_violation then
        if sqlerrm <> 'typefully_attempt_already_reserved' then raise; end if;
    end;
    begin
        perform public.confirm_typefully_draft_once(attempt_id,1234,5678,'published');
        raise exception 'typefully_published_receipt_accepted';
    exception when check_violation then
        if sqlerrm <> 'typefully_draft_receipt_invalid' then raise; end if;
    end;
    confirmation := public.confirm_typefully_draft_once(attempt_id,1234,5678,'draft');
    if confirmation->>'status' <> 'draft_created'
       or public.get_typefully_draft_attempt(workspace_id,item_id)->>'provider_draft_id'
            <> '5678'
       or (select count(*) from private.typefully_draft_attempts as stored_attempt
            where stored_attempt.workspace_id=fixture.workspace_id
              and stored_attempt.content_item_id=fixture.item_id) <> 1
       or exists (select 1 from public.publications as publication
            where publication.content_item_id=fixture.item_id) then
        raise exception 'typefully_confirmation_boundary_invalid';
    end if;
    confirmation := public.confirm_typefully_draft_once(attempt_id,1234,5678,'draft');
    if confirmation->>'reused' <> 'true' then
        raise exception 'typefully_exact_confirmation_replay_rejected';
    end if;
    begin
        perform public.confirm_typefully_draft_once(attempt_id,1234,5679,'draft');
        raise exception 'typefully_changed_confirmation_accepted';
    exception when check_violation then
        if sqlerrm <> 'typefully_draft_receipt_invalid' then raise; end if;
    end;
end;
$behavior$;
rollback;
