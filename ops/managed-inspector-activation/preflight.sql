-- managed-inspector-production-preflight@1
-- One catalog-only query. Every returned row must have passed=true.
with recursive
expected_migrations(version) as (
    values ('20260831180000'::text), ('20260901120000'::text)
),
expected_roles(role_name) as (
    values
        ('postgres'::text),
        ('authenticator'::text),
        ('anon'::text),
        ('authenticated'::text),
        ('service_role'::text),
        ('coineasy_telegram_resolution'::text)
),
expected_auth_columns(schema_name, table_name, column_name, allowed_udt_names) as (
    values
        ('auth'::text, 'users'::text, 'id'::text, array['pg_catalog.uuid']::text[]),
        ('auth', 'users', 'role', array['pg_catalog.text', 'pg_catalog.varchar']),
        ('auth', 'users', 'deleted_at', array['pg_catalog.timestamptz']),
        ('auth', 'users', 'is_anonymous', array['pg_catalog.bool']),
        ('auth', 'users', 'banned_until', array['pg_catalog.timestamptz']),
        ('auth', 'users', 'encrypted_password', array['pg_catalog.text', 'pg_catalog.varchar']),
        ('auth', 'users', 'recovery_sent_at', array['pg_catalog.timestamptz']),
        ('auth', 'sessions', 'id', array['pg_catalog.uuid']),
        ('auth', 'sessions', 'user_id', array['pg_catalog.uuid']),
        ('auth', 'sessions', 'aal', array['auth.aal_level', 'pg_catalog.text', 'pg_catalog.varchar']),
        ('auth', 'sessions', 'not_after', array['pg_catalog.timestamptz']),
        ('auth', 'sessions', 'factor_id', array['pg_catalog.uuid']),
        ('auth', 'mfa_factors', 'id', array['pg_catalog.uuid']),
        ('auth', 'mfa_factors', 'user_id', array['pg_catalog.uuid']),
        ('auth', 'mfa_factors', 'factor_type', array['auth.factor_type', 'pg_catalog.text', 'pg_catalog.varchar']),
        ('auth', 'mfa_factors', 'status', array['auth.factor_status', 'pg_catalog.text', 'pg_catalog.varchar']),
        ('auth', 'mfa_factors', 'created_at', array['pg_catalog.timestamptz']),
        ('auth', 'mfa_amr_claims', 'session_id', array['pg_catalog.uuid']),
        ('auth', 'mfa_amr_claims', 'authentication_method', array['pg_catalog.text', 'pg_catalog.varchar']),
        ('auth', 'mfa_amr_claims', 'updated_at', array['pg_catalog.timestamptz'])
),
actual_auth_columns(schema_name, table_name, column_name, qualified_udt_name) as (
    select
        n.nspname,
        c.relname,
        a.attname,
        tn.nspname || '.' || t.typname
    from pg_catalog.pg_attribute a
    join pg_catalog.pg_class c on c.oid = a.attrelid
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    join pg_catalog.pg_type t on t.oid = a.atttypid
    join pg_catalog.pg_namespace tn on tn.oid = t.typnamespace
    where n.nspname = 'auth'
      and c.relkind in ('r', 'p')
      and a.attnum > 0
      and not a.attisdropped
),
expected_base_relations(signature) as (
    values
        ('public.workspaces'::text),
        ('public.workspace_members'),
        ('public.jobs'),
        ('public.publications'),
        ('private.exact_telegram_delivery_unknown_approvals'),
        ('private.exact_telegram_delivery_unknown_resolutions')
),
expected_base_functions(signature) as (
    values
        ('auth.uid()'::text),
        ('extensions.digest(bytea,text)'),
        ('private.exact_telegram_delivery_resolution_subject(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz,timestamptz,text,jsonb)')
),
target_functions(schema_name, function_name, argument_types) as (
    values
        ('private'::text, 'deny_managed_telegram_inspect_ledger_mutation'::text, ''::text),
        ('private', 'managed_telegram_inspect_hash', 'jsonb'),
        ('private', 'require_managed_telegram_inspect_identity', 'uuid, text'),
        ('private', 'validate_managed_telegram_inspect_request', 'jsonb, timestamp with time zone'),
        ('private', 'managed_telegram_inspect_fresh_subject', 'jsonb'),
        ('public', 'managed_telegram_inspect_context', 'uuid, text'),
        ('public', 'register_managed_telegram_inspect_consent', 'uuid, jsonb, text'),
        ('public', 'inspect_managed_telegram_delivery_unknown', 'uuid')
),
inspected_schemas(oid, schema_name) as (
    select n.oid, n.nspname
    from pg_catalog.pg_namespace n
    where n.nspname <> 'information_schema'
      and n.nspname !~ '^pg_'
),
public_usable_schemas(oid, schema_name) as (
    select n.oid, n.schema_name
    from inspected_schemas n
    join pg_catalog.pg_namespace namespace on namespace.oid = n.oid
    where n.schema_name = 'public'
       or exists (
           select 1
           from pg_catalog.aclexplode(
               coalesce(
                   namespace.nspacl,
                   pg_catalog.acldefault('n', namespace.nspowner)
               )
           ) acl
           where acl.grantee = 0 and acl.privilege_type = 'USAGE'
       )
),
public_existing_public_function_execute(signature) as (
    select pg_catalog.format(
        '%I.%I(%s)', n.nspname, p.proname,
        pg_catalog.oidvectortypes(p.proargtypes)
    )
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    cross join lateral pg_catalog.aclexplode(
        coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
    ) acl
    where n.nspname = 'public'
      and acl.grantee = 0
      and acl.privilege_type = 'EXECUTE'
),
public_existing_private_function_execute(signature) as (
    select pg_catalog.format(
        '%I.%I(%s)', n.nspname, p.proname,
        pg_catalog.oidvectortypes(p.proargtypes)
    )
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    cross join lateral pg_catalog.aclexplode(
        coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
    ) acl
    where n.nspname = 'private'
      and acl.grantee = 0
      and acl.privilege_type = 'EXECUTE'
),
public_relation_privileges(object_name) as (
    select pg_catalog.format(
        '%I.%I:%s', n.schema_name, c.relname, acl.privilege_type
    )
    from pg_catalog.pg_class c
    join public_usable_schemas n on n.oid = c.relnamespace
    cross join lateral pg_catalog.aclexplode(
        coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))
    ) acl
    where c.relkind in ('r', 'p', 'v', 'm', 'f')
      and acl.grantee = 0
      and acl.privilege_type in (
          'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
          'REFERENCES', 'TRIGGER', 'MAINTAIN'
      )
),
public_column_privileges(object_name) as (
    select pg_catalog.format(
        '%I.%I.%I:%s', n.schema_name, c.relname, a.attname,
        acl.privilege_type
    )
    from pg_catalog.pg_class c
    join public_usable_schemas n on n.oid = c.relnamespace
    join pg_catalog.pg_attribute a
      on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
    cross join lateral pg_catalog.aclexplode(a.attacl) acl
    where c.relkind in ('r', 'p', 'v', 'm', 'f')
      and acl.grantee = 0
      and acl.privilege_type in ('SELECT', 'INSERT', 'UPDATE', 'REFERENCES')
),
public_sequence_privileges(object_name) as (
    select pg_catalog.format(
        '%I.%I:%s', n.schema_name, c.relname, acl.privilege_type
    )
    from pg_catalog.pg_class c
    join public_usable_schemas n on n.oid = c.relnamespace
    cross join lateral pg_catalog.aclexplode(
        coalesce(c.relacl, pg_catalog.acldefault('S', c.relowner))
    ) acl
    where c.relkind = 'S'
      and acl.grantee = 0
      and acl.privilege_type in ('USAGE', 'SELECT', 'UPDATE')
),
unexpected_public_schema_privileges(object_name) as (
    select n.schema_name || ':' || acl.privilege_type
    from inspected_schemas n
    join pg_catalog.pg_namespace namespace on namespace.oid = n.oid
    cross join lateral pg_catalog.aclexplode(
        coalesce(
            namespace.nspacl,
            pg_catalog.acldefault('n', namespace.nspowner)
        )
    ) acl
    where acl.grantee = 0
      and (
          acl.privilege_type = 'CREATE'
          or (
              n.schema_name <> 'public'
              and acl.privilege_type = 'USAGE'
          )
      )
),
authenticator_downstream(member, membership_path) as (
    select m.member, array[m.roleid, m.member]::oid[]
    from pg_catalog.pg_auth_members m
    join pg_catalog.pg_roles r on r.oid = m.roleid
    where r.rolname = 'authenticator'
    union all
    select m.member, d.membership_path || m.member
    from authenticator_downstream d
    join pg_catalog.pg_auth_members m on m.roleid = d.member
    where not m.member = any(d.membership_path)
),
expected_authenticator_admin_members(
    role_name, admin_option, inherit_option, set_option,
    rolcreaterole, rolbypassrls, grantor_name, path_cardinality
) as (
    values
        ('postgres'::text, true, true, true, true, true, 'supabase_admin'::text, 2),
        ('supabase_storage_admin'::text, false, false, true, true, false, 'supabase_admin', 2)
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
        'server_version_pg17',
        pg_catalog.current_setting('server_version_num')::integer between 170000 and 179999,
        '170000..179999',
        pg_catalog.current_setting('server_version_num')
    union all
    select
        'required_schemas_present',
        count(*) = 5,
        'auth,extensions,graphql_public,private,public',
        coalesce(pg_catalog.string_agg(n.nspname, ',' order by n.nspname), '')
    from pg_catalog.pg_namespace n
    where n.nspname in ('auth', 'extensions', 'graphql_public', 'private', 'public')
    union all
    select
        'required_roles_present',
        count(*) = (select count(*) from expected_roles),
        pg_catalog.array_to_string(array(select role_name from expected_roles order by role_name), ','),
        coalesce(pg_catalog.string_agg(r.rolname, ',' order by r.rolname), '')
    from pg_catalog.pg_roles r
    where r.rolname in (select role_name from expected_roles)
    union all
    select
        'postgres_definer_prerequisite',
        count(*) = 1,
        'postgres superuser-or-bypassrls',
        coalesce(pg_catalog.string_agg(r.rolname || ':' || (r.rolsuper or r.rolbypassrls)::text, ','), '')
    from pg_catalog.pg_roles r
    where r.rolname = 'postgres' and (r.rolsuper or r.rolbypassrls)
    union all
    select
        'required_auth_columns_present',
        count(*) = (select count(*) from expected_auth_columns),
        (select count(*)::text from expected_auth_columns),
        count(*)::text
    from actual_auth_columns c
    join expected_auth_columns e
      on e.schema_name = c.schema_name
     and e.table_name = c.table_name
     and e.column_name = c.column_name
    union all
    select
        'required_auth_columns_missing',
        count(*) = 0,
        'none',
        coalesce(pg_catalog.string_agg(
            e.schema_name || '.' || e.table_name || '.' || e.column_name,
            ',' order by e.schema_name, e.table_name, e.column_name
        ), '')
    from expected_auth_columns e
    left join actual_auth_columns c
      on e.schema_name = c.schema_name
     and e.table_name = c.table_name
     and e.column_name = c.column_name
    where c.column_name is null
    union all
    select
        'required_auth_column_types_allowed',
        count(*) = (select count(*) from expected_auth_columns),
        (select count(*)::text from expected_auth_columns),
        count(*)::text
    from expected_auth_columns e
    join actual_auth_columns c
      on e.schema_name = c.schema_name
     and e.table_name = c.table_name
     and e.column_name = c.column_name
     and c.qualified_udt_name = any(e.allowed_udt_names)
    union all
    select
        'required_auth_column_type_mismatches',
        count(*) = 0,
        'none',
        coalesce(pg_catalog.string_agg(
            e.schema_name || '.' || e.table_name || '.' || e.column_name
            || ':actual=' || c.qualified_udt_name
            || ':allowed=' || pg_catalog.array_to_string(e.allowed_udt_names, '|'),
            ',' order by e.schema_name, e.table_name, e.column_name
        ), '')
    from expected_auth_columns e
    join actual_auth_columns c
      on e.schema_name = c.schema_name
     and e.table_name = c.table_name
     and e.column_name = c.column_name
    where not (c.qualified_udt_name = any(e.allowed_udt_names))
    union all
    select
        'required_base_relations_present',
        count(*) = (select count(*) from expected_base_relations),
        (select count(*)::text from expected_base_relations),
        count(*)::text
    from expected_base_relations e
    where pg_catalog.to_regclass(e.signature) is not null
    union all
    select
        'required_base_functions_present',
        count(*) = (select count(*) from expected_base_functions),
        (select count(*)::text from expected_base_functions),
        count(*)::text
    from expected_base_functions e
    where pg_catalog.to_regprocedure(e.signature) is not null
    union all
    select
        'target_migration_rows_absent',
        count(*) = 0,
        '0',
        count(*)::text
    from supabase_migrations.schema_migrations m
    where m.version in (select version from expected_migrations)
    union all
    select
        'target_role_absent',
        count(*) = 0,
        '0',
        count(*)::text
    from pg_catalog.pg_roles r
    where r.rolname = 'coineasy_managed_inspector'
    union all
    select
        'authenticator_platform_admin_descendants_exact',
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
          and (select count(*) from authenticator_downstream d)
              = (select count(*) from expected_authenticator_admin_members)
          and not exists (
              select 1
              from authenticator_downstream d
              left join pg_catalog.pg_roles member on member.oid = d.member
              left join expected_authenticator_admin_members e
                on e.role_name = member.rolname
               and e.path_cardinality = pg_catalog.cardinality(d.membership_path)
              where e.role_name is null
          )
          and not exists (
              select 1
              from expected_authenticator_admin_members e
              where not exists (
                  select 1
                  from authenticator_downstream d
                  join pg_catalog.pg_roles member on member.oid = d.member
                  where member.rolname = e.role_name
                    and pg_catalog.cardinality(d.membership_path) = e.path_cardinality
              )
          ),
        'postgres(admin=true,inherit=true,set=true);supabase_storage_admin(admin=false,inherit=false,set=true); no other descendants',
        coalesce((
            select pg_catalog.string_agg(
                pg_catalog.concat_ws(',',
                    a.role_name,
                    'admin=' || a.admin_option::text,
                    'inherit=' || a.inherit_option::text,
                    'set=' || a.set_option::text,
                    'createrole=' || a.rolcreaterole::text,
                    'bypassrls=' || a.rolbypassrls::text,
                    'grantor=' || a.grantor_name
                ), ';' order by a.role_name
            )
            from actual_authenticator_admin_members a
        ), '')
    union all
    select
        'public_execute_on_existing_public_functions_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(signature, ',' order by signature), '')
    from public_existing_public_function_execute
    union all
    select
        'public_execute_on_existing_private_functions_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(signature, ',' order by signature), '')
    from public_existing_private_function_execute
    union all
    select
        'public_relation_privileges_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(object_name, ',' order by object_name), '')
    from public_relation_privileges
    union all
    select
        'public_column_privileges_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(object_name, ',' order by object_name), '')
    from public_column_privileges
    union all
    select
        'public_sequence_privileges_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(object_name, ',' order by object_name), '')
    from public_sequence_privileges
    union all
    select
        'public_schema_privileges_compatible',
        count(*) = 0,
        'no PUBLIC CREATE; no PUBLIC USAGE outside public',
        coalesce(pg_catalog.string_agg(object_name, ',' order by object_name), '')
    from unexpected_public_schema_privileges
    union all
    select
        'target_relations_absent',
        count(*) = 0,
        '0',
        count(*)::text
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('private', 'public')
      and c.relname like 'managed_telegram_inspect_%'
    union all
    select
        'target_functions_absent',
        count(*) = 0,
        '0',
        count(*)::text
    from target_functions e
    join pg_catalog.pg_namespace n on n.nspname = e.schema_name
    join pg_catalog.pg_proc p
      on p.pronamespace = n.oid
     and p.proname = e.function_name
     and pg_catalog.oidvectortypes(p.proargtypes) = e.argument_types
    union all
    select
        'target_function_name_or_overload_collisions_absent',
        count(*) = 0,
        '0',
        count(*)::text
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('private', 'public')
      and (
          p.proname like '%managed_telegram_inspect%'
          or p.proname = 'inspect_managed_telegram_delivery_unknown'
      )
    union all
    select
        'target_triggers_absent',
        count(*) = 0,
        '0',
        count(*)::text
    from pg_catalog.pg_trigger t
    where not t.tgisinternal
      and t.tgname in ('managed_inspect_immutable', 'managed_inspect_no_truncate')
)
select
    'managed-inspector-production-preflight@1'::text as pack,
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
