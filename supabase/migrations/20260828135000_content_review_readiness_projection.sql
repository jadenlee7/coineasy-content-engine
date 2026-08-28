-- Bounded, exact-version operator evidence for the authenticated Studio.
--
-- This projection deliberately excludes source copy, media URLs, Grok provider
-- responses, private delivery details, and every mutation. It lets the Studio
-- replace a manual multi-ledger audit with one service-role-only read while the
-- existing human approval and publication gates remain unchanged.

begin;

create or replace function public.get_content_review_readiness(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object(
        'content_item_id', item.id,
        'content_version_id', version.id,
        'generate_job_id', generation.job_id,
        'source_item_id', source.source_item_id,
        'source_published_at', source.published_at,
        'source_is_latest', coalesce(source.is_latest, false),
        'source_within_24h', coalesce(
            source.published_at >= statement_timestamp() - interval '24 hours'
            and source.published_at <= statement_timestamp() + interval '5 minutes',
            false
        ),
        'feed_active', coalesce(source.feed_active, false),
        'feed_poll_interval_minutes', source.poll_interval_minutes,
        'feed_last_polled_at', source.last_polled_at,
        'feed_poll_recent', coalesce(
            source.last_polled_at >= statement_timestamp() - greatest(
                interval '30 minutes',
                source.poll_interval_minutes * interval '2 minutes'
            )
            and source.last_polled_at <= statement_timestamp() + interval '5 minutes',
            false
        ),
        'banner_sha256', banner.sha256,
        'grok_outbox_count', case when dispatch.content_version_id is null then 0 else 1 end,
        'grok_status', dispatch.status,
        'grok_decision', dispatch.verdict ->> 'decision',
        'grok_next_action', dispatch.verdict ->> 'next_action',
        'grok_verdict_sha256', dispatch.verdict_sha256,
        'grok_banner_sha256', dispatch.banner_sha256,
        'approval_count', coalesce(approval.total, 0),
        'publication_count', coalesce(publication.total, 0)
    )
    from public.content_items as item
    join public.content_versions as version
      on version.workspace_id = item.workspace_id
     and version.content_item_id = item.id
     and version.id = item.current_version_id
    left join lateral (
        select candidate.source_item_id,
               candidate.published_at,
               candidate.canonical_url,
               candidate.author_handle,
               candidate.feed_active,
               candidate.poll_interval_minutes,
               candidate.last_polled_at,
               candidate.is_latest
        from (
          select
            link.source_item_id as source_item_id,
            source_item.published_at as published_at,
            source_item.canonical_url as canonical_url,
            source_item.author_handle as author_handle,
            feed.active as feed_active,
            feed.poll_interval_minutes as poll_interval_minutes,
            feed.last_polled_at as last_polled_at,
            source_item.id = (
                select latest.id
                from public.source_items as latest
                where latest.workspace_id = source_item.workspace_id
                  and latest.client_id = source_item.client_id
                  and latest.source_feed_id is not distinct from source_item.source_feed_id
                  and latest.source_type = 'tweet'
                order by
                    latest.published_at desc nulls last,
                    latest.id desc
                limit 1
            ) as is_latest
          from public.content_source_links as link
          join public.source_items as source_item
          on source_item.workspace_id = link.workspace_id
         and source_item.client_id = link.client_id
         and source_item.id = link.source_item_id
          join public.source_feeds as feed
          on feed.workspace_id = source_item.workspace_id
         and feed.client_id = source_item.client_id
         and feed.id = source_item.source_feed_id
          where link.workspace_id = item.workspace_id
          and link.client_id = item.client_id
          and link.content_item_id = item.id
          and link.position = 0
          and (
              select count(*)
              from public.content_source_links as all_position_zero
              where all_position_zero.workspace_id = item.workspace_id
                and all_position_zero.client_id = item.client_id
                and all_position_zero.content_item_id = item.id
                and all_position_zero.position = 0
          ) = 1
          and source_item.source_type = 'tweet'
          and source_item.author_handle = case item.client_id
              when 'yellow' then '@Yellow'
              when 'origintrail' then '@origin_trail'
              when 'squid' then '@SquidRouter'
              when 'babylon' then '@babylonlabs_io'
              else null
          end
          and feed.provider = 'x'
          and feed.handle = source_item.author_handle
        ) as candidate
        limit 1
    ) as source on true
    left join lateral (
        select asset.id as asset_id, asset.sha256
        from public.assets as asset
        join storage.objects as stored
          on stored.bucket_id = asset.storage_bucket
         and stored.name = asset.storage_path
        where asset.workspace_id = item.workspace_id
          and asset.content_item_id = item.id
          and asset.content_version_id = version.id
          and asset.id::text = version.deliverables ->> 'primary_asset_id'
          and asset.asset_kind = 'png'
          and asset.storage_bucket = 'content-studio'
          and asset.mime_type = 'image/png'
          and asset.metadata ->> 'filename' = 'news-card.png'
          and asset.storage_path = item.workspace_id::text || '/'
              || item.client_id || '/' || asset.id::text || '/news-card.png'
          and asset.sha256 ~ '^[a-f0-9]{64}$'
        limit 1
    ) as banner on true
    left join private.grok_qa_dispatch_outbox as dispatch
     on dispatch.workspace_id = item.workspace_id
     and dispatch.content_item_id = item.id
     and dispatch.content_version_id = version.id
     and dispatch.client_id = item.client_id
     and dispatch.source_item_id = source.source_item_id
     and dispatch.source_url = source.canonical_url
     and dispatch.source_author_handle = source.author_handle
     and dispatch.source_published_at = source.published_at
    left join lateral (
        select (array_agg(candidate.job_id order by candidate.job_id))[1]
            as job_id
        from (
            select review_job.id as job_id
            from public.event_log as source_event
            join public.jobs as review_job
              on review_job.workspace_id = source_event.workspace_id
             and review_job.id::text = source_event.data ->> 'job_id'
            where dispatch.source_event_type
                    = 'official_x_review_draft_completed'
              and source_event.id = dispatch.source_event_id
              and source_event.workspace_id = item.workspace_id
              and source_event.entity_type = 'content_item'
              and source_event.entity_id = item.id
              and source_event.event_type = dispatch.source_event_type
              and source_event.data ->> 'content_version_id' = version.id::text
              and source_event.data -> 'source_item_ids' is not distinct from (
                  select jsonb_agg(
                      linked_source.source_item_id::text
                      order by linked_source.position
                  )
                  from public.content_source_links as linked_source
                  where linked_source.workspace_id = item.workspace_id
                    and linked_source.client_id = item.client_id
                    and linked_source.content_item_id = item.id
              )
              and review_job.client_id = item.client_id
              and review_job.content_item_id = item.id
              and review_job.job_kind = 'generate'
              and review_job.status = 'succeeded'
              and review_job.input ->> 'workflow'
                    = 'official_x_review_draft_v1'
              and review_job.input -> 'manual_only' = 'false'::jsonb
              and review_job.input -> 'source_item_ids'
                    is not distinct from source_event.data -> 'source_item_ids'
              and review_job.output ->> 'content_item_id' = item.id::text
              and review_job.output ->> 'content_version_id' = version.id::text
              and review_job.output -> 'source_item_ids'
                    is not distinct from source_event.data -> 'source_item_ids'
              and (
                  select count(*)
                  from public.jobs as duplicate_job
                  where duplicate_job.workspace_id = item.workspace_id
                    and duplicate_job.client_id = item.client_id
                    and duplicate_job.content_item_id = item.id
                    and duplicate_job.job_kind = 'generate'
                    and duplicate_job.status = 'succeeded'
                    and duplicate_job.input ->> 'workflow'
                          = 'official_x_review_draft_v1'
                    and duplicate_job.input -> 'manual_only' = 'false'::jsonb
                    and duplicate_job.output ->> 'content_item_id'
                          = item.id::text
                    and duplicate_job.output ->> 'content_version_id'
                          = version.id::text
              ) = 1

            union all

            select review_job.id as job_id
            from public.event_log as source_event
            join agent_runtime.origintrail_batch_review_packs as review_pack
              on review_pack.workspace_id = source_event.workspace_id
             and review_pack.job_id::text = source_event.data ->> 'job_id'
            join agent_runtime.batch_jobs as batch_job
              on batch_job.workspace_id = review_pack.workspace_id
             and batch_job.job_id = review_pack.job_id
             and batch_job.client_id = review_pack.client_id
            join public.jobs as review_job
              on review_job.workspace_id = review_pack.workspace_id
             and review_job.client_id = review_pack.client_id
             and review_job.output = jsonb_build_object(
                  'workflow', 'agent_batch_review_handoff_v1',
                  'handoff', 'openai_batch',
                  'batch_job_id', batch_job.job_id,
                  'input_sha256', batch_job.input_sha256,
                  'review_state', 'pending'
             )
            where item.client_id = 'origintrail'
              and dispatch.source_event_type
                    = 'origintrail_batch_review_pack_materialized'
              and source_event.id = dispatch.source_event_id
              and source_event.workspace_id = item.workspace_id
              and source_event.entity_type = 'content_item'
              and source_event.entity_id = item.id
              and source_event.event_type = dispatch.source_event_type
              and source_event.data ->> 'content_version_id' = version.id::text
              and source_event.data ->> 'source_item_id'
                    = source.source_item_id::text
              and source_event.data ->> 'asset_id' = banner.asset_id::text
              and source_event.data ->> 'banner_sha256' = banner.sha256
              and review_pack.content_item_id = item.id
              and review_pack.content_version_id = version.id
              and review_pack.source_item_id = source.source_item_id
              and review_pack.asset_id = banner.asset_id
              and review_pack.banner_sha256 = banner.sha256
              and batch_job.agent_id = 'origintrail_client_agent'
              and batch_job.workflow_kind = 'official_source_nonurgent_pack'
              and batch_job.stage = 'generate'
              and batch_job.status = 'completed'
              and batch_job.reservation_state = 'settled'
              and batch_job.result_code = 'needs_review'
              and batch_job.input_payload -> 'approval_required' = 'true'::jsonb
              and review_job.content_item_id is null
              and review_job.job_kind = 'generate'
              and review_job.status = 'succeeded'
              and review_job.input ->> 'workflow'
                    = 'official_x_review_draft_v1'
              and review_job.input ->> 'content_kind' = 'daily_news'
              and review_job.input -> 'manual_only' = 'false'::jsonb
              and review_job.input ->> 'source_url' = source.canonical_url
        ) as candidate
        having count(*) = 1
    ) as generation on true
    left join lateral (
        select count(*) as total
        from public.approvals as approval_row
        where approval_row.workspace_id = item.workspace_id
          and approval_row.client_id = item.client_id
          and approval_row.content_item_id = item.id
          and approval_row.content_version_id = version.id
    ) as approval on true
    left join lateral (
        select count(*) as total
        from public.publications as publication_row
        where publication_row.workspace_id = item.workspace_id
          and publication_row.client_id = item.client_id
          and publication_row.content_item_id = item.id
          and publication_row.content_version_id = version.id
    ) as publication on true
    where item.workspace_id = target_workspace_id
      and item.id = target_content_item_id
      and item.current_version_id = target_content_version_id
      and item.content_kind = 'daily_news'
      and item.status = 'needs_review'
$$;

comment on function public.get_content_review_readiness(uuid, uuid, uuid) is
    'Returns bounded exact-version Studio review evidence without source copy, provider responses, delivery payloads, approval, or publication authority.';

revoke all on function public.get_content_review_readiness(uuid, uuid, uuid)
from public, anon, authenticated, service_role;
grant execute on function public.get_content_review_readiness(uuid, uuid, uuid)
to service_role;

commit;
