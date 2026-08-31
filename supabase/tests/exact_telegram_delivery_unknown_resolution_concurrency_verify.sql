-- Post-race proof: one insert winner, one exact replay, no delivery mutation.

\set ON_ERROR_STOP on
set timezone = 'UTC';

do $verify_race$
declare
    target exact_telegram_resolution_concurrency.fixture%rowtype;
    first_result jsonb;
    second_result jsonb;
    resolution_event jsonb;
    deadlocks_after bigint;
begin
    select * into strict target
    from exact_telegram_resolution_concurrency.fixture
    where singleton;
    select result into strict first_result
    from exact_telegram_resolution_concurrency.results
    where session_name = 'first';
    select result into strict second_result
    from exact_telegram_resolution_concurrency.results
    where session_name = 'second';

    if target.second_waited_for_first is not true then
        raise exception 'resolve race did not observe a competing lock wait';
    end if;

    if first_result ->> 'resolved' is distinct from 'true'
       or first_result ->> 'reused' is distinct from 'false'
       or second_result ->> 'resolved' is distinct from 'true'
       or second_result ->> 'reused' is distinct from 'true' then
        raise exception
            'resolve race did not produce one insert and one exact replay';
    end if;

    if exists (
        select 1
        from exact_telegram_resolution_concurrency.results as result
        where result.result ->> 'resolution_id'
                is distinct from target.resolution_id::text
           or result.result ->> 'publication_id'
                is distinct from target.publication_id::text
           or result.result ->> 'job_id' is distinct from target.job_id::text
           or result.result ->> 'content_item_id'
                is distinct from target.content_item_id::text
           or result.result ->> 'content_version_id'
                is distinct from target.content_version_id::text
           or result.result ->> 'publication_status'
                is distinct from 'delivery_unknown'
           or result.result ->> 'job_status' is distinct from 'failed'
           or result.result ->> 'delivery_outcome' is distinct from 'unknown'
           or result.result ->> 'disposition'
                is distinct from 'operator_closed_without_resend'
           or result.result ->> 'public_observation'
                is distinct from 'not_observed_at_checked_at'
           or result.result ->> 'approval_subject_sha256'
                is distinct from target.approval_subject_sha256
           or result.result ->> 'resend_authorized' is distinct from 'false'
           or result.result ->> 'provider_calls' is distinct from '0'
           or result.result ->> 'database_claims' is distinct from '0'
    ) then
        raise exception 'resolve race returned a non-exact or active result';
    end if;

    if (select count(*)
        from private.exact_telegram_delivery_unknown_resolutions as receipt
        where receipt.workspace_id = target.workspace_id
          and receipt.resolution_id = target.resolution_id
          and receipt.publication_id = target.publication_id
          and receipt.job_id = target.job_id
          and receipt.operator_approval_id = target.operator_approval_id
          and receipt.approval_subject_sha256
                = target.approval_subject_sha256) <> 1 then
        raise exception 'resolve race did not leave exactly one receipt';
    end if;

    if (select count(*)
        from private.exact_telegram_delivery_unknown_approvals as approval
        where approval.workspace_id = target.workspace_id
          and approval.operator_approval_id = target.operator_approval_id
          and approval.approval_subject_sha256
                = target.approval_subject_sha256) <> 1 then
        raise exception 'resolve race changed the durable approval cardinality';
    end if;

    if (select pg_catalog.to_jsonb(approval)
        from private.exact_telegram_delivery_unknown_approvals as approval
        where approval.workspace_id = target.workspace_id
          and approval.operator_approval_id = target.operator_approval_id)
            is distinct from target.resolution_approval_before
       or (select count(*)
           from public.event_log as event
           where event.workspace_id = target.workspace_id
             and event.entity_id = target.publication_id
             and event.event_type =
                'exact_telegram_delivery_unknown_resolution_approved') <> 1
    then
        raise exception 'resolve race mutated or duplicated approval evidence';
    end if;

    if (select count(*)
        from public.event_log as event
        where event.workspace_id = target.workspace_id
          and event.entity_id = target.publication_id
          and event.event_type =
            'exact_telegram_delivery_unknown_resolved_without_resend') <> 1 then
        raise exception 'resolve race did not leave exactly one event';
    end if;

    select event.data into strict resolution_event
    from public.event_log as event
    where event.workspace_id = target.workspace_id
      and event.entity_id = target.publication_id
      and event.event_type =
        'exact_telegram_delivery_unknown_resolved_without_resend';
    if resolution_event ->> 'provider_calls' is distinct from '0'
       or resolution_event ->> 'database_claims' is distinct from '0'
       or resolution_event ->> 'resend_authorized' is distinct from 'false'
       or resolution_event ->> 'automatic_publication' is distinct from 'false'
       or resolution_event ->> 'publication_state_changed'
            is distinct from 'false'
       or resolution_event ->> 'job_state_changed' is distinct from 'false' then
        raise exception 'resolve event claims an external or delivery action';
    end if;

    if (select pg_catalog.to_jsonb(item)
        from public.content_items as item
        where item.workspace_id = target.workspace_id
          and item.id = target.content_item_id)
            is distinct from target.item_before
       or (select pg_catalog.to_jsonb(version)
           from public.content_versions as version
           where version.workspace_id = target.workspace_id
             and version.content_item_id = target.content_item_id
             and version.id = target.content_version_id)
            is distinct from target.version_before
       or (select pg_catalog.to_jsonb(publication)
        from public.publications as publication
        where publication.workspace_id = target.workspace_id
          and publication.id = target.publication_id)
            is distinct from target.publication_before
       or (select pg_catalog.to_jsonb(job)
           from public.jobs as job
           where job.workspace_id = target.workspace_id
             and job.id = target.job_id)
            is distinct from target.job_before
       or (select pg_catalog.to_jsonb(approval)
           from public.approvals as approval
           where approval.workspace_id = target.workspace_id
             and approval.id =
                (target.publication_approval_before ->> 'id')::uuid)
            is distinct from target.publication_approval_before
       or (select pg_catalog.to_jsonb(asset)
           from public.assets as asset
           where asset.workspace_id = target.workspace_id
             and asset.id = (target.asset_before ->> 'id')::uuid)
            is distinct from target.asset_before then
        raise exception 'resolve race mutated original forensic evidence';
    end if;

    if (select count(*)
        from public.publications as publication
        where publication.workspace_id = target.workspace_id)
            <> target.publication_count_before
       or (select count(*)
           from public.jobs as job
           where job.workspace_id = target.workspace_id)
            <> target.job_count_before
       or exists (
           select 1
           from public.publications as publication
           where publication.workspace_id = target.workspace_id
             and (
                 publication.status <> 'delivery_unknown'
                 or publication.published_at is not null
                 or publication.external_id is not null
                 or publication.external_url is not null
             )
       ) then
        raise exception 'resolve race created or published delivery work';
    end if;

    select deadlocks into strict deadlocks_after
    from pg_catalog.pg_stat_database
    where datname = pg_catalog.current_database();
    if deadlocks_after <> target.deadlock_count_before then
        raise exception 'resolve race recorded a database deadlock';
    end if;
end
$verify_race$;
