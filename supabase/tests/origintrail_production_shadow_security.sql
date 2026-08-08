-- Transactional smoke for the seven-day, one-OriginTrail-Batch-per-KST-day
-- Production Shadow fence. All rows roll back; no provider call is made.

begin;

do $test$
declare
    peek_signature constant text :=
        'public.peek_origintrail_batch_shadow_candidate(uuid,text,uuid,timestamp with time zone,timestamp with time zone)';
    configure_signature constant text :=
        'public.configure_origintrail_batch_shadow_day(uuid,date,text,uuid,timestamp with time zone,timestamp with time zone,text,uuid,text,uuid,uuid,text,text,timestamp with time zone,bigint,integer)';
begin
    if has_function_privilege('anon', peek_signature, 'execute')
       or has_function_privilege('authenticated', peek_signature, 'execute')
       or has_function_privilege('anon', configure_signature, 'execute')
       or has_function_privilege('authenticated', configure_signature, 'execute') then
        raise exception 'OriginTrail Production Shadow RPC leaked to browser role';
    end if;
    if not has_function_privilege('service_role', peek_signature, 'execute')
       or not has_function_privilege('service_role', configure_signature, 'execute') then
        raise exception 'OriginTrail Production Shadow RPC unavailable to service_role';
    end if;
    if has_table_privilege(
        'service_role',
        'agent_runtime.origintrail_batch_production_shadow_days',
        'select'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.origintrail_batch_production_shadow_days',
        'insert'
    ) or has_table_privilege(
        'anon',
        'agent_runtime.origintrail_batch_production_shadow_days',
        'select'
    ) then
        raise exception 'OriginTrail Production Shadow table leaked direct access';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'e5000000-0000-4000-8000-000000000001',
    'OriginTrail Production Shadow Test',
    'origintrail-production-shadow-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values (
    'e5000000-0000-4000-8000-000000000001',
    'origintrail',
    'OriginTrail',
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
    'e5000000-0000-4000-8000-000000000001',
    'batch-general:' || (
        statement_timestamp() at time zone 'Asia/Seoul'
    )::date::text,
    (
        (statement_timestamp() at time zone 'Asia/Seoul')::date
        ::timestamp at time zone 'Asia/Seoul'
    ),
    (
        ((statement_timestamp() at time zone 'Asia/Seoul')::date + 1)
        ::timestamp at time zone 'Asia/Seoul'
    ),
    100000,
    100000
);

-- The shadow ledger rows must use the same public review-job boundary as
-- production. Shadow selection is independent of media admission, so these
-- fixtures intentionally point at one legacy text-only standalone source.
insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, active, created_by
)
values (
    'e5500000-0000-4000-8000-000000000001',
    'e5000000-0000-4000-8000-000000000001',
    'origintrail',
    'x',
    'OriginTrail production-shadow test source',
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
    'e5600000-0000-4000-8000-000000000001',
    'e5000000-0000-4000-8000-000000000001',
    'origintrail',
    'e5500000-0000-4000-8000-000000000001',
    '1966666666666666666',
    'tweet',
    'https://x.com/origin_trail/status/1966666666666666666',
    '@origin_trail',
    statement_timestamp() - interval '1 hour',
    'Pinned legacy text-only OriginTrail shadow evidence.',
    '[]'::jsonb,
    '{"is_note_tweet":false,"metrics":{}}'::jsonb,
    pg_catalog.md5('origintrail-shadow-text-source'),
    null
);

insert into private.origintrail_standalone_sources (
    workspace_id, client_id, source_item_id, is_quote,
    first_poll_request_id
)
values (
    'e5000000-0000-4000-8000-000000000001',
    'origintrail',
    'e5600000-0000-4000-8000-000000000001',
    false,
    'e5700000-0000-4000-8000-000000000001'
);

insert into public.jobs (
    id, workspace_id, client_id, job_kind, status, priority, input, output,
    idempotency_key, attempts, max_attempts, available_at
)
select
    fixture.job_id,
    'e5000000-0000-4000-8000-000000000001',
    'origintrail',
    'generate',
    'queued',
    0,
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'kst_date',
            (statement_timestamp() at time zone 'Asia/Seoul')::date,
        'source_item_ids', jsonb_build_array(
            'e5600000-0000-4000-8000-000000000001'::uuid
        ),
        'content_kind', 'daily_news',
        'request_id', fixture.request_id,
        'source_content',
            'Pinned legacy text-only OriginTrail shadow evidence.',
        'source_url',
            'https://x.com/origin_trail/status/1966666666666666666',
        'source_image_url', '',
        'manual_only', false
    ),
    '{}'::jsonb,
    'origintrail-shadow-review:' || fixture.ordinal::text,
    0,
    3,
    statement_timestamp()
