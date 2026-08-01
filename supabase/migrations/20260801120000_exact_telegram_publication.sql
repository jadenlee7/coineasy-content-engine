-- Exact approved-version Telegram delivery.
--
-- The Studio session is authenticated by Netlify rather than Supabase Auth, so
-- its request RPC is service-only.  A publication pins one immutable approved
-- daily-news version, its exact Telegram caption, and its one private PNG.  The
-- provider call is deliberately separated from claim by an irreversible attempt
-- marker: after that marker, a lost response or lease can only become
-- delivery_unknown and can never be automatically retried.

begin;

alter table public.publications
    drop constraint publications_status_check;

alter table public.publications
    add constraint publications_status_check check (
        status in (
            'queued', 'publishing', 'published', 'failed', 'cancelled',
            'delivery_unknown'
        )
    ),
    add column delivery_attempt_id uuid,
    add column delivery_started_at timestamptz,
    add column delivery_request_sha256 text,
    add constraint publications_delivery_attempt_check check (
        (delivery_attempt_id is null
         and delivery_started_at is null
         and delivery_request_sha256 is null)
        or
        (delivery_attempt_id is not null
         and delivery_started_at is not null
         and delivery_request_sha256 ~ '^[a-f0-9]{64}$')
    );

create unique index publications_exact_telegram_once_idx
    on public.publications (workspace_id, content_version_id, channel)
    where channel = 'telegram'
      and request_payload ->> 'workflow' = 'exact_telegram_publication_v1';

create unique index jobs_exact_telegram_once_idx
    on public.jobs (
        workspace_id,
        (input ->> 'content_version_id'),
        (input ->> 'channel')
    )
    where job_kind = 'publish'
      and input ->> 'workflow' = 'exact_telegram_publication_v1';

create or replace function private.exact_telegram_asset_snapshot(
    target_asset public.assets,
    target_storage_object jsonb
)
returns jsonb
language sql
immutable
set search_path = ''
as $$
    select jsonb_build_object(
        'asset_id', (target_asset).id,
        'asset_kind', (target_asset).asset_kind,
        'sha256', (target_asset).sha256,
        'byte_size', (target_asset).byte_size,
        'storage_bucket', (target_asset).storage_bucket,
        'storage_path', (target_asset).storage_path,
        'mime_type', (target_asset).mime_type,
        'width', (target_asset).width,
        'height', (target_asset).height,
        'metadata', (target_asset).metadata,
        'storage_object', jsonb_build_object(
            'id', target_storage_object -> 'id',
            'created_at', target_storage_object -> 'created_at',
            'updated_at', target_storage_object -> 'updated_at',
            'version', target_storage_object -> 'version',
            'metadata', target_storage_object -> 'metadata',
            'user_metadata', target_storage_object -> 'user_metadata'
        )
    )
$$;

-- Serialize every active Telegram publication mutation on one immutable tuple
-- key. INSERT can wait because no publication tuple is held yet. PostgreSQL
-- locks P before a BEFORE UPDATE row trigger, so UPDATE uses a non-blocking
-- acquisition and fails closed instead of introducing P -> advisory cycles.
create or replace function private.enforce_exact_telegram_publication_exclusivity()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    incoming_is_exact boolean;
    incoming_is_manual_observation boolean;
    tuple_lock_key bigint;
begin
    if new.channel <> 'telegram'
       or new.status not in (
           'queued', 'publishing', 'published', 'delivery_unknown'
       ) then
        return new;
    end if;

    tuple_lock_key := pg_catalog.hashtextextended(
        new.workspace_id::text || ':'
            || new.content_item_id::text || ':'
            || new.content_version_id::text || ':telegram',
        0
    );
    if tg_op = 'UPDATE' then
        if not pg_catalog.pg_try_advisory_xact_lock(tuple_lock_key) then
            raise exception
                'competing Telegram publication mutation is in progress'
                using errcode = '55P03';
        end if;
    else
        perform pg_catalog.pg_advisory_xact_lock(tuple_lock_key);
    end if;

    incoming_is_exact := coalesce(
        new.request_payload ->> 'workflow'
            = 'exact_telegram_publication_v1',
        false
    );
    incoming_is_manual_observation :=
        not incoming_is_exact
        and new.status = 'published'
        and new.request_payload = jsonb_build_object(
            'observation', 'manual_existing_publication',
            'external_publish_performed', false
        )
        and new.response_payload = jsonb_build_object(
            'observed', true,
            'external_publish_performed', false
        )
        and coalesce(
            new.external_url ~
                '^https://t\.me/squid_kor_update/[1-9][0-9]{0,18}$',
            false
        );
    if exists (
        select 1
        from public.publications as existing
        where existing.workspace_id = new.workspace_id
          and existing.id <> new.id
          and existing.content_item_id = new.content_item_id
          and existing.content_version_id = new.content_version_id
          and existing.channel = 'telegram'
          and existing.status in (
              'queued', 'publishing', 'published', 'delivery_unknown'
          )
          and (
              (incoming_is_exact and existing.request_payload ->> 'workflow'
                   is distinct from 'exact_telegram_publication_v1')
              or
              (not incoming_is_exact and existing.request_payload ->> 'workflow'
                   = 'exact_telegram_publication_v1')
          )
          and not (
              incoming_is_manual_observation
              and existing.request_payload ->> 'workflow'
                    = 'exact_telegram_publication_v1'
              and existing.status = 'delivery_unknown'
          )
    ) then
        raise exception 'generic and exact Telegram publications cannot coexist'
            using errcode = '23505';
    end if;
    return new;
end;
$$;

create trigger publications_exact_telegram_exclusivity
before insert or update of
    workspace_id, content_item_id, content_version_id, channel, status,
    request_payload, response_payload, external_url
on public.publications
for each row execute function
    private.enforce_exact_telegram_publication_exclusivity();

