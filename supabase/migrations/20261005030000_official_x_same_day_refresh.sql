-- Isolated, operator-selected same-day refresh. No FIFO claim, schedule,
-- delivery reconciliation, automatic approval or publication is installed.
-- Existing natural daily slots, items, versions and worker RPCs are unchanged.
begin;

create table private.official_x_same_day_refresh_requests (
    refresh_id uuid primary key,
    workspace_id uuid not null,
    client_id text not null check (client_id in ('yellow','babylon','squid','origintrail')),
    kst_date date not null,
    predecessor_job_id uuid not null references public.jobs(id) on delete restrict,
    predecessor_content_item_id uuid not null,
    predecessor_content_version_id uuid not null,
    source_item_id uuid not null,
    request_id uuid not null unique,
    job_id uuid not null unique references public.jobs(id) on delete restrict,
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    job_input_sha256 text not null check (job_input_sha256 ~ '^[a-f0-9]{64}$'),
    source_snapshot_sha256 text not null check (source_snapshot_sha256 ~ '^[a-f0-9]{64}$'),
    expires_at timestamptz not null,
    created_at timestamptz not null default statement_timestamp(),
    claimed_at timestamptz,
    claimed_by text,
    unique (workspace_id,client_id,kst_date),
    unique (workspace_id,client_id,source_item_id),
    foreign key (workspace_id,client_id)
        references public.workspace_clients(workspace_id,client_id) on delete restrict,
    foreign key (workspace_id,client_id,source_item_id)
        references public.source_items(workspace_id,client_id,id) on delete restrict,
    foreign key (workspace_id,client_id,predecessor_content_item_id)
        references public.content_items(workspace_id,client_id,id) on delete restrict,
    foreign key (workspace_id,predecessor_content_item_id,predecessor_content_version_id)
        references public.content_versions(workspace_id,content_item_id,id) on delete restrict,
    check (expires_at > created_at and expires_at <= created_at + interval '2 hours'),
    check ((claimed_at is null) = (claimed_by is null)),
    check (claimed_by is null or claimed_by ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    check (request_id <> predecessor_content_item_id)
);
alter table private.official_x_same_day_refresh_requests enable row level security;
alter table private.official_x_same_day_refresh_requests force row level security;
revoke all on table private.official_x_same_day_refresh_requests
    from public,anon,authenticated,service_role;

-- A durable one-shot receipt cannot be deleted, rebound or reset after claim.
create function private.guard_official_x_same_day_refresh_request()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    if tg_op = 'DELETE' then
        raise exception 'same_day_refresh_receipt_immutable' using errcode='55000';
    end if;
    if (to_jsonb(new) - 'claimed_at' - 'claimed_by')
          is distinct from (to_jsonb(old) - 'claimed_at' - 'claimed_by')
       or old.claimed_at is not null or new.claimed_at is null
       or new.claimed_by is null or new.claimed_at >= old.expires_at then
        raise exception 'same_day_refresh_receipt_immutable' using errcode='55000';
    end if;
    return new;
end $$;
create trigger official_x_same_day_refresh_request_immutable
before update or delete on private.official_x_same_day_refresh_requests
for each row execute function private.guard_official_x_same_day_refresh_request();
revoke all on function private.guard_official_x_same_day_refresh_request()
    from public,anon,authenticated,service_role;

-- Pure eligibility, called only after the caller locks the exact feed. Intake
-- takes the same feed lock, so a newer source cannot race queue/claim.
create function private.official_x_same_day_refresh_source_valid(
    target_workspace_id uuid,target_client_id text,target_source_item_id uuid,
    target_now timestamptz
) returns boolean language sql stable security definer set search_path = '' as $$
    select exists (
        select 1 from public.source_items as source
        join public.source_feeds as feed on feed.id=source.source_feed_id
          and feed.workspace_id=source.workspace_id and feed.client_id=source.client_id
        join public.workspace_clients as client on client.workspace_id=source.workspace_id
          and client.client_id=source.client_id and client.active is true
        where source.workspace_id=target_workspace_id and source.client_id=target_client_id
          and source.id=target_source_item_id and source.source_type='tweet'
          and source.author_handle=case target_client_id when 'yellow' then '@Yellow'
            when 'babylon' then '@babylonlabs_io' when 'squid' then '@SquidRouter'
            when 'origintrail' then '@origin_trail' end
          and feed.handle=source.author_handle and feed.provider='x' and feed.active is true
          and feed.poll_interval_minutes=15 and feed.last_polled_at<=target_now
          and feed.last_polled_at>=target_now-interval '15 minutes'
          and source.published_at<=target_now and source.published_at>target_now-interval '24 hours'
          and source.canonical_url ~ ('^https://x\.com/' || substring(source.author_handle from 2)
              || '/status/[0-9]{1,19}$')
          and source.external_id=split_part(source.canonical_url,'/',6)
          and char_length(btrim(source.body)) between 10 and 20000
          and source.id=(select latest.id from public.source_items as latest
              where latest.workspace_id=source.workspace_id and latest.client_id=source.client_id
                and latest.source_feed_id=feed.id and latest.source_type='tweet'
              order by latest.published_at desc nulls last,latest.id desc limit 1)
    )
$$;
revoke all on function private.official_x_same_day_refresh_source_valid(uuid,text,uuid,timestamptz)
    from public,anon,authenticated,service_role;

-- No provider response or private message material is returned. Optional button
-- proposals may be absent on a fresh migration-only database; when installed,
-- any review/card reservation for this item is conservatively held.
create function private.official_x_same_day_refresh_undelivered(
    target_workspace_id uuid,target_content_item_id uuid
) returns boolean language plpgsql stable security definer set search_path = '' as $$
declare reserved boolean;
begin
    if exists(select 1 from public.approvals where workspace_id=target_workspace_id
          and content_item_id=target_content_item_id)
       or exists(select 1 from public.publications where workspace_id=target_workspace_id
          and content_item_id=target_content_item_id)
       or exists(select 1 from private.content_ops_review_outbox where workspace_id=target_workspace_id
          and content_item_id=target_content_item_id)
       or exists(select 1 from private.grok_qa_verdict_receipts where workspace_id=target_workspace_id
          and content_item_id=target_content_item_id)
       or exists(select 1 from private.grok_qa_dispatch_outbox where workspace_id=target_workspace_id
          and content_item_id=target_content_item_id
          and (status<>'pending' or attempts<>0 or provider_attempt_started_at is not null)) then
        return false;
    end if;
    if to_regclass('private.content_ops_button_reviews') is not null then
        execute 'select exists(select 1 from private.content_ops_button_reviews
            where workspace_id=$1 and content_item_id=$2)'
            into reserved using target_workspace_id,target_content_item_id;
        if reserved then return false; end if;
    end if;
    return true;
