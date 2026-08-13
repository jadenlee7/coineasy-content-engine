-- Transactional security/state-machine smoke for Buzz Operations Agent v1.
begin;

do $test$
begin
    if has_table_privilege(
        'coineasy_buzz_operations_worker',
        'agent_runtime.buzz_operations_tasks', 'select'
    ) or has_table_privilege(
        'coineasy_buzz_operations_worker',
        'agent_runtime.buzz_operations_commands', 'insert'
    ) then
        raise exception 'Buzz operations scoped role leaked table access';
    end if;
    if not exists (
        select 1 from pg_catalog.pg_class as relation
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'agent_runtime'
          and relation.relname in (
              'buzz_operations_tasks', 'buzz_operations_commands'
          )
          and relation.relrowsecurity and relation.relforcerowsecurity
        group by namespace.nspname
        having count(*) = 2
    ) then
        raise exception 'Buzz operations tables do not force RLS';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'fa000000-0000-4000-8000-000000000001',
    'Buzz Operations Security', 'buzz-operations-security', null
);
insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
) values (
    'fa000000-0000-4000-8000-000000000001',
    'origintrail', 'OriginTrail', true, null
);

do $test$
declare
    workspace constant uuid := 'fa000000-0000-4000-8000-000000000001';
    channel constant uuid := 'fb000000-0000-4000-8000-000000000001';
    reviewer constant text := repeat('a', 64);
    start_epoch constant bigint := 1700000000;
    created_epoch bigint := extract(epoch from statement_timestamp())::bigint - 10;
    event_id text := repeat('1', 64);
    plan_event_id text := repeat('2', 64);
    next_event_id text := repeat('3', 64);
    hold_event_id text := repeat('4', 64);
    plan_relay_event_id text := repeat('5', 64);
    response_relay_event_id text := repeat('6', 64);
    request_sha text := repeat('7', 64);
    command_sha text;
    result jsonb;
    plan_result jsonb;
    task uuid;
    before_publications integer;
    before_approvals integer;
    before_jobs integer;
    before_runs integer;
    before_members integer;
