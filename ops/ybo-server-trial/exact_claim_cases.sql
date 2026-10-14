-- TEST ONLY. Run after exact_claim_fixture.sql and the clock-substituted copy.
begin;
select set_config('request.jwt.claim.role','service_role',true);
do $$
declare ctx jsonb; result jsonb; before_state jsonb; original_job jsonb; after_job jsonb;
 signature text:='public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)';
 c text; mt text; scenario integer; changes jsonb; j uuid; s uuid; f uuid; w uuid; r uuid;
 extra_ctx jsonb; extra_item uuid; case_total integer:=0;
begin
 perform exact_claim_fixture.assert_true(not has_function_privilege('anon',signature,'EXECUTE')
   and not has_function_privilege('authenticated',signature,'EXECUTE')
   and has_function_privilege('service_role',signature,'EXECUTE'),'acl_named_roles');
 perform exact_claim_fixture.assert_true(not exists(select 1 from pg_proc p,
   lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
   where p.oid=signature::regprocedure and a.grantee=0 and a.privilege_type='EXECUTE'),'acl_public');
 if exists(select 1 from pg_roles where rolname='coineasy_batch_producer') then
   perform exact_claim_fixture.assert_true(not has_function_privilege('coineasy_batch_producer',signature,'EXECUTE'),
     'no_extra_batch_role_privilege'); end if;
 perform exact_claim_fixture.assert_true(exists(select 1 from pg_proc where oid=signature::regprocedure
   and prosecdef and proconfig @> array['search_path=""']),'definer_empty_search_path');

 -- Every supported client and media type has a positive path.
 foreach c in array array['babylon','origintrail'] loop
   foreach mt in array array['photo','video','animated_gif'] loop
     ctx:=exact_claim_fixture.seed(c);j:=(ctx->>'job_id')::uuid;s:=(ctx->>'source_item_id')::uuid;
     update public.source_items set media=jsonb_set(media,'{0,type}',to_jsonb(mt)) where id=s;
     select to_jsonb(t) into original_job from public.jobs t where id=j;
     before_state:=exact_claim_fixture.snapshot(j);
     result:=exact_claim_fixture.call_claim(ctx);
     select to_jsonb(t) into after_job from public.jobs t where id=j;
     perform exact_claim_fixture.assert_true(result is not null and result->>'job_id'=j::text
       and result->>'workspace_id'=ctx->>'workspace_id' and result->>'client_id'=c
       and result->>'status'='running' and result->>'attempts'='1' and result->>'max_attempts'='3'
       and result->>'locked_by'='ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d' and result->'origintrail_batch_eligible'='false'::jsonb
       and result->'batch_handoff_recovery_only'='false'::jsonb and result->'input'=original_job->'input'
       and (select count(*) from jsonb_object_keys(result))=11,'positive_return_shape');
     perform exact_claim_fixture.assert_true((after_job->>'locked_at')::timestamptz='2026-10-14T15:40:51Z'::timestamptz
       and (after_job->>'lease_expires_at')::timestamptz='2026-10-14T15:55:51Z'::timestamptz,
       'lease_transition');
     perform exact_claim_fixture.assert_true((after_job-array['status','attempts','locked_by','locked_at',
       'lease_expires_at','started_at','finished_at','last_error_code','last_error_message','updated_at'])=
       (original_job-array['status','attempts','locked_by','locked_at','lease_expires_at','started_at',
       'finished_at','last_error_code','last_error_message','updated_at']),'only_sanctioned_fields');
     perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(j),'other_rows_unchanged_positive');
     before_state:=exact_claim_fixture.snapshot();
     perform exact_claim_fixture.assert_true(exact_claim_fixture.call_claim(ctx) is null,'no_repeat_claim');
     perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(),'no_repeat_mutation');
     case_total:=case_total+1;
   end loop;
 end loop;

 -- Genuine no-media sources use an explicit empty string, not a missing URL.
 foreach c in array array['babylon','origintrail'] loop
   ctx:=exact_claim_fixture.seed(c);j:=(ctx->>'job_id')::uuid;s:=(ctx->>'source_item_id')::uuid;
   update public.source_items set media='[]' where id=s;
   update public.jobs set input=jsonb_set(input,'{source_image_url}','""') where id=j;
   changes:=jsonb_build_object('input_sha256',(select encode(extensions.digest(
     convert_to(input::text,'UTF8'),'sha256'),'hex') from public.jobs where id=j));
   before_state:=exact_claim_fixture.snapshot(j);
   result:=exact_claim_fixture.call_claim(ctx,changes);
   perform exact_claim_fixture.assert_true(result is not null and result->>'job_id'=j::text
     and result->'input'->'source_image_url'='""'::jsonb and result->>'attempts'='1',
     'empty_media_exact_initial_claim_'||c);
   perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(j),'logo_other_rows_unchanged');
   before_state:=exact_claim_fixture.snapshot();
   perform exact_claim_fixture.assert_true(exact_claim_fixture.call_claim(ctx,changes) is null,'logo_no_repeat');
   perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(),'logo_no_repeat_mutation');
   case_total:=case_total+1;
 end loop;

 ctx:=exact_claim_fixture.seed();j:=(ctx->>'job_id')::uuid;s:=(ctx->>'source_item_id')::uuid;
 f:=(ctx->>'feed_id')::uuid;w:=(ctx->>'workspace_id')::uuid;r:=(ctx->>'request_id')::uuid;
 for scenario in 1..72 loop
   declare case_ctx jsonb:=ctx;
   begin
     changes:='{}';
     case scenario
       when 1 then changes:=jsonb_build_object('workspace_id',gen_random_uuid());
       when 2 then changes:='{"client_id":"origintrail"}';
       when 3 then changes:=jsonb_build_object('job_id',gen_random_uuid());
       when 4 then changes:=jsonb_build_object('request_id',gen_random_uuid());
       when 5 then changes:=jsonb_build_object('source_item_id',gen_random_uuid());
       when 6 then changes:=jsonb_build_object('input_sha256',repeat('f',64));
       when 7 then changes:=jsonb_build_object('style_pack_hash',repeat('f',32));
       when 8 then update public.jobs set status='retrying' where id=j;
       when 9 then update public.jobs set status='failed' where id=j;
       when 10 then update public.jobs set attempts=1 where id=j;
       when 11 then update public.jobs set max_attempts=1 where id=j;
       when 12 then update public.jobs set available_at='2026-10-14T15:40:52Z' where id=j;
       when 13 then case_ctx:=exact_claim_fixture.seed('babylon','2026-10-14T15:10:50Z');
       when 14 then case_ctx:=exact_claim_fixture.seed('babylon','2026-10-14T15:40:52Z');
       when 15 then update public.jobs set output='{"execution_plane":"studio_sync"}' where id=j;
       when 16 then update public.jobs set started_at='2026-10-14T15:39:51Z' where id=j;
       when 17 then update public.jobs set finished_at='2026-10-14T15:39:51Z' where id=j;
       when 18 then update public.jobs set locked_at='2026-10-14T15:39:51Z' where id=j;
       when 19 then update public.jobs set idempotency_key='unrelated-key' where id=j;
       when 20 then update public.jobs set input=jsonb_set(input,'{manual_only}','true') where id=j;
       when 21 then update public.jobs set input=jsonb_set(input,'{content_kind}','"tutorial"') where id=j;
       when 22 then update public.jobs set input=input||'{"same_day_refresh_id":"not-natural"}' where id=j;
       when 23 then update public.jobs set input=jsonb_set(input,'{kst_date}','"2026-10-14"') where id=j;
       when 24 then update public.jobs set input=jsonb_set(input,'{source_content}','"Wrong but long enough body"') where id=j;
       when 25 then update public.jobs set input=jsonb_set(input,'{source_url}','"https://x.com/babylonlabs_io/status/42"') where id=j;
       when 26 then update public.jobs set input=jsonb_set(input,'{source_image_url}','""') where id=j;
       when 27 then update public.jobs set input=jsonb_set(input,'{source_image_url}','"https://pbs.twimg.com/media/mismatch.jpg"') where id=j;
       when 28 then update public.jobs set input=jsonb_set(input,'{source_image_url}','"https://untrusted.test/image.jpg"') where id=j;
       when 29 then update public.source_items set author_handle='@Wrong' where id=s;
       when 30 then update public.source_items set external_id='42' where id=s;
       when 31 then update public.source_items set body='Short' where id=s;
       when 32 then update public.source_items set published_at='2026-10-13T15:40:51Z' where id=s;
       when 33 then update public.source_items set published_at='2026-10-14T15:40:52Z' where id=s;
       when 34 then update public.source_feeds set active=false where id=f;
       when 35 then update public.source_feeds set handle='@Wrong' where id=f;
       when 36 then update public.source_feeds set poll_interval_minutes=30 where id=f;
       when 37 then update public.source_feeds set last_polled_at='2026-10-14T15:10:51Z' where id=f;
       when 38 then update public.source_feeds set last_polled_at='2026-10-14T15:40:52Z' where id=f;
       when 39 then update public.source_items set media='[]' where id=s;
       when 40 then update public.source_items set media=jsonb_set(media,'{0,type}','"other"') where id=s;
       when 41 then update public.workspace_clients set active=false where workspace_id=w;
       when 42 then update private.official_x_source_state set queued_job_id=null,queued_at=null where source_item_id=s;
       when 43 then delete from private.official_x_daily_slots where job_id=j;
       when 44 then insert into public.content_items(id,workspace_id,client_id,content_kind) values(r,w,'babylon','daily_news');
       when 45 then insert into public.jobs(workspace_id,client_id,job_kind,status,input)
           select workspace_id,client_id,job_kind,'queued',input from public.jobs where id=j;
       when 46 then insert into public.jobs(workspace_id,client_id,job_kind,status,input)
           select workspace_id,client_id,job_kind,'failed',jsonb_set(input,'{request_id}',to_jsonb(gen_random_uuid()::text))
           from public.jobs where id=j;
       when 47 then insert into public.jobs(workspace_id,client_id,job_kind,status,input)
           select workspace_id,client_id,job_kind,'failed',jsonb_set(input,'{source_item_ids}','[]')
           from public.jobs where id=j;
       when 48 then insert into public.source_items(workspace_id,client_id,source_feed_id,external_id,source_type,
           canonical_url,author_handle,published_at,body,source_hash)
           values(w,'babylon',f,'987654321','tweet','https://x.com/babylonlabs_io/status/987654321','@babylonlabs_io',
             '2026-10-14T15:39:51Z','A newer synthetic source',repeat('c',64));
       when 49 then perform set_config('exact_claim_fixture.now','2026-10-14T15:10:50Z',true);
       when 50 then perform set_config('exact_claim_fixture.now','2026-10-14T17:10:51Z',true);
       when 51 then perform set_config('exact_claim_fixture.now','2026-10-14T17:09:51Z',true);
       when 52 then update public.jobs set input=input-'source_image_url' where id=j;
       when 53 then extra_ctx:=exact_claim_fixture.seed('origintrail');
         update public.jobs set input=jsonb_set(input,'{request_id}',to_jsonb(r::text))
         where id=(extra_ctx->>'job_id')::uuid;
       when 54 then extra_item:=gen_random_uuid();
         insert into public.content_items(id,workspace_id,client_id,content_kind) values(extra_item,w,'babylon','daily_news');
         insert into public.content_source_links(workspace_id,client_id,content_item_id,source_item_id)
         values(w,'babylon',extra_item,s);
       when 55 then update public.jobs set status='running',locked_by='other-worker',locked_at='2026-10-14T15:39:51Z',
         lease_expires_at='2026-10-14T15:55:51Z' where id=j;
       when 56 then update public.jobs set job_kind='render' where id=j;
       when 57 then update public.source_feeds set provider='rss' where id=f;
       when 58 then update public.source_items set published_at=null where id=s;
       when 59 then update public.source_feeds set last_polled_at=null where id=f;
       when 60 then update public.jobs set input=jsonb_set(input,'{source_image_url}',
         to_jsonb('https://pbs.twimg.com/media/'||repeat('a',2050))) where id=j;
         update public.source_items set media=jsonb_set(media,'{0,url}',
           to_jsonb('https://pbs.twimg.com/media/'||repeat('a',2050))) where id=s;
       when 61 then update public.source_items set media='[]' where id=s;
         update public.jobs set input=jsonb_set(input,'{source_image_url}','null') where id=j;
       when 62 then update public.source_items set media='[]' where id=s;
         update public.jobs set input=input-'source_image_url' where id=j;
       when 63 then update public.source_items set media='[]' where id=s;
         update public.jobs set input=jsonb_set(input,'{source_image_url}','0') where id=j;
       when 64 then update public.source_items set media='[]' where id=s;
         update public.jobs set input=jsonb_set(input,'{source_image_url}','false') where id=j;
       when 65 then update public.source_items set media='[]' where id=s;
         update public.jobs set input=jsonb_set(input,'{source_image_url}','" "') where id=j;
       when 66 then update public.source_items set media='[null]' where id=s;
         update public.jobs set input=jsonb_set(input,'{source_image_url}','""') where id=j;
       when 67 then update public.source_items set media='[{}]' where id=s;
         update public.jobs set input=jsonb_set(input,'{source_image_url}','""') where id=j;
       when 68 then update public.source_items set media='[{"type":"photo"}]' where id=s;
         update public.jobs set input=jsonb_set(input,'{source_image_url}','""') where id=j;
       when 69 then update public.source_items set media='[]' where id=s;
         update public.jobs set input=jsonb_set(input,'{source_image_url}','[]') where id=j;
       when 70 then update public.source_items set media='[]' where id=s;
         update public.jobs set input=jsonb_set(input,'{source_image_url}','{}') where id=j;
       when 71 then changes:=jsonb_build_object('worker','fixture-worker');
       when 72 then changes:=jsonb_build_object('worker','ybo-trial:3eceb628-0f6b-4336-aad6-799f907c8157');
     end case;
     -- Cases altering input must prove full identity checks, not merely digest rejection.
     if scenario between 20 and 28 or scenario=52 or scenario between 60 and 70 then
       changes:=changes||jsonb_build_object('input_sha256',(select encode(extensions.digest(
         convert_to(input::text,'UTF8'),'sha256'),'hex') from public.jobs where id=j)); end if;
     before_state:=exact_claim_fixture.snapshot();
     result:=exact_claim_fixture.call_claim(case_ctx,changes);
     perform exact_claim_fixture.assert_true(result is null,'negative_case_'||scenario);
     perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(),'negative_unchanged_'||scenario);
     raise exception 'fixture_rollback_case' using errcode='P7701';
   exception when sqlstate 'P7701' then null; end;
   case_total:=case_total+1;
 end loop;
 -- Wrong JWT role cannot invoke a security-definer through a privileged session.
 before_state:=exact_claim_fixture.snapshot();
 begin
   perform set_config('request.jwt.claim.role','authenticated',true);
   perform exact_claim_fixture.call_claim(ctx);
   raise exception 'role_guard_missing';
 exception when insufficient_privilege then null; end;
 perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(),'wrong_role_unchanged');
 -- Lease close to fixed gate end is clipped, never extends beyond trial deadline.
 perform set_config('exact_claim_fixture.now','2026-10-14T17:09:50Z',true);
 update public.source_feeds set last_polled_at='2026-10-14T17:05:51Z' where id=f;
 result:=exact_claim_fixture.call_claim(ctx);
 perform exact_claim_fixture.assert_true((result->>'lease_expires_at')::timestamptz='2026-10-14T17:10:51Z',
   'lease_bounded_by_gate_end');
 -- Yellow is explicitly excluded, even with valid same-day source/job/worker.
 perform set_config('exact_claim_fixture.now','2026-10-14T15:40:51Z',true);
 ctx:=exact_claim_fixture.seed('yellow');
 before_state:=exact_claim_fixture.snapshot();
 perform exact_claim_fixture.assert_true(exact_claim_fixture.call_claim(ctx) is null,'yellow_excluded');
 perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(),'yellow_no_mutation');
 raise notice 'exact_claim_fixture_cases_passed: % positive/negative + ACL/role/lease checks',case_total;
end $$;
rollback;
