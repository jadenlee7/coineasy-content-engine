-- Audited operational closure for one exact Telegram delivery whose provider
-- outcome remains unknown.
--
-- This migration never changes the publication, job, content item, provider
-- receipt, or delivery-attempt evidence. It records one immutable operator
-- resolution that means only "closed without resend". The transport fact stays
-- delivery_unknown. The existing canonical manual-observation RPC can still
-- record a discovered public message while this exact version remains current;
-- an older version requires a separate future audited observation path.

begin;

do $role$
begin
    if not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_telegram_resolution'
    ) then
        create role coineasy_telegram_resolution
            nologin noinherit nosuperuser nocreaterole nocreatedb
            noreplication nobypassrls;
    end if;
    -- Supabase's migration owner can create unprivileged roles but is not a
    -- true superuser. Avoid ALTER ROLE clauses that require superuser even
    -- when they only restate an already-false privileged attribute.
    alter role coineasy_telegram_resolution
        nologin noinherit nobypassrls;
    if exists (
        select 1
        from pg_catalog.pg_roles
        where rolname = 'coineasy_telegram_resolution'
          and (
              rolsuper
              or rolcreaterole
              or rolcreatedb
              or rolcanlogin
              or rolreplication
              or rolbypassrls
              or rolinherit
          )
    ) then
        raise exception 'Telegram resolution role is privileged';
    end if;
    grant usage on schema public to coineasy_telegram_resolution;
    grant coineasy_telegram_resolution to authenticator;
end
$role$;

create table private.exact_telegram_delivery_unknown_approvals (
    workspace_id uuid not null
        references public.workspaces(id) on delete restrict,
    operator_approval_id uuid not null,
    resolution_id uuid not null,
    publication_id uuid not null
        references public.publications(id) on delete restrict,
    job_id uuid not null references public.jobs(id) on delete restrict,
    content_item_id uuid not null,
    content_version_id uuid not null,
    approval_subject jsonb not null check (
        pg_catalog.jsonb_typeof(approval_subject) = 'object'
        and pg_catalog.octet_length(approval_subject::text) <= 16384
    ),
    approval_subject_sha256 text not null check (
        approval_subject_sha256 ~ '^[a-f0-9]{64}$'
    ),
    approved_by text not null check (
        approved_by ~ '^[A-Za-z0-9@._:-]{3,120}$'
    ),
    approved_at timestamptz not null default pg_catalog.clock_timestamp(),
    expires_at timestamptz not null,
    approved_release_sha text not null check (
        approved_release_sha ~ '^[a-f0-9]{40}$'
    ),
    primary key (workspace_id, operator_approval_id),
    unique (workspace_id, approval_subject_sha256),
    foreign key (workspace_id, content_item_id)
        references public.content_items(workspace_id, id) on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete restrict,
    check (expires_at > approved_at),
    check (expires_at <= approved_at + interval '2 hours')
);

alter table private.exact_telegram_delivery_unknown_approvals
    enable row level security;
alter table private.exact_telegram_delivery_unknown_approvals
    force row level security;

revoke all on table private.exact_telegram_delivery_unknown_approvals
from public, anon, authenticated, service_role,
     coineasy_telegram_resolution;

create table private.exact_telegram_delivery_unknown_resolutions (
    workspace_id uuid not null
        references public.workspaces(id) on delete restrict,
    resolution_id uuid not null,
    publication_id uuid not null
        references public.publications(id) on delete restrict,
    job_id uuid not null references public.jobs(id) on delete restrict,
    content_item_id uuid not null,
    content_version_id uuid not null,
    publication_approval_id uuid not null
        references public.approvals(id) on delete restrict,
    asset_id uuid not null references public.assets(id) on delete restrict,
    delivery_attempt_id uuid not null,
    delivery_started_at timestamptz not null,
    delivery_request_sha256 text not null check (
        delivery_request_sha256 ~ '^[a-f0-9]{64}$'
    ),
    publication_request_sha256 text not null check (
        publication_request_sha256 ~ '^[a-f0-9]{64}$'
    ),
    publication_response_sha256 text not null check (
        publication_response_sha256 ~ '^[a-f0-9]{64}$'
    ),
    job_input_sha256 text not null check (
        job_input_sha256 ~ '^[a-f0-9]{64}$'
    ),
    job_output_sha256 text not null check (
        job_output_sha256 ~ '^[a-f0-9]{64}$'
    ),
    content_item_row_sha256 text not null check (
        content_item_row_sha256 ~ '^[a-f0-9]{64}$'
    ),
    content_version_row_sha256 text not null check (
        content_version_row_sha256 ~ '^[a-f0-9]{64}$'
    ),
    publication_row_sha256 text not null check (
        publication_row_sha256 ~ '^[a-f0-9]{64}$'
    ),
    job_row_sha256 text not null check (
        job_row_sha256 ~ '^[a-f0-9]{64}$'
    ),
    publication_approval_row_sha256 text not null check (
        publication_approval_row_sha256 ~ '^[a-f0-9]{64}$'
    ),
    asset_row_sha256 text not null check (
        asset_row_sha256 ~ '^[a-f0-9]{64}$'
    ),
    caption_sha256 text not null check (
        caption_sha256 ~ '^[a-f0-9]{64}$'
    ),
    asset_sha256 text not null check (asset_sha256 ~ '^[a-f0-9]{64}$'),
    public_audit jsonb not null check (
        pg_catalog.jsonb_typeof(public_audit) = 'object'
        and pg_catalog.octet_length(public_audit::text) <= 4096
    ),
    public_audit_sha256 text not null check (
        public_audit_sha256 ~ '^[a-f0-9]{64}$'
    ),
    disposition text not null check (
        disposition = 'operator_closed_without_resend'
    ),
    delivery_outcome text not null check (delivery_outcome = 'unknown'),
    public_observation text not null check (
        public_observation = 'not_observed_at_checked_at'
    ),
    operator_approval_id uuid not null,
    approval_subject jsonb not null check (
        pg_catalog.jsonb_typeof(approval_subject) = 'object'
        and pg_catalog.octet_length(approval_subject::text) <= 16384
    ),
    approval_subject_sha256 text not null check (
        approval_subject_sha256 ~ '^[a-f0-9]{64}$'
    ),
    approved_by text not null check (
        approved_by ~ '^[A-Za-z0-9@._:-]{3,120}$'
    ),
    approved_at timestamptz not null,
    expires_at timestamptz not null,
    approved_release_sha text not null check (
        approved_release_sha ~ '^[a-f0-9]{40}$'
    ),
    resolved_by text not null check (
        resolved_by ~ '^[A-Za-z0-9@._:-]{3,120}$'
    ),
    resolved_at timestamptz not null default pg_catalog.clock_timestamp(),
    primary key (workspace_id, resolution_id),
    unique (workspace_id, publication_id),
    unique (workspace_id, job_id),
    unique (workspace_id, delivery_attempt_id),
    unique (workspace_id, operator_approval_id),
    unique (workspace_id, approval_subject_sha256),
    foreign key (workspace_id, content_item_id)
        references public.content_items(workspace_id, id) on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete restrict,
    foreign key (workspace_id, operator_approval_id)
        references private.exact_telegram_delivery_unknown_approvals(
            workspace_id, operator_approval_id
        ) on delete restrict,
    check (approved_at <= resolved_at),
    check (expires_at > approved_at),
    check (expires_at > resolved_at),
    check (expires_at <= approved_at + interval '2 hours')
);

