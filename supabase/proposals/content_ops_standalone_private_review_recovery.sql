-- OFF successor only: admit the exact preserved standalone Babylon tuple.
-- No grant insertion, old outbox reset, trial mutation, generation or send.
-- This is not replayable. Register a fresh exact grant separately after readiness.
begin;
set local lock_timeout='5s';
set local statement_timeout='60s';
set local timezone='UTC';

create temporary table recovery_successor_expected(name text, body_sha256 text) on commit drop;
insert into recovery_successor_expected values
 ('private.content_ops_recovery_json_canonical(jsonb)','3370f5653da6e69a546244fd959d59c56b69ce37640e6da578256256fed28eb4'),
 ('private.content_ops_recovery_json_sha256(jsonb)','dddc425a3880620743619c2959e8d53f3780b7eca5e5abe90c913207aa93530b'),
 ('private.guard_content_ops_recovery_immutable()','6bb9db424481c6b4351abf45c032e49ca7744ef04a5a597512bb31f37d59ec2b'),
 ('private.guard_content_ops_recovery_grant()','1a0ef57f39d6e2900bfecdbf45e76b40db2d300393ee1350a61744e71e293cbe'),
 ('private.content_ops_recovery_candidate(uuid)','8d303ff2fa2e510bebb10f4b84d6545b9a76e7be6539b3cc8ed6ef0397e01eff'),
 ('private.content_ops_button_card_outbox_owned(uuid)','b72fb26f8aae6219d809b33be7e5cd32a91a911017e821950a338c064878a52a'),
 ('private.content_ops_recovery_image_locator(uuid)','6ff51ffb8286a4f5a19462fa4c2a98917f3201e53a79b1a014a6bba63b7a93de'),
 ('private.content_ops_recovery_terminal(uuid,uuid,uuid,uuid)','c13d9d9712e48952efe9860350715e3ddd48f9a21ae3c476d538e0e0d9028b9c'),
 ('public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)','6fb50974b75010ed66bc99d17647766dfa2c394084a9405c0b26f3c838726c77');

-- Block concurrent registration; exact protected outboxes/anchors are read-locked.
lock table private.content_ops_review_recovery_grants in access exclusive mode;
select 1 from private.content_ops_review_outbox where outbox_id in
 ('94f31aa1-34dd-4220-9c88-34716e760c47','e82406d9-dc09-4367-883c-95dc2f6d47b5') for share;
select 1 from private.ybo_standalone_babylon_trial_anchor for share;
select 1 from private.ybo_readiness_babylon_trial_anchor for share;

