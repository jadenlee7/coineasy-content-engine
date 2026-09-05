-- Transactional security smoke for the disposable Harmony Preview fabric.
-- Run as database owner after migrations 20260825130000..140000. No rows persist.

begin;

do $test$
declare
    table_name text;
    role_name text;
    role_row pg_catalog.pg_roles%rowtype;
begin
    foreach table_name in array array[
        'agent_runtime.harmony_connector_attestation_receipts',
        'agent_runtime.harmony_signals',
        'agent_runtime.harmony_rounds',
        'agent_runtime.harmony_plans',
        'agent_runtime.harmony_stage_receipts',
        'agent_runtime.harmony_operator_inbox',
        'private.harmony_preview_environment_fence',
        'private.harmony_preview_squid_specialist_bindings'
    ] loop
        if not exists (
            select 1
            from pg_catalog.pg_class relation
            join pg_catalog.pg_namespace namespace
              on namespace.oid = relation.relnamespace
            where namespace.nspname = pg_catalog.split_part(table_name, '.', 1)
              and relation.relname = pg_catalog.split_part(table_name, '.', 2)
              and relation.relrowsecurity
              and relation.relforcerowsecurity
        ) then
            raise exception 'Harmony table is not FORCE RLS: %', table_name;
        end if;
        if exists (
            select 1
            from pg_catalog.pg_policies policy
            where policy.schemaname = pg_catalog.split_part(table_name, '.', 1)
              and policy.tablename = pg_catalog.split_part(table_name, '.', 2)
              and policy.cmd <> 'SELECT'
        ) then
            raise exception 'Harmony table has a write policy: %', table_name;
        end if;
        foreach role_name in array array[
            'public', 'anon', 'authenticated', 'service_role',
            'coineasy_harmony_connector', 'coineasy_harmony_orchestrator',
            'coineasy_harmony_content', 'coineasy_harmony_qa',
            'coineasy_harmony_operator', 'coineasy_harmony_recap',
            'coineasy_harmony_dashboard'
        ] loop
            if pg_catalog.has_table_privilege(
                role_name, table_name, 'select,insert,update,delete,truncate,references,trigger'
            ) then
                raise exception 'direct Harmony table privilege leaked: % -> %',
                    role_name, table_name;
            end if;
        end loop;
    end loop;

    foreach role_name in array array[
        'coineasy_harmony_connector', 'coineasy_harmony_orchestrator',
        'coineasy_harmony_content', 'coineasy_harmony_qa',
        'coineasy_harmony_operator', 'coineasy_harmony_recap',
        'coineasy_harmony_dashboard'
    ] loop
        select * into strict role_row
        from pg_catalog.pg_roles role_value
        where role_value.rolname = role_name;
        if role_row.rolsuper or role_row.rolinherit or role_row.rolcreaterole
           or role_row.rolcreatedb or role_row.rolcanlogin or role_row.rolreplication
           or role_row.rolbypassrls
        then
            raise exception 'Harmony role is privileged: %', role_name;
        end if;
        if not pg_catalog.pg_has_role('authenticator', role_name, 'MEMBER') then
            raise exception 'authenticator cannot assume Harmony role: %', role_name;
        end if;
        -- PostgreSQL 16 grants the creating CREATEROLE principal an
        -- ADMIN-only membership with SET/INHERIT disabled.  Supabase creates
        -- custom roles as postgres, so accept that non-assumable management
        -- edge together with the one intended authenticator SET edge.
        if exists (
            select 1
            from pg_catalog.pg_auth_members membership
            join pg_catalog.pg_roles granted_role
              on granted_role.oid = membership.roleid
            join pg_catalog.pg_roles member_role
              on member_role.oid = membership.member
            where granted_role.rolname = role_name
              and not (
                  (
                      member_role.rolname = 'authenticator'
                      and not membership.admin_option
                      and not membership.inherit_option
                      and membership.set_option
                  )
                  or (
                      member_role.rolname = 'postgres'
                      and membership.admin_option
                      and not membership.inherit_option
                      and not membership.set_option
                  )
              )
        ) then
            raise exception 'unexpected principal can assume Harmony role: %', role_name;
        end if;
        if not pg_catalog.has_schema_privilege(role_name, 'public', 'usage')
           or pg_catalog.has_schema_privilege(role_name, 'public', 'create')
           or pg_catalog.has_schema_privilege(role_name, 'private', 'usage')
           or pg_catalog.has_schema_privilege(role_name, 'agent_runtime', 'usage')
        then
            raise exception 'Harmony role schema privilege is invalid: %', role_name;
        end if;
        if exists (
            select 1
            from pg_catalog.pg_auth_members membership
            join pg_catalog.pg_roles granted_role
              on granted_role.oid = membership.roleid
            join pg_catalog.pg_roles member_role
              on member_role.oid = membership.member
            where member_role.rolname = role_name
        ) then
            raise exception 'Harmony role belongs to another role: %', role_name;
        end if;
    end loop;
end
$test$;

