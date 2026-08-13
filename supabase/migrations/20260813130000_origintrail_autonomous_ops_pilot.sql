-- Staging-first, propose-only autonomous operations ledger.  The observation
-- RPC reads existing operational state; the record RPC may create only an
-- immutable observation and a non-executable proposal.

begin;

create table agent_runtime.autonomous_ops_observations (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    observation_id uuid not null default gen_random_uuid(),
    protocol_version text not null check (
        protocol_version = 'origintrail-autonomous-ops@1'
    ),
    observation_date_kst date not null,
    snapshot_sha256 text not null check (snapshot_sha256 ~ '^[a-f0-9]{64}$'),
    metrics jsonb not null check (
        jsonb_typeof(metrics) = 'object'
        and octet_length(metrics::text) <= 4096
    ),
    observed_at timestamptz not null,
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, observation_id),
    unique (workspace_id, snapshot_sha256)
);

create table agent_runtime.autonomous_ops_tasks (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    task_id uuid not null default gen_random_uuid(),
    observation_id uuid not null,
    incident_key text not null check (incident_key ~ '^[a-f0-9]{64}$'),
    client_id text not null default 'origintrail' check (client_id = 'origintrail'),
    category text not null check (category in (
        'unexpected_publication', 'batch_cost_overage', 'batch_failed',
        'batch_stale', 'buzz_delivery_unknown', 'buzz_delivery_failed',
        'review_ack_unknown', 'operations_response_unknown'
    )),
    severity text not null check (severity in ('medium', 'high', 'critical')),
    title_ko text not null check (
        octet_length(title_ko) between 1 and 240
        and title_ko = btrim(title_ko)
        and title_ko !~ '[[:cntrl:]]'
    ),
    summary_ko text not null check (
        octet_length(summary_ko) between 1 and 1200
        and summary_ko = btrim(summary_ko)
        and summary_ko !~ '[[:cntrl:]]'
    ),
    steps_ko jsonb not null check (
        jsonb_typeof(steps_ko) = 'array'
        and jsonb_array_length(steps_ko) between 1 and 5
        and octet_length(steps_ko::text) <= 2400
    ),
    status text not null default 'proposed' check (status = 'proposed'),
    execution_mode text not null check (execution_mode = 'propose_only'),
    automatic_execution boolean not null default false check (not automatic_execution),
    automatic_publication boolean not null default false check (not automatic_publication),
    external_writes boolean not null default false check (not external_writes),
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, task_id),
    unique (workspace_id, incident_key),
    foreign key (workspace_id, observation_id)
        references agent_runtime.autonomous_ops_observations(
            workspace_id, observation_id
        ) on delete restrict
);

alter table agent_runtime.autonomous_ops_observations enable row level security;
alter table agent_runtime.autonomous_ops_observations force row level security;
alter table agent_runtime.autonomous_ops_tasks enable row level security;
alter table agent_runtime.autonomous_ops_tasks force row level security;
revoke all on table agent_runtime.autonomous_ops_observations
from public, anon, authenticated, service_role;
revoke all on table agent_runtime.autonomous_ops_tasks
from public, anon, authenticated, service_role;

create or replace function agent_runtime.reject_autonomous_ops_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'Autonomous Ops evidence is immutable' using errcode = '23505';
end;
$$;

revoke all on function agent_runtime.reject_autonomous_ops_mutation()
from public, anon, authenticated, service_role;

create trigger autonomous_ops_observations_immutable
before update or delete on agent_runtime.autonomous_ops_observations
for each row execute function agent_runtime.reject_autonomous_ops_mutation();
create trigger autonomous_ops_tasks_immutable
before update or delete on agent_runtime.autonomous_ops_tasks
for each row execute function agent_runtime.reject_autonomous_ops_mutation();

