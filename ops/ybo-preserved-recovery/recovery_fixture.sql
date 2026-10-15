-- Synthetic disposable schema only. Never execute on a hosted database.
create schema preserved_fixture;
create function preserved_fixture.clock() returns timestamptz language plpgsql volatile as $$
declare n integer:=coalesce(nullif(current_setting('preserved_fixture.calls',true),''),'0')::integer+1;
  later text:=nullif(current_setting('preserved_fixture.advance_after_scan',true),'');
begin
  perform set_config('preserved_fixture.calls',n::text,false);
  if n>=2 and later is not null then return later::timestamptz; end if;
  return coalesce(nullif(current_setting('preserved_fixture.now',true),''),'2026-10-15T12:00:00Z')::timestamptz;
end $$;

create function preserved_fixture.seed() returns void language plpgsql as $$
declare w uuid:='5cc143cc-b253-4841-8a6c-ab3ac98623a6';
  j uuid:='e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af';
  s uuid:='35ae7c96-993a-4453-9946-3263f4f2842a';
  r uuid:='52f95e4e-1f66-549a-baa4-32b0fdf82b30'; f uuid:=gen_random_uuid();
  body text:='Synthetic preserved source for disposable fixture';
  image text:='https://pbs.twimg.com/media/fixture.jpg';
  u text:='https://x.com/babylonlabs_io/status/2110380535927427174';
begin
  insert into public.workspaces(id,name,slug) values(w,'Preserved fixture','preserved-fixture') on conflict do nothing;
  insert into public.workspace_clients(workspace_id,client_id,display_name) values(w,'babylon','Fixture')
    on conflict(workspace_id,client_id) do update set active=true;
  delete from private.official_x_daily_slots where workspace_id=w and client_id='babylon';
  update public.jobs set idempotency_key=null where workspace_id=w and client_id='babylon';
  update public.source_feeds set active=false where workspace_id=w and client_id='babylon' and active;
  insert into public.source_feeds(id,workspace_id,client_id,provider,name,handle,active,poll_interval_minutes,last_polled_at)
    values(f,w,'babylon','x','Fixture','@babylonlabs_io',true,15,'2026-10-15T11:55:00Z');
  insert into public.source_items(id,workspace_id,client_id,source_feed_id,external_id,source_type,canonical_url,
    author_handle,published_at,body,source_hash,media)
    values(s,w,'babylon',f,'2110380535927427174','tweet',u,'@babylonlabs_io','2026-10-14T14:41:43Z',body,
      repeat('1',64),jsonb_build_array(jsonb_build_object('type','photo','url',image)));
  insert into public.jobs(id,workspace_id,client_id,job_kind,status,attempts,max_attempts,idempotency_key,
    available_at,created_at,started_at,locked_by,locked_at,lease_expires_at,input,output)
    values(j,w,'babylon','generate','running',1,3,'official-x-review:v1:2026-10-15:babylon',
      '2026-10-15T07:49:29Z','2026-10-15T07:49:29Z','2026-10-15T07:49:29Z',
      'ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d','2026-10-15T07:49:29Z','2026-10-15T08:04:29Z',
      jsonb_build_object('workflow','official_x_review_draft_v1','kst_date','2026-10-15',
      'source_item_ids',jsonb_build_array(s::text),'content_kind','daily_news','request_id',r,
      'source_content',body,'source_url',u,'source_image_url',image,'manual_only',false),'{"execution_plane":"studio_sync"}');
  insert into private.official_x_daily_slots(workspace_id,kst_date,client_id,slot,job_id) values(w,'2026-10-15','babylon',2,j);
  insert into private.official_x_source_state(workspace_id,client_id,source_item_id,queued_job_id,queued_at)
    values(w,'babylon',s,j,'2026-10-15T07:49:29Z');
  insert into private.official_x_style_reference_packs(workspace_id,client_id,request_id,primary_source_item_id,style_references,reference_pack_hash)
    values(w,'babylon',r,s,'[]',repeat('b',32));
  perform set_config('preserved_fixture.now','2026-10-15T07:40:59Z',false);
  insert into private.ybo_trial_readiness_anchor values(w,'1a2bef47-4827-4bc3-9b4d-5baf2714015d',
    repeat('b',64),'b77a54e12dcdadefd001e4dd8cca255dead96d3c','2026-10-15T07:40:59Z','2026-10-15T09:40:59Z','2026-10-15');
  perform set_config('preserved_fixture.now','2026-10-15T12:00:00Z',false);
end $$;

