-- READ-ONLY PARTIAL CHECK. This deliberately excludes the known stale
-- producer-binding helper; a pass is NOT button-card readiness.
begin transaction read only;

do $$
declare function_name text;
begin
    if current_setting('transaction_read_only') <> 'on' then
        raise exception 'button_card_remaining_not_read_only';
    end if;
    foreach function_name in array array[
        'public.content_ops_reconcile_daily(uuid,uuid)',
        'public.content_ops_claim_review(uuid,uuid,uuid)',
        'public.content_ops_begin_review_send(uuid,uuid,uuid,text,uuid)',
        'public.content_ops_finish_review_send(uuid,uuid,uuid,text,bigint,uuid)'
    ] loop
        if to_regprocedure(function_name) is null
           or has_function_privilege('anon',function_name,'EXECUTE')
           or has_function_privilege('authenticated',function_name,'EXECUTE')
           or not has_function_privilege('service_role',function_name,'EXECUTE') then
            raise exception 'button_card_remaining_base_rpc_acl_mismatch';
        end if;
    end loop;
    if to_regclass('private.content_ops_review_outbox') is null
       or has_table_privilege('anon','private.content_ops_review_outbox',
           'SELECT,INSERT,UPDATE,DELETE')
       or has_table_privilege('authenticated','private.content_ops_review_outbox',
           'SELECT,INSERT,UPDATE,DELETE')
       or has_table_privilege('service_role','private.content_ops_review_outbox',
           'SELECT,INSERT,UPDATE,DELETE') then
        raise exception 'button_card_remaining_base_table_acl_mismatch';
    end if;
    if not exists (
        select 1 from pg_catalog.pg_class c
        where c.oid=to_regclass('private.content_ops_review_outbox')
          and c.relrowsecurity and c.relforcerowsecurity
    ) then
        raise exception 'button_card_remaining_base_rls_mismatch';
    end if;
    if to_regclass('private.content_ops_button_card_send_attempts') is not null
       or to_regclass('private.content_ops_button_card_outbox_owners') is not null
       or to_regprocedure('public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)') is not null then
        raise exception 'button_card_remaining_partial_installation';
    end if;
end $$;

select jsonb_build_object(
    'remaining_preapply_contract','pass',
    'producer_binding_checked',false,
    'overall_ready',false,
    'read_only',true,'changes',0,'provider_calls',0);
commit;
