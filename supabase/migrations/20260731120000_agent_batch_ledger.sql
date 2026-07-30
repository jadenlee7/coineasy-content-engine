-- Private, budget-capped OpenAI Batch ledger for the first 24-hour agent pilot.
--
-- This is deliberately separate from public.jobs. Browser roles and service_role
-- receive no direct table privileges; the service role can only use the narrow
-- state-transition RPCs at the end of this migration.

begin;

create schema if not exists agent_runtime;
revoke all on schema agent_runtime
from public, anon, authenticated, service_role;

create table agent_runtime.batch_budgets (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    budget_key text not null check (
        budget_key ~ '^[a-z0-9][a-z0-9._:-]{0,95}$'
    ),
    period_start timestamptz not null,
    period_end timestamptz not null,
    hard_limit_microusd bigint not null check (
        hard_limit_microusd between 1 and 6000000
    ),
    reserved_microusd bigint not null default 0 check (reserved_microusd >= 0),
    spent_microusd bigint not null default 0 check (spent_microusd >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (workspace_id, budget_key),
    check (period_end > period_start),
    check (period_end - period_start <= interval '24 hours'),
    check (reserved_microusd + spent_microusd <= hard_limit_microusd)
);

create table agent_runtime.batch_runs (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    batch_id text not null check (
        batch_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ),
    input_file_id text not null check (
        input_file_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ),
    output_file_id text check (
        output_file_id is null
        or output_file_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ),
    error_file_id text check (
        error_file_id is null
        or error_file_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ),
    provider_status text not null default 'validating' check (
        provider_status in (
            'validating', 'in_progress', 'finalizing', 'completed',
            'failed', 'expired', 'cancelling', 'cancelled'
        )
    ),
    job_count integer not null check (job_count between 1 and 500),
    poll_count integer not null default 0 check (poll_count >= 0),
    submitted_at timestamptz not null default now(),
    last_polled_at timestamptz,
    provider_completed_at timestamptz,
    finalized_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (workspace_id, batch_id),
    check (
        finalized_at is null
        or provider_status in ('completed', 'failed', 'expired', 'cancelled')
    )
);

create table agent_runtime.batch_jobs (
    job_id uuid primary key,
    workspace_id uuid not null,
    client_id text not null check (
        client_id in ('yellow', 'origintrail', 'squid', 'babylon')
    ),
    idempotency_key text not null check (
        idempotency_key ~ '^[a-f0-9]{64}$'
    ),
    custom_id text not null check (
        custom_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    agent_id text not null check (agent_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    workflow_kind text not null check (
        workflow_kind in (
            'official_source_nonurgent_pack',
            'daily_digest',
            'weekly_client_report',
            'content_qa',
            'naver_seo_article',
            'naver_seo_refresh',
            'partnership_screen',
            'partnership_deep_brief',
            'analytics_narrative',
            'visual_copy_qa',
            'release_changelog'
        )
    ),
    stage text not null check (
        stage in ('generate', 'review', 'qa', 'summarize')
    ),
    priority smallint not null default 0 check (priority between 0 and 3),
    latency_class text not null check (latency_class = 'batch_24h'),
    model text not null check (
        model in ('gpt-5.6-luna', 'gpt-5.6-terra')
    ),
    model_tier text not null check (model_tier in ('S', 'M')),
    deadline_at timestamptz not null,
    input_payload jsonb not null check (
        jsonb_typeof(input_payload) = 'object'
        and octet_length(input_payload::text) <= 1048576
    ),
    input_sha256 text not null check (input_sha256 ~ '^[a-f0-9]{64}$'),
    estimated_input_tokens bigint not null check (
        estimated_input_tokens between 1 and 1050000
    ),
    max_output_tokens integer not null check (
        max_output_tokens between 1 and 128000
    ),
    max_cost_microusd bigint not null check (
        max_cost_microusd between 1 and 1000000000000
    ),
    budget_key text not null,
    status text not null default 'queued' check (
        status in (
            'queued', 'claimed', 'submitted', 'in_progress',
            'retry_wait', 'completed', 'failed'
        )
    ),
    reservation_state text not null default 'held' check (
        reservation_state in ('held', 'settled', 'released')
    ),
    attempts integer not null default 0 check (attempts >= 0),
    max_attempts integer not null default 2 check (max_attempts between 1 and 2),
    available_at timestamptz not null default now(),
    locked_by text check (
        locked_by is null or char_length(locked_by) between 1 and 120
    ),
    locked_at timestamptz,
    lease_expires_at timestamptz,
    current_batch_id text,
    actual_input_tokens bigint check (
        actual_input_tokens is null or actual_input_tokens >= 0
    ),
    actual_output_tokens bigint check (
        actual_output_tokens is null or actual_output_tokens >= 0
    ),
    actual_cost_microusd bigint check (
        actual_cost_microusd is null or actual_cost_microusd >= 0
    ),
    result_code text check (
        result_code is null or result_code ~ '^[a-z0-9][a-z0-9_.-]{0,63}$'
    ),
    result_payload jsonb not null default '{}'::jsonb check (
        jsonb_typeof(result_payload) = 'object'
        and octet_length(result_payload::text) <= 1048576
    ),
    error_code text check (
        error_code is null or error_code ~ '^[a-z0-9][a-z0-9_.-]{0,63}$'
    ),
    queued_at timestamptz not null default now(),
    claimed_at timestamptz,
    submitted_at timestamptz,
    last_polled_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (workspace_id, idempotency_key),
    unique (workspace_id, custom_id),
    unique (workspace_id, job_id),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id) on delete restrict,
    foreign key (workspace_id, budget_key)
        references agent_runtime.batch_budgets(workspace_id, budget_key)
        on delete restrict,
    foreign key (workspace_id, current_batch_id)
        references agent_runtime.batch_runs(workspace_id, batch_id)
        on delete restrict,
    check (
        (status = 'claimed')
        = (
            locked_by is not null
            and locked_at is not null
            and lease_expires_at is not null
        )
    ),
    check (
        status not in ('submitted', 'in_progress')
        or current_batch_id is not null
    ),
    check (
        (status = 'completed')
        = (
            reservation_state = 'settled'
            and actual_input_tokens is not null
            and actual_output_tokens is not null
            and actual_cost_microusd is not null
            and result_code is not null
            and finished_at is not null
        )
    ),
    check (
        status <> 'failed'
        or (
            reservation_state = 'released'
            and error_code is not null
            and finished_at is not null
        )
    ),
    check (
        status not in ('queued', 'claimed', 'submitted', 'in_progress', 'retry_wait')
        or reservation_state = 'held'
    ),
    check (
        (
            model_tier = 'S'
            and model = 'gpt-5.6-luna'
            and max_cost_microusd <= 50000
        )
        or (
            model_tier = 'M'
            and model = 'gpt-5.6-terra'
            and max_cost_microusd <= 500000
        )
    )
);

create table agent_runtime.batch_members (
    workspace_id uuid not null,
    batch_id text not null,
    job_id uuid not null,
    attempt integer not null check (attempt > 0),
    custom_id text not null,
    created_at timestamptz not null default now(),
    primary key (workspace_id, batch_id, job_id),
    unique (job_id, attempt),
    foreign key (workspace_id, batch_id)
        references agent_runtime.batch_runs(workspace_id, batch_id)
        on delete restrict,
    foreign key (workspace_id, job_id)
        references agent_runtime.batch_jobs(workspace_id, job_id)
        on delete restrict
);

create index agent_batch_jobs_dequeue_idx
    on agent_runtime.batch_jobs (
        workspace_id, priority desc, deadline_at, available_at, queued_at
    )
    where status in ('queued', 'retry_wait', 'claimed');
create index agent_batch_jobs_budget_idx
    on agent_runtime.batch_jobs (workspace_id, budget_key, status);
create index agent_batch_jobs_current_batch_idx
    on agent_runtime.batch_jobs (workspace_id, current_batch_id)
    where current_batch_id is not null;
create index agent_batch_runs_active_idx
    on agent_runtime.batch_runs (workspace_id, submitted_at)
    where finalized_at is null;
create index agent_batch_members_job_idx
    on agent_runtime.batch_members (workspace_id, job_id, attempt desc);
create index agent_batch_review_inbox_idx
    on agent_runtime.batch_jobs (workspace_id, finished_at desc, job_id desc)
    where client_id = 'squid'
      and workflow_kind = 'official_source_nonurgent_pack'
      and status = 'completed'
      and result_code = 'needs_review';

alter table agent_runtime.batch_budgets enable row level security;
alter table agent_runtime.batch_budgets force row level security;
alter table agent_runtime.batch_runs enable row level security;
alter table agent_runtime.batch_runs force row level security;
alter table agent_runtime.batch_jobs enable row level security;
alter table agent_runtime.batch_jobs force row level security;
alter table agent_runtime.batch_members enable row level security;
alter table agent_runtime.batch_members force row level security;

revoke all on table
    agent_runtime.batch_budgets,
    agent_runtime.batch_runs,
    agent_runtime.batch_jobs,
    agent_runtime.batch_members
from public, anon, authenticated, service_role;

create or replace function public.configure_agent_batch_budget(
    target_workspace_id uuid,
    target_budget_key text,
    target_period_start timestamptz,
    target_period_end timestamptz,
    target_hard_limit_microusd bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    budget agent_runtime.batch_budgets%rowtype;
    reused boolean := false;
begin
    if target_workspace_id is null
       or not exists (
           select 1 from public.workspaces
           where id = target_workspace_id
       ) then
        raise exception 'batch budget workspace is invalid'
            using errcode = '22023';
    end if;
    if target_budget_key is null
       or target_budget_key !~ '^[a-z0-9][a-z0-9._:-]{0,95}$' then
        raise exception 'batch budget key is invalid'
            using errcode = '22023';
    end if;
    if target_period_start is null
       or target_period_end is null
       or target_period_end <= target_period_start
       or target_period_end - target_period_start > interval '24 hours' then
        raise exception 'batch budget period is invalid'
            using errcode = '22023';
    end if;
    if target_hard_limit_microusd not between 1 and 6000000 then
        raise exception 'batch budget hard limit is invalid'
            using errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            target_workspace_id::text || ':' || target_budget_key,
            0
        )
    );
    select configured.* into budget
    from agent_runtime.batch_budgets as configured
    where configured.workspace_id = target_workspace_id
      and configured.budget_key = target_budget_key
    for update;

    if found then
        if budget.period_start is distinct from target_period_start
           or budget.period_end is distinct from target_period_end then
            raise exception 'batch budget key is already bound to another period'
                using errcode = '23505';
        end if;
        if budget.hard_limit_microusd
           is distinct from target_hard_limit_microusd then
            raise exception 'batch budget hard limit is immutable for its key'
                using errcode = '23514';
        end if;
        reused := true;
    else
        insert into agent_runtime.batch_budgets (
            workspace_id, budget_key, period_start, period_end,
            hard_limit_microusd
        )
        values (
            target_workspace_id, target_budget_key, target_period_start,
            target_period_end, target_hard_limit_microusd
        )
        returning * into budget;
    end if;

    return jsonb_build_object(
        'workspace_id', budget.workspace_id,
        'budget_key', budget.budget_key,
        'period_start', budget.period_start,
        'period_end', budget.period_end,
        'hard_limit_microusd', budget.hard_limit_microusd,
        'reserved_microusd', budget.reserved_microusd,
        'spent_microusd', budget.spent_microusd,
        'available_microusd',
            budget.hard_limit_microusd
            - budget.reserved_microusd
            - budget.spent_microusd,
        'reused', reused
    );
end;
$$;

create or replace function public.queue_agent_batch_job(
    target_workspace_id uuid,
    target_client_id text,
    target_job_id uuid,
    target_idempotency_key text,
    target_agent_id text,
    target_workflow_kind text,
    target_stage text,
    target_priority smallint,
    target_latency_class text,
    target_model text,
    target_model_tier text,
    target_deadline_at timestamptz,
    target_input_payload jsonb,
    target_input_sha256 text,
    target_estimated_input_tokens bigint,
    target_max_output_tokens integer,
    target_max_cost_microusd bigint,
    target_budget_key text,
    target_custom_id text,
    target_replay_only boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    existing_job agent_runtime.batch_jobs%rowtype;
    budget agent_runtime.batch_budgets%rowtype;
    expected_first_custom_id text;
begin
    if target_workspace_id is null
       or target_job_id is null
       or target_idempotency_key is null
       or target_idempotency_key !~ '^[a-f0-9]{64}$'
       or target_replay_only is null then
        raise exception 'batch job identity is invalid'
            using errcode = '22023';
    end if;
    if target_client_id is null
       or target_client_id not in (
           'yellow', 'origintrail', 'squid', 'babylon'
       ) then
        raise exception 'batch client id is invalid'
            using errcode = '22023';
    end if;
    if target_agent_id is null
       or target_agent_id !~ '^[a-z0-9][a-z0-9_-]{0,63}$'
       or target_workflow_kind is null
       or target_workflow_kind not in (
           'official_source_nonurgent_pack',
           'daily_digest',
           'weekly_client_report',
           'content_qa',
           'naver_seo_article',
           'naver_seo_refresh',
           'partnership_screen',
           'partnership_deep_brief',
           'analytics_narrative',
           'visual_copy_qa',
           'release_changelog'
       )
       or target_stage is null
       or target_stage not in ('generate', 'review', 'qa', 'summarize') then
        raise exception 'batch workflow routing is invalid'
            using errcode = '22023';
    end if;
    expected_first_custom_id := target_job_id::text || ':' || target_stage || ':1';
    if target_custom_id is null
       or target_custom_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_custom_id is distinct from expected_first_custom_id then
        raise exception 'batch first-attempt custom id is invalid'
            using errcode = '22023';
    end if;
    if target_priority not between 0 and 3
       or target_latency_class is distinct from 'batch_24h' then
        raise exception 'only bounded batch_24h jobs may enter this ledger'
            using errcode = '22023';
    end if;
    if target_model is null
       or target_model_tier is null
       or target_max_cost_microusd is null
       or not (
           (
               target_model_tier = 'S'
               and target_model = 'gpt-5.6-luna'
               and target_max_cost_microusd between 1 and 50000
           )
           or (
               target_model_tier = 'M'
               and target_model = 'gpt-5.6-terra'
               and target_max_cost_microusd between 1 and 500000
           )
       ) then
        raise exception 'batch model routing is invalid'
            using errcode = '22023';
    end if;
    if target_deadline_at is null then
        raise exception 'batch deadline is required'
            using errcode = '22023';
    end if;
    if jsonb_typeof(target_input_payload) is distinct from 'object'
       or octet_length(target_input_payload::text) > 1048576
       or target_input_sha256 is null
       or target_input_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'batch input payload or hash is invalid'
            using errcode = '22023';
    end if;
    if target_estimated_input_tokens not between 1 and 1050000
       or target_max_output_tokens not between 1 and 128000
       or target_max_cost_microusd not between 1 and 1000000000000 then
        raise exception 'batch token or cost bound is invalid'
            using errcode = '22023';
    end if;

    -- Serialize the read-before-insert idempotency path. This also makes a
    -- replay deterministic when two callers first queue the same local job.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            target_workspace_id::text || ':' || target_idempotency_key,
            0
        )
    );

    select job.* into existing_job
    from agent_runtime.batch_jobs as job
    where job.workspace_id = target_workspace_id
      and job.idempotency_key = target_idempotency_key
    for update;
    if found then
        -- The first admission durably binds the job to its budget row. A
        -- commit-unknown replay may cross a UTC/KST budget-key rollover, so
        -- the stored binding is authoritative after every other immutable
        -- request field has matched.
        if existing_job.job_id is distinct from target_job_id
           or existing_job.client_id is distinct from target_client_id
           or existing_job.agent_id is distinct from target_agent_id
           or existing_job.workflow_kind is distinct from target_workflow_kind
           or existing_job.stage is distinct from target_stage
           or existing_job.priority is distinct from target_priority
           or existing_job.latency_class is distinct from target_latency_class
           or existing_job.model is distinct from target_model
           or existing_job.model_tier is distinct from target_model_tier
           or existing_job.deadline_at is distinct from target_deadline_at
           or existing_job.input_payload is distinct from target_input_payload
           or existing_job.input_sha256 is distinct from target_input_sha256
           or existing_job.estimated_input_tokens
                is distinct from target_estimated_input_tokens
           or existing_job.max_output_tokens
                is distinct from target_max_output_tokens
           or existing_job.max_cost_microusd
                is distinct from target_max_cost_microusd then
            raise exception 'batch idempotency key is bound to another request'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'job_id', existing_job.job_id,
            -- The queue receipt belongs to the producer's immutable first
            -- attempt. Retry allocation may have advanced the durable row to
            -- :2 or later, but a commit-unknown queue replay must still match
            -- the original producer item.
            'custom_id', expected_first_custom_id,
            'status', existing_job.status,
            'reserved_microusd', existing_job.max_cost_microusd,
            'budget_key', existing_job.budget_key,
            'reused', true
        );
    end if;

    -- Replay-only recovery must never turn an uncertain prior response into a
    -- second admission. This guard runs before all mutable admission checks,
    -- budget reservation, and insertion.
    if target_replay_only then
        raise exception 'replay-only batch job was not previously committed'
            using errcode = '23514';
    end if;

    -- These checks are admission-time policy, not replay policy. A committed
    -- idempotent job remains replayable if its client is later disabled or its
    -- deadline has since moved inside the Batch safety window.
    if not exists (
        select 1
        from public.workspace_clients
        where workspace_id = target_workspace_id
          and client_id = target_client_id
          and active is true
    ) then
        raise exception 'batch job workspace or client is inactive'
            using errcode = '22023';
    end if;
    if target_deadline_at < statement_timestamp() + interval '26 hours'
       or target_deadline_at > statement_timestamp() + interval '7 days' then
        raise exception 'batch deadline is outside the accepted window'
            using errcode = '22023';
    end if;

    select configured.* into budget
    from agent_runtime.batch_budgets as configured
    where configured.workspace_id = target_workspace_id
      and configured.budget_key = target_budget_key
    for update;
    if not found
       or statement_timestamp() < budget.period_start
       or statement_timestamp() >= budget.period_end then
        raise exception 'active batch budget was not found at reservation time'
            using errcode = '23514';
    end if;
    if budget.reserved_microusd
       + budget.spent_microusd
       + target_max_cost_microusd
       > budget.hard_limit_microusd then
        raise exception 'batch budget hard limit would be exceeded'
            using errcode = '23514';
    end if;

    insert into agent_runtime.batch_jobs (
        job_id, workspace_id, client_id, idempotency_key, custom_id,
        agent_id, workflow_kind, stage, priority, latency_class, model,
        model_tier, deadline_at, input_payload, input_sha256,
        estimated_input_tokens, max_output_tokens, max_cost_microusd,
        budget_key
    )
    values (
        target_job_id, target_workspace_id, target_client_id,
        target_idempotency_key, target_custom_id, target_agent_id,
        target_workflow_kind, target_stage, target_priority,
        target_latency_class, target_model, target_model_tier,
        target_deadline_at, target_input_payload, target_input_sha256,
        target_estimated_input_tokens, target_max_output_tokens,
        target_max_cost_microusd, target_budget_key
    );

    update agent_runtime.batch_budgets
    set reserved_microusd = reserved_microusd + target_max_cost_microusd,
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and budget_key = target_budget_key;

    return jsonb_build_object(
        'job_id', target_job_id,
        'custom_id', target_custom_id,
        'status', 'queued',
        'reserved_microusd', target_max_cost_microusd,
        'budget_key', target_budget_key,
        'reused', false
    );
