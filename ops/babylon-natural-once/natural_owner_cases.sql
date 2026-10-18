-- DISPOSABLE FIXTURE ONLY: owner/request binding and fresh KST gate regressions.
begin;
select set_config('request.jwt.claim.role','service_role',true);
do $$
declare ctx jsonb; j uuid; s uuid; f uuid; r uuid; w uuid; result jsonb;
  before_state jsonb; scenario integer; media_mode text;
  changed_ctx jsonb; d date; created timestamptz; now_at timestamptz;
begin
  foreach media_mode in array array['photo','logo'] loop
    ctx:=babylon_natural_fixture.seed(); j:=(ctx->>'job_id')::uuid;
    s:=(ctx->>'source_item_id')::uuid; f:=(ctx->>'feed_id')::uuid;
    r:=(ctx->>'request_id')::uuid; w:=(ctx->>'workspace_id')::uuid;
    if media_mode='logo' then
      update public.source_items set media='[]' where id=s;
      update public.jobs set input=jsonb_set(input,'{source_image_url}','""') where id=j;
      ctx:=ctx||jsonb_build_object('input_sha256',(select encode(extensions.digest(
        convert_to(input::text,'UTF8'),'sha256'),'hex') from public.jobs where id=j));
    end if;
    before_state:=babylon_natural_fixture.snapshot();
    perform babylon_natural_fixture.assert_true(babylon_natural_fixture.call_claim(ctx,
      jsonb_build_object('worker','babylon-natural-once:'||gen_random_uuid()::text)) is null,'wrong_request_owner');
    perform babylon_natural_fixture.assert_true(before_state=babylon_natural_fixture.snapshot(),'wrong_owner_noop');
    result:=babylon_natural_fixture.call_claim(ctx);
    perform babylon_natural_fixture.assert_true(result is not null,'owner_positive_claim_'||media_mode);
    before_state:=babylon_natural_fixture.snapshot();
    perform babylon_natural_fixture.assert_true(public.get_quality_banner_generation_context(w,'babylon',r) is not null,
      'owner_positive_context_'||media_mode);
    perform babylon_natural_fixture.assert_true(before_state=babylon_natural_fixture.snapshot(),'context_positive_readonly');
    for scenario in 1..13 loop
      begin
        case scenario
          when 1 then update public.jobs set locked_by='babylon-natural-once:'||gen_random_uuid()::text where id=j;
          when 2 then update public.jobs set locked_at='2026-10-16T20:24:50Z' where id=j;
          when 3 then update public.jobs set started_at=null where id=j;
          when 4 then update public.jobs set started_at='2026-10-16T20:25:50Z' where id=j;
          when 5 then update public.jobs set finished_at='2026-10-16T20:25:51Z' where id=j;
          when 6 then update public.jobs set lease_expires_at='2026-10-16T20:40:52Z' where id=j;
          when 7 then update public.jobs set attempts=2 where id=j;
          when 8 then update public.jobs set max_attempts=4 where id=j;
          when 9 then update public.jobs set input=input||'{"extra":false}' where id=j;
          when 10 then update public.jobs set idempotency_key='not-natural' where id=j;
          when 11 then update public.jobs set locked_at='2026-10-16T20:25:52Z' where id=j;
          when 12 then update public.jobs set locked_by='babylon-natural-once:NOT-A-UUID' where id=j;
          when 13 then changed_ctx:=babylon_natural_fixture.seed('origintrail');
            update public.jobs set input=jsonb_set(input,'{request_id}',to_jsonb(r::text))
              where id=(changed_ctx->>'job_id')::uuid;
        end case;
        before_state:=babylon_natural_fixture.snapshot();
        perform babylon_natural_fixture.assert_true(public.get_quality_banner_generation_context(w,'babylon',r) is null,
          'owner_context_negative_'||media_mode||'_'||scenario);
        perform babylon_natural_fixture.assert_true(before_state=babylon_natural_fixture.snapshot(),'owner_context_noop');
        raise exception 'case_rollback' using errcode='P7701';
      exception when sqlstate 'P7701' then null; end;
    end loop;
  end loop;
  -- No fixed UUID/date registry: fresh same-day identity works on later days.
  -- Less than two hours before midnight is clipped exactly at KST midnight.
  foreach d in array array['2026-10-17'::date,'2026-11-04'::date] loop
    created:=(d::timestamp at time zone 'Asia/Seoul')+interval '23 hours 55 minutes';
    now_at:=created+interval '3 minutes';
    perform set_config('babylon_natural_fixture.now',now_at::text,true);
    ctx:=babylon_natural_fixture.seed('babylon',created);
    j:=(ctx->>'job_id')::uuid;s:=(ctx->>'source_item_id')::uuid;
    f:=(ctx->>'feed_id')::uuid;r:=(ctx->>'request_id')::uuid;w:=(ctx->>'workspace_id')::uuid;
    update private.official_x_daily_slots set kst_date=d where job_id=j;
    update public.jobs set input=jsonb_set(input,'{kst_date}',to_jsonb(d::text)),
      idempotency_key='official-x-review:v1:'||d::text||':babylon' where id=j;
    update public.source_items set published_at=now_at-interval '1 hour' where id=s;
    update public.source_feeds set last_polled_at=now_at-interval '1 minute' where id=f;
    ctx:=ctx||jsonb_build_object('input_sha256',(select encode(extensions.digest(
      convert_to(input::text,'UTF8'),'sha256'),'hex') from public.jobs where id=j));
    before_state:=babylon_natural_fixture.snapshot(j);
    result:=babylon_natural_fixture.call_claim(ctx);
    perform babylon_natural_fixture.assert_true(result is not null
      and (result->>'lease_expires_at')::timestamptz=((d+1)::timestamp at time zone 'Asia/Seoul'),
      'natural_next_kst_midnight_clips_lease');
    perform babylon_natural_fixture.assert_true(before_state=babylon_natural_fixture.snapshot(j),'midnight_other_rows_preserved');
    perform babylon_natural_fixture.assert_true(public.get_quality_banner_generation_context(w,'babylon',r) is not null,
      'fresh_calendar_context');
    perform set_config('babylon_natural_fixture.now',((d+1)::timestamp at time zone 'Asia/Seoul')::text,true);
    before_state:=babylon_natural_fixture.snapshot();
    perform babylon_natural_fixture.assert_true(public.get_quality_banner_generation_context(w,'babylon',r) is null,'kst_day_closed');
    perform babylon_natural_fixture.assert_true(before_state=babylon_natural_fixture.snapshot(),'kst_day_noop');
  end loop;
end $$;
rollback;
