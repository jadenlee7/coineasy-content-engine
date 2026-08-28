-- Security and exact-version smoke for the bounded Studio operator projection.
-- Run as database owner after all migrations; every fixture rolls back.

begin;

do $test$
declare
    signature constant text :=
        'public.get_content_review_readiness(uuid,uuid,uuid)';
    function_oid oid := to_regprocedure(signature);
    is_security_definer boolean;
    volatility "char";
    settings text[];
begin
    if function_oid is null then
        raise exception 'content review readiness RPC is missing';
    end if;
    if has_function_privilege('public', signature, 'execute')
       or has_function_privilege('anon', signature, 'execute')
       or has_function_privilege('authenticated', signature, 'execute')
       or not has_function_privilege('service_role', signature, 'execute') then
        raise exception 'content review readiness RPC privilege is invalid';
    end if;
    select procedure.prosecdef, procedure.provolatile, procedure.proconfig
    into is_security_definer, volatility, settings
    from pg_catalog.pg_proc as procedure
    where procedure.oid = function_oid;
    if not is_security_definer
       or volatility <> 's'
       or not coalesce(settings @> array['search_path=""']::text[], false) then
        raise exception 'content review readiness RPC hardening is invalid';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug)
values (
    'f1000000-0000-4000-8000-000000000001',
    'Content Review Readiness',
    'content-review-readiness'
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active
) values (
    'f1000000-0000-4000-8000-000000000001',
    'squid', 'Squid', true
);

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, last_polled_at, active
) values (
    'f1100000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'squid', 'x', 'Squid official X', 'https://x.com/SquidRouter',
    '@SquidRouter', 15, statement_timestamp() - interval '5 minutes', true
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, source_hash
) values (
    'f1200000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'squid', 'f1100000-0000-4000-8000-000000000001',
    '2083266484789514999', 'tweet',
    'https://x.com/SquidRouter/status/2083266484789514999',
    '@SquidRouter', statement_timestamp() - interval '1 hour',
    'Private source body must never appear in readiness output.',
    'readiness:squid:2083266484789514999'
);

insert into public.content_items (
    id, workspace_id, client_id, content_kind, title, status
) values (
    'f1300000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'squid', 'daily_news', 'Readiness candidate', 'needs_review'
);

insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version,
    locale, title, content, channel_copy, deliverables, qa, generation_meta
) values (
    'f1400000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'f1300000-0000-4000-8000-000000000001',
    1, 'readiness@1', 'ko-KR', 'Readiness candidate',
    '{"private_copy":"must-not-leak"}'::jsonb,
    '{"telegram":"must-not-leak","x":"must-not-leak"}'::jsonb,
    jsonb_build_object(
        'primary_asset_id', 'f1600000-0000-4000-8000-000000000001',
        'asset_ids', jsonb_build_array(
            'f1600000-0000-4000-8000-000000000001'
        )
    ),
    '{}'::jsonb,
    '{"mock_mode":false}'::jsonb
);

update public.content_items
set current_version_id = 'f1400000-0000-4000-8000-000000000001'
where id = 'f1300000-0000-4000-8000-000000000001';

insert into public.content_source_links (
    workspace_id, client_id, content_item_id, source_item_id, position
) values (
    'f1000000-0000-4000-8000-000000000001', 'squid',
    'f1300000-0000-4000-8000-000000000001',
    'f1200000-0000-4000-8000-000000000001', 0
);

insert into storage.objects (bucket_id, name)
values (
    'content-studio',
    'f1000000-0000-4000-8000-000000000001/squid/f1600000-0000-4000-8000-000000000001/news-card.png'
);

insert into public.assets (
    id, workspace_id, content_item_id, content_version_id, asset_kind,
    storage_bucket, storage_path, mime_type, byte_size, sha256, width,
    height, metadata
) values (
    'f1600000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'f1300000-0000-4000-8000-000000000001',
    'f1400000-0000-4000-8000-000000000001', 'png',
    'content-studio',
    'f1000000-0000-4000-8000-000000000001/squid/f1600000-0000-4000-8000-000000000001/news-card.png',
    'image/png', 128, repeat('a', 64), 1080, 1080,
    '{"filename":"news-card.png"}'::jsonb
);

insert into public.jobs (
    id, workspace_id, client_id, content_item_id, job_kind, status,
    input, output, idempotency_key, finished_at
) values (
    'f1500000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001', 'squid',
    'f1300000-0000-4000-8000-000000000001', 'generate', 'succeeded',
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'manual_only', false,
        'source_item_ids', jsonb_build_array(
            'f1200000-0000-4000-8000-000000000001'
        )
    ),
    jsonb_build_object(
        'content_item_id', 'f1300000-0000-4000-8000-000000000001',
        'content_version_id', 'f1400000-0000-4000-8000-000000000001',
        'source_item_ids', jsonb_build_array(
            'f1200000-0000-4000-8000-000000000001'
        )
    ),
    'content-review-readiness:one', statement_timestamp()
);

