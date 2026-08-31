\set ON_ERROR_STOP on

begin;
set local deadlock_timeout = '200ms';
set local lock_timeout = '7s';
set local statement_timeout = '9s';

select pg_catalog.set_config(
    'request.jwt.claims',
    '{"role":"coineasy_content_qa","workspace_id":"cc100000-0000-4000-8000-000000000001","sub":"codex:content-qa","capability":"content_qa_review","release_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","environment":"production","automatic_publication":false,"max_external_actions":0}',
    true
);

do $record$
declare
    result jsonb;
    verdict constant jsonb := '{
      "decision":"PASS",
      "summary":"The exact official source and private banner evidence match.",
      "fact_check":{
        "status":"PASS",
        "checks":["The official source facts match."],
        "source_urls":["https://x.com/SquidRouter/status/2083266484789514777"]
      },
      "brand_check":{
        "status":"PASS",
        "checks":["The Squid brand identity matches."]
      },
      "issues":[],
      "next_action":"ready_for_human_approval"
    }'::jsonb;
begin
    result := public.record_content_qa_verdict(
        'cc100000-0000-4000-8000-000000000001',
        'cc130000-0000-4000-8000-000000000001',
        'cc140000-0000-4000-8000-000000000001',
        'official-x-content-qa@1',
        'codex:content-qa',
        'codex',
        repeat('b', 40),
        'cc150000-0000-4000-8000-000000000001',
        'cc120000-0000-4000-8000-000000000001',
        'https://x.com/SquidRouter/status/2083266484789514777',
        (
            select published_at
            from public.source_items
            where id = 'cc120000-0000-4000-8000-000000000001'
        ),
        repeat('a', 64),
        verdict
    );
    if result -> 'recorded' is distinct from 'true'::jsonb
       or result ->> 'status' <> 'reviewed' then
        raise exception 'Content QA did not finish after the legacy lock: %',
            result;
    end if;
    if not exists (
        select 1
        from private.grok_qa_dispatch_outbox
        where workspace_id = 'cc100000-0000-4000-8000-000000000001'
          and content_version_id = 'cc140000-0000-4000-8000-000000000001'
          and status = 'obsolete'
          and content_qa_job_id = (result ->> 'job_id')::uuid
    ) then
        raise exception 'Content QA did not atomically fence the Grok row';
    end if;
end
$record$;

-- This is a concurrency proof, not durable review authorization.
rollback;

do $restored$
begin
    if exists (
        select 1
        from private.content_qa_jobs
        where workspace_id = 'cc100000-0000-4000-8000-000000000001'
    ) or not exists (
        select 1
        from private.grok_qa_dispatch_outbox
        where workspace_id = 'cc100000-0000-4000-8000-000000000001'
          and content_version_id = 'cc140000-0000-4000-8000-000000000001'
          and status = 'pending'
          and attempts = 0
          and content_qa_job_id is null
    ) then
        raise exception 'claim-order scenario did not restore its fixture';
    end if;
end
$restored$;
