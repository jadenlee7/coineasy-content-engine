-- CoinEasy Content Studio: team history, review, publishing and Figma foundation.
--
-- This migration intentionally creates data structures only. It does not connect
-- Railway, Netlify, or a Figma file to a live Supabase project.

begin;

create schema if not exists private;
revoke all on schema private from public, anon;

-- ---------------------------------------------------------------------------
-- Workspaces and client registry
-- ---------------------------------------------------------------------------

create table public.workspaces (
    id uuid primary key default gen_random_uuid(),
    name text not null check (char_length(name) between 1 and 120),
    slug text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'),
    created_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index workspaces_created_by_idx
    on public.workspaces (created_by)
    where created_by is not null;

create table public.workspace_members (
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    role text not null check (role in ('owner', 'admin', 'editor', 'viewer')),
    status text not null default 'active'
        check (status in ('invited', 'active', 'suspended')),
    invited_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (workspace_id, user_id)
);

create index workspace_members_user_id_idx
    on public.workspace_members (user_id, workspace_id);
create index workspace_members_invited_by_idx
    on public.workspace_members (invited_by)
    where invited_by is not null;

create table public.workspace_clients (
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    client_id text not null check (client_id ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    display_name text not null check (char_length(display_name) between 1 and 120),
    config_revision text,
    active boolean not null default true,
    created_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (workspace_id, client_id)
);

create index workspace_clients_created_by_idx
    on public.workspace_clients (created_by)
    where created_by is not null;

-- ---------------------------------------------------------------------------
-- Intake and source deduplication
-- ---------------------------------------------------------------------------

create table public.source_feeds (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null,
    provider text not null check (provider in ('x', 'rss', 'webhook', 'manual')),
    name text not null check (char_length(name) between 1 and 160),
    source_url text,
    handle text,
    credential_ref text,
    poll_interval_minutes integer check (poll_interval_minutes between 5 and 10080),
    last_cursor text,
    last_polled_at timestamptz,
    active boolean not null default true,
    created_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (workspace_id, id),
    unique (workspace_id, client_id, id),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id) on delete restrict
);

create index source_feeds_workspace_client_idx
    on public.source_feeds (workspace_id, client_id, active);
create index source_feeds_created_by_idx
    on public.source_feeds (created_by)
    where created_by is not null;

create table public.source_items (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null,
    source_feed_id uuid,
    external_id text,
    source_type text not null check (source_type in ('tweet', 'thread', 'article', 'blog', 'video', 'manual')),
    canonical_url text,
    author_handle text,
    published_at timestamptz,
    title text,
    body text not null,
    media jsonb not null default '[]'::jsonb check (jsonb_typeof(media) = 'array'),
    raw_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(raw_payload) = 'object'),
    source_hash text not null check (char_length(source_hash) between 16 and 128),
    ingested_by uuid references auth.users(id) on delete set null,
    ingested_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    unique (workspace_id, id),
    unique (workspace_id, client_id, id),
    unique (workspace_id, client_id, source_hash),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id) on delete restrict,
    foreign key (workspace_id, client_id, source_feed_id)
        references public.source_feeds(workspace_id, client_id, id) on delete restrict
);

create index source_items_workspace_client_published_idx
    on public.source_items (workspace_id, client_id, published_at desc);
create index source_items_workspace_feed_idx
    on public.source_items (workspace_id, client_id, source_feed_id)
    where source_feed_id is not null;
create index source_items_ingested_by_idx
    on public.source_items (ingested_by)
    where ingested_by is not null;

-- ---------------------------------------------------------------------------
-- Content packages, immutable versions, and generated assets
-- ---------------------------------------------------------------------------

create table public.content_items (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null,
    content_kind text not null check (content_kind in ('daily_news', 'article', 'tutorial')),
    title text not null default '',
    status text not null default 'draft' check (
        status in (
            'draft', 'queued', 'generating', 'needs_review', 'approved',
            'rejected', 'scheduled', 'published', 'failed', 'archived'
        )
    ),
    current_version_id uuid,
    scheduled_for timestamptz,
    created_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (workspace_id, id),
    unique (workspace_id, client_id, id),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id) on delete restrict
);

create index content_items_workspace_status_idx
    on public.content_items (workspace_id, status, updated_at desc);
create index content_items_workspace_client_kind_idx
    on public.content_items (workspace_id, client_id, content_kind, created_at desc);
create index content_items_created_by_idx
    on public.content_items (created_by)
    where created_by is not null;
create index content_items_scheduled_idx
    on public.content_items (scheduled_for)
    where status = 'scheduled';

create table public.content_source_links (
    workspace_id uuid not null,
    client_id text not null,
    content_item_id uuid not null,
    source_item_id uuid not null,
    position smallint not null default 0 check (position >= 0),
    created_at timestamptz not null default now(),
    primary key (content_item_id, source_item_id),
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id) on delete cascade,
    foreign key (workspace_id, client_id, source_item_id)
        references public.source_items(workspace_id, client_id, id) on delete restrict
);

create index content_source_links_workspace_idx
    on public.content_source_links (workspace_id, client_id, content_item_id, position);
create index content_source_links_workspace_source_idx
    on public.content_source_links (workspace_id, client_id, source_item_id);

create table public.content_versions (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    content_item_id uuid not null,
    version_number integer not null check (version_number > 0),
    prompt_version text not null,
    locale text not null default 'ko-KR',
    title text not null default '',
    content jsonb not null default '{}'::jsonb check (jsonb_typeof(content) = 'object'),
    channel_copy jsonb not null default '{}'::jsonb check (jsonb_typeof(channel_copy) = 'object'),
    deliverables jsonb not null default '{}'::jsonb check (jsonb_typeof(deliverables) = 'object'),
    qa jsonb not null default '{}'::jsonb check (jsonb_typeof(qa) = 'object'),
    generation_meta jsonb not null default '{}'::jsonb check (jsonb_typeof(generation_meta) = 'object'),
    created_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    unique (workspace_id, id),
    unique (workspace_id, content_item_id, id),
    unique (content_item_id, version_number),
    foreign key (workspace_id, content_item_id)
        references public.content_items(workspace_id, id) on delete restrict
);

create index content_versions_workspace_item_idx
    on public.content_versions (workspace_id, content_item_id, version_number desc);
create index content_versions_created_by_idx
    on public.content_versions (created_by)
    where created_by is not null;

alter table public.content_items
    add constraint content_items_current_version_fk
    foreign key (current_version_id)
    references public.content_versions(id) on delete set null
    deferrable initially deferred;

create index content_items_current_version_id_idx
    on public.content_items (current_version_id)
    where current_version_id is not null;

create table public.assets (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    asset_kind text not null check (
        asset_kind in ('source_image', 'png', 'svg', 'carousel_page', 'document', 'thumbnail')
    ),
    storage_bucket text not null default 'content-studio',
    storage_path text not null,
    mime_type text not null,
    byte_size bigint check (byte_size is null or byte_size >= 0),
    sha256 text check (sha256 is null or sha256 ~ '^[a-f0-9]{64}$'),
    width integer check (width is null or width > 0),
    height integer check (height is null or height > 0),
    metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
    created_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    unique (storage_bucket, storage_path),
    foreign key (workspace_id, content_item_id)
        references public.content_items(workspace_id, id) on delete cascade,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete cascade
);

create index assets_workspace_item_idx
    on public.assets (workspace_id, content_item_id, created_at desc);
create index assets_workspace_version_idx
    on public.assets (workspace_id, content_version_id);
