-- Transactional smoke for exact provider costs above the internal Batch
-- reservation cap. All state is rolled back; no provider call is made.

begin;

do $test$
declare
    complete_signature constant text :=
        'public.complete_agent_batch_job(uuid,uuid,text,text,jsonb,bigint,bigint,bigint)';
    fail_signature constant text :=
        'public.fail_agent_batch_job(uuid,uuid,text,text,boolean,timestamp with time zone,bigint,bigint,bigint,boolean)';
    old_complete_signature constant text :=
        'public.complete_agent_batch_job_within_cap(uuid,uuid,text,text,jsonb,bigint,bigint,bigint)';
    old_fail_signature constant text :=
        'public.fail_agent_batch_job_within_cap(uuid,uuid,text,text,boolean,timestamp with time zone,bigint,bigint,bigint,boolean)';
    helper_signature constant text :=
        'agent_runtime.settle_batch_cost_overage(uuid,uuid,text,text,text,jsonb,bigint,bigint,bigint)';
begin
    if has_function_privilege('anon', complete_signature, 'execute')
       or has_function_privilege('authenticated', complete_signature, 'execute')
       or has_function_privilege('anon', fail_signature, 'execute')
       or has_function_privilege('authenticated', fail_signature, 'execute') then
        raise exception 'Batch settlement wrappers leaked to a browser role';
    end if;
    if not has_function_privilege('service_role', complete_signature, 'execute')
       or not has_function_privilege('service_role', fail_signature, 'execute') then
        raise exception 'Batch settlement wrappers unavailable to service_role';
    end if;
    if has_function_privilege('service_role', old_complete_signature, 'execute')
       or has_function_privilege('service_role', old_fail_signature, 'execute')
       or has_function_privilege('service_role', helper_signature, 'execute')
       or has_function_privilege('anon', helper_signature, 'execute')
       or has_function_privilege('authenticated', helper_signature, 'execute') then
        raise exception 'A private Batch settlement implementation is callable';
    end if;
    if has_table_privilege(
        'service_role',
        'agent_runtime.batch_cost_overage_incidents',
        'select'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.batch_cost_overage_incidents',
        'insert'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.batch_cost_overage_incidents',
        'update'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.batch_cost_overage_incidents',
        'delete'
    ) or has_table_privilege(
        'anon',
        'agent_runtime.batch_cost_overage_incidents',
        'select'
    ) or has_table_privilege(
        'authenticated',
        'agent_runtime.batch_cost_overage_incidents',
        'select'
    ) then
        raise exception 'Batch cost overage evidence leaked direct access';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'e0000000-0000-4000-8000-000000000001',
    'Batch Cost Overage Test',
    'batch-cost-overage-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values
    (
        'e0000000-0000-4000-8000-000000000001',
        'origintrail',
        'OriginTrail',
        true,
        null
    ),
    (
        'e0000000-0000-4000-8000-000000000001',
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
    'e0000000-0000-4000-8000-000000000001',
    'batch-general:2099-02-01',
    statement_timestamp() - interval '1 hour',
    statement_timestamp() + interval '23 hours',
    350000,
    300000
);

insert into agent_runtime.batch_runs (
    workspace_id,
    batch_id,
    input_file_id,
    output_file_id,
    error_file_id,
    provider_status,
    job_count,
    provider_completed_at
)
values
    (
        'e0000000-0000-4000-8000-000000000001',
        'batch_overage_completion',
        'file_overage_completion_input',
        'file_overage_completion_output',
        null,
        'completed',
        1,
        statement_timestamp()
    ),
    (
        'e0000000-0000-4000-8000-000000000001',
        'batch_overage_failure',
        'file_overage_failure_input',
        null,
        'file_overage_failure_error',
        'failed',
        1,
        statement_timestamp()
    );