create function preserved_fixture.register(changes text default null) returns void language plpgsql as $$
begin
  insert into private.ybo_preserved_babylon_compatibility_receipts values(
    '14831c91-0bae-45d9-9658-aa225e2a24cb','5cc143cc-b253-4841-8a6c-ab3ac98623a6',
    'd346ceea-4ea9-479f-a5cf-05097b50ce48','e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af',
    '52f95e4e-1f66-549a-baa4-32b0fdf82b30','35ae7c96-993a-4453-9946-3263f4f2842a',
    'b77a54e12dcdadefd001e4dd8cca255dead96d3c',repeat('a',40),
    '000a4243bf92c082db1120b6ccbd6aa8e2ade95ed355aaef124f79a31c6aa0a4',
    '2f71160c21ebb2b40d000ccf6cf6d36553c0039766416d52e89d3ab0af653982',
    repeat('a',64),repeat('b',64),'1e27adee8d0b14a8af4af546b19e0bbb38e96b2e93981d0f989bd710fd2bebcd',
    'fb3999197f2c07def1aab966a2b7c6a5c48ff4d12c446667632c74e0b188c177',
    (select encode(extensions.digest(convert_to(input::text,'UTF8'),'sha256'),'hex') from public.jobs
      where id='e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af'),repeat('b',32),repeat('e',64),repeat('f',64),repeat('1',64),
    '2026-10-15T12:00:00Z','babylon');
  create temporary table proposed_anchor on commit drop as select * from private.ybo_recovery_readiness_anchor;
  insert into proposed_anchor values('5cc143cc-b253-4841-8a6c-ab3ac98623a6','d346ceea-4ea9-479f-a5cf-05097b50ce48',
    '14831c91-0bae-45d9-9658-aa225e2a24cb',repeat('e',64),repeat('a',40),'2026-10-15T12:00:00Z','2026-10-15T14:00:00Z','2026-10-15');
  if changes is not null then execute 'update proposed_anchor set '||changes; end if;
  insert into private.ybo_recovery_readiness_anchor select * from proposed_anchor;
end $$;

create function preserved_fixture.context() returns jsonb language plpgsql volatile as $$
begin
  return public.get_exact_preserved_babylon_context('5cc143cc-b253-4841-8a6c-ab3ac98623a6',
    'e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af','14831c91-0bae-45d9-9658-aa225e2a24cb');
end
$$;
create function preserved_fixture.claim() returns jsonb language plpgsql volatile as $$
declare h text;
begin
  perform set_config('preserved_fixture.calls','0',false);
  select input_sha256 into h from private.ybo_preserved_babylon_compatibility_receipts
    where receipt_id='14831c91-0bae-45d9-9658-aa225e2a24cb';
  return public.claim_exact_preserved_babylon_job('5cc143cc-b253-4841-8a6c-ab3ac98623a6',
    'e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af','52f95e4e-1f66-549a-baa4-32b0fdf82b30',
    '35ae7c96-993a-4453-9946-3263f4f2842a',coalesce(h,repeat('a',64)),repeat('b',32),
    '14831c91-0bae-45d9-9658-aa225e2a24cb','ybo-trial:d346ceea-4ea9-479f-a5cf-05097b50ce48',900);
end $$;

create function preserved_fixture.snapshot(exclude_lease boolean default false) returns jsonb language plpgsql as $$
declare r record; rows jsonb; result jsonb:='{}'; condition text;
begin
  for r in select schemaname,tablename from pg_tables where schemaname in ('public','private','agent_runtime')
    order by schemaname,tablename loop
    if exclude_lease and r.tablename='ybo_preserved_babylon_handoffs' then continue; end if;
    condition:=case when exclude_lease and r.schemaname='public' and r.tablename='jobs'
      then ' where t.id<>''e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af''::uuid' else '' end;
    execute format('select coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text),''[]''::jsonb) from %I.%I t%s',
      r.schemaname,r.tablename,condition) into rows;
    result:=result||jsonb_build_object(r.schemaname||'.'||r.tablename,rows);
  end loop;
  return result;
end $$;
create function preserved_fixture.assert_no_claim() returns boolean language plpgsql as $$
declare before jsonb:=preserved_fixture.snapshot(); result jsonb;
begin
  result:=preserved_fixture.claim();
  if result is not null or before is distinct from preserved_fixture.snapshot() then
    raise exception 'preserved_negative_changed_state'; end if;
  return true;
end $$;
create function preserved_fixture.assert_handoff() returns jsonb language plpgsql as $$
declare before jsonb:=preserved_fixture.snapshot(true); before_job jsonb; after_job jsonb; c jsonb; got jsonb;
begin
  select to_jsonb(j) into before_job from public.jobs j where id='e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af';
  c:=preserved_fixture.context();
  if c is null or c->'style_reference_pack'->'reused' is distinct from 'true'::jsonb then raise exception 'preserved_context_missing'; end if;
  got:=preserved_fixture.claim();
  if got is null or got->>'attempts'<>'1' or got->>'locked_by'<>'ybo-trial:d346ceea-4ea9-479f-a5cf-05097b50ce48'
    or (got->>'lease_expires_at')::timestamptz<>'2026-10-15T12:15:00Z'::timestamptz then
    raise exception 'preserved_claim_missing'; end if;
  select to_jsonb(j) into after_job from public.jobs j where id='e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af';
  return jsonb_build_object('lease_only',before_job-array['locked_by','locked_at','lease_expires_at','updated_at']
      =after_job-array['locked_by','locked_at','lease_expires_at','updated_at'],
    'replay_rejected',preserved_fixture.claim() is null,
    'other_rows_unchanged',before=preserved_fixture.snapshot(true));
end $$;
