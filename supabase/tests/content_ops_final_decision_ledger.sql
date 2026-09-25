-- Disposable full-schema check. No provider call or production write.
begin;
do $$
declare
    choice text;
    w uuid; i uuid; v uuid; feed uuid; source uuid; job uuid;
    banner uuid; actor uuid; review uuid; parent uuid; card uuid;
    fingerprint text; result jsonb; expiry timestamptz;
    bot text:=repeat('a',64); room text:=repeat('b',64);
    human text:=repeat('c',64); control_binding text:=repeat('d',64);
    snapshot_hash text:=repeat('e',64); release_sha text:=repeat('f',40);
    operation_key text:=repeat('1',64); decision_id uuid;
begin
    foreach choice in array array['confirm_publication','hold'] loop
        operation_key:=case when choice='hold' then repeat('2',64)
            else repeat('1',64) end;
        room:=case when choice='hold' then repeat('9',64) else repeat('b',64) end;
        control_binding:=case when choice='hold' then repeat('0',64)
            else repeat('d',64) end;
        w:=gen_random_uuid(); i:=gen_random_uuid(); v:=gen_random_uuid();
        feed:=gen_random_uuid(); source:=gen_random_uuid(); job:=gen_random_uuid();
        banner:=gen_random_uuid(); actor:=gen_random_uuid(); review:=gen_random_uuid();
        parent:=gen_random_uuid(); card:=gen_random_uuid();
        expiry:=clock_timestamp()+interval '10 minutes';
        insert into public.workspaces(id,name,slug) values
            (w,'Synthetic final decision','synthetic-decision-'||substr(w::text,1,8));
        insert into public.workspace_clients(workspace_id,client_id,display_name)
            values(w,'yellow','Synthetic Yellow');
        insert into auth.users(id) values(actor);
        insert into private.content_ops_review_principals(id,workspace_id,
            bot_binding,human_binding) values(actor,w,bot,human);
        insert into public.source_feeds(id,workspace_id,client_id,provider,
            name,handle,poll_interval_minutes,last_polled_at,active)
            values(feed,w,'yellow','x','Synthetic X','@Yellow',15,clock_timestamp(),true);
        insert into public.source_items(id,workspace_id,client_id,source_feed_id,
            external_id,source_type,canonical_url,author_handle,published_at,body,source_hash)
            values(source,w,'yellow',feed,'123456789','tweet',
                'https://x.com/Yellow/status/123456789','@Yellow',
                clock_timestamp()-interval '1 hour','Synthetic source',repeat('2',64));
        insert into public.content_items(id,workspace_id,client_id,content_kind,title,status)
            values(i,w,'yellow','daily_news','Synthetic','needs_review');
        insert into public.content_versions(id,workspace_id,content_item_id,
            version_number,prompt_version,title,generation_meta,deliverables,channel_copy)
            values(v,w,i,1,'synthetic@1','Synthetic',
                jsonb_build_object('mock_mode',false,'request_id',i::text),
                jsonb_build_object('primary_asset_id',banner::text),
                jsonb_build_object('telegram','Synthetic Korean','x','Synthetic X'));
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
            values(w,pg_catalog.timezone('Asia/Seoul',clock_timestamp())::date,'yellow',1,job);
        insert into public.assets(id,workspace_id,content_item_id,content_version_id,
            asset_kind,storage_bucket,storage_path,mime_type,byte_size,sha256,
            width,height,metadata)
            values(banner,w,i,v,'png','content-studio',
                w::text||'/yellow/'||banner::text||'/news-card.png','image/png',
                1024,repeat('3',64),1080,1080,'{"filename":"news-card.png"}'::jsonb);
        insert into storage.buckets(id,name) values('content-studio','content-studio')
            on conflict(id) do nothing;
        insert into storage.objects(bucket_id,name) values('content-studio',
            w::text||'/yellow/'||banner::text||'/news-card.png');
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
            values(parent,review,0,fingerprint,
                jsonb_build_object('bot',bot,'room',room,'message',repeat('4',64)),
                jsonb_build_array(
                    jsonb_build_object('kind','image','outcome','sent','message_binding',repeat('5',64)),
                    jsonb_build_object('kind','telegram','outcome','sent','message_binding',repeat('6',64)),
                    jsonb_build_object('kind','x','outcome','sent','message_binding',repeat('7',64))),
                clock_timestamp()-interval '1 second',expiry,true);
        insert into private.content_ops_final_card_deliveries(id,review_id,parent_card_id,
            actor_id,epoch,version_fingerprint,snapshot_sha256,packet_sha256,
            release_sha,bot_binding,room_binding,human_binding,expires_at)
            values(card,review,parent,actor,0,fingerprint,snapshot_hash,repeat('8',64),
                release_sha,bot,room,human,expiry);
        insert into private.content_ops_final_cards(id,review_id,parent_card_id,
            actor_id,epoch,version_fingerprint,snapshot_sha256,
            control_message_binding,expires_at)
            values(card,review,parent,actor,0,fingerprint,snapshot_hash,
                control_binding,expiry);
        if private.content_ops_final_card_preflight(review,parent,actor,
            fingerprint,bot,room,human)->>'status' <> 'ready_for_final_card' then
            raise exception 'synthetic_final_decision_candidate_invalid';
        end if;
        update public.source_feeds set last_polled_at=clock_timestamp()-interval '16 minutes'
            where id=feed;
        begin
            perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                snapshot_hash,control_binding,bot,room,human,release_sha,choice,operation_key);
            raise exception 'stale_poll_final_decision_allowed';
        exception when check_violation then null; end;
        update public.source_feeds set last_polled_at=clock_timestamp() where id=feed;
        update public.content_items set current_version_id=null where id=i;
        begin
            perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                snapshot_hash,control_binding,bot,room,human,release_sha,choice,operation_key);
            raise exception 'noncurrent_version_final_decision_allowed';
        exception when check_violation then null; end;
        update public.content_items set current_version_id=v where id=i;
        update private.content_ops_button_reviewers set active=false
            where workspace_id=w and client_id='yellow' and actor_id=actor;
        begin
            perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                snapshot_hash,control_binding,bot,room,human,release_sha,choice,operation_key);
            raise exception 'revoked_actor_final_decision_allowed';
        exception when check_violation then null; end;
        update private.content_ops_button_reviewers set active=true
            where workspace_id=w and client_id='yellow' and actor_id=actor;
        insert into public.approvals(workspace_id,client_id,content_item_id,
            content_version_id,reviewer_source,decision)
            values(w,'yellow',i,v,'studio_session','commented');
        begin
            perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                snapshot_hash,control_binding,bot,room,human,release_sha,choice,operation_key);
            raise exception 'already_approved_final_decision_allowed';
        exception when check_violation then null; end;
        delete from public.approvals where workspace_id=w and content_item_id=i;
        begin
            perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                snapshot_hash,control_binding,bot,room,human,repeat('0',40),choice,operation_key);
            raise exception 'wrong_release_final_decision_allowed';
        exception when check_violation then null; end;
        begin
            perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                snapshot_hash,repeat('a',64),bot,room,human,release_sha,choice,operation_key);
            raise exception 'wrong_control_message_allowed';
        exception when check_violation then null; end;
        if exists(select 1 from private.content_ops_final_decisions where review_id=review) then
            raise exception 'failed_decision_wrote_ledger';
        end if;
        if choice='hold' then
            begin
                perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                    snapshot_hash,control_binding,bot,room,human,release_sha,
                    choice,repeat('1',64));
                raise exception 'global_callback_key_reused_for_another_card';
            exception when unique_violation then null; end;
        end if;
        result:=private.record_content_ops_final_decision(card,actor,v,fingerprint,
            snapshot_hash,control_binding,bot,room,human,release_sha,choice,operation_key);
        if result->>'status' is distinct from (case when choice='hold' then 'held'
            else 'confirmed_pending_publication_owner' end)
           or result->>'reused' <> 'false'
           or result->'execution_authorized' is distinct from 'false'::jsonb then
            raise exception 'final_decision_record_invalid';
        end if;
        decision_id:=(result->>'decision_id')::uuid;
        result:=private.record_content_ops_final_decision(card,actor,v,fingerprint,
            snapshot_hash,control_binding,bot,room,human,release_sha,choice,operation_key);
        if result->>'reused' <> 'true' or (result->>'decision_id')::uuid<>decision_id then
            raise exception 'final_decision_replay_invalid';
        end if;
        begin
            perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                snapshot_hash,control_binding,bot,room,human,release_sha,choice,repeat('9',64));
            raise exception 'second_final_decision_allowed';
        exception when unique_violation then null; end;
        if private.read_content_ops_final_decision_terminal(card,actor,operation_key)
            ->>'decision_id' is distinct from decision_id::text
           or private.read_content_ops_final_decision_terminal(card,actor,repeat('9',64))
            ->>'status' <> 'not_recorded' then
            raise exception 'final_decision_terminal_invalid';
        end if;
        if choice='hold' and not exists(select 1 from private.content_ops_button_reviews
            where id=review and state='held' and epoch=1) then
            raise exception 'final_hold_did_not_invalidate_review';
        end if;
        if exists(select 1 from public.approvals where workspace_id=w)
           or exists(select 1 from public.publications where workspace_id=w)
           or exists(select 1 from public.jobs where workspace_id=w and job_kind='publish') then
            raise exception 'private_final_decision_created_public_work';
        end if;
    end loop;
    if has_function_privilege('service_role',
        'private.record_content_ops_final_decision(uuid,uuid,uuid,text,text,text,text,text,text,text,text,text)',
        'EXECUTE') or has_function_privilege('authenticated',
        'private.read_content_ops_final_decision_terminal(uuid,uuid,text)','EXECUTE')
       or has_table_privilege('service_role','private.content_ops_final_decisions','SELECT') then
        raise exception 'final_decision_runtime_grant_leaked';
    end if;
end $$;
rollback;
