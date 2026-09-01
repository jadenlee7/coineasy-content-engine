-- Transactional SQL-only security/immutability smoke. Claims are synthetic;
-- actual Auth signature/MFA/PostgREST evidence comes from the separate harness.
\set ON_ERROR_STOP on
begin;
set local timezone='UTC';
\ir managed_auth_telegram_inspect_fixture.sql

create function pg_temp.assert_true(ok boolean, label text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'managed inspect assertion: %',label; end if; end $$;

-- SECURITY INVOKER, and switch the actual role, not only JWT claims. No helper
-- is installed persistently and no claims-spoofing production path is added.
create function pg_temp.operator_call(command text, claims jsonb) returns jsonb language plpgsql as $$
declare result jsonb;
begin
    perform set_config('request.jwt.claims',claims::text,true);
    perform set_config('request.jwt.claim.sub',coalesce(claims ->> 'sub',''),true);
    perform set_config('role','coineasy_managed_inspector',true);
    if command ~* '^select| returning ' then execute command into result;
    else execute command; result := 'null'::jsonb; end if;
    perform set_config('role','postgres',true);
    return result;
exception when others then
    perform set_config('role','postgres',true);
    raise;
end $$;

create function pg_temp.expect_denied(command text, claims jsonb, label text, expected_state text default null) returns void language plpgsql as $$
declare denied boolean := false;
begin
    begin perform pg_temp.operator_call(command,claims);
    exception when others then
        if expected_state is not null and sqlstate <> expected_state then raise; end if;
        denied := true;
    end;
    perform pg_temp.assert_true(denied,label);
end $$;

create function pg_temp.state_snapshot(workspace_uuid uuid) returns jsonb language sql as $$
    select jsonb_build_object(
        'items',(select coalesce(jsonb_agg(to_jsonb(x) order by x.id),'[]'::jsonb) from public.content_items x where x.workspace_id=workspace_uuid),
        'versions',(select coalesce(jsonb_agg(to_jsonb(x) order by x.id),'[]'::jsonb) from public.content_versions x where x.workspace_id=workspace_uuid),
        'assets',(select coalesce(jsonb_agg(to_jsonb(x) order by x.id),'[]'::jsonb) from public.assets x where x.workspace_id=workspace_uuid),
        'approvals',(select coalesce(jsonb_agg(to_jsonb(x) order by x.id),'[]'::jsonb) from public.approvals x where x.workspace_id=workspace_uuid),
        'publications',(select coalesce(jsonb_agg(to_jsonb(x) order by x.id),'[]'::jsonb) from public.publications x where x.workspace_id=workspace_uuid),
        'jobs',(select coalesce(jsonb_agg(to_jsonb(x) order by x.id),'[]'::jsonb) from public.jobs x where x.workspace_id=workspace_uuid),
        'events',(select coalesce(jsonb_agg(to_jsonb(x) order by x.id),'[]'::jsonb) from public.event_log x where x.workspace_id=workspace_uuid),
        'resolution_approvals',(select coalesce(jsonb_agg(to_jsonb(x) order by x.operator_approval_id),'[]'::jsonb) from private.exact_telegram_delivery_unknown_approvals x where x.workspace_id=workspace_uuid),
        'resolutions',(select coalesce(jsonb_agg(to_jsonb(x) order by x.resolution_id),'[]'::jsonb) from private.exact_telegram_delivery_unknown_resolutions x where x.workspace_id=workspace_uuid),
        'consents',(select coalesce(jsonb_agg(to_jsonb(x) order by x.consent_id),'[]'::jsonb) from private.managed_telegram_inspect_consents x where x.workspace_id=workspace_uuid),
        'releases',(select coalesce(jsonb_agg(to_jsonb(x) order by x.release_id),'[]'::jsonb) from private.managed_telegram_inspect_releases x where x.workspace_id=workspace_uuid),
        'allowlist',(select coalesce(jsonb_agg(to_jsonb(x) order by x.allowlist_id),'[]'::jsonb) from private.managed_telegram_inspect_allowlist x where x.workspace_id=workspace_uuid),
        'revocations',(select coalesce(jsonb_agg(to_jsonb(x) order by x.revocation_id),'[]'::jsonb) from private.managed_telegram_inspect_revocations x)
    )
$$;

do $acl$
declare role_name text; relation_name text; privilege text; signature text; proc record;
    expected_rpc text[] := array[
      'public.authorize_agent_work_order(uuid,uuid,text,bigint)',
      'public.complete_agent_work_order(uuid,uuid,text,bigint)',
      'public.get_agent_company_dashboard(uuid)', 'public.get_agent_work_order(uuid,uuid)',
      'public.list_agent_operator_inbox(uuid,integer,timestamptz,uuid)',
      'public.propose_agent_work_order(uuid,jsonb,text)',
      'public.record_agent_operator_decision(uuid,uuid,text,bigint,text,text)',
      'public.queue_content_generation(uuid,jsonb,text)',
      'public.record_approved_figma_link(uuid,uuid,text,text,text,text,jsonb)',
      'public.request_content_publication(uuid,uuid,text,timestamptz,text)'
    ];
begin
    -- JSONB schema-key contracts use ASCII order, independent of the database
    -- initialization locale (Linux en_US and macOS C need not sort alike).
    perform pg_temp.assert_true(regexp_count(pg_get_functiondef('private.validate_managed_telegram_inspect_request(jsonb,timestamptz)'::regprocedure),
        'order by k collate "C"')=2,'request and audit schema checks pin ASCII collation');
    -- Freeze the complete cumulative authenticated public EXECUTE surface. A
    -- newly exposed overload/function fails this inventory, not just a small
    -- blacklist. Every non-managed endpoint below also has a target denial.
    foreach signature in array expected_rpc loop
        perform pg_temp.assert_true(has_function_privilege('authenticated',signature,'execute'),'expected cumulative RPC '||signature);
    end loop;
    perform pg_temp.assert_true((select count(*)=cardinality(expected_rpc) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
      where n.nspname='public' and has_function_privilege('authenticated',p.oid,'execute')),'no extra authenticated public RPC');
    perform pg_temp.assert_true((select array_agg(c.relname::text order by c.relname) from pg_class c join pg_namespace n on n.oid=c.relnamespace
      where n.nspname='public' and c.relkind='r' and (has_table_privilege('authenticated',c.oid,'insert,update,delete')
      or has_any_column_privilege('authenticated',c.oid,'insert,update')))
      =array['content_items','content_source_links','content_versions','workspace_clients','workspace_members','workspaces']::text[],'cumulative public DML inventory');
    perform pg_temp.assert_true(not exists(select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
      where n.nspname='public' and c.relkind='r' and (has_table_privilege('authenticated',c.oid,'insert,update,delete')
      or has_any_column_privilege('authenticated',c.oid,'insert,update')) and not c.relrowsecurity),'all public DML surfaces have RLS');
    foreach relation_name in array array['managed_telegram_inspect_releases','managed_telegram_inspect_allowlist','managed_telegram_inspect_consents','managed_telegram_inspect_revocations'] loop
        perform pg_temp.assert_true((select relrowsecurity and relforcerowsecurity from pg_class where oid=('private.'||relation_name)::regclass), 'force RLS '||relation_name);
        execute format('select pg_temp.assert_true(count(*)=0,%L) from private.%I','default empty '||relation_name,relation_name);
        foreach role_name in array array['anon','authenticated','service_role','coineasy_telegram_resolution','coineasy_managed_inspector'] loop
            foreach privilege in array array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] loop
                perform pg_temp.assert_true(not has_table_privilege(role_name,'private.'||relation_name,privilege),'private ledger ACL '||role_name||privilege);
            end loop;
        end loop;
    end loop;
    foreach signature in array array['public.managed_telegram_inspect_context(uuid,text)','public.register_managed_telegram_inspect_consent(uuid,jsonb,text)','public.inspect_managed_telegram_delivery_unknown(uuid)'] loop
        perform pg_temp.assert_true(has_function_privilege('coineasy_managed_inspector',signature,'execute'),'dedicated managed entry');
        foreach role_name in array array['anon','authenticated','service_role','coineasy_telegram_resolution'] loop
            perform pg_temp.assert_true(not has_function_privilege(role_name,signature,'execute'),'new entry denied '||role_name);
        end loop;
    end loop;
    for proc in select p.oid,p.proname,p.prosecdef,p.proconfig,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace
      where (n.nspname='private' and (p.proname like '%managed_telegram_inspect%' or p.proname='managed_telegram_inspect_hash'))
         or (n.nspname='public' and p.proname in('managed_telegram_inspect_context','register_managed_telegram_inspect_consent','inspect_managed_telegram_delivery_unknown')) loop
        perform pg_temp.assert_true(proc.proowner='postgres'::regrole,'designated owner');
        perform pg_temp.assert_true(proc.proconfig @> array['search_path=""'],'fixed search_path');
        if proc.proname not in('managed_telegram_inspect_context','register_managed_telegram_inspect_consent','inspect_managed_telegram_delivery_unknown') then
            foreach role_name in array array['anon','authenticated','service_role','coineasy_telegram_resolution','coineasy_managed_inspector'] loop
                perform pg_temp.assert_true(not has_function_privilege(role_name,proc.oid,'execute'),'private helper ACL');
            end loop;
        end if;
    end loop;
    -- Full cumulative public RPC privilege inventory for this broad base role.
    -- Old dedicated delivery resolution/worker/approval RPCs remain excluded.
    for proc in select p.oid,p.proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' loop
        if proc.proname in('inspect_exact_telegram_delivery_unknown_resolution','approve_exact_telegram_delivery_unknown_resolution',
          'resolve_exact_telegram_delivery_unknown_without_resend','claim_exact_telegram_publication_job','mark_exact_telegram_attempt_started',
          'fail_exact_telegram_publication_job','complete_exact_telegram_publication_job') then
            perform pg_temp.assert_true(not has_function_privilege('authenticated',proc.oid,'execute'),'old sensitive RPC denied '||proc.proname);
        end if;
    end loop;
