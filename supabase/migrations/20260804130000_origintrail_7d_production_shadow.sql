-- Seven-day OriginTrail Production Shadow admission fence.
--
-- The pilot approval authorizes at most one immutable OriginTrail Batch job
-- per KST day. Each admitted day is converted into the existing exact-job,
-- one-provider-Batch grant, preserving the durable provider-create fence.

begin;

create table agent_runtime.origintrail_batch_production_shadow_days (
    workspace_id uuid not null,
    kst_date date not null,
    pilot_subject_sha256 text not null check (
        pilot_subject_sha256 ~ '^[a-f0-9]{64}$'
    ),
    pilot_approval_id uuid not null,
    config_subject_sha256 text not null check (
        config_subject_sha256 ~ '^[a-f0-9]{64}$'
    ),
    config_approval_id uuid not null,
    dispatch_subject_sha256 text not null check (
        dispatch_subject_sha256 ~ '^[a-f0-9]{64}$'
    ),
    dispatch_approval_id uuid not null,
    job_id uuid not null,
    input_sha256 text not null check (
        input_sha256 ~ '^[a-f0-9]{64}$'
    ),
    request_sha256 text not null check (
        request_sha256 ~ '^[a-f0-9]{64}$'
    ),
    expires_at timestamptz not null,
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, kst_date),
    unique (workspace_id, config_subject_sha256),
    unique (workspace_id, config_approval_id),
    unique (workspace_id, dispatch_subject_sha256),
    unique (workspace_id, dispatch_approval_id),
    unique (workspace_id, job_id),
    foreign key (workspace_id, config_subject_sha256)
        references agent_runtime.origintrail_batch_canary_grants(
            workspace_id, config_subject_sha256
        ) on delete restrict,
    foreign key (workspace_id, job_id)
        references agent_runtime.batch_jobs(workspace_id, job_id)
        on delete restrict,
    check (expires_at > created_at)
);

alter table agent_runtime.origintrail_batch_production_shadow_days
    enable row level security;
alter table agent_runtime.origintrail_batch_production_shadow_days
    force row level security;

revoke all on table
    agent_runtime.origintrail_batch_production_shadow_days
from public, anon, authenticated, service_role;

create or replace function agent_runtime.reject_origintrail_shadow_day_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'OriginTrail Production Shadow day is immutable'
        using errcode = '23505';
end;
$$;

revoke all on function
    agent_runtime.reject_origintrail_shadow_day_mutation()
from public, anon, authenticated, service_role;

create trigger origintrail_batch_production_shadow_days_immutable
before update or delete
on agent_runtime.origintrail_batch_production_shadow_days
for each row execute function
    agent_runtime.reject_origintrail_shadow_day_mutation();

