-- Durable command/task/response control plane for Buzz Operations Agent v1.
-- This migration creates no schedule and performs no provider/publication I/O.

begin;

create or replace function private.origintrail_buzz_operations_sha256(
    target_value text
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select encode(extensions.digest(
        pg_catalog.convert_to(target_value, 'UTF8'), 'sha256'
    ), 'hex')
$$;

revoke all on function private.origintrail_buzz_operations_sha256(text)
from public, anon, authenticated, service_role;

create or replace function private.origintrail_buzz_operations_command_sha256(
    target_protocol_version text,
    target_channel_id uuid,
    target_command_event_id text,
    target_reviewer_pubkey text,
    target_command text,
    target_command_created_at_epoch bigint,
    target_reply_to_event_id text
)
returns text
language sql
immutable
set search_path = ''
as $$
    select encode(extensions.digest(
        pg_catalog.convert_to('coineasy-buzz-operations-command', 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_protocol_version, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_channel_id::text, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_command_event_id, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_reviewer_pubkey, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_command, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(target_command_created_at_epoch::text, 'UTF8')
        || decode('00', 'hex')
        || pg_catalog.convert_to(coalesce(target_reply_to_event_id, ''), 'UTF8'),
        'sha256'
    ), 'hex')
$$;

revoke all on function private.origintrail_buzz_operations_command_sha256(
    text, uuid, text, text, text, bigint, text
) from public, anon, authenticated, service_role;

create table agent_runtime.buzz_operations_tasks (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    task_id uuid not null default gen_random_uuid(),
    client_id text not null default 'origintrail' check (client_id = 'origintrail'),
    task_type text not null check (task_type = 'daily_plan'),
    task_day date not null,
    status text not null default 'pending'
        check (status in ('pending', 'held', 'completed')),
    created_by_command_event_id text not null
        check (created_by_command_event_id ~ '^[a-f0-9]{64}$'),
    held_by_command_event_id text
        check (held_by_command_event_id is null
            or held_by_command_event_id ~ '^[a-f0-9]{64}$'),
    created_at timestamptz not null default statement_timestamp(),
    updated_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, task_id),
    unique (workspace_id, task_type, task_day),
    check ((status = 'held') = (held_by_command_event_id is not null))
);

create index buzz_operations_tasks_queue_idx
    on agent_runtime.buzz_operations_tasks (
        workspace_id, status, task_day, created_at, task_id
    );

alter table agent_runtime.buzz_operations_tasks enable row level security;
alter table agent_runtime.buzz_operations_tasks force row level security;
revoke all on table agent_runtime.buzz_operations_tasks
from public, anon, authenticated, service_role;

create table agent_runtime.buzz_operations_commands (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    command_event_id text not null check (command_event_id ~ '^[a-f0-9]{64}$'),
    channel_id uuid not null,
    reviewer_pubkey text not null check (reviewer_pubkey ~ '^[a-f0-9]{64}$'),
    protocol_version text not null
        check (protocol_version = 'origintrail-buzz-operations@1'),
    command text not null
        check (command in ('status', 'plan_today', 'next_task', 'hold')),
    command_sha256 text not null check (command_sha256 ~ '^[a-f0-9]{64}$'),
    command_created_at timestamptz not null,
    reply_to_event_id text check (
        reply_to_event_id is null or reply_to_event_id ~ '^[a-f0-9]{64}$'
    ),
    thread_root_event_id text not null
        check (thread_root_event_id ~ '^[a-f0-9]{64}$'),
    task_id uuid,
    response_template_version text not null check (
        response_template_version = 'origintrail-buzz-operations-response@1'
    ),
    response_message text not null,
    response_message_sha256 text not null
        check (response_message_sha256 ~ '^[a-f0-9]{64}$'),
    response_request_sha256 text check (
        response_request_sha256 is null
        or response_request_sha256 ~ '^[a-f0-9]{64}$'
    ),
    response_status text not null check (
        response_status in (
            'pending', 'claimed', 'attempt_started', 'delivered',
            'delivery_unknown', 'failed'
        )
    ),
    attempts integer not null default 0 check (attempts between 0 and 3),
    max_attempts integer not null default 3 check (max_attempts = 3),
    available_at timestamptz not null default statement_timestamp(),
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
    response_relay_event_id text check (
        response_relay_event_id is null
        or response_relay_event_id ~ '^[a-f0-9]{64}$'
    ),
    delivered_at timestamptz,
    error_code text check (
        error_code is null or error_code ~ '^[a-z][a-z0-9_]{0,79}$'
    ),
    created_at timestamptz not null default statement_timestamp(),
    updated_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, command_event_id),
    unique (response_relay_event_id),
    foreign key (workspace_id, task_id)
        references agent_runtime.buzz_operations_tasks(workspace_id, task_id)
        on delete restrict,
    check (
        octet_length(response_message) between 1 and 1024
        and position('@' in response_message) = 0
        and position('nostr:npub1' in lower(response_message)) = 0
        and response_message_sha256 =
            private.origintrail_buzz_operations_sha256(response_message)
    ),
    check ((command = 'hold') = (reply_to_event_id is not null)),
    check (
        (response_status in ('claimed', 'attempt_started')) = (
            locked_by is not null
            and locked_at is not null
            and lease_expires_at is not null
        )
    ),
    check (
        response_status not in ('claimed', 'attempt_started')
        or lease_expires_at > locked_at
    ),
    check (
        (delivery_started_at is null) = (delivery_attempt_id is null)
        and (delivery_started_at is null) = (attempt_worker_id is null)
    ),
    check (
        response_status not in ('attempt_started', 'delivered', 'delivery_unknown')
        or delivery_started_at is not null
    ),
    check (
        response_status not in ('pending', 'claimed', 'failed')
        or delivery_started_at is null
    ),
    check (
        (response_request_sha256 is not null) =
            (response_status in ('attempt_started', 'delivered', 'delivery_unknown'))
    ),
    check (
        (response_status = 'delivered') =
            (response_relay_event_id is not null and delivered_at is not null)
    ),
    check (
        response_status = 'delivered'
        or (response_relay_event_id is null and delivered_at is null)
    ),
    check (
        response_status not in ('delivery_unknown', 'failed')
        or error_code is not null
    )
);

