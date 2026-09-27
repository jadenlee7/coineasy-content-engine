-- LOCAL VALIDATION PACK ONLY. Safe to run after the button-card proposals are
-- applied to a disposable database. Do not treat this as permission to apply
-- them to production or to send a Telegram card. No row contents are emitted.
begin transaction read only;

do $$
declare
    relation_name text;
    function_name text;
    role_name text;
    exposed_function text;
begin
    if current_setting('transaction_read_only') <> 'on' then
        raise exception 'button_card_preflight_not_read_only';
    end if;

    foreach relation_name in array array[
        'private.content_ops_review_outbox',
        'private.content_ops_button_reviews',
        'private.content_ops_button_cards',
        'private.content_ops_button_card_outbox_owners',
        'private.content_ops_button_card_send_attempts'
    ] loop
        if to_regclass(relation_name) is null then
            raise exception 'button_card_preflight_relation_missing';
        end if;
    end loop;
    foreach relation_name in array array[
        'private.content_ops_button_card_outbox_owners',
        'private.content_ops_button_card_send_attempts'
    ] loop
        if not exists (
            select 1 from pg_catalog.pg_class c
            where c.oid = to_regclass(relation_name)
              and c.relrowsecurity and c.relforcerowsecurity
        ) then
            raise exception 'button_card_preflight_rls_missing';
        end if;
    end loop;

    foreach function_name in array array[
        'private.prepare_content_ops_button_review_from_claim(uuid,uuid,uuid,uuid,uuid)',
        'private.bind_content_ops_button_card_outbox(uuid,uuid,uuid,text)',
        'private.content_ops_button_card_outbox_owned(uuid)',
        'private.reserve_content_ops_button_card_send(uuid,uuid,smallint,text)',
        'private.confirm_content_ops_button_card_send(uuid,uuid,smallint,text,bigint,text,text,timestamptz)',
        'private.register_content_ops_button_card_from_sends(uuid,uuid,text,bigint,jsonb,jsonb,text,jsonb,timestamptz,timestamptz)',
        'private.read_content_ops_button_card_terminal(uuid,uuid,uuid)',
        'public.content_ops_button_card_image_locator(uuid,uuid,uuid,uuid)',
        'public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)'
    ] loop
        if to_regprocedure(function_name) is null then
            raise exception 'button_card_preflight_function_missing';
        end if;
    end loop;

    foreach role_name in array array['anon', 'authenticated', 'service_role'] loop
        if has_table_privilege(role_name,
            'private.content_ops_button_card_outbox_owners',
            'SELECT,INSERT,UPDATE,DELETE')
           or has_table_privilege(role_name,
            'private.content_ops_button_card_send_attempts',
            'SELECT,INSERT,UPDATE,DELETE') then
            raise exception 'button_card_preflight_private_table_acl';
        end if;
        foreach function_name in array array[
            'private.prepare_content_ops_button_review_from_claim(uuid,uuid,uuid,uuid,uuid)',
            'private.bind_content_ops_button_card_outbox(uuid,uuid,uuid,text)',
            'private.content_ops_button_card_outbox_owned(uuid)',
            'private.reserve_content_ops_button_card_send(uuid,uuid,smallint,text)',
            'private.confirm_content_ops_button_card_send(uuid,uuid,smallint,text,bigint,text,text,timestamptz)',
            'private.register_content_ops_button_card_from_sends(uuid,uuid,text,bigint,jsonb,jsonb,text,jsonb,timestamptz,timestamptz)',
            'private.read_content_ops_button_card_terminal(uuid,uuid,uuid)'
        ] loop
            if has_function_privilege(role_name, function_name, 'EXECUTE') then
                raise exception 'button_card_preflight_private_function_acl';
            end if;
        end loop;
    end loop;

    foreach exposed_function in array array[
        'public.content_ops_button_card_image_locator(uuid,uuid,uuid,uuid)',
        'public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)'
    ] loop
        if has_function_privilege('anon', exposed_function, 'EXECUTE')
           or has_function_privilege('authenticated', exposed_function, 'EXECUTE')
           or not has_function_privilege('service_role', exposed_function, 'EXECUTE')
           or not exists (
               select 1 from pg_catalog.pg_proc p
               where p.oid = to_regprocedure(exposed_function)
                 and p.prosecdef
                 and 'search_path=""' = any(p.proconfig)
           ) then
            raise exception 'button_card_preflight_public_function_boundary';
        end if;
    end loop;
end $$;

select jsonb_build_object('button_card_post_apply_acl','pass',
    'read_only',true,'provider_calls',0,'production_changes',0);
commit;