end;
$$;

create or replace function public.expire_agent_batch_jobs(
    target_workspace_id uuid,
    target_client_ids text[]
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    stale_job agent_runtime.batch_jobs%rowtype;
    requested_client_count integer;
    configured_client_count integer;
    expired_job_count integer := 0;
    ambiguous_claimed_count integer := 0;
    released_microusd bigint := 0;
    stale_error_code text;
begin
    requested_client_count := coalesce(cardinality(target_client_ids), 0);
    if target_workspace_id is null
       or not exists (
           select 1
           from public.workspaces as workspace
           where workspace.id = target_workspace_id
       )
       or requested_client_count not between 1 and 32
       or (
           select count(distinct client_id)
           from unnest(target_client_ids) as requested(client_id)
       ) <> requested_client_count
       or exists (
           select 1
           from unnest(target_client_ids) as requested(client_id)
           where client_id is null
              or client_id not in (
                  'yellow', 'origintrail', 'squid', 'babylon'
              )
       ) then
        raise exception 'batch expiration parameters are invalid'
            using errcode = '22023';
    end if;

    -- Inactive configured clients remain cleanup-eligible, but a caller may
    -- not widen the scope to a client that is absent from this workspace.
    select count(*) into configured_client_count
    from public.workspace_clients as client
    where client.workspace_id = target_workspace_id
      and client.client_id = any(target_client_ids);
    if configured_client_count <> requested_client_count then
        raise exception 'batch expiration client allowlist is invalid'
            using errcode = '22023';
    end if;

    -- Only work that is known not to have entered the provider is releasable.
    -- A claimed row may represent an upload/create whose provider response was
    -- lost, so it stays held for deterministic metadata recovery or operator
    -- investigation even after its local deadline passes.
    for stale_job in
        select job.*
        from agent_runtime.batch_jobs as job
        where job.workspace_id = target_workspace_id
          and job.client_id = any(target_client_ids)
          and job.status in ('queued', 'retry_wait')
          and job.reservation_state = 'held'
          and (
              job.deadline_at <= statement_timestamp()
              or job.attempts >= job.max_attempts
          )
        for update
    loop
        stale_error_code := case
            when stale_job.deadline_at <= statement_timestamp()
                then 'batch_deadline_expired'
            else 'batch_attempts_exhausted'
        end;

        update agent_runtime.batch_budgets
        set reserved_microusd =
                reserved_microusd - stale_job.max_cost_microusd,
            updated_at = statement_timestamp()
        where workspace_id = stale_job.workspace_id
          and budget_key = stale_job.budget_key;

        update agent_runtime.batch_jobs
        set status = 'failed',
            reservation_state = 'released',
            error_code = stale_error_code,
            locked_by = null,
            locked_at = null,
            lease_expires_at = null,
            finished_at = statement_timestamp(),
            updated_at = statement_timestamp()
        where job_id = stale_job.job_id;

        expired_job_count := expired_job_count + 1;
        released_microusd :=
            released_microusd + stale_job.max_cost_microusd;
    end loop;

    select count(*) into ambiguous_claimed_count
    from agent_runtime.batch_jobs as job
    where job.workspace_id = target_workspace_id
      and job.client_id = any(target_client_ids)
      and job.status = 'claimed'
      and job.reservation_state = 'held'
      and job.deadline_at <= statement_timestamp();

    return jsonb_build_object(
        'expired_job_count', expired_job_count,
        'released_microusd', released_microusd,
        'ambiguous_claimed_count', ambiguous_claimed_count
    );
end;
$$;

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
              or client_id not in (
                  'yellow', 'origintrail', 'squid', 'babylon'
              )
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

    -- Poll-only deployments call this RPC directly; active claimers reuse the
    -- same narrow transition before selecting fresh work.
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
    )
    into claimed
    from updated;

    return claimed;
