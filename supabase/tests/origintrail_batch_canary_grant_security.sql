-- Transactional state-machine smoke for the exact, durable OriginTrail Batch
-- canary grant. Run after every migration as the database owner; all rows are
-- rolled back and no provider or network call is made.

begin;

do $test$
declare
    configure_signature constant text :=
        'public.configure_origintrail_batch_canary_grant(uuid,text,uuid,text,uuid,uuid,text,text,timestamp with time zone,bigint,integer)';
    claim_signature constant text :=
        'public.claim_origintrail_batch_canary_job(uuid,text,text,uuid,text,uuid,uuid,text,text,timestamp with time zone,bigint,integer,integer)';
begin
    if has_function_privilege('anon', configure_signature, 'execute')
       or has_function_privilege('authenticated', configure_signature, 'execute')
       or has_function_privilege('anon', claim_signature, 'execute')
       or has_function_privilege('authenticated', claim_signature, 'execute') then
        raise exception 'OriginTrail canary RPC leaked to a browser role';
    end if;
    if not has_function_privilege('service_role', configure_signature, 'execute')
       or not has_function_privilege('service_role', claim_signature, 'execute') then
        raise exception 'OriginTrail canary RPC unavailable to service_role';
    end if;
    if has_table_privilege(
        'service_role',
        'agent_runtime.origintrail_batch_canary_grants',
        'select'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.origintrail_batch_canary_grants',
        'insert'
    ) or has_table_privilege(
        'service_role',
        'agent_runtime.origintrail_batch_canary_grants',
        'update'
    ) or has_table_privilege(
        'anon',
        'agent_runtime.origintrail_batch_canary_grants',
        'select'
    ) or has_table_privilege(
        'authenticated',
        'agent_runtime.origintrail_batch_canary_grants',
        'select'
    ) then
        raise exception 'OriginTrail canary grant table leaked direct access';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'd0000000-0000-4000-8000-000000000001',
    'OriginTrail Batch Canary Grant Test',
    'origintrail-batch-canary-grant-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values
    (
        'd0000000-0000-4000-8000-000000000001',
        'origintrail',
        'OriginTrail',
        true,
        null
    ),
    (
        'd0000000-0000-4000-8000-000000000001',
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
    'd0000000-0000-4000-8000-000000000001',
    'batch-general:2099-01-01',
    statement_timestamp() - interval '1 hour',
    statement_timestamp() + interval '23 hours',
    150000,
    150000
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
    budget_key
)
values
    (
        'd1000000-0000-4000-8000-000000000001',
        'd0000000-0000-4000-8000-000000000001',
        'origintrail',
        repeat('1', 64),
        'd1000000-0000-4000-8000-000000000001:generate:1',
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
        'batch-general:2099-01-01'
    ),
    (
        'd1000000-0000-4000-8000-000000000002',
        'd0000000-0000-4000-8000-000000000001',
        'origintrail',
        repeat('2', 64),
        'd1000000-0000-4000-8000-000000000002:generate:1',
        'origintrail_client_agent',
        'official_source_nonurgent_pack',
        'generate',
        3,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'Do not claim this ungranted OriginTrail job.',
            'input', 'A second immutable evidence snapshot.',
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
        'batch-general:2099-01-01'
    ),
    (
        'd1000000-0000-4000-8000-000000000003',
        'd0000000-0000-4000-8000-000000000001',
        'yellow',
        repeat('3', 64),
        'd1000000-0000-4000-8000-000000000003:generate:1',
        'yellow_client_agent',
        'daily_digest',
        'generate',
        2,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        jsonb_build_object(
            'instructions', 'Return the Yellow digest.',
            'input', 'Pinned Yellow evidence.',
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
            'request_sha256', repeat('9', 64)
        ),
        repeat('8', 64),
        1000,
        1000,
        50000,
        'batch-general:2099-01-01'
    );

do $test$
declare
    test_workspace_id constant uuid :=
        'd0000000-0000-4000-8000-000000000001';
    exact_job_id constant uuid :=
        'd1000000-0000-4000-8000-000000000001';
    ungranted_job_id constant uuid :=
        'd1000000-0000-4000-8000-000000000002';
    yellow_job_id constant uuid :=
        'd1000000-0000-4000-8000-000000000003';
    config_subject constant text := repeat('a', 64);
    dispatch_subject constant text := repeat('b', 64);
    input_sha constant text := repeat('d', 64);
    request_sha constant text := repeat('c', 64);
    expires_at timestamptz := statement_timestamp() + interval '1 hour';
    configured jsonb;
    replayed jsonb;
    generic_claim jsonb;
    fresh_claim jsonb;
    live_lease_claim jsonb;
    recovery_claim jsonb;
