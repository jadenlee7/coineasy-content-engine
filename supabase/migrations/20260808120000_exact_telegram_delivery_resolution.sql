-- Audited, non-sending resolution for a fenced Telegram delivery whose
-- provider outcome stayed unknown. This function can only close the exact
-- attempt after a Studio operator confirms that the canonical public channel,
-- exact caption, and exact PNG were checked. It never creates or requeues work.

begin;

create or replace function public.cancel_unobserved_exact_telegram_publication(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_publication_id uuid,
    target_delivery_started_at timestamptz,
    target_public_channel text,
    target_channel_checked boolean,
    target_caption_checked boolean,
    target_png_checked boolean,
    request_idempotency_key text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    job public.jobs%rowtype;
    publication public.publications%rowtype;
    resolution jsonb;
    tuple_lock_key bigint;
begin
    if target_workspace_id is null
       or target_content_item_id is null
       or target_content_version_id is null
       or target_publication_id is null
       or target_delivery_started_at is null
       or target_public_channel is null
       or lower(target_public_channel) <> 'squid_kor_update'
       or target_channel_checked is distinct from true
       or target_caption_checked is distinct from true
       or target_png_checked is distinct from true
       or request_idempotency_key is null
       or request_idempotency_key
            !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
        raise exception 'exact Telegram delivery resolution is invalid'
            using errcode = '22023';
    end if;

    -- Preserve the established item -> job -> publication lock order.
    select content.* into item
    from public.content_items as content
    where content.workspace_id = target_workspace_id
      and content.id = target_content_item_id
    for update;
    if not found then
        raise exception 'exact Telegram delivery resolution item does not exist'
            using errcode = 'P0002';
    end if;

    select queued_job.* into job
    from public.jobs as queued_job
    where queued_job.workspace_id = item.workspace_id
      and queued_job.content_item_id = item.id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow' = 'exact_telegram_publication_v1'
      and queued_job.input ->> 'publication_id' = target_publication_id::text
      and queued_job.input ->> 'content_version_id'
            = target_content_version_id::text
      and queued_job.input ->> 'channel' = 'telegram'
    for update;
    if not found then
        raise exception 'exact Telegram delivery resolution job does not exist'
            using errcode = 'P0002';
    end if;

    select delivery.* into publication
    from public.publications as delivery
    where delivery.id = target_publication_id
      and delivery.workspace_id = item.workspace_id
      and delivery.content_item_id = item.id
      and delivery.content_version_id = target_content_version_id
      and delivery.client_id = 'squid'
      and delivery.channel = 'telegram'
      and delivery.request_payload ->> 'workflow'
            = 'exact_telegram_publication_v1'
      and delivery.request_payload ->> 'approval_id'
            is not distinct from job.input ->> 'approval_id'
      and delivery.request_payload ->> 'asset_id'
            is not distinct from job.input ->> 'asset_id'
      and delivery.request_payload -> 'asset_snapshot'
            is not distinct from job.input -> 'asset_snapshot'
    for update;
    if not found then
        raise exception 'exact Telegram delivery resolution publication does not exist'
            using errcode = 'P0002';
    end if;

    resolution := publication.response_payload -> 'resolution';
    if publication.status = 'cancelled'
       and job.status = 'failed'
       and resolution = jsonb_build_object(
            'workflow', 'exact_telegram_delivery_resolution_v1',
            'outcome', 'confirmed_not_observed_cancelled',
            'public_channel', 'squid_kor_update',
            'channel_checked', true,
            'caption_checked', true,
            'png_checked', true,
            'idempotency_key', lower(request_idempotency_key),
            'resolved_at', resolution -> 'resolved_at'
       )
       and job.output -> 'delivery_resolution' = resolution
       and pg_catalog.date_trunc('milliseconds', publication.delivery_started_at)
            is not distinct from target_delivery_started_at then
        return jsonb_build_object(
            'publication_id', publication.id,
            'content_item_id', publication.content_item_id,
            'content_version_id', publication.content_version_id,
            'channel', publication.channel,
            'status', publication.status,
            'delivery_started_at', publication.delivery_started_at,
            'external_url', publication.external_url,
            'error_code', publication.response_payload ->> 'error_code',
            'reused', true
        );
    end if;

    tuple_lock_key := pg_catalog.hashtextextended(
        publication.workspace_id::text || ':'
            || publication.content_item_id::text || ':'
            || publication.content_version_id::text || ':telegram',
        0
    );
    if not pg_catalog.pg_try_advisory_xact_lock(tuple_lock_key) then
        raise exception 'exact Telegram delivery resolution is competing with another mutation'
            using errcode = '55P03';
    end if;

    if publication.status <> 'delivery_unknown'
       or pg_catalog.date_trunc('milliseconds', publication.delivery_started_at)
            is distinct from target_delivery_started_at
       or publication.delivery_started_at > statement_timestamp() - interval '10 minutes'
       or publication.delivery_attempt_id is null
       or publication.delivery_request_sha256 is null
       or publication.external_id is not null
       or publication.external_url is not null
       or publication.published_at is not null
       or publication.response_payload ->> 'error_code'
            is distinct from 'telegram_delivery_unknown'
       or job.status <> 'failed'
       or job.locked_by is not null
       or job.lease_expires_at is not null then
        raise exception 'exact Telegram delivery resolution state changed'
            using errcode = '55000';
    end if;

    -- A separately observed canonical message proves that the post exists and
    -- blocks the "not observed" resolution.
    if exists (
        select 1
        from public.publications as observed
        where observed.workspace_id = publication.workspace_id
          and observed.id <> publication.id
          and observed.content_item_id = publication.content_item_id
          and observed.content_version_id = publication.content_version_id
          and observed.channel = 'telegram'
          and observed.status = 'published'
          and observed.external_url ~
                '^https://t\.me/squid_kor_update/[1-9][0-9]{0,18}$'
    ) then
        raise exception 'exact Telegram delivery was already observed publicly'
            using errcode = '23505';
    end if;

    resolution := jsonb_build_object(
        'workflow', 'exact_telegram_delivery_resolution_v1',
        'outcome', 'confirmed_not_observed_cancelled',
        'public_channel', 'squid_kor_update',
        'channel_checked', true,
        'caption_checked', true,
        'png_checked', true,
        'idempotency_key', lower(request_idempotency_key),
        'resolved_at', statement_timestamp()
    );

    update public.jobs
    set output = job.output || jsonb_build_object(
            'delivery_resolution', resolution
        ),
        last_error_code = 'delivery_not_observed_cancelled',
        last_error_message =
            'A Studio operator attested that the exact Telegram delivery was not publicly observed.',
        finished_at = coalesce(job.finished_at, statement_timestamp()),
        updated_at = statement_timestamp()
    where id = job.id;

    update public.publications
    set status = 'cancelled',
        response_payload = publication.response_payload || jsonb_build_object(
            'resolution', resolution
        ),
        last_error = 'delivery_not_observed_cancelled',
        updated_at = statement_timestamp()
    where id = publication.id
    returning * into publication;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        publication.workspace_id,
        'publication',
        publication.id,
        'exact_telegram_delivery_not_observed_cancelled',
        jsonb_build_object(
            'job_id', job.id,
            'content_item_id', publication.content_item_id,
            'content_version_id', publication.content_version_id,
            'delivery_attempt_id', publication.delivery_attempt_id,
            'delivery_started_at', publication.delivery_started_at,
            'public_channel', 'squid_kor_update',
            'reviewer_source', 'studio_session',
            'resolution_workflow', 'exact_telegram_delivery_resolution_v1'
        )
    );

    return jsonb_build_object(
        'publication_id', publication.id,
        'content_item_id', publication.content_item_id,
        'content_version_id', publication.content_version_id,
        'channel', publication.channel,
        'status', publication.status,
        'delivery_started_at', publication.delivery_started_at,
        'external_url', publication.external_url,
        'error_code', publication.response_payload ->> 'error_code',
        'reused', false
    );
end;
$$;

revoke all on function public.cancel_unobserved_exact_telegram_publication(
    uuid, uuid, uuid, uuid, timestamptz, text, boolean, boolean, boolean, text
) from public, anon, authenticated, service_role;

grant execute on function public.cancel_unobserved_exact_telegram_publication(
    uuid, uuid, uuid, uuid, timestamptz, text, boolean, boolean, boolean, text
) to service_role;

commit;