alter table private.exact_telegram_delivery_unknown_resolutions
    enable row level security;
alter table private.exact_telegram_delivery_unknown_resolutions
    force row level security;

revoke all on table private.exact_telegram_delivery_unknown_resolutions
from public, anon, authenticated, service_role,
     coineasy_telegram_resolution;

-- Both ledgers use FORCE RLS with no caller policy. Their SECURITY DEFINER
-- functions and freeze triggers must therefore be owned by a role that can
-- bypass RLS; otherwise inserts would fail and the forensic freeze lookup
-- could fail open. Abort the migration instead of depending on an unstated
-- deployment-owner property.
do $owner$
begin
    if not exists (
        select 1
        from pg_catalog.pg_roles
        where rolname = current_user
          and (rolsuper or rolbypassrls)
    ) then
        raise exception
            'Telegram resolution function owner must bypass row security';
    end if;
end
$owner$;

create or replace function private.enforce_exact_telegram_resolution_immutable()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'exact Telegram delivery resolutions are immutable'
        using errcode = '55000';
end;
$$;

create trigger exact_telegram_delivery_resolution_immutable
before update or delete
on private.exact_telegram_delivery_unknown_resolutions
for each row execute function
    private.enforce_exact_telegram_resolution_immutable();

create trigger exact_telegram_delivery_resolution_approval_immutable
before update or delete
on private.exact_telegram_delivery_unknown_approvals
for each row execute function
    private.enforce_exact_telegram_resolution_immutable();

-- Once a resolution receipt exists, the original forensic rows are frozen.
-- A later positive manual observation is a separate publication row and does
-- not modify either of these rows.
create or replace function private.enforce_resolved_exact_telegram_row_immutable()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    -- A pre-resolution REPEATABLE READ snapshot cannot see the later receipt.
    -- Resolve deliberately leaves the original row version unchanged, so row
    -- locking alone cannot force a serialization failure for that stale writer.
    -- Restrict only potential exact terminal-unknown originals, not unrelated
    -- publication or job traffic, to fresh READ COMMITTED trigger snapshots.
    if pg_catalog.current_setting('transaction_isolation') <> 'read committed'
       and old.client_id = 'squid' then
        if tg_table_name = 'publications' then
            if old.channel = 'telegram'
               and old.status = 'delivery_unknown'
               and old.request_payload ->> 'workflow'
                    = 'exact_telegram_publication_v1' then
                raise exception
                    'exact Telegram unknown-row mutation requires READ COMMITTED'
                    using errcode = '25001';
            end if;
        elsif tg_table_name = 'jobs' then
            if old.job_kind = 'publish'
               and old.status = 'failed'
               and old.input ->> 'channel' = 'telegram'
               and old.input ->> 'workflow'
                    = 'exact_telegram_publication_v1' then
                raise exception
                    'exact Telegram unknown-row mutation requires READ COMMITTED'
                    using errcode = '25001';
            end if;
        end if;
    end if;
    if tg_table_name = 'publications'
       and exists (
           select 1
           from private.exact_telegram_delivery_unknown_resolutions as receipt
           where receipt.workspace_id = old.workspace_id
             and receipt.publication_id = old.id
       ) then
        raise exception 'resolved exact Telegram publication is immutable'
            using errcode = '55000';
    end if;
    if tg_table_name = 'jobs'
       and exists (
           select 1
           from private.exact_telegram_delivery_unknown_resolutions as receipt
           where receipt.workspace_id = old.workspace_id
             and receipt.job_id = old.id
       ) then
        raise exception 'resolved exact Telegram job is immutable'
            using errcode = '55000';
    end if;
    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end;
$$;

create trigger resolved_exact_telegram_publication_immutable
before update or delete on public.publications
for each row execute function
    private.enforce_resolved_exact_telegram_row_immutable();

create trigger resolved_exact_telegram_job_immutable
before update or delete on public.jobs
for each row execute function
    private.enforce_resolved_exact_telegram_row_immutable();