create index buzz_operations_response_queue_idx
    on agent_runtime.buzz_operations_commands (
        workspace_id, response_status, available_at, lease_expires_at,
        command_created_at, command_event_id
    ) where response_status in ('pending', 'claimed', 'attempt_started');

alter table agent_runtime.buzz_operations_commands enable row level security;
alter table agent_runtime.buzz_operations_commands force row level security;
revoke all on table agent_runtime.buzz_operations_commands
from public, anon, authenticated, service_role;

create or replace function private.origintrail_buzz_operations_response_object(
    target_workspace_id uuid,
    target_command_event_id text,
    target_claim_granted boolean,
    target_reused boolean,
    target_authorized_once boolean default false
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object(
        'workspace_id', command_row.workspace_id,
        'command_event_id', command_row.command_event_id,
        'channel_id', command_row.channel_id,
        'reply_to_event_id', command_row.command_event_id,
        'thread_root_event_id', command_row.thread_root_event_id,
        'command', command_row.command,
        'task_id', command_row.task_id,
        'message', command_row.response_message,
        'message_sha256', command_row.response_message_sha256,
        'status', command_row.response_status,
        'claim_granted', target_claim_granted,
        'reused', target_reused,
        'authorized_once', target_authorized_once,
        'request_sha256', command_row.response_request_sha256,
        'delivery_started_at_epoch', case
            when command_row.delivery_started_at is null then null
            else extract(epoch from command_row.delivery_started_at)::bigint
        end,
        'relay_event_id', command_row.response_relay_event_id
    )
    from agent_runtime.buzz_operations_commands as command_row
    where command_row.workspace_id = target_workspace_id
      and command_row.command_event_id = target_command_event_id
$$;

revoke all on function private.origintrail_buzz_operations_response_object(
    uuid, text, boolean, boolean, boolean
) from public, anon, authenticated, service_role;

create or replace function public.record_origintrail_buzz_operations_command(
    target_workspace_id uuid,
    target_channel_id uuid,
    target_command_event_id text,
    target_reviewer_pubkey text,
    target_protocol_version text,
    target_protocol_start_epoch bigint,
    target_command text,
    target_command_sha256 text,
    target_command_created_at_epoch bigint,
    target_reply_to_event_id text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    existing agent_runtime.buzz_operations_commands%rowtype;
    selected_task agent_runtime.buzz_operations_tasks%rowtype;
    expected_sha text;
    response_message text;
    selected_thread_root_event_id text;
    pending_count integer;
    held_count integer;
    kst_day date;
begin
    if target_workspace_id is null
       or target_channel_id is null
       or target_command_event_id is null
       or target_command_event_id !~ '^[a-f0-9]{64}$'
       or target_reviewer_pubkey is null
       or target_reviewer_pubkey !~ '^[a-f0-9]{64}$'
       or target_protocol_version is distinct from 'origintrail-buzz-operations@1'
       or target_protocol_start_epoch is null
       or target_protocol_start_epoch not between 1700000000 and 4294967295
       or target_protocol_start_epoch
            > extract(epoch from statement_timestamp())::bigint + 300
       or target_command is null
       or target_command not in ('status', 'plan_today', 'next_task', 'hold')
       or target_command_sha256 is null
       or target_command_sha256 !~ '^[a-f0-9]{64}$'
       or target_command_created_at_epoch is null
       or target_command_created_at_epoch not between 1 and 4294967295
       or target_command_created_at_epoch < target_protocol_start_epoch
       or target_command_created_at_epoch
            > extract(epoch from statement_timestamp())::bigint + 300
       or ((target_command = 'hold') <> (target_reply_to_event_id is not null))
       or (target_reply_to_event_id is not null
            and target_reply_to_event_id !~ '^[a-f0-9]{64}$') then
        raise exception 'OriginTrail Buzz operations command is invalid'
            using errcode = '22023';
    end if;
    if not exists (
        select 1 from public.workspace_clients as client
        where client.workspace_id = target_workspace_id
          and client.client_id = 'origintrail'
          and client.active
    ) then
        raise exception 'OriginTrail workspace is not active'
            using errcode = '23514';
    end if;

    expected_sha := private.origintrail_buzz_operations_command_sha256(
        target_protocol_version, target_channel_id, target_command_event_id,
        target_reviewer_pubkey, target_command,
        target_command_created_at_epoch, target_reply_to_event_id
    );
    if target_command_sha256 <> expected_sha then
        raise exception 'OriginTrail Buzz operations command hash is invalid'
            using errcode = '23514';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            target_workspace_id::text || ':' || target_command_event_id, 0
        )
    );
    select command_row.* into existing
    from agent_runtime.buzz_operations_commands as command_row
    where command_row.workspace_id = target_workspace_id
      and command_row.command_event_id = target_command_event_id;
    if found then
        if existing.channel_id <> target_channel_id
           or existing.reviewer_pubkey <> target_reviewer_pubkey
           or existing.protocol_version <> target_protocol_version
           or existing.command <> target_command
           or existing.command_sha256 <> target_command_sha256
           or extract(epoch from existing.command_created_at)::bigint
                <> target_command_created_at_epoch
           or existing.reply_to_event_id is distinct from target_reply_to_event_id then
            raise exception 'OriginTrail Buzz operations command conflicts'
                using errcode = '23505';
        end if;
        return private.origintrail_buzz_operations_response_object(
            target_workspace_id, target_command_event_id, false, true, false
        );
    end if;

    kst_day := (statement_timestamp() at time zone 'Asia/Seoul')::date;
    selected_thread_root_event_id := target_command_event_id;
    if target_command = 'status' then
        select
            count(*) filter (where task.status = 'pending'),
            count(*) filter (where task.status = 'held')
        into pending_count, held_count
        from agent_runtime.buzz_operations_tasks as task
        where task.workspace_id = target_workspace_id;
        response_message := E'CoinEasy 운영 상태\n대기 기획: '
            || pending_count::text || E' · 보류: ' || held_count::text
            || E'\n자동 발행: OFF';
    elsif target_command = 'plan_today' then
        insert into agent_runtime.buzz_operations_tasks (
            workspace_id, task_type, task_day, created_by_command_event_id
        ) values (
            target_workspace_id, 'daily_plan', kst_day,
            target_command_event_id
        ) on conflict (workspace_id, task_type, task_day) do nothing;
        select task.* into selected_task
        from agent_runtime.buzz_operations_tasks as task
        where task.workspace_id = target_workspace_id
          and task.task_type = 'daily_plan'
          and task.task_day = kst_day
        for update;
        response_message := E'오늘 기획 작업을 접수했습니다.\n작업 ID: '
            || selected_task.task_id::text || E'\n상태: '
            || selected_task.status || E'\n자동 발행: OFF';
    elsif target_command = 'next_task' then
        select task.* into selected_task
        from agent_runtime.buzz_operations_tasks as task
        where task.workspace_id = target_workspace_id
          and task.status = 'pending'
        order by task.task_day, task.created_at, task.task_id
        limit 1;
        if found then
            response_message := E'다음 작업\n작업 ID: '
                || selected_task.task_id::text
                || E'\n유형: 오늘 기획 · 상태: pending\n자동 발행: OFF';
        else
            response_message := E'현재 대기 중인 작업이 없습니다.\n자동 발행: OFF';
        end if;
    else
        select task.* into selected_task
        from agent_runtime.buzz_operations_commands as parent_command
        join agent_runtime.buzz_operations_tasks as task
          on task.workspace_id = parent_command.workspace_id
         and task.task_id = parent_command.task_id
        where parent_command.workspace_id = target_workspace_id
          and parent_command.channel_id = target_channel_id
          and parent_command.response_status = 'delivered'
          and parent_command.response_relay_event_id = target_reply_to_event_id
        for update of task;
        select parent_command.command_event_id
        into selected_thread_root_event_id
        from agent_runtime.buzz_operations_commands as parent_command
        where parent_command.workspace_id = target_workspace_id
          and parent_command.channel_id = target_channel_id
          and parent_command.response_status = 'delivered'
          and parent_command.response_relay_event_id = target_reply_to_event_id;
        if selected_thread_root_event_id is null then
            raise exception 'OriginTrail Buzz operations hold target is invalid'
                using errcode = '23514';
        end if;
        if found and selected_task.status = 'pending' then
            update agent_runtime.buzz_operations_tasks
            set status = 'held',
                held_by_command_event_id = target_command_event_id,
                updated_at = statement_timestamp()
            where workspace_id = selected_task.workspace_id
              and task_id = selected_task.task_id;
            selected_task.status := 'held';
        end if;
        if found and selected_task.status = 'held' then
            response_message := E'작업을 보류했습니다.\n작업 ID: '
                || selected_task.task_id::text
                || E'\n자동 발행: OFF';
        else
            selected_task.task_id := null;
            response_message := E'보류할 대기 작업을 찾지 못했습니다.\n자동 발행: OFF';
        end if;
    end if;

    insert into agent_runtime.buzz_operations_commands (
        workspace_id, command_event_id, channel_id, reviewer_pubkey,
        protocol_version, command, command_sha256, command_created_at,
        reply_to_event_id, thread_root_event_id, task_id, response_template_version,
        response_message, response_message_sha256, response_status
    ) values (
        target_workspace_id, target_command_event_id, target_channel_id,
        target_reviewer_pubkey, target_protocol_version, target_command,
        target_command_sha256,
        pg_catalog.to_timestamp(target_command_created_at_epoch),
        target_reply_to_event_id, selected_thread_root_event_id,
        selected_task.task_id,
        'origintrail-buzz-operations-response@1', response_message,
        private.origintrail_buzz_operations_sha256(response_message), 'pending'
    );
    return private.origintrail_buzz_operations_response_object(
        target_workspace_id, target_command_event_id, false, false, false
    );
