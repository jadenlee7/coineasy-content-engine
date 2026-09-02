-- managed-inspector-production-postflight@1
-- One catalog-only query. Every returned row must have passed=true.
with recursive
expected_migrations(version, migration_name, migration_sha256) as (
    values
        ('20260831180000'::text, 'managed_auth_telegram_inspect'::text,
            '61bf61ee4be6993c88d471b0d9b3e3fa2bf1063ba87d1a901cceff2fc953ab46'::text),
        ('20260901120000'::text, 'managed_inspector_role_boundary'::text,
            '256f8ddb19a6bbfaf2fc98ea168a1da6dc1945c54856f7450b0ba90d70817a25'::text)
),
target_role as (
    select r.*
    from pg_catalog.pg_roles r
    where r.rolname = 'coineasy_managed_inspector'
),
expected_tables(schema_name, relation_name) as (
    values
        ('private'::text, 'managed_telegram_inspect_releases'::text),
        ('private', 'managed_telegram_inspect_allowlist'),
        ('private', 'managed_telegram_inspect_consents'),
        ('private', 'managed_telegram_inspect_revocations')
),
expected_triggers(
    trigger_name, tgtype, tgenabled,
    function_schema, function_name, function_argument_types,
    tgnargs, tgargs_byte_length, tgqual_is_null, tgattr_is_empty
) as (
    values
        ('managed_inspect_immutable'::text, 27::smallint, 'O'::"char",
            'private'::text, 'deny_managed_telegram_inspect_ledger_mutation'::text,
            ''::text, 0::smallint, 0::integer, true, true),
        ('managed_inspect_no_truncate', 34::smallint, 'O'::"char",
            'private', 'deny_managed_telegram_inspect_ledger_mutation', '',
            0::smallint, 0::integer, true, true)
),
expected_functions(
    schema_name, function_name, argument_types, public_entrypoint,
    security_definer, expected_proconfig
) as (
    values
        ('private'::text, 'deny_managed_telegram_inspect_ledger_mutation'::text,
            ''::text, false, true, array['search_path=""']::text[]),
        ('private', 'managed_telegram_inspect_hash',
            'jsonb', false, false, array['search_path=""']),
        ('private', 'require_managed_telegram_inspect_identity',
            'uuid, text', false, true, array['search_path=""', 'TimeZone=UTC']),
        ('private', 'validate_managed_telegram_inspect_request',
            'jsonb, timestamp with time zone', false, false,
            array['search_path=""', 'TimeZone=UTC']),
        ('private', 'managed_telegram_inspect_fresh_subject',
            'jsonb', false, true, array['search_path=""', 'TimeZone=UTC']),
        ('public', 'managed_telegram_inspect_context',
            'uuid, text', true, true, array['search_path=""', 'TimeZone=UTC']),
        ('public', 'register_managed_telegram_inspect_consent',
            'uuid, jsonb, text', true, true, array['search_path=""', 'TimeZone=UTC']),
        ('public', 'inspect_managed_telegram_delivery_unknown',
            'uuid', true, true, array['search_path=""', 'TimeZone=UTC'])
),
expected_public_signatures(signature) as (
    values
        ('public.inspect_managed_telegram_delivery_unknown(uuid)'::text),
        ('public.managed_telegram_inspect_context(uuid, text)'::text),
        ('public.register_managed_telegram_inspect_consent(uuid, jsonb, text)'::text)
),
observed_exposed_schemas(schema_name) as (
    values ('public'::text), ('graphql_public'::text)
),
inspected_schemas as (
    select n.oid, n.nspname
    from pg_catalog.pg_namespace n
    where n.nspname <> 'information_schema'
      and n.nspname !~ '^pg_'
),
expected_authenticator_admin_members(
    role_name, admin_option, inherit_option, set_option,
    rolcreaterole, rolbypassrls, grantor_name
) as (
    values
        ('postgres'::text, true, true, true, true, true, 'supabase_admin'::text),
        ('supabase_storage_admin'::text, false, false, true, true, false, 'supabase_admin')
),
expected_target_direct_members(
    role_name, admin_option, inherit_option, set_option,
    rolcreaterole, rolbypassrls, grantor_name
) as (
    values
        ('authenticator'::text, false, false, true, false, false, 'postgres'::text),
        ('postgres', true, false, false, true, true, 'supabase_admin')
),
expected_target_descendant_edges(
    role_path, parent_role_name, member_role_name, grantor_name,
    admin_option, inherit_option, set_option,
    member_rolsuper, member_rolinherit, member_rolcreaterole,
    member_rolcreatedb, member_rolcanlogin, member_rolreplication,
    member_rolbypassrls
) as (
    values
        (
            array['coineasy_managed_inspector', 'authenticator']::text[],
            'coineasy_managed_inspector'::text, 'authenticator'::text,
            'postgres'::text, false, false, true,
            false, false, false, false, true, false, false
        ),
        (
            array['coineasy_managed_inspector', 'postgres']::text[],
            'coineasy_managed_inspector', 'postgres', 'supabase_admin',
            true, false, false,
            false, true, true, true, true, true, true
        ),
        (
            array['coineasy_managed_inspector', 'authenticator', 'postgres']::text[],
            'authenticator', 'postgres', 'supabase_admin',
            true, true, true,
            false, true, true, true, true, true, true
        ),
        (
            array['coineasy_managed_inspector', 'authenticator', 'supabase_storage_admin']::text[],
            'authenticator', 'supabase_storage_admin', 'supabase_admin',
            false, false, true,
            false, false, true, false, true, false, false
        )
    union all
    select * from (values
        (
            array['coineasy_managed_inspector', 'postgres', 'cli_login_postgres']::text[],
            'postgres'::text, 'cli_login_postgres'::text,
            'supabase_admin'::text, false, false, true,
            false, false, false, false, true, false, false
        ),
        (
            array['coineasy_managed_inspector', 'authenticator', 'postgres', 'cli_login_postgres']::text[],
            'postgres', 'cli_login_postgres', 'supabase_admin',
            false, false, true,
            false, false, false, false, true, false, false
        )
    ) cli_login(
        role_path, parent_role_name, member_role_name, grantor_name,
        admin_option, inherit_option, set_option,
        member_rolsuper, member_rolinherit, member_rolcreaterole,
        member_rolcreatedb, member_rolcanlogin, member_rolreplication,
        member_rolbypassrls
    )
    where pg_catalog.to_regrole('cli_login_postgres') is not null
),
actual_authenticator_admin_members(
    role_name, admin_option, inherit_option, set_option,
    rolcreaterole, rolbypassrls, grantor_name
) as (
    select
        member.rolname, m.admin_option, m.inherit_option, m.set_option,
        member.rolcreaterole, member.rolbypassrls, grantor.rolname
    from pg_catalog.pg_auth_members m
    join pg_catalog.pg_roles parent on parent.oid = m.roleid
    join pg_catalog.pg_roles member on member.oid = m.member
    join pg_catalog.pg_roles grantor on grantor.oid = m.grantor
    where parent.rolname = 'authenticator'
),
actual_target_direct_members(
    role_name, admin_option, inherit_option, set_option,
    rolcreaterole, rolbypassrls, grantor_name
) as (
    select
        member.rolname, m.admin_option, m.inherit_option, m.set_option,
        member.rolcreaterole, member.rolbypassrls, grantor.rolname
    from target_role target
    join pg_catalog.pg_auth_members m on m.roleid = target.oid
    join pg_catalog.pg_roles member on member.oid = m.member
    join pg_catalog.pg_roles grantor on grantor.oid = m.grantor
),
actual_target_functions as (
    select
        n.nspname as schema_name,
        p.proname as function_name,
        pg_catalog.oidvectortypes(p.proargtypes) as argument_types,
        p.oid,
        p.proowner,
        p.prosecdef,
        p.proconfig
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('private', 'public')
      and (
          p.proname like '%managed_telegram_inspect%'
          or p.proname = 'inspect_managed_telegram_delivery_unknown'
      )
),
actual_target_relations as (
    select n.nspname as schema_name, c.relname as relation_name, c.relkind
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('private', 'public')
      and c.relname like 'managed_telegram_inspect_%'
      and c.relkind in ('r', 'p', 'v', 'm', 'S', 'f')
),
expected_function_acl(signature, grantee_name, privilege_type, is_grantable) as (
    select
        pg_catalog.format(
            '%I.%I(%s)', e.schema_name, e.function_name, e.argument_types
        ),
        'postgres'::text,
        'EXECUTE'::text,
        false
    from expected_functions e
    union all
    select
        pg_catalog.format(
            '%I.%I(%s)', e.schema_name, e.function_name, e.argument_types
        ),
        'coineasy_managed_inspector'::text,
        'EXECUTE'::text,
        false
    from expected_functions e
    where e.public_entrypoint
),
actual_function_acl(signature, grantee_name, privilege_type, is_grantable) as (
    select
        pg_catalog.format(
            '%I.%I(%s)', a.schema_name, a.function_name, a.argument_types
        ),
        case
            when acl.grantee = 0 then 'PUBLIC'
            else coalesce(grantee.rolname, 'oid:' || acl.grantee::text)
        end,
        acl.privilege_type,
        acl.is_grantable
    from actual_target_functions a
    join pg_catalog.pg_proc p on p.oid = a.oid
    cross join lateral pg_catalog.aclexplode(
        coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
    ) acl
    left join pg_catalog.pg_roles grantee on grantee.oid = acl.grantee
),
table_privileges(privilege_type) as (
    values
        ('DELETE'::text), ('INSERT'), ('MAINTAIN'), ('REFERENCES'),
        ('SELECT'), ('TRIGGER'), ('TRUNCATE'), ('UPDATE')
),
expected_table_acl(signature, grantee_name, privilege_type, is_grantable) as (
    select
        pg_catalog.format('%I.%I', e.schema_name, e.relation_name),
        'postgres'::text,
        p.privilege_type,
        false
    from expected_tables e
    cross join table_privileges p
),
actual_table_acl(signature, grantee_name, privilege_type, is_grantable) as (
    select
        pg_catalog.format('%I.%I', e.schema_name, e.relation_name),
        case
            when acl.grantee = 0 then 'PUBLIC'
            else coalesce(grantee.rolname, 'oid:' || acl.grantee::text)
        end,
        acl.privilege_type,
        acl.is_grantable
    from expected_tables e
    join pg_catalog.pg_namespace n on n.nspname = e.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid and c.relname = e.relation_name and c.relkind = 'r'
    cross join lateral pg_catalog.aclexplode(
        coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))
    ) acl
    left join pg_catalog.pg_roles grantee on grantee.oid = acl.grantee
),
actual_target_column_acl(
    signature, grantee_name, privilege_type, is_grantable
) as (
    select
        pg_catalog.format('%I.%I.%I', e.schema_name, e.relation_name, a.attname),
        case
            when acl.grantee = 0 then 'PUBLIC'
            else coalesce(grantee.rolname, 'oid:' || acl.grantee::text)
        end,
        acl.privilege_type,
        acl.is_grantable
    from expected_tables e
    join pg_catalog.pg_namespace n on n.nspname = e.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid and c.relname = e.relation_name and c.relkind = 'r'
    join pg_catalog.pg_attribute a
      on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
    cross join lateral pg_catalog.aclexplode(a.attacl) acl
    left join pg_catalog.pg_roles grantee on grantee.oid = acl.grantee
),
actual_target_triggers as (
    select
        e.schema_name,
        e.relation_name,
        t.tgname as trigger_name,
        t.tgtype,
        t.tgenabled,
        t.tgnargs,
        pg_catalog.octet_length(t.tgargs) as tgargs_byte_length,
        t.tgqual,
        t.tgattr,
        fnn.nspname as function_schema,
        fn.proname as function_name,
        pg_catalog.oidvectortypes(fn.proargtypes) as function_argument_types
    from expected_tables e
    join pg_catalog.pg_namespace n on n.nspname = e.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid and c.relname = e.relation_name and c.relkind = 'r'
    join pg_catalog.pg_trigger t on t.tgrelid = c.oid and not t.tgisinternal
    join pg_catalog.pg_proc fn on fn.oid = t.tgfoid
    join pg_catalog.pg_namespace fnn on fnn.oid = fn.pronamespace
),
effective_exposed_functions as (
    select pg_catalog.format(
        '%I.%I(%s)', n.nspname, p.proname, pg_catalog.oidvectortypes(p.proargtypes)
    ) as signature
    from target_role r
    join pg_catalog.pg_proc p
      on pg_catalog.has_function_privilege(r.oid, p.oid, 'EXECUTE')
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    join observed_exposed_schemas e on e.schema_name = n.nspname
    where pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')
),
unexpected_relation_privileges as (
    select pg_catalog.format('%I.%I', n.nspname, c.relname) as object_name
    from target_role r
    join pg_catalog.pg_class c
     on c.relkind in ('r', 'p', 'v', 'm', 'f')
     and pg_catalog.has_table_privilege(
         r.oid, c.oid,
         'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'
     )
    join inspected_schemas n on n.oid = c.relnamespace
    where pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')
),
unexpected_column_privileges as (
    select pg_catalog.format('%I.%I.%I', n.nspname, c.relname, a.attname) as object_name
    from target_role r
    join pg_catalog.pg_class c on c.relkind in ('r', 'p', 'v', 'm', 'f')
    join inspected_schemas n on n.oid = c.relnamespace
    join pg_catalog.pg_attribute a
      on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
    where pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')
      and pg_catalog.has_column_privilege(
          r.oid, c.oid, a.attnum, 'SELECT,INSERT,UPDATE,REFERENCES'
      )
),
unexpected_sequence_privileges as (
    select pg_catalog.format('%I.%I', n.nspname, c.relname) as object_name
    from target_role r
    join pg_catalog.pg_class c
      on c.relkind = 'S'
     and pg_catalog.has_sequence_privilege(r.oid, c.oid, 'USAGE,SELECT,UPDATE')
    join inspected_schemas n on n.oid = c.relnamespace
    where pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')
),
unexpected_schema_privileges as (
    select
        n.nspname as object_name,
        pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE') as has_usage,
        pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE') as has_create
    from target_role r
    join inspected_schemas n
      on pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE')
      or (
          n.nspname <> 'public'
          and pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')
      )
),
membership_descendants(
    root_role, parent_role, member, membership_path, role_path,
    parent_role_name, member_role_name, grantor_name,
    admin_option, inherit_option, set_option,
    member_rolsuper, member_rolinherit, member_rolcreaterole,
    member_rolcreatedb, member_rolcanlogin, member_rolreplication,
    member_rolbypassrls
) as (
    select
        m.roleid, m.roleid, m.member, array[m.roleid, m.member]::oid[],
        array[parent.rolname, member.rolname]::text[],
        parent.rolname, member.rolname, grantor.rolname,
        m.admin_option, m.inherit_option, m.set_option,
        member.rolsuper, member.rolinherit, member.rolcreaterole,
        member.rolcreatedb, member.rolcanlogin, member.rolreplication,
        member.rolbypassrls
    from pg_catalog.pg_auth_members m
    join pg_catalog.pg_roles parent on parent.oid = m.roleid
    join pg_catalog.pg_roles member on member.oid = m.member
    join pg_catalog.pg_roles grantor on grantor.oid = m.grantor
    union all
    select
        d.root_role, m.roleid, m.member, d.membership_path || m.member,
        d.role_path || member.rolname,
        parent.rolname, member.rolname, grantor.rolname,
        m.admin_option, m.inherit_option, m.set_option,
        member.rolsuper, member.rolinherit, member.rolcreaterole,
        member.rolcreatedb, member.rolcanlogin, member.rolreplication,
        member.rolbypassrls
    from membership_descendants d
    join pg_catalog.pg_auth_members m on m.roleid = d.member
    join pg_catalog.pg_roles parent on parent.oid = m.roleid
    join pg_catalog.pg_roles member on member.oid = m.member
    join pg_catalog.pg_roles grantor on grantor.oid = m.grantor
    where not m.member = any(d.membership_path)
),
actual_target_descendant_edges(
    role_path, parent_role_name, member_role_name, grantor_name,
    admin_option, inherit_option, set_option,
    member_rolsuper, member_rolinherit, member_rolcreaterole,
    member_rolcreatedb, member_rolcanlogin, member_rolreplication,
    member_rolbypassrls
) as (
    select
        d.role_path, d.parent_role_name, d.member_role_name,
        d.grantor_name, d.admin_option, d.inherit_option,
        d.set_option, d.member_rolsuper, d.member_rolinherit,
        d.member_rolcreaterole, d.member_rolcreatedb,
        d.member_rolcanlogin, d.member_rolreplication,
        d.member_rolbypassrls
    from membership_descendants d
    join target_role target on target.oid = d.root_role
),
membership_parents(member, parent_role, membership_path) as (
    select m.member, m.roleid, array[m.member, m.roleid]::oid[]
    from pg_catalog.pg_auth_members m
    union all
    select p.member, m.roleid, p.membership_path || m.roleid
    from membership_parents p
    join pg_catalog.pg_auth_members m on m.member = p.parent_role
    where not m.roleid = any(p.membership_path)
),
owned_objects as (
    select 'relation:' || c.oid::pg_catalog.regclass::text as object_name
    from target_role r join pg_catalog.pg_class c on c.relowner = r.oid
    union all
    select 'function:' || p.oid::pg_catalog.regprocedure::text
    from target_role r join pg_catalog.pg_proc p on p.proowner = r.oid
    union all
    select 'schema:' || n.nspname
    from target_role r join pg_catalog.pg_namespace n on n.nspowner = r.oid
    union all
    select 'type:' || t.oid::pg_catalog.regtype::text
    from target_role r join pg_catalog.pg_type t on t.typowner = r.oid
),
unexpected_default_acl_privileges as (
    select
        owner.rolname || ':' || coalesce(n.nspname, '*') || ':' || d.defaclobjtype::text
        || ':' || acl.privilege_type as object_name
    from target_role r
    join pg_catalog.pg_default_acl d on true
    join pg_catalog.pg_roles owner on owner.oid = d.defaclrole
    left join pg_catalog.pg_namespace n on n.oid = d.defaclnamespace
    cross join lateral pg_catalog.aclexplode(d.defaclacl) acl
    where acl.grantee = r.oid
),
unexpected_public_target_execute as (
    select pg_catalog.format(
        '%I.%I(%s)', n.nspname, p.proname, pg_catalog.oidvectortypes(p.proargtypes)
    ) as signature
    from actual_target_functions f
    join pg_catalog.pg_proc p on p.oid = f.oid
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    cross join lateral pg_catalog.aclexplode(
        coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
    ) acl
    where acl.grantee = 0 and acl.privilege_type = 'EXECUTE'
),
unexpected_named_principal_execute as (
    select pg_catalog.format(
        '%s:%I.%I(%s)', principal.rolname, n.nspname, p.proname,
        pg_catalog.oidvectortypes(p.proargtypes)
    ) as signature
    from actual_target_functions f
    join pg_catalog.pg_proc p on p.oid = f.oid
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    join pg_catalog.pg_roles principal
      on principal.rolname in (
          'anon', 'authenticated', 'service_role', 'coineasy_telegram_resolution'
      )
     and pg_catalog.has_function_privilege(principal.oid, p.oid, 'EXECUTE')
),
checks(check_id, passed, expected, observed) as (
    select
        'read_only_executor_exact',
        current_user::text = 'supabase_read_only_user',
        'supabase_read_only_user',
        current_user::text
    union all
    select
        'transaction_read_only_on',
        pg_catalog.current_setting('transaction_read_only') = 'on',
        'on',
        pg_catalog.current_setting('transaction_read_only')
    union all
    select
        'observed_exposed_schemas_present',
        count(*) = 2,
        'graphql_public,public',
        coalesce(pg_catalog.string_agg(n.nspname, ',' order by n.nspname), '')
    from pg_catalog.pg_namespace n
    where n.nspname in (select schema_name from observed_exposed_schemas)
    union all
    select
        'migration_rows_exact',
        count(*) = 2
          and count(*) filter (
              where m.name = e.migration_name
                and pg_catalog.cardinality(m.statements) = 1
                and pg_catalog.encode(extensions.digest(
                    pg_catalog.convert_to(m.statements[1], 'UTF8'), 'sha256'
                ), 'hex') = e.migration_sha256
          ) = 2,
        'two canonical version/name rows with one exact-source statement each',
        coalesce(pg_catalog.string_agg(
            m.version || ':' || coalesce(m.name, '')
            || ':statements=' || coalesce(pg_catalog.cardinality(m.statements)::text, 'null')
            || ':sha256=' || coalesce(pg_catalog.encode(extensions.digest(
                pg_catalog.convert_to(m.statements[1], 'UTF8'), 'sha256'
            ), 'hex'), 'null'),
            ',' order by m.version
        ), '')
    from expected_migrations e
    left join supabase_migrations.schema_migrations m on m.version = e.version
    union all
    select
        'target_role_exists_once',
        count(*) = 1,
        '1',
        count(*)::text
    from target_role
    union all
    select
        'target_role_attributes_exact',
        count(*) = 1,
        'nologin,noinherit,nosuperuser,nocreaterole,nocreatedb,noreplication,nobypassrls',
        coalesce(pg_catalog.string_agg(pg_catalog.concat_ws(',',
            'login=' || r.rolcanlogin::text,
            'inherit=' || r.rolinherit::text,
            'super=' || r.rolsuper::text,
            'createrole=' || r.rolcreaterole::text,
            'createdb=' || r.rolcreatedb::text,
            'replication=' || r.rolreplication::text,
            'bypassrls=' || r.rolbypassrls::text
        ), ''), '')
    from target_role r
    where not r.rolcanlogin and not r.rolinherit and not r.rolsuper
      and not r.rolcreaterole and not r.rolcreatedb
      and not r.rolreplication and not r.rolbypassrls
    union all
    select
        'target_role_membership_cardinality',
        count(*) = (select count(*) from expected_target_direct_members),
        '2',
        count(*)::text
    from pg_catalog.pg_auth_members m
    join target_role r on r.oid = m.roleid
    union all
    select
        'authenticator_membership_exact',
        count(*) = 1,
        'admin=false,inherit=false,set=true',
        coalesce(pg_catalog.string_agg(pg_catalog.concat_ws(',',
            'admin=' || m.admin_option::text,
            'inherit=' || m.inherit_option::text,
            'set=' || m.set_option::text
        ), ''), '')
    from pg_catalog.pg_auth_members m
    join target_role r on r.oid = m.roleid
    join pg_catalog.pg_roles member on member.oid = m.member
    where member.rolname = 'authenticator'
      and not m.admin_option and not m.inherit_option and m.set_option
    union all
    select
        'target_role_has_no_parent_membership',
        count(*) = 0,
        '0',
        count(*)::text
    from pg_catalog.pg_auth_members m
    join target_role r on r.oid = m.member
    union all
    select
        'target_role_has_no_transitive_parent_membership',
        count(*) = 0,
        '0',
        count(*)::text
    from membership_parents p
    join target_role r on r.oid = p.member
    union all
    select
        'target_role_direct_members_exact',
        (select count(*) from actual_target_direct_members)
            = (select count(*) from expected_target_direct_members)
          and not exists (
              select 1
              from actual_target_direct_members a
              left join expected_target_direct_members e
                on e.role_name = a.role_name
               and e.admin_option = a.admin_option
               and e.inherit_option = a.inherit_option
               and e.set_option = a.set_option
               and e.rolcreaterole = a.rolcreaterole
               and e.rolbypassrls = a.rolbypassrls
               and e.grantor_name = a.grantor_name
              where e.role_name is null
          )
          and not exists (
              select 1
              from expected_target_direct_members e
              left join actual_target_direct_members a
                on a.role_name = e.role_name
               and a.admin_option = e.admin_option
               and a.inherit_option = e.inherit_option
               and a.set_option = e.set_option
               and a.rolcreaterole = e.rolcreaterole
               and a.rolbypassrls = e.rolbypassrls
               and a.grantor_name = e.grantor_name
              where a.role_name is null
          ),
        'authenticator plus canonical postgres platform edge',
        coalesce((
            select pg_catalog.string_agg(
                pg_catalog.concat_ws(',',
                    a.role_name,
                    'admin=' || a.admin_option::text,
                    'inherit=' || a.inherit_option::text,
                    'set=' || a.set_option::text
                ), ';' order by a.role_name
            )
            from actual_target_direct_members a
        ), '')
    union all
    select
        'target_role_transitive_members_exact',
        (select count(*) from actual_authenticator_admin_members)
            = (select count(*) from expected_authenticator_admin_members)
          and not exists (
              select 1
              from actual_authenticator_admin_members a
              left join expected_authenticator_admin_members e
                on e.role_name = a.role_name
               and e.admin_option = a.admin_option
               and e.inherit_option = a.inherit_option
               and e.set_option = a.set_option
               and e.rolcreaterole = a.rolcreaterole
               and e.rolbypassrls = a.rolbypassrls
               and e.grantor_name = a.grantor_name
              where e.role_name is null
          )
          and not exists (
              select 1
              from expected_authenticator_admin_members e
              left join actual_authenticator_admin_members a
                on a.role_name = e.role_name
               and a.admin_option = e.admin_option
               and a.inherit_option = e.inherit_option
               and a.set_option = e.set_option
               and a.rolcreaterole = e.rolcreaterole
               and a.rolbypassrls = e.rolbypassrls
               and a.grantor_name = e.grantor_name
              where a.role_name is null
          )
          and not exists (
              (
                  select * from actual_target_descendant_edges
                  except all
                  select * from expected_target_descendant_edges
              )
              union all
              (
                  select * from expected_target_descendant_edges
                  except all
                  select * from actual_target_descendant_edges
              )
          ),
        'four exact hosted paths plus optional two exact cli_login_postgres paths',
        coalesce((
            select pg_catalog.string_agg(
                pg_catalog.array_to_string(member.role_path, '>'),
                ',' order by member.role_path::text
            )
            from actual_target_descendant_edges member
        ), '')
    union all
    select
        'public_schema_boundary',
        count(*) = 1,
        'usage=true,create=false',
        coalesce(pg_catalog.string_agg(
            'usage=' || pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')::text
            || ',create=' || pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE')::text,
            ''
        ), '')
    from target_role r
    join pg_catalog.pg_namespace n on n.nspname = 'public'
    where pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')
      and not pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE')
    union all
    select
        'private_schema_boundary',
        count(*) = 1,
        'usage=false,create=false',
        coalesce(pg_catalog.string_agg(
            'usage=' || pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')::text
            || ',create=' || pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE')::text,
            ''
        ), '')
    from target_role r
    join pg_catalog.pg_namespace n on n.nspname = 'private'
    where not pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')
      and not pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE')
    union all
    select
        'unexpected_schema_privileges_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(
            object_name || ':usage=' || has_usage::text || ',create=' || has_create::text,
            ',' order by object_name
        ), '')
    from unexpected_schema_privileges
    union all
    select
        'target_tables_exact',
        count(*) = 4,
        '4',
        count(*)::text
    from expected_tables e
    join pg_catalog.pg_namespace n on n.nspname = e.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid and c.relname = e.relation_name and c.relkind = 'r'
    union all
    select
        'target_relations_no_unexpected_object',
        count(*) = 4
          and count(*) filter (where e.relation_name is not null) = 4,
        '4 expected tables and no target-prefixed view, foreign table, or sequence',
        count(*)::text
    from actual_target_relations a
    left join expected_tables e
      on e.schema_name = a.schema_name
     and e.relation_name = a.relation_name
     and a.relkind = 'r'
    union all
    select
        'target_table_acls_explicit',
        count(*) = 4,
        '4',
        count(*)::text
    from expected_tables e
    join pg_catalog.pg_namespace n on n.nspname = e.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid and c.relname = e.relation_name and c.relkind = 'r'
    where c.relacl is not null
    union all
    select
        'target_table_acl_exact_allowlist',
        (select count(*) from actual_table_acl) = (select count(*) from expected_table_acl)
          and not exists (
              select 1
              from actual_table_acl a
              left join expected_table_acl e
                on e.signature = a.signature
               and e.grantee_name = a.grantee_name
               and e.privilege_type = a.privilege_type
               and e.is_grantable = a.is_grantable
              where e.signature is null
          )
          and not exists (
              select 1
              from expected_table_acl e
              left join actual_table_acl a
                on a.signature = e.signature
               and a.grantee_name = e.grantee_name
               and a.privilege_type = e.privilege_type
               and a.is_grantable = e.is_grantable
              where a.signature is null
          ),
        'owner postgres only; eight PostgreSQL 17 table privileges per target table',
        coalesce((
            select pg_catalog.string_agg(
                a.signature || ':' || a.grantee_name || ':' || a.privilege_type
                || ':grantable=' || a.is_grantable::text,
                ',' order by a.signature, a.grantee_name, a.privilege_type
            )
            from actual_table_acl a
        ), '')
    union all
    select
        'target_column_acl_inventory_zero',
        count(*) = 0,
        '0 explicit target-column ACL entries',
        coalesce(pg_catalog.string_agg(
            a.signature || ':' || a.grantee_name || ':' || a.privilege_type
            || ':grantable=' || a.is_grantable::text,
            ',' order by a.signature, a.grantee_name, a.privilege_type
        ), '')
    from actual_target_column_acl a
    union all
    select
        'target_tables_rls_forced',
        count(*) = 4,
        '4',
        count(*)::text
    from expected_tables e
    join pg_catalog.pg_namespace n on n.nspname = e.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid and c.relname = e.relation_name and c.relkind = 'r'
    where c.relrowsecurity and c.relforcerowsecurity
    union all
    select
        'target_tables_owned_by_postgres',
        count(*) = 4,
        '4',
        count(*)::text
    from expected_tables e
    join pg_catalog.pg_namespace n on n.nspname = e.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid and c.relname = e.relation_name and c.relkind = 'r'
    join pg_catalog.pg_roles owner on owner.oid = c.relowner and owner.rolname = 'postgres'
    union all
    select
        'target_table_triggers_exact',
        count(*) = 8 and count(*) filter (where e.trigger_name is not null) = 8,
        'two exact enabled triggers per target table: row-level BEFORE UPDATE OR DELETE; statement-level BEFORE TRUNCATE; both call private.deny_managed_telegram_inspect_ledger_mutation()',
        coalesce(pg_catalog.string_agg(
            pg_catalog.format('%I.%I:%I', a.schema_name, a.relation_name, a.trigger_name)
            || ':tgtype=' || a.tgtype::text
            || ':enabled=' || a.tgenabled::text
            || ':nargs=' || a.tgnargs::text
            || ':args_bytes=' || a.tgargs_byte_length::text
            || ':qual_is_null=' || (a.tgqual is null)::text
            || ':attr_is_empty=' || (pg_catalog.cardinality(a.tgattr) = 0)::text
            || ':function=' || pg_catalog.format(
                '%I.%I(%s)', a.function_schema, a.function_name,
                a.function_argument_types
            ),
            ',' order by a.schema_name, a.relation_name, a.trigger_name
        ), '')
    from actual_target_triggers a
    left join expected_triggers e
      on e.trigger_name = a.trigger_name
     and e.tgtype = a.tgtype
     and e.tgenabled = a.tgenabled
     and e.function_schema = a.function_schema
     and e.function_name = a.function_name
     and e.function_argument_types = a.function_argument_types
     and e.tgnargs = a.tgnargs
     and e.tgargs_byte_length = a.tgargs_byte_length
     and e.tgqual_is_null = (a.tgqual is null)
     and e.tgattr_is_empty = (pg_catalog.cardinality(a.tgattr) = 0)
    union all
    select
        'target_functions_exact',
        count(*) = 8,
        '8',
        count(*)::text
    from expected_functions e
    join actual_target_functions a
      on a.schema_name = e.schema_name
     and a.function_name = e.function_name
     and a.argument_types = e.argument_types
    union all
    select
        'target_functions_no_unexpected_overload',
        count(*) = 8,
        '8',
        count(*)::text
    from actual_target_functions
    union all
    select
        'target_function_acls_explicit',
        count(*) = 8,
        '8',
        count(*)::text
    from actual_target_functions a
    join pg_catalog.pg_proc p on p.oid = a.oid
    where p.proacl is not null
    union all
    select
        'target_function_acl_exact_allowlist',
        (select count(*) from actual_function_acl) = (select count(*) from expected_function_acl)
          and not exists (
              select 1
              from actual_function_acl a
              left join expected_function_acl e
                on e.signature = a.signature
               and e.grantee_name = a.grantee_name
               and e.privilege_type = a.privilege_type
               and e.is_grantable = a.is_grantable
              where e.signature is null
          )
          and not exists (
              select 1
              from expected_function_acl e
              left join actual_function_acl a
                on a.signature = e.signature
               and a.grantee_name = e.grantee_name
               and a.privilege_type = e.privilege_type
               and a.is_grantable = e.is_grantable
              where a.signature is null
          ),
        'postgres execute on eight; managed role execute on public three only',
        coalesce((
            select pg_catalog.string_agg(
                a.signature || ':' || a.grantee_name || ':' || a.privilege_type
                || ':grantable=' || a.is_grantable::text,
                ',' order by a.signature, a.grantee_name, a.privilege_type
            )
            from actual_function_acl a
        ), '')
    union all
    select
        'target_function_security_and_config_exact',
        count(*) = 8,
        'eight exact prosecdef/proconfig contracts',
        coalesce(pg_catalog.string_agg(
            pg_catalog.format('%I.%I(%s)', a.schema_name, a.function_name, a.argument_types)
            || ':security_definer=' || a.prosecdef::text
            || ':proconfig=' || coalesce(pg_catalog.array_to_string(a.proconfig, '|'), ''),
            ',' order by a.schema_name, a.function_name, a.argument_types
        ), '')
    from expected_functions e
    join actual_target_functions a
      on a.schema_name = e.schema_name
     and a.function_name = e.function_name
     and a.argument_types = e.argument_types
     and a.prosecdef = e.security_definer
     and coalesce(a.proconfig, array[]::text[]) = e.expected_proconfig
    union all
    select
        'target_functions_owned_by_postgres',
        count(*) = 8,
        '8',
        count(*)::text
    from actual_target_functions a
    join pg_catalog.pg_roles owner on owner.oid = a.proowner and owner.rolname = 'postgres'
    union all
    select
        'public_entrypoints_security_definer',
        count(*) = 3,
        '3',
        count(*)::text
    from expected_functions e
    join actual_target_functions a
      on a.schema_name = e.schema_name
     and a.function_name = e.function_name
     and a.argument_types = e.argument_types
    where e.public_entrypoint and a.prosecdef
    union all
    select
        'effective_exposed_functions_exactly_three',
        count(*) = 3
          and count(*) filter (where e.signature is not null) = 3,
        pg_catalog.array_to_string(array(select signature from expected_public_signatures order by signature), ','),
        coalesce(pg_catalog.string_agg(a.signature, ',' order by a.signature), '')
    from effective_exposed_functions a
    left join expected_public_signatures e on e.signature = a.signature
    union all
    select
        'effective_private_functions_zero',
        count(*) = 0,
        '0',
        count(*)::text
    from target_role r
    join pg_catalog.pg_proc p
      on pg_catalog.has_function_privilege(r.oid, p.oid, 'EXECUTE')
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'private'
      and pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE')
    union all
    select
        'relation_privileges_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(object_name, ',' order by object_name), '')
    from unexpected_relation_privileges
    union all
    select
        'column_privileges_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(object_name, ',' order by object_name), '')
    from unexpected_column_privileges
    union all
    select
        'sequence_privileges_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(object_name, ',' order by object_name), '')
    from unexpected_sequence_privileges
    union all
    select
        'owned_objects_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(object_name, ',' order by object_name), '')
    from owned_objects
    union all
    select
        'default_acl_grants_to_target_role_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(object_name, ',' order by object_name), '')
    from unexpected_default_acl_privileges
    union all
    select
        'public_execute_on_target_functions_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(signature, ',' order by signature), '')
    from unexpected_public_target_execute
    union all
    select
        'ordinary_roles_execute_on_target_functions_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(signature, ',' order by signature), '')
    from unexpected_named_principal_execute
)
select
    'managed-inspector-production-postflight@1'::text as pack,
    check_id,
    passed and pg_catalog.octet_length(observed) <= 4096 as passed,
    expected,
    case
        when pg_catalog.octet_length(observed) <= 4096 then observed
        else '[omitted:observed_exceeds_4096_bytes]'
    end as observed,
    pg_catalog.octet_length(observed) as observed_byte_length,
    pg_catalog.encode(
        extensions.digest(pg_catalog.convert_to(observed, 'UTF8'), 'sha256'), 'hex'
    ) as observed_sha256,
    false as generic_db_push_allowed,
    true as full_history_not_reconciled,
    false as exact_migration_bytes_proven,
    true as custom_apply_receipt_required
from checks
order by check_id;
