-- Allow a verified, media-free standalone OriginTrail status to materialize a
-- review pack without weakening the existing X Article provenance path.
-- The shared predicate is anchored to the immutable normalized source row,
-- its durable standalone marker, and the exact first poll receipt.

begin;

create or replace function private.origintrail_source_evidence_kind(
    target_workspace_id uuid,
    target_source_item_id uuid,
    target_first_poll_request_id uuid
)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    source public.source_items%rowtype;
    article private.origintrail_x_article_evidence%rowtype;
    content_sha256 text;
begin
    if target_workspace_id is null
       or target_source_item_id is null
       or target_first_poll_request_id is null then
        return null;
    end if;

    select current_source.*
    into source
    from public.source_items as current_source
    join private.origintrail_standalone_sources as marker
      on marker.workspace_id = current_source.workspace_id
     and marker.client_id = current_source.client_id
     and marker.source_item_id = current_source.id
    where current_source.workspace_id = target_workspace_id
      and current_source.client_id = 'origintrail'
      and current_source.id = target_source_item_id
      and current_source.source_type = 'tweet'
      and current_source.author_handle = '@origin_trail'
      and current_source.canonical_url = 'https://x.com/origin_trail/status/'
            || current_source.external_id
      and marker.is_quote is false
      and marker.first_poll_request_id = target_first_poll_request_id;
    if not found then
        return null;
    end if;

    if not exists (
        select 1
        from private.official_x_poll_receipts as receipt
        where receipt.workspace_id = target_workspace_id
          and receipt.client_id = 'origintrail'
          and receipt.poll_request_id = target_first_poll_request_id
          and target_source_item_id = any(receipt.source_item_ids)
    ) then
        return null;
    end if;

    content_sha256 := encode(extensions.digest(
        pg_catalog.convert_to(source.body, 'UTF8'), 'sha256'
    ), 'hex');

    select evidence.* into article
    from private.origintrail_x_article_evidence as evidence
    where evidence.workspace_id = target_workspace_id
      and evidence.client_id = 'origintrail'
      and evidence.source_item_id = target_source_item_id
      and evidence.external_id = source.external_id
      and evidence.first_poll_request_id = target_first_poll_request_id
      and evidence.source_content_sha256 = content_sha256
      and evidence.article_url = 'https://x.com/i/article/' || evidence.article_id
      and evidence.retrieval_method in ('x_api_timeline', 'x_api_post_lookup')
      and position('[X Article]' in source.body) > 0
      and position('Title: ' || evidence.title in source.body) > 0;
    if found then
        return 'x_article';
    end if;

    if source.media = '[]'::jsonb
       and position('[X Article]' in source.body) = 0
       and char_length(source.body) between 10 and 20000
       and btrim(regexp_replace(
            source.body, 'https?://[^[:space:]]+', '', 'g'
       )) <> '' then
        return 'x_post_text';
    end if;
    return null;
exception when others then
    return null;
end;
$$;

revoke all on function private.origintrail_source_evidence_kind(uuid, uuid, uuid)
from public, anon, authenticated, service_role;

