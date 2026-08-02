-- Forward-only settlement hardening for provider usage above the internal
-- reservation model. The full provider cost is preserved as immutable incident
-- evidence while only the reserved cap is moved into the bounded budget spend.

begin;

create table agent_runtime.batch_cost_overage_incidents (
    workspace_id uuid not null,
    job_id uuid not null,
    provider_batch_id text not null check (
        provider_batch_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ),
    attempt integer not null check (attempt between 1 and 2),
    outcome_kind text not null check (
        outcome_kind in ('completion', 'failure')
    ),
    outcome_code text not null check (
        outcome_code ~ '^[a-z0-9][a-z0-9_.-]{0,63}$'
    ),
    outcome_payload_sha256 text not null check (
        outcome_payload_sha256 ~ '^[a-f0-9]{64}$'
    ),
    input_tokens bigint not null check (input_tokens >= 0),
    output_tokens bigint not null check (output_tokens >= 0),
    reservation_cap_microusd bigint not null check (
        reservation_cap_microusd > 0
    ),
    actual_cost_microusd bigint not null check (
        actual_cost_microusd > reservation_cap_microusd
    ),
    overage_microusd bigint not null check (
        overage_microusd > 0
        and actual_cost_microusd
            = reservation_cap_microusd + overage_microusd
    ),
    budget_spent_microusd bigint not null check (
        budget_spent_microusd = reservation_cap_microusd
    ),
    outcome_fingerprint text not null check (
        outcome_fingerprint ~ '^[a-f0-9]{64}$'
    ),
    resolution_status text not null default 'unresolved' check (
        resolution_status = 'unresolved'
    ),
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, job_id),
    unique (workspace_id, outcome_fingerprint),
    foreign key (workspace_id, job_id)
        references agent_runtime.batch_jobs(workspace_id, job_id)
        on delete restrict,
    foreign key (workspace_id, provider_batch_id)
        references agent_runtime.batch_runs(workspace_id, batch_id)
        on delete restrict,
    foreign key (workspace_id, provider_batch_id, job_id)
        references agent_runtime.batch_members(workspace_id, batch_id, job_id)
        on delete restrict
);

alter table agent_runtime.batch_cost_overage_incidents
    enable row level security;
alter table agent_runtime.batch_cost_overage_incidents
    force row level security;

revoke all on table agent_runtime.batch_cost_overage_incidents
from public, anon, authenticated, service_role;

create or replace function agent_runtime.reject_batch_cost_overage_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'Batch cost overage incident evidence is immutable'
        using errcode = '23505';
end;
$$;

revoke all on function
    agent_runtime.reject_batch_cost_overage_mutation()
from public, anon, authenticated, service_role;

create trigger batch_cost_overage_incidents_immutable
before update or delete on agent_runtime.batch_cost_overage_incidents
for each row execute function
    agent_runtime.reject_batch_cost_overage_mutation();

-- A PostgreSQL transaction cannot remain open across an external provider
-- request made by an HTTP worker.  This durable fence bridges that boundary:
-- settlement must not pass an armed create intent, even after its short create
-- window expires.  Expiry forbids a late provider create; it never silently
-- proves that an already-started request did not reach the provider.
create table agent_runtime.origintrail_batch_provider_create_intents (
    workspace_id uuid not null,
    intent_id uuid not null,
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
    dispatch_key text not null check (
        dispatch_key ~ '^[a-f0-9]{64}$'
    ),
    create_request_sha256 text not null check (
        create_request_sha256 ~ '^[a-f0-9]{64}$'
    ),
    input_file_id text not null check (
        input_file_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ),
    attempt smallint not null check (attempt = 1),
    authorized_by text not null check (
        authorized_by ~ '^[A-Za-z0-9][A-Za-z0-9:_-]{7,119}$'
    ),
    authorized_at timestamptz not null default statement_timestamp(),
    create_not_after timestamptz not null,
    status text not null default 'armed' check (
        status in ('armed', 'registered')
    ),
    provider_batch_id text check (
        provider_batch_id is null
        or provider_batch_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ),
    registered_at timestamptz,
    primary key (workspace_id, intent_id),
    unique (workspace_id, config_subject_sha256),
    unique (workspace_id, job_id),
    foreign key (workspace_id, config_subject_sha256)
        references agent_runtime.origintrail_batch_canary_grants(
            workspace_id, config_subject_sha256
        ) on delete restrict,
    foreign key (workspace_id, job_id)
        references agent_runtime.batch_jobs(workspace_id, job_id)
        on delete restrict,
    foreign key (workspace_id, provider_batch_id)
        references agent_runtime.batch_runs(workspace_id, batch_id)
        on delete restrict,
    foreign key (workspace_id, provider_batch_id, job_id)
        references agent_runtime.batch_members(
            workspace_id, batch_id, job_id
        ) on delete restrict,
    check (
        create_not_after > authorized_at
        and create_not_after <= authorized_at + interval '2 minutes'
    ),
    check (
        (
            status = 'armed'
            and provider_batch_id is null
            and registered_at is null
        )
        or (
            status = 'registered'
            and provider_batch_id is not null
            and registered_at is not null
        )
    )
);

