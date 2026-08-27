-- Add one bounded transient Squid localization failure to the existing
-- exact-job recovery contract.  This migration deliberately leaves every
-- current-day, freshness, newest-source, one-claim, and no-publication fence
-- unchanged.

begin;

alter table private.official_x_failed_draft_recovery_grants
    drop constraint official_x_failed_draft_recovery_grants_failure_code_check;

alter table private.official_x_failed_draft_recovery_grants
    add constraint official_x_failed_draft_recovery_grants_failure_code_check
    check (
        failure_code in (
            'squid_visual_localization_incomplete',
            'squid_copy_discovery_unavailable'
        )
    );

create or replace function private.squid_failed_draft_recovery_subject(
    target_workspace_id uuid,
    target_job_id uuid,
    target_recovery_id uuid,
    target_approval_id uuid,
    target_approved_by text,
    target_approved_at timestamptz,
    target_expires_at timestamptz,
    target_release_sha text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    failed_job public.jobs%rowtype;
    source public.source_items%rowtype;
    source_feed public.source_feeds%rowtype;
    source_state private.official_x_source_state%rowtype;
    daily_slot private.official_x_daily_slots%rowtype;
    style_pack private.official_x_style_reference_packs%rowtype;
    resolved_request_id uuid;
    resolved_source_item_id uuid;
    resolved_job_kst_date date;
    input_sha text;
    source_sha text;
    style_sha text;
    output_sha text;
    safe_failure jsonb;
    decision_now timestamptz;
begin
    if target_workspace_id is null
       or target_job_id is null
       or target_recovery_id is null
       or target_approval_id is null
       or target_approved_by is null
       or target_approved_by !~ '^[A-Za-z0-9@._:-]{3,120}$'
       or target_approved_at is null
       or target_approved_at < clock_timestamp() - interval '2 hours'
       or target_approved_at > clock_timestamp() + interval '5 minutes'
       or target_expires_at is null
       or target_expires_at <= clock_timestamp()
       or target_expires_at > target_approved_at + interval '2 hours'
       or target_release_sha is null
       or target_release_sha !~ '^[a-f0-9]{40}$' then
        raise exception 'Squid failed draft recovery approval is invalid'
            using errcode = '22023';
    end if;

    select candidate.* into failed_job
    from public.jobs as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.id = target_job_id;
    if not found
       or failed_job.client_id <> 'squid'
       or failed_job.job_kind <> 'generate'
       or failed_job.content_item_id is not null
       or failed_job.status <> 'failed'
       or failed_job.attempts <> 3
       or failed_job.max_attempts <> 3
       or failed_job.attempts <> failed_job.max_attempts
       or failed_job.locked_by is not null
       or failed_job.locked_at is not null
       or failed_job.lease_expires_at is not null
       or failed_job.finished_at is null
       or failed_job.input ->> 'workflow'
            is distinct from 'official_x_review_draft_v1'
       or failed_job.input ->> 'content_kind' is distinct from 'daily_news'
       or failed_job.input -> 'manual_only' is distinct from 'false'::jsonb
       or failed_job.output ->> 'execution_plane' is distinct from 'studio_sync'
       or failed_job.last_error_code is null
       or failed_job.last_error_code not in (
            'squid_visual_localization_incomplete',
            'squid_copy_discovery_unavailable'
       )
       or jsonb_typeof(failed_job.output -> 'last_failure') <> 'object'
       or failed_job.output -> 'last_failure' ->> 'error_code'
            is distinct from failed_job.last_error_code
       or failed_job.output -> 'last_failure' -> 'retryable'
            is distinct from 'false'::jsonb
       or jsonb_typeof(failed_job.input -> 'source_item_ids') <> 'array'
       or jsonb_array_length(failed_job.input -> 'source_item_ids') <> 1
       or coalesce(failed_job.input ->> 'request_id', '')
            !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or coalesce(failed_job.input -> 'source_item_ids' ->> 0, '')
            !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or coalesce(failed_job.input ->> 'kst_date', '')
            !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
        raise exception 'Squid failed draft is not recovery eligible'
            using errcode = '23514';
    end if;

    resolved_request_id := (failed_job.input ->> 'request_id')::uuid;
    resolved_source_item_id :=
        (failed_job.input -> 'source_item_ids' ->> 0)::uuid;
    resolved_job_kst_date := (failed_job.input ->> 'kst_date')::date;
    if resolved_job_kst_date is distinct from
       pg_catalog.timezone('Asia/Seoul', clock_timestamp())::date then
        raise exception 'Squid failed draft recovery cannot backfill a prior KST day'
            using errcode = '23514';
    end if;

    select slot.* into daily_slot
    from private.official_x_daily_slots as slot
    where slot.workspace_id = target_workspace_id
      and slot.kst_date = resolved_job_kst_date
      and slot.client_id = 'squid'
      and slot.job_id = target_job_id;
    if not found then
        raise exception 'Squid recovery daily slot is not bound to the job'
            using errcode = '23514';
    end if;

    select state.* into source_state
    from private.official_x_source_state as state
    where state.workspace_id = target_workspace_id
      and state.client_id = 'squid'
      and state.source_item_id = resolved_source_item_id
      and state.queued_job_id = target_job_id
      and state.queued_at is not null;
    if not found then
        raise exception 'Squid recovery source state is not bound to the job'
            using errcode = '23514';
    end if;

    select current_source.* into source
    from public.source_items as current_source
    where current_source.workspace_id = target_workspace_id
      and current_source.client_id = 'squid'
      and current_source.id = resolved_source_item_id
      and current_source.source_type = 'tweet';
    if not found
       or source.published_at is null
       or source.published_at < clock_timestamp() - interval '24 hours'
       or source.published_at > clock_timestamp() + interval '5 minutes'
       or source.external_id !~ '^[0-9]{1,19}$'
       or source.canonical_url is distinct from
            'https://x.com/SquidRouter/status/' || source.external_id
       or source.canonical_url is distinct from failed_job.input ->> 'source_url'
       or source.body is distinct from failed_job.input ->> 'source_content'
       or coalesce(failed_job.input ->> 'source_image_url', '') = ''
       or not exists (
           select 1
           from jsonb_array_elements(source.media) as media(item)
           where media.item ->> 'url'
                = failed_job.input ->> 'source_image_url'
       ) then
        raise exception 'Squid recovery source snapshot no longer matches'
            using errcode = '23514';
    end if;

    select feed.* into source_feed
    from public.source_feeds as feed
    where feed.workspace_id = target_workspace_id
      and feed.client_id = 'squid'
      and feed.id = source.source_feed_id
      and feed.provider = 'x'
      and feed.handle = '@SquidRouter'
      and feed.active is true
    for share;
    if not found
       or source_feed.poll_interval_minutes is null
       or source_feed.last_polled_at is null
       or source_feed.last_polled_at < clock_timestamp() - make_interval(
            mins => least(
                greatest(source_feed.poll_interval_minutes * 2, 15),
                60
            )
       )
       or source_feed.last_polled_at > clock_timestamp() + interval '5 minutes'
       or source_feed.last_cursor is null
       or source_feed.last_cursor !~ '^[0-9]{1,19}$'
       or (
            case
                when source_feed.last_cursor ~ '^[0-9]{1,19}$'
                    then source_feed.last_cursor::numeric
                        < source.external_id::numeric
                else true
            end
       ) then
        raise exception 'Squid recovery feed is not recently synchronized'
            using errcode = '23514';
    end if;

    if exists (
        select 1
        from public.source_items as newer
        where newer.workspace_id = target_workspace_id
          and newer.client_id = 'squid'
          and newer.source_feed_id = source.source_feed_id
          and newer.source_type = 'tweet'
          and newer.published_at is not null
          and (
              newer.published_at > source.published_at
              or (
                  newer.published_at = source.published_at
                  and newer.external_id ~ '^[0-9]{1,19}$'
                  and newer.external_id::numeric > source.external_id::numeric
              )
          )
    ) then
        raise exception 'A newer official Squid source supersedes this recovery'
            using errcode = '23514';
    end if;

    select pack.* into style_pack
    from private.official_x_style_reference_packs as pack
    where pack.workspace_id = target_workspace_id
      and pack.client_id = 'squid'
      and pack.request_id = resolved_request_id
      and pack.primary_source_item_id = resolved_source_item_id;
    if not found then
        raise exception 'Squid recovery style pack is missing'
            using errcode = '23514';
    end if;

    if exists (
        select 1 from public.content_items as item
        where item.workspace_id = target_workspace_id
          and item.id = resolved_request_id
    ) or exists (
        select 1 from public.content_versions as version
        where version.workspace_id = target_workspace_id
          and version.generation_meta ->> 'request_id'
                = resolved_request_id::text
    ) or exists (
        select 1 from public.content_source_links as link
        where link.workspace_id = target_workspace_id
          and link.source_item_id = resolved_source_item_id
    ) or exists (
        select 1 from private.grok_qa_dispatch_outbox as dispatch
        where dispatch.workspace_id = target_workspace_id
          and (
              dispatch.content_item_id = resolved_request_id
              or dispatch.source_item_id = resolved_source_item_id
          )
    ) or exists (
        select 1 from public.jobs as other_job
        where other_job.workspace_id = target_workspace_id
          and other_job.id <> target_job_id
          and (
              other_job.input ->> 'request_id' = resolved_request_id::text
              or (other_job.input -> 'source_item_ids')
                    @> to_jsonb(array[resolved_source_item_id])
          )
          and other_job.status <> 'cancelled'
    ) then
        raise exception 'Squid recovery already has durable or duplicate output'
            using errcode = '23505';
    end if;

    -- The feed lock can wait behind an in-flight official X poll. Use one
    -- post-lock decision time so a grant cannot cross its approval expiry,
    -- the KST-day boundary, or the source-age boundary while waiting.
    decision_now := clock_timestamp();
    if target_approved_at < decision_now - interval '2 hours'
       or target_approved_at > decision_now + interval '5 minutes'
       or target_expires_at <= decision_now then
        raise exception 'Squid failed draft recovery approval is invalid'
            using errcode = '22023';
    end if;
    if resolved_job_kst_date is distinct from
       pg_catalog.timezone('Asia/Seoul', decision_now)::date then
        raise exception 'Squid failed draft recovery cannot backfill a prior KST day'
            using errcode = '23514';
    end if;
    if source.published_at < decision_now - interval '24 hours'
       or source.published_at > decision_now + interval '5 minutes' then
        raise exception 'Squid recovery source snapshot no longer matches'
            using errcode = '23514';
    end if;
    if source_feed.last_polled_at < decision_now - make_interval(
            mins => least(
                greatest(source_feed.poll_interval_minutes * 2, 15),
                60
            )
       )
       or source_feed.last_polled_at > decision_now + interval '5 minutes' then
        raise exception 'Squid recovery feed is not recently synchronized'
            using errcode = '23514';
    end if;

    input_sha := encode(extensions.digest(
        convert_to(failed_job.input::text, 'UTF8'), 'sha256'
    ), 'hex');
    source_sha := encode(extensions.digest(convert_to(jsonb_build_object(
        'source_item_id', source.id,
        'source_feed_id', source.source_feed_id,
        'external_id', source.external_id,
        'canonical_url', source.canonical_url,
        'body', source.body,
        'media', source.media,
        'published_at', source.published_at
    )::text, 'UTF8'), 'sha256'), 'hex');
    style_sha := encode(extensions.digest(convert_to(jsonb_build_object(
        'workspace_id', style_pack.workspace_id,
        'client_id', style_pack.client_id,
        'request_id', style_pack.request_id,
        'primary_source_item_id', style_pack.primary_source_item_id,
        'style_references', style_pack.style_references,
        'reference_pack_hash', style_pack.reference_pack_hash
    )::text, 'UTF8'), 'sha256'), 'hex');
    output_sha := encode(extensions.digest(
        convert_to(failed_job.output::text, 'UTF8'), 'sha256'
    ), 'hex');
    safe_failure := jsonb_build_object(
        'execution_plane', failed_job.output ->> 'execution_plane',
        'last_error_code', failed_job.last_error_code,
        'last_failure_error_code',
            failed_job.output -> 'last_failure' ->> 'error_code',
        'last_failure_retryable',
            failed_job.output -> 'last_failure' -> 'retryable',
        'finished_at', failed_job.finished_at
    );

    return jsonb_build_object(
        'contract', 'squid-failed-draft-recovery@1',
        'workspace_id', target_workspace_id,
        'job_id', target_job_id,
        'recovery_id', target_recovery_id,
        'request_id', resolved_request_id,
        'source_item_id', resolved_source_item_id,
        'kst_date', resolved_job_kst_date,
        'job_input_sha256', input_sha,
        'source_snapshot_sha256', source_sha,
        'style_pack_sha256', style_sha,
        'failed_output_sha256', output_sha,
        'failure_code', failed_job.last_error_code,
        'failed_attempts', failed_job.attempts,
        'failed_max_attempts', failed_job.max_attempts,
        'approval_id', target_approval_id,
        'approved_by', target_approved_by,
        'approved_at', target_approved_at,
        'expires_at', target_expires_at,
        'release_sha', target_release_sha,
        'claims_allowed', 1,
        'same_job', true,
        'same_request_id', true,
        'automatic_approval', false,
        'automatic_publication', false,
        'human_review_required', true,
        'legacy_failure_requires_explicit_review', true,
        'failed_output_snapshot', safe_failure
    );
end;
$$;

revoke all on function private.squid_failed_draft_recovery_subject(
    uuid, uuid, uuid, uuid, text, timestamptz, timestamptz, text
) from public, anon, authenticated, service_role;

commit;