create or replace function public.request_studio_telegram_publication(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    request_idempotency_key text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    approval public.approvals%rowtype;
    asset public.assets%rowtype;
    storage_object jsonb;
    asset_snapshot jsonb;
    existing_publication public.publications%rowtype;
    existing_job public.jobs%rowtype;
    publication_id uuid := gen_random_uuid();
    job_id uuid := gen_random_uuid();
    telegram_text text;
    asset_count integer;
begin
    if target_workspace_id is null
       or target_content_item_id is null
       or target_content_version_id is null
       or request_idempotency_key is null
       or request_idempotency_key
            !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
        raise exception 'exact Telegram publication request is invalid'
            using errcode = '22023';
    end if;

    select content.* into item
    from public.content_items as content
    where content.workspace_id = target_workspace_id
      and content.id = target_content_item_id
    for update;
    if not found then
        raise exception 'exact Telegram publication content item not found'
            using errcode = 'P0002';
    end if;

    -- A key is globally bound inside one workspace.  This check precedes all
    -- mutable workflow checks so an exact retry remains readable after delivery.
    -- Probe without a row lock first: a globally reused key belonging to a
    -- different item must not make this transaction lock J before that item.
    select queued_job.* into existing_job
    from public.jobs as queued_job
    where queued_job.workspace_id = target_workspace_id
      and queued_job.idempotency_key = lower(request_idempotency_key);
    if found then
        if existing_job.job_kind <> 'publish'
           or existing_job.content_item_id is distinct from target_content_item_id
           or existing_job.input ->> 'workflow'
                is distinct from 'exact_telegram_publication_v1'
           or existing_job.input ->> 'content_version_id'
                is distinct from target_content_version_id::text
           or existing_job.input ->> 'channel' is distinct from 'telegram' then
            raise exception 'exact Telegram publication idempotency conflict'
                using errcode = '23505';
        end if;
        select queued_job.* into existing_job
        from public.jobs as queued_job
        where queued_job.id = existing_job.id
          and queued_job.workspace_id = target_workspace_id
          and queued_job.content_item_id = target_content_item_id
          and queued_job.job_kind = 'publish'
          and queued_job.input ->> 'workflow'
                = 'exact_telegram_publication_v1'
        for update;
        if not found then
            raise exception 'exact Telegram publication idempotency state changed'
                using errcode = '40001';
        end if;
        select publication.* into existing_publication
        from public.publications as publication
        where publication.id = (existing_job.input ->> 'publication_id')::uuid
          and publication.workspace_id = existing_job.workspace_id
          and publication.content_item_id = target_content_item_id
          and publication.content_version_id = target_content_version_id
          and publication.channel = 'telegram'
          and publication.request_payload ->> 'workflow'
                = 'exact_telegram_publication_v1';
        if not found then
            raise exception 'exact Telegram publication queue is inconsistent'
                using errcode = '23514';
        end if;
        return jsonb_build_object(
            'publication_id', existing_publication.id,
            'job_id', existing_job.id,
            'content_item_id', target_content_item_id,
            'content_version_id', target_content_version_id,
            'channel', 'telegram',
            'status', existing_publication.status,
            'delivery_started_at', existing_publication.delivery_started_at,
            'external_url', existing_publication.external_url,
            'error_code', existing_publication.response_payload ->> 'error_code',
            'reused', true
        );
    end if;

    -- Different request keys still converge, but J must be locked before P.
    select queued_job.* into existing_job
    from public.jobs as queued_job
    where queued_job.workspace_id = target_workspace_id
      and queued_job.content_item_id = target_content_item_id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow'
            = 'exact_telegram_publication_v1'
      and queued_job.input ->> 'content_version_id'
            = target_content_version_id::text
      and queued_job.input ->> 'channel' = 'telegram'
    for update;
    if found then
        select publication.* into existing_publication
        from public.publications as publication
        where publication.id = (existing_job.input ->> 'publication_id')::uuid
          and publication.workspace_id = target_workspace_id
          and publication.content_item_id = target_content_item_id
          and publication.content_version_id = target_content_version_id
          and publication.channel = 'telegram'
          and publication.request_payload ->> 'workflow'
                = 'exact_telegram_publication_v1'
        for update;
        if not found then
            raise exception 'exact Telegram publication queue is inconsistent'
                using errcode = '23514';
        end if;
        return jsonb_build_object(
            'publication_id', existing_publication.id,
            'job_id', existing_job.id,
            'content_item_id', target_content_item_id,
            'content_version_id', target_content_version_id,
            'channel', 'telegram',
            'status', existing_publication.status,
            'delivery_started_at', existing_publication.delivery_started_at,
            'external_url', existing_publication.external_url,
            'error_code', existing_publication.response_payload ->> 'error_code',
            'reused', true
        );
    end if;
    if exists (
        select 1
        from public.publications as orphaned_publication
        where orphaned_publication.workspace_id = target_workspace_id
          and orphaned_publication.content_item_id = target_content_item_id
          and orphaned_publication.content_version_id = target_content_version_id
          and orphaned_publication.channel = 'telegram'
          and orphaned_publication.request_payload ->> 'workflow'
                = 'exact_telegram_publication_v1'
    ) then
        raise exception 'exact Telegram publication queue is inconsistent'
            using errcode = '23514';
    end if;

    -- Never create an automated delivery beside an already observed/manual or
    -- legacy delivery for the same immutable version and channel.
    if exists (
        select 1
        from public.publications as other_publication
        where other_publication.workspace_id = target_workspace_id
          and other_publication.content_item_id = target_content_item_id
          and other_publication.content_version_id = target_content_version_id
          and other_publication.channel = 'telegram'
          and other_publication.status in (
              'queued', 'publishing', 'published', 'delivery_unknown'
          )
    ) or exists (
        select 1
        from public.jobs as other_job
        where other_job.workspace_id = target_workspace_id
          and other_job.content_item_id = target_content_item_id
          and other_job.job_kind = 'publish'
          and other_job.status in ('queued', 'running', 'retrying', 'succeeded')
          and other_job.input ->> 'content_version_id'
                = target_content_version_id::text
          and other_job.input ->> 'channel' = 'telegram'
    ) then
        raise exception 'Telegram publication already exists for this exact version'
            using errcode = '23505';
    end if;

    if item.client_id <> 'squid'
       or item.content_kind <> 'daily_news'
       or item.status <> 'approved'
       or item.current_version_id is distinct from target_content_version_id then
        raise exception 'only exact current approved Squid daily news can be published'
            using errcode = '23514';
    end if;

    select content_version.* into version
    from public.content_versions as content_version
    where content_version.workspace_id = target_workspace_id
      and content_version.content_item_id = target_content_item_id
      and content_version.id = target_content_version_id
    for key share;
    if not found then
        raise exception 'exact Telegram publication version is invalid'
            using errcode = '23514';
    end if;
    if version.generation_meta -> 'mock_mode' is distinct from 'false'::jsonb then
        raise exception 'exact Telegram publication requires a production version'
            using errcode = '23514';
    end if;
    if not exists (
        select 1
        from public.workspace_clients as client
        where client.workspace_id = target_workspace_id
          and client.client_id = item.client_id
          and client.active is true
    ) then
        raise exception 'exact Telegram publication client is not active'
            using errcode = '23514';
    end if;

    select review.* into approval
    from public.approvals as review
    where review.workspace_id = target_workspace_id
      and review.content_item_id = target_content_item_id
      and review.content_version_id = target_content_version_id
    order by review.created_at desc, review.id desc
    limit 1;
    if not found or approval.decision <> 'approved' then
        raise exception 'exact Telegram publication requires an approval record'
            using errcode = '23514';
    end if;

    telegram_text := version.channel_copy ->> 'telegram';
    if telegram_text is null
       or char_length(btrim(telegram_text)) = 0
       or char_length(telegram_text) > 1024 then
        raise exception 'exact Telegram caption must be 1 to 1024 characters'
            using errcode = '23514';
    end if;

    select count(*) into asset_count
    from public.assets as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.content_item_id = target_content_item_id
      and candidate.content_version_id = target_content_version_id;
    if asset_count <> 1 then
        raise exception 'exact Telegram publication requires exactly one PNG asset'
            using errcode = '23514';
    end if;

    select candidate.* into asset
    from public.assets as candidate
    join storage.objects as stored
      on stored.bucket_id = candidate.storage_bucket
     and stored.name = candidate.storage_path
    where candidate.workspace_id = target_workspace_id
      and candidate.content_item_id = target_content_item_id
      and candidate.content_version_id = target_content_version_id
      and candidate.asset_kind = 'png'
      and candidate.storage_bucket = 'content-studio'
      and candidate.mime_type = 'image/png'
      and candidate.byte_size between 8 and 10485760
      and candidate.sha256 ~ '^[a-f0-9]{64}$'
      and candidate.width between 1 and 10000
      and candidate.height between 1 and 10000
      and candidate.metadata ->> 'filename' = 'news-card.png'
      and candidate.storage_path = target_workspace_id::text || '/'
            || item.client_id || '/' || candidate.id::text || '/news-card.png';
    if found then
        select to_jsonb(stored) into storage_object
        from storage.objects as stored
        where stored.bucket_id = asset.storage_bucket
          and stored.name = asset.storage_path;
    end if;
    if not found
       or version.deliverables ->> 'primary_asset_id' is distinct from asset.id::text
       or version.deliverables -> 'asset_ids' is distinct from
            jsonb_build_array(asset.id::text) then
        raise exception 'exact Telegram publication PNG is invalid or missing'
            using errcode = '23514';
    end if;
    asset_snapshot := private.exact_telegram_asset_snapshot(
        asset, storage_object
    );

    -- Pre-generated IDs allow the only new-row path to preserve I -> J -> P.
    insert into public.jobs (
        id,
        workspace_id,
        client_id,
        content_item_id,
        job_kind,
        status,
        input,
        idempotency_key,
        max_attempts,
        available_at
    ) values (
        job_id,
        target_workspace_id,
        item.client_id,
        target_content_item_id,
        'publish',
        'queued',
        jsonb_build_object(
            'workflow', 'exact_telegram_publication_v1',
            'publication_id', publication_id,
            'content_version_id', target_content_version_id,
            'approval_id', approval.id,
            'asset_id', asset.id,
            'asset_snapshot', asset_snapshot,
            'channel', 'telegram'
        ),
        lower(request_idempotency_key),
        3,
        statement_timestamp()
    );

    insert into public.publications (
        id,
        workspace_id,
        client_id,
        content_item_id,
        content_version_id,
        channel,
        status,
        request_payload
    ) values (
        publication_id,
        target_workspace_id,
        item.client_id,
        target_content_item_id,
        target_content_version_id,
        'telegram',
        'queued',
        jsonb_build_object(
            'workflow', 'exact_telegram_publication_v1',
            'approval_id', approval.id,
            'asset_id', asset.id,
            'asset_snapshot', asset_snapshot,
            'request_idempotency_key', lower(request_idempotency_key)
        )
    );

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        target_workspace_id,
        'publication',
        publication_id,
        'exact_telegram_publication_requested',
        jsonb_build_object(
            'job_id', job_id,
            'content_item_id', target_content_item_id,
            'content_version_id', target_content_version_id,
            'approval_id', approval.id,
            'asset_id', asset.id
        )
    );

    return jsonb_build_object(
        'publication_id', publication_id,
        'job_id', job_id,
        'content_item_id', target_content_item_id,
        'content_version_id', target_content_version_id,
        'channel', 'telegram',
        'status', 'queued',
        'delivery_started_at', null,
        'external_url', null,
        'error_code', null,
        'reused', false
    );