end;
$$;

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
declare
    existing_batch agent_runtime.batch_runs%rowtype;
    requested_count integer;
    eligible_count integer;
    existing_members uuid[];
begin
    requested_count := coalesce(cardinality(target_job_ids), 0);
    if target_workspace_id is null
       or target_worker_id is null
       or char_length(target_worker_id) not between 1 and 120
       or target_input_file_id is null
       or target_input_file_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       or target_batch_id is null
       or target_batch_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       or requested_count not between 1 and 500
       or (
           select count(distinct job_id)
           from unnest(target_job_ids) as requested(job_id)
       ) <> requested_count then
        raise exception 'batch registration parameters are invalid'
            using errcode = '22023';
    end if;

    select run.* into existing_batch
    from agent_runtime.batch_runs as run
    where run.workspace_id = target_workspace_id
      and run.batch_id = target_batch_id
    for update;
    if found then
        select array_agg(member.job_id order by member.job_id)
        into existing_members
        from agent_runtime.batch_members as member
        where member.workspace_id = target_workspace_id
          and member.batch_id = target_batch_id;
        if existing_batch.input_file_id is distinct from target_input_file_id
           or existing_members is distinct from (
               select array_agg(job_id order by job_id)
               from unnest(target_job_ids) as requested(job_id)
           ) then
            raise exception 'provider batch id is bound to another submission'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'batch_id', existing_batch.batch_id,
            'input_file_id', existing_batch.input_file_id,
            'provider_status', existing_batch.provider_status,
            'job_count', existing_batch.job_count,
            'reused', true
        );
    end if;

    perform 1
    from agent_runtime.batch_jobs as job
    where job.workspace_id = target_workspace_id
      and job.job_id = any(target_job_ids)
    for update;

    select count(*) into eligible_count
    from agent_runtime.batch_jobs as job
    where job.workspace_id = target_workspace_id
      and job.job_id = any(target_job_ids)
      and job.status = 'claimed'
      and job.locked_by = target_worker_id
      and job.lease_expires_at > statement_timestamp()
      and job.reservation_state = 'held';
    if eligible_count <> requested_count then
        raise exception 'batch jobs are not held by the current worker lease'
            using errcode = '40001';
    end if;

    insert into agent_runtime.batch_runs (
        workspace_id, batch_id, input_file_id, job_count
    )
    values (
        target_workspace_id, target_batch_id, target_input_file_id,
        requested_count
    );

    insert into agent_runtime.batch_members (
        workspace_id, batch_id, job_id, attempt, custom_id
    )
    select
        job.workspace_id, target_batch_id, job.job_id, job.attempts, job.custom_id
    from agent_runtime.batch_jobs as job
    where job.workspace_id = target_workspace_id
      and job.job_id = any(target_job_ids);

    update agent_runtime.batch_jobs
    set status = 'submitted',
        current_batch_id = target_batch_id,
        submitted_at = statement_timestamp(),
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and job_id = any(target_job_ids);

    return jsonb_build_object(
        'batch_id', target_batch_id,
        'input_file_id', target_input_file_id,
        'provider_status', 'validating',
        'job_count', requested_count,
        'reused', false
    );
