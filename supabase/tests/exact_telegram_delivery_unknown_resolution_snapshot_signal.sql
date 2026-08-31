\set ON_ERROR_STOP on

-- The caller starts this connection only after the normal resolver commits.
-- Keep the signal lock until the stale session releases its ready lock, then
-- close normally so no marker leaks into the next isolation-level iteration.
select pg_catalog.pg_advisory_lock(20260831, 172);
do $await_stale_session_completion$
declare
    attempt integer;
begin
    for attempt in 1..200 loop
        if pg_catalog.pg_try_advisory_lock(20260831, 171) then
            perform pg_catalog.pg_advisory_unlock(20260831, 171);
            return;
        end if;
        perform pg_catalog.pg_sleep(0.05);
    end loop;
    raise exception 'stale snapshot session did not finish';
end
$await_stale_session_completion$;
select pg_catalog.pg_advisory_unlock(20260831, 172);