end $$;
revoke all on function private.official_x_same_day_refresh_undelivered(uuid,uuid)
    from public,anon,authenticated,service_role;

create function private.official_x_same_day_refresh_predecessor_valid(
    target_workspace_id uuid,target_client_id text,target_kst_date date,
    target_predecessor_job_id uuid,target_predecessor_content_item_id uuid,
    target_predecessor_content_version_id uuid,target_source_item_id uuid
) returns boolean language sql stable security definer set search_path = '' as $$
    select exists (
        select 1 from private.official_x_daily_slots as slot
        join public.jobs as job on job.id=slot.job_id and job.workspace_id=slot.workspace_id
          and job.client_id=slot.client_id
        join public.content_items as item on item.id=target_predecessor_content_item_id
          and item.workspace_id=slot.workspace_id and item.client_id=slot.client_id
        join public.content_versions as version on version.id=target_predecessor_content_version_id
          and version.content_item_id=item.id and version.workspace_id=item.workspace_id
        join public.content_source_links as link on link.workspace_id=item.workspace_id
          and link.client_id=item.client_id and link.content_item_id=item.id and link.position=0
        join public.source_items as old_source on old_source.id=link.source_item_id
          and old_source.workspace_id=slot.workspace_id and old_source.client_id=slot.client_id
        join public.source_items as new_source on new_source.id=target_source_item_id
          and new_source.workspace_id=slot.workspace_id and new_source.client_id=slot.client_id
        join private.official_x_source_state as state on state.source_item_id=old_source.id
          and state.workspace_id=slot.workspace_id and state.client_id=slot.client_id
        where slot.workspace_id=target_workspace_id and slot.client_id=target_client_id
          and slot.kst_date=target_kst_date and slot.job_id=target_predecessor_job_id
          and job.job_kind='generate' and job.status='succeeded'
          and job.input->>'workflow'='official_x_review_draft_v1'
          and job.input->>'content_kind'='daily_news' and job.input->'manual_only'='false'::jsonb
          and job.input->>'kst_date'=target_kst_date::text
          and job.input->>'request_id'=item.id::text
          and job.input->'source_item_ids'=jsonb_build_array(old_source.id::text)
          and job.output->>'content_item_id'=item.id::text
          and job.output->>'content_version_id'=version.id::text
          and job.output->'source_item_ids'=jsonb_build_array(old_source.id::text)
          and (job.content_item_id is null or job.content_item_id=item.id)
          and pg_catalog.timezone('Asia/Seoul',job.finished_at)::date=target_kst_date
          and item.content_kind='daily_news' and item.status='needs_review'
          and item.current_version_id=version.id
          and version.generation_meta->'mock_mode'='false'::jsonb
          and version.generation_meta->>'request_id'=item.id::text
          and pg_catalog.timezone('Asia/Seoul',version.created_at)::date=target_kst_date
          and old_source.id<>new_source.id and old_source.source_feed_id=new_source.source_feed_id
          and old_source.source_type='tweet' and old_source.author_handle=new_source.author_handle
          and old_source.canonical_url ~ ('^https://x\.com/'||substring(old_source.author_handle from 2)
            ||'/status/[0-9]{1,19}$')
          and old_source.external_id=split_part(old_source.canonical_url,'/',6)
          and (new_source.published_at>old_source.published_at
            or (new_source.published_at=old_source.published_at and new_source.id>old_source.id))
          and state.queued_job_id=job.id
          and (select count(*) from public.content_source_links where content_item_id=item.id)=1
          and (select count(*) from public.jobs as producer
            where producer.workspace_id=item.workspace_id and producer.client_id=item.client_id
              and producer.job_kind='generate' and (producer.content_item_id=item.id
                or producer.input->>'request_id'=item.id::text
                or producer.output->>'content_item_id'=item.id::text))=1
          and private.official_x_same_day_refresh_undelivered(item.workspace_id,item.id)
    )
$$;
revoke all on function private.official_x_same_day_refresh_predecessor_valid(uuid,text,date,uuid,uuid,uuid,uuid)
    from public,anon,authenticated,service_role;

