-- LOCAL_ROLLBACK_PROOF: exact installed function body and service-only ACL.
-- Repository-only proof input, not a migration or production apply script.
-- The transaction always rolls back; executable fixture tests separately
-- substitute a local clock only inside a disposable PostgreSQL instance.
begin;

create function public.claim_exact_natural_review_draft_job(
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
    gate_start constant timestamptz := '2026-10-14T00:00:00Z';
    gate_end constant timestamptz := '2026-10-14T02:00:00Z';
    gate_day constant date := '2026-10-14';
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
       or job.input->>'source_image_url' is null
       or char_length(job.input->>'source_image_url')>2048
       or (job.input->>'source_image_url') !~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./?=&%:+-]+$'
       or jsonb_typeof(source.media) is distinct from 'array' then return null; end if;
    if not exists(select 1 from jsonb_array_elements(source.media) as media(value)
        where media.value->>'type' in ('photo','video','animated_gif')
          and media.value->>'url'=job.input->>'source_image_url') then return null; end if;
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

-- Only the NEW routine's ACL. Existing roles/grants/routines remain unchanged.
revoke all on function public.claim_exact_natural_review_draft_job(
    uuid,text,uuid,uuid,uuid,text,text,text,integer) from public,anon,authenticated,service_role;
grant execute on function public.claim_exact_natural_review_draft_job(
    uuid,text,uuid,uuid,uuid,text,text,text,integer) to service_role;

rollback; -- LOCAL_ROLLBACK_PROOF; never change to COMMIT here.
