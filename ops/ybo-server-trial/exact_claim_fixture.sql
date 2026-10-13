-- TEST ONLY: synthetic rows and controlled clock for a disposable local DB.
-- Never install this schema/function on production. No provider/Telegram I/O.
create schema exact_claim_fixture;
create function exact_claim_fixture.clock() returns timestamptz
language plpgsql volatile as $$
declare call_count integer:=coalesce(nullif(current_setting('exact_claim_fixture.clock_calls',true),''),'0')::integer+1;
  final_now text:=nullif(current_setting('exact_claim_fixture.pre_update_now',true),'');
begin
  perform set_config('exact_claim_fixture.clock_calls',call_count::text,false);
  -- TEST ONLY: simulate time consumed by validation scans, without changing
  -- the production clock or exposing a clock override in the production RPC.
  if call_count>=3 and final_now is not null then return final_now::timestamptz; end if;
  return coalesce(nullif(current_setting('exact_claim_fixture.now',true),'')::timestamptz,
    '2026-10-14T00:30:00Z'::timestamptz)
    + case when current_setting('exact_claim_fixture.advance',true)='true'
        then clock_timestamp()-statement_timestamp() else interval '0' end;
end
$$;

create function exact_claim_fixture.seed(c text default 'yellow',
    job_created_at timestamptz default '2026-10-14T00:29:00Z') returns jsonb
language plpgsql as $$
declare w uuid:=gen_random_uuid(); j uuid:=gen_random_uuid(); s uuid:=gen_random_uuid();
 f uuid:=gen_random_uuid(); r uuid:=gen_random_uuid(); h text; u text;
 t timestamptz:='2026-10-14T00:30:00Z'; image text:='https://pbs.twimg.com/media/fixture.jpg?name=orig';
 body text:='Synthetic official source for isolated claim tests';
begin
 h:=case c when 'yellow' then '@Yellow' when 'babylon' then '@babylonlabs_io'
   when 'origintrail' then '@origin_trail' else '@SquidRouter' end;
 u:='https://x.com/'||substring(h from 2)||'/status/123456789';
 insert into public.workspaces(id,name,slug) values(w,'Exact claim fixture','exact-'||w);
 insert into public.workspace_clients(workspace_id,client_id,display_name) values(w,c,'Fixture');
 insert into public.source_feeds(id,workspace_id,client_id,provider,name,handle,
   active,poll_interval_minutes,last_polled_at) values(f,w,c,'x','Fixture',h,true,15,t-interval '5 minutes');
 insert into public.source_items(id,workspace_id,client_id,source_feed_id,external_id,source_type,
   canonical_url,author_handle,published_at,body,source_hash,media)
 values(s,w,c,f,'123456789','tweet',u,h,t-interval '1 hour',body,repeat('a',64),
   jsonb_build_array(jsonb_build_object('type','photo','url',image)));
 insert into public.jobs(id,workspace_id,client_id,job_kind,status,attempts,max_attempts,
   idempotency_key,available_at,created_at,input,output)
 values(j,w,c,'generate','queued',0,3,'official-x-review:v1:2026-10-14:'||c,t-interval '1 minute',
   job_created_at,jsonb_build_object('workflow','official_x_review_draft_v1',
   'kst_date','2026-10-14','source_item_ids',jsonb_build_array(s::text),'content_kind','daily_news',
   'request_id',r,'source_content',body,'source_url',u,'source_image_url',image,'manual_only',false),'{}');
 insert into private.official_x_daily_slots(workspace_id,kst_date,client_id,slot,job_id)
 values(w,'2026-10-14',c,1,j);
 insert into private.official_x_source_state(workspace_id,client_id,source_item_id,queued_job_id,queued_at)
 values(w,c,s,j,t-interval '1 minute');
 insert into private.official_x_style_reference_packs(workspace_id,client_id,request_id,
   primary_source_item_id,style_references,reference_pack_hash) values(w,c,r,s,'[]',repeat('b',32));
 -- Unrelated queue/recovery states must remain byte-identical, including expired leases.
 insert into public.jobs(workspace_id,client_id,job_kind,status,attempts,max_attempts,input,output)
 values(w,c,'generate','queued',0,3,'{"workflow":"official_x_review_draft_v1","manual_only":false}','{}'),
   (w,c,'generate','retrying',1,3,'{"workflow":"official_x_review_draft_v1","manual_only":false}','{}'),
   (w,c,'generate','failed',3,3,'{"workflow":"official_x_review_draft_v1","manual_only":false}','{}');
 insert into public.jobs(workspace_id,client_id,job_kind,status,attempts,max_attempts,input,
   locked_by,locked_at,lease_expires_at)
 values(w,c,'generate','running',1,3,'{"workflow":"official_x_review_draft_v1","manual_only":false}',
   'expired-fixture',t-interval '2 hours',t-interval '1 hour');
 insert into public.event_log(workspace_id,entity_type,entity_id,event_type,data)
 values(w,'job',j,'fixture_event','{}');
 return jsonb_build_object('workspace_id',w,'client_id',c,'job_id',j,'request_id',r,'source_item_id',s,
   'feed_id',f,'input_sha256',(select encode(extensions.digest(convert_to(input::text,'UTF8'),'sha256'),'hex')
     from public.jobs where id=j),'style_pack_hash',repeat('b',32));
end $$;

create function exact_claim_fixture.call_claim(ctx jsonb, changes jsonb default '{}') returns jsonb
language plpgsql as $$
declare v jsonb:=ctx||changes;
begin
 perform set_config('exact_claim_fixture.clock_calls','0',false);
 return public.claim_exact_natural_review_draft_job((v->>'workspace_id')::uuid,v->>'client_id',
   (v->>'job_id')::uuid,(v->>'request_id')::uuid,(v->>'source_item_id')::uuid,
   v->>'input_sha256',v->>'style_pack_hash',coalesce(v->>'worker','fixture-worker'),
   coalesce((v->>'lease')::integer,900));
end $$;

create function exact_claim_fixture.snapshot(excluded_job uuid default null) returns jsonb
language plpgsql as $$
declare relation record; rows jsonb; result jsonb:='{}'; condition text;
begin
 -- All pre-existing application tables, not just the selected workspace.
 for relation in select schemaname,tablename from pg_tables
   where schemaname in ('public','private','agent_runtime') order by schemaname,tablename loop
   condition:=case when relation.schemaname='public' and relation.tablename='jobs'
      and excluded_job is not null then format(' where t.id<>%L::uuid',excluded_job) else '' end;
   execute format('select coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text),''[]''::jsonb) from %I.%I t%s',
     relation.schemaname,relation.tablename,condition) into rows;
   result:=result||jsonb_build_object(relation.schemaname||'.'||relation.tablename,rows);
 end loop;
 return result;
end $$;

create function exact_claim_fixture.assert_true(value boolean,label text) returns void
language plpgsql as $$begin
 if value is distinct from true then raise exception 'fixture_failed: %',label; end if;
end $$;
