-- LOCAL READ-ONLY PRE-APPLY CHECK. Run against a fresh hosted catalog before
-- considering content_ops_button_prompt_runtime_capability.sql. No row reads,
-- role grants, migration, provider I/O, Telegram call, or activation.
-- Passing is necessary catalog evidence, not hosted runtime/E2E proof.
begin transaction read only;

do $$
declare
    relation_name text;
    field_spec text;
    actor_table text;
    function_name text;
    principal_oid oid := to_regclass('private.content_ops_review_principals');
    role_oid oid := (select oid from pg_roles where rolname='coineasy_private_review');
begin
    if current_setting('transaction_read_only') <> 'on' then
        raise exception 'prompt_preapply_not_read_only';
    end if;
    if role_oid is null or principal_oid is null or not exists (
        select 1 from pg_roles r where r.oid=role_oid and r.rolcanlogin
          and not (r.rolsuper or r.rolcreaterole or r.rolcreatedb
                   or r.rolreplication or r.rolbypassrls)
    ) or exists (select 1 from pg_auth_members where member=role_oid) then
        raise exception 'prompt_preapply_role_or_principal_missing';
    end if;
    foreach relation_name in array array[
        'private.content_ops_review_principals',
        'private.content_ops_button_reviews',
        'private.content_ops_button_cards',
        'private.content_ops_button_actions',
        'private.content_ops_button_identities',
        'private.content_ops_button_reviewers',
        'private.content_ops_button_prompt_attempts',
        'private.content_ops_button_prompt_receipts',
        'private.content_ops_button_edit_prompts',
        'public.content_items', 'public.workspace_clients'
    ] loop
        if to_regclass(relation_name) is null then
            raise exception 'prompt_preapply_relation_missing';
        end if;
    end loop;
    foreach field_spec in array array[
        'private.content_ops_review_principals|id|uuid',
        'private.content_ops_review_principals|workspace_id|uuid',
        'private.content_ops_review_principals|bot_binding|text',
        'private.content_ops_review_principals|human_binding|text',
        'private.content_ops_button_cards|id|uuid',
        'private.content_ops_button_cards|review_id|uuid',
        'private.content_ops_button_cards|bindings|jsonb',
        'private.content_ops_button_cards|parts|jsonb',
        'private.content_ops_button_cards|active|boolean',
        'private.content_ops_button_cards|delivered_at|timestamptz',
        'private.content_ops_button_prompt_attempts|id|uuid',
        'private.content_ops_button_prompt_attempts|card_id|uuid',
        'private.content_ops_button_prompt_attempts|review_id|uuid',
        'private.content_ops_button_prompt_attempts|actor_id|uuid',
        'private.content_ops_button_prompt_attempts|epoch|bigint',
        'private.content_ops_button_prompt_attempts|human_binding|text',
        'private.content_ops_button_prompt_attempts|edit_action_key|text',
        'private.content_ops_button_prompt_attempts|started_at|timestamptz',
        'private.content_ops_button_prompt_attempts|expires_at|timestamptz',
        'private.content_ops_button_prompt_receipts|id|uuid',
        'private.content_ops_button_prompt_receipts|review_id|uuid',
        'private.content_ops_button_prompt_receipts|actor_id|uuid',
        'private.content_ops_button_prompt_receipts|message_binding|text',
        'private.content_ops_button_prompt_receipts|receipt_sha256|text',
        'private.content_ops_button_prompt_receipts|outcome|text',
        'private.content_ops_button_prompt_receipts|delivered_at|timestamptz',
        'private.content_ops_button_prompt_receipts|recorded_at|timestamptz',
        'private.content_ops_button_prompt_receipts|reservation_expires_at|timestamptz',
        'private.content_ops_button_edit_prompts|id|uuid',
        'private.content_ops_button_edit_prompts|actor_id|uuid',
        'private.content_ops_button_edit_prompts|owner_receipt_id|uuid'
    ] loop
        if not exists (
            select 1 from pg_attribute a
            where a.attrelid=to_regclass(split_part(field_spec,'|',1))
              and a.attname=split_part(field_spec,'|',2)
              and a.atttypid=to_regtype(split_part(field_spec,'|',3))
              and a.attnum>0 and not a.attisdropped
        ) then
            raise exception 'prompt_preapply_column_mismatch';
        end if;
    end loop;
    -- The hosted signup-free migration rebounded all eight actor lineages.
    foreach actor_table in array array[
        'content_ops_banner_requests', 'content_ops_button_actions',
        'content_ops_button_checks', 'content_ops_button_edit_prompts',
        'content_ops_button_identities', 'content_ops_button_prompt_attempts',
        'content_ops_button_prompt_receipts', 'content_ops_button_reviewers'
    ] loop
        if not exists (
            select 1 from pg_constraint c
            where c.conrelid=format('private.%I',actor_table)::regclass
              and c.confrelid=principal_oid and c.contype='f'
              and cardinality(c.conkey)=1
              and c.conkey[1]=(select a.attnum from pg_attribute a
                  where a.attrelid=c.conrelid and a.attname='actor_id')
              and cardinality(c.confkey)=1
              and c.confkey[1]=(select a.attnum from pg_attribute a
                  where a.attrelid=principal_oid and a.attname='id')
        ) or exists (
            select 1 from pg_constraint c
            where c.conrelid=format('private.%I',actor_table)::regclass
              and c.confrelid=to_regclass('auth.users') and c.contype='f'
        ) then
            raise exception 'prompt_preapply_principal_fk_mismatch';
        end if;
    end loop;
    if not exists (
        select 1 from pg_constraint c
        where c.conrelid='private.content_ops_button_identities'::regclass
          and c.confrelid=principal_oid and c.contype='f'
          and c.conkey=array[
              (select attnum from pg_attribute where attrelid=c.conrelid and attname='workspace_id'),
              (select attnum from pg_attribute where attrelid=c.conrelid and attname='bot_binding'),
              (select attnum from pg_attribute where attrelid=c.conrelid and attname='human_binding'),
              (select attnum from pg_attribute where attrelid=c.conrelid and attname='actor_id')
          ]::smallint[]
          and c.confkey=array[
              (select attnum from pg_attribute where attrelid=principal_oid and attname='workspace_id'),
              (select attnum from pg_attribute where attrelid=principal_oid and attname='bot_binding'),
              (select attnum from pg_attribute where attrelid=principal_oid and attname='human_binding'),
              (select attnum from pg_attribute where attrelid=principal_oid and attname='id')
          ]::smallint[]
    ) or not exists (
        select 1 from pg_constraint c
        where c.conrelid='private.content_ops_button_reviewers'::regclass
          and c.confrelid=principal_oid and c.contype='f'
          and c.conkey=array[
              (select attnum from pg_attribute where attrelid=c.conrelid and attname='workspace_id'),
              (select attnum from pg_attribute where attrelid=c.conrelid and attname='actor_id')
          ]::smallint[]
          and c.confkey=array[
              (select attnum from pg_attribute where attrelid=principal_oid and attname='workspace_id'),
              (select attnum from pg_attribute where attrelid=principal_oid and attname='id')
          ]::smallint[]
    ) then
        raise exception 'prompt_preapply_principal_scope_missing';
    end if;
    if not exists (select 1 from pg_class c where c.oid=principal_oid
            and c.relrowsecurity and c.relforcerowsecurity)
       or not exists (select 1 from pg_class c
            where c.oid='private.content_ops_button_prompt_attempts'::regclass
              and c.relrowsecurity and c.relforcerowsecurity) then
        raise exception 'prompt_preapply_principal_rls_mismatch';
    end if;
    -- Bodies must match the 2026-09-23 hosted schema-only readback. A later
    -- change requires a fresh review, not a signature-only compatibility claim.
    foreach field_spec in array array[
        'private.reserve_content_ops_button_prompt_attempt(uuid,uuid,uuid,text,text)|76064f55cd646b839873e20db5799201691473286f00729c5989be3cbec6eab9',
        'private.register_content_ops_button_edit_prompt(uuid,text)|f71b9d2ac48ed83c467eedbd8fcb2a2bf629439ec05b401828558af45093bcf1'
    ] loop
        function_name:=split_part(field_spec,'|',1);
        if to_regprocedure(function_name) is null or not exists (
            select 1 from pg_proc p where p.oid=to_regprocedure(function_name)
              and p.prorettype='jsonb'::regtype and p.provolatile='v'
              and not p.prosecdef and pg_get_userbyid(p.proowner)='postgres'
              and coalesce(p.proconfig @> array['search_path=""']::text[],false)
              and encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')=
                  split_part(field_spec,'|',2)
        ) then
            raise exception 'prompt_preapply_base_function_mismatch';
        end if;
        if has_function_privilege('coineasy_private_review',function_name,'EXECUTE')
           or has_function_privilege('anon',function_name,'EXECUTE')
           or has_function_privilege('authenticated',function_name,'EXECUTE')
           or has_function_privilege('service_role',function_name,'EXECUTE') then
            raise exception 'prompt_preapply_base_function_acl';
        end if;
    end loop;
    if not has_schema_privilege('coineasy_private_review','private','USAGE')
       or not has_table_privilege('coineasy_private_review',
            'private.content_ops_button_prompt_attempts','SELECT')
       or not has_table_privilege('coineasy_private_review',
            'private.content_ops_button_prompt_receipts','SELECT')
       or not has_table_privilege('coineasy_private_review',
            'private.content_ops_button_edit_prompts','SELECT') then
        raise exception 'prompt_preapply_runtime_read_acl';
    end if;
    foreach relation_name in array array[
        'private.content_ops_button_prompt_attempts',
        'private.content_ops_button_prompt_receipts',
        'private.content_ops_button_edit_prompts'
    ] loop
        if has_table_privilege('coineasy_private_review',relation_name,'INSERT')
           or not exists (select 1 from pg_class c
               where c.oid=to_regclass(relation_name) and c.relrowsecurity)
           or not exists (select 1 from pg_policies p
               where p.schemaname='private'
                 and p.tablename=split_part(relation_name,'.',2)
                 and p.cmd='INSERT' and p.permissive='RESTRICTIVE'
                 and 'coineasy_private_review'=any(p.roles)
                 and p.with_check='false') then
            raise exception 'prompt_preapply_insert_boundary';
        end if;
    end loop;
    if exists (select 1 from pg_proc p
        where p.pronamespace='private'::regnamespace
          and p.proname in (
            'reserve_content_ops_button_prompt_for_runtime',
            'register_content_ops_button_prompt_response_for_runtime')) then
        raise exception 'prompt_preapply_partial_install';
    end if;
end $$;

select jsonb_build_object('prompt_preapply','pass','read_only',true,
    'hosted_runtime_verified',false,'provider_calls',0,'changes',0);
commit;
