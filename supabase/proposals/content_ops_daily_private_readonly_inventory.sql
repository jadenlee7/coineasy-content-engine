-- Catalog-only inventory. Run via an existing authorized read-only SQL owner.
-- Compare source_sha256 and ACLs to the disposable expected inventory; missing
-- objects are observations, NOT permission to replay/apply historical SQL.
begin transaction read only;
with required(signature) as (values
    ('private.content_ops_review_candidate(uuid,uuid,uuid)'),
    ('public.content_ops_reconcile_daily(uuid,uuid)'),
    ('public.content_ops_claim_review(uuid,uuid,uuid)'),
    ('public.content_ops_begin_review_send(uuid,uuid,uuid,text,uuid)'),
    ('private.content_ops_button_version_fingerprint(uuid,uuid,uuid)'),
    ('private.record_content_ops_button_card(uuid,uuid,text,bigint,jsonb,jsonb,timestamptz,timestamptz)'),
    ('private.prepare_content_ops_button_review_from_claim(uuid,uuid,uuid,uuid,uuid)'),
    ('private.reserve_content_ops_button_card_send(uuid,uuid,smallint,text)'),
    ('private.confirm_content_ops_button_card_send(uuid,uuid,smallint,text,bigint,text,text,timestamptz)'),
    ('public.content_ops_button_card_image_locator(uuid,uuid,uuid,uuid)'),
    ('public.content_ops_button_card_owner_step(uuid,uuid,text,jsonb)'),
    ('private.content_ops_ybo_quality_version(uuid,uuid)'),
    ('public.content_ops_reconcile_daily_ybo(uuid)'),
    ('public.content_ops_claim_daily_ybo(uuid,uuid)'),
    ('public.get_quality_banner_generation_context(uuid,text,uuid)')
), functions as (
    select signature, p.oid is not null as installed,
        encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') as source_sha256,
        p.prosecdef as security_definer, p.provolatile::text as volatility,
        pg_catalog.pg_get_userbyid(p.proowner) as owner,
        p.proconfig as config,
        case when p.oid is not null then has_function_privilege('anon',p.oid,'EXECUTE') end as anon_execute,
        case when p.oid is not null then has_function_privilege('authenticated',p.oid,'EXECUTE') end as authenticated_execute,
        case when p.oid is not null then has_function_privilege('service_role',p.oid,'EXECUTE') end as service_role_execute
    from required left join pg_catalog.pg_proc p on p.oid=to_regprocedure(signature)
), required_tables(name) as (values
    ('private.content_ops_review_outbox'),('private.content_ops_button_reviews'),
    ('private.content_ops_button_cards'),('private.content_ops_button_card_send_attempts'),
    ('private.content_ops_button_card_outbox_owners')
), tables as (
    select name,c.oid is not null as installed,c.relrowsecurity as rls,c.relforcerowsecurity as force_rls,
        case when c.oid is not null then has_table_privilege('anon',c.oid,'SELECT,INSERT,UPDATE,DELETE') end as anon_direct,
        case when c.oid is not null then has_table_privilege('authenticated',c.oid,'SELECT,INSERT,UPDATE,DELETE') end as authenticated_direct,
        case when c.oid is not null then has_table_privilege('service_role',c.oid,'SELECT,INSERT,UPDATE,DELETE') end as service_role_direct
    from required_tables left join pg_catalog.pg_class c on c.oid=to_regclass(name)
)
select jsonb_build_object('schema','daily-private-catalog-inventory@1','read_only',true,
    'server_version_num',current_setting('server_version_num'),
    'functions',(select jsonb_agg(to_jsonb(functions) order by signature) from functions),
    'tables',(select jsonb_agg(to_jsonb(tables) order by name) from tables),
    'production_ready',false,'writes',0);
commit;
