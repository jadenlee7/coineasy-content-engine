-- Transactional security and exactly-once smoke for provider-neutral Content QA.
-- Run as database owner after all migrations; every fixture rolls back.

begin;

do $test$
declare
    record_signature constant text :=
        'public.record_content_qa_verdict(uuid,uuid,uuid,text,text,text,text,uuid,uuid,text,timestamptz,text,jsonb)';
    read_signature constant text :=
        'public.get_content_qa_job(uuid,uuid,uuid,text)';
    role_name text;
    privilege_name text;
    record_oid oid := to_regprocedure(record_signature);
    read_oid oid := to_regprocedure(read_signature);
    record_security_definer boolean;
    read_security_definer boolean;
    read_volatility "char";
    record_settings text[];
    read_settings text[];
begin
    if record_oid is null or read_oid is null then
        raise exception 'Content QA RPC is missing';
    end if;

    foreach role_name in array array[
        'public', 'anon', 'authenticated', 'service_role',
        'coineasy_content_qa'
    ] loop
        foreach privilege_name in array array[
            'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
            'REFERENCES', 'TRIGGER'
        ] loop
            if has_table_privilege(
                role_name,
                'private.content_qa_jobs',
                privilege_name
            ) then
                raise exception
                    '% has direct % on private.content_qa_jobs',
                    role_name, privilege_name;
            end if;
        end loop;
    end loop;

    if has_function_privilege('public', record_signature, 'EXECUTE')
       or has_function_privilege('anon', record_signature, 'EXECUTE')
       or has_function_privilege('authenticated', record_signature, 'EXECUTE')
       or has_function_privilege('service_role', record_signature, 'EXECUTE')
       or not has_function_privilege('coineasy_content_qa', record_signature, 'EXECUTE')
       or has_function_privilege('public', read_signature, 'EXECUTE')
       or has_function_privilege('anon', read_signature, 'EXECUTE')
       or has_function_privilege('authenticated', read_signature, 'EXECUTE')
       or has_function_privilege('service_role', read_signature, 'EXECUTE')
       or not has_function_privilege('coineasy_content_qa', read_signature, 'EXECUTE') then
        raise exception 'Content QA RPC privilege boundary is invalid';
    end if;

    if not has_function_privilege(
            'coineasy_content_qa',
            'public.list_content_qa_library(uuid,text,text,text,integer,timestamptz,uuid)',
            'EXECUTE'
       ) or not has_function_privilege(
            'coineasy_content_qa',
            'public.get_content_qa_library_item(uuid,uuid)',
            'EXECUTE'
       ) or not has_function_privilege(
            'coineasy_content_qa',
            'public.get_content_qa_readiness(uuid,uuid,uuid)',
            'EXECUTE'
       ) then
        raise exception 'Content QA bounded read privilege is missing';
    end if;

    select procedure.prosecdef, procedure.proconfig
    into record_security_definer, record_settings
    from pg_catalog.pg_proc as procedure
    where procedure.oid = record_oid;
    select procedure.prosecdef, procedure.provolatile, procedure.proconfig
    into read_security_definer, read_volatility, read_settings
    from pg_catalog.pg_proc as procedure
    where procedure.oid = read_oid;
    if not record_security_definer
       or not read_security_definer
       or read_volatility <> 's'
       or not coalesce(
            record_settings @> array['search_path=""']::text[], false
       )
       or not coalesce(
            read_settings @> array['search_path=""']::text[], false
       ) then
        raise exception 'Content QA RPC hardening is invalid';
    end if;
end
$test$;

select pg_catalog.set_config(
    'request.jwt.claims',
    '{"role":"coineasy_content_qa","workspace_id":"ca100000-0000-4000-8000-000000000001","sub":"codex:content-qa","capability":"content_qa_review","release_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","environment":"production","automatic_publication":false,"max_external_actions":0}',
    true
);

insert into public.workspaces (id, name, slug)
values (
    'ca100000-0000-4000-8000-000000000001',
    'Content QA Security',
    'content-qa-security'
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active
) values (
    'ca100000-0000-4000-8000-000000000001',
    'squid', 'Squid', true
);

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, last_polled_at, active
) values (
    'ca110000-0000-4000-8000-000000000001',
    'ca100000-0000-4000-8000-000000000001',
    'squid', 'x', 'Squid official X', 'https://x.com/SquidRouter',
    '@SquidRouter', 15, statement_timestamp() - interval '5 minutes', true
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, source_hash
) values (
    'ca120000-0000-4000-8000-000000000001',
    'ca100000-0000-4000-8000-000000000001',
    'squid', 'ca110000-0000-4000-8000-000000000001',
    '2083266484789514777', 'tweet',
    'https://x.com/SquidRouter/status/2083266484789514777',
    '@SquidRouter', statement_timestamp() - interval '1 hour',
    'Private official source body must never appear in the receipt read RPC.',
    'content-qa-security:squid:2083266484789514777'
);