create index assets_created_by_idx
    on public.assets (created_by)
    where created_by is not null;

-- ---------------------------------------------------------------------------
-- Durable jobs, append-only review records, publishing, and Figma links
-- ---------------------------------------------------------------------------

create table public.jobs (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null,
    content_item_id uuid,
    job_kind text not null check (
        job_kind in ('ingest', 'generate', 'render', 'publish', 'figma_export')
    ),
    status text not null default 'queued'
        check (status in ('queued', 'running', 'retrying', 'succeeded', 'failed', 'cancelled')),
    priority smallint not null default 0 check (priority between -100 and 100),
    input jsonb not null default '{}'::jsonb check (jsonb_typeof(input) = 'object'),
    output jsonb not null default '{}'::jsonb check (jsonb_typeof(output) = 'object'),
    idempotency_key text,
    attempts integer not null default 0 check (attempts >= 0),
    max_attempts integer not null default 3 check (max_attempts between 1 and 20),
    available_at timestamptz not null default now(),
    locked_by text,
    locked_at timestamptz,
    lease_expires_at timestamptz,
    last_error_code text,
    last_error_message text,
    started_at timestamptz,
    finished_at timestamptz,
    created_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id) on delete restrict,
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id) on delete restrict,
    check ((status = 'running') = (locked_by is not null)),
    check (lease_expires_at is null or locked_at is not null)
);

-- Worker query target: SELECT ... FOR UPDATE SKIP LOCKED ordered by priority.
create index jobs_dequeue_idx
    on public.jobs (priority desc, available_at, created_at)
    where status in ('queued', 'retrying');
create index jobs_lease_recovery_idx
    on public.jobs (lease_expires_at)
    where status = 'running' and lease_expires_at is not null;
create unique index jobs_idempotency_idx
    on public.jobs (workspace_id, idempotency_key)
    where idempotency_key is not null;
create index jobs_workspace_content_idx
    on public.jobs (workspace_id, client_id, content_item_id)
    where content_item_id is not null;
create index jobs_workspace_client_idx
    on public.jobs (workspace_id, client_id, created_at desc);
create index jobs_created_by_idx
    on public.jobs (created_by)
    where created_by is not null;

create table public.approvals (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    reviewer_id uuid not null references auth.users(id) on delete restrict,
    decision text not null check (decision in ('approved', 'rejected', 'commented')),
    comment text,
    created_at timestamptz not null default now(),
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id) on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete restrict
);

create index approvals_workspace_item_idx
    on public.approvals (workspace_id, client_id, content_item_id, created_at desc);
create index approvals_workspace_version_idx
    on public.approvals (workspace_id, content_version_id);
create index approvals_reviewer_id_idx
    on public.approvals (reviewer_id, created_at desc);

create table public.publications (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    channel text not null check (channel in ('telegram', 'x', 'web')),
    status text not null default 'queued'
        check (status in ('queued', 'publishing', 'published', 'failed', 'cancelled')),
    scheduled_for timestamptz,
    published_at timestamptz,
    external_id text,
    external_url text,
    request_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(request_payload) = 'object'),
    response_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(response_payload) = 'object'),
    last_error text,
    requested_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id) on delete restrict,
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id) on delete cascade,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete cascade
);

create index publications_schedule_idx
    on public.publications (scheduled_for)
    where status = 'queued' and scheduled_for is not null;
create index publications_workspace_item_idx
    on public.publications (workspace_id, client_id, content_item_id, created_at desc);
create index publications_workspace_client_idx
    on public.publications (workspace_id, client_id, created_at desc);
create index publications_workspace_version_idx
    on public.publications (workspace_id, content_version_id);
create index publications_requested_by_idx
    on public.publications (requested_by)
    where requested_by is not null;

create table public.figma_links (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    file_key text not null,
    node_id text not null,
    page_name text,
    section_name text,
    sync_status text not null default 'linked'
        check (sync_status in ('linked', 'outdated', 'synced', 'error')),
    last_synced_at timestamptz,
    metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
    created_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (content_version_id, file_key, node_id),
    foreign key (workspace_id, content_item_id)
        references public.content_items(workspace_id, id) on delete cascade,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete cascade
);

create index figma_links_workspace_item_idx
    on public.figma_links (workspace_id, content_item_id, updated_at desc);
create index figma_links_workspace_version_idx
    on public.figma_links (workspace_id, content_version_id);
create index figma_links_created_by_idx
    on public.figma_links (created_by)
    where created_by is not null;

create table public.event_log (
    id bigint generated always as identity primary key,
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    actor_id uuid references auth.users(id) on delete set null,
    entity_type text not null,
    entity_id uuid,
    event_type text not null,
    data jsonb not null default '{}'::jsonb check (jsonb_typeof(data) = 'object'),
    occurred_at timestamptz not null default now()
);

create index event_log_workspace_time_idx
    on public.event_log (workspace_id, occurred_at desc, id desc);
create index event_log_actor_id_idx
    on public.event_log (actor_id, occurred_at desc)
    where actor_id is not null;
create index event_log_entity_idx
    on public.event_log (workspace_id, entity_type, entity_id, occurred_at desc)
    where entity_id is not null;

-- ---------------------------------------------------------------------------
-- Security-definer membership helpers live outside the exposed public schema.
-- They return only the caller's own workspace IDs and never trust user metadata.
-- ---------------------------------------------------------------------------

create or replace function private.current_workspace_ids()
returns uuid[]
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce(array_agg(wm.workspace_id), '{}'::uuid[])
    from public.workspace_members as wm
    where wm.user_id = (select auth.uid())
      and wm.status = 'active'
$$;

create or replace function private.current_write_workspace_ids()
returns uuid[]
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce(array_agg(wm.workspace_id), '{}'::uuid[])
    from public.workspace_members as wm
    where wm.user_id = (select auth.uid())
      and wm.status = 'active'
      and wm.role in ('owner', 'admin', 'editor')
$$;

create or replace function private.current_admin_workspace_ids()
returns uuid[]
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce(array_agg(wm.workspace_id), '{}'::uuid[])
    from public.workspace_members as wm
    where wm.user_id = (select auth.uid())
      and wm.status = 'active'
      and wm.role in ('owner', 'admin')
$$;

create or replace function private.current_owner_workspace_ids()
returns uuid[]
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce(array_agg(wm.workspace_id), '{}'::uuid[])
    from public.workspace_members as wm
    where wm.user_id = (select auth.uid())
      and wm.status = 'active'
      and wm.role = 'owner'
$$;

