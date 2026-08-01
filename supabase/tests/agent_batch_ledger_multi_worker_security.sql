-- Transactional smoke for the invariants a second concurrent Batch dispatcher
-- depends on. Run after all migrations as the database owner; every test row is
-- rolled back.
--
-- Scope note: a single session cannot exercise the `for update skip locked`
-- race itself. What this asserts is the state machine that decides which rows
-- are claimable at all — job status, lease expiry, attempt accounting, and the
-- provider-facing custom_id. If that logic is right, SKIP LOCKED resolves the
-- remaining race; if it is wrong, no amount of locking saves the dispatcher.
--
-- The dispatcher already presents a fresh worker id per process
-- (`scripts/run_batch_dispatcher.py` uses `batch:<uuid4>`), so these are also
-- the invariants that already hold between consecutive cron runs today.

begin;

insert into public.workspaces (id, name, slug, created_by)
values (
    'c0000000-0000-4000-8000-0000000000b1',
    'Agent Batch Ledger Multi-Worker Test',
    'agent-batch-ledger-multi-worker-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values (
    'c0000000-0000-4000-8000-0000000000b1',
    'squid',
    'Squid',
    true,
    null
);

do $test$
declare
    test_workspace_id uuid := 'c0000000-0000-4000-8000-0000000000b1';
    first_job_id uuid := 'c1000000-0000-4000-8000-0000000000b1';
    second_job_id uuid := 'c1000000-0000-4000-8000-0000000000b2';
    deadline timestamptz := statement_timestamp() + interval '30 hours';
    period_start timestamptz := date_trunc('day', statement_timestamp());
    period_end timestamptz := date_trunc('day', statement_timestamp())
        + interval '1 day';
    first_claim jsonb;
    second_claim jsonb;
    empty_claim jsonb;
    recovery_claim jsonb;
    budget_rows integer;
begin
    -- Two dispatchers waking in the same minute both configure the day's
    -- budget before claiming. That must converge on one budget, not two.
    perform public.configure_agent_batch_budget(
        test_workspace_id, 'openai:multi-worker', period_start, period_end, 500000
    );
    perform public.configure_agent_batch_budget(
        test_workspace_id, 'openai:multi-worker', period_start, period_end, 500000
    );
    select count(*) into budget_rows
    from agent_runtime.batch_budgets
    where workspace_id = test_workspace_id
      and budget_key = 'openai:multi-worker';
    if budget_rows <> 1 then
        raise exception
            'concurrent budget configuration created % rows, expected 1',
            budget_rows;
    end if;

    perform public.queue_agent_batch_job(
        test_workspace_id, 'squid', first_job_id, repeat('b', 62) || 'b1',
        'naver_seo_writer', 'naver_seo_article', 'generate', 3::smallint,
        'batch_24h', 'gpt-5.6-luna', 'S', deadline,
        '{"topic":"first multi-worker job"}'::jsonb, repeat('1', 64),
        1200::bigint, 1400, 50000::bigint, 'openai:multi-worker',
        first_job_id::text || ':generate:1', false
    );
    perform public.queue_agent_batch_job(
        test_workspace_id, 'squid', second_job_id, repeat('b', 62) || 'b2',
        'naver_seo_writer', 'naver_seo_article', 'generate', 3::smallint,
        'batch_24h', 'gpt-5.6-luna', 'S', deadline,
        '{"topic":"second multi-worker job"}'::jsonb, repeat('2', 64),
        1200::bigint, 1400, 50000::bigint, 'openai:multi-worker',
        second_job_id::text || ':generate:1', false
    );

    -- Two workers, one job each. They must not land on the same row.
    first_claim := public.claim_agent_batch_jobs(
        test_workspace_id, 'batch:worker-one', array['squid']::text[], 1, 300
    );
    second_claim := public.claim_agent_batch_jobs(
        test_workspace_id, 'batch:worker-two', array['squid']::text[], 1, 300
    );
    if jsonb_array_length(first_claim) <> 1
       or jsonb_array_length(second_claim) <> 1 then
        raise exception 'each worker must claim exactly one job, got % and %',
            jsonb_array_length(first_claim), jsonb_array_length(second_claim);
    end if;
    if first_claim -> 0 ->> 'job_id' = second_claim -> 0 ->> 'job_id' then
        raise exception 'two workers claimed the same job: %',
            first_claim -> 0 ->> 'job_id';
    end if;
    if (first_claim -> 0 ->> 'recovery_required')::boolean
       or (second_claim -> 0 ->> 'recovery_required')::boolean then
        raise exception 'a first claim must not report recovery';
    end if;

    -- Ownership is recorded per worker, not merged.
    if (
        select count(distinct locked_by)
        from agent_runtime.batch_jobs
        where workspace_id = test_workspace_id
    ) <> 2 then
        raise exception 'claimed jobs did not record two distinct owners';
    end if;

    -- While both leases are live there is nothing left to take, even for a
    -- worker asking for more than the queue holds.
    empty_claim := public.claim_agent_batch_jobs(
        test_workspace_id, 'batch:worker-three', array['squid']::text[], 5, 300
    );
    if jsonb_array_length(empty_claim) <> 0 then
        raise exception 'a live lease was handed to a third worker: %',
            empty_claim;
    end if;
    if (
        select count(*)
        from agent_runtime.batch_jobs
        where workspace_id = test_workspace_id
          and locked_by = 'batch:worker-three'
    ) <> 0 then
        raise exception 'a third worker took ownership of a live lease';
    end if;

    -- A worker that dies mid-flight leaves an expiring lease. Another worker
    -- must be able to resume it — without inventing a new attempt, and without
    -- changing the identity the provider already saw.
    update agent_runtime.batch_jobs
    set lease_expires_at = statement_timestamp() - interval '1 minute'
    where workspace_id = test_workspace_id
      and job_id = first_job_id;

    recovery_claim := public.claim_agent_batch_jobs(
        test_workspace_id, 'batch:worker-two', array['squid']::text[], 5, 300
    );
    if jsonb_array_length(recovery_claim) <> 1
       or recovery_claim -> 0 ->> 'job_id' <> first_job_id::text then
        raise exception 'the expired lease was not recovered, got %',
            recovery_claim;
    end if;
    if not (recovery_claim -> 0 ->> 'recovery_required')::boolean then
        raise exception 'a recovered job must be flagged for recovery';
    end if;
    if (recovery_claim -> 0 ->> 'attempt')::integer
       <> (first_claim -> 0 ->> 'attempt')::integer then
        raise exception
            'recovery changed the attempt number from % to %',
            first_claim -> 0 ->> 'attempt',
            recovery_claim -> 0 ->> 'attempt';
    end if;
    if recovery_claim -> 0 ->> 'custom_id'
       <> first_job_id::text || ':generate:1' then
        raise exception
            'recovery changed the provider-facing custom_id to %',
            recovery_claim -> 0 ->> 'custom_id';
    end if;
    if (
        select locked_by
        from agent_runtime.batch_jobs
        where workspace_id = test_workspace_id
          and job_id = first_job_id
    ) <> 'batch:worker-two' then
        raise exception 'recovery did not transfer ownership to the claimer';
    end if;
end
$test$;

rollback;
