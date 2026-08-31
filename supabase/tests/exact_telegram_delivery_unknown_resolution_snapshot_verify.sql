\set ON_ERROR_STOP on
set timezone = 'UTC';

do $verify_snapshot_attempts_preserved_all_evidence$
declare
    target exact_telegram_resolution_concurrency.fixture%rowtype;
begin
    select * into strict target
    from exact_telegram_resolution_concurrency.fixture
    where singleton;
    if (select pg_catalog.to_jsonb(item)
        from public.content_items as item
        where item.id = target.content_item_id)
            is distinct from target.item_before
       or (select pg_catalog.to_jsonb(version)
           from public.content_versions as version
           where version.id = target.content_version_id)
            is distinct from target.version_before
       or (select pg_catalog.to_jsonb(publication)
           from public.publications as publication
           where publication.id = target.publication_id)
            is distinct from target.publication_before
       or (select pg_catalog.to_jsonb(job)
           from public.jobs as job
           where job.id = target.job_id)
            is distinct from target.job_before
       or (select pg_catalog.to_jsonb(approval)
           from public.approvals as approval
           where approval.id = (
               target.publication_before -> 'request_payload' ->> 'approval_id'
           )::uuid)
            is distinct from target.publication_approval_before
       or (select pg_catalog.to_jsonb(asset)
           from public.assets as asset
           where asset.id = (
               target.publication_before -> 'request_payload' ->> 'asset_id'
           )::uuid)
            is distinct from target.asset_before then
        raise exception 'stale-snapshot attempt changed original forensic rows';
    end if;

    if (select pg_catalog.to_jsonb(approval)
        from private.exact_telegram_delivery_unknown_approvals as approval
        where approval.workspace_id = target.workspace_id
          and approval.operator_approval_id = target.operator_approval_id)
            is distinct from target.resolution_approval_before then
        raise exception 'stale-snapshot attempt changed durable approval';
    end if;
    if (select count(*)
        from private.exact_telegram_delivery_unknown_resolutions
        where workspace_id = target.workspace_id) <> 1
       or (select count(*)
           from private.exact_telegram_delivery_unknown_approvals
           where workspace_id = target.workspace_id) <> 1
       or (select count(*) from public.event_log
           where workspace_id = target.workspace_id
             and event_type =
                'exact_telegram_delivery_unknown_resolved_without_resend') <> 1
       or (select count(*) from public.event_log
           where workspace_id = target.workspace_id
             and event_type =
                'exact_telegram_delivery_unknown_resolution_approved') <> 1
       or (select count(*) from public.publications
           where workspace_id = target.workspace_id)
            <> target.publication_count_before
       or (select count(*) from public.jobs
           where workspace_id = target.workspace_id)
            <> target.job_count_before then
        raise exception 'stale-snapshot attempt duplicated receipt/event/job/publication';
    end if;
end
$verify_snapshot_attempts_preserved_all_evidence$;
