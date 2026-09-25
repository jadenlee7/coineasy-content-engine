-- Disposable full-schema behavior after the local-only preflight proposal.
-- Synthetic data rolls back; this test never calls Telegram, Typefully or X.
begin;
do $$
declare
    w uuid:=gen_random_uuid(); i uuid:=gen_random_uuid(); v uuid:=gen_random_uuid();
    feed uuid:=gen_random_uuid(); source uuid:=gen_random_uuid();
    job uuid:=gen_random_uuid(); banner uuid:=gen_random_uuid();
    actor uuid:=gen_random_uuid(); review uuid:=gen_random_uuid();
    card uuid:=gen_random_uuid(); fingerprint text; result jsonb;
    bot text:=repeat('a',64); room text:=repeat('b',64);
    human text:=repeat('c',64); source_time timestamptz;
    poll_time timestamptz;
    before_approvals bigint; before_publications bigint;
begin
    insert into public.workspaces(id,name,slug) values
        (w,'Synthetic final-card preflight','synthetic-final-card-'||substr(w::text,1,8));
    insert into public.workspace_clients(workspace_id,client_id,display_name)
        values(w,'yellow','Synthetic Yellow');
    insert into auth.users(id) values(actor);
    insert into private.content_ops_review_principals(id,workspace_id,bot_binding,human_binding)
        values(actor,w,bot,human);
    insert into public.source_feeds(id,workspace_id,client_id,provider,name,handle,
        poll_interval_minutes,last_polled_at,active)
        values(feed,w,'yellow','x','Synthetic official X','@Yellow',15,
            clock_timestamp(),true);
    select last_polled_at into poll_time from public.source_feeds where id=feed;
    insert into public.source_items(id,workspace_id,client_id,source_feed_id,
        external_id,source_type,canonical_url,author_handle,published_at,body,source_hash)
        values(source,w,'yellow',feed,'123456789','tweet',
            'https://x.com/Yellow/status/123456789','@Yellow',
            clock_timestamp()-interval '1 hour','Synthetic source',repeat('d',64));
    select published_at into source_time from public.source_items where id=source;
    insert into public.content_items(id,workspace_id,client_id,content_kind,title,status)
        values(i,w,'yellow','daily_news','Synthetic','needs_review');
    insert into public.content_versions(id,workspace_id,content_item_id,
        version_number,prompt_version,title,generation_meta,deliverables,channel_copy)
        values(v,w,i,1,'synthetic@1','Synthetic',
            jsonb_build_object('mock_mode',false,'request_id',i::text),
            jsonb_build_object('primary_asset_id',banner::text),
            jsonb_build_object('telegram','Synthetic Korean review','x','Synthetic X review'));
    update public.content_items set current_version_id=v where id=i;
    insert into public.content_source_links(workspace_id,client_id,content_item_id,
        source_item_id,position) values(w,'yellow',i,source,0);
    insert into public.jobs(id,workspace_id,client_id,content_item_id,
        job_kind,status,input,output,finished_at)
        values(job,w,'yellow',i,'generate','succeeded',
            jsonb_build_object('workflow','official_x_review_draft_v1',
                'content_kind','daily_news','manual_only',false,
                'kst_date',pg_catalog.timezone('Asia/Seoul',clock_timestamp())::date,
                'request_id',i::text,'source_item_ids',jsonb_build_array(source::text)),
            jsonb_build_object('content_item_id',i::text,
                'content_version_id',v::text,'source_item_ids',jsonb_build_array(source::text)),
            clock_timestamp());
    insert into private.official_x_daily_slots(workspace_id,kst_date,client_id,slot,job_id)
        values(w,pg_catalog.timezone('Asia/Seoul',clock_timestamp())::date,
            'yellow',1,job);
    insert into public.assets(id,workspace_id,content_item_id,content_version_id,
        asset_kind,storage_bucket,storage_path,mime_type,byte_size,sha256,width,
        height,metadata)
        values(banner,w,i,v,'png','content-studio',
            w::text||'/yellow/'||banner::text||'/news-card.png','image/png',
            1024,repeat('e',64),1080,1080,'{"filename":"news-card.png"}'::jsonb);
    insert into storage.buckets(id,name) values('content-studio','content-studio')
        on conflict(id) do nothing;
    insert into storage.objects(bucket_id,name)
        values('content-studio',w::text||'/yellow/'||banner::text||'/news-card.png');
    if private.content_ops_review_candidate(w,i,v) is null then
        raise exception 'synthetic_final_candidate_invalid';
    end if;
    fingerprint:=private.content_ops_button_version_fingerprint(w,i,v);
    insert into private.content_ops_button_reviewers(workspace_id,client_id,actor_id,active)
        values(w,'yellow',actor,true);
    insert into private.content_ops_button_reviews(id,workspace_id,client_id,
        content_item_id,content_version_id,version_fingerprint)
        values(review,w,'yellow',i,v,fingerprint);
    insert into private.content_ops_button_checks(review_id,epoch,actor_id,check_kind)
        values(review,0,actor,'source_checked'),(review,0,actor,'claims_checked');
    insert into private.content_ops_button_cards(id,review_id,epoch,
        version_fingerprint,bindings,parts,delivered_at,expires_at,active)
        values(card,review,0,fingerprint,
            jsonb_build_object('bot',bot,'room',room,'message',repeat('f',64)),
            jsonb_build_array(
                jsonb_build_object('kind','image','outcome','sent','message_binding',repeat('1',64)),
                jsonb_build_object('kind','telegram','outcome','sent','message_binding',repeat('2',64)),
                jsonb_build_object('kind','x','outcome','sent','message_binding',repeat('3',64))),
            clock_timestamp()-interval '1 second',clock_timestamp()+interval '10 minutes',true);
    select count(*) into before_approvals from public.approvals where workspace_id=w;
    select count(*) into before_publications from public.publications where workspace_id=w;
    result:=private.content_ops_final_card_preflight(review,card,actor,
        fingerprint,bot,room,human);
    if result->>'status' <> 'ready_for_final_card'
       or result->>'content_version_id' <> v::text
       or result->>'banner_sha256' <> repeat('e',64)
       or result->'execution_authorized' is distinct from 'false'::jsonb then
        raise exception 'final_card_preflight_ready_projection_invalid';
    end if;
    if private.content_ops_final_card_preflight(review,card,actor,
        fingerprint,bot,room,repeat('9',64))->>'status' <> 'blocked'
       or private.content_ops_final_card_preflight(review,card,actor,
        fingerprint,bot,repeat('8',64),human)->>'status' <> 'blocked'
       or private.content_ops_final_card_preflight(review,card,gen_random_uuid(),
        fingerprint,bot,room,human)->>'status' <> 'blocked' then
        raise exception 'final_card_preflight_identity_bypass';
    end if;
    delete from private.content_ops_button_checks where review_id=review
        and check_kind='claims_checked';
    if private.content_ops_final_card_preflight(review,card,actor,
        fingerprint,bot,room,human)->>'status' <> 'blocked' then
        raise exception 'final_card_preflight_missing_check';
    end if;
    insert into private.content_ops_button_checks(review_id,epoch,actor_id,check_kind)
        values(review,0,actor,'claims_checked');
    update public.source_feeds set last_polled_at=clock_timestamp()-interval '16 minutes'
        where id=feed;
    if private.content_ops_final_card_preflight(review,card,actor,
        fingerprint,bot,room,human)->>'status' <> 'blocked' then
        raise exception 'final_card_preflight_stale_poll';
    end if;
    update public.source_feeds set last_polled_at=poll_time where id=feed;
    update public.source_items set published_at=clock_timestamp()-interval '25 hours'
        where id=source;
    if private.content_ops_final_card_preflight(review,card,actor,
        fingerprint,bot,room,human)->>'status' <> 'blocked' then
        raise exception 'final_card_preflight_stale_source';
    end if;
    update public.source_items set published_at=source_time where id=source;
    update private.content_ops_button_reviewers set active=false
        where workspace_id=w and client_id='yellow' and actor_id=actor;
    if private.content_ops_final_card_preflight(review,card,actor,
        fingerprint,bot,room,human)->>'status' <> 'blocked' then
        raise exception 'final_card_preflight_revoked_actor';
    end if;
    update private.content_ops_button_reviewers set active=true
        where workspace_id=w and client_id='yellow' and actor_id=actor;
    insert into public.approvals(workspace_id,client_id,content_item_id,
        content_version_id,reviewer_source,decision)
        values(w,'yellow',i,v,'studio_session','commented');
    if private.content_ops_final_card_preflight(review,card,actor,
        fingerprint,bot,room,human)->>'status' <> 'blocked' then
        raise exception 'final_card_preflight_existing_approval';
    end if;
    delete from public.approvals where workspace_id=w and content_item_id=i;
    update private.content_ops_button_cards set active=false where id=card;
    if private.content_ops_final_card_preflight(review,card,actor,
        fingerprint,bot,room,human)->>'status' <> 'blocked' then
        raise exception 'final_card_preflight_revoked_parent';
    end if;
    if (select count(*) from public.approvals where workspace_id=w)<>before_approvals
       or (select count(*) from public.publications where workspace_id=w)<>before_publications then
        raise exception 'final_card_preflight_mutated_publication_state';
    end if;
    if has_function_privilege('anon',
        'private.content_ops_final_card_preflight(uuid,uuid,uuid,text,text,text,text)',
        'EXECUTE') or has_function_privilege('authenticated',
        'private.content_ops_final_card_preflight(uuid,uuid,uuid,text,text,text,text)',
        'EXECUTE') or has_function_privilege('service_role',
        'private.content_ops_final_card_preflight(uuid,uuid,uuid,text,text,text,text)',
        'EXECUTE') then
        raise exception 'final_card_preflight_runtime_grant_leaked';
    end if;
end $$;
rollback;
