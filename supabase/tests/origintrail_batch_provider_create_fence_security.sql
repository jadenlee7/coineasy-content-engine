-- Transactional smoke for the durable fence spanning an exact canary claim
-- and its external provider-create/register boundary. No provider call occurs.

begin;

do $test$
declare
    authorize_signature constant text :=
        'public.authorize_origintrail_batch_provider_create(uuid,text,text,uuid,text,uuid,uuid,text,text,timestamp with time zone,bigint,integer,uuid,text,text,text)';
    register_signature constant text :=
        'public.register_origintrail_batch_provider_create(uuid,text,uuid,text,uuid,text,text,text,text,text,text)';
    private_register_signature constant text :=
        'public.register_agent_batch_without_canary_intent(uuid,text,text,text,uuid[])';
begin
    if has_function_privilege('anon', authorize_signature, 'execute')
       or has_function_privilege('authenticated', authorize_signature, 'execute')
       or has_function_privilege('anon', register_signature, 'execute')
       or has_function_privilege('authenticated', register_signature, 'execute')
       or not has_function_privilege(
           'service_role', authorize_signature, 'execute'
       )
       or not has_function_privilege(
           'service_role', register_signature, 'execute'
       ) then
        raise exception 'Provider-create fence RPC privilege boundary is invalid';
    end if;
    if has_function_privilege(
        'service_role', private_register_signature, 'execute'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.origintrail_batch_provider_create_intents',
        'select'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.origintrail_batch_provider_create_intents',
        'insert'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.origintrail_batch_provider_create_intents',
        'update'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.origintrail_batch_provider_create_intents',
        'delete'
    ) then
        raise exception 'Provider-create fence private state leaked';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'f0000000-0000-4000-8000-000000000001',
    'OriginTrail Provider Fence Test',
    'origintrail-provider-fence-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values
    (
        'f0000000-0000-4000-8000-000000000001',
        'origintrail',
        'OriginTrail',
        true,
        null
    ),
    (
        'f0000000-0000-4000-8000-000000000001',
        'yellow',
        'Yellow',
        true,
        null
    );

insert into agent_runtime.batch_budgets (
    workspace_id,
    budget_key,
    period_start,
    period_end,
    hard_limit_microusd,
    reserved_microusd
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'batch-general:2099-03-01',
    statement_timestamp() - interval '1 hour',
    statement_timestamp() + interval '23 hours',
    200000,
    150000
);

insert into agent_runtime.batch_runs (
    workspace_id,
    batch_id,
    input_file_id,
    output_file_id,
    provider_status,
    job_count,
    provider_completed_at
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'batch_fence_overage',
    'file_fence_overage_input',
    'file_fence_overage_output',
    'completed',
    1,
    statement_timestamp()
);

-- Anchor the OriginTrail ledger fixture to the same public review boundary
-- enforced in production. Provider fencing itself does not exercise media, so
-- this is intentionally a legacy text-only source.
insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, active, created_by
)
values (
    'f5000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'x',
    'OriginTrail provider-fence test source',
    'https://x.com/origin_trail',
    '@origin_trail',
    15,
    true,
    null
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, media, raw_payload,
    source_hash, ingested_by
)
values (
    'f6000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'f5000000-0000-4000-8000-000000000001',
    '1977777777777777777',
    'tweet',
    'https://x.com/origin_trail/status/1977777777777777777',
    '@origin_trail',
    statement_timestamp() - interval '1 hour',
    'Pinned legacy text-only OriginTrail provider-fence evidence.',
    '[]'::jsonb,
    '{"is_note_tweet":false,"metrics":{}}'::jsonb,
    pg_catalog.md5('origintrail-provider-fence-text-source'),
    null
);

insert into private.origintrail_standalone_sources (
    workspace_id, client_id, source_item_id, is_quote,
    first_poll_request_id
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'f6000000-0000-4000-8000-000000000001',
    false,
    'f7000000-0000-4000-8000-000000000001'
);

