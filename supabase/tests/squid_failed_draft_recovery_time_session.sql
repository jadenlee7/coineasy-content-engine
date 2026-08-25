\set ON_ERROR_STOP on

do $test$
declare
    inspected jsonb;
begin
    begin
        inspected := public.inspect_squid_failed_draft_recovery(
            'e0000000-0000-4000-8000-000000000001',
            'e3000000-0000-4000-8000-000000000001',
            'e5000000-0000-4000-8000-000000000002',
            'e6000000-0000-4000-8000-000000000002',
            'sql-time-boundary-test',
            clock_timestamp(),
            clock_timestamp() + interval '1 second',
            repeat('d', 40)
        );
        raise exception 'recovery inspection crossed its approval expiry';
    exception when sqlstate '22023' then
        if sqlerrm <> 'Squid failed draft recovery approval is invalid' then
            raise;
        end if;
    end;
end
$test$;
