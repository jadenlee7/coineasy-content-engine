\set ON_ERROR_STOP on

do $test$
declare
    inspected jsonb;
begin
    begin
        inspected := public.inspect_squid_failed_draft_recovery(
            'e0000000-0000-4000-8000-000000000001',
            'e3000000-0000-4000-8000-000000000001',
            'e5000000-0000-4000-8000-000000000001',
            'e6000000-0000-4000-8000-000000000001',
            'sql-concurrency-test',
            clock_timestamp(),
            clock_timestamp() + interval '1 hour',
            repeat('c', 40)
        );
        raise exception 'recovery inspection ignored the concurrent X poll';
    exception when sqlstate '23514' then
        if sqlerrm <> 'A newer official Squid source supersedes this recovery' then
            raise;
        end if;
    end;
end
$test$;
