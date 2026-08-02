-- Durable, exact one-shot authorization for the first OriginTrail Batch canary.
--
-- The grant is deliberately consumed in the same transaction that claims the
-- exact attempt-1 job.  Burning the grant before any provider call makes a
-- lost response fail closed: a stale attempt may be recovered for provider
-- lookup, but it can never authorize a second provider Batch creation.

begin;

create table agent_runtime.origintrail_batch_canary_grants (
    workspace_id uuid not null
        references public.workspaces(id) on delete restrict,
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
    hard_limit_microusd bigint not null check (
        hard_limit_microusd between 1 and 50000
    ),
    max_provider_batches smallint not null check (
        max_provider_batches = 1
    ),
    provider_batches_consumed smallint not null default 0 check (
        provider_batches_consumed between 0 and max_provider_batches
    ),
    consumed_at timestamptz,
    consumed_by text check (
        consumed_by is null
        or consumed_by ~ '^[A-Za-z0-9][A-Za-z0-9:_-]{7,119}$'
    ),
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, config_subject_sha256),
    unique (workspace_id, config_approval_id),
    unique (workspace_id, dispatch_subject_sha256),
    unique (workspace_id, dispatch_approval_id),
    unique (workspace_id, job_id),
    foreign key (workspace_id, job_id)
        references agent_runtime.batch_jobs(workspace_id, job_id)
        on delete restrict,
    check (expires_at > created_at),
    check (
        (
            provider_batches_consumed = 0
            and consumed_at is null
            and consumed_by is null
        )
        or (
            provider_batches_consumed = 1
            and consumed_at is not null
            and consumed_by is not null
        )
    )
);

alter table agent_runtime.origintrail_batch_canary_grants
    enable row level security;
alter table agent_runtime.origintrail_batch_canary_grants
    force row level security;

revoke all on table agent_runtime.origintrail_batch_canary_grants
from public, anon, authenticated, service_role;

create or replace function agent_runtime.enforce_origintrail_canary_grant_immutable()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.workspace_id is distinct from old.workspace_id
       or new.config_subject_sha256 is distinct from old.config_subject_sha256
       or new.config_approval_id is distinct from old.config_approval_id
       or new.dispatch_subject_sha256 is distinct from old.dispatch_subject_sha256
       or new.dispatch_approval_id is distinct from old.dispatch_approval_id
       or new.job_id is distinct from old.job_id
       or new.input_sha256 is distinct from old.input_sha256
       or new.request_sha256 is distinct from old.request_sha256
       or new.expires_at is distinct from old.expires_at
       or new.hard_limit_microusd is distinct from old.hard_limit_microusd
       or new.max_provider_batches is distinct from old.max_provider_batches
       or new.created_at is distinct from old.created_at then
        raise exception 'OriginTrail Batch canary grant binding is immutable'
            using errcode = '23505';
    end if;
    if new.provider_batches_consumed < old.provider_batches_consumed
       or (old.consumed_at is not null and new.consumed_at is distinct from old.consumed_at)
       or (old.consumed_by is not null and new.consumed_by is distinct from old.consumed_by) then
        raise exception 'OriginTrail Batch canary consumption is irreversible'
            using errcode = '23505';
    end if;
    return new;
end;
$$;

revoke all on function
    agent_runtime.enforce_origintrail_canary_grant_immutable()
from public, anon, authenticated, service_role;

create trigger enforce_origintrail_canary_grant_immutable
before update on agent_runtime.origintrail_batch_canary_grants
for each row execute function
    agent_runtime.enforce_origintrail_canary_grant_immutable();