insert into public.content_items (
    id, workspace_id, client_id, content_kind, title, status
) values (
    'ca130000-0000-4000-8000-000000000001',
    'ca100000-0000-4000-8000-000000000001',
    'squid', 'daily_news', 'Content QA candidate', 'needs_review'
);

insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version,
    locale, title, content, channel_copy, deliverables, qa, generation_meta
) values (
    'ca140000-0000-4000-8000-000000000001',
    'ca100000-0000-4000-8000-000000000001',
    'ca130000-0000-4000-8000-000000000001',
    1, 'content-qa-security@1', 'ko-KR', 'Content QA candidate',
    '{"private_copy":"must-not-leak"}'::jsonb,
    '{"telegram":"must-not-leak","x":"must-not-leak"}'::jsonb,
    jsonb_build_object(
        'primary_asset_id', 'ca160000-0000-4000-8000-000000000001',
        'asset_ids', jsonb_build_array(
            'ca160000-0000-4000-8000-000000000001'
        )
    ),
    '{}'::jsonb,
    '{"mock_mode":false}'::jsonb
);

update public.content_items
set current_version_id = 'ca140000-0000-4000-8000-000000000001'
where id = 'ca130000-0000-4000-8000-000000000001';

insert into public.content_source_links (
    workspace_id, client_id, content_item_id, source_item_id, position
) values (
    'ca100000-0000-4000-8000-000000000001', 'squid',
    'ca130000-0000-4000-8000-000000000001',
    'ca120000-0000-4000-8000-000000000001', 0
);

insert into storage.objects (bucket_id, name)
values
    (
        'content-studio',
        'ca100000-0000-4000-8000-000000000001/squid/ca160000-0000-4000-8000-000000000001/news-card.png'
    ),
    (
        'content-studio',
        'ca100000-0000-4000-8000-000000000001/squid/ca160000-0000-4000-8000-000000000099/unregistered.png'
    );

insert into public.assets (
    id, workspace_id, content_item_id, content_version_id, asset_kind,
    storage_bucket, storage_path, mime_type, byte_size, sha256, width,
    height, metadata
) values (
    'ca160000-0000-4000-8000-000000000001',
    'ca100000-0000-4000-8000-000000000001',
    'ca130000-0000-4000-8000-000000000001',
    'ca140000-0000-4000-8000-000000000001', 'png',
    'content-studio',
    'ca100000-0000-4000-8000-000000000001/squid/ca160000-0000-4000-8000-000000000001/news-card.png',
    'image/png', 128, repeat('a', 64), 1080, 1080,
    '{"filename":"news-card.png"}'::jsonb
);

insert into public.jobs (
    id, workspace_id, client_id, content_item_id, job_kind, status,
    input, output, idempotency_key, finished_at
) values (
    'ca150000-0000-4000-8000-000000000001',
    'ca100000-0000-4000-8000-000000000001', 'squid',
    'ca130000-0000-4000-8000-000000000001', 'generate', 'succeeded',
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'manual_only', false,
        'source_item_ids', jsonb_build_array(
            'ca120000-0000-4000-8000-000000000001'
        )
    ),
    jsonb_build_object(
        'content_item_id', 'ca130000-0000-4000-8000-000000000001',
        'content_version_id', 'ca140000-0000-4000-8000-000000000001',
        'source_item_ids', jsonb_build_array(
            'ca120000-0000-4000-8000-000000000001'
        )
    ),
    'content-qa-security:natural-cron', statement_timestamp()
);

-- The existing authoritative completion trigger creates the legacy pending
-- Grok row. The first Content QA record must atomically retire this untouched
-- alternative path before returning success.
insert into public.event_log (
    workspace_id, entity_type, entity_id, event_type, data
) values (
    'ca100000-0000-4000-8000-000000000001', 'content_item',
    'ca130000-0000-4000-8000-000000000001',
    'official_x_review_draft_completed',
    jsonb_build_object(
        'job_id', 'ca150000-0000-4000-8000-000000000001',
        'content_version_id', 'ca140000-0000-4000-8000-000000000001',
        'source_item_ids', jsonb_build_array(
            'ca120000-0000-4000-8000-000000000001'
        )
    )
);

