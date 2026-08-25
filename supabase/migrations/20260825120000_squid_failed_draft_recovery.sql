-- Exact, approval-bound recovery for one failed Squid official-X draft.
--
-- This lane never creates a sibling job, changes the request UUID, releases a
-- daily slot, or rewrites source ownership.  It can lease the exact terminal
-- job once, with attempts already at max, so every recovery failure remains
-- terminal.  Success still stops at needs_review; no approval or publication
-- row is created here.

begin;

create table private.official_x_failed_draft_recovery_grants (
    workspace_id uuid not null
        references public.workspaces(id) on delete restrict,
    recovery_id uuid not null,
    job_id uuid not null references public.jobs(id) on delete restrict,
    request_id uuid not null,
    source_item_id uuid not null,
    kst_date date not null,
    job_input_sha256 text not null check (
        job_input_sha256 ~ '^[a-f0-9]{64}$'
    ),
    source_snapshot_sha256 text not null check (
        source_snapshot_sha256 ~ '^[a-f0-9]{64}$'
    ),
    style_pack_sha256 text not null check (
        style_pack_sha256 ~ '^[a-f0-9]{64}$'
    ),
    failed_output_snapshot jsonb not null check (
        jsonb_typeof(failed_output_snapshot) = 'object'
    ),
    failed_output_sha256 text not null check (
        failed_output_sha256 ~ '^[a-f0-9]{64}$'
    ),
    failure_code text not null check (
        failure_code = 'squid_visual_localization_incomplete'
    ),
    failed_attempts integer not null check (failed_attempts = 3),
    failed_max_attempts integer not null check (failed_max_attempts = 3),
    approval_id uuid not null,
    approval_subject jsonb not null check (
        jsonb_typeof(approval_subject) = 'object'
        and octet_length(approval_subject::text) <= 8192
    ),
    approval_subject_sha256 text not null check (
        approval_subject_sha256 ~ '^[a-f0-9]{64}$'
    ),
    approved_by text not null check (
        approved_by ~ '^[A-Za-z0-9@._:-]{3,120}$'
    ),
    approved_at timestamptz not null,
    expires_at timestamptz not null,
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    claims_allowed smallint not null check (claims_allowed = 1),
    claims_consumed smallint not null default 0 check (
        claims_consumed between 0 and claims_allowed
    ),
    consumed_at timestamptz,
    consumed_by text check (
        consumed_by is null
        or consumed_by ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
    ),
    authorized_at timestamptz not null default clock_timestamp(),
    primary key (workspace_id, recovery_id),
    unique (workspace_id, job_id),
    unique (workspace_id, approval_id),
    unique (workspace_id, approval_subject_sha256),
    foreign key (workspace_id, source_item_id)
        references public.source_items(workspace_id, id) on delete restrict,
    check (approved_at <= authorized_at + interval '5 minutes'),
    check (expires_at > authorized_at),
    check (expires_at <= approved_at + interval '2 hours'),
    check (
        (
            claims_consumed = 0
            and consumed_at is null
            and consumed_by is null
        )
        or (
            claims_consumed = 1
            and consumed_at is not null
            and consumed_by is not null
        )
    )
);

alter table private.official_x_failed_draft_recovery_grants
    enable row level security;
alter table private.official_x_failed_draft_recovery_grants
    force row level security;

revoke all on table private.official_x_failed_draft_recovery_grants
from public, anon, authenticated, service_role;

create or replace function private.enforce_failed_draft_recovery_grant_immutable()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'DELETE' then
        raise exception 'failed draft recovery grants cannot be deleted'
            using errcode = '55000';
    end if;
    if new.workspace_id is distinct from old.workspace_id
       or new.recovery_id is distinct from old.recovery_id
       or new.job_id is distinct from old.job_id
       or new.request_id is distinct from old.request_id
       or new.source_item_id is distinct from old.source_item_id
       or new.kst_date is distinct from old.kst_date
       or new.job_input_sha256 is distinct from old.job_input_sha256
       or new.source_snapshot_sha256 is distinct from old.source_snapshot_sha256
       or new.style_pack_sha256 is distinct from old.style_pack_sha256
       or new.failed_output_snapshot is distinct from old.failed_output_snapshot
       or new.failed_output_sha256 is distinct from old.failed_output_sha256
       or new.failure_code is distinct from old.failure_code
       or new.failed_attempts is distinct from old.failed_attempts
       or new.failed_max_attempts is distinct from old.failed_max_attempts
       or new.approval_id is distinct from old.approval_id
       or new.approval_subject is distinct from old.approval_subject
       or new.approval_subject_sha256 is distinct from old.approval_subject_sha256
       or new.approved_by is distinct from old.approved_by
       or new.approved_at is distinct from old.approved_at
       or new.expires_at is distinct from old.expires_at
       or new.release_sha is distinct from old.release_sha
       or new.claims_allowed is distinct from old.claims_allowed
       or new.authorized_at is distinct from old.authorized_at then
        raise exception 'failed draft recovery grant binding is immutable'
            using errcode = '23505';
    end if;
    if new.claims_consumed < old.claims_consumed
       or new.claims_consumed > old.claims_consumed + 1
       or (old.consumed_at is not null
           and new.consumed_at is distinct from old.consumed_at)
       or (old.consumed_by is not null
           and new.consumed_by is distinct from old.consumed_by) then
        raise exception 'failed draft recovery consumption is irreversible'
            using errcode = '23505';
    end if;
    return new;
