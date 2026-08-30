\set ON_ERROR_STOP on

begin;
set local deadlock_timeout = '200ms';
set local lock_timeout = '5s';
set local statement_timeout = '10s';

do $claim$
declare
    claimed jsonb;
begin
    claimed := public.claim_grok_qa_dispatch_job(
        'cc100000-0000-4000-8000-000000000001',
        'ci:content-qa-claim',
        180,
        array['squid'],
        86400,
        'cc140000-0000-4000-8000-000000000001'
    );
    if claimed -> 'job' ->> 'content_version_id'
            <> 'cc140000-0000-4000-8000-000000000001'
       or claimed -> 'job' ->> 'status' <> 'claimed' then
        raise exception 'legacy Grok claim did not lock the exact fixture';
    end if;
end
$claim$;

-- The harness observes this only after the real claim RPC owns both the
-- outbox lock and the item key-share lock.
select pg_catalog.pg_advisory_lock(20260830, 1);
select pg_catalog.pg_sleep(2.5);

-- Restore the pristine row after proving the concurrent recorder waited.
rollback;
