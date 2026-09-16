-- Owner-run ACL/definition smoke after applying migrations to an isolated test
-- database only. Not executed by offline static tests; no fixture can persist.
begin;
do $test$
declare
    rpc text;
    role_name text;
    definition record;
begin
    if not exists (
        select 1 from pg_catalog.pg_class
        where oid = 'private.content_ops_review_outbox'::regclass
          and relrowsecurity and relforcerowsecurity
    ) then raise exception 'content_ops_outbox_rls_missing'; end if;
    if exists (
        select 1 from pg_catalog.pg_class as relation,
            lateral pg_catalog.aclexplode(coalesce(relation.relacl,
                pg_catalog.acldefault('r', relation.relowner))) as privilege
        where relation.oid = 'private.content_ops_review_outbox'::regclass
          and privilege.grantee = 0
    ) then raise exception 'content_ops_public_table_acl'; end if;
    foreach role_name in array array['anon', 'authenticated', 'service_role'] loop
        if has_table_privilege(role_name, 'private.content_ops_review_outbox',
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') then
            raise exception 'content_ops_direct_table_acl';
        end if;
    end loop;
    foreach rpc in array array[
        'public.content_ops_reconcile_daily(uuid,uuid)',
        'public.content_ops_claim_review(uuid,uuid,uuid)',
        'public.content_ops_begin_review_send(uuid,uuid,uuid,text,uuid)',
        'public.content_ops_finish_review_send(uuid,uuid,uuid,text,bigint,uuid)'
    ] loop
        select procedure.prosecdef, procedure.proconfig, procedure.proacl, procedure.proowner
        into definition from pg_catalog.pg_proc as procedure
        where procedure.oid = to_regprocedure(rpc);
        if not found or not definition.prosecdef
           or not coalesce(definition.proconfig @> array['search_path=""'], false)
           or not has_function_privilege('service_role', rpc, 'EXECUTE')
           or has_function_privilege('anon', rpc, 'EXECUTE')
           or has_function_privilege('authenticated', rpc, 'EXECUTE') then
            raise exception 'content_ops_rpc_acl_or_search_path';
        end if;
        if exists (select 1 from pg_catalog.aclexplode(coalesce(definition.proacl,
            pg_catalog.acldefault('f', definition.proowner))) as privilege
            where privilege.grantee = 0 and privilege.privilege_type = 'EXECUTE') then
            raise exception 'content_ops_public_rpc_execute';
        end if;
    end loop;
    if exists (select 1 from pg_catalog.pg_trigger
        where tgrelid = 'private.content_ops_review_outbox'::regclass and not tgisinternal) then
        raise exception 'content_ops_automatic_trigger_not_allowed';
    end if;
    -- Even an owner invocation cannot bypass the RPC's explicit caller-role guard.
    perform set_config('request.jwt.claim.role', 'anon', true);
    begin
        perform public.content_ops_reconcile_daily(gen_random_uuid());
        raise exception 'content_ops_nonservice_role_accepted';
    exception when insufficient_privilege then
        if sqlerrm <> 'content_ops_service_role_required' then raise; end if;
    end;
end;
$test$;

-- Behavioral checks are enabled only in the explicitly synthetic bootstrap.
do $runtime$
declare
    workspace uuid;
    token uuid;
    card jsonb;
    result jsonb;
    keys integer;
    condition text;
    client_count integer;
begin
    if to_regprocedure('private.test_content_ops_seed()') is null then
        raise notice 'Synthetic behavioral fixtures skipped: bootstrap helper absent';
        return;
    end if;
    perform set_config('request.jwt.claim.role', 'service_role', true);
    workspace := private.test_content_ops_seed();
    if public.content_ops_reconcile_daily(workspace) <> 4
       or public.content_ops_reconcile_daily(workspace) <> 0 then
        raise exception 'daily reconciliation is not four-client idempotent';
    end if;
    select count(distinct client_id) into client_count from private.content_ops_review_outbox
    where workspace_id = workspace;
    if client_count <> 4 then raise exception 'four-client coverage missing'; end if;
    token := gen_random_uuid();
    card := public.content_ops_claim_review(workspace,token);
    select count(*) into keys from jsonb_object_keys(card);
    if keys <> 14 or card ? 'banner_asset_id' or card ? 'destination_role'
       or card ->> 'claim_token' <> token::text then raise exception 'claim projection invalid'; end if;
    if public.content_ops_claim_review(workspace,token) is not null then
        raise exception 'claim token replay produced a second card';
    end if;
    result := public.content_ops_begin_review_send(workspace,(card->>'outbox_id')::uuid,gen_random_uuid(),repeat('b',64));
    if result->>'accepted' <> 'false' then raise exception 'wrong owner accepted'; end if;
    result := public.content_ops_begin_review_send(workspace,(card->>'outbox_id')::uuid,token,repeat('b',64));
    if result->>'accepted' <> 'true' then raise exception 'first begin denied'; end if;
    result := public.content_ops_begin_review_send(workspace,(card->>'outbox_id')::uuid,token,repeat('b',64));
    if result->>'accepted' <> 'false' then raise exception 'begin replay accepted'; end if;
    result := public.content_ops_finish_review_send(workspace,(card->>'outbox_id')::uuid,token,'sent',123);
    if result->>'accepted' <> 'true' then raise exception 'sent not recorded'; end if;
    result := public.content_ops_finish_review_send(workspace,(card->>'outbox_id')::uuid,token,'sent',123);
    if result->>'accepted' <> 'true' then raise exception 'exact finish replay rejected'; end if;
    result := public.content_ops_finish_review_send(workspace,(card->>'outbox_id')::uuid,token,'sent',124);
    if result->>'accepted' <> 'false' then raise exception 'message receipt changed'; end if;
    result := public.content_ops_finish_review_send(workspace,(card->>'outbox_id')::uuid,token,'rejected',null);
    if result->>'accepted' <> 'false' then raise exception 'terminal outcome changed'; end if;

    token := gen_random_uuid(); card := public.content_ops_claim_review(workspace,token);
    update private.content_ops_review_outbox
    set claimed_at = clock_timestamp() - interval '3 minutes', lease_expires_at = clock_timestamp() - interval '1 minute'
    where outbox_id = (card->>'outbox_id')::uuid;
    result := public.content_ops_begin_review_send(workspace,(card->>'outbox_id')::uuid,token,repeat('b',64));
    if result->>'status' <> 'delivery_unknown' or result->>'accepted' <> 'false' then
        raise exception 'expired claim not quarantined';
    end if;
    token := gen_random_uuid(); card := public.content_ops_claim_review(workspace,token);
    result := public.content_ops_begin_review_send(workspace,(card->>'outbox_id')::uuid,token,repeat('b',64));
    update private.content_ops_review_outbox
    set claimed_at = clock_timestamp() - interval '3 minutes', lease_expires_at = clock_timestamp() - interval '1 minute'
    where outbox_id = (card->>'outbox_id')::uuid;
    result := public.content_ops_finish_review_send(workspace,(card->>'outbox_id')::uuid,token,'sent',456);
    if result->>'status' <> 'delivery_unknown' or result->>'accepted' <> 'false' then
        raise exception 'expired sending accepted late receipt';
    end if;
    token := gen_random_uuid(); card := public.content_ops_claim_review(workspace,token);
    update public.content_items set status='approved' where id=(card->>'content_item_id')::uuid;
    result := public.content_ops_begin_review_send(workspace,(card->>'outbox_id')::uuid,token,repeat('b',64));
    if result->>'status' <> 'obsolete' or result->>'accepted' <> 'false' then
        raise exception 'begin failed to revalidate eligibility';
    end if;
    if public.content_ops_claim_review(workspace,gen_random_uuid()) is not null then
        raise exception 'terminal work was reclaimed';
    end if;

    -- Each disqualifier excludes exactly one client; other clients still queue.
    foreach condition in array array['mock','stale_feed','stale_source','future_source','wrong_handle',
        'not_latest','job_failed','wrong_day','wrong_workflow','manual_job','missing_png','bad_hash',
        'missing_storage','approved','publication','inactive','duplicate_primary','superseded'] loop
        workspace := private.test_content_ops_seed();
        case condition
        when 'mock' then update public.content_versions set generation_meta=generation_meta||'{"mock_mode":true}'
            where workspace_id=workspace and content_item_id in(select id from public.content_items where workspace_id=workspace and client_id='yellow');
        when 'stale_feed' then update public.source_feeds set last_polled_at=statement_timestamp()-interval '16 minutes' where workspace_id=workspace and client_id='yellow';
        when 'stale_source' then update public.source_items set published_at=statement_timestamp()-interval '25 hours' where workspace_id=workspace and client_id='yellow';
        when 'future_source' then update public.source_items set published_at=statement_timestamp()+interval '1 hour' where workspace_id=workspace and client_id='yellow';
        when 'wrong_handle' then update public.source_items set author_handle='@other' where workspace_id=workspace and client_id='yellow';
        when 'not_latest' then insert into public.source_items
            select gen_random_uuid(),workspace_id,client_id,source_feed_id,'tweet',author_handle,
                'https://x.com/Yellow/status/987654321','987654321',statement_timestamp(),'Synthetic newer',repeat('c',64)
            from public.source_items where workspace_id=workspace and client_id='yellow';
        when 'job_failed' then update public.jobs set status='failed' where workspace_id=workspace and client_id='yellow';
        when 'wrong_day' then update public.jobs set input=input||jsonb_build_object('kst_date','2000-01-01') where workspace_id=workspace and client_id='yellow';
        when 'wrong_workflow' then update public.jobs set input=input||jsonb_build_object('workflow','manual') where workspace_id=workspace and client_id='yellow';
        when 'manual_job' then update public.jobs set input=input||'{"manual_only":true}' where workspace_id=workspace and client_id='yellow';
        when 'missing_png' then update public.assets set asset_kind='svg' where workspace_id=workspace and content_item_id in(select id from public.content_items where workspace_id=workspace and client_id='yellow');
        when 'bad_hash' then update public.assets set sha256=null where workspace_id=workspace and content_item_id in(select id from public.content_items where workspace_id=workspace and client_id='yellow');
        when 'missing_storage' then delete from storage.objects where name like workspace::text||'/yellow/%';
        when 'approved' then insert into public.approvals select workspace,id from public.content_items where workspace_id=workspace and client_id='yellow';
        when 'publication' then insert into public.publications select workspace,id from public.content_items where workspace_id=workspace and client_id='yellow';
        when 'inactive' then update public.workspace_clients set active=false where workspace_id=workspace and client_id='yellow';
        when 'duplicate_primary' then insert into public.content_source_links
            select link.workspace_id,link.client_id,link.content_item_id,gen_random_uuid(),0
            from public.content_source_links as link where link.workspace_id=workspace and link.client_id='yellow';
        when 'superseded' then update public.content_items set current_version_id=gen_random_uuid() where workspace_id=workspace and client_id='yellow';
        end case;
        if public.content_ops_reconcile_daily(workspace) <> 3 then
            raise exception 'candidate exclusion failed: %',condition;
        end if;
    end loop;
    raise notice 'Synthetic runtime: four-client idempotency, strict projection, one-shot transitions, unknown quarantine, and 18 eligibility exclusions passed';
end;
$runtime$;

do $canary$
declare
    workspace uuid;
    exact_version uuid;
    missing_version uuid := gen_random_uuid();
    token uuid := gen_random_uuid();
    other_token uuid;
    other_outbox uuid;
    card jsonb;
    result jsonb;
    before_rows jsonb;
    after_rows jsonb;
    operation text;
    enqueued integer;
    persisted integer;
begin
    if to_regprocedure('private.test_content_ops_seed()') is null then
        raise notice 'Synthetic canary fixtures skipped: bootstrap helper absent';
        return;
    end if;
    perform set_config('request.jwt.claim.role', 'service_role', true);
    workspace := private.test_content_ops_seed();
    select current_version_id into exact_version from public.content_items
    where workspace_id = workspace and client_id = 'babylon';
    if public.content_ops_reconcile_daily(workspace, missing_version) <> 0
       or public.content_ops_claim_review(workspace, gen_random_uuid(), missing_version) is not null
       or exists (select 1 from private.content_ops_review_outbox where workspace_id = workspace) then
        raise exception 'zero-match canary enqueued or claimed arbitrary content';
    end if;
    enqueued := public.content_ops_reconcile_daily(workspace, exact_version);
    select count(*) into persisted from private.content_ops_review_outbox where workspace_id = workspace;
    if enqueued <> 1 or persisted <> 1 then
        raise exception 'canary one-version enqueue invalid';
    end if;
    if public.content_ops_reconcile_daily(workspace, null) <> 3 then
        raise exception 'canary one-version enqueue or daily default scope invalid';
    end if;
    -- Unrelated rows intentionally qualify for cleanup but the canary must
    -- preserve every field of all three pending/claimed/sending rows.
    update private.content_ops_review_outbox set kst_date = kst_date - 1
    where workspace_id = workspace and client_id = 'yellow';
    update private.content_ops_review_outbox set status = 'claimed', claim_token = gen_random_uuid(),
        claimed_at = clock_timestamp() - interval '3 minutes', lease_expires_at = clock_timestamp() - interval '1 minute'
    where workspace_id = workspace and client_id = 'origintrail';
    update private.content_ops_review_outbox set status = 'sending', claim_token = gen_random_uuid(),
        claimed_at = clock_timestamp() - interval '3 minutes', lease_expires_at = clock_timestamp() - interval '1 minute',
        send_started_at = clock_timestamp() - interval '2 minutes', packet_sha256 = repeat('c',64)
    where workspace_id = workspace and client_id = 'squid';
    select jsonb_agg(to_jsonb(queued) order by queued.outbox_id) into before_rows
    from private.content_ops_review_outbox as queued
    where queued.workspace_id = workspace and queued.content_version_id <> exact_version;

    foreach operation in array array['reconcile','zero_claim','foreign_token','wrong_begin','wrong_finish',
        'claim','begin','finish','repeated_reconcile','repeated_claim','repeated_begin','repeated_finish'] loop
        case operation
        when 'reconcile' then
            if public.content_ops_reconcile_daily(workspace, exact_version) <> 0 then
                raise exception 'canary reconcile duplicated'; end if;
        when 'zero_claim' then
            if public.content_ops_claim_review(workspace,gen_random_uuid(),missing_version) is not null then
                raise exception 'zero-match canary claimed arbitrary row'; end if;
        when 'foreign_token' then
            select claim_token into other_token from private.content_ops_review_outbox
            where workspace_id=workspace and client_id='origintrail';
            if public.content_ops_claim_review(workspace,other_token,exact_version) is not null then
                raise exception 'unrelated claim token reused'; end if;
        when 'wrong_begin' then
            select claim_token,outbox_id into other_token,other_outbox from private.content_ops_review_outbox
            where workspace_id=workspace and client_id='origintrail';
            result := public.content_ops_begin_review_send(workspace,other_outbox,other_token,repeat('d',64),exact_version);
            if result->>'accepted' <> 'false' or result->>'status' <> 'not_owned' then
                raise exception 'canary begin touched different version'; end if;
        when 'wrong_finish' then
            select claim_token,outbox_id into other_token,other_outbox from private.content_ops_review_outbox
            where workspace_id=workspace and client_id='squid';
            result := public.content_ops_finish_review_send(workspace,other_outbox,other_token,'sent',123,exact_version);
            if result->>'accepted' <> 'false' or result->>'status' <> 'not_owned' then
                raise exception 'canary finish touched different version'; end if;
        when 'claim' then
            card := public.content_ops_claim_review(workspace,token,exact_version);
            if card is null or card->>'content_version_id' <> exact_version::text
               or (select count(*) from jsonb_object_keys(card)) <> 14 then
                raise exception 'canary did not claim exact version'; end if;
        when 'begin' then
            result := public.content_ops_begin_review_send(workspace,(card->>'outbox_id')::uuid,token,repeat('d',64),exact_version);
            if result->>'accepted' <> 'true' then raise exception 'canary begin denied'; end if;
        when 'finish' then
            result := public.content_ops_finish_review_send(workspace,(card->>'outbox_id')::uuid,token,'sent',789,exact_version);
            if result->>'accepted' <> 'true' then raise exception 'canary finish denied'; end if;
        when 'repeated_reconcile' then
            if public.content_ops_reconcile_daily(workspace,exact_version) <> 0 then
                raise exception 'completed canary re-enqueued'; end if;
        when 'repeated_claim' then
            if public.content_ops_claim_review(workspace,gen_random_uuid(),exact_version) is not null
               or public.content_ops_claim_review(workspace,token,exact_version) is not null then
                raise exception 'completed canary reclaimed'; end if;
        when 'repeated_begin' then
            result := public.content_ops_begin_review_send(workspace,(card->>'outbox_id')::uuid,token,repeat('d',64),exact_version);
            if result->>'accepted' <> 'false' then raise exception 'completed canary resend allowed'; end if;
        when 'repeated_finish' then
            result := public.content_ops_finish_review_send(workspace,(card->>'outbox_id')::uuid,token,'sent',789,exact_version);
            if result->>'accepted' <> 'true' then raise exception 'exact canary receipt replay denied'; end if;
        end case;
        select jsonb_agg(to_jsonb(queued) order by queued.outbox_id) into after_rows
        from private.content_ops_review_outbox as queued
        where queued.workspace_id=workspace and queued.content_version_id<>exact_version;
        if after_rows is distinct from before_rows then
            raise exception 'canary mutated unrelated rows during %',operation;
        end if;
    end loop;
    -- NULL restores daily cleanup, proving the canary fence was the only
    -- reason the deliberately expired unrelated rows stayed untouched.
    perform public.content_ops_reconcile_daily(workspace,null);
    if (select count(*) from private.content_ops_review_outbox
        where workspace_id=workspace and status='delivery_unknown') <> 2
       or (select count(*) from private.content_ops_review_outbox
        where workspace_id=workspace and status='obsolete') <> 1 then
        raise exception 'daily cleanup scope changed';
    end if;
    raise notice 'Synthetic canary: exact-version enqueue/claim, every RPC isolated from unrelated pending/claimed/sending, no-match no-op, no resend, and NULL daily cleanup passed';
end;
$canary$;
rollback;
