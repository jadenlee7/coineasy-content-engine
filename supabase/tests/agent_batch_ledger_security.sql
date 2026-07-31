-- Transactional security, budget, idempotency, lease, and reconciliation
-- smoke for the private Batch-first agent ledger. Run after all migrations as
-- the database owner; every test row is rolled back.

begin;

do $test$
declare
    signature text;
    ledger_table text;
begin
    foreach signature in array array[
        'public.record_origintrail_nonquote_sources(uuid,text,text,uuid,text,text,jsonb,timestamp with time zone)',
        'public.configure_agent_batch_budget(uuid,text,timestamp with time zone,timestamp with time zone,bigint)',
        'public.queue_agent_batch_job(uuid,text,uuid,text,text,text,text,smallint,text,text,text,timestamp with time zone,jsonb,text,bigint,integer,bigint,text,text,boolean)',
        'public.expire_agent_batch_jobs(uuid,text[])',
        'public.claim_agent_batch_jobs(uuid,text,text[],integer,integer)',
        'public.register_agent_batch(uuid,text,text,text,uuid[])',
        'public.list_active_agent_batches(uuid,integer)',
        'public.update_agent_batch_poll(uuid,text,text,text,text)',
        'public.complete_agent_batch_job(uuid,uuid,text,text,jsonb,bigint,bigint,bigint)',
        'public.fail_agent_batch_job(uuid,uuid,text,text,boolean,timestamp with time zone,bigint,bigint,bigint,boolean)',
        'public.finalize_agent_batch(uuid,text)',
        'public.bind_review_draft_execution_plane(uuid,text,text)',
        'public.complete_review_draft_batch_handoff(uuid,text,uuid,text)',
        'public.recover_review_draft_batch_handoff(uuid,text)',
        'public.list_agent_batch_review_inbox(uuid,integer,timestamp with time zone,uuid)',
        'public.get_agent_batch_review_item(uuid,uuid)'
    ]
    loop
        if has_function_privilege('anon', signature, 'execute')
           or has_function_privilege('authenticated', signature, 'execute') then
            raise exception 'agent batch RPC leaked to a browser role: %', signature;
        end if;
        if not has_function_privilege('service_role', signature, 'execute') then
            raise exception 'agent batch RPC unavailable to service_role: %', signature;
        end if;
    end loop;

    if has_schema_privilege('service_role', 'agent_runtime', 'usage') then
        raise exception 'service_role has direct agent_runtime schema access';
    end if;
    if has_table_privilege(
        'service_role',
        'private.origintrail_standalone_sources',
        'select'
    ) or has_table_privilege(
        'anon',
        'private.origintrail_standalone_sources',
        'select'
    ) then
        raise exception 'OriginTrail standalone allowlist leaked direct access';
    end if;
    foreach ledger_table in array array[
        'agent_runtime.batch_budgets',
        'agent_runtime.batch_runs',
        'agent_runtime.batch_jobs',
        'agent_runtime.batch_members'
    ]
    loop
        if has_table_privilege('service_role', ledger_table, 'select')
           or has_table_privilege('service_role', ledger_table, 'insert')
           or has_table_privilege('service_role', ledger_table, 'update')
           or has_table_privilege('service_role', ledger_table, 'delete')
           or has_table_privilege('anon', ledger_table, 'select')
           or has_table_privilege('authenticated', ledger_table, 'select') then
            raise exception 'agent batch table leaked direct access: %', ledger_table;
        end if;
    end loop;

    if (
        select count(*)
        from pg_catalog.pg_class as relation
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'agent_runtime'
          and relation.relname in (
              'batch_budgets', 'batch_runs', 'batch_jobs', 'batch_members'
          )
          and relation.relrowsecurity
          and relation.relforcerowsecurity
    ) <> 4 then
        raise exception 'agent batch tables do not force RLS';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'c0000000-0000-4000-8000-000000000001',
    'Agent Batch Ledger Security Test',
    'agent-batch-ledger-security-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values
    (
        'c0000000-0000-4000-8000-000000000001',
        'squid',
        'Squid',
        true,
        null
    ),
    (
        'c0000000-0000-4000-8000-000000000001',
        'yellow',
        'Yellow',
        true,
        null
    ),
    (
        'c0000000-0000-4000-8000-000000000001',
        'origintrail',
        'OriginTrail',
        true,
        null
    ),
    (
        'c0000000-0000-4000-8000-000000000001',
        'rogue',
        'Unsupported active client',
        true,
        null
    );

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, active, created_by
)
values (
    'c2000000-0000-4000-8000-000000000001',
    'c0000000-0000-4000-8000-000000000001',
    'origintrail',
    'x',
    'OriginTrail Batch security source',
    'https://x.com/origin_trail',
    '@origin_trail',
    15,
    true,
    null
);

do $test$
declare
    test_workspace_id constant uuid :=
        'c0000000-0000-4000-8000-000000000001';
    poll_request_id constant uuid :=
        'c3000000-0000-4000-8000-000000000001';
    committed_source_id uuid;
    receipt jsonb;
    replay jsonb;
    invalid_quote jsonb;
    invalid_index integer := 0;
    first_verified_at timestamptz;
    item jsonb := jsonb_build_object(
        'external_id', '1888888888888888888',
        'source_content', 'A verified standalone OriginTrail source.',
        'source_url',
            'https://x.com/origin_trail/status/1888888888888888888',
        'published_at', statement_timestamp() - interval '30 minutes',
        'media', '[]'::jsonb,
        'metrics', '{}'::jsonb,
        'is_note_tweet', false,
        'is_quote', false,
        'is_retweet', false,
        'is_reply', false
    );
begin
    begin
        perform public.record_origintrail_nonquote_sources(
            test_workspace_id,
            'origintrail',
            '@origin_trail',
            'c3000000-0000-4000-8000-000000000002',
            null,
            '1888888888888888888',
            jsonb_build_array(item - 'is_quote'),
            statement_timestamp()
        );
        raise exception 'OriginTrail intake accepted a missing quote signal';
    exception when invalid_parameter_value then null;
    end;

    for invalid_quote in
        select value
        from jsonb_array_elements('[true,null,"false"]'::jsonb)
    loop
        invalid_index := invalid_index + 1;
        begin
            perform public.record_origintrail_nonquote_sources(
                test_workspace_id,
                'origintrail',
                '@origin_trail',
                ('c3000000-0000-4000-8000-00000000000'
                    || (invalid_index + 2)::text)::uuid,
                null,
                '1888888888888888888',
                jsonb_build_array(
                    jsonb_set(item, '{is_quote}', invalid_quote, true)
                ),
                statement_timestamp()
            );
            raise exception 'OriginTrail intake accepted a non-false quote signal';
        exception when invalid_parameter_value then null;
        end;
    end loop;

    begin
        perform public.record_origintrail_nonquote_sources(
            test_workspace_id,
            'origintrail',
            '@origin_trail',
            'c3000000-0000-4000-8000-000000000006',
            null,
            '1888888888888888888',
            jsonb_build_array(item - 'is_retweet'),
            statement_timestamp()
        );
        raise exception 'OriginTrail intake accepted a missing standalone signal';
    exception when invalid_parameter_value then null;
    end;

    begin
        perform public.record_origintrail_nonquote_sources(
            test_workspace_id,
            'origintrail',
            '@origin_trail',
            'c3000000-0000-4000-8000-000000000007',
            null,
            '1888888888888888888',
            jsonb_build_array(
                jsonb_set(item, '{is_reply}', 'true'::jsonb, true)
            ),
            statement_timestamp()
        );
        raise exception 'OriginTrail intake accepted a referenced standalone signal';
    exception when invalid_parameter_value then null;
    end;

    receipt := public.record_origintrail_nonquote_sources(
        test_workspace_id,
        'origintrail',
        '@origin_trail',
        poll_request_id,
        null,
        '1888888888888888888',
        jsonb_build_array(item),
        statement_timestamp()
    );
    committed_source_id := (receipt -> 'source_item_ids' ->> 0)::uuid;
    if receipt ->> 'last_cursor' <> '1888888888888888888'
       or not exists (
           select 1
           from private.origintrail_standalone_sources as standalone
           where standalone.workspace_id = test_workspace_id
             and standalone.client_id = 'origintrail'
             and standalone.source_item_id = committed_source_id
             and standalone.is_quote is false
             and standalone.first_poll_request_id = poll_request_id
       ) then
        raise exception 'standalone OriginTrail source was not durably allowlisted';
    end if;
    select standalone.verified_at into first_verified_at
    from private.origintrail_standalone_sources as standalone
    where standalone.source_item_id = committed_source_id;
    replay := public.record_origintrail_nonquote_sources(
        test_workspace_id,
        'origintrail',
        '@origin_trail',
        poll_request_id,
        null,
        '1888888888888888888',
        jsonb_build_array(item),
        statement_timestamp()
    );
    if replay ->> 'reused' <> 'true'
       or not exists (
           select 1
           from private.origintrail_standalone_sources as standalone
           where standalone.source_item_id = committed_source_id
             and standalone.first_poll_request_id = poll_request_id
             and standalone.verified_at = first_verified_at
       ) then
        raise exception 'OriginTrail standalone allowlist replay was mutable';
    end if;
end
$test$;

do $test$
declare
    test_workspace_id constant uuid := 'c0000000-0000-4000-8000-000000000001';
    retry_job_id constant uuid := 'c1000000-0000-4000-8000-000000000001';
    yellow_job_id constant uuid := 'c1000000-0000-4000-8000-000000000002';
    expired_job_id constant uuid := 'c1000000-0000-4000-8000-000000000003';
    exhausted_job_id constant uuid := 'c1000000-0000-4000-8000-000000000004';
    expired_retry_job_id constant uuid :=
        'c1000000-0000-4000-8000-000000000010';
    ambiguous_claimed_job_id constant uuid :=
        'c1000000-0000-4000-8000-000000000011';
    partial_ok_job_id constant uuid := 'c1000000-0000-4000-8000-000000000005';
    partial_missing_job_id constant uuid := 'c1000000-0000-4000-8000-000000000006';
    completed_missing_job_id constant uuid :=
        'c1000000-0000-4000-8000-000000000007';
    billable_exact_job_id constant uuid :=
        'c1000000-0000-4000-8000-000000000008';
    full_reservation_job_id constant uuid :=
        'c1000000-0000-4000-8000-000000000009';
    replay_only_miss_job_id constant uuid :=
        'c1000000-0000-4000-8000-000000000098';
    period_start timestamptz := statement_timestamp() - interval '1 hour';
    period_end timestamptz := statement_timestamp() + interval '23 hours';
    deadline timestamptz := statement_timestamp() + interval '30 hours';
    queued jsonb;
    replay jsonb;
    claimed jsonb;
    expiration_receipt jsonb;
    failure jsonb;
    active_batches jsonb;
    poll_result jsonb;
    completed jsonb;
    finalized jsonb;
    first_attempt_started_at timestamptz;
