-- Transactional security smoke test for the Grok QA OAuth code ledger.
-- Run only after 20260813213000_grok_qa_oauth_authorization_codes.sql.
-- Every row and assertion is rolled back.

\set ON_ERROR_STOP on
begin;

create temporary table grok_qa_oauth_security_baseline (
    approvals bigint not null,
    publications bigint not null,
    batch_jobs bigint not null
) on commit drop;

insert into grok_qa_oauth_security_baseline
select
    (select count(*) from public.approvals),
    (select count(*) from public.publications),
    (select count(*) from agent_runtime.batch_jobs);

do $permissions$
begin
    if has_table_privilege(
        'coineasy_grok_qa_oauth',
        'private.grok_qa_oauth_codes',
        'select'
    ) or has_table_privilege(
        'coineasy_grok_qa_oauth',
        'private.grok_qa_oauth_codes',
        'insert'
    ) or not has_function_privilege(
        'coineasy_grok_qa_oauth',
        'public.create_grok_qa_oauth_code(text,text,text,text,text,text,timestamptz)',
        'execute'
    ) or not has_function_privilege(
        'coineasy_grok_qa_oauth',
        'public.consume_grok_qa_oauth_code(text,text,text,text,text,text)',
        'execute'
    ) then
        raise exception 'Grok QA OAuth scoped privilege boundary is invalid';
    end if;
end;
$permissions$;

set local role coineasy_grok_qa_oauth;

select 1 / case when (
    public.create_grok_qa_oauth_code(
        repeat('1', 64),
        repeat('2', 64),
        'https://grok.com/oauth/callback',
        'https://coineasy-newscard.netlify.app/api/grok-qa/mcp',
        'coineasy.qa',
        repeat('A', 43),
        clock_timestamp() + interval '5 minutes'
    ) ->> 'status'
) = 'created' then 1 else 0 end;

select 1 / case when (
    public.consume_grok_qa_oauth_code(
        repeat('1', 64),
        repeat('2', 64),
        'https://grok.com/oauth/callback',
        'https://coineasy-newscard.netlify.app/api/grok-qa/mcp',
        'coineasy.qa',
        repeat('B', 43)
    ) ->> 'authorized'
)::boolean = false then 1 else 0 end;

select 1 / case when (
    public.consume_grok_qa_oauth_code(
        repeat('1', 64),
        repeat('2', 64),
        'https://grok.com/oauth/callback',
        'https://coineasy-newscard.netlify.app/api/grok-qa/mcp',
        'coineasy.qa',
        repeat('A', 43)
    ) ->> 'authorized'
)::boolean then 1 else 0 end;

select 1 / case when (
    public.consume_grok_qa_oauth_code(
        repeat('1', 64),
        repeat('2', 64),
        'https://grok.com/oauth/callback',
        'https://coineasy-newscard.netlify.app/api/grok-qa/mcp',
        'coineasy.qa',
        repeat('A', 43)
    ) ->> 'authorized'
)::boolean = false then 1 else 0 end;

reset role;

do $invariants$
declare
    baseline grok_qa_oauth_security_baseline%rowtype;
begin
    select * into strict baseline from grok_qa_oauth_security_baseline;
    if (select count(*) from private.grok_qa_oauth_codes) <> 1 then
        raise exception 'OAuth code replay created or removed a ledger row';
    end if;
    if not exists (
        select 1 from private.grok_qa_oauth_codes
        where code_sha256 = repeat('1', 64) and consumed_at is not null
    ) then
        raise exception 'OAuth code was not consumed exactly once';
    end if;
    if baseline.approvals <> (select count(*) from public.approvals)
        or baseline.publications <> (select count(*) from public.publications)
        or baseline.batch_jobs <> (select count(*) from agent_runtime.batch_jobs)
    then
        raise exception 'OAuth flow mutated approval, publication, or Batch state';
    end if;
end;
$invariants$;

rollback;
