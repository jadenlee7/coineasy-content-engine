-- Least-privilege PostgREST roles for the planning-only Agent Work Order ledger.
-- No credential is created here.  Adoption requires a separately approved JWT
-- whose `role` and `workspace_id` claims match the checks inside the RPCs.

begin;

do $roles$
declare
    dashboard_routines constant text[] := array[
        'public.list_agent_operator_inbox(uuid,integer,timestamp with time zone,uuid)',
        'public.get_agent_work_order(uuid,uuid)',
        'public.get_agent_company_dashboard(uuid)'
    ];
    control_plane_routines constant text[] := array[
        'public.complete_agent_work_order(uuid,uuid,text,bigint)'
    ];
    role_name text;
    routine text;
    routines text[];
begin
    foreach role_name in array array[
        'coineasy_agent_dashboard',
        'coineasy_agent_control_plane'
    ]
    loop
        if not exists (
            select 1 from pg_catalog.pg_roles where rolname = role_name
        ) then
            execute pg_catalog.format(
                'create role %I nologin noinherit nosuperuser '
                'nocreaterole nocreatedb noreplication nobypassrls',
                role_name
            );
        end if;
        -- Supabase's migration owner has CREATEROLE but is intentionally not
        -- a true superuser.  PostgreSQL therefore rejects ALTER ROLE clauses
        -- that mention NOSUPERUSER/NOREPLICATION even when the target is
        -- already unprivileged.  Apply the supported hardening flags and then
        -- fail closed if any immutable privileged attribute is present.
        execute pg_catalog.format(
            'alter role %I nologin noinherit nobypassrls', role_name
        );
        if exists (
            select 1
            from pg_catalog.pg_roles
            where rolname = role_name
              and (
                  rolsuper
                  or rolcreaterole
                  or rolcreatedb
                  or rolcanlogin
                  or rolreplication
                  or rolbypassrls
                  or rolinherit
              )
        ) then
            raise exception 'Agent work order role is privileged: %', role_name;
        end if;
        execute pg_catalog.format('grant usage on schema public to %I', role_name);
        execute pg_catalog.format('grant %I to authenticator', role_name);
    end loop;

    foreach role_name in array array[
        'coineasy_agent_dashboard',
        'coineasy_agent_control_plane'
    ]
    loop
        routines := case role_name
            when 'coineasy_agent_dashboard' then dashboard_routines
            else control_plane_routines
        end;
        foreach routine in array routines
        loop
            if pg_catalog.to_regprocedure(routine) is null then
                raise exception 'Agent work order grant target is missing: %',
                    routine;
            end if;
            execute pg_catalog.format(
                'grant execute on function %s to %I', routine, role_name
            );
        end loop;
    end loop;
end;
$roles$;

revoke all on table
    agent_runtime.agent_work_orders,
    agent_runtime.agent_work_order_events,
    agent_runtime.agent_runs,
    agent_runtime.agent_dispatch_outbox,
    agent_runtime.agent_action_receipts,
    agent_runtime.agent_incidents
from coineasy_agent_dashboard, coineasy_agent_control_plane;

revoke all on function private.agent_json_canonical(jsonb)
from coineasy_agent_dashboard, coineasy_agent_control_plane;
revoke all on function private.agent_json_sha256(jsonb)
from coineasy_agent_dashboard, coineasy_agent_control_plane;
revoke all on function private.agent_branch_scope_key(text, text, boolean)
from coineasy_agent_dashboard, coineasy_agent_control_plane;
revoke all on function private.agent_safe_text(text, integer, integer, boolean)
from coineasy_agent_dashboard, coineasy_agent_control_plane;
revoke all on function private.agent_work_order_scope_valid(jsonb)
from coineasy_agent_dashboard, coineasy_agent_control_plane;
revoke all on function private.agent_operator_can_write(uuid)
from coineasy_agent_dashboard, coineasy_agent_control_plane;
revoke all on function private.agent_scoped_workspace_matches(uuid, text)
from coineasy_agent_dashboard, coineasy_agent_control_plane;
revoke all on function private.agent_can_read(uuid)
from coineasy_agent_dashboard, coineasy_agent_control_plane;
revoke all on function private.agent_append_event(
    uuid, uuid, text, text, text, text, uuid, uuid, uuid, jsonb
) from coineasy_agent_dashboard, coineasy_agent_control_plane;
revoke all on function private.agent_work_order_object(uuid, uuid)
from coineasy_agent_dashboard, coineasy_agent_control_plane;

-- Explicitly keep the broad service token outside the new control plane.
revoke all on function public.propose_agent_work_order(uuid, jsonb, text)
from service_role;
revoke all on function public.authorize_agent_work_order(uuid, uuid, text, bigint)
from service_role;
revoke all on function public.record_agent_operator_decision(
    uuid, uuid, text, bigint, text, text
) from service_role;
revoke all on function public.complete_agent_work_order(uuid, uuid, text, bigint)
from service_role;
revoke all on function public.list_agent_operator_inbox(
    uuid, integer, timestamp with time zone, uuid
) from service_role;
revoke all on function public.get_agent_work_order(uuid, uuid)
from service_role;
revoke all on function public.get_agent_company_dashboard(uuid)
from service_role;

commit;