insert into public.event_log (
    workspace_id, entity_type, entity_id, event_type, data
) values (
    'f1000000-0000-4000-8000-000000000001', 'content_item',
    'f1300000-0000-4000-8000-000000000001',
    'official_x_review_draft_completed',
    jsonb_build_object(
        'job_id', 'f1500000-0000-4000-8000-000000000001',
        'content_version_id', 'f1400000-0000-4000-8000-000000000001',
        'source_item_ids', jsonb_build_array(
            'f1200000-0000-4000-8000-000000000001'
        )
    )
);

do $test$
declare
    snapshot jsonb;
    second_snapshot jsonb;
    expected_keys text[] := array[
        'approval_count', 'banner_sha256', 'content_item_id',
        'content_version_id', 'feed_active', 'feed_last_polled_at',
        'feed_poll_interval_minutes', 'feed_poll_recent', 'generate_job_id',
        'grok_banner_sha256', 'grok_decision', 'grok_next_action',
        'grok_outbox_count', 'grok_status', 'grok_verdict_sha256',
        'publication_count', 'source_is_latest', 'source_item_id',
        'source_published_at', 'source_within_24h'
    ];
begin
    snapshot := public.get_content_review_readiness(
        'f1000000-0000-4000-8000-000000000001',
        'f1300000-0000-4000-8000-000000000001',
        'f1400000-0000-4000-8000-000000000001'
    );
    second_snapshot := public.get_content_review_readiness(
        'f1000000-0000-4000-8000-000000000001',
        'f1300000-0000-4000-8000-000000000001',
        'f1400000-0000-4000-8000-000000000001'
    );
    if snapshot is null or snapshot is distinct from second_snapshot then
        raise exception 'readiness snapshot is missing or not repeatable';
    end if;
    if (select array_agg(key order by key) from jsonb_object_keys(snapshot) as key)
       is distinct from expected_keys then
        raise exception 'readiness snapshot key allowlist changed: %', snapshot;
    end if;
    if snapshot ->> 'generate_job_id'
           <> 'f1500000-0000-4000-8000-000000000001'
       or snapshot ->> 'source_item_id'
           <> 'f1200000-0000-4000-8000-000000000001'
       or snapshot ->> 'banner_sha256' <> repeat('a', 64)
       or snapshot ->> 'grok_status' <> 'pending'
       or (snapshot ->> 'grok_outbox_count')::integer <> 1
       or (snapshot ->> 'approval_count')::integer <> 0
       or (snapshot ->> 'publication_count')::integer <> 0
       or (snapshot ->> 'source_is_latest')::boolean is not true
       or (snapshot ->> 'source_within_24h')::boolean is not true
       or (snapshot ->> 'feed_poll_recent')::boolean is not true then
        raise exception 'readiness snapshot evidence mismatch: %', snapshot;
    end if;
    if snapshot::text ~ '(must-not-leak|source_url|provider_response|storage_path|request_payload|response_payload|verdict":)' then
        raise exception 'private field leaked through readiness snapshot: %', snapshot;
    end if;
    if public.get_content_review_readiness(
        'f1000000-0000-4000-8000-000000000001',
        'f1300000-0000-4000-8000-000000000001',
        'f1400000-0000-4000-8000-000000000099'
    ) is not null then
        raise exception 'stale or foreign version returned a readiness snapshot';
    end if;
end
$test$;

update public.source_items
set published_at = published_at + interval '1 minute'
where id = 'f1200000-0000-4000-8000-000000000001';

do $test$
declare
    snapshot jsonb := public.get_content_review_readiness(
        'f1000000-0000-4000-8000-000000000001',
        'f1300000-0000-4000-8000-000000000001',
        'f1400000-0000-4000-8000-000000000001'
    );
begin
    if (snapshot ->> 'grok_outbox_count')::integer <> 0
       or snapshot ->> 'generate_job_id' is not null then
        raise exception 'stale Grok source identity was rebound: %', snapshot;
    end if;
end
$test$;

update public.source_items
set published_at = published_at - interval '1 minute'
where id = 'f1200000-0000-4000-8000-000000000001';

insert into public.jobs (
    id, workspace_id, client_id, content_item_id, job_kind, status,
    input, output, idempotency_key, finished_at
) values (
    'f1500000-0000-4000-8000-000000000002',
    'f1000000-0000-4000-8000-000000000001', 'squid',
    'f1300000-0000-4000-8000-000000000001', 'generate', 'succeeded',
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'manual_only', false,
        'source_item_ids', jsonb_build_array(
            'f1200000-0000-4000-8000-000000000001'
        )
    ),
    jsonb_build_object(
        'content_item_id', 'f1300000-0000-4000-8000-000000000001',
        'content_version_id', 'f1400000-0000-4000-8000-000000000001',
        'source_item_ids', jsonb_build_array(
            'f1200000-0000-4000-8000-000000000001'
        )
    ),
    'content-review-readiness:duplicate', statement_timestamp()
);