create unique index origintrail_batch_one_armed_provider_create
on agent_runtime.origintrail_batch_provider_create_intents (workspace_id)
where status = 'armed';

alter table agent_runtime.origintrail_batch_provider_create_intents
    enable row level security;
alter table agent_runtime.origintrail_batch_provider_create_intents
    force row level security;

revoke all on table
    agent_runtime.origintrail_batch_provider_create_intents
from public, anon, authenticated, service_role;

create or replace function agent_runtime.enforce_origintrail_batch_provider_create_intent()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'DELETE' then
        raise exception 'OriginTrail provider-create intent is immutable'
            using errcode = '23505';
    end if;
    if new.workspace_id is distinct from old.workspace_id
       or new.intent_id is distinct from old.intent_id
       or new.config_subject_sha256 is distinct from old.config_subject_sha256
       or new.config_approval_id is distinct from old.config_approval_id
       or new.dispatch_subject_sha256 is distinct from old.dispatch_subject_sha256
       or new.dispatch_approval_id is distinct from old.dispatch_approval_id
       or new.job_id is distinct from old.job_id
       or new.input_sha256 is distinct from old.input_sha256
       or new.request_sha256 is distinct from old.request_sha256
       or new.dispatch_key is distinct from old.dispatch_key
       or new.create_request_sha256
            is distinct from old.create_request_sha256
       or new.input_file_id is distinct from old.input_file_id
       or new.attempt is distinct from old.attempt
       or new.authorized_by is distinct from old.authorized_by
       or new.authorized_at is distinct from old.authorized_at
       or new.create_not_after is distinct from old.create_not_after then
        raise exception 'OriginTrail provider-create intent binding is immutable'
            using errcode = '23505';
    end if;
    if old.status = 'armed'
       and new.status = 'registered'
       and old.provider_batch_id is null
       and new.provider_batch_id is not null
       and old.registered_at is null
       and new.registered_at is not null then
        return new;
    end if;
    if new.status is distinct from old.status
       or new.provider_batch_id is distinct from old.provider_batch_id
       or new.registered_at is distinct from old.registered_at then
        raise exception 'OriginTrail provider-create intent transition is invalid'
            using errcode = '23505';
    end if;
    return new;
end;
$$;

revoke all on function
    agent_runtime.enforce_origintrail_batch_provider_create_intent()
from public, anon, authenticated, service_role;

create trigger enforce_origintrail_batch_provider_create_intent
before update or delete
on agent_runtime.origintrail_batch_provider_create_intents
for each row execute function
    agent_runtime.enforce_origintrail_batch_provider_create_intent();