begin
    begin
        perform public.configure_agent_batch_budget(
            test_workspace_id,
            'openai:too-long',
            period_start,
            period_end + interval '1 hour',
            500000
        );
        raise exception 'batch budget accepted a period longer than 24 hours';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.configure_agent_batch_budget(
            test_workspace_id,
            'openai:above-pilot-ceiling',
            period_start,
            period_end,
            6000001
        );
        raise exception 'Batch budget accepted more than the $6 pilot ceiling';
    exception when invalid_parameter_value then null;
    end;

    perform public.configure_agent_batch_budget(
        test_workspace_id,
        'openai:pilot',
        period_start,
        period_end,
        500000
    );
    replay := public.configure_agent_batch_budget(
        test_workspace_id,
        'openai:pilot',
        period_start,
        period_end,
        500000
    );
    if replay ->> 'reused' <> 'true' then
        raise exception 'exact Batch budget replay was not reused';
    end if;
    begin
        perform public.configure_agent_batch_budget(
            test_workspace_id,
            'openai:pilot',
            period_start,
            period_end,
            500001
        );
        raise exception 'Batch budget key accepted a hard-cap raise';
    exception when check_violation then null;
    end;
    perform public.configure_agent_batch_budget(
        test_workspace_id,
        'openai:pilot-next',
        period_start,
        period_end,
        500000
    );

    begin
        perform public.queue_agent_batch_job(
            test_workspace_id,
            'origintrail',
            'c1000000-0000-4000-8000-000000000097',
            repeat('a', 62) || '97',
            'data_analyst',
            'daily_digest',
            'summarize',
            0::smallint,
            'batch_24h',
            'gpt-5.6-luna',
            'S',
            deadline,
            '{"topic":"wrong OriginTrail canary route"}'::jsonb,
            repeat('f', 62) || '97',
            100::bigint,
            100,
            1000::bigint,
            'openai:pilot-next',
            'c1000000-0000-4000-8000-000000000097:summarize:1',
            false
        );
        raise exception 'wrong OriginTrail canary identity was admitted';
    exception when invalid_parameter_value then null;
    end;

    begin
        perform public.queue_agent_batch_job(
            test_workspace_id,
            'squid',
            replay_only_miss_job_id,
            repeat('a', 62) || '98',
            'data_analyst',
            'daily_digest',
            'summarize',
            0::smallint,
            'batch_24h',
            'gpt-5.6-luna',
            'S',
            deadline,
            '{"topic":"missing replay-only receipt"}'::jsonb,
            repeat('f', 63) || '8',
            100::bigint,
            100,
            1000::bigint,
            'openai:pilot-next',
            replay_only_miss_job_id::text || ':summarize:1',
            true
        );
        raise exception 'replay-only miss created a new Batch admission';
    exception when check_violation then null;
    end;
    if exists (
        select 1
        from agent_runtime.batch_jobs
        where job_id = replay_only_miss_job_id
    ) or exists (
        select 1
        from agent_runtime.batch_budgets
        where workspace_id = test_workspace_id
          and budget_key in ('openai:pilot', 'openai:pilot-next')
          and reserved_microusd <> 0
    ) then
        raise exception 'replay-only miss created a job or reservation';
    end if;

    begin
        perform public.queue_agent_batch_job(
            test_workspace_id,
            'rogue',
            'c1000000-0000-4000-8000-000000000099'::uuid,
            repeat('a', 63) || '0',
            'rogue_client_agent',
            'daily_digest',
            'generate',
            0::smallint,
            'batch_24h',
            'gpt-5.6-luna',
            'S',
            deadline,
            '{"topic":"unsupported client"}'::jsonb,
            repeat('0', 64),
            100::bigint,
            100,
            1000::bigint,
            'openai:pilot',
            'c1000000-0000-4000-8000-000000000099:generate:1',
            false
        );
        raise exception 'unsupported active client entered the batch ledger';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.claim_agent_batch_jobs(
            test_workspace_id,
            'worker:rogue',
            array['rogue']::text[],
            100,
            300
        );
        raise exception 'unsupported active client entered the claim scope';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.expire_agent_batch_jobs(
            test_workspace_id,
            array['babylon']::text[]
        );
        raise exception 'unconfigured client entered the expiration scope';
    exception when invalid_parameter_value then null;
    end;

    queued := public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        retry_job_id,
        repeat('a', 64),
        'naver_seo_writer',
        'naver_seo_article',
        'generate',
        3::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"retry and lease recovery"}'::jsonb,
        repeat('1', 64),
        1200::bigint,
        1400,
        50000::bigint,
        'openai:pilot',
        retry_job_id::text || ':generate:1',
        false
    );
    if queued ->> 'job_id' <> retry_job_id::text
       or queued ->> 'custom_id' <> retry_job_id::text || ':generate:1'
       or queued ->> 'budget_key' <> 'openai:pilot'
       or queued ->> 'reused' <> 'false' then
        raise exception 'first-attempt identity was not queued deterministically';
    end if;
    replay := public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        retry_job_id,
        repeat('a', 64),
        'naver_seo_writer',
        'naver_seo_article',
        'generate',
        3::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"retry and lease recovery"}'::jsonb,
        repeat('1', 64),
        1200::bigint,
        1400,
        50000::bigint,
        'openai:pilot-next',
        retry_job_id::text || ':generate:1',
        true
    );
    if replay ->> 'reused' <> 'true'
       or replay ->> 'budget_key' <> 'openai:pilot'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = retry_job_id
             and budget_key = 'openai:pilot'
       )
       or not exists (
           select 1
           from agent_runtime.batch_budgets
           where workspace_id = test_workspace_id
             and budget_key = 'openai:pilot'
             and reserved_microusd = 50000
       )
       or not exists (
           select 1
           from agent_runtime.batch_budgets
           where workspace_id = test_workspace_id
             and budget_key = 'openai:pilot-next'
             and reserved_microusd = 0
       ) then
        raise exception 'cross-budget queue replay changed its durable reservation';
    end if;
    begin
        perform public.queue_agent_batch_job(
            test_workspace_id,
            'squid',
            retry_job_id,
            repeat('a', 64),
            'naver_seo_writer',
            'naver_seo_article',
            'generate',
            3::smallint,
            'batch_24h',
            'gpt-5.6-luna',
            'S',
            deadline,
            '{"topic":"immutable mismatch"}'::jsonb,
            repeat('1', 64),
            1200::bigint,
            1400,
            50000::bigint,
            'openai:pilot-next',
            retry_job_id::text || ':generate:1',
            true
        );
        raise exception 'replay-only immutable mismatch was accepted';
    exception when unique_violation then null;
    end;
    if not exists (
        select 1
        from agent_runtime.batch_jobs
        where job_id = retry_job_id
          and input_payload = '{"topic":"retry and lease recovery"}'::jsonb
          and budget_key = 'openai:pilot'
    ) or not exists (
        select 1
        from agent_runtime.batch_budgets
        where workspace_id = test_workspace_id
          and budget_key = 'openai:pilot-next'
          and reserved_microusd = 0
    ) then
        raise exception 'replay-only conflict mutated the committed job';
    end if;

    perform public.queue_agent_batch_job(
        test_workspace_id,
        'yellow',
        yellow_job_id,
        repeat('b', 64),
        'yellow_client_agent',
        'daily_digest',
        'generate',
        0::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"client allowlist"}'::jsonb,
        repeat('2', 64),
        800::bigint,
        900,
        20000::bigint,
        'openai:pilot',
        yellow_job_id::text || ':generate:1',
        false
    );

    claimed := public.claim_agent_batch_jobs(
        test_workspace_id,
        'worker:first',
        array['squid']::text[],
        100,
        300
    );
    if jsonb_array_length(claimed) <> 1
       or claimed -> 0 ->> 'job_id' <> retry_job_id::text
       or claimed -> 0 ->> 'attempt' <> '1'
       or claimed -> 0 ->> 'recovery_required' <> 'false'
       or claimed -> 0 ->> 'attempt_started_at' is null
       or claimed -> 0 ->> 'custom_id'
          <> retry_job_id::text || ':generate:1'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = yellow_job_id
             and status = 'queued'
             and attempts = 0
       ) then
        raise exception 'claim did not atomically filter the client allowlist';
    end if;
    first_attempt_started_at :=
        (claimed -> 0 ->> 'attempt_started_at')::timestamptz;

    update agent_runtime.batch_jobs
    set lease_expires_at = statement_timestamp() - interval '1 minute'
    where job_id = retry_job_id;

    claimed := public.claim_agent_batch_jobs(
        test_workspace_id,
        'worker:recovery',
        array['squid']::text[],
        100,
        300
    );
    if jsonb_array_length(claimed) <> 1
       or claimed -> 0 ->> 'attempt' <> '1'
       or claimed -> 0 ->> 'recovery_required' <> 'true'
       or (claimed -> 0 ->> 'attempt_started_at')::timestamptz
          is distinct from first_attempt_started_at
       or claimed -> 0 ->> 'custom_id'
          <> retry_job_id::text || ':generate:1' then
        raise exception 'stale lease reclaim consumed another provider attempt';
    end if;

    perform public.register_agent_batch(
        test_workspace_id,
        'worker:recovery',
        'file-input-retry-1',
        'batch-retry-1',
        array[retry_job_id]
    );
    poll_result := public.update_agent_batch_poll(
        test_workspace_id,
        'batch-retry-1',
        'completed',
        null,
        'file-error-retry-1'
    );
    if poll_result ->> 'finalized' <> 'false'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = retry_job_id
             and status = 'submitted'
             and reservation_state = 'held'
       ) then
        raise exception 'terminal poll finalized or failed a job before reconciliation';
    end if;

    failure := public.fail_agent_batch_job(
        test_workspace_id,
        retry_job_id,
        'batch-retry-1',
        'provider_request_failed',
        true,
        statement_timestamp(),
        null::bigint,
        null::bigint,
        null::bigint,
        false
    );
    if failure ->> 'status' <> 'retry_wait'
       or failure ->> 'reused' <> 'false'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = retry_job_id
             and reservation_state = 'held'
             and actual_input_tokens is null
             and actual_output_tokens is null
             and actual_cost_microusd is null
       )
       or not exists (
           select 1
           from agent_runtime.batch_budgets
           where workspace_id = test_workspace_id
             and budget_key = 'openai:pilot'
             and reserved_microusd = 70000
             and spent_microusd = 0
       ) then
        raise exception 'first provider failure did not enter retry_wait';
    end if;
    failure := public.fail_agent_batch_job(
        test_workspace_id,
        retry_job_id,
        'batch-retry-1',
        'provider_request_failed',
        true,
        statement_timestamp(),
        null::bigint,
        null::bigint,
        null::bigint,
        false
    );
    if failure ->> 'status' <> 'retry_wait'
       or failure ->> 'reused' <> 'true' then
        raise exception 'old terminal failure reconciliation was not idempotent';
    end if;

    claimed := public.claim_agent_batch_jobs(
        test_workspace_id,
        'worker:second',
        array['squid']::text[],
        100,
        300
    );
    if jsonb_array_length(claimed) <> 1
       or claimed -> 0 ->> 'attempt' <> '2'
       or claimed -> 0 ->> 'recovery_required' <> 'false'
       or claimed -> 0 ->> 'attempt_started_at' is null
       or claimed -> 0 ->> 'custom_id'
          <> retry_job_id::text || ':generate:2' then
        raise exception 'retry did not allocate deterministic provider attempt two';
    end if;

    update public.workspace_clients
    set active = false
    where workspace_id = test_workspace_id
      and client_id = 'squid';

    replay := public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        retry_job_id,
        repeat('a', 64),
        'naver_seo_writer',
        'naver_seo_article',
        'generate',
        3::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"retry and lease recovery"}'::jsonb,
        repeat('1', 64),
        1200::bigint,
        1400,
        50000::bigint,
        'openai:pilot',
        retry_job_id::text || ':generate:1',
        false
    );
    if replay ->> 'reused' <> 'true'
       or replay ->> 'custom_id'
          <> retry_job_id::text || ':generate:1'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = retry_job_id
             and custom_id = retry_job_id::text || ':generate:2'
       ) then
        raise exception 'queue replay did not return the immutable producer custom id';
    end if;

    update public.workspace_clients
    set active = true
    where workspace_id = test_workspace_id
      and client_id = 'squid';

    perform public.register_agent_batch(
        test_workspace_id,
        'worker:second',
        'file-input-retry-2',
        'batch-retry-2',
        array[retry_job_id]
    );

    failure := public.fail_agent_batch_job(
        test_workspace_id,
        retry_job_id,
        'batch-retry-1',
        'provider_request_failed',
        true,
        statement_timestamp(),
        null::bigint,
        null::bigint,
        null::bigint,
        false
    );
    if failure ->> 'stale' <> 'true'
       or failure ->> 'reused' <> 'true'
       or failure ->> 'status' <> 'submitted' then
        raise exception 'old retry outcome was not a safe stale replay';
    end if;
    finalized := public.finalize_agent_batch(
        test_workspace_id,
        'batch-retry-1'
    );
    if finalized ->> 'finalized' <> 'true'
       or finalized ->> 'missing_job_count' <> '0' then
        raise exception 'old retry batch could not be finalized after replay';
    end if;

    update agent_runtime.batch_jobs
    set deadline_at = statement_timestamp() - interval '1 minute'
    where job_id = retry_job_id;
    expiration_receipt := public.expire_agent_batch_jobs(
        test_workspace_id,
        array['squid']::text[]
    );
    if expiration_receipt ->> 'expired_job_count' <> '0'
       or expiration_receipt ->> 'released_microusd' <> '0'
       or expiration_receipt ->> 'ambiguous_claimed_count' <> '0'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = retry_job_id
             and status = 'submitted'
             and reservation_state = 'held'
       ) then
        raise exception 'expiration cleanup mutated submitted provider work';
    end if;

    perform public.update_agent_batch_poll(
        test_workspace_id,
        'batch-retry-2',
        'in_progress',
        null,
        null
    );
    expiration_receipt := public.expire_agent_batch_jobs(
        test_workspace_id,
        array['squid']::text[]
    );
    if expiration_receipt ->> 'expired_job_count' <> '0'
       or expiration_receipt ->> 'released_microusd' <> '0'
       or expiration_receipt ->> 'ambiguous_claimed_count' <> '0'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = retry_job_id
             and status = 'in_progress'
             and reservation_state = 'held'
       ) then
        raise exception 'expiration cleanup mutated in-progress provider work';
    end if;

    perform public.update_agent_batch_poll(
        test_workspace_id,
        'batch-retry-2',
        'expired',
        'file-output-retry-2',
        null
    );
    completed := public.complete_agent_batch_job(
        test_workspace_id,
        retry_job_id,
        'batch-retry-2',
        'needs_review',
        '{"draft_id":"retry-draft"}'::jsonb,
        1000::bigint,
        500::bigint,
        15000::bigint
    );
    if completed ->> 'status' <> 'completed' then
        raise exception 'partial result from expired batch was not reconcilable';
    end if;
    expiration_receipt := public.expire_agent_batch_jobs(
        test_workspace_id,
        array['squid']::text[]
    );
    if expiration_receipt ->> 'expired_job_count' <> '0'
       or expiration_receipt ->> 'released_microusd' <> '0'
       or expiration_receipt ->> 'ambiguous_claimed_count' <> '0'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = retry_job_id
             and status = 'completed'
             and reservation_state = 'settled'
       ) then
        raise exception 'expiration cleanup mutated completed provider work';
    end if;
    finalized := public.finalize_agent_batch(
        test_workspace_id,
        'batch-retry-2'
    );
    if finalized ->> 'missing_job_count' <> '0' then
        raise exception 'reconciled expired batch retained a missing result';
    end if;

    perform public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        expired_job_id,
        repeat('c', 64),
        'data_analyst',
        'daily_digest',
        'summarize',
        0::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"deadline sweep"}'::jsonb,
        repeat('3', 64),
        500::bigint,
        500,
        10000::bigint,
        'openai:pilot',
        expired_job_id::text || ':summarize:1',
        false
    );
    update agent_runtime.batch_jobs
    set deadline_at = statement_timestamp() - interval '1 minute'
    where job_id = expired_job_id;

    perform public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        expired_retry_job_id,
        repeat('c', 63) || '0',
        'data_analyst',
        'daily_digest',
        'summarize',
        0::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"retry deadline sweep"}'::jsonb,
        repeat('3', 63) || '0',
        500::bigint,
        500,
        10000::bigint,
        'openai:pilot',
        expired_retry_job_id::text || ':summarize:1',
        false
    );
    update agent_runtime.batch_jobs
    set status = 'retry_wait',
        attempts = 1,
        error_code = 'retry_scheduled',
        available_at = statement_timestamp(),
        deadline_at = statement_timestamp() - interval '1 minute'
    where job_id = expired_retry_job_id;

    perform public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        exhausted_job_id,
        repeat('d', 64),
        'data_analyst',
        'daily_digest',
        'summarize',
        0::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"attempt sweep"}'::jsonb,
        repeat('4', 64),
        500::bigint,
        500,
        10000::bigint,
        'openai:pilot',
        exhausted_job_id::text || ':summarize:1',
        false
    );
    update agent_runtime.batch_jobs
    set status = 'retry_wait',
        attempts = 2,
        custom_id = exhausted_job_id::text || ':summarize:2',
        error_code = 'retry_exhausted',
        available_at = statement_timestamp()
    where job_id = exhausted_job_id;

    expiration_receipt := public.expire_agent_batch_jobs(
        test_workspace_id,
        array['squid']::text[]
    );
    if expiration_receipt ->> 'expired_job_count' <> '3'
       or expiration_receipt ->> 'released_microusd' <> '30000'
       or expiration_receipt ->> 'ambiguous_claimed_count' <> '0'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = expired_job_id
             and status = 'failed'
             and error_code = 'batch_deadline_expired'
             and reservation_state = 'released'
       )
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = expired_retry_job_id
             and status = 'failed'
             and error_code = 'batch_deadline_expired'
             and reservation_state = 'released'
       )
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = exhausted_job_id
             and status = 'failed'
             and error_code = 'batch_attempts_exhausted'
             and reservation_state = 'released'
       ) then
        raise exception 'expiration cleanup did not release all three pre-provider cases';
    end if;
    expiration_receipt := public.expire_agent_batch_jobs(
        test_workspace_id,
        array['squid']::text[]
    );
    if expiration_receipt ->> 'expired_job_count' <> '0'
       or expiration_receipt ->> 'released_microusd' <> '0'
       or expiration_receipt ->> 'ambiguous_claimed_count' <> '0' then
        raise exception 'expiration cleanup was not idempotent';
    end if;

    update agent_runtime.batch_jobs
    set deadline_at = statement_timestamp() - interval '1 minute'
    where job_id = yellow_job_id;
    update public.workspace_clients
    set active = false
    where workspace_id = test_workspace_id
      and client_id = 'yellow';
    expiration_receipt := public.expire_agent_batch_jobs(
        test_workspace_id,
        array['yellow']::text[]
    );
    if expiration_receipt ->> 'expired_job_count' <> '1'
       or expiration_receipt ->> 'released_microusd' <> '20000'
       or expiration_receipt ->> 'ambiguous_claimed_count' <> '0'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = yellow_job_id
             and status = 'failed'
             and error_code = 'batch_deadline_expired'
       ) then
        raise exception 'expiration cleanup rejected an inactive client scope';
    end if;
    update public.workspace_clients
    set active = true
    where workspace_id = test_workspace_id
      and client_id = 'yellow';

    perform public.configure_agent_batch_budget(
        test_workspace_id,
        'openai:expiration-canary',
        period_start,
        period_end,
        100000
    );
    perform public.queue_agent_batch_job(
        test_workspace_id,
        'yellow',
        ambiguous_claimed_job_id,
        repeat('b', 63) || '0',
        'yellow_client_agent',
        'daily_digest',
        'generate',
        0::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"ambiguous claimed cleanup"}'::jsonb,
        repeat('2', 63) || '0',
        500::bigint,
        500,
        10000::bigint,
        'openai:expiration-canary',
        ambiguous_claimed_job_id::text || ':generate:1',
        false
    );
    claimed := public.claim_agent_batch_jobs(
        test_workspace_id,
        'worker:ambiguous',
        array['yellow']::text[],
        100,
        300
    );
    if jsonb_array_length(claimed) <> 1
       or claimed -> 0 ->> 'job_id' <> ambiguous_claimed_job_id::text then
        raise exception 'ambiguous claimed canary was not claimed';
    end if;
    update agent_runtime.batch_jobs
    set deadline_at = statement_timestamp() - interval '1 minute'
    where job_id = ambiguous_claimed_job_id;

    expiration_receipt := public.expire_agent_batch_jobs(
        test_workspace_id,
        array['yellow']::text[]
    );
    if expiration_receipt ->> 'expired_job_count' <> '0'
       or expiration_receipt ->> 'released_microusd' <> '0'
       or expiration_receipt ->> 'ambiguous_claimed_count' <> '1'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = ambiguous_claimed_job_id
             and status = 'claimed'
             and reservation_state = 'held'
             and locked_by = 'worker:ambiguous'
             and attempts = 1
       )
       or not exists (
           select 1
           from agent_runtime.batch_budgets
           where workspace_id = test_workspace_id
             and budget_key = 'openai:expiration-canary'
             and reserved_microusd = 10000
             and spent_microusd = 0
       ) then
        raise exception 'expiration cleanup released ambiguous claimed provider work';
    end if;
    expiration_receipt := public.expire_agent_batch_jobs(
        test_workspace_id,
        array['yellow']::text[]
    );
    if expiration_receipt ->> 'expired_job_count' <> '0'
       or expiration_receipt ->> 'released_microusd' <> '0'
       or expiration_receipt ->> 'ambiguous_claimed_count' <> '1' then
        raise exception 'ambiguous claimed cleanup receipt was not idempotent';
    end if;
    claimed := public.claim_agent_batch_jobs(
        test_workspace_id,
        'worker:ambiguous-recovery',
        array['yellow']::text[],
        100,
        300
    );
    if jsonb_array_length(claimed) <> 0
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = ambiguous_claimed_job_id
             and status = 'claimed'
             and reservation_state = 'held'
             and locked_by = 'worker:ambiguous'
       ) then
        raise exception 'claim cleanup reclaimed expired ambiguous provider work';
    end if;

    perform public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        partial_ok_job_id,
        repeat('e', 64),
        'partnership_researcher',
        'partnership_screen',
        'generate',
        2::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"account":"partial success"}'::jsonb,
        repeat('5', 64),
        900::bigint,
        900,
        50000::bigint,
        'openai:pilot',
        partial_ok_job_id::text || ':generate:1',
        false
    );
    perform public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        partial_missing_job_id,
        repeat('f', 64),
        'partnership_researcher',
        'partnership_screen',
        'generate',
        2::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"account":"partial missing"}'::jsonb,
        repeat('6', 64),
        900::bigint,
        900,
        50000::bigint,
        'openai:pilot',
        partial_missing_job_id::text || ':generate:1',
        false
    );
    claimed := public.claim_agent_batch_jobs(
        test_workspace_id,
        'worker:partial',
        array['squid']::text[],
        100,
        300
    );
    if jsonb_array_length(claimed) <> 2 then
        raise exception 'partial batch jobs were not claimed together';
    end if;
    perform public.register_agent_batch(
        test_workspace_id,
        'worker:partial',
        'file-input-partial',
        'batch-partial',
        array[partial_ok_job_id, partial_missing_job_id]
    );
    perform public.update_agent_batch_poll(
        test_workspace_id,
        'batch-partial',
        'failed',
        null,
        'file-error-partial'
    );
    if exists (
        select 1
        from agent_runtime.batch_runs
        where workspace_id = test_workspace_id
          and batch_id = 'batch-partial'
          and finalized_at is not null
    ) or (
        select count(*)
        from agent_runtime.batch_jobs
        where job_id in (partial_ok_job_id, partial_missing_job_id)
          and status = 'submitted'
          and reservation_state = 'held'
    ) <> 2 then
        raise exception 'terminal provider poll mutated unreconciled jobs';
    end if;

    perform public.update_agent_batch_poll(
        test_workspace_id,
        'batch-partial',
        'failed',
        null,
        null
    );
    begin
        perform public.update_agent_batch_poll(
            test_workspace_id,
            'batch-partial',
            'failed',
            null,
            'file-error-different'
        );
        raise exception 'provider error file id changed after first observation';
    exception when unique_violation then null;
    end;

    perform public.complete_agent_batch_job(
        test_workspace_id,
        partial_ok_job_id,
        'batch-partial',
        'needs_review',
        '{"draft_id":"partial-draft"}'::jsonb,
        700::bigint,
        300::bigint,
        10000::bigint
    );
    finalized := public.finalize_agent_batch(
        test_workspace_id,
        'batch-partial'
    );
    if finalized ->> 'missing_job_count' <> '1'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = partial_missing_job_id
             and status = 'failed'
             and error_code = 'provider_batch_failed'
             and reservation_state = 'released'
             and actual_cost_microusd is null
       ) then
        raise exception 'failed partial batch did not close only its missing line';
    end if;

    perform public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        completed_missing_job_id,
        repeat('0', 64),
        'data_analyst',
        'daily_digest',
        'summarize',
        0::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"completed missing result"}'::jsonb,
        repeat('7', 64),
        500::bigint,
        500,
        20000::bigint,
        'openai:pilot',
        completed_missing_job_id::text || ':summarize:1',
        false
    );
    claimed := public.claim_agent_batch_jobs(
        test_workspace_id,
        'worker:missing',
        array['squid']::text[],
        100,
        300
    );
    perform public.register_agent_batch(
        test_workspace_id,
        'worker:missing',
        'file-input-missing',
        'batch-missing',
        array[completed_missing_job_id]
    );
    begin
        perform public.update_agent_batch_poll(
            test_workspace_id,
            'batch-missing',
            'completed',
            null,
            null
        );
        raise exception 'completed provider batch accepted no result files';
    exception when check_violation then null;
    end;
    begin
        update agent_runtime.batch_runs
        set provider_status = 'completed'
        where workspace_id = test_workspace_id
          and batch_id = 'batch-missing';
        perform public.finalize_agent_batch(
            test_workspace_id,
            'batch-missing'
        );
        raise exception 'completed provider batch finalized without result files';
    exception when check_violation then null;
    end;
    poll_result := public.update_agent_batch_poll(
        test_workspace_id,
        'batch-missing',
        'completed',
        null,
        'file-error-missing'
    );
    if poll_result ->> 'error_file_id' <> 'file-error-missing' then
        raise exception 'terminal first-non-null error file was not recorded';
    end if;
    finalized := public.finalize_agent_batch(
        test_workspace_id,
        'batch-missing'
    );
    if finalized ->> 'missing_job_count' <> '1'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = completed_missing_job_id
             and status = 'failed'
             and error_code = 'batch_result_missing'
             and reservation_state = 'released'
             and actual_cost_microusd = 20000
       ) then
        raise exception 'completed batch missing result used the wrong safe code';
    end if;

    perform public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        billable_exact_job_id,
        repeat('8', 64),
        'content_qa_agent',
        'content_qa',
        'qa',
        1::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"billable refusal"}'::jsonb,
        repeat('8', 64),
        800::bigint,
        500,
        50000::bigint,
        'openai:pilot',
        billable_exact_job_id::text || ':qa:1',
        false
    );
    perform public.queue_agent_batch_job(
        test_workspace_id,
        'squid',
        full_reservation_job_id,
        repeat('9', 64),
        'content_qa_agent',
        'content_qa',
        'qa',
        1::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        deadline,
        '{"topic":"missing usage fallback"}'::jsonb,
        repeat('9', 64),
        800::bigint,
        500,
        50000::bigint,
        'openai:pilot',
        full_reservation_job_id::text || ':qa:1',
        false
    );
    claimed := public.claim_agent_batch_jobs(
        test_workspace_id,
        'worker:billable',
        array['squid']::text[],
        100,
        300
    );
    if jsonb_array_length(claimed) <> 2 then
        raise exception 'billable failure jobs were not claimed';
    end if;
    perform public.register_agent_batch(
        test_workspace_id,
        'worker:billable',
        'file-input-billable',
        'batch-billable',
        array[billable_exact_job_id, full_reservation_job_id]
    );
    perform public.update_agent_batch_poll(
        test_workspace_id,
        'batch-billable',
        'completed',
        null,
        'file-error-billable'
    );

    begin
        perform public.fail_agent_batch_job(
            test_workspace_id,
            billable_exact_job_id,
            'batch-billable',
            'model_refusal',
            false,
            null::timestamptz,
            800::bigint,
            20::bigint,
            60000::bigint,
            false
        );
        raise exception 'failure accounting exceeded the hard reservation';
    exception when check_violation then null;
    end;
    if not exists (
        select 1
        from agent_runtime.batch_jobs
        where job_id = billable_exact_job_id
          and status = 'submitted'
          and reservation_state = 'held'
          and actual_cost_microusd is null
    ) or not exists (
        select 1
        from agent_runtime.batch_budgets
        where workspace_id = test_workspace_id
          and budget_key = 'openai:pilot'
          and reserved_microusd = 100000
          and spent_microusd = 45000
    ) then
        raise exception 'over-cost failure changed held accounting';
    end if;

    failure := public.fail_agent_batch_job(
        test_workspace_id,
        billable_exact_job_id,
        'batch-billable',
        'model_refusal',
        false,
        null::timestamptz,
        800::bigint,
        20::bigint,
        12000::bigint,
        false
    );
    if failure ->> 'actual_cost_microusd' <> '12000'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = billable_exact_job_id
             and status = 'failed'
             and reservation_state = 'released'
             and actual_input_tokens = 800
             and actual_output_tokens = 20
             and actual_cost_microusd = 12000
       ) then
        raise exception 'exact billable failure was not settled';
    end if;
    failure := public.fail_agent_batch_job(
        test_workspace_id,
        billable_exact_job_id,
        'batch-billable',
        'model_refusal',
        false,
        null::timestamptz,
        800::bigint,
        20::bigint,
        12000::bigint,
        false
    );
    if failure ->> 'reused' <> 'true' then
        raise exception 'exact failure accounting replay was not idempotent';
    end if;
    begin
        perform public.fail_agent_batch_job(
            test_workspace_id,
            billable_exact_job_id,
            'batch-billable',
            'model_refusal',
            false,
            null::timestamptz,
            800::bigint,
            20::bigint,
            13000::bigint,
            false
        );
        raise exception 'failed replay changed settled accounting';
    exception when unique_violation then null;
    end;

    failure := public.fail_agent_batch_job(
        test_workspace_id,
        full_reservation_job_id,
        'batch-billable',
        'usage_unavailable',
        false,
        null::timestamptz,
        null::bigint,
        null::bigint,
        null::bigint,
        true
    );
    if failure ->> 'actual_cost_microusd' <> '50000'
       or not exists (
           select 1
           from agent_runtime.batch_jobs
           where job_id = full_reservation_job_id
             and status = 'failed'
             and reservation_state = 'released'
             and actual_input_tokens is null
             and actual_output_tokens is null
             and actual_cost_microusd = 50000
       ) then
        raise exception 'full-reservation fallback was not settled';
    end if;
    finalized := public.finalize_agent_batch(
        test_workspace_id,
        'batch-billable'
    );
    if finalized ->> 'missing_job_count' <> '0' then
        raise exception 'settled billable failures remained unreconciled';
    end if;

    active_batches := public.list_active_agent_batches(
        test_workspace_id,
        100
    );
    if jsonb_array_length(active_batches) <> 0 then
        raise exception 'finalized retry batch remained in the active list';
    end if;

    if not exists (
        select 1
        from agent_runtime.batch_budgets
        where workspace_id = test_workspace_id
          and budget_key = 'openai:pilot'
          and reserved_microusd = 0
          and spent_microusd = 107000
    ) then
        raise exception 'batch lifecycle leaked or double-settled a reservation';
    end if;
