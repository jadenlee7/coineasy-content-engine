-- Transactional security and idempotency smoke for OriginTrail Buzz delivery.
-- Run after every migration as the database owner. No relay or network call is
-- made and every fixture is rolled back.

begin;

do $test$
declare
    signature text;
begin
    foreach signature in array array[
        'public.claim_origintrail_buzz_delivery(uuid,uuid,text,uuid,text,text,text,integer)',
        'public.mark_origintrail_buzz_delivery_attempt(uuid,text,text,text)',
        'public.complete_origintrail_buzz_delivery(uuid,text,text,text,text)',
        'public.fail_origintrail_buzz_delivery(uuid,text,text,text,boolean)',
        'public.reconcile_origintrail_buzz_delivery_leases(uuid,integer)'
    ]
    loop
        if has_function_privilege('anon', signature, 'execute')
           or has_function_privilege('authenticated', signature, 'execute') then
            raise exception 'Buzz delivery RPC leaked to a browser role: %', signature;
        end if;
        if not has_function_privilege('service_role', signature, 'execute') then
            raise exception 'Buzz delivery RPC unavailable to service_role: %', signature;
        end if;
    end loop;
    if has_table_privilege(
        'service_role', 'agent_runtime.buzz_delivery_receipts', 'select'
    ) or has_table_privilege(
        'service_role', 'agent_runtime.buzz_delivery_receipts', 'insert'
    ) or has_table_privilege(
        'anon', 'agent_runtime.buzz_delivery_receipts', 'select'
    ) or has_table_privilege(
        'authenticated', 'agent_runtime.buzz_delivery_receipts', 'select'
    ) then
        raise exception 'Buzz delivery receipt table leaked direct access';
    end if;
    if not exists (
        select 1
        from pg_catalog.pg_class as relation
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'agent_runtime'
          and relation.relname = 'buzz_delivery_receipts'
          and relation.relrowsecurity
          and relation.relforcerowsecurity
    ) then
        raise exception 'Buzz delivery receipts do not force RLS';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'e0000000-0000-4000-8000-000000000001',
    'OriginTrail Buzz Delivery Test',
    'origintrail-buzz-delivery-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values (
    'e0000000-0000-4000-8000-000000000001',
    'origintrail',
    'OriginTrail',
    true,
    null
);

insert into agent_runtime.batch_budgets (
    workspace_id, budget_key, period_start, period_end,
    hard_limit_microusd, reserved_microusd, spent_microusd
)
values (
    'e0000000-0000-4000-8000-000000000001',
    'buzz-security:2099-01-01',
    statement_timestamp() - interval '1 hour',
    statement_timestamp() + interval '23 hours',
    50000,
    0,
    4400
);

insert into public.jobs (
    id, workspace_id, client_id, job_kind, status, input, output,
    attempts, finished_at
)
values
    (
        'e1000000-0000-4000-8000-000000000001',
        'e0000000-0000-4000-8000-000000000001',
        'origintrail',
        'generate',
        'succeeded',
        jsonb_build_object(
            'workflow', 'official_x_review_draft_v1',
            'content_kind', 'daily_news',
            'manual_only', false,
            'source_url',
                'https://x.com/origin_trail/status/2082883998829752783'
        ),
        jsonb_build_object(
            'workflow', 'agent_batch_review_handoff_v1',
            'handoff', 'openai_batch',
            'batch_job_id', 'e1000000-0000-4000-8000-000000000001'::uuid,
            'input_sha256', repeat('1', 64),
            'review_state', 'pending'
        ),
        1,
        statement_timestamp()
    ),
    (
        'e1000000-0000-4000-8000-000000000002',
        'e0000000-0000-4000-8000-000000000001',
        'origintrail',
        'generate',
        'succeeded',
        jsonb_build_object(
            'workflow', 'official_x_review_draft_v1',
            'content_kind', 'daily_news',
            'manual_only', false,
            'source_url',
                'https://x.com/origin_trail/status/2082883998829752784'
        ),
        jsonb_build_object(
            'workflow', 'agent_batch_review_handoff_v1',
            'handoff', 'openai_batch',
            'batch_job_id', 'e1000000-0000-4000-8000-000000000002'::uuid,
            'input_sha256', repeat('2', 64),
            'review_state', 'pending'
        ),
        1,
        statement_timestamp()
    );

