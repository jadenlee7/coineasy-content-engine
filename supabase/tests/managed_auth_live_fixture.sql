-- LOCAL DISPOSABLE STACK ONLY. Synthetic transport-unknown data; no provider.
create schema managed_auth_live_test;
revoke all on schema managed_auth_live_test from public,anon,authenticated,service_role;
create function managed_auth_live_test.fixture(operator_id uuid) returns jsonb language plpgsql set timezone = 'UTC' as $$
declare
 workspace_id uuid := gen_random_uuid(); owner_id uuid := gen_random_uuid();
 item_id uuid := gen_random_uuid(); asset_id uuid := gen_random_uuid(); request_id uuid := gen_random_uuid();
 version_id uuid; publication_id uuid; job_id uuid;
 generated jsonb; requested jsonb; claimed jsonb; result jsonb; request jsonb;
 source_hash text := repeat('a',64); banner_hash text := repeat('b',64);
begin
 insert into auth.users(id) values(owner_id);
 insert into public.workspaces(id,name,slug) values(workspace_id,'LOCAL managed Auth fixture','local-managed-'||workspace_id::text);
 insert into public.workspace_clients(workspace_id,client_id,display_name,active) values(workspace_id,'squid','Local Squid',true);
 insert into public.workspace_members(workspace_id,user_id,role,status) values(workspace_id,owner_id,'owner','active');
 insert into storage.objects(bucket_id,name) values('content-studio',workspace_id||'/squid/'||asset_id||'/news-card.png');
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 perform set_config('request.jwt.claims',jsonb_build_object('sub',owner_id,'role','authenticated')::text,true);
 generated := public.record_generated_content(item_id,workspace_id,'squid','daily_news','LOCAL synthetic fixture',
   jsonb_build_object('request_hash',source_hash), jsonb_build_object('telegram','LOCAL synthetic text; never sent.'),
   jsonb_build_object('request_hash',source_hash,'mock_mode',false,'fact_check',jsonb_build_object(
     'schema_version','1.0','policy_version','double-fact-check@1','content_kind','daily_news','status','review',
     'human_review_required',true,'input_sha256',source_hash,'output_sha256',banner_hash,'checks',jsonb_build_array(
       jsonb_build_object('id','source_evidence','status','review','label','Local source','detail','Synthetic only.','metrics','{}'::jsonb),
       jsonb_build_object('id','output_claims','status','pass','label','Local output','detail','Synthetic only.','metrics','{}'::jsonb)))),
   jsonb_build_object('asset_id',asset_id,'filename','news-card.png','storage_path',workspace_id||'/squid/'||asset_id||'/news-card.png',
     'mime_type','image/png','byte_size',128,'sha256',banner_hash,'width',1080,'height',1080), 'local-managed-auth-test@1');
 version_id := (generated->>'content_version_id')::uuid;
 perform public.record_studio_content_review_v2(workspace_id,item_id,version_id,'approved','double-fact-check@1',true,true,'{}'::text[],null,'local-managed-'||item_id);
 requested := public.request_studio_telegram_publication(workspace_id,item_id,version_id,request_id::text);
 publication_id := (requested->>'publication_id')::uuid; job_id := (requested->>'job_id')::uuid;
 claimed := public.claim_exact_telegram_publication_job(workspace_id,'local-test-worker',300);
 if claimed->>'job_id' is distinct from job_id::text then raise exception 'local fixture wrong job'; end if;
 perform public.mark_exact_telegram_attempt_started(job_id,'local-test-worker',repeat('c',64));
 result := public.fail_exact_telegram_publication_job(job_id,'local-test-worker','telegram_delivery_unknown',false);
 if result->>'status' is distinct from 'delivery_unknown' then raise exception 'local fixture wrong status'; end if;
 update public.publications set delivery_started_at=clock_timestamp()-interval '20 minutes' where id=publication_id;
 insert into private.managed_telegram_inspect_releases(release_id,workspace_id,project_ref,release_sha,migration_sha256,verified_deployment_reference,enabled,valid_from,expires_at)
 values(gen_random_uuid(),workspace_id,'abcdefghijklmnopqrst',repeat('a',40),repeat('b',64),'local:test:synthetic',true,clock_timestamp()-interval '1 minute',clock_timestamp()+interval '2 hours');
 insert into private.managed_telegram_inspect_allowlist(allowlist_id,user_id,workspace_id,operation,approved_by,enabled,valid_from,expires_at)
 select gen_random_uuid(),operator_id,workspace_id,operation,'operator:local-test',true,clock_timestamp()-interval '1 minute',clock_timestamp()+interval '2 hours'
 from unnest(array['consent_inspect','inspect']) operation;
 request := jsonb_build_object('schema_version','telegram-resolution-inspect-request@1','project_ref','abcdefghijklmnopqrst',
  'environment','production','client_id','squid','release_sha',repeat('a',40),'workspace_id',workspace_id,'content_item_id',item_id,
  'content_version_id',version_id,'publication_id',publication_id,'job_id',job_id,'resolution_id',gen_random_uuid(),
  'operator_approval_id',gen_random_uuid(),'inspected_by','auth:'||operator_id,'approved_by','operator:local-test',
  'expires_at',to_char(clock_timestamp()+interval '1 hour','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
  'public_audit',jsonb_build_object('schema_version','telegram-public-channel-audit@1','scan_source','public_telegram_web_history',
    'public_channel','squid_kor_update','first_message_id','500','last_message_id','620','message_count',121,
    'checked_at',to_char(clock_timestamp()-interval '1 minute','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'caption_match_count',0,'png_match_count',0,'snapshot_sha256',repeat('e',64)));
 return jsonb_build_object('request',request,'request_sha256',private.managed_telegram_inspect_hash(request),'consent_id',gen_random_uuid());
end;
$$;
revoke all on function managed_auth_live_test.fixture(uuid) from public,anon,authenticated,service_role;