begin
    select count(*) into before_publications from public.publications;
    select count(*) into before_approvals from public.approvals;
    select count(*) into before_jobs from agent_runtime.batch_jobs;
    select count(*) into before_runs from agent_runtime.batch_runs;
    select count(*) into before_members from agent_runtime.batch_members;

    command_sha := private.origintrail_buzz_operations_command_sha256(
        'origintrail-buzz-operations@1', channel, event_id, reviewer,
        'status', created_epoch, null
    );
    result := public.record_origintrail_buzz_operations_command(
        workspace, channel, event_id, reviewer,
        'origintrail-buzz-operations@1', start_epoch, 'status', command_sha,
        created_epoch, null
    );
    if result->>'status' <> 'pending'
       or (result->>'reused')::boolean
       or result->>'task_id' is not null
       or position('자동 발행: OFF' in result->>'message') = 0 then
        raise exception 'Status command was not atomically enqueued';
    end if;
    result := public.record_origintrail_buzz_operations_command(
        workspace, channel, event_id, reviewer,
        'origintrail-buzz-operations@1', start_epoch, 'status', command_sha,
        created_epoch, null
    );
    if not (result->>'reused')::boolean or (
        select count(*) from agent_runtime.buzz_operations_commands
        where workspace_id = workspace and command_event_id = event_id
    ) <> 1 then
        raise exception 'Exact command replay duplicated durable state';
    end if;

    command_sha := private.origintrail_buzz_operations_command_sha256(
        'origintrail-buzz-operations@1', channel, plan_event_id, reviewer,
        'plan_today', created_epoch + 1, null
    );
    plan_result := public.record_origintrail_buzz_operations_command(
        workspace, channel, plan_event_id, reviewer,
        'origintrail-buzz-operations@1', start_epoch, 'plan_today', command_sha,
        created_epoch + 1, null
    );
    task := (plan_result->>'task_id')::uuid;
    if task is null or (
        select count(*) from agent_runtime.buzz_operations_tasks
        where workspace_id = workspace and task_id = task and status = 'pending'
    ) <> 1 then
        raise exception 'Daily-plan task was not created exactly once';
    end if;

    command_sha := private.origintrail_buzz_operations_command_sha256(
        'origintrail-buzz-operations@1', channel, next_event_id, reviewer,
        'next_task', created_epoch + 2, null
    );
    result := public.record_origintrail_buzz_operations_command(
        workspace, channel, next_event_id, reviewer,
        'origintrail-buzz-operations@1', start_epoch, 'next_task', command_sha,
        created_epoch + 2, null
    );
    if (result->>'task_id')::uuid <> task then
        raise exception 'Next-task command did not select the pending plan';
    end if;

    result := public.claim_origintrail_buzz_operations_response(
        workspace, plan_event_id, 'origintrail-operations:test', 180
    );
    if result->>'status' <> 'claimed'
       or not (result->>'claim_granted')::boolean then
        raise exception 'Plan response claim was not granted';
    end if;
    result := public.mark_origintrail_buzz_operations_response_attempt(
        workspace, plan_event_id, 'origintrail-operations:test',
        plan_result->>'message_sha256', request_sha
    );
    if not (result->>'authorized_once')::boolean then
        raise exception 'First response attempt was not authorized';
    end if;
    result := public.mark_origintrail_buzz_operations_response_attempt(
        workspace, plan_event_id, 'origintrail-operations:test',
        plan_result->>'message_sha256', request_sha
    );
    if (result->>'authorized_once')::boolean
       or not (result->>'reused')::boolean then
        raise exception 'Attempt replay gained a second relay authorization';
    end if;
    perform public.complete_origintrail_buzz_operations_response(
        workspace, plan_event_id, 'origintrail-operations:test', request_sha,
        plan_relay_event_id, false
    );

    command_sha := private.origintrail_buzz_operations_command_sha256(
        'origintrail-buzz-operations@1', channel, hold_event_id, reviewer,
        'hold', created_epoch + 3, plan_relay_event_id
    );
    result := public.record_origintrail_buzz_operations_command(
        workspace, channel, hold_event_id, reviewer,
        'origintrail-buzz-operations@1', start_epoch, 'hold', command_sha,
        created_epoch + 3, plan_relay_event_id
    );
    if (result->>'task_id')::uuid <> task or (
        select status from agent_runtime.buzz_operations_tasks
        where workspace_id = workspace and task_id = task
    ) <> 'held' then
        raise exception 'Direct-reply hold did not transition its exact task';
    end if;

    -- Prove an expired post-attempt lease becomes terminal unknown and cannot
    -- be claimed or returned to pending.
    result := public.claim_origintrail_buzz_operations_response(
        workspace, event_id, 'origintrail-operations:test', 180
    );
    perform public.mark_origintrail_buzz_operations_response_attempt(
        workspace, event_id, 'origintrail-operations:test',
        result->>'message_sha256', response_relay_event_id
    );
    update agent_runtime.buzz_operations_commands
    set locked_at = statement_timestamp() - interval '2 minutes',
        lease_expires_at = statement_timestamp() - interval '1 second'
    where workspace_id = workspace and command_event_id = event_id;
    perform public.reconcile_origintrail_buzz_operations_leases(workspace, 100);
    if (
        select response_status from agent_runtime.buzz_operations_commands
        where workspace_id = workspace and command_event_id = event_id
    ) <> 'delivery_unknown'
       or public.claim_origintrail_buzz_operations_response(
            workspace, event_id, 'origintrail-operations:test2', 180
          ) is not null then
        raise exception 'Unknown response was automatically requeued';
    end if;
    perform public.complete_origintrail_buzz_operations_response(
        workspace, event_id, 'origintrail-operations:reconcile',
        response_relay_event_id, repeat('8', 64), true
    );

    if (select count(*) from public.publications) <> before_publications
       or (select count(*) from public.approvals) <> before_approvals
       or (select count(*) from agent_runtime.batch_jobs) <> before_jobs
       or (select count(*) from agent_runtime.batch_runs) <> before_runs
       or (select count(*) from agent_runtime.batch_members) <> before_members then
        raise exception 'Operations flow mutated publication, approval, or Batch state';
    end if;
end
$test$;

rollback;
