-- Exact least-privilege PostgREST role for Buzz Operations Agent v1.

begin;

do $role$
declare
    operations_routines constant text[] := array[
        'public.record_origintrail_buzz_operations_command(uuid,uuid,text,text,text,bigint,text,text,bigint,text)',
        'public.claim_origintrail_buzz_operations_response(uuid,text,text,integer)',
        'public.mark_origintrail_buzz_operations_response_attempt(uuid,text,text,text,text)',
        'public.complete_origintrail_buzz_operations_response(uuid,text,text,text,text,boolean)',
        'public.fail_origintrail_buzz_operations_response(uuid,text,text,text,boolean)',
        'public.reconcile_origintrail_buzz_operations_leases(uuid,integer)',
        'public.list_origintrail_buzz_operations_unknown(uuid,integer)'
    ];
    routine text;
begin
    if not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_buzz_operations_worker'
    ) then
        create role coineasy_buzz_operations_worker nologin noinherit;
    end if;
    alter role coineasy_buzz_operations_worker nologin noinherit nobypassrls;
    grant usage on schema public to coineasy_buzz_operations_worker;
    grant coineasy_buzz_operations_worker to authenticator;
    foreach routine in array operations_routines loop
        if to_regprocedure(routine) is null then
            raise exception 'Buzz operations grant target is missing: %', routine;
        end if;
        execute format(
            'grant execute on function %s to coineasy_buzz_operations_worker',
            routine
        );
    end loop;
end;
$role$;

revoke all on table agent_runtime.buzz_operations_tasks,
    agent_runtime.buzz_operations_commands
from coineasy_buzz_operations_worker;
revoke all on function private.origintrail_buzz_operations_sha256(text)
from coineasy_buzz_operations_worker;
revoke all on function private.origintrail_buzz_operations_command_sha256(
    text, uuid, text, text, text, bigint, text
) from coineasy_buzz_operations_worker;
revoke all on function private.origintrail_buzz_operations_response_object(
    uuid, text, boolean, boolean, boolean
) from coineasy_buzz_operations_worker;

commit;