end;
$$;

create or replace function public.claim_origintrail_buzz_operations_response(
    target_workspace_id uuid,
    target_command_event_id text,
    target_worker_id text,
    target_lease_seconds integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare selected_event_id text;
begin
    if target_workspace_id is null
       or (target_command_event_id is not null
            and target_command_event_id !~ '^[a-f0-9]{64}$')
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_lease_seconds is null
       or target_lease_seconds not between 180 and 600 then
        raise exception 'OriginTrail Buzz operations claim is invalid'
            using errcode = '22023';
    end if;
    with candidate as (
        select command_row.command_event_id
        from agent_runtime.buzz_operations_commands as command_row
        where command_row.workspace_id = target_workspace_id
          and command_row.response_status = 'pending'
          and command_row.available_at <= statement_timestamp()
          and command_row.attempts < command_row.max_attempts
          and (target_command_event_id is null
               or command_row.command_event_id = target_command_event_id)
        order by command_row.command_created_at, command_row.command_event_id
        limit 1 for update skip locked
    )
    update agent_runtime.buzz_operations_commands as command_row
    set response_status = 'claimed', attempts = command_row.attempts + 1,
        locked_by = target_worker_id, locked_at = statement_timestamp(),
        lease_expires_at = statement_timestamp()
            + make_interval(secs => target_lease_seconds),
        error_code = null, updated_at = statement_timestamp()
    from candidate
    where command_row.workspace_id = target_workspace_id
      and command_row.command_event_id = candidate.command_event_id
    returning command_row.command_event_id into selected_event_id;
    if selected_event_id is null then return null; end if;
    return private.origintrail_buzz_operations_response_object(
        target_workspace_id, selected_event_id, true, false, false
    );
end;
$$;

create or replace function public.mark_origintrail_buzz_operations_response_attempt(
    target_workspace_id uuid,
    target_command_event_id text,
    target_worker_id text,
    target_message_sha256 text,
    target_request_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare changed integer;
begin
    if target_workspace_id is null
       or target_command_event_id is null
       or target_command_event_id !~ '^[a-f0-9]{64}$'
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_message_sha256 is null
       or target_message_sha256 !~ '^[a-f0-9]{64}$'
       or target_request_sha256 is null
       or target_request_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'OriginTrail Buzz operations attempt is invalid'
            using errcode = '22023';
    end if;
    update agent_runtime.buzz_operations_commands
    set response_status = 'attempt_started',
        response_request_sha256 = target_request_sha256,
        delivery_attempt_id = gen_random_uuid(),
        attempt_worker_id = target_worker_id,
        delivery_started_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and command_event_id = target_command_event_id
      and response_status = 'claimed'
      and locked_by = target_worker_id
      and lease_expires_at > statement_timestamp()
      and response_message_sha256 = target_message_sha256
      and response_request_sha256 is null;
    get diagnostics changed = row_count;
    if changed = 1 then
        return private.origintrail_buzz_operations_response_object(
            target_workspace_id, target_command_event_id, false, false, true
        );
    end if;
    if exists (
        select 1 from agent_runtime.buzz_operations_commands
        where workspace_id = target_workspace_id
          and command_event_id = target_command_event_id
          and response_message_sha256 = target_message_sha256
          and response_request_sha256 = target_request_sha256
          and response_status in ('attempt_started', 'delivered', 'delivery_unknown')
    ) then
        return private.origintrail_buzz_operations_response_object(
            target_workspace_id, target_command_event_id, false, true, false
        );
    end if;
    raise exception 'OriginTrail Buzz operations attempt is not authorized'
        using errcode = '23514';
end;
$$;

create or replace function public.complete_origintrail_buzz_operations_response(
    target_workspace_id uuid,
    target_command_event_id text,
    target_worker_id text,
    target_request_sha256 text,
    target_relay_event_id text,
    target_reconciled boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare changed integer;
begin
    if target_workspace_id is null
       or target_command_event_id is null
       or target_command_event_id !~ '^[a-f0-9]{64}$'
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_request_sha256 is null
       or target_request_sha256 !~ '^[a-f0-9]{64}$'
       or target_relay_event_id is null
       or target_relay_event_id !~ '^[a-f0-9]{64}$'
       or target_reconciled is null then
        raise exception 'OriginTrail Buzz operations completion is invalid'
            using errcode = '22023';
    end if;
    if exists (
        select 1 from agent_runtime.buzz_operations_commands
        where workspace_id = target_workspace_id
          and command_event_id = target_command_event_id
          and response_status = 'delivered'
          and response_request_sha256 = target_request_sha256
          and response_relay_event_id = target_relay_event_id
    ) then
        return private.origintrail_buzz_operations_response_object(
            target_workspace_id, target_command_event_id, false, true, false
        );
    end if;
    update agent_runtime.buzz_operations_commands
    set response_status = 'delivered',
        response_relay_event_id = target_relay_event_id,
        delivered_at = statement_timestamp(), locked_by = null,
        locked_at = null, lease_expires_at = null, error_code = null,
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and command_event_id = target_command_event_id
      and response_request_sha256 = target_request_sha256
      and (
          (not target_reconciled and response_status = 'attempt_started'
              and attempt_worker_id = target_worker_id)
          or (target_reconciled and response_status = 'delivery_unknown')
      );
    get diagnostics changed = row_count;
    if changed <> 1 then
        raise exception 'OriginTrail Buzz operations completion conflicts'
            using errcode = '23514';
    end if;
    return private.origintrail_buzz_operations_response_object(
        target_workspace_id, target_command_event_id, false, false, false
    );
end;
$$;

create or replace function public.fail_origintrail_buzz_operations_response(
    target_workspace_id uuid,
    target_command_event_id text,
    target_worker_id text,
    target_error_code text,
    target_retryable_before_attempt boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare current_row agent_runtime.buzz_operations_commands%rowtype;
begin
    if target_workspace_id is null
       or target_command_event_id is null
       or target_command_event_id !~ '^[a-f0-9]{64}$'
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_error_code is null
       or target_error_code !~ '^[a-z][a-z0-9_]{0,79}$'
       or target_retryable_before_attempt is null then
        raise exception 'OriginTrail Buzz operations failure is invalid'
            using errcode = '22023';
    end if;
    select command_row.* into current_row
    from agent_runtime.buzz_operations_commands as command_row
    where command_row.workspace_id = target_workspace_id
      and command_row.command_event_id = target_command_event_id
    for update;
    if not found or current_row.locked_by is distinct from target_worker_id then
        raise exception 'OriginTrail Buzz operations failure conflicts'
            using errcode = '23514';
    end if;
    if current_row.response_status = 'claimed' and target_retryable_before_attempt then
        update agent_runtime.buzz_operations_commands
        set response_status = case when attempts < max_attempts
                then 'pending' else 'failed' end,
            available_at = statement_timestamp() + interval '1 minute',
            locked_by = null, locked_at = null, lease_expires_at = null,
            error_code = case when attempts < max_attempts
                then null else target_error_code end,
            updated_at = statement_timestamp()
        where workspace_id = target_workspace_id
          and command_event_id = target_command_event_id;
    elsif current_row.response_status = 'attempt_started'
          and not target_retryable_before_attempt then
        update agent_runtime.buzz_operations_commands
        set response_status = 'delivery_unknown', locked_by = null,
            locked_at = null, lease_expires_at = null,
            error_code = target_error_code, updated_at = statement_timestamp()
        where workspace_id = target_workspace_id
          and command_event_id = target_command_event_id;
    else
        raise exception 'OriginTrail Buzz operations failure transition is invalid'
            using errcode = '23514';
    end if;
    return private.origintrail_buzz_operations_response_object(
        target_workspace_id, target_command_event_id, false, false, false
    );
end;
$$;

create or replace function public.reconcile_origintrail_buzz_operations_leases(
    target_workspace_id uuid,
    target_limit integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare pending_count integer := 0; failed_count integer := 0;
declare unknown_count integer := 0;
begin
    if target_workspace_id is null or target_limit is null
       or target_limit not between 1 and 100 then
        raise exception 'OriginTrail Buzz operations reconcile is invalid'
            using errcode = '22023';
    end if;
    with expired as (
        select command_event_id
        from agent_runtime.buzz_operations_commands
        where workspace_id = target_workspace_id
          and response_status = 'claimed'
          and lease_expires_at <= statement_timestamp()
        order by lease_expires_at, command_event_id
        limit target_limit for update skip locked
    ), updated as (
        update agent_runtime.buzz_operations_commands as command_row
        set response_status = case when attempts < max_attempts
                then 'pending' else 'failed' end,
            available_at = statement_timestamp(), locked_by = null,
            locked_at = null, lease_expires_at = null,
            error_code = case when attempts < max_attempts
                then null else 'buzz_operations_attempts_exhausted' end,
            updated_at = statement_timestamp()
        from expired
        where command_row.workspace_id = target_workspace_id
          and command_row.command_event_id = expired.command_event_id
        returning command_row.response_status
    ) select count(*) filter (where response_status = 'pending'),
             count(*) filter (where response_status = 'failed')
      into pending_count, failed_count from updated;
    with expired as (
        select command_event_id
        from agent_runtime.buzz_operations_commands
        where workspace_id = target_workspace_id
          and response_status = 'attempt_started'
          and lease_expires_at <= statement_timestamp()
        order by lease_expires_at, command_event_id
        limit target_limit for update skip locked
    )
    update agent_runtime.buzz_operations_commands as command_row
    set response_status = 'delivery_unknown', locked_by = null,
        locked_at = null, lease_expires_at = null,
        error_code = 'buzz_delivery_unknown', updated_at = statement_timestamp()
    from expired
    where command_row.workspace_id = target_workspace_id
      and command_row.command_event_id = expired.command_event_id;
    get diagnostics unknown_count = row_count;
    return jsonb_build_object(
        'ok', true, 'workspace_id', target_workspace_id,
        'requeued_count', pending_count,
        'failed_count', failed_count, 'unknown_count', unknown_count
    );
end;
$$;

create or replace function public.list_origintrail_buzz_operations_unknown(
    target_workspace_id uuid,
    target_limit integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare result jsonb;
begin
    if target_workspace_id is null or target_limit is null
       or target_limit not between 1 and 10 then
        raise exception 'OriginTrail Buzz operations unknown list is invalid'
            using errcode = '22023';
    end if;
    select jsonb_build_object(
        'workspace_id', target_workspace_id,
        'items', coalesce(jsonb_agg(item order by item->>'command_event_id'), '[]'::jsonb)
    ) into result
    from (
        select private.origintrail_buzz_operations_response_object(
            target_workspace_id, command_row.command_event_id,
            false, false, false
        ) as item
        from agent_runtime.buzz_operations_commands as command_row
        where command_row.workspace_id = target_workspace_id
          and command_row.response_status = 'delivery_unknown'
        order by command_row.delivery_started_at, command_row.command_event_id
        limit target_limit
    ) as unknown_items;
    return result;
end;
$$;

revoke all on function public.record_origintrail_buzz_operations_command(
    uuid, uuid, text, text, text, bigint, text, text, bigint, text
) from public, anon, authenticated;
revoke all on function public.claim_origintrail_buzz_operations_response(
    uuid, text, text, integer
) from public, anon, authenticated;
revoke all on function public.mark_origintrail_buzz_operations_response_attempt(
    uuid, text, text, text, text
) from public, anon, authenticated;
revoke all on function public.complete_origintrail_buzz_operations_response(
    uuid, text, text, text, text, boolean
) from public, anon, authenticated;
revoke all on function public.fail_origintrail_buzz_operations_response(
    uuid, text, text, text, boolean
) from public, anon, authenticated;
revoke all on function public.reconcile_origintrail_buzz_operations_leases(
    uuid, integer
) from public, anon, authenticated;
revoke all on function public.list_origintrail_buzz_operations_unknown(
    uuid, integer
) from public, anon, authenticated;

grant execute on function public.record_origintrail_buzz_operations_command(
    uuid, uuid, text, text, text, bigint, text, text, bigint, text
) to service_role;
grant execute on function public.claim_origintrail_buzz_operations_response(
    uuid, text, text, integer
) to service_role;
grant execute on function public.mark_origintrail_buzz_operations_response_attempt(
    uuid, text, text, text, text
) to service_role;
grant execute on function public.complete_origintrail_buzz_operations_response(
    uuid, text, text, text, text, boolean
) to service_role;
grant execute on function public.fail_origintrail_buzz_operations_response(
    uuid, text, text, text, boolean
) to service_role;
grant execute on function public.reconcile_origintrail_buzz_operations_leases(
    uuid, integer
) to service_role;
grant execute on function public.list_origintrail_buzz_operations_unknown(
    uuid, integer
) to service_role;

commit;