do $test$
declare
    verdict constant jsonb := '{
      "decision":"PASS",
      "summary":"공식 원문과 Squid 브랜드 표현이 모두 일치합니다.",
      "fact_check":{
        "status":"PASS",
        "checks":["공식 X 원문의 핵심 사실을 확인했습니다."],
        "source_urls":["https://x.com/SquidRouter/status/2083266484789514777"]
      },
      "brand_check":{
        "status":"PASS",
        "checks":["Squid 공식 명칭과 브랜드 톤을 확인했습니다."]
      },
      "issues":[],
      "next_action":"ready_for_human_approval"
    }'::jsonb;
    blocked boolean;
    receipt_first_verified boolean := false;
    receipt_first_result jsonb;
begin
    if not exists (
        select 1 from private.grok_qa_dispatch_outbox as legacy
        where legacy.workspace_id = 'ca100000-0000-4000-8000-000000000001'
          and legacy.content_version_id = 'ca140000-0000-4000-8000-000000000001'
          and legacy.status = 'pending'
          and legacy.provider_attempt_started_at is null
          and legacy.verdict is null
    ) then
        raise exception 'authoritative event did not create pristine Grok work';
    end if;

    -- Exercise the opposite race ordering inside a rollback-only subtransaction:
    -- no outbox -> Content QA receipt -> later authoritative legacy enqueue.
    -- The BEFORE INSERT fence must create only an obsolete, receipt-bound row.
    begin
        delete from private.grok_qa_dispatch_outbox
        where workspace_id = 'ca100000-0000-4000-8000-000000000001'
          and content_version_id = 'ca140000-0000-4000-8000-000000000001';

        receipt_first_result := public.record_content_qa_verdict(
            'ca100000-0000-4000-8000-000000000001',
            'ca130000-0000-4000-8000-000000000001',
            'ca140000-0000-4000-8000-000000000001',
            'official-x-content-qa@1', 'codex:content-qa',
            'codex', repeat('b', 40),
            'ca150000-0000-4000-8000-000000000001',
            'ca120000-0000-4000-8000-000000000001',
            'https://x.com/SquidRouter/status/2083266484789514777',
            (
                select published_at from public.source_items
                where id = 'ca120000-0000-4000-8000-000000000001'
            ),
            repeat('a', 64), verdict
        );
        if receipt_first_result -> 'recorded' is distinct from 'true'::jsonb then
            raise exception 'receipt-first Content QA setup failed: %',
                receipt_first_result;
        end if;

        insert into public.event_log (
            workspace_id, entity_type, entity_id, event_type, data
        ) values (
            'ca100000-0000-4000-8000-000000000001', 'content_item',
            'ca130000-0000-4000-8000-000000000001',
            'official_x_review_draft_completed',
            jsonb_build_object(
                'job_id', 'ca150000-0000-4000-8000-000000000001',
                'content_version_id',
                    'ca140000-0000-4000-8000-000000000001',
                'source_item_ids', jsonb_build_array(
                    'ca120000-0000-4000-8000-000000000001'
                )
            )
        );

        if not exists (
            select 1
            from private.grok_qa_dispatch_outbox as legacy
            where legacy.workspace_id =
                    'ca100000-0000-4000-8000-000000000001'
              and legacy.content_version_id =
                    'ca140000-0000-4000-8000-000000000001'
              and legacy.status = 'obsolete'
              and legacy.content_qa_job_id =
                    (receipt_first_result ->> 'job_id')::uuid
              and legacy.completed_at is not null
              and legacy.provider_attempt_started_at is null
              and legacy.verdict is null
        ) then
            raise exception 'receipt-first Grok enqueue was not born obsolete';
        end if;

        raise exception 'receipt_first_scenario_complete'
            using errcode = 'CQ001';
    exception
        when sqlstate 'CQ001' then receipt_first_verified := true;
    end;
    if not receipt_first_verified then
        raise exception 'receipt-first advisory-lock scenario did not complete';
    end if;

    -- A terminal legacy state must block the first Content QA write.
    update private.grok_qa_dispatch_outbox
    set status = 'failed',
        error_code = 'content_qa_security_terminal',
        completed_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = 'ca100000-0000-4000-8000-000000000001'
      and content_version_id = 'ca140000-0000-4000-8000-000000000001';
    blocked := false;
    begin
        perform public.record_content_qa_verdict(
            'ca100000-0000-4000-8000-000000000001',
            'ca130000-0000-4000-8000-000000000001',
            'ca140000-0000-4000-8000-000000000001',
            'official-x-content-qa@1', 'codex:content-qa',
            'codex', repeat('b', 40),
            'ca150000-0000-4000-8000-000000000001',
            'ca120000-0000-4000-8000-000000000001',
            'https://x.com/SquidRouter/status/2083266484789514777',
            (
                select published_at from public.source_items
                where id = 'ca120000-0000-4000-8000-000000000001'
            ),
            repeat('a', 64), verdict
        );
    exception
        when check_violation then blocked := true;
    end;
    if not blocked then
        raise exception 'terminal Grok work did not block Content QA';
    end if;
    update private.grok_qa_dispatch_outbox
    set status = 'pending',
        error_code = null,
        completed_at = null,
        updated_at = statement_timestamp()
    where workspace_id = 'ca100000-0000-4000-8000-000000000001'
      and content_version_id = 'ca140000-0000-4000-8000-000000000001';

    -- Any durable Grok delivery receipt also blocks the Codex path.
    insert into private.grok_qa_verdict_receipts (
        workspace_id, content_item_id, content_version_id, decision,
        payload, payload_sha256
    ) values (
        'ca100000-0000-4000-8000-000000000001',
        'ca130000-0000-4000-8000-000000000001',
        'ca140000-0000-4000-8000-000000000001', 'PASS',
        '{"test":"must-not-leak"}'::jsonb, repeat('c', 64)
    );
    blocked := false;
    begin
        perform public.record_content_qa_verdict(
            'ca100000-0000-4000-8000-000000000001',
            'ca130000-0000-4000-8000-000000000001',
            'ca140000-0000-4000-8000-000000000001',
            'official-x-content-qa@1', 'codex:content-qa',
            'codex', repeat('b', 40),
            'ca150000-0000-4000-8000-000000000001',
            'ca120000-0000-4000-8000-000000000001',
            'https://x.com/SquidRouter/status/2083266484789514777',
            (
                select published_at from public.source_items
                where id = 'ca120000-0000-4000-8000-000000000001'
            ),
            repeat('a', 64), verdict
        );
    exception
        when check_violation then blocked := true;
    end;
    if not blocked then
        raise exception 'Grok receipt did not block Content QA';
    end if;
    delete from private.grok_qa_verdict_receipts
    where workspace_id = 'ca100000-0000-4000-8000-000000000001'
      and content_version_id = 'ca140000-0000-4000-8000-000000000001';

    -- Duplicate natural generations must fail closed.
    insert into public.jobs (
        id, workspace_id, client_id, content_item_id, job_kind, status,
        input, output, idempotency_key, finished_at
    )
    select
        'ca150000-0000-4000-8000-000000000002'::uuid,
        workspace_id, client_id, content_item_id, job_kind, status,
        input, output, 'content-qa-security:duplicate-cron', finished_at
    from public.jobs
    where id = 'ca150000-0000-4000-8000-000000000001';
    blocked := false;
    begin
        perform public.record_content_qa_verdict(
            'ca100000-0000-4000-8000-000000000001',
            'ca130000-0000-4000-8000-000000000001',
            'ca140000-0000-4000-8000-000000000001',
            'official-x-content-qa@1', 'codex:content-qa',
            'codex', repeat('b', 40),
            'ca150000-0000-4000-8000-000000000001',
            'ca120000-0000-4000-8000-000000000001',
            'https://x.com/SquidRouter/status/2083266484789514777',
            (
                select published_at from public.source_items
                where id = 'ca120000-0000-4000-8000-000000000001'
            ),
            repeat('a', 64), verdict
        );
    exception
        when check_violation then blocked := true;
    end;
    if not blocked then
        raise exception 'duplicate natural generation did not block Content QA';
    end if;
    delete from public.jobs
    where id = 'ca150000-0000-4000-8000-000000000002';

    -- A package/read provenance tuple cannot be swapped before recording.
    blocked := false;
    begin
        perform public.record_content_qa_verdict(
            'ca100000-0000-4000-8000-000000000001',
            'ca130000-0000-4000-8000-000000000001',
            'ca140000-0000-4000-8000-000000000001',
            'official-x-content-qa@1', 'codex:content-qa',
            'codex', repeat('b', 40),
            'ca150000-0000-4000-8000-000000000099',
            'ca120000-0000-4000-8000-000000000001',
            'https://x.com/SquidRouter/status/2083266484789514777',
            (
                select published_at from public.source_items
                where id = 'ca120000-0000-4000-8000-000000000001'
            ),
            repeat('a', 64), verdict
        );
    exception
        when check_violation then blocked := true;
    end;
    if not blocked then
        raise exception 'mismatched expected Content QA provenance was accepted';
    end if;