create or replace function private.require_telegram_resolution_claims(
    target_workspace_id uuid,
    target_principal text,
    target_release_sha text,
    target_capability text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb;
begin
    -- Every phase must see evidence committed before its row locks complete.
    if pg_catalog.current_setting('transaction_isolation') <> 'read committed' then
        raise exception 'Telegram resolution requires READ COMMITTED'
            using errcode = '25001';
    end if;
    begin
        claims := nullif(
            pg_catalog.current_setting('request.jwt.claims', true), ''
        )::jsonb;
    exception when others then
        claims := null;
    end;

    if target_workspace_id is null
       or target_principal is null
       or target_principal !~ '^[A-Za-z0-9@._:-]{3,120}$'
       or target_release_sha is null
       or target_release_sha !~ '^[a-f0-9]{40}$'
       or target_capability is null
       or target_capability not in (
           'telegram_delivery_unknown_inspect',
           'telegram_delivery_unknown_approve',
           'telegram_delivery_unknown_resolve'
       )
       or claims is null
       or claims ->> 'role' is distinct from
            'coineasy_telegram_resolution'
       or claims ->> 'workspace_id' is distinct from target_workspace_id::text
       or claims ->> 'sub' is distinct from target_principal
       or claims ->> 'capability' is distinct from target_capability
       or claims ->> 'environment' is distinct from 'production'
       or claims ->> 'release_sha' is distinct from target_release_sha
       or claims -> 'automatic_publication' is distinct from 'false'::jsonb
       or claims -> 'resend_authorized' is distinct from 'false'::jsonb
       or claims -> 'max_external_actions' is distinct from '0'::jsonb then
        raise exception 'Telegram resolution credential is not authorized'
            using errcode = '42501';
    end if;
    return claims;
end;
$$;

create or replace function private.require_telegram_resolution_approval_claims(
    claims jsonb,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_publication_id uuid,
    target_job_id uuid,
    target_resolution_id uuid,
    target_operator_approval_id uuid,
    target_approval_subject_sha256 text,
    target_expires_at timestamptz
)
returns void
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if claims ->> 'jti' is distinct from target_operator_approval_id::text
       or claims ->> 'content_item_id'
            is distinct from target_content_item_id::text
       or claims ->> 'content_version_id'
            is distinct from target_content_version_id::text
       or claims ->> 'publication_id'
            is distinct from target_publication_id::text
       or claims ->> 'job_id' is distinct from target_job_id::text
       or claims ->> 'resolution_id'
            is distinct from target_resolution_id::text
       or claims ->> 'operator_approval_id'
            is distinct from target_operator_approval_id::text
       or claims ->> 'approval_subject_sha256'
            is distinct from target_approval_subject_sha256
       or claims ->> 'expires_at' is null then
        raise exception 'Telegram resolution approval credential is not exact'
            using errcode = '42501';
    end if;
    begin
        if (claims ->> 'expires_at')::timestamptz
                is distinct from target_expires_at then
            raise exception
                'Telegram resolution approval credential is not exact'
                using errcode = '42501';
        end if;
    exception when invalid_datetime_format or datetime_field_overflow then
        raise exception 'Telegram resolution approval credential is not exact'
            using errcode = '42501';
    end;
end;
$$;

create or replace function private.require_telegram_resolution_inspect_claims(
    claims jsonb,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_publication_id uuid,
    target_job_id uuid,
    target_resolution_id uuid,
    target_operator_approval_id uuid,
    target_approved_by text,
    target_expires_at timestamptz,
    target_public_audit_sha256 text
)
returns void
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if claims ->> 'jti' is distinct from target_resolution_id::text
       or claims ->> 'content_item_id'
            is distinct from target_content_item_id::text
       or claims ->> 'content_version_id'
            is distinct from target_content_version_id::text
       or claims ->> 'publication_id'
            is distinct from target_publication_id::text
       or claims ->> 'job_id' is distinct from target_job_id::text
       or claims ->> 'resolution_id'
            is distinct from target_resolution_id::text
       or claims ->> 'operator_approval_id'
            is distinct from target_operator_approval_id::text
       or claims ->> 'approved_by' is distinct from target_approved_by
       or claims ->> 'public_audit_sha256'
            is distinct from target_public_audit_sha256
       or claims ->> 'expires_at' is null then
        raise exception 'Telegram resolution inspection credential is not exact'
            using errcode = '42501';
    end if;
    begin
        if (claims ->> 'expires_at')::timestamptz
                is distinct from target_expires_at then
            raise exception
                'Telegram resolution inspection credential is not exact'
                using errcode = '42501';
        end if;
    exception when invalid_datetime_format or datetime_field_overflow then
        raise exception 'Telegram resolution inspection credential is not exact'
            using errcode = '42501';
    end;
end;
$$;

create or replace function private.require_telegram_resolution_resolve_claims(
    claims jsonb,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_publication_id uuid,
    target_job_id uuid,
    target_resolution_id uuid,
    target_operator_approval_id uuid,
    target_approval_subject_sha256 text
)
returns void
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if claims ->> 'jti' is distinct from target_resolution_id::text
       or claims ->> 'content_item_id'
            is distinct from target_content_item_id::text
       or claims ->> 'content_version_id'
            is distinct from target_content_version_id::text
       or claims ->> 'publication_id'
            is distinct from target_publication_id::text
       or claims ->> 'job_id' is distinct from target_job_id::text
       or claims ->> 'resolution_id'
            is distinct from target_resolution_id::text
       or claims ->> 'operator_approval_id'
            is distinct from target_operator_approval_id::text
       or claims ->> 'approval_subject_sha256'
            is distinct from target_approval_subject_sha256 then
        raise exception 'Telegram resolution credential is not exact'
            using errcode = '42501';
    end if;
end;
$$;

create or replace function private.exact_telegram_delivery_resolution_subject(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_publication_id uuid,
    target_job_id uuid,
    target_resolution_id uuid,
    target_operator_approval_id uuid,
    target_approved_by text,
    target_expires_at timestamptz,
    target_validation_reference_at timestamptz,
    target_release_sha text,
    target_public_audit jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
set timezone = 'UTC'
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    job public.jobs%rowtype;
    publication public.publications%rowtype;
    publication_approval public.approvals%rowtype;
    asset public.assets%rowtype;
    checked_at timestamptz;
    first_message_id bigint;
    last_message_id bigint;
    message_count integer;
    public_audit_sha text;
    caption_sha text;
    publication_request_sha text;
    publication_response_sha text;
    job_input_sha text;
    job_output_sha text;
    content_item_row_sha text;
    content_version_row_sha text;
    publication_row_sha text;
    job_row_sha text;
    publication_approval_row_sha text;
    asset_row_sha text;
begin
    if target_workspace_id is null
       or target_content_item_id is null
       or target_content_version_id is null
       or target_publication_id is null
       or target_job_id is null
       or target_resolution_id is null
       or target_operator_approval_id is null
       or target_approved_by is null
       or target_approved_by !~ '^[A-Za-z0-9@._:-]{3,120}$'
       or target_expires_at is null
       or target_validation_reference_at is null
       or target_validation_reference_at
            > pg_catalog.clock_timestamp() + interval '1 minute'
       or target_validation_reference_at
            < pg_catalog.clock_timestamp() - interval '2 hours'
       or target_expires_at <= target_validation_reference_at
       or target_expires_at
            > target_validation_reference_at + interval '2 hours'
       or target_release_sha is null
       or target_release_sha !~ '^[a-f0-9]{40}$'
       or pg_catalog.jsonb_typeof(target_public_audit) <> 'object'
       or pg_catalog.octet_length(target_public_audit::text) > 4096
       or (select pg_catalog.array_agg(key order by key)
           from pg_catalog.jsonb_object_keys(target_public_audit) as key)
            is distinct from array[
                'caption_match_count', 'checked_at', 'first_message_id',
                'last_message_id', 'message_count', 'png_match_count',
                'public_channel', 'scan_source', 'schema_version',
                'snapshot_sha256'
            ]::text[]
       or target_public_audit ->> 'schema_version' is distinct from
            'telegram-public-channel-audit@1'
       or target_public_audit ->> 'scan_source' is distinct from
            'public_telegram_web_history'
       or target_public_audit ->> 'public_channel' is distinct from
            'squid_kor_update'
       or target_public_audit -> 'caption_match_count'
            is distinct from '0'::jsonb
       or target_public_audit -> 'png_match_count'
            is distinct from '0'::jsonb
       or coalesce(target_public_audit ->> 'first_message_id', '')
            !~ '^[1-9][0-9]{0,18}$'
       or coalesce(target_public_audit ->> 'last_message_id', '')
            !~ '^[1-9][0-9]{0,18}$'
       or coalesce(target_public_audit ->> 'message_count', '')
            !~ '^[1-9][0-9]{0,3}$'
       or coalesce(target_public_audit ->> 'checked_at', '')
            !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
       or coalesce(target_public_audit ->> 'snapshot_sha256', '')
            !~ '^[a-f0-9]{64}$' then
        raise exception 'exact Telegram resolution subject is invalid'
            using errcode = '22023';
    end if;

    checked_at := (target_public_audit ->> 'checked_at')::timestamptz;
    first_message_id := (target_public_audit ->> 'first_message_id')::bigint;
    last_message_id := (target_public_audit ->> 'last_message_id')::bigint;
    message_count := (target_public_audit ->> 'message_count')::integer;
    if first_message_id > last_message_id
       or message_count not between 1 and 1000
       or checked_at > target_validation_reference_at + interval '1 minute'
       or checked_at
            < target_validation_reference_at - interval '30 minutes' then
        raise exception 'exact Telegram public audit window is invalid'
            using errcode = '22023';
    end if;

    select content.* into item
    from public.content_items as content
    where content.workspace_id = target_workspace_id
      and content.id = target_content_item_id;
    if not found
       or item.client_id <> 'squid'
       or item.content_kind <> 'daily_news' then
        raise exception 'exact Telegram resolution item does not exist'
            using errcode = '23514';
    end if;

    select immutable_version.* into version
    from public.content_versions as immutable_version
    where immutable_version.workspace_id = target_workspace_id
      and immutable_version.content_item_id = item.id
      and immutable_version.id = target_content_version_id;
    if not found
       or pg_catalog.jsonb_typeof(version.channel_copy) <> 'object'
       or coalesce(version.channel_copy ->> 'telegram', '') = '' then
        raise exception 'exact Telegram resolution version is invalid'
            using errcode = '23514';
    end if;

    select queued_job.* into job
    from public.jobs as queued_job
    where queued_job.workspace_id = target_workspace_id
      and queued_job.id = target_job_id
      and queued_job.content_item_id = item.id
      and queued_job.job_kind = 'publish'
      and queued_job.input ->> 'workflow' = 'exact_telegram_publication_v1'
      and queued_job.input ->> 'content_version_id' = version.id::text
      and queued_job.input ->> 'channel' = 'telegram';
    if not found
       or job.status <> 'failed'
       or job.locked_by is not null
       or job.locked_at is not null
       or job.lease_expires_at is not null
       or job.last_error_code is distinct from 'delivery_outcome_unknown'
       or job.output -> 'last_failure' ->> 'error_code'
            is distinct from 'telegram_delivery_unknown'
       or job.output -> 'last_failure' -> 'retryable_before_attempt'
            is distinct from 'false'::jsonb
       or job.output -> 'last_failure' -> 'attempt_started'
            is distinct from 'true'::jsonb then
        raise exception 'exact Telegram resolution job is not terminal unknown'
            using errcode = '23514';
    end if;

    select delivery.* into publication
    from public.publications as delivery
    where delivery.workspace_id = target_workspace_id
      and delivery.id = target_publication_id
      and delivery.content_item_id = item.id
      and delivery.content_version_id = version.id
      and delivery.client_id = 'squid'
      and delivery.channel = 'telegram'
      and delivery.request_payload ->> 'workflow'
            = 'exact_telegram_publication_v1'
      and delivery.id::text = job.input ->> 'publication_id'
      and delivery.request_payload ->> 'approval_id'
            is not distinct from job.input ->> 'approval_id'
      and delivery.request_payload ->> 'asset_id'
            is not distinct from job.input ->> 'asset_id'
      and delivery.request_payload -> 'asset_snapshot'
            is not distinct from job.input -> 'asset_snapshot';
    if not found
       or publication.status <> 'delivery_unknown'
       or publication.delivery_attempt_id is null
       or publication.delivery_started_at is null
       or publication.delivery_request_sha256 !~ '^[a-f0-9]{64}$'
       or publication.delivery_started_at > checked_at - interval '10 minutes'
       or publication.published_at is not null
       or publication.external_id is not null
       or publication.external_url is not null
       or publication.response_payload ->> 'error_code'
            is distinct from 'telegram_delivery_unknown' then
        raise exception 'exact Telegram publication is not terminal unknown'
            using errcode = '23514';
    end if;

    select approval.* into publication_approval
    from public.approvals as approval
    where approval.workspace_id = target_workspace_id
      and approval.id = (publication.request_payload ->> 'approval_id')::uuid
      and approval.content_item_id = item.id
      and approval.content_version_id = version.id;
    if not found
       or publication_approval.client_id <> 'squid'
       or publication_approval.decision <> 'approved'
       or publication_approval.fact_check_policy_version
            is distinct from 'double-fact-check@1'
       or publication_approval.source_facts_verified is not true
       or publication_approval.output_claims_verified is not true then
        raise exception 'exact Telegram publication approval pin is missing'
            using errcode = '23514';
    end if;

    select stored_asset.* into asset
    from public.assets as stored_asset
    where stored_asset.workspace_id = target_workspace_id
      and stored_asset.id = (publication.request_payload ->> 'asset_id')::uuid
      and stored_asset.content_item_id = item.id
      and stored_asset.content_version_id = version.id
      and stored_asset.asset_kind = 'png'
      and stored_asset.mime_type = 'image/png'
      and stored_asset.sha256 ~ '^[a-f0-9]{64}$'
      and stored_asset.sha256 is not distinct from
            publication.request_payload -> 'asset_snapshot' ->> 'sha256';
    if not found then
        raise exception 'exact Telegram publication asset pin is missing'
            using errcode = '23514';
    end if;

    if exists (
        select 1
        from public.publications as observed
        where observed.workspace_id = publication.workspace_id
          and observed.id <> publication.id
          and observed.content_item_id = publication.content_item_id
          and observed.content_version_id = publication.content_version_id
          and observed.channel = 'telegram'
          and observed.status = 'published'
          and observed.request_payload = pg_catalog.jsonb_build_object(
              'observation', 'manual_existing_publication',
              'external_publish_performed', false
          )
          and observed.response_payload = pg_catalog.jsonb_build_object(
              'observed', true,
              'external_publish_performed', false
          )
          and observed.external_url ~
                '^https://t\.me/squid_kor_update/[1-9][0-9]{0,18}$'
    ) then
        raise exception 'exact Telegram delivery is already observed publicly'
            using errcode = '23505';
    end if;

    public_audit_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(target_public_audit::text, 'UTF8'), 'sha256'
    ), 'hex');
    caption_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(version.channel_copy ->> 'telegram', 'UTF8'),
        'sha256'
    ), 'hex');
    publication_request_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(publication.request_payload::text, 'UTF8'),
        'sha256'
    ), 'hex');
    publication_response_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(publication.response_payload::text, 'UTF8'),
        'sha256'
    ), 'hex');
    job_input_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(job.input::text, 'UTF8'), 'sha256'
    ), 'hex');
    job_output_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(job.output::text, 'UTF8'), 'sha256'
    ), 'hex');
    content_item_row_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(pg_catalog.to_jsonb(item)::text, 'UTF8'),
        'sha256'
    ), 'hex');
    content_version_row_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(pg_catalog.to_jsonb(version)::text, 'UTF8'),
        'sha256'
    ), 'hex');
    publication_row_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(pg_catalog.to_jsonb(publication)::text, 'UTF8'),
        'sha256'
    ), 'hex');
    job_row_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(pg_catalog.to_jsonb(job)::text, 'UTF8'),
        'sha256'
    ), 'hex');
    publication_approval_row_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(
            pg_catalog.to_jsonb(publication_approval)::text, 'UTF8'
        ),
        'sha256'
    ), 'hex');
    asset_row_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(pg_catalog.to_jsonb(asset)::text, 'UTF8'),
        'sha256'
    ), 'hex');

    return pg_catalog.jsonb_build_object(
        'schema_version', 'exact-telegram-delivery-resolution@1',
        'action', 'resolve_delivery_unknown_without_resend',
        'workspace_id', target_workspace_id,
        'client_id', 'squid',
        'content_item_id', item.id,
        'content_version_id', version.id,
        'publication_id', publication.id,
        'job_id', job.id,
        'publication_approval_id', publication_approval.id,
        'asset_id', asset.id,
        'delivery_attempt_id', publication.delivery_attempt_id,
        'delivery_started_at', publication.delivery_started_at,
        'delivery_request_sha256', publication.delivery_request_sha256,
        'publication_request_sha256', publication_request_sha,
        'publication_response_sha256', publication_response_sha,
        'job_input_sha256', job_input_sha,
        'job_output_sha256', job_output_sha,
        'content_item_row_sha256', content_item_row_sha,
        'content_version_row_sha256', content_version_row_sha,
        'publication_row_sha256', publication_row_sha,
        'job_row_sha256', job_row_sha,
        'publication_approval_row_sha256', publication_approval_row_sha,
        'asset_row_sha256', asset_row_sha,
        'caption_sha256', caption_sha,
        'asset_sha256', asset.sha256,
        'publication_status', 'delivery_unknown',
        'job_status', 'failed',
        'delivery_outcome', 'unknown',
        'disposition', 'operator_closed_without_resend',
        'public_observation', 'not_observed_at_checked_at',
        'public_audit', target_public_audit,
        'public_audit_sha256', public_audit_sha,
        'resolution_id', target_resolution_id,
        'operator_approval_id', target_operator_approval_id,
        'approved_by', target_approved_by,
        'expires_at', target_expires_at,
        'approved_release_sha', target_release_sha,
        'resend_authorized', false,
        'provider_calls', 0,
        'database_claims', 0,
        'publication_state_changed', false,
        'job_state_changed', false,
        'forbidden_actions', pg_catalog.jsonb_build_array(
            'provider_call', 'claim', 'requeue', 'resend',
            'mark_published', 'create_publication', 'create_job'
        )
    );