end;
$$;

create or replace function public.list_active_agent_batches(
    target_workspace_id uuid,
    target_limit integer
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    active_batches jsonb;
begin
    if target_workspace_id is null or target_limit not between 1 and 500 then
        raise exception 'active batch list parameters are invalid'
            using errcode = '22023';
    end if;

    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'batch_id', batch_id,
                'provider_status', provider_status,
                'input_file_id', input_file_id,
                'output_file_id', output_file_id,
                'error_file_id', error_file_id,
                'job_count', job_count,
                'poll_count', poll_count,
                'submitted_at', submitted_at,
                'last_polled_at', last_polled_at
            )
            order by submitted_at
        ),
        '[]'::jsonb
    )
    into active_batches
    from (
        select run.*
        from agent_runtime.batch_runs as run
        where run.workspace_id = target_workspace_id
          and run.finalized_at is null
        order by run.submitted_at
        limit target_limit
    ) as active;

    return active_batches;
end;
$$;

create or replace function public.update_agent_batch_poll(
    target_workspace_id uuid,
    target_batch_id text,
    target_provider_status text,
    target_output_file_id text,
    target_error_file_id text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    run agent_runtime.batch_runs%rowtype;
begin
    if target_workspace_id is null
       or target_batch_id is null
       or target_provider_status not in (
           'validating', 'in_progress', 'finalizing', 'completed',
           'failed', 'expired', 'cancelling', 'cancelled'
       )
       or (
           target_output_file_id is not null
           and target_output_file_id
               !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       )
       or (
           target_error_file_id is not null
           and target_error_file_id
               !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       ) then
        raise exception 'batch poll result is invalid'
            using errcode = '22023';
    end if;

    select current_run.* into run
    from agent_runtime.batch_runs as current_run
    where current_run.workspace_id = target_workspace_id
      and current_run.batch_id = target_batch_id
    for update;
    if not found then
        raise exception 'provider batch was not found'
            using errcode = 'P0002';
    end if;
    if run.finalized_at is not null then
        if run.provider_status is distinct from target_provider_status
           or (
               target_output_file_id is not null
               and run.output_file_id is distinct from target_output_file_id
           )
           or (
               target_error_file_id is not null
               and run.error_file_id is distinct from target_error_file_id
           ) then
            raise exception 'finalized provider batch cannot change'
                using errcode = '23514';
        end if;
        return jsonb_build_object(
            'batch_id', run.batch_id,
            'provider_status', run.provider_status,
            'output_file_id', run.output_file_id,
            'error_file_id', run.error_file_id,
            'finalized', true,
            'reused', true
        );
    end if;

    if (
        run.output_file_id is not null
        and target_output_file_id is not null
        and run.output_file_id is distinct from target_output_file_id
    ) or (
        run.error_file_id is not null
        and target_error_file_id is not null
        and run.error_file_id is distinct from target_error_file_id
    ) then
        raise exception 'provider batch file id is immutable once recorded'
            using errcode = '23505';
    end if;

    if not (
        (run.provider_status = 'validating'
            and target_provider_status in (
                'validating', 'in_progress', 'finalizing', 'completed',
                'failed', 'expired', 'cancelling', 'cancelled'
            ))
        or (run.provider_status = 'in_progress'
            and target_provider_status in (
                'in_progress', 'finalizing', 'completed', 'failed',
                'expired', 'cancelling', 'cancelled'
            ))
        or (run.provider_status = 'finalizing'
            and target_provider_status in (
                'finalizing', 'completed', 'failed', 'expired', 'cancelled'
            ))
        or (run.provider_status = 'cancelling'
            and target_provider_status in (
                'cancelling', 'cancelled', 'completed'
            ))
        or (
            run.provider_status in ('completed', 'failed', 'expired', 'cancelled')
            and target_provider_status = run.provider_status
        )
    ) then
        raise exception 'provider batch status transition is invalid'
            using errcode = '23514';
    end if;
    if target_provider_status = 'completed'
       and run.output_file_id is null
       and run.error_file_id is null
       and target_output_file_id is null
       and target_error_file_id is null then
        raise exception 'completed provider batch requires a result or error file'
            using errcode = '23514';
    end if;

    update agent_runtime.batch_runs
    set provider_status = target_provider_status,
        output_file_id = coalesce(output_file_id, target_output_file_id),
        error_file_id = coalesce(error_file_id, target_error_file_id),
        poll_count = poll_count + 1,
        last_polled_at = statement_timestamp(),
        provider_completed_at = case
            when target_provider_status in (
                'completed', 'failed', 'expired', 'cancelled'
            ) then coalesce(provider_completed_at, statement_timestamp())
            else provider_completed_at
        end,
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and batch_id = target_batch_id;

    update agent_runtime.batch_jobs
    set status = case
            when target_provider_status in ('in_progress', 'finalizing')
                then 'in_progress'
            else status
        end,
        last_polled_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and current_batch_id = target_batch_id
      and status in ('submitted', 'in_progress');

    return jsonb_build_object(
        'batch_id', target_batch_id,
        'provider_status', target_provider_status,
        'output_file_id', coalesce(run.output_file_id, target_output_file_id),
        'error_file_id', coalesce(run.error_file_id, target_error_file_id),
        'finalized', false,
        'reused', false
    );
end;
$$;

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
    job agent_runtime.batch_jobs%rowtype;
    run agent_runtime.batch_runs%rowtype;
begin
    if target_workspace_id is null
       or target_job_id is null
       or target_batch_id is null
       or target_result_code is null
       or target_result_code !~ '^[a-z0-9][a-z0-9_.-]{0,63}$'
       or jsonb_typeof(target_result_payload) is distinct from 'object'
       or octet_length(target_result_payload::text) > 1048576
       or target_input_tokens < 0
       or target_output_tokens < 0
       or target_actual_cost_microusd < 0 then
        raise exception 'batch completion result is invalid'
            using errcode = '22023';
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
    if job.client_id = 'squid'
       and job.workflow_kind = 'official_source_nonurgent_pack'
       and (
           target_result_code is distinct from 'needs_review'
           or not (
               target_result_payload ?& array[
                   'headline_ko', 'body_ko', 'x_copy_ko',
                   'telegram_copy_ko'
               ]::text[]
           )
           or (
               select count(*)
               from jsonb_object_keys(target_result_payload)
           ) <> 4
           or jsonb_typeof(target_result_payload -> 'headline_ko')
                is distinct from 'string'
           or jsonb_typeof(target_result_payload -> 'body_ko')
                is distinct from 'string'
           or jsonb_typeof(target_result_payload -> 'x_copy_ko')
                is distinct from 'string'
           or jsonb_typeof(target_result_payload -> 'telegram_copy_ko')
                is distinct from 'string'
           or char_length(target_result_payload ->> 'headline_ko')
                not between 1 and 120
           or char_length(target_result_payload ->> 'body_ko')
                not between 1 and 1800
           or char_length(target_result_payload ->> 'x_copy_ko')
                not between 1 and 500
           or char_length(target_result_payload ->> 'telegram_copy_ko')
                not between 1 and 1800
           or not coalesce(
               (target_result_payload ->> 'headline_ko')
                   ~ '[^[:space:]]',
               false
           )
           or not coalesce(
               (target_result_payload ->> 'body_ko')
                   ~ '[^[:space:]]',
               false
           )
           or not coalesce(
               (target_result_payload ->> 'x_copy_ko')
                   ~ '[^[:space:]]',
               false
           )
           or not coalesce(
               (target_result_payload ->> 'telegram_copy_ko')
                   ~ '[^[:space:]]',
               false
           )
       ) then
        raise exception 'official-source Batch review result is invalid'
            using errcode = '23514';
    end if;
    if job.status = 'completed' then
        if job.current_batch_id is distinct from target_batch_id
           or job.result_code is distinct from target_result_code
           or job.result_payload is distinct from target_result_payload
           or job.actual_input_tokens is distinct from target_input_tokens
           or job.actual_output_tokens is distinct from target_output_tokens
           or job.actual_cost_microusd
                is distinct from target_actual_cost_microusd then
            raise exception 'completed batch job result cannot change'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'job_id', job.job_id,
            'status', job.status,
            'actual_cost_microusd', job.actual_cost_microusd,
            'reused', true
        );
    end if;
    if job.status not in ('submitted', 'in_progress')
       or job.reservation_state <> 'held'
       or job.current_batch_id is distinct from target_batch_id then
        raise exception 'batch job is not awaiting this provider result'
            using errcode = '23514';
    end if;

    select current_run.* into run
    from agent_runtime.batch_runs as current_run
    where current_run.workspace_id = target_workspace_id
      and current_run.batch_id = target_batch_id
    for update;
    if not found
       or run.provider_status not in (
           'completed', 'failed', 'expired', 'cancelled'
       )
       or (run.output_file_id is null and run.error_file_id is null)
       or run.finalized_at is not null then
        raise exception 'provider batch is not open for result reconciliation'
            using errcode = '23514';
    end if;
    if target_actual_cost_microusd > job.max_cost_microusd then
        raise exception 'actual batch cost exceeds the hard reservation'
            using errcode = '23514';
    end if;

    update agent_runtime.batch_budgets
    set reserved_microusd = reserved_microusd - job.max_cost_microusd,
        spent_microusd = spent_microusd + target_actual_cost_microusd,
        updated_at = statement_timestamp()
    where workspace_id = job.workspace_id
      and budget_key = job.budget_key;

    update agent_runtime.batch_jobs
    set status = 'completed',
        reservation_state = 'settled',
        actual_input_tokens = target_input_tokens,
        actual_output_tokens = target_output_tokens,
        actual_cost_microusd = target_actual_cost_microusd,
        result_code = target_result_code,
        result_payload = target_result_payload,
        error_code = null,
        finished_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where job_id = target_job_id;

    return jsonb_build_object(
        'job_id', target_job_id,
        'status', 'completed',
        'actual_cost_microusd', target_actual_cost_microusd,
        'released_reservation_microusd', job.max_cost_microusd,
        'reused', false
    );