end;
$$;

revoke all on function
    private.enforce_failed_draft_recovery_grant_immutable()
from public, anon, authenticated, service_role;

create trigger enforce_failed_draft_recovery_grant_immutable
before update or delete on private.official_x_failed_draft_recovery_grants
for each row execute function
    private.enforce_failed_draft_recovery_grant_immutable();

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
       or failed_job.last_error_code
            is distinct from 'squid_visual_localization_incomplete'
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

create or replace function public.inspect_squid_failed_draft_recovery(
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
    existing private.official_x_failed_draft_recovery_grants%rowtype;
    subject jsonb;
    subject_sha text;
begin
    select grant_row.* into existing
    from private.official_x_failed_draft_recovery_grants as grant_row
    where grant_row.workspace_id = target_workspace_id
      and grant_row.recovery_id = target_recovery_id;
    if found then
        if existing.job_id is distinct from target_job_id
           or existing.approval_id is distinct from target_approval_id
           or existing.approved_by is distinct from target_approved_by
           or existing.approved_at is distinct from target_approved_at
           or existing.expires_at is distinct from target_expires_at
           or existing.release_sha is distinct from target_release_sha then
            raise exception 'Squid recovery inspection conflicts with its grant'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'eligible', true,
            'authorized', true,
            'recovery_id', existing.recovery_id,
            'job_id', existing.job_id,
            'request_id', existing.request_id,
            'source_item_id', existing.source_item_id,
            'approval_subject', existing.approval_subject,
            'approval_subject_sha256', existing.approval_subject_sha256,
            'claims_allowed', existing.claims_allowed,
            'claims_consumed', existing.claims_consumed,
            'expires_at', existing.expires_at,
            'release_sha', existing.release_sha
        );
    end if;

    subject := private.squid_failed_draft_recovery_subject(
        target_workspace_id,
        target_job_id,
        target_recovery_id,
        target_approval_id,
        target_approved_by,
        target_approved_at,
        target_expires_at,
        target_release_sha
    );
    subject_sha := encode(extensions.digest(
        convert_to(subject::text, 'UTF8'), 'sha256'
    ), 'hex');
    return jsonb_build_object(
        'eligible', true,
        'authorized', false,
        'recovery_id', target_recovery_id,
        'job_id', target_job_id,
        'request_id', subject ->> 'request_id',
        'source_item_id', subject ->> 'source_item_id',
        'approval_subject', subject,
        'approval_subject_sha256', subject_sha,
        'claims_allowed', 1,
        'claims_consumed', 0,
        'expires_at', target_expires_at,
        'release_sha', target_release_sha
    );
end;
$$;

