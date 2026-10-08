-- Disposable full-schema check. No provider call or production write.
begin;
create function pg_temp.reject_queue_stage() returns trigger language plpgsql as $f$
begin
    if current_setting('coineasy.fixture_fail_stage',true)=tg_table_name then
        raise exception 'synthetic_queue_stage_failure' using errcode='23514';
    end if;
    return new;
end $f$;
create trigger fixture_final_queue_fail before insert on public.publications
    for each row execute function pg_temp.reject_queue_stage();
create trigger fixture_final_slot_fail before insert on private.typefully_daily_slots
    for each row execute function pg_temp.reject_queue_stage();
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
    rejected boolean; failed boolean; replay jsonb; sent_before bigint;
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
    for scenario_index in 1..16 loop
        client:=(array['yellow','babylon','squid','origintrail'])[((scenario_index-1)%4)+1];
        official_handle:=case client when 'yellow' then 'Yellow'
            when 'babylon' then 'babylonlabs_io' when 'squid' then 'SquidRouter'
            else 'origin_trail' end;
        choice:=case when scenario_index=10 then 'hold' else 'confirm_publication' end;
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
                jsonb_build_object('primary_asset_id',banner::text,'asset_ids',jsonb_build_array(banner::text)),
                jsonb_build_object('telegram',case when scenario_index=6 then repeat('😀',513) else 'Synthetic Korean' end,'x',case when scenario_index=7 then repeat('x',281) else 'Synthetic X' end));
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
        insert into private.content_ops_button_identities(workspace_id,bot_binding,human_binding,actor_id,active)
            values(w,bot,human,actor,true);
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
            release_sha,telegram_route_binding,typefully_route_binding,decision_mode,
            bot_binding,room_binding,human_binding,expires_at)
            values(card,review,parent,actor,0,fingerprint,snapshot_hash,repeat('8',64),
                release_sha,repeat('a',64),repeat('b',64),
                case when scenario_index=5 then 'record_only' else 'telegram_and_typefully_draft' end,
                bot,room,human,expiry);
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

        perform set_config('coineasy.fixture_fail_stage',
            case scenario_index when 8 then 'publications'
                when 9 then 'typefully_daily_slots' else '' end,true);
        if scenario_index=12 then
            update private.content_ops_button_reviewers set active=false
                where workspace_id=w and actor_id=actor;
        elsif scenario_index=15 then
            update private.content_ops_publication_routes set active=false
                where workspace_id=w and channel='telegram';
        elsif scenario_index=16 then
            update public.source_feeds set last_polled_at=clock_timestamp()-interval '1 hour'
                where id=feed;
        end if;
        rejected:=scenario_index between 5 and 9 or scenario_index>=11;
        failed:=false;
        begin
            result:=private.record_content_ops_final_publication(card,
                case when scenario_index=11 then gen_random_uuid() else actor end,
                v,case when scenario_index=14 then repeat('0',64) else fingerprint end,
                snapshot_hash,control_binding,bot,room,human,
                case when scenario_index=13 then repeat('0',40) else release_sha end,
                choice,operation_key);
        exception when check_violation or invalid_parameter_value then
            if not rejected then raise; end if;
            failed:=true;
        end;
        if failed is distinct from rejected then
            raise exception 'unexpected_final_queue_result scenario=%',scenario_index;
        end if;
        if rejected then
            if exists(select 1 from private.content_ops_final_decisions where card_id=card)
               or exists(select 1 from public.approvals where content_item_id=i)
               or exists(select 1 from private.content_ops_channel_handoffs where content_item_id=i)
               or exists(select 1 from public.publications where content_item_id=i)
               or exists(select 1 from public.jobs where content_item_id=i and job_kind='publish')
               or exists(select 1 from private.typefully_daily_slots where content_item_id=i)
               or (select status from public.content_items where id=i) <> 'needs_review' then
                raise exception 'final_queue_partial_commit scenario=%',scenario_index;
            end if;
            continue;
        end if;
        if choice='hold' then
            if result->>'status'<>'held'
               or exists(select 1 from public.approvals where content_item_id=i)
               or exists(select 1 from public.publications where content_item_id=i) then
                raise exception 'hold_created_channel_work';
            end if;
            continue;
        end if;
        if result->>'status'<>'channels_queued'
           or result->'reused'<>'false'::jsonb
           or result->'execution_authorized'<>'false'::jsonb
           or (select count(*) from public.approvals where content_item_id=i)<>1
           or (select count(*) from private.content_ops_channel_handoffs where content_item_id=i)<>2
           or (select count(*) from public.publications where content_item_id=i and status='queued')<>1
           or (select count(*) from private.typefully_daily_slots where content_item_id=i)<>1 then
            raise exception 'final_queue_atomic_receipt_invalid client=%',client;
        end if;
        replay:=private.record_content_ops_final_publication(card,actor,v,fingerprint,
            snapshot_hash,control_binding,bot,room,human,release_sha,choice,operation_key);
        if replay-'reused' <> result-'reused' or replay->'reused'<>'true'::jsonb then
            raise exception 'final_queue_replay_changed_ids';
        end if;
        begin
            perform private.record_content_ops_final_publication(card,actor,v,fingerprint,
                snapshot_hash,control_binding,bot,room,human,release_sha,choice,repeat('9',64));
            raise exception 'different_callback_key_created_second_queue';
        exception when unique_violation then null; end;
        -- Claim/attempt proof is a deliberately rolled-back subtransaction:
        -- later Typefully tests retain the original, untouched queued pair.
        declare
            tg_claim jsonb; tg_mark jsonb; tg_job uuid; tg_publication uuid;
            original_route text; original_release text;
        begin
            begin
                tg_claim:=public.claim_exact_telegram_publication_job(w,'tg-legacy-fixture',180);
                if client='squid' then
                    if tg_claim is null or tg_claim->>'client_id'<>client
                       or tg_claim->>'content_ops_decision_id'<>result->>'decision_id' then
                        raise exception 'legacy_squid_principal_projection_missing';
                    end if;
                elsif tg_claim is not null or exists(select 1 from public.jobs z
                    where z.workspace_id=w and z.job_kind='publish' and z.attempts<>0) then
                    raise exception 'legacy_squid_worker_stole_other_client';
                end if;
                raise exception 'synthetic_rollback_legacy_claim' using errcode='P7701';
            exception when sqlstate 'P7701' then null;end;
            tg_claim:=public.claim_exact_telegram_publication_job_v2(w,'tg-v2-fixture',180,array[client]);
            if tg_claim is null or tg_claim->>'client_id'<>client
               or tg_claim->>'content_version_id'<>v::text
               or tg_claim->>'content_ops_decision_id'<>result->>'decision_id'
               or tg_claim->>'approval_reviewer_source'<>'telegram_principal'
               or tg_claim->>'telegram_route_binding'<>repeat('a',64)
               or tg_claim->>'content_ops_release_sha'<>release_sha then
                raise exception 'v2_exact_principal_projection_missing';
            end if;
            tg_job:=(tg_claim->>'job_id')::uuid;
            tg_publication:=(tg_claim->>'publication_id')::uuid;
            select route_binding into original_route from private.content_ops_publication_routes
                where workspace_id=w and client_id=client and channel='telegram';
            update private.content_ops_publication_routes set route_binding=repeat('9',64)
                where workspace_id=w and client_id=client and channel='telegram';
            begin
                perform public.mark_exact_telegram_attempt_started(tg_job,'tg-v2-fixture',repeat('6',64));
                raise exception 'telegram_route_drift_reached_attempt';
            exception when check_violation then null;end;
            if (select delivery_started_at from public.publications where id=tg_publication) is not null then
                raise exception 'telegram_route_drift_persisted_attempt';
            end if;
            update private.content_ops_publication_routes set route_binding=original_route
                where workspace_id=w and client_id=client and channel='telegram';
            update public.jobs set input=jsonb_set(input,'{content_ops_release_sha}',to_jsonb(repeat('9',40)))
                where id=tg_job;
            begin
                perform public.mark_exact_telegram_attempt_started(tg_job,'tg-v2-fixture',repeat('6',64));
                raise exception 'telegram_release_drift_reached_attempt';
            exception when check_violation then null;end;
            update public.jobs set input=jsonb_set(input,'{content_ops_release_sha}',to_jsonb(release_sha))
                where id=tg_job;
            tg_mark:=public.mark_exact_telegram_attempt_started(tg_job,'tg-v2-fixture',repeat('6',64));
            if tg_mark->>'status'<>'publishing'
               or (select delivery_started_at from public.publications where id=tg_publication) is null
               or public.claim_exact_telegram_publication_job_v2(w,'tg-v2-fixture',180,array[client]) is not null then
                raise exception 'telegram_valid_route_attempt_or_no_reclaim_failed';
            end if;
            -- Already-fenced same request is receipt-only, never a new attempt.
            perform public.mark_exact_telegram_attempt_started(tg_job,'tg-v2-fixture',repeat('6',64));
            if (select count(*) from public.event_log where entity_id=tg_publication
                and event_type='exact_telegram_attempt_started')<>1 then
                raise exception 'telegram_attempt_was_not_exactly_once';
            end if;
            raise exception 'synthetic_rollback_telegram_attempt' using errcode='P7702';
        exception when sqlstate 'P7702' then null;end;
        -- Exact Typefully coexistence/attribution checks, synthetic DB ONLY.
        -- No provider or media/draft attempt is made by these assertions.
        declare
            approved public.approvals; spoof public.approvals;
            slot_id uuid; publication_id uuid; provider_job_id uuid;
            projected jsonb; old_claim text; old_route text; tg_handle text;
            old_day date;
        begin
            select * into approved from public.approvals where workspace_id=w
                and content_item_id=i and content_version_id=v;
            select id into slot_id from private.typefully_daily_slots where workspace_id=w
                and content_item_id=i;
            select id into publication_id from public.publications where workspace_id=w
                and content_item_id=i and channel='telegram';
            select id into provider_job_id from public.jobs where workspace_id=w
                and content_item_id=i and job_kind='publish';
            old_claim:=current_setting('request.jwt.claim.role',true);
            perform set_config('request.jwt.claim.role','',true);
            if private.reserve_content_ops_typefully_slot(
                    (result->>'decision_id')::uuid,actor,release_sha) is distinct from slot_id then
                raise exception 'typefully_exact_slot_replay_changed';
            end if;
            projected:=private.typefully_draft_candidate_owner(w,i,v,approved.id);
            if projected is null or projected->>'reviewer_source'<>'telegram_principal'
               or projected#>>'{telegram_principal_approval,decision_id}'<>result->>'decision_id'
               or projected#>>'{telegram_principal_approval,review_principal_id}'<>actor::text then
                raise exception 'typefully_queued_pair_candidate_missing';
            end if;
            update private.content_ops_button_reviewers set active=false
                where workspace_id=w and client_id=client and actor_id=actor;
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is not null then
                raise exception 'typefully_revoked_reviewer_accepted';
            end if;
            update private.content_ops_button_reviewers set active=true
                where workspace_id=w and client_id=client and actor_id=actor;
            update private.content_ops_button_identities set active=false
                where workspace_id=w and actor_id=actor;
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is not null then
                raise exception 'typefully_revoked_identity_accepted';
            end if;
            update private.content_ops_button_identities set active=true
                where workspace_id=w and actor_id=actor;
            update private.content_ops_button_reviews set state='held' where id=review;
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is not null then
                raise exception 'typefully_held_review_accepted';
            end if;
            update private.content_ops_button_reviews set state='active',epoch=epoch+1 where id=review;
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is not null then
                raise exception 'typefully_changed_review_epoch_accepted';
            end if;
            update private.content_ops_button_reviews set epoch=epoch-1 where id=review;
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is null then
                raise exception 'typefully_restored_scope_not_ready';
            end if;
            begin
                perform public.get_typefully_draft_candidate(w,i,v,approved.id);
                raise exception 'typefully_public_candidate_auth_bypassed';
            exception when insufficient_privilege then null;end;
            -- A source string, wrong principal, or invented decision is not
            -- attribution. Composite copies do not mutate immutable approvals.
            spoof:=approved; spoof.review_principal_id:=gen_random_uuid();
            if private.typefully_approval_attribution_valid(spoof) then
                raise exception 'typefully_spoofed_principal_accepted';
            end if;
            spoof:=approved; spoof.idempotency_key:='content-ops-final:'||gen_random_uuid()::text;
            if private.typefully_approval_attribution_valid(spoof) then
                raise exception 'typefully_invented_final_decision_accepted';
            end if;
            spoof:=approved; spoof.reviewer_source:='codex:content-qa';
            if private.typefully_approval_attribution_valid(spoof) then
                raise exception 'typefully_agent_advisory_became_approval';
            end if;
            -- Stored opaque routes must match the handoff, and reservations
            -- additionally bind the actual configured social-set number.
            select route_binding into old_route from private.content_ops_publication_routes
                where workspace_id=w and client_id=client and channel='typefully_x';
            update private.content_ops_publication_routes set route_binding=repeat('9',64)
                where workspace_id=w and client_id=client and channel='typefully_x';
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is not null then
                raise exception 'typefully_changed_route_accepted';
            end if;
            update private.content_ops_publication_routes set route_binding=old_route
                where workspace_id=w and client_id=client and channel='typefully_x';
            begin
                perform private.require_typefully_approval_route(approved.id,12345);
                raise exception 'typefully_unbound_social_set_accepted';
            exception when check_violation then null;end;
            select kst_date into old_day from private.typefully_daily_slots where id=slot_id;
            update private.typefully_daily_slots set kst_date=old_day-1 where id=slot_id;
            begin
                perform private.reserve_content_ops_typefully_slot(
                    (result->>'decision_id')::uuid,actor,release_sha);
                raise exception 'typefully_stale_slot_reopened';
            exception when check_violation then null;end;
            update private.typefully_daily_slots set kst_date=old_day where id=slot_id;
            -- Simulated TG completion preserves the exact immutable version.
            tg_handle:=case client when 'yellow' then 'yellowkorea_ann'
                when 'babylon' then 'babylonbtc' when 'squid' then 'squid_kor_update'
                else 'origintrailkr' end;
            update public.publications set status='published',published_at=clock_timestamp(),
                external_id='123',external_url='https://t.me/'||tg_handle||'/123',
                delivery_attempt_id=gen_random_uuid(),delivery_started_at=clock_timestamp(),
                delivery_request_sha256=repeat('a',64) where id=publication_id;
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is not null then
                raise exception 'typefully_inconsistent_telegram_job_state_accepted';
            end if;
            update public.jobs set status='succeeded',finished_at=clock_timestamp()
                where id=provider_job_id;
            update public.content_items set status='published' where id=i;
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is null then
                raise exception 'typefully_published_pair_candidate_missing';
            end if;
            update public.publications set status='delivery_unknown' where id=publication_id;
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is not null then
                raise exception 'typefully_unknown_telegram_coexistence_accepted';
            end if;
            -- A nonmatching public URL is also a foreign-destination hold.
            update public.publications set status='published',external_url='https://t.me/other_channel/123'
                where id=publication_id;
            if private.typefully_draft_candidate_owner(w,i,v,approved.id) is not null then
                raise exception 'typefully_foreign_telegram_destination_accepted';
            end if;
            update public.publications set status='published',external_url='https://t.me/'||tg_handle||'/123'
                where id=publication_id;
            -- No media allocation, upload, draft attempt, X post or schedule.
            if exists(select 1 from private.typefully_media_allocation_attempts where content_item_id=i)
               or exists(select 1 from private.typefully_draft_attempts where content_item_id=i) then
                raise exception 'typefully_readiness_created_provider_attempt';
            end if;
            perform set_config('request.jwt.claim.role',coalesce(old_claim,''),true);
        end;
        -- Reconciliation survives stale source/revocation and never requeues.
        update public.source_feeds set last_polled_at=clock_timestamp()-interval '2 hours' where id=feed;
        update private.content_ops_button_reviewers set active=false where workspace_id=w;
        replay:=private.read_content_ops_final_publication_terminal(card,actor,operation_key);
        if replay <> result-'reused' then
            raise exception 'final_queue_terminal_lost_original_receipt';
        end if;
        if (select count(*) from public.jobs where content_item_id=i and job_kind='publish')<>1 then
            raise exception 'final_queue_duplicated';
        end if;
    end loop;
end $$;
rollback;