end;
$$;

create or replace function public.inspect_exact_telegram_delivery_unknown_resolution(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_publication_id uuid,
    target_job_id uuid,
    target_resolution_id uuid,
    target_operator_approval_id uuid,
    target_inspected_by text,
    target_approved_by text,
    target_expires_at timestamptz,
    target_release_sha text,
    target_public_audit jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
set timezone = 'UTC'
as $$
declare
    existing private.exact_telegram_delivery_unknown_resolutions%rowtype;
    existing_approval
        private.exact_telegram_delivery_unknown_approvals%rowtype;
    subject jsonb;
    subject_sha text;
    public_audit_sha text;
begin
    public_audit_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(target_public_audit::text, 'UTF8'), 'sha256'
    ), 'hex');
    if public_audit_sha is null then
        raise exception 'exact Telegram inspection audit is invalid'
            using errcode = '22023';
    end if;
    perform private.require_telegram_resolution_inspect_claims(
        private.require_telegram_resolution_claims(
            target_workspace_id,
            target_inspected_by,
            target_release_sha,
            'telegram_delivery_unknown_inspect'
        ),
        target_content_item_id,
        target_content_version_id,
        target_publication_id,
        target_job_id,
        target_resolution_id,
        target_operator_approval_id,
        target_approved_by,
        target_expires_at,
        public_audit_sha
    );

    select receipt.* into existing
    from private.exact_telegram_delivery_unknown_resolutions as receipt
    where receipt.workspace_id = target_workspace_id
      and receipt.publication_id = target_publication_id;
    if found then
        if existing.content_item_id is distinct from target_content_item_id
           or existing.content_version_id is distinct from target_content_version_id
           or existing.job_id is distinct from target_job_id
           or existing.resolution_id is distinct from target_resolution_id
           or existing.operator_approval_id
                is distinct from target_operator_approval_id
           or existing.approved_by is distinct from target_approved_by
           or existing.expires_at is distinct from target_expires_at
           or existing.approved_release_sha is distinct from target_release_sha
           or existing.public_audit is distinct from target_public_audit then
            raise exception 'exact Telegram resolution conflicts with receipt'
                using errcode = '23505';
        end if;
        return pg_catalog.jsonb_build_object(
            'eligible', true,
            'resolved', true,
            'reused', true,
            'resolution_id', existing.resolution_id,
            'publication_id', existing.publication_id,
            'job_id', existing.job_id,
            'content_item_id', existing.content_item_id,
            'content_version_id', existing.content_version_id,
            'delivery_outcome', existing.delivery_outcome,
            'disposition', existing.disposition,
            'public_observation', existing.public_observation,
            'approval_subject', existing.approval_subject,
            'approval_subject_sha256', existing.approval_subject_sha256,
            'approved', true,
            'approved_at', existing.approved_at,
            'resolved_at', existing.resolved_at,
            'resend_authorized', false
        );
    end if;

    subject := private.exact_telegram_delivery_resolution_subject(
        target_workspace_id,
        target_content_item_id,
        target_content_version_id,
        target_publication_id,
        target_job_id,
        target_resolution_id,
        target_operator_approval_id,
        target_approved_by,
        target_expires_at,
        pg_catalog.clock_timestamp(),
        target_release_sha,
        target_public_audit
    );
    subject_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(subject::text, 'UTF8'), 'sha256'
    ), 'hex');

    select approval.* into existing_approval
    from private.exact_telegram_delivery_unknown_approvals as approval
    where approval.workspace_id = target_workspace_id
      and approval.operator_approval_id = target_operator_approval_id;
    if found
       and (
           existing_approval.content_item_id
                is distinct from target_content_item_id
           or existing_approval.content_version_id
                is distinct from target_content_version_id
           or existing_approval.publication_id
                is distinct from target_publication_id
           or existing_approval.job_id is distinct from target_job_id
           or existing_approval.resolution_id
                is distinct from target_resolution_id
           or existing_approval.approved_by is distinct from target_approved_by
           or existing_approval.expires_at is distinct from target_expires_at
           or existing_approval.approved_release_sha
                is distinct from target_release_sha
           or existing_approval.approval_subject is distinct from subject
           or existing_approval.approval_subject_sha256
                is distinct from subject_sha
       ) then
        raise exception 'exact Telegram approval conflicts with inspection'
            using errcode = '23505';
    end if;
    return pg_catalog.jsonb_build_object(
        'eligible', true,
        'resolved', false,
        'reused', false,
        'resolution_id', target_resolution_id,
        'publication_id', target_publication_id,
        'job_id', target_job_id,
        'content_item_id', target_content_item_id,
        'content_version_id', target_content_version_id,
        'delivery_outcome', 'unknown',
        'disposition', 'operator_closed_without_resend',
        'public_observation', 'not_observed_at_checked_at',
        'approval_subject', subject,
        'approval_subject_sha256', subject_sha,
        'approved', found,
        'approved_at', case when found
            then pg_catalog.to_jsonb(existing_approval.approved_at)
            else 'null'::jsonb end,
        'resend_authorized', false
    );