insert into public.jobs (
    id, workspace_id, client_id, job_kind, status, priority, input, output,
    idempotency_key, attempts, max_attempts, available_at
)
values (
    'f1000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'generate',
    'queued',
    0,
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'kst_date',
            (statement_timestamp() at time zone 'Asia/Seoul')::date,
        'source_item_ids', jsonb_build_array(
            'f6000000-0000-4000-8000-000000000001'::uuid
        ),
        'content_kind', 'daily_news',
        'request_id',
            'f8000000-0000-4000-8000-000000000001'::uuid,
        'source_content',
            'Pinned legacy text-only OriginTrail provider-fence evidence.',
        'source_url',
            'https://x.com/origin_trail/status/1977777777777777777',
        'source_image_url', '',
        'manual_only', false
    ),
    '{}'::jsonb,
    'origintrail-provider-fence-review:1',
    0,
    3,
    statement_timestamp()
);

insert into agent_runtime.batch_jobs (
    job_id,
    workspace_id,
    client_id,
    idempotency_key,
    custom_id,
    agent_id,
    workflow_kind,
    stage,
    priority,
    latency_class,
    model,
    model_tier,
    deadline_at,
    input_payload,
    input_sha256,
    estimated_input_tokens,
    max_output_tokens,
    max_cost_microusd,
    budget_key,
    status,
    attempts,
    current_batch_id,
    submitted_at
)
values
    (
        'f1000000-0000-4000-8000-000000000001',
        'f0000000-0000-4000-8000-000000000001',
        'origintrail',
        repeat('1', 64),
        'f1000000-0000-4000-8000-000000000001:generate:1',
        'origintrail_client_agent',
        'official_source_nonurgent_pack',
        'generate',
        3,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'Return the exact OriginTrail canary draft.',
            'input', 'Pinned immutable OriginTrail evidence.',
            'output_schema', jsonb_build_object('type', 'object'),
            'estimated_output_tokens', 500,
            'risk_tier', 'T1',
            'approval_required', true,
            'interactive', false,
            'incident_or_release_blocker', false,
            'live_tools_required', false,
            'source_snapshot_complete', true,
            'input_immutable', true,
            'retry_idempotent', true,
            'remaining_batch_stages', 1,
            'request_sha256', repeat('a', 64)
        ),
        repeat('9', 64),
        1000,
        1000,
        50000,
        'batch-general:2099-03-01',
        'queued',
        0,
        null,
        null
    ),
    (
        'f1000000-0000-4000-8000-000000000002',
        'f0000000-0000-4000-8000-000000000001',
        'yellow',
        repeat('2', 64),
        'f1000000-0000-4000-8000-000000000002:generate:1',
        'yellow_client_agent',
        'daily_digest',
        'generate',
        2,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'Return the Yellow test draft.',
            'input', 'Pinned immutable Yellow evidence.',
            'output_schema', jsonb_build_object('type', 'object'),
            'estimated_output_tokens', 500,
            'risk_tier', 'T1',
            'approval_required', true,
            'interactive', false,
            'incident_or_release_blocker', false,
            'live_tools_required', false,
            'source_snapshot_complete', true,
            'input_immutable', true,
            'retry_idempotent', true,
            'remaining_batch_stages', 1,
            'request_sha256', repeat('b', 64)
        ),
        repeat('8', 64),
        1000,
        1000,
        50000,
        'batch-general:2099-03-01',
        'submitted',
        1,
        'batch_fence_overage',
        statement_timestamp()
    ),
    (
        'f1000000-0000-4000-8000-000000000003',
        'f0000000-0000-4000-8000-000000000001',
        'yellow',
        repeat('3', 64),
        'f1000000-0000-4000-8000-000000000003:generate:1',
        'yellow_client_agent',
        'daily_digest',
        'generate',
        1,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'This fresh claim must be fenced.',
            'input', 'Pinned immutable queued evidence.',
            'output_schema', jsonb_build_object('type', 'object'),
            'estimated_output_tokens', 500,
            'risk_tier', 'T1',
            'approval_required', true,
            'interactive', false,
            'incident_or_release_blocker', false,
            'live_tools_required', false,
            'source_snapshot_complete', true,
            'input_immutable', true,
            'retry_idempotent', true,
            'remaining_batch_stages', 1,
            'request_sha256', repeat('c', 64)
        ),
        repeat('7', 64),
        1000,
        1000,
        50000,
        'batch-general:2099-03-01',
        'queued',
        0,
        null,
        null
    );

