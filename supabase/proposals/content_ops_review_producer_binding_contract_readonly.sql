-- Read-only pre/post contract for the separately authorized
-- 20260916190000_content_ops_review_producer_binding.sql migration.
-- Never applies that migration or reads content/job rows.
begin transaction read only;

do $$
declare
    candidate_oid oid := to_regprocedure(
        'private.content_ops_review_candidate(uuid,uuid,uuid)');
    observed_hash text;
    field_spec text;
begin
    if current_setting('transaction_read_only') <> 'on' then
        raise exception 'producer_binding_contract_not_read_only';
    end if;
    if candidate_oid is null or to_regclass('public.jobs') is null
      or to_regclass('supabase_migrations.schema_migrations') is null then
        raise exception 'producer_binding_contract_missing';
    end if;
    foreach field_spec in array array[
        'id|uuid', 'workspace_id|uuid', 'client_id|text',
        'content_item_id|uuid', 'job_kind|text', 'status|text',
        'input|jsonb', 'output|jsonb', 'finished_at|timestamptz'
    ] loop
        if not exists (
            select 1 from pg_catalog.pg_attribute a
            where a.attrelid='public.jobs'::regclass
              and a.attname=split_part(field_spec,'|',1)
              and a.atttypid=to_regtype(split_part(field_spec,'|',2))
              and a.attnum>0 and not a.attisdropped
              and (a.attname<>'content_item_id' or not a.attnotnull)
        ) then
            raise exception 'producer_binding_contract_job_column_mismatch';
        end if;
    end loop;
    foreach field_spec in array array[
        'version|text', 'name|text', 'statements|text[]'
    ] loop
        if not exists (
            select 1 from pg_catalog.pg_attribute a
            where a.attrelid='supabase_migrations.schema_migrations'::regclass
              and a.attname=split_part(field_spec,'|',1)
              and a.atttypid=to_regtype(split_part(field_spec,'|',2))
              and a.attnum>0 and not a.attisdropped
        ) then
            raise exception 'producer_binding_contract_history_column_mismatch';
        end if;
    end loop;
    if exists (
        select 1 from pg_catalog.pg_attribute a
        where a.attrelid='supabase_migrations.schema_migrations'::regclass
          and a.attnum>0 and not a.attisdropped and a.attnotnull
          and a.attname not in ('version','name','statements')
          and not exists (select 1 from pg_catalog.pg_attrdef d
              where d.adrelid=a.attrelid and d.adnum=a.attnum)
    ) then
        raise exception 'producer_binding_contract_history_column_mismatch';
    end if;
    select encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') into observed_hash
    from pg_catalog.pg_proc p where p.oid=candidate_oid
      and p.prorettype='jsonb'::regtype and p.provolatile='v'
      and p.prosecdef and pg_catalog.pg_get_userbyid(p.proowner)='postgres'
      and coalesce(p.proconfig @> array['search_path=""']::text[],false);
    if observed_hash is null or observed_hash not in (
        '5de6d755095e63f3f7e03eb9f53db5d7fdada0911e222237fd459f12eaa98ffb',
        '491114d72507197cb680794cc04d5755950f257f34574de4c88d0fe15f7a541d'
    ) or has_function_privilege('anon',candidate_oid,'EXECUTE')
      or has_function_privilege('authenticated',candidate_oid,'EXECUTE')
      or has_function_privilege('service_role',candidate_oid,'EXECUTE') then
        raise exception 'producer_binding_contract_function_mismatch';
    end if;
    if observed_hash=
        '5de6d755095e63f3f7e03eb9f53db5d7fdada0911e222237fd459f12eaa98ffb'
    then
        if exists (select 1 from supabase_migrations.schema_migrations
            where version='20260916190000') then
            raise exception 'producer_binding_contract_history_mismatch';
        end if;
    elsif (select count(*) from supabase_migrations.schema_migrations
        where version='20260916190000'
          and name='content_ops_review_producer_binding'
          and pg_catalog.cardinality(statements)=1
          and encode(sha256(convert_to(statements[1],'UTF8')),'hex')=
              'dd5dc69d4bc18053a828a8634cd8d603e9282f375ccf21d6b7d96dc29f247ae6'
    ) <> 1 or (select count(*) from supabase_migrations.schema_migrations
        where version='20260916190000') <> 1 then
        raise exception 'producer_binding_contract_history_mismatch';
    end if;
end $$;

select jsonb_build_object(
    'producer_binding_contract',
    case when encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')=
        '491114d72507197cb680794cc04d5755950f257f34574de4c88d0fe15f7a541d'
      then 'corrected_exact_history' else 'legacy_no_history' end,
    'read_only',true,'changes',0,'provider_calls',0)
from pg_catalog.pg_proc p
where p.oid='private.content_ops_review_candidate(uuid,uuid,uuid)'::regprocedure;
commit;