-- The media-evidence trigger now requires every OriginTrail ledger fixture to
-- have the same public review-job boundary used in production. These rows are
-- deliberately legacy text-only evidence; this test is about cost settlement,
-- not the separately covered reviewed-media admission path.
insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, active, created_by
)
values (
    'e2000000-0000-4000-8000-000000000001',
    'e0000000-0000-4000-8000-000000000001',
    'origintrail',
    'x',
    'OriginTrail cost test source',
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
    'e2100000-0000-4000-8000-000000000001',
    'e0000000-0000-4000-8000-000000000001',
    'origintrail',
    'e2000000-0000-4000-8000-000000000001',
    '1999999999999999999',
    'tweet',
    'https://x.com/origin_trail/status/1999999999999999999',
    '@origin_trail',
    statement_timestamp() - interval '1 hour',
    'Pinned legacy text-only OriginTrail cost evidence.',
    '[]'::jsonb,
    '{"is_note_tweet":false,"metrics":{}}'::jsonb,
    pg_catalog.md5('origintrail-cost-text-source'),
    null
);

insert into private.origintrail_standalone_sources (
    workspace_id, client_id, source_item_id, is_quote,
    first_poll_request_id
)
values (
    'e0000000-0000-4000-8000-000000000001',
    'origintrail',
    'e2100000-0000-4000-8000-000000000001',
    false,
    'e2200000-0000-4000-8000-000000000001'
);

insert into public.jobs (
    id, workspace_id, client_id, job_kind, status, priority, input, output,
    idempotency_key, attempts, max_attempts, available_at
)
select
    fixture.job_id,
    'e0000000-0000-4000-8000-000000000001',
    'origintrail',
    'generate',
    'queued',
    0,
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'kst_date',
            (statement_timestamp() at time zone 'Asia/Seoul')::date,
        'source_item_ids', jsonb_build_array(
            'e2100000-0000-4000-8000-000000000001'::uuid
        ),
        'content_kind', 'daily_news',
        'request_id', fixture.request_id,
        'source_content',
            'Pinned legacy text-only OriginTrail cost evidence.',
        'source_url',
            'https://x.com/origin_trail/status/1999999999999999999',
        'source_image_url', '',
        'manual_only', false
    ),
    '{}'::jsonb,
    'origintrail-cost-review:' || fixture.ordinal::text,
    0,
    3,
    statement_timestamp()