insert into agent_runtime.batch_members (
    workspace_id, batch_id, job_id, attempt, custom_id
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'batch_fence_overage',
    'f1000000-0000-4000-8000-000000000002',
    1,
    'f1000000-0000-4000-8000-000000000002:generate:1'
);

do $test$
declare
    test_workspace_id constant uuid :=
        'f0000000-0000-4000-8000-000000000001';
    canary_job_id constant uuid :=
        'f1000000-0000-4000-8000-000000000001';
    overage_job_id constant uuid :=
        'f1000000-0000-4000-8000-000000000002';
    queued_job_id constant uuid :=
        'f1000000-0000-4000-8000-000000000003';
    config_subject constant text := repeat('d', 64);
    dispatch_subject constant text := repeat('e', 64);
    input_sha constant text := repeat('9', 64);
    request_sha constant text := repeat('a', 64);
    dispatch_key constant text := repeat('f', 64);
    create_request_sha constant text := repeat('0', 64);
    expires_at timestamptz := statement_timestamp() + interval '1 hour';
    test_intent_id constant uuid :=
        'f4000000-0000-4000-8000-000000000001';
    claim jsonb;
    authorization_receipt jsonb;
    authorization_replay_receipt jsonb;
    registration jsonb;
    registration_replay jsonb;
    settlement jsonb;