begin
    configured := public.configure_origintrail_batch_canary_grant(
        test_workspace_id,
        config_subject,
        'd2000000-0000-4000-8000-000000000001',
        dispatch_subject,
        'd3000000-0000-4000-8000-000000000001',
        exact_job_id,
        input_sha,
        request_sha,
        expires_at,
        50000,
        1
    );
    if configured ->> 'reused' <> 'false'
       or configured ->> 'canary_job_id' <> exact_job_id::text
       or configured ->> 'canary_request_sha256' <> request_sha
       or (configured ->> 'canary_provider_batches_consumed')::integer <> 0 then
        raise exception 'fresh canary grant receipt is invalid: %', configured;
    end if;

    replayed := public.configure_origintrail_batch_canary_grant(
        test_workspace_id,
        config_subject,
        'd2000000-0000-4000-8000-000000000001',
        dispatch_subject,
        'd3000000-0000-4000-8000-000000000001',
        exact_job_id,
        input_sha,
        request_sha,
        expires_at,
        50000,
        1
    );
    if replayed ->> 'reused' <> 'true'
       or (replayed ->> 'canary_provider_batches_consumed')::integer <> 0 then
        raise exception 'identical canary grant registration did not converge';
    end if;

    begin
        perform public.configure_origintrail_batch_canary_grant(
            test_workspace_id,
            config_subject,
            'd2000000-0000-4000-8000-000000000001',
            repeat('7', 64),
            'd3000000-0000-4000-8000-000000000002',
            ungranted_job_id,
            repeat('f', 64),
            repeat('e', 64),
            expires_at,
            50000,
            1
        );
        raise exception 'same config subject accepted a replacement dispatch';
    exception when unique_violation then null;
    end;

    begin
        perform public.claim_origintrail_batch_canary_job(
            test_workspace_id,
            'batch:wrong-worker',
            config_subject,
            'd2000000-0000-4000-8000-000000000001',
            dispatch_subject,
            'd3000000-0000-4000-8000-000000000001',
            ungranted_job_id,
            repeat('f', 64),
            repeat('e', 64),
            expires_at,
            50000,
            1,
            300
        );
        raise exception 'wrong job matched the exact canary grant';
    exception when check_violation then null;
    end;
    if exists (
        select 1
        from agent_runtime.batch_jobs as job
        where job.workspace_id = test_workspace_id
          and job.job_id in (exact_job_id, ungranted_job_id)
          and (job.status <> 'queued' or job.attempts <> 0)
    ) then
        raise exception 'wrong canary binding leased or mutated an OriginTrail job';
    end if;

    generic_claim := public.claim_agent_batch_jobs(
        test_workspace_id,
        'batch:generic-worker',
        array['origintrail', 'yellow']::text[],
        10,
        300
    );
    if jsonb_array_length(generic_claim) <> 1
       or generic_claim -> 0 ->> 'job_id' <> yellow_job_id::text then
        raise exception 'generic claim did not preserve Yellow-only behavior: %',
            generic_claim;
    end if;
    if exists (
        select 1
        from agent_runtime.batch_jobs as job
        where job.workspace_id = test_workspace_id
          and job.client_id = 'origintrail'
          and (job.status <> 'queued' or job.attempts <> 0)
    ) then
        raise exception 'generic claim leased an OriginTrail job';
    end if;

    fresh_claim := public.claim_origintrail_batch_canary_job(
        test_workspace_id,
        'batch:canary-worker-one',
        config_subject,
        'd2000000-0000-4000-8000-000000000001',
        dispatch_subject,
        'd3000000-0000-4000-8000-000000000001',
        exact_job_id,
        input_sha,
        request_sha,
        expires_at,
        50000,
        1,
        300
    );
    if jsonb_array_length(fresh_claim) <> 1
       or fresh_claim -> 0 ->> 'job_id' <> exact_job_id::text
       or fresh_claim -> 0 ->> 'attempt' <> '1'
       or fresh_claim -> 0 ->> 'provider_create_allowed' <> 'true'
       or fresh_claim -> 0 ->> 'recovery_required' <> 'false' then
        raise exception 'fresh exact canary claim is invalid: %', fresh_claim;
    end if;
    if not exists (
        select 1
        from agent_runtime.origintrail_batch_canary_grants as canary_grant
        where canary_grant.workspace_id = test_workspace_id
          and canary_grant.config_subject_sha256 = config_subject
          and canary_grant.provider_batches_consumed = 1
          and canary_grant.consumed_at is not null
          and canary_grant.consumed_by = 'batch:canary-worker-one'
    ) then
        raise exception 'fresh claim did not durably consume the one-shot grant';
    end if;

    live_lease_claim := public.claim_origintrail_batch_canary_job(
        test_workspace_id,
        'batch:canary-worker-two',
        config_subject,
        'd2000000-0000-4000-8000-000000000001',
        dispatch_subject,
        'd3000000-0000-4000-8000-000000000001',
        exact_job_id,
        input_sha,
        request_sha,
        expires_at,
        50000,
        1,
        300
    );
    if jsonb_array_length(live_lease_claim) <> 0 then
        raise exception 'consumed grant authorized a second live claim: %',
            live_lease_claim;
    end if;

    update agent_runtime.batch_jobs
    set lease_expires_at = statement_timestamp() - interval '1 minute'
    where agent_runtime.batch_jobs.workspace_id = test_workspace_id
      and agent_runtime.batch_jobs.job_id = exact_job_id;

    recovery_claim := public.claim_origintrail_batch_canary_job(
        test_workspace_id,
        'batch:canary-worker-two',
        config_subject,
        'd2000000-0000-4000-8000-000000000001',
        dispatch_subject,
        'd3000000-0000-4000-8000-000000000001',
        exact_job_id,
        input_sha,
        request_sha,
        expires_at,
        50000,
        1,
        300
    );
    if jsonb_array_length(recovery_claim) <> 1
       or recovery_claim -> 0 ->> 'attempt' <> '1'
       or recovery_claim -> 0 ->> 'provider_create_allowed' <> 'false'
       or recovery_claim -> 0 ->> 'recovery_required' <> 'true' then
        raise exception 'stale attempt-1 recovery is invalid: %', recovery_claim;
    end if;
    if (
        select canary_grant.provider_batches_consumed
        from agent_runtime.origintrail_batch_canary_grants as canary_grant
        where canary_grant.workspace_id = test_workspace_id
          and canary_grant.config_subject_sha256 = config_subject
    ) <> 1 or (
        select job.attempts
        from agent_runtime.batch_jobs as job
        where job.workspace_id = test_workspace_id
          and job.job_id = exact_job_id
    ) <> 1 then
        raise exception 'recovery changed the grant count or attempt number';
    end if;
end
$test$;

rollback;
