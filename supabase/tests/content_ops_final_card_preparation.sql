-- Disposable full-schema behavior after the local-only preflight proposal.
-- Synthetic data rolls back; this test never calls Telegram, Typefully or X.
begin;
do $$
<<fixture>>
declare
    w uuid:=gen_random_uuid(); i uuid:=gen_random_uuid(); v uuid:=gen_random_uuid();
    feed uuid:=gen_random_uuid(); source uuid:=gen_random_uuid();
    job uuid:=gen_random_uuid(); banner uuid:=gen_random_uuid();
    actor uuid:=gen_random_uuid(); review uuid:=gen_random_uuid();
    card uuid:=gen_random_uuid(); fingerprint text; result jsonb;
    intent uuid:=gen_random_uuid(); receipt jsonb;
    delivery uuid:=gen_random_uuid(); part_no smallint;
    payload_hash text; message_hash text; response_hash text;
    bot text:=repeat('a',64); room text:=repeat('b',64);
    human text:=repeat('c',64); source_time timestamptz;
    poll_time timestamptz;
    callback_release_sha text:=repeat('5',40); release_pair_sha256 text; stale_pair text;
    pair_case integer; pair_engine text; pair_callback text; pair_digest text;
    before_approvals bigint; before_publications bigint;
    next_card uuid:=gen_random_uuid(); next_intent uuid:=gen_random_uuid(); pending_ids text[];