end;
$$;

create or replace function public.approve_exact_telegram_delivery_unknown_resolution(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_publication_id uuid,
    target_job_id uuid,
    target_resolution_id uuid,
    target_operator_approval_id uuid,
    target_approved_by text,
    target_expires_at timestamptz,
    target_release_sha text,
    target_public_audit jsonb,
    target_approval_subject_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
set timezone = 'UTC'
as $$
declare
    claims jsonb;
    existing private.exact_telegram_delivery_unknown_approvals%rowtype;
    committed private.exact_telegram_delivery_unknown_approvals%rowtype;
    subject jsonb;
    subject_sha text;
begin
    if target_approval_subject_sha256 is null
       or target_approval_subject_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'exact Telegram approval hash is invalid'
            using errcode = '22023';
    end if;
    claims := private.require_telegram_resolution_claims(
        target_workspace_id,
        target_approved_by,
        target_release_sha,
        'telegram_delivery_unknown_approve'
    );
    perform private.require_telegram_resolution_approval_claims(
        claims,
        target_content_item_id,
        target_content_version_id,
        target_publication_id,
        target_job_id,
        target_resolution_id,
        target_operator_approval_id,
        target_approval_subject_sha256,
        target_expires_at
    );

    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        target_workspace_id::text || ':'
            || target_operator_approval_id::text,
        0
    ));
    select approval.* into existing
    from private.exact_telegram_delivery_unknown_approvals as approval
    where approval.workspace_id = target_workspace_id
      and approval.operator_approval_id = target_operator_approval_id;
    if found then
        if existing.content_item_id is distinct from target_content_item_id
           or existing.content_version_id
                is distinct from target_content_version_id
           or existing.publication_id is distinct from target_publication_id
           or existing.job_id is distinct from target_job_id
           or existing.resolution_id is distinct from target_resolution_id
           or existing.approved_by is distinct from target_approved_by
           or existing.expires_at is distinct from target_expires_at
           or existing.approved_release_sha is distinct from target_release_sha
           or existing.approval_subject -> 'public_audit'
                is distinct from target_public_audit
           or existing.approval_subject_sha256
                is distinct from target_approval_subject_sha256 then
            raise exception 'exact Telegram approval replay conflicts'
                using errcode = '23505';
        end if;
        return pg_catalog.jsonb_build_object(
            'approved', true,
            'reused', true,
            'operator_approval_id', existing.operator_approval_id,
            'resolution_id', existing.resolution_id,
            'publication_id', existing.publication_id,
            'job_id', existing.job_id,
            'approval_subject_sha256', existing.approval_subject_sha256,
            'approved_by', existing.approved_by,
            'approved_at', existing.approved_at,
            'expires_at', existing.expires_at,
            'resend_authorized', false,
            'provider_calls', 0,
            'database_claims', 0
        );
    end if;

    subject := private.exact_telegram_delivery_resolution_subject(
        target_workspace_id,
        target_content_item_id,
        target_content_version_id,
        target_publication_id,
        target_job_id,
        target_resolution_id,
        target_operator_approval_id,
        target_approved_by,
        target_expires_at,
        pg_catalog.clock_timestamp(),
        target_release_sha,
        target_public_audit
    );
    subject_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(subject::text, 'UTF8'), 'sha256'
    ), 'hex');
    if subject_sha is distinct from target_approval_subject_sha256 then
        raise exception 'exact Telegram approval subject changed'
            using errcode = '23514';
    end if;

    insert into private.exact_telegram_delivery_unknown_approvals (
        workspace_id,
        operator_approval_id,
        resolution_id,
        publication_id,
        job_id,
        content_item_id,
        content_version_id,
        approval_subject,
        approval_subject_sha256,
        approved_by,
        expires_at,
        approved_release_sha
    ) values (
        target_workspace_id,
        target_operator_approval_id,
        target_resolution_id,
        target_publication_id,
        target_job_id,
        target_content_item_id,
        target_content_version_id,
        subject,
        subject_sha,
        target_approved_by,
        target_expires_at,
        target_release_sha
    ) returning * into committed;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        target_workspace_id,
        'publication',
        target_publication_id,
        'exact_telegram_delivery_unknown_resolution_approved',
        pg_catalog.jsonb_build_object(
            'operator_approval_id', committed.operator_approval_id,
            'resolution_id', committed.resolution_id,
            'job_id', committed.job_id,
            'content_item_id', committed.content_item_id,
            'content_version_id', committed.content_version_id,
            'approval_subject_sha256', committed.approval_subject_sha256,
            'approved_by', committed.approved_by,
            'approved_at', committed.approved_at,
            'expires_at', committed.expires_at,
            'approved_release_sha', committed.approved_release_sha,
            'resend_authorized', false,
            'automatic_publication', false,
            'provider_calls', 0,
            'database_claims', 0
        )
    );

    return pg_catalog.jsonb_build_object(
        'approved', true,
        'reused', false,
        'operator_approval_id', committed.operator_approval_id,
        'resolution_id', committed.resolution_id,
        'publication_id', committed.publication_id,
        'job_id', committed.job_id,
        'approval_subject_sha256', committed.approval_subject_sha256,
        'approved_by', committed.approved_by,
        'approved_at', committed.approved_at,
        'expires_at', committed.expires_at,
        'resend_authorized', false,
        'provider_calls', 0,
        'database_claims', 0
    );