create or replace function public.peek_origintrail_batch_shadow_candidate(
    target_workspace_id uuid,
    target_pilot_subject_sha256 text,
    target_pilot_approval_id uuid,
    target_experiment_start_at timestamptz,
    target_experiment_end_at timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    pilot_day date;
    candidate agent_runtime.batch_jobs%rowtype;
begin
    pilot_day := (statement_timestamp() at time zone 'Asia/Seoul')::date;
    if target_workspace_id is null
       or target_pilot_subject_sha256 is null
       or target_pilot_subject_sha256 !~ '^[a-f0-9]{64}$'
       or target_pilot_approval_id is null
       or target_experiment_start_at is null
       or target_experiment_end_at is null
       or target_experiment_end_at - target_experiment_start_at
            <> interval '7 days'
       or target_experiment_start_at is distinct from (
            (target_experiment_start_at at time zone 'Asia/Seoul')::date
            ::timestamp at time zone 'Asia/Seoul'
       )
       or target_experiment_end_at is distinct from (
            (target_experiment_end_at at time zone 'Asia/Seoul')::date
            ::timestamp at time zone 'Asia/Seoul'
       )
       or statement_timestamp() < target_experiment_start_at
       or statement_timestamp() >= target_experiment_end_at
       or pilot_day < (
            target_experiment_start_at at time zone 'Asia/Seoul'
       )::date
       or pilot_day >= (
            target_experiment_end_at at time zone 'Asia/Seoul'
       )::date then
        raise exception 'OriginTrail Production Shadow window is invalid'
            using errcode = '22023';
    end if;

    if not exists (
        select 1
        from public.workspace_clients as client
        where client.workspace_id = target_workspace_id
          and client.client_id = 'origintrail'
          and client.active is true
    ) then
        raise exception 'OriginTrail Production Shadow workspace is inactive'
            using errcode = '22023';
    end if;

    if exists (
        select 1
        from agent_runtime.origintrail_batch_production_shadow_days as admitted
        where admitted.workspace_id = target_workspace_id
          and admitted.kst_date = pilot_day
    ) then
        return '[]'::jsonb;
    end if;

    select queued.* into candidate
    from agent_runtime.batch_jobs as queued
    where queued.workspace_id = target_workspace_id
      and queued.client_id = 'origintrail'
      and queued.agent_id = 'origintrail_client_agent'
      and queued.workflow_kind = 'official_source_nonurgent_pack'
      and queued.stage = 'generate'
      and queued.latency_class = 'batch_24h'
      and queued.model = 'gpt-5.6-luna'
      and queued.model_tier = 'S'
      and queued.status = 'queued'
      and queued.reservation_state = 'held'
      and queued.attempts = 0
      and queued.available_at <= statement_timestamp()
      and queued.deadline_at > statement_timestamp() + interval '26 hours'
      and queued.max_cost_microusd between 1 and 50000
      and queued.current_batch_id is null
      and queued.custom_id = queued.job_id::text || ':generate:1'
      and queued.budget_key = 'batch-general:' || pilot_day::text
      and queued.input_payload -> 'approval_required' = 'true'::jsonb
      and queued.input_payload -> 'interactive' = 'false'::jsonb
      and queued.input_payload -> 'incident_or_release_blocker' = 'false'::jsonb
      and queued.input_payload -> 'live_tools_required' = 'false'::jsonb
      and queued.input_payload -> 'source_snapshot_complete' = 'true'::jsonb
      and queued.input_payload -> 'input_immutable' = 'true'::jsonb
      and queued.input_payload -> 'retry_idempotent' = 'true'::jsonb
      and queued.input_payload ->> 'remaining_batch_stages' = '1'
      and not exists (
          select 1
          from agent_runtime.origintrail_batch_canary_grants as exact_grant
          where exact_grant.workspace_id = queued.workspace_id
            and exact_grant.job_id = queued.job_id
      )
    order by queued.priority desc, queued.deadline_at, queued.queued_at
    limit 1;

    if not found then
        return '[]'::jsonb;
    end if;
    return jsonb_build_array(jsonb_build_object(
        'kst_date', pilot_day,
        'job_id', candidate.job_id,
        'input_sha256', candidate.input_sha256,
        'request_sha256', candidate.input_payload ->> 'request_sha256'
    ));
end;
$$;

create or replace function public.configure_origintrail_batch_shadow_day(
    target_workspace_id uuid,
    target_kst_date date,
    target_pilot_subject_sha256 text,
    target_pilot_approval_id uuid,
    target_experiment_start_at timestamptz,
    target_experiment_end_at timestamptz,
    target_config_subject_sha256 text,
    target_config_approval_id uuid,
    target_dispatch_subject_sha256 text,
    target_dispatch_approval_id uuid,
    target_job_id uuid,
    target_input_sha256 text,
    target_request_sha256 text,
    target_expires_at timestamptz,
    target_hard_limit_microusd bigint,
    target_max_provider_batches integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    existing agent_runtime.origintrail_batch_production_shadow_days%rowtype;
    batch_job agent_runtime.batch_jobs%rowtype;
    exact_receipt jsonb;
begin
    if target_workspace_id is null
       or target_kst_date is null
       or target_kst_date is distinct from
            (statement_timestamp() at time zone 'Asia/Seoul')::date
       or target_pilot_subject_sha256 is null
       or target_pilot_subject_sha256 !~ '^[a-f0-9]{64}$'
       or target_pilot_approval_id is null
       or target_experiment_start_at is null
       or target_experiment_end_at is null
       or target_experiment_end_at - target_experiment_start_at
            <> interval '7 days'
       or target_experiment_start_at is distinct from (
            (target_experiment_start_at at time zone 'Asia/Seoul')::date
            ::timestamp at time zone 'Asia/Seoul'
       )
       or target_experiment_end_at is distinct from (
            (target_experiment_end_at at time zone 'Asia/Seoul')::date
            ::timestamp at time zone 'Asia/Seoul'
       )
       or statement_timestamp() < target_experiment_start_at
       or statement_timestamp() >= target_experiment_end_at
       or target_kst_date < (
            target_experiment_start_at at time zone 'Asia/Seoul'
       )::date
       or target_kst_date >= (
            target_experiment_end_at at time zone 'Asia/Seoul'
       )::date
       or target_config_subject_sha256 is null
       or target_config_subject_sha256 !~ '^[a-f0-9]{64}$'
       or target_config_approval_id is null
       or target_dispatch_subject_sha256 is null
       or target_dispatch_subject_sha256 !~ '^[a-f0-9]{64}$'
       or target_dispatch_approval_id is null
       or target_job_id is null
       or target_input_sha256 is null
       or target_input_sha256 !~ '^[a-f0-9]{64}$'
       or target_request_sha256 is null
       or target_request_sha256 !~ '^[a-f0-9]{64}$'
       or target_expires_at is null
       or target_expires_at <= statement_timestamp()
       or target_expires_at > statement_timestamp() + interval '2 hours'
       or target_expires_at > target_experiment_end_at
       or target_hard_limit_microusd is distinct from 50000
       or target_max_provider_batches is distinct from 1 then
        raise exception 'OriginTrail Production Shadow day is invalid'
            using errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'coineasy:origintrail-shadow:'
            || target_workspace_id::text || ':' || target_kst_date::text,
            0
        )
    );

    select admitted.* into existing
    from agent_runtime.origintrail_batch_production_shadow_days as admitted
    where admitted.workspace_id = target_workspace_id
      and admitted.kst_date = target_kst_date;
    if found then
        if existing.pilot_subject_sha256
                is distinct from target_pilot_subject_sha256
           or existing.pilot_approval_id
                is distinct from target_pilot_approval_id
           or existing.config_subject_sha256
                is distinct from target_config_subject_sha256
           or existing.config_approval_id
                is distinct from target_config_approval_id
           or existing.dispatch_subject_sha256
                is distinct from target_dispatch_subject_sha256
           or existing.dispatch_approval_id
                is distinct from target_dispatch_approval_id
           or existing.job_id is distinct from target_job_id
           or existing.input_sha256 is distinct from target_input_sha256
           or existing.request_sha256 is distinct from target_request_sha256
           or existing.expires_at is distinct from target_expires_at then
            raise exception 'OriginTrail Production Shadow day is immutable'
                using errcode = '23505';
        end if;
        select jsonb_build_object(
            'canary_config_subject_sha256', exact_grant.config_subject_sha256,
            'canary_config_approval_id', exact_grant.config_approval_id,
            'canary_dispatch_subject_sha256', exact_grant.dispatch_subject_sha256,
            'canary_dispatch_approval_id', exact_grant.dispatch_approval_id,
            'canary_job_id', exact_grant.job_id,
            'canary_input_sha256', exact_grant.input_sha256,
            'canary_request_sha256', exact_grant.request_sha256,
            'canary_expires_at', exact_grant.expires_at,
            'canary_hard_limit_microusd', exact_grant.hard_limit_microusd,
            'canary_max_provider_batches', exact_grant.max_provider_batches,
            'canary_provider_batches_consumed', exact_grant.provider_batches_consumed,
            'canary_consumed_at', exact_grant.consumed_at,
            'pilot_subject_sha256', existing.pilot_subject_sha256,
            'pilot_approval_id', existing.pilot_approval_id,
            'pilot_kst_date', existing.kst_date,
            'reused', true
        ) into exact_receipt
        from agent_runtime.origintrail_batch_canary_grants as exact_grant
        where exact_grant.workspace_id = existing.workspace_id
          and exact_grant.config_subject_sha256
                = existing.config_subject_sha256;
        return exact_receipt;
    end if;

    select queued.* into batch_job
    from agent_runtime.batch_jobs as queued
    where queued.workspace_id = target_workspace_id
      and queued.job_id = target_job_id
    for update;
    if not found
       or batch_job.client_id <> 'origintrail'
       or batch_job.agent_id <> 'origintrail_client_agent'
       or batch_job.workflow_kind <> 'official_source_nonurgent_pack'
       or batch_job.stage <> 'generate'
       or batch_job.latency_class <> 'batch_24h'
       or batch_job.model <> 'gpt-5.6-luna'
       or batch_job.model_tier <> 'S'
       or batch_job.input_sha256 is distinct from target_input_sha256
       or batch_job.input_payload ->> 'request_sha256'
            is distinct from target_request_sha256
       or batch_job.budget_key
            <> 'batch-general:' || target_kst_date::text
       or batch_job.status <> 'queued'
       or batch_job.reservation_state <> 'held'
       or batch_job.attempts <> 0
       or batch_job.available_at > statement_timestamp()
       or batch_job.deadline_at <= target_expires_at
       or batch_job.max_cost_microusd > target_hard_limit_microusd
       or batch_job.current_batch_id is not null then
        raise exception 'OriginTrail Production Shadow job binding is invalid'
            using errcode = '23514';
    end if;

    exact_receipt := public.configure_origintrail_batch_canary_grant(
        target_workspace_id,
        target_config_subject_sha256,
        target_config_approval_id,
        target_dispatch_subject_sha256,
        target_dispatch_approval_id,
        target_job_id,
        target_input_sha256,
        target_request_sha256,
        target_expires_at,
        target_hard_limit_microusd,
        target_max_provider_batches
    );

    insert into agent_runtime.origintrail_batch_production_shadow_days (
        workspace_id,
        kst_date,
        pilot_subject_sha256,
        pilot_approval_id,
        config_subject_sha256,
        config_approval_id,
        dispatch_subject_sha256,
        dispatch_approval_id,
        job_id,
        input_sha256,
        request_sha256,
        expires_at
    ) values (
        target_workspace_id,
        target_kst_date,
        target_pilot_subject_sha256,
        target_pilot_approval_id,
        target_config_subject_sha256,
        target_config_approval_id,
        target_dispatch_subject_sha256,
        target_dispatch_approval_id,
        target_job_id,
        target_input_sha256,
        target_request_sha256,
        target_expires_at
    );

    return exact_receipt || jsonb_build_object(
        'pilot_subject_sha256', target_pilot_subject_sha256,
        'pilot_approval_id', target_pilot_approval_id,
        'pilot_kst_date', target_kst_date,
        'reused', false
    );
end;
$$;

revoke all on function public.peek_origintrail_batch_shadow_candidate(
    uuid, text, uuid, timestamptz, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function public.configure_origintrail_batch_shadow_day(
    uuid, date, text, uuid, timestamptz, timestamptz, text, uuid,
    text, uuid, uuid, text, text, timestamptz, bigint, integer
) from public, anon, authenticated, service_role;

grant execute on function public.peek_origintrail_batch_shadow_candidate(
    uuid, text, uuid, timestamptz, timestamptz
) to service_role;
grant execute on function public.configure_origintrail_batch_shadow_day(
    uuid, date, text, uuid, timestamptz, timestamptz, text, uuid,
    text, uuid, uuid, text, text, timestamptz, bigint, integer
) to service_role;

commit;