create or replace function private.can_read_storage_path(object_name text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select split_part(object_name, '/', 1) = any(
        select workspace_id::text
        from public.workspace_members
        where user_id = (select auth.uid())
          and status = 'active'
    )
$$;

revoke all on function private.current_workspace_ids() from public, anon;
revoke all on function private.current_write_workspace_ids() from public, anon;
revoke all on function private.current_admin_workspace_ids() from public, anon;
revoke all on function private.current_owner_workspace_ids() from public, anon;
revoke all on function private.can_read_storage_path(text) from public, anon;
grant usage on schema private to authenticated, service_role;
grant execute on function private.current_workspace_ids() to authenticated, service_role;
grant execute on function private.current_write_workspace_ids() to authenticated, service_role;
grant execute on function private.current_admin_workspace_ids() to authenticated, service_role;
grant execute on function private.current_owner_workspace_ids() to authenticated, service_role;
grant execute on function private.can_read_storage_path(text) to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Integrity triggers
-- ---------------------------------------------------------------------------

create or replace function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

create or replace function private.enforce_immutable_identity()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    old_row jsonb := to_jsonb(old);
    new_row jsonb := to_jsonb(new);
    key_name text;
begin
    foreach key_name in array array[
        'id', 'workspace_id', 'user_id', 'client_id', 'created_at',
        'content_item_id', 'content_version_id', 'source_item_id', 'source_feed_id',
        'reviewer_id'
    ] loop
        if old_row ? key_name
           and (old_row -> key_name) is distinct from (new_row -> key_name) then
            raise exception 'immutable column cannot change: %', key_name
                using errcode = '22000';
        end if;
    end loop;
    return new;
end;
$$;

create or replace function private.add_workspace_owner()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.created_by is null then
        raise exception 'created_by is required when creating a workspace'
            using errcode = '23502';
    end if;

    insert into public.workspace_members (workspace_id, user_id, role, status, invited_by)
    values (new.id, new.created_by, 'owner', 'active', new.created_by);
    return new;
end;
$$;

create or replace function private.prevent_last_workspace_owner()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    -- Serialize owner removals for the same workspace. Without this lock, two
    -- concurrent transactions can each observe the other active owner and both
    -- commit a demotion/deletion.
    perform 1
    from public.workspaces
    where id = old.workspace_id
    for update;
    if not found then
        return null;
    end if;

    if old.role = 'owner' and old.status = 'active'
       and (tg_op = 'DELETE' or new.role <> 'owner' or new.status <> 'active')
       and not exists (
           select 1
           from public.workspace_members
           where workspace_id = old.workspace_id
             and role = 'owner'
             and status = 'active'
             and user_id <> old.user_id
       ) then
        raise exception 'a workspace must retain at least one active owner'
            using errcode = '23514';
    end if;
    return null;
end;
$$;

create or replace function private.validate_current_version()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.current_version_id is not null and not exists (
        select 1
        from public.content_versions as cv
        where cv.id = new.current_version_id
          and cv.content_item_id = new.id
          and cv.workspace_id = new.workspace_id
    ) then
        raise exception 'current_version_id must belong to this content item'
            using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger workspaces_add_owner
after insert on public.workspaces
for each row execute function private.add_workspace_owner();

create trigger workspace_members_keep_owner
after update of role, status or delete on public.workspace_members
for each row execute function private.prevent_last_workspace_owner();

create trigger content_items_validate_current_version
before insert or update of current_version_id on public.content_items
for each row execute function private.validate_current_version();

create trigger workspaces_updated_at before update on public.workspaces
for each row execute function private.set_updated_at();
create trigger workspace_members_updated_at before update on public.workspace_members
for each row execute function private.set_updated_at();
create trigger workspace_clients_updated_at before update on public.workspace_clients
for each row execute function private.set_updated_at();
create trigger source_feeds_updated_at before update on public.source_feeds
for each row execute function private.set_updated_at();
create trigger content_items_updated_at before update on public.content_items
for each row execute function private.set_updated_at();
create trigger jobs_updated_at before update on public.jobs
for each row execute function private.set_updated_at();
create trigger publications_updated_at before update on public.publications
for each row execute function private.set_updated_at();
create trigger figma_links_updated_at before update on public.figma_links
for each row execute function private.set_updated_at();

create trigger workspaces_immutable before update on public.workspaces
for each row execute function private.enforce_immutable_identity();
create trigger workspace_members_immutable before update on public.workspace_members
for each row execute function private.enforce_immutable_identity();
create trigger workspace_clients_immutable before update on public.workspace_clients
for each row execute function private.enforce_immutable_identity();
create trigger source_feeds_immutable before update on public.source_feeds
for each row execute function private.enforce_immutable_identity();
create trigger source_items_immutable before update on public.source_items
for each row execute function private.enforce_immutable_identity();
create trigger content_items_immutable before update on public.content_items
for each row execute function private.enforce_immutable_identity();
create trigger assets_immutable before update on public.assets
for each row execute function private.enforce_immutable_identity();
create trigger jobs_immutable before update on public.jobs
for each row execute function private.enforce_immutable_identity();
create trigger publications_immutable before update on public.publications
for each row execute function private.enforce_immutable_identity();
create trigger figma_links_immutable before update on public.figma_links
for each row execute function private.enforce_immutable_identity();

revoke all on function private.set_updated_at() from public, anon, authenticated;
revoke all on function private.enforce_immutable_identity() from public, anon, authenticated;
revoke all on function private.add_workspace_owner() from public, anon, authenticated;
revoke all on function private.prevent_last_workspace_owner() from public, anon, authenticated;
revoke all on function private.validate_current_version() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Explicit privileges. Public has no implicit access; anon has no table access.
-- RLS remains the second, row-level authorization boundary for authenticated.
-- ---------------------------------------------------------------------------

revoke all on table
    public.workspaces,
    public.workspace_members,
    public.workspace_clients,
    public.source_feeds,
    public.source_items,
    public.content_items,
    public.content_source_links,
    public.content_versions,
    public.assets,
    public.jobs,
    public.approvals,
    public.publications,
    public.figma_links,
    public.event_log
from public, anon, authenticated, service_role;

grant select, insert on table public.workspaces to authenticated;
grant update (name, slug) on table public.workspaces to authenticated;
grant select, insert, delete on table public.workspace_members to authenticated;
grant update (role, status) on table public.workspace_members to authenticated;
grant select, insert, delete on table public.workspace_clients to authenticated;
grant update (display_name, config_revision, active)
    on table public.workspace_clients to authenticated;
grant select (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, last_polled_at, active, created_by, created_at, updated_at
) on table public.source_feeds to authenticated;
grant select (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, title, body, media, source_hash,
    ingested_by, ingested_at, created_at
) on table public.source_items to authenticated;
grant select, insert on table public.content_items to authenticated;
grant update (title) on table public.content_items to authenticated;
grant select, insert, delete on table public.content_source_links to authenticated;
grant select, insert on table public.content_versions to authenticated;
grant select on table public.assets to authenticated;
grant select (
    id, workspace_id, client_id, content_item_id, job_kind, status, priority,
    attempts, max_attempts, available_at, last_error_code, started_at, finished_at,
    created_by, created_at, updated_at
) on table public.jobs to authenticated;
grant select on table public.approvals to authenticated;
grant select (
    id, workspace_id, client_id, content_item_id, content_version_id, channel,
    status, scheduled_for, published_at, external_id, external_url, requested_by,
    created_at, updated_at
) on table public.publications to authenticated;
grant select on table public.figma_links to authenticated;
grant select (
    id, workspace_id, actor_id, entity_type, entity_id, event_type, occurred_at
) on table public.event_log to authenticated;

grant all privileges on table
    public.workspace_members,
    public.workspace_clients,
    public.source_feeds,
    public.source_items,
    public.content_source_links,
    public.assets,
    public.jobs,
    public.publications,
    public.figma_links
to service_role;
-- Workspace and content-item removal is a retention workflow, not an ordinary
-- worker operation. Archive in place; do not let a service bug cascade through
-- immutable versions, approvals, or the event ledger.
grant select, insert, update on table
    public.workspaces,
    public.content_items
to service_role;
-- These records are an audit trail. Trusted servers may append and read them,
-- but even the service role cannot rewrite or delete accepted history.
grant select, insert on table
    public.content_versions,
    public.approvals,
    public.event_log
to service_role;
grant usage, select on sequence public.event_log_id_seq to service_role;

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------

alter table public.workspaces enable row level security;
alter table public.workspace_members enable row level security;
alter table public.workspace_clients enable row level security;
alter table public.source_feeds enable row level security;
alter table public.source_items enable row level security;
alter table public.content_items enable row level security;
alter table public.content_source_links enable row level security;
alter table public.content_versions enable row level security;
alter table public.assets enable row level security;
alter table public.jobs enable row level security;
alter table public.approvals enable row level security;
alter table public.publications enable row level security;
alter table public.figma_links enable row level security;
alter table public.event_log enable row level security;

create policy workspaces_select_member on public.workspaces
for select to authenticated
using (id = any(private.current_workspace_ids()));
create policy workspaces_insert_self on public.workspaces
for insert to authenticated
with check ((select auth.uid()) is not null and created_by = (select auth.uid()));
create policy workspaces_update_owner on public.workspaces
for update to authenticated
using (id = any(private.current_owner_workspace_ids()))
with check (id = any(private.current_owner_workspace_ids()));
create policy workspace_members_select_member on public.workspace_members
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));
create policy workspace_members_insert_admin on public.workspace_members
for insert to authenticated
with check (
    workspace_id = any(private.current_owner_workspace_ids())
    or (
        workspace_id = any(private.current_admin_workspace_ids())
        and role in ('editor', 'viewer')
    )
);
create policy workspace_members_update_admin on public.workspace_members
for update to authenticated
using (
    workspace_id = any(private.current_owner_workspace_ids())
    or (
        workspace_id = any(private.current_admin_workspace_ids())
        and role in ('editor', 'viewer')
    )
)
with check (
    workspace_id = any(private.current_owner_workspace_ids())
    or (
        workspace_id = any(private.current_admin_workspace_ids())
        and role in ('editor', 'viewer')
    )
);
create policy workspace_members_delete_admin on public.workspace_members
for delete to authenticated
using (
    workspace_id = any(private.current_owner_workspace_ids())
    or (
        workspace_id = any(private.current_admin_workspace_ids())
        and role in ('editor', 'viewer')
    )
);