end $acl$;

insert into auth.users(id,encrypted_password,is_anonymous,role) values
('fa900000-0000-4000-8000-000000000001','synthetic-password-state',false,'coineasy_managed_inspector'),
('fa900000-0000-4000-8000-000000000002',null,false,'authenticated'),
('fa900000-0000-4000-8000-000000000003',null,false,'authenticated');
insert into auth.mfa_factors(id,user_id,factor_type,status) values
('fa910000-0000-4000-8000-000000000001','fa900000-0000-4000-8000-000000000001','totp','verified');
insert into auth.sessions(id,user_id,factor_id,aal,not_after) values
('fa920000-0000-4000-8000-000000000001','fa900000-0000-4000-8000-000000000001','fa910000-0000-4000-8000-000000000001','aal2',clock_timestamp()+interval '1 hour');
insert into auth.mfa_amr_claims(session_id,authentication_method,updated_at) values
('fa920000-0000-4000-8000-000000000001','totp',date_trunc('second',clock_timestamp())-interval '1 minute');

create temp table managed_fixture as select pg_temp.managed_inspect_fixture(
    'fa000000-0000-4000-8000-000000000001','fa900000-0000-4000-8000-000000000002') tuple;

do $tests$
declare
    tuple jsonb := (select f.tuple from managed_fixture f);
    workspace_uuid uuid := (tuple ->> 'workspace_id')::uuid;
    actor_uuid uuid := 'fa900000-0000-4000-8000-000000000001';
    session_uuid uuid := 'fa920000-0000-4000-8000-000000000001';
    consent_uuid uuid := 'fa930000-0000-4000-8000-000000000001';
    release_uuid uuid := 'fa940000-0000-4000-8000-000000000001';
    claims jsonb;
    changed jsonb;
    request jsonb;
    invalid_request jsonb;
    receipt jsonb;
    second_receipt jsonb;
    response jsonb;
    before_state jsonb;
    context_call text := format('select public.managed_telegram_inspect_context(%L::uuid,%L)',workspace_uuid,repeat('c',40));
    inspect_call text := format('select public.inspect_managed_telegram_delivery_unknown(%L::uuid)',consent_uuid);
    request_hash text;
    key_name text;
    role_name text;
    row_count integer;
    denied boolean;
    subject jsonb;
    historical_state jsonb;
    new_version_uuid uuid;
