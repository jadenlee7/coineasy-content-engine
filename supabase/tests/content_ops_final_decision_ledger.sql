-- Disposable full-schema check. No provider call or production write.
begin;
do $$
<<fixture>>
declare
    choice text;
    client text; official_handle text; scenario_index integer; destination_channel text;
    previous_confirmation_key text;
    w uuid; i uuid; v uuid; feed uuid; source uuid; job uuid;
    banner uuid; actor uuid; review uuid; parent uuid; card uuid;
    fingerprint text; result jsonb; expiry timestamptz;
    bot text:=repeat('a',64); room text:=repeat('b',64);
    human text:=repeat('c',64); control_binding text:=repeat('d',64);
    snapshot_hash text:=repeat('e',64); release_sha text:=repeat('f',40);
    operation_key text:=repeat('1',64); decision_id uuid;
    approval_id uuid; part_index integer; fact_report jsonb;
begin
    fact_report:=jsonb_build_object('schema_version','1.0',
        'policy_version','double-fact-check@1','content_kind','daily_news',
        'human_review_required',true,'status','pass',
        'input_sha256',repeat('a',64),'output_sha256',repeat('b',64),
        'checks',jsonb_build_array(
            jsonb_build_object('id','source_evidence','status','pass',
                'label','Synthetic source check','detail','Synthetic only','metrics','{}'::jsonb),
            jsonb_build_object('id','output_claims','status','pass',
                'label','Synthetic output check','detail','Synthetic only','metrics','{}'::jsonb)));
    for scenario_index in 1..8 loop
        client:=(array['yellow','babylon','squid','origintrail'])[(scenario_index+1)/2];
        official_handle:=case client when 'yellow' then 'Yellow'
            when 'babylon' then 'babylonlabs_io' when 'squid' then 'SquidRouter'
            else 'origin_trail' end;
        choice:=case when scenario_index%2=0 then 'hold' else 'confirm_publication' end;
        operation_key:=encode(sha256(convert_to('key-'||scenario_index,'UTF8')),'hex');
        if choice='confirm_publication' then previous_confirmation_key:=operation_key; end if;
        room:=encode(sha256(convert_to('room-'||scenario_index,'UTF8')),'hex');
        control_binding:=encode(sha256(convert_to('control-'||scenario_index,'UTF8')),'hex');
        w:=gen_random_uuid(); i:=gen_random_uuid(); v:=gen_random_uuid();
        feed:=gen_random_uuid(); source:=gen_random_uuid(); job:=gen_random_uuid();
        banner:=gen_random_uuid(); actor:=gen_random_uuid(); review:=gen_random_uuid();
        parent:=gen_random_uuid(); card:=gen_random_uuid();
        expiry:=clock_timestamp()+interval '10 minutes';
        insert into public.workspaces(id,name,slug) values
            (w,'Synthetic final decision','synthetic-decision-'||substr(w::text,1,8));
        insert into public.workspace_clients(workspace_id,client_id,display_name)
            values(w,client,'Synthetic client');
        insert into private.content_ops_publication_routes(workspace_id,
            client_id,channel,route_binding,release_sha,verified_at,active)
        values(w,client,'telegram',repeat('a',64),release_sha,
                clock_timestamp()-interval '1 minute',true),
              (w,client,'typefully_x',repeat('b',64),release_sha,
                clock_timestamp()-interval '1 minute',true);
        insert into auth.users(id) values(actor);
        insert into private.content_ops_review_principals(id,workspace_id,
            bot_binding,human_binding) values(actor,w,bot,human);
        insert into public.source_feeds(id,workspace_id,client_id,provider,
            name,handle,poll_interval_minutes,last_polled_at,active)
            values(feed,w,client,'x','Synthetic X','@'||official_handle,15,clock_timestamp(),true);
        insert into public.source_items(id,workspace_id,client_id,source_feed_id,
            external_id,source_type,canonical_url,author_handle,published_at,body,source_hash)
            values(source,w,client,feed,'123456789','tweet',
                'https://x.com/'||official_handle||'/status/123456789','@'||official_handle,
                clock_timestamp()-interval '1 hour','Synthetic source',repeat('2',64));
        insert into public.content_items(id,workspace_id,client_id,content_kind,title,status)
            values(i,w,client,'daily_news','Synthetic','needs_review');
        insert into public.content_versions(id,workspace_id,content_item_id,
            version_number,prompt_version,title,generation_meta,deliverables,channel_copy)
            values(v,w,i,1,'synthetic@1','Synthetic',
                jsonb_build_object('mock_mode',false,'request_id',i::text,
                    'fact_check',fact_report),
                jsonb_build_object('primary_asset_id',banner::text),
                jsonb_build_object('telegram','Synthetic Korean','x','Synthetic X'));
        update public.content_items set current_version_id=v where id=i;
        insert into public.content_source_links(workspace_id,client_id,content_item_id,
            source_item_id,position) values(w,client,i,source,0);
        insert into public.jobs(id,workspace_id,client_id,content_item_id,
            job_kind,status,input,output,finished_at)
            values(job,w,client,i,'generate','succeeded',
                jsonb_build_object('workflow','official_x_review_draft_v1',
                    'content_kind','daily_news','manual_only',false,
                    'kst_date',pg_catalog.timezone('Asia/Seoul',clock_timestamp())::date,
                    'request_id',i::text,'source_item_ids',jsonb_build_array(source::text)),
                jsonb_build_object('content_item_id',i::text,
                    'content_version_id',v::text,'source_item_ids',jsonb_build_array(source::text)),
                clock_timestamp());
        insert into private.official_x_daily_slots(workspace_id,kst_date,client_id,slot,job_id)
            values(w,pg_catalog.timezone('Asia/Seoul',clock_timestamp())::date,client,1,job);
        insert into public.assets(id,workspace_id,content_item_id,content_version_id,
            asset_kind,storage_bucket,storage_path,mime_type,byte_size,sha256,
            width,height,metadata)
            values(banner,w,i,v,'png','content-studio',
                w::text||'/'||client||'/'||banner::text||'/news-card.png','image/png',
                1024,repeat('3',64),1080,1080,'{"filename":"news-card.png"}'::jsonb);
        insert into storage.buckets(id,name) values('content-studio','content-studio')
            on conflict(id) do nothing;
        insert into storage.objects(bucket_id,name) values('content-studio',
            w::text||'/'||client||'/'||banner::text||'/news-card.png');
        fingerprint:=private.content_ops_button_version_fingerprint(w,i,v);
        insert into private.content_ops_button_reviewers(workspace_id,client_id,actor_id,active)
            values(w,client,actor,true);
        insert into private.content_ops_button_reviews(id,workspace_id,client_id,
            content_item_id,content_version_id,version_fingerprint)
            values(review,w,client,i,v,fingerprint);
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
            release_sha,telegram_route_binding,typefully_route_binding,
            bot_binding,room_binding,human_binding,expires_at)
            values(card,review,parent,actor,0,fingerprint,snapshot_hash,repeat('8',64),
                release_sha,repeat('a',64),repeat('b',64),bot,room,human,expiry);
        insert into private.content_ops_final_cards(id,review_id,parent_card_id,
            actor_id,epoch,version_fingerprint,snapshot_sha256,
            control_message_binding,expires_at)
            values(card,review,parent,actor,0,fingerprint,snapshot_hash,
                control_binding,expiry);
        for part_index in 0..3 loop
            insert into private.content_ops_final_card_parts(delivery_id,part_index,
                payload_sha256,state,message_binding,response_sha256,
                started_at,confirmed_at)
            values(card,part_index,repeat('8',64),'confirmed',
                encode(sha256(convert_to(card::text||':'||part_index,'UTF8')),'hex'),
                repeat('7',64),clock_timestamp()-interval '1 second',clock_timestamp());
        end loop;
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
            where workspace_id=w and client_id=client and actor_id=actor;
        begin
            perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                snapshot_hash,control_binding,bot,room,human,release_sha,choice,operation_key);
            raise exception 'revoked_actor_final_decision_allowed';
        exception when check_violation then null; end;
        update private.content_ops_button_reviewers set active=true
            where workspace_id=w and client_id=client and actor_id=actor;
        insert into public.approvals(workspace_id,client_id,content_item_id,
            content_version_id,reviewer_source,decision)
            values(w,client,i,v,'studio_session','commented');
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
                    choice,previous_confirmation_key);
                raise exception 'global_callback_key_reused_for_another_card';
            exception when unique_violation then null; end;
        end if;
        if choice='confirm_publication' then
            foreach destination_channel in array array['telegram','typefully_x'] loop
                update private.content_ops_publication_routes set route_binding=repeat('c',64)
                    where workspace_id=w and channel=destination_channel;
                begin
                    perform private.record_content_ops_final_decision(card,actor,v,fingerprint,
                        snapshot_hash,control_binding,bot,room,human,release_sha,choice,operation_key);
                    raise exception 'changed_destination_final_confirmation_allowed';
                exception when check_violation then null; end;
                if exists(select 1 from private.content_ops_final_decisions where review_id=review) then
                    raise exception 'changed_destination_wrote_final_decision';
                end if;
                update private.content_ops_publication_routes set route_binding=
                    case destination_channel when 'telegram' then repeat('a',64) else repeat('b',64) end
                    where workspace_id=w and channel=destination_channel;
            end loop;
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
        if choice='confirm_publication' then
            update private.content_ops_publication_routes
                set verified_at=clock_timestamp()-interval '16 minutes'
                where workspace_id=w and channel='telegram';
            begin
                perform private.materialize_content_ops_publication_handoff(
                    decision_id,actor,release_sha,operation_key);
                raise exception 'stale_destination_verification_created_approval';
            exception when check_violation then null; end;
            if exists(select 1 from public.approvals where workspace_id=w)
               or exists(select 1 from private.content_ops_channel_handoffs where workspace_id=w) then
                raise exception 'stale_destination_verification_created_partial_work';
            end if;
            update private.content_ops_publication_routes set verified_at=clock_timestamp()
                where workspace_id=w and channel='telegram';
            update private.content_ops_publication_routes set active=false
                where workspace_id=w and channel='typefully_x';
            begin
                perform private.materialize_content_ops_publication_handoff(
                    decision_id,actor,release_sha,operation_key);
                raise exception 'unverified_routes_created_approval';
            exception when check_violation then null; end;
            if exists(select 1 from public.approvals where workspace_id=w) then
                raise exception 'failed_route_gate_created_approval';
            end if;
            update private.content_ops_publication_routes set active=true
                where workspace_id=w;
            foreach destination_channel in array array['telegram','typefully_x'] loop
                update private.content_ops_publication_routes set route_binding=repeat('c',64)
                    where workspace_id=w and channel=destination_channel;
                begin
                    perform private.materialize_content_ops_publication_handoff(
                        decision_id,actor,release_sha,operation_key);
                    raise exception 'changed_destination_created_approval';
                exception when check_violation then null; end;
                if exists(select 1 from public.approvals where workspace_id=w)
                   or exists(select 1 from private.content_ops_channel_handoffs where workspace_id=w) then
                    raise exception 'changed_destination_created_partial_work';
                end if;
                update private.content_ops_publication_routes set route_binding=
                    case destination_channel when 'telegram' then repeat('a',64) else repeat('b',64) end
                    where workspace_id=w and channel=destination_channel;
            end loop;
            update public.source_feeds set last_polled_at=clock_timestamp()-interval '16 minutes'
                where id=feed;
            begin
                perform private.materialize_content_ops_publication_handoff(
                    decision_id,actor,release_sha,operation_key);
                raise exception 'stale_source_created_approval';
            exception when check_violation then null; end;
            update public.source_feeds set last_polled_at=clock_timestamp() where id=feed;
            result:=private.materialize_content_ops_publication_handoff(
                decision_id,actor,release_sha,operation_key);
            approval_id:=(result->>'approval_id')::uuid;
            if result->>'status' <> 'approved_handoff_unwired'
               or result->>'reused' <> 'false'
               or result->'execution_authorized' is distinct from 'false'::jsonb
               or (select count(*) from private.content_ops_channel_handoffs h
                    where h.decision_id=fixture.decision_id
                      and h.approval_id=fixture.approval_id
                      and h.status='awaiting_channel_owner')<>2
               or (select count(*) from public.approvals a where a.workspace_id=w
                    and a.content_item_id=i and a.review_principal_id=actor
                      and a.reviewer_source='telegram_principal'
                      and a.source_facts_verified and a.output_claims_verified)<>1
               or not exists(select 1 from public.content_items
                    where id=i and status='approved') then
                raise exception 'atomic_approval_handoff_invalid';
            end if;
            perform private.require_double_fact_check_approval(w,i,v,approval_id);
            result:=private.materialize_content_ops_publication_handoff(
                decision_id,actor,release_sha,operation_key);
            if result->>'reused' <> 'true'
               or (result->>'approval_id')::uuid is distinct from approval_id
               or private.read_content_ops_publication_handoff_terminal(
                    decision_id,actor,operation_key)->>'approval_id'
                    is distinct from approval_id::text then
                raise exception 'atomic_approval_handoff_replay_invalid';
            end if;
            begin
                perform private.materialize_content_ops_publication_handoff(
                    decision_id,actor,release_sha,repeat('9',64));
                raise exception 'new_operation_key_replayed_approval';
            exception when check_violation then null; end;
            if exists(select 1 from public.publications where workspace_id=w)
               or exists(select 1 from public.jobs where workspace_id=w
                    and job_kind='publish') then
                raise exception 'handoff_created_dispatchable_public_work';
            end if;
        else
            begin
                perform private.materialize_content_ops_publication_handoff(
                    decision_id,actor,release_sha,operation_key);
                raise exception 'held_decision_created_approval';
            exception when check_violation then null; end;
        end if;
    end loop;
    if has_function_privilege('service_role',
        'private.record_content_ops_final_decision(uuid,uuid,uuid,text,text,text,text,text,text,text,text,text)',
        'EXECUTE') or has_function_privilege('authenticated',
        'private.read_content_ops_final_decision_terminal(uuid,uuid,text)','EXECUTE')
       or has_table_privilege('service_role','private.content_ops_final_decisions','SELECT') then
        raise exception 'final_decision_runtime_grant_leaked';
    end if;
    if has_function_privilege('service_role',
        'private.materialize_content_ops_publication_handoff(uuid,uuid,text,text)',
        'EXECUTE') or has_table_privilege('service_role',
        'private.content_ops_channel_handoffs','SELECT')
       or has_table_privilege('service_role',
        'private.content_ops_publication_routes','SELECT') then
        raise exception 'publication_handoff_runtime_grant_leaked';
    end if;
end $$;
rollback;