end;
$$;

create or replace function public.resolve_exact_telegram_delivery_unknown_without_resend(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_publication_id uuid,
    target_job_id uuid,
    target_resolution_id uuid,
    target_operator_approval_id uuid,
    target_resolved_by text,
    target_release_sha text,
    target_public_audit jsonb,
    target_approval_subject_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
set timezone = 'UTC'
as $$
declare
    claims jsonb;
    ignored_item public.content_items%rowtype;
    ignored_job public.jobs%rowtype;
    ignored_publication public.publications%rowtype;
    ignored_version public.content_versions%rowtype;
    ignored_publication_approval public.approvals%rowtype;
    ignored_asset public.assets%rowtype;
    approved private.exact_telegram_delivery_unknown_approvals%rowtype;
    existing private.exact_telegram_delivery_unknown_resolutions%rowtype;
    committed private.exact_telegram_delivery_unknown_resolutions%rowtype;
    subject jsonb;
    subject_sha text;
    resolved_time timestamptz;
begin
    if target_approval_subject_sha256 is null
       or target_approval_subject_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'exact Telegram resolution approval hash is invalid'
            using errcode = '22023';
    end if;
    claims := private.require_telegram_resolution_claims(
        target_workspace_id,
        target_resolved_by,
        target_release_sha,
        'telegram_delivery_unknown_resolve'
    );
    perform private.require_telegram_resolution_resolve_claims(
        claims,
        target_content_item_id,
        target_content_version_id,
        target_publication_id,
        target_job_id,
        target_resolution_id,
        target_operator_approval_id,
        target_approval_subject_sha256
    );

    -- Preserve the established item -> job -> publication lock order before
    -- taking shared locks on the immutable evidence rows referenced by the
    -- publication. This keeps concurrent resolution attempts serialized on
    -- the same forensic publication and avoids reading pins from an
    -- uninitialized publication record.
    select content.* into ignored_item
    from public.content_items as content
    where content.workspace_id = target_workspace_id
      and content.id = target_content_item_id
    for update;
    if not found then
        raise exception 'exact Telegram resolution item does not exist'
            using errcode = '23514';
    end if;

    select queued_job.* into ignored_job
    from public.jobs as queued_job
    where queued_job.workspace_id = target_workspace_id
      and queued_job.id = target_job_id
      and queued_job.content_item_id = target_content_item_id
    for update;
    if not found then
        raise exception 'exact Telegram resolution job does not exist'
            using errcode = '23514';
    end if;

    select delivery.* into ignored_publication
    from public.publications as delivery
    where delivery.workspace_id = target_workspace_id
      and delivery.id = target_publication_id
      and delivery.content_item_id = target_content_item_id
      and delivery.content_version_id = target_content_version_id
    for update;
    if not found then
        raise exception 'exact Telegram resolution publication does not exist'
            using errcode = '23514';
    end if;

    select immutable_version.* into ignored_version
    from public.content_versions as immutable_version
    where immutable_version.workspace_id = target_workspace_id
      and immutable_version.content_item_id = target_content_item_id
      and immutable_version.id = target_content_version_id
    for share;
    if not found then
        raise exception 'exact Telegram resolution version does not exist'
            using errcode = '23514';
    end if;

    select approval.* into ignored_publication_approval
    from public.approvals as approval
    where approval.workspace_id = target_workspace_id
      and approval.id = (
          ignored_publication.request_payload ->> 'approval_id'
      )::uuid
      and approval.content_item_id = target_content_item_id
      and approval.content_version_id = target_content_version_id
    for share;
    if not found then
        raise exception 'exact Telegram resolution approval pin does not exist'
            using errcode = '23514';
    end if;

    select asset.* into ignored_asset
    from public.assets as asset
    where asset.workspace_id = target_workspace_id
      and asset.id = (
          ignored_publication.request_payload ->> 'asset_id'
      )::uuid
      and asset.content_item_id = target_content_item_id
      and asset.content_version_id = target_content_version_id
    for share;
    if not found then
        raise exception 'exact Telegram resolution asset pin does not exist'
            using errcode = '23514';
    end if;

    select receipt.* into existing
    from private.exact_telegram_delivery_unknown_resolutions as receipt
    where receipt.workspace_id = target_workspace_id
      and receipt.publication_id = target_publication_id;
    if found then
        if existing.content_item_id is distinct from target_content_item_id
           or existing.content_version_id is distinct from target_content_version_id
           or existing.job_id is distinct from target_job_id
           or existing.resolution_id is distinct from target_resolution_id
           or existing.operator_approval_id
                is distinct from target_operator_approval_id
           or existing.resolved_by is distinct from target_resolved_by
           or existing.approved_release_sha is distinct from target_release_sha
           or existing.public_audit is distinct from target_public_audit
           or existing.approval_subject_sha256
                is distinct from target_approval_subject_sha256 then
            raise exception 'exact Telegram resolution replay conflicts'
                using errcode = '23505';
        end if;
        return pg_catalog.jsonb_build_object(
            'resolved', true,
            'reused', true,
            'resolution_id', existing.resolution_id,
            'publication_id', existing.publication_id,
            'job_id', existing.job_id,
            'content_item_id', existing.content_item_id,
            'content_version_id', existing.content_version_id,
            'publication_status', 'delivery_unknown',
            'job_status', 'failed',
            'delivery_outcome', existing.delivery_outcome,
            'disposition', existing.disposition,
            'public_observation', existing.public_observation,
            'approval_subject_sha256', existing.approval_subject_sha256,
            'resolved_at', existing.resolved_at,
            'resend_authorized', false,
            'provider_calls', 0,
            'database_claims', 0
        );
    end if;

    select approval.* into approved
    from private.exact_telegram_delivery_unknown_approvals as approval
    where approval.workspace_id = target_workspace_id
      and approval.operator_approval_id = target_operator_approval_id
      and approval.content_item_id = target_content_item_id
      and approval.content_version_id = target_content_version_id
      and approval.publication_id = target_publication_id
      and approval.job_id = target_job_id
      and approval.resolution_id = target_resolution_id
      and approval.approval_subject_sha256
            = target_approval_subject_sha256
      and approval.approved_release_sha = target_release_sha;
    if not found
       or approved.expires_at <= pg_catalog.clock_timestamp() then
        raise exception 'exact Telegram resolution approval is missing or expired'
            using errcode = '23514';
    end if;

    subject := private.exact_telegram_delivery_resolution_subject(
        target_workspace_id,
        target_content_item_id,
        target_content_version_id,
        target_publication_id,
        target_job_id,
        target_resolution_id,
        target_operator_approval_id,
        approved.approved_by,
        approved.expires_at,
        approved.approved_at,
        target_release_sha,
        target_public_audit
    );
    subject_sha := pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(subject::text, 'UTF8'), 'sha256'
    ), 'hex');
    if subject is distinct from approved.approval_subject
       or subject_sha is distinct from target_approval_subject_sha256 then
        raise exception 'exact Telegram resolution approval subject changed'
            using errcode = '23514';
    end if;

    resolved_time := pg_catalog.clock_timestamp();
    if resolved_time >= approved.expires_at then
        raise exception 'exact Telegram resolution approval expired'
            using errcode = '22023';
    end if;

    insert into private.exact_telegram_delivery_unknown_resolutions (
        workspace_id,
        resolution_id,
        publication_id,
        job_id,
        content_item_id,
        content_version_id,
        publication_approval_id,
        asset_id,
        delivery_attempt_id,
        delivery_started_at,
        delivery_request_sha256,
        publication_request_sha256,
        publication_response_sha256,
        job_input_sha256,
        job_output_sha256,
        content_item_row_sha256,
        content_version_row_sha256,
        publication_row_sha256,
        job_row_sha256,
        publication_approval_row_sha256,
        asset_row_sha256,
        caption_sha256,
        asset_sha256,
        public_audit,
        public_audit_sha256,
        disposition,
        delivery_outcome,
        public_observation,
        operator_approval_id,
        approval_subject,
        approval_subject_sha256,
        approved_by,
        approved_at,
        expires_at,
        approved_release_sha,
        resolved_by,
        resolved_at
    ) values (
        target_workspace_id,
        target_resolution_id,
        target_publication_id,
        target_job_id,
        target_content_item_id,
        target_content_version_id,
        (subject ->> 'publication_approval_id')::uuid,
        (subject ->> 'asset_id')::uuid,
        (subject ->> 'delivery_attempt_id')::uuid,
        (subject ->> 'delivery_started_at')::timestamptz,
        subject ->> 'delivery_request_sha256',
        subject ->> 'publication_request_sha256',
        subject ->> 'publication_response_sha256',
        subject ->> 'job_input_sha256',
        subject ->> 'job_output_sha256',
        subject ->> 'content_item_row_sha256',
        subject ->> 'content_version_row_sha256',
        subject ->> 'publication_row_sha256',
        subject ->> 'job_row_sha256',
        subject ->> 'publication_approval_row_sha256',
        subject ->> 'asset_row_sha256',
        subject ->> 'caption_sha256',
        subject ->> 'asset_sha256',
        target_public_audit,
        subject ->> 'public_audit_sha256',
        'operator_closed_without_resend',
        'unknown',
        'not_observed_at_checked_at',
        target_operator_approval_id,
        subject,
        subject_sha,
        approved.approved_by,
        approved.approved_at,
        approved.expires_at,
        target_release_sha,
        target_resolved_by,
        resolved_time
    ) returning * into committed;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        target_workspace_id,
        'publication',
        target_publication_id,
        'exact_telegram_delivery_unknown_resolved_without_resend',
        pg_catalog.jsonb_build_object(
            'resolution_id', committed.resolution_id,
            'job_id', committed.job_id,
            'content_item_id', committed.content_item_id,
            'content_version_id', committed.content_version_id,
            'delivery_attempt_id', committed.delivery_attempt_id,
            'delivery_outcome', committed.delivery_outcome,
            'disposition', committed.disposition,
            'public_observation', committed.public_observation,
            'public_audit_sha256', committed.public_audit_sha256,
            'operator_approval_id', committed.operator_approval_id,
            'approval_subject_sha256', committed.approval_subject_sha256,
            'approved_release_sha', committed.approved_release_sha,
            'approved_by', committed.approved_by,
            'resolved_by', committed.resolved_by,
            'resend_authorized', false,
            'automatic_publication', false,
            'provider_calls', 0,
            'database_claims', 0,
            'publication_state_changed', false,
            'job_state_changed', false
        )
    );

    return pg_catalog.jsonb_build_object(
        'resolved', true,
        'reused', false,
        'resolution_id', committed.resolution_id,
        'publication_id', committed.publication_id,
        'job_id', committed.job_id,
        'content_item_id', committed.content_item_id,
        'content_version_id', committed.content_version_id,
        'publication_status', 'delivery_unknown',
        'job_status', 'failed',
        'delivery_outcome', committed.delivery_outcome,
        'disposition', committed.disposition,
        'public_observation', committed.public_observation,
        'approval_subject_sha256', committed.approval_subject_sha256,
        'resolved_by', committed.resolved_by,
        'resolved_at', committed.resolved_at,
        'resend_authorized', false,
        'provider_calls', 0,
        'database_claims', 0
    );