create or replace function private.origintrail_autonomous_ops_metrics(
    target_workspace_id uuid
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    with bounds as (
        select (
            date_trunc('day', statement_timestamp() at time zone 'Asia/Seoul')
            at time zone 'Asia/Seoul'
        ) as day_start
    )
    select jsonb_build_object(
        'actual_cost_microusd', coalesce((
            select sum(job.actual_cost_microusd)
            from agent_runtime.batch_jobs as job
            where job.workspace_id = target_workspace_id
              and job.client_id = 'origintrail'
              and job.actual_cost_microusd is not null
        ), 0),
        'batch_failed_count', (
            select count(*) from agent_runtime.batch_jobs as job, bounds
            where job.workspace_id = target_workspace_id
              and job.client_id = 'origintrail' and job.status = 'failed'
              and job.finished_at >= bounds.day_start
        ),
        'batch_stale_count', (
            select count(*) from agent_runtime.batch_jobs as job
            where job.workspace_id = target_workspace_id
              and job.client_id = 'origintrail'
              and job.status in (
                  'queued', 'claimed', 'submitted', 'in_progress', 'retry_wait'
              )
              and job.updated_at < statement_timestamp() - interval '2 hours'
        ),
        'buzz_delivery_failed_count', (
            select count(*) from agent_runtime.buzz_delivery_receipts as receipt
            where receipt.workspace_id = target_workspace_id
              and receipt.client_id = 'origintrail' and receipt.status = 'failed'
        ),
        'buzz_delivery_unknown_count', (
            select count(*) from agent_runtime.buzz_delivery_receipts as receipt
            where receipt.workspace_id = target_workspace_id
              and receipt.client_id = 'origintrail'
              and receipt.status = 'delivery_unknown'
        ),
        'cost_overage_count', (
            select count(*)
            from agent_runtime.batch_cost_overage_incidents as incident
            where incident.workspace_id = target_workspace_id
              and incident.resolution_status = 'unresolved'
        ),
        'nonterminal_batch_count', (
            select count(*) from agent_runtime.batch_jobs as job
            where job.workspace_id = target_workspace_id
              and job.client_id = 'origintrail'
              and job.status in (
                  'queued', 'claimed', 'submitted', 'in_progress', 'retry_wait'
              )
        ),
        'operations_response_unknown_count', (
            select count(*)
            from agent_runtime.buzz_operations_commands as command
            where command.workspace_id = target_workspace_id
              and command.response_status = 'delivery_unknown'
        ),
        'review_ack_unknown_count', (
            select count(*)
            from agent_runtime.buzz_review_ack_receipts as receipt
            where receipt.workspace_id = target_workspace_id
              and receipt.status = 'delivery_unknown'
        ),
        'unexpected_publication_count', (
            select count(*) from public.publications as publication, bounds
            where publication.workspace_id = target_workspace_id
              and publication.client_id = 'origintrail'
              and publication.created_at >= bounds.day_start
        )
    )
$$;

create or replace function private.origintrail_autonomous_ops_snapshot_sha256(
    target_workspace_id uuid,
    target_observation_date_kst date,
    target_metrics jsonb
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select encode(extensions.digest(
        pg_catalog.convert_to('coineasy-autonomous-ops-snapshot', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to('origintrail-autonomous-ops@1', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_workspace_id::text, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_observation_date_kst::text, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'actual_cost_microusd', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'batch_failed_count', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'batch_stale_count', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'buzz_delivery_failed_count', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'buzz_delivery_unknown_count', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'cost_overage_count', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'nonterminal_batch_count', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'operations_response_unknown_count', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'review_ack_unknown_count', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_metrics ->> 'unexpected_publication_count', 'UTF8'),
        'sha256'
    ), 'hex')
$$;

create or replace function private.origintrail_autonomous_ops_incident_key(
    target_workspace_id uuid,
    target_observation_date_kst date,
    target_category text
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select encode(extensions.digest(
        pg_catalog.convert_to('coineasy-autonomous-ops-incident', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to('origintrail-autonomous-ops@1', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_workspace_id::text, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_observation_date_kst::text, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_category, 'UTF8'),
        'sha256'
    ), 'hex')
$$;

revoke all on function private.origintrail_autonomous_ops_metrics(uuid)
from public, anon, authenticated, service_role;
revoke all on function private.origintrail_autonomous_ops_snapshot_sha256(
    uuid, date, jsonb
) from public, anon, authenticated, service_role;
revoke all on function private.origintrail_autonomous_ops_incident_key(
    uuid, date, text
) from public, anon, authenticated, service_role;

create or replace function public.observe_origintrail_autonomous_ops(
    target_workspace_id uuid,
    target_protocol_version text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    metrics jsonb;
    observed_at timestamptz := statement_timestamp();
    day_kst date := (statement_timestamp() at time zone 'Asia/Seoul')::date;
begin
    if target_protocol_version <> 'origintrail-autonomous-ops@1'
       or not exists (
           select 1 from public.workspace_clients as client
           where client.workspace_id = target_workspace_id
             and client.client_id = 'origintrail' and client.active
       ) then
        raise exception 'Autonomous Ops observation input is invalid'
            using errcode = '22023';
    end if;
    metrics := private.origintrail_autonomous_ops_metrics(target_workspace_id);
    return jsonb_build_object(
        'workspace_id', target_workspace_id,
        'protocol_version', target_protocol_version,
        'observed_at_epoch', extract(epoch from observed_at)::bigint,
        'observation_date_kst', day_kst::text,
        'snapshot_sha256', private.origintrail_autonomous_ops_snapshot_sha256(
            target_workspace_id, day_kst, metrics
        )
    ) || metrics;
end;
$$;

create or replace function public.record_origintrail_autonomous_ops_plan(
    target_workspace_id uuid,
    target_protocol_version text,
    target_snapshot_sha256 text,
    target_incident_key text,
    target_category text,
    target_severity text,
    target_title_ko text,
    target_summary_ko text,
    target_steps_ko jsonb,
    target_execution_mode text,
    target_automatic_publication boolean,
    target_external_writes boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    observed jsonb;
    metrics jsonb;
    day_kst date;
    observation agent_runtime.autonomous_ops_observations%rowtype;
    task agent_runtime.autonomous_ops_tasks%rowtype;
    was_reused boolean := false;
begin
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            target_workspace_id::text || target_incident_key, 0
        )
    );
    observed := public.observe_origintrail_autonomous_ops(
        target_workspace_id, target_protocol_version
    );
    day_kst := (observed ->> 'observation_date_kst')::date;
    metrics := observed
        - 'workspace_id' - 'protocol_version' - 'observed_at_epoch'
        - 'observation_date_kst' - 'snapshot_sha256';
    if target_snapshot_sha256 is distinct from observed ->> 'snapshot_sha256'
       or target_incident_key is distinct from
            private.origintrail_autonomous_ops_incident_key(
                target_workspace_id, day_kst, target_category
            )
       or target_category not in (
            'unexpected_publication', 'batch_cost_overage', 'batch_failed',
            'batch_stale', 'buzz_delivery_unknown', 'buzz_delivery_failed',
            'review_ack_unknown', 'operations_response_unknown'
       )
       or (case target_category
            when 'unexpected_publication' then
                (metrics ->> 'unexpected_publication_count')::bigint
            when 'batch_cost_overage' then
                (metrics ->> 'cost_overage_count')::bigint
            when 'batch_failed' then
                (metrics ->> 'batch_failed_count')::bigint
            when 'batch_stale' then
                (metrics ->> 'batch_stale_count')::bigint
            when 'buzz_delivery_unknown' then
                (metrics ->> 'buzz_delivery_unknown_count')::bigint
            when 'buzz_delivery_failed' then
                (metrics ->> 'buzz_delivery_failed_count')::bigint
            when 'review_ack_unknown' then
                (metrics ->> 'review_ack_unknown_count')::bigint
            when 'operations_response_unknown' then
                (metrics ->> 'operations_response_unknown_count')::bigint
            else 0
          end) <= 0
       or target_severity is distinct from (case target_category
            when 'unexpected_publication' then 'critical'
            when 'batch_cost_overage' then 'critical'
            when 'batch_failed' then 'high'
            when 'batch_stale' then 'high'
            when 'buzz_delivery_unknown' then 'high'
            else 'medium'
          end)
       or target_execution_mode <> 'propose_only'
       or target_automatic_publication is distinct from false
       or target_external_writes is distinct from false
       or jsonb_typeof(target_steps_ko) <> 'array'
       or jsonb_array_length(target_steps_ko) not between 1 and 5
       or exists (
           select 1 from jsonb_array_elements(target_steps_ko) as step(value)
           where jsonb_typeof(step.value) <> 'string'
              or octet_length(step.value #>> '{}') not between 1 and 600
       ) then
        raise exception 'Autonomous Ops plan input is invalid'
            using errcode = '22023';
    end if;
    insert into agent_runtime.autonomous_ops_observations (
        workspace_id, protocol_version, observation_date_kst,
        snapshot_sha256, metrics, observed_at
    ) values (
        target_workspace_id, target_protocol_version, day_kst,
        target_snapshot_sha256, metrics,
        pg_catalog.to_timestamp((observed ->> 'observed_at_epoch')::bigint)
    ) on conflict (workspace_id, snapshot_sha256) do nothing;
    select * into observation
    from agent_runtime.autonomous_ops_observations as item
    where item.workspace_id = target_workspace_id
      and item.snapshot_sha256 = target_snapshot_sha256;

    select * into task
    from agent_runtime.autonomous_ops_tasks as item
    where item.workspace_id = target_workspace_id
      and item.incident_key = target_incident_key;
    if found then
        was_reused := true;
        if task.category is distinct from target_category
           or task.severity is distinct from target_severity
           or task.title_ko is distinct from target_title_ko
           or task.summary_ko is distinct from target_summary_ko
           or task.steps_ko is distinct from target_steps_ko then
            raise exception 'Autonomous Ops proposal conflicts with existing task'
                using errcode = '23505';
        end if;
    else
        insert into agent_runtime.autonomous_ops_tasks (
            workspace_id, observation_id, incident_key, category, severity,
            title_ko, summary_ko, steps_ko, execution_mode,
            automatic_publication, external_writes
        ) values (
            target_workspace_id, observation.observation_id,
            target_incident_key, target_category, target_severity,
            target_title_ko, target_summary_ko, target_steps_ko,
            target_execution_mode, false, false
        ) returning * into task;
    end if;
    return jsonb_build_object(
        'workspace_id', task.workspace_id,
        'task_id', task.task_id,
        'incident_key', task.incident_key,
        'category', task.category,
        'severity', task.severity,
        'title_ko', task.title_ko,
        'summary_ko', task.summary_ko,
        'steps_ko', task.steps_ko,
        'status', task.status,
        'reused', was_reused,
        'automatic_execution', task.automatic_execution
    );
end;
$$;

revoke all on function public.observe_origintrail_autonomous_ops(uuid, text)
from public, anon, authenticated;
revoke all on function public.record_origintrail_autonomous_ops_plan(
    uuid, text, text, text, text, text, text, text, jsonb, text, boolean, boolean
) from public, anon, authenticated;
grant execute on function public.observe_origintrail_autonomous_ops(uuid, text)
to service_role;
grant execute on function public.record_origintrail_autonomous_ops_plan(
    uuid, text, text, text, text, text, text, text, jsonb, text, boolean, boolean
) to service_role;

commit;