create policy workspace_clients_select_member on public.workspace_clients
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));
create policy workspace_clients_insert_admin on public.workspace_clients
for insert to authenticated
with check (
    workspace_id = any(private.current_admin_workspace_ids())
    and created_by = (select auth.uid())
);
create policy workspace_clients_update_admin on public.workspace_clients
for update to authenticated
using (workspace_id = any(private.current_admin_workspace_ids()))
with check (workspace_id = any(private.current_admin_workspace_ids()));
create policy workspace_clients_delete_admin on public.workspace_clients
for delete to authenticated
using (workspace_id = any(private.current_admin_workspace_ids()));

create policy source_feeds_select_member on public.source_feeds
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));

create policy source_items_select_member on public.source_items
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));
create policy content_items_select_member on public.content_items
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));
create policy content_items_insert_editor on public.content_items
for insert to authenticated
with check (
    workspace_id = any(private.current_write_workspace_ids())
    and created_by = (select auth.uid())
    and status = 'draft'
    and current_version_id is null
    and scheduled_for is null
);
create policy content_items_update_editor on public.content_items
for update to authenticated
using (workspace_id = any(private.current_write_workspace_ids()))
with check (workspace_id = any(private.current_write_workspace_ids()));
create policy content_source_links_select_member on public.content_source_links
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));
create policy content_source_links_insert_editor on public.content_source_links
for insert to authenticated
with check (workspace_id = any(private.current_write_workspace_ids()));
create policy content_source_links_delete_editor on public.content_source_links
for delete to authenticated
using (workspace_id = any(private.current_write_workspace_ids()));

create policy content_versions_select_member on public.content_versions
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));
create policy content_versions_insert_editor on public.content_versions
for insert to authenticated
with check (
    workspace_id = any(private.current_write_workspace_ids())
    and created_by = (select auth.uid())
);

create policy assets_select_member on public.assets
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));

create policy jobs_select_member on public.jobs
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));

create policy approvals_select_member on public.approvals
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));

create policy publications_select_member on public.publications
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));

create policy figma_links_select_member on public.figma_links
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));

create policy event_log_select_member on public.event_log
for select to authenticated
using (workspace_id = any(private.current_workspace_ids()));

-- ---------------------------------------------------------------------------
-- Browser-safe projections. These views run with the caller's permissions and
-- therefore retain the base-table RLS boundary. Authenticated users receive only
-- column-level SELECT on the fields projected here; secret references, provider
-- payloads, job locks, and publication request/response payloads stay server-only.
-- ---------------------------------------------------------------------------

create view public.content_studio_source_feeds
with (security_invoker = true, security_barrier = true)
as
select
    id,
    workspace_id,
    client_id,
    provider,
    name,
    source_url,
    handle,
    poll_interval_minutes,
    last_polled_at,
    active,
    created_by,
    created_at,
    updated_at
from public.source_feeds;

create view public.content_studio_source_items
with (security_invoker = true, security_barrier = true)
as
select
    id,
    workspace_id,
    client_id,
    source_feed_id,
    external_id,
    source_type,
    canonical_url,
    author_handle,
    published_at,
    title,
    body,
    media,
    source_hash,
    ingested_by,
    ingested_at,
    created_at
from public.source_items;

create view public.content_studio_job_statuses
with (security_invoker = true, security_barrier = true)
as
select
    id,
    workspace_id,
    client_id,
    content_item_id,
    job_kind,
    status,
    priority,
    attempts,
    max_attempts,
    available_at,
    last_error_code,
    started_at,
    finished_at,
    created_by,
    created_at,
    updated_at
from public.jobs;

create view public.content_studio_publication_statuses
with (security_invoker = true, security_barrier = true)
as
select
    id,
    workspace_id,
    client_id,
    content_item_id,
    content_version_id,
    channel,
    status,
    scheduled_for,
    published_at,
    external_id,
    external_url,
    requested_by,
    created_at,
    updated_at
from public.publications;

create view public.content_studio_activity
with (security_invoker = true, security_barrier = true)
as
select
    id,
    workspace_id,
    actor_id,
    entity_type,
    entity_id,
    event_type,
    occurred_at
from public.event_log;

revoke all on table
    public.content_studio_source_feeds,
    public.content_studio_source_items,
    public.content_studio_job_statuses,
    public.content_studio_publication_statuses,
    public.content_studio_activity
from public, anon, authenticated;

grant select on table
    public.content_studio_source_feeds,
    public.content_studio_source_items,
    public.content_studio_job_statuses,
    public.content_studio_publication_statuses,
    public.content_studio_activity
to authenticated;

-- ---------------------------------------------------------------------------
-- Workflow RPCs. The browser can create a draft row, but status transitions,
-- queue internals, review decisions, and publication requests are atomic,
-- membership-checked security-definer operations.
-- ---------------------------------------------------------------------------

