-- Durable planning-only Agent Work Order ledger.
--
-- This migration persists the already shipped `agent-work-order@1` contract.
-- It deliberately exposes no agent adapter, provider attempt, message delivery,
-- publication, deployment, or paid-action routine.  Authorization only creates
-- a pending dispatch packet; a later, separately approved migration must add a
-- worker transition before that packet can leave the database.

begin;

create schema if not exists agent_runtime;
revoke all on schema agent_runtime
from public, anon, authenticated, service_role;

-- Python's canonical scope uses recursively sorted object keys, ordered arrays,
-- UTF-8 JSON, and no insignificant whitespace.  jsonb::text alone is not the
-- same byte sequence, so keep the serializer explicit and cross-language safe.
create or replace function private.agent_json_canonical(target jsonb)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select case pg_catalog.jsonb_typeof(target)
        when 'object' then (
            select '{' || coalesce(pg_catalog.string_agg(
                pg_catalog.to_json(pair.key)::text || ':' ||
                    private.agent_json_canonical(pair.value),
                ',' order by pg_catalog.convert_to(pair.key, 'UTF8')
            ), '') || '}'
            from pg_catalog.jsonb_each(target) as pair(key, value)
        )
        when 'array' then (
            select '[' || coalesce(pg_catalog.string_agg(
                private.agent_json_canonical(element.value),
                ',' order by element.ordinality
            ), '') || ']'
            from pg_catalog.jsonb_array_elements(target)
                with ordinality as element(value, ordinality)
        )
        else target::text
    end
$$;

create or replace function private.agent_json_sha256(target jsonb)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(private.agent_json_canonical(target), 'UTF8'),
        'sha256'
    ), 'hex')
$$;

create or replace function private.agent_branch_scope_key(
    target_repository text,
    target_branch_name text,
    target_casefold boolean
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(case when target_casefold
            then pg_catalog.lower(target_repository)
            else target_repository end, 'UTF8')
        || pg_catalog.decode('00', 'hex')
        || pg_catalog.convert_to(case when target_casefold
            then pg_catalog.lower(target_branch_name)
            else target_branch_name end, 'UTF8'),
        'sha256'
    ), 'hex')
$$;

create or replace function private.agent_safe_text(
    target text,
    minimum_octets integer,
    maximum_octets integer,
    single_line boolean
)
returns boolean
language sql
immutable
set search_path = ''
as $$
    select target is not null
       and target = pg_catalog.btrim(target)
       and pg_catalog.octet_length(pg_catalog.btrim(target))
            between minimum_octets and maximum_octets
       and target !~ '[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]'
       and (not single_line or target !~ E'[\r\n\t]')
       and target !~* '(sk|xai)-[A-Za-z0-9_-]{20,}'
       and target !~* '(github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}'
       and target !~* 'glpat-[A-Za-z0-9_-]{20,}'
       and target !~* 'sb_(secret|publishable)_[A-Za-z0-9_-]{20,}'
       and target !~ '(^|[^A-Z0-9])AKIA[0-9A-Z]{16}([^A-Z0-9]|$)'
       and target !~ '(^|[^A-Za-z0-9])AIza[0-9A-Za-z_-]{30,}([^A-Za-z0-9]|$)'
       and target !~* '(^|[^A-Za-z0-9])nsec1[023456789acdefghjklmnpqrstuvwxyz]{50,}([^A-Za-z0-9]|$)'
       and target !~ '-----BEGIN [A-Z ]*PRIVATE KEY-----'
       and target !~* 'Bearer[[:space:]]+[A-Za-z0-9._~+/-]{16,}={0,2}'
       and target !~ 'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
       and target !~ '(^|[^0-9])[0-9]{6,}:[A-Za-z0-9_-]{20,}([^A-Za-z0-9]|$)'
       and target !~ '(^|[^A-Za-z0-9])[A-Za-z0-9_+=]{32,}([^A-Za-z0-9]|$)'
$$;

create or replace function private.agent_work_order_scope_valid(target jsonb)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    created_at_value timestamptz;
    expires_at_value timestamptz;
    required_actions constant text[] := array[
        'branch_push', 'draft_pr_create', 'merge', 'preview_deploy',
        'production_deploy', 'production_database_write', 'credential_change',
        'paid_provider_call', 'public_message', 'publication'
    ];
