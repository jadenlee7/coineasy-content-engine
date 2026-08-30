\set ON_ERROR_STOP on

begin;

insert into public.workspaces (id, name, slug)
values (
    'cc100000-0000-4000-8000-000000000001',
    'Content QA Concurrency',
    'content-qa-concurrency'
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active
) values (
    'cc100000-0000-4000-8000-000000000001',
    'squid', 'Squid', true
);

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, last_cursor, last_polled_at, active
) values (
    'cc110000-0000-4000-8000-000000000001',
    'cc100000-0000-4000-8000-000000000001',
    'squid', 'x', 'Squid official X', 'https://x.com/SquidRouter',
    '@SquidRouter', 15, '2083266484789514777',
    statement_timestamp() - interval '5 minutes', true
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, source_hash
) values (
    'cc120000-0000-4000-8000-000000000001',
    'cc100000-0000-4000-8000-000000000001',
    'squid', 'cc110000-0000-4000-8000-000000000001',
    '2083266484789514777', 'tweet',
    'https://x.com/SquidRouter/status/2083266484789514777',
    '@SquidRouter', statement_timestamp() - interval '1 hour',
    'Private deterministic source for Content QA concurrency.',
    'content-qa-concurrency:squid:2083266484789514777'
);

insert into public.content_items (
    id, workspace_id, client_id, content_kind, title, status
) values (
    'cc130000-0000-4000-8000-000000000001',
    'cc100000-0000-4000-8000-000000000001',
    'squid', 'daily_news', 'Content QA concurrency candidate',
    'needs_review'
);

insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version,
    locale, title, content, channel_copy, deliverables, qa, generation_meta
) values (
    'cc140000-0000-4000-8000-000000000001',
    'cc100000-0000-4000-8000-000000000001',
    'cc130000-0000-4000-8000-000000000001',
    1, 'content-qa-concurrency@1', 'ko-KR',
    'Content QA concurrency candidate',
    '{"private_copy":"concurrency-only"}'::jsonb,
    '{}'::jsonb,
    jsonb_build_object(
        'primary_asset_id', 'cc160000-0000-4000-8000-000000000001',
        'asset_ids', jsonb_build_array(
            'cc160000-0000-4000-8000-000000000001'
        )
    ),
    '{}'::jsonb,
    '{"mock_mode":false}'::jsonb
);

update public.content_items
set current_version_id = 'cc140000-0000-4000-8000-000000000001'
where workspace_id = 'cc100000-0000-4000-8000-000000000001'
  and id = 'cc130000-0000-4000-8000-000000000001';

insert into public.content_source_links (
    workspace_id, client_id, content_item_id, source_item_id, position
) values (
    'cc100000-0000-4000-8000-000000000001', 'squid',
    'cc130000-0000-4000-8000-000000000001',
    'cc120000-0000-4000-8000-000000000001', 0
);

insert into storage.objects (bucket_id, name)
values (
    'content-studio',
    'cc100000-0000-4000-8000-000000000001/squid/cc160000-0000-4000-8000-000000000001/news-card.png'
);

insert into public.assets (
    id, workspace_id, content_item_id, content_version_id, asset_kind,
    storage_bucket, storage_path, mime_type, byte_size, sha256, width,
    height, metadata
) values (
    'cc160000-0000-4000-8000-000000000001',
    'cc100000-0000-4000-8000-000000000001',
    'cc130000-0000-4000-8000-000000000001',
    'cc140000-0000-4000-8000-000000000001', 'png',
    'content-studio',
    'cc100000-0000-4000-8000-000000000001/squid/cc160000-0000-4000-8000-000000000001/news-card.png',
    'image/png', 128, repeat('a', 64), 1080, 1080,
    '{"filename":"news-card.png"}'::jsonb
);

insert into public.jobs (
    id, workspace_id, client_id, content_item_id, job_kind, status,
    input, output, idempotency_key, finished_at
) values (
    'cc150000-0000-4000-8000-000000000001',
    'cc100000-0000-4000-8000-000000000001', 'squid',
    'cc130000-0000-4000-8000-000000000001', 'generate', 'succeeded',
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'manual_only', false,
        'source_item_ids', jsonb_build_array(
            'cc120000-0000-4000-8000-000000000001'
        )
    ),
    jsonb_build_object(
        'content_item_id', 'cc130000-0000-4000-8000-000000000001',
        'content_version_id', 'cc140000-0000-4000-8000-000000000001',
        'source_item_ids', jsonb_build_array(
            'cc120000-0000-4000-8000-000000000001'
        )
    ),
    'content-qa-concurrency:natural-cron', statement_timestamp()
);

-- The authoritative completion event creates the pristine legacy Grok row
-- used by both two-session races.
insert into public.event_log (
    workspace_id, entity_type, entity_id, event_type, data
) values (
    'cc100000-0000-4000-8000-000000000001', 'content_item',
    'cc130000-0000-4000-8000-000000000001',
    'official_x_review_draft_completed',
    jsonb_build_object(
        'job_id', 'cc150000-0000-4000-8000-000000000001',
        'content_version_id', 'cc140000-0000-4000-8000-000000000001',
        'source_item_ids', jsonb_build_array(
            'cc120000-0000-4000-8000-000000000001'
        )
    )
);

commit;

do $verify$
begin
    if not exists (
        select 1
        from private.grok_qa_dispatch_outbox
        where workspace_id = 'cc100000-0000-4000-8000-000000000001'
          and content_version_id = 'cc140000-0000-4000-8000-000000000001'
          and status = 'pending'
          and attempts = 0
          and provider_attempt_started_at is null
          and verdict is null
    ) then
        raise exception 'Content QA concurrency fixture has no pristine Grok row';
    end if;
end
$verify$;