begin
    insert into public.workspaces(id,name,slug) values
        (w,'Synthetic final-card preflight','synthetic-final-card-'||substr(w::text,1,8));
    insert into public.workspace_clients(workspace_id,client_id,display_name)
        values(w,'yellow','Synthetic Yellow');
    release_pair_sha256:=encode(sha256(convert_to(E'content-ops-release-pair@1\nengine='||repeat('6',40)||E'\ncallback='||callback_release_sha||E'\n','UTF8')),'hex');
    stale_pair:=encode(sha256(convert_to(E'content-ops-release-pair@1\nengine='||repeat('6',40)||E'\ncallback='||repeat('9',40)||E'\n','UTF8')),'hex');
    insert into private.content_ops_release_pairs(release_sha,callback_release_sha,release_pair_sha256)
        values(repeat('6',40),callback_release_sha,release_pair_sha256),
              (repeat('6',40),repeat('9',40),stale_pair);
    insert into private.content_ops_current_release_pairs values(w,'yellow',release_pair_sha256);
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
    insert into private.content_ops_button_identities(workspace_id,bot_binding,human_binding,actor_id,active)
        values(w,bot,human,actor,true);
    insert into private.content_ops_publication_routes(workspace_id,client_id,channel,route_binding,release_sha,verified_at,active)
        values(w,'yellow','telegram',repeat('7',64),repeat('6',40),clock_timestamp(),true),
              (w,'yellow','typefully_x',repeat('8',64),repeat('6',40),clock_timestamp(),true);
    -- Missing same-reviewer check is not admission and creates no intent.
    delete from private.content_ops_button_checks where review_id=review and check_kind='claims_checked';
    result:=private.request_content_ops_final_card_preparation(intent,review,card,actor,fingerprint,
        bot,room,human,repeat('6',40),callback_release_sha,release_pair_sha256);
    if result->>'status'<>'not_ready' or exists(select 1 from private.content_ops_final_card_preparations where id=intent)
        then raise exception 'missing_check_admitted'; end if;
    insert into private.content_ops_button_checks(review_id,epoch,actor_id,check_kind) values(review,0,actor,'claims_checked');
    result:=private.request_content_ops_final_card_preparation(intent,review,card,actor,fingerprint,
        bot,room,human,repeat('6',40),callback_release_sha,release_pair_sha256);
    receipt:=private.request_content_ops_final_card_preparation(gen_random_uuid(),review,card,actor,fingerprint,
        bot,room,human,repeat('6',40),callback_release_sha,release_pair_sha256);
    if result->>'status'<>'preparation_recorded' or result->'reused'<>'false'::jsonb
        or receipt->>'intent_id'<>intent::text or receipt->'reused'<>'true'::jsonb
        or (select count(*) from private.content_ops_final_card_preparations where review_id=review)<>1
        then raise exception 'preparation_dedupe_failed'; end if;
    begin
        perform private.claim_content_ops_final_card_preparation(intent,callback_release_sha,repeat('6',40),release_pair_sha256);
        raise exception 'swapped_pair_claimed';
    exception when check_violation then null; end;
    if (select claimed_at from private.content_ops_final_card_preparations where id=intent) is not null
        then raise exception 'bad_claim_mutated'; end if;
    select array_agg(id) into pending_ids from public.fixture_pending_preparations(w,array['yellow'],repeat('6',40),callback_release_sha,release_pair_sha256) id;
    if pending_ids is distinct from array[intent::text] then raise exception 'runtime_discovery_missing_valid'; end if;
    -- The original unclaimed intent stays immutable. A later current epoch
    -- must be discovered without the oldest stale intent blocking it.
    begin
        update private.content_ops_button_reviews set epoch=1 where id=review;
        update private.content_ops_button_cards set active=false where id=card;
        insert into private.content_ops_button_cards(id,review_id,epoch,version_fingerprint,bindings,parts,delivered_at,expires_at,active)
          select next_card,review_id,1,version_fingerprint,
            bindings||jsonb_build_object('message',repeat('9',64)),parts,delivered_at,expires_at,true
          from private.content_ops_button_cards where id=card;
        insert into private.content_ops_button_checks(review_id,epoch,actor_id,check_kind)
          values(review,1,actor,'source_checked'),(review,1,actor,'claims_checked');
        perform private.request_content_ops_final_card_preparation(next_intent,review,next_card,actor,fingerprint,
          bot,room,human,repeat('6',40),callback_release_sha,release_pair_sha256);
        select array_agg(id) into pending_ids from public.fixture_pending_preparations(w,array['yellow'],repeat('6',40),callback_release_sha,release_pair_sha256) id;
        if pending_ids is distinct from array[next_intent::text] then raise exception 'stale_oldest_starved_new_epoch'; end if;
        if (select claimed_at from private.content_ops_final_card_preparations where id=intent) is not null
          then raise exception 'discovery_claimed_old_intent'; end if;
        raise exception 'rollback_fixture_discovery' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    begin
        update private.content_ops_current_release_pairs set release_pair_sha256=stale_pair where workspace_id=w and client_id='yellow';
        update private.content_ops_current_release_pairs set release_pair_sha256=fixture.release_pair_sha256 where workspace_id=w and client_id='yellow';
        if exists(select 1 from public.fixture_pending_preparations(w,array['yellow'],repeat('6',40),callback_release_sha,release_pair_sha256))
          then raise exception 'runtime_discovery_reopened_pair_generation'; end if;
        raise exception 'rollback_fixture_discovery_pair' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    -- A hold/edit epoch change after intent creation blocks before claim.
    begin
        update private.content_ops_button_reviews set state='held' where id=review;
        begin
            perform private.claim_content_ops_final_card_preparation(intent,repeat('6',40),callback_release_sha,release_pair_sha256);
            raise exception 'held_claimed';
        exception when check_violation then null; end;
        raise exception 'rollback_fixture_hold' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    begin
        update private.content_ops_button_identities set active=false where workspace_id=w and actor_id=actor;
        begin
            perform private.claim_content_ops_final_card_preparation(intent,repeat('6',40),callback_release_sha,release_pair_sha256);
            raise exception 'revoked_identity_claimed';
        exception when check_violation then null; end;
        raise exception 'rollback_fixture_identity' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    begin
        update private.content_ops_publication_routes set route_binding=repeat('9',64) where workspace_id=w and channel='telegram';
        begin
            perform private.claim_content_ops_final_card_preparation(intent,repeat('6',40),callback_release_sha,release_pair_sha256);
            raise exception 'changed_route_claimed';
        exception when check_violation then null; end;
        raise exception 'rollback_fixture_route' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    result:=private.claim_content_ops_final_card_preparation(intent,repeat('6',40),callback_release_sha,release_pair_sha256);
    if result->>'status'<>'preparation_claimed' or result->>'intent_id'<>intent::text
       or result->'execution_authorized'<>'false'::jsonb
       or result->'candidate'->>'banner_asset_id'<>banner::text
       or result->'candidate'->>'content_version_id'<>v::text
       or result->'candidate'->>'telegram_copy'<>'Synthetic Korean review'
       then raise exception 'preparation_projection_invalid'; end if;
    -- Exercise the hosted read-store projection against the actual local
    -- schema (not just mocked cursor results). It never writes/claims here.
    select jsonb_build_object('size',a.byte_size,'path',a.storage_path) into receipt
      from public.assets a join public.content_versions cv on cv.id=a.content_version_id and cv.workspace_id=a.workspace_id
      join public.content_items i on i.id=cv.content_item_id and i.workspace_id=a.workspace_id
      join storage.objects o on o.bucket_id=a.storage_bucket and o.name=a.storage_path
      join storage.buckets b on b.id=o.bucket_id and b.public=false
      where i.workspace_id=w and i.client_id='yellow' and i.current_version_id=cv.id
        and i.status='needs_review' and a.content_version_id=v and a.id=banner
        and a.content_item_id=i.id and cv.deliverables->>'primary_asset_id'=a.id::text
        and a.asset_kind='png' and a.mime_type='image/png' and a.storage_bucket='content-studio'
        and a.metadata->>'filename'='news-card.png'
        and a.storage_path=w::text||'/yellow/'||banner::text||'/news-card.png';
    if receipt is null or receipt->>'path'<>w::text||'/yellow/'||banner::text||'/news-card.png'
       then raise exception 'runtime_canonical_projection_invalid'; end if;
    if (select count(*) from private.content_ops_publication_routes pr where pr.workspace_id=w
          and pr.client_id='yellow' and pr.release_sha=repeat('6',40) and pr.active
          and pr.verified_at<=clock_timestamp() and pr.verified_at>clock_timestamp()-interval '15 minutes')<>2
       then raise exception 'runtime_routes_projection_invalid'; end if;
    if exists(select 1 from public.fixture_pending_preparations(w,array['yellow'],repeat('6',40),callback_release_sha,release_pair_sha256))
       then raise exception 'runtime_discovery_reopened_claim'; end if;
    begin
        update private.content_ops_current_release_pairs set release_pair_sha256=stale_pair
            where workspace_id=w and client_id='yellow';
        update private.content_ops_current_release_pairs set release_pair_sha256=fixture.release_pair_sha256
            where workspace_id=w and client_id='yellow';
        begin
            perform private.reserve_prepared_content_ops_final_card_delivery(intent,review,card,actor,fingerprint,
                bot,room,human,repeat('4',64),repeat('5',64),repeat('6',40),repeat('7',64),repeat('8',64),
                'telegram_and_typefully_draft',callback_release_sha,release_pair_sha256);
            raise exception 'old_preparation_reserved_after_pair_roundtrip';
        exception when check_violation then null; end;
        raise exception 'rollback_fixture_pair' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    begin
        update private.content_ops_button_identities set active=false where workspace_id=w and actor_id=actor;
        begin
            perform private.reserve_prepared_content_ops_final_card_delivery(intent,review,card,actor,fingerprint,
                bot,room,human,repeat('4',64),repeat('5',64),repeat('6',40),repeat('7',64),repeat('8',64),
                'telegram_and_typefully_draft',callback_release_sha,release_pair_sha256);
            raise exception 'revoked_postclaim_identity_reserved';
        exception when check_violation then null; end;
        raise exception 'rollback_fixture_postclaim_identity' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    begin
        receipt:=private.reserve_prepared_content_ops_final_card_delivery(intent,review,card,actor,fingerprint,
            bot,room,human,repeat('4',64),repeat('5',64),repeat('6',40),repeat('7',64),repeat('8',64),
            'telegram_and_typefully_draft',callback_release_sha,release_pair_sha256);
        if receipt->>'status'<>'delivery_reserved' or receipt->>'delivery_id'<>intent::text then
            raise exception 'prepared_transport_not_reserved'; end if;
        begin
            update private.content_ops_current_release_pairs set release_pair_sha256=stale_pair
                where workspace_id=w and client_id='yellow';
            update private.content_ops_current_release_pairs set release_pair_sha256=fixture.release_pair_sha256
                where workspace_id=w and client_id='yellow';
            begin
                perform private.begin_prepared_content_ops_final_card_part(intent,0::smallint,repeat('a',64));
                raise exception 'old_preparation_began_after_pair_roundtrip';
            exception when check_violation then null; end;
            raise exception 'rollback_fixture_part_pair' using errcode='ZX001';
        exception when sqlstate 'ZX001' then null; end;
        receipt:=private.begin_prepared_content_ops_final_card_part(intent,0::smallint,repeat('a',64));
        if receipt->>'status'<>'attempt_recorded' then raise exception 'prepared_part_not_attempted'; end if;
        -- Revoking after an admitted send must not suppress its real receipt.
        update private.content_ops_button_identities set active=false where workspace_id=w and actor_id=actor;
        receipt:=private.confirm_content_ops_final_card_part(intent,0::smallint,repeat('a',64),repeat('b',64),repeat('c',64));
        if receipt->>'status'<>'confirmed' then raise exception 'revocation_lost_prior_receipt'; end if;
        begin
            perform private.begin_prepared_content_ops_final_card_part(intent,1::smallint,repeat('d',64));
            raise exception 'revoked_identity_began_next_part';
        exception when check_violation then null; end;
        if (select count(*) from private.content_ops_final_card_parts where delivery_id=intent)<>1
           or (select state from private.content_ops_final_card_parts where delivery_id=intent and part_index=0)<>'confirmed'
           then raise exception 'revocation_mutated_prior_or_new_part'; end if;
        raise exception 'rollback_fixture_delivery' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    result:=private.claim_content_ops_final_card_preparation(intent,repeat('6',40),callback_release_sha,release_pair_sha256);
    if result<>jsonb_build_object('status','outcome_unknown','execution_authorized',false)
        then raise exception 'claimed_intent_reopened'; end if;
    begin
        update private.content_ops_final_card_preparations set claimed_at=null where id=intent;
        raise exception 'claim_reset_allowed';
    exception when check_violation then null; end;
    if exists(select 1 from private.content_ops_final_card_deliveries where review_id=review)
       or (select count(*) from public.approvals where workspace_id=w)<>before_approvals
       or (select count(*) from public.publications where workspace_id=w)<>before_publications
       then raise exception 'preparation_created_delivery_or_public_work'; end if;
    if has_function_privilege('service_role','private.request_content_ops_final_card_preparation(uuid,uuid,uuid,uuid,text,text,text,text,text,text,text)','EXECUTE')
       or has_function_privilege('authenticated','private.claim_content_ops_final_card_preparation(uuid,text,text,text)','EXECUTE')
       or has_function_privilege('service_role','private.reserve_prepared_content_ops_final_card_delivery(uuid,uuid,uuid,uuid,text,text,text,text,text,text,text,text,text,text,text,text)','EXECUTE')
       or has_function_privilege('service_role','private.begin_prepared_content_ops_final_card_part(uuid,smallint,text)','EXECUTE')
       or has_table_privilege('anon','private.content_ops_final_card_preparations','SELECT')
       then raise exception 'preparation_runtime_grants_present'; end if;
end $$;
rollback;