create or replace function public.authorize_origintrail_batch_provider_create(
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
    target_intent_id uuid,
    target_dispatch_key text,
    target_create_request_sha256 text,
    target_input_file_id text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    canary_grant agent_runtime.origintrail_batch_canary_grants%rowtype;
    batch_job agent_runtime.batch_jobs%rowtype;
    create_intent
        agent_runtime.origintrail_batch_provider_create_intents%rowtype;
    create_not_after timestamptz;
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
       or target_intent_id is null
       or target_dispatch_key is null
       or target_dispatch_key !~ '^[a-f0-9]{64}$'
       or target_create_request_sha256 is null
       or target_create_request_sha256 !~ '^[a-f0-9]{64}$'
       or target_input_file_id is null
       or target_input_file_id
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' then
        raise exception 'OriginTrail provider-create authorization is invalid'
            using errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'coineasy:batch-cost-overage:' || target_workspace_id::text,
            0
        )
    );

    if exists (
        select 1
        from agent_runtime.batch_cost_overage_incidents as incident
        where incident.workspace_id = target_workspace_id
          and incident.resolution_status = 'unresolved'
    ) then
        raise exception 'Unresolved Batch cost overage blocks provider create'
            using errcode = '23514';
    end if;

    select registered.* into canary_grant
    from agent_runtime.origintrail_batch_canary_grants as registered
    where registered.workspace_id = target_workspace_id
      and registered.config_subject_sha256 = target_config_subject_sha256
    for update;
    if not found
       or canary_grant.config_approval_id
            is distinct from target_config_approval_id
       or canary_grant.dispatch_subject_sha256
            is distinct from target_dispatch_subject_sha256
       or canary_grant.dispatch_approval_id
            is distinct from target_dispatch_approval_id
       or canary_grant.job_id is distinct from target_job_id
       or canary_grant.input_sha256 is distinct from target_input_sha256
       or canary_grant.request_sha256 is distinct from target_request_sha256
       or canary_grant.expires_at is distinct from target_expires_at
       or canary_grant.hard_limit_microusd
            is distinct from target_hard_limit_microusd
       or canary_grant.max_provider_batches
            is distinct from target_max_provider_batches
       or canary_grant.provider_batches_consumed <> 1
       or canary_grant.consumed_by is distinct from target_worker_id then
        raise exception 'OriginTrail provider-create grant binding is invalid'
            using errcode = '23514';
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
       or batch_job.status <> 'claimed'
       or batch_job.reservation_state <> 'held'
       or batch_job.attempts <> 1
       or batch_job.locked_by is distinct from target_worker_id
       or batch_job.lease_expires_at <= statement_timestamp()
       or batch_job.claimed_at is distinct from canary_grant.consumed_at
       or batch_job.current_batch_id is not null then
        raise exception 'OriginTrail provider-create job binding is invalid'
            using errcode = '23514';
    end if;

    select recorded.* into create_intent
    from agent_runtime.origintrail_batch_provider_create_intents as recorded
    where recorded.workspace_id = target_workspace_id
      and recorded.job_id = target_job_id
    for update;
    if found then
        if create_intent.intent_id is distinct from target_intent_id
           or create_intent.config_subject_sha256
                is distinct from target_config_subject_sha256
           or create_intent.config_approval_id
                is distinct from target_config_approval_id
           or create_intent.dispatch_subject_sha256
                is distinct from target_dispatch_subject_sha256
           or create_intent.dispatch_approval_id
                is distinct from target_dispatch_approval_id
           or create_intent.input_sha256 is distinct from target_input_sha256
           or create_intent.request_sha256
                is distinct from target_request_sha256
           or create_intent.dispatch_key is distinct from target_dispatch_key
           or create_intent.create_request_sha256
                is distinct from target_create_request_sha256
           or create_intent.input_file_id is distinct from target_input_file_id
           or create_intent.attempt <> 1
           or create_intent.authorized_by is distinct from target_worker_id then
            raise exception 'OriginTrail provider-create intent is immutable'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'provider_create_intent_id', create_intent.intent_id,
            'intent_status', create_intent.status,
            -- A replay may be a lost positive response after the caller
            -- already entered an ambiguous external path.  It is therefore
            -- lookup-only even while the original short window is live.
            'provider_create_allowed', false,
            'create_not_after', create_intent.create_not_after,
            'job_id', create_intent.job_id,
            'attempt', create_intent.attempt,
            'config_subject_sha256', create_intent.config_subject_sha256,
            'config_approval_id', create_intent.config_approval_id,
            'dispatch_subject_sha256', create_intent.dispatch_subject_sha256,
            'dispatch_approval_id', create_intent.dispatch_approval_id,
            'input_sha256', create_intent.input_sha256,
            'request_sha256', create_intent.request_sha256,
            'dispatch_key', create_intent.dispatch_key,
            'create_request_sha256', create_intent.create_request_sha256,
            'input_file_id', create_intent.input_file_id,
            'reused', true
        );
    end if;

    if exists (
        select 1
        from agent_runtime.origintrail_batch_provider_create_intents as armed
        where armed.workspace_id = target_workspace_id
          and armed.status = 'armed'
    ) then
        raise exception 'Another provider-create intent already fences workspace'
            using errcode = '55P03';
    end if;

    create_not_after := least(
        canary_grant.expires_at,
        batch_job.lease_expires_at,
        statement_timestamp() + interval '2 minutes'
    );
    if create_not_after <= statement_timestamp() then
        raise exception 'OriginTrail provider-create window is closed'
            using errcode = '23514';
    end if;

    insert into agent_runtime.origintrail_batch_provider_create_intents (
        workspace_id,
        intent_id,
        config_subject_sha256,
        config_approval_id,
        dispatch_subject_sha256,
        dispatch_approval_id,
        job_id,
        input_sha256,
        request_sha256,
        dispatch_key,
        create_request_sha256,
        input_file_id,
        attempt,
        authorized_by,
        create_not_after
    ) values (
        target_workspace_id,
        target_intent_id,
        target_config_subject_sha256,
        target_config_approval_id,
        target_dispatch_subject_sha256,
        target_dispatch_approval_id,
        target_job_id,
        target_input_sha256,
        target_request_sha256,
        target_dispatch_key,
        target_create_request_sha256,
        target_input_file_id,
        1,
        target_worker_id,
        create_not_after
    ) returning * into create_intent;

    return jsonb_build_object(
        'provider_create_intent_id', create_intent.intent_id,
        'intent_status', create_intent.status,
        'provider_create_allowed', true,
        'create_not_after', create_intent.create_not_after,
        'job_id', create_intent.job_id,
        'attempt', create_intent.attempt,
        'config_subject_sha256', create_intent.config_subject_sha256,
        'config_approval_id', create_intent.config_approval_id,
        'dispatch_subject_sha256', create_intent.dispatch_subject_sha256,
        'dispatch_approval_id', create_intent.dispatch_approval_id,
        'input_sha256', create_intent.input_sha256,
        'request_sha256', create_intent.request_sha256,
        'dispatch_key', create_intent.dispatch_key,
        'create_request_sha256', create_intent.create_request_sha256,
        'input_file_id', create_intent.input_file_id,
        'reused', false
    );