end
$test$;

do $test$
declare
    test_workspace_id constant uuid :=
        'c0000000-0000-4000-8000-000000000001';
    review_job_ids constant uuid[] := array[
        'd1000000-0000-4000-8000-000000000001'::uuid,
        'd1000000-0000-4000-8000-000000000002'::uuid,
        'd1000000-0000-4000-8000-000000000003'::uuid,
        'd1000000-0000-4000-8000-000000000004'::uuid
    ];
    sync_route_job_id constant uuid :=
        'd0000000-0000-4000-8000-000000000001';
    batch_route_job_id constant uuid :=
        'd0000000-0000-4000-8000-000000000002';
    no_receipt_retry_job_id constant uuid :=
        'd0000000-0000-4000-8000-000000000005';
    batch_route_source_id constant uuid :=
        'e0000000-0000-4000-8000-000000000001';
    legacy_source_id constant uuid :=
        'e0000000-0000-4000-8000-000000000002';
    legacy_route_job_id constant uuid :=
        'd0000000-0000-4000-8000-000000000099';
    input_hashes constant text[] := array[
        repeat('b', 63) || '1',
        repeat('b', 63) || '2',
        repeat('b', 63) || '3',
        repeat('b', 63) || '4'
    ];
    review_job_id uuid;
    input_hash text;
    expected_output jsonb;
    handoff jsonb;
    replay jsonb;
    route_receipt jsonb;
    failure_receipt jsonb;
    claimed jsonb;
    completed jsonb;
    finalized jsonb;
    review_payload jsonb;
    inbox jsonb;
    next_page jsonb;
    detail jsonb;
    candidate jsonb;
    batch_input_payload jsonb;
    item_index integer;
    content_item_count_before bigint;
    content_version_count_before bigint;
    approval_count_before bigint;
    publication_count_before bigint;
    batch_run_count_before bigint;