end
$test$;

select pg_catalog.set_config(
    'request.jwt.claims',
    '{"role":"coineasy_content_qa","workspace_id":"ca100000-0000-4000-8000-000000000001","sub":"codex:content-qa","capability":"content_qa_review","release_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","environment":"production","automatic_publication":false,"max_external_actions":0}',
    true
);
set local role coineasy_content_qa;

do $scoped_read$
declare
    cross_workspace_blocked boolean := false;
begin
    if public.get_content_qa_job(
        'ca100000-0000-4000-8000-000000000001',
        'ca130000-0000-4000-8000-000000000001',
        'ca140000-0000-4000-8000-000000000001',
        'official-x-content-qa@1'
    ) is not null then
        raise exception 'Content QA receipt unexpectedly existed before record';
    end if;
    if jsonb_array_length(
        public.list_content_qa_library(
            'ca100000-0000-4000-8000-000000000001',
            'squid', 'daily_news', 'needs_review', 5, null, null
        ) -> 'items'
    ) <> 1 then
        raise exception 'Scoped Content QA library did not return its fixture';
    end if;
    if (select count(*) from storage.objects) <> 1
       or not exists (
           select 1 from storage.objects
           where name =
             'ca100000-0000-4000-8000-000000000001/squid/ca160000-0000-4000-8000-000000000001/news-card.png'
       ) then
        raise exception 'Content QA Storage policy was not canonical-PNG-only';
    end if;
    begin
        perform public.list_content_qa_library(
            'ca100000-0000-4000-8000-000000000099',
            'squid', 'daily_news', 'needs_review', 5, null, null
        );
    exception
        when insufficient_privilege then cross_workspace_blocked := true;
    end;
    if not cross_workspace_blocked then
        raise exception 'Scoped Content QA role crossed its workspace claim';
    end if;
