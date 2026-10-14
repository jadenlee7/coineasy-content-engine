-- DISPOSABLE TEST ONLY: exact pre-anchor hosted function body, not an apply script.
create or replace function public.get_quality_banner_generation_context(
    target_workspace_id uuid, target_client_id text, target_request_id uuid
) returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare result jsonb;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'quality_banner_service_role_required' using errcode='42501';
    end if;
    if target_workspace_id is null or target_request_id is null
       or target_client_id not in ('yellow','babylon','origintrail') then return null; end if;
    select jsonb_build_object(
        'job_id',job.id,'request_id',target_request_id,'workspace_id',job.workspace_id,
        'client_id',job.client_id,'source_item_id',source.id,
        'source_published_at',source.published_at,
        'source_sha256',encode(extensions.digest(convert_to(source.body,'UTF8'),'sha256'),'hex'),
        'source_content',source.body,'source_url',source.canonical_url,
        'source_image_url',job.input->>'source_image_url') into result
    from private.official_x_daily_slots as slot
    join public.jobs as job on job.id=slot.job_id and job.workspace_id=slot.workspace_id
      and job.client_id=slot.client_id
    join private.official_x_source_state as state on state.queued_job_id=job.id
      and state.workspace_id=job.workspace_id and state.client_id=job.client_id
    join public.source_items as source on source.id=state.source_item_id
      and source.workspace_id=job.workspace_id and source.client_id=job.client_id
    join public.source_feeds as feed on feed.id=source.source_feed_id
      and feed.workspace_id=source.workspace_id and feed.client_id=source.client_id
    join public.workspace_clients as client on client.workspace_id=job.workspace_id
      and client.client_id=job.client_id and client.active is true
    join private.official_x_style_reference_packs as pack on pack.workspace_id=job.workspace_id
      and pack.client_id=job.client_id and pack.request_id=target_request_id
      and pack.primary_source_item_id=source.id
    where slot.workspace_id=target_workspace_id and slot.client_id=target_client_id
      and slot.kst_date=timezone('Asia/Seoul',statement_timestamp())::date
      and job.job_kind='generate' and job.status='running' and job.content_item_id is null
      and job.locked_by is not null and job.lease_expires_at>statement_timestamp()
      and job.attempts between 1 and job.max_attempts
      and job.input->>'workflow'='official_x_review_draft_v1'
      and job.input->>'content_kind'='daily_news' and job.input->'manual_only'='false'::jsonb
      and job.input->>'request_id'=target_request_id::text
      and job.input->>'kst_date'=slot.kst_date::text
      and job.input->'source_item_ids'=jsonb_build_array(source.id::text)
      and job.input->>'source_content'=source.body
      and job.input->>'source_url'=source.canonical_url
      and (
        -- Existing attached-media admission remains available unchanged.
        (job.input->>'source_image_url' ~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./?=&%:+-]+$'
          and exists(select 1 from jsonb_array_elements(
              case when jsonb_typeof(source.media)='array' then source.media else '[]'::jsonb end
            ) as media(value)
            where media.value->>'type' in ('photo','video','animated_gif')
              and media.value->>'url'=job.input->>'source_image_url'))
        or (
          -- Trial-only official-logo path: genuine empty media and an explicit
          -- empty JSON string, never NULL/missing/non-string/omitted evidence.
          source.media='[]'::jsonb and job.input->'source_image_url'='""'::jsonb
          and statement_timestamp()>='2026-10-14T15:10:51Z'::timestamptz
          and statement_timestamp()<'2026-10-14T17:10:51Z'::timestamptz
          and slot.kst_date='2026-10-15'::date
          and job.created_at>='2026-10-14T15:10:51Z'::timestamptz
          and job.created_at<=statement_timestamp()
          and job.attempts=1 and job.max_attempts=3
          and job.lease_expires_at<='2026-10-14T17:10:51Z'::timestamptz
          and job.idempotency_key='official-x-review:v1:2026-10-15:'||target_client_id
          and job.input=jsonb_build_object(
            'workflow','official_x_review_draft_v1','kst_date',slot.kst_date,
            'source_item_ids',jsonb_build_array(source.id::text),'content_kind','daily_news',
            'request_id',target_request_id,'source_content',source.body,
            'source_url',source.canonical_url,'source_image_url','','manual_only',false)
        )
      )
      and source.source_type='tweet'
      and source.author_handle=case target_client_id when 'yellow' then '@Yellow'
          when 'babylon' then '@babylonlabs_io' when 'origintrail' then '@origin_trail' end
      and source.canonical_url ~ ('^https://x\.com/'||substring(source.author_handle from 2)||'/status/[0-9]{1,19}$')
      and source.external_id=split_part(source.canonical_url,'/',6)
      and char_length(source.body) between 10 and 20000
      and source.published_at<=statement_timestamp()
      and source.published_at>statement_timestamp()-interval '24 hours'
      and feed.provider='x' and feed.handle=source.author_handle and feed.active is true
      and feed.poll_interval_minutes=15 and feed.last_polled_at<=statement_timestamp()
      and feed.last_polled_at>statement_timestamp()-interval '30 minutes'
      and source.id=(select newest.id from public.source_items as newest
          where newest.workspace_id=source.workspace_id and newest.client_id=source.client_id
            and newest.source_feed_id=feed.id and newest.source_type='tweet'
          order by newest.published_at desc nulls last,newest.id desc limit 1)
      and not exists(select 1 from public.content_items where id=target_request_id)
      and not exists(select 1 from public.content_source_links where workspace_id=target_workspace_id
          and client_id=target_client_id and source_item_id=source.id)
      and (select count(*) from public.jobs as producer where producer.workspace_id=job.workspace_id
          and producer.client_id=job.client_id and producer.job_kind='generate'
          and (producer.input->>'request_id'=target_request_id::text
            or producer.input->'source_item_ids' @> jsonb_build_array(source.id::text)))=1;
    return result;
end $$;