begin
    claims := jsonb_build_object('role','coineasy_managed_inspector','sub',actor_uuid,'session_id',session_uuid,'aal','aal2',
        'iss','https://abcdefghijklmnopqrst.supabase.co/auth/v1','aud','authenticated','is_anonymous',false,
        'iat',floor(extract(epoch from clock_timestamp()))::bigint,'exp',floor(extract(epoch from clock_timestamp()+interval '1 hour'))::bigint,
        'amr',jsonb_build_array(jsonb_build_object('method','totp','timestamp',
            (select floor(extract(epoch from updated_at))::bigint from auth.mfa_amr_claims where session_id=session_uuid))));
    perform pg_temp.expect_denied(context_call,claims,'default empty release/allowlist');
    insert into private.managed_telegram_inspect_releases(release_id,workspace_id,project_ref,release_sha,migration_sha256,verified_deployment_reference,enabled,expires_at)
      values(release_uuid,workspace_uuid,'abcdefghijklmnopqrst',repeat('c',40),repeat('d',64),'local:test:managed-inspect',true,clock_timestamp()+interval '1 hour');
    perform pg_temp.expect_denied(context_call,claims,'empty allowlist');
    insert into private.managed_telegram_inspect_allowlist(allowlist_id,user_id,workspace_id,operation,approved_by,enabled,expires_at) values
      ('fa950000-0000-4000-8000-000000000001',actor_uuid,workspace_uuid,'consent_inspect','human:separate-approver',true,clock_timestamp()+interval '1 hour'),
      ('fa950000-0000-4000-8000-000000000002',actor_uuid,workspace_uuid,'inspect','human:separate-approver',true,clock_timestamp()+interval '1 hour');
    response := pg_temp.operator_call(context_call,claims);
    perform pg_temp.assert_true(response ->> 'inspected_by'='auth:'||actor_uuid::text and response ->> 'approved_by'='human:separate-approver','derived actors');
    perform pg_temp.assert_true(not(response ? 'session_id') and not(response ? 'auth_fingerprint_sha256'),'context strips auth internals');
    before_state := pg_temp.state_snapshot(workspace_uuid);
    perform pg_temp.operator_call(context_call,claims);
    perform pg_temp.assert_true(before_state=pg_temp.state_snapshot(workspace_uuid),'context no DB writes');

    foreach key_name in array array['role','sub','session_id','aal','iss','aud','is_anonymous','amr','iat','exp'] loop
        perform pg_temp.expect_denied(context_call,claims-key_name,'missing claim '||key_name);
    end loop;
    for changed in select value from jsonb_array_elements(jsonb_build_array(
      jsonb_build_object('role','authenticated'),jsonb_build_object('role','service_role'),jsonb_build_object('role','coineasy_telegram_resolution'),
      jsonb_build_object('sub','fa900000-0000-4000-8000-000000000003'),jsonb_build_object('aal','aal1'),
      jsonb_build_object('iss','https://wrong.invalid/auth/v1'),jsonb_build_object('aud','wrong'),jsonb_build_object('is_anonymous',true),
      jsonb_build_object('session_id','fa920000-0000-4000-8000-000000000002'),
      jsonb_build_object('exp',floor(extract(epoch from clock_timestamp()))::bigint-1),
      jsonb_build_object('iat',floor(extract(epoch from clock_timestamp()))::bigint+60),
      jsonb_build_object('amr',jsonb_build_array(jsonb_build_object('method','password','timestamp',floor(extract(epoch from clock_timestamp()))::bigint))),
      jsonb_build_object('amr',(claims->'amr')||(claims->'amr')),
      jsonb_build_object('amr',jsonb_build_array(jsonb_build_object('method','totp','timestamp',floor(extract(epoch from clock_timestamp()))::bigint)))
    )) loop perform pg_temp.expect_denied(context_call,claims||changed,'claim mismatch'); end loop;

    update auth.users set banned_until=clock_timestamp()+interval '1 hour' where id=actor_uuid;
    perform pg_temp.expect_denied(context_call,claims,'banned user');
    update auth.users set banned_until=null,deleted_at=clock_timestamp() where id=actor_uuid;
    perform pg_temp.expect_denied(context_call,claims,'deleted user');
    update auth.users set deleted_at=null,is_anonymous=true where id=actor_uuid;
    perform pg_temp.expect_denied(context_call,claims,'anonymous DB user');
    update auth.users set is_anonymous=false where id=actor_uuid;
    update auth.users set role='authenticated' where id=actor_uuid;
    perform pg_temp.expect_denied(context_call,claims,'live DB role mismatch');
    update auth.users set role='coineasy_managed_inspector' where id=actor_uuid;
    update auth.sessions set not_after=clock_timestamp()-interval '1 second' where id=session_uuid;
    perform pg_temp.expect_denied(context_call,claims,'expired live session');
    update auth.sessions set not_after=clock_timestamp()+interval '1 hour',aal='aal1' where id=session_uuid;
    perform pg_temp.expect_denied(context_call,claims,'downgraded live session');
    update auth.sessions set aal='aal2',user_id='fa900000-0000-4000-8000-000000000003' where id=session_uuid;
    perform pg_temp.expect_denied(context_call,claims,'session ownership');
    update auth.sessions set user_id=actor_uuid where id=session_uuid;
    update auth.mfa_factors set status='unverified' where user_id=actor_uuid;
    perform pg_temp.expect_denied(context_call,claims,'factor unenrolled/unverified');
    update auth.mfa_factors set status='verified',factor_type='phone' where user_id=actor_uuid;
    perform pg_temp.expect_denied(context_call,claims,'non TOTP factor');
    update auth.mfa_factors set factor_type='totp' where user_id=actor_uuid;
    update auth.mfa_amr_claims set updated_at=updated_at-interval '11 minutes' where session_id=session_uuid;
    changed := jsonb_set(claims,'{amr,0,timestamp}',to_jsonb((select floor(extract(epoch from updated_at))::bigint from auth.mfa_amr_claims where session_id=session_uuid)));
    perform pg_temp.expect_denied(context_call,changed,'stale MFA despite fresh JWT iat');
    update auth.mfa_amr_claims set updated_at=updated_at+interval '11 minutes' where session_id=session_uuid;

    foreach role_name in array array['owner','admin','editor'] loop
      insert into public.workspace_members(workspace_id,user_id,role,status) values(workspace_uuid,actor_uuid,role_name,'active');
      perform pg_temp.expect_denied(context_call,claims,'general role excluded '||role_name);
      delete from public.workspace_members where workspace_id=workspace_uuid and user_id=actor_uuid;
    end loop;

    request := (tuple-'asset_id') || jsonb_build_object('schema_version','telegram-resolution-inspect-request@1',
      'project_ref','abcdefghijklmnopqrst','environment','production','client_id','squid','release_sha',repeat('c',40),
      'resolution_id','fa960000-0000-4000-8000-000000000001','operator_approval_id','fa970000-0000-4000-8000-000000000001',
      'inspected_by','auth:'||actor_uuid::text,'approved_by','human:separate-approver',
      'expires_at',to_char(clock_timestamp()+interval '30 minutes','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
      'public_audit',jsonb_build_object('schema_version','telegram-public-channel-audit@1','scan_source','public_telegram_web_history',
        'public_channel','squid_kor_update','first_message_id','100','last_message_id','102','message_count',3,
        'checked_at',to_char(clock_timestamp(),'YYYY-MM-DD"T"HH24:MI:SS"Z"'),'caption_match_count',0,'png_match_count',0,'snapshot_sha256',repeat('e',64)));
    request_hash := private.managed_telegram_inspect_hash(request);
    for invalid_request in select value from jsonb_array_elements(jsonb_build_array(
      request||jsonb_build_object('extra',false),request-'approved_by',request||jsonb_build_object('inspected_by','attacker'),
      request||jsonb_build_object('approved_by','attacker'),request||jsonb_build_object('project_ref','zzzzzzzzzzzzzzzzzzzz'),
      request||jsonb_build_object('release_sha',repeat('d',40)),request||jsonb_build_object('job_id',request->>'publication_id'),
      request||jsonb_build_object('expires_at',to_char(clock_timestamp()+interval '3 hours','YYYY-MM-DD"T"HH24:MI:SS"Z"')),
      request||jsonb_build_object('expires_at','2026-02-30T00:00:00Z'),
      jsonb_set(request,'{public_audit,message_count}','4'),jsonb_set(request,'{public_audit,message_count}','"3"'),
      jsonb_set(request,'{public_audit,first_message_id}','100'),jsonb_set(request,'{public_audit,last_message_id}','"9223372036854775808"'),
      jsonb_set(request,'{public_audit,caption_match_count}','1'),jsonb_set(request,'{public_audit,png_match_count}','1'),
      jsonb_set(request,'{public_audit,checked_at}',to_jsonb(to_char(clock_timestamp()+interval '10 seconds','YYYY-MM-DD"T"HH24:MI:SS"Z"'))),
      jsonb_set(request,'{public_audit,checked_at}',to_jsonb(to_char(clock_timestamp()-interval '31 minutes','YYYY-MM-DD"T"HH24:MI:SS"Z"')))
    )) loop
      perform pg_temp.expect_denied(format('select public.register_managed_telegram_inspect_consent(%L,%L::jsonb,%L)',consent_uuid,invalid_request,private.managed_telegram_inspect_hash(invalid_request)),claims,'strict request/audit binding');
    end loop;
    perform pg_temp.expect_denied(format('select public.register_managed_telegram_inspect_consent(%L,%L::jsonb,%L)',consent_uuid,request,repeat('0',64)),claims,'request hash mismatch');
    perform pg_temp.expect_denied(format('select public.register_managed_telegram_inspect_consent(%L,%L::jsonb,%L)',request->>'operator_approval_id',request,request_hash),claims,'consent UUID not approval UUID');
    perform pg_temp.assert_true((select count(*)=0 from private.managed_telegram_inspect_consents),'no rejected consent writes');
    receipt := pg_temp.operator_call(format('select public.register_managed_telegram_inspect_consent(%L,%L::jsonb,%L)',consent_uuid,request,request_hash),claims);
    perform pg_temp.assert_true(receipt->'reused'='false'::jsonb and (receipt->>'expires_at')::timestamptz<=(receipt->>'consented_at')::timestamptz+interval '10 minutes','bounded new consent');
    second_receipt := pg_temp.operator_call(format('select public.register_managed_telegram_inspect_consent(%L,%L::jsonb,%L)',consent_uuid,request,request_hash),claims);
    perform pg_temp.assert_true(second_receipt-'reused'=receipt-'reused' and second_receipt->'reused'='true'::jsonb,'idempotent registration no extension');
    before_state := pg_temp.state_snapshot(workspace_uuid);
    response := pg_temp.operator_call(inspect_call,claims);
    perform pg_temp.assert_true(response->'eligible'='true'::jsonb and response->'approved'='false'::jsonb and response->'resolved'='false'::jsonb and response->'reused'='false'::jsonb,'fresh-only response');
    perform pg_temp.assert_true(response->>'approval_subject_sha256'=private.managed_telegram_inspect_hash(response->'approval_subject'),'canonical subject hash');
    perform pg_temp.assert_true(response->'approval_subject'=private.exact_telegram_delivery_resolution_subject(workspace_uuid,(request->>'content_item_id')::uuid,
      (request->>'content_version_id')::uuid,(request->>'publication_id')::uuid,(request->>'job_id')::uuid,(request->>'resolution_id')::uuid,
      (request->>'operator_approval_id')::uuid,request->>'approved_by',(request->>'expires_at')::timestamptz,clock_timestamp(),request->>'release_sha',request->'public_audit'),'legacy subject parity');
    perform pg_temp.assert_true(before_state=pg_temp.state_snapshot(workspace_uuid),'full source/ledger/consent no-write snapshot');
    perform pg_temp.assert_true(pg_temp.operator_call(inspect_call,claims)=response,'inspect repeat permitted, no global once claim');
    perform pg_temp.assert_true(before_state=pg_temp.state_snapshot(workspace_uuid),'repeat inspect no writes');

    invalid_request := request||jsonb_build_object('resolution_id','fa960000-0000-4000-8000-000000000099');
    perform pg_temp.expect_denied(format('select public.register_managed_telegram_inspect_consent(%L,%L::jsonb,%L)',consent_uuid,invalid_request,private.managed_telegram_inspect_hash(invalid_request)),claims,'existing consent cannot be rebound','23505');
    begin
      insert into auth.sessions(id,user_id,factor_id,aal,not_after) values('fa920000-0000-4000-8000-000000000099',actor_uuid,'fa910000-0000-4000-8000-000000000001','aal2',clock_timestamp()+interval '1 hour');
      insert into auth.mfa_amr_claims(session_id,authentication_method,updated_at)
        select 'fa920000-0000-4000-8000-000000000099','totp',updated_at from auth.mfa_amr_claims where session_id=session_uuid;
      changed:=claims||jsonb_build_object('session_id','fa920000-0000-4000-8000-000000000099');
      perform pg_temp.operator_call(context_call,changed);
      perform pg_temp.expect_denied(inspect_call,changed,'another valid session cannot reuse consent','42501');
      raise exception 'rollback second session' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;

    -- Test-only fault injection in a rollback subtransaction: expensive subject
    -- work crosses request expiry. The final real-clock check must reject and
    -- leave no inserted consent, even though entry-time validation succeeded.
    begin
      execute $function$create or replace function private.managed_telegram_inspect_fresh_subject(target_request jsonb)
        returns jsonb language plpgsql security definer set search_path='' set timezone='UTC' as $body$
        begin perform pg_catalog.pg_sleep(1.2); return '{}'::jsonb; end $body$ $function$;
      invalid_request:=request||jsonb_build_object('expires_at',to_char(clock_timestamp()+interval '1 second','YYYY-MM-DD"T"HH24:MI:SS"Z"'));
      perform pg_temp.expect_denied(format('select public.register_managed_telegram_inspect_consent(''fa930000-0000-4000-8000-000000000098'',%L::jsonb,%L)',invalid_request,private.managed_telegram_inspect_hash(invalid_request)),claims,'late request expiry after slow validation','22023');
      perform pg_temp.assert_true(not exists(select 1 from private.managed_telegram_inspect_consents where consent_id='fa930000-0000-4000-8000-000000000098'),'late expiry no consent write');
      raise exception 'restore injected helper' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;

    -- Existing approvals/receipts are never returned as successful fresh
    -- inspection, including an otherwise identical immutable subject.
    subject := response -> 'approval_subject';
    begin
      insert into private.exact_telegram_delivery_unknown_approvals(workspace_id,operator_approval_id,resolution_id,publication_id,job_id,
        content_item_id,content_version_id,approval_subject,approval_subject_sha256,approved_by,expires_at,approved_release_sha)
      values(workspace_uuid,(request->>'operator_approval_id')::uuid,(request->>'resolution_id')::uuid,(request->>'publication_id')::uuid,
        (request->>'job_id')::uuid,(request->>'content_item_id')::uuid,(request->>'content_version_id')::uuid,
        subject,response->>'approval_subject_sha256',request->>'approved_by',(request->>'expires_at')::timestamptz,request->>'release_sha');
      perform pg_temp.expect_denied(inspect_call,claims,'existing approval is not fresh');
      insert into private.exact_telegram_delivery_unknown_resolutions
      select (jsonb_populate_record(null::private.exact_telegram_delivery_unknown_resolutions,subject||jsonb_build_object(
        'approval_subject',subject,'approval_subject_sha256',response->>'approval_subject_sha256',
        'approved_at',clock_timestamp(),'resolved_at',clock_timestamp(),'resolved_by','human:fixture'))).*;
      perform pg_temp.expect_denied(inspect_call,claims,'existing resolution is not fresh');
      raise exception 'rollback old ledger fixtures' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;

    -- Duplicate exact jobs are prevented by the cumulative unique index.
    perform pg_temp.assert_true(exists(select 1 from pg_index i where i.indexrelid='public.jobs_exact_telegram_once_idx'::regclass and i.indisunique and i.indisvalid),'duplicate exact job uniqueness retained');
    begin
      insert into public.publications(workspace_id,client_id,content_item_id,content_version_id,channel,status)
        values(workspace_uuid,'squid',(request->>'content_item_id')::uuid,(request->>'content_version_id')::uuid,'telegram','failed');
      perform pg_temp.expect_denied(inspect_call,claims,'duplicate publication tuple');
      raise exception 'rollback duplicate fixture' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    begin
      insert into public.publications(workspace_id,client_id,content_item_id,content_version_id,channel,status,external_id,external_url,request_payload,response_payload,published_at)
        values(workspace_uuid,'squid',(request->>'content_item_id')::uuid,(request->>'content_version_id')::uuid,'telegram','published','101',
          'https://t.me/squid_kor_update/101',jsonb_build_object('observation','manual_existing_publication','external_publish_performed',false),
          jsonb_build_object('observed',true,'external_publish_performed',false),clock_timestamp());
      perform pg_temp.expect_denied(inspect_call,claims,'canonical positive observation');
      raise exception 'rollback positive fixture' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;
    -- Negative inspection may use a historical immutable version; it must not
    -- silently require changing the current pointer to the pinned old version.
    begin
      new_version_uuid := gen_random_uuid();
      insert into public.content_versions
        select (jsonb_populate_record(null::public.content_versions,to_jsonb(v)||jsonb_build_object('id',new_version_uuid,'version_number',v.version_number+1))).*
        from public.content_versions v where v.id=(request->>'content_version_id')::uuid;
      update public.content_items set current_version_id=new_version_uuid where id=(request->>'content_item_id')::uuid;
      historical_state := pg_temp.state_snapshot(workspace_uuid);
      changed := pg_temp.operator_call(inspect_call,claims);
      perform pg_temp.assert_true(changed->>'content_version_id'=request->>'content_version_id','historical exact immutable version allowed');
      perform pg_temp.assert_true(historical_state=pg_temp.state_snapshot(workspace_uuid),'historical inspect also no writes');
      raise exception 'rollback historical fixture' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;

    -- Reuse must fail after expiry rather than re-register/extend. Owner inserts
    -- a synthetic already expired row; ordinary API roles cannot do this.
    insert into private.managed_telegram_inspect_consents
      select (jsonb_populate_record(null::private.managed_telegram_inspect_consents,to_jsonb(c)||jsonb_build_object(
        'consent_id','fa930000-0000-4000-8000-000000000002','consented_at',clock_timestamp()-interval '10 minutes',
        'expires_at',clock_timestamp()-interval '1 minute'))).*
      from private.managed_telegram_inspect_consents c where c.consent_id=consent_uuid;
    perform pg_temp.expect_denied('select public.inspect_managed_telegram_delivery_unknown(''fa930000-0000-4000-8000-000000000002'')',claims,'expired consent');
    perform pg_temp.expect_denied(format('select public.register_managed_telegram_inspect_consent(''fa930000-0000-4000-8000-000000000002'',%L::jsonb,%L)',request,request_hash),claims,'expired consent cannot extend');
    before_state := pg_temp.state_snapshot(workspace_uuid);

    -- A deleted live session is denied even while its signed JWT would expire
    -- later. The test rollback restores the fixture, not a production session.
    begin
      delete from auth.sessions where id=session_uuid;
      perform pg_temp.expect_denied(inspect_call,claims,'session deletion/logout');
      raise exception 'rollback deleted session fixture' using errcode='ZX001';
    exception when sqlstate 'ZX001' then null; end;

    update auth.users set encrypted_password='different-synthetic-password-state' where id=actor_uuid;
    perform pg_temp.expect_denied(inspect_call,claims,'password/recovery completion invalidates consent');
    update auth.users set encrypted_password='synthetic-password-state',recovery_sent_at=clock_timestamp() where id=actor_uuid;
    perform pg_temp.expect_denied(inspect_call,claims,'recovery request conservatively invalidates consent');
    update auth.users set recovery_sent_at=null where id=actor_uuid;
    insert into auth.mfa_factors(id,user_id,factor_type,status) values(gen_random_uuid(),actor_uuid,'totp','verified');
    perform pg_temp.expect_denied(inspect_call,claims,'MFA reset/enrollment fingerprint');
    delete from auth.mfa_factors where user_id=actor_uuid and id<>'fa910000-0000-4000-8000-000000000001';

    foreach key_name in array array['release','allowlist','consent','user','session'] loop
      begin
        insert into private.managed_telegram_inspect_revocations(revocation_id,target_type,target_id,reason_code)
          values(gen_random_uuid(),key_name,case key_name when 'release' then release_uuid when 'allowlist' then 'fa950000-0000-4000-8000-000000000001'::uuid
            when 'consent' then consent_uuid when 'user' then actor_uuid else session_uuid end,'local_security_test');
        perform pg_temp.expect_denied(inspect_call,claims,'revocation '||key_name);
        raise exception 'rollback synthetic revocation' using errcode='ZX001';
      exception when sqlstate 'ZX001' then null; end;
    end loop;

    foreach key_name in array array['expired','disabled','ambiguous'] loop
      begin
        if key_name<>'ambiguous' then
          insert into private.managed_telegram_inspect_revocations(revocation_id,target_type,target_id,reason_code)
            values(gen_random_uuid(),'release',release_uuid,'local_security_test');
        end if;
        insert into private.managed_telegram_inspect_releases(release_id,workspace_id,project_ref,release_sha,migration_sha256,verified_deployment_reference,enabled,valid_from,expires_at)
          values(gen_random_uuid(),workspace_uuid,'abcdefghijklmnopqrst',repeat('c',40),repeat('d',64),'local:test:alternate',key_name<>'disabled',
            clock_timestamp()-interval '2 hours',case when key_name='expired' then clock_timestamp()-interval '1 hour' else clock_timestamp()+interval '1 hour' end);
        perform pg_temp.expect_denied(inspect_call,claims,'release '||key_name,'42501');
        raise exception 'rollback release fixture' using errcode='ZX001';
      exception when sqlstate 'ZX001' then null; end;
      begin
        if key_name<>'ambiguous' then
          insert into private.managed_telegram_inspect_revocations(revocation_id,target_type,target_id,reason_code)
            values(gen_random_uuid(),'allowlist','fa950000-0000-4000-8000-000000000001','local_security_test');
        end if;
        insert into private.managed_telegram_inspect_allowlist(allowlist_id,user_id,workspace_id,operation,approved_by,enabled,valid_from,expires_at)
          values(gen_random_uuid(),actor_uuid,workspace_uuid,'consent_inspect','human:separate-approver',key_name<>'disabled',
            clock_timestamp()-interval '2 hours',case when key_name='expired' then clock_timestamp()-interval '1 hour' else clock_timestamp()+interval '1 hour' end);
        perform pg_temp.expect_denied(inspect_call,claims,'allowlist '||key_name,'42501');
        raise exception 'rollback allowlist fixture' using errcode='ZX001';
      exception when sqlstate 'ZX001' then null; end;
    end loop;

    -- Actual authenticated private table DML and direct helper invocation denied.
    perform pg_temp.expect_denied('select to_jsonb(x) from private.managed_telegram_inspect_consents x limit 1',claims,'private consent SELECT');
    perform pg_temp.expect_denied(format('delete from private.managed_telegram_inspect_consents where consent_id=%L',consent_uuid),claims,'private consent DELETE');
    perform pg_temp.expect_denied(format('update private.managed_telegram_inspect_allowlist set enabled=true where user_id=%L',actor_uuid),claims,'self allowlist UPDATE');
    perform pg_temp.expect_denied(format('insert into private.managed_telegram_inspect_allowlist(allowlist_id,user_id,workspace_id,operation,approved_by,enabled,expires_at) values(gen_random_uuid(),%L,%L,''inspect'',''attacker'',true,now()+interval ''1 hour'')',actor_uuid,workspace_uuid),claims,'self allowlist INSERT');
    perform pg_temp.expect_denied(format('select private.require_managed_telegram_inspect_identity(%L,%L)',workspace_uuid,repeat('c',40)),claims,'private identity helper');
    -- Workspace-scoped target data remain inaccessible to the dedicated user.
    perform pg_temp.expect_denied(format('select to_jsonb(count(*)) from public.content_items where workspace_id=%L',workspace_uuid),claims,'target read has no table privilege','42501');
    perform pg_temp.expect_denied(format('update public.content_items set title=''not permitted'' where id=%L returning to_jsonb(id)',request->>'content_item_id'),claims,'target update has no table privilege','42501');
    perform pg_temp.assert_true(before_state=pg_temp.state_snapshot(workspace_uuid),'target denied calls no-op');
    perform pg_temp.expect_denied(format('select to_jsonb(public.queue_content_generation(%L,''{}''::jsonb,''local-denial-test''))',request->>'content_item_id'),claims,'target generation denied','42501');
    perform pg_temp.expect_denied(format('select to_jsonb(public.request_content_publication(%L,%L,''telegram'',null,''local-denial-test''))',request->>'content_item_id',request->>'content_version_id'),claims,'target publication denied','42501');
    perform pg_temp.expect_denied(format('select to_jsonb(public.record_approved_figma_link(%L,%L,''synthetic-file'',''1:2'',null,null,''{}''::jsonb))',request->>'content_item_id',request->>'content_version_id'),claims,'target Figma write denied','42501');
    perform pg_temp.expect_denied(format('select public.get_agent_company_dashboard(%L)',workspace_uuid),claims,'dashboard target denied','42501');
    perform pg_temp.expect_denied(format('select public.get_agent_work_order(%L,%L)',workspace_uuid,consent_uuid),claims,'work order target denied','42501');
    perform pg_temp.expect_denied(format('select public.list_agent_operator_inbox(%L,20,null,null)',workspace_uuid),claims,'operator inbox target denied','42501');
    perform pg_temp.expect_denied(format('select public.propose_agent_work_order(%L,''{}''::jsonb,%L)',workspace_uuid,repeat('a',64)),claims,'work order propose target denied','42501');
    perform pg_temp.expect_denied(format('select public.authorize_agent_work_order(%L,%L,%L,0)',workspace_uuid,consent_uuid,repeat('a',64)),claims,'work order authorize target denied','42501');
    perform pg_temp.expect_denied(format('select public.complete_agent_work_order(%L,%L,%L,0)',workspace_uuid,consent_uuid,repeat('a',64)),claims,'work order complete target denied','42501');
    perform pg_temp.expect_denied(format('select public.record_agent_operator_decision(%L,%L,%L,0,''approve'',''local_test'')',workspace_uuid,consent_uuid,repeat('a',64)),claims,'operator decision target denied','42501');

    -- Immutable records remain immutable even to the table owner (revocations
    -- are append-only too); replacement IDs do not rewrite existing consent.
    denied:=false;
    begin update private.managed_telegram_inspect_consents set expires_at=expires_at+interval '1 second' where consent_id=consent_uuid;
    exception when check_violation then denied:=true; end;
    perform pg_temp.assert_true(denied,'owner cannot extend consent');

    -- The dedicated role cannot reach the broad authenticated self-workspace
    -- bootstrap or any general table DML.
    perform pg_temp.expect_denied(format('insert into public.workspaces(id,name,slug,created_by) values(''fa000000-0000-4000-8000-000000000099'',''Denied ambient capability'',''managed-inspect-ambient-test'',%L)',actor_uuid),claims,'dedicated self-workspace denied','42501');
    perform pg_temp.assert_true(not exists(select 1 from public.workspaces where id='fa000000-0000-4000-8000-000000000099'),'denied workspace absent');
    raise notice 'PASS managed inspect dedicated-role claims/ACL/exact consent/hash/freshness/full-row no-write tests';
end $tests$;

rollback;

-- Stale repeatable-read/serializable snapshots must be rejected before identity
-- lookup, not accidentally accepted as a still-live authorization snapshot.
begin isolation level repeatable read;
set local role coineasy_managed_inspector;
do $$
declare denied boolean:=false;
begin
  begin perform public.managed_telegram_inspect_context('fa000000-0000-4000-8000-000000000001',repeat('c',40));
  exception when sqlstate '25001' then denied:=true; end;
  if not denied then raise exception 'repeatable read unexpectedly allowed'; end if;
end $$;
rollback;
begin isolation level serializable;
set local role coineasy_managed_inspector;
do $$
declare denied boolean:=false;
begin
  begin perform public.managed_telegram_inspect_context('fa000000-0000-4000-8000-000000000001',repeat('c',40));
  exception when sqlstate '25001' then denied:=true; end;
  if not denied then raise exception 'serializable unexpectedly allowed'; end if;
end $$;
rollback;