end;
$$;

create or replace function public.reconcile_expired_exact_telegram_publication_leases(
    target_workspace_id uuid,
    target_limit integer default 100
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    candidate_job_id uuid;
    candidate_item_id uuid;
    item public.content_items%rowtype;
    job public.jobs%rowtype;
    publication public.publications%rowtype;
    reconciled_count integer := 0;
    retrying_count integer := 0;
    failed_count integer := 0;
    delivery_unknown_count integer := 0;
begin
    if target_workspace_id is null
       or target_limit is null
       or target_limit not between 1 and 100
       or not exists (
           select 1 from public.workspaces where id = target_workspace_id
       ) then
        raise exception 'exact Telegram lease reconciliation request is invalid'
            using errcode = '22023';
    end if;

    while reconciled_count < target_limit loop
        -- Lock only I while selecting work. SKIP LOCKED lets concurrent
        -- reconcilers partition items without ever taking J first.
        select queued_job.id, content.id
        into candidate_job_id, candidate_item_id
        from public.jobs as queued_job
        join public.content_items as content
          on content.workspace_id = queued_job.workspace_id
         and content.id = queued_job.content_item_id
        where queued_job.workspace_id = target_workspace_id
          and queued_job.job_kind = 'publish'
          and queued_job.input ->> 'workflow'
                = 'exact_telegram_publication_v1'
          and queued_job.status = 'running'
          and queued_job.lease_expires_at <= statement_timestamp()
        order by content.id, queued_job.id
        for update of content skip locked
        limit 1;
        if not found then
            exit;
        end if;

        select content.* into item
        from public.content_items as content
        where content.workspace_id = target_workspace_id
          and content.id = candidate_item_id
        for update;
        if not found then
            exit;
        end if;

        -- Re-read after the item lock; another transaction may have completed
        -- the job before I was acquired. A legacy J-first holder is skipped.
        select queued_job.* into job
        from public.jobs as queued_job
        where queued_job.id = candidate_job_id
          and queued_job.workspace_id = item.workspace_id
          and queued_job.content_item_id = item.id
          and queued_job.job_kind = 'publish'
          and queued_job.input ->> 'workflow'
                = 'exact_telegram_publication_v1'
          and queued_job.status = 'running'
          and queued_job.lease_expires_at <= statement_timestamp()
        for update skip locked;
        if not found then
            exit;
        end if;

        publication := null;
        if coalesce(
            (job.input ->> 'publication_id')
                ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            false
        ) then
            select delivery.* into publication
            from public.publications as delivery
            where delivery.id = (job.input ->> 'publication_id')::uuid
              and delivery.workspace_id = job.workspace_id
              and delivery.content_item_id = job.content_item_id
              and delivery.content_version_id::text
                    = job.input ->> 'content_version_id'
              and delivery.channel = 'telegram'
              and delivery.request_payload ->> 'workflow'
                    = 'exact_telegram_publication_v1'
              and delivery.request_payload ->> 'approval_id'
                    is not distinct from job.input ->> 'approval_id'
              and delivery.request_payload ->> 'asset_id'
                    is not distinct from job.input ->> 'asset_id'
              and delivery.request_payload -> 'asset_snapshot'
                    is not distinct from job.input -> 'asset_snapshot'
            for update;
        end if;

        if publication.id is null
           or (publication.delivery_started_at is null
               and publication.status <> 'queued')
           or (publication.delivery_started_at is not null
               and publication.status not in ('publishing', 'delivery_unknown')) then
            update public.jobs
            set status = 'failed',
                locked_by = null,
                locked_at = null,
                lease_expires_at = null,
                last_error_code = 'publication_state_invalid',
                last_error_message =
                    'The expired exact Telegram job has inconsistent pins.',
                finished_at = statement_timestamp()
            where id = job.id;
            reconciled_count := reconciled_count + 1;
            failed_count := failed_count + 1;
            continue;
        end if;

        if publication.delivery_started_at is null then
            if job.attempts >= job.max_attempts then
                update public.jobs
                set status = 'failed',
                    locked_by = null,
                    locked_at = null,
                    lease_expires_at = null,
                    last_error_code = 'lease_expired_before_attempt',
                    last_error_message =
                        'The worker lease expired before the attempt marker.',
                    finished_at = statement_timestamp()
                where id = job.id;
                update public.publications
                set status = 'failed',
                    last_error = 'lease_expired_before_attempt'
                where id = publication.id;
                failed_count := failed_count + 1;
            else
                update public.jobs
                set status = 'retrying',
                    available_at = statement_timestamp(),
                    locked_by = null,
                    locked_at = null,
                    lease_expires_at = null,
                    last_error_code = 'lease_expired_before_attempt',
                    last_error_message =
                        'The worker lease expired before the attempt marker.',
                    finished_at = null
                where id = job.id;
                update public.publications
                set status = 'queued',
                    last_error = 'lease_expired_before_attempt'
                where id = publication.id;
                retrying_count := retrying_count + 1;
            end if;
        else
            update public.jobs
            set status = 'failed',
                locked_by = null,
                locked_at = null,
                lease_expires_at = null,
                last_error_code = 'delivery_outcome_unknown',
                last_error_message =
                    'The worker lease expired after the attempt marker.',
                finished_at = statement_timestamp()
            where id = job.id;
            update public.publications
            set status = 'delivery_unknown',
                response_payload = jsonb_build_object(
                    'error_code', 'telegram_delivery_unknown'
                ),
                last_error = 'delivery_outcome_unknown'
            where id = publication.id;
            delivery_unknown_count := delivery_unknown_count + 1;
        end if;

        reconciled_count := reconciled_count + 1;
        insert into public.event_log (
            workspace_id, entity_type, entity_id, event_type, data
        ) values (
            job.workspace_id,
            'publication',
            publication.id,
            case
                when publication.delivery_started_at is not null
                    then 'exact_telegram_delivery_unknown'
                else 'exact_telegram_lease_expired_before_attempt'
            end,
            jsonb_build_object('job_id', job.id, 'lease_reconciliation', true)
        );
    end loop;

    return jsonb_build_object(
        'workspace_id', target_workspace_id,
        'reconciled_count', reconciled_count,
        'retrying_count', retrying_count,
        'failed_count', failed_count,
        'delivery_unknown_count', delivery_unknown_count
    );
end;
$$;

create or replace function public.claim_exact_telegram_publication_job(
    target_workspace_id uuid,
    target_worker_id text,
    target_lease_seconds integer default 300
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    candidate_job_id uuid;
    candidate_item_id uuid;
    candidate public.jobs%rowtype;
    publication public.publications%rowtype;
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    approval public.approvals%rowtype;
    asset public.assets%rowtype;
    storage_object jsonb;
    current_asset_snapshot jsonb;
    asset_count integer;
    telegram_text text;
    telegram_public_username text;
    validation_error text;
begin
    if target_workspace_id is null
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_lease_seconds is null
       or target_lease_seconds not between 180 and 600
       or not exists (
           select 1 from public.workspaces where id = target_workspace_id
       ) then
        raise exception 'exact Telegram worker lease request is invalid'
            using errcode = '22023';
    end if;

    perform public.reconcile_expired_exact_telegram_publication_leases(
        target_workspace_id, 100
    );

    select queued_job.id, content.id into candidate_job_id, candidate_item_id
    from public.jobs as queued_job
    join public.content_items as content
      on content.workspace_id = queued_job.workspace_id
     and content.id = queued_job.content_item_id
    where queued_job.workspace_id = target_workspace_id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow' = 'exact_telegram_publication_v1'
      and queued_job.status in ('queued', 'retrying')
      and queued_job.attempts < queued_job.max_attempts
      and queued_job.available_at <= statement_timestamp()
    order by queued_job.priority desc, queued_job.available_at,
             queued_job.created_at, queued_job.id
    for update of content skip locked
    limit 1;
    if not found then
        return null;
    end if;

    select content.* into item
    from public.content_items as content
    where content.workspace_id = target_workspace_id
      and content.id = candidate_item_id
    for update;
    if not found then
        return null;
    end if;

    select queued_job.* into candidate
    from public.jobs as queued_job
    where queued_job.id = candidate_job_id
      and queued_job.workspace_id = item.workspace_id
      and queued_job.content_item_id = item.id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow' = 'exact_telegram_publication_v1'
      and queued_job.status in ('queued', 'retrying')
      and queued_job.attempts < queued_job.max_attempts
      and queued_job.available_at <= statement_timestamp()
    for update skip locked;
    if not found then
        return null;
    end if;

    publication := null;
    if coalesce(
        (candidate.input ->> 'publication_id')
            ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
        false
    ) then
        select delivery.* into publication
        from public.publications as delivery
        where delivery.id = (candidate.input ->> 'publication_id')::uuid
          and delivery.workspace_id = candidate.workspace_id
          and delivery.content_item_id = candidate.content_item_id
          and delivery.content_version_id::text
                = candidate.input ->> 'content_version_id'
          and delivery.channel = 'telegram'
          and delivery.request_payload ->> 'workflow'
                = 'exact_telegram_publication_v1'
        for update;
    end if;
    if publication.id is null
       or publication.status <> 'queued'
       or publication.delivery_started_at is not null
       or publication.request_payload ->> 'approval_id'
            is distinct from candidate.input ->> 'approval_id'
       or publication.request_payload ->> 'asset_id'
            is distinct from candidate.input ->> 'asset_id'
       or publication.request_payload -> 'asset_snapshot'
            is distinct from candidate.input -> 'asset_snapshot' then
        validation_error := 'publication_state_invalid';
    end if;

    if item.client_id <> candidate.client_id
       or item.client_id <> 'squid'
       or item.content_kind <> 'daily_news'
       or item.status <> 'approved'
       or item.current_version_id::text
            is distinct from candidate.input ->> 'content_version_id'
       or not exists (
           select 1
           from public.workspace_clients as active_client
           where active_client.workspace_id = item.workspace_id
             and active_client.client_id = item.client_id
             and active_client.active is true
       ) then
        validation_error := coalesce(
            validation_error, 'approved_item_state_invalid'
        );
    end if;

    if validation_error is null then
        select content_version.* into version
        from public.content_versions as content_version
        where content_version.id = publication.content_version_id
          and content_version.workspace_id = candidate.workspace_id
          and content_version.content_item_id = candidate.content_item_id
        for key share;
        telegram_text := version.channel_copy ->> 'telegram';
        if not found
           or version.generation_meta -> 'mock_mode' is distinct from 'false'::jsonb
           or telegram_text is null
           or char_length(btrim(telegram_text)) = 0
           or char_length(telegram_text) > 1024 then
            validation_error := 'approved_version_payload_invalid';
        end if;
    end if;

    if validation_error is null then
        select review.* into approval
        from public.approvals as review
        where review.id = (candidate.input ->> 'approval_id')::uuid
          and review.workspace_id = candidate.workspace_id
          and review.content_item_id = candidate.content_item_id
          and review.content_version_id = publication.content_version_id
          and review.decision = 'approved';
        if not found then
            validation_error := 'approval_missing';
        end if;
    end if;

    if validation_error is null then
        select count(*) into asset_count
        from public.assets as stored_asset
        where stored_asset.workspace_id = candidate.workspace_id
          and stored_asset.content_item_id = candidate.content_item_id
          and stored_asset.content_version_id = publication.content_version_id;
        select stored_asset.* into asset
        from public.assets as stored_asset
        join storage.objects as stored
          on stored.bucket_id = stored_asset.storage_bucket
         and stored.name = stored_asset.storage_path
        where stored_asset.id = (candidate.input ->> 'asset_id')::uuid
          and stored_asset.workspace_id = candidate.workspace_id
          and stored_asset.content_item_id = candidate.content_item_id
          and stored_asset.content_version_id = publication.content_version_id
          and stored_asset.asset_kind = 'png'
          and stored_asset.storage_bucket = 'content-studio'
          and stored_asset.mime_type = 'image/png'
          and stored_asset.byte_size between 8 and 10485760
          and stored_asset.sha256 ~ '^[a-f0-9]{64}$'
          and stored_asset.width between 1 and 10000
          and stored_asset.height between 1 and 10000
          and stored_asset.metadata ->> 'filename' = 'news-card.png'
          and stored_asset.storage_path = candidate.workspace_id::text || '/'
                || candidate.client_id || '/' || stored_asset.id::text
                || '/news-card.png';
        if found then
            select to_jsonb(stored) into storage_object
            from storage.objects as stored
            where stored.bucket_id = asset.storage_bucket
              and stored.name = asset.storage_path;
        end if;
        if found then
            current_asset_snapshot := private.exact_telegram_asset_snapshot(
                asset, storage_object
            );
        end if;
        if not found
           or asset_count <> 1
           or version.deliverables ->> 'primary_asset_id'
                is distinct from asset.id::text
           or version.deliverables -> 'asset_ids'
                is distinct from jsonb_build_array(asset.id::text)
           or candidate.input -> 'asset_snapshot'
                is distinct from current_asset_snapshot
           or publication.request_payload -> 'asset_snapshot'
                is distinct from current_asset_snapshot then
            validation_error := 'approved_asset_invalid';
        end if;
    end if;

    if validation_error is not null then
        update public.jobs
        set status = 'failed',
            last_error_code = validation_error,
            last_error_message =
                'The queued exact Telegram payload failed database validation.',
            finished_at = statement_timestamp()
        where id = candidate.id;
        if publication.id is not null then
            update public.publications
            set status = 'failed', last_error = validation_error
            where id = publication.id;
        end if;
        return null;
    end if;

    telegram_public_username := case candidate.client_id
        when 'yellow' then 'yellowkorea_ann'
        when 'origintrail' then 'origintrailkr'
        when 'squid' then 'squid_kor_update'
        when 'babylon' then 'babylonbtc'
        else null
    end;
    if telegram_public_username is null then
        update public.jobs
        set status = 'failed',
            last_error_code = 'telegram_channel_not_allowlisted',
            last_error_message = 'No canonical Telegram channel is allowlisted.',
            finished_at = statement_timestamp()
        where id = candidate.id;
        update public.publications
        set status = 'failed', last_error = 'telegram_channel_not_allowlisted'
        where id = publication.id;
        return null;
    end if;

    update public.jobs
    set status = 'running',
        attempts = attempts + 1,
        locked_by = target_worker_id,
        locked_at = statement_timestamp(),
        lease_expires_at = statement_timestamp()
            + make_interval(secs => target_lease_seconds),
        started_at = coalesce(started_at, statement_timestamp()),
        finished_at = null,
        last_error_code = null,
        last_error_message = null
    where id = candidate.id
    returning * into candidate;

    return jsonb_build_object(
        'job_id', candidate.id,
        'publication_id', publication.id,
        'content_item_id', candidate.content_item_id,
        'content_version_id', publication.content_version_id,
        'approval_id', approval.id,
        'client_id', candidate.client_id,
        'attempts', candidate.attempts,
        'max_attempts', candidate.max_attempts,
        'locked_by', candidate.locked_by,
        'lease_expires_at', candidate.lease_expires_at,
        'telegram_public_username', telegram_public_username,
        'telegram_text', telegram_text,
        'asset', jsonb_build_object(
            'asset_id', asset.id,
            'storage_bucket', asset.storage_bucket,
            'storage_path', asset.storage_path,
            'mime_type', asset.mime_type,
            'byte_size', asset.byte_size,
            'sha256', asset.sha256,
            'width', asset.width,
            'height', asset.height
        )
    );
end;
$$;

create or replace function public.get_studio_telegram_publication(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    publication public.publications%rowtype;
    job public.jobs%rowtype;
begin
    if target_workspace_id is null
       or target_content_item_id is null
       or target_content_version_id is null then
        raise exception 'exact Telegram publication lookup is invalid'
            using errcode = '22023';
    end if;

    select delivery.* into publication
    from public.publications as delivery
    where delivery.workspace_id = target_workspace_id
      and delivery.content_item_id = target_content_item_id
      and delivery.content_version_id = target_content_version_id
      and delivery.channel = 'telegram'
      and delivery.request_payload ->> 'workflow'
            = 'exact_telegram_publication_v1';
    if not found then
        return null;
    end if;

    select queued_job.* into job
    from public.jobs as queued_job
    where queued_job.workspace_id = target_workspace_id
      and queued_job.content_item_id = target_content_item_id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow' = 'exact_telegram_publication_v1'
      and queued_job.input ->> 'publication_id' = publication.id::text
      and queued_job.input ->> 'content_version_id'
            = target_content_version_id::text
      and queued_job.input ->> 'channel' = 'telegram';
    if not found then
        raise exception 'exact Telegram publication queue is inconsistent'
            using errcode = '23514';
    end if;

    return jsonb_build_object(
        'publication_id', publication.id,
        'job_id', job.id,
        'content_item_id', publication.content_item_id,
        'content_version_id', publication.content_version_id,
        'client_id', publication.client_id,
        'channel', publication.channel,
        'status', publication.status,
        'delivery_started_at', publication.delivery_started_at,
        'published_at', publication.published_at,
        'external_id', publication.external_id,
        'external_url', publication.external_url,
        'error_code', publication.response_payload ->> 'error_code',
        'last_error_code', job.last_error_code,
        'reused', true
    );
end;
$$;

create or replace function public.mark_exact_telegram_attempt_started(
    target_job_id uuid,
    target_worker_id text,
    target_request_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    probe_workspace_id uuid;
    probe_content_item_id uuid;
    job public.jobs%rowtype;
    publication public.publications%rowtype;
    item public.content_items%rowtype;
    approval public.approvals%rowtype;
    attempt_id uuid := gen_random_uuid();
begin
    if target_job_id is null
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_request_sha256 is null
       or lower(target_request_sha256) !~ '^[a-f0-9]{64}$' then
        raise exception 'exact Telegram attempt marker is invalid'
            using errcode = '22023';
    end if;

    select queued_job.workspace_id, queued_job.content_item_id
    into probe_workspace_id, probe_content_item_id
    from public.jobs as queued_job
    where queued_job.id = target_job_id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow'
            = 'exact_telegram_publication_v1';
    if not found then
        raise exception 'exact Telegram publication job does not exist'
            using errcode = '23514';
    end if;

    select content.* into item
    from public.content_items as content
    where content.workspace_id = probe_workspace_id
      and content.id = probe_content_item_id
    for update;
    if not found then
        raise exception 'exact Telegram publication item does not exist'
            using errcode = '23514';
    end if;

    select queued_job.* into job
    from public.jobs as queued_job
    where queued_job.id = target_job_id
      and queued_job.workspace_id = item.workspace_id
      and queued_job.content_item_id = item.id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow'
            = 'exact_telegram_publication_v1'
    for update;
    if not found then
        raise exception 'exact Telegram publication job state changed'
            using errcode = '40001';
    end if;

    select delivery.* into publication
    from public.publications as delivery
    where delivery.id = (job.input ->> 'publication_id')::uuid
      and delivery.workspace_id = job.workspace_id
      and delivery.content_item_id = job.content_item_id
      and delivery.content_version_id::text
            = job.input ->> 'content_version_id'
      and delivery.channel = 'telegram'
      and delivery.request_payload ->> 'workflow'
            = 'exact_telegram_publication_v1'
      and delivery.request_payload ->> 'approval_id'
            is not distinct from job.input ->> 'approval_id'
      and delivery.request_payload ->> 'asset_id'
            is not distinct from job.input ->> 'asset_id'
      and delivery.request_payload -> 'asset_snapshot'
            is not distinct from job.input -> 'asset_snapshot'
    for update;
    if not found then
        raise exception 'exact Telegram publication queue is inconsistent'
            using errcode = '23514';
    end if;

    if publication.delivery_started_at is not null then
        if publication.delivery_request_sha256
                is distinct from lower(target_request_sha256)
           or publication.status not in (
               'publishing', 'published', 'delivery_unknown'
           ) then
            raise exception 'exact Telegram attempt retry does not match'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'job_id', job.id,
            'publication_id', publication.id,
            'delivery_attempt_id', publication.delivery_attempt_id,
            'delivery_started_at', publication.delivery_started_at,
            'request_sha256', publication.delivery_request_sha256,
            'status', publication.status,
            'attempt_started', true,
            'reused', true
        );
    end if;

    if job.status <> 'running'
       or job.locked_by is distinct from target_worker_id
       or job.lease_expires_at is null
       or job.lease_expires_at <= statement_timestamp()
       or publication.status <> 'queued' then
        raise exception 'exact Telegram publication lease is not owned by this worker'
            using errcode = '55000';
    end if;

    -- This is the final database fence before the non-idempotent provider
    -- call.  A review or version change after claim must prevent the send.
    if item.client_id <> publication.client_id
       or item.client_id <> 'squid'
       or item.content_kind <> 'daily_news'
       or item.status <> 'approved'
       or item.current_version_id is distinct from publication.content_version_id
       or publication.request_payload ->> 'approval_id'
            is distinct from job.input ->> 'approval_id'
       or publication.request_payload ->> 'asset_id'
            is distinct from job.input ->> 'asset_id'
       or not exists (
           select 1
           from public.workspace_clients as active_client
           where active_client.workspace_id = item.workspace_id
             and active_client.client_id = item.client_id
             and active_client.active is true
       ) then
        raise exception 'exact Telegram attempt no longer targets current approval'
            using errcode = '23514';
    end if;

    select review.* into approval
    from public.approvals as review
    where review.id = (job.input ->> 'approval_id')::uuid
      and review.workspace_id = job.workspace_id
      and review.content_item_id = job.content_item_id
      and review.content_version_id = publication.content_version_id
      and review.decision = 'approved';
    if not found then
        raise exception 'exact Telegram attempt approval is no longer valid'
            using errcode = '23514';
    end if;

    update public.publications
    set status = 'publishing',
        delivery_attempt_id = attempt_id,
        delivery_started_at = statement_timestamp(),
        delivery_request_sha256 = lower(target_request_sha256),
        last_error = null
    where id = publication.id
    returning * into publication;

    update public.jobs
    set output = job.output || jsonb_build_object(
            'delivery_attempt_id', publication.delivery_attempt_id,
            'delivery_started_at', publication.delivery_started_at,
            'request_sha256', publication.delivery_request_sha256
        )
    where id = job.id;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        job.workspace_id,
        'publication',
        publication.id,
        'exact_telegram_attempt_started',
        jsonb_build_object(
            'job_id', job.id,
            'delivery_attempt_id', publication.delivery_attempt_id,
            'request_sha256', publication.delivery_request_sha256
        )
    );

    return jsonb_build_object(
        'job_id', job.id,
        'publication_id', publication.id,
        'delivery_attempt_id', publication.delivery_attempt_id,
        'delivery_started_at', publication.delivery_started_at,
        'request_sha256', publication.delivery_request_sha256,
        'status', publication.status,
        'attempt_started', true,
        'reused', false
    );
end;
$$;

create or replace function public.complete_exact_telegram_publication_job(
    target_job_id uuid,
    target_worker_id text,
    target_request_sha256 text,
    target_message_id bigint,
    target_chat_username text,
    target_provider_date timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    probe_workspace_id uuid;
    probe_content_item_id uuid;
    item public.content_items%rowtype;
    job public.jobs%rowtype;
    publication public.publications%rowtype;
    expected_chat_username text;
    normalized_chat_username text;
    canonical_url text;
begin
    normalized_chat_username := lower(btrim(coalesce(target_chat_username, '')));
    if target_job_id is null
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_request_sha256 is null
       or lower(target_request_sha256) !~ '^[a-f0-9]{64}$'
       or target_message_id is null
       or target_message_id <= 0
       or normalized_chat_username !~ '^[a-z][a-z0-9_]{4,31}$'
       or target_provider_date is null
       or target_provider_date > statement_timestamp() + interval '5 minutes' then
        raise exception 'exact Telegram completion request is invalid'
            using errcode = '22023';
    end if;

    select queued_job.workspace_id, queued_job.content_item_id
    into probe_workspace_id, probe_content_item_id
    from public.jobs as queued_job
    where queued_job.id = target_job_id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow'
            = 'exact_telegram_publication_v1';
    if not found then
        raise exception 'exact Telegram publication job does not exist'
            using errcode = '23514';
    end if;

    select content.* into item
    from public.content_items as content
    where content.workspace_id = probe_workspace_id
      and content.id = probe_content_item_id
    for update;
    if not found then
        raise exception 'exact Telegram publication item does not exist'
            using errcode = '23514';
    end if;

    select queued_job.* into job
    from public.jobs as queued_job
    where queued_job.id = target_job_id
      and queued_job.workspace_id = item.workspace_id
      and queued_job.content_item_id = item.id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow'
            = 'exact_telegram_publication_v1'
    for update;
    if not found then
        raise exception 'exact Telegram publication job state changed'
            using errcode = '40001';
    end if;

    select delivery.* into publication
    from public.publications as delivery
    where delivery.id = (job.input ->> 'publication_id')::uuid
      and delivery.workspace_id = job.workspace_id
      and delivery.content_item_id = job.content_item_id
      and delivery.content_version_id::text
            = job.input ->> 'content_version_id'
      and delivery.channel = 'telegram'
      and delivery.request_payload ->> 'workflow'
            = 'exact_telegram_publication_v1'
      and delivery.request_payload ->> 'approval_id'
            is not distinct from job.input ->> 'approval_id'
      and delivery.request_payload ->> 'asset_id'
            is not distinct from job.input ->> 'asset_id'
      and delivery.request_payload -> 'asset_snapshot'
            is not distinct from job.input -> 'asset_snapshot'
    for update;
    if not found then
        raise exception 'exact Telegram publication queue is inconsistent'
            using errcode = '23514';
    end if;

    expected_chat_username := case publication.client_id
        when 'yellow' then 'yellowkorea_ann'
        when 'origintrail' then 'origintrailkr'
        when 'squid' then 'squid_kor_update'
        when 'babylon' then 'babylonbtc'
        else null
    end;
    if normalized_chat_username is distinct from expected_chat_username then
        raise exception 'Telegram completion channel is not canonical for the client'
            using errcode = '23514';
    end if;
    canonical_url := 'https://t.me/' || normalized_chat_username || '/'
        || target_message_id::text;

    if job.status = 'succeeded' then
        if publication.status <> 'published'
           or publication.delivery_request_sha256
                is distinct from lower(target_request_sha256)
           or publication.external_id is distinct from target_message_id::text
           or publication.external_url is distinct from canonical_url
           or publication.published_at is distinct from target_provider_date
           or job.output ->> 'completed_by' is distinct from target_worker_id then
            raise exception 'exact Telegram completion retry does not match'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'job_id', job.id,
            'publication_id', publication.id,
            'content_item_id', publication.content_item_id,
            'content_version_id', publication.content_version_id,
            'status', publication.status,
            'external_id', publication.external_id,
            'external_url', publication.external_url,
            'published_at', publication.published_at,
            'reused', true
        );
    end if;

    if job.status <> 'running'
       or job.locked_by is distinct from target_worker_id
       or job.lease_expires_at is null
       or job.lease_expires_at <= statement_timestamp()
       or publication.status <> 'publishing'
       or publication.delivery_started_at is null
       or publication.delivery_request_sha256
            is distinct from lower(target_request_sha256)
       or target_provider_date < publication.delivery_started_at - interval '10 minutes' then
        raise exception 'exact Telegram publication lease or attempt is invalid'
            using errcode = '55000';
    end if;

    update public.publications
    set status = 'published',
        published_at = target_provider_date,
        external_id = target_message_id::text,
        external_url = canonical_url,
        response_payload = jsonb_build_object(
            'message_id', target_message_id,
            'chat_username', normalized_chat_username,
            'provider_date', target_provider_date,
            'request_sha256', lower(target_request_sha256)
        ),
        last_error = null
    where id = publication.id
    returning * into publication;

    update public.jobs
    set status = 'succeeded',
        output = job.output || jsonb_build_object(
            'completed_by', target_worker_id,
            'publication_id', publication.id,
            'content_version_id', publication.content_version_id,
            'request_sha256', lower(target_request_sha256),
            'message_id', target_message_id,
            'external_url', canonical_url,
            'published_at', target_provider_date
        ),
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        finished_at = statement_timestamp(),
        last_error_code = null,
        last_error_message = null
    where id = job.id;

    update public.content_items
    set status = 'published', scheduled_for = null
    where id = publication.content_item_id
      and workspace_id = publication.workspace_id
      and current_version_id = publication.content_version_id;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        publication.workspace_id,
        'publication',
        publication.id,
        'exact_telegram_publication_completed',
        jsonb_build_object(
            'job_id', job.id,
            'content_item_id', publication.content_item_id,
            'content_version_id', publication.content_version_id,
            'message_id', target_message_id,
            'external_url', canonical_url
        )
    );

    return jsonb_build_object(
        'job_id', job.id,
        'publication_id', publication.id,
        'content_item_id', publication.content_item_id,
        'content_version_id', publication.content_version_id,
        'status', publication.status,
        'external_id', publication.external_id,
        'external_url', publication.external_url,
        'published_at', publication.published_at,
        'reused', false
    );
end;
$$;

create or replace function public.fail_exact_telegram_publication_job(
    target_job_id uuid,
    target_worker_id text,
    target_error_code text,
    target_retryable_before_attempt boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    probe_workspace_id uuid;
    probe_content_item_id uuid;
    item public.content_items%rowtype;
    job public.jobs%rowtype;
    publication public.publications%rowtype;
    next_job_status text;
    next_publication_status text;
    retry_at timestamptz;
    failure jsonb;
begin
    if target_job_id is null
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_error_code is null
       or not (target_error_code = any (array[
           'publication_client_not_allowed',
           'telegram_publication_config_invalid',
           'telegram_publication_channel_inactive',
           'telegram_publication_credentials_invalid',
           'telegram_publication_target_invalid',
           'telegram_publication_target_mismatch',
           'telegram_preflight_unavailable',
           'telegram_preflight_rejected',
           'telegram_response_invalid',
           'publication_asset_unavailable',
           'publication_asset_invalid',
           'telegram_publication_preflight_failed',
           'telegram_publication_request_invalid',
           'telegram_delivery_unknown'
       ]::text[]))
       or target_retryable_before_attempt is null then
        raise exception 'exact Telegram failure request is invalid'
            using errcode = '22023';
    end if;

    select queued_job.workspace_id, queued_job.content_item_id
    into probe_workspace_id, probe_content_item_id
    from public.jobs as queued_job
    where queued_job.id = target_job_id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow'
            = 'exact_telegram_publication_v1';
    if not found then
        raise exception 'exact Telegram publication job does not exist'
            using errcode = '23514';
    end if;

    select content.* into item
    from public.content_items as content
    where content.workspace_id = probe_workspace_id
      and content.id = probe_content_item_id
    for update;
    if not found then
        raise exception 'exact Telegram publication item does not exist'
            using errcode = '23514';
    end if;

    select queued_job.* into job
    from public.jobs as queued_job
    where queued_job.id = target_job_id
      and queued_job.workspace_id = item.workspace_id
      and queued_job.content_item_id = item.id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow'
            = 'exact_telegram_publication_v1'
    for update;
    if not found then
        raise exception 'exact Telegram publication job state changed'
            using errcode = '40001';
    end if;

    select delivery.* into publication
    from public.publications as delivery
    where delivery.id = (job.input ->> 'publication_id')::uuid
      and delivery.workspace_id = job.workspace_id
      and delivery.content_item_id = job.content_item_id
      and delivery.content_version_id::text
            = job.input ->> 'content_version_id'
      and delivery.channel = 'telegram'
      and delivery.request_payload ->> 'workflow'
            = 'exact_telegram_publication_v1'
      and delivery.request_payload ->> 'approval_id'
            is not distinct from job.input ->> 'approval_id'
      and delivery.request_payload ->> 'asset_id'
            is not distinct from job.input ->> 'asset_id'
      and delivery.request_payload -> 'asset_snapshot'
            is not distinct from job.input -> 'asset_snapshot'
    for update;
    if not found then
        raise exception 'exact Telegram publication queue is inconsistent'
            using errcode = '23514';
    end if;

    if job.status in ('retrying', 'failed')
       and job.output -> 'last_failure' ->> 'worker_id' = target_worker_id
       and job.output -> 'last_failure' ->> 'error_code' = target_error_code
       and (job.output -> 'last_failure' ->> 'retryable_before_attempt')::boolean
            = target_retryable_before_attempt then
        return jsonb_build_object(
            'job_id', job.id,
            'publication_id', publication.id,
            'status', publication.status,
            'job_status', job.status,
            'available_at', job.available_at,
            'reused', true
        );
    end if;

    if job.status <> 'running'
       or job.locked_by is distinct from target_worker_id
       or job.lease_expires_at is null
       or job.lease_expires_at <= statement_timestamp() then
        raise exception 'exact Telegram publication lease is not owned by this worker'
            using errcode = '55000';
    end if;

    if (publication.delivery_started_at is not null
        and (target_error_code <> 'telegram_delivery_unknown'
             or target_retryable_before_attempt))
       or (publication.delivery_started_at is null
           and target_error_code = 'telegram_delivery_unknown') then
        raise exception 'exact Telegram failure does not match attempt state'
            using errcode = '23514';
    end if;

    if publication.delivery_started_at is not null then
        next_job_status := 'failed';
        next_publication_status := 'delivery_unknown';
        retry_at := statement_timestamp();
    elsif target_retryable_before_attempt and job.attempts < job.max_attempts then
        next_job_status := 'retrying';
        next_publication_status := 'queued';
        retry_at := statement_timestamp() + case
            when job.attempts <= 1 then interval '1 minute'
            else interval '5 minutes'
        end;
    else
        next_job_status := 'failed';
        next_publication_status := 'failed';
        retry_at := statement_timestamp();
    end if;

    failure := jsonb_build_object(
        'worker_id', target_worker_id,
        'error_code', target_error_code,
        'retryable_before_attempt', target_retryable_before_attempt,
        'attempt_started', publication.delivery_started_at is not null,
        'failed_at', statement_timestamp(),
        'retry_at', case when next_job_status = 'retrying'
                         then to_jsonb(retry_at) else 'null'::jsonb end
    );

    update public.jobs
    set status = next_job_status,
        output = job.output || jsonb_build_object('last_failure', failure),
        available_at = retry_at,
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        last_error_code = case
            when next_publication_status = 'delivery_unknown'
                then 'delivery_outcome_unknown'
            else target_error_code
        end,
        last_error_message =
            'The exact Telegram worker reported an allowlisted failure.',
        finished_at = case when next_job_status = 'failed'
                           then statement_timestamp() else null end
    where id = job.id;

    update public.publications
    set status = next_publication_status,
        response_payload = jsonb_build_object(
            'error_code', case
                when next_publication_status = 'delivery_unknown'
                    then 'telegram_delivery_unknown'
                else target_error_code
            end
        ),
        last_error = case
            when next_publication_status = 'delivery_unknown'
                then 'delivery_outcome_unknown: ' || target_error_code
            else target_error_code
        end
    where id = publication.id
    returning * into publication;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        job.workspace_id,
        'publication',
        publication.id,
        case when publication.status = 'delivery_unknown'
             then 'exact_telegram_delivery_unknown'
             else 'exact_telegram_publication_failed' end,
        jsonb_build_object(
            'job_id', job.id,
            'error_code', target_error_code,
            'job_status', next_job_status,
            'publication_status', publication.status
        )
    );

    return jsonb_build_object(
        'job_id', job.id,
        'publication_id', publication.id,
        'status', publication.status,
        'job_status', next_job_status,
        'available_at', retry_at,
        'reused', false
    );
end;
$$;

revoke all on function private.exact_telegram_asset_snapshot(
    public.assets, jsonb
) from public, anon, authenticated, service_role;
revoke all on function private.enforce_exact_telegram_publication_exclusivity()
    from public, anon, authenticated, service_role;
revoke all on function public.request_studio_telegram_publication(
    uuid, uuid, uuid, text
) from public, anon, authenticated, service_role;
revoke all on function public.get_studio_telegram_publication(
    uuid, uuid, uuid
) from public, anon, authenticated, service_role;
revoke all on function public.reconcile_expired_exact_telegram_publication_leases(
    uuid, integer
) from public, anon, authenticated, service_role;
revoke all on function public.claim_exact_telegram_publication_job(
    uuid, text, integer
) from public, anon, authenticated, service_role;
revoke all on function public.mark_exact_telegram_attempt_started(
    uuid, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.complete_exact_telegram_publication_job(
    uuid, text, text, bigint, text, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function public.fail_exact_telegram_publication_job(
    uuid, text, text, boolean
) from public, anon, authenticated, service_role;

grant execute on function public.request_studio_telegram_publication(
    uuid, uuid, uuid, text
) to service_role;
grant execute on function public.get_studio_telegram_publication(
    uuid, uuid, uuid
) to service_role;
grant execute on function public.reconcile_expired_exact_telegram_publication_leases(
    uuid, integer
) to service_role;
grant execute on function public.claim_exact_telegram_publication_job(
    uuid, text, integer
) to service_role;
grant execute on function public.mark_exact_telegram_attempt_started(
    uuid, text, text
) to service_role;
grant execute on function public.complete_exact_telegram_publication_job(
    uuid, text, text, bigint, text, timestamptz
) to service_role;
grant execute on function public.fail_exact_telegram_publication_job(
    uuid, text, text, boolean
) to service_role;

commit;