create or replace function public.authorize_squid_failed_draft_recovery(
    target_workspace_id uuid,
    target_job_id uuid,
    target_recovery_id uuid,
    target_approval_id uuid,
    target_approved_by text,
    target_approved_at timestamptz,
    target_expires_at timestamptz,
    target_release_sha text,
    target_approval_subject_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    ignored_job public.jobs%rowtype;
    existing private.official_x_failed_draft_recovery_grants%rowtype;
    committed private.official_x_failed_draft_recovery_grants%rowtype;
    subject jsonb;
    subject_sha text;
begin
    if target_approval_subject_sha256 is null
       or target_approval_subject_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'Squid recovery approval subject hash is invalid'
            using errcode = '22023';
    end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'squid-failed-draft-recovery:' || target_job_id::text, 0
    ));
    select job.* into ignored_job
    from public.jobs as job
    where job.workspace_id = target_workspace_id
      and job.id = target_job_id
    for update;
    if not found then
        raise exception 'Squid failed draft recovery job does not exist'
            using errcode = '23514';
    end if;

    select grant_row.* into existing
    from private.official_x_failed_draft_recovery_grants as grant_row
    where grant_row.workspace_id = target_workspace_id
      and grant_row.job_id = target_job_id
    for update;
    if found then
        if existing.recovery_id is distinct from target_recovery_id
           or existing.approval_id is distinct from target_approval_id
           or existing.approved_by is distinct from target_approved_by
           or existing.approved_at is distinct from target_approved_at
           or existing.expires_at is distinct from target_expires_at
           or existing.release_sha is distinct from target_release_sha
           or existing.approval_subject_sha256
                is distinct from target_approval_subject_sha256 then
            raise exception 'Squid recovery grant binding is immutable'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'authorized', true,
            'reused', true,
            'recovery_id', existing.recovery_id,
            'job_id', existing.job_id,
            'request_id', existing.request_id,
            'approval_subject_sha256', existing.approval_subject_sha256,
            'claims_allowed', existing.claims_allowed,
            'claims_consumed', existing.claims_consumed,
            'expires_at', existing.expires_at,
            'release_sha', existing.release_sha
        );
    end if;

    subject := private.squid_failed_draft_recovery_subject(
        target_workspace_id,
        target_job_id,
        target_recovery_id,
        target_approval_id,
        target_approved_by,
        target_approved_at,
        target_expires_at,
        target_release_sha
    );
    subject_sha := encode(extensions.digest(
        convert_to(subject::text, 'UTF8'), 'sha256'
    ), 'hex');
    if subject_sha is distinct from target_approval_subject_sha256 then
        raise exception 'Squid recovery approval subject changed'
            using errcode = '23514';
    end if;

    insert into private.official_x_failed_draft_recovery_grants (
        workspace_id,
        recovery_id,
        job_id,
        request_id,
        source_item_id,
        kst_date,
        job_input_sha256,
        source_snapshot_sha256,
        style_pack_sha256,
        failed_output_snapshot,
        failed_output_sha256,
        failure_code,
        failed_attempts,
        failed_max_attempts,
        approval_id,
        approval_subject,
        approval_subject_sha256,
        approved_by,
        approved_at,
        expires_at,
        release_sha,
        claims_allowed
    ) values (
        target_workspace_id,
        target_recovery_id,
        target_job_id,
        (subject ->> 'request_id')::uuid,
        (subject ->> 'source_item_id')::uuid,
        (subject ->> 'kst_date')::date,
        subject ->> 'job_input_sha256',
        subject ->> 'source_snapshot_sha256',
        subject ->> 'style_pack_sha256',
        subject -> 'failed_output_snapshot',
        subject ->> 'failed_output_sha256',
        subject ->> 'failure_code',
        (subject ->> 'failed_attempts')::integer,
        (subject ->> 'failed_max_attempts')::integer,
        target_approval_id,
        subject,
        subject_sha,
        target_approved_by,
        target_approved_at,
        target_expires_at,
        target_release_sha,
        1
    ) returning * into committed;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        target_workspace_id,
        'job',
        target_job_id,
        'squid_failed_draft_recovery_authorized',
        jsonb_build_object(
            'recovery_id', committed.recovery_id,
            'approval_id', committed.approval_id,
            'approval_subject_sha256', committed.approval_subject_sha256,
            'release_sha', committed.release_sha,
            'expires_at', committed.expires_at,
            'claims_allowed', committed.claims_allowed,
            'automatic_approval', false,
            'automatic_publication', false
        )
    );

    return jsonb_build_object(
        'authorized', true,
        'reused', false,
        'recovery_id', committed.recovery_id,
        'job_id', committed.job_id,
        'request_id', committed.request_id,
        'approval_subject_sha256', committed.approval_subject_sha256,
        'claims_allowed', committed.claims_allowed,
        'claims_consumed', committed.claims_consumed,
        'expires_at', committed.expires_at,
        'release_sha', committed.release_sha
    );
end;
$$;

