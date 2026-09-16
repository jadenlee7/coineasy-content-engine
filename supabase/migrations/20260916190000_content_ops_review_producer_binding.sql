-- Align the private-review reader with complete_review_draft_job's existing
-- request/output contract. No job backfill, delivery, enablement or scope change.
begin;

create or replace function private.content_ops_review_candidate(
    target_workspace_id uuid, target_content_item_id uuid, target_content_version_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    source public.source_items%rowtype;
    feed public.source_feeds%rowtype;
    generation public.jobs%rowtype;
    banner public.assets%rowtype;
    expected_handle text;
    effective_date date := pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date;
    telegram_copy text;
    x_copy text;
    decision_now timestamptz;
begin
    select candidate.* into item from public.content_items as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.id = target_content_item_id
    for update;
    if not found or item.client_id not in ('yellow', 'origintrail', 'squid', 'babylon')
       or item.content_kind is distinct from 'daily_news'
       or item.status is distinct from 'needs_review'
       or item.current_version_id is distinct from target_content_version_id then
        return null;
    end if;
    perform 1 from public.workspace_clients as client
    where client.workspace_id = target_workspace_id and client.client_id = item.client_id
      and client.active is true for share;
    if not found then return null; end if;
    select candidate.* into version from public.content_versions as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.content_item_id = item.id and candidate.id = target_content_version_id
    for share;
    if not found or version.generation_meta -> 'mock_mode' is distinct from 'false'::jsonb
       or version.generation_meta ->> 'request_id' is distinct from item.id::text
       or pg_catalog.timezone('Asia/Seoul', version.created_at)::date <> effective_date then
        return null;
    end if;
    expected_handle := case item.client_id
        when 'yellow' then '@Yellow' when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter' when 'babylon' then '@babylonlabs_io' end;

    perform 1 from public.content_source_links as link
    where link.workspace_id = target_workspace_id and link.content_item_id = item.id
    order by link.source_item_id for share;
    if (select count(*) from public.content_source_links as link
        where link.workspace_id = target_workspace_id and link.client_id = item.client_id
          and link.content_item_id = item.id and link.position = 0) <> 1 then
        return null;
    end if;
    select candidate.* into source from public.source_items as candidate
    join public.content_source_links as link
      on link.workspace_id = candidate.workspace_id and link.client_id = candidate.client_id
     and link.source_item_id = candidate.id
    where link.workspace_id = target_workspace_id and link.client_id = item.client_id
      and link.content_item_id = item.id and link.position = 0
    for share of candidate;
    select candidate.* into feed from public.source_feeds as candidate
    where candidate.workspace_id = target_workspace_id and candidate.client_id = item.client_id
      and candidate.id = source.source_feed_id for update;
    if not found or source.source_type is distinct from 'tweet'
       or source.author_handle is distinct from expected_handle
       or source.canonical_url is null
       or source.canonical_url !~ ('^https://x\.com/' || substring(expected_handle from 2)
            || '/status/[0-9]{1,19}$')
       or source.external_id is distinct from split_part(source.canonical_url, '/', 6)
       or source.published_at is null
       or source.published_at <= clock_timestamp() - interval '24 hours'
       or source.published_at > clock_timestamp()
       or feed.provider is distinct from 'x' or feed.handle is distinct from expected_handle
       or feed.active is not true or feed.poll_interval_minutes is distinct from 15
       or feed.last_polled_at is null
       or feed.last_polled_at < clock_timestamp() - interval '15 minutes'
       or feed.last_polled_at > clock_timestamp() then return null; end if;
    perform 1 from public.source_items as candidate
    where candidate.workspace_id = target_workspace_id and candidate.client_id = item.client_id
      and candidate.source_feed_id = feed.id order by candidate.id for share;
    if source.id is distinct from (
        select candidate.id from public.source_items as candidate
        where candidate.workspace_id = target_workspace_id and candidate.client_id = item.client_id
          and candidate.source_feed_id = feed.id and candidate.source_type = 'tweet'
        order by candidate.published_at desc nulls last, candidate.id desc limit 1
    ) then return null; end if;

    -- The production completion RPC writes the authoritative request/output IDs,
    -- not jobs.content_item_id. Lock and count every possible producer binding;
    -- conflicting or duplicate producers (even failed ones) remain ineligible.
    perform 1 from public.jobs as candidate
    where candidate.workspace_id = target_workspace_id and candidate.client_id = item.client_id
      and candidate.job_kind = 'generate'
      and (candidate.content_item_id = item.id
        or candidate.input ->> 'request_id' = item.id::text
        or candidate.output ->> 'content_item_id' = item.id::text)
    order by candidate.id for share;
    -- Ambiguous producer sets are never guessed, including a second failed job.
    if (select count(*) from public.jobs as candidate
        where candidate.workspace_id = target_workspace_id and candidate.client_id = item.client_id
          and candidate.job_kind = 'generate'
          and (candidate.content_item_id = item.id
            or candidate.input ->> 'request_id' = item.id::text
            or candidate.output ->> 'content_item_id' = item.id::text)) <> 1 then
        return null;
    end if;
    select candidate.* into generation from public.jobs as candidate
    where candidate.workspace_id = target_workspace_id and candidate.client_id = item.client_id
      and candidate.job_kind = 'generate'
      and (candidate.content_item_id is null or candidate.content_item_id = item.id)
      and candidate.status = 'succeeded'
      and candidate.input ->> 'workflow' = 'official_x_review_draft_v1'
      and candidate.input ->> 'content_kind' = 'daily_news'
      and candidate.input -> 'manual_only' = 'false'::jsonb
      and candidate.input ->> 'kst_date' = effective_date::text
      and candidate.input ->> 'request_id' = item.id::text
      and candidate.input -> 'source_item_ids' = jsonb_build_array(source.id::text)
      and candidate.output ->> 'content_item_id' = item.id::text
      and candidate.output ->> 'content_version_id' = version.id::text
      and candidate.output -> 'source_item_ids' = jsonb_build_array(source.id::text)
      and pg_catalog.timezone('Asia/Seoul', candidate.finished_at)::date = effective_date;
    if not found then return null; end if;
    perform 1 from private.official_x_daily_slots as slot
    where slot.workspace_id = target_workspace_id and slot.client_id = item.client_id
      and slot.kst_date = effective_date and slot.job_id = generation.id for share;
    if not found then return null; end if;

    perform 1 from public.assets as candidate
    where candidate.workspace_id = target_workspace_id and candidate.content_item_id = item.id
      and candidate.content_version_id = version.id order by candidate.id for share;
    if (select count(*) from public.assets as candidate
        where candidate.workspace_id = target_workspace_id and candidate.content_item_id = item.id
          and candidate.content_version_id = version.id and candidate.asset_kind = 'png') <> 1 then
        return null;
    end if;
    select candidate.* into banner from public.assets as candidate
    join storage.objects as stored
      on stored.bucket_id = candidate.storage_bucket and stored.name = candidate.storage_path
    where candidate.workspace_id = target_workspace_id and candidate.content_item_id = item.id
      and candidate.content_version_id = version.id
      and candidate.id::text = version.deliverables ->> 'primary_asset_id'
      and candidate.asset_kind = 'png' and candidate.mime_type = 'image/png'
      and candidate.storage_bucket = 'content-studio'
      and candidate.metadata ->> 'filename' = 'news-card.png'
      and candidate.storage_path = target_workspace_id::text || '/' || item.client_id
            || '/' || candidate.id::text || '/news-card.png'
      and candidate.sha256 ~ '^[a-f0-9]{64}$'
      and candidate.byte_size > 0 and candidate.width > 0 and candidate.height > 0
    for share of candidate, stored;
    if not found then return null; end if;
    decision_now := clock_timestamp();
    if source.published_at <= decision_now - interval '24 hours'
       or source.published_at > decision_now
       or feed.last_polled_at < decision_now - interval '15 minutes'
       or feed.last_polled_at > decision_now
       or exists (select 1 from public.approvals as approval
        where approval.workspace_id = target_workspace_id and approval.content_item_id = item.id)
       or exists (select 1 from public.publications as publication
        where publication.workspace_id = target_workspace_id and publication.content_item_id = item.id)
       or pg_catalog.timezone('Asia/Seoul', decision_now)::date <> effective_date then
        return null;
    end if;
    telegram_copy := version.channel_copy ->> 'telegram';
    x_copy := version.channel_copy ->> 'x';
    if jsonb_typeof(version.channel_copy -> 'telegram') is distinct from 'string'
       or jsonb_typeof(version.channel_copy -> 'x') is distinct from 'string'
       or btrim(telegram_copy) = '' or btrim(x_copy) = '' or btrim(version.title) = '' then
        return null;
    end if;
    return jsonb_build_object(
        'client_id', item.client_id, 'kst_date', effective_date,
        'content_item_id', item.id, 'content_version_id', version.id,
        'source_item_id', source.id, 'generate_job_id', generation.id,
        'banner_asset_id', banner.id, 'banner_sha256', banner.sha256,
        'title', left(version.title, 160), 'telegram_copy', left(telegram_copy, 2000),
        'x_copy', left(x_copy, 600), 'source_url', source.canonical_url,
        'source_published_at', source.published_at
    );
end;
$$;

revoke all on function private.content_ops_review_candidate(uuid,uuid,uuid)
    from public, anon, authenticated, service_role;
commit;