end;
$$;

revoke all on function
    private.enforce_exact_telegram_resolution_immutable()
from public, anon, authenticated, service_role,
     coineasy_telegram_resolution;
revoke all on function
    private.enforce_resolved_exact_telegram_row_immutable()
from public, anon, authenticated, service_role,
     coineasy_telegram_resolution;
revoke all on function private.require_telegram_resolution_claims(
    uuid, text, text, text
) from public, anon, authenticated, service_role,
         coineasy_telegram_resolution;
revoke all on function private.require_telegram_resolution_approval_claims(
    jsonb, uuid, uuid, uuid, uuid, uuid, uuid, text, timestamptz
) from public, anon, authenticated, service_role,
         coineasy_telegram_resolution;
revoke all on function private.require_telegram_resolution_inspect_claims(
    jsonb, uuid, uuid, uuid, uuid, uuid, uuid, text, timestamptz, text
) from public, anon, authenticated, service_role,
         coineasy_telegram_resolution;
revoke all on function private.require_telegram_resolution_resolve_claims(
    jsonb, uuid, uuid, uuid, uuid, uuid, uuid, text
) from public, anon, authenticated, service_role,
         coineasy_telegram_resolution;
revoke all on function private.exact_telegram_delivery_resolution_subject(
    uuid, uuid, uuid, uuid, uuid, uuid, uuid, text,
    timestamptz, timestamptz, text, jsonb
) from public, anon, authenticated, service_role,
         coineasy_telegram_resolution;