create function private.official_x_same_day_refresh_receipt(
    target_refresh_id uuid,target_reused boolean
) returns jsonb language sql stable security definer set search_path = '' as $$
    select jsonb_build_object('refresh_id',r.refresh_id,'job_id',r.job_id,
        'request_id',r.request_id,'source_item_id',r.source_item_id,'client_id',r.client_id,
        'kst_date',r.kst_date,'release_sha',r.release_sha,'expires_at',r.expires_at,
        'status',j.status,'reused',target_reused)
    from private.official_x_same_day_refresh_requests as r
    join public.jobs as j on j.id=r.job_id where r.refresh_id=target_refresh_id
$$;
revoke all on function private.official_x_same_day_refresh_receipt(uuid,boolean)
    from public,anon,authenticated,service_role;

create function private.official_x_same_day_refresh_source_sha256(target_source_item_id uuid)
returns text language sql stable security definer set search_path = '' as $$
    select pg_catalog.encode(extensions.digest(jsonb_build_object('id',s.id,
        'source_feed_id',s.source_feed_id,'external_id',s.external_id,
        'author_handle',s.author_handle,'published_at',s.published_at,
        'canonical_url',s.canonical_url,'body',s.body,'media',s.media,'source_hash',s.source_hash)::text,
        'sha256'),'hex') from public.source_items as s where s.id=target_source_item_id
$$;
revoke all on function private.official_x_same_day_refresh_source_sha256(uuid)
    from public,anon,authenticated,service_role;

-- The public jobs identity trigger does not freeze arbitrary input JSON. Bind
-- the entire source snapshot and derived input, not just identity markers.
create function private.official_x_same_day_refresh_input_valid(target_refresh_id uuid)
returns boolean language sql stable security definer set search_path = '' as $$
    select exists(select 1 from private.official_x_same_day_refresh_requests as r
        join public.jobs as j on j.id=r.job_id
        join public.source_items as s on s.id=r.source_item_id
          and s.workspace_id=r.workspace_id and s.client_id=r.client_id
        where r.refresh_id=target_refresh_id
          and r.job_input_sha256=pg_catalog.encode(extensions.digest(j.input::text,'sha256'),'hex')
          and r.source_snapshot_sha256=private.official_x_same_day_refresh_source_sha256(s.id)
          and j.input=jsonb_build_object('workflow','official_x_review_draft_v1','kst_date',r.kst_date,
            'source_item_ids',jsonb_build_array(s.id::text),'content_kind','daily_news','request_id',r.request_id,
            'source_content',btrim(s.body),'source_url',s.canonical_url,
            'source_image_url',coalesce((select m.value->>'url' from jsonb_array_elements(s.media) as m(value)
              where m.value->>'type'='photo' and char_length(m.value->>'url')<=2048
                and m.value->>'url' ~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./?=&%:+-]+$'
              order by m.value->>'media_key' limit 1),''),
            'manual_only',true,'same_day_refresh_id',r.refresh_id))
$$;
revoke all on function private.official_x_same_day_refresh_input_valid(uuid)
    from public,anon,authenticated,service_role;

