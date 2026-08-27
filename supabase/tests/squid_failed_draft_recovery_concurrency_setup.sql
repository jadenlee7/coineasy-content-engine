\set ON_ERROR_STOP on

insert into public.workspaces (id, name, slug, created_by)
values (
    'e0000000-0000-4000-8000-000000000001',
    'Squid Recovery Concurrency Test',
    'squid-recovery-concurrency-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
) values (
    'e0000000-0000-4000-8000-000000000001',
    'squid',
    'Squid',
    true,
    null
);

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, last_cursor, last_polled_at, active
) values (
    'e1000000-0000-4000-8000-000000000001',
    'e0000000-0000-4000-8000-000000000001',
    'squid',
    'x',
    'Squid official X',
    'https://x.com/SquidRouter',
    '@SquidRouter',
    15,
    '2091935028565459431',
    clock_timestamp(),
    true
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, media, raw_payload,
    source_hash
) values (
    'e2000000-0000-4000-8000-000000000001',
    'e0000000-0000-4000-8000-000000000001',
    'squid',
    'e1000000-0000-4000-8000-000000000001',
    '2091935028565459431',
    'tweet',
    'https://x.com/SquidRouter/status/2091935028565459431',
    '@SquidRouter',
    statement_timestamp() - interval '1 hour',
    'Squid recovery concurrency source.',
    jsonb_build_array(jsonb_build_object(
        'type', 'photo',
        'url', 'https://pbs.twimg.com/media/recovery_concurrency.jpg'
    )),
    '{}'::jsonb,
    repeat('a', 64)
);

insert into public.jobs (
    id, workspace_id, client_id, job_kind, status, priority, input, output,
    idempotency_key, attempts, max_attempts, available_at,
    last_error_code, last_error_message, started_at, finished_at
) values (
    'e3000000-0000-4000-8000-000000000001',
    'e0000000-0000-4000-8000-000000000001',
    'squid',
    'generate',
    'failed',
    0,
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'kst_date',
            pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date,
        'source_item_ids', jsonb_build_array(
            'e2000000-0000-4000-8000-000000000001'
        ),
        'content_kind', 'daily_news',
        'request_id', 'e4000000-0000-4000-8000-000000000001',
        'source_content', 'Squid recovery concurrency source.',
        'source_url',
            'https://x.com/SquidRouter/status/2091935028565459431',
        'source_image_url',
            'https://pbs.twimg.com/media/recovery_concurrency.jpg',
        'manual_only', false
    ),
    jsonb_build_object(
        'execution_plane', 'studio_sync',
        'last_failure', jsonb_build_object(
            'error_code', 'squid_copy_discovery_unavailable',
            'retryable', false
        )
    ),
    'official-x-review:v1:recovery-concurrency:squid',
    3,
    3,
    statement_timestamp(),
    'squid_copy_discovery_unavailable',
    'squid_copy_discovery_unavailable',
    statement_timestamp() - interval '20 minutes',
    statement_timestamp() - interval '5 minutes'
);

insert into private.official_x_source_state (
    workspace_id, client_id, source_item_id, queued_job_id,
    discovered_at, queued_at
) values (
    'e0000000-0000-4000-8000-000000000001',
    'squid',
    'e2000000-0000-4000-8000-000000000001',
    'e3000000-0000-4000-8000-000000000001',
    statement_timestamp() - interval '10 minutes',
    statement_timestamp() - interval '9 minutes'
);

insert into private.official_x_daily_slots (
    workspace_id, kst_date, client_id, slot, job_id
) values (
    'e0000000-0000-4000-8000-000000000001',
    pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date,
    'squid',
    1,
    'e3000000-0000-4000-8000-000000000001'
);

insert into private.official_x_style_reference_packs (
    workspace_id, client_id, request_id, primary_source_item_id,
    style_references, reference_pack_hash
) values (
    'e0000000-0000-4000-8000-000000000001',
    'squid',
    'e4000000-0000-4000-8000-000000000001',
    'e2000000-0000-4000-8000-000000000001',
    '[]'::jsonb,
    md5('[]'::jsonb::text)
);
