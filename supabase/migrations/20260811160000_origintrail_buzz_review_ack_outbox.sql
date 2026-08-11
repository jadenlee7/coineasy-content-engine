-- Durable, at-most-once Buzz acknowledgement delivery for immutable
-- OriginTrail review decisions.  This migration is deliberately forward-only:
-- legacy decisions are never backfilled into the acknowledgement outbox.

begin;

create or replace function private.origintrail_buzz_review_ack_message(
    target_decision text
)
returns text
language plpgsql
immutable
strict
set search_path = ''
as $$
begin
    if target_decision = 'approved' then
        return E'✅ 게시 승인 접수\n원문·최종물 확인 결정을 기록했습니다.\n\n현재 상태: 검토 결정 기록 완료\n자동 발행: OFF';
    end if;
    if target_decision = 'changes_requested' then
        return E'🛠 수정 요청 접수\n사유는 검토자의 원문 답글에 기록했습니다.\n\n현재 상태: 수정 대기\n자동 재생성·발행: OFF';
    end if;
    raise exception 'OriginTrail Buzz acknowledgement decision is invalid'
        using errcode = '22023';
end;
$$;

create or replace function private.origintrail_buzz_review_ack_message_sha256(
    target_message text
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select encode(extensions.digest(
        pg_catalog.convert_to(target_message, 'UTF8'), 'sha256'
    ), 'hex')
$$;

revoke all on function private.origintrail_buzz_review_ack_message(text)
from public, anon, authenticated, service_role;
revoke all on function private.origintrail_buzz_review_ack_message_sha256(text)
from public, anon, authenticated, service_role;

create table agent_runtime.buzz_review_ack_receipts (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    job_id uuid not null,
    channel_id uuid not null,
    root_relay_event_id text not null check (
        root_relay_event_id ~ '^[a-f0-9]{64}$'
    ),
    decision_event_id text not null check (
        decision_event_id ~ '^[a-f0-9]{64}$'
        and decision_event_id <> root_relay_event_id
    ),
    decision text not null check (
        decision in ('approved', 'changes_requested')
    ),
    template_version text not null check (
        template_version = 'origintrail-buzz-review-ack@1'
    ),
    message text not null,
    message_sha256 text not null check (message_sha256 ~ '^[a-f0-9]{64}$'),
    -- Bound exactly once immediately before the only authorized relay call.
    -- Python includes release SHA, relay origin, channel, service pubkey,
    -- reply target, template version, and message SHA in this fingerprint.
    request_sha256 text check (
        request_sha256 is null or request_sha256 ~ '^[a-f0-9]{64}$'
    ),
    status text not null check (
        status in (
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
    relay_event_id text check (
        relay_event_id is null or relay_event_id ~ '^[a-f0-9]{64}$'
    ),
    delivered_at timestamptz,
    error_code text check (
        error_code is null or error_code ~ '^[a-z][a-z0-9_]{0,79}$'
    ),
    created_at timestamptz not null default statement_timestamp(),
    updated_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, job_id),
    unique (decision_event_id),
    foreign key (workspace_id, job_id)
        references agent_runtime.buzz_review_decisions(workspace_id, job_id)
        on delete restrict,
    check (
        message = private.origintrail_buzz_review_ack_message(decision)
        and message_sha256 =
            private.origintrail_buzz_review_ack_message_sha256(message)
        and octet_length(message) between 1 and 1024
        and position('@' in message) = 0
        and position('nostr:npub1' in lower(message)) = 0
    ),
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
        (request_sha256 is not null) =
            (status in ('attempt_started', 'delivered', 'delivery_unknown'))
    ),
    check (
        (status = 'delivered') =
            (relay_event_id is not null and delivered_at is not null)
    ),
    check (
        status = 'delivered'
        or (relay_event_id is null and delivered_at is null)
    ),
    check (
        status not in ('delivery_unknown', 'failed') or error_code is not null
    )
);

create unique index buzz_review_ack_relay_event_unique_idx
    on agent_runtime.buzz_review_ack_receipts (relay_event_id)
    where relay_event_id is not null;
create index buzz_review_ack_claim_reconcile_idx
    on agent_runtime.buzz_review_ack_receipts (
        workspace_id, status, available_at, lease_expires_at, created_at
    )
    where status in ('pending', 'claimed', 'attempt_started');

alter table agent_runtime.buzz_review_ack_receipts enable row level security;
alter table agent_runtime.buzz_review_ack_receipts force row level security;
revoke all on table agent_runtime.buzz_review_ack_receipts
from public, anon, authenticated, service_role;

create or replace function private.origintrail_buzz_review_ack_object(
    target_workspace_id uuid,
    target_job_id uuid,
    target_claim_granted boolean,
    target_reused boolean
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object(
        'job_id', receipt.job_id,
        'channel_id', receipt.channel_id,
        'root_relay_event_id', receipt.root_relay_event_id,
        'decision_event_id', receipt.decision_event_id,
        'decision', receipt.decision,
        'reason', decision.reason,
        'command_created_at_epoch',
            extract(epoch from decision.command_created_at)::bigint,
        'template_version', receipt.template_version,
        'message', receipt.message,
        'status', receipt.status,
        'claim_granted', target_claim_granted,
        'reused', target_reused,
        'message_sha256', receipt.message_sha256,
        'request_sha256', receipt.request_sha256,
        'delivery_started_at_epoch', case
            when receipt.delivery_started_at is null then null
            else extract(epoch from receipt.delivery_started_at)::bigint
        end,
        'relay_event_id', receipt.relay_event_id
    )
    from agent_runtime.buzz_review_ack_receipts as receipt
    join agent_runtime.buzz_review_decisions as decision
      on decision.workspace_id = receipt.workspace_id
     and decision.job_id = receipt.job_id
     and decision.channel_id = receipt.channel_id
     and decision.root_relay_event_id = receipt.root_relay_event_id
     and decision.decision_event_id = receipt.decision_event_id
     and decision.decision = receipt.decision
    where receipt.workspace_id = target_workspace_id
      and receipt.job_id = target_job_id
$$;

revoke all on function private.origintrail_buzz_review_ack_object(
    uuid, uuid, boolean, boolean
) from public, anon, authenticated, service_role;

create or replace function public.record_origintrail_buzz_review_decision_with_ack(
    target_workspace_id uuid,
    target_job_id uuid,
    target_delivery_event_id text,
    target_channel_id uuid,
    target_root_relay_event_id text,
    target_message_sha256 text,
    target_protocol_version text,
    target_protocol_start_epoch bigint,
    target_decision_event_id text,
    target_reviewer_pubkey text,
    target_decision text,
    target_reason text,
    target_command_sha256 text,
    target_command_created_at_epoch bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    decision_result jsonb;
    receipt agent_runtime.buzz_review_ack_receipts%rowtype;
    acknowledgement_message text;
    acknowledgement_message_sha256 text;
begin
    decision_result := public.record_origintrail_buzz_review_decision(
        target_workspace_id, target_job_id, target_delivery_event_id,
        target_channel_id, target_root_relay_event_id, target_message_sha256,
        target_protocol_version, target_protocol_start_epoch,
        target_decision_event_id, target_reviewer_pubkey, target_decision,
        target_reason, target_command_sha256, target_command_created_at_epoch
    );

    if (decision_result ->> 'reused')::boolean then
        select current_receipt.* into receipt
        from agent_runtime.buzz_review_ack_receipts as current_receipt
        where current_receipt.workspace_id = target_workspace_id
          and current_receipt.job_id = target_job_id;
        if not found then
            -- A legacy decision must never acquire a retrospective relay write.
            raise exception 'OriginTrail Buzz acknowledgement was not enqueued'
                using errcode = '55000';
        end if;
        if receipt.channel_id is distinct from target_channel_id
           or receipt.root_relay_event_id is distinct from target_root_relay_event_id
           or receipt.decision_event_id is distinct from target_decision_event_id
           or receipt.decision is distinct from target_decision
           or receipt.message is distinct from
                private.origintrail_buzz_review_ack_message(target_decision)
           or receipt.message_sha256 is distinct from
                private.origintrail_buzz_review_ack_message_sha256(receipt.message) then
            raise exception 'OriginTrail Buzz acknowledgement conflicts'
                using errcode = '23505';
        end if;
        return decision_result || jsonb_build_object(
            'acknowledgement_status', receipt.status
        );
    end if;

    acknowledgement_message :=
        private.origintrail_buzz_review_ack_message(target_decision);
    acknowledgement_message_sha256 :=
        private.origintrail_buzz_review_ack_message_sha256(
            acknowledgement_message
        );

    insert into agent_runtime.buzz_review_ack_receipts (
        workspace_id, job_id, channel_id, root_relay_event_id,
        decision_event_id, decision, template_version, message,
        message_sha256, request_sha256, status
    ) values (
        target_workspace_id, target_job_id, target_channel_id,
        target_root_relay_event_id, target_decision_event_id,
        target_decision, 'origintrail-buzz-review-ack@1',
        acknowledgement_message, acknowledgement_message_sha256,
        null, 'pending'
    ) returning * into receipt;

    return decision_result || jsonb_build_object(
        'acknowledgement_status', receipt.status
    );
end;
$$;

create or replace function public.claim_origintrail_buzz_review_ack(
    target_workspace_id uuid,
    target_job_id uuid,
    target_worker_id text,
    target_lease_seconds integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    receipt agent_runtime.buzz_review_ack_receipts%rowtype;
    acknowledgement jsonb;
begin
    if target_workspace_id is null
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_lease_seconds not between 180 and 600 then
        raise exception 'OriginTrail Buzz acknowledgement claim is invalid'
            using errcode = '22023';
    end if;

    if target_job_id is not null then
        select current_receipt.* into receipt
        from agent_runtime.buzz_review_ack_receipts as current_receipt
        where current_receipt.workspace_id = target_workspace_id
          and current_receipt.job_id = target_job_id
        for update;
    else
        select current_receipt.* into receipt
        from agent_runtime.buzz_review_ack_receipts as current_receipt
        where current_receipt.workspace_id = target_workspace_id
          and current_receipt.status = 'pending'
          and current_receipt.available_at <= statement_timestamp()
          and current_receipt.attempts < current_receipt.max_attempts
        order by current_receipt.created_at, current_receipt.job_id
        limit 1
        for update skip locked;
    end if;

    if not found then
        return jsonb_build_object(
            'schema_version', '1.0',
            'mode', 'durable_review_acknowledgement',
            'workspace_id', target_workspace_id,
            'acknowledgement', null
        );
    end if;

    acknowledgement := private.origintrail_buzz_review_ack_object(
        receipt.workspace_id, receipt.job_id, false, true
    );
    if acknowledgement is null
       or receipt.message is distinct from
            private.origintrail_buzz_review_ack_message(receipt.decision)
       or receipt.message_sha256 is distinct from
            private.origintrail_buzz_review_ack_message_sha256(receipt.message) then
        raise exception 'OriginTrail Buzz acknowledgement payload is invalid'
            using errcode = '23514';
    end if;

    if receipt.status = 'claimed'
       and receipt.lease_expires_at > statement_timestamp() then
        acknowledgement := private.origintrail_buzz_review_ack_object(
            receipt.workspace_id, receipt.job_id,
            receipt.locked_by = target_worker_id, true
        );
        return jsonb_build_object(
            'schema_version', '1.0',
            'mode', 'durable_review_acknowledgement',
            'workspace_id', target_workspace_id,
            'acknowledgement', acknowledgement
        );
    end if;

    if receipt.status <> 'pending'
       or receipt.available_at > statement_timestamp()
       or receipt.attempts >= receipt.max_attempts then
        return jsonb_build_object(
            'schema_version', '1.0',
            'mode', 'durable_review_acknowledgement',
            'workspace_id', target_workspace_id,
            'acknowledgement', acknowledgement
        );
    end if;

    update agent_runtime.buzz_review_ack_receipts
    set status = 'claimed',
        attempts = attempts + 1,
        locked_by = target_worker_id,
        locked_at = statement_timestamp(),
        lease_expires_at = statement_timestamp()
            + make_interval(secs => target_lease_seconds),
        error_code = null,
        updated_at = statement_timestamp()
    where workspace_id = receipt.workspace_id
      and job_id = receipt.job_id
    returning * into receipt;

    acknowledgement := private.origintrail_buzz_review_ack_object(
        receipt.workspace_id, receipt.job_id, true, false
    );
    return jsonb_build_object(
        'schema_version', '1.0',
        'mode', 'durable_review_acknowledgement',
        'workspace_id', target_workspace_id,
        'acknowledgement', acknowledgement
    );
end;
$$;

create or replace function public.mark_origintrail_buzz_review_ack_attempt(
    target_workspace_id uuid,
    target_job_id uuid,
    target_worker_id text,
    target_message_sha256 text,
    target_request_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    receipt agent_runtime.buzz_review_ack_receipts%rowtype;
begin
    if target_workspace_id is null or target_job_id is null
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or lower(coalesce(target_message_sha256, '')) !~ '^[a-f0-9]{64}$'
       or lower(coalesce(target_request_sha256, '')) !~ '^[a-f0-9]{64}$' then
        raise exception 'OriginTrail Buzz acknowledgement attempt is invalid'
            using errcode = '22023';
    end if;

    select current_receipt.* into receipt
    from agent_runtime.buzz_review_ack_receipts as current_receipt
    where current_receipt.workspace_id = target_workspace_id
      and current_receipt.job_id = target_job_id
    for update;
    if not found then
        raise exception 'OriginTrail Buzz acknowledgement does not exist'
            using errcode = 'P0002';
    end if;
    if receipt.message_sha256 is distinct from lower(target_message_sha256)
       or receipt.message_sha256 is distinct from
            private.origintrail_buzz_review_ack_message_sha256(receipt.message) then
        raise exception 'OriginTrail Buzz acknowledgement message hash conflicts'
            using errcode = '23505';
    end if;

    if receipt.delivery_started_at is not null then
        if receipt.request_sha256 is distinct from lower(target_request_sha256)
           or receipt.attempt_worker_id is distinct from target_worker_id
           or receipt.status not in (
               'attempt_started', 'delivered', 'delivery_unknown'
           ) then
            raise exception 'OriginTrail Buzz acknowledgement attempt conflicts'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'schema_version', '1.0',
            'mode', 'durable_review_acknowledgement',
            'workspace_id', receipt.workspace_id,
            'job_id', receipt.job_id,
            'status', receipt.status,
            'message_sha256', receipt.message_sha256,
            'request_sha256', receipt.request_sha256,
            'authorized_once', false,
            'reused', true
        );
    end if;

    if receipt.status <> 'claimed'
       or receipt.locked_by is distinct from target_worker_id
       or receipt.lease_expires_at <= statement_timestamp()
       or receipt.request_sha256 is not null then
        raise exception 'OriginTrail Buzz acknowledgement attempt lease is invalid'
            using errcode = '55000';
    end if;

    update agent_runtime.buzz_review_ack_receipts
    set status = 'attempt_started',
        request_sha256 = lower(target_request_sha256),
        delivery_attempt_id = gen_random_uuid(),
        attempt_worker_id = target_worker_id,
        delivery_started_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and job_id = target_job_id
    returning * into receipt;

    return jsonb_build_object(
        'schema_version', '1.0',
        'mode', 'durable_review_acknowledgement',
        'workspace_id', receipt.workspace_id,
        'job_id', receipt.job_id,
        'status', receipt.status,
        'message_sha256', receipt.message_sha256,
        'request_sha256', receipt.request_sha256,
        'authorized_once', true,
        'reused', false
    );
end;
$$;

create or replace function public.complete_origintrail_buzz_review_ack(
    target_workspace_id uuid,
    target_job_id uuid,
    target_worker_id text,
    target_request_sha256 text,
    target_relay_event_id text,
    target_reconciled boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    receipt agent_runtime.buzz_review_ack_receipts%rowtype;
begin
    if target_workspace_id is null or target_job_id is null
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or lower(coalesce(target_request_sha256, '')) !~ '^[a-f0-9]{64}$'
       or lower(coalesce(target_relay_event_id, '')) !~ '^[a-f0-9]{64}$'
       or target_reconciled is null then
        raise exception 'OriginTrail Buzz acknowledgement completion is invalid'
            using errcode = '22023';
    end if;

    select current_receipt.* into receipt
    from agent_runtime.buzz_review_ack_receipts as current_receipt
    where current_receipt.workspace_id = target_workspace_id
      and current_receipt.job_id = target_job_id
    for update;
    if not found then
        raise exception 'OriginTrail Buzz acknowledgement does not exist'
            using errcode = 'P0002';
    end if;

    if receipt.status = 'delivered' then
        if receipt.request_sha256 is distinct from lower(target_request_sha256)
           or receipt.relay_event_id is distinct from lower(target_relay_event_id) then
            raise exception 'OriginTrail Buzz acknowledgement completion conflicts'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'schema_version', '1.0',
            'mode', 'durable_review_acknowledgement',
            'workspace_id', receipt.workspace_id,
            'job_id', receipt.job_id,
            'status', receipt.status,
            'request_sha256', receipt.request_sha256,
            'relay_event_id', receipt.relay_event_id,
            'reused', true
        );
    end if;

    if target_reconciled then
        if receipt.status <> 'delivery_unknown'
           or receipt.request_sha256 is distinct from
                lower(target_request_sha256) then
            raise exception 'OriginTrail Buzz acknowledgement reconciliation is invalid'
                using errcode = '55000';
        end if;
    elsif receipt.status <> 'attempt_started'
       or receipt.locked_by is distinct from target_worker_id
       or receipt.attempt_worker_id is distinct from target_worker_id
       or receipt.lease_expires_at <= statement_timestamp()
       or receipt.request_sha256 is distinct from
            lower(target_request_sha256) then
        raise exception 'OriginTrail Buzz acknowledgement completion lease is invalid'
            using errcode = '55000';
    end if;

    update agent_runtime.buzz_review_ack_receipts
    set status = 'delivered',
        relay_event_id = lower(target_relay_event_id),
        delivered_at = statement_timestamp(),
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        error_code = null,
        updated_at = statement_timestamp()
    where workspace_id = target_workspace_id
      and job_id = target_job_id
    returning * into receipt;

    return jsonb_build_object(
        'schema_version', '1.0',
        'mode', 'durable_review_acknowledgement',
        'workspace_id', receipt.workspace_id,
        'job_id', receipt.job_id,
        'status', receipt.status,
        'request_sha256', receipt.request_sha256,
        'relay_event_id', receipt.relay_event_id,
        'reused', false
    );
end;
$$;

create or replace function public.fail_origintrail_buzz_review_ack(
    target_workspace_id uuid,
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
    receipt agent_runtime.buzz_review_ack_receipts%rowtype;
    next_status text;
begin
    if target_workspace_id is null or target_job_id is null
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
        raise exception 'OriginTrail Buzz acknowledgement failure is invalid'
            using errcode = '22023';
    end if;

    select current_receipt.* into receipt
    from agent_runtime.buzz_review_ack_receipts as current_receipt
    where current_receipt.workspace_id = target_workspace_id
      and current_receipt.job_id = target_job_id
    for update;
    if not found then
        raise exception 'OriginTrail Buzz acknowledgement does not exist'
            using errcode = 'P0002';
    end if;

    if receipt.status in ('pending', 'failed', 'delivery_unknown')
       and receipt.error_code is not distinct from target_error_code then
        return jsonb_build_object(
            'schema_version', '1.0',
            'mode', 'durable_review_acknowledgement',
            'workspace_id', receipt.workspace_id,
            'job_id', receipt.job_id,
            'status', receipt.status,
            'reused', true
        );
    end if;
    if receipt.locked_by is distinct from target_worker_id
       or receipt.lease_expires_at <= statement_timestamp() then
        raise exception 'OriginTrail Buzz acknowledgement failure lease is invalid'
            using errcode = '55000';
    end if;

    if receipt.delivery_started_at is not null then
        if target_error_code <> 'buzz_delivery_unknown'
           or target_retryable_before_attempt
           or receipt.status <> 'attempt_started'
           or receipt.attempt_worker_id is distinct from target_worker_id then
            raise exception 'OriginTrail Buzz acknowledgement failure state is invalid'
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
        raise exception 'OriginTrail Buzz acknowledgement failure state is invalid'
            using errcode = '23514';
    end if;

    update agent_runtime.buzz_review_ack_receipts
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
      and job_id = target_job_id
    returning * into receipt;

    return jsonb_build_object(
        'schema_version', '1.0',
        'mode', 'durable_review_acknowledgement',
        'workspace_id', receipt.workspace_id,
        'job_id', receipt.job_id,
        'status', receipt.status,
        'reused', false
    );
end;
$$;

create or replace function public.reconcile_origintrail_buzz_review_ack_leases(
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
        raise exception 'OriginTrail Buzz acknowledgement reconciliation is invalid'
            using errcode = '22023';
    end if;

    with expired as (
        select receipt.workspace_id, receipt.job_id, receipt.status,
               receipt.attempts, receipt.max_attempts
        from agent_runtime.buzz_review_ack_receipts as receipt
        where receipt.workspace_id = target_workspace_id
          and receipt.status in ('claimed', 'attempt_started')
          and receipt.lease_expires_at <= statement_timestamp()
        order by receipt.lease_expires_at, receipt.job_id
        limit target_limit
        for update skip locked
    ), changed as (
        update agent_runtime.buzz_review_ack_receipts as receipt
        set status = case
                when receipt.status = 'attempt_started' then 'delivery_unknown'
                when receipt.attempts < receipt.max_attempts then 'pending'
                else 'failed'
            end,
            available_at = case
                when receipt.status = 'claimed'
                 and receipt.attempts < receipt.max_attempts
                    then statement_timestamp() + interval '1 minute'
                else receipt.available_at
            end,
            locked_by = null,
            locked_at = null,
            lease_expires_at = null,
            error_code = case
                when receipt.status = 'attempt_started'
                    then 'buzz_delivery_unknown'
                when receipt.attempts >= receipt.max_attempts
                    then 'buzz_claim_lease_expired'
                else null
            end,
            updated_at = statement_timestamp()
        from expired
        where receipt.workspace_id = expired.workspace_id
          and receipt.job_id = expired.job_id
        returning receipt.status
    )
    select
        count(*) filter (where status = 'pending'),
        count(*) filter (where status = 'failed'),
        count(*) filter (where status = 'delivery_unknown')
    into pending_count, failed_count, unknown_count
    from changed;

    return jsonb_build_object(
        'schema_version', '1.0',
        'mode', 'durable_review_acknowledgement',
        'workspace_id', target_workspace_id,
        'reconciled_count', pending_count + failed_count + unknown_count,
        'pending_count', pending_count,
        'failed_count', failed_count,
        'delivery_unknown_count', unknown_count
    );
end;
$$;

create or replace function public.list_origintrail_buzz_review_ack_unknown(
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
    result jsonb;
begin
    if target_workspace_id is null or target_limit not between 1 and 10 then
        raise exception 'OriginTrail Buzz acknowledgement unknown list is invalid'
            using errcode = '22023';
    end if;

    with unknown_receipts as (
        select receipt.job_id, receipt.delivery_started_at
        from agent_runtime.buzz_review_ack_receipts as receipt
        where receipt.workspace_id = target_workspace_id
          and receipt.status = 'delivery_unknown'
        order by receipt.delivery_started_at, receipt.job_id
        limit target_limit
    )
    select coalesce(jsonb_agg(
        private.origintrail_buzz_review_ack_object(
            target_workspace_id, unknown_receipts.job_id, false, true
        ) order by unknown_receipts.delivery_started_at,
                   unknown_receipts.job_id
    ), '[]'::jsonb)
    into result
    from unknown_receipts;

    return jsonb_build_object(
        'schema_version', '1.0',
        'mode', 'durable_review_acknowledgement',
        'workspace_id', target_workspace_id,
        'acknowledgements', result
    );
end;
$$;

revoke all on function public.record_origintrail_buzz_review_decision_with_ack(
    uuid, uuid, text, uuid, text, text, text, bigint, text, text,
    text, text, text, bigint
) from public, anon, authenticated;
revoke all on function public.claim_origintrail_buzz_review_ack(
    uuid, uuid, text, integer
) from public, anon, authenticated;
revoke all on function public.mark_origintrail_buzz_review_ack_attempt(
    uuid, uuid, text, text, text
) from public, anon, authenticated;
revoke all on function public.complete_origintrail_buzz_review_ack(
    uuid, uuid, text, text, text, boolean
) from public, anon, authenticated;
revoke all on function public.fail_origintrail_buzz_review_ack(
    uuid, uuid, text, text, boolean
) from public, anon, authenticated;
revoke all on function public.reconcile_origintrail_buzz_review_ack_leases(
    uuid, integer
) from public, anon, authenticated;
revoke all on function public.list_origintrail_buzz_review_ack_unknown(
    uuid, integer
) from public, anon, authenticated;

grant execute on function public.record_origintrail_buzz_review_decision_with_ack(
    uuid, uuid, text, uuid, text, text, text, bigint, text, text,
    text, text, text, bigint
) to service_role;
grant execute on function public.claim_origintrail_buzz_review_ack(
    uuid, uuid, text, integer
) to service_role;
grant execute on function public.mark_origintrail_buzz_review_ack_attempt(
    uuid, uuid, text, text, text
) to service_role;
grant execute on function public.complete_origintrail_buzz_review_ack(
    uuid, uuid, text, text, text, boolean
) to service_role;
grant execute on function public.fail_origintrail_buzz_review_ack(
    uuid, uuid, text, text, boolean
) to service_role;
grant execute on function public.reconcile_origintrail_buzz_review_ack_leases(
    uuid, integer
) to service_role;
grant execute on function public.list_origintrail_buzz_review_ack_unknown(
    uuid, integer
) to service_role;

commit;
