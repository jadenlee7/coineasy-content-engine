\set ON_ERROR_STOP on

delete from public.source_items
where id = 'e2000000-0000-4000-8000-000000000002';

update public.source_feeds
set last_cursor = '2091935028565459431',
    last_polled_at = clock_timestamp()
where id = 'e1000000-0000-4000-8000-000000000001';

begin;

select id
from public.source_feeds
where id = 'e1000000-0000-4000-8000-000000000001'
for update;

select pg_catalog.pg_advisory_lock(20260825, 2);
select pg_catalog.pg_sleep(2);

update public.source_feeds
set last_polled_at = clock_timestamp()
where id = 'e1000000-0000-4000-8000-000000000001';

commit;