end;
$$;

revoke all on function public.authorize_origintrail_batch_provider_create(
    uuid, text, text, uuid, text, uuid, uuid, text, text, timestamptz,
    bigint, integer, uuid, text, text, text
) from public, anon, authenticated, service_role;
grant execute on function public.authorize_origintrail_batch_provider_create(
    uuid, text, text, uuid, text, uuid, uuid, text, text, timestamptz,
    bigint, integer, uuid, text, text, text
) to service_role;

-- Keep the original implementation private.  The public generic signature is
-- restored below with an explicit ban on exact-canary jobs; only the fenced
-- registration RPC may close an armed provider-create intent.
alter function public.register_agent_batch(
    uuid, text, text, text, uuid[]
) rename to register_agent_batch_without_canary_intent;

revoke all on function public.register_agent_batch_without_canary_intent(
    uuid, text, text, text, uuid[]
) from public, anon, authenticated, service_role;

create or replace function public.register_agent_batch(
    target_workspace_id uuid,
    target_worker_id text,
    target_input_file_id text,
    target_batch_id text,
    target_job_ids uuid[]
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
    if exists (
        select 1
        from agent_runtime.origintrail_batch_canary_grants as canary_grant
        where canary_grant.workspace_id = target_workspace_id
          and canary_grant.job_id = any(target_job_ids)
    ) then
        raise exception 'Exact canary registration requires provider-create intent'
            using errcode = '23514';
    end if;
    return public.register_agent_batch_without_canary_intent(
        target_workspace_id,
        target_worker_id,
        target_input_file_id,
        target_batch_id,
        target_job_ids
    );
end;
$$;

revoke all on function public.register_agent_batch(
    uuid, text, text, text, uuid[]
) from public, anon, authenticated, service_role;
grant execute on function public.register_agent_batch(
    uuid, text, text, text, uuid[]
) to service_role;

create or replace function public.register_origintrail_batch_provider_create(
    target_workspace_id uuid,
    target_worker_id text,
    target_intent_id uuid,
    target_config_subject_sha256 text,
    target_job_id uuid,
    target_input_sha256 text,
    target_request_sha256 text,
    target_dispatch_key text,
    target_create_request_sha256 text,
    target_input_file_id text,
    target_batch_id text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    create_intent
        agent_runtime.origintrail_batch_provider_create_intents%rowtype;
    canary_grant agent_runtime.origintrail_batch_canary_grants%rowtype;
    batch_job agent_runtime.batch_jobs%rowtype;
    batch_run agent_runtime.batch_runs%rowtype;
    grant_found boolean;
    job_found boolean;
    run_found boolean;
    registration jsonb;
begin
    if target_workspace_id is null
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9:_-]{7,119}$'
       or target_intent_id is null
       or target_config_subject_sha256 is null
       or target_config_subject_sha256 !~ '^[a-f0-9]{64}$'
       or target_job_id is null
       or target_input_sha256 is null
       or target_input_sha256 !~ '^[a-f0-9]{64}$'
       or target_request_sha256 is null
       or target_request_sha256 !~ '^[a-f0-9]{64}$'
       or target_dispatch_key is null
       or target_dispatch_key !~ '^[a-f0-9]{64}$'
       or target_create_request_sha256 is null
       or target_create_request_sha256 !~ '^[a-f0-9]{64}$'
       or target_input_file_id is null
       or target_input_file_id
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       or target_batch_id is null
       or target_batch_id
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' then
        raise exception 'OriginTrail provider-create registration is invalid'
            using errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'coineasy:batch-cost-overage:' || target_workspace_id::text,
            0
        )
    );

    select recorded.* into create_intent
    from agent_runtime.origintrail_batch_provider_create_intents as recorded
    where recorded.workspace_id = target_workspace_id
      and recorded.intent_id = target_intent_id
    for update;
    if not found
       or create_intent.config_subject_sha256
            is distinct from target_config_subject_sha256
       or create_intent.job_id is distinct from target_job_id
       or create_intent.input_sha256 is distinct from target_input_sha256
       or create_intent.request_sha256 is distinct from target_request_sha256
       or create_intent.dispatch_key is distinct from target_dispatch_key
       or create_intent.create_request_sha256
            is distinct from target_create_request_sha256
       or create_intent.input_file_id is distinct from target_input_file_id
       or create_intent.attempt <> 1 then
        raise exception 'OriginTrail provider-create intent binding is invalid'
            using errcode = '23514';
    end if;

    select registered.* into canary_grant
    from agent_runtime.origintrail_batch_canary_grants as registered
    where registered.workspace_id = target_workspace_id
      and registered.config_subject_sha256 = target_config_subject_sha256
    for update;
    grant_found := found;

    -- Keep the shared run -> job lock order used by poll/finalize/settlement.
    select current_run.* into batch_run
    from agent_runtime.batch_runs as current_run
    where current_run.workspace_id = target_workspace_id
      and current_run.batch_id = target_batch_id
    for update;
    run_found := found;
    select exact_job.* into batch_job
    from agent_runtime.batch_jobs as exact_job
    where exact_job.workspace_id = target_workspace_id
      and exact_job.job_id = target_job_id
    for update;
    job_found := found;

    if create_intent.status = 'registered' then
        if create_intent.provider_batch_id is distinct from target_batch_id
           or not run_found
           or batch_run.input_file_id is distinct from target_input_file_id
           or not job_found
           or batch_job.attempts <> 1
           or batch_job.current_batch_id is distinct from target_batch_id
           or not exists (
               select 1
               from agent_runtime.batch_members as member
               where member.workspace_id = target_workspace_id
                 and member.batch_id = target_batch_id
                 and member.job_id = target_job_id
                 and member.attempt = 1
           ) then
            raise exception 'OriginTrail provider-create result is immutable'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'provider_create_intent_id', create_intent.intent_id,
            'intent_status', create_intent.status,
            'job_id', create_intent.job_id,
            'attempt', create_intent.attempt,
            'dispatch_key', create_intent.dispatch_key,
            'create_request_sha256', create_intent.create_request_sha256,
            'input_file_id', create_intent.input_file_id,
            'provider_batch_id', create_intent.provider_batch_id,
            'registered_at', create_intent.registered_at,
            'reused', true
        );
    end if;

    if exists (
        select 1
        from agent_runtime.batch_cost_overage_incidents as incident
        where incident.workspace_id = target_workspace_id
          and incident.resolution_status = 'unresolved'
    ) then
        raise exception 'Unresolved Batch cost overage blocks provider registration'
            using errcode = '23514';
    end if;

    if not grant_found
       or not job_found
       or canary_grant.provider_batches_consumed <> 1
       or canary_grant.job_id is distinct from target_job_id
       or canary_grant.input_sha256 is distinct from target_input_sha256
       or canary_grant.request_sha256 is distinct from target_request_sha256
       or batch_job.status <> 'claimed'
       or batch_job.reservation_state <> 'held'
       or batch_job.attempts <> 1
       or batch_job.locked_by is distinct from target_worker_id
       or batch_job.lease_expires_at <= statement_timestamp()
       or batch_job.claimed_at is distinct from canary_grant.consumed_at
       or batch_job.current_batch_id is not null then
        raise exception 'OriginTrail provider-create registration lost its lease'
            using errcode = '40001';
    end if;

    registration := public.register_agent_batch_without_canary_intent(
        target_workspace_id,
        target_worker_id,
        target_input_file_id,
        target_batch_id,
        array[target_job_id]::uuid[]
    );

    update agent_runtime.origintrail_batch_provider_create_intents
    set status = 'registered',
        provider_batch_id = target_batch_id,
        registered_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and intent_id = target_intent_id
    returning * into create_intent;

    return jsonb_build_object(
        'provider_create_intent_id', create_intent.intent_id,
        'intent_status', create_intent.status,
        'job_id', create_intent.job_id,
        'attempt', create_intent.attempt,
        'dispatch_key', create_intent.dispatch_key,
        'create_request_sha256', create_intent.create_request_sha256,
        'input_file_id', create_intent.input_file_id,
        'provider_batch_id', create_intent.provider_batch_id,
        'registered_at', create_intent.registered_at,
        'batch_reused', registration -> 'reused',
        'reused', false
    );
