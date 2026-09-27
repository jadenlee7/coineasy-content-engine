-- READ-ONLY FOUNDATION CHECK for the uninstalled button-card send ledger.
-- Pins the already-hosted private review/card functions the proposal calls.
-- A pass is necessary catalog evidence, not permission to apply or send.
begin transaction read only;

do $$
declare
    field_spec text;
    function_spec text;
    function_name text;
begin
    if current_setting('transaction_read_only') <> 'on' then
        raise exception 'button_card_foundation_not_read_only';
    end if;
    if to_regclass('private.content_ops_button_reviews') is null
       or to_regclass('private.content_ops_button_cards') is null then
        raise exception 'button_card_foundation_relation_missing';
    end if;
    foreach field_spec in array array[
        'private.content_ops_button_reviews|id|uuid',
        'private.content_ops_button_reviews|workspace_id|uuid',
        'private.content_ops_button_reviews|client_id|text',
        'private.content_ops_button_reviews|content_item_id|uuid',
        'private.content_ops_button_reviews|content_version_id|uuid',
        'private.content_ops_button_reviews|version_fingerprint|text',
        'private.content_ops_button_reviews|epoch|bigint',
        'private.content_ops_button_reviews|state|text',
        'private.content_ops_button_reviews|expires_at|timestamptz',
        'private.content_ops_button_cards|id|uuid',
        'private.content_ops_button_cards|review_id|uuid',
        'private.content_ops_button_cards|epoch|bigint',
        'private.content_ops_button_cards|version_fingerprint|text',
        'private.content_ops_button_cards|bindings|jsonb',
        'private.content_ops_button_cards|parts|jsonb',
        'private.content_ops_button_cards|delivered_at|timestamptz',
        'private.content_ops_button_cards|expires_at|timestamptz',
        'private.content_ops_button_cards|active|boolean'
    ] loop
        if not exists (
            select 1 from pg_catalog.pg_attribute a
            where a.attrelid=to_regclass(split_part(field_spec,'|',1))
              and a.attname=split_part(field_spec,'|',2)
              and a.atttypid=to_regtype(split_part(field_spec,'|',3))
              and a.attnum>0 and not a.attisdropped
        ) then
            raise exception 'button_card_foundation_column_mismatch';
        end if;
    end loop;
    -- These hashes are from the checked-in foundation proposals and the
    -- read-only hosted function inventory on 2026-09-25. Drift fails closed.
    foreach function_spec in array array[
        'private.content_ops_button_version_fingerprint(uuid,uuid,uuid)|text|s|false|3e0ef935f1f4bcb53a4574bf83afcbff47b93c71407e2b98efd554c4d1ec4076',
        'private.record_content_ops_button_card(uuid,uuid,text,bigint,jsonb,jsonb,timestamptz,timestamptz)|jsonb|v|false|0fe4b8da47e089d1c16d3dfde079ff8e20be84d27fe1bc22dbc59975da477e17'
    ] loop
        function_name:=split_part(function_spec,'|',1);
        if to_regprocedure(function_name) is null or not exists (
            select 1 from pg_catalog.pg_proc p
            where p.oid=to_regprocedure(function_name)
              and p.prorettype=to_regtype(split_part(function_spec,'|',2))
              and p.provolatile=split_part(function_spec,'|',3)::char
              and p.prosecdef=split_part(function_spec,'|',4)::boolean
              and pg_catalog.pg_get_userbyid(p.proowner)='postgres'
              and coalesce(p.proconfig @> array['search_path=""']::text[],false)
              and encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')=
                  split_part(function_spec,'|',5)
        ) then
            raise exception 'button_card_foundation_function_mismatch';
        end if;
        if has_function_privilege('anon',function_name,'EXECUTE')
           or has_function_privilege('authenticated',function_name,'EXECUTE')
           or has_function_privilege('service_role',function_name,'EXECUTE') then
            raise exception 'button_card_foundation_function_acl';
        end if;
    end loop;
    if not exists (
        select 1 from pg_catalog.pg_class c
        where c.oid=to_regclass('private.content_ops_button_reviews')
          and c.relrowsecurity
    ) or not exists (
        select 1 from pg_catalog.pg_class c
        where c.oid=to_regclass('private.content_ops_button_cards')
          and c.relrowsecurity and c.relforcerowsecurity
    ) or has_table_privilege('anon','private.content_ops_button_reviews',
            'SELECT,INSERT,UPDATE,DELETE')
      or has_table_privilege('authenticated','private.content_ops_button_reviews',
            'SELECT,INSERT,UPDATE,DELETE')
      or has_table_privilege('service_role','private.content_ops_button_reviews',
            'SELECT,INSERT,UPDATE,DELETE')
      or has_table_privilege('anon','private.content_ops_button_cards',
            'SELECT,INSERT,UPDATE,DELETE')
      or has_table_privilege('authenticated','private.content_ops_button_cards',
            'SELECT,INSERT,UPDATE,DELETE')
      or has_table_privilege('service_role','private.content_ops_button_cards',
            'SELECT,INSERT,UPDATE,DELETE') then
        raise exception 'button_card_foundation_table_acl_or_rls';
    end if;
    if to_regclass('private.content_ops_button_card_send_attempts') is not null
       or to_regclass('private.content_ops_button_card_outbox_owners') is not null
       or to_regprocedure('public.content_ops_button_card_image_locator(uuid,uuid,uuid,uuid)') is not null
       or to_regprocedure('public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)') is not null then
        raise exception 'button_card_foundation_partial_installation';
    end if;
end $$;

select jsonb_build_object('button_card_foundation_preapply','pass',
    'read_only',true,'changes',0,'provider_calls',0,
    'hosted_runtime_verified',false);
commit;