end;
$$;

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
    job agent_runtime.batch_jobs%rowtype;
    run agent_runtime.batch_runs%rowtype;
    next_status text;
    exact_accounting boolean := false;
    billed_failure boolean := false;
    settled_cost_microusd bigint := 0;
begin
    if target_workspace_id is null
       or target_job_id is null
       or target_error_code is null
       or target_error_code !~ '^[a-z0-9][a-z0-9_.-]{0,63}$'
       or target_retryable is null
       or target_charge_full_reservation is null
       or (
           target_expected_batch_id is not null
           and target_expected_batch_id
               !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       ) then
        raise exception 'batch failure result is invalid'
            using errcode = '22023';
    end if;

    if target_charge_full_reservation then
        if target_input_tokens is not null
           or target_output_tokens is not null
           or target_actual_cost_microusd is not null then
            raise exception 'full-reservation failure cannot include exact usage'
                using errcode = '22023';
        end if;
    elsif target_input_tokens is null
          and target_output_tokens is null
          and target_actual_cost_microusd is null then
        exact_accounting := false;
    elsif target_input_tokens is not null
          and target_output_tokens is not null
          and target_actual_cost_microusd is not null
          and target_input_tokens >= 0
          and target_output_tokens >= 0
          and target_actual_cost_microusd >= 0 then
        exact_accounting := true;
    else
        raise exception 'exact failure accounting must be complete and nonnegative'
            using errcode = '22023';
    end if;

    billed_failure := target_charge_full_reservation or exact_accounting;
    if billed_failure and target_retryable then
        raise exception 'a billed batch failure cannot be retried'
            using errcode = '23514';
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

    settled_cost_microusd := case
        when target_charge_full_reservation then job.max_cost_microusd
        when exact_accounting then target_actual_cost_microusd
        else 0
    end;
    if exact_accounting
       and target_actual_cost_microusd > job.max_cost_microusd then
        raise exception 'actual batch failure cost exceeds the hard reservation'
            using errcode = '23514';
    end if;
    if billed_failure and target_expected_batch_id is null then
        raise exception 'billed failure requires a provider batch membership'
            using errcode = '23514';
    end if;

    -- A worker may crash after recording a retryable attempt-one outcome but
    -- before finalizing that terminal run. Once the job has advanced to a
    -- later attempt, replaying the immutable old outcome is a safe no-op. This
    -- lets the dispatcher finish finalizing the old run instead of leaving it
    -- permanently at the head of the active-poll queue.
    if target_retryable
       and not billed_failure
       and target_expected_batch_id is not null
       and exists (
           select 1
           from agent_runtime.batch_members as member
           where member.workspace_id = target_workspace_id
             and member.batch_id = target_expected_batch_id
             and member.job_id = target_job_id
             and member.attempt < job.attempts
       ) then
        return jsonb_build_object(
            'job_id', job.job_id,
            'status', job.status,
            'error_code', target_error_code,
            'reservation_held', job.reservation_state = 'held',
            'stale', true,
            'reused', true
        );
    end if;

    if job.status = 'failed' then
        if job.error_code is distinct from target_error_code
           or target_retryable
           or job.current_batch_id is distinct from target_expected_batch_id
           or job.actual_input_tokens is distinct from (
               case when exact_accounting then target_input_tokens else null end
           )
           or job.actual_output_tokens is distinct from (
               case when exact_accounting then target_output_tokens else null end
           )
           or job.actual_cost_microusd is distinct from (
               case
                   when billed_failure then settled_cost_microusd
                   else null
               end
           )
           or (
               target_expected_batch_id is not null
               and not exists (
                   select 1
                   from agent_runtime.batch_members as member
                   where member.workspace_id = target_workspace_id
                     and member.batch_id = target_expected_batch_id
                     and member.job_id = target_job_id
                     and member.attempt = job.attempts
               )
           ) then
            raise exception 'failed batch job result cannot change'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'job_id', job.job_id,
            'status', job.status,
            'error_code', job.error_code,
            'actual_input_tokens', job.actual_input_tokens,
            'actual_output_tokens', job.actual_output_tokens,
            'actual_cost_microusd', job.actual_cost_microusd,
            'reused', true
        );
    end if;
    if job.status = 'completed' then
        raise exception 'completed batch job cannot fail'
            using errcode = '23514';
    end if;
    if job.status = 'retry_wait'
       and target_retryable
       and not billed_failure
       and target_expected_batch_id is not null
       and job.error_code is not distinct from target_error_code
       and exists (
           select 1
           from agent_runtime.batch_members as member
           where member.workspace_id = target_workspace_id
             and member.batch_id = target_expected_batch_id
             and member.job_id = target_job_id
             and member.attempt = job.attempts
       ) then
        return jsonb_build_object(
            'job_id', job.job_id,
            'status', job.status,
            'error_code', job.error_code,
            'reservation_held', true,
            'reused', true
        );
    end if;
    if job.current_batch_id is distinct from target_expected_batch_id then
        raise exception 'batch failure does not match the active submission'
            using errcode = '40001';
    end if;
    if job.status not in (
        'queued', 'claimed', 'submitted', 'in_progress', 'retry_wait'
    ) or job.reservation_state <> 'held' then
        raise exception 'batch job is not fail-able'
            using errcode = '23514';
    end if;

    if target_expected_batch_id is not null then
        select current_run.* into run
        from agent_runtime.batch_runs as current_run
        where current_run.workspace_id = target_workspace_id
          and current_run.batch_id = target_expected_batch_id
        for update;
        if not found
           or run.provider_status not in (
               'completed', 'failed', 'expired', 'cancelled'
           )
           or (run.output_file_id is null and run.error_file_id is null)
           or run.finalized_at is not null
           or not exists (
               select 1
               from agent_runtime.batch_members as member
               where member.workspace_id = target_workspace_id
                 and member.batch_id = target_expected_batch_id
                 and member.job_id = target_job_id
                 and member.attempt = job.attempts
           ) then
            raise exception 'provider batch is not open for error reconciliation'
                using errcode = '23514';
        end if;
    end if;

    next_status := case
        when target_retryable
         and job.attempts < job.max_attempts
         and target_available_at is not null
         and target_available_at >= statement_timestamp()
         and target_available_at < job.deadline_at
            then 'retry_wait'
        else 'failed'
    end;

    if next_status = 'failed' then
        update agent_runtime.batch_budgets
        set reserved_microusd = reserved_microusd - job.max_cost_microusd,
            spent_microusd = spent_microusd + settled_cost_microusd,
            updated_at = statement_timestamp()
        where workspace_id = job.workspace_id
          and budget_key = job.budget_key;
    end if;

    update agent_runtime.batch_jobs
    set status = next_status,
        reservation_state = case
            when next_status = 'failed' then 'released'
            else 'held'
        end,
        available_at = case
            when next_status = 'retry_wait' then target_available_at
            else available_at
        end,
        current_batch_id = case
            when next_status = 'retry_wait' then null
            else current_batch_id
        end,
        actual_input_tokens = case
            when next_status = 'failed' and exact_accounting
                then target_input_tokens
            else null
        end,
        actual_output_tokens = case
            when next_status = 'failed' and exact_accounting
                then target_output_tokens
            else null
        end,
        actual_cost_microusd = case
            when next_status = 'failed' and billed_failure
                then settled_cost_microusd
            else null
        end,
        error_code = target_error_code,
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        finished_at = case
            when next_status = 'failed' then statement_timestamp()
            else null
        end,
        updated_at = statement_timestamp()
    where job_id = target_job_id;

    return jsonb_build_object(
        'job_id', target_job_id,
        'status', next_status,
        'error_code', target_error_code,
        'actual_input_tokens',
            case when exact_accounting then target_input_tokens else null end,
        'actual_output_tokens',
            case when exact_accounting then target_output_tokens else null end,
        'actual_cost_microusd',
            case when billed_failure then settled_cost_microusd else null end,
        'reservation_held', next_status = 'retry_wait',
        'reused', false
    );