revoke all on function
    public.inspect_exact_telegram_delivery_unknown_resolution(
        uuid, uuid, uuid, uuid, uuid, uuid, uuid,
        text, text, timestamptz, text, jsonb
    ) from public, anon, authenticated, service_role,
             coineasy_telegram_resolution;
revoke all on function
    public.approve_exact_telegram_delivery_unknown_resolution(
        uuid, uuid, uuid, uuid, uuid, uuid, uuid,
        text, timestamptz, text, jsonb, text
    ) from public, anon, authenticated, service_role,
             coineasy_telegram_resolution;
revoke all on function
    public.resolve_exact_telegram_delivery_unknown_without_resend(
        uuid, uuid, uuid, uuid, uuid, uuid, uuid,
        text, text, jsonb, text
    ) from public, anon, authenticated, service_role,
             coineasy_telegram_resolution;

grant execute on function
    public.inspect_exact_telegram_delivery_unknown_resolution(
        uuid, uuid, uuid, uuid, uuid, uuid, uuid,
        text, text, timestamptz, text, jsonb
    ) to coineasy_telegram_resolution;
grant execute on function
    public.approve_exact_telegram_delivery_unknown_resolution(
        uuid, uuid, uuid, uuid, uuid, uuid, uuid,
        text, timestamptz, text, jsonb, text
    ) to coineasy_telegram_resolution;
grant execute on function
    public.resolve_exact_telegram_delivery_unknown_without_resend(
        uuid, uuid, uuid, uuid, uuid, uuid, uuid,
        text, text, jsonb, text
    ) to coineasy_telegram_resolution;

commit;
