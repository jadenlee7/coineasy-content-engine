-- LOCAL_ROLLBACK_PROOF: new standalone Babylon natural-slot trial, owner installation only.
-- The existing fresh Babylon anchor/row and all jobs, content, checkpoints,
-- budgets and grants remain unchanged. Only a NEW empty anchor is installed.
-- The two existing bodies are exact-current guarded; signatures/OIDs/ACL stay.
-- This installs an EMPTY private anchor. No readiness insertion or activation.
begin;
set local lock_timeout='5s';
set local statement_timeout='60s';
set local timezone='UTC';


create table private.ybo_standalone_babylon_trial_anchor (
    workspace_id uuid not null check (workspace_id='5cc143cc-b253-4841-8a6c-ab3ac98623a6'::uuid),
    trial_id uuid primary key check (trial_id='7b27e277-0641-4aef-b739-761f86547287'::uuid),
    plan_sha256 text not null check (plan_sha256 ~ '^[a-f0-9]{64}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    not_before timestamptz not null,
    deadline timestamptz not null,
    kst_date date not null,
    check (not_before>='2026-10-16T19:45:03Z'::timestamptz),
    check (isfinite(not_before) and isfinite(deadline)),
    check (date_trunc('second',not_before)=not_before and date_trunc('second',deadline)=deadline),
    check (deadline=not_before+interval '2 hours'),
    check (timezone('Asia/Seoul',not_before)::date=kst_date
        and timezone('Asia/Seoul',deadline)::date=kst_date)
);
-- Explicitly remove any inherited default grants on this NEW private table.
-- The owner retains administrative SELECT/INSERT; no application role may write.
revoke all on table private.ybo_standalone_babylon_trial_anchor from public,anon,authenticated,service_role;
alter table private.ybo_standalone_babylon_trial_anchor enable row level security;

create function private.guard_ybo_standalone_babylon_trial_anchor()
returns trigger language plpgsql volatile set search_path='' as $$
declare observed_at timestamptz;
begin
    if TG_OP <> 'INSERT' then
        raise exception 'ybo_standalone_babylon_anchor_immutable' using errcode='55000';
    end if;
    observed_at := clock_timestamp();
    if new.kst_date is distinct from timezone('Asia/Seoul',observed_at)::date
       or new.not_before is null or new.not_before>observed_at
       or new.not_before<observed_at-interval '60 seconds' then
        raise exception 'ybo_standalone_babylon_anchor_start_not_fresh' using errcode='22023';
    end if;
    if exists(select 1 from private.ybo_standalone_babylon_trial_anchor) then
        raise exception 'ybo_standalone_babylon_anchor_already_fixed' using errcode='55000';
    end if;
    return new;
end $$;
revoke all on function private.guard_ybo_standalone_babylon_trial_anchor() from public,anon,authenticated,service_role;
create trigger ybo_standalone_babylon_anchor_insert
before insert on private.ybo_standalone_babylon_trial_anchor for each row
execute function private.guard_ybo_standalone_babylon_trial_anchor();
create trigger ybo_standalone_babylon_anchor_immutable
before update or delete or truncate on private.ybo_standalone_babylon_trial_anchor for each statement
execute function private.guard_ybo_standalone_babylon_trial_anchor();



do $$begin
  if (select encode(extensions.digest(convert_to(prosrc,'UTF8'),'sha256'),'hex')
      from pg_proc where oid='public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)'::regprocedure)
      is distinct from '0fc3dfa2ffa4b9e4619996c0faa28b2af5719f8c40f660eceedd48291dd68152' then
    raise exception 'exact_claim_previous_body_mismatch';
  end if;
end $$;
create temporary table ybo_exact_claim_before on commit drop as
  select to_jsonb(p)-'prosrc' as catalog from pg_proc p
  where p.oid='public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)'::regprocedure;

create or replace function public.claim_exact_natural_review_draft_job(
    target_workspace_id uuid,
    target_client_id text,
    target_job_id uuid,
    target_request_id uuid,
    target_source_item_id uuid,
    target_input_sha256 text,
    target_style_pack_hash text,
    target_worker_id text,
    target_lease_seconds integer default 900
) returns jsonb language plpgsql volatile security definer set search_path = '' as $$
declare
    gate_start timestamptz;
    gate_end timestamptz;
    gate_day date;
    job public.jobs%rowtype;
    slot private.official_x_daily_slots%rowtype;
    source public.source_items%rowtype;
    feed public.source_feeds%rowtype;
    pack private.official_x_style_reference_packs%rowtype;
    expected_handle text;
    feed_id uuid;
    decision_now timestamptz;
    expected_input jsonb;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'exact_natural_claim_service_role_required' using errcode='42501';
    end if;
    if target_workspace_id is null or target_job_id is null
       or target_request_id is null or target_source_item_id is null
       or target_client_id is null or target_client_id not in ('yellow','babylon','origintrail')
       or target_worker_id is null or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_input_sha256 is null or target_input_sha256 !~ '^[a-f0-9]{64}$'
       or target_style_pack_hash is null or target_style_pack_hash !~ '^[a-f0-9]{32}$'
       or target_lease_seconds is null or target_lease_seconds not between 60 and 900 then
        raise exception 'exact_natural_claim_arguments_invalid' using errcode='22023';
    end if;
    -- The prior readiness and fresh routes remain exact; this distinct new
    -- worker is Babylon-only and OFF until its own immutable anchor exists.
    if target_worker_id='ybo-trial:7b27e277-0641-4aef-b739-761f86547287'
       and target_client_id='babylon' then
      select anchor.not_before,anchor.deadline,anchor.kst_date into gate_start,gate_end,gate_day
        from private.ybo_standalone_babylon_trial_anchor as anchor
        where anchor.trial_id='7b27e277-0641-4aef-b739-761f86547287'::uuid
          and anchor.workspace_id=target_workspace_id;
    elsif target_worker_id='ybo-trial:c34dbb8a-ff7c-484e-8e43-757f692ec743'
       and target_client_id='babylon' then
      select anchor.not_before,anchor.deadline,anchor.kst_date into gate_start,gate_end,gate_day
        from private.ybo_readiness_babylon_trial_anchor as anchor
        where anchor.trial_id='c34dbb8a-ff7c-484e-8e43-757f692ec743'::uuid
          and anchor.workspace_id=target_workspace_id;
    elsif target_worker_id='ybo-trial:9d7865c1-c13b-4b1b-aba1-83124aa8d2cd'
       and target_client_id='babylon' then
      select anchor.not_before,anchor.deadline,anchor.kst_date into gate_start,gate_end,gate_day
        from private.ybo_fresh_babylon_trial_anchor as anchor
        where anchor.trial_id='9d7865c1-c13b-4b1b-aba1-83124aa8d2cd'::uuid
          and anchor.workspace_id=target_workspace_id;
    elsif target_worker_id='ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d'
       and target_client_id in ('babylon','origintrail') then
      select anchor.not_before,anchor.deadline,anchor.kst_date into gate_start,gate_end,gate_day
        from private.ybo_trial_readiness_anchor as anchor
        where anchor.trial_id='1a2bef47-4827-4bc3-9b4d-5baf2714015d'::uuid
          and anchor.workspace_id=target_workspace_id;
    else return null;
    end if;
    if not found then return null; end if;
    decision_now := clock_timestamp();
    if decision_now < gate_start or decision_now >= gate_end
       or timezone('Asia/Seoul',decision_now)::date <> gate_day then return null; end if;
    expected_handle := case target_client_id when 'yellow' then '@Yellow'
        when 'babylon' then '@babylonlabs_io' when 'origintrail' then '@origin_trail' end;

    -- Match queue_review_draft_job's KST workspace lock and slot-first order.
    -- Lock/check only the supplied natural reservation, never pick another row.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        target_workspace_id::text||':'||gate_day::text,0));
    select * into slot from private.official_x_daily_slots
        where workspace_id=target_workspace_id and client_id=target_client_id
          and kst_date=gate_day and job_id=target_job_id for update;
    if not found then return null; end if;
    select source_feed_id into feed_id from public.source_items
        where id=target_source_item_id and workspace_id=target_workspace_id
          and client_id=target_client_id;
    if not found then return null; end if;
    -- Normal official-source recording locks its feed before writing sources.
    -- This holds the observed latest source stable against that writer during
    -- this transaction. Arbitrary table writers are NOT covered by that claim.
    select * into feed from public.source_feeds where id=feed_id
        and workspace_id=target_workspace_id and client_id=target_client_id for update;
    if not found then return null; end if;
    select * into source from public.source_items where id=target_source_item_id
        and workspace_id=target_workspace_id and client_id=target_client_id
        and source_feed_id=feed_id for share;
    if not found then return null; end if;
    select * into job from public.jobs where id=target_job_id
        and workspace_id=target_workspace_id and client_id=target_client_id for update;
    if not found then return null; end if;
    perform 1 from public.workspace_clients where workspace_id=target_workspace_id
        and client_id=target_client_id and active is true for share;
    if not found then return null; end if;
    perform 1 from private.official_x_source_state where workspace_id=target_workspace_id
        and client_id=target_client_id and source_item_id=target_source_item_id
        and queued_job_id=target_job_id for share;
    if not found then return null; end if;
    select * into pack from private.official_x_style_reference_packs
        where workspace_id=target_workspace_id and client_id=target_client_id
          and request_id=target_request_id and primary_source_item_id=target_source_item_id
          and reference_pack_hash=target_style_pack_hash for share;
    if not found then return null; end if;

    -- Re-sample wall time AFTER potentially waiting for locks. statement time
    -- alone can admit a source/window which expired while the caller waited.
    decision_now := clock_timestamp();
    if decision_now < gate_start or decision_now >= gate_end-interval '60 seconds'
       or timezone('Asia/Seoul',decision_now)::date <> gate_day then return null; end if;
    if job.job_kind is distinct from 'generate' or job.status is distinct from 'queued'
       or job.attempts is distinct from 0 or job.max_attempts is distinct from 3
       or job.content_item_id is not null or job.locked_by is not null
       or job.locked_at is not null or job.lease_expires_at is not null
       or job.started_at is not null or job.finished_at is not null
       or job.available_at is null or job.available_at>decision_now
       or job.created_at<gate_start or job.created_at>decision_now
       or job.output is distinct from '{}'::jsonb
       or job.idempotency_key is distinct from
          ('official-x-review:v1:'||gate_day::text||':'||target_client_id)
       or encode(extensions.digest(convert_to(job.input::text,'UTF8'),'sha256'),'hex')
          is distinct from target_input_sha256 then return null; end if;
    expected_input := jsonb_build_object(
        'workflow','official_x_review_draft_v1','kst_date',gate_day,
        'source_item_ids',jsonb_build_array(target_source_item_id::text),
        'content_kind','daily_news','request_id',target_request_id,
        'source_content',source.body,'source_url',source.canonical_url,
        'source_image_url',job.input->>'source_image_url','manual_only',false);
    if job.input is distinct from expected_input then return null; end if;
    if source.source_type is distinct from 'tweet' or source.author_handle is distinct from expected_handle
       or source.canonical_url is null
       or source.canonical_url !~ ('^https://x\.com/'||substring(expected_handle from 2)||'/status/[0-9]{1,19}$')
       or source.external_id is distinct from split_part(source.canonical_url,'/',6)
       or source.body is null or char_length(source.body) not between 10 and 20000
       or source.published_at is null or source.published_at>decision_now
       or source.published_at<=decision_now-interval '24 hours'
       or feed.provider is distinct from 'x' or feed.handle is distinct from expected_handle
       or feed.active is distinct from true or feed.poll_interval_minutes is distinct from 15
       or feed.last_polled_at is null or feed.last_polled_at>decision_now
       or feed.last_polled_at<=decision_now-interval '30 minutes'
       or jsonb_typeof(source.media) is distinct from 'array' then return null; end if;
    -- Only a genuinely media-less official source may use the already pinned
    -- client logo. Empty strings cannot hide existing/malformed source media;
    -- NULL, missing, whitespace and non-string job fields stay rejected.
    if source.media = '[]'::jsonb then
        if job.input->'source_image_url' is distinct from '""'::jsonb then return null; end if;
    else
        if job.input->>'source_image_url' is null
           or char_length(job.input->>'source_image_url')>2048
           or (job.input->>'source_image_url') !~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./?=&%:+-]+$'
           or not exists(select 1 from jsonb_array_elements(source.media) as media(value)
               where media.value->>'type' in ('photo','video','animated_gif')
                 and media.value->>'url'=job.input->>'source_image_url') then return null; end if;
    end if;
    if source.id is distinct from (select newest.id from public.source_items as newest
        where newest.workspace_id=target_workspace_id and newest.client_id=target_client_id
          and newest.source_feed_id=feed_id and newest.source_type='tweet'
        order by newest.published_at desc nulls last,newest.id desc limit 1) then return null; end if;
    if exists(select 1 from public.content_items where id=target_request_id)
       or exists(select 1 from public.content_source_links where workspace_id=target_workspace_id
          and client_id=target_client_id and source_item_id=target_source_item_id)
       or (select count(*) from public.jobs as producer
          where producer.job_kind='generate'
            and (producer.input->>'request_id'=target_request_id::text
              or (producer.workspace_id=target_workspace_id and producer.client_id=target_client_id
                and producer.input->'source_item_ids' @> jsonb_build_array(target_source_item_id::text))))<>1
       then return null; end if;

    -- Read scans above can themselves take time. Recheck immediately before
    -- the only write, not merely after lock waits; do not extend a stale grant.
    decision_now := clock_timestamp();
    if decision_now < gate_start or decision_now >= gate_end-interval '60 seconds'
       or timezone('Asia/Seoul',decision_now)::date <> gate_day
       or source.published_at>decision_now
       or source.published_at<=decision_now-interval '24 hours'
       or feed.last_polled_at>decision_now
       or feed.last_polled_at<=decision_now-interval '30 minutes' then return null; end if;

    -- Exactly one normal initial claim; no retry, recovery, bulk expiry,
    -- source mutation, manufactured output, additional event or release grant.
    -- Existing initial claim emits no event (only Batch recovery does), so keep
    -- that contract. The normal bind/complete/fail RPCs remain responsible later.
    update public.jobs set status='running',attempts=attempts+1,
        locked_by=target_worker_id,locked_at=decision_now,
        lease_expires_at=least(gate_end,decision_now+make_interval(secs=>target_lease_seconds)),
        started_at=coalesce(started_at,decision_now),finished_at=null,
        last_error_code=null,last_error_message=null
        where id=target_job_id and workspace_id=target_workspace_id and client_id=target_client_id
          and status='queued' and attempts=0
        returning * into job;
    if not found then return null; end if;
    return jsonb_build_object('job_id',job.id,'workspace_id',job.workspace_id,
        'client_id',job.client_id,'status',job.status,'attempts',job.attempts,
        'max_attempts',job.max_attempts,'origintrail_batch_eligible',false,
        'batch_handoff_recovery_only',false,'locked_by',job.locked_by,
        'lease_expires_at',job.lease_expires_at,'input',job.input);