begin
    perform public.configure_origintrail_batch_canary_grant(
        test_workspace_id,
        config_subject,
        'f2000000-0000-4000-8000-000000000001',
        dispatch_subject,
        'f3000000-0000-4000-8000-000000000001',
        canary_job_id,
        input_sha,
        request_sha,
        expires_at,
        50000,
        1
    );
    claim := public.claim_origintrail_batch_canary_job(
        test_workspace_id,
        'batch:fence-worker',
        config_subject,
        'f2000000-0000-4000-8000-000000000001',
        dispatch_subject,
        'f3000000-0000-4000-8000-000000000001',
        canary_job_id,
        input_sha,
        request_sha,
        expires_at,
        50000,
        1,
        900
    );
    if jsonb_array_length(claim) <> 1
       or claim -> 0 ->> 'provider_create_allowed' <> 'true' then
        raise exception 'Fence setup claim is invalid: %', claim;
    end if;

    authorization_receipt := public.authorize_origintrail_batch_provider_create(
        test_workspace_id,
        'batch:fence-worker',
        config_subject,
        'f2000000-0000-4000-8000-000000000001',
        dispatch_subject,
        'f3000000-0000-4000-8000-000000000001',
        canary_job_id,
        input_sha,
        request_sha,
        expires_at,
        50000,
        1,
        test_intent_id,
        dispatch_key,
        create_request_sha,
        'file_fenced_canary_input'
    );
    if authorization_receipt ->> 'provider_create_intent_id'
            <> test_intent_id::text
       or authorization_receipt ->> 'provider_create_allowed' <> 'true'
       or authorization_receipt ->> 'intent_status' <> 'armed'
       or authorization_receipt ->> 'create_request_sha256'
            <> create_request_sha
       or authorization_receipt ->> 'reused' <> 'false' then
        raise exception 'Fresh provider-create authorization is invalid: %',
            authorization_receipt;
    end if;

    authorization_replay_receipt :=
        public.authorize_origintrail_batch_provider_create(
            test_workspace_id,
            'batch:fence-worker',
            config_subject,
            'f2000000-0000-4000-8000-000000000001',
            dispatch_subject,
            'f3000000-0000-4000-8000-000000000001',
            canary_job_id,
            input_sha,
            request_sha,
            expires_at,
            50000,
            1,
            test_intent_id,
            dispatch_key,
            create_request_sha,
            'file_fenced_canary_input'
        );
    if authorization_replay_receipt ->> 'provider_create_allowed' <> 'false'
       or authorization_replay_receipt ->> 'reused' <> 'true' then
        raise exception 'Authorization replay permitted another create: %',
            authorization_replay_receipt;
    end if;

    begin
        perform public.claim_agent_batch_jobs(
            test_workspace_id,
            'batch:fenced-fresh-worker',
            array['yellow']::text[],
            1,
            300
        );
        raise exception 'Armed intent allowed a fresh workspace claim';
    exception when check_violation then null;
    end;
    if not exists (
        select 1
        from agent_runtime.batch_jobs as job
        where job.workspace_id = test_workspace_id
          and job.job_id = queued_job_id
          and job.status = 'queued'
          and job.attempts = 0
    ) then
        raise exception 'Blocked fresh claim mutated its candidate';
    end if;

    begin
        perform public.complete_agent_batch_job(
            test_workspace_id,
            overage_job_id,
            'batch_fence_overage',
            'needs_review',
            jsonb_build_object('draft', 'over cap'),
            100,
            50,
            60000
        );
        raise exception 'Armed intent allowed overage settlement';
    exception when lock_not_available then null;
    end;
    if exists (
        select 1
        from agent_runtime.batch_cost_overage_incidents as incident
        where incident.workspace_id = test_workspace_id
    ) then
        raise exception 'Fenced settlement committed an incident';
    end if;

    begin
        perform public.register_agent_batch(
            test_workspace_id,
            'batch:fence-worker',
            'file_fenced_canary_input',
            'batch_fenced_canary',
            array[canary_job_id]::uuid[]
        );
        raise exception 'Generic registration bypassed the exact intent';
    exception when check_violation then null;
    end;

    registration := public.register_origintrail_batch_provider_create(
        test_workspace_id,
        'batch:fence-worker',
        test_intent_id,
        config_subject,
        canary_job_id,
        input_sha,
        request_sha,
        dispatch_key,
        create_request_sha,
        'file_fenced_canary_input',
        'batch_fenced_canary'
    );
    if registration ->> 'intent_status' <> 'registered'
       or registration ->> 'provider_batch_id' <> 'batch_fenced_canary'
       or registration ->> 'reused' <> 'false'
       or not exists (
           select 1
           from agent_runtime.batch_jobs as job
           where job.workspace_id = test_workspace_id
             and job.job_id = canary_job_id
             and job.status = 'submitted'
             and job.attempts = 1
             and job.current_batch_id = 'batch_fenced_canary'
       ) then
        raise exception 'Exact provider registration is invalid: %', registration;
    end if;

    -- The job lease is gone after registration. An exact retry must still
    -- converge by validating the durable run/member/intent binding.
    registration_replay :=
        public.register_origintrail_batch_provider_create(
            test_workspace_id,
            'batch:fence-worker',
            test_intent_id,
            config_subject,
            canary_job_id,
            input_sha,
            request_sha,
            dispatch_key,
            create_request_sha,
            'file_fenced_canary_input',
            'batch_fenced_canary'
        );
    if registration_replay ->> 'reused' <> 'true'
       or registration_replay ->> 'provider_batch_id'
            <> 'batch_fenced_canary' then
        raise exception 'Exact registration replay did not converge: %',
            registration_replay;
    end if;

    settlement := public.complete_agent_batch_job(
        test_workspace_id,
        overage_job_id,
        'batch_fence_overage',
        'needs_review',
        jsonb_build_object('draft', 'over cap'),
        100,
        50,
        60000
    );
    if settlement ->> 'settlement' <> 'cost_cap_breached'
       or settlement ->> 'resolution_status' <> 'unresolved'
       or not exists (
           select 1
           from agent_runtime.batch_budgets as budget
           where budget.workspace_id = test_workspace_id
             and budget.reserved_microusd = 100000
             and budget.spent_microusd = 50000
       ) then
        raise exception 'Settlement did not resume after exact registration: %',
            settlement;
    end if;

    begin
        update agent_runtime.origintrail_batch_provider_create_intents
        set create_not_after = create_not_after + interval '1 second'
        where agent_runtime.origintrail_batch_provider_create_intents.workspace_id
            = test_workspace_id
          and agent_runtime.origintrail_batch_provider_create_intents.intent_id
            = test_intent_id;
        raise exception 'Provider-create intent binding accepted mutation';
    exception when unique_violation then null;
    end;
end
$test$;

rollback;
