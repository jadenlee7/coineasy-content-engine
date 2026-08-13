begin;

do $verify$
declare
    direct_grants integer;
    function_grants text[];
begin
    if exists (
        select 1 from pg_class as relation
        join pg_namespace as namespace on namespace.oid = relation.relnamespace
        where namespace.nspname = 'agent_runtime'
          and relation.relname in (
              'autonomous_ops_observations', 'autonomous_ops_tasks'
          )
          and (not relation.relrowsecurity or not relation.relforcerowsecurity)
    ) then
        raise exception 'Autonomous Ops FORCE RLS is missing';
    end if;
    select count(*) into direct_grants
    from information_schema.role_table_grants
    where grantee = 'coineasy_autonomous_ops_worker'
      and table_schema = 'agent_runtime'
      and table_name in ('autonomous_ops_observations', 'autonomous_ops_tasks');
    if direct_grants <> 0 then
        raise exception 'Autonomous Ops role has a direct table grant';
    end if;
    select array_agg(routine_name order by routine_name) into function_grants
    from information_schema.role_routine_grants
    where grantee = 'coineasy_autonomous_ops_worker'
      and routine_schema = 'public'
      and routine_name like '%autonomous_ops%';
    if function_grants is distinct from array[
        'observe_origintrail_autonomous_ops',
        'record_origintrail_autonomous_ops_plan'
    ]::text[] then
        raise exception 'Autonomous Ops RPC grants are not exact';
    end if;
    if exists (
        select 1 from pg_roles
        where rolname = 'coineasy_autonomous_ops_worker'
          and (rolcanlogin or rolsuper or rolbypassrls or rolinherit)
    ) then
        raise exception 'Autonomous Ops role attributes are unsafe';
    end if;
    if (select count(*) from agent_runtime.autonomous_ops_observations) <> 0
       or (select count(*) from agent_runtime.autonomous_ops_tasks) <> 0 then
        raise exception 'Autonomous Ops migration created runtime rows';
    end if;
end
$verify$;

rollback;