end;
$$;

revoke all on function public.register_origintrail_batch_provider_create(
    uuid, text, uuid, text, uuid, text, text, text, text, text, text
) from public, anon, authenticated, service_role;
grant execute on function public.register_origintrail_batch_provider_create(
    uuid, text, uuid, text, uuid, text, text, text, text, text, text
) to service_role;

create or replace function agent_runtime.settle_batch_cost_overage(
    target_workspace_id uuid,
    target_job_id uuid,
    target_batch_id text,
    target_outcome_kind text,
    target_outcome_code text,
    target_outcome_payload jsonb,
    target_input_tokens bigint,
    target_output_tokens bigint,
    target_actual_cost_microusd bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    job agent_runtime.batch_jobs%rowtype;
    run agent_runtime.batch_runs%rowtype;
    incident agent_runtime.batch_cost_overage_incidents%rowtype;
    payload_sha256 text;
    fingerprint text;
    affected_rows integer;
begin
    if target_workspace_id is null
       or target_job_id is null
       or target_batch_id is null
       or target_batch_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       or target_outcome_kind not in ('completion', 'failure')
       or target_outcome_code is null
       or target_outcome_code !~ '^[a-z0-9][a-z0-9_.-]{0,63}$'
       or jsonb_typeof(target_outcome_payload) is distinct from 'object'
       or octet_length(target_outcome_payload::text) > 1048576
       or target_input_tokens is null
       or target_input_tokens < 0
       or target_output_tokens is null
       or target_output_tokens < 0
       or target_actual_cost_microusd is null
       or target_actual_cost_microusd < 0 then
        raise exception 'Batch cost overage outcome is invalid'
            using errcode = '22023';
    end if;

    -- Claim admission uses the same transaction-scoped lock before its first
    -- incident-sensitive statement.  If settlement held the lock first, this
    -- standalone PERFORM waits; the following SELECT receives a fresh
    -- READ COMMITTED snapshot that includes the committed incident.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'coineasy:batch-cost-overage:' || target_workspace_id::text,
            0
        )
    );

    -- Never infer from wall-clock expiry that an external HTTPS request did
    -- not reach the provider.  An armed (including stale) intent must be
    -- resolved through exact registration/lookup before settlement can pass.
    if exists (
        select 1
        from agent_runtime.origintrail_batch_provider_create_intents as intent
        where intent.workspace_id = target_workspace_id
          and intent.status = 'armed'
    ) then
        raise exception 'Provider-create intent fences Batch settlement'
            using errcode = '55P03';
    end if;

    -- Poll/finalize paths lock a run before its jobs.  Preserve that global
    -- order here after taking the workspace fence to avoid run/job inversion.
    select current_run.* into run
    from agent_runtime.batch_runs as current_run
    where current_run.workspace_id = target_workspace_id
      and current_run.batch_id = target_batch_id
    for update;
    if not found then
        raise exception 'provider batch was not found'
            using errcode = 'P0002';
    end if;

    select current_job.* into job
    from agent_runtime.batch_jobs as current_job
    where current_job.workspace_id = target_workspace_id
      and current_job.job_id = target_job_id
    for update;
    if not found then
        raise exception 'batch job was not found'
            using errcode = 'P0002';
    end if;

    payload_sha256 := pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(target_outcome_payload::text, 'UTF8')
        ),
        'hex'
    );
    fingerprint := pg_catalog.encode(
        pg_catalog.sha256(pg_catalog.convert_to(
            jsonb_build_object(
                'schema', 'coineasy.batch.cost_overage.v1',
                'workspace_id', target_workspace_id::text,
                'job_id', target_job_id::text,
                'provider_batch_id', target_batch_id,
                'attempt', job.attempts,
                'outcome_kind', target_outcome_kind,
                'outcome_code', target_outcome_code,
                'outcome_payload_sha256', payload_sha256,
                'input_tokens', target_input_tokens,
                'output_tokens', target_output_tokens,
                'reservation_cap_microusd', job.max_cost_microusd,
                'actual_cost_microusd', target_actual_cost_microusd
            )::text,
            'UTF8'
        )),
        'hex'
    );

    select recorded.* into incident
    from agent_runtime.batch_cost_overage_incidents as recorded
    where recorded.workspace_id = target_workspace_id
      and recorded.job_id = target_job_id
    for update;
    if found then
        if incident.provider_batch_id is distinct from target_batch_id
           or incident.attempt is distinct from job.attempts
           or incident.outcome_kind is distinct from target_outcome_kind
           or incident.outcome_code is distinct from target_outcome_code
           or incident.outcome_payload_sha256 is distinct from payload_sha256
           or incident.input_tokens is distinct from target_input_tokens
           or incident.output_tokens is distinct from target_output_tokens
           or incident.reservation_cap_microusd
                is distinct from job.max_cost_microusd
           or incident.actual_cost_microusd
                is distinct from target_actual_cost_microusd
           or incident.outcome_fingerprint is distinct from fingerprint then
            raise exception 'Batch cost overage outcome cannot change'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'job_id', incident.job_id,
            'status', 'failed',
            'settlement', 'cost_cap_breached',
            'error_code', 'batch_cost_cap_breached',
            'provider_batch_id', incident.provider_batch_id,
            'outcome_kind', incident.outcome_kind,
            'input_tokens', incident.input_tokens,
            'output_tokens', incident.output_tokens,
            'reservation_cap_microusd', incident.reservation_cap_microusd,
            'actual_cost_microusd', incident.actual_cost_microusd,
            'overage_microusd', incident.overage_microusd,
            'budget_spent_microusd', incident.budget_spent_microusd,
            'outcome_fingerprint', incident.outcome_fingerprint,
            'resolution_status', incident.resolution_status,
            'reused', true
        );
    end if;

    if target_actual_cost_microusd <= job.max_cost_microusd then
        return null;
    end if;
    if job.status not in ('submitted', 'in_progress')
       or job.reservation_state <> 'held'
       or job.current_batch_id is distinct from target_batch_id then
        raise exception 'batch job is not awaiting this overage result'
            using errcode = '23514';
    end if;

    if run.provider_status not in (
           'completed', 'failed', 'expired', 'cancelled'
       )
       or (run.output_file_id is null and run.error_file_id is null)
       or run.finalized_at is not null
       or not exists (
           select 1
           from agent_runtime.batch_members as member
           where member.workspace_id = target_workspace_id
             and member.batch_id = target_batch_id
             and member.job_id = target_job_id
             and member.attempt = job.attempts
       ) then
        raise exception 'provider batch is not open for overage reconciliation'
            using errcode = '23514';
    end if;

    insert into agent_runtime.batch_cost_overage_incidents (
        workspace_id,
        job_id,
        provider_batch_id,
        attempt,
        outcome_kind,
        outcome_code,
        outcome_payload_sha256,
        input_tokens,
        output_tokens,
        reservation_cap_microusd,
        actual_cost_microusd,
        overage_microusd,
        budget_spent_microusd,
        outcome_fingerprint
    ) values (
        target_workspace_id,
        target_job_id,
        target_batch_id,
        job.attempts,
        target_outcome_kind,
        target_outcome_code,
        payload_sha256,
        target_input_tokens,
        target_output_tokens,
        job.max_cost_microusd,
        target_actual_cost_microusd,
        target_actual_cost_microusd - job.max_cost_microusd,
        job.max_cost_microusd,
        fingerprint
    ) returning * into incident;

    update agent_runtime.batch_budgets
    set reserved_microusd = reserved_microusd - job.max_cost_microusd,
        spent_microusd = spent_microusd + job.max_cost_microusd,
        updated_at = statement_timestamp()
    where workspace_id = job.workspace_id
      and budget_key = job.budget_key
      and reserved_microusd >= job.max_cost_microusd;
    get diagnostics affected_rows = row_count;
    if affected_rows <> 1 then
        raise exception 'Batch overage budget reservation is inconsistent'
            using errcode = '23514';
    end if;

    update agent_runtime.batch_jobs
    set status = 'failed',
        reservation_state = 'released',
        actual_input_tokens = target_input_tokens,
        actual_output_tokens = target_output_tokens,
        actual_cost_microusd = target_actual_cost_microusd,
        result_code = null,
        result_payload = '{}'::jsonb,
        error_code = 'batch_cost_cap_breached',
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        finished_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and job_id = target_job_id;
    get diagnostics affected_rows = row_count;
    if affected_rows <> 1 then
        raise exception 'Batch overage job settlement was lost'
            using errcode = '40001';
    end if;

    return jsonb_build_object(
        'job_id', incident.job_id,
        'status', 'failed',
        'settlement', 'cost_cap_breached',
        'error_code', 'batch_cost_cap_breached',
        'provider_batch_id', incident.provider_batch_id,
        'outcome_kind', incident.outcome_kind,
        'input_tokens', incident.input_tokens,
        'output_tokens', incident.output_tokens,
        'reservation_cap_microusd', incident.reservation_cap_microusd,
        'actual_cost_microusd', incident.actual_cost_microusd,
        'overage_microusd', incident.overage_microusd,
        'budget_spent_microusd', incident.budget_spent_microusd,
        'outcome_fingerprint', incident.outcome_fingerprint,
        'resolution_status', incident.resolution_status,
        'reused', false
    );