end;
$scoped_read$;

reset role;

do $test$
declare
    verdict constant jsonb := '{
      "decision":"PASS",
      "summary":"공식 원문과 Squid 브랜드 표현이 모두 일치합니다.",
      "fact_check":{
        "status":"PASS",
        "checks":["공식 X 원문의 핵심 사실을 확인했습니다."],
        "source_urls":["https://x.com/SquidRouter/status/2083266484789514777"]
      },
      "brand_check":{
        "status":"PASS",
        "checks":["Squid 공식 명칭과 브랜드 톤을 확인했습니다."]
      },
      "issues":[],
      "next_action":"ready_for_human_approval"
    }'::jsonb;
    first_result jsonb;
    replay_result jsonb;
    conflict_result jsonb;
    snapshot jsonb;
    grok_receipt_blocked boolean := false;
    identity_override_blocked boolean := false;
    expected_record_keys text[] := array[
        'decision', 'input_sha256', 'job_id', 'policy_version', 'recorded',
        'reviewer_model', 'reviewer_principal', 'reviewer_release_sha',
        'status', 'verdict_sha256'
    ];
    expected_read_keys text[] := array[
        'banner_sha256', 'content_item_id', 'content_version_id', 'decision',
        'input_sha256', 'job_id', 'policy_version', 'reviewed_at',
        'reviewer_model', 'reviewer_principal', 'reviewer_release_sha',
        'source_item_id', 'status', 'verdict_sha256', 'workspace_id'
    ];
