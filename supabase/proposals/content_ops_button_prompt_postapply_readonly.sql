-- READ-ONLY POST-APPLY CATALOG CHECK for the proposed two-function runtime
-- capability. A pass is necessary but does not prove an enabled bot login,
-- row-level policy, a Telegram receipt, or authority to apply this proposal.
begin transaction read only;

do $$
declare
    spec text;
    function_name text;
    expected_hash text;
    relation_name text;
begin
    if current_setting('transaction_read_only') <> 'on' then
        raise exception 'prompt_postapply_not_read_only';
    end if;
    foreach spec in array array[
        'private.reserve_content_ops_button_prompt_for_runtime(uuid,uuid,uuid,text,text)|6aceb31a624492732f97372ebac5ec5b02a870222adb16d268d7abcc017f9083',
        'private.register_content_ops_button_prompt_response_for_runtime(uuid,text,text,text,text,timestamptz)|8b7b5f25116556c401ef3d957f13701b43297e3eb3b5175daa181a3108134a1f'
    ] loop
        function_name:=split_part(spec,'|',1);
        expected_hash:=split_part(spec,'|',2);
        if to_regprocedure(function_name) is null or not exists (
            select 1 from pg_catalog.pg_proc p
            where p.oid=to_regprocedure(function_name)
              and p.prorettype='jsonb'::regtype
              and p.provolatile='v' and p.prosecdef
              and pg_catalog.pg_get_userbyid(p.proowner)='postgres'
              and coalesce(p.proconfig @> array['search_path=""']::text[],false)
              and encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')=expected_hash
        ) then
            raise exception 'prompt_postapply_function_contract_mismatch';
        end if;
        if not has_function_privilege('coineasy_private_review',function_name,'EXECUTE')
           or has_function_privilege('anon',function_name,'EXECUTE')
           or has_function_privilege('authenticated',function_name,'EXECUTE')
           or has_function_privilege('service_role',function_name,'EXECUTE') then
            raise exception 'prompt_postapply_wrapper_acl_mismatch';
        end if;
    end loop;
    foreach function_name in array array[
        'private.reserve_content_ops_button_prompt_attempt(uuid,uuid,uuid,text,text)',
        'private.register_content_ops_button_edit_prompt(uuid,text)'
    ] loop
        if to_regprocedure(function_name) is null
           or has_function_privilege('coineasy_private_review',function_name,'EXECUTE') then
            raise exception 'prompt_postapply_underlying_acl_mismatch';
        end if;
    end loop;
    foreach relation_name in array array[
        'private.content_ops_button_prompt_attempts',
        'private.content_ops_button_prompt_receipts',
        'private.content_ops_button_edit_prompts'
    ] loop
        if to_regclass(relation_name) is null
           or has_table_privilege('coineasy_private_review',relation_name,'INSERT')
           or not exists (select 1 from pg_catalog.pg_class c
               where c.oid=to_regclass(relation_name)
                 and c.relrowsecurity)
           or not exists (select 1 from pg_catalog.pg_policies p
               where p.schemaname='private'
                 and p.tablename=split_part(relation_name,'.',2)
                 and p.cmd='INSERT' and p.permissive='RESTRICTIVE'
                 and 'coineasy_private_review'=any(p.roles)
                 and p.with_check='false') then
            raise exception 'prompt_postapply_table_boundary_mismatch';
        end if;
    end loop;
end $$;

select jsonb_build_object(
    'prompt_runtime_postapply','pass',
    'catalog_only',true,'hosted_runtime_verified',false,
    'read_only',true,'changes',0,'provider_calls',0);
commit;
