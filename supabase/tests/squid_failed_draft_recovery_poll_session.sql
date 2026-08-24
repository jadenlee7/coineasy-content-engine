\set ON_ERROR_STOP on

begin;

select id
from public.source_feeds
where id = 'e1000000-0000-4000-8000-000000000001'
for update;

-- The shell harness observes this session lock only after the feed row lock is
-- held, then starts the concurrent recovery inspection.
select pg_catalog.pg_advisory_lock(20260825, 1);
select pg_catalog.pg_sleep(2);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, media, raw_payload,
    source_hash
) values (
    'e2000000-0000-4000-8000-000000000002',
    'e0000000-0000-4000-8000-000000000001',
    'squid',
    'e1000000-0000-4000-8000-000000000001',
    '2091935028565459432',
    'tweet',
    'https://x.com/SquidRouter/status/2091935028565459432',
    '@SquidRouter',
    statement_timestamp() - interval '30 minutes',
    'A newer official Squid source committed by the concurrent poll.',
    '[]'::jsonb,
    '{}'::jsonb,
    repeat('b', 64)
);

update public.source_feeds
set last_cursor = '2091935028565459432',
    last_polled_at = clock_timestamp()
where id = 'e1000000-0000-4000-8000-000000000001';

commit;