create or replace function public.claim_squid_failed_draft_recovery(
    target_workspace_id uuid,
    target_job_id uuid,
    target_recovery_id uuid,
    target_approval_subject_sha256 text,
    target_release_sha text,
    target_worker_id text,
    target_lease_seconds integer default 900
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    exact_grant private.official_x_failed_draft_recovery_grants%rowtype;
    failed_job public.jobs%rowtype;
    current_subject jsonb;
    current_subject_sha text;
begin
    if target_workspace_id is null
       or target_job_id is null
       or target_recovery_id is null
       or target_approval_subject_sha256 is null
       or target_approval_subject_sha256 !~ '^[a-f0-9]{64}$'
       or target_release_sha is null
       or target_release_sha !~ '^[a-f0-9]{40}$'
       or target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_lease_seconds not between 60 and 1800 then
        raise exception 'Squid failed draft recovery claim is invalid'
            using errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'squid-failed-draft-recovery:' || target_job_id::text, 0
    ));

    select grant_row.* into exact_grant
    from private.official_x_failed_draft_recovery_grants as grant_row
    where grant_row.workspace_id = target_workspace_id
      and grant_row.recovery_id = target_recovery_id
    for update;
    if not found
       or exact_grant.job_id is distinct from target_job_id
       or exact_grant.approval_subject_sha256
            is distinct from target_approval_subject_sha256
       or exact_grant.release_sha is distinct from target_release_sha then
        raise exception 'Squid failed draft recovery grant does not match'
            using errcode = '23514';
    end if;

    if exact_grant.claims_consumed = 1 then
        return jsonb_build_object(
            'claim_granted', false,
            'generation_allowed', false,
            'failed_draft_recovery_only', true,
            'recovery_id', exact_grant.recovery_id,
            'job_id', exact_grant.job_id,
            'request_id', exact_grant.request_id,
            'approval_subject_sha256',
                exact_grant.approval_subject_sha256,
            'claims_allowed', exact_grant.claims_allowed,
            'claims_consumed', exact_grant.claims_consumed,
            'release_sha', exact_grant.release_sha
        );
    end if;
    if exact_grant.expires_at <= clock_timestamp() then
        return jsonb_build_object(
            'claim_granted', false,
            'generation_allowed', false,
            'failed_draft_recovery_only', true,
            'recovery_id', exact_grant.recovery_id,
            'job_id', exact_grant.job_id,
            'request_id', exact_grant.request_id,
            'approval_subject_sha256',
                exact_grant.approval_subject_sha256,
            'claims_allowed', exact_grant.claims_allowed,
            'claims_consumed', exact_grant.claims_consumed,
            'release_sha', exact_grant.release_sha
        );
    end if;

    select job.* into failed_job
    from public.jobs as job
    where job.workspace_id = target_workspace_id
      and job.id = target_job_id
    for update;
    if not found then
        raise exception 'Squid failed draft recovery job does not exist'
            using errcode = '23514';
    end if;

    current_subject := private.squid_failed_draft_recovery_subject(
        exact_grant.workspace_id,
        exact_grant.job_id,
        exact_grant.recovery_id,
        exact_grant.approval_id,
        exact_grant.approved_by,
        exact_grant.approved_at,
        exact_grant.expires_at,
        exact_grant.release_sha
    );
    current_subject_sha := encode(extensions.digest(
        convert_to(current_subject::text, 'UTF8'), 'sha256'
    ), 'hex');
    if current_subject is distinct from exact_grant.approval_subject
       or current_subject_sha is distinct from exact_grant.approval_subject_sha256
       or current_subject ->> 'job_input_sha256'
            is distinct from exact_grant.job_input_sha256
       or current_subject ->> 'source_snapshot_sha256'
            is distinct from exact_grant.source_snapshot_sha256
       or current_subject ->> 'style_pack_sha256'
            is distinct from exact_grant.style_pack_sha256
       or current_subject ->> 'failed_output_sha256'
            is distinct from exact_grant.failed_output_sha256 then
        raise exception 'Squid failed draft recovery evidence changed'
            using errcode = '23514';
    end if;

    update private.official_x_failed_draft_recovery_grants
    set claims_consumed = 1,
        consumed_at = clock_timestamp(),
        consumed_by = target_worker_id
    where workspace_id = target_workspace_id
      and recovery_id = target_recovery_id
    returning * into exact_grant;

    -- Deliberately preserve attempts, max_attempts, input, output, failure
    -- evidence, finished_at, slot ownership, source ownership, and style pack.
    update public.jobs
    set status = 'running',
        locked_by = target_worker_id,
        locked_at = clock_timestamp(),
        lease_expires_at = clock_timestamp()
            + make_interval(secs => target_lease_seconds)
    where workspace_id = target_workspace_id
      and id = target_job_id
      and status = 'failed'
      and attempts = max_attempts
    returning * into failed_job;
    if not found then
        raise exception 'Squid failed draft recovery claim lost its job fence'
            using errcode = '40001';
    end if;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        target_workspace_id,
        'job',
        target_job_id,
        'squid_failed_draft_recovery_claimed',
        jsonb_build_object(
            'recovery_id', exact_grant.recovery_id,
            'approval_id', exact_grant.approval_id,
            'approval_subject_sha256', exact_grant.approval_subject_sha256,
            'release_sha', exact_grant.release_sha,
            'claims_consumed', exact_grant.claims_consumed,
            'automatic_approval', false,
            'automatic_publication', false
        )
    );

    return jsonb_build_object(
        'claim_granted', true,
        'generation_allowed', true,
        'failed_draft_recovery_only', true,
        'recovery_id', exact_grant.recovery_id,
        'approval_subject_sha256', exact_grant.approval_subject_sha256,
        'release_sha', exact_grant.release_sha,
        'claims_allowed', exact_grant.claims_allowed,
        'claims_consumed', exact_grant.claims_consumed,
        'job_id', failed_job.id,
        'request_id', exact_grant.request_id,
        'workspace_id', failed_job.workspace_id,
        'client_id', failed_job.client_id,
        'status', failed_job.status,
        'attempts', failed_job.attempts,
        'max_attempts', failed_job.max_attempts,
        'origintrail_batch_eligible', false,
        'batch_handoff_recovery_only', false,
        'locked_by', failed_job.locked_by,
        'lease_expires_at', failed_job.lease_expires_at,
        'input', failed_job.input
    );