do $test$
begin
    if exists (
        select 1 from private.harmony_preview_squid_specialist_bindings
    ) then
        raise exception 'fixed-specialist roster was not empty by default';
    end if;
end
$test$;

do $test$
declare
    role_name text;
    expected text[];
    actual text[];
begin
    foreach role_name in array array[
        'coineasy_harmony_connector', 'coineasy_harmony_orchestrator',
        'coineasy_harmony_content', 'coineasy_harmony_qa',
        'coineasy_harmony_operator', 'coineasy_harmony_recap',
        'coineasy_harmony_dashboard'
    ] loop
        expected := case role_name
            when 'coineasy_harmony_connector' then array[
                'submit_preview_harmony_signal(uuid,text,uuid,jsonb)'
            ]
            when 'coineasy_harmony_orchestrator' then array[
                'create_preview_harmony_squid_plan(uuid,text,uuid,uuid,uuid,text[],text)',
                'get_preview_harmony_round(uuid,text,uuid)'
            ]
            when 'coineasy_harmony_content' then array[
                'append_preview_harmony_squid_stage(uuid,text,uuid,uuid,text,uuid,uuid,jsonb)'
            ]
            when 'coineasy_harmony_qa' then array[
                'claim_preview_harmony_squid_codex_qa(uuid,text,integer)',
                'prepare_preview_harmony_squid_codex_qa(uuid,text,uuid,uuid,bigint)',
                'reconcile_preview_harmony_squid_codex_qa_lease(uuid,text,integer)',
                'record_preview_harmony_squid_qa_denial(uuid,text,uuid,uuid,uuid,jsonb)',
                'start_preview_harmony_squid_codex_qa_attempt(uuid,text,text,text)',
                'submit_preview_harmony_squid_codex_qa_result(uuid,text,text,text,jsonb,text,text[],text)',
                'verify_preview_harmony_squid_codex_qa_result(uuid,text,text)'
            ]
            when 'coineasy_harmony_operator' then array[
                'append_preview_harmony_squid_stage(uuid,text,uuid,uuid,text,uuid,uuid,jsonb)',
                'get_preview_harmony_round(uuid,text,uuid)'
            ]
            when 'coineasy_harmony_recap' then array[
                'append_preview_harmony_squid_stage(uuid,text,uuid,uuid,text,uuid,uuid,jsonb)'
            ]
            when 'coineasy_harmony_dashboard' then array[
                'get_preview_harmony_dashboard(uuid,text)'
            ]
        end;
        select coalesce(pg_catalog.array_agg(
            routine.oid::pg_catalog.regprocedure::text
            order by routine.oid::pg_catalog.regprocedure::text
        ), '{}'::text[])
        into actual
        from pg_catalog.pg_proc routine
        join pg_catalog.pg_namespace namespace
          on namespace.oid = routine.pronamespace
        where namespace.nspname = 'public'
          and pg_catalog.has_function_privilege(role_name, routine.oid, 'execute');
        select pg_catalog.array_agg(value order by value)
        into expected from unnest(expected) item(value);
        if actual is distinct from expected then
            raise exception 'public routine effective EXECUTE drift for %: % <> %',
                role_name, actual, expected;
        end if;
        if exists (
            select 1
            from pg_catalog.pg_class relation
            join pg_catalog.pg_namespace namespace
              on namespace.oid = relation.relnamespace
            where relation.relkind in ('r', 'p', 'v', 'm', 'f')
              and namespace.nspname in ('agent_runtime', 'private')
              and pg_catalog.has_table_privilege(
                    role_name,
                    pg_catalog.format('%I.%I', namespace.nspname, relation.relname),
                    'select,insert,update,delete,truncate,references,trigger'
                  )
        ) then
            raise exception 'Harmony role has any private/agent table grant: %',
                role_name;
        end if;
    end loop;
end
$test$;

do $test$
declare
    signature text;
    function_oid pg_catalog.oid;
    definition text;
begin
    foreach signature in array array[
        'public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)',
        'public.create_preview_harmony_squid_plan(uuid,text,uuid,uuid,uuid,text[],text)',
        'public.append_preview_harmony_squid_stage(uuid,text,uuid,uuid,text,uuid,uuid,jsonb)',
        'public.record_preview_harmony_squid_qa_denial(uuid,text,uuid,uuid,uuid,jsonb)',
        'public.get_preview_harmony_round(uuid,text,uuid)',
        'public.get_preview_harmony_dashboard(uuid,text)'
    ] loop
        function_oid := pg_catalog.to_regprocedure(signature);
        if function_oid is null then
            raise exception 'Harmony RPC missing: %', signature;
        end if;
        if not (select routine.prosecdef from pg_catalog.pg_proc routine
                where routine.oid = function_oid) then
            raise exception 'Harmony RPC is not SECURITY DEFINER: %', signature;
        end if;
        if not coalesce((select routine.proconfig @> array['search_path=""']::text[]
                         from pg_catalog.pg_proc routine
                         where routine.oid = function_oid), false) then
            raise exception 'Harmony RPC lacks empty search_path: %', signature;
        end if;
        if pg_catalog.has_function_privilege('public', signature, 'execute')
           or pg_catalog.has_function_privilege('anon', signature, 'execute')
           or pg_catalog.has_function_privilege('authenticated', signature, 'execute')
           or pg_catalog.has_function_privilege('service_role', signature, 'execute')
        then
            raise exception 'broad Harmony RPC execute leaked: %', signature;
        end if;
        definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(function_oid));
        if definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?public\.(approvals|publications)'
           or definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?agent_runtime\.buzz_'
           or definition ~ '(update|delete)[[:space:]]+private\.grok_qa_dispatch_outbox'
        then
            raise exception 'Harmony RPC contains forbidden side-effect SQL: %', signature;
        end if;
    end loop;