do $preflight$
declare n text;
begin
 if exists(select 1 from recovery_successor_expected e left join pg_proc p on p.oid=to_regprocedure(e.name)
   where p.oid is null or encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>e.body_sha256) then
   raise exception 'recovery_successor_preimage_mismatch'; end if;
 if exists(select 1 from recovery_successor_expected e join pg_proc p on p.oid=to_regprocedure(e.name)
   where pg_get_userbyid(p.proowner)<>'postgres' or p.proconfig is distinct from array['search_path=""']
    or p.prosecdef is distinct from (e.name like 'public.%')
    or (e.name like 'private.%' and (has_function_privilege('anon',p.oid,'EXECUTE')
     or has_function_privilege('authenticated',p.oid,'EXECUTE')
     or has_function_privilege('service_role',p.oid,'EXECUTE')))) then
   raise exception 'recovery_successor_function_acl_mismatch'; end if;
 if (select count(*) from private.content_ops_review_outbox q where
   (q.outbox_id,q.content_version_id) in
   (('94f31aa1-34dd-4220-9c88-34716e760c47'::uuid,'43e68255-9797-451d-909f-0dce460a6c18'::uuid),
    ('e82406d9-dc09-4367-883c-95dc2f6d47b5'::uuid,'2732080a-91d4-4666-bcdf-c21a60faff17'::uuid))
   and q.workspace_id='5cc143cc-b253-4841-8a6c-ab3ac98623a6'::uuid and q.client_id='babylon'
   and q.status='claimed' and q.lease_expires_at<clock_timestamp()
   and q.packet_sha256 is null and q.send_started_at is null
   and q.message_id is null and q.finished_at is null)<>2
   or exists(select 1 from private.content_ops_button_reviews where content_version_id in
     ('43e68255-9797-451d-909f-0dce460a6c18','2732080a-91d4-4666-bcdf-c21a60faff17'))
   or exists(select 1 from private.content_ops_button_card_outbox_owners where outbox_id in
     ('94f31aa1-34dd-4220-9c88-34716e760c47','e82406d9-dc09-4367-883c-95dc2f6d47b5')) then
   raise exception 'recovery_successor_no_send_preflight_failed'; end if;
 if not exists(select 1 from private.ybo_standalone_babylon_trial_anchor a
   where a.workspace_id='5cc143cc-b253-4841-8a6c-ab3ac98623a6'::uuid
    and a.trial_id='7b27e277-0641-4aef-b739-761f86547287'::uuid
    and a.plan_sha256='247339a8b6064f3a82589e3d01f7b88275fcc300be9d4b28ffbe826a62271642'
    and a.release_sha='cb6ac7ff9c0669da277068148a0dc1da24f8c39c'
    and a.not_before='2026-10-16T20:23:58Z'::timestamptz
    and a.deadline='2026-10-16T22:23:58Z'::timestamptz and a.kst_date='2026-10-17'::date) then
   raise exception 'recovery_successor_original_anchor_mismatch'; end if;
 foreach n in array array['grants','claims','prepared','begins','terminals'] loop
   if (xpath('/row/n/text()',query_to_xml(format('select count(*) n from private.content_ops_review_recovery_%I',n),false,true,'')))[1]::text::bigint<>0
    or has_table_privilege('anon','private.content_ops_review_recovery_'||n,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    or has_table_privilege('authenticated','private.content_ops_review_recovery_'||n,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    or has_table_privilege('service_role','private.content_ops_review_recovery_'||n,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    or not (select relrowsecurity from pg_class where oid=('private.content_ops_review_recovery_'||n)::regclass) then
    raise exception 'recovery_successor_empty_off_acl_required'; end if;
 end loop;
 if has_function_privilege('anon','public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)','EXECUTE')
  or has_function_privilege('authenticated','public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)','EXECUTE')
  or not has_function_privilege('service_role','public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)','EXECUTE') then
  raise exception 'recovery_successor_rpc_acl_mismatch'; end if;
end $preflight$;

create temporary table recovery_successor_before on commit drop as select
 (select jsonb_agg(to_jsonb(p)-'prosrc' order by e.name) from recovery_successor_expected e
  join pg_proc p on p.oid=to_regprocedure(e.name)) functions,
 (select jsonb_agg(jsonb_build_object('oid',c.oid,'owner',c.relowner,'acl',c.relacl,
  'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity) order by c.oid) from pg_class c
  where c.oid in(select ('private.content_ops_review_recovery_'||n)::regclass
   from unnest(array['grants','claims','prepared','begins','terminals']) n)) tables,
 (select jsonb_agg(to_jsonb(t) order by t.oid) from pg_trigger t where not t.tgisinternal
  and t.tgrelid='private.content_ops_review_recovery_grants'::regclass) triggers,
 (select jsonb_agg(to_jsonb(c) order by c.oid) from pg_constraint c
  where c.conrelid='private.content_ops_review_recovery_grants'::regclass
   and pg_get_constraintdef(c.oid) not in
    ('CHECK ((outbox_id = ''94f31aa1-34dd-4220-9c88-34716e760c47''::uuid))',
     'CHECK ((content_version_id = ''43e68255-9797-451d-909f-0dce460a6c18''::uuid))')) other_constraints,
 (select jsonb_agg(to_jsonb(q) order by q.outbox_id) from private.content_ops_review_outbox q where q.outbox_id in
  ('94f31aa1-34dd-4220-9c88-34716e760c47','e82406d9-dc09-4367-883c-95dc2f6d47b5')) outboxes,
 (select jsonb_agg(to_jsonb(a) order by a.trial_id) from private.ybo_standalone_babylon_trial_anchor a) standalone_anchor,
 (select jsonb_agg(to_jsonb(a) order by a.trial_id) from private.ybo_readiness_babylon_trial_anchor a) readiness_anchor;
create temporary table recovery_successor_guard_hash(sha256 text) on commit drop;

do $change$
declare old_outbox text; old_version text; original_body text; successor_body text; definition text;
 old_anchor text := $old$
    select * into original_anchor from private.ybo_readiness_babylon_trial_anchor
        where workspace_id = new.workspace_id and trial_id =
            'c34dbb8a-ff7c-484e-8e43-757f692ec743'::uuid;
    if not found or new.plan->>'original_trial_id' is distinct from original_anchor.trial_id::text
       or new.plan->>'original_plan_sha256' is distinct from original_anchor.plan_sha256 then
        raise exception 'content_ops_recovery_anchor_invalid' using errcode='23514';
    end if;$old$;
 new_anchor text := $new$
    if new.outbox_id='e82406d9-dc09-4367-883c-95dc2f6d47b5'::uuid then
        if new.content_version_id<>'2732080a-91d4-4666-bcdf-c21a60faff17'::uuid
           or new.plan->>'content_item_id' is distinct from '57d6d430-8825-5a40-a4b3-9241eab6f8af'
           or new.plan->>'source_item_id' is distinct from '33b312ba-b80c-4b5e-975a-a8630b6b4601'
           or new.plan->>'generate_job_id' is distinct from 'ce42b75b-8938-49a5-a1c2-dbe758e96544'
           or new.plan->>'banner_sha256' is distinct from '1427fd53385787ebcfddd7131a539ee2963e20f9f979a4e881c8c19aa1059544'
           or new.plan->>'telegram_copy_sha256' is distinct from 'b98236484311660575a046b72cef6cc8ba7850e3690512536b10b3c50f83b2fe'
           or new.plan->>'x_copy_sha256' is distinct from '81229be211e10dd035f46d2102fb4371727a2cac0a60c5b43162caac83c67691'
           or new.plan->>'original_trial_id' is distinct from '7b27e277-0641-4aef-b739-761f86547287'
           or new.plan->>'original_plan_sha256' is distinct from '247339a8b6064f3a82589e3d01f7b88275fcc300be9d4b28ffbe826a62271642'
           or not exists(select 1 from private.ybo_standalone_babylon_trial_anchor a
               where a.workspace_id=new.workspace_id and a.trial_id='7b27e277-0641-4aef-b739-761f86547287'::uuid
                 and a.plan_sha256=new.plan->>'original_plan_sha256'
                 and a.release_sha='cb6ac7ff9c0669da277068148a0dc1da24f8c39c'
                 and a.not_before='2026-10-16T20:23:58Z'::timestamptz
                 and a.deadline='2026-10-16T22:23:58Z'::timestamptz and a.kst_date='2026-10-17'::date) then
            raise exception 'content_ops_recovery_standalone_scope_invalid' using errcode='23514';
        end if;
    else$new$;
begin
 -- Discover the two predecessor constraints by exact definitions, never guessed names.
 select min(conname::text) into old_outbox from pg_constraint
  where conrelid='private.content_ops_review_recovery_grants'::regclass and contype='c'
   and pg_get_constraintdef(oid)='CHECK ((outbox_id = ''94f31aa1-34dd-4220-9c88-34716e760c47''::uuid))'
  having count(*)=1;
 select min(conname::text) into old_version from pg_constraint
  where conrelid='private.content_ops_review_recovery_grants'::regclass and contype='c'
   and pg_get_constraintdef(oid)='CHECK ((content_version_id = ''43e68255-9797-451d-909f-0dce460a6c18''::uuid))'
  having count(*)=1;
 if old_outbox is null or old_version is null then raise exception 'recovery_successor_constraint_preimage_mismatch'; end if;
 execute format('alter table private.content_ops_review_recovery_grants drop constraint %I, drop constraint %I',old_outbox,old_version);
 alter table private.content_ops_review_recovery_grants add constraint content_ops_recovery_exact_tuple_check check
  ((outbox_id='94f31aa1-34dd-4220-9c88-34716e760c47'::uuid and content_version_id='43e68255-9797-451d-909f-0dce460a6c18'::uuid)
   or (outbox_id='e82406d9-dc09-4367-883c-95dc2f6d47b5'::uuid and content_version_id='2732080a-91d4-4666-bcdf-c21a60faff17'::uuid));
 select prosrc,pg_get_functiondef(oid) into original_body,definition from pg_proc
  where oid='private.guard_content_ops_recovery_grant()'::regprocedure;
 if length(original_body)-length(replace(original_body,old_anchor,''))<>length(old_anchor) then
  raise exception 'recovery_successor_anchor_preimage_mismatch'; end if;
 successor_body:=replace(original_body,old_anchor,new_anchor||old_anchor||E'\n    end if;');
 execute replace(definition,original_body,successor_body);
 insert into recovery_successor_guard_hash values(encode(sha256(convert_to(successor_body,'UTF8')),'hex'));
end $change$;

do $postflight$
declare before recovery_successor_before; n text;
begin
 select * into before from recovery_successor_before;
 if before.functions is distinct from (select jsonb_agg(to_jsonb(p)-'prosrc' order by e.name)
  from recovery_successor_expected e join pg_proc p on p.oid=to_regprocedure(e.name))
  or exists(select 1 from recovery_successor_expected e join pg_proc p on p.oid=to_regprocedure(e.name)
   where e.name<>'private.guard_content_ops_recovery_grant()' and encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>e.body_sha256)
  or (select sha256 from recovery_successor_guard_hash) is distinct from (select encode(sha256(convert_to(prosrc,'UTF8')),'hex')
   from pg_proc where oid='private.guard_content_ops_recovery_grant()'::regprocedure)
  or before.tables is distinct from (select jsonb_agg(jsonb_build_object('oid',c.oid,'owner',c.relowner,'acl',c.relacl,
   'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity) order by c.oid) from pg_class c
   where c.oid in(select ('private.content_ops_review_recovery_'||names.part)::regclass
    from unnest(array['grants','claims','prepared','begins','terminals']) as names(part)))
  or before.triggers is distinct from (select jsonb_agg(to_jsonb(t) order by t.oid) from pg_trigger t where not t.tgisinternal
   and t.tgrelid='private.content_ops_review_recovery_grants'::regclass)
  or before.other_constraints is distinct from (select jsonb_agg(to_jsonb(c) order by c.oid) from pg_constraint c
   where c.conrelid='private.content_ops_review_recovery_grants'::regclass and c.conname<>'content_ops_recovery_exact_tuple_check')
  or (select count(*) from pg_constraint where conrelid='private.content_ops_review_recovery_grants'::regclass
   and conname='content_ops_recovery_exact_tuple_check' and contype='c' and convalidated)<>1
  or before.outboxes is distinct from (select jsonb_agg(to_jsonb(q) order by q.outbox_id) from private.content_ops_review_outbox q where q.outbox_id in
   ('94f31aa1-34dd-4220-9c88-34716e760c47','e82406d9-dc09-4367-883c-95dc2f6d47b5'))
  or before.standalone_anchor is distinct from (select jsonb_agg(to_jsonb(a) order by a.trial_id) from private.ybo_standalone_babylon_trial_anchor a)
  or before.readiness_anchor is distinct from (select jsonb_agg(to_jsonb(a) order by a.trial_id) from private.ybo_readiness_babylon_trial_anchor a) then
  raise exception 'recovery_successor_preservation_failed'; end if;
 foreach n in array array['grants','claims','prepared','begins','terminals'] loop
  if (xpath('/row/n/text()',query_to_xml(format('select count(*) n from private.content_ops_review_recovery_%I',n),false,true,'')))[1]::text::bigint<>0 then
   raise exception 'recovery_successor_not_empty_off'; end if;
 end loop;
end $postflight$;
commit;
