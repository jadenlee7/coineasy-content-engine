/** Offline SQL builder only. No CLI, database connection, or network path. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

export const MIGRATION_VERSION = '20260916190000';
export const MIGRATION_NAME = 'content_ops_review_producer_binding';
export const MIGRATION_SHA256 = 'dd5dc69d4bc18053a828a8634cd8d603e9282f375ccf21d6b7d96dc29f247ae6';
export const LEGACY_BODY_SHA256 = '5de6d755095e63f3f7e03eb9f53db5d7fdada0911e222237fd459f12eaa98ffb';
export const CORRECTED_BODY_SHA256 = '491114d72507197cb680794cc04d5755950f257f34574de4c88d0fe15f7a541d';

export function buildAtomicProducerBindingSql(source) {
  assert.equal(typeof source, 'string');
  const bytes = Buffer.from(source, 'utf8');
  assert.equal(bytes.length, 10717, 'migration byte length changed');
  assert.equal(createHash('sha256').update(bytes).digest('hex'), MIGRATION_SHA256,
    'migration bytes changed');
  assert.equal(source.includes('\r'), false, 'migration must use LF');
  assert.equal(source.endsWith('\n'), true, 'migration needs a final newline');
  const begin = source.indexOf('begin;\n');
  assert.ok(begin >= 0 && source.indexOf('begin;\n', begin + 1) < 0,
    'migration transaction start changed');
  assert.equal(source.endsWith('commit;\n'), true, 'migration transaction end changed');
  const body = source.slice(begin + 'begin;\n'.length, -'commit;\n'.length);
  assert.ok(body.includes('create or replace function private.content_ops_review_candidate('));
  assert.ok(body.includes('revoke all on function private.content_ops_review_candidate('));
  const exactSourceHex = bytes.toString('hex');

  return `-- Offline-generated, one-migration transaction; no network runner is provided.
begin;
set local lock_timeout='5s';
set local statement_timeout='60s';
set local idle_in_transaction_session_timeout='30s';
set local search_path=pg_catalog;
lock table supabase_migrations.schema_migrations in share row exclusive mode;
do $producer_pre$
declare candidate_oid oid := to_regprocedure(
    'private.content_ops_review_candidate(uuid,uuid,uuid)');
begin
    if current_user::text <> 'postgres' or session_user::text <> 'postgres'
       or current_setting('transaction_read_only') <> 'off'
       or not pg_try_advisory_xact_lock(hashtextextended(
           'coineasy:private-review:producer-binding:${MIGRATION_VERSION}',0)) then
        raise exception 'producer_binding_apply_executor_or_lock_invalid';
    end if;
    if candidate_oid is null or exists (
        select 1 from supabase_migrations.schema_migrations
        where version='${MIGRATION_VERSION}'
    ) or not exists (
        select 1 from pg_proc p where p.oid=candidate_oid
          and p.prorettype='jsonb'::regtype and p.provolatile='v'
          and p.prosecdef and pg_get_userbyid(p.proowner)='postgres'
          and coalesce(p.proconfig @> array['search_path=""']::text[],false)
          and encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')=
              '${LEGACY_BODY_SHA256}'
    ) or not exists (
        select 1 from pg_attribute a
        where a.attrelid='public.jobs'::regclass
          and a.attname='content_item_id' and a.atttypid='uuid'::regtype
          and a.attnum>0 and not a.attisdropped and not a.attnotnull
    ) or exists (
        select 1 from pg_attribute a
        where a.attrelid='supabase_migrations.schema_migrations'::regclass
          and a.attnum>0 and not a.attisdropped and a.attnotnull
          and a.attname not in ('version','name','statements')
          and not exists (select 1 from pg_attrdef d
              where d.adrelid=a.attrelid and d.adnum=a.attnum)
    ) or has_function_privilege('anon',candidate_oid,'EXECUTE')
      or has_function_privilege('authenticated',candidate_oid,'EXECUTE')
      or has_function_privilege('service_role',candidate_oid,'EXECUTE') then
        raise exception 'producer_binding_apply_precondition_mismatch';
    end if;
    if encode(sha256(decode('${exactSourceHex}','hex')),'hex')
       <> '${MIGRATION_SHA256}' then
        raise exception 'producer_binding_apply_source_mismatch';
    end if;
end
$producer_pre$;
-- BEGIN EXACT MIGRATION ${MIGRATION_VERSION} SHA256 ${MIGRATION_SHA256}
${body}-- END EXACT MIGRATION ${MIGRATION_VERSION}
insert into supabase_migrations.schema_migrations(version,name,statements)
values ('${MIGRATION_VERSION}','${MIGRATION_NAME}',array[
    convert_from(decode('${exactSourceHex}','hex'),'UTF8')]);
do $producer_post$
begin
    if not exists (
        select 1 from pg_proc p where p.oid=
          'private.content_ops_review_candidate(uuid,uuid,uuid)'::regprocedure
          and p.prorettype='jsonb'::regtype and p.provolatile='v'
          and p.prosecdef and pg_get_userbyid(p.proowner)='postgres'
          and coalesce(p.proconfig @> array['search_path=""']::text[],false)
          and encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')=
              '${CORRECTED_BODY_SHA256}'
    ) or (select count(*) from supabase_migrations.schema_migrations
        where version='${MIGRATION_VERSION}'
          and name='${MIGRATION_NAME}'
          and cardinality(statements)=1
          and encode(sha256(convert_to(statements[1],'UTF8')),'hex')=
              '${MIGRATION_SHA256}') <> 1
      or has_function_privilege('anon',
        'private.content_ops_review_candidate(uuid,uuid,uuid)','EXECUTE')
      or has_function_privilege('authenticated',
        'private.content_ops_review_candidate(uuid,uuid,uuid)','EXECUTE')
      or has_function_privilege('service_role',
        'private.content_ops_review_candidate(uuid,uuid,uuid)','EXECUTE') then
        raise exception 'producer_binding_apply_postcondition_mismatch';
    end if;
end
$producer_post$;
commit;
select jsonb_build_object('producer_binding_apply','committed',
    'version','${MIGRATION_VERSION}',
    'migration_sha256','${MIGRATION_SHA256}',
    'provider_calls',0,'runtime_activation',false);
`;
}