end
$test$;

do $test$
begin
    if not pg_catalog.has_function_privilege(
        'coineasy_harmony_connector',
        'public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)', 'execute'
    ) or pg_catalog.has_function_privilege(
        'coineasy_harmony_connector',
        'public.create_preview_harmony_squid_plan(uuid,text,uuid,uuid,uuid,text[],text)',
        'execute'
    ) or not pg_catalog.has_function_privilege(
        'coineasy_harmony_orchestrator',
        'public.create_preview_harmony_squid_plan(uuid,text,uuid,uuid,uuid,text[],text)',
        'execute'
    ) or not pg_catalog.has_function_privilege(
        'coineasy_harmony_dashboard',
        'public.get_preview_harmony_dashboard(uuid,text)', 'execute'
    ) or not pg_catalog.has_function_privilege(
        'coineasy_harmony_qa',
        'public.record_preview_harmony_squid_qa_denial(uuid,text,uuid,uuid,uuid,jsonb)',
        'execute'
    ) or pg_catalog.has_function_privilege(
        'coineasy_harmony_content',
        'public.record_preview_harmony_squid_qa_denial(uuid,text,uuid,uuid,uuid,jsonb)',
        'execute'
    ) or pg_catalog.has_function_privilege(
        'coineasy_harmony_dashboard',
        'public.append_preview_harmony_squid_stage(uuid,text,uuid,uuid,text,uuid,uuid,jsonb)',
        'execute'
    ) then
        raise exception 'Harmony RPC role matrix is invalid';
    end if;
end
$test$;

insert into private.harmony_preview_environment_fence(
    branch_ref, active, expires_at
) values (
    'previewsecurity00000', true, statement_timestamp() + interval '1 hour'
);

select pg_catalog.set_config(
    'request.jwt.claims',
    pg_catalog.jsonb_build_object(
        'iss', 'supabase', 'aud', 'authenticated',
        'sub', 'a1000000-0000-4000-8000-000000000001',
        'role', 'coineasy_harmony_connector',
        'workspace_id', 'a0000000-0000-4000-8000-000000000001',
        'client_id', 'squid', 'environment', 'preview',
        'ref', 'previewsecurity00000',
        'producer_principal_id', 'a1000000-0000-4000-8000-000000000001',
        'release_sha', pg_catalog.repeat('b', 40),
        'config_sha256', pg_catalog.repeat('c', 64),
        'capability', 'harmony_submit_quiz_bot',
        'connector_id', 'quiz_bot_security',
        'jti', 'a2000000-0000-4000-8000-000000000001',
        'iat', extract(epoch from statement_timestamp())::bigint,
        'exp', extract(epoch from statement_timestamp())::bigint + 3600,
        'automatic_publication', false,
        'max_cost_microusd', 0,
        'max_external_actions', 0
    )::text,
    true
);

do $test$
begin
    if not private.harmony_preview_scope_matches(
        'a0000000-0000-4000-8000-000000000001', 'squid',
        array['coineasy_harmony_connector']::text[]
    ) or private.harmony_preview_scope_matches(
        'a0000000-0000-4000-8000-000000000001', 'yellow',
        array['coineasy_harmony_connector']::text[]
    ) or not private.harmony_preview_lane_visible(
        'a0000000-0000-4000-8000-000000000001', 'squid', 'quiz_bot',
        array['coineasy_harmony_connector']::text[]
    ) or private.harmony_preview_lane_visible(
        'a0000000-0000-4000-8000-000000000001', 'squid', 'community_ops',
        array['coineasy_harmony_connector']::text[]
    ) then
        raise exception 'client/lane RLS claim fence failed';
    end if;
end
$test$;

select pg_catalog.set_config(
    'request.jwt.claims',
    (pg_catalog.current_setting('request.jwt.claims')::jsonb
        || '{"automatic_publication":true}'::jsonb)::text,
    true
);

do $test$
begin
    if private.harmony_preview_scope_matches(
        'a0000000-0000-4000-8000-000000000001', 'squid',
        array['coineasy_harmony_connector']::text[]
    ) then
        raise exception 'automatic publication claim crossed fail-closed fence';
    end if;
end
$test$;

rollback;