end;
$$;

create or replace function public.finalize_agent_batch(
    target_workspace_id uuid,
    target_batch_id text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    run agent_runtime.batch_runs%rowtype;
    job agent_runtime.batch_jobs%rowtype;
    missing_job_count integer := 0;
    missing_error_code text;
begin
    if target_workspace_id is null
       or target_batch_id is null
       or target_batch_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' then
        raise exception 'batch finalize parameters are invalid'
            using errcode = '22023';
    end if;

    select current_run.* into run
    from agent_runtime.batch_runs as current_run
    where current_run.workspace_id = target_workspace_id
      and current_run.batch_id = target_batch_id
    for update;
    if not found then
        raise exception 'provider batch was not found'
            using errcode = 'P0002';
    end if;
    if run.finalized_at is not null then
        return jsonb_build_object(
            'batch_id', run.batch_id,
            'provider_status', run.provider_status,
            'missing_job_count', 0,
            'finalized', true,
            'reused', true
        );
    end if;
    if run.provider_status not in (
        'completed', 'failed', 'expired', 'cancelled'
    ) then
        raise exception 'only a terminal provider batch can be finalized'
            using errcode = '23514';
    end if;
    if run.provider_status = 'completed'
       and run.output_file_id is null
       and run.error_file_id is null then
        raise exception 'completed provider batch requires a result or error file'
            using errcode = '23514';
    end if;

    missing_error_code := case run.provider_status
        when 'completed' then 'batch_result_missing'
        when 'failed' then 'provider_batch_failed'
        when 'expired' then 'provider_batch_expired'
        else 'provider_batch_cancelled'
    end;

    for job in
        select pending.*
        from agent_runtime.batch_jobs as pending
        where pending.workspace_id = target_workspace_id
          and pending.current_batch_id = target_batch_id
          and pending.status in ('submitted', 'in_progress')
          and pending.reservation_state = 'held'
        for update
    loop
        update agent_runtime.batch_budgets
        set reserved_microusd = reserved_microusd - job.max_cost_microusd,
            spent_microusd = spent_microusd + case
                when run.provider_status = 'completed'
                    then job.max_cost_microusd
                else 0
            end,
            updated_at = statement_timestamp()
        where workspace_id = job.workspace_id
          and budget_key = job.budget_key;

        update agent_runtime.batch_jobs
        set status = 'failed',
            reservation_state = 'released',
            actual_cost_microusd = case
                when run.provider_status = 'completed'
                    then job.max_cost_microusd
                else null
            end,
            error_code = missing_error_code,
            finished_at = statement_timestamp(),
            updated_at = statement_timestamp()
        where job_id = job.job_id;
        missing_job_count := missing_job_count + 1;
    end loop;

    update agent_runtime.batch_runs
    set finalized_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and batch_id = target_batch_id;

    return jsonb_build_object(
        'batch_id', target_batch_id,
        'provider_status', run.provider_status,
        'missing_job_count', missing_job_count,
        'finalized', true,
        'reused', false
    );
end;
$$;

-- Bind an official-X review job to one execution plane for its entire retry
-- lifecycle. The first live lease holder chooses the plane; later lease
-- holders can recover that decision, but can never switch it.
create or replace function public.bind_review_draft_execution_plane(
    target_job_id uuid,
    target_worker_id text,
    target_requested_plane text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    review_job public.jobs%rowtype;
    bound_plane text;
    reused boolean;
begin
    if target_job_id is null
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_requested_plane is null
       or target_requested_plane not in ('studio_sync', 'openai_batch') then
        raise exception 'review draft execution plane request is invalid'
            using errcode = '22023';
    end if;

    select queued_job.* into review_job
    from public.jobs as queued_job
    where queued_job.id = target_job_id
    for update;
    if not found
       or review_job.job_kind <> 'generate'
       or review_job.content_item_id is not null
       or review_job.input ->> 'workflow'
            is distinct from 'official_x_review_draft_v1'
       or review_job.input -> 'manual_only' is distinct from 'false'::jsonb then
        raise exception 'review draft execution plane job does not exist'
            using errcode = '23514';
    end if;
    if review_job.status <> 'running'
       or review_job.locked_by is distinct from target_worker_id
       or review_job.lease_expires_at is null
       or review_job.lease_expires_at <= statement_timestamp() then
        raise exception 'review draft lease is not owned by this worker'
            using errcode = '55000';
    end if;

    if review_job.output ? 'execution_plane' then
        if jsonb_typeof(review_job.output -> 'execution_plane') <> 'string'
           or review_job.output ->> 'execution_plane'
                not in ('studio_sync', 'openai_batch') then
            raise exception 'review draft execution plane marker is invalid'
                using errcode = '23514';
        end if;
        bound_plane := review_job.output ->> 'execution_plane';
        reused := true;
    else
        bound_plane := target_requested_plane;
        reused := false;

        update public.jobs
        set output = jsonb_set(
                review_job.output,
                '{execution_plane}',
                to_jsonb(bound_plane),
                true
            ),
            updated_at = statement_timestamp()
        where id = review_job.id;

        insert into public.event_log (
            workspace_id, entity_type, entity_id, event_type, data
        ) values (
            review_job.workspace_id,
            'job',
            review_job.id,
            'official_x_review_draft_execution_plane_bound',
            jsonb_build_object(
                'job_id', review_job.id,
                'execution_plane', bound_plane
            )
        );
    end if;

    return jsonb_build_object(
        'job_id', review_job.id,
        'execution_plane', bound_plane,
        'reused', reused
    );
end;
$$;

-- Commit only the durable queue handoff. The Batch result remains in the
-- private ledger until it is completed and exposed by the read-only review
-- inbox below. This function deliberately creates no catalog content,
-- approval, publication, or export row.
create or replace function public.complete_review_draft_batch_handoff(
    target_job_id uuid,
    target_worker_id text,
    target_batch_job_id uuid,
    target_input_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    review_job public.jobs%rowtype;
    batch_job agent_runtime.batch_jobs%rowtype;
    expected_output jsonb;
begin
    if target_job_id is null
       or target_batch_job_id is null
       or target_batch_job_id is distinct from target_job_id
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_input_sha256 is null
       or target_input_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'review draft Batch handoff request is invalid'
            using errcode = '22023';
    end if;

    select queued_job.* into review_job
    from public.jobs as queued_job
    where queued_job.id = target_job_id
    for update;
    if not found
       or review_job.client_id <> 'squid'
       or review_job.job_kind <> 'generate'
       or review_job.content_item_id is not null
       or review_job.input ->> 'workflow'
            is distinct from 'official_x_review_draft_v1'
       or review_job.input -> 'manual_only' is distinct from 'false'::jsonb then
        raise exception 'review draft Batch handoff job does not exist'
            using errcode = '23514';
    end if;

    select queued_batch_job.* into batch_job
    from agent_runtime.batch_jobs as queued_batch_job
    where queued_batch_job.workspace_id = review_job.workspace_id
      and queued_batch_job.job_id = target_batch_job_id
    for key share;
    if not found
       or batch_job.job_id is distinct from review_job.id
       or batch_job.client_id <> 'squid'
       or batch_job.workflow_kind
            <> 'official_source_nonurgent_pack'
       or batch_job.stage <> 'generate'
       or batch_job.latency_class <> 'batch_24h'
       or batch_job.input_sha256 is distinct from target_input_sha256
       or batch_job.input_payload -> 'approval_required'
            is distinct from 'true'::jsonb
       or batch_job.input_payload -> 'interactive'
            is distinct from 'false'::jsonb
       or batch_job.input_payload -> 'source_snapshot_complete'
            is distinct from 'true'::jsonb
       or batch_job.input_payload -> 'input_immutable'
            is distinct from 'true'::jsonb then
        raise exception 'review draft Batch handoff does not match its queued work'
            using errcode = '23514';
    end if;

    expected_output := jsonb_build_object(
        'workflow', 'agent_batch_review_handoff_v1',
        'handoff', 'openai_batch',
        'batch_job_id', batch_job.job_id,
        'input_sha256', batch_job.input_sha256,
        'review_state', 'pending'
    );

    -- The receipt is worker-independent, so a different worker can safely
    -- resolve an uncertain response without changing the committed handoff.
    if review_job.status = 'succeeded' then
        if review_job.output is distinct from expected_output
           or review_job.locked_by is not null
           or review_job.locked_at is not null
           or review_job.lease_expires_at is not null
           or review_job.finished_at is null then
            raise exception 'review draft Batch handoff retry does not match'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'job_id', review_job.id,
            'status', review_job.status,
            'batch_job_id', batch_job.job_id,
            'input_sha256', batch_job.input_sha256,
            'review_state', 'pending',
            'reused', true
        );
    end if;

    if review_job.status <> 'running'
       or review_job.locked_by is distinct from target_worker_id
       or review_job.lease_expires_at is null
       or review_job.lease_expires_at <= statement_timestamp() then
        raise exception 'review draft lease is not owned by this worker'
            using errcode = '55000';
    end if;
    if jsonb_typeof(review_job.output -> 'execution_plane') <> 'string'
       or review_job.output ->> 'execution_plane'
            is distinct from 'openai_batch' then
        raise exception 'review draft job is not bound to the Batch plane'
            using errcode = '23514';
    end if;
    -- Deadline is admission-time policy. A durable queued receipt remains
    -- recoverable after that deadline, while its ledger state still must be
    -- active/held or an exact completed review result.
    if batch_job.status not in (
           'queued', 'claimed', 'submitted', 'in_progress',
           'retry_wait', 'completed'
       )
       or (
           batch_job.status = 'completed'
           and (
               batch_job.reservation_state <> 'settled'
               or batch_job.result_code <> 'needs_review'
           )
       )
       or (
           batch_job.status <> 'completed'
           and (
               batch_job.reservation_state <> 'held'
               or batch_job.result_code is not null
           )
       ) then
        raise exception 'review draft Batch work is not safely queued'
            using errcode = '23514';
    end if;

    update public.jobs
    set status = 'succeeded',
        output = expected_output,
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        finished_at = statement_timestamp(),
        last_error_code = null,
        last_error_message = null,
        updated_at = statement_timestamp()
    where id = review_job.id;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        review_job.workspace_id,
        'job',
        review_job.id,
        'official_x_review_draft_batch_handoff_completed',
        expected_output
    );

    return jsonb_build_object(
        'job_id', review_job.id,
        'status', 'succeeded',
        'batch_job_id', batch_job.job_id,
        'input_sha256', batch_job.input_sha256,
        'review_state', 'pending',
        'reused', false
    );
end;
$$;

create or replace function public.list_agent_batch_review_inbox(
    target_workspace_id uuid,
    target_limit integer,
    target_before_finished_at timestamptz,
    target_before_job_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    response jsonb;
begin
    if target_workspace_id is null
       or target_limit not between 1 and 100
       or (
           (target_before_finished_at is null)
           <> (target_before_job_id is null)
       )
       or not exists (
           select 1
           from public.workspaces as workspace
           where workspace.id = target_workspace_id
       ) then
        raise exception 'agent Batch review inbox request is invalid'
            using errcode = '22023';
    end if;

    with candidates as (
        select
            batch_job.job_id,
            batch_job.client_id,
            batch_job.agent_id,
            batch_job.workflow_kind,
            batch_job.stage,
            batch_job.status,
            batch_job.model,
            batch_job.model_tier,
            case
                when jsonb_typeof(
                         batch_job.result_payload -> 'headline_ko'
                     ) = 'string'
                 and char_length(
                         btrim(batch_job.result_payload ->> 'headline_ko')
                     )
                     between 1 and 120
                    then btrim(batch_job.result_payload ->> 'headline_ko')
                else 'Squid Batch review draft'
            end as title,
            batch_job.result_code,
            batch_job.actual_cost_microusd,
            batch_job.finished_at,
            review_job.input ->> 'source_url' as source_url
        from agent_runtime.batch_jobs as batch_job
        join public.jobs as review_job
          on review_job.id = batch_job.job_id
         and review_job.workspace_id = batch_job.workspace_id
         and review_job.client_id = batch_job.client_id
        where batch_job.workspace_id = target_workspace_id
          and batch_job.client_id = 'squid'
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
          and jsonb_typeof(batch_job.result_payload -> 'headline_ko')
                = 'string'
          and jsonb_typeof(batch_job.result_payload -> 'body_ko')
                = 'string'
          and jsonb_typeof(batch_job.result_payload -> 'x_copy_ko')
                = 'string'
          and jsonb_typeof(batch_job.result_payload -> 'telegram_copy_ko')
                = 'string'
          and char_length(
              batch_job.result_payload ->> 'headline_ko'
          ) between 1 and 120
          and char_length(
              batch_job.result_payload ->> 'body_ko'
          ) between 1 and 1800
          and char_length(
              batch_job.result_payload ->> 'x_copy_ko'
          ) between 1 and 500
          and char_length(
              batch_job.result_payload ->> 'telegram_copy_ko'
          ) between 1 and 1800
          and (batch_job.result_payload ->> 'headline_ko')
                ~ '[^[:space:]]'
          and (batch_job.result_payload ->> 'body_ko')
                ~ '[^[:space:]]'
          and (batch_job.result_payload ->> 'x_copy_ko')
                ~ '[^[:space:]]'
          and (batch_job.result_payload ->> 'telegram_copy_ko')
                ~ '[^[:space:]]'
          and review_job.job_kind = 'generate'
          and review_job.status = 'succeeded'
          and review_job.content_item_id is null
          and review_job.input ->> 'workflow'
                = 'official_x_review_draft_v1'
          and review_job.input -> 'manual_only' = 'false'::jsonb
          and review_job.output = jsonb_build_object(
              'workflow', 'agent_batch_review_handoff_v1',
              'handoff', 'openai_batch',
              'batch_job_id', batch_job.job_id,
              'input_sha256', batch_job.input_sha256,
              'review_state', 'pending'
          )
          and (
              target_before_finished_at is null
              or (batch_job.finished_at, batch_job.job_id)
                 < (target_before_finished_at, target_before_job_id)
          )
        order by batch_job.finished_at desc, batch_job.job_id desc
        limit target_limit + 1
    ),
    page as (
        select candidate.*
        from candidates as candidate
        order by candidate.finished_at desc, candidate.job_id desc
        limit target_limit
    )
    select jsonb_build_object(
        'items',
        coalesce((
            select jsonb_agg(
                jsonb_build_object(
                    'job_id', item.job_id,
                    'client_id', item.client_id,
                    'agent_id', item.agent_id,
                    'workflow_kind', item.workflow_kind,
                    'stage', item.stage,
                    'status', item.status,
                    'model', item.model,
                    'model_tier', item.model_tier,
                    'title', item.title,
                    'result_code', item.result_code,
                    'actual_cost_microusd', item.actual_cost_microusd,
                    'finished_at', item.finished_at,
                    'source_url', item.source_url
                )
                order by item.finished_at desc, item.job_id desc
            )
            from page as item
        ), '[]'::jsonb),
        'next_cursor',
        case
            when (select count(*) from candidates) > target_limit then (
                select jsonb_build_object(
                    'finished_at', cursor_item.finished_at,
                    'job_id', cursor_item.job_id
                )
                from page as cursor_item
                order by cursor_item.finished_at, cursor_item.job_id
                limit 1
            )
            else null
        end
    )
    into response;

    return response;
end;
$$;

create or replace function public.get_agent_batch_review_item(
    target_workspace_id uuid,
    target_job_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    item jsonb;
begin
    if target_workspace_id is null
       or target_job_id is null
       or not exists (
           select 1
           from public.workspaces as workspace
           where workspace.id = target_workspace_id
       ) then
        raise exception 'agent Batch review item request is invalid'
            using errcode = '22023';
    end if;

    select jsonb_build_object(
        'job_id', batch_job.job_id,
        'client_id', batch_job.client_id,
        'agent_id', batch_job.agent_id,
        'workflow_kind', batch_job.workflow_kind,
        'stage', batch_job.stage,
        'status', batch_job.status,
        'model', batch_job.model,
        'model_tier', batch_job.model_tier,
        'title',
            case
                when jsonb_typeof(
                         batch_job.result_payload -> 'headline_ko'
                     ) = 'string'
                 and char_length(
                         btrim(batch_job.result_payload ->> 'headline_ko')
                     )
                     between 1 and 120
                    then btrim(batch_job.result_payload ->> 'headline_ko')
                else 'Squid Batch review draft'
            end,
        'result_code', batch_job.result_code,
        'actual_cost_microusd', batch_job.actual_cost_microusd,
        'finished_at', batch_job.finished_at,
        'source_url', review_job.input ->> 'source_url',
        'result_payload', batch_job.result_payload,
        'input_sha256', batch_job.input_sha256,
        'actual_input_tokens', batch_job.actual_input_tokens,
        'actual_output_tokens', batch_job.actual_output_tokens
    )
    into item
    from agent_runtime.batch_jobs as batch_job
    join public.jobs as review_job
      on review_job.id = batch_job.job_id
     and review_job.workspace_id = batch_job.workspace_id
     and review_job.client_id = batch_job.client_id
    where batch_job.workspace_id = target_workspace_id
      and batch_job.job_id = target_job_id
      and batch_job.client_id = 'squid'
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
      and jsonb_typeof(batch_job.result_payload -> 'telegram_copy_ko')
            = 'string'
      and char_length(
          batch_job.result_payload ->> 'headline_ko'
      ) between 1 and 120
      and char_length(
          batch_job.result_payload ->> 'body_ko'
      ) between 1 and 1800
      and char_length(
          batch_job.result_payload ->> 'x_copy_ko'
      ) between 1 and 500
      and char_length(
          batch_job.result_payload ->> 'telegram_copy_ko'
      ) between 1 and 1800
      and (batch_job.result_payload ->> 'headline_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'body_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'x_copy_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'telegram_copy_ko')
            ~ '[^[:space:]]'
      and review_job.job_kind = 'generate'
      and review_job.status = 'succeeded'
      and review_job.content_item_id is null
      and review_job.input ->> 'workflow' = 'official_x_review_draft_v1'
      and review_job.input -> 'manual_only' = 'false'::jsonb
      and review_job.output = jsonb_build_object(
          'workflow', 'agent_batch_review_handoff_v1',
          'handoff', 'openai_batch',
          'batch_job_id', batch_job.job_id,
          'input_sha256', batch_job.input_sha256,
          'review_state', 'pending'
      );

    return item;
end;
$$;

revoke all on function public.configure_agent_batch_budget(
    uuid, text, timestamptz, timestamptz, bigint
) from public, anon, authenticated, service_role;
revoke all on function public.queue_agent_batch_job(
    uuid, text, uuid, text, text, text, text, smallint, text, text,
    text, timestamptz, jsonb, text, bigint, integer, bigint, text, text,
    boolean
) from public, anon, authenticated, service_role;
revoke all on function public.expire_agent_batch_jobs(
    uuid, text[]
) from public, anon, authenticated, service_role;
revoke all on function public.claim_agent_batch_jobs(
    uuid, text, text[], integer, integer
) from public, anon, authenticated, service_role;
revoke all on function public.register_agent_batch(
    uuid, text, text, text, uuid[]
) from public, anon, authenticated, service_role;
revoke all on function public.list_active_agent_batches(
    uuid, integer
) from public, anon, authenticated, service_role;
revoke all on function public.update_agent_batch_poll(
    uuid, text, text, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.complete_agent_batch_job(
    uuid, uuid, text, text, jsonb, bigint, bigint, bigint
) from public, anon, authenticated, service_role;
revoke all on function public.fail_agent_batch_job(
    uuid, uuid, text, text, boolean, timestamptz,
    bigint, bigint, bigint, boolean
) from public, anon, authenticated, service_role;
revoke all on function public.finalize_agent_batch(
    uuid, text
) from public, anon, authenticated, service_role;
revoke all on function public.bind_review_draft_execution_plane(
    uuid, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.complete_review_draft_batch_handoff(
    uuid, text, uuid, text
) from public, anon, authenticated, service_role;
revoke all on function public.list_agent_batch_review_inbox(
    uuid, integer, timestamptz, uuid
) from public, anon, authenticated, service_role;
revoke all on function public.get_agent_batch_review_item(
    uuid, uuid
) from public, anon, authenticated, service_role;

grant execute on function public.configure_agent_batch_budget(
    uuid, text, timestamptz, timestamptz, bigint
) to service_role;
grant execute on function public.queue_agent_batch_job(
    uuid, text, uuid, text, text, text, text, smallint, text, text,
    text, timestamptz, jsonb, text, bigint, integer, bigint, text, text,
    boolean
) to service_role;
grant execute on function public.expire_agent_batch_jobs(
    uuid, text[]
) to service_role;
grant execute on function public.claim_agent_batch_jobs(
    uuid, text, text[], integer, integer
) to service_role;
grant execute on function public.register_agent_batch(
    uuid, text, text, text, uuid[]
) to service_role;
grant execute on function public.list_active_agent_batches(
    uuid, integer
) to service_role;
grant execute on function public.update_agent_batch_poll(
    uuid, text, text, text, text
) to service_role;
grant execute on function public.complete_agent_batch_job(
    uuid, uuid, text, text, jsonb, bigint, bigint, bigint
) to service_role;
grant execute on function public.fail_agent_batch_job(
    uuid, uuid, text, text, boolean, timestamptz,
    bigint, bigint, bigint, boolean
) to service_role;
grant execute on function public.finalize_agent_batch(
    uuid, text
) to service_role;
grant execute on function public.bind_review_draft_execution_plane(
    uuid, text, text
) to service_role;
grant execute on function public.complete_review_draft_batch_handoff(
    uuid, text, uuid, text
) to service_role;
grant execute on function public.list_agent_batch_review_inbox(
    uuid, integer, timestamptz, uuid
) to service_role;
grant execute on function public.get_agent_batch_review_item(
    uuid, uuid
) to service_role;

commit;
