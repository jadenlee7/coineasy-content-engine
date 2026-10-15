-- Read-only metadata after the independently approved schema install.
-- No drafts, assets, source bodies, API credentials or provider responses.
begin read only;
set local statement_timeout='15s';
with new_tables as (
  select c.oid,n.nspname,c.relname,c.relowner,c.relacl,c.relrowsecurity,c.relforcerowsecurity
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='private' and c.relname in ('ybo_preserved_babylon_compatibility_receipts',
    'ybo_recovery_readiness_anchor','ybo_preserved_babylon_handoffs')
), new_functions as (
  select p.oid,n.nspname,p.proname,p.proowner,p.proacl,p.prosecdef,p.provolatile,p.proconfig,p.prosrc,
    pg_get_function_identity_arguments(p.oid) as args
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where (n.nspname='private' and p.proname in ('guard_ybo_preserved_babylon_immutable',
    'guard_ybo_recovery_anchor_insert','exact_preserved_babylon_context'))
    or (n.nspname='public' and p.proname in ('get_exact_preserved_babylon_context','claim_exact_preserved_babylon_job'))
)
select jsonb_build_object(
  'schema','exact-preserved-babylon-catalog@1',
  'tables',(select jsonb_agg(jsonb_build_object('name',t.relname,'owner',pg_get_userbyid(t.relowner),
    'rls',t.relrowsecurity,'force_rls',t.relforcerowsecurity,
    'nonowner_acl_count',(select count(*) from aclexplode(coalesce(t.relacl,acldefault('r',t.relowner))) a where a.grantee<>t.relowner),
    'policy_count',(select count(*) from pg_policy p where p.polrelid=t.oid),
    'columns_sha256',(select encode(extensions.digest(convert_to(coalesce(jsonb_agg(jsonb_build_object(
      'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),'not_null',a.attnotnull)
      order by a.attnum),'[]'::jsonb)::text,'UTF8'),'sha256'),'hex') from pg_attribute a where a.attrelid=t.oid and a.attnum>0 and not a.attisdropped),
    'constraints_sha256',(select encode(extensions.digest(convert_to(coalesce(jsonb_agg(pg_get_constraintdef(c.oid)
      order by c.conname),'[]'::jsonb)::text,'UTF8'),'sha256'),'hex') from pg_constraint c where c.conrelid=t.oid),
    'triggers_sha256',(select encode(extensions.digest(convert_to(coalesce(jsonb_agg(pg_get_triggerdef(g.oid)
      order by g.tgname),'[]'::jsonb)::text,'UTF8'),'sha256'),'hex') from pg_trigger g where g.tgrelid=t.oid and not g.tgisinternal)
    ) order by t.relname) from new_tables t),
  'functions',(select jsonb_agg(jsonb_build_object('schema',f.nspname,'name',f.proname,'args',f.args,
    'owner',pg_get_userbyid(f.proowner),'security_definer',f.prosecdef,'volatility',f.provolatile,
    'config',f.proconfig,'body_sha256',encode(extensions.digest(convert_to(f.prosrc,'UTF8'),'sha256'),'hex'),
    'anon_execute',has_function_privilege('anon',f.oid,'EXECUTE'),
    'authenticated_execute',has_function_privilege('authenticated',f.oid,'EXECUTE'),
    'service_role_execute',has_function_privilege('service_role',f.oid,'EXECUTE')) order by f.nspname,f.proname) from new_functions f),
  'receipt_count',(select count(*) from private.ybo_preserved_babylon_compatibility_receipts),
  'anchor_count',(select count(*) from private.ybo_recovery_readiness_anchor),
  'handoff_count',(select count(*) from private.ybo_preserved_babylon_handoffs)
);
rollback;
