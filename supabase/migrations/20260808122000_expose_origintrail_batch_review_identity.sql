-- Expose the stable catalog identity for a verified OriginTrail Batch result.
--
-- The request UUID becomes the Content Studio content item UUID. The single
-- source UUID is exposed only after the immutable X Article body, URL, and
-- SHA-256 evidence all agree. Existing callers keep the same RPC signature.

begin;

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
    join private.origintrail_x_article_evidence as article_evidence
     on article_evidence.workspace_id = source.workspace_id
     and article_evidence.client_id = source.client_id
     and article_evidence.source_item_id = source.id
     and article_evidence.external_id = source.external_id
     and article_evidence.first_poll_request_id
            = standalone.first_poll_request_id
     and source.canonical_url = 'https://x.com/origin_trail/status/'
            || article_evidence.external_id
     and article_evidence.source_content_sha256 = encode(
            extensions.digest(convert_to(source.body, 'UTF8'), 'sha256'),
            'hex'
         )
     and position('[X Article]' in source.body) > 0
     and position('Title: ' || article_evidence.title in source.body) > 0
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

revoke all on function public.get_agent_batch_review_item(uuid, uuid)
from public, anon, authenticated;

commit;
