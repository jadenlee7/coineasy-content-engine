-- Durable, duplicate-resistant OriginTrail -> Buzz delivery receipts.
--
-- The table is private and FORCE RLS.  The Buzz worker never receives a
-- Supabase key; Netlify may use only the narrow service-role RPCs below.
-- A relay call is authorized only by a fresh attempt marker.  Any state after
-- that marker is terminal for automation unless a human reconciles it.

begin;

create table agent_runtime.buzz_delivery_receipts (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    event_id text not null check (event_id ~ '^[a-f0-9]{64}$'),
    event_type text not null check (
        event_type = 'origintrail.batch_review_ready.v1'
    ),
    job_id uuid not null,
    client_id text not null check (client_id = 'origintrail'),
    agent_id text not null check (agent_id = 'origintrail_client_agent'),
    workflow_kind text not null check (
        workflow_kind = 'official_source_nonurgent_pack'
    ),
    channel_id uuid not null,
    message_sha256 text not null check (message_sha256 ~ '^[a-f0-9]{64}$'),
    request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
    status text not null check (
        status in (
            'pending', 'claimed', 'attempt_started', 'delivered',
            'delivery_unknown', 'failed'
        )
    ),
    attempts integer not null default 0 check (attempts between 0 and 3),
    max_attempts integer not null default 3 check (max_attempts = 3),
    available_at timestamptz not null default now(),
    locked_by text check (
        locked_by is null
        or locked_by ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
    ),
    locked_at timestamptz,
    lease_expires_at timestamptz,
    delivery_attempt_id uuid,
    attempt_worker_id text check (
        attempt_worker_id is null
        or attempt_worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
    ),
    delivery_started_at timestamptz,
    relay_event_id text check (
        relay_event_id is null or relay_event_id ~ '^[a-f0-9]{64}$'
    ),
    delivered_at timestamptz,
    error_code text check (
        error_code is null or error_code ~ '^[a-z][a-z0-9_]{0,79}$'
    ),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (workspace_id, event_id),
    unique (workspace_id, job_id),
    foreign key (workspace_id, job_id)
        references agent_runtime.batch_jobs(workspace_id, job_id)
        on delete restrict,
    check (
        (status in ('claimed', 'attempt_started')) = (
            locked_by is not null
            and locked_at is not null
            and lease_expires_at is not null
        )
    ),
    check (
        status not in ('claimed', 'attempt_started')
        or lease_expires_at > locked_at
    ),
    check (
        (delivery_started_at is null) = (delivery_attempt_id is null)
        and (delivery_started_at is null) = (attempt_worker_id is null)
    ),
    check (
        status not in ('attempt_started', 'delivered', 'delivery_unknown')
        or delivery_started_at is not null
    ),
    check (
        status not in ('pending', 'claimed', 'failed')
        or delivery_started_at is null
    ),
    check (
        (status = 'delivered') = (
            relay_event_id is not null and delivered_at is not null
        )
    ),
    check (
        status = 'delivered' or (relay_event_id is null and delivered_at is null)
    ),
    check (
        status not in ('delivery_unknown', 'failed') or error_code is not null
    )
);

create unique index buzz_delivery_relay_event_unique_idx
    on agent_runtime.buzz_delivery_receipts (relay_event_id)
    where relay_event_id is not null;
create index buzz_delivery_reconcile_idx
    on agent_runtime.buzz_delivery_receipts (
        workspace_id, status, lease_expires_at, available_at
    )
    where status in ('pending', 'claimed', 'attempt_started');

alter table agent_runtime.buzz_delivery_receipts enable row level security;
alter table agent_runtime.buzz_delivery_receipts force row level security;
revoke all on table agent_runtime.buzz_delivery_receipts
from public, anon, authenticated, service_role;