create or replace function public.queue_content_generation(
    target_content_item_id uuid,
    generation_options jsonb default '{}'::jsonb,
    request_idempotency_key text default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := (select auth.uid());
    item public.content_items%rowtype;
    effective_key text;
    existing_job_id uuid;
    new_job_id uuid;
begin
    if actor_id is null then
        raise exception 'authentication required' using errcode = '42501';
    end if;
    if jsonb_typeof(generation_options) <> 'object'
       or octet_length(generation_options::text) > 32768 then
        raise exception 'generation_options must be an object no larger than 32KB'
            using errcode = '22023';
    end if;
    if request_idempotency_key is not null
       and char_length(request_idempotency_key) not between 8 and 200 then
        raise exception 'idempotency key must be 8 to 200 characters'
            using errcode = '22023';
    end if;

    select * into item
    from public.content_items
    where id = target_content_item_id
    for update;
    if not found then
        raise exception 'content item not found' using errcode = 'P0002';
    end if;
    if not exists (
        select 1
        from public.workspace_members
        where workspace_id = item.workspace_id
          and user_id = actor_id
          and status = 'active'
          and role in ('owner', 'admin', 'editor')
    ) then
        raise exception 'workspace editor role required' using errcode = '42501';
    end if;
    effective_key := coalesce(
        request_idempotency_key,
        'generate:' || item.id::text || ':' || gen_random_uuid()::text
    );
    select id into existing_job_id
    from public.jobs
    where workspace_id = item.workspace_id
      and content_item_id = item.id
      and job_kind = 'generate'
      and idempotency_key = effective_key;
    if found then
        return existing_job_id;
    end if;
    if item.status not in ('draft', 'needs_review', 'approved', 'rejected', 'failed') then
        raise exception 'content item cannot be queued from status %', item.status
            using errcode = '23514';
    end if;

    insert into public.jobs (
        workspace_id,
        client_id,
        content_item_id,
        job_kind,
        input,
        idempotency_key,
        created_by
    ) values (
        item.workspace_id,
        item.client_id,
        item.id,
        'generate',
        generation_options,
        effective_key,
        actor_id
    ) returning id into new_job_id;

    update public.content_items
    set status = 'queued', scheduled_for = null
    where id = item.id;

    insert into public.event_log (
        workspace_id, actor_id, entity_type, entity_id, event_type, data
    ) values (
        item.workspace_id,
        actor_id,
        'content_item',
        item.id,
        'generation_queued',
        jsonb_build_object('job_id', new_job_id)
    );
    return new_job_id;
end;
$$;

create or replace function public.review_content_version(
    target_content_item_id uuid,
    target_content_version_id uuid,
    review_decision text,
    review_comment text default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := (select auth.uid());
    item public.content_items%rowtype;
    target_version public.content_versions%rowtype;
    approval_id uuid;
begin
    if actor_id is null then
        raise exception 'authentication required' using errcode = '42501';
    end if;
    if review_decision not in ('approved', 'rejected', 'commented') then
        raise exception 'invalid review decision' using errcode = '22023';
    end if;
    if review_comment is not null and char_length(review_comment) > 4000 then
        raise exception 'review comment is too long' using errcode = '22023';
    end if;

    select * into item
    from public.content_items
    where id = target_content_item_id
    for update;
    if not found then
        raise exception 'content item not found' using errcode = 'P0002';
    end if;
    if not exists (
        select 1
        from public.workspace_members
        where workspace_id = item.workspace_id
          and user_id = actor_id
          and status = 'active'
          and role in ('owner', 'admin', 'editor')
    ) then
        raise exception 'workspace editor role required' using errcode = '42501';
    end if;
    if item.status <> 'needs_review' then
        raise exception 'content item is not awaiting review' using errcode = '23514';
    end if;
    select * into target_version
    from public.content_versions
    where workspace_id = item.workspace_id
      and content_item_id = item.id
      and id = target_content_version_id;
    if item.current_version_id is distinct from target_content_version_id
       or not found then
        raise exception 'review version is not the current version for this item'
            using errcode = '23514';
    end if;
    if review_decision = 'approved'
       and target_version.generation_meta @> '{"mock_mode":true}'::jsonb then
        raise exception 'mock/test content cannot be approved'
            using errcode = '23514';
    end if;

    insert into public.approvals (
        workspace_id,
        client_id,
        content_item_id,
        content_version_id,
        reviewer_id,
        decision,
        comment
    ) values (
        item.workspace_id,
        item.client_id,
        item.id,
        target_content_version_id,
        actor_id,
        review_decision,
        review_comment
    ) returning id into approval_id;

    if review_decision in ('approved', 'rejected') then
        update public.content_items
        set status = review_decision, scheduled_for = null
        where id = item.id;
    end if;

    insert into public.event_log (
        workspace_id, actor_id, entity_type, entity_id, event_type, data
    ) values (
        item.workspace_id,
        actor_id,
        'content_item',
        item.id,
        'content_' || review_decision,
        jsonb_build_object(
            'approval_id', approval_id,
            'content_version_id', target_content_version_id
        )
    );
    return approval_id;
end;
$$;

create or replace function public.request_content_publication(
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_channel text,
    target_scheduled_for timestamptz default null,
    request_idempotency_key text default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := (select auth.uid());
    item public.content_items%rowtype;
    target_version public.content_versions%rowtype;
    effective_key text;
    existing_publication_id uuid;
    publication_id uuid;
begin
    if actor_id is null then
        raise exception 'authentication required' using errcode = '42501';
    end if;
    if target_channel not in ('telegram', 'x', 'web') then
        raise exception 'invalid publication channel' using errcode = '22023';
    end if;
    if request_idempotency_key is not null
       and char_length(request_idempotency_key) not between 8 and 200 then
        raise exception 'idempotency key must be 8 to 200 characters'
            using errcode = '22023';
    end if;

    select * into item
    from public.content_items
    where id = target_content_item_id
    for update;
    if not found then
        raise exception 'content item not found' using errcode = 'P0002';
    end if;
    if not exists (
        select 1
        from public.workspace_members
        where workspace_id = item.workspace_id
          and user_id = actor_id
          and status = 'active'
          and role in ('owner', 'admin', 'editor')
    ) then
        raise exception 'workspace editor role required' using errcode = '42501';
    end if;
    select * into target_version
    from public.content_versions
    where workspace_id = item.workspace_id
      and content_item_id = item.id
      and id = target_content_version_id;
    if found
       and target_version.generation_meta @> '{"mock_mode":true}'::jsonb then
        raise exception 'mock/test content cannot be published'
            using errcode = '23514';
    end if;
    effective_key := coalesce(
        request_idempotency_key,
        'publish:' || item.id::text || ':' || target_content_version_id::text
            || ':' || target_channel || ':'
            || coalesce(target_scheduled_for::text, 'now')
    );
    select (input ->> 'publication_id')::uuid into existing_publication_id
    from public.jobs
    where workspace_id = item.workspace_id
      and content_item_id = item.id
      and job_kind = 'publish'
      and input ->> 'content_version_id' = target_content_version_id::text
      and input ->> 'channel' = target_channel
      and idempotency_key = effective_key;
    if found then
        return existing_publication_id;
    end if;
    -- Validate time only after the idempotency lookup. A legitimate retry must
    -- keep returning its original publication after its scheduled time passes.
    if target_scheduled_for is not null and target_scheduled_for < now() then
        raise exception 'scheduled time must be in the future' using errcode = '22023';
    end if;
    if item.status not in ('approved', 'scheduled')
       or item.current_version_id is distinct from target_content_version_id
       or not exists (
           select 1
           from public.content_versions
           where workspace_id = item.workspace_id
             and content_item_id = item.id
             and id = target_content_version_id
       ) then
        raise exception 'only the current approved version can be published'
            using errcode = '23514';
    end if;

    insert into public.publications (
        workspace_id,
        client_id,
        content_item_id,
        content_version_id,
        channel,
        scheduled_for,
        requested_by
    ) values (
        item.workspace_id,
        item.client_id,
        item.id,
        target_content_version_id,
        target_channel,
        target_scheduled_for,
        actor_id
    ) returning id into publication_id;

    insert into public.jobs (
        workspace_id,
        client_id,
        content_item_id,
        job_kind,
        input,
        idempotency_key,
        available_at,
        created_by
    ) values (
        item.workspace_id,
        item.client_id,
        item.id,
        'publish',
        jsonb_build_object(
            'publication_id', publication_id,
            'content_version_id', target_content_version_id,
            'channel', target_channel
        ),
        effective_key,
        coalesce(target_scheduled_for, now()),
        actor_id
    );

    update public.content_items
    set status = case
            when item.status = 'scheduled' or target_scheduled_for is not null
                then 'scheduled'
            else 'approved'
        end,
        scheduled_for = case
            when target_scheduled_for is null then item.scheduled_for
            when item.scheduled_for is null then target_scheduled_for
            else least(item.scheduled_for, target_scheduled_for)
        end
    where id = item.id;

    insert into public.event_log (
        workspace_id, actor_id, entity_type, entity_id, event_type, data
    ) values (
        item.workspace_id,
        actor_id,
        'content_item',
        item.id,
        'publication_requested',
        jsonb_build_object(
            'publication_id', publication_id,
            'content_version_id', target_content_version_id,
            'channel', target_channel
        )
    );
    return publication_id;
end;
$$;

create or replace function public.record_approved_figma_link(
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_file_key text,
    target_node_id text,
    target_page_name text default null,
    target_section_name text default null,
    link_metadata jsonb default '{}'::jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := (select auth.uid());
    item public.content_items%rowtype;
    figma_link_id uuid;
begin
    if actor_id is null then
        raise exception 'authentication required' using errcode = '42501';
    end if;
    if target_file_key is null
       or char_length(btrim(target_file_key)) not between 1 and 200 then
        raise exception 'file_key must be 1 to 200 characters'
            using errcode = '22023';
    end if;
    if target_node_id is null
       or char_length(btrim(target_node_id)) not between 1 and 200 then
        raise exception 'node_id must be 1 to 200 characters'
            using errcode = '22023';
    end if;
    if target_page_name is not null and char_length(target_page_name) > 200 then
        raise exception 'page_name is too long' using errcode = '22023';
    end if;
    if target_section_name is not null and char_length(target_section_name) > 200 then
        raise exception 'section_name is too long' using errcode = '22023';
    end if;
    if jsonb_typeof(link_metadata) <> 'object'
       or octet_length(link_metadata::text) > 32768 then
        raise exception 'link_metadata must be an object no larger than 32KB'
            using errcode = '22023';
    end if;

    select * into item
    from public.content_items
    where id = target_content_item_id
    for update;
    if not found then
        raise exception 'content item not found' using errcode = 'P0002';
    end if;
    if not exists (
        select 1
        from public.workspace_members
        where workspace_id = item.workspace_id
          and user_id = actor_id
          and status = 'active'
          and role in ('owner', 'admin', 'editor')
    ) then
        raise exception 'workspace editor role required' using errcode = '42501';
    end if;
    if item.current_version_id is distinct from target_content_version_id
       or item.status not in ('approved', 'scheduled', 'published')
       or not exists (
           select 1
           from public.content_versions
           where workspace_id = item.workspace_id
             and content_item_id = item.id
             and id = target_content_version_id
       )
       or not exists (
           select 1
           from public.approvals
           where workspace_id = item.workspace_id
             and content_item_id = item.id
             and content_version_id = target_content_version_id
             and decision = 'approved'
       ) then
        raise exception 'only the current approved version can be linked to Figma'
            using errcode = '23514';
    end if;

    insert into public.figma_links (
        workspace_id,
        content_item_id,
        content_version_id,
        file_key,
        node_id,
        page_name,
        section_name,
        metadata,
        created_by
    ) values (
        item.workspace_id,
        item.id,
        target_content_version_id,
        btrim(target_file_key),
        btrim(target_node_id),
        target_page_name,
        target_section_name,
        link_metadata,
        actor_id
    )
    on conflict (content_version_id, file_key, node_id) do update
    set page_name = excluded.page_name,
        section_name = excluded.section_name,
        sync_status = 'linked',
        last_synced_at = null,
        metadata = excluded.metadata,
        updated_at = now()
    returning id into figma_link_id;

    insert into public.event_log (
        workspace_id, actor_id, entity_type, entity_id, event_type, data
    ) values (
        item.workspace_id,
        actor_id,
        'content_item',
        item.id,
        'figma_link_recorded',
        jsonb_build_object(
            'figma_link_id', figma_link_id,
            'content_version_id', target_content_version_id,
            'file_key', btrim(target_file_key),
            'node_id', btrim(target_node_id)
        )
    );
    return figma_link_id;
end;
$$;

create or replace function public.record_tutorial_generation(
    target_content_item_id uuid,
    target_workspace_id uuid,
    target_client_id text,
    target_title text,
    target_content jsonb,
    target_generation_meta jsonb,
    target_slides jsonb,
    target_prompt_version text default 'edu-carousel@1'
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    content_version_id uuid := gen_random_uuid();
    asset_id uuid;
    slide jsonb;
    normalized_slides jsonb := '[]'::jsonb;
    asset_ids jsonb := '[]'::jsonb;
    storage_path text;
    file_name text;
    path_parts text[];
    slide_count integer;
    distinct_numbers integer;
    minimum_number integer;
    maximum_number integer;
    effective_generation_meta jsonb;
    existing_item public.content_items%rowtype;
    existing_version public.content_versions%rowtype;
    existing_asset_count integer;
    slide_ordinal integer;
begin
    if target_content_item_id is null then
        raise exception 'tutorial request id is required'
            using errcode = '22023';
    end if;
    if not exists (
        select 1
        from public.workspace_clients
        where workspace_id = target_workspace_id
          and client_id = target_client_id
          and active is true
    ) then
        raise exception 'workspace client is not ready for tutorial storage'
            using errcode = '23514';
    end if;
    if target_title is null
       or char_length(btrim(target_title)) not between 1 and 200 then
        raise exception 'tutorial title must be 1 to 200 characters'
            using errcode = '22023';
    end if;
    if target_prompt_version is null
       or char_length(btrim(target_prompt_version)) not between 1 and 120 then
        raise exception 'prompt version must be 1 to 120 characters'
            using errcode = '22023';
    end if;
    if jsonb_typeof(target_content) is distinct from 'object' then
        raise exception 'tutorial content must be an object no larger than 1MB'
            using errcode = '22023';
    end if;
    if octet_length(target_content::text) > 1048576 then
        raise exception 'tutorial content must be an object no larger than 1MB'
            using errcode = '22023';
    end if;
    if jsonb_typeof(target_generation_meta) is distinct from 'object' then
        raise exception 'generation metadata must be an object no larger than 64KB'
            using errcode = '22023';
    end if;
    if octet_length(target_generation_meta::text) > 65536 then
        raise exception 'generation metadata must be an object no larger than 64KB'
            using errcode = '22023';
    end if;
    if not coalesce(
           (target_content ->> 'request_hash') ~ '^[a-f0-9]{64}$',
           false
       )
       or target_generation_meta ->> 'request_hash'
          is distinct from target_content ->> 'request_hash' then
        raise exception 'tutorial request hash is missing or inconsistent'
            using errcode = '22023';
    end if;
    if jsonb_typeof(target_slides) is distinct from 'array' then
        raise exception 'tutorial slides must contain 1 to 12 items'
            using errcode = '22023';
    end if;
    if jsonb_array_length(target_slides) not between 1 and 12 then
        raise exception 'tutorial slides must contain 1 to 12 items'
            using errcode = '22023';
    end if;

    slide_count := jsonb_array_length(target_slides);
    effective_generation_meta := target_generation_meta || jsonb_build_object(
        'request_id', target_content_item_id::text
    );
    for slide, slide_ordinal in
        select value, ordinality
        from jsonb_array_elements(target_slides) with ordinality
    loop
        if jsonb_typeof(slide) is distinct from 'object' then
            raise exception 'tutorial slide metadata is incomplete'
                using errcode = '22023';
        end if;
        if not (slide ?& array[
               'number', 'filename', 'storage_path', 'byte_size', 'sha256',
               'width', 'height'
           ]) then
            raise exception 'tutorial slide metadata is incomplete'
                using errcode = '22023';
        end if;
        if not coalesce((slide ->> 'number') ~ '^[0-9]{1,2}$', false) then
            raise exception 'tutorial slide number is invalid'
                using errcode = '22023';
        end if;
        if (slide ->> 'number')::integer not between 1 and 12
           or (slide ->> 'number')::integer <> slide_ordinal then
            raise exception 'tutorial slide number is invalid'
                using errcode = '22023';
        end if;
        file_name := slide ->> 'filename';
        storage_path := slide ->> 'storage_path';
        if not coalesce(file_name ~ '^lesson_[0-9]{2}\.png$', false)
           or file_name <> 'lesson_' || lpad(slide ->> 'number', 2, '0') || '.png'
           or storage_path is null
           or char_length(storage_path) > 500 then
            raise exception 'tutorial slide path is invalid'
                using errcode = '22023';
        end if;
        path_parts := string_to_array(storage_path, '/');
        if array_length(path_parts, 1) <> 4
           or path_parts[1] <> target_workspace_id::text
           or path_parts[2] <> target_client_id
           or path_parts[3] !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
           or path_parts[4] <> file_name then
            raise exception 'tutorial slide is outside its workspace scope'
                using errcode = '22023';
        end if;
        if not coalesce((slide ->> 'byte_size') ~ '^[0-9]+$', false)
           or not coalesce((slide ->> 'sha256') ~ '^[a-f0-9]{64}$', false)
           or not coalesce((slide ->> 'width') ~ '^[0-9]+$', false)
           or not coalesce((slide ->> 'height') ~ '^[0-9]+$', false) then
            raise exception 'tutorial slide dimensions or digest are invalid'
                using errcode = '22023';
        end if;
        if (slide ->> 'byte_size')::bigint not between 8 and 26214400
           or (slide ->> 'width')::integer not between 1 and 10000
           or (slide ->> 'height')::integer not between 1 and 10000 then
            raise exception 'tutorial slide dimensions or digest are invalid'
                using errcode = '22023';
        end if;
        perform 1
        from storage.objects
        where bucket_id = 'content-studio'
          and name = storage_path
        for key share;
        if not found then
            raise exception 'tutorial slide object is not present in private storage'
                using errcode = '23514';
        end if;

        -- The object folder is the asset UUID, so catalog rows and Storage paths
        -- stay referentially aligned and can be audited without guesswork.
        asset_id := path_parts[3]::uuid;
        normalized_slides := normalized_slides || jsonb_build_array(
            slide || jsonb_build_object('asset_id', asset_id::text)
        );
        asset_ids := asset_ids || jsonb_build_array(asset_id::text);
    end loop;

    select count(distinct (value ->> 'number')::integer),
           min((value ->> 'number')::integer),
           max((value ->> 'number')::integer)
    into distinct_numbers, minimum_number, maximum_number
    from jsonb_array_elements(normalized_slides);
    if distinct_numbers <> slide_count
       or minimum_number <> 1
       or maximum_number <> slide_count then
        raise exception 'tutorial slide numbers must be contiguous'
            using errcode = '22023';
    end if;

    -- The caller supplies a stable request UUID. Serializing on it makes a
    -- timeout retry return the already-committed catalog instead of duplicating
    -- a tutorial or deleting files that a committed version references.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(target_content_item_id::text, 0)
    );
    select * into existing_item
    from public.content_items
    where id = target_content_item_id;
    if found then
        if existing_item.workspace_id <> target_workspace_id
           or existing_item.client_id <> target_client_id
           or existing_item.content_kind <> 'tutorial' then
            raise exception 'tutorial request id belongs to another catalog entry'
                using errcode = '23505';
        end if;
        select * into existing_version
        from public.content_versions
        where content_item_id = target_content_item_id
          and version_number = 1;
        if not found
           or existing_version.prompt_version <> btrim(target_prompt_version)
           or existing_version.title <> btrim(target_title)
           or existing_version.content is distinct from target_content
           or existing_version.generation_meta is distinct from effective_generation_meta
           or existing_version.deliverables -> 'asset_ids' is distinct from asset_ids
           or existing_version.deliverables ->> 'primary_asset_id'
              is distinct from normalized_slides -> 0 ->> 'asset_id' then
            raise exception 'tutorial request retry does not match committed content'
                using errcode = '23505';
        end if;
        select count(*) into existing_asset_count
        from public.assets as a
        where a.content_item_id = target_content_item_id
          and a.content_version_id = existing_version.id
          and a.asset_kind = 'carousel_page'
          and a.storage_bucket = 'content-studio';
        if existing_asset_count <> slide_count then
            raise exception 'tutorial request retry does not match committed assets'
                using errcode = '23505';
        end if;
        for slide in select value from jsonb_array_elements(normalized_slides)
        loop
            if not exists (
                select 1
                from public.assets as a
                where a.id = (slide ->> 'asset_id')::uuid
                  and a.workspace_id = target_workspace_id
                  and a.content_item_id = target_content_item_id
                  and a.content_version_id = existing_version.id
                  and a.asset_kind = 'carousel_page'
                  and a.storage_bucket = 'content-studio'
                  and a.storage_path = slide ->> 'storage_path'
                  and a.mime_type = 'image/png'
                  and a.byte_size = (slide ->> 'byte_size')::bigint
                  and a.sha256 = slide ->> 'sha256'
                  and a.width = (slide ->> 'width')::integer
                  and a.height = (slide ->> 'height')::integer
            ) then
                raise exception 'tutorial request retry does not match committed assets'
                    using errcode = '23505';
            end if;
        end loop;
        return jsonb_build_object(
            'content_item_id', target_content_item_id,
            'content_version_id', existing_version.id,
            'asset_ids', asset_ids
        );
    end if;

    insert into public.content_items (
        id, workspace_id, client_id, content_kind, title, status
    ) values (
        target_content_item_id,
        target_workspace_id,
        target_client_id,
        'tutorial',
        btrim(target_title),
        'needs_review'
    );

    insert into public.content_versions (
        id, workspace_id, content_item_id, version_number, prompt_version,
        title, content, deliverables, qa, generation_meta
    ) values (
        content_version_id,
        target_workspace_id,
        target_content_item_id,
        1,
        btrim(target_prompt_version),
        btrim(target_title),
        target_content,
        jsonb_build_object(
            'primary_asset_id', normalized_slides -> 0 ->> 'asset_id',
            'asset_ids', asset_ids
        ),
        jsonb_build_object(
            'source_fidelity', 'needs_review',
            'brand_alignment', 'needs_review',
            'manual_review_required', true
        ),
        effective_generation_meta
    );

    for slide in select value from jsonb_array_elements(normalized_slides)
    loop
        insert into public.assets (
            id, workspace_id, content_item_id, content_version_id, asset_kind,
            storage_bucket, storage_path, mime_type, byte_size, sha256, width,
            height, metadata
        ) values (
            (slide ->> 'asset_id')::uuid,
            target_workspace_id,
            target_content_item_id,
            content_version_id,
            'carousel_page',
            'content-studio',
            slide ->> 'storage_path',
            'image/png',
            (slide ->> 'byte_size')::bigint,
            slide ->> 'sha256',
            (slide ->> 'width')::integer,
            (slide ->> 'height')::integer,
            jsonb_build_object(
                'number', (slide ->> 'number')::integer,
                'filename', slide ->> 'filename'
            )
        );
    end loop;

    update public.content_items
    set current_version_id = content_version_id
    where id = target_content_item_id;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        target_workspace_id,
        'content_item',
        target_content_item_id,
        'tutorial_generated',
        jsonb_build_object(
            'content_version_id', content_version_id,
            'asset_ids', asset_ids
        )
    );

    return jsonb_build_object(
        'content_item_id', target_content_item_id,
        'content_version_id', content_version_id,
        'asset_ids', asset_ids
    );
end;
$$;

create or replace function public.get_tutorial_generation(
    target_content_item_id uuid,
    target_workspace_id uuid,
    target_client_id text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    expected_asset_ids jsonb;
    expected_asset_count integer;
    distinct_asset_count integer;
    resolved_asset_count integer;
    slide_rows jsonb;
begin
    if target_content_item_id is null then
        raise exception 'tutorial request id is required'
            using errcode = '22023';
    end if;
    if not exists (
        select 1
        from public.workspace_clients
        where workspace_id = target_workspace_id
          and client_id = target_client_id
          and active is true
    ) then
        raise exception 'workspace client is not ready for tutorial storage'
            using errcode = '23514';
    end if;

    select * into item
    from public.content_items
    where id = target_content_item_id
      and workspace_id = target_workspace_id
      and client_id = target_client_id
      and content_kind = 'tutorial';
    if not found then
        return null;
    end if;

    select * into version
    from public.content_versions
    where workspace_id = target_workspace_id
      and content_item_id = target_content_item_id
      and version_number = 1;
    if not found then
        return null;
    end if;

    expected_asset_ids := version.deliverables -> 'asset_ids';
    if jsonb_typeof(expected_asset_ids) is distinct from 'array' then
        raise exception 'committed tutorial catalog has invalid deliverables'
            using errcode = '23514';
    end if;
    expected_asset_count := jsonb_array_length(expected_asset_ids);
    if expected_asset_count not between 1 and 12
       or version.deliverables ->> 'primary_asset_id'
          is distinct from expected_asset_ids ->> 0 then
        raise exception 'committed tutorial catalog has invalid deliverables'
            using errcode = '23514';
    end if;
    select count(distinct expected.asset_id_text)
    into distinct_asset_count
    from jsonb_array_elements_text(expected_asset_ids)
        as expected(asset_id_text);
    if distinct_asset_count <> expected_asset_count
       or exists (
           select 1
           from jsonb_array_elements_text(expected_asset_ids)
               as expected(asset_id_text)
           where not coalesce(
               expected.asset_id_text
                   ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
               false
           )
       ) then
        raise exception 'committed tutorial catalog has invalid deliverables'
            using errcode = '23514';
    end if;

    select jsonb_agg(
        jsonb_build_object(
            'asset_id', a.id,
            'number', expected.ordinality,
            'filename', a.metadata ->> 'filename',
            'storage_path', a.storage_path,
            'byte_size', a.byte_size,
            'sha256', a.sha256,
            'width', a.width,
            'height', a.height
        ) order by expected.ordinality
    ), count(*)
    into slide_rows, resolved_asset_count
    from jsonb_array_elements_text(expected_asset_ids) with ordinality
        as expected(asset_id_text, ordinality)
    join public.assets as a
      on a.id = expected.asset_id_text::uuid
     and a.workspace_id = target_workspace_id
     and a.content_item_id = target_content_item_id
     and a.content_version_id = version.id
     and a.asset_kind = 'carousel_page'
     and a.storage_bucket = 'content-studio'
     and a.mime_type = 'image/png'
     and a.metadata ->> 'number' = expected.ordinality::text
     and a.metadata ->> 'filename'
         = 'lesson_' || lpad(expected.ordinality::text, 2, '0') || '.png'
     and a.storage_path = target_workspace_id::text || '/' || target_client_id
         || '/' || a.id::text || '/' || (a.metadata ->> 'filename')
    join storage.objects as stored
      on stored.bucket_id = a.storage_bucket
     and stored.name = a.storage_path;

    if resolved_asset_count <> expected_asset_count
       or jsonb_typeof(slide_rows) is distinct from 'array' then
        raise exception 'committed tutorial catalog has invalid slide metadata'
            using errcode = '23514';
    end if;
    if jsonb_array_length(slide_rows) <> expected_asset_count
       or slide_rows -> 0 ->> 'asset_id'
          is distinct from version.deliverables ->> 'primary_asset_id' then
        raise exception 'committed tutorial catalog has invalid slide metadata'
            using errcode = '23514';
    end if;

    return jsonb_build_object(
        'content_item_id', item.id,
        'content_version_id', version.id,
        'title', version.title,
        'content', version.content,
        'generation_meta', version.generation_meta,
        'slides', slide_rows
    );
end;
$$;

revoke all on function public.queue_content_generation(uuid, jsonb, text)
    from public, anon, authenticated;
revoke all on function public.review_content_version(uuid, uuid, text, text)
    from public, anon, authenticated;
revoke all on function public.request_content_publication(uuid, uuid, text, timestamptz, text)
    from public, anon, authenticated;
revoke all on function public.record_approved_figma_link(
    uuid, uuid, text, text, text, text, jsonb
) from public, anon, authenticated;
revoke all on function public.record_tutorial_generation(
    uuid, uuid, text, text, jsonb, jsonb, jsonb, text
) from public, anon, authenticated;
revoke all on function public.get_tutorial_generation(uuid, uuid, text)
    from public, anon, authenticated;
grant execute on function public.queue_content_generation(uuid, jsonb, text)
    to authenticated;
grant execute on function public.review_content_version(uuid, uuid, text, text)
    to authenticated;
grant execute on function public.request_content_publication(uuid, uuid, text, timestamptz, text)
    to authenticated;
grant execute on function public.record_approved_figma_link(
    uuid, uuid, text, text, text, text, jsonb
) to authenticated;
grant execute on function public.record_tutorial_generation(
    uuid, uuid, text, text, jsonb, jsonb, jsonb, text
) to service_role;
grant execute on function public.get_tutorial_generation(uuid, uuid, text)
    to service_role;

-- ---------------------------------------------------------------------------
-- Private asset bucket. Browser clients receive only the publishable key and a
-- user JWT; the service-role key remains server-side. Object paths must be:
--   {workspace_uuid}/{client_id}/{asset_uuid}/{filename}
-- ---------------------------------------------------------------------------

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'content-studio',
    'content-studio',
    false,
    26214400,
    array[
        'image/png', 'image/jpeg', 'image/webp', 'image/svg+xml',
        'application/pdf', 'application/json', 'text/plain'
    ]
)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create policy content_studio_objects_select on storage.objects
for select to authenticated
using (
    bucket_id = 'content-studio'
    and (select private.can_read_storage_path(name))
);

commit;