create or replace function public.configure_origintrail_batch_canary_grant(
    target_workspace_id uuid,
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
    canary_grant agent_runtime.origintrail_batch_canary_grants%rowtype;
    batch_job agent_runtime.batch_jobs%rowtype;
begin
    if target_workspace_id is null
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
       or target_hard_limit_microusd not between 1 and 50000
       or target_max_provider_batches is distinct from 1 then
        raise exception 'OriginTrail Batch canary grant parameters are invalid'
            using errcode = '22023';
    end if;

    if not exists (
        select 1
        from public.workspace_clients as client
        where client.workspace_id = target_workspace_id
          and client.client_id = 'origintrail'
          and client.active is true
    ) then
        raise exception 'OriginTrail Batch canary workspace is inactive'
            using errcode = '22023';
    end if;

    -- Serialize one immutable registration per workspace/config subject.  A
    -- second dispatch receipt cannot replace the first job under that config.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            target_workspace_id::text || ':' || target_config_subject_sha256,
            0
        )
    );

    select registered.* into canary_grant
    from agent_runtime.origintrail_batch_canary_grants as registered
    where registered.workspace_id = target_workspace_id
      and registered.config_subject_sha256 = target_config_subject_sha256
    for update;
    if found then
        if canary_grant.config_approval_id is distinct from target_config_approval_id
           or canary_grant.dispatch_subject_sha256 is distinct from target_dispatch_subject_sha256
           or canary_grant.dispatch_approval_id is distinct from target_dispatch_approval_id
           or canary_grant.job_id is distinct from target_job_id
           or canary_grant.input_sha256 is distinct from target_input_sha256
           or canary_grant.request_sha256 is distinct from target_request_sha256
           or canary_grant.expires_at is distinct from target_expires_at
           or canary_grant.hard_limit_microusd is distinct from target_hard_limit_microusd
           or canary_grant.max_provider_batches is distinct from target_max_provider_batches then
            raise exception 'OriginTrail Batch canary grant binding is immutable'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'canary_config_subject_sha256', canary_grant.config_subject_sha256,
            'canary_config_approval_id', canary_grant.config_approval_id,
            'canary_dispatch_subject_sha256', canary_grant.dispatch_subject_sha256,
            'canary_dispatch_approval_id', canary_grant.dispatch_approval_id,
            'canary_job_id', canary_grant.job_id,
            'canary_input_sha256', canary_grant.input_sha256,
            'canary_request_sha256', canary_grant.request_sha256,
            'canary_expires_at', canary_grant.expires_at,
            'canary_hard_limit_microusd', canary_grant.hard_limit_microusd,
            'canary_max_provider_batches', canary_grant.max_provider_batches,
            'canary_provider_batches_consumed', canary_grant.provider_batches_consumed,
            'canary_consumed_at', canary_grant.consumed_at,
            'reused', true
        );
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
       or batch_job.max_cost_microusd > target_hard_limit_microusd
       or batch_job.max_cost_microusd > 50000
       or batch_job.status <> 'queued'
       or batch_job.reservation_state <> 'held'
       or batch_job.attempts <> 0
       or batch_job.custom_id is distinct from
            target_job_id::text || ':generate:1'
       or batch_job.current_batch_id is not null
       or batch_job.deadline_at <= target_expires_at
       or batch_job.input_payload -> 'approval_required' is distinct from 'true'::jsonb
       or batch_job.input_payload -> 'interactive' is distinct from 'false'::jsonb
       or batch_job.input_payload -> 'incident_or_release_blocker' is distinct from 'false'::jsonb
       or batch_job.input_payload -> 'live_tools_required' is distinct from 'false'::jsonb
       or batch_job.input_payload -> 'source_snapshot_complete' is distinct from 'true'::jsonb
       or batch_job.input_payload -> 'input_immutable' is distinct from 'true'::jsonb
       or batch_job.input_payload -> 'retry_idempotent' is distinct from 'true'::jsonb
       or batch_job.input_payload ->> 'remaining_batch_stages'
            is distinct from '1' then
        raise exception 'OriginTrail Batch canary job binding is invalid'
            using errcode = '23514';
    end if;

    insert into agent_runtime.origintrail_batch_canary_grants (
        workspace_id,
        config_subject_sha256,
        config_approval_id,
        dispatch_subject_sha256,
        dispatch_approval_id,
        job_id,
        input_sha256,
        request_sha256,
        expires_at,
        hard_limit_microusd,
        max_provider_batches
    ) values (
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
    )
    returning * into canary_grant;

    return jsonb_build_object(
        'canary_config_subject_sha256', canary_grant.config_subject_sha256,
        'canary_config_approval_id', canary_grant.config_approval_id,
        'canary_dispatch_subject_sha256', canary_grant.dispatch_subject_sha256,
        'canary_dispatch_approval_id', canary_grant.dispatch_approval_id,
        'canary_job_id', canary_grant.job_id,
        'canary_input_sha256', canary_grant.input_sha256,
        'canary_request_sha256', canary_grant.request_sha256,
        'canary_expires_at', canary_grant.expires_at,
        'canary_hard_limit_microusd', canary_grant.hard_limit_microusd,
        'canary_max_provider_batches', canary_grant.max_provider_batches,
        'canary_provider_batches_consumed', canary_grant.provider_batches_consumed,
        'canary_consumed_at', canary_grant.consumed_at,
        'reused', false
    );
