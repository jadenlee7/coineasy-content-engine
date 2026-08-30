\set ON_ERROR_STOP on

-- This fixture is disposable and every mutating test transaction rolls back.
-- Refuse to hide a leaked Content QA receipt before removing the remaining
-- deterministic rows.
do $cleanup$
begin
    if exists (
        select 1
        from private.content_qa_jobs
        where workspace_id = 'cc100000-0000-4000-8000-000000000001'
    ) then
        raise exception 'Content QA concurrency test leaked a durable receipt';
    end if;
end
$cleanup$;

delete from private.grok_qa_verdict_receipts
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from private.grok_qa_dispatch_outbox
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.approvals
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.publications
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.event_log
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.assets
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from storage.objects
where bucket_id = 'content-studio'
  and name like 'cc100000-0000-4000-8000-000000000001/%';

delete from public.jobs
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

update public.content_items
set current_version_id = null
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.content_source_links
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.content_versions
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.content_items
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.source_items
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.source_feeds
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.workspace_clients
where workspace_id = 'cc100000-0000-4000-8000-000000000001';

delete from public.workspaces
where id = 'cc100000-0000-4000-8000-000000000001';