from (
    values
        (
            'e1000000-0000-4000-8000-000000000001'::uuid,
            'e3000000-0000-4000-8000-000000000001'::uuid,
            1
        ),
        (
            'e1000000-0000-4000-8000-000000000004'::uuid,
            'e3000000-0000-4000-8000-000000000004'::uuid,
            4
        ),
        (
            'e1000000-0000-4000-8000-000000000005'::uuid,
            'e3000000-0000-4000-8000-000000000005'::uuid,
            5
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
    budget_key,
    status,
    attempts,
    current_batch_id,
    submitted_at
)
values
    (
        'e1000000-0000-4000-8000-000000000001',
        'e0000000-0000-4000-8000-000000000001',
        'origintrail',
        repeat('1', 64),
        'e1000000-0000-4000-8000-000000000001:generate:1',
        'origintrail_client_agent',
        'official_source_nonurgent_pack',
        'generate',
        3,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'Return the approved Korean OriginTrail draft.',
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
        repeat('5', 64),
        1000,
        1000,
        50000,
        'batch-general:2099-02-01',
        'submitted',
        1,
        'batch_overage_completion',
        statement_timestamp()
    ),
    (
        'e1000000-0000-4000-8000-000000000002',
        'e0000000-0000-4000-8000-000000000001',
        'yellow',
        repeat('2', 64),
        'e1000000-0000-4000-8000-000000000002:generate:1',
        'yellow_client_agent',
        'daily_digest',
        'generate',
        2,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'Return the approved Yellow digest.',
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
        repeat('6', 64),
        1000,
        1000,
        50000,
        'batch-general:2099-02-01',
        'submitted',
        1,
        'batch_overage_failure',
        statement_timestamp()
    ),
    (
        'e1000000-0000-4000-8000-000000000003',
        'e0000000-0000-4000-8000-000000000001',
        'yellow',
        repeat('3', 64),
        'e1000000-0000-4000-8000-000000000003:generate:1',
        'yellow_client_agent',
        'daily_digest',
        'generate',
        2,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'This generic claim must remain blocked.',
            'input', 'Pinned generic-claim evidence.',
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
        'batch-general:2099-02-01',
        'queued',
        0,
        null,
        null
    ),
    (
        'e1000000-0000-4000-8000-000000000004',
        'e0000000-0000-4000-8000-000000000001',
        'origintrail',
        repeat('4', 64),
        'e1000000-0000-4000-8000-000000000004:generate:1',
        'origintrail_client_agent',
        'official_source_nonurgent_pack',
        'generate',
        3,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'This exact canary claim must remain blocked.',
            'input', 'Pinned exact-canary evidence.',
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
            'request_sha256', repeat('9', 64)
        ),
        repeat('8', 64),
        1000,
        1000,
        50000,
        'batch-general:2099-02-01',
        'queued',
        0,
        null,
        null
    ),
    (
        'e1000000-0000-4000-8000-000000000005',
        'e0000000-0000-4000-8000-000000000001',
        'origintrail',
        repeat('5', 64),
        'e1000000-0000-4000-8000-000000000005:generate:1',
        'origintrail_client_agent',
        'official_source_nonurgent_pack',
        'generate',
        3,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'Recover the exact canary by provider lookup only.',
            'input', 'Pinned exact-recovery evidence.',
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
            'request_sha256', repeat('4', 64)
        ),
        repeat('3', 64),
        1000,
        1000,
        50000,
        'batch-general:2099-02-01',
        'queued',
        0,
        null,
        null
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
    locked_by,
    locked_at,
    lease_expires_at,
    claimed_at
)
values (
    'e1000000-0000-4000-8000-000000000006',
    'e0000000-0000-4000-8000-000000000001',
    'yellow',
    repeat('6', 64),
    'e1000000-0000-4000-8000-000000000006:generate:1',
    'yellow_client_agent',
    'daily_digest',
    'generate',
    3,
    'batch_24h',
    'gpt-5.6-luna',
    'S',
    statement_timestamp() + interval '30 hours',
    jsonb_build_object(
        'instructions', 'Recover this generic attempt without a new provider create.',
        'input', 'Pinned generic-recovery evidence.',
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
        'request_sha256', repeat('2', 64)
    ),
    repeat('1', 64),
    1000,
    1000,
    50000,
    'batch-general:2099-02-01',
    'claimed',
    1,
    'batch:generic-stale',
    statement_timestamp() - interval '2 hours',
    statement_timestamp() - interval '1 hour',
    statement_timestamp() - interval '2 hours'
);

insert into agent_runtime.batch_members (
    workspace_id, batch_id, job_id, attempt, custom_id
)
values
    (
        'e0000000-0000-4000-8000-000000000001',
        'batch_overage_completion',
        'e1000000-0000-4000-8000-000000000001',
        1,
        'e1000000-0000-4000-8000-000000000001:generate:1'
    ),
    (
        'e0000000-0000-4000-8000-000000000001',
        'batch_overage_failure',
        'e1000000-0000-4000-8000-000000000002',
        1,
        'e1000000-0000-4000-8000-000000000002:generate:1'
    );