end;
$$;

create or replace function public.claim_origintrail_batch_canary_job(
    target_workspace_id uuid,
    target_worker_id text,
    target_config_subject_sha256 text,
    target_config_approval_id uuid,
    target_dispatch_subject_sha256 text,
    target_dispatch_approval_id uuid,
    target_job_id uuid,
    target_input_sha256 text,
    target_request_sha256 text,
    target_expires_at timestamptz,
    target_hard_limit_microusd bigint,
    target_max_provider_batches integer,
    target_lease_seconds integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    canary_grant agent_runtime.origintrail_batch_canary_grants%rowtype;
    batch_job agent_runtime.batch_jobs%rowtype;
    recovery_required boolean := false;
    provider_create_allowed boolean := false;
    reused boolean := false;
begin
    if target_workspace_id is null
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9:_-]{7,119}$'
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
       or target_hard_limit_microusd not between 1 and 50000
       or target_max_provider_batches is distinct from 1
       or target_lease_seconds not between 60 and 1800 then
        raise exception 'OriginTrail Batch canary claim parameters are invalid'
            using errcode = '22023';
    end if;

    if not exists (
        select 1
        from public.workspace_clients as client
        where client.workspace_id = target_workspace_id
          and client.client_id = 'origintrail'
          and client.active is true
    ) then
        raise exception 'OriginTrail Batch canary workspace is inactive'
            using errcode = '22023';
    end if;

    -- Settlement takes the same transaction-scoped workspace lock before it
    -- can insert an overage incident.  Acquiring it in this separate statement
    -- gives every later claim statement a post-wait READ COMMITTED snapshot.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'coineasy:batch-cost-overage:' || target_workspace_id::text,
            0
        )
    );

    select registered.* into canary_grant
    from agent_runtime.origintrail_batch_canary_grants as registered
    where registered.workspace_id = target_workspace_id
      and registered.config_subject_sha256 = target_config_subject_sha256
    for update;
    if not found then
        raise exception 'OriginTrail Batch canary grant is not registered'
            using errcode = '23514';
    end if;
    -- Validate the complete durable authorization before taking any job lock.
    -- A wrong job or request therefore cannot be leased or mutated.
    if canary_grant.config_approval_id is distinct from target_config_approval_id
       or canary_grant.dispatch_subject_sha256 is distinct from target_dispatch_subject_sha256
       or canary_grant.dispatch_approval_id is distinct from target_dispatch_approval_id
       or canary_grant.job_id is distinct from target_job_id
       or canary_grant.input_sha256 is distinct from target_input_sha256
       or canary_grant.request_sha256 is distinct from target_request_sha256
       or canary_grant.expires_at is distinct from target_expires_at
       or canary_grant.hard_limit_microusd is distinct from target_hard_limit_microusd
       or canary_grant.max_provider_batches is distinct from target_max_provider_batches then
        raise exception 'OriginTrail Batch canary claim does not match grant'
            using errcode = '23514';
    end if;
    if canary_grant.expires_at <= statement_timestamp() then
        return '[]'::jsonb;
    end if;

    select exact_job.* into batch_job
    from agent_runtime.batch_jobs as exact_job
    where exact_job.workspace_id = target_workspace_id
      and exact_job.job_id = target_job_id
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
       or batch_job.max_cost_microusd > target_hard_limit_microusd
       or batch_job.max_cost_microusd > 50000
       or batch_job.custom_id is distinct from
            target_job_id::text || ':generate:1'
       or batch_job.reservation_state <> 'held'
       or batch_job.current_batch_id is not null
       or batch_job.deadline_at <= statement_timestamp() then
        raise exception 'OriginTrail Batch canary job no longer matches grant'
            using errcode = '23514';
    end if;

    if canary_grant.provider_batches_consumed = 0 then
        if batch_job.status <> 'queued'
           or batch_job.attempts <> 0
           or batch_job.available_at > statement_timestamp() then
            return '[]'::jsonb;
        end if;

        -- The irreversible one-shot burn and exact attempt-1 claim are atomic.
        update agent_runtime.origintrail_batch_canary_grants
        set provider_batches_consumed = 1,
            consumed_at = statement_timestamp(),
            consumed_by = target_worker_id
        where workspace_id = target_workspace_id
          and config_subject_sha256 = target_config_subject_sha256
        returning * into canary_grant;

        update agent_runtime.batch_jobs
        set status = 'claimed',
            attempts = 1,
            custom_id = target_job_id::text || ':generate:1',
            locked_by = target_worker_id,
            locked_at = statement_timestamp(),
            lease_expires_at = statement_timestamp()
                + make_interval(secs => target_lease_seconds),
            claimed_at = statement_timestamp(),
            current_batch_id = null,
            updated_at = statement_timestamp()
        where workspace_id = target_workspace_id
          and job_id = target_job_id
        returning * into batch_job;
        provider_create_allowed := true;
    elsif canary_grant.provider_batches_consumed = 1 then
        -- Recovery preserves attempt 1 and the consumed grant.  It authorizes
        -- provider lookup only; a lookup miss must never create another Batch.
        if batch_job.status <> 'claimed'
           or batch_job.attempts <> 1
           or batch_job.lease_expires_at > statement_timestamp()
           or batch_job.claimed_at is distinct from canary_grant.consumed_at then
            return '[]'::jsonb;
        end if;
        update agent_runtime.batch_jobs
        set locked_by = target_worker_id,
            locked_at = statement_timestamp(),
            lease_expires_at = statement_timestamp()
                + make_interval(secs => target_lease_seconds),
            updated_at = statement_timestamp()
        where workspace_id = target_workspace_id
          and job_id = target_job_id
        returning * into batch_job;
        recovery_required := true;
        reused := true;
    else
        raise exception 'OriginTrail Batch canary grant consumption is invalid'
            using errcode = '23514';
    end if;

    return jsonb_build_array(jsonb_build_object(
        'job_id', batch_job.job_id,
        'custom_id', batch_job.custom_id,
        'client_id', batch_job.client_id,
        'agent_id', batch_job.agent_id,
        'workflow_kind', batch_job.workflow_kind,
        'stage', batch_job.stage,
        'priority', batch_job.priority,
        'latency_class', batch_job.latency_class,
        'model', batch_job.model,
        'model_tier', batch_job.model_tier,
        'deadline', batch_job.deadline_at,
        'input_payload', batch_job.input_payload,
        'input_sha256', batch_job.input_sha256,
        'estimated_input_tokens', batch_job.estimated_input_tokens,
        'max_output_tokens', batch_job.max_output_tokens,
        'max_cost_microusd', batch_job.max_cost_microusd,
        'budget_key', batch_job.budget_key,
        'attempt', batch_job.attempts,
        'recovery_required', recovery_required,
        'attempt_started_at', batch_job.claimed_at,
        'lease_expires_at', batch_job.lease_expires_at,
        'provider_create_allowed', provider_create_allowed,
        'canary_config_subject_sha256', canary_grant.config_subject_sha256,
        'canary_config_approval_id', canary_grant.config_approval_id,
        'canary_dispatch_subject_sha256', canary_grant.dispatch_subject_sha256,
        'canary_dispatch_approval_id', canary_grant.dispatch_approval_id,
        'canary_job_id', canary_grant.job_id,
        'canary_input_sha256', canary_grant.input_sha256,
        'canary_request_sha256', canary_grant.request_sha256,
        'canary_expires_at', canary_grant.expires_at,
        'canary_hard_limit_microusd', canary_grant.hard_limit_microusd,
        'canary_max_provider_batches', canary_grant.max_provider_batches,
        'canary_provider_batches_consumed', canary_grant.provider_batches_consumed,
        'canary_consumed_at', canary_grant.consumed_at,
        'reused', reused
    ));