end;
$$;

-- Preserve the first terminal failure inside the job ledger as well as in the
-- immutable grant.  A successful recovery merges the normal completion receipt;
-- a failed recovery keeps the original `last_failure` and records the recovery
-- outcome separately.  This trigger grants no retry and creates no publication.
create or replace function private.preserve_failed_draft_recovery_evidence()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    recovery_consumed boolean;
    recovery_failure jsonb;
begin
    if old.status <> 'running'
       or new.status not in ('succeeded', 'failed') then
        return new;
    end if;
    select exists (
        select 1
        from private.official_x_failed_draft_recovery_grants as grant_row
        where grant_row.workspace_id = old.workspace_id
          and grant_row.job_id = old.id
          and grant_row.claims_consumed = 1
    ) into recovery_consumed;
    if not recovery_consumed then
        return new;
    end if;

    if new.status = 'succeeded' then
        new.output := old.output || new.output;
    else
        recovery_failure := case
            when new.output -> 'last_failure'
                    is distinct from old.output -> 'last_failure'
                then new.output -> 'last_failure'
            else jsonb_build_object(
                'error_code', coalesce(
                    new.last_error_code,
                    'recovery_completion_uncertain'
                ),
                'error_message', coalesce(
                    new.last_error_message,
                    new.last_error_code,
                    'recovery_completion_uncertain'
                ),
                'retryable', false,
                'failed_at', clock_timestamp(),
                'retry_at', 'null'::jsonb
            )
        end;
        new.output := (new.output - 'last_failure') || jsonb_build_object(
            'last_failure', old.output -> 'last_failure',
            'recovery_failure', recovery_failure
        );
    end if;
    return new;
end;
$$;

revoke all on function private.preserve_failed_draft_recovery_evidence()
from public, anon, authenticated, service_role;

create trigger preserve_failed_draft_recovery_evidence
before update on public.jobs
for each row execute function
    private.preserve_failed_draft_recovery_evidence();

revoke all on function public.inspect_squid_failed_draft_recovery(
    uuid, uuid, uuid, uuid, text, timestamptz, timestamptz, text
) from public, anon, authenticated, service_role;
revoke all on function public.authorize_squid_failed_draft_recovery(
    uuid, uuid, uuid, uuid, text, timestamptz, timestamptz, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.claim_squid_failed_draft_recovery(
    uuid, uuid, uuid, text, text, text, integer
) from public, anon, authenticated, service_role;

grant execute on function public.inspect_squid_failed_draft_recovery(
    uuid, uuid, uuid, uuid, text, timestamptz, timestamptz, text
) to service_role;
grant execute on function public.authorize_squid_failed_draft_recovery(
    uuid, uuid, uuid, uuid, text, timestamptz, timestamptz, text, text
) to service_role;
grant execute on function public.claim_squid_failed_draft_recovery(
    uuid, uuid, uuid, text, text, text, integer
) to service_role;

commit;