insert into agent_runtime.batch_jobs (
    job_id, workspace_id, client_id, idempotency_key, custom_id,
    agent_id, workflow_kind, stage, priority, latency_class, model,
    model_tier, deadline_at, input_payload, input_sha256,
    estimated_input_tokens, max_output_tokens, max_cost_microusd,
    budget_key, status, reservation_state, attempts,
    actual_input_tokens, actual_output_tokens, actual_cost_microusd,
    result_code, result_payload, finished_at
)
values
    (
        'e1000000-0000-4000-8000-000000000001',
        'e0000000-0000-4000-8000-000000000001',
        'origintrail', repeat('a', 64),
        'e1000000-0000-4000-8000-000000000001:generate:1',
        'origintrail_client_agent', 'official_source_nonurgent_pack',
        'generate', 3, 'batch_24h', 'gpt-5.6-luna', 'S',
        statement_timestamp() + interval '24 hours',
        jsonb_build_object('approval_required', true),
        repeat('1', 64), 1000, 1000, 2200,
        'buzz-security:2099-01-01', 'completed', 'settled', 1,
        500, 200, 2200, 'needs_review',
        jsonb_build_object(
            'headline_ko', 'OriginTrail 검토 제목',
            'body_ko', '검토 본문',
            'x_copy_ko', 'X 검토 문구',
            'telegram_copy_ko', 'Telegram 검토 문구'
        ),
        statement_timestamp()
    ),
    (
        'e1000000-0000-4000-8000-000000000002',
        'e0000000-0000-4000-8000-000000000001',
        'origintrail', repeat('b', 64),
        'e1000000-0000-4000-8000-000000000002:generate:1',
        'origintrail_client_agent', 'official_source_nonurgent_pack',
        'generate', 3, 'batch_24h', 'gpt-5.6-luna', 'S',
        statement_timestamp() + interval '24 hours',
        jsonb_build_object('approval_required', true),
        repeat('2', 64), 1000, 1000, 2200,
        'buzz-security:2099-01-01', 'completed', 'settled', 1,
        500, 200, 2200, 'needs_review',
        jsonb_build_object(
            'headline_ko', 'OriginTrail 두 번째 제목',
            'body_ko', '두 번째 검토 본문',
            'x_copy_ko', '두 번째 X 문구',
            'telegram_copy_ko', '두 번째 Telegram 문구'
        ),
        statement_timestamp()
    );

do $test$
declare
    workspace_id constant uuid :=
        'e0000000-0000-4000-8000-000000000001';
    job_id constant uuid :=
        'e1000000-0000-4000-8000-000000000001';
    second_job_id constant uuid :=
        'e1000000-0000-4000-8000-000000000002';
    channel_id constant uuid :=
        'e2000000-0000-4000-8000-000000000001';
    worker_id constant text := 'origintrail-buzz:security-1';
    message_sha constant text := repeat('c', 64);
    request_sha constant text := repeat('d', 64);
    relay_event_id constant text := repeat('e', 64);
    event_id text := private.origintrail_buzz_event_id(workspace_id, job_id);
    second_event_id text :=
        private.origintrail_buzz_event_id(workspace_id, second_job_id);
    claimed jsonb;
    replayed jsonb;
    attempt jsonb;
    completed jsonb;
    failed jsonb;
begin
    begin
        perform public.claim_origintrail_buzz_delivery(
            workspace_id, job_id, repeat('f', 64), channel_id,
            message_sha, request_sha, worker_id, 180
        );
        raise exception 'wrong deterministic Buzz event ID was accepted';
    exception when check_violation then null;
    end;

    claimed := public.claim_origintrail_buzz_delivery(
        workspace_id, job_id, event_id, channel_id,
        message_sha, request_sha, worker_id, 180
    );
    if claimed ->> 'claim_granted' <> 'true'
       or claimed ->> 'reused' <> 'false'
       or claimed ->> 'status' <> 'claimed' then
        raise exception 'fresh Buzz claim is invalid: %', claimed;
    end if;

    replayed := public.claim_origintrail_buzz_delivery(
        workspace_id, job_id, event_id, channel_id,
        message_sha, request_sha, worker_id, 180
    );
    if replayed ->> 'claim_granted' <> 'true'
       or replayed ->> 'reused' <> 'true' then
        raise exception 'same-worker claim retry did not converge';
    end if;

    attempt := public.mark_origintrail_buzz_delivery_attempt(
        workspace_id, event_id, worker_id, request_sha
    );
    if attempt ->> 'authorized_once' <> 'true'
       or attempt ->> 'reused' <> 'false' then
        raise exception 'fresh attempt did not authorize exactly one send';
    end if;

    replayed := public.mark_origintrail_buzz_delivery_attempt(
        workspace_id, event_id, worker_id, request_sha
    );
    if replayed ->> 'authorized_once' <> 'false'
       or replayed ->> 'reused' <> 'true' then
        raise exception 'attempt replay authorized a duplicate send';
    end if;

    completed := public.complete_origintrail_buzz_delivery(
        workspace_id, event_id, worker_id, request_sha, relay_event_id
    );
    if completed ->> 'status' <> 'delivered'
       or completed ->> 'relay_event_id' <> relay_event_id then
        raise exception 'Buzz completion receipt is invalid';
    end if;

    replayed := public.claim_origintrail_buzz_delivery(
        workspace_id, job_id, event_id, channel_id,
        message_sha, request_sha, worker_id, 180
    );
    if replayed ->> 'claim_granted' <> 'false'
       or replayed ->> 'status' <> 'delivered' then
        raise exception 'delivered event became claimable again';
    end if;

    perform public.claim_origintrail_buzz_delivery(
        workspace_id, second_job_id, second_event_id, channel_id,
        repeat('1', 64), repeat('2', 64), worker_id, 180
    );
    perform public.mark_origintrail_buzz_delivery_attempt(
        workspace_id, second_event_id, worker_id, repeat('2', 64)
    );
    failed := public.fail_origintrail_buzz_delivery(
        workspace_id, second_event_id, worker_id,
        'buzz_delivery_unknown', false
    );
    if failed ->> 'status' <> 'delivery_unknown' then
        raise exception 'post-attempt failure did not become delivery_unknown';
    end if;
end
$test$;

rollback;