begin
    if target is null
       or pg_catalog.jsonb_typeof(target) <> 'object'
       or pg_catalog.octet_length(target::text) > 1048576
       or not target ?& array[
            'acceptance_criteria', 'allowed_environment', 'allowed_paths',
            'automatic_publication', 'base_sha', 'branch_name', 'causation_id',
            'client_id', 'created_at', 'evidence', 'expected_artifacts',
            'expires_at', 'forbidden_actions', 'idempotency_key',
            'max_cost_microusd', 'max_external_actions', 'max_handoffs',
            'max_runtime_seconds', 'objective', 'objective_id', 'owner',
            'parent_work_order_id', 'repository', 'requested_by', 'reviewer',
            'risk_tier', 'schema_version', 'title', 'verification_commands',
            'work_order_id', 'work_type'
       ]
       or (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(target)) <> 31
       or exists (
            select 1
            from unnest(array[
                'base_sha', 'branch_name', 'causation_id', 'created_at',
                'expires_at', 'idempotency_key', 'objective', 'objective_id',
                'owner', 'repository', 'requested_by', 'reviewer', 'risk_tier',
                'schema_version', 'title', 'work_order_id', 'work_type'
            ]) as scalar(key)
            where pg_catalog.jsonb_typeof(target -> scalar.key) <> 'string'
       )
       or pg_catalog.jsonb_typeof(target -> 'automatic_publication')
            <> 'boolean'
       or pg_catalog.jsonb_typeof(target -> 'max_cost_microusd') <> 'number'
       or pg_catalog.jsonb_typeof(target -> 'max_external_actions') <> 'number'
       or pg_catalog.jsonb_typeof(target -> 'max_handoffs') <> 'number'
       or pg_catalog.jsonb_typeof(target -> 'max_runtime_seconds') <> 'number'
       or coalesce(target ->> 'max_runtime_seconds', '') !~ '^[0-9]+$'
       or target ->> 'schema_version' <> 'agent-work-order@1'
       or target ->> 'requested_by' <> 'human_operator'
       or target ->> 'work_type' <> 'engineering'
       or target ->> 'risk_tier' <> 'R1'
       or target ->> 'allowed_environment' <> 'local'
       or target -> 'automatic_publication' is distinct from 'false'::jsonb
       or target -> 'max_cost_microusd' is distinct from '0'::jsonb
       or target -> 'max_external_actions' is distinct from '0'::jsonb
       or target -> 'max_handoffs' is distinct from '1'::jsonb
       or coalesce(target ->> 'owner', '') not in (
            'devin', 'claude_code', 'codex', 'grok_build'
       )
       or coalesce(target ->> 'reviewer', '') not in (
            'codex', 'claude_code', 'human_operator'
       )
       or target ->> 'owner' = target ->> 'reviewer'
       or coalesce(target ->> 'work_order_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or coalesce(target ->> 'objective_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or coalesce(target ->> 'causation_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or (
            target -> 'parent_work_order_id' <> 'null'::jsonb
            and coalesce(target ->> 'parent_work_order_id', '')
                !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       )
       or target ->> 'parent_work_order_id' = target ->> 'work_order_id'
       or coalesce(target ->> 'base_sha', '') !~ '^[a-f0-9]{40}$'
       or coalesce(target ->> 'repository', '')
            !~ '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'
       or coalesce(target ->> 'branch_name', '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._/-]{2,119}$'
       or target ->> 'branch_name' like '%..%'
       or target ->> 'branch_name' like '%//%'
       or target ->> 'branch_name' like '%/'
       or coalesce(target ->> 'idempotency_key', '')
            !~ '^[a-z0-9][a-z0-9:._/-]{7,199}$'
       or not private.agent_safe_text(target ->> 'title', 3, 160, true)
       or not private.agent_safe_text(target ->> 'objective', 10, 2000, false)
       or not private.agent_safe_text(target ->> 'repository', 3, 200, true)
       or not private.agent_safe_text(target ->> 'branch_name', 3, 120, true)
       or not private.agent_safe_text(target ->> 'idempotency_key', 8, 200, true)
       or coalesce((target ->> 'max_runtime_seconds')::numeric, 0)
            not between 60 and 86400
       or coalesce(target ->> 'created_at', '')
            !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
       or coalesce(target ->> 'expires_at', '')
            !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
       or (
            target -> 'client_id' <> 'null'::jsonb
            and (
                pg_catalog.jsonb_typeof(target -> 'client_id') <> 'string'
                or coalesce(target ->> 'client_id', '')
                    !~ '^[a-z][a-z0-9_-]{1,39}$'
                or not private.agent_safe_text(
                    target ->> 'client_id', 2, 40, true
                )
            )
       )
       or (
            target -> 'parent_work_order_id' <> 'null'::jsonb
            and pg_catalog.jsonb_typeof(target -> 'parent_work_order_id')
                <> 'string'
       ) then
        return false;
    end if;

    begin
        created_at_value := (target ->> 'created_at')::timestamptz;
        expires_at_value := (target ->> 'expires_at')::timestamptz;
    exception when others then
        return false;
    end;
    if expires_at_value <= created_at_value
       or expires_at_value - created_at_value > interval '14 days' then
        return false;
    end if;

    if pg_catalog.jsonb_typeof(target -> 'allowed_paths') <> 'array'
       or pg_catalog.jsonb_array_length(target -> 'allowed_paths') not between 1 and 32
       or (select pg_catalog.count(*) from (
            select distinct path.value #>> '{}' as path
            from pg_catalog.jsonb_array_elements(target -> 'allowed_paths') as path(value)
       ) as distinct_paths) <> pg_catalog.jsonb_array_length(target -> 'allowed_paths')
       or exists (
            select 1
            from pg_catalog.jsonb_array_elements(target -> 'allowed_paths') as path(value)
            where pg_catalog.jsonb_typeof(path.value) <> 'string'
               or not private.agent_safe_text(path.value #>> '{}', 1, 2048, true)
               or (path.value #>> '{}') !~ '^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$'
               or (path.value #>> '{}') = '.git'
               or (path.value #>> '{}') like '.git/%'
               or pg_catalog.strpos(path.value #>> '{}', '..') > 0
       ) then
        return false;
    end if;

    if pg_catalog.jsonb_typeof(target -> 'expected_artifacts') <> 'array'
       or pg_catalog.jsonb_array_length(target -> 'expected_artifacts')
            not between 1 and 12
       or (select pg_catalog.count(distinct item.data #>> '{}')
           from pg_catalog.jsonb_array_elements(
                target -> 'expected_artifacts'
           ) as item(data))
            <> pg_catalog.jsonb_array_length(target -> 'expected_artifacts')
       or exists (
            select 1
            from pg_catalog.jsonb_array_elements(
                target -> 'expected_artifacts'
            ) as item(data)
            where pg_catalog.jsonb_typeof(item.data) <> 'string'
               or not private.agent_safe_text(item.data #>> '{}', 3, 200, true)
       ) then
        return false;
    end if;
    if pg_catalog.jsonb_typeof(target -> 'acceptance_criteria') <> 'array'
       or pg_catalog.jsonb_array_length(target -> 'acceptance_criteria')
            not between 1 and 16
       or (select pg_catalog.count(distinct item.data #>> '{}')
           from pg_catalog.jsonb_array_elements(
                target -> 'acceptance_criteria'
           ) as item(data))
            <> pg_catalog.jsonb_array_length(target -> 'acceptance_criteria')
       or exists (
            select 1
            from pg_catalog.jsonb_array_elements(
                target -> 'acceptance_criteria'
            ) as item(data)
            where pg_catalog.jsonb_typeof(item.data) <> 'string'
               or not private.agent_safe_text(item.data #>> '{}', 3, 500, true)
       ) then
        return false;
    end if;
    if pg_catalog.jsonb_typeof(target -> 'verification_commands') <> 'array'
       or pg_catalog.jsonb_array_length(target -> 'verification_commands')
            not between 1 and 16
       or (select pg_catalog.count(distinct item.data #>> '{}')
           from pg_catalog.jsonb_array_elements(
                target -> 'verification_commands'
           ) as item(data))
            <> pg_catalog.jsonb_array_length(target -> 'verification_commands')
       or exists (
            select 1
            from pg_catalog.jsonb_array_elements(
                target -> 'verification_commands'
            ) as item(data)
            where pg_catalog.jsonb_typeof(item.data) <> 'string'
               or not private.agent_safe_text(item.data #>> '{}', 2, 500, true)
       )
       or exists (
            select 1
            from pg_catalog.jsonb_array_elements_text(
                target -> 'verification_commands'
            ) as command(value)
            where command.value ~* '(^|[[:space:]])(rm[[:space:]]+-rf|git[[:space:]]+(push|merge|reset)|gh[[:space:]]+(api|pr[[:space:]]+(create|merge))|netlify[[:space:]]+(deploy|api)|railway[[:space:]]+(up|redeploy|variable)|supabase[[:space:]]+(db|functions|branches)|curl([[:space:]]|$)|wget([[:space:]]|$))'
       ) then
        return false;
    end if;

    if pg_catalog.jsonb_typeof(target -> 'evidence') <> 'array'
       or pg_catalog.jsonb_array_length(target -> 'evidence') not between 1 and 16
       or exists (
            select 1
            from pg_catalog.jsonb_array_elements(target -> 'evidence') as evidence(value)
            where pg_catalog.jsonb_typeof(evidence.value) <> 'object'
               or not evidence.value ?& array['uri', 'sha256']
               or (select pg_catalog.count(*)
                   from pg_catalog.jsonb_object_keys(evidence.value)) <> 2
               or coalesce(evidence.value ->> 'sha256', '') !~ '^[a-f0-9]{64}$'
               or not private.agent_safe_text(
                    evidence.value ->> 'uri', 1, 2048, false
               )
               or coalesce(evidence.value ->> 'uri', '')
                    !~ '^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$'
               or pg_catalog.lower(evidence.value ->> 'uri') like 'http%'
               or evidence.value ->> 'uri' = '.git'
               or evidence.value ->> 'uri' like '.git/%'
               or pg_catalog.strpos(evidence.value ->> 'uri', '..') > 0
       ) then
        return false;
    end if;

    if pg_catalog.jsonb_typeof(target -> 'forbidden_actions') <> 'array'
       or pg_catalog.jsonb_array_length(target -> 'forbidden_actions') <> 10
       or (select pg_catalog.count(distinct action.value)
           from pg_catalog.jsonb_array_elements_text(
                target -> 'forbidden_actions'
           ) as action(value)) <> 10
       or exists (
            select 1
            from pg_catalog.jsonb_array_elements_text(
                target -> 'forbidden_actions'
            ) as action(value)
            where not (action.value = any(required_actions))
       ) then
        return false;
    end if;
    return true;
exception when others then
    return false;
end;
$$;

create table agent_runtime.agent_work_orders (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    work_order_id uuid not null,
    scope jsonb not null,
    scope_sha256 text not null check (scope_sha256 ~ '^[a-f0-9]{64}$'),
    branch_scope_key text not null check (branch_scope_key ~ '^[a-f0-9]{64}$'),
    branch_collision_key text not null check (
        branch_collision_key ~ '^[a-f0-9]{64}$'
    ),
    objective_id uuid not null,
    parent_work_order_id uuid,
    causation_id uuid not null,
    idempotency_key text not null check (
        idempotency_key ~ '^[a-z0-9][a-z0-9:._/-]{7,199}$'
    ),
    requested_by_user_id uuid not null references auth.users(id) on delete restrict,
    owner text not null check (
        owner in ('devin', 'claude_code', 'codex', 'grok_build')
    ),
    reviewer text not null check (
        reviewer in ('codex', 'claude_code', 'human_operator')
    ),
    client_id text,
    repository text not null,
    base_sha text not null check (base_sha ~ '^[a-f0-9]{40}$'),
    branch_name text not null,
    allowed_paths text[] not null check (
        pg_catalog.cardinality(allowed_paths) between 1 and 32
    ),
    title text not null,
    risk_tier text not null check (risk_tier = 'R1'),
    status text not null default 'proposed' check (
        status in (
            'proposed', 'authorized', 'claimed', 'in_progress',
            'awaiting_review', 'verified', 'approved', 'completed',
            'blocked', 'cancelled'
        )
    ),
    status_version bigint not null default 0 check (status_version >= 0),
    priority smallint not null default 0 check (priority between 0 and 3),
    created_at timestamptz not null,
    expires_at timestamptz not null,
    authorized_at timestamptz,
    finished_at timestamptz,
    last_event_sha256 text check (
        last_event_sha256 is null or last_event_sha256 ~ '^[a-f0-9]{64}$'
    ),
    updated_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, work_order_id),
    unique (workspace_id, idempotency_key),
    unique (workspace_id, scope_sha256),
    foreign key (workspace_id, parent_work_order_id)
        references agent_runtime.agent_work_orders(workspace_id, work_order_id)
        on delete restrict,
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    check (owner <> reviewer),
    check (expires_at > created_at and expires_at - created_at <= interval '14 days'),
    check (private.agent_work_order_scope_valid(scope)),
    check (scope_sha256 = private.agent_json_sha256(scope)),
    check (scope ->> 'work_order_id' = work_order_id::text),
    check (scope ->> 'objective_id' = objective_id::text),
    check (scope ->> 'causation_id' = causation_id::text),
    check (scope ->> 'idempotency_key' = idempotency_key),
    check (scope ->> 'owner' = owner and scope ->> 'reviewer' = reviewer),
    check (scope ->> 'repository' = repository),
    check (scope ->> 'base_sha' = base_sha),
    check (scope ->> 'branch_name' = branch_name),
    check (scope ->> 'title' = title),
    check (scope ->> 'risk_tier' = risk_tier),
    check (scope ->> 'client_id' is not distinct from client_id),
    check (branch_scope_key = private.agent_branch_scope_key(
        repository, branch_name, false
    )),
    check (branch_collision_key = private.agent_branch_scope_key(
        repository, branch_name, true
    )),
    check (status <> 'proposed' or authorized_at is null),
    check (
        status not in (
            'authorized', 'claimed', 'in_progress', 'awaiting_review',
            'verified', 'approved', 'completed'
        ) or authorized_at is not null
    ),
    check (
        status not in ('completed', 'blocked', 'cancelled')
        or finished_at is not null
    ),
    check (
        status in ('completed', 'blocked', 'cancelled')
        or finished_at is null
    )
);

create unique index agent_work_orders_active_branch_idx
    on agent_runtime.agent_work_orders (workspace_id, branch_collision_key)
    where status in (
        'authorized', 'claimed', 'in_progress', 'awaiting_review',
        'verified', 'approved'
    );
create index agent_work_orders_owner_queue_idx
    on agent_runtime.agent_work_orders (
        workspace_id, owner, status, priority desc, created_at, work_order_id
    ) where status in ('authorized', 'claimed', 'in_progress');
create index agent_work_orders_reviewer_queue_idx
    on agent_runtime.agent_work_orders (
        workspace_id, reviewer, status, updated_at, work_order_id
    ) where status in ('awaiting_review', 'verified');
create index agent_work_orders_operator_inbox_idx
    on agent_runtime.agent_work_orders (
        workspace_id, status, updated_at desc, work_order_id
    ) where status <> 'completed';
create index agent_work_orders_active_repo_idx
    on agent_runtime.agent_work_orders (
        workspace_id, lower(repository), status
    ) where status in (
        'authorized', 'claimed', 'in_progress', 'awaiting_review',
        'verified', 'approved'
    );

create table agent_runtime.agent_work_order_events (
    workspace_id uuid not null,
    work_order_id uuid not null,
    event_seq integer not null check (event_seq > 0),
    event_type text not null check (
        event_type in (
            'proposed', 'authorized', 'claimed', 'started',
            'result_submitted', 'verification_submitted', 'approved',
            'completed', 'blocked', 'cancelled'
        )
    ),
    from_status text,
    to_status text not null,
    actor_kind text not null check (
        actor_kind in ('human_operator', 'control_plane')
    ),
    actor_user_id uuid references auth.users(id) on delete restrict,
    causation_id uuid not null,
    receipt_id uuid,
    payload jsonb not null check (
        jsonb_typeof(payload) = 'object'
        and octet_length(payload::text) <= 32768
    ),
    previous_event_sha256 text check (
        previous_event_sha256 is null
        or previous_event_sha256 ~ '^[a-f0-9]{64}$'
    ),
    event_sha256 text not null check (event_sha256 ~ '^[a-f0-9]{64}$'),
    occurred_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, work_order_id, event_seq),
    unique (workspace_id, work_order_id, event_sha256),
    foreign key (workspace_id, work_order_id)
        references agent_runtime.agent_work_orders(workspace_id, work_order_id)
        on delete restrict,
    check ((event_seq = 1) = (previous_event_sha256 is null)),
    check ((actor_kind = 'human_operator') = (actor_user_id is not null))
);

create index agent_work_order_events_recent_idx
    on agent_runtime.agent_work_order_events (
        workspace_id, occurred_at desc, work_order_id, event_seq desc
    );

create table agent_runtime.agent_runs (
    workspace_id uuid not null,
    run_id uuid not null default gen_random_uuid(),
    work_order_id uuid not null,
    run_kind text not null check (run_kind in ('owner', 'review')),
    agent_identity text not null check (
        agent_identity in (
            'devin', 'claude_code', 'codex', 'grok_build', 'human_operator'
        )
    ),
    status text not null check (
        status in (
            'claimed', 'in_progress', 'result_submitted',
            'verification_submitted', 'abandoned'
        )
    ),
    attempt integer not null check (attempt between 1 and 5),
    locked_by text not null check (
        locked_by ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
    ),
    claimed_at timestamptz not null default statement_timestamp(),
    lease_expires_at timestamptz not null,
    started_at timestamptz,
    finished_at timestamptz,
    result_sha256 text check (
        result_sha256 is null or result_sha256 ~ '^[a-f0-9]{64}$'
    ),
    actual_cost_microusd bigint check (
        actual_cost_microusd is null or actual_cost_microusd = 0
    ),
    external_action_count integer not null default 0 check (
        external_action_count = 0
    ),
    created_at timestamptz not null default statement_timestamp(),
    updated_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, run_id),
    foreign key (workspace_id, work_order_id)
        references agent_runtime.agent_work_orders(workspace_id, work_order_id)
        on delete restrict,
    check (lease_expires_at > claimed_at),
    check (
        status not in ('result_submitted', 'verification_submitted', 'abandoned')
        or finished_at is not null
    )
);

create unique index agent_runs_one_active_kind_idx
    on agent_runtime.agent_runs (workspace_id, work_order_id, run_kind)
    where status in ('claimed', 'in_progress');
create index agent_runs_lease_idx
    on agent_runtime.agent_runs (workspace_id, lease_expires_at, run_id)
    where status in ('claimed', 'in_progress');

create table agent_runtime.agent_dispatch_outbox (
    workspace_id uuid not null,
    work_order_id uuid not null,
    target_agent text not null check (
        target_agent in ('devin', 'claude_code', 'codex', 'grok_build')
    ),
    packet jsonb not null check (
        jsonb_typeof(packet) = 'object'
        and octet_length(packet::text) <= 65536
        and packet -> 'automatic_publication' = 'false'::jsonb
        and packet -> 'max_cost_microusd' = '0'::jsonb
        and packet -> 'max_external_actions' = '0'::jsonb
    ),
    packet_sha256 text not null check (packet_sha256 ~ '^[a-f0-9]{64}$'),
    request_sha256 text check (
        request_sha256 is null or request_sha256 ~ '^[a-f0-9]{64}$'
    ),
    status text not null default 'pending' check (
        status in (
            'pending', 'claimed', 'attempt_started', 'delivered',
            'delivery_unknown', 'failed', 'cancelled'
        )
    ),
    attempts integer not null default 0 check (attempts between 0 and 3),
    max_attempts integer not null default 3 check (max_attempts = 3),
    available_at timestamptz not null default statement_timestamp(),
    locked_by text,
    locked_at timestamptz,
    lease_expires_at timestamptz,
    delivery_reference text,
    error_code text check (
        error_code is null or error_code ~ '^[a-z][a-z0-9_]{2,79}$'
    ),
    created_at timestamptz not null default statement_timestamp(),
    updated_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, work_order_id),
    foreign key (workspace_id, work_order_id)
        references agent_runtime.agent_work_orders(workspace_id, work_order_id)
        on delete restrict,
    check (packet_sha256 = private.agent_json_sha256(packet)),
    check (
        (status in ('claimed', 'attempt_started')) = (
            locked_by is not null and locked_at is not null
            and lease_expires_at is not null
        )
    ),
    check ((request_sha256 is not null) = (status in (
        'attempt_started', 'delivered', 'delivery_unknown'
    ))),
    check (status <> 'delivery_unknown' or error_code is not null)
);

create index agent_dispatch_pending_idx
    on agent_runtime.agent_dispatch_outbox (
        workspace_id, status, available_at, created_at, work_order_id
    ) where status = 'pending';
create index agent_dispatch_lease_idx
    on agent_runtime.agent_dispatch_outbox (
        workspace_id, lease_expires_at, work_order_id
    ) where status in ('claimed', 'attempt_started');
create unique index agent_dispatch_request_sha_idx
    on agent_runtime.agent_dispatch_outbox (request_sha256)
    where request_sha256 is not null;

create table agent_runtime.agent_action_receipts (
    workspace_id uuid not null,
    receipt_id uuid not null default gen_random_uuid(),
    work_order_id uuid not null,
    run_id uuid,
    receipt_kind text not null check (
        receipt_kind in (
            'authorization', 'dispatch_delivery', 'work_result',
            'verification', 'operator_decision', 'completion'
        )
    ),
    schema_version text not null check (
        schema_version in (
            'agent-authorization-receipt@1', 'agent-dispatch-receipt@1',
            'agent-work-result@1', 'agent-verification-receipt@1',
            'operator-decision@1', 'agent-completion-receipt@1'
        )
    ),
    actor_kind text not null check (
        actor_kind in (
            'human_operator', 'control_plane', 'devin', 'claude_code',
            'codex', 'grok_build'
        )
    ),
    actor_user_id uuid references auth.users(id) on delete restrict,
    payload jsonb not null check (
        jsonb_typeof(payload) = 'object'
        and octet_length(payload::text) <= 262144
    ),
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    scope_sha256 text not null check (scope_sha256 ~ '^[a-f0-9]{64}$'),
    result_sha256 text check (
        result_sha256 is null or result_sha256 ~ '^[a-f0-9]{64}$'
    ),
    verification_sha256 text check (
        verification_sha256 is null
        or verification_sha256 ~ '^[a-f0-9]{64}$'
    ),
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, receipt_id),
    unique (workspace_id, work_order_id, receipt_kind),
    foreign key (workspace_id, work_order_id)
        references agent_runtime.agent_work_orders(workspace_id, work_order_id)
        on delete restrict,
    foreign key (workspace_id, run_id)
        references agent_runtime.agent_runs(workspace_id, run_id)
        on delete restrict,
    check (payload_sha256 = private.agent_json_sha256(payload)),
    check ((actor_kind = 'human_operator') = (actor_user_id is not null)),
    check (
        (receipt_kind = 'authorization'
            and schema_version = 'agent-authorization-receipt@1')
        or (receipt_kind = 'dispatch_delivery'
            and schema_version = 'agent-dispatch-receipt@1')
        or (receipt_kind = 'work_result'
            and schema_version = 'agent-work-result@1')
        or (receipt_kind = 'verification'
            and schema_version = 'agent-verification-receipt@1')
        or (receipt_kind = 'operator_decision'
            and schema_version = 'operator-decision@1')
        or (receipt_kind = 'completion'
            and schema_version = 'agent-completion-receipt@1')
    ),
    check (payload ->> 'schema_version' = schema_version),
    check (payload ->> 'work_order_id' = work_order_id::text),
    check (payload ->> 'scope_sha256' = scope_sha256),
    check (payload -> 'automatic_publication' = 'false'::jsonb),
    check (
        receipt_kind not in ('work_result', 'verification', 'completion')
        or result_sha256 is not null
    ),
    check (
        receipt_kind not in ('work_result', 'verification', 'completion')
        or payload ->> 'result_sha256' = result_sha256
    ),
    check (
        receipt_kind not in ('verification', 'completion')
        or verification_sha256 is not null
    ),
    check (
        receipt_kind not in ('verification', 'completion')
        or payload ->> 'verification_sha256' = verification_sha256
    ),
    check (
        receipt_kind <> 'verification'
        or payload -> 'passed' = 'true'::jsonb
    )
);

create index agent_action_receipts_work_order_idx
    on agent_runtime.agent_action_receipts (
        workspace_id, work_order_id, created_at, receipt_id
    );

alter table agent_runtime.agent_work_order_events
    add constraint agent_work_order_events_receipt_fk
    foreign key (workspace_id, receipt_id)
    references agent_runtime.agent_action_receipts(workspace_id, receipt_id)
    on delete restrict;

create table agent_runtime.agent_incidents (
    workspace_id uuid not null,
    incident_id uuid not null default gen_random_uuid(),
    work_order_id uuid not null,
    run_id uuid,
    incident_code text not null check (
        incident_code ~ '^[a-z][a-z0-9_]{2,79}$'
    ),
    severity text not null check (severity in ('warning', 'critical')),
    status text not null default 'open' check (
        status in ('open', 'acknowledged', 'resolved')
    ),
    details jsonb not null check (
        jsonb_typeof(details) = 'object'
        and octet_length(details::text) <= 32768
    ),
    details_sha256 text not null check (details_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz not null default statement_timestamp(),
    resolved_at timestamptz,
    primary key (workspace_id, incident_id),
    foreign key (workspace_id, work_order_id)
        references agent_runtime.agent_work_orders(workspace_id, work_order_id)
        on delete restrict,
    foreign key (workspace_id, run_id)
        references agent_runtime.agent_runs(workspace_id, run_id)
        on delete restrict,
    check (details_sha256 = private.agent_json_sha256(details)),
    check ((status = 'resolved') = (resolved_at is not null))
);

create unique index agent_incidents_one_open_code_idx
    on agent_runtime.agent_incidents (
        workspace_id, work_order_id, incident_code
    ) where status in ('open', 'acknowledged');
create index agent_incidents_operator_idx
    on agent_runtime.agent_incidents (
        workspace_id, status, severity desc, created_at, incident_id
    ) where status <> 'resolved';

alter table agent_runtime.agent_work_orders enable row level security;
alter table agent_runtime.agent_work_orders force row level security;
alter table agent_runtime.agent_work_order_events enable row level security;
alter table agent_runtime.agent_work_order_events force row level security;
alter table agent_runtime.agent_runs enable row level security;
alter table agent_runtime.agent_runs force row level security;
alter table agent_runtime.agent_dispatch_outbox enable row level security;
alter table agent_runtime.agent_dispatch_outbox force row level security;
alter table agent_runtime.agent_action_receipts enable row level security;
alter table agent_runtime.agent_action_receipts force row level security;
alter table agent_runtime.agent_incidents enable row level security;
alter table agent_runtime.agent_incidents force row level security;

revoke all on table
    agent_runtime.agent_work_orders,
    agent_runtime.agent_work_order_events,
    agent_runtime.agent_runs,
    agent_runtime.agent_dispatch_outbox,
    agent_runtime.agent_action_receipts,
    agent_runtime.agent_incidents
from public, anon, authenticated, service_role;

create or replace function private.agent_immutable_row()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'Agent ledger receipts and events are append-only'
        using errcode = '55000';
end;
$$;

create trigger agent_work_order_events_immutable
before update or delete on agent_runtime.agent_work_order_events
for each row execute function private.agent_immutable_row();
create trigger agent_action_receipts_immutable
before update or delete on agent_runtime.agent_action_receipts
for each row execute function private.agent_immutable_row();

create or replace function private.agent_scope_immutable()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.workspace_id is distinct from old.workspace_id
       or new.work_order_id is distinct from old.work_order_id
       or new.scope is distinct from old.scope
       or new.scope_sha256 is distinct from old.scope_sha256
       or new.branch_scope_key is distinct from old.branch_scope_key
       or new.branch_collision_key is distinct from old.branch_collision_key
       or new.objective_id is distinct from old.objective_id
       or new.parent_work_order_id is distinct from old.parent_work_order_id
       or new.causation_id is distinct from old.causation_id
       or new.idempotency_key is distinct from old.idempotency_key
       or new.requested_by_user_id is distinct from old.requested_by_user_id
       or new.owner is distinct from old.owner
       or new.reviewer is distinct from old.reviewer
       or new.client_id is distinct from old.client_id
       or new.repository is distinct from old.repository
       or new.base_sha is distinct from old.base_sha
       or new.branch_name is distinct from old.branch_name
       or new.allowed_paths is distinct from old.allowed_paths
       or new.title is distinct from old.title
       or new.risk_tier is distinct from old.risk_tier
       or new.created_at is distinct from old.created_at
       or new.expires_at is distinct from old.expires_at then
        raise exception 'Agent work order immutable scope changed'
            using errcode = '55000';
    end if;
    return new;
end;
$$;

create trigger agent_work_order_scope_immutable
before update on agent_runtime.agent_work_orders
for each row execute function private.agent_scope_immutable();

create or replace function private.agent_operator_can_write(
    target_workspace_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select (select auth.uid()) is not null and exists (
        select 1
        from public.workspace_members as member
        where member.workspace_id = target_workspace_id
          and member.user_id = (select auth.uid())
          and member.status = 'active'
          and member.role in ('owner', 'admin')
    )
$$;

create or replace function private.agent_scoped_workspace_matches(
    target_workspace_id uuid,
    target_role text
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb;
begin
    begin
        claims := coalesce(
            nullif(pg_catalog.current_setting(
                'request.jwt.claims', true
            ), '')::jsonb,
            '{}'::jsonb
        );
    exception when others then
        return false;
    end;
    if claims ->> 'role' is distinct from target_role
       or coalesce(claims ->> 'workspace_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
        return false;
    end if;
    return (claims ->> 'workspace_id')::uuid = target_workspace_id;
exception when others then
    return false;
end;
$$;

create or replace function private.agent_can_read(target_workspace_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select private.agent_operator_can_write(target_workspace_id)
        or private.agent_scoped_workspace_matches(
            target_workspace_id, 'coineasy_agent_dashboard'
        )
$$;

create or replace function private.agent_append_event(
    target_workspace_id uuid,
    target_work_order_id uuid,
    target_event_type text,
    target_from_status text,
    target_to_status text,
    target_actor_kind text,
    target_actor_user_id uuid,
    target_causation_id uuid,
    target_receipt_id uuid,
    target_payload jsonb
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    next_seq integer;
    previous_hash text;
    event_hash text;
    event_body jsonb;
begin
    select coalesce(max(event.event_seq), 0) + 1,
           max(event.event_sha256) filter (
               where event.event_seq = (
                   select max(last_event.event_seq)
                   from agent_runtime.agent_work_order_events as last_event
                   where last_event.workspace_id = target_workspace_id
                     and last_event.work_order_id = target_work_order_id
               )
           )
    into next_seq, previous_hash
    from agent_runtime.agent_work_order_events as event
    where event.workspace_id = target_workspace_id
      and event.work_order_id = target_work_order_id;

    event_body := pg_catalog.jsonb_build_object(
        'actor_kind', target_actor_kind,
        'actor_user_id', case when target_actor_user_id is null
            then null else target_actor_user_id::text end,
        'causation_id', target_causation_id::text,
        'event_seq', next_seq,
        'event_type', target_event_type,
        'from_status', target_from_status,
        'payload', target_payload,
        'previous_event_sha256', previous_hash,
        'receipt_id', case when target_receipt_id is null
            then null else target_receipt_id::text end,
        'to_status', target_to_status,
        'work_order_id', target_work_order_id::text,
        'workspace_id', target_workspace_id::text
    );
    event_hash := private.agent_json_sha256(event_body);

    insert into agent_runtime.agent_work_order_events (
        workspace_id, work_order_id, event_seq, event_type, from_status,
        to_status, actor_kind, actor_user_id, causation_id, receipt_id,
        payload, previous_event_sha256, event_sha256
    ) values (
        target_workspace_id, target_work_order_id, next_seq,
        target_event_type, target_from_status, target_to_status,
        target_actor_kind, target_actor_user_id, target_causation_id,
        target_receipt_id, target_payload, previous_hash, event_hash
    );
    update agent_runtime.agent_work_orders
    set last_event_sha256 = event_hash,
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and work_order_id = target_work_order_id;
    return event_hash;
end;
$$;

create or replace function private.agent_work_order_object(
    target_workspace_id uuid,
    target_work_order_id uuid
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select pg_catalog.jsonb_build_object(
        'work_order_id', work.work_order_id,
        'objective_id', work.objective_id,
        'parent_work_order_id', work.parent_work_order_id,
        'scope_sha256', work.scope_sha256,
        'branch_scope_key', work.branch_scope_key,
        'title', work.title,
        'client_id', work.client_id,
        'repository', work.repository,
        'base_sha', work.base_sha,
        'branch_name', work.branch_name,
        'owner', work.owner,
        'reviewer', work.reviewer,
        'risk_tier', work.risk_tier,
        'status', work.status,
        'status_version', work.status_version,
        'created_at', work.created_at,
        'expires_at', work.expires_at,
        'authorized_at', work.authorized_at,
        'finished_at', work.finished_at,
        'last_event_sha256', work.last_event_sha256,
        'automatic_publication', false,
        'max_cost_microusd', 0,
        'max_external_actions', 0
    )
    from agent_runtime.agent_work_orders as work
    where work.workspace_id = target_workspace_id
      and work.work_order_id = target_work_order_id
$$;

revoke all on function private.agent_json_canonical(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.agent_json_sha256(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.agent_branch_scope_key(text, text, boolean)
from public, anon, authenticated, service_role;
revoke all on function private.agent_safe_text(text, integer, integer, boolean)
from public, anon, authenticated, service_role;
revoke all on function private.agent_work_order_scope_valid(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.agent_immutable_row()
from public, anon, authenticated, service_role;
revoke all on function private.agent_scope_immutable()
from public, anon, authenticated, service_role;
revoke all on function private.agent_operator_can_write(uuid)
from public, anon, authenticated, service_role;
revoke all on function private.agent_scoped_workspace_matches(uuid, text)
from public, anon, authenticated, service_role;
revoke all on function private.agent_can_read(uuid)
from public, anon, authenticated, service_role;
revoke all on function private.agent_append_event(
    uuid, uuid, text, text, text, text, uuid, uuid, uuid, jsonb
) from public, anon, authenticated, service_role;
revoke all on function private.agent_work_order_object(uuid, uuid)
from public, anon, authenticated, service_role;

create or replace function public.propose_agent_work_order(
    target_workspace_id uuid,
    target_scope jsonb,
    target_scope_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := (select auth.uid());
    expected_sha text;
    target_work_order_id uuid;
    existing agent_runtime.agent_work_orders%rowtype;
    inserted agent_runtime.agent_work_orders%rowtype;
    allowed_path_values text[];
begin
    if target_workspace_id is null
       or actor_id is null
       or not private.agent_operator_can_write(target_workspace_id) then
        raise exception 'Workspace owner or admin role required'
            using errcode = '42501';
    end if;
    if not private.agent_work_order_scope_valid(target_scope)
       or lower(coalesce(target_scope_sha256, '')) !~ '^[a-f0-9]{64}$' then
        raise exception 'Agent work order scope is invalid'
            using errcode = '22023';
    end if;
    expected_sha := private.agent_json_sha256(target_scope);
    if expected_sha is distinct from lower(target_scope_sha256) then
        raise exception 'Agent work order scope hash does not match'
            using errcode = '23514';
    end if;
    target_work_order_id := (target_scope ->> 'work_order_id')::uuid;

    -- Serialize proposals inside one workspace so concurrent exact replays
    -- observe the first insert and return reused=true instead of leaking a
    -- unique-constraint race.  Proposal volume is deliberately tiny in P0.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'agent_work_order_propose:' || target_workspace_id::text,
        0
    ));

    select work.* into existing
    from agent_runtime.agent_work_orders as work
    where work.workspace_id = target_workspace_id
      and (
        work.work_order_id = target_work_order_id
        or work.idempotency_key = target_scope ->> 'idempotency_key'
        or work.scope_sha256 = expected_sha
      )
    order by work.created_at, work.work_order_id
    limit 1
    for update;
    if found then
        if existing.work_order_id is distinct from target_work_order_id
           or existing.scope is distinct from target_scope
           or existing.scope_sha256 is distinct from expected_sha then
            raise exception 'Agent work order proposal conflicts with durable scope'
                using errcode = '23505';
        end if;
        return pg_catalog.jsonb_build_object(
            'schema_version', 'agent-work-order-ledger@1',
            'reused', true,
            'work_order', private.agent_work_order_object(
                target_workspace_id, target_work_order_id
            )
        );
    end if;

    select pg_catalog.array_agg(path.value order by path.ordinality)
    into allowed_path_values
    from pg_catalog.jsonb_array_elements_text(target_scope -> 'allowed_paths')
        with ordinality as path(value, ordinality);

    insert into agent_runtime.agent_work_orders (
        workspace_id, work_order_id, scope, scope_sha256, branch_scope_key,
        branch_collision_key, objective_id, parent_work_order_id, causation_id,
        idempotency_key, requested_by_user_id, owner, reviewer, client_id,
        repository, base_sha, branch_name, allowed_paths, title, risk_tier,
        status, status_version, created_at, expires_at
    ) values (
        target_workspace_id,
        target_work_order_id,
        target_scope,
        expected_sha,
        private.agent_branch_scope_key(
            target_scope ->> 'repository', target_scope ->> 'branch_name', false
        ),
        private.agent_branch_scope_key(
            target_scope ->> 'repository', target_scope ->> 'branch_name', true
        ),
        (target_scope ->> 'objective_id')::uuid,
        case when target_scope -> 'parent_work_order_id' = 'null'::jsonb
            then null else (target_scope ->> 'parent_work_order_id')::uuid end,
        (target_scope ->> 'causation_id')::uuid,
        target_scope ->> 'idempotency_key',
        actor_id,
        target_scope ->> 'owner',
        target_scope ->> 'reviewer',
        target_scope ->> 'client_id',
        target_scope ->> 'repository',
        target_scope ->> 'base_sha',
        target_scope ->> 'branch_name',
        allowed_path_values,
        target_scope ->> 'title',
        target_scope ->> 'risk_tier',
        'proposed', 0,
        (target_scope ->> 'created_at')::timestamptz,
        (target_scope ->> 'expires_at')::timestamptz
    ) returning * into inserted;

    perform private.agent_append_event(
        inserted.workspace_id, inserted.work_order_id, 'proposed', null,
        'proposed', 'human_operator', actor_id, inserted.causation_id, null,
        pg_catalog.jsonb_build_object(
            'scope_sha256', inserted.scope_sha256,
            'automatic_publication', false,
            'max_cost_microusd', 0,
            'max_external_actions', 0
        )
    );
    return pg_catalog.jsonb_build_object(
        'schema_version', 'agent-work-order-ledger@1',
        'reused', false,
        'work_order', private.agent_work_order_object(
            inserted.workspace_id, inserted.work_order_id
        )
    );
end;
$$;

create or replace function public.authorize_agent_work_order(
    target_workspace_id uuid,
    target_work_order_id uuid,
    target_scope_sha256 text,
    target_expected_status_version bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := (select auth.uid());
    work agent_runtime.agent_work_orders%rowtype;
    conflict_work_id uuid;
    authorization_payload jsonb;
    dispatch_packet jsonb;
    authorization_receipt_id uuid;
begin
    if target_workspace_id is null or target_work_order_id is null
       or actor_id is null
       or not private.agent_operator_can_write(target_workspace_id) then
        raise exception 'Workspace owner or admin role required'
            using errcode = '42501';
    end if;
    if lower(coalesce(target_scope_sha256, '')) !~ '^[a-f0-9]{64}$'
       or target_expected_status_version is null
       or target_expected_status_version < 0 then
        raise exception 'Agent authorization request is invalid'
            using errcode = '22023';
    end if;

    select current_work.* into work
    from agent_runtime.agent_work_orders as current_work
    where current_work.workspace_id = target_workspace_id
      and current_work.work_order_id = target_work_order_id
    for update;
    if not found then
        raise exception 'Agent work order does not exist' using errcode = 'P0002';
    end if;
    if work.scope_sha256 is distinct from lower(target_scope_sha256) then
        raise exception 'Agent authorization scope hash conflicts'
            using errcode = '23505';
    end if;
    if work.status = 'authorized' then
        return pg_catalog.jsonb_build_object(
            'schema_version', 'agent-work-order-ledger@1',
            'reused', true,
            'dispatch_status', (
                select dispatch.status
                from agent_runtime.agent_dispatch_outbox as dispatch
                where dispatch.workspace_id = work.workspace_id
                  and dispatch.work_order_id = work.work_order_id
            ),
            'work_order', private.agent_work_order_object(
                work.workspace_id, work.work_order_id
            )
        );
    end if;
    if work.status <> 'proposed'
       or work.status_version <> target_expected_status_version
       or work.expires_at <= statement_timestamp() then
        raise exception 'Agent work order is not authorizable'
            using errcode = '55000';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        work.workspace_id::text || ':' || pg_catalog.lower(work.repository), 0
    ));
    select other.work_order_id into conflict_work_id
    from agent_runtime.agent_work_orders as other
    where other.workspace_id = work.workspace_id
      and pg_catalog.lower(other.repository) = pg_catalog.lower(work.repository)
      and other.work_order_id <> work.work_order_id
      and other.status in (
        'authorized', 'claimed', 'in_progress', 'awaiting_review',
        'verified', 'approved'
      )
      and (
        other.branch_collision_key = work.branch_collision_key
        or exists (
            select 1
            from unnest(other.allowed_paths) as other_path(value)
            cross join unnest(work.allowed_paths) as target_path(value)
            where pg_catalog.lower(other_path.value) =
                    pg_catalog.lower(target_path.value)
               or pg_catalog.lower(other_path.value) like
                    pg_catalog.lower(target_path.value) || '/%'
               or pg_catalog.lower(target_path.value) like
                    pg_catalog.lower(other_path.value) || '/%'
        )
      )
    order by other.work_order_id
    limit 1;
    if conflict_work_id is not null then
        raise exception 'Agent work order branch or path scope conflicts'
            using errcode = '23505';
    end if;

    authorization_payload := pg_catalog.jsonb_build_object(
        'actor_user_id', actor_id::text,
        'automatic_publication', false,
        'decision', 'authorized',
        'max_cost_microusd', 0,
        'max_external_actions', 0,
        'schema_version', 'agent-authorization-receipt@1',
        'scope_sha256', work.scope_sha256,
        'work_order_id', work.work_order_id::text
    );
    insert into agent_runtime.agent_action_receipts (
        workspace_id, work_order_id, receipt_kind, schema_version,
        actor_kind, actor_user_id, payload, payload_sha256, scope_sha256
    ) values (
        work.workspace_id, work.work_order_id, 'authorization',
        'agent-authorization-receipt@1', 'human_operator', actor_id,
        authorization_payload, private.agent_json_sha256(authorization_payload),
        work.scope_sha256
    ) returning receipt_id into authorization_receipt_id;

    dispatch_packet := pg_catalog.jsonb_build_object(
        'automatic_publication', false,
        'base_sha', work.base_sha,
        'branch_name', work.branch_name,
        'max_cost_microusd', 0,
        'max_external_actions', 0,
        'owner', work.owner,
        'repository', work.repository,
        'reviewer', work.reviewer,
        'schema_version', 'agent-dispatch-packet@1',
        'scope_sha256', work.scope_sha256,
        'work_order_id', work.work_order_id::text
    );
    insert into agent_runtime.agent_dispatch_outbox (
        workspace_id, work_order_id, target_agent, packet, packet_sha256,
        status
    ) values (
        work.workspace_id, work.work_order_id, work.owner, dispatch_packet,
        private.agent_json_sha256(dispatch_packet), 'pending'
    );

    update agent_runtime.agent_work_orders
    set status = 'authorized',
        status_version = status_version + 1,
        authorized_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = work.workspace_id
      and work_order_id = work.work_order_id;
    perform private.agent_append_event(
        work.workspace_id, work.work_order_id, 'authorized', work.status,
        'authorized', 'human_operator', actor_id, work.causation_id,
        authorization_receipt_id,
        pg_catalog.jsonb_build_object(
            'scope_sha256', work.scope_sha256,
            'dispatch_status', 'pending'
        )
    );
    return pg_catalog.jsonb_build_object(
        'schema_version', 'agent-work-order-ledger@1',
        'reused', false,
        'dispatch_status', 'pending',
        'work_order', private.agent_work_order_object(
            work.workspace_id, work.work_order_id
        )
    );
end;
$$;

create or replace function public.record_agent_operator_decision(
    target_workspace_id uuid,
    target_work_order_id uuid,
    target_scope_sha256 text,
    target_expected_status_version bigint,
    target_decision text,
    target_reason_code text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := (select auth.uid());
    work agent_runtime.agent_work_orders%rowtype;
    stored_receipt agent_runtime.agent_action_receipts%rowtype;
    result_receipt agent_runtime.agent_action_receipts%rowtype;
    verification_receipt agent_runtime.agent_action_receipts%rowtype;
    decision_payload jsonb;
    decision_receipt_id uuid;
    next_status text;
begin
    if target_workspace_id is null or target_work_order_id is null
       or actor_id is null
       or not private.agent_operator_can_write(target_workspace_id) then
        raise exception 'Workspace owner or admin role required'
            using errcode = '42501';
    end if;
    if lower(coalesce(target_scope_sha256, '')) !~ '^[a-f0-9]{64}$'
       or target_expected_status_version is null
       or target_expected_status_version < 0
       or target_decision not in ('approved', 'blocked', 'cancelled')
       or coalesce(target_reason_code, '') !~ '^[a-z][a-z0-9_]{2,63}$' then
        raise exception 'Agent operator decision is invalid'
            using errcode = '22023';
    end if;

    select current_work.* into work
    from agent_runtime.agent_work_orders as current_work
    where current_work.workspace_id = target_workspace_id
      and current_work.work_order_id = target_work_order_id
    for update;
    if not found then
        raise exception 'Agent work order does not exist' using errcode = 'P0002';
    end if;
    if work.scope_sha256 is distinct from lower(target_scope_sha256) then
        raise exception 'Agent decision scope hash conflicts'
            using errcode = '23505';
    end if;

    select receipt.* into stored_receipt
    from agent_runtime.agent_action_receipts as receipt
    where receipt.workspace_id = work.workspace_id
      and receipt.work_order_id = work.work_order_id
      and receipt.receipt_kind = 'operator_decision';
    if found then
        if stored_receipt.payload ->> 'decision' is distinct from target_decision
           or stored_receipt.payload ->> 'reason_code'
                is distinct from target_reason_code then
            raise exception 'Agent operator decision conflicts'
                using errcode = '23505';
        end if;
        return pg_catalog.jsonb_build_object(
            'schema_version', 'agent-work-order-ledger@1',
            'reused', true,
            'work_order', private.agent_work_order_object(
                work.workspace_id, work.work_order_id
            )
        );
    end if;
    if work.status_version <> target_expected_status_version
       or work.expires_at <= statement_timestamp() then
        raise exception 'Agent operator decision fence is stale'
            using errcode = '55000';
    end if;

    if target_decision = 'approved' then
        if work.status <> 'verified' then
            raise exception 'Only a verified work order can be approved'
                using errcode = '23514';
        end if;
        select receipt.* into result_receipt
        from agent_runtime.agent_action_receipts as receipt
        where receipt.workspace_id = work.workspace_id
          and receipt.work_order_id = work.work_order_id
          and receipt.receipt_kind = 'work_result';
        select receipt.* into verification_receipt
        from agent_runtime.agent_action_receipts as receipt
        where receipt.workspace_id = work.workspace_id
          and receipt.work_order_id = work.work_order_id
          and receipt.receipt_kind = 'verification';
        if result_receipt.receipt_id is null
           or verification_receipt.receipt_id is null
           or result_receipt.schema_version
                is distinct from 'agent-work-result@1'
           or verification_receipt.schema_version
                is distinct from 'agent-verification-receipt@1'
           or result_receipt.actor_kind is distinct from work.owner
           or verification_receipt.actor_kind is distinct from work.reviewer
           or result_receipt.scope_sha256 is distinct from work.scope_sha256
           or verification_receipt.scope_sha256 is distinct from work.scope_sha256
           or verification_receipt.result_sha256
                is distinct from result_receipt.result_sha256
           or verification_receipt.payload -> 'passed'
                is distinct from 'true'::jsonb
           or verification_receipt.payload ->> 'independent_reviewer'
                is distinct from work.reviewer then
            raise exception 'Agent verification receipt chain is incomplete'
                using errcode = '23514';
        end if;
        next_status := 'approved';
    elsif target_decision = 'cancelled' then
        if work.status not in ('proposed', 'authorized') then
            raise exception 'Agent work order cannot be cancelled from this state'
                using errcode = '23514';
        end if;
        next_status := 'cancelled';
    else
        if work.status not in (
            'authorized', 'claimed', 'in_progress', 'awaiting_review',
            'verified', 'approved'
        ) then
            raise exception 'Agent work order cannot be blocked from this state'
                using errcode = '23514';
        end if;
        next_status := 'blocked';
    end if;

    decision_payload := pg_catalog.jsonb_build_object(
        'actor_user_id', actor_id::text,
        'automatic_publication', false,
        'decision', target_decision,
        'operator', 'human_operator',
        'reason_code', target_reason_code,
        'result_sha256', result_receipt.result_sha256,
        'schema_version', 'operator-decision@1',
        'scope_sha256', work.scope_sha256,
        'verification_receipt_id', verification_receipt.receipt_id,
        'verification_sha256', verification_receipt.verification_sha256,
        'work_order_id', work.work_order_id::text
    );
    insert into agent_runtime.agent_action_receipts (
        workspace_id, work_order_id, receipt_kind, schema_version,
        actor_kind, actor_user_id, payload, payload_sha256, scope_sha256,
        result_sha256, verification_sha256
    ) values (
        work.workspace_id, work.work_order_id, 'operator_decision',
        'operator-decision@1', 'human_operator', actor_id,
        decision_payload, private.agent_json_sha256(decision_payload),
        work.scope_sha256, result_receipt.result_sha256,
        verification_receipt.verification_sha256
    ) returning receipt_id into decision_receipt_id;

    update agent_runtime.agent_work_orders
    set status = next_status,
        status_version = status_version + 1,
        finished_at = case when next_status in ('blocked', 'cancelled')
            then statement_timestamp() else null end,
        updated_at = statement_timestamp()
    where workspace_id = work.workspace_id
      and work_order_id = work.work_order_id;
    if next_status in ('blocked', 'cancelled') then
        update agent_runtime.agent_dispatch_outbox
        set status = 'cancelled', updated_at = statement_timestamp()
        where workspace_id = work.workspace_id
          and work_order_id = work.work_order_id
          and status = 'pending';
    end if;
    perform private.agent_append_event(
        work.workspace_id, work.work_order_id, target_decision, work.status,
        next_status, 'human_operator', actor_id, work.causation_id,
        decision_receipt_id,
        pg_catalog.jsonb_build_object('reason_code', target_reason_code)
    );
    return pg_catalog.jsonb_build_object(
        'schema_version', 'agent-work-order-ledger@1',
        'reused', false,
        'work_order', private.agent_work_order_object(
            work.workspace_id, work.work_order_id
        )
    );
end;
$$;

create or replace function public.complete_agent_work_order(
    target_workspace_id uuid,
    target_work_order_id uuid,
    target_scope_sha256 text,
    target_expected_status_version bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := (select auth.uid());
    work agent_runtime.agent_work_orders%rowtype;
    result_receipt agent_runtime.agent_action_receipts%rowtype;
    verification_receipt agent_runtime.agent_action_receipts%rowtype;
    decision_receipt agent_runtime.agent_action_receipts%rowtype;
    completion_receipt agent_runtime.agent_action_receipts%rowtype;
    completion_payload jsonb;
begin
    if target_workspace_id is null or target_work_order_id is null
       or (
            not private.agent_operator_can_write(target_workspace_id)
            and not private.agent_scoped_workspace_matches(
                target_workspace_id, 'coineasy_agent_control_plane'
            )
       ) then
        raise exception 'Agent control-plane completion role required'
            using errcode = '42501';
    end if;
    if lower(coalesce(target_scope_sha256, '')) !~ '^[a-f0-9]{64}$'
       or target_expected_status_version is null
       or target_expected_status_version < 0 then
        raise exception 'Agent completion request is invalid'
            using errcode = '22023';
    end if;

    select current_work.* into work
    from agent_runtime.agent_work_orders as current_work
    where current_work.workspace_id = target_workspace_id
      and current_work.work_order_id = target_work_order_id
    for update;
    if not found then
        raise exception 'Agent work order does not exist' using errcode = 'P0002';
    end if;
    if work.scope_sha256 is distinct from lower(target_scope_sha256) then
        raise exception 'Agent completion scope hash conflicts'
            using errcode = '23505';
    end if;
    select receipt.* into completion_receipt
    from agent_runtime.agent_action_receipts as receipt
    where receipt.workspace_id = work.workspace_id
      and receipt.work_order_id = work.work_order_id
      and receipt.receipt_kind = 'completion';
    if found and work.status = 'completed' then
        return pg_catalog.jsonb_build_object(
            'schema_version', 'agent-work-order-ledger@1',
            'reused', true,
            'completion_sha256', completion_receipt.payload_sha256,
            'work_order', private.agent_work_order_object(
                work.workspace_id, work.work_order_id
            )
        );
    end if;
    if work.status <> 'approved'
       or work.status_version <> target_expected_status_version
       or work.expires_at <= statement_timestamp() then
        raise exception 'Agent work order is not completable'
            using errcode = '55000';
    end if;

    select receipt.* into result_receipt
    from agent_runtime.agent_action_receipts as receipt
    where receipt.workspace_id = work.workspace_id
      and receipt.work_order_id = work.work_order_id
      and receipt.receipt_kind = 'work_result';
    select receipt.* into verification_receipt
    from agent_runtime.agent_action_receipts as receipt
    where receipt.workspace_id = work.workspace_id
      and receipt.work_order_id = work.work_order_id
      and receipt.receipt_kind = 'verification';
    select receipt.* into decision_receipt
    from agent_runtime.agent_action_receipts as receipt
    where receipt.workspace_id = work.workspace_id
      and receipt.work_order_id = work.work_order_id
      and receipt.receipt_kind = 'operator_decision';
    if result_receipt.receipt_id is null
       or verification_receipt.receipt_id is null
       or decision_receipt.receipt_id is null
       or decision_receipt.payload ->> 'decision' <> 'approved'
       or result_receipt.scope_sha256 is distinct from work.scope_sha256
       or verification_receipt.scope_sha256 is distinct from work.scope_sha256
       or decision_receipt.scope_sha256 is distinct from work.scope_sha256
       or verification_receipt.result_sha256
            is distinct from result_receipt.result_sha256
       or decision_receipt.result_sha256
            is distinct from result_receipt.result_sha256
       or decision_receipt.verification_sha256
            is distinct from verification_receipt.verification_sha256 then
        raise exception 'Agent completion receipt chain is incomplete'
            using errcode = '23514';
    end if;

    completion_payload := pg_catalog.jsonb_build_object(
        'automatic_publication', false,
        'max_cost_microusd', 0,
        'max_external_actions', 0,
        'operator_decision_receipt_id', decision_receipt.receipt_id,
        'operator_decision_sha256', decision_receipt.payload_sha256,
        'result_sha256', result_receipt.result_sha256,
        'schema_version', 'agent-completion-receipt@1',
        'scope_sha256', work.scope_sha256,
        'verification_sha256', verification_receipt.verification_sha256,
        'work_order_id', work.work_order_id::text
    );
    insert into agent_runtime.agent_action_receipts (
        workspace_id, work_order_id, receipt_kind, schema_version,
        actor_kind, actor_user_id, payload, payload_sha256, scope_sha256,
        result_sha256, verification_sha256
    ) values (
        work.workspace_id, work.work_order_id, 'completion',
        'agent-completion-receipt@1',
        case when actor_id is null then 'control_plane' else 'human_operator' end,
        actor_id, completion_payload, private.agent_json_sha256(completion_payload),
        work.scope_sha256, result_receipt.result_sha256,
        verification_receipt.verification_sha256
    ) returning * into completion_receipt;

    update agent_runtime.agent_work_orders
    set status = 'completed',
        status_version = status_version + 1,
        finished_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = work.workspace_id
      and work_order_id = work.work_order_id;
    perform private.agent_append_event(
        work.workspace_id, work.work_order_id, 'completed', work.status,
        'completed', case when actor_id is null
            then 'control_plane' else 'human_operator' end,
        actor_id, work.causation_id, completion_receipt.receipt_id,
        pg_catalog.jsonb_build_object(
            'completion_sha256', completion_receipt.payload_sha256
        )
    );
    return pg_catalog.jsonb_build_object(
        'schema_version', 'agent-work-order-ledger@1',
        'reused', false,
        'completion_sha256', completion_receipt.payload_sha256,
        'work_order', private.agent_work_order_object(
            work.workspace_id, work.work_order_id
        )
    );
end;
$$;

create or replace function public.list_agent_operator_inbox(
    target_workspace_id uuid,
    target_limit integer default 20,
    target_before_updated_at timestamptz default null,
    target_before_work_order_id uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    result jsonb;
begin
    if target_workspace_id is null
       or target_limit not between 1 and 100
       or not private.agent_can_read(target_workspace_id)
       or ((target_before_updated_at is null)
            <> (target_before_work_order_id is null)) then
        raise exception 'Agent operator inbox request is invalid or unauthorized'
            using errcode = '42501';
    end if;
    select coalesce(pg_catalog.jsonb_agg(
        private.agent_work_order_object(
            target_workspace_id, candidate.work_order_id
        ) order by candidate.updated_at desc, candidate.work_order_id desc
    ), '[]'::jsonb)
    into result
    from (
        select work.work_order_id, work.updated_at
        from agent_runtime.agent_work_orders as work
        where work.workspace_id = target_workspace_id
          and work.status in (
            'proposed', 'authorized', 'claimed', 'in_progress',
            'awaiting_review', 'verified', 'approved', 'blocked'
          )
          and (
            target_before_updated_at is null
            or (work.updated_at, work.work_order_id)
                < (target_before_updated_at, target_before_work_order_id)
          )
        order by work.updated_at desc, work.work_order_id desc
        limit target_limit
    ) as candidate;
    return pg_catalog.jsonb_build_object(
        'schema_version', 'agent-operator-inbox@1',
        'workspace_id', target_workspace_id,
        'work_orders', result,
        'automatic_publication', false
    );
end;
$$;

create or replace function public.get_agent_work_order(
    target_workspace_id uuid,
    target_work_order_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    result jsonb;
begin
    if target_workspace_id is null or target_work_order_id is null
       or not private.agent_can_read(target_workspace_id) then
        raise exception 'Agent work order read is unauthorized'
            using errcode = '42501';
    end if;
    result := private.agent_work_order_object(
        target_workspace_id, target_work_order_id
    );
    if result is null then
        raise exception 'Agent work order does not exist' using errcode = 'P0002';
    end if;
    return pg_catalog.jsonb_build_object(
        'schema_version', 'agent-work-order-detail@1',
        'work_order', result,
        'receipts', (
            select coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                'receipt_id', receipt.receipt_id,
                'receipt_kind', receipt.receipt_kind,
                'schema_version', receipt.schema_version,
                'payload_sha256', receipt.payload_sha256,
                'scope_sha256', receipt.scope_sha256,
                'result_sha256', receipt.result_sha256,
                'verification_sha256', receipt.verification_sha256,
                'created_at', receipt.created_at
            ) order by receipt.created_at, receipt.receipt_id), '[]'::jsonb)
            from agent_runtime.agent_action_receipts as receipt
            where receipt.workspace_id = target_workspace_id
              and receipt.work_order_id = target_work_order_id
        ),
        'dispatch_status', (
            select dispatch.status
            from agent_runtime.agent_dispatch_outbox as dispatch
            where dispatch.workspace_id = target_workspace_id
              and dispatch.work_order_id = target_work_order_id
        )
    );
end;
$$;

create or replace function public.get_agent_company_dashboard(
    target_workspace_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    counts jsonb;
    run_cost bigint;
    unobserved_runs bigint;
begin
    if target_workspace_id is null
       or not private.agent_can_read(target_workspace_id) then
        raise exception 'Agent company dashboard read is unauthorized'
            using errcode = '42501';
    end if;
    select coalesce(pg_catalog.jsonb_object_agg(summary.status, summary.total),
                    '{}'::jsonb)
    into counts
    from (
        select work.status, count(*)::bigint as total
        from agent_runtime.agent_work_orders as work
        where work.workspace_id = target_workspace_id
        group by work.status
    ) as summary;
    select coalesce(sum(run.actual_cost_microusd), 0),
           count(*) filter (where run.actual_cost_microusd is null)
    into run_cost, unobserved_runs
    from agent_runtime.agent_runs as run
    where run.workspace_id = target_workspace_id;
    return pg_catalog.jsonb_build_object(
        'schema_version', 'agent-company-dashboard@1',
        'workspace_id', target_workspace_id,
        'status_counts', counts,
        'pending_dispatch_count', (
            select count(*)
            from agent_runtime.agent_dispatch_outbox as dispatch
            where dispatch.workspace_id = target_workspace_id
              and dispatch.status = 'pending'
        ),
        'open_incident_count', (
            select count(*)
            from agent_runtime.agent_incidents as incident
            where incident.workspace_id = target_workspace_id
              and incident.status <> 'resolved'
        ),
        'actual_cost_microusd', run_cost,
        'unobserved_run_count', unobserved_runs,
        'cost_observation_complete', unobserved_runs = 0,
        'max_external_actions', 0,
        'automatic_publication', false
    );
end;
$$;

revoke all on function public.propose_agent_work_order(uuid, jsonb, text)
from public, anon, authenticated, service_role;
revoke all on function public.authorize_agent_work_order(uuid, uuid, text, bigint)
from public, anon, authenticated, service_role;
revoke all on function public.record_agent_operator_decision(
    uuid, uuid, text, bigint, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.complete_agent_work_order(uuid, uuid, text, bigint)
from public, anon, authenticated, service_role;
revoke all on function public.list_agent_operator_inbox(
    uuid, integer, timestamptz, uuid
) from public, anon, authenticated, service_role;
revoke all on function public.get_agent_work_order(uuid, uuid)
from public, anon, authenticated, service_role;
revoke all on function public.get_agent_company_dashboard(uuid)
from public, anon, authenticated, service_role;

grant execute on function public.propose_agent_work_order(uuid, jsonb, text)
to authenticated;
grant execute on function public.authorize_agent_work_order(uuid, uuid, text, bigint)
to authenticated;
grant execute on function public.record_agent_operator_decision(
    uuid, uuid, text, bigint, text, text
) to authenticated;
grant execute on function public.complete_agent_work_order(uuid, uuid, text, bigint)
to authenticated;
grant execute on function public.list_agent_operator_inbox(
    uuid, integer, timestamptz, uuid
) to authenticated;
grant execute on function public.get_agent_work_order(uuid, uuid)
to authenticated;
grant execute on function public.get_agent_company_dashboard(uuid)
to authenticated;

commit;