end;
$$;

-- Forward-only override: a job with a durable canary grant is invisible to
-- the generic multi-client claimer.  Only the exact RPC above can mutate it.
create or replace function public.claim_agent_batch_jobs(
    target_workspace_id uuid,
    target_worker_id text,
    target_client_ids text[],
    target_limit integer,
    target_lease_seconds integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    claimed jsonb;
    requested_client_count integer;
    active_client_count integer;
begin
    requested_client_count := coalesce(cardinality(target_client_ids), 0);
    if target_workspace_id is null
       or target_worker_id is null
       or char_length(target_worker_id) not between 1 and 120
       or requested_client_count not between 1 and 32
       or (
           select count(distinct client_id)
           from unnest(target_client_ids) as requested(client_id)
       ) <> requested_client_count
       or exists (
           select 1
           from unnest(target_client_ids) as requested(client_id)
           where client_id is null
              or client_id not in ('yellow', 'origintrail', 'squid', 'babylon')
       )
       or target_limit not between 1 and 500
       or target_lease_seconds not between 60 and 1800 then
        raise exception 'batch claim parameters are invalid'
            using errcode = '22023';
    end if;

    select count(*) into active_client_count
    from public.workspace_clients as client
    where client.workspace_id = target_workspace_id
      and client.client_id = any(target_client_ids)
      and client.active is true;
    if active_client_count <> requested_client_count then
        raise exception 'batch claim client allowlist is invalid'
            using errcode = '22023';
    end if;

    -- Serialize fresh admission with overage settlement.  This must remain a
    -- standalone statement so the later expiry/claim statements see an
    -- incident committed by a settlement that held the lock first.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'coineasy:batch-cost-overage:' || target_workspace_id::text,
            0
        )
    );

    perform public.expire_agent_batch_jobs(
        target_workspace_id,
        target_client_ids
    );

    with candidates as (
        select
            job.job_id,
            case
                when job.status = 'claimed' then job.attempts
                else job.attempts + 1
            end as next_attempt,
            job.status = 'claimed' as recovery_required,
            case
                when job.status = 'claimed' then job.claimed_at
                else statement_timestamp()
            end as attempt_started_at
        from agent_runtime.batch_jobs as job
        where job.workspace_id = target_workspace_id
          and job.client_id = any(target_client_ids)
          and not exists (
              select 1
              from agent_runtime.origintrail_batch_canary_grants as canary_grant
              where canary_grant.workspace_id = job.workspace_id
                and canary_grant.job_id = job.job_id
          )
          -- OriginTrail is exclusive to the exact canary RPC.  Excluding the
          -- whole client also closes the queue-to-grant registration race.
          and job.client_id <> 'origintrail'
          and job.reservation_state = 'held'
          and job.deadline_at > statement_timestamp()
          and (
              (
                  job.status in ('queued', 'retry_wait')
                  and job.attempts < job.max_attempts
                  and job.available_at <= statement_timestamp()
              )
              or (
                  job.status = 'claimed'
                  and job.lease_expires_at <= statement_timestamp()
                  and job.attempts between 1 and job.max_attempts
              )
          )
        order by job.priority desc, job.deadline_at, job.available_at, job.queued_at
        for update skip locked
        limit target_limit
    ),
    updated as (
        update agent_runtime.batch_jobs as job
        set status = 'claimed',
            attempts = candidates.next_attempt,
            custom_id = job.job_id::text
                || ':' || job.stage
                || ':' || candidates.next_attempt::text,
            locked_by = target_worker_id,
            locked_at = statement_timestamp(),
            lease_expires_at = statement_timestamp()
                + make_interval(secs => target_lease_seconds),
            claimed_at = candidates.attempt_started_at,
            current_batch_id = null,
            updated_at = statement_timestamp()
        from candidates
        where job.job_id = candidates.job_id
        returning job.*, candidates.recovery_required
    )
    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'job_id', job_id,
                'custom_id', custom_id,
                'client_id', client_id,
                'agent_id', agent_id,
                'workflow_kind', workflow_kind,
                'stage', stage,
                'priority', priority,
                'latency_class', latency_class,
                'model', model,
                'model_tier', model_tier,
                'deadline', deadline_at,
                'input_payload', input_payload,
                'input_sha256', input_sha256,
                'estimated_input_tokens', estimated_input_tokens,
                'max_output_tokens', max_output_tokens,
                'max_cost_microusd', max_cost_microusd,
                'budget_key', budget_key,
                'attempt', attempts,
                'recovery_required', recovery_required,
                'attempt_started_at', claimed_at,
                'lease_expires_at', lease_expires_at
            )
            order by priority desc, deadline_at, queued_at
        ),
        '[]'::jsonb
    ) into claimed
    from updated;

    return claimed;
end;
$$;

revoke all on function public.configure_origintrail_batch_canary_grant(
    uuid, text, uuid, text, uuid, uuid, text, text, timestamptz,
    bigint, integer
) from public, anon, authenticated, service_role;
revoke all on function public.claim_origintrail_batch_canary_job(
    uuid, text, text, uuid, text, uuid, uuid, text, text, timestamptz,
    bigint, integer, integer
) from public, anon, authenticated, service_role;

grant execute on function public.configure_origintrail_batch_canary_grant(
    uuid, text, uuid, text, uuid, uuid, text, text, timestamptz,
    bigint, integer
) to service_role;
grant execute on function public.claim_origintrail_batch_canary_job(
    uuid, text, text, uuid, text, uuid, uuid, text, text, timestamptz,
    bigint, integer, integer
) to service_role;

commit;