do $test$
declare
    snapshot jsonb := public.get_content_review_readiness(
        'f1000000-0000-4000-8000-000000000001',
        'f1300000-0000-4000-8000-000000000001',
        'f1400000-0000-4000-8000-000000000001'
    );
begin
    if snapshot ->> 'generate_job_id' is not null then
        raise exception 'duplicate exact-version generate jobs were treated as ready: %', snapshot;
    end if;
end
$test$;

delete from public.jobs
where id = 'f1500000-0000-4000-8000-000000000002';

-- OriginTrail's valid Batch materialization uses a public review job whose
-- content_item_id is null and an immutable Batch review-pack handoff.
insert into public.workspace_clients (
    workspace_id, client_id, display_name, active
) values (
    'f1000000-0000-4000-8000-000000000001',
    'origintrail', 'OriginTrail', true
);

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, last_polled_at, active
) values (
    'f2100000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'origintrail', 'x', 'OriginTrail official X',
    'https://x.com/origin_trail', '@origin_trail', 15,
    statement_timestamp() - interval '5 minutes', true
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, source_hash
) values (
    'f2200000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'origintrail', 'f2100000-0000-4000-8000-000000000001',
    '2083266484789515000', 'tweet',
    'https://x.com/origin_trail/status/2083266484789515000',
    '@origin_trail', statement_timestamp() - interval '2 hours',
    'Private OriginTrail source body must never appear in readiness output.',
    'readiness:origintrail:2083266484789515000'
);

insert into public.content_items (
    id, workspace_id, client_id, content_kind, title, status
) values (
    'f2300000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'origintrail', 'daily_news', 'OriginTrail Batch candidate',
    'needs_review'
);

insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version,
    locale, title, content, channel_copy, deliverables, qa, generation_meta
) values (
    'f2400000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'f2300000-0000-4000-8000-000000000001',
    1, 'origintrail-readiness@1', 'ko-KR',
    'OriginTrail Batch candidate',
    '{"private_copy":"must-not-leak"}'::jsonb,
    '{"telegram":"must-not-leak","x":"must-not-leak"}'::jsonb,
    jsonb_build_object(
        'primary_asset_id', 'f2600000-0000-4000-8000-000000000001',
        'asset_ids', jsonb_build_array(
            'f2600000-0000-4000-8000-000000000001'
        )
    ),
    '{}'::jsonb,
    '{"mock_mode":false}'::jsonb
);

update public.content_items
set current_version_id = 'f2400000-0000-4000-8000-000000000001'
where id = 'f2300000-0000-4000-8000-000000000001';

insert into public.content_source_links (
    workspace_id, client_id, content_item_id, source_item_id, position
) values (
    'f1000000-0000-4000-8000-000000000001', 'origintrail',
    'f2300000-0000-4000-8000-000000000001',
    'f2200000-0000-4000-8000-000000000001', 0
);

insert into storage.objects (bucket_id, name)
values (
    'content-studio',
    'f1000000-0000-4000-8000-000000000001/origintrail/f2600000-0000-4000-8000-000000000001/news-card.png'
);

insert into public.assets (
    id, workspace_id, content_item_id, content_version_id, asset_kind,
    storage_bucket, storage_path, mime_type, byte_size, sha256, width,
    height, metadata
) values (
    'f2600000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'f2300000-0000-4000-8000-000000000001',
    'f2400000-0000-4000-8000-000000000001', 'png',
    'content-studio',
    'f1000000-0000-4000-8000-000000000001/origintrail/f2600000-0000-4000-8000-000000000001/news-card.png',
    'image/png', 128, repeat('8', 64), 1200, 630,
    '{"filename":"news-card.png"}'::jsonb
);

insert into agent_runtime.batch_budgets (
    workspace_id, budget_key, period_start, period_end,
    hard_limit_microusd
) values (
    'f1000000-0000-4000-8000-000000000001',
    'readiness-origintrail-budget',
    statement_timestamp() - interval '1 minute',
    statement_timestamp() + interval '23 hours', 100000
);