do $test$
declare
    test_workspace_id constant uuid :=
        'e0000000-0000-4000-8000-000000000001';
    completion_job_id constant uuid :=
        'e1000000-0000-4000-8000-000000000001';
    failure_job_id constant uuid :=
        'e1000000-0000-4000-8000-000000000002';
    generic_job_id constant uuid :=
        'e1000000-0000-4000-8000-000000000003';
    canary_job_id constant uuid :=
        'e1000000-0000-4000-8000-000000000004';
    exact_recovery_job_id constant uuid :=
        'e1000000-0000-4000-8000-000000000005';
    generic_recovery_job_id constant uuid :=
        'e1000000-0000-4000-8000-000000000006';
    config_subject constant text := repeat('d', 64);
    dispatch_subject constant text := repeat('e', 64);
    canary_input_sha constant text := repeat('8', 64);
    canary_request_sha constant text := repeat('9', 64);
    canary_expires_at timestamptz :=
        statement_timestamp() + interval '1 hour';
    exact_recovery_config_subject constant text := repeat('f', 64);
    exact_recovery_dispatch_subject constant text := repeat('0', 64);
    exact_recovery_input_sha constant text := repeat('3', 64);
    exact_recovery_request_sha constant text := repeat('4', 64);
    exact_recovery_expires_at timestamptz :=
        statement_timestamp() + interval '1 hour';
    result_payload jsonb := jsonb_build_object(
        'headline_ko', '검증 가능한 지식 그래프',
        'body_ko', 'OriginTrail 공식 근거를 바탕으로 만든 검토 초안입니다.',
        'x_copy_ko', 'OriginTrail 검토 초안',
        'telegram_copy_ko', 'OriginTrail 공식 근거 검토 초안'
    );
    completion_receipt jsonb;
    completion_replay jsonb;
    failure_receipt jsonb;
    failure_replay jsonb;
    exact_recovery_initial_claim jsonb;
    exact_recovery_claim jsonb;
    generic_recovery_claim jsonb;