end $$;

-- Preserve every non-body catalog field, including owner/OID/ACL. No grants.
do $$begin
  if (select to_jsonb(p)-'prosrc' from pg_proc p
      where p.oid='public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)'::regprocedure)
      is distinct from (select catalog from pg_temp.ybo_exact_claim_before) then
    raise exception 'exact_claim_nonbody_catalog_changed';
  end if;
end $$;



do $$begin
  if (select encode(extensions.digest(convert_to(prosrc,'UTF8'),'sha256'),'hex')
      from pg_proc where oid='public.get_quality_banner_generation_context(uuid,text,uuid)'::regprocedure)
      is distinct from '57249c5210b32ead5e529a5dad3eab1c9213563550f3e50a09fd1083ff419e2b' then
    raise exception 'quality_context_previous_body_mismatch';
  end if;
end $$;
create temporary table ybo_quality_context_before on commit drop as
  select to_jsonb(p)-'prosrc' as catalog from pg_proc p
  where p.oid='public.get_quality_banner_generation_context(uuid,text,uuid)'::regprocedure;

create or replace function public.get_quality_banner_generation_context(
    target_workspace_id uuid, target_client_id text, target_request_id uuid
) returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare result jsonb;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'quality_banner_service_role_required' using errcode='42501';
    end if;
    if target_workspace_id is null or target_request_id is null
       or target_client_id not in ('yellow','babylon','origintrail') then return null; end if;
    select jsonb_build_object(
        'job_id',job.id,'request_id',target_request_id,'workspace_id',job.workspace_id,
        'client_id',job.client_id,'source_item_id',source.id,
        'source_published_at',source.published_at,
        'source_sha256',encode(extensions.digest(convert_to(source.body,'UTF8'),'sha256'),'hex'),
        'source_content',source.body,'source_url',source.canonical_url,
        'source_image_url',job.input->>'source_image_url') into result
    from private.official_x_daily_slots as slot
    join public.jobs as job on job.id=slot.job_id and job.workspace_id=slot.workspace_id
      and job.client_id=slot.client_id
    join private.official_x_source_state as state on state.queued_job_id=job.id
      and state.workspace_id=job.workspace_id and state.client_id=job.client_id
    join public.source_items as source on source.id=state.source_item_id
      and source.workspace_id=job.workspace_id and source.client_id=job.client_id
    join public.source_feeds as feed on feed.id=source.source_feed_id
      and feed.workspace_id=source.workspace_id and feed.client_id=source.client_id
    join public.workspace_clients as client on client.workspace_id=job.workspace_id
      and client.client_id=job.client_id and client.active is true
    join private.official_x_style_reference_packs as pack on pack.workspace_id=job.workspace_id
      and pack.client_id=job.client_id and pack.request_id=target_request_id
      and pack.primary_source_item_id=source.id
    where slot.workspace_id=target_workspace_id and slot.client_id=target_client_id
      and slot.kst_date=timezone('Asia/Seoul',statement_timestamp())::date
      and job.job_kind='generate' and job.status='running' and job.content_item_id is null
      and job.locked_by is not null and job.lease_expires_at>statement_timestamp()
      -- Only the new worker is additionally fenced for every media mode.
      -- Older workers retain their exact existing attached-media behavior.
      and (job.locked_by<>'ybo-trial:7b27e277-0641-4aef-b739-761f86547287'
        or (target_client_id='babylon' and exists(
          select 1 from private.ybo_standalone_babylon_trial_anchor as anchor
          where anchor.trial_id='7b27e277-0641-4aef-b739-761f86547287'::uuid
            and anchor.workspace_id=job.workspace_id
            and statement_timestamp()>=anchor.not_before and statement_timestamp()<anchor.deadline
            and slot.kst_date=anchor.kst_date and job.created_at>=anchor.not_before
            and job.created_at<=statement_timestamp()
            and job.lease_expires_at<=anchor.deadline)))
      and job.attempts between 1 and job.max_attempts
      and job.input->>'workflow'='official_x_review_draft_v1'
      and job.input->>'content_kind'='daily_news' and job.input->'manual_only'='false'::jsonb
      and job.input->>'request_id'=target_request_id::text
      and job.input->>'kst_date'=slot.kst_date::text
      and job.input->'source_item_ids'=jsonb_build_array(source.id::text)
      and job.input->>'source_content'=source.body
      and job.input->>'source_url'=source.canonical_url
      and (
        -- Existing attached-media admission remains available unchanged.
        (job.input->>'source_image_url' ~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./?=&%:+-]+$'
          and exists(select 1 from jsonb_array_elements(
              case when jsonb_typeof(source.media)='array' then source.media else '[]'::jsonb end
            ) as media(value)
            where media.value->>'type' in ('photo','video','animated_gif')
              and media.value->>'url'=job.input->>'source_image_url'))
        or (
          -- Trial-only official-logo path: genuine empty media and an explicit
          -- empty JSON string, never NULL/missing/non-string/omitted evidence.
          source.media='[]'::jsonb and job.input->'source_image_url'='""'::jsonb
          and target_client_id in ('babylon','origintrail')
          and (
            (job.locked_by='ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d'
              and exists(select 1 from private.ybo_trial_readiness_anchor as anchor
                where anchor.trial_id='1a2bef47-4827-4bc3-9b4d-5baf2714015d'::uuid
                  and anchor.workspace_id=job.workspace_id
                  and statement_timestamp()>=anchor.not_before and statement_timestamp()<anchor.deadline
                  and slot.kst_date=anchor.kst_date and job.created_at>=anchor.not_before
                  and job.lease_expires_at<=anchor.deadline))
            or (target_client_id='babylon'
              and job.locked_by='ybo-trial:7b27e277-0641-4aef-b739-761f86547287'
              and exists(select 1 from private.ybo_standalone_babylon_trial_anchor as anchor
                where anchor.trial_id='7b27e277-0641-4aef-b739-761f86547287'::uuid
                  and anchor.workspace_id=job.workspace_id
                  and statement_timestamp()>=anchor.not_before and statement_timestamp()<anchor.deadline
                  and slot.kst_date=anchor.kst_date and job.created_at>=anchor.not_before
                  and job.lease_expires_at<=anchor.deadline))
            or (target_client_id='babylon'
              and job.locked_by='ybo-trial:c34dbb8a-ff7c-484e-8e43-757f692ec743'
              and exists(select 1 from private.ybo_readiness_babylon_trial_anchor as anchor
                where anchor.trial_id='c34dbb8a-ff7c-484e-8e43-757f692ec743'::uuid
                  and anchor.workspace_id=job.workspace_id
                  and statement_timestamp()>=anchor.not_before and statement_timestamp()<anchor.deadline
                  and slot.kst_date=anchor.kst_date and job.created_at>=anchor.not_before
                  and job.lease_expires_at<=anchor.deadline))
            or (target_client_id='babylon'
              and job.locked_by='ybo-trial:9d7865c1-c13b-4b1b-aba1-83124aa8d2cd'
              and exists(select 1 from private.ybo_fresh_babylon_trial_anchor as anchor
                where anchor.trial_id='9d7865c1-c13b-4b1b-aba1-83124aa8d2cd'::uuid
                  and anchor.workspace_id=job.workspace_id
                  and statement_timestamp()>=anchor.not_before and statement_timestamp()<anchor.deadline
                  and slot.kst_date=anchor.kst_date and job.created_at>=anchor.not_before
                  and job.lease_expires_at<=anchor.deadline))
          )
          and job.created_at<=statement_timestamp()
          and job.attempts=1 and job.max_attempts=3
          and job.idempotency_key='official-x-review:v1:'||slot.kst_date::text||':'||target_client_id
          and job.input=jsonb_build_object(
            'workflow','official_x_review_draft_v1','kst_date',slot.kst_date,
            'source_item_ids',jsonb_build_array(source.id::text),'content_kind','daily_news',
            'request_id',target_request_id,'source_content',source.body,
            'source_url',source.canonical_url,'source_image_url','','manual_only',false)
        )
      )
      and source.source_type='tweet'
      and source.author_handle=case target_client_id when 'yellow' then '@Yellow'
          when 'babylon' then '@babylonlabs_io' when 'origintrail' then '@origin_trail' end
      and source.canonical_url ~ ('^https://x\.com/'||substring(source.author_handle from 2)||'/status/[0-9]{1,19}$')
      and source.external_id=split_part(source.canonical_url,'/',6)
      and char_length(source.body) between 10 and 20000
      and source.published_at<=statement_timestamp()
      and source.published_at>statement_timestamp()-interval '24 hours'
      and feed.provider='x' and feed.handle=source.author_handle and feed.active is true
      and feed.poll_interval_minutes=15 and feed.last_polled_at<=statement_timestamp()
      and feed.last_polled_at>statement_timestamp()-interval '30 minutes'
      and source.id=(select newest.id from public.source_items as newest
          where newest.workspace_id=source.workspace_id and newest.client_id=source.client_id
            and newest.source_feed_id=feed.id and newest.source_type='tweet'
          order by newest.published_at desc nulls last,newest.id desc limit 1)
      and not exists(select 1 from public.content_items where id=target_request_id)
      and not exists(select 1 from public.content_source_links where workspace_id=target_workspace_id
          and client_id=target_client_id and source_item_id=source.id)
      and (select count(*) from public.jobs as producer where producer.workspace_id=job.workspace_id
          and producer.client_id=job.client_id and producer.job_kind='generate'
          and (producer.input->>'request_id'=target_request_id::text
            or producer.input->'source_item_ids' @> jsonb_build_array(source.id::text)))=1;
    return result;
end $$;

-- CREATE OR REPLACE must retain every non-body catalog field, including OID,
-- owner, service-role-only ACL, volatility, security definer and search_path.
-- No GRANT/REVOKE/role mutation accompanies this proposal.
do $$begin
  if (select to_jsonb(p)-'prosrc' from pg_proc p
      where p.oid='public.get_quality_banner_generation_context(uuid,text,uuid)'::regprocedure)
      is distinct from (select catalog from pg_temp.ybo_quality_context_before) then
    raise exception 'quality_context_nonbody_catalog_changed';
  end if;
end $$;

rollback; -- LOCAL_ROLLBACK_PROOF; never change to COMMIT here.
