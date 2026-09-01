-- LOCAL DISPOSABLE DATABASE ONLY.
-- Run immediately after 20260831180000 and before 20260901120000. This proves
-- that separately committed migration files do not create an ordinary-role
-- EXECUTE window.

do $check$
declare
    signature text;
    role_name text;
    function_oid oid;
    table_name text;
    table_oid oid;
    owner_oid oid := pg_catalog.to_regrole('postgres');
    acl_difference_count integer;
begin
    if exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_managed_inspector'
    ) then
        raise exception 'dedicated role must not exist before boundary migration';
    end if;

    if pg_catalog.to_regrole('coineasy_acl_hostile_fixture') is null
       or pg_catalog.to_regrole('coineasy_acl_hostile_member_fixture') is null then
        raise exception 'hostile default-ACL fixtures are required for the local transition proof';
    end if;

    if (
        select count(*)
        from pg_catalog.pg_proc p
        join pg_catalog.pg_namespace n on n.oid = p.pronamespace
        where n.nspname in ('private', 'public')
          and (
              p.proname like '%managed_telegram_inspect%'
              or p.proname = 'inspect_managed_telegram_delivery_unknown'
          )
    ) <> 8 then
        raise exception 'managed function inventory is not exact after build migration';
    end if;

    foreach signature in array array[
        'private.deny_managed_telegram_inspect_ledger_mutation()',
        'private.managed_telegram_inspect_hash(jsonb)',
        'private.require_managed_telegram_inspect_identity(uuid,text)',
        'private.validate_managed_telegram_inspect_request(jsonb,timestamptz)',
        'private.managed_telegram_inspect_fresh_subject(jsonb)',
        'public.managed_telegram_inspect_context(uuid,text)',
        'public.register_managed_telegram_inspect_consent(uuid,jsonb,text)',
        'public.inspect_managed_telegram_delivery_unknown(uuid)'
    ] loop
        function_oid := pg_catalog.to_regprocedure(signature);
        if function_oid is null then
            raise exception 'managed function missing after build migration';
        end if;

        if (
            select count(*)
            from pg_catalog.pg_proc p
            cross join lateral pg_catalog.aclexplode(
                coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
            ) acl
            where p.oid = function_oid
        ) <> 1 or exists (
            select 1
            from pg_catalog.pg_proc p
            cross join lateral pg_catalog.aclexplode(
                coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
            ) acl
            where p.oid = function_oid
              and not (
                  acl.grantee = owner_oid
                  and acl.privilege_type = 'EXECUTE'
                  and not acl.is_grantable
              )
        ) or exists (
            select 1 from pg_catalog.pg_proc p
            where p.oid = function_oid and p.proowner <> owner_oid
        ) then
            raise exception 'managed function ACL is not postgres-owner-only between migrations';
        end if;

        if signature like 'public.%' then
            foreach role_name in array array[
                'anon',
                'authenticated',
                'service_role',
                'coineasy_telegram_resolution',
                'coineasy_acl_hostile_fixture',
                'coineasy_acl_hostile_member_fixture'
            ] loop
                if pg_catalog.has_function_privilege(role_name, function_oid, 'EXECUTE') then
                    raise exception '% can execute managed entry RPC between migrations', role_name;
                end if;
            end loop;
        end if;
    end loop;

    if (
        select count(*)
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        where n.nspname in ('private', 'public')
          and c.relname like 'managed_telegram_inspect_%'
          and c.relkind in ('r', 'p', 'v', 'm', 'S', 'f')
    ) <> 4 then
        raise exception 'managed relation inventory is not exact after build migration';
    end if;

    foreach table_name in array array[
        'managed_telegram_inspect_releases',
        'managed_telegram_inspect_allowlist',
        'managed_telegram_inspect_consents',
        'managed_telegram_inspect_revocations'
    ] loop
        select c.oid into table_oid
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'private' and c.relname = table_name and c.relkind = 'r';
        if table_oid is null then
            raise exception 'managed table missing after build migration';
        end if;
        if exists (
            select 1 from pg_catalog.pg_class c
            where c.oid = table_oid and c.relowner <> owner_oid
        ) then
            raise exception 'managed table owner is not postgres between migrations';
        end if;
        select count(*) into acl_difference_count
        from (
            (select acl.grantee, acl.privilege_type, acl.is_grantable
             from pg_catalog.pg_class c
             cross join lateral pg_catalog.aclexplode(
                 coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))
             ) acl
             where c.oid = table_oid
             except
             select acl.grantee, acl.privilege_type, acl.is_grantable
             from pg_catalog.aclexplode(pg_catalog.acldefault('r', owner_oid)) acl)
            union all
            (select acl.grantee, acl.privilege_type, acl.is_grantable
             from pg_catalog.aclexplode(pg_catalog.acldefault('r', owner_oid)) acl
             except
             select acl.grantee, acl.privilege_type, acl.is_grantable
             from pg_catalog.pg_class c
             cross join lateral pg_catalog.aclexplode(
                 coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))
             ) acl
             where c.oid = table_oid)
        ) differences;
        if acl_difference_count <> 0 then
            raise exception 'managed table ACL is not postgres-owner-only between migrations';
        end if;
        if exists (
            select 1
            from pg_catalog.pg_attribute a
            cross join lateral pg_catalog.aclexplode(a.attacl) acl
            where a.attrelid = table_oid and a.attnum > 0 and not a.attisdropped
        ) then
            raise exception 'managed column ACL exists between migrations';
        end if;
    end loop;
end
$check$;
