-- READ-ONLY PRE-APPLY CHECK. Catalog reads in a read-only transaction;
-- no migration, grant, row write, provider call or message dispatch.
-- This checks prerequisites for the proposed button-card pack, not its hosted
-- PostgreSQL-version compatibility or authorization to apply it.
begin transaction read only;

do $$
declare
    relation_name text;
    function_name text;
    field_spec text;
begin
    if current_setting('transaction_read_only') <> 'on' then
        raise exception 'button_card_preapply_not_read_only';
    end if;
    foreach relation_name in array array[
        'private.content_ops_review_outbox', 'public.content_items',
        'public.content_versions', 'public.assets', 'public.source_items',
        'public.content_source_links', 'public.workspace_clients',
        'public.approvals', 'public.publications', 'storage.objects'
    ] loop
        if to_regclass(relation_name) is null then
            raise exception 'button_card_preapply_relation_missing';
        end if;
    end loop;
    foreach field_spec in array array[
        'private.content_ops_review_outbox|outbox_id|uuid',
        'private.content_ops_review_outbox|workspace_id|uuid',
        'private.content_ops_review_outbox|client_id|text',
        'private.content_ops_review_outbox|content_item_id|uuid',
        'private.content_ops_review_outbox|content_version_id|uuid',
        'private.content_ops_review_outbox|banner_asset_id|uuid',
        'private.content_ops_review_outbox|banner_sha256|text',
        'private.content_ops_review_outbox|status|text',
        'private.content_ops_review_outbox|claim_token|uuid',
        'private.content_ops_review_outbox|lease_expires_at|timestamptz',
        'private.content_ops_review_outbox|send_started_at|timestamptz',
        'private.content_ops_review_outbox|packet_sha256|text',
        'private.content_ops_review_outbox|message_id|bigint',
        'private.content_ops_review_outbox|finished_at|timestamptz',
        'public.assets|id|uuid',
        'public.assets|workspace_id|uuid',
        'public.assets|content_item_id|uuid',
        'public.assets|content_version_id|uuid',
        'public.assets|storage_bucket|text',
        'public.assets|storage_path|text',
        'public.assets|metadata|jsonb',
        'public.assets|sha256|text',
        'public.assets|byte_size|bigint',
        'public.assets|width|integer',
        'public.assets|height|integer',
        'storage.objects|bucket_id|text',
        'storage.objects|name|text'
    ] loop
        if not exists (
            select 1 from pg_catalog.pg_attribute a
            where a.attrelid = to_regclass(split_part(field_spec,'|',1))
              and a.attname = split_part(field_spec,'|',2)
              and a.atttypid = to_regtype(split_part(field_spec,'|',3))
              and a.attnum > 0 and not a.attisdropped
        ) then
            raise exception 'button_card_preapply_column_mismatch';
        end if;
    end loop;
    -- Pin the checked-in producer-binding correction and the remaining base
    -- functions independently replayed from migrations. The 2026-09-23 hosted
    -- readback still had the older candidate body; reject that drift rather
    -- than accepting a signature-only match.
    foreach field_spec in array array[
        'private.content_ops_review_candidate(uuid,uuid,uuid)|jsonb|v|true|491114d72507197cb680794cc04d5755950f257f34574de4c88d0fe15f7a541d',
        'private.content_ops_review_matches(jsonb,private.content_ops_review_outbox)|boolean|s|false|cd6398f50fc9207a305c886aebe27ffc2a913d2f751c55e60a7659cdef9f27c2',
        'public.content_ops_reconcile_daily(uuid,uuid)|integer|v|true|8d8c2e5a1419b82867a52fed82d159b4edd77663b8d0a5e76e246f229115a4cf',
        'public.content_ops_claim_review(uuid,uuid,uuid)|jsonb|v|true|87e5737bac993dec7e9f840c28ca5f87c8d251e6f5f469aa2c783a5ae6b0fa77',
        'public.content_ops_begin_review_send(uuid,uuid,uuid,text,uuid)|jsonb|v|true|1dcc1fb48328070aae3ec11a32ce8b49ca45d6ba00160115e96985526469a1c2',
        'public.content_ops_finish_review_send(uuid,uuid,uuid,text,bigint,uuid)|jsonb|v|true|38c899357208bd1edae896a728420a979762929bdc2124e54b1b85c46619e52e'
    ] loop
        function_name:=split_part(field_spec,'|',1);
        if to_regprocedure(function_name) is null or not exists (
            select 1 from pg_catalog.pg_proc p
            where p.oid=to_regprocedure(function_name)
              and p.prorettype=to_regtype(split_part(field_spec,'|',2))
              and p.provolatile=split_part(field_spec,'|',3)::char
              and p.prosecdef=split_part(field_spec,'|',4)::boolean
              and pg_catalog.pg_get_userbyid(p.proowner)='postgres'
              and coalesce(p.proconfig @> array['search_path=""']::text[],false)
              and encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')=
                  split_part(field_spec,'|',5)
        ) then
            raise exception 'button_card_preapply_function_contract_mismatch';
        end if;
    end loop;
    foreach function_name in array array[
        'public.content_ops_reconcile_daily(uuid,uuid)',
        'public.content_ops_claim_review(uuid,uuid,uuid)',
        'public.content_ops_begin_review_send(uuid,uuid,uuid,text,uuid)',
        'public.content_ops_finish_review_send(uuid,uuid,uuid,text,bigint,uuid)'
    ] loop
        if has_function_privilege('anon',function_name,'EXECUTE')
           or has_function_privilege('authenticated',function_name,'EXECUTE')
           or not has_function_privilege('service_role',function_name,'EXECUTE') then
            raise exception 'button_card_preapply_base_rpc_acl';
        end if;
    end loop;
    if has_table_privilege('anon','private.content_ops_review_outbox',
           'SELECT,INSERT,UPDATE,DELETE')
       or has_table_privilege('authenticated','private.content_ops_review_outbox',
           'SELECT,INSERT,UPDATE,DELETE')
       or has_table_privilege('service_role','private.content_ops_review_outbox',
           'SELECT,INSERT,UPDATE,DELETE') then
        raise exception 'button_card_preapply_base_table_acl';
    end if;
    if not exists (
        select 1 from pg_catalog.pg_class c
        where c.oid = to_regclass('private.content_ops_review_outbox')
          and c.relrowsecurity and c.relforcerowsecurity
    ) or to_regclass('private.content_ops_button_card_send_attempts') is not null
      or to_regclass('private.content_ops_button_card_outbox_owners') is not null
      or to_regprocedure('public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)') is not null then
        raise exception 'button_card_preapply_state_conflict';
    end if;
end $$;

select jsonb_build_object('button_card_preapply','pass','read_only',true,
    'provider_calls',0,'production_changes',0);
commit;