from (
    values
        (
            'e5100000-0000-4000-8000-000000000001'::uuid,
            'e5800000-0000-4000-8000-000000000001'::uuid,
            1
        ),
        (
            'e5100000-0000-4000-8000-000000000002'::uuid,
            'e5800000-0000-4000-8000-000000000002'::uuid,
            2
        )
) as fixture(job_id, request_id, ordinal);

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
    budget_key
)
values
    (
        'e5100000-0000-4000-8000-000000000001',
        'e5000000-0000-4000-8000-000000000001',
        'origintrail',
        repeat('1', 64),
        'e5100000-0000-4000-8000-000000000001:generate:1',
        'origintrail_client_agent',
        'official_source_nonurgent_pack',
        'generate',
        3,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'Return the first shadow draft.',
            'input', 'Pinned immutable OriginTrail evidence one.',
            'output_schema', jsonb_build_object(
                'type', 'object',
                'properties', jsonb_build_object(
                    'draft', jsonb_build_object('type', 'string')
                ),
                'required', jsonb_build_array('draft'),
                'additionalProperties', false
            ),
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
        repeat('d', 64),
        1000,
        1000,
        50000,
        'batch-general:' || (
            statement_timestamp() at time zone 'Asia/Seoul'
        )::date::text
    ),
    (
        'e5100000-0000-4000-8000-000000000002',
        'e5000000-0000-4000-8000-000000000001',
        'origintrail',
        repeat('2', 64),
        'e5100000-0000-4000-8000-000000000002:generate:1',
        'origintrail_client_agent',
        'official_source_nonurgent_pack',
        'generate',
        2,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'Do not admit a second same-day shadow draft.',
            'input', 'Pinned immutable OriginTrail evidence two.',
            'output_schema', jsonb_build_object(
                'type', 'object',
                'properties', jsonb_build_object(
                    'draft', jsonb_build_object('type', 'string')
                ),
                'required', jsonb_build_array('draft'),
                'additionalProperties', false
            ),
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
            'request_sha256', repeat('e', 64)
        ),
        repeat('f', 64),
        1000,
        1000,
        50000,
        'batch-general:' || (
            statement_timestamp() at time zone 'Asia/Seoul'
        )::date::text
    );

do $test$
declare
    test_workspace constant uuid :=
        'e5000000-0000-4000-8000-000000000001';
    pilot_subject constant text := repeat('a', 64);
    pilot_approval constant uuid :=
        'e5200000-0000-4000-8000-000000000001';
    pilot_day date := (
        statement_timestamp() at time zone 'Asia/Seoul'
    )::date;
    pilot_start timestamptz := (
        (statement_timestamp() at time zone 'Asia/Seoul')::date
        ::timestamp at time zone 'Asia/Seoul'
    );
    pilot_end timestamptz := pilot_start + interval '7 days';
    expires_at timestamptz := statement_timestamp() + interval '1 hour';
    candidate jsonb;
    admitted jsonb;
    candidate_job uuid;
    candidate_input text;
    candidate_request text;
begin
    candidate := public.peek_origintrail_batch_shadow_candidate(
        test_workspace,
        pilot_subject,
        pilot_approval,
        pilot_start,
        pilot_end
    );
    if jsonb_array_length(candidate) <> 1 then
        raise exception 'Production Shadow candidate was not unique: %', candidate;
    end if;
    candidate_job := (candidate -> 0 ->> 'job_id')::uuid;
    candidate_input := candidate -> 0 ->> 'input_sha256';
    candidate_request := candidate -> 0 ->> 'request_sha256';

    admitted := public.configure_origintrail_batch_shadow_day(
        test_workspace,
        pilot_day,
        pilot_subject,
        pilot_approval,
        pilot_start,
        pilot_end,
        repeat('3', 64),
        'e5300000-0000-4000-8000-000000000001',
        repeat('4', 64),
        'e5400000-0000-4000-8000-000000000001',
        candidate_job,
        candidate_input,
        candidate_request,
        expires_at,
        50000,
        1
    );
    if admitted ->> 'reused' <> 'false'
       or admitted ->> 'pilot_kst_date' <> pilot_day::text
       or admitted ->> 'pilot_subject_sha256' <> pilot_subject
       or admitted ->> 'canary_job_id' <> candidate_job::text then
        raise exception 'Production Shadow day receipt is invalid: %', admitted;
    end if;

    if public.peek_origintrail_batch_shadow_candidate(
        test_workspace,
        pilot_subject,
        pilot_approval,
        pilot_start,
        pilot_end
    ) <> '[]'::jsonb then
        raise exception 'same KST day exposed a second candidate';
    end if;

    begin
        perform public.configure_origintrail_batch_shadow_day(
            test_workspace,
            pilot_day,
            pilot_subject,
            pilot_approval,
            pilot_start,
            pilot_end,
            repeat('5', 64),
            'e5300000-0000-4000-8000-000000000002',
            repeat('6', 64),
            'e5400000-0000-4000-8000-000000000002',
            'e5100000-0000-4000-8000-000000000002',
            repeat('f', 64),
            repeat('e', 64),
            expires_at,
            50000,
            1
        );
        raise exception 'same KST day accepted a replacement job';
    exception when unique_violation then null;
    end;
end
$test$;

rollback;