insert into agent_runtime.batch_jobs (
    job_id, workspace_id, client_id, idempotency_key, custom_id,
    agent_id, workflow_kind, stage, priority, latency_class, model,
    model_tier, deadline_at, input_payload, input_sha256,
    estimated_input_tokens, max_output_tokens, max_cost_microusd,
    budget_key, status, reservation_state, attempts,
    actual_input_tokens, actual_output_tokens, actual_cost_microusd,
    result_code, result_payload, finished_at
) values (
    'f2510000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001',
    'origintrail', repeat('1', 64),
    'f2510000-0000-4000-8000-000000000001:generate:1',
    'origintrail_client_agent', 'official_source_nonurgent_pack',
    'generate', 3, 'batch_24h', 'gpt-5.6-luna', 'S',
    statement_timestamp() + interval '23 hours',
    jsonb_build_object('approval_required', true),
    repeat('2', 64), 1000, 1000, 2200,
    'readiness-origintrail-budget', 'completed', 'settled', 1,
    500, 200, 2200, 'needs_review',
    jsonb_build_object(
        'headline_ko', 'OriginTrail 검토 제목',
        'body_ko', '검토 본문',
        'x_copy_ko', 'X 검토 문구',
        'telegram_copy_ko', 'Telegram 검토 문구'
    ),
    statement_timestamp()
);

insert into public.jobs (
    id, workspace_id, client_id, content_item_id, job_kind, status,
    input, output, idempotency_key, finished_at
) values (
    'f2500000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001', 'origintrail', null,
    'generate', 'succeeded',
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'content_kind', 'daily_news',
        'manual_only', false,
        'source_url',
            'https://x.com/origin_trail/status/2083266484789515000'
    ),
    jsonb_build_object(
        'workflow', 'agent_batch_review_handoff_v1',
        'handoff', 'openai_batch',
        'batch_job_id', 'f2510000-0000-4000-8000-000000000001'::uuid,
        'input_sha256', repeat('2', 64),
        'review_state', 'pending'
    ),
    'content-review-readiness:origintrail', statement_timestamp()
);

insert into agent_runtime.origintrail_batch_review_packs (
    workspace_id, job_id, client_id, content_item_id, content_version_id,
    asset_id, source_item_id, input_sha256, result_sha256,
    source_content_sha256, banner_sha256, review_pack_sha256,
    protocol_version
) values (
    'f1000000-0000-4000-8000-000000000001',
    'f2510000-0000-4000-8000-000000000001', 'origintrail',
    'f2300000-0000-4000-8000-000000000001',
    'f2400000-0000-4000-8000-000000000001',
    'f2600000-0000-4000-8000-000000000001',
    'f2200000-0000-4000-8000-000000000001',
    repeat('2', 64), repeat('3', 64), repeat('4', 64),
    repeat('8', 64), repeat('5', 64), 'origintrail-review-pack@1'
);

insert into public.event_log (
    workspace_id, entity_type, entity_id, event_type, data
) values (
    'f1000000-0000-4000-8000-000000000001', 'content_item',
    'f2300000-0000-4000-8000-000000000001',
    'origintrail_batch_review_pack_materialized',
    jsonb_build_object(
        'job_id', 'f2510000-0000-4000-8000-000000000001',
        'content_version_id', 'f2400000-0000-4000-8000-000000000001',
        'asset_id', 'f2600000-0000-4000-8000-000000000001',
        'source_item_id', 'f2200000-0000-4000-8000-000000000001',
        'banner_sha256', repeat('8', 64)
    )
);

do $test$
declare
    snapshot jsonb := public.get_content_review_readiness(
        'f1000000-0000-4000-8000-000000000001',
        'f2300000-0000-4000-8000-000000000001',
        'f2400000-0000-4000-8000-000000000001'
    );
begin
    if snapshot ->> 'generate_job_id'
            <> 'f2500000-0000-4000-8000-000000000001'
       or snapshot ->> 'source_item_id'
            <> 'f2200000-0000-4000-8000-000000000001'
       or snapshot ->> 'banner_sha256' <> repeat('8', 64)
       or snapshot ->> 'grok_status' <> 'pending'
       or (snapshot ->> 'grok_outbox_count')::integer <> 1 then
        raise exception 'OriginTrail Batch readiness binding failed: %', snapshot;
    end if;
end
$test$;

insert into public.publications (
    id, workspace_id, client_id, content_item_id, content_version_id,
    channel, status, request_payload, response_payload, last_error
) values (
    'f1700000-0000-4000-8000-000000000001',
    'f1000000-0000-4000-8000-000000000001', 'squid',
    'f1300000-0000-4000-8000-000000000001',
    'f1400000-0000-4000-8000-000000000001',
    'x', 'failed', '{}'::jsonb, '{}'::jsonb, 'fixture only'
);

do $test$
declare
    snapshot jsonb := public.get_content_review_readiness(
        'f1000000-0000-4000-8000-000000000001',
        'f1300000-0000-4000-8000-000000000001',
        'f1400000-0000-4000-8000-000000000001'
    );
begin
    if (snapshot ->> 'publication_count')::integer <> 1 then
        raise exception 'exact-version publication count is incorrect: %', snapshot;
    end if;
end
$test$;

rollback;