begin
    first_result := public.record_content_qa_verdict(
        'ca100000-0000-4000-8000-000000000001',
        'ca130000-0000-4000-8000-000000000001',
        'ca140000-0000-4000-8000-000000000001',
        'official-x-content-qa@1',
        'codex:content-qa',
        'codex',
        repeat('b', 40),
        'ca150000-0000-4000-8000-000000000001',
        'ca120000-0000-4000-8000-000000000001',
        'https://x.com/SquidRouter/status/2083266484789514777',
        (
            select published_at from public.source_items
            where id = 'ca120000-0000-4000-8000-000000000001'
        ),
        repeat('a', 64),
        verdict
    );
    if (select array_agg(key order by key)
        from jsonb_object_keys(first_result) as key)
            is distinct from expected_record_keys
       or first_result -> 'recorded' is distinct from 'true'::jsonb
       or first_result ->> 'status' <> 'reviewed'
       or first_result ->> 'decision' <> 'PASS'
       or first_result ->> 'input_sha256' !~ '^[a-f0-9]{64}$'
       or first_result ->> 'verdict_sha256' !~ '^[a-f0-9]{64}$' then
        raise exception 'first Content QA record is invalid: %', first_result;
    end if;

    begin
        perform public.claim_grok_qa_verdict(
            'ca100000-0000-4000-8000-000000000001',
            'ca130000-0000-4000-8000-000000000001',
            'ca140000-0000-4000-8000-000000000001',
            verdict
        );
    exception
        when check_violation then grok_receipt_blocked := true;
    end;
    if not grok_receipt_blocked then
        raise exception 'Content QA did not block the later Grok receipt RPC';
    end if;

    replay_result := public.record_content_qa_verdict(
        'ca100000-0000-4000-8000-000000000001',
        'ca130000-0000-4000-8000-000000000001',
        'ca140000-0000-4000-8000-000000000001',
        'official-x-content-qa@1',
        'codex:content-qa',
        'codex',
        repeat('b', 40),
        'ca150000-0000-4000-8000-000000000001',
        'ca120000-0000-4000-8000-000000000001',
        'https://x.com/SquidRouter/status/2083266484789514777',
        (
            select published_at from public.source_items
            where id = 'ca120000-0000-4000-8000-000000000001'
        ),
        repeat('a', 64),
        verdict
    );
    if replay_result -> 'recorded' is distinct from 'false'::jsonb
       or replay_result ->> 'status' <> 'reviewed'
       or replay_result ->> 'job_id' <> first_result ->> 'job_id'
       or replay_result ->> 'input_sha256'
            <> first_result ->> 'input_sha256'
       or replay_result ->> 'verdict_sha256'
            <> first_result ->> 'verdict_sha256' then
        raise exception 'exact Content QA replay was not suppressed: %',
            replay_result;
    end if;

    conflict_result := public.record_content_qa_verdict(
        'ca100000-0000-4000-8000-000000000001',
        'ca130000-0000-4000-8000-000000000001',
        'ca140000-0000-4000-8000-000000000001',
        'official-x-content-qa@1',
        'codex:content-qa',
        'codex',
        repeat('b', 40),
        'ca150000-0000-4000-8000-000000000001',
        'ca120000-0000-4000-8000-000000000001',
        'https://x.com/SquidRouter/status/2083266484789514777',
        (
            select published_at from public.source_items
            where id = 'ca120000-0000-4000-8000-000000000001'
        ),
        repeat('a', 64),
        jsonb_set(
            verdict,
            '{summary}',
            '"공식 근거와 다른 판정은 기존 기록과 충돌해야 합니다."'::jsonb
        )
    );
    if conflict_result -> 'recorded' is distinct from 'false'::jsonb
       or conflict_result ->> 'status' <> 'duplicate_conflict'
       or conflict_result ->> 'job_id' <> first_result ->> 'job_id' then
        raise exception 'conflicting Content QA verdict was accepted: %',
            conflict_result;
    end if;

    begin
        perform public.record_content_qa_verdict(
            'ca100000-0000-4000-8000-000000000001',
            'ca130000-0000-4000-8000-000000000001',
            'ca140000-0000-4000-8000-000000000001',
            'official-x-content-qa@1',
            'codex:content-qa',
            'gpt-5.6-terra',
            repeat('b', 40),
            'ca150000-0000-4000-8000-000000000001',
            'ca120000-0000-4000-8000-000000000001',
            'https://x.com/SquidRouter/status/2083266484789514777',
            (
                select published_at from public.source_items
                where id = 'ca120000-0000-4000-8000-000000000001'
            ),
            repeat('a', 64),
            verdict
        );
    exception
        when invalid_parameter_value then identity_override_blocked := true;
    end;
    if not identity_override_blocked then
        raise exception 'Content QA reviewer model override was accepted';
    end if;

    snapshot := public.get_content_qa_job(
        'ca100000-0000-4000-8000-000000000001',
        'ca130000-0000-4000-8000-000000000001',
        'ca140000-0000-4000-8000-000000000001',
        'official-x-content-qa@1'
    );
    if snapshot is null
       or (select array_agg(key order by key)
           from jsonb_object_keys(snapshot) as key)
            is distinct from expected_read_keys
       or snapshot ->> 'job_id' <> first_result ->> 'job_id'
       or snapshot ->> 'status' <> 'reviewed'
       or snapshot ->> 'source_item_id'
            <> 'ca120000-0000-4000-8000-000000000001'
       or snapshot ->> 'banner_sha256' <> repeat('a', 64)
       or snapshot ->> 'reviewed_at' is null then
        raise exception 'bounded Content QA read is invalid: %', snapshot;
    end if;
    if snapshot::text ~ '(must-not-leak|source_canonical_url|source_published_at|"verdict":|provider_payload|response_payload)' then
        raise exception 'private payload leaked from Content QA read: %', snapshot;
    end if;