create or replace function public.get_agent_batch_review_item(
    target_workspace_id uuid,
    target_job_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    item jsonb;
begin
    if target_workspace_id is null
       or target_job_id is null
       or not exists (
           select 1
           from public.workspaces as workspace
           where workspace.id = target_workspace_id
       ) then
        raise exception 'agent Batch review item request is invalid'
            using errcode = '22023';
    end if;

    select jsonb_build_object(
        'job_id', batch_job.job_id,
        'request_id', input_document ->> 'request_id',
        'source_item_ids', review_job.input -> 'source_item_ids',
        'client_id', batch_job.client_id,
        'agent_id', batch_job.agent_id,
        'workflow_kind', batch_job.workflow_kind,
        'stage', batch_job.stage,
        'status', batch_job.status,
        'model', batch_job.model,
        'model_tier', batch_job.model_tier,
        'title', btrim(batch_job.result_payload ->> 'headline_ko'),
        'result_code', batch_job.result_code,
        'actual_cost_microusd', batch_job.actual_cost_microusd,
        'finished_at', batch_job.finished_at,
        'source_url', review_job.input ->> 'source_url',
        'source_content', input_document -> 'source' ->> 'content',
        'source_evidence_kind', source_evidence.kind,
        'result_payload', batch_job.result_payload,
        'result_sha256', encode(extensions.digest(
            convert_to(batch_job.result_payload::text, 'UTF8'),
            'sha256'
        ), 'hex'),
        'input_sha256', batch_job.input_sha256,
        'actual_input_tokens', batch_job.actual_input_tokens,
        'actual_output_tokens', batch_job.actual_output_tokens
    )
    into item
    from agent_runtime.batch_jobs as batch_job
    join public.jobs as review_job
      on review_job.id = batch_job.job_id
     and review_job.workspace_id = batch_job.workspace_id
     and review_job.client_id = batch_job.client_id
    cross join lateral (
        select (batch_job.input_payload ->> 'input')::jsonb as input_document
    ) as parsed
    join lateral (
        select source.*
        from public.source_items as source
        where jsonb_typeof(review_job.input -> 'source_item_ids') = 'array'
          and jsonb_array_length(review_job.input -> 'source_item_ids') = 1
          and source.id::text = review_job.input -> 'source_item_ids' ->> 0
          and source.workspace_id = review_job.workspace_id
          and source.client_id = 'origintrail'
    ) as source on true
    join private.origintrail_standalone_sources as standalone
      on standalone.workspace_id = source.workspace_id
     and standalone.client_id = source.client_id
     and standalone.source_item_id = source.id
     and standalone.is_quote is false
    join lateral (
        select private.origintrail_source_evidence_kind(
            source.workspace_id,
            source.id,
            standalone.first_poll_request_id
        ) as kind
    ) as source_evidence on source_evidence.kind is not null
    where batch_job.workspace_id = target_workspace_id
      and batch_job.job_id = target_job_id
      and batch_job.client_id = 'origintrail'
      and batch_job.agent_id = 'origintrail_client_agent'
      and batch_job.workflow_kind = 'official_source_nonurgent_pack'
      and batch_job.stage = 'generate'
      and batch_job.status = 'completed'
      and batch_job.reservation_state = 'settled'
      and batch_job.result_code = 'needs_review'
      and batch_job.input_payload -> 'approval_required' = 'true'::jsonb
      and batch_job.input_payload -> 'input_immutable' = 'true'::jsonb
      and batch_job.input_payload -> 'source_snapshot_complete' = 'true'::jsonb
      and jsonb_typeof(input_document) = 'object'
      and input_document ->> 'client_id' = 'origintrail'
      and input_document ->> 'content_kind' = 'daily_news'
      and coalesce(
            (input_document ->> 'request_id')
              ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            false
          )
      and input_document ->> 'request_id'
            is not distinct from review_job.input ->> 'request_id'
      and jsonb_typeof(input_document -> 'source') = 'object'
      and input_document -> 'source' ->> 'url'
            is not distinct from review_job.input ->> 'source_url'
      and input_document -> 'source' ->> 'url'
            is not distinct from source.canonical_url
      and input_document -> 'source' ->> 'content'
            is not distinct from review_job.input ->> 'source_content'
      and input_document -> 'source' ->> 'content'
            is not distinct from source.body
      and char_length(input_document -> 'source' ->> 'content')
            between 10 and 60000
      and btrim(regexp_replace(
            input_document -> 'source' ->> 'content',
            'https?://[^[:space:]]+',
            '',
            'g'
          )) <> ''
      and input_document -> 'source' ->> 'content_sha256'
            = encode(extensions.digest(
                convert_to(input_document -> 'source' ->> 'content', 'UTF8'),
                'sha256'
              ), 'hex')
      and agent_runtime.origintrail_review_is_text_only(batch_job.job_id)
      and batch_job.result_payload ?& array[
          'headline_ko', 'body_ko', 'x_copy_ko', 'telegram_copy_ko'
      ]::text[]
      and (select count(*) from jsonb_object_keys(batch_job.result_payload)) = 4
      and jsonb_typeof(batch_job.result_payload -> 'headline_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'body_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'x_copy_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'telegram_copy_ko') = 'string'
      and char_length(batch_job.result_payload ->> 'headline_ko') between 1 and 120
      and char_length(batch_job.result_payload ->> 'body_ko') between 1 and 1800
      and char_length(batch_job.result_payload ->> 'x_copy_ko') between 1 and 500
      and char_length(batch_job.result_payload ->> 'telegram_copy_ko') between 1 and 1024
      and (batch_job.result_payload ->> 'headline_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'body_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'x_copy_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'telegram_copy_ko') ~ '[^[:space:]]'
      and review_job.job_kind = 'generate'
      and review_job.status = 'succeeded'
      and review_job.content_item_id is null
      and review_job.input ->> 'workflow' = 'official_x_review_draft_v1'
      and review_job.input ->> 'content_kind' = 'daily_news'
      and review_job.input -> 'manual_only' = 'false'::jsonb
      and review_job.output = jsonb_build_object(
          'workflow', 'agent_batch_review_handoff_v1',
          'handoff', 'openai_batch',
          'batch_job_id', batch_job.job_id,
          'input_sha256', batch_job.input_sha256,
          'review_state', 'pending'
      );

    return item;
exception
    when invalid_text_representation then
        return null;
end;
$$;

-- Review actions consume the exact same review-item predicate, then verify the
-- immutable review-pack and PNG binding. This removes the former duplicated
-- X-Article-only source query and prevents policy drift.
create or replace function private.origintrail_buzz_review_evidence_ready(
    target_workspace_id uuid,
    target_job_id uuid
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    review_detail jsonb;
    review_source_item_id uuid;
    review_pack_count integer;
begin
    review_detail := public.get_agent_batch_review_item(
        target_workspace_id, target_job_id
    );
    if review_detail is null
       or review_detail ->> 'source_evidence_kind'
            not in ('x_article', 'x_post_text')
       or jsonb_typeof(review_detail -> 'source_item_ids') is distinct from 'array'
       or jsonb_array_length(review_detail -> 'source_item_ids') <> 1 then
        return false;
    end if;
    begin
        review_source_item_id :=
            (review_detail -> 'source_item_ids' ->> 0)::uuid;
    exception when others then
        return false;
    end;

    select count(*) into review_pack_count
    from agent_runtime.origintrail_batch_review_packs as review_pack
    join public.content_items as content_item
      on content_item.workspace_id = review_pack.workspace_id
     and content_item.id = review_pack.content_item_id
     and content_item.client_id = 'origintrail'
     and content_item.content_kind = 'daily_news'
     and content_item.status = 'needs_review'
     and content_item.current_version_id = review_pack.content_version_id
    join public.content_versions as content_version
      on content_version.workspace_id = review_pack.workspace_id
     and content_version.content_item_id = review_pack.content_item_id
     and content_version.id = review_pack.content_version_id
     and content_version.prompt_version = 'origintrail-batch-review-pack@1'
    join public.assets as asset
      on asset.workspace_id = review_pack.workspace_id
     and asset.content_item_id = review_pack.content_item_id
     and asset.content_version_id = review_pack.content_version_id
     and asset.id = review_pack.asset_id
     and asset.sha256 = review_pack.banner_sha256
     and asset.asset_kind = 'png'
     and asset.mime_type = 'image/png'
     and asset.width = 1200
     and asset.height = 630
    where review_pack.workspace_id = target_workspace_id
      and review_pack.job_id = target_job_id
      and review_pack.content_item_id::text = review_detail ->> 'request_id'
      and review_pack.source_item_id = review_source_item_id
      and review_pack.input_sha256 = review_detail ->> 'input_sha256'
      and review_pack.result_sha256 = review_detail ->> 'result_sha256'
      and review_pack.source_content_sha256 = encode(extensions.digest(
            pg_catalog.convert_to(review_detail ->> 'source_content', 'UTF8'),
            'sha256'
          ), 'hex')
      and review_pack.protocol_version = 'origintrail-review-pack@1'
      and review_pack.review_pack_sha256 = private.origintrail_review_pack_sha256(
            review_pack.workspace_id,
            review_pack.job_id,
            review_pack.content_item_id,
            review_pack.source_item_id,
            review_pack.input_sha256,
            review_pack.result_sha256,
            review_pack.source_content_sha256,
            review_pack.banner_sha256
          );
    return review_pack_count = 1;
exception when others then
    return false;
end;
$$;

revoke all on function private.origintrail_buzz_review_evidence_ready(uuid, uuid)
from public, anon, authenticated, service_role;

commit;
