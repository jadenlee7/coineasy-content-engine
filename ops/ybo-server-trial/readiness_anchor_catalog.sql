-- READ ONLY. Run only after the separately approved atomic schema/body apply.
-- No anchor values, business rows, source bodies, credentials or API calls.
with relation as (
  select * from pg_class where oid='private.ybo_trial_readiness_anchor'::regclass
), routine as (
  select * from pg_proc where oid='private.guard_ybo_trial_readiness_anchor()'::regprocedure
), constraint_defs as (
  select jsonb_agg(jsonb_build_object('name',conname,'type',contype,
    'validated',convalidated,'definition',pg_get_constraintdef(oid,true)) order by conname) as value
  from pg_constraint where conrelid='private.ybo_trial_readiness_anchor'::regclass
), trigger_defs as (
  select jsonb_agg(jsonb_build_object('name',tgname,'enabled',tgenabled,
    'definition',pg_get_triggerdef(oid,true)) order by tgname) as value
  from pg_trigger where tgrelid='private.ybo_trial_readiness_anchor'::regclass and not tgisinternal
), column_defs as (
  select jsonb_agg(jsonb_build_object('name',attname,'type',format_type(atttypid,atttypmod),
    'not_null',attnotnull,'generated',attgenerated,'identity',attidentity) order by attnum) as value
  from pg_attribute where attrelid='private.ybo_trial_readiness_anchor'::regclass
    and attnum>0 and not attisdropped
)
select jsonb_build_object(
  'schema','ybo-readiness-anchor-catalog@1',
  'owner',pg_get_userbyid(relation.relowner),
  'guard_owner',pg_get_userbyid(routine.proowner),
  'owner_matches_existing_routines',relation.relowner=routine.proowner
    and relation.relowner=(select proowner from pg_proc where oid=
      'public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)'::regprocedure)
    and relation.relowner=(select proowner from pg_proc where oid=
      'public.get_quality_banner_generation_context(uuid,text,uuid)'::regprocedure),
  'relkind',relation.relkind,'persistence',relation.relpersistence,
  'rls_enabled',relation.relrowsecurity,'rls_forced',relation.relforcerowsecurity,
  'policy_count',(select count(*) from pg_policy where polrelid=relation.oid),
  'table_acl',coalesce(to_jsonb(relation.relacl),'null'::jsonb),
  'guard_acl',coalesce(to_jsonb(routine.proacl),'null'::jsonb),
  'non_owner_table_acl_count',(select count(*) from aclexplode(coalesce(relation.relacl,
    acldefault('r',relation.relowner))) where grantee<>relation.relowner),
  'non_owner_guard_acl_count',(select count(*) from aclexplode(coalesce(routine.proacl,
    acldefault('f',routine.proowner))) where grantee<>routine.proowner),
  'public_table_acl_count',(select count(*) from aclexplode(relation.relacl) where grantee=0),
  'public_guard_acl_count',(select count(*) from aclexplode(routine.proacl) where grantee=0),
  'runtime_roles_have_no_access',not exists(select 1 from
    (values ('anon'),('authenticated'),('service_role')) as roles(name)
    where has_table_privilege(roles.name,relation.oid,
      'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      or has_function_privilege(roles.name,routine.oid,'EXECUTE')),
  'columns',column_defs.value,'constraints',constraint_defs.value,'triggers',trigger_defs.value,
  'constraints_sha256',encode(extensions.digest(convert_to(constraint_defs.value::text,'UTF8'),'sha256'),'hex'),
  'triggers_sha256',encode(extensions.digest(convert_to(trigger_defs.value::text,'UTF8'),'sha256'),'hex'),
  'indexes',(select jsonb_agg(pg_get_indexdef(indexrelid) order by indexrelid::regclass::text)
    from pg_index where indrelid=relation.oid),
  'guard_body_sha256',encode(extensions.digest(convert_to(routine.prosrc,'UTF8'),'sha256'),'hex'),
  'guard_security_definer',routine.prosecdef,'guard_volatility',routine.provolatile,
  'guard_config',to_jsonb(routine.proconfig),
  'row_count',(select count(*) from private.ybo_trial_readiness_anchor)
) from relation,routine,column_defs,constraint_defs,trigger_defs;