end
$test$;

reset role;

do $test$
declare
    direct_grok_receipt_blocked boolean := false;
begin
    if (select count(*) from private.content_qa_jobs
        where workspace_id = 'ca100000-0000-4000-8000-000000000001'
          and content_version_id = 'ca140000-0000-4000-8000-000000000001'
          and policy_version = 'official-x-content-qa@1') <> 1 then
        raise exception 'Content QA did not remain exactly once';
    end if;
    if exists (
        select 1 from public.approvals
        where workspace_id = 'ca100000-0000-4000-8000-000000000001'
          and content_item_id = 'ca130000-0000-4000-8000-000000000001'
          and content_version_id = 'ca140000-0000-4000-8000-000000000001'
    ) then
        raise exception 'Content QA created a human approval';
    end if;
    if exists (
        select 1 from public.publications
        where workspace_id = 'ca100000-0000-4000-8000-000000000001'
          and content_item_id = 'ca130000-0000-4000-8000-000000000001'
          and content_version_id = 'ca140000-0000-4000-8000-000000000001'
    ) then
        raise exception 'Content QA created a publication';
    end if;
    if (
        select status from public.content_items
        where workspace_id = 'ca100000-0000-4000-8000-000000000001'
          and id = 'ca130000-0000-4000-8000-000000000001'
    ) <> 'needs_review' then
        raise exception 'Content QA changed Content Studio status';
    end if;
    if not exists (
        select 1
        from private.grok_qa_dispatch_outbox as legacy
        join private.content_qa_jobs as content_qa
          on content_qa.job_id = legacy.content_qa_job_id
        where legacy.workspace_id =
                'ca100000-0000-4000-8000-000000000001'
          and legacy.content_version_id =
                'ca140000-0000-4000-8000-000000000001'
          and legacy.status = 'obsolete'
          and legacy.provider_attempt_started_at is null
          and legacy.verdict is null
          and legacy.completed_at is not null
    ) then
        raise exception 'Content QA did not atomically fence pending Grok work';
    end if;
    if exists (
        select 1 from private.grok_qa_verdict_receipts
        where workspace_id = 'ca100000-0000-4000-8000-000000000001'
          and content_version_id = 'ca140000-0000-4000-8000-000000000001'
    ) then
        raise exception 'Content QA unexpectedly created a Grok receipt';
    end if;

    begin
        insert into private.grok_qa_verdict_receipts (
            workspace_id, content_item_id, content_version_id, decision,
            payload, payload_sha256
        ) values (
            'ca100000-0000-4000-8000-000000000001',
            'ca130000-0000-4000-8000-000000000001',
            'ca140000-0000-4000-8000-000000000001', 'PASS',
            '{"test":"direct-race-must-block"}'::jsonb,
            repeat('d', 64)
        );
    exception
        when check_violation then direct_grok_receipt_blocked := true;
    end;
    if not direct_grok_receipt_blocked then
        raise exception 'direct Grok receipt insert bypassed Content QA fence';
    end if;

    begin
        update private.grok_qa_dispatch_outbox
        set status = 'pending',
            completed_at = null
        where workspace_id = 'ca100000-0000-4000-8000-000000000001'
          and content_version_id = 'ca140000-0000-4000-8000-000000000001';
        raise exception 'Content QA fenced Grok work became claimable';
    exception
        when check_violation then
            null;
    end;
end
$test$;

rollback;
