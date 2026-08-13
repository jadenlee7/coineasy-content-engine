begin;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'coineasy_autonomous_ops_worker') then
        create role coineasy_autonomous_ops_worker
            nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole;
    end if;
end
$$;

alter role coineasy_autonomous_ops_worker
    nologin noinherit nobypassrls nosuperuser nocreatedb nocreaterole;
grant coineasy_autonomous_ops_worker to authenticator;

revoke all on table agent_runtime.autonomous_ops_observations
from coineasy_autonomous_ops_worker;
revoke all on table agent_runtime.autonomous_ops_tasks
from coineasy_autonomous_ops_worker;
grant execute on function public.observe_origintrail_autonomous_ops(uuid, text)
to coineasy_autonomous_ops_worker;
grant execute on function public.record_origintrail_autonomous_ops_plan(
    uuid, text, text, text, text, text, text, text, jsonb, text, boolean, boolean
) to coineasy_autonomous_ops_worker;

commit;
