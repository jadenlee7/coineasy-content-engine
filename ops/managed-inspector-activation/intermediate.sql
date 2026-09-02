-- managed-inspector-production-intermediate@1
-- One catalog-only query. Run after the first canonical migration and its
-- history registration, before the role-boundary migration. Every row must
-- have passed=true.
with
expected_tables(schema_name, relation_name) as (
    values
        ('private'::text, 'managed_telegram_inspect_releases'::text),
        ('private', 'managed_telegram_inspect_allowlist'),
        ('private', 'managed_telegram_inspect_consents'),
        ('private', 'managed_telegram_inspect_revocations')
),
expected_functions(
    schema_name, function_name, argument_types,
    public_entrypoint, expected_prosecdef, expected_proconfig
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
            'uuid, jsonb, text', true, true,
            array['search_path=""', 'TimeZone=UTC']),
        ('public', 'inspect_managed_telegram_delivery_unknown',
            'uuid', true, true, array['search_path=""', 'TimeZone=UTC'])
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
ordinary_roles(role_name) as (
    values
        ('anon'::text), ('authenticated'), ('service_role'),
        ('coineasy_telegram_resolution')
),
actual_functions as (
    select
        n.nspname as schema_name,
        p.proname as function_name,
        pg_catalog.oidvectortypes(p.proargtypes) as argument_types,
        p.oid, p.proowner, p.proacl, p.prosecdef, p.proconfig
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('private', 'public')
      and (
          p.proname like '%managed_telegram_inspect%'
          or p.proname = 'inspect_managed_telegram_delivery_unknown'
      )
),
actual_relations as (
    select n.nspname as schema_name, c.relname as relation_name, c.relkind
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('private', 'public')
      and c.relname like 'managed_telegram_inspect_%'
      and c.relkind in ('r', 'p', 'v', 'm', 'S', 'f')
),
table_privileges(privilege_type) as (
    values
        ('DELETE'::text), ('INSERT'), ('MAINTAIN'), ('REFERENCES'),
        ('SELECT'), ('TRIGGER'), ('TRUNCATE'), ('UPDATE')
),
expected_table_acl(signature, grantee_name, privilege_type, is_grantable) as (
    select
        pg_catalog.format('%I.%I', t.schema_name, t.relation_name),
        'postgres'::text, p.privilege_type, false
    from expected_tables t
    cross join table_privileges p
),
actual_table_acl(signature, grantee_name, privilege_type, is_grantable) as (
    select
        pg_catalog.format('%I.%I', t.schema_name, t.relation_name),
        case
            when acl.grantee = 0 then 'PUBLIC'
            else coalesce(grantee.rolname, 'oid:' || acl.grantee::text)
        end,
        acl.privilege_type,
        acl.is_grantable
    from expected_tables t
    join pg_catalog.pg_namespace n on n.nspname = t.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid
     and c.relname = t.relation_name
     and c.relkind = 'r'
    cross join lateral pg_catalog.aclexplode(
        coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))
    ) acl
    left join pg_catalog.pg_roles grantee on grantee.oid = acl.grantee
),
actual_column_acl(signature, grantee_name, privilege_type, is_grantable) as (
    select
        pg_catalog.format('%I.%I.%I', t.schema_name, t.relation_name, a.attname),
        case
            when acl.grantee = 0 then 'PUBLIC'
            else coalesce(grantee.rolname, 'oid:' || acl.grantee::text)
        end,
        acl.privilege_type,
        acl.is_grantable
    from expected_tables t
    join pg_catalog.pg_namespace n on n.nspname = t.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid
     and c.relname = t.relation_name
     and c.relkind = 'r'
    join pg_catalog.pg_attribute a
      on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
    cross join lateral pg_catalog.aclexplode(a.attacl) acl
    left join pg_catalog.pg_roles grantee on grantee.oid = acl.grantee
),
expected_function_acl(signature, grantee_name, privilege_type, is_grantable) as (
    select
        pg_catalog.format('%I.%I(%s)', f.schema_name, f.function_name, f.argument_types),
        'postgres'::text, 'EXECUTE'::text, false
    from expected_functions f
),
actual_function_acl(signature, grantee_name, privilege_type, is_grantable) as (
    select
        pg_catalog.format('%I.%I(%s)', f.schema_name, f.function_name, f.argument_types),
        case
            when acl.grantee = 0 then 'PUBLIC'
            else coalesce(grantee.rolname, 'oid:' || acl.grantee::text)
        end,
        acl.privilege_type,
        acl.is_grantable
    from actual_functions f
    cross join lateral pg_catalog.aclexplode(
        coalesce(f.proacl, pg_catalog.acldefault('f', f.proowner))
    ) acl
    left join pg_catalog.pg_roles grantee on grantee.oid = acl.grantee
),
actual_triggers as (
    select
        t.schema_name,
        t.relation_name,
        trigger_row.tgname as trigger_name,
        trigger_row.tgtype,
        trigger_row.tgenabled,
        trigger_row.tgnargs,
        pg_catalog.octet_length(trigger_row.tgargs) as tgargs_byte_length,
        trigger_row.tgqual,
        trigger_row.tgattr,
        fnn.nspname as function_schema,
        fn.proname as function_name,
        pg_catalog.oidvectortypes(fn.proargtypes) as function_argument_types
    from expected_tables t
    join pg_catalog.pg_namespace n on n.nspname = t.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid
     and c.relname = t.relation_name
     and c.relkind = 'r'
    join pg_catalog.pg_trigger trigger_row
      on trigger_row.tgrelid = c.oid and not trigger_row.tgisinternal
    join pg_catalog.pg_proc fn on fn.oid = trigger_row.tgfoid
    join pg_catalog.pg_namespace fnn on fnn.oid = fn.pronamespace
),
unexpected_public_entry_execute as (
    select pg_catalog.format(
        '%I.%I(%s)', f.schema_name, f.function_name, f.argument_types
    ) as signature
    from actual_functions f
    cross join lateral pg_catalog.aclexplode(
        coalesce(f.proacl, pg_catalog.acldefault('f', f.proowner))
    ) acl
    join expected_functions e
      on e.schema_name = f.schema_name
     and e.function_name = f.function_name
     and e.argument_types = f.argument_types
     and e.public_entrypoint
    where acl.grantee = 0 and acl.privilege_type = 'EXECUTE'
),
unexpected_ordinary_entry_execute as (
    select pg_catalog.format(
        '%s:%I.%I(%s)', role_row.rolname,
        f.schema_name, f.function_name, f.argument_types
    ) as signature
    from actual_functions f
    join expected_functions e
      on e.schema_name = f.schema_name
     and e.function_name = f.function_name
     and e.argument_types = f.argument_types
     and e.public_entrypoint
    cross join ordinary_roles expected_role
    join pg_catalog.pg_roles role_row on role_row.rolname = expected_role.role_name
    where pg_catalog.has_function_privilege(role_row.oid, f.oid, 'EXECUTE')
),
target_row_counts(relation_name, row_count) as (
    select 'managed_telegram_inspect_releases'::text, count(*)::bigint
    from private.managed_telegram_inspect_releases
    union all
    select 'managed_telegram_inspect_allowlist', count(*)::bigint
    from private.managed_telegram_inspect_allowlist
    union all
    select 'managed_telegram_inspect_consents', count(*)::bigint
    from private.managed_telegram_inspect_consents
    union all
    select 'managed_telegram_inspect_revocations', count(*)::bigint
    from private.managed_telegram_inspect_revocations
),
checks(check_id, passed, expected, observed) as (
    select
        'execution_role_read_only',
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
        'execution_role_can_observe_forced_rls_rows',
        count(*) = 1,
        'supabase_read_only_user:bypassrls=true,pg_read_all_data=true',
        coalesce(pg_catalog.string_agg(
            r.rolname || ':bypassrls=' || r.rolbypassrls::text
            || ',pg_read_all_data='
            || pg_catalog.pg_has_role(r.oid, reader.oid, 'USAGE')::text,
            ',' order by r.rolname
        ), '')
    from pg_catalog.pg_roles r
    join pg_catalog.pg_roles reader on reader.rolname = 'pg_read_all_data'
    where r.rolname = 'supabase_read_only_user'
      and r.rolbypassrls
      and pg_catalog.pg_has_role(r.oid, reader.oid, 'USAGE')
    union all
    select
        'ordinary_roles_exist_exact',
        count(*) = 4,
        'anon,authenticated,coineasy_telegram_resolution,service_role',
        coalesce(pg_catalog.string_agg(
            role_row.rolname, ',' order by role_row.rolname
        ), '')
    from ordinary_roles expected_role
    join pg_catalog.pg_roles role_row on role_row.rolname = expected_role.role_name
    union all
    select
        'first_migration_history_exact',
        count(*) = 1
          and count(*) filter (
              where name = 'managed_auth_telegram_inspect'
                and pg_catalog.cardinality(statements) = 1
                and pg_catalog.encode(extensions.digest(
                    pg_catalog.convert_to(statements[1], 'UTF8'), 'sha256'
                ), 'hex') = '61bf61ee4be6993c88d471b0d9b3e3fa2bf1063ba87d1a901cceff2fc953ab46'
          ) = 1,
        '20260831180000:managed_auth_telegram_inspect:statements=1:sha256=61bf61ee4be6993c88d471b0d9b3e3fa2bf1063ba87d1a901cceff2fc953ab46',
        coalesce(pg_catalog.string_agg(
            version || ':' || coalesce(name, '')
            || ':statements=' || coalesce(pg_catalog.cardinality(statements)::text, 'null')
            || ':sha256=' || coalesce(pg_catalog.encode(extensions.digest(
                pg_catalog.convert_to(statements[1], 'UTF8'), 'sha256'
            ), 'hex'), 'null'),
            ',' order by version
        ), '')
    from supabase_migrations.schema_migrations
    where version = '20260831180000'
    union all
    select
        'second_migration_history_absent',
        count(*) = 0,
        '0',
        count(*)::text
    from supabase_migrations.schema_migrations
    where version = '20260901120000'
    union all
    select
        'target_role_absent',
        count(*) = 0,
        '0',
        count(*)::text
    from pg_catalog.pg_roles
    where rolname = 'coineasy_managed_inspector'
    union all
    select
        'target_tables_exact',
        count(*) = 4,
        '4',
        count(*)::text
    from expected_tables t
    join pg_catalog.pg_namespace n on n.nspname = t.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid
     and c.relname = t.relation_name
     and c.relkind = 'r'
    union all
    select
        'target_relations_no_unexpected_object',
        count(*) = 4
          and count(*) filter (where t.relation_name is not null) = 4,
        '4 expected tables and no target-prefixed extra relation',
        count(*)::text
    from actual_relations a
    left join expected_tables t
      on t.schema_name = a.schema_name
     and t.relation_name = a.relation_name
     and a.relkind = 'r'
    union all
    select
        'target_tables_owned_by_postgres',
        count(*) = 4,
        '4',
        count(*)::text
    from expected_tables t
    join pg_catalog.pg_namespace n on n.nspname = t.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid
     and c.relname = t.relation_name
     and c.relkind = 'r'
    join pg_catalog.pg_roles owner
      on owner.oid = c.relowner and owner.rolname = 'postgres'
    union all
    select
        'target_tables_rls_forced',
        count(*) = 4,
        '4',
        count(*)::text
    from expected_tables t
    join pg_catalog.pg_namespace n on n.nspname = t.schema_name
    join pg_catalog.pg_class c
      on c.relnamespace = n.oid
     and c.relname = t.relation_name
     and c.relkind = 'r'
    where c.relrowsecurity and c.relforcerowsecurity
    union all
    select
        'target_table_acl_exact_owner_only',
        (select count(*) from actual_table_acl)
            = (select count(*) from expected_table_acl)
          and not exists (
              select * from actual_table_acl
              except all
              select * from expected_table_acl
          )
          and not exists (
              select * from expected_table_acl
              except all
              select * from actual_table_acl
          ),
        'postgres owner default only',
        coalesce((
            select pg_catalog.string_agg(
                signature || ':' || grantee_name || ':' || privilege_type
                || ':grantable=' || is_grantable::text,
                ',' order by signature, grantee_name, privilege_type
            )
            from actual_table_acl
        ), '')
    union all
    select
        'target_column_acl_inventory_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(
            signature || ':' || grantee_name || ':' || privilege_type,
            ',' order by signature, grantee_name, privilege_type
        ), '')
    from actual_column_acl
    union all
    select
        'target_functions_exact',
        count(*) = 8,
        '8',
        count(*)::text
    from expected_functions e
    join actual_functions a
      on a.schema_name = e.schema_name
     and a.function_name = e.function_name
     and a.argument_types = e.argument_types
    union all
    select
        'target_functions_no_unexpected_overload',
        count(*) = 8,
        '8',
        count(*)::text
    from actual_functions
    union all
    select
        'target_functions_owned_by_postgres',
        count(*) = 8,
        '8',
        count(*)::text
    from actual_functions a
    join pg_catalog.pg_roles owner
      on owner.oid = a.proowner and owner.rolname = 'postgres'
    union all
    select
        'target_function_acl_exact_owner_only',
        (select count(*) from actual_function_acl)
            = (select count(*) from expected_function_acl)
          and not exists (
              select * from actual_function_acl
              except all
              select * from expected_function_acl
          )
          and not exists (
              select * from expected_function_acl
              except all
              select * from actual_function_acl
          ),
        'postgres execute on all eight; no other grantee',
        coalesce((
            select pg_catalog.string_agg(
                signature || ':' || grantee_name || ':' || privilege_type
                || ':grantable=' || is_grantable::text,
                ',' order by signature, grantee_name, privilege_type
            )
            from actual_function_acl
        ), '')
    union all
    select
        'target_function_security_and_config_exact',
        count(*) = 8,
        '8 exact prosecdef/proconfig contracts',
        coalesce(pg_catalog.string_agg(
            pg_catalog.format('%I.%I(%s)', a.schema_name, a.function_name, a.argument_types)
            || ':prosecdef=' || a.prosecdef::text
            || ':proconfig=' || coalesce(pg_catalog.array_to_string(a.proconfig, '|'), ''),
            ',' order by a.schema_name, a.function_name, a.argument_types
        ), '')
    from expected_functions e
    join actual_functions a
      on a.schema_name = e.schema_name
     and a.function_name = e.function_name
     and a.argument_types = e.argument_types
     and a.prosecdef = e.expected_prosecdef
     and coalesce(a.proconfig, array[]::text[]) = e.expected_proconfig
    union all
    select
        'target_table_triggers_exact',
        count(*) = 8
          and count(*) filter (where expected_trigger.trigger_name is not null) = 8,
        'two exact enabled triggers per target table',
        coalesce(pg_catalog.string_agg(
            pg_catalog.format('%I.%I:%I', a.schema_name, a.relation_name, a.trigger_name)
            || ':tgtype=' || a.tgtype::text
            || ':enabled=' || a.tgenabled::text,
            ',' order by a.schema_name, a.relation_name, a.trigger_name
        ), '')
    from actual_triggers a
    left join expected_triggers expected_trigger
      on expected_trigger.trigger_name = a.trigger_name
     and expected_trigger.tgtype = a.tgtype
     and expected_trigger.tgenabled = a.tgenabled
     and expected_trigger.function_schema = a.function_schema
     and expected_trigger.function_name = a.function_name
     and expected_trigger.function_argument_types = a.function_argument_types
     and expected_trigger.tgnargs = a.tgnargs
     and expected_trigger.tgargs_byte_length = a.tgargs_byte_length
     and expected_trigger.tgqual_is_null = (a.tgqual is null)
     and expected_trigger.tgattr_is_empty = (pg_catalog.cardinality(a.tgattr) = 0)
    union all
    select
        'target_tables_zero_rows',
        count(*) = 4 and coalesce(sum(row_count), 0) = 0,
        'four empty target tables',
        coalesce(pg_catalog.string_agg(
            relation_name || ':' || row_count::text,
            ',' order by relation_name
        ), '')
    from target_row_counts
    union all
    select
        'public_execute_on_entrypoints_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(signature, ',' order by signature), '')
    from unexpected_public_entry_execute
    union all
    select
        'ordinary_roles_execute_on_entrypoints_zero',
        count(*) = 0,
        '0',
        coalesce(pg_catalog.string_agg(signature, ',' order by signature), '')
    from unexpected_ordinary_entry_execute
)
select
    'managed-inspector-production-intermediate@1'::text as pack,
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