create function public.queue_official_x_same_day_refresh(
    target_workspace_id uuid,target_client_id text,target_kst_date date,
    target_predecessor_job_id uuid,target_predecessor_content_item_id uuid,
    target_predecessor_content_version_id uuid,target_source_item_id uuid,
    target_refresh_id uuid,target_request_id uuid,target_release_sha text,
    target_expires_at timestamptz
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    receipt private.official_x_same_day_refresh_requests%rowtype;
    source public.source_items%rowtype;
    new_job_id uuid:=gen_random_uuid();
    image_url text;
    decision_now timestamptz;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'same_day_refresh_service_role_required' using errcode='42501';
    end if;
    if target_workspace_id is null or target_client_id is null
       or target_client_id not in ('yellow','babylon','squid','origintrail')
       or target_kst_date is null or target_predecessor_job_id is null
       or target_predecessor_content_item_id is null or target_predecessor_content_version_id is null
       or target_source_item_id is null or target_refresh_id is null or target_request_id is null
       or target_request_id=target_predecessor_content_item_id or target_refresh_id=target_request_id
       or target_release_sha is null or target_release_sha !~ '^[a-f0-9]{40}$'
       or target_expires_at is null
       or target_refresh_id='00000000-0000-0000-0000-000000000000'::uuid
       or target_request_id='00000000-0000-0000-0000-000000000000'::uuid then
        raise exception 'same_day_refresh_queue_arguments_invalid' using errcode='22023';
    end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        target_workspace_id::text||':'||target_kst_date::text,0));
    select * into receipt from private.official_x_same_day_refresh_requests
        where refresh_id=target_refresh_id for update;
    if found then
        if receipt.workspace_id is distinct from target_workspace_id
           or receipt.client_id is distinct from target_client_id
           or receipt.kst_date is distinct from target_kst_date
           or receipt.predecessor_job_id is distinct from target_predecessor_job_id
           or receipt.predecessor_content_item_id is distinct from target_predecessor_content_item_id
           or receipt.predecessor_content_version_id is distinct from target_predecessor_content_version_id
           or receipt.source_item_id is distinct from target_source_item_id
           or receipt.request_id is distinct from target_request_id
           or receipt.release_sha is distinct from target_release_sha
           or receipt.expires_at is distinct from target_expires_at then
            raise exception 'same_day_refresh_receipt_conflict' using errcode='23505';
        end if;
        -- This returns a receipt only, even after expiry/failure. It is not a claim.
        return private.official_x_same_day_refresh_receipt(target_refresh_id,true);
    end if;
    decision_now:=clock_timestamp();
    if target_kst_date is distinct from pg_catalog.timezone('Asia/Seoul',decision_now)::date
       or target_expires_at<=decision_now
       or target_expires_at>statement_timestamp()+interval '2 hours' then
        raise exception 'same_day_refresh_queue_window_invalid' using errcode='22023';
    end if;
    -- Lock existing rows in one deterministic order. Only the fresh source-state
    -- row is subsequently changed; predecessor catalog/slot/job stay immutable.
    perform 1 from public.jobs where id=target_predecessor_job_id for share;
    perform 1 from public.content_items where id=target_predecessor_content_item_id for share;
    perform 1 from public.content_versions where id=target_predecessor_content_version_id for share;
    select s.* into source from public.source_items as s
        where s.workspace_id=target_workspace_id and s.client_id=target_client_id
          and s.id=target_source_item_id for share;
    if not found then
        raise exception 'same_day_refresh_source_ineligible' using errcode='23514';
    end if;
    perform 1 from public.source_feeds where id=source.source_feed_id
        and workspace_id=target_workspace_id and client_id=target_client_id for update;
    if private.official_x_same_day_refresh_source_valid(target_workspace_id,target_client_id,
            target_source_item_id,clock_timestamp()) is not true
       or private.official_x_same_day_refresh_predecessor_valid(target_workspace_id,target_client_id,
            target_kst_date,target_predecessor_job_id,target_predecessor_content_item_id,
            target_predecessor_content_version_id,target_source_item_id) is not true then
        raise exception 'same_day_refresh_source_or_predecessor_ineligible' using errcode='23514';
    end if;
    perform 1 from private.official_x_source_state where workspace_id=target_workspace_id
        and client_id=target_client_id and source_item_id=source.id and queued_job_id is null for update;
    if not found or exists(select 1 from public.content_items where id=target_request_id)
       or exists(select 1 from private.official_x_style_reference_packs where request_id=target_request_id)
       or exists(select 1 from public.jobs as producer where producer.job_kind='generate'
          and (producer.input->>'request_id'=target_request_id::text
            or (producer.workspace_id=target_workspace_id and producer.client_id=target_client_id
              and producer.input->'source_item_ids' @> jsonb_build_array(source.id::text))))
       or exists(select 1 from public.content_source_links where workspace_id=target_workspace_id
          and client_id=target_client_id and source_item_id=source.id) then
        raise exception 'same_day_refresh_source_or_request_already_reserved' using errcode='23505';
    end if;
    select m.value->>'url' into image_url from jsonb_array_elements(source.media) as m(value)
        where m.value->>'type'='photo' and char_length(m.value->>'url')<=2048
          and m.value->>'url' ~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./?=&%:+-]+$'
        order by m.value->>'media_key' limit 1;
    -- Row-lock waits may cross approval expiry, source freshness or midnight.
    -- Check again immediately before reserving any new row.
    decision_now:=clock_timestamp();
    if target_expires_at<=decision_now
       or target_kst_date is distinct from pg_catalog.timezone('Asia/Seoul',decision_now)::date
       or private.official_x_same_day_refresh_source_valid(target_workspace_id,target_client_id,
            target_source_item_id,decision_now) is not true then
        raise exception 'same_day_refresh_queue_window_invalid' using errcode='22023';
    end if;
    insert into public.jobs(id,workspace_id,client_id,job_kind,status,priority,input,output,
        idempotency_key,attempts,max_attempts,available_at)
    values(new_job_id,target_workspace_id,target_client_id,'generate','queued',0,
        jsonb_build_object('workflow','official_x_review_draft_v1','kst_date',target_kst_date,
          'source_item_ids',jsonb_build_array(source.id::text),'content_kind','daily_news',
          'request_id',target_request_id,'source_content',btrim(source.body),
          'source_url',source.canonical_url,'source_image_url',coalesce(image_url,''),
          'manual_only',true,'same_day_refresh_id',target_refresh_id),
        '{}'::jsonb,'official-x-same-day-refresh:v1:'||target_refresh_id::text,0,1,statement_timestamp());
    insert into private.official_x_same_day_refresh_requests(refresh_id,workspace_id,client_id,kst_date,
        predecessor_job_id,predecessor_content_item_id,predecessor_content_version_id,
        source_item_id,request_id,job_id,release_sha,job_input_sha256,source_snapshot_sha256,expires_at)
    values(target_refresh_id,target_workspace_id,target_client_id,target_kst_date,
        target_predecessor_job_id,target_predecessor_content_item_id,target_predecessor_content_version_id,
        source.id,target_request_id,new_job_id,target_release_sha,
        (select pg_catalog.encode(extensions.digest(input::text,'sha256'),'hex') from public.jobs where id=new_job_id),
        private.official_x_same_day_refresh_source_sha256(source.id),target_expires_at);
    update private.official_x_source_state set queued_job_id=new_job_id,queued_at=statement_timestamp()
        where workspace_id=target_workspace_id and client_id=target_client_id
          and source_item_id=source.id and queued_job_id is null;
    if not found then
        raise exception 'same_day_refresh_source_owner_conflict' using errcode='23505';
    end if;
    return private.official_x_same_day_refresh_receipt(target_refresh_id,false);
end $$;

