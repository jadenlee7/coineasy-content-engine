\set ON_ERROR_STOP on

begin;
set local deadlock_timeout = '200ms';
set local lock_timeout = '5s';
set local statement_timeout = '10s';

select id
from public.source_feeds
where workspace_id = 'cc100000-0000-4000-8000-000000000001'
  and id = 'cc110000-0000-4000-8000-000000000001'
for update;

-- The recorder starts only after this session owns the official-feed lock.
select pg_catalog.pg_advisory_lock(20260830, 2);
select pg_catalog.pg_sleep(2.5);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, source_hash
) values (
    'cc120000-0000-4000-8000-000000000002',
    'cc100000-0000-4000-8000-000000000001',
    'squid', 'cc110000-0000-4000-8000-000000000001',
    '2083266484789514778', 'tweet',
    'https://x.com/SquidRouter/status/2083266484789514778',
    '@SquidRouter', statement_timestamp() - interval '30 minutes',
    'A newer official source committed by the concurrent poll.',
    'content-qa-concurrency:squid:2083266484789514778'
);

update public.source_feeds
set last_cursor = '2083266484789514778',
    last_polled_at = clock_timestamp()
where workspace_id = 'cc100000-0000-4000-8000-000000000001'
  and id = 'cc110000-0000-4000-8000-000000000001';

commit;
