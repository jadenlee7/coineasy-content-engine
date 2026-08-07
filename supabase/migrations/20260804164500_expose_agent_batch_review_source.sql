-- Expose only the immutable, bounded official-source snapshot used by the
-- Batch request. This lets the read-only review UI distinguish a real draft
-- from a URL-only input without exposing the rest of the provider payload.

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
        'client_id', batch_job.client_id,
        'agent_id', batch_job.agent_id,
        'workflow_kind', batch_job.workflow_kind,
        'stage', batch_job.stage,
        'status', batch_job.status,
        'model', batch_job.model,
        'model_tier', batch_job.model_tier,
        'title',
            case
                when jsonb_typeof(
                         batch_job.result_payload -> 'headline_ko'
                     ) = 'string'
                 and char_length(
                         btrim(batch_job.result_payload ->> 'headline_ko')
                     )
                     between 1 and 120
                    then btrim(batch_job.result_payload ->> 'headline_ko')
                else 'OriginTrail Batch review draft'
            end,
        'result_code', batch_job.result_code,
        'actual_cost_microusd', batch_job.actual_cost_microusd,
        'finished_at', batch_job.finished_at,
        'source_url', review_job.input ->> 'source_url',
        'source_content',
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' ->> 'content',
        'result_payload', batch_job.result_payload,
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
      and jsonb_typeof(batch_job.input_payload -> 'input') = 'string'
      and jsonb_typeof(
            (batch_job.input_payload ->> 'input')::jsonb
          ) = 'object'
      and jsonb_typeof(
            (batch_job.input_payload ->> 'input')::jsonb -> 'source'
          ) = 'object'
      and jsonb_typeof(
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' -> 'content'
          ) = 'string'
      and char_length(
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' ->> 'content'
          ) between 1 and 60000
      and (
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' ->> 'content'
          ) ~ '[^[:space:]]'
      and (
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' ->> 'url'
          ) = review_job.input ->> 'source_url'
      and batch_job.result_payload ?& array[
          'headline_ko', 'body_ko', 'x_copy_ko', 'telegram_copy_ko'
      ]::text[]
      and (
          select count(*)
          from jsonb_object_keys(batch_job.result_payload)
      ) = 4
      and jsonb_typeof(batch_job.result_payload -> 'headline_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'body_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'x_copy_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'telegram_copy_ko')
            = 'string'
      and char_length(
          batch_job.result_payload ->> 'headline_ko'
      ) between 1 and 120
      and char_length(
          batch_job.result_payload ->> 'body_ko'
      ) between 1 and 1800
      and char_length(
          batch_job.result_payload ->> 'x_copy_ko'
      ) between 1 and 500
      and char_length(
          batch_job.result_payload ->> 'telegram_copy_ko'
      ) between 1 and 1800
      and (batch_job.result_payload ->> 'headline_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'body_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'x_copy_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'telegram_copy_ko')
            ~ '[^[:space:]]'
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
end;
$$;

revoke all on function public.get_agent_batch_review_item(
    uuid, uuid
) from public, anon, authenticated, service_role;

grant execute on function public.get_agent_batch_review_item(
    uuid, uuid
) to service_role;
