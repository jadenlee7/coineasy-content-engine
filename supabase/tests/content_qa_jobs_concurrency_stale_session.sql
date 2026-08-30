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

do $stale$
declare
    blocked boolean := false;
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
    begin
        perform public.record_content_qa_verdict(
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
    exception
        when check_violation then
            if sqlerrm <> 'Content QA primary source is not the latest official tweet' then
                raise;
            end if;
            blocked := true;
    end;
    if not blocked then
        raise exception 'Content QA accepted evidence superseded by the poll';
    end if;
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
          and content_qa_job_id is null
    ) then
        raise exception 'stale-source rejection mutated durable QA state';
    end if;
end
$stale$;

commit;
