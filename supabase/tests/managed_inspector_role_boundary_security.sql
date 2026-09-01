-- LOCAL DISPOSABLE DATABASE ONLY. Exact effective-privilege inventory for the
-- managed inspector PostgREST role. No fixture reaches an external service.
\set ON_ERROR_STOP on
begin;

create function pg_temp.assert_true(ok boolean, label text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'managed inspector role assertion: %',label; end if; end $$;

do $security$
declare
    target_role constant text := 'coineasy_managed_inspector';
    signature text;
    schema_name text;
    rel record;
    expected oid[] := array[
      'public.managed_telegram_inspect_context(uuid,text)'::regprocedure::oid,
      'public.register_managed_telegram_inspect_consent(uuid,jsonb,text)'::regprocedure::oid,
      'public.inspect_managed_telegram_delivery_unknown(uuid)'::regprocedure::oid
    ];
begin
    begin
      execute 'create role coineasy_managed_inspector nologin';
      raise exception 'same-name role collision unexpectedly adopted';
    exception when duplicate_object then null; end;
    perform pg_temp.assert_true(exists(
      select 1 from pg_roles where rolname=target_role and not rolcanlogin and not rolinherit
        and not rolsuper and not rolcreaterole and not rolcreatedb and not rolreplication and not rolbypassrls
    ),'NOLOGIN NOINHERIT unprivileged role attributes');
    perform pg_temp.assert_true(not exists(
      select 1 from pg_auth_members where member=target_role::regrole
    ),'dedicated role inherits no role, direct or otherwise');
    perform pg_temp.assert_true((select count(*)=1 from pg_auth_members
      where roleid=target_role::regrole and member='authenticator'::regrole
        and not admin_option and not inherit_option and set_option
    ),'authenticator membership is SET TRUE INHERIT FALSE ADMIN FALSE');
    perform pg_temp.assert_true(not exists(
      with recursive inherited(roleid) as (
        select m.roleid from pg_auth_members m where m.member=target_role::regrole
        union select m.roleid from pg_auth_members m join inherited i on m.member=i.roleid
      ) select 1 from inherited
    ),'no transitive inherited role');
    perform pg_temp.assert_true(not exists(
      select 1 from pg_class c where c.relowner=target_role::regrole
      union all select 1 from pg_proc p where p.proowner=target_role::regrole
      union all select 1 from pg_namespace n where n.nspowner=target_role::regrole
      union all select 1 from pg_type t where t.typowner=target_role::regrole
    ),'dedicated role owns no database object');

    -- Do not filter p.proacl: NULL is the built-in PUBLIC EXECUTE default.
    perform pg_temp.assert_true((select count(*)=cardinality(expected)
      from pg_proc p join pg_namespace n on n.oid=p.pronamespace
      where n.nspname='public' and has_function_privilege(target_role,p.oid,'execute')),
      'exact public executable routine cardinality including PUBLIC and NULL ACL');
    perform pg_temp.assert_true(not exists(
      select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
      where n.nspname='public' and has_function_privilege(target_role,p.oid,'execute')
        and not (p.oid=any(expected))
    ),'no public executable routine outside exact three OIDs');
    foreach signature in array array[
      'public.managed_telegram_inspect_context(uuid,text)',
      'public.register_managed_telegram_inspect_consent(uuid,jsonb,text)',
      'public.inspect_managed_telegram_delivery_unknown(uuid)'
    ] loop
      perform pg_temp.assert_true(has_function_privilege(target_role,signature,'execute'),'required execute '||signature);
      perform pg_temp.assert_true(not has_function_privilege('authenticated',signature,'execute'),'authenticated removed '||signature);
      perform pg_temp.assert_true(not exists(
        select 1 from pg_proc p
        cross join lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        where p.oid=signature::regprocedure and a.grantee=0 and a.privilege_type='EXECUTE'
      ),'PUBLIC removed '||signature);
    end loop;

    foreach schema_name in array array['private','auth','storage','agent_runtime'] loop
      if to_regnamespace(schema_name) is not null then
        perform pg_temp.assert_true(not has_schema_privilege(target_role,schema_name,'usage'),'no schema usage '||schema_name);
        perform pg_temp.assert_true(not has_schema_privilege(target_role,schema_name,'create'),'no schema create '||schema_name);
      end if;
    end loop;
    perform pg_temp.assert_true(has_schema_privilege(target_role,'public','usage'),'public lookup required');
    perform pg_temp.assert_true(not has_schema_privilege(target_role,'public','create'),'no public CREATE');

    for rel in select c.oid,n.nspname,c.relname,c.relkind from pg_class c
      join pg_namespace n on n.oid=c.relnamespace
      where n.nspname in('public','private','auth','storage','agent_runtime')
        and c.relkind in('r','p','v','m','f','S') loop
      if rel.relkind='S' then
        perform pg_temp.assert_true(not has_sequence_privilege(target_role,rel.oid,'usage')
          and not has_sequence_privilege(target_role,rel.oid,'select')
          and not has_sequence_privilege(target_role,rel.oid,'update'),
          'no sequence privilege '||rel.nspname||'.'||rel.relname);
      else
        perform pg_temp.assert_true(not has_table_privilege(target_role,rel.oid,'select,insert,update,delete,truncate,references,trigger')
          and not has_any_column_privilege(target_role,rel.oid,'select,insert,update,references'),
          'no table or column privilege '||rel.nspname||'.'||rel.relname);
      end if;
    end loop;

    foreach signature in array array[
      'public.queue_content_generation(uuid,jsonb,text)',
      'public.request_content_publication(uuid,uuid,text,timestamptz,text)',
      'public.propose_agent_work_order(uuid,jsonb,text)',
      'public.inspect_exact_telegram_delivery_unknown_resolution(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz,text,jsonb)',
      'public.approve_exact_telegram_delivery_unknown_resolution(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz,text,jsonb)',
      'public.resolve_exact_telegram_delivery_unknown_without_resend(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz,text,jsonb)'
    ] loop
      if to_regprocedure(signature) is not null then
        perform pg_temp.assert_true(not has_function_privilege(target_role,signature,'execute'),'old/general RPC denied '||signature);
      end if;
    end loop;
end
$security$;

-- Negative fixtures prove the inventory includes PUBLIC/NULL function ACLs,
-- schema, relation/column/sequence, ownership and indirect membership rights.
-- The surrounding transaction rolls every fixture back.
create function public.managed_inspector_public_execute_negative_fixture()
returns void language sql as $$ select $$;
select pg_temp.assert_true(
  has_function_privilege('coineasy_managed_inspector','public.managed_inspector_public_execute_negative_fixture()','execute'),
  'negative NULL ACL fixture is effectively executable through PUBLIC');
drop function public.managed_inspector_public_execute_negative_fixture();

grant create on schema public to coineasy_managed_inspector;
select pg_temp.assert_true(
  has_schema_privilege('coineasy_managed_inspector','public','create'),
  'negative public schema CREATE fixture is effectively visible');
revoke create on schema public from coineasy_managed_inspector;

grant usage on schema private to coineasy_managed_inspector;
select pg_temp.assert_true(
  has_schema_privilege('coineasy_managed_inspector','private','usage'),
  'negative private schema USAGE fixture is effectively visible');
revoke usage on schema private from coineasy_managed_inspector;

create table public.managed_inspector_public_dml_negative_fixture(id integer);
grant insert on public.managed_inspector_public_dml_negative_fixture to public;
select pg_temp.assert_true(
  has_table_privilege('coineasy_managed_inspector','public.managed_inspector_public_dml_negative_fixture','insert'),
  'negative PUBLIC DML fixture is effectively writable');
drop table public.managed_inspector_public_dml_negative_fixture;

create table public.managed_inspector_column_negative_fixture(id integer, detail text);
grant select(id) on public.managed_inspector_column_negative_fixture to coineasy_managed_inspector;
select pg_temp.assert_true(
  has_any_column_privilege('coineasy_managed_inspector','public.managed_inspector_column_negative_fixture','select'),
  'negative column SELECT fixture is effectively readable');
drop table public.managed_inspector_column_negative_fixture;

create sequence public.managed_inspector_sequence_negative_fixture;
grant usage on sequence public.managed_inspector_sequence_negative_fixture to coineasy_managed_inspector;
select pg_temp.assert_true(
  has_sequence_privilege('coineasy_managed_inspector','public.managed_inspector_sequence_negative_fixture','usage'),
  'negative sequence USAGE fixture is effectively visible');
drop sequence public.managed_inspector_sequence_negative_fixture;

create table public.managed_inspector_owner_negative_fixture(id integer);
alter table public.managed_inspector_owner_negative_fixture owner to coineasy_managed_inspector;
select pg_temp.assert_true(
  (select relowner='coineasy_managed_inspector'::regrole
     from pg_class where oid='public.managed_inspector_owner_negative_fixture'::regclass),
  'negative ownership fixture is catalog-visible');
alter table public.managed_inspector_owner_negative_fixture owner to postgres;
drop table public.managed_inspector_owner_negative_fixture;

create role managed_inspector_negative_parent nologin noinherit;
grant managed_inspector_negative_parent to coineasy_managed_inspector with inherit false, set false, admin false;
select pg_temp.assert_true(exists(
  select 1 from pg_auth_members where roleid='managed_inspector_negative_parent'::regrole
    and member='coineasy_managed_inspector'::regrole),
  'negative membership fixture is catalog-visible');
revoke managed_inspector_negative_parent from coineasy_managed_inspector;
drop role managed_inspector_negative_parent;

-- Actual invoker denials: no workspace bootstrap and no old/general RPC.
set local role coineasy_managed_inspector;
do $denials$
declare denied boolean;
begin
  denied:=false;
  begin
    insert into public.workspaces(id,name,slug,created_by)
      values(gen_random_uuid(),'denied','managed-inspector-denied',gen_random_uuid());
  exception when insufficient_privilege then denied:=true; end;
  if not denied then raise exception 'managed inspector self-workspace unexpectedly allowed'; end if;

  denied:=false;
  begin perform public.queue_content_generation(gen_random_uuid(),'{}'::jsonb,'denied');
  exception when insufficient_privilege then denied:=true; end;
  if not denied then raise exception 'managed inspector general RPC unexpectedly allowed'; end if;
end
$denials$;

reset role;
do $$ begin raise notice 'PASS managed inspector exact role/ACL/PUBLIC/ownership/membership/negative-fixture tests'; end $$;
rollback;