begin
    perform public.configure_origintrail_batch_canary_grant(
        test_workspace_id,
        config_subject,
        'e2000000-0000-4000-8000-000000000001',
        dispatch_subject,
        'e3000000-0000-4000-8000-000000000001',
        canary_job_id,
        canary_input_sha,
        canary_request_sha,
        canary_expires_at,
        50000,
        1
    );

    perform public.configure_origintrail_batch_canary_grant(
        test_workspace_id,
        exact_recovery_config_subject,
        'e2000000-0000-4000-8000-000000000002',
        exact_recovery_dispatch_subject,
        'e3000000-0000-4000-8000-000000000002',
        exact_recovery_job_id,
        exact_recovery_input_sha,
        exact_recovery_request_sha,
        exact_recovery_expires_at,
        50000,
        1
    );
    exact_recovery_initial_claim :=
        public.claim_origintrail_batch_canary_job(
            test_workspace_id,
            'batch:exact-recovery-start',
            exact_recovery_config_subject,
            'e2000000-0000-4000-8000-000000000002',
            exact_recovery_dispatch_subject,
            'e3000000-0000-4000-8000-000000000002',
            exact_recovery_job_id,
            exact_recovery_input_sha,
            exact_recovery_request_sha,
            exact_recovery_expires_at,
            50000,
            1,
            300
        );
    if jsonb_array_length(exact_recovery_initial_claim) <> 1
       or exact_recovery_initial_claim -> 0 ->> 'job_id'
            <> exact_recovery_job_id::text
       or exact_recovery_initial_claim -> 0 ->> 'attempt' <> '1'
       or exact_recovery_initial_claim -> 0 ->> 'provider_create_allowed'
            <> 'true'
       or exact_recovery_initial_claim -> 0 ->> 'recovery_required'
            <> 'false' then
        raise exception 'exact recovery setup claim is invalid: %',
            exact_recovery_initial_claim;
    end if;
    update agent_runtime.batch_jobs
    set lease_expires_at = statement_timestamp() - interval '1 minute'
    where workspace_id = test_workspace_id
      and job_id = exact_recovery_job_id;

    completion_receipt := public.complete_agent_batch_job(
        test_workspace_id,
        completion_job_id,
        'batch_overage_completion',
        'needs_review',
        result_payload,
        100,
        50,
        60000
    );
    if completion_receipt ->> 'status' <> 'failed'
       or completion_receipt ->> 'settlement' <> 'cost_cap_breached'
       or completion_receipt ->> 'error_code' <> 'batch_cost_cap_breached'
       or completion_receipt ->> 'outcome_kind' <> 'completion'
       or completion_receipt ->> 'reused' <> 'false'
       or (completion_receipt ->> 'reservation_cap_microusd')::bigint <> 50000
       or (completion_receipt ->> 'actual_cost_microusd')::bigint <> 60000
       or (completion_receipt ->> 'overage_microusd')::bigint <> 10000
       or (completion_receipt ->> 'budget_spent_microusd')::bigint <> 50000
       or completion_receipt ->> 'resolution_status' <> 'unresolved'
       or completion_receipt ->> 'outcome_fingerprint' !~ '^[a-f0-9]{64}$' then
        raise exception 'completion overage receipt is invalid: %',
            completion_receipt;
    end if;
    if not exists (
        select 1
        from agent_runtime.batch_cost_overage_incidents as incident
        where incident.workspace_id = test_workspace_id
          and incident.job_id = completion_job_id
          and incident.provider_batch_id = 'batch_overage_completion'
          and incident.attempt = 1
          and incident.outcome_kind = 'completion'
          and incident.outcome_code = 'needs_review'
          and incident.outcome_payload_sha256 = pg_catalog.encode(
              pg_catalog.sha256(pg_catalog.convert_to(
                  result_payload::text,
                  'UTF8'
              )),
              'hex'
          )
          and incident.input_tokens = 100
          and incident.output_tokens = 50
          and incident.reservation_cap_microusd = 50000
          and incident.actual_cost_microusd = 60000
          and incident.overage_microusd = 10000
          and incident.budget_spent_microusd = 50000
          and incident.resolution_status = 'unresolved'
    ) then
        raise exception 'completion overage evidence is incomplete';
    end if;
    if not exists (
        select 1
        from agent_runtime.batch_jobs as job
        where job.workspace_id = test_workspace_id
          and job.job_id = completion_job_id
          and job.status = 'failed'
          and job.reservation_state = 'released'
          and job.actual_input_tokens = 100
          and job.actual_output_tokens = 50
          and job.actual_cost_microusd = 60000
          and job.error_code = 'batch_cost_cap_breached'
          and job.result_code is null
          and job.result_payload = '{}'::jsonb
          and job.finished_at is not null
    ) then
        raise exception 'completion overage did not fail with full actual cost';
    end if;
    if not exists (
        select 1
        from agent_runtime.batch_budgets as budget
        where budget.workspace_id = test_workspace_id
          and budget.budget_key = 'batch-general:2099-02-01'
          and budget.reserved_microusd = 250000
          and budget.spent_microusd = 50000
          and budget.reserved_microusd + budget.spent_microusd = 300000
          and budget.reserved_microusd + budget.spent_microusd
                <= budget.hard_limit_microusd
    ) then
        raise exception 'completion overage broke the bounded budget invariant';
    end if;

    completion_replay := public.complete_agent_batch_job(
        test_workspace_id,
        completion_job_id,
        'batch_overage_completion',
        'needs_review',
        result_payload,
        100,
        50,
        60000
    );
    if completion_replay ->> 'reused' <> 'true'
       or completion_replay ->> 'outcome_fingerprint'
            <> completion_receipt ->> 'outcome_fingerprint'
       or (
           select count(*)
           from agent_runtime.batch_cost_overage_incidents as incident
           where incident.workspace_id = test_workspace_id
             and incident.job_id = completion_job_id
       ) <> 1 then
        raise exception 'exact completion overage replay did not converge';
    end if;
    if not exists (
        select 1
        from agent_runtime.batch_budgets as budget
        where budget.workspace_id = test_workspace_id
          and budget.budget_key = 'batch-general:2099-02-01'
          and budget.reserved_microusd = 250000
          and budget.spent_microusd = 50000
    ) then
        raise exception 'completion replay charged the budget twice';
    end if;

    begin
        perform public.complete_agent_batch_job(
            test_workspace_id,
            completion_job_id,
            'batch_overage_completion',
            'needs_review',
            result_payload,
            100,
            51,
            60000
        );
        raise exception 'mismatched completion overage replay was accepted';
    exception when unique_violation then null;
    end;

    failure_receipt := public.fail_agent_batch_job(
        test_workspace_id,
        failure_job_id,
        'batch_overage_failure',
        'openai_response_refused',
        false,
        null,
        200,
        25,
        70000,
        false
    );
    if failure_receipt ->> 'status' <> 'failed'
       or failure_receipt ->> 'settlement' <> 'cost_cap_breached'
       or failure_receipt ->> 'outcome_kind' <> 'failure'
       or failure_receipt ->> 'reused' <> 'false'
       or (failure_receipt ->> 'actual_cost_microusd')::bigint <> 70000
       or (failure_receipt ->> 'overage_microusd')::bigint <> 20000 then
        raise exception 'exact failure overage receipt is invalid: %',
            failure_receipt;
    end if;
    if not exists (
        select 1
        from agent_runtime.batch_jobs as job
        where job.workspace_id = test_workspace_id
          and job.job_id = failure_job_id
          and job.status = 'failed'
          and job.reservation_state = 'released'
          and job.actual_input_tokens = 200
          and job.actual_output_tokens = 25
          and job.actual_cost_microusd = 70000
          and job.error_code = 'batch_cost_cap_breached'
    ) then
        raise exception 'failure overage did not preserve the full actual cost';
    end if;

    failure_replay := public.fail_agent_batch_job(
        test_workspace_id,
        failure_job_id,
        'batch_overage_failure',
        'openai_response_refused',
        false,
        null,
        200,
        25,
        70000,
        false
    );
    if failure_replay ->> 'reused' <> 'true'
       or failure_replay ->> 'outcome_fingerprint'
            <> failure_receipt ->> 'outcome_fingerprint' then
        raise exception 'exact failure overage replay did not converge';
    end if;
    if not exists (
        select 1
        from agent_runtime.batch_budgets as budget
        where budget.workspace_id = test_workspace_id
          and budget.budget_key = 'batch-general:2099-02-01'
          and budget.reserved_microusd = 200000
          and budget.spent_microusd = 100000
          and budget.reserved_microusd + budget.spent_microusd = 300000
          and budget.reserved_microusd + budget.spent_microusd
                <= budget.hard_limit_microusd
    ) then
        raise exception 'failure overage or replay broke the budget invariant';
    end if;
    if (
        select count(*)
        from agent_runtime.batch_cost_overage_incidents as incident
        where incident.workspace_id = test_workspace_id
    ) <> 2 then
        raise exception 'overage incident cardinality is invalid';
    end if;

    begin
        update agent_runtime.batch_cost_overage_incidents
        set resolution_status = 'unresolved'
        where workspace_id = test_workspace_id
          and job_id = completion_job_id;
        raise exception 'immutable overage evidence accepted an update';
    exception when unique_violation then null;
    end;

    exact_recovery_claim := public.claim_origintrail_batch_canary_job(
        test_workspace_id,
        'batch:exact-recovery-lookup',
        exact_recovery_config_subject,
        'e2000000-0000-4000-8000-000000000002',
        exact_recovery_dispatch_subject,
        'e3000000-0000-4000-8000-000000000002',
        exact_recovery_job_id,
        exact_recovery_input_sha,
        exact_recovery_request_sha,
        exact_recovery_expires_at,
        50000,
        1,
        300
    );
    if jsonb_array_length(exact_recovery_claim) <> 1
       or exact_recovery_claim -> 0 ->> 'job_id'
            <> exact_recovery_job_id::text
       or exact_recovery_claim -> 0 ->> 'attempt' <> '1'
       or exact_recovery_claim -> 0 ->> 'provider_create_allowed'
            <> 'false'
       or exact_recovery_claim -> 0 ->> 'recovery_required' <> 'true'
       or exact_recovery_claim -> 0 ->> 'reused' <> 'true'
       or not exists (
           select 1
           from agent_runtime.origintrail_batch_canary_grants as canary_grant
           where canary_grant.workspace_id = test_workspace_id
             and canary_grant.job_id = exact_recovery_job_id
             and canary_grant.provider_batches_consumed = 1
             and canary_grant.consumed_at is not null
             and canary_grant.consumed_by = 'batch:exact-recovery-start'
       ) then
        raise exception 'unresolved incident blocked or reburned exact recovery: %',
            exact_recovery_claim;
    end if;

    generic_recovery_claim := public.claim_agent_batch_jobs(
        test_workspace_id,
        'batch:generic-recovery-lookup',
        array['yellow']::text[],
        1,
        300
    );
    if jsonb_array_length(generic_recovery_claim) <> 1
       or generic_recovery_claim -> 0 ->> 'job_id'
            <> generic_recovery_job_id::text
       or generic_recovery_claim -> 0 ->> 'attempt' <> '1'
       or generic_recovery_claim -> 0 ->> 'recovery_required' <> 'true'
       or not exists (
           select 1
           from agent_runtime.batch_jobs as job
           where job.workspace_id = test_workspace_id
             and job.job_id = generic_recovery_job_id
             and job.status = 'claimed'
             and job.attempts = 1
             and job.locked_by = 'batch:generic-recovery-lookup'
             and job.claimed_at = (
                 generic_recovery_claim -> 0 ->> 'attempt_started_at'
             )::timestamptz
       ) then
        raise exception 'unresolved incident blocked or restarted generic recovery: %',
            generic_recovery_claim;
    end if;

    begin
        perform public.claim_agent_batch_jobs(
            test_workspace_id,
            'batch:blocked-generic',
            array['yellow']::text[],
            10,
            300
        );
        raise exception 'generic claim crossed an unresolved overage';
    exception when check_violation then null;
    end;
    if not exists (
        select 1
        from agent_runtime.batch_jobs as job
        where job.workspace_id = test_workspace_id
          and job.job_id = generic_job_id
          and job.status = 'queued'
          and job.attempts = 0
          and job.locked_by is null
          and job.lease_expires_at is null
    ) then
        raise exception 'blocked generic claim mutated its candidate';
    end if;

    begin
        perform public.claim_origintrail_batch_canary_job(
            test_workspace_id,
            'batch:blocked-exact',
            config_subject,
            'e2000000-0000-4000-8000-000000000001',
            dispatch_subject,
            'e3000000-0000-4000-8000-000000000001',
            canary_job_id,
            canary_input_sha,
            canary_request_sha,
            canary_expires_at,
            50000,
            1,
            300
        );
        raise exception 'exact canary claim crossed an unresolved overage';
    exception when check_violation then null;
    end;
    if not exists (
        select 1
        from agent_runtime.batch_jobs as job
        where job.workspace_id = test_workspace_id
          and job.job_id = canary_job_id
          and job.status = 'queued'
          and job.attempts = 0
          and job.locked_by is null
          and job.lease_expires_at is null
    ) or not exists (
        select 1
        from agent_runtime.origintrail_batch_canary_grants as canary_grant
        where canary_grant.workspace_id = test_workspace_id
          and canary_grant.job_id = canary_job_id
          and canary_grant.provider_batches_consumed = 0
          and canary_grant.consumed_at is null
          and canary_grant.consumed_by is null
    ) then
        raise exception 'blocked exact claim consumed its grant or job';
    end if;
end
$test$;

rollback;