begin
    insert into public.source_items (
        id, workspace_id, client_id, source_feed_id, external_id,
        source_type, canonical_url, author_handle, published_at, body,
        media, raw_payload, source_hash, ingested_by
    ) values (
        batch_route_source_id,
        test_workspace_id,
        'origintrail',
        'c2000000-0000-4000-8000-000000000001',
        '2000000000000000000',
        'tweet',
        'https://x.com/origin_trail/status/2000000000000000000',
        '@origin_trail',
        statement_timestamp() - interval '1 hour',
        'Pinned text-only OriginTrail canary route evidence.',
        '[]'::jsonb,
        '{"is_note_tweet":false,"metrics":{}}'::jsonb,
        pg_catalog.md5('origintrail-batch-route-source'),
        null
    );
    insert into public.source_items (
        id, workspace_id, client_id, source_feed_id, external_id,
        source_type, canonical_url, author_handle, published_at, body,
        media, raw_payload, source_hash, ingested_by
    ) values (
        legacy_source_id,
        test_workspace_id,
        'origintrail',
        'c2000000-0000-4000-8000-000000000001',
        '1999999999999999999',
        'tweet',
        'https://x.com/origin_trail/status/1999999999999999999',
        '@origin_trail',
        statement_timestamp() - interval '2 hours',
        'A pre-cutover source with no durable quote classification.',
        '[]'::jsonb,
        '{"is_note_tweet":false,"metrics":{}}'::jsonb,
        pg_catalog.md5('origintrail-pre-cutover-source'),
        null
    );
    insert into private.official_x_source_state (
        workspace_id, client_id, source_item_id
    ) values (
        test_workspace_id, 'origintrail', legacy_source_id
    );
    insert into public.source_items (
        id, workspace_id, client_id, source_feed_id, external_id,
        source_type, canonical_url, author_handle, published_at, body,
        media, raw_payload, source_hash, ingested_by
    )
    select
        ('e1000000-0000-4000-8000-00000000000'
            || source_index::text)::uuid,
        test_workspace_id,
        'origintrail',
        'c2000000-0000-4000-8000-000000000001'::uuid,
        (2000000000000000000 + source_index)::text,
        'tweet',
        'https://x.com/origin_trail/status/'
            || (2000000000000000000 + source_index)::text,
        '@origin_trail',
        statement_timestamp() - make_interval(mins => source_index),
        'Pinned official OriginTrail source evidence.',
        '[]'::jsonb,
        '{"is_note_tweet":false,"metrics":{}}'::jsonb,
        pg_catalog.md5('origintrail-review-source:' || source_index::text),
        null
    from generate_series(1, 4) as source_index;

    insert into private.origintrail_standalone_sources (
        workspace_id, client_id, source_item_id, is_quote,
        first_poll_request_id, verified_at
    )
    select
        test_workspace_id,
        'origintrail',
        source.id,
        false,
        'e3000000-0000-4000-8000-000000000001'::uuid,
        statement_timestamp()
    from public.source_items as source
    where source.id = batch_route_source_id
       or source.id = any(array[
           'e1000000-0000-4000-8000-000000000001'::uuid,
           'e1000000-0000-4000-8000-000000000002'::uuid,
           'e1000000-0000-4000-8000-000000000003'::uuid,
           'e1000000-0000-4000-8000-000000000004'::uuid
       ]);

    select count(*) into content_item_count_before
    from public.content_items
    where workspace_id = test_workspace_id;
    select count(*) into content_version_count_before
    from public.content_versions
    where workspace_id = test_workspace_id;
    select count(*) into approval_count_before
    from public.approvals
    where workspace_id = test_workspace_id;
    select count(*) into publication_count_before
    from public.publications
    where workspace_id = test_workspace_id;

    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority,
        input, output, idempotency_key, attempts, max_attempts,
        available_at
    ) values (
        legacy_route_job_id,
        test_workspace_id,
        'origintrail',
        'generate',
        'queued',
        0,
        jsonb_build_object(
            'workflow', 'official_x_review_draft_v1',
            'kst_date',
                pg_catalog.timezone(
                    'Asia/Seoul', statement_timestamp()
                )::date,
            'source_item_ids', jsonb_build_array(legacy_source_id),
            'content_kind', 'daily_news',
            'request_id',
                'e2000000-0000-4000-8000-000000000099'::uuid,
            'source_content',
                'A pre-cutover source with no durable quote classification.',
            'source_url',
                'https://x.com/origin_trail/status/1999999999999999999',
            'source_image_url', '',
            'manual_only', false
        ),
        '{}'::jsonb,
        'origintrail-pre-cutover-studio-route',
        0,
        3,
        statement_timestamp()
    );
    claimed := public.claim_review_draft_job(
        test_workspace_id,
        'official-x:legacy-route',
        900
    );
    if claimed ->> 'job_id' <> legacy_route_job_id::text
       or claimed ->> 'origintrail_batch_eligible' <> 'false' then
        raise exception 'pre-cutover OriginTrail source was Batch eligible';
    end if;
    route_receipt := public.bind_review_draft_execution_plane(
        legacy_route_job_id,
        'official-x:legacy-route',
        'studio_sync'
    );
    if route_receipt ->> 'execution_plane' <> 'studio_sync' then
        raise exception 'pre-cutover OriginTrail source did not route Studio';
    end if;
    perform public.fail_review_draft_job(
        legacy_route_job_id,
        'official-x:legacy-route',
        'legacy_route_canary_done',
        'Close the pre-cutover source route canary.',
        false,
        null
    );

    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority,
        input, output, idempotency_key, attempts, max_attempts,
        available_at, locked_by, locked_at, lease_expires_at, started_at
    ) values (
        sync_route_job_id,
        test_workspace_id,
        'squid',
        'generate',
        'running',
        0,
        jsonb_build_object(
            'workflow', 'official_x_review_draft_v1',
            'manual_only', false
        ),
        '{}'::jsonb,
        'execution-plane-sync-canary',
        1,
        3,
        statement_timestamp(),
        'official-x:sync-route',
        statement_timestamp(),
        statement_timestamp() + interval '15 minutes',
        statement_timestamp()
    );
    route_receipt := public.bind_review_draft_execution_plane(
        sync_route_job_id,
        'official-x:sync-route',
        'studio_sync'
    );
    if route_receipt ->> 'execution_plane' <> 'studio_sync'
       or route_receipt ->> 'reused' <> 'false' then
        raise exception 'sync execution plane was not bound atomically';
    end if;
    failure_receipt := public.fail_review_draft_job(
        sync_route_job_id,
        'official-x:sync-route',
        'sync_retry_canary',
        'Retry after the deployment phase changes.',
        true,
        statement_timestamp()
    );
    if failure_receipt ->> 'status' <> 'retrying'
       or not exists (
           select 1
           from public.jobs
           where id = sync_route_job_id
             and output ->> 'execution_plane' = 'studio_sync'
             and output ? 'last_failure'
       ) then
        raise exception 'review draft failure erased the execution plane marker';
    end if;
    claimed := public.claim_review_draft_job(
        test_workspace_id,
        'official-x:sync-route-retry',
        900
    );
    if claimed ->> 'job_id' <> sync_route_job_id::text then
        raise exception 'sync-bound review retry was not reclaimed';
    end if;
    route_receipt := public.bind_review_draft_execution_plane(
        sync_route_job_id,
        'official-x:sync-route-retry',
        'openai_batch'
    );
    if route_receipt ->> 'execution_plane' <> 'studio_sync'
       or route_receipt ->> 'reused' <> 'true'
       or not exists (
           select 1
           from public.jobs
           where id = sync_route_job_id
             and output ->> 'execution_plane' = 'studio_sync'
       ) then
        raise exception 'sync-bound retry under Batch phase changed planes';
    end if;
    perform public.fail_review_draft_job(
        sync_route_job_id,
        'official-x:sync-route-retry',
        'sync_route_canary_done',
        'Close the sync route canary.',
        false,
        null
    );

    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority,
        input, output, idempotency_key, attempts, max_attempts,
        available_at, locked_by, locked_at, lease_expires_at, started_at
    ) values (
        batch_route_job_id,
        test_workspace_id,
        'origintrail',
        'generate',
        'running',
        0,
        jsonb_build_object(
            'workflow', 'official_x_review_draft_v1',
            'source_item_ids', jsonb_build_array(batch_route_source_id),
            'content_kind', 'daily_news',
            'source_image_url', '',
            'manual_only', false
        ),
        '{}'::jsonb,
        'execution-plane-batch-canary',
        1,
        3,
        statement_timestamp(),
        'official-x:batch-route',
        statement_timestamp(),
        statement_timestamp() + interval '15 minutes',
        statement_timestamp()
    );
    begin
        perform public.bind_review_draft_execution_plane(
            batch_route_job_id,
            'official-x:wrong-route-worker',
            'openai_batch'
        );
        raise exception 'wrong worker bound a review execution plane';
    exception when object_not_in_prerequisite_state then null;
    end;
    update public.jobs
    set lease_expires_at = statement_timestamp() - interval '1 minute'
    where id = batch_route_job_id;
    begin
        perform public.bind_review_draft_execution_plane(
            batch_route_job_id,
            'official-x:batch-route',
            'openai_batch'
        );
        raise exception 'expired lease bound a review execution plane';
    exception when object_not_in_prerequisite_state then null;
    end;
    update public.jobs
    set lease_expires_at = statement_timestamp() + interval '15 minutes'
    where id = batch_route_job_id;
    update public.source_items
    set media = '[{"type":"video","url":"https://pbs.twimg.com/media/canary.jpg"}]'::jsonb
    where id = batch_route_source_id;
    begin
        perform public.bind_review_draft_execution_plane(
            batch_route_job_id,
            'official-x:batch-route',
            'openai_batch'
        );
        raise exception 'media-backed OriginTrail job entered the Batch plane';
    exception when check_violation then null;
    end;
    update public.source_items
    set media = '[]'::jsonb
    where id = batch_route_source_id;
    route_receipt := public.bind_review_draft_execution_plane(
        batch_route_job_id,
        'official-x:batch-route',
        'openai_batch'
    );
    if route_receipt ->> 'execution_plane' <> 'openai_batch'
       or route_receipt ->> 'reused' <> 'false' then
        raise exception 'Batch execution plane was not bound atomically';
    end if;
    route_receipt := public.bind_review_draft_execution_plane(
        batch_route_job_id,
        'official-x:batch-route',
        'studio_sync'
    );
    if route_receipt ->> 'execution_plane' <> 'openai_batch'
       or route_receipt ->> 'reused' <> 'true'
       or not exists (
           select 1
           from public.jobs
           where id = batch_route_job_id
             and output ->> 'execution_plane' = 'openai_batch'
       ) then
        raise exception 'same lease changed the bound Batch execution plane';
    end if;
    perform public.fail_review_draft_job(
        batch_route_job_id,
        'official-x:batch-route',
        'batch_retry_canary',
        'Retry after the deployment phase changes.',
        true,
        statement_timestamp()
    );
    claimed := public.claim_review_draft_job(
        test_workspace_id,
        'official-x:batch-route-retry',
        900
    );
    if claimed ->> 'job_id' <> batch_route_job_id::text then
        raise exception 'Batch-bound review retry was not reclaimed';
    end if;
    route_receipt := public.bind_review_draft_execution_plane(
        batch_route_job_id,
        'official-x:batch-route-retry',
        'studio_sync'
    );
    if route_receipt ->> 'execution_plane' <> 'openai_batch'
       or route_receipt ->> 'reused' <> 'true'
       or not exists (
           select 1
           from public.jobs
           where id = batch_route_job_id
             and output ->> 'execution_plane' = 'openai_batch'
       ) then
        raise exception 'Batch-bound retry after phase changed planes';
    end if;
    perform public.fail_review_draft_job(
        batch_route_job_id,
        'official-x:batch-route-retry',
        'batch_route_canary_done',
        'Close the Batch route canary.',
        false,
        null
    );
    if (
        select count(*)
        from public.event_log
        where workspace_id = test_workspace_id
          and entity_type = 'job'
          and entity_id in (sync_route_job_id, batch_route_job_id)
          and event_type =
                'official_x_review_draft_execution_plane_bound'
    ) <> 2 then
        raise exception 'execution plane binding event was missing or duplicated';
    end if;

    perform public.configure_agent_batch_budget(
        test_workspace_id,
        'openai:review-handoff',
        statement_timestamp() - interval '1 hour',
        statement_timestamp() + interval '23 hours',
        100000
    );

    for item_index in 1..4
    loop
        review_job_id := review_job_ids[item_index];
        input_hash := input_hashes[item_index];

        insert into public.jobs (
            id, workspace_id, client_id, job_kind, status, priority,
            input, output, idempotency_key, attempts, max_attempts,
            available_at, locked_by, locked_at, lease_expires_at, started_at
        ) values (
            review_job_id,
            test_workspace_id,
            'origintrail',
            'generate',
            'running',
            0,
            jsonb_build_object(
                'workflow', 'official_x_review_draft_v1',
                'kst_date',
                    pg_catalog.timezone(
                        'Asia/Seoul', statement_timestamp()
                    )::date,
                'source_item_ids',
                    jsonb_build_array(
                        ('e1000000-0000-4000-8000-00000000000'
                            || item_index::text)::uuid
                    ),
                'content_kind', 'daily_news',
                'request_id',
                    ('e2000000-0000-4000-8000-00000000000'
                        || item_index::text)::uuid,
                'source_content', 'Pinned official OriginTrail source evidence.',
                'source_url',
                    'https://x.com/origin_trail/status/'
                        || (2000000000000000000 + item_index)::text,
                'source_image_url', '',
                'manual_only', false
            ),
            '{}'::jsonb,
            'agent-batch-review-handoff:' || item_index::text,
            1,
            3,
            statement_timestamp(),
            'official-x:handoff-worker',
            statement_timestamp(),
            statement_timestamp() + interval '15 minutes',
            statement_timestamp()
        );

        batch_input_payload := jsonb_build_object(
            'instructions', 'Return a review-only Korean draft.',
            'input', jsonb_build_object(
                'client_id', 'origintrail',
                'content_kind', 'daily_news',
                'request_id',
                    ('e2000000-0000-4000-8000-00000000000'
                        || item_index::text)::uuid,
                'source', jsonb_build_object(
                    'content',
                        'Pinned official OriginTrail source evidence.',
                    'url',
                        'https://x.com/origin_trail/status/'
                            || (2000000000000000000 + item_index)::text
                )
            )::text,
            'output_schema', jsonb_build_object(
                'type', 'object',
                'properties', jsonb_build_object(
                    'headline_ko', jsonb_build_object(
                        'type', 'string', 'maxLength', 120
                    ),
                    'body_ko', jsonb_build_object(
                        'type', 'string', 'maxLength', 1800
                    ),
                    'x_copy_ko', jsonb_build_object(
                        'type', 'string', 'maxLength', 500
                    ),
                    'telegram_copy_ko', jsonb_build_object(
                        'type', 'string', 'maxLength', 1800
                    )
                ),
                'required', jsonb_build_array(
                    'headline_ko', 'body_ko', 'x_copy_ko',
                    'telegram_copy_ko'
                ),
                'additionalProperties', false
            ),
            'estimated_output_tokens', 200,
            'risk_tier', 'T1',
            'approval_required', true,
            'interactive', false,
            'incident_or_release_blocker', false,
            'live_tools_required', false,
            'source_snapshot_complete', true,
            'input_immutable', true,
            'retry_idempotent', true,
            'remaining_batch_stages', 1
        );

        perform public.queue_agent_batch_job(
            test_workspace_id,
            'origintrail',
            review_job_id,
            repeat('a', 63) || item_index::text,
            'origintrail_client_agent',
            'official_source_nonurgent_pack',
            'generate',
            3::smallint,
            'batch_24h',
            'gpt-5.6-luna',
            'S',
            statement_timestamp() + interval '30 hours',
            batch_input_payload,
            input_hash,
            500::bigint,
            500,
            10000::bigint,
            'openai:review-handoff',
            review_job_id::text || ':generate:1',
            false
        );
    end loop;
    update agent_runtime.batch_jobs
    set deadline_at = statement_timestamp() - interval '1 minute'
    where job_id = review_job_ids[4];
    select count(*) into batch_run_count_before
    from agent_runtime.batch_runs
    where workspace_id = test_workspace_id;

    route_receipt := public.bind_review_draft_execution_plane(
        review_job_ids[4],
        'official-x:handoff-worker',
        'openai_batch'
    );
    if route_receipt ->> 'execution_plane' <> 'openai_batch'
       or route_receipt ->> 'reused' <> 'false' then
        raise exception 'recovery canary did not bind the Batch plane';
    end if;
    update public.jobs
    set status = 'failed',
        attempts = max_attempts,
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        finished_at = statement_timestamp()
    where id = review_job_ids[4];
    claimed := public.claim_review_draft_job(
        test_workspace_id,
        'official-x:handoff-recovery',
        900
    );
    if claimed is not null then
        raise exception 'max-attempt Batch recovery bypassed its cooldown';
    end if;
    update public.jobs
    set finished_at = statement_timestamp() - interval '2 minutes'
    where id = review_job_ids[4];
    claimed := public.claim_review_draft_job(
        test_workspace_id,
        'official-x:handoff-recovery',
        900
    );
    if claimed ->> 'job_id' <> review_job_ids[4]::text
       or claimed ->> 'attempts' <> '3'
       or claimed ->> 'max_attempts' <> '3'
       or claimed ->> 'batch_handoff_recovery_only' <> 'true'
       or not exists (
           select 1
           from public.jobs
           where id = review_job_ids[4]
             and attempts = max_attempts
             and output -> 'batch_handoff_recovery_only' = 'true'::jsonb
       ) then
        raise exception 'max-attempt Batch handoff recovery claim is invalid';
    end if;
    begin
        perform public.recover_review_draft_batch_handoff(
            review_job_ids[4],
            'official-x:wrong-recovery-worker'
        );
        raise exception 'another worker consumed the stored recovery receipt';
    exception when object_not_in_prerequisite_state then null;
    end;
    handoff := public.recover_review_draft_batch_handoff(
        review_job_ids[4],
        'official-x:handoff-recovery'
    );
    if handoff ->> 'status' <> 'succeeded'
       or handoff ->> 'batch_job_id' <> review_job_ids[4]::text
       or handoff ->> 'input_sha256' <> input_hashes[4]
       or handoff ->> 'reused' <> 'false' then
        raise exception 'stored Batch handoff receipt was not completed';
    end if;
    if not exists (
        select 1
        from agent_runtime.batch_budgets
        where workspace_id = test_workspace_id
          and budget_key = 'openai:review-handoff'
          and reserved_microusd = 40000
          and spent_microusd = 0
    ) or (
        select count(*)
        from agent_runtime.batch_runs
        where workspace_id = test_workspace_id
    ) <> batch_run_count_before then
        raise exception 'stored receipt recovery created Batch work or cost';
    end if;
    if (
        select count(*)
        from public.event_log
        where workspace_id = test_workspace_id
          and entity_type = 'job'
          and entity_id = review_job_ids[4]
          and event_type =
                'official_x_review_draft_batch_handoff_recovery_claimed'
    ) <> 1 then
        raise exception 'Batch handoff recovery claim event is invalid';
    end if;

    -- A normal retry also consumes the old durable receipt before any current
    -- application prompt, schema, deadline, or idempotency key is rebuilt.
    route_receipt := public.bind_review_draft_execution_plane(
        review_job_ids[3],
        'official-x:handoff-worker',
        'openai_batch'
    );
    perform public.fail_review_draft_job(
        review_job_ids[3],
        'official-x:handoff-worker',
        'stored_receipt_retry_canary',
        'Retry the already admitted stored receipt.',
        true,
        statement_timestamp()
    );
    claimed := public.claim_review_draft_job(
        test_workspace_id,
        'official-x:stored-receipt-retry',
        900
    );
    if claimed ->> 'job_id' <> review_job_ids[3]::text
       or claimed ->> 'attempts' <> '2'
       or claimed ->> 'batch_handoff_recovery_only' <> 'false' then
        raise exception 'ordinary stored-receipt retry claim is invalid';
    end if;
    handoff := public.recover_review_draft_batch_handoff(
        review_job_ids[3],
        'official-x:stored-receipt-retry'
    );
    if handoff ->> 'status' <> 'succeeded'
       or handoff ->> 'batch_job_id' <> review_job_ids[3]::text
       or handoff ->> 'input_sha256' <> input_hashes[3] then
        raise exception 'ordinary retry did not consume its stored receipt';
    end if;

    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority,
        input, output, idempotency_key, attempts, max_attempts,
        available_at, locked_by, locked_at, lease_expires_at, started_at
    ) values (
        no_receipt_retry_job_id,
        test_workspace_id,
        'origintrail',
        'generate',
        'running',
        0,
        jsonb_build_object(
            'workflow', 'official_x_review_draft_v1',
            'content_kind', 'daily_news',
            'manual_only', false
        ),
        '{"execution_plane":"openai_batch"}'::jsonb,
        'stored-receipt-miss-canary',
        2,
        3,
        statement_timestamp(),
        'official-x:stored-receipt-miss',
        statement_timestamp(),
        statement_timestamp() + interval '15 minutes',
        statement_timestamp()
    );
    handoff := public.recover_review_draft_batch_handoff(
        no_receipt_retry_job_id,
        'official-x:stored-receipt-miss'
    );
    if handoff is not null then
        raise exception 'ordinary retry receipt miss was not nullable';
    end if;

    begin
        perform public.complete_review_draft_batch_handoff(
            review_job_ids[1],
            'official-x:handoff-worker',
            review_job_ids[2],
            input_hashes[1]
        );
        raise exception 'cross-job Batch handoff was accepted';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.complete_review_draft_batch_handoff(
            review_job_ids[1],
            'official-x:other-worker',
            review_job_ids[1],
            input_hashes[1]
        );
        raise exception 'another worker completed the active handoff lease';
    exception when object_not_in_prerequisite_state then null;
    end;
    begin
        perform public.complete_review_draft_batch_handoff(
            review_job_ids[1],
            'official-x:handoff-worker',
            review_job_ids[1],
            repeat('f', 64)
        );
        raise exception 'Batch handoff accepted a different input hash';
    exception when check_violation then null;
    end;

    begin
        perform public.complete_review_draft_batch_handoff(
            review_job_ids[1],
            'official-x:handoff-worker',
            review_job_ids[1],
            input_hashes[1]
        );
        raise exception 'unbound review job entered the Batch handoff';
    exception when check_violation then null;
    end;

    for item_index in 1..2
    loop
        route_receipt := public.bind_review_draft_execution_plane(
            review_job_ids[item_index],
            'official-x:handoff-worker',
            'openai_batch'
        );
        if route_receipt ->> 'execution_plane' <> 'openai_batch'
           or route_receipt ->> 'reused' <> 'false' then
            raise exception 'Batch handoff execution plane was not bound';
        end if;
    end loop;

    update public.source_items
    set media = '[{"type":"video","url":"https://pbs.twimg.com/media/handoff.jpg"}]'::jsonb
    where id = 'e1000000-0000-4000-8000-000000000001';
    begin
        perform public.complete_review_draft_batch_handoff(
            review_job_ids[1],
            'official-x:handoff-worker',
            review_job_ids[1],
            input_hashes[1]
        );
        raise exception 'media mutation bypassed Batch handoff revalidation';
    exception when check_violation then null;
    end;
    update public.source_items
    set media = '[]'::jsonb
    where id = 'e1000000-0000-4000-8000-000000000001';

    for item_index in 1..2
    loop
        handoff := public.complete_review_draft_batch_handoff(
            review_job_ids[item_index],
            'official-x:handoff-worker',
            review_job_ids[item_index],
            input_hashes[item_index]
        );
        if handoff ->> 'status' <> 'succeeded'
           or handoff ->> 'reused' <> 'false'
           or handoff ->> 'review_state' <> 'pending' then
            raise exception 'Batch queue handoff did not complete exactly';
        end if;
    end loop;

    -- An uncertain response can be replayed by a fresh worker because worker
    -- identity is intentionally absent from the immutable handoff receipt.
    handoff := public.complete_review_draft_batch_handoff(
        review_job_ids[1],
        'official-x:fresh-worker',
        review_job_ids[1],
        input_hashes[1]
    );
    if handoff ->> 'reused' <> 'true' then
        raise exception 'Batch handoff replay was not worker-independent';
    end if;
    expected_output := jsonb_build_object(
        'workflow', 'agent_batch_review_handoff_v1',
        'handoff', 'openai_batch',
        'batch_job_id', review_job_ids[1],
        'input_sha256', input_hashes[1],
        'review_state', 'pending'
    );
    if not exists (
        select 1
        from public.jobs
        where id = review_job_ids[1]
          and status = 'succeeded'
          and output = expected_output
          and locked_by is null
          and locked_at is null
          and lease_expires_at is null
          and finished_at is not null
    ) then
        raise exception 'public review job did not retain the exact handoff receipt';
    end if;
    if (
        select count(*)
        from public.event_log
        where workspace_id = test_workspace_id
          and entity_type = 'job'
          and entity_id = review_job_ids[1]
          and event_type =
                'official_x_review_draft_batch_handoff_completed'
          and data = expected_output
    ) <> 1 then
        raise exception 'Batch handoff event was missing or duplicated';
    end if;
    expected_output := jsonb_build_object(
        'workflow', 'agent_batch_review_handoff_v1',
        'handoff', 'openai_batch',
        'batch_job_id', review_job_ids[4],
        'input_sha256', input_hashes[4],
        'review_state', 'pending'
    );
    if not exists (
        select 1
        from public.jobs as review_job
        join agent_runtime.batch_jobs as batch_job
          on batch_job.workspace_id = review_job.workspace_id
         and batch_job.job_id = review_job.id
        where review_job.id = review_job_ids[4]
          and review_job.status = 'succeeded'
          and review_job.output = expected_output
          and batch_job.status = 'queued'
          and batch_job.reservation_state = 'held'
          and batch_job.deadline_at <= statement_timestamp()
    ) then
        raise exception 'expired queued Batch handoff receipt was not recovered';
    end if;

    inbox := public.list_agent_batch_review_inbox(
        test_workspace_id, 100, null::timestamptz, null::uuid
    );
    if jsonb_array_length(inbox -> 'items') <> 0
       or inbox -> 'next_cursor' <> 'null'::jsonb then
        raise exception 'unfinished Batch work leaked into the review inbox';
    end if;

    claimed := public.claim_agent_batch_jobs(
        test_workspace_id,
        'batch:review-worker',
        array['origintrail']::text[],
        3,
        900
    );
    if jsonb_array_length(claimed) <> 3 then
        raise exception 'review handoff Batch jobs were not claimed together';
    end if;
    if not exists (
        select 1
        from agent_runtime.batch_jobs
        where job_id = review_job_ids[4]
          and status = 'failed'
          and reservation_state = 'released'
          and error_code = 'batch_deadline_expired'
    ) or public.get_agent_batch_review_item(
        test_workspace_id, review_job_ids[4]
    ) is not null then
        raise exception 'expired handoff entered the review inbox';
    end if;
    perform public.register_agent_batch(
        test_workspace_id,
        'batch:review-worker',
        'file-input-review-inbox',
        'batch-review-inbox',
        review_job_ids[1:3]
    );
    perform public.update_agent_batch_poll(
        test_workspace_id,
        'batch-review-inbox',
        'completed',
        'file-output-review-inbox',
        null
    );

    review_payload := jsonb_build_object(
        'headline_ko', 'OriginTrail 첫 리뷰',
        'body_ko', '첫 본문 초안',
        'x_copy_ko', '첫 X 초안',
        'telegram_copy_ko', '첫 텔레그램 초안'
    );
    begin
        perform public.complete_agent_batch_job(
            test_workspace_id,
            review_job_ids[1],
            'batch-review-inbox',
            'content_ready',
            review_payload,
            400::bigint,
            100::bigint,
            1000::bigint
        );
        raise exception 'official-source Batch accepted a non-review result code';
    exception when check_violation then null;
    end;
    begin
        perform public.complete_agent_batch_job(
            test_workspace_id,
            review_job_ids[1],
            'batch-review-inbox',
            'needs_review',
            review_payload || '{"unexpected":true}'::jsonb,
            400::bigint,
            100::bigint,
            1000::bigint
        );
        raise exception 'official-source Batch accepted an extra result key';
    exception when check_violation then null;
    end;
    begin
        perform public.complete_agent_batch_job(
            test_workspace_id,
            review_job_ids[1],
            'batch-review-inbox',
            'needs_review',
            jsonb_set(review_payload, '{headline_ko}', '""'::jsonb),
            400::bigint,
            100::bigint,
            1000::bigint
        );
        raise exception 'official-source Batch accepted an empty headline';
    exception when check_violation then null;
    end;
    begin
        perform public.complete_agent_batch_job(
            test_workspace_id,
            review_job_ids[1],
            'batch-review-inbox',
            'needs_review',
            jsonb_set(
                review_payload,
                '{headline_ko}',
                to_jsonb(E' \n\t'::text)
            ),
            400::bigint,
            100::bigint,
            1000::bigint
        );
        raise exception 'official-source Batch accepted a whitespace-only field';
    exception when check_violation then null;
    end;
    if not exists (
        select 1
        from agent_runtime.batch_jobs
        where job_id = review_job_ids[1]
          and status = 'submitted'
          and reservation_state = 'held'
          and result_code is null
    ) then
        raise exception 'malformed review completion mutated the Batch job';
    end if;

    for item_index in 1..3
    loop
        review_payload := case item_index
            when 1 then jsonb_build_object(
                'headline_ko', 'OriginTrail 첫 리뷰',
                'body_ko', '첫 본문 초안',
                'x_copy_ko', '첫 X 초안',
                'telegram_copy_ko', '첫 텔레그램 초안'
            )
            when 2 then jsonb_build_object(
                'headline_ko', 'OriginTrail 두 번째 리뷰',
                'body_ko', '두 번째 본문 초안',
                'x_copy_ko', '두 번째 X 초안',
                'telegram_copy_ko', '두 번째 텔레그램 초안'
            )
            else jsonb_build_object(
                'headline_ko', 'OriginTrail 세 번째 리뷰',
                'body_ko', '세 번째 본문 초안',
                'x_copy_ko', '세 번째 X 초안',
                'telegram_copy_ko', '세 번째 텔레그램 초안'
            )
        end;
        completed := public.complete_agent_batch_job(
            test_workspace_id,
            review_job_ids[item_index],
            'batch-review-inbox',
            'needs_review',
            review_payload,
            400::bigint,
            100::bigint,
            (item_index * 1000)::bigint
        );
        if completed ->> 'status' <> 'completed' then
            raise exception 'valid review Batch result was not completed';
        end if;
    end loop;
    finalized := public.finalize_agent_batch(
        test_workspace_id,
        'batch-review-inbox'
    );
    if finalized ->> 'missing_job_count' <> '0' then
        raise exception 'completed review Batch retained a missing result';
    end if;
    if not exists (
        select 1
        from agent_runtime.batch_budgets
        where workspace_id = test_workspace_id
          and budget_key = 'openai:review-handoff'
          and reserved_microusd = 0
          and spent_microusd = 6000
    ) then
        raise exception 'review Batch completion did not settle its budget';
    end if;

    inbox := public.list_agent_batch_review_inbox(
        test_workspace_id, 2, null::timestamptz, null::uuid
    );
    if jsonb_array_length(inbox -> 'items') <> 2
       or jsonb_typeof(inbox -> 'next_cursor') <> 'object'
       or not (inbox -> 'next_cursor' ? 'finished_at')
       or not (inbox -> 'next_cursor' ? 'job_id') then
        raise exception 'Batch review inbox first page is invalid';
    end if;
    for candidate in
        select value from jsonb_array_elements(inbox -> 'items')
    loop
        if candidate ? 'input_payload'
           or candidate ? 'current_batch_id'
           or candidate ? 'batch_id'
           or candidate ? 'input_file_id'
           or candidate ? 'output_file_id'
           or candidate ? 'error_file_id'
           or candidate ->> 'client_id' <> 'origintrail'
           or candidate ->> 'agent_id' <> 'origintrail_client_agent'
           or not (candidate ?& array[
               'job_id', 'client_id', 'agent_id', 'workflow_kind',
               'stage', 'status', 'model', 'model_tier', 'title',
               'result_code', 'actual_cost_microusd', 'finished_at',
               'source_url'
           ]::text[]) then
            raise exception 'Batch review inbox exposed a private or invalid field';
        end if;
    end loop;

    next_page := public.list_agent_batch_review_inbox(
        test_workspace_id,
        2,
        (inbox -> 'next_cursor' ->> 'finished_at')::timestamptz,
        (inbox -> 'next_cursor' ->> 'job_id')::uuid
    );
    if jsonb_array_length(next_page -> 'items') <> 1
       or next_page -> 'next_cursor' <> 'null'::jsonb
       or exists (
           select 1
           from jsonb_array_elements(inbox -> 'items') as first_page(item)
           join jsonb_array_elements(next_page -> 'items') as second_page(item)
             on first_page.item ->> 'job_id'
                = second_page.item ->> 'job_id'
       ) then
        raise exception 'Batch review inbox keyset pagination is invalid';
    end if;
    begin
        perform public.list_agent_batch_review_inbox(
            test_workspace_id,
            2,
            statement_timestamp(),
            null::uuid
        );
        raise exception 'Batch review inbox accepted a partial cursor';
    exception when invalid_parameter_value then null;
    end;

    detail := public.get_agent_batch_review_item(
        test_workspace_id, review_job_ids[1]
    );
    if detail ->> 'title' <> 'OriginTrail 첫 리뷰'
       or detail -> 'result_payload'
            <> jsonb_build_object(
                'headline_ko', 'OriginTrail 첫 리뷰',
                'body_ko', '첫 본문 초안',
                'x_copy_ko', '첫 X 초안',
                'telegram_copy_ko', '첫 텔레그램 초안'
            )
       or detail ->> 'input_sha256' <> input_hashes[1]
       or detail ->> 'actual_input_tokens' <> '400'
       or detail ->> 'actual_output_tokens' <> '100'
       or detail ? 'input_payload'
       or detail ? 'current_batch_id'
       or detail ? 'batch_id'
       or detail ? 'input_file_id'
       or detail ? 'output_file_id'
       or detail ? 'error_file_id' then
        raise exception 'Batch review detail DTO is invalid';
    end if;
    detail := public.get_agent_batch_review_item(
        test_workspace_id, review_job_ids[2]
    );
    if detail ->> 'title' <> 'OriginTrail 두 번째 리뷰' then
        raise exception 'Batch review headline_ko title is invalid';
    end if;

    -- Each eligibility boundary is independently fail-closed.
    update agent_runtime.batch_jobs
    set input_payload =
        jsonb_set(input_payload, '{approval_required}', 'false'::jsonb)
    where job_id = review_job_ids[1];
    inbox := public.list_agent_batch_review_inbox(
        test_workspace_id, 100, null::timestamptz, null::uuid
    );
    if jsonb_array_length(inbox -> 'items') <> 2 then
        raise exception 'non-approval Batch output entered the review inbox';
    end if;
    update agent_runtime.batch_jobs
    set input_payload =
        jsonb_set(input_payload, '{approval_required}', 'true'::jsonb)
    where job_id = review_job_ids[1];

    update agent_runtime.batch_jobs
    set result_code = 'content_ready'
    where job_id = review_job_ids[1];
    inbox := public.list_agent_batch_review_inbox(
        test_workspace_id, 100, null::timestamptz, null::uuid
    );
    if jsonb_array_length(inbox -> 'items') <> 2 then
        raise exception 'non-review Batch result entered the review inbox';
    end if;
    update agent_runtime.batch_jobs
    set result_code = 'needs_review'
    where job_id = review_job_ids[1];

    update agent_runtime.batch_jobs
    set workflow_kind = 'daily_digest'
    where job_id = review_job_ids[1];
    inbox := public.list_agent_batch_review_inbox(
        test_workspace_id, 100, null::timestamptz, null::uuid
    );
    if jsonb_array_length(inbox -> 'items') <> 2 then
        raise exception 'wrong Batch workflow entered the review inbox';
    end if;
    update agent_runtime.batch_jobs
    set workflow_kind = 'official_source_nonurgent_pack'
    where job_id = review_job_ids[1];

    update agent_runtime.batch_jobs
    set client_id = 'yellow'
    where job_id = review_job_ids[1];
    inbox := public.list_agent_batch_review_inbox(
        test_workspace_id, 100, null::timestamptz, null::uuid
    );
    if jsonb_array_length(inbox -> 'items') <> 2 then
        raise exception 'non-OriginTrail Batch output entered the review inbox';
    end if;
    update agent_runtime.batch_jobs
    set client_id = 'origintrail'
    where job_id = review_job_ids[1];

    update agent_runtime.batch_jobs
    set agent_id = 'squid_client_agent'
    where job_id = review_job_ids[1];
    inbox := public.list_agent_batch_review_inbox(
        test_workspace_id, 100, null::timestamptz, null::uuid
    );
    if jsonb_array_length(inbox -> 'items') <> 2 then
        raise exception 'wrong Batch agent entered the review inbox';
    end if;
    update agent_runtime.batch_jobs
    set agent_id = 'origintrail_client_agent'
    where job_id = review_job_ids[1];

    update public.jobs
    set output = output || '{"unexpected":true}'::jsonb
    where id = review_job_ids[1];
    inbox := public.list_agent_batch_review_inbox(
        test_workspace_id, 100, null::timestamptz, null::uuid
    );
    if jsonb_array_length(inbox -> 'items') <> 2
       or public.get_agent_batch_review_item(
           test_workspace_id, review_job_ids[1]
       ) is not null then
        raise exception 'mutated public handoff output entered the review inbox';
    end if;
    update public.jobs
    set output = jsonb_build_object(
        'workflow', 'agent_batch_review_handoff_v1',
        'handoff', 'openai_batch',
        'batch_job_id', review_job_ids[1],
        'input_sha256', input_hashes[1],
        'review_state', 'pending'
    )
    where id = review_job_ids[1];

    begin
        perform public.complete_review_draft_batch_handoff(
            review_job_ids[1],
            'official-x:fresh-worker',
            review_job_ids[1],
            repeat('f', 64)
        );
        raise exception 'committed Batch handoff input hash was mutable';
    exception when check_violation then null;
    end;
    if (
        select count(*)
        from public.content_items
        where workspace_id = test_workspace_id
    ) <> content_item_count_before
       or (
           select count(*)
           from public.content_versions
           where workspace_id = test_workspace_id
       ) <> content_version_count_before
       or (
           select count(*)
           from public.approvals
           where workspace_id = test_workspace_id
       ) <> approval_count_before
       or (
           select count(*)
           from public.publications
           where workspace_id = test_workspace_id
       ) <> publication_count_before then
        raise exception 'Batch handoff or review reads created Studio side effects';
    end if;
end
$test$;

rollback;