create function public.claim_official_x_same_day_refresh(
    target_workspace_id uuid,target_refresh_id uuid,target_job_id uuid,target_request_id uuid,
    target_source_item_id uuid,target_release_sha text,target_worker_id text,
    target_lease_seconds integer default 900
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    receipt private.official_x_same_day_refresh_requests%rowtype;
    job public.jobs%rowtype;
    feed_id uuid;
    decision_now timestamptz;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'same_day_refresh_service_role_required' using errcode='42501';
    end if;
    if target_workspace_id is null or target_refresh_id is null or target_job_id is null
       or target_request_id is null or target_source_item_id is null or target_release_sha is null
       or target_release_sha !~ '^[a-f0-9]{40}$' or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_lease_seconds is null or target_lease_seconds not between 60 and 900 then
        raise exception 'same_day_refresh_claim_arguments_invalid' using errcode='22023';
    end if;
    -- Serialize only the explicitly selected receipt, never scan/FIFO another job.
    select * into receipt from private.official_x_same_day_refresh_requests
        where workspace_id=target_workspace_id and refresh_id=target_refresh_id for update;
    if not found or receipt.job_id is distinct from target_job_id
       or receipt.request_id is distinct from target_request_id
       or receipt.source_item_id is distinct from target_source_item_id
       or receipt.release_sha is distinct from target_release_sha then
        raise exception 'same_day_refresh_claim_binding_invalid' using errcode='23514';
    end if;
    decision_now:=clock_timestamp();
    if receipt.claimed_at is not null or receipt.expires_at<=decision_now
       or receipt.kst_date<>pg_catalog.timezone('Asia/Seoul',decision_now)::date then
        return null;
    end if;
    perform 1 from public.jobs where id=receipt.predecessor_job_id for share;
    perform 1 from public.content_items where id=receipt.predecessor_content_item_id for share;
    perform 1 from public.content_versions where id=receipt.predecessor_content_version_id for share;
    perform 1 from public.source_items where id=receipt.source_item_id for share;
    select * into job from public.jobs where id=receipt.job_id for update;
    if job.workspace_id is distinct from receipt.workspace_id or job.client_id is distinct from receipt.client_id
       or job.job_kind is distinct from 'generate' or job.status is distinct from 'queued'
       or job.attempts is distinct from 0 or job.max_attempts is distinct from 1
       or job.content_item_id is not null or job.locked_by is not null
       or job.input->>'workflow' is distinct from 'official_x_review_draft_v1'
       or job.input->'manual_only' is distinct from 'true'::jsonb
       or job.input->>'content_kind' is distinct from 'daily_news'
       or job.input->>'kst_date' is distinct from receipt.kst_date::text
       or job.input->>'request_id' is distinct from receipt.request_id::text
       or job.input->>'same_day_refresh_id' is distinct from receipt.refresh_id::text
       or job.input->'source_item_ids' is distinct from jsonb_build_array(receipt.source_item_id::text)
       or job.output is distinct from '{}'::jsonb
       or private.official_x_same_day_refresh_input_valid(receipt.refresh_id) is not true then return null; end if;
    select source_feed_id into feed_id from public.source_items where id=receipt.source_item_id;
    perform 1 from public.source_feeds where id=feed_id for update;
    if private.official_x_same_day_refresh_source_valid(receipt.workspace_id,receipt.client_id,
            receipt.source_item_id,clock_timestamp()) is not true
       or private.official_x_same_day_refresh_predecessor_valid(receipt.workspace_id,receipt.client_id,
            receipt.kst_date,receipt.predecessor_job_id,receipt.predecessor_content_item_id,
            receipt.predecessor_content_version_id,receipt.source_item_id) is not true
       or not exists(select 1 from private.official_x_source_state where workspace_id=receipt.workspace_id
          and client_id=receipt.client_id and source_item_id=receipt.source_item_id and queued_job_id=job.id)
       or exists(select 1 from public.content_items where id=receipt.request_id)
       or exists(select 1 from public.content_source_links where workspace_id=receipt.workspace_id
          and client_id=receipt.client_id and source_item_id=receipt.source_item_id)
       or exists(select 1 from public.jobs as producer where producer.id<>job.id
          and producer.workspace_id=receipt.workspace_id and producer.client_id=receipt.client_id
          and producer.job_kind='generate' and (producer.input->>'request_id'=receipt.request_id::text
            or producer.input->'source_item_ids' @> jsonb_build_array(receipt.source_item_id::text))) then
        return null;
    end if;
    decision_now:=clock_timestamp();
    if receipt.expires_at<=decision_now
       or receipt.kst_date<>pg_catalog.timezone('Asia/Seoul',decision_now)::date
       or private.official_x_same_day_refresh_source_valid(receipt.workspace_id,receipt.client_id,
            receipt.source_item_id,decision_now) is not true then return null; end if;
    update private.official_x_same_day_refresh_requests
        set claimed_at=decision_now,claimed_by=target_worker_id where refresh_id=receipt.refresh_id;
    update public.jobs set status='running',attempts=1,locked_by=target_worker_id,
        locked_at=decision_now,lease_expires_at=least(receipt.expires_at,
          decision_now+make_interval(secs=>target_lease_seconds)),started_at=decision_now,
        output=jsonb_build_object('execution_plane','studio_sync')
        where id=job.id returning * into job;
    return jsonb_build_object('job_id',job.id,'workspace_id',job.workspace_id,'client_id',job.client_id,
        'status',job.status,'attempts',job.attempts,'max_attempts',job.max_attempts,
        'locked_by',job.locked_by,'lease_expires_at',job.lease_expires_at,'input',job.input,
        'refresh_id',receipt.refresh_id,'release_sha',receipt.release_sha,
        'execution_plane','studio_sync','origintrail_batch_eligible',false,'batch_handoff_recovery_only',false);
end $$;

-- Inspect is advisory readiness only. It never reconciles, claims or grants
-- delivery. Existing natural-only private-review eligibility remains unchanged.
create function public.inspect_official_x_same_day_refresh(
    target_workspace_id uuid,target_refresh_id uuid,target_content_version_id uuid
) returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare
    receipt private.official_x_same_day_refresh_requests%rowtype;
    job public.jobs%rowtype;
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    source public.source_items%rowtype;
    banner public.assets%rowtype;
    ready boolean:=false;
    result jsonb;
    decision_now timestamptz:=statement_timestamp();
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'same_day_refresh_service_role_required' using errcode='42501';
    end if;
    if target_workspace_id is null or target_refresh_id is null or target_content_version_id is null then
        raise exception 'same_day_refresh_inspect_arguments_invalid' using errcode='22023';
    end if;
    select * into receipt from private.official_x_same_day_refresh_requests
        where workspace_id=target_workspace_id and refresh_id=target_refresh_id;
    if not found then raise exception 'same_day_refresh_inspect_binding_invalid' using errcode='23514'; end if;
    select * into job from public.jobs where id=receipt.job_id;
    select * into item from public.content_items where id=receipt.request_id;
    select * into version from public.content_versions where id=target_content_version_id;
    select * into source from public.source_items where id=receipt.source_item_id;
    result:=jsonb_build_object('status','refresh_not_ready','refresh_id',receipt.refresh_id,
        'workspace_id',receipt.workspace_id,'client_id',receipt.client_id,'kst_date',receipt.kst_date,
        'job_id',receipt.job_id,'request_id',receipt.request_id,'source_item_id',receipt.source_item_id,
        'content_item_id',receipt.request_id,'content_version_id',target_content_version_id,
        'banner_asset_id',null,'banner_sha256',null,'source_published_at',source.published_at,
        'source_age_seconds',extract(epoch from decision_now-source.published_at),
        'release_sha',receipt.release_sha,'execution_plane','studio_sync',
        'execution_authorized',false,'delivery_authorized',false);
    if receipt.claimed_at is null or receipt.claimed_by is null
       or receipt.kst_date<>pg_catalog.timezone('Asia/Seoul',decision_now)::date
       or job.workspace_id is distinct from receipt.workspace_id or job.client_id is distinct from receipt.client_id
       or job.status is distinct from 'succeeded' or job.job_kind is distinct from 'generate'
       or (job.content_item_id is not null and job.content_item_id is distinct from item.id)
       or job.attempts is distinct from 1 or job.max_attempts is distinct from 1
       or job.input->>'workflow' is distinct from 'official_x_review_draft_v1'
       or job.input->'manual_only' is distinct from 'true'::jsonb
       or job.input->>'content_kind' is distinct from 'daily_news'
       or job.input->>'kst_date' is distinct from receipt.kst_date::text
       or job.input->>'same_day_refresh_id' is distinct from receipt.refresh_id::text
       or job.input->>'request_id' is distinct from receipt.request_id::text
       or job.input->'source_item_ids' is distinct from jsonb_build_array(receipt.source_item_id::text)
       or job.output->>'content_item_id' is distinct from item.id::text
       or job.output->>'content_version_id' is distinct from target_content_version_id::text
       or job.output->>'completed_by' is distinct from receipt.claimed_by
       or job.output->'source_item_ids' is distinct from jsonb_build_array(receipt.source_item_id::text)
       or pg_catalog.timezone('Asia/Seoul',job.finished_at)::date is distinct from receipt.kst_date
       or item.id is distinct from receipt.request_id or item.workspace_id is distinct from receipt.workspace_id
       or item.client_id is distinct from receipt.client_id or item.content_kind is distinct from 'daily_news'
       or item.status is distinct from 'needs_review' or item.current_version_id is distinct from version.id
       or version.id is distinct from target_content_version_id
       or version.workspace_id is distinct from receipt.workspace_id or version.content_item_id is distinct from item.id
       or version.generation_meta->'mock_mode' is distinct from 'false'::jsonb
       or version.generation_meta->>'request_id' is distinct from item.id::text
       or pg_catalog.timezone('Asia/Seoul',version.created_at)::date is distinct from receipt.kst_date
       or jsonb_typeof(version.channel_copy->'telegram') is distinct from 'string'
       or jsonb_typeof(version.channel_copy->'x') is distinct from 'string'
       or btrim(version.title)='' or char_length(version.title) not between 1 and 160
       or btrim(version.channel_copy->>'telegram')=''
       or char_length(version.channel_copy->>'telegram') not between 1 and 3400
       or btrim(version.channel_copy->>'x')='' or char_length(version.channel_copy->>'x') not between 1 and 1000
       or version.content->'source'->>'submitted_content' is distinct from job.input->>'source_content'
       or version.content->'source'->>'url' is distinct from job.input->>'source_url'
       or private.official_x_same_day_refresh_source_valid(receipt.workspace_id,receipt.client_id,
            receipt.source_item_id,decision_now) is not true
       or private.official_x_same_day_refresh_undelivered(receipt.workspace_id,item.id) is not true
       or private.official_x_same_day_refresh_input_valid(receipt.refresh_id) is not true
       or exists(select 1 from private.grok_qa_dispatch_outbox where workspace_id=receipt.workspace_id
            and content_item_id=item.id)
       or not exists(select 1 from private.official_x_source_state where workspace_id=receipt.workspace_id
            and client_id=receipt.client_id and source_item_id=receipt.source_item_id and queued_job_id=job.id)
       or not exists(select 1 from private.official_x_style_reference_packs where workspace_id=receipt.workspace_id
            and client_id=receipt.client_id and request_id=receipt.request_id
            and primary_source_item_id=receipt.source_item_id
            and reference_pack_hash=version.generation_meta->>'style_reference_pack_hash')
       or (select count(*) from public.content_source_links where content_item_id=item.id)<>1
       or not exists(select 1 from public.content_source_links where workspace_id=receipt.workspace_id
            and client_id=receipt.client_id and content_item_id=item.id and source_item_id=receipt.source_item_id
            and position=0)
       or (select count(*) from public.jobs as producer where producer.workspace_id=receipt.workspace_id
            and producer.client_id=receipt.client_id and producer.job_kind='generate'
            and (producer.content_item_id=item.id or producer.input->>'request_id'=item.id::text
              or producer.output->>'content_item_id'=item.id::text
              or producer.input->'source_item_ids' @> jsonb_build_array(source.id::text)))<>1
       or (select count(*) from public.assets where workspace_id=receipt.workspace_id
            and content_item_id=item.id and content_version_id=version.id and asset_kind='png')<>1 then
        return result;
    end if;
    select a.* into banner from public.assets as a join storage.objects as stored
        on stored.bucket_id=a.storage_bucket and stored.name=a.storage_path
        where a.workspace_id=receipt.workspace_id and a.content_item_id=item.id and a.content_version_id=version.id
          and a.id::text=version.deliverables->>'primary_asset_id'
          and a.asset_kind='png' and a.mime_type='image/png' and a.storage_bucket='content-studio'
          and a.metadata->>'filename'='news-card.png'
          and a.storage_path=receipt.workspace_id::text||'/'||receipt.client_id||'/'||a.id::text||'/news-card.png'
          and a.sha256 ~ '^[a-f0-9]{64}$' and a.byte_size between 9 and 10000000
          and a.width>0 and a.height>0;
    if not found then return result; end if;
    return result||jsonb_build_object('status','refresh_ready','banner_asset_id',banner.id,'banner_sha256',banner.sha256);
end $$;

revoke all on function public.queue_official_x_same_day_refresh(uuid,text,date,uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz)
    from public,anon,authenticated,service_role;
revoke all on function public.claim_official_x_same_day_refresh(uuid,uuid,uuid,uuid,uuid,text,text,integer)
    from public,anon,authenticated,service_role;
revoke all on function public.inspect_official_x_same_day_refresh(uuid,uuid,uuid)
    from public,anon,authenticated,service_role;
grant execute on function public.queue_official_x_same_day_refresh(uuid,text,date,uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz)
    to service_role;
grant execute on function public.claim_official_x_same_day_refresh(uuid,uuid,uuid,uuid,uuid,text,text,integer)
    to service_role;
grant execute on function public.inspect_official_x_same_day_refresh(uuid,uuid,uuid) to service_role;

-- Preserve natural and Batch dispatch exactly; exclude only a ledger-owned
-- one-shot refresh after all existing authoritative validations.
create or replace function private.enqueue_official_x_grok_qa_dispatch()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    target_content_version_id uuid;
    target_job_id uuid;
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    review_job public.jobs%rowtype;
    primary_source public.source_items%rowtype;
    expected_handle text;
    primary_source_count integer;
begin
    if new.event_type not in (
        'official_x_review_draft_completed',
        'origintrail_batch_review_pack_materialized'
    ) then
        return new;
    end if;
    if new.entity_type is distinct from 'content_item'
       or new.entity_id is null
       or jsonb_typeof(new.data) <> 'object'
       or coalesce(new.data ->> 'content_version_id', '')
            !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
        raise exception 'Grok QA dispatch source event is invalid'
            using errcode = '23514';
    end if;
    target_content_version_id := (new.data ->> 'content_version_id')::uuid;

    select current_item.* into item
    from public.content_items as current_item
    where current_item.workspace_id = new.workspace_id
      and current_item.id = new.entity_id
    for key share;
    if not found
       or item.status is distinct from 'needs_review'
       or item.current_version_id is distinct from target_content_version_id
       or item.client_id not in ('yellow', 'origintrail', 'squid', 'babylon') then
        raise exception 'Grok QA dispatch target is not current needs_review'
            using errcode = '23514';
    end if;
    -- Article/tutorial records intentionally remain in the manual Studio flow:
    -- their canonical durable banner contract does not exist yet. Do not make
    -- their otherwise-valid completion event fail, and never enqueue them.
    if item.content_kind is distinct from 'daily_news' then
        return new;
    end if;

    select current_version.* into version
    from public.content_versions as current_version
    where current_version.workspace_id = new.workspace_id
      and current_version.content_item_id = item.id
      and current_version.id = target_content_version_id;
    if not found or version.generation_meta -> 'mock_mode' = 'true'::jsonb then
        raise exception 'Grok QA dispatch target version is not eligible'
            using errcode = '23514';
    end if;

    expected_handle := case item.client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
        else null
    end;
    select count(*) into primary_source_count
        from public.content_source_links as link
        join public.source_items as source
          on source.workspace_id = link.workspace_id
         and source.client_id = link.client_id
         and source.id = link.source_item_id
        join public.source_feeds as feed
          on feed.workspace_id = source.workspace_id
         and feed.client_id = source.client_id
         and feed.id = source.source_feed_id
        where link.workspace_id = item.workspace_id
          and link.client_id = item.client_id
          and link.content_item_id = item.id
          and source.source_type = 'tweet'
          and feed.provider = 'x'
          and feed.handle = expected_handle
          and feed.active is true
          and link.position = 0;
    if primary_source_count <> 1 then
        raise exception 'Grok QA dispatch lacks an active official X source'
            using errcode = '23514';
    end if;
    select source.* into primary_source
    from public.content_source_links as link
    join public.source_items as source
      on source.workspace_id = link.workspace_id
     and source.client_id = link.client_id
     and source.id = link.source_item_id
    join public.source_feeds as feed
      on feed.workspace_id = source.workspace_id
     and feed.client_id = source.client_id
     and feed.id = source.source_feed_id
    where link.workspace_id = item.workspace_id
      and link.client_id = item.client_id
      and link.content_item_id = item.id
      and link.position = 0
      and source.source_type = 'tweet'
      and feed.provider = 'x'
      and feed.handle = expected_handle
      and feed.active is true;
    if not found
       or primary_source.author_handle is distinct from expected_handle
       or primary_source.canonical_url !~ (
           '^https://x\.' || 'com/' ||
           substring(expected_handle from 2) || '/status/[0-9]{1,19}$'
       )
       or primary_source.external_id is distinct from
            split_part(primary_source.canonical_url, '/', 6)
       or primary_source.published_at is null then
        raise exception 'Grok QA dispatch official X identity is invalid'
            using errcode = '23514';
    end if;

    if new.event_type = 'official_x_review_draft_completed' then
        if coalesce(new.data ->> 'job_id', '')
                !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
            raise exception 'Grok QA review job identity is invalid'
                using errcode = '23514';
        end if;
        target_job_id := (new.data ->> 'job_id')::uuid;
        select queued_job.* into review_job
        from public.jobs as queued_job
        where queued_job.workspace_id = new.workspace_id
          and queued_job.id = target_job_id;
        if not found
           or review_job.client_id is distinct from item.client_id
           or review_job.status is distinct from 'succeeded'
           or review_job.job_kind is distinct from 'generate'
           or review_job.input ->> 'workflow'
                is distinct from 'official_x_review_draft_v1'
           or not coalesce(
               review_job.input -> 'source_item_ids'
                   @> jsonb_build_array(primary_source.id::text),
               false
           )
           or review_job.output ->> 'content_item_id'
                is distinct from item.id::text
           or review_job.output ->> 'content_version_id'
                is distinct from target_content_version_id::text
           or not coalesce(
               new.data -> 'source_item_ids'
                   @> jsonb_build_array(primary_source.id::text),
               false
           ) then
            raise exception 'Grok QA review completion event is not authoritative'
                using errcode = '23514';
        end if;
    elsif item.client_id is distinct from 'origintrail'
       or item.content_kind is distinct from 'daily_news'
       or coalesce(new.data ->> 'job_id', '')
            !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or not exists (
           select 1
           from agent_runtime.origintrail_batch_review_packs as review_pack
           where review_pack.workspace_id = new.workspace_id
             and review_pack.job_id = (new.data ->> 'job_id')::uuid
             and review_pack.content_item_id = item.id
             and review_pack.content_version_id = target_content_version_id
             and review_pack.source_item_id = primary_source.id
       ) then
        raise exception 'Grok QA Batch materialization event is not authoritative'
            using errcode = '23514';
    end if;

    -- BEGIN exact isolated refresh QA exclusion
    -- The original authoritative event/source/catalog validations above still
    -- run first. A marker alone cannot suppress legacy/natural QA dispatch.
    if new.event_type = 'official_x_review_draft_completed'
       and exists(select 1 from private.official_x_same_day_refresh_requests
           where job_id = review_job.id) then
        if not coalesce(review_job.input -> 'manual_only' = 'true'::jsonb
       and review_job.attempts = 1 and review_job.max_attempts = 1
       and exists (
           select 1 from private.official_x_same_day_refresh_requests as refresh
           join private.official_x_source_state as state
             on state.workspace_id = refresh.workspace_id
            and state.client_id = refresh.client_id
            and state.source_item_id = refresh.source_item_id
            and state.queued_job_id = refresh.job_id
           where refresh.workspace_id = new.workspace_id
             and refresh.client_id = item.client_id
             and refresh.job_id = review_job.id
             and refresh.request_id = item.id
             and refresh.source_item_id = primary_source.id
             and refresh.claimed_at is not null
             and refresh.claimed_at < refresh.expires_at
             and refresh.claimed_by is not null
             and refresh.release_sha ~ '^[a-f0-9]{40}$'
             and private.official_x_same_day_refresh_input_valid(refresh.refresh_id)
             and refresh.kst_date::text = review_job.input ->> 'kst_date'
             and refresh.refresh_id::text = review_job.input ->> 'same_day_refresh_id'
             and refresh.request_id::text = review_job.input ->> 'request_id'
             and review_job.input -> 'source_item_ids'
                   = jsonb_build_array(refresh.source_item_id::text)
             and review_job.output -> 'source_item_ids'
                   = jsonb_build_array(refresh.source_item_id::text)
             and review_job.output ->> 'completed_by' = refresh.claimed_by
             and review_job.output ->> 'content_item_id' = refresh.request_id::text
             and review_job.output ->> 'content_version_id' = target_content_version_id::text
             and new.data -> 'source_item_ids'
                   = jsonb_build_array(refresh.source_item_id::text)
       ), false) then
            raise exception 'same_day_refresh_completion_binding_invalid'
                using errcode = '23514';
        end if;
        return new;
    end if;
    -- END exact isolated refresh QA exclusion

    insert into private.grok_qa_dispatch_outbox (
        workspace_id, content_item_id, content_version_id, client_id,
        content_kind, source_item_id, source_url, source_author_handle,
        source_published_at, source_event_id, source_event_type
    ) values (
        new.workspace_id, item.id, target_content_version_id, item.client_id,
        item.content_kind, primary_source.id, primary_source.canonical_url,
        primary_source.author_handle, primary_source.published_at,
        new.id, new.event_type
    ) on conflict (workspace_id, content_version_id) do nothing;

    return new;
end;
$$;
commit;
