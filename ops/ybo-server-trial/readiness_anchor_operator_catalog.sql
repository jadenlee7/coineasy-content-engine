begin read only;
set local statement_timeout='15s';
with anchor_snapshot as (
-- READ ONLY. Run only after the separately approved atomic schema/body apply.
-- No anchor values, business rows, source bodies, credentials or API calls.
with relation as (
  select * from pg_class where oid=to_regclass('private.ybo_trial_readiness_anchor')
), routine as (
  select * from pg_proc where oid=to_regprocedure('private.guard_ybo_trial_readiness_anchor()')
), constraint_defs as (
  select jsonb_agg(jsonb_build_object('name',conname,'type',contype,
    'validated',convalidated,'definition',pg_get_constraintdef(oid,true)) order by conname) as value
  from pg_constraint where conrelid=to_regclass('private.ybo_trial_readiness_anchor')
), trigger_defs as (
  select jsonb_agg(jsonb_build_object('name',tgname,'enabled',tgenabled,
    'definition',pg_get_triggerdef(oid,true)) order by tgname) as value
  from pg_trigger where tgrelid=to_regclass('private.ybo_trial_readiness_anchor') and not tgisinternal
), column_defs as (
  select jsonb_agg(jsonb_build_object('name',attname,'type',format_type(atttypid,atttypmod),
    'not_null',attnotnull,'generated',attgenerated,'identity',attidentity) order by attnum) as value
  from pg_attribute where attrelid=to_regclass('private.ybo_trial_readiness_anchor')
    and attnum>0 and not attisdropped
)
select jsonb_build_object(
  'schema','ybo-readiness-anchor-catalog@1','exists',true,
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
  'row_count',((xpath('/row/count/text()',query_to_xml('select count(*) from private.ybo_trial_readiness_anchor',false,true,'')))[1]::text)::bigint
) as value from relation,routine,column_defs,constraint_defs,trigger_defs

), relations as (
 select c.oid,n.nspname||'.'||c.relname as name from pg_class c join pg_namespace n on n.oid=c.relnamespace
 where n.nspname||'.'||c.relname = any(array[
 'public.jobs','public.source_items','public.source_feeds','public.workspace_clients',
 'public.content_items','public.content_source_links','private.official_x_daily_slots',
 'private.official_x_source_state','private.official_x_style_reference_packs'])
),
dependencies as (
 select p.oid,n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')' as signature,
 p.prosrc,p.prosecdef,p.proconfig,p.provolatile,pg_get_userbyid(p.proowner) as owner
 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where (n.nspname='public' and p.proname=any(array['claim_review_draft_job','queue_review_draft_job',
 'record_official_x_sources','get_or_create_official_x_style_reference_pack','complete_review_draft_job',
 'fail_review_draft_job','bind_review_draft_execution_plane']))
 or p.oid in(select t.tgfoid from pg_trigger t where t.tgrelid in(select oid from relations) and not t.tgisinternal)
), snapshot as (
 select jsonb_build_object(
 'relations',(select jsonb_agg(jsonb_build_object('name',r.name,
 'columns',(select jsonb_agg(jsonb_build_object('name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
 'notnull',a.attnotnull,'default',pg_get_expr(d.adbin,d.adrelid)) order by a.attnum)
 from pg_attribute a left join pg_attrdef d on d.adrelid=a.attrelid and d.adnum=a.attnum
 where a.attrelid=r.oid and a.attnum>0 and not a.attisdropped),
 'constraints',(select coalesce(jsonb_agg(jsonb_build_object('name',conname,'definition',pg_get_constraintdef(oid)) order by conname),'[]'::jsonb) from pg_constraint where conrelid=r.oid),
 'triggers',(select coalesce(jsonb_agg(jsonb_build_object('name',tgname,'enabled',tgenabled,'definition',pg_get_triggerdef(oid)) order by tgname),'[]'::jsonb) from pg_trigger where tgrelid=r.oid and not tgisinternal)
 ) order by r.name) from relations r),
 'functions',(select jsonb_agg(jsonb_build_object('signature',signature,'body_sha256',encode(extensions.digest(convert_to(prosrc,'UTF8'),'sha256'),'hex'),
 'owner',owner,'security_definer',prosecdef,'configuration',proconfig,'volatility',provolatile) order by signature) from dependencies)
 ) as value
)
select jsonb_build_object('observed_at',clock_timestamp(),'server_version_num',current_setting('server_version_num'),
 'current_role',current_user,'read_only',current_setting('transaction_read_only'),
 'anchor_schema',coalesce((select value from anchor_snapshot),jsonb_build_object('schema','ybo-readiness-anchor-catalog@1','exists',false,'row_count',0)),
 'candidate_installed',to_regprocedure('public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)') is not null,
 'dependency_catalog',snapshot.value,
 'dependency_sha256',encode(extensions.digest(convert_to(snapshot.value::text,'UTF8'),'sha256'),'hex'),
 'target_functions',(
 select jsonb_object_agg(p.proname,jsonb_build_object(
   'oid',p.oid,'owner',pg_get_userbyid(p.proowner),'security_definer',p.prosecdef,
   'volatility',p.provolatile,'configuration',p.proconfig,
   'body_sha256',encode(extensions.digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex'),
   'non_body_catalog_sha256',encode(extensions.digest(convert_to((to_jsonb(p)-'prosrc')::text,'UTF8'),'sha256'),'hex'),
   'service_role_execute',has_function_privilege('service_role',p.oid,'EXECUTE'),
   'anon_execute',has_function_privilege('anon',p.oid,'EXECUTE'),
   'authenticated_execute',has_function_privilege('authenticated',p.oid,'EXECUTE'),
   'other_grantees',(select count(*) from aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
       where a.grantee not in(p.proowner,'service_role'::regrole::oid)),
   'unexpected_grant_options',(select count(*) from aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
       where a.is_grantable),
   'anchored_window_present',p.prosrc like '%private.ybo_trial_readiness_anchor%' and p.prosrc like '%1a2bef47-4827-4bc3-9b4d-5baf2714015d%'
 )) from pg_proc p where p.oid in(
   to_regprocedure('public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)'),
   to_regprocedure('public.get_quality_banner_generation_context(uuid,text,uuid)'))),
 'target_dependency_sha256',(
   select encode(extensions.digest(convert_to(coalesce(jsonb_agg(d.row order by d.row::text)::text,'[]'),'UTF8'),'sha256'),'hex')
   from (
     select to_jsonb(dep) as row from pg_depend dep
     where (dep.classid='pg_proc'::regclass and dep.objid in(
       to_regprocedure('public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)'),
       to_regprocedure('public.get_quality_banner_generation_context(uuid,text,uuid)')))
     or (dep.refclassid='pg_proc'::regclass and dep.refobjid in(
       to_regprocedure('public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)'),
       to_regprocedure('public.get_quality_banner_generation_context(uuid,text,uuid)')))
     union all
     select to_jsonb(dep) from pg_shdepend dep
     where dep.classid='pg_proc'::regclass
       and dep.dbid=(select oid from pg_database where datname=current_database())
       and dep.objid in(
         to_regprocedure('public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)'),
         to_regprocedure('public.get_quality_banner_generation_context(uuid,text,uuid)'))
   ) d),
 'existing_security_catalog_sha256',(
 select encode(extensions.digest(convert_to(coalesce(jsonb_agg(data order by sort)::text,'[]'),'UTF8'),'sha256'),'hex') from(
 select 'fn:'||n.nspname||'.'||p.oid::regprocedure::text as sort,jsonb_build_object('kind','function','schema',n.nspname,'name',p.proname,
 'identity',pg_get_function_identity_arguments(p.oid),'owner',pg_get_userbyid(p.proowner),'acl',p.proacl::text,'body',p.prosrc,'config',p.proconfig,'definer',p.prosecdef) as data
 from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in('public','private','agent_runtime')
 and p.proname not in ('claim_exact_natural_review_draft_job','get_quality_banner_generation_context')
 and not (n.nspname='private' and p.proname='guard_ybo_trial_readiness_anchor' and pg_get_function_identity_arguments(p.oid)='')
 union all select 'rel:'||n.nspname||'.'||c.relname,jsonb_build_object('kind','relation','schema',n.nspname,'name',c.relname,'owner',pg_get_userbyid(c.relowner),'acl',c.relacl::text,'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity)
 from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname in('public','private','agent_runtime')
 and not (n.nspname='private' and c.relname in('ybo_trial_readiness_anchor','ybo_trial_readiness_anchor_pkey'))
 union all select 'role:'||rolname,jsonb_build_object('kind','role','name',rolname,'super',rolsuper,'login',rolcanlogin,'inherit',rolinherit,'bypass',rolbypassrls,'create_role',rolcreaterole,'create_db',rolcreatedb) from pg_roles
 union all select 'member:'||roleid::regrole::text||':'||member::regrole::text,jsonb_build_object('kind','membership','role',roleid::regrole::text,'member',member::regrole::text,'admin',admin_option) from pg_auth_members
 ) security
 )) as catalog from snapshot;
rollback;
