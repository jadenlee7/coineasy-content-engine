-- DISPOSABLE TEST ONLY. Requires the exact-claim fixture and clock-substituted
-- quality context candidate. No production/provider/Telegram execution.
begin;
select set_config('request.jwt.claim.role','service_role',true);
select set_config('exact_claim_fixture.now','2026-10-14T15:40:51Z',true);
do $$
declare ctx jsonb; c text; j uuid; s uuid; f uuid; w uuid; r uuid; result jsonb;
  changes jsonb; before_state jsonb; scenario integer; extra_item uuid;
  case_ctx jsonb; case_w uuid; case_r uuid; case_j uuid; case_s uuid;
begin
  foreach c in array array['babylon','origintrail'] loop
    ctx:=exact_claim_fixture.seed(c);j:=(ctx->>'job_id')::uuid;s:=(ctx->>'source_item_id')::uuid;
    f:=(ctx->>'feed_id')::uuid;w:=(ctx->>'workspace_id')::uuid;r:=(ctx->>'request_id')::uuid;
    update public.source_items set media='[]' where id=s;
    update public.jobs set input=jsonb_set(input,'{source_image_url}','""') where id=j;
    changes:=jsonb_build_object('input_sha256',(select encode(extensions.digest(
      convert_to(input::text,'UTF8'),'sha256'),'hex') from public.jobs where id=j));
    perform exact_claim_fixture.assert_true(exact_claim_fixture.call_claim(ctx,changes) is not null,
      'logo_context_initial_claim_'||c);
    before_state:=exact_claim_fixture.snapshot();
    result:=public.get_quality_banner_generation_context(w,c,r);
    perform exact_claim_fixture.assert_true(result is not null
      and result->>'job_id'=j::text and result->>'source_item_id'=s::text
      and result->>'request_id'=r::text and result->>'client_id'=c
      and result->'source_image_url'='""'::jsonb
      and (select count(*) from jsonb_object_keys(result))=10,'logo_context_identity_'||c);
    perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(),'logo_context_readonly');

    for scenario in 1..31 loop
      begin
        case_w:=w;case_r:=r;
        case scenario
          when 1 then update public.jobs set input=input-'source_image_url' where id=j;
          when 2 then update public.jobs set input=jsonb_set(input,'{source_image_url}','null') where id=j;
          when 3 then update public.jobs set input=jsonb_set(input,'{source_image_url}','0') where id=j;
          when 4 then update public.jobs set input=jsonb_set(input,'{source_image_url}','false') where id=j;
          when 5 then update public.jobs set input=jsonb_set(input,'{source_image_url}','" "') where id=j;
          when 6 then update public.jobs set input=jsonb_set(input,'{source_image_url}','[]') where id=j;
          when 7 then update public.jobs set input=jsonb_set(input,'{source_image_url}','{}') where id=j;
          when 8 then update public.jobs set input=jsonb_set(input,'{source_image_url}',
            '"https://pbs.twimg.com/media/unattached.jpg"') where id=j;
          when 9 then update public.source_items set media='[null]' where id=s;
          when 10 then update public.source_items set media='[{}]' where id=s;
          when 11 then update public.source_items set media='[{"type":"photo"}]' where id=s;
          when 12 then case_ctx:=exact_claim_fixture.seed(c,'2026-10-14T15:10:50Z');
          when 13 then case_ctx:=exact_claim_fixture.seed(c,'2026-10-14T15:40:52Z');
          when 14 then update public.jobs set attempts=2 where id=j;
          when 15 then update public.jobs set max_attempts=4 where id=j;
          when 16 then update public.jobs set idempotency_key='not-natural' where id=j;
          when 17 then update public.jobs set lease_expires_at='2026-10-14T17:10:52Z' where id=j;
          when 18 then update public.jobs set input=input||'{"same_day_refresh_id":"unexpected"}' where id=j;
          when 19 then update public.source_items set published_at='2026-10-13T15:40:51Z' where id=s;
          when 20 then update public.source_feeds set last_polled_at='2026-10-14T15:10:51Z' where id=f;
          when 21 then update public.source_feeds set active=false where id=f;
          when 22 then insert into public.source_items(workspace_id,client_id,source_feed_id,
            external_id,source_type,canonical_url,author_handle,published_at,body,source_hash)
            select workspace_id,client_id,source_feed_id,'987654321','tweet',
              regexp_replace(canonical_url,'123456789$','987654321'),author_handle,
              '2026-10-14T15:39:51Z','Newer synthetic official source',repeat('c',64)
              from public.source_items where id=s;
          when 23 then insert into public.jobs(workspace_id,client_id,job_kind,status,input)
            select workspace_id,client_id,job_kind,'failed',input from public.jobs where id=j;
          when 24 then insert into public.content_items(id,workspace_id,client_id,content_kind)
            values(r,w,c,'daily_news');
          when 25 then extra_item:=gen_random_uuid();
            insert into public.content_items(id,workspace_id,client_id,content_kind)
              values(extra_item,w,c,'daily_news');
            insert into public.content_source_links(workspace_id,client_id,content_item_id,source_item_id)
              values(w,c,extra_item,s);
          when 26 then update public.jobs set input=jsonb_set(input,'{source_content}','"Changed body"') where id=j;
          when 27 then update public.jobs set input=jsonb_set(input,'{source_url}',
            '"https://x.com/Wrong/status/123456789"') where id=j;
          when 28 then update public.jobs set lease_expires_at='2026-10-14T15:40:50Z' where id=j;
          when 29 then update public.jobs set input=jsonb_set(input,'{manual_only}','true') where id=j;
          when 30 then update public.jobs set locked_by='synthetic-worker' where id=j;
          when 31 then update public.jobs set locked_by='ybo-trial:3eceb628-0f6b-4336-aad6-799f907c8157' where id=j;
        end case;
        if scenario in (12,13) then
          -- created_at is immutable: seed a separate synthetic job rather
          -- than disabling the trigger or mutating that identity field.
          case_w:=(case_ctx->>'workspace_id')::uuid;case_r:=(case_ctx->>'request_id')::uuid;
          case_j:=(case_ctx->>'job_id')::uuid;case_s:=(case_ctx->>'source_item_id')::uuid;
          update public.source_items set media='[]' where id=case_s;
          update public.jobs set input=jsonb_set(input,'{source_image_url}','""'),status='running',
            attempts=1,locked_by='ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d',locked_at='2026-10-14T15:40:51Z',
            lease_expires_at='2026-10-14T15:55:51Z' where id=case_j;
        end if;
        before_state:=exact_claim_fixture.snapshot();
        perform exact_claim_fixture.assert_true(public.get_quality_banner_generation_context(case_w,c,case_r) is null,
          'logo_context_negative_'||c||'_'||scenario);
        perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(),
          'logo_context_negative_readonly_'||c||'_'||scenario);
        raise exception 'rollback_synthetic_logo_case' using errcode='P7701';
      exception when sqlstate 'P7701' then null; end;
    end loop;

    -- A malformed/SQL-NULL media collection cannot even enter the source table.
    for scenario in 1..3 loop
      begin
        case scenario
          when 1 then update public.source_items set media=null where id=s;
          when 2 then update public.source_items set media='null' where id=s;
          when 3 then update public.source_items set media='{}' where id=s;
        end case;
        raise exception 'malformed_media_storage_admitted';
      exception when not_null_violation or check_violation then null; end;
    end loop;

    -- Only the new empty-media branch is window-gated. Existing attached media
    -- still works outside that window when its original freshness checks pass.
    perform set_config('exact_claim_fixture.now','2026-10-14T17:11:51Z',true);
    update public.source_items set published_at='2026-10-14T16:11:51Z' where id=s;
    update public.source_feeds set last_polled_at='2026-10-14T17:06:51Z' where id=f;
    update public.jobs set lease_expires_at='2026-10-14T17:25:51Z' where id=j;
    perform exact_claim_fixture.assert_true(public.get_quality_banner_generation_context(w,c,r) is null,
      'logo_context_expired_window_'||c);
    update public.source_items set media='[{"type":"photo","url":"https://pbs.twimg.com/media/fixture.jpg"}]'
      where id=s;
    update public.jobs set input=jsonb_set(input,'{source_image_url}','"https://pbs.twimg.com/media/fixture.jpg"') where id=j;
    result:=public.get_quality_banner_generation_context(w,c,r);
    perform exact_claim_fixture.assert_true(result is not null
      and result->>'source_image_url'='https://pbs.twimg.com/media/fixture.jpg','existing_media_context_unchanged_'||c);
    perform set_config('exact_claim_fixture.now','2026-10-14T15:40:51Z',true);
  end loop;
  -- Even a synthetic otherwise valid Yellow logo job must not enter this
  -- anchor's new branch. Existing attached-media behavior is not rewritten.
  ctx:=exact_claim_fixture.seed('yellow');
  j:=(ctx->>'job_id')::uuid;s:=(ctx->>'source_item_id')::uuid;
  w:=(ctx->>'workspace_id')::uuid;r:=(ctx->>'request_id')::uuid;
  update public.source_items set media='[]' where id=s;
  update public.jobs set input=jsonb_set(input,'{source_image_url}','""'),status='running',
    attempts=1,locked_by='ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d',
    locked_at='2026-10-14T15:40:51Z',lease_expires_at='2026-10-14T15:55:51Z' where id=j;
  before_state:=exact_claim_fixture.snapshot();
  perform exact_claim_fixture.assert_true(public.get_quality_banner_generation_context(w,'yellow',r) is null,
    'logo_context_yellow_excluded');
  perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(),'yellow_unchanged');
  perform set_config('request.jwt.claim.role','authenticated',true);
  begin perform public.get_quality_banner_generation_context(w,c,r);
    raise exception 'logo_context_role_guard_missing';
  exception when insufficient_privilege then null; end;
end $$;
rollback;