create or replace function private.origintrail_buzz_event_id(
    target_workspace_id uuid,
    target_job_id uuid
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select encode(extensions.digest(
        convert_to('coineasy-buzz-shadow', 'UTF8') || decode('00', 'hex')
        || convert_to('1.0', 'UTF8') || decode('00', 'hex')
        || convert_to(target_workspace_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to('origintrail', 'UTF8') || decode('00', 'hex')
        || convert_to(target_job_id::text, 'UTF8'),
        'sha256'
    ), 'hex')
$$;

revoke all on function private.origintrail_buzz_event_id(uuid, uuid)
from public, anon, authenticated, service_role;

create or replace function public.claim_origintrail_buzz_delivery(
    target_workspace_id uuid,
    target_job_id uuid,
    target_event_id text,
    target_channel_id uuid,
    target_message_sha256 text,
    target_request_sha256 text,
    target_worker_id text,
    target_lease_seconds integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    job agent_runtime.batch_jobs%rowtype;
    receipt agent_runtime.buzz_delivery_receipts%rowtype;
    expected_event_id text;
begin
    if target_workspace_id is null
       or target_job_id is null
       or target_event_id is null
       or lower(target_event_id) !~ '^[a-f0-9]{64}$'
       or target_channel_id is null
       or target_message_sha256 is null
       or lower(target_message_sha256) !~ '^[a-f0-9]{64}$'
       or target_request_sha256 is null
       or lower(target_request_sha256) !~ '^[a-f0-9]{64}$'
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_lease_seconds not between 180 and 600 then
        raise exception 'OriginTrail Buzz claim is invalid'
            using errcode = '22023';
    end if;

    expected_event_id := private.origintrail_buzz_event_id(
        target_workspace_id, target_job_id
    );
    if lower(target_event_id) <> expected_event_id then
        raise exception 'OriginTrail Buzz event identity does not match'
            using errcode = '23514';
    end if;

    select batch_job.* into job
    from agent_runtime.batch_jobs as batch_job
    join public.jobs as review_job
      on review_job.id = batch_job.job_id
     and review_job.workspace_id = batch_job.workspace_id
     and review_job.client_id = batch_job.client_id
    where batch_job.workspace_id = target_workspace_id
      and batch_job.job_id = target_job_id
      and batch_job.client_id = 'origintrail'
      and batch_job.agent_id = 'origintrail_client_agent'
      and batch_job.workflow_kind = 'official_source_nonurgent_pack'
      and batch_job.stage = 'generate'
      and batch_job.status = 'completed'
      and batch_job.reservation_state = 'settled'
      and batch_job.result_code = 'needs_review'
      and batch_job.input_payload -> 'approval_required' = 'true'::jsonb
      and batch_job.result_payload ?& array[
          'headline_ko', 'body_ko', 'x_copy_ko', 'telegram_copy_ko'
      ]::text[]
      and (
          select count(*)
          from jsonb_object_keys(batch_job.result_payload)
      ) = 4
      and jsonb_typeof(batch_job.result_payload -> 'headline_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'body_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'x_copy_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'telegram_copy_ko') = 'string'
      and char_length(batch_job.result_payload ->> 'headline_ko') between 1 and 120
      and char_length(batch_job.result_payload ->> 'body_ko') between 1 and 1800
      and char_length(batch_job.result_payload ->> 'x_copy_ko') between 1 and 500
      and char_length(batch_job.result_payload ->> 'telegram_copy_ko') between 1 and 1800
      and (batch_job.result_payload ->> 'headline_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'body_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'x_copy_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'telegram_copy_ko') ~ '[^[:space:]]'
      and review_job.job_kind = 'generate'
      and review_job.status = 'succeeded'
      and review_job.content_item_id is null
      and review_job.input ->> 'workflow' = 'official_x_review_draft_v1'
      and review_job.input ->> 'content_kind' = 'daily_news'
      and review_job.input -> 'manual_only' = 'false'::jsonb
      and review_job.input ->> 'source_url'
            ~ '^https://x[.]com/origin_trail/status/[0-9]{1,19}$'
      and review_job.output = jsonb_build_object(
          'workflow', 'agent_batch_review_handoff_v1',
          'handoff', 'openai_batch',
          'batch_job_id', batch_job.job_id,
          'input_sha256', batch_job.input_sha256,
          'review_state', 'pending'
      );
    if not found then
        raise exception 'OriginTrail Buzz job is not delivery eligible'
            using errcode = '23514';
    end if;

    perform pg_advisory_xact_lock(hashtextextended(
        target_workspace_id::text || ':' || lower(target_event_id), 0
    ));
    select delivery.* into receipt
    from agent_runtime.buzz_delivery_receipts as delivery
    where delivery.workspace_id = target_workspace_id
      and delivery.event_id = lower(target_event_id)
    for update;

    if not found then
        insert into agent_runtime.buzz_delivery_receipts (
            workspace_id, event_id, event_type, job_id, client_id, agent_id,
            workflow_kind, channel_id, message_sha256, request_sha256,
            status, attempts, locked_by, locked_at, lease_expires_at
        ) values (
            target_workspace_id, lower(target_event_id),
            'origintrail.batch_review_ready.v1', target_job_id, 'origintrail',
            'origintrail_client_agent', 'official_source_nonurgent_pack',
            target_channel_id, lower(target_message_sha256),
            lower(target_request_sha256), 'claimed', 1, target_worker_id,
            statement_timestamp(),
            statement_timestamp() + make_interval(secs => target_lease_seconds)
        ) returning * into receipt;
        return jsonb_build_object(
            'event_id', receipt.event_id,
            'job_id', receipt.job_id,
            'channel_id', receipt.channel_id,
            'message_sha256', receipt.message_sha256,
            'request_sha256', receipt.request_sha256,
            'status', receipt.status,
            'lease_expires_at', receipt.lease_expires_at,
            'claim_granted', true,
            'reused', false
        );
    end if;

    if receipt.job_id is distinct from target_job_id
       or receipt.channel_id is distinct from target_channel_id
       or receipt.message_sha256 is distinct from lower(target_message_sha256)
       or receipt.request_sha256 is distinct from lower(target_request_sha256)
       or receipt.event_type <> 'origintrail.batch_review_ready.v1' then
        raise exception 'OriginTrail Buzz claim conflicts with its receipt'
            using errcode = '23505';
    end if;

    if receipt.status = 'claimed'
       and receipt.lease_expires_at > statement_timestamp() then
        return jsonb_build_object(
            'event_id', receipt.event_id,
            'job_id', receipt.job_id,
            'channel_id', receipt.channel_id,
            'message_sha256', receipt.message_sha256,
            'request_sha256', receipt.request_sha256,
            'status', receipt.status,
            'lease_expires_at', receipt.lease_expires_at,
            'claim_granted', receipt.locked_by = target_worker_id,
            'reused', true
        );
    end if;

    if receipt.status = 'claimed'
       and receipt.lease_expires_at <= statement_timestamp() then
        update agent_runtime.buzz_delivery_receipts
        set status = case when attempts < max_attempts then 'pending' else 'failed' end,
            available_at = statement_timestamp() + interval '1 minute',
            locked_by = null,
            locked_at = null,
            lease_expires_at = null,
            error_code = case when attempts < max_attempts
                then null else 'buzz_claim_lease_expired' end,
            updated_at = statement_timestamp()
        where workspace_id = receipt.workspace_id
          and event_id = receipt.event_id
        returning * into receipt;
    end if;

    if receipt.status <> 'pending'
       or receipt.available_at > statement_timestamp() then
        return jsonb_build_object(
            'event_id', receipt.event_id,
            'job_id', receipt.job_id,
            'channel_id', receipt.channel_id,
            'message_sha256', receipt.message_sha256,
            'request_sha256', receipt.request_sha256,
            'status', receipt.status,
            'lease_expires_at', receipt.lease_expires_at,
            'claim_granted', false,
            'reused', true
        );
    end if;

    update agent_runtime.buzz_delivery_receipts
    set status = 'claimed',
        attempts = attempts + 1,
        locked_by = target_worker_id,
        locked_at = statement_timestamp(),
        lease_expires_at = statement_timestamp()
            + make_interval(secs => target_lease_seconds),
        error_code = null,
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and event_id = lower(target_event_id)
      and attempts < max_attempts
    returning * into receipt;
    if not found then
        raise exception 'OriginTrail Buzz claim exhausted its attempts'
            using errcode = '55000';
    end if;

    return jsonb_build_object(
        'event_id', receipt.event_id,
        'job_id', receipt.job_id,
        'channel_id', receipt.channel_id,
        'message_sha256', receipt.message_sha256,
        'request_sha256', receipt.request_sha256,
        'status', receipt.status,
        'lease_expires_at', receipt.lease_expires_at,
        'claim_granted', true,
        'reused', false
    );
end;
$$;

create or replace function public.mark_origintrail_buzz_delivery_attempt(
    target_workspace_id uuid,
    target_event_id text,
    target_worker_id text,
    target_request_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    receipt agent_runtime.buzz_delivery_receipts%rowtype;
begin
    if target_workspace_id is null
       or lower(coalesce(target_event_id, '')) !~ '^[a-f0-9]{64}$'
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or lower(coalesce(target_request_sha256, '')) !~ '^[a-f0-9]{64}$' then
        raise exception 'OriginTrail Buzz attempt marker is invalid'
            using errcode = '22023';
    end if;

    select delivery.* into receipt
    from agent_runtime.buzz_delivery_receipts as delivery
    where delivery.workspace_id = target_workspace_id
      and delivery.event_id = lower(target_event_id)
    for update;
    if not found then
        raise exception 'OriginTrail Buzz receipt does not exist'
            using errcode = 'P0002';
    end if;

    if receipt.delivery_started_at is not null then
        if receipt.request_sha256 is distinct from lower(target_request_sha256)
           or receipt.attempt_worker_id is distinct from target_worker_id
           or receipt.status not in (
               'attempt_started', 'delivered', 'delivery_unknown'
           ) then
            raise exception 'OriginTrail Buzz attempt retry does not match'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'event_id', receipt.event_id,
            'job_id', receipt.job_id,
            'channel_id', receipt.channel_id,
            'request_sha256', receipt.request_sha256,
            'status', receipt.status,
            'authorized_once', false,
            'reused', true
        );
    end if;

    if receipt.status <> 'claimed'
       or receipt.locked_by is distinct from target_worker_id
       or receipt.lease_expires_at <= statement_timestamp()
       or receipt.request_sha256 is distinct from lower(target_request_sha256) then
        raise exception 'OriginTrail Buzz attempt lease is invalid'
            using errcode = '55000';
    end if;

    update agent_runtime.buzz_delivery_receipts
    set status = 'attempt_started',
        delivery_attempt_id = gen_random_uuid(),
        attempt_worker_id = target_worker_id,
        delivery_started_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and event_id = lower(target_event_id)
    returning * into receipt;

    return jsonb_build_object(
        'event_id', receipt.event_id,
        'job_id', receipt.job_id,
        'channel_id', receipt.channel_id,
        'request_sha256', receipt.request_sha256,
        'status', receipt.status,
        'authorized_once', true,
        'reused', false
    );
end;
$$;

create or replace function public.complete_origintrail_buzz_delivery(
    target_workspace_id uuid,
    target_event_id text,
    target_worker_id text,
    target_request_sha256 text,
    target_relay_event_id text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    receipt agent_runtime.buzz_delivery_receipts%rowtype;
begin
    if target_workspace_id is null
       or lower(coalesce(target_event_id, '')) !~ '^[a-f0-9]{64}$'
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or lower(coalesce(target_request_sha256, '')) !~ '^[a-f0-9]{64}$'
       or lower(coalesce(target_relay_event_id, '')) !~ '^[a-f0-9]{64}$' then
        raise exception 'OriginTrail Buzz completion is invalid'
            using errcode = '22023';
    end if;

    select delivery.* into receipt
    from agent_runtime.buzz_delivery_receipts as delivery
    where delivery.workspace_id = target_workspace_id
      and delivery.event_id = lower(target_event_id)
    for update;
    if not found then
        raise exception 'OriginTrail Buzz receipt does not exist'
            using errcode = 'P0002';
    end if;

    if receipt.status = 'delivered' then
        if receipt.request_sha256 is distinct from lower(target_request_sha256)
           or receipt.relay_event_id is distinct from lower(target_relay_event_id)
           or receipt.attempt_worker_id is distinct from target_worker_id then
            raise exception 'OriginTrail Buzz completion retry does not match'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'event_id', receipt.event_id,
            'job_id', receipt.job_id,
            'channel_id', receipt.channel_id,
            'request_sha256', receipt.request_sha256,
            'relay_event_id', receipt.relay_event_id,
            'status', receipt.status,
            'reused', true
        );
    end if;

    if receipt.status <> 'attempt_started'
       or receipt.locked_by is distinct from target_worker_id
       or receipt.attempt_worker_id is distinct from target_worker_id
       or receipt.lease_expires_at <= statement_timestamp()
       or receipt.request_sha256 is distinct from lower(target_request_sha256) then
        raise exception 'OriginTrail Buzz completion lease is invalid'
            using errcode = '55000';
    end if;

    update agent_runtime.buzz_delivery_receipts
    set status = 'delivered',
        relay_event_id = lower(target_relay_event_id),
        delivered_at = statement_timestamp(),
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        error_code = null,
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and event_id = lower(target_event_id)
    returning * into receipt;

    return jsonb_build_object(
        'event_id', receipt.event_id,
        'job_id', receipt.job_id,
        'channel_id', receipt.channel_id,
        'request_sha256', receipt.request_sha256,
        'relay_event_id', receipt.relay_event_id,
        'status', receipt.status,
        'reused', false
    );
end;
$$;

create or replace function public.fail_origintrail_buzz_delivery(
    target_workspace_id uuid,
    target_event_id text,
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
    receipt agent_runtime.buzz_delivery_receipts%rowtype;
    next_status text;
begin
    if target_workspace_id is null
       or lower(coalesce(target_event_id, '')) !~ '^[a-f0-9]{64}$'
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_error_code is null
       or not (target_error_code = any (array[
           'buzz_cli_config_invalid',
           'buzz_cli_preflight_failed',
           'buzz_delivery_request_invalid',
           'buzz_delivery_unknown'
       ]::text[]))
       or target_retryable_before_attempt is null then
        raise exception 'OriginTrail Buzz failure is invalid'
            using errcode = '22023';
    end if;

    select delivery.* into receipt
    from agent_runtime.buzz_delivery_receipts as delivery
    where delivery.workspace_id = target_workspace_id
      and delivery.event_id = lower(target_event_id)
    for update;
    if not found then
        raise exception 'OriginTrail Buzz receipt does not exist'
            using errcode = 'P0002';
    end if;

    if receipt.status in ('pending', 'failed', 'delivery_unknown')
       and receipt.error_code is not distinct from target_error_code then
        return jsonb_build_object(
            'event_id', receipt.event_id,
            'job_id', receipt.job_id,
            'status', receipt.status,
            'reused', true
        );
    end if;

    if receipt.locked_by is distinct from target_worker_id
       or receipt.lease_expires_at <= statement_timestamp() then
        raise exception 'OriginTrail Buzz failure lease is invalid'
            using errcode = '55000';
    end if;

    if receipt.delivery_started_at is not null then
        if target_error_code <> 'buzz_delivery_unknown'
           or target_retryable_before_attempt
           or receipt.status <> 'attempt_started'
           or receipt.attempt_worker_id is distinct from target_worker_id then
            raise exception 'OriginTrail Buzz failure does not match attempt state'
                using errcode = '23514';
        end if;
        next_status := 'delivery_unknown';
    elsif receipt.status = 'claimed'
          and target_retryable_before_attempt
          and receipt.attempts < receipt.max_attempts then
        next_status := 'pending';
    elsif receipt.status = 'claimed' then
        next_status := 'failed';
    else
        raise exception 'OriginTrail Buzz failure state is invalid'
            using errcode = '23514';
    end if;

    update agent_runtime.buzz_delivery_receipts
    set status = next_status,
        available_at = case when next_status = 'pending'
            then statement_timestamp() + interval '1 minute'
            else available_at end,
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        error_code = target_error_code,
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and event_id = lower(target_event_id)
    returning * into receipt;

    return jsonb_build_object(
        'event_id', receipt.event_id,
        'job_id', receipt.job_id,
        'status', receipt.status,
        'reused', false
    );
end;
$$;

create or replace function public.reconcile_origintrail_buzz_delivery_leases(
    target_workspace_id uuid,
    target_limit integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    pending_count integer := 0;
    failed_count integer := 0;
    unknown_count integer := 0;
begin
    if target_workspace_id is null or target_limit not between 1 and 100 then
        raise exception 'OriginTrail Buzz reconciliation request is invalid'
            using errcode = '22023';
    end if;

    with expired as (
        select delivery.event_id
        from agent_runtime.buzz_delivery_receipts as delivery
        where delivery.workspace_id = target_workspace_id
          and delivery.status in ('claimed', 'attempt_started')
          and delivery.lease_expires_at <= statement_timestamp()
        order by delivery.lease_expires_at, delivery.event_id
        limit target_limit
        for update skip locked
    ), changed as (
        update agent_runtime.buzz_delivery_receipts as delivery
        set status = case
                when delivery.status = 'attempt_started' then 'delivery_unknown'
                when delivery.attempts < delivery.max_attempts then 'pending'
                else 'failed'
            end,
            available_at = case
                when delivery.status = 'claimed'
                 and delivery.attempts < delivery.max_attempts
                    then statement_timestamp() + interval '1 minute'
                else delivery.available_at
            end,
            locked_by = null,
            locked_at = null,
            lease_expires_at = null,
            error_code = case
                when delivery.status = 'attempt_started'
                    then 'buzz_delivery_unknown'
                when delivery.attempts >= delivery.max_attempts
                    then 'buzz_claim_lease_expired'
                else null
            end,
            updated_at = statement_timestamp()
        from expired
        where delivery.workspace_id = target_workspace_id
          and delivery.event_id = expired.event_id
        returning delivery.status
    )
    select
        count(*) filter (where status = 'pending'),
        count(*) filter (where status = 'failed'),
        count(*) filter (where status = 'delivery_unknown')
    into pending_count, failed_count, unknown_count
    from changed;

    return jsonb_build_object(
        'workspace_id', target_workspace_id,
        'reconciled_count', pending_count + failed_count + unknown_count,
        'pending_count', pending_count,
        'failed_count', failed_count,
        'delivery_unknown_count', unknown_count
    );
end;
$$;

revoke all on function public.claim_origintrail_buzz_delivery(
    uuid, uuid, text, uuid, text, text, text, integer
) from public, anon, authenticated;
revoke all on function public.mark_origintrail_buzz_delivery_attempt(
    uuid, text, text, text
) from public, anon, authenticated;
revoke all on function public.complete_origintrail_buzz_delivery(
    uuid, text, text, text, text
) from public, anon, authenticated;
revoke all on function public.fail_origintrail_buzz_delivery(
    uuid, text, text, text, boolean
) from public, anon, authenticated;
revoke all on function public.reconcile_origintrail_buzz_delivery_leases(
    uuid, integer
) from public, anon, authenticated;

grant execute on function public.claim_origintrail_buzz_delivery(
    uuid, uuid, text, uuid, text, text, text, integer
) to service_role;
grant execute on function public.mark_origintrail_buzz_delivery_attempt(
    uuid, text, text, text
) to service_role;
grant execute on function public.complete_origintrail_buzz_delivery(
    uuid, text, text, text, text
) to service_role;
grant execute on function public.fail_origintrail_buzz_delivery(
    uuid, text, text, text, boolean
) to service_role;
grant execute on function public.reconcile_origintrail_buzz_delivery_leases(
    uuid, integer
) to service_role;

commit;