end;
$$;

revoke all on function agent_runtime.settle_batch_cost_overage(
    uuid, uuid, text, text, text, jsonb, bigint, bigint, bigint
) from public, anon, authenticated, service_role;

alter function public.complete_agent_batch_job(
    uuid, uuid, text, text, jsonb, bigint, bigint, bigint
) rename to complete_agent_batch_job_within_cap;

revoke all on function public.complete_agent_batch_job_within_cap(
    uuid, uuid, text, text, jsonb, bigint, bigint, bigint
) from public, anon, authenticated, service_role;

create or replace function public.complete_agent_batch_job(
    target_workspace_id uuid,
    target_job_id uuid,
    target_batch_id text,
    target_result_code text,
    target_result_payload jsonb,
    target_input_tokens bigint,
    target_output_tokens bigint,
    target_actual_cost_microusd bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    overage_receipt jsonb;
begin
    overage_receipt := agent_runtime.settle_batch_cost_overage(
        target_workspace_id,
        target_job_id,
        target_batch_id,
        'completion',
        target_result_code,
        target_result_payload,
        target_input_tokens,
        target_output_tokens,
        target_actual_cost_microusd
    );
    if overage_receipt is not null then
        return overage_receipt;
    end if;
    return public.complete_agent_batch_job_within_cap(
        target_workspace_id,
        target_job_id,
        target_batch_id,
        target_result_code,
        target_result_payload,
        target_input_tokens,
        target_output_tokens,
        target_actual_cost_microusd
    );
end;
$$;

revoke all on function public.complete_agent_batch_job(
    uuid, uuid, text, text, jsonb, bigint, bigint, bigint
) from public, anon, authenticated, service_role;
grant execute on function public.complete_agent_batch_job(
    uuid, uuid, text, text, jsonb, bigint, bigint, bigint
) to service_role;

alter function public.fail_agent_batch_job(
    uuid, uuid, text, text, boolean, timestamptz,
    bigint, bigint, bigint, boolean
) rename to fail_agent_batch_job_within_cap;

revoke all on function public.fail_agent_batch_job_within_cap(
    uuid, uuid, text, text, boolean, timestamptz,
    bigint, bigint, bigint, boolean
) from public, anon, authenticated, service_role;

create or replace function public.fail_agent_batch_job(
    target_workspace_id uuid,
    target_job_id uuid,
    target_expected_batch_id text,
    target_error_code text,
    target_retryable boolean,
    target_available_at timestamptz,
    target_input_tokens bigint,
    target_output_tokens bigint,
    target_actual_cost_microusd bigint,
    target_charge_full_reservation boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    overage_receipt jsonb;
begin
    if target_retryable is false
       and target_charge_full_reservation is false
       and target_expected_batch_id is not null
       and target_input_tokens is not null
       and target_output_tokens is not null
       and target_actual_cost_microusd is not null then
        overage_receipt := agent_runtime.settle_batch_cost_overage(
            target_workspace_id,
            target_job_id,
            target_expected_batch_id,
            'failure',
            target_error_code,
            '{}'::jsonb,
            target_input_tokens,
            target_output_tokens,
            target_actual_cost_microusd
        );
        if overage_receipt is not null then
            return overage_receipt;
        end if;
    end if;
    return public.fail_agent_batch_job_within_cap(
        target_workspace_id,
        target_job_id,
        target_expected_batch_id,
        target_error_code,
        target_retryable,
        target_available_at,
        target_input_tokens,
        target_output_tokens,
        target_actual_cost_microusd,
        target_charge_full_reservation
    );
end;
$$;

revoke all on function public.fail_agent_batch_job(
    uuid, uuid, text, text, boolean, timestamptz,
    bigint, bigint, bigint, boolean
) from public, anon, authenticated, service_role;
grant execute on function public.fail_agent_batch_job(
    uuid, uuid, text, text, boolean, timestamptz,
    bigint, bigint, bigint, boolean
) to service_role;

create or replace function agent_runtime.block_batch_claim_after_cost_overage()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if old.status in ('queued', 'retry_wait')
       and new.status = 'claimed'
       and (
           exists (
               select 1
               from agent_runtime.batch_cost_overage_incidents as incident
               where incident.workspace_id = new.workspace_id
                 and incident.resolution_status = 'unresolved'
           )
           or exists (
               select 1
               from agent_runtime.origintrail_batch_provider_create_intents
                    as intent
               where intent.workspace_id = new.workspace_id
                 and intent.status = 'armed'
           )
       ) then
        raise exception 'Batch safety fence blocks fresh claims'
            using errcode = '23514';
    end if;
    return new;
end;
$$;

revoke all on function
    agent_runtime.block_batch_claim_after_cost_overage()
from public, anon, authenticated, service_role;

create trigger block_batch_claim_after_cost_overage
before update of status, locked_by, lease_expires_at
on agent_runtime.batch_jobs
for each row execute function
    agent_runtime.block_batch_claim_after_cost_overage();

commit;
