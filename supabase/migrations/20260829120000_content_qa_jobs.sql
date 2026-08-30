-- Durable, provider-neutral Content QA receipts.
--
-- A receipt is advisory only. Recording one never changes Content Studio
-- status, creates a human approval, or creates a publication. The scoped QA
-- RPC binds the verdict to one current immutable version, its latest official
-- X source, and its canonical PNG before inserting an exactly-once record.

begin;

create table private.content_qa_jobs (
    job_id uuid not null default extensions.gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    source_item_id uuid not null,
    source_canonical_url text not null check (
        source_canonical_url
            ~ '^https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{1,19}$'
    ),
    source_published_at timestamptz not null,
    banner_sha256 text not null check (banner_sha256 ~ '^[a-f0-9]{64}$'),
    input_sha256 text not null check (input_sha256 ~ '^[a-f0-9]{64}$'),
    policy_version text not null check (
        policy_version ~ '^[a-z][a-z0-9._-]{2,63}@[1-9][0-9]{0,3}$'
    ),
    decision text not null check (decision in ('PASS', 'WARN', 'BLOCK')),
    verdict jsonb not null check (jsonb_typeof(verdict) = 'object'),
    verdict_sha256 text not null check (verdict_sha256 ~ '^[a-f0-9]{64}$'),
    reviewer_principal text not null check (
        reviewer_principal
            ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,127}$'
    ),
    reviewer_model text not null check (
        reviewer_model ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{1,127}$'
    ),
    reviewer_release_sha text not null check (
        reviewer_release_sha ~ '^[a-f0-9]{40}$'
    ),
    status text not null default 'reviewed' check (status = 'reviewed'),
    reviewed_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, content_version_id, policy_version),
    unique (job_id),
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id)
        on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete restrict,
    foreign key (workspace_id, client_id, source_item_id)
        references public.source_items(workspace_id, client_id, id)
        on delete restrict
);

alter table private.content_qa_jobs enable row level security;
alter table private.content_qa_jobs force row level security;

revoke all on table private.content_qa_jobs
from public, anon, authenticated, service_role;

comment on table private.content_qa_jobs is
    'Exactly-once, provider-neutral advisory Content QA receipts bound to an immutable version, official source, and canonical banner.';

do $role$
begin
    if not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_content_qa'
    ) then
        create role coineasy_content_qa
            nologin noinherit nosuperuser nocreaterole nocreatedb
            noreplication nobypassrls;
    end if;
    alter role coineasy_content_qa nologin noinherit nobypassrls;
    if exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_content_qa'
          and (rolsuper or rolcreaterole or rolcreatedb or rolcanlogin
               or rolreplication or rolbypassrls or rolinherit)
    ) then
        raise exception 'Content QA role is privileged';
    end if;
    grant coineasy_content_qa to authenticator;
end;
$role$;

revoke all on table private.content_qa_jobs from coineasy_content_qa;

create or replace function private.content_qa_scope_matches(
    target_workspace_id uuid
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb;
begin
    begin
        claims := nullif(
            pg_catalog.current_setting('request.jwt.claims', true), ''
        )::jsonb;
    exception when others then
        return false;
    end;
    return claims ->> 'role' = 'coineasy_content_qa'
       and claims ->> 'workspace_id' = target_workspace_id::text
       and claims ->> 'sub' = 'codex:content-qa'
       and claims ->> 'capability' = 'content_qa_review'
       and claims ->> 'release_sha' ~ '^[a-f0-9]{40}$'
       and claims ->> 'environment' = 'production'
       and claims -> 'automatic_publication' = 'false'::jsonb
       and claims -> 'max_external_actions' = '0'::jsonb;
end;
$$;

revoke all on function private.content_qa_scope_matches(uuid)
from public, anon, authenticated, service_role;
grant usage on schema private to coineasy_content_qa;
grant execute on function private.content_qa_scope_matches(uuid)
to coineasy_content_qa;

create or replace function private.content_qa_release_matches(
    target_release_sha text
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb;
begin
    begin
        claims := nullif(
            pg_catalog.current_setting('request.jwt.claims', true), ''
        )::jsonb;
    exception when others then
        return false;
    end;
    return target_release_sha ~ '^[a-f0-9]{40}$'
       and claims ->> 'release_sha' = target_release_sha;
end;
$$;

revoke all on function private.content_qa_release_matches(text)
from public, anon, authenticated, service_role;
grant execute on function private.content_qa_release_matches(text)
to coineasy_content_qa;

create or replace function private.content_qa_can_read_storage_object(
    object_name text
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.assets as asset
        join public.content_items as item
          on item.workspace_id = asset.workspace_id
         and item.id = asset.content_item_id
         and item.current_version_id = asset.content_version_id
        join public.content_versions as version
          on version.workspace_id = item.workspace_id
         and version.content_item_id = item.id
         and version.id = item.current_version_id
        where private.content_qa_scope_matches(asset.workspace_id)
          and item.client_id in ('yellow', 'squid', 'babylon')
          and item.content_kind = 'daily_news'
          and item.status = 'needs_review'
          and version.generation_meta -> 'mock_mode' is distinct from 'true'::jsonb
          and version.deliverables ->> 'primary_asset_id' = asset.id::text
          and asset.storage_bucket = 'content-studio'
          and asset.storage_path = object_name
          and asset.asset_kind = 'png'
          and asset.mime_type = 'image/png'
          and asset.metadata ->> 'filename' = 'news-card.png'
          and asset.sha256 ~ '^[a-f0-9]{64}$'
    )
$$;

revoke all on function private.content_qa_can_read_storage_object(text)
from public, anon, authenticated, service_role;
grant execute on function private.content_qa_can_read_storage_object(text)
to coineasy_content_qa;

-- The legacy Grok outbox remains as immutable historical evidence during the
-- cutover. This nullable foreign key is set only when a pristine pending row
-- is atomically superseded by a Content QA receipt. It distinguishes that
-- deliberate dual-path fence from every other obsolete reason, so an exact
-- Content QA replay can be recognized without making legacy work claimable.
alter table private.grok_qa_dispatch_outbox
    add column content_qa_job_id uuid,
    add constraint grok_qa_dispatch_content_qa_job_fk
        foreign key (content_qa_job_id)
        references private.content_qa_jobs(job_id) on delete restrict,
    add constraint grok_qa_dispatch_content_qa_obsolete_check check (
        content_qa_job_id is null or status = 'obsolete'
    );

create or replace function private.enforce_content_qa_grok_fence()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if old.content_qa_job_id is null then
        if tg_op = 'DELETE' then
            return old;
        end if;
        return new;
    end if;
    if tg_op = 'DELETE' then
        raise exception 'Content QA fenced Grok rows cannot be deleted'
            using errcode = '23514';
    end if;
    if new.content_qa_job_id is distinct from old.content_qa_job_id
       or new.status is distinct from 'obsolete'
       or new.locked_by is not null
       or new.locked_at is not null
       or new.lease_expires_at is not null
       or new.provider_attempt_started_at is not null
       or new.provider_input_sha256 is not null
       or new.banner_sha256 is not null
       or new.verdict is not null
       or new.verdict_sha256 is not null
       or new.model is not null
       or new.prompt_version is not null
       or new.provider_response_id is not null
       or new.cost_in_usd_ticks is not null
       or new.x_search_citations is not null
       or new.x_search_calls is not null
       or new.completed_at is null then
        raise exception 'Content QA fenced Grok rows cannot become claimable'
            using errcode = '23514';
    end if;
    return new;
end;
$$;

revoke all on function private.enforce_content_qa_grok_fence()
from public, anon, authenticated, service_role;

create trigger enforce_content_qa_grok_fence
before update or delete on private.grok_qa_dispatch_outbox
for each row
execute function private.enforce_content_qa_grok_fence();

-- Serialize the opposite insert ordering as well. The authoritative legacy
-- completion trigger already takes a key-share lock on content_items before
-- it inserts this outbox row. A direct insert follows that same
-- item -> advisory order. The Content QA recorder separately follows the
-- established worker order for an existing row (outbox -> item -> advisory)
-- and rechecks after the item lock; an absent-row insert therefore completes
-- before that item lock or waits behind it, without creating a lock cycle. If
-- Content QA committed first, a later Grok enqueue is born obsolete and can
-- never become provider-claimable.
create or replace function private.fence_grok_insert_after_content_qa()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    existing_content_qa_job_id uuid;
begin
    -- Direct table INSERTs do not inherit the legacy RPC/completion trigger's
    -- item lock. Take it explicitly before the advisory key so absent-row
    -- inserts serialize with the Content QA recorder's item recheck.
    perform 1
    from public.content_items as target_item
    where target_item.workspace_id = new.workspace_id
      and target_item.id = new.content_item_id
    for key share;
    if not found then
        raise exception 'Grok outbox content item does not exist'
            using errcode = '23503';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'content-qa:' || new.workspace_id::text || ':'
                || new.content_version_id::text,
            0
        )
    );

    select job.job_id into existing_content_qa_job_id
    from private.content_qa_jobs as job
    where job.workspace_id = new.workspace_id
      and job.content_version_id = new.content_version_id
    order by job.reviewed_at, job.job_id
    limit 1;
    if not found then
        return new;
    end if;

    if new.status is distinct from 'pending'
       or new.attempts <> 0
       or new.content_qa_job_id is not null
       or new.locked_by is not null
       or new.locked_at is not null
       or new.lease_expires_at is not null
       or new.provider_attempt_started_at is not null
       or new.provider_input_sha256 is not null
       or new.banner_sha256 is not null
       or new.verdict is not null
       or new.verdict_sha256 is not null
       or new.model is not null
       or new.prompt_version is not null
       or new.provider_response_id is not null
       or new.cost_in_usd_ticks is not null
       or new.x_search_citations is not null
       or new.x_search_calls is not null then
        raise exception 'Content QA receipt conflicts with new Grok work'
            using errcode = '23514';
    end if;

    new.status := 'obsolete';
    new.completed_at := statement_timestamp();
    new.updated_at := statement_timestamp();
    new.content_qa_job_id := existing_content_qa_job_id;
    return new;
end;
$$;

revoke all on function private.fence_grok_insert_after_content_qa()
from public, anon, authenticated, service_role;

create trigger fence_grok_insert_after_content_qa
before insert on private.grok_qa_dispatch_outbox
for each row
execute function private.fence_grok_insert_after_content_qa();

-- The legacy receipt RPC also locks content_items before inserting. Taking
-- the same advisory key here serializes direct and RPC-backed receipt inserts
-- against Content QA. A committed Content QA receipt always wins and no later
-- Grok delivery receipt can be created for that immutable version.
create or replace function private.block_grok_receipt_after_content_qa()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    -- Match record_content_qa_verdict's lock order even for a direct receipt
    -- INSERT. The legacy receipt RPC already owns an item lock, so this is a
    -- compatible re-entrant guard for that path as well.
    perform 1
    from public.content_items as target_item
    where target_item.workspace_id = new.workspace_id
      and target_item.id = new.content_item_id
    for key share;
    if not found then
        raise exception 'Grok receipt content item does not exist'
            using errcode = '23503';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'content-qa:' || new.workspace_id::text || ':'
                || new.content_version_id::text,
            0
        )
    );
    if exists (
        select 1
        from private.content_qa_jobs as job
        where job.workspace_id = new.workspace_id
          and job.content_version_id = new.content_version_id
    ) then
        raise exception 'Grok receipt is blocked by an existing Content QA receipt'
            using errcode = '23514';
    end if;
    return new;
end;
$$;

revoke all on function private.block_grok_receipt_after_content_qa()
from public, anon, authenticated, service_role;

create trigger block_grok_receipt_after_content_qa
before insert on private.grok_qa_verdict_receipts
for each row
execute function private.block_grok_receipt_after_content_qa();

create or replace function public.record_content_qa_verdict(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_policy_version text,
    target_reviewer_principal text,
    target_reviewer_model text,
    target_reviewer_release_sha text,
    target_expected_generate_job_id uuid,
    target_expected_source_item_id uuid,
    target_expected_source_canonical_url text,
    target_expected_source_published_at timestamptz,
    target_expected_banner_sha256 text,
    target_verdict jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    primary_source public.source_items%rowtype;
    official_feed public.source_feeds%rowtype;
    receipt private.content_qa_jobs%rowtype;
    grok_dispatch private.grok_qa_dispatch_outbox%rowtype;
    grok_receipt private.grok_qa_verdict_receipts%rowtype;
    expected_handle text;
    primary_source_count integer;
    generate_job_count integer;
    generate_job_id uuid;
    approval_count integer;
    publication_count integer;
    latest_source_id uuid;
    banner_hash text;
    banner_count integer;
    input_document jsonb;
    calculated_input_sha256 text;
    calculated_verdict_sha256 text;
    issue jsonb;
    recorded boolean := false;
    content_qa_found boolean := false;
    grok_dispatch_found boolean := false;
    grok_receipt_found boolean := false;
begin
    if not private.content_qa_scope_matches(target_workspace_id)
       or not private.content_qa_release_matches(
           target_reviewer_release_sha
       ) then
        raise exception 'Content QA principal scope does not match workspace'
            using errcode = '42501';
    end if;
    if target_workspace_id is null
       or target_content_item_id is null
       or target_content_version_id is null
       or target_policy_version is distinct from 'official-x-content-qa@1'
       or target_reviewer_principal is distinct from 'codex:content-qa'
       or target_reviewer_model is distinct from 'codex'
       or coalesce(target_policy_version, '')
            !~ '^[a-z][a-z0-9._-]{2,63}@[1-9][0-9]{0,3}$'
       or coalesce(target_reviewer_principal, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,127}$'
       or target_reviewer_principal is distinct from
            btrim(target_reviewer_principal)
       or coalesce(target_reviewer_model, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{1,127}$'
       or target_reviewer_model is distinct from btrim(target_reviewer_model)
       or coalesce(target_reviewer_release_sha, '') !~ '^[a-f0-9]{40}$'
       or target_expected_generate_job_id is null
       or target_expected_source_item_id is null
       or coalesce(target_expected_source_canonical_url, '')
            !~ '^https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{1,19}$'
       or target_expected_source_published_at is null
       or coalesce(target_expected_banner_sha256, '') !~ '^[a-f0-9]{64}$'
       or jsonb_typeof(target_verdict) <> 'object'
       or not target_verdict ?& array[
           'decision', 'summary', 'fact_check', 'brand_check', 'issues',
           'next_action'
       ]
       or (select count(*) from jsonb_object_keys(target_verdict)) <> 6
       or target_verdict ->> 'decision' not in ('PASS', 'WARN', 'BLOCK')
       or jsonb_typeof(target_verdict -> 'summary') <> 'string'
       or char_length(target_verdict ->> 'summary') not between 10 and 800
       or btrim(target_verdict ->> 'summary') = ''
       or target_verdict ->> 'next_action' not in (
           'ready_for_human_approval', 'human_review', 'verify_source',
           'revise_copy', 'revise_banner'
       ) then
        raise exception 'Content QA verdict is invalid'
            using errcode = '22023';
    end if;

    if jsonb_typeof(target_verdict -> 'fact_check') <> 'object'
       or not (target_verdict -> 'fact_check') ?& array[
           'status', 'checks', 'source_urls'
       ]
       or (select count(*) from jsonb_object_keys(
           target_verdict -> 'fact_check'
       )) <> 3
       or target_verdict -> 'fact_check' ->> 'status'
            not in ('PASS', 'WARN', 'BLOCK')
       or jsonb_typeof(target_verdict -> 'fact_check' -> 'checks') <> 'array'
       or jsonb_array_length(target_verdict -> 'fact_check' -> 'checks')
            not between 1 and 6
       or exists (
           select 1
           from jsonb_array_elements(
               target_verdict -> 'fact_check' -> 'checks'
           ) as check_item(value)
           where jsonb_typeof(check_item.value) <> 'string'
              or char_length(check_item.value #>> '{}') not between 3 and 300
              or btrim(check_item.value #>> '{}') = ''
       )
       or jsonb_typeof(
            target_verdict -> 'fact_check' -> 'source_urls'
       ) <> 'array'
       or jsonb_array_length(
            target_verdict -> 'fact_check' -> 'source_urls'
       ) > 8
       or exists (
           select 1
           from jsonb_array_elements(
               target_verdict -> 'fact_check' -> 'source_urls'
           ) as source_url(value)
           where jsonb_typeof(source_url.value) <> 'string'
              or char_length(source_url.value #>> '{}') not between 9 and 2048
              or (source_url.value #>> '{}') !~ '^https://[^[:space:]#]+$'
       ) then
        raise exception 'Content QA fact check is invalid'
            using errcode = '22023';
    end if;

    if jsonb_typeof(target_verdict -> 'brand_check') <> 'object'
       or not (target_verdict -> 'brand_check') ?& array['status', 'checks']
       or (select count(*) from jsonb_object_keys(
           target_verdict -> 'brand_check'
       )) <> 2
       or target_verdict -> 'brand_check' ->> 'status'
            not in ('PASS', 'WARN', 'BLOCK')
       or jsonb_typeof(target_verdict -> 'brand_check' -> 'checks') <> 'array'
       or jsonb_array_length(target_verdict -> 'brand_check' -> 'checks')
            not between 1 and 6
       or exists (
           select 1
           from jsonb_array_elements(
               target_verdict -> 'brand_check' -> 'checks'
           ) as check_item(value)
           where jsonb_typeof(check_item.value) <> 'string'
              or char_length(check_item.value #>> '{}') not between 3 and 300
              or btrim(check_item.value #>> '{}') = ''
       ) then
        raise exception 'Content QA brand check is invalid'
            using errcode = '22023';
    end if;

    if jsonb_typeof(target_verdict -> 'issues') <> 'array'
       or jsonb_array_length(target_verdict -> 'issues') > 3 then
        raise exception 'Content QA issues are invalid'
            using errcode = '22023';
    end if;
    for issue in
        select value from jsonb_array_elements(target_verdict -> 'issues')
    loop
        if jsonb_typeof(issue) <> 'object'
           or not issue ?& array['severity', 'code', 'message']
           or (select count(*) from jsonb_object_keys(issue))
                not between 3 and 4
           or issue ->> 'severity' not in ('WARN', 'BLOCK')
           or coalesce(issue ->> 'code', '') !~ '^[a-z][a-z0-9_]{2,47}$'
           or char_length(coalesce(issue ->> 'message', ''))
                not between 3 and 500
           or (issue ? 'evidence_url' and (
               jsonb_typeof(issue -> 'evidence_url') <> 'string'
               or char_length(issue ->> 'evidence_url') not between 9 and 2048
               or (issue ->> 'evidence_url') !~ '^https://[^[:space:]#]+$'
           )) then
            raise exception 'Content QA issue is invalid'
                using errcode = '22023';
        end if;
    end loop;

    if target_verdict ->> 'decision' = 'PASS' and (
        target_verdict -> 'fact_check' ->> 'status' <> 'PASS'
        or target_verdict -> 'brand_check' ->> 'status' <> 'PASS'
        or jsonb_array_length(target_verdict -> 'issues') <> 0
        or target_verdict ->> 'next_action' <> 'ready_for_human_approval'
        or jsonb_array_length(
            target_verdict -> 'fact_check' -> 'source_urls'
        ) = 0
    ) then
        raise exception 'Content QA PASS evidence is incomplete'
            using errcode = '22023';
    end if;
    if target_verdict ->> 'decision' <> 'PASS'
       and target_verdict ->> 'next_action' = 'ready_for_human_approval' then
        raise exception 'Content QA non-PASS next action is invalid'
            using errcode = '22023';
    end if;
    if target_verdict ->> 'decision' = 'BLOCK'
       and target_verdict -> 'fact_check' ->> 'status' <> 'BLOCK'
       and target_verdict -> 'brand_check' ->> 'status' <> 'BLOCK'
       and not exists (
           select 1
           from jsonb_array_elements(target_verdict -> 'issues')
                as blocking_issue(value)
           where blocking_issue.value ->> 'severity' = 'BLOCK'
       ) then
        raise exception 'Content QA BLOCK evidence is incomplete'
            using errcode = '22023';
    end if;

    -- Existing Grok workers claim and authorize in outbox -> item order. Lock
    -- that row first so Content QA cannot form the inverse item -> outbox
    -- deadlock. An absent outbox is rechecked after the item/advisory locks,
    -- covering a concurrent authoritative enqueue as well.
    select legacy.* into grok_dispatch
    from private.grok_qa_dispatch_outbox as legacy
    where legacy.workspace_id = target_workspace_id
      and legacy.content_version_id = target_content_version_id
    for update;
    grok_dispatch_found := found;

    -- Serialize this advisory receipt with normal review/publication writers.
    -- Their FK checks or RPCs take a key-share/update lock on this item, which
    -- conflicts with FOR UPDATE and closes the zero-approval/publication gap.
    select current_item.* into item
    from public.content_items as current_item
    where current_item.workspace_id = target_workspace_id
      and current_item.id = target_content_item_id
    for update;

    if not found
       or item.content_kind is distinct from 'daily_news'
       or item.status is distinct from 'needs_review'
       or item.current_version_id is distinct from target_content_version_id then
        raise exception 'Content QA target is not the current daily-news review version'
            using errcode = '23514';
    end if;

    -- Match the legacy outbox BEFORE INSERT fence after owning the item.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'content-qa:' || target_workspace_id::text || ':'
                || target_content_version_id::text,
            0
        )
    );

    -- If no row existed at the first probe, an enqueue could have committed
    -- before the item lock. Re-read under lock; later enqueues now wait on the
    -- item and are born obsolete from this receipt.
    select legacy.* into grok_dispatch
    from private.grok_qa_dispatch_outbox as legacy
    where legacy.workspace_id = target_workspace_id
      and legacy.content_version_id = target_content_version_id
    for update;
    grok_dispatch_found := found;

    select current_version.* into version
    from public.content_versions as current_version
    where current_version.workspace_id = target_workspace_id
      and current_version.content_item_id = target_content_item_id
      and current_version.id = target_content_version_id
    for share;

    if not found
       or version.generation_meta -> 'mock_mode' = 'true'::jsonb then
        raise exception 'Content QA target version is not eligible'
            using errcode = '23514';
    end if;

    expected_handle := case item.client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
        else null
    end;
    if expected_handle is null then
        raise exception 'Content QA client has no official X identity'
            using errcode = '23514';
    end if;

    -- Freeze every existing link before counting; the item FOR UPDATE blocks
    -- new FK-linked rows while these row locks block delete or mutation.
    perform 1
    from public.content_source_links as link
    where link.workspace_id = target_workspace_id
      and link.client_id = item.client_id
      and link.content_item_id = item.id
    order by link.source_item_id
    for share of link;

    select count(*) into primary_source_count
    from public.content_source_links as link
    where link.workspace_id = target_workspace_id
      and link.client_id = item.client_id
      and link.content_item_id = item.id
      and link.position = 0;
    if primary_source_count <> 1 then
        raise exception 'Content QA requires exactly one primary source'
            using errcode = '23514';
    end if;

    select source.* into primary_source
    from public.content_source_links as link
    join public.source_items as source
      on source.workspace_id = link.workspace_id
     and source.client_id = link.client_id
     and source.id = link.source_item_id
    where link.workspace_id = target_workspace_id
      and link.client_id = item.client_id
      and link.content_item_id = item.id
      and link.position = 0
    for share of link, source;

    select feed.* into official_feed
    from public.source_feeds as feed
    where feed.workspace_id = primary_source.workspace_id
      and feed.client_id = primary_source.client_id
      and feed.id = primary_source.source_feed_id
    for update;

    if not found
       or primary_source.source_type is distinct from 'tweet'
       or primary_source.author_handle is distinct from expected_handle
       or primary_source.canonical_url !~ (
           '^https://x\.com/' || substring(expected_handle from 2)
           || '/status/[0-9]{1,19}$'
       )
       or primary_source.external_id is distinct from
            split_part(primary_source.canonical_url, '/', 6)
       or primary_source.published_at is null
       or primary_source.published_at
            < statement_timestamp() - interval '24 hours'
       or primary_source.published_at
            > statement_timestamp() + interval '5 minutes'
       or official_feed.provider is distinct from 'x'
       or official_feed.handle is distinct from expected_handle
       or official_feed.active is not true
       or official_feed.poll_interval_minutes is distinct from 15
       or official_feed.last_polled_at is null
       or official_feed.last_polled_at < statement_timestamp() - interval '30 minutes'
       or official_feed.last_polled_at > statement_timestamp() + interval '5 minutes' then
        raise exception 'Content QA official X source is not fresh and eligible'
            using errcode = '23514';
    end if;

    -- New source rows reference this feed and take KEY SHARE, so the feed
    -- FOR UPDATE above blocks inserts. Lock the committed source set before
    -- deciding which official tweet is latest.
    perform 1
    from public.source_items as candidate_source
    where candidate_source.workspace_id = primary_source.workspace_id
      and candidate_source.client_id = primary_source.client_id
      and candidate_source.source_feed_id is not distinct from
            primary_source.source_feed_id
    order by candidate_source.id
    for share of candidate_source;

    select latest.id into latest_source_id
    from public.source_items as latest
    where latest.workspace_id = primary_source.workspace_id
      and latest.client_id = primary_source.client_id
      and latest.source_feed_id is not distinct from
            primary_source.source_feed_id
      and latest.source_type = 'tweet'
    order by latest.published_at desc nulls last, latest.id desc
    limit 1;
    if latest_source_id is distinct from primary_source.id then
        raise exception 'Content QA primary source is not the latest official tweet'
            using errcode = '23514';
    end if;

    -- Standard Yellow/Babylon/Squid Content QA may only attest the single
    -- natural official-X generation that produced this exact current version.
    -- manual_only and duplicate jobs fail closed.
    -- The item lock blocks new FK-linked jobs. Freeze all existing generation
    -- rows so a status/output update cannot change the exact producer set.
    perform 1
    from public.jobs as review_job
    where review_job.workspace_id = target_workspace_id
      and review_job.client_id = item.client_id
      and review_job.content_item_id = target_content_item_id
      and review_job.job_kind = 'generate'
    order by review_job.id
    for share of review_job;

    select count(*), (array_agg(review_job.id order by review_job.id))[1]
    into generate_job_count, generate_job_id
    from public.jobs as review_job
    where review_job.workspace_id = target_workspace_id
      and review_job.client_id = item.client_id
      and review_job.content_item_id = target_content_item_id
      and review_job.job_kind = 'generate'
      and review_job.status = 'succeeded'
      and review_job.input ->> 'workflow' = 'official_x_review_draft_v1'
      and review_job.input -> 'manual_only' = 'false'::jsonb
      and review_job.input -> 'source_item_ids'
            is not distinct from jsonb_build_array(primary_source.id::text)
      and review_job.output ->> 'content_item_id'
            = target_content_item_id::text
      and review_job.output ->> 'content_version_id'
            = target_content_version_id::text
      and review_job.output -> 'source_item_ids'
            is not distinct from jsonb_build_array(primary_source.id::text);
    if generate_job_count <> 1 or generate_job_id is null then
        raise exception 'Content QA requires exactly one natural official-X generation'
            using errcode = '23514';
    end if;

    -- Freeze asset metadata and the corresponding Storage catalog row. The
    -- item lock already blocks a new asset FK from appearing mid-record.
    perform 1
    from public.assets as asset
    join storage.objects as stored
      on stored.bucket_id = asset.storage_bucket
     and stored.name = asset.storage_path
    where asset.workspace_id = target_workspace_id
      and asset.content_item_id = target_content_item_id
      and asset.content_version_id = target_content_version_id
    order by asset.id
    for share of asset, stored;

    select count(*), max(asset.sha256)
    into banner_count, banner_hash
    from public.assets as asset
    join storage.objects as stored
      on stored.bucket_id = asset.storage_bucket
     and stored.name = asset.storage_path
    where asset.workspace_id = target_workspace_id
      and asset.content_item_id = target_content_item_id
      and asset.content_version_id = target_content_version_id
      and asset.id::text = version.deliverables ->> 'primary_asset_id'
      and asset.asset_kind = 'png'
      and asset.storage_bucket = 'content-studio'
      and asset.mime_type = 'image/png'
      and asset.metadata ->> 'filename' = 'news-card.png'
      and asset.storage_path = item.workspace_id::text || '/'
            || item.client_id || '/' || asset.id::text || '/news-card.png'
      and asset.sha256 ~ '^[a-f0-9]{64}$';
    if banner_count <> 1 or banner_hash is null then
        raise exception 'Content QA canonical PNG is unavailable'
            using errcode = '23514';
    end if;

    -- The MCP must echo the immutable provenance it showed to the reviewer.
    -- Comparing every server-resolved value closes the read/package/record
    -- TOCTOU window without trusting caller-computed hashes.
    if target_expected_generate_job_id is distinct from generate_job_id
       or target_expected_source_item_id is distinct from primary_source.id
       or target_expected_source_canonical_url
            is distinct from primary_source.canonical_url
       or target_expected_source_published_at
            is distinct from primary_source.published_at
       or target_expected_banner_sha256 is distinct from banner_hash then
        raise exception 'Content QA expected provenance does not match current evidence'
            using errcode = '23514';
    end if;

    -- Existing rows cannot disappear while counted; new rows are blocked by
    -- the parent item FOR UPDATE FK conflict.
    perform 1
    from public.approvals as approval
    where approval.workspace_id = target_workspace_id
      and approval.client_id = item.client_id
      and approval.content_item_id = target_content_item_id
      and approval.content_version_id = target_content_version_id
    order by approval.id
    for share of approval;
    perform 1
    from public.publications as publication
    where publication.workspace_id = target_workspace_id
      and publication.client_id = item.client_id
      and publication.content_item_id = target_content_item_id
      and publication.content_version_id = target_content_version_id
    order by publication.id
    for share of publication;

    select count(*) into approval_count
    from public.approvals as approval
    where approval.workspace_id = target_workspace_id
      and approval.client_id = item.client_id
      and approval.content_item_id = target_content_item_id
      and approval.content_version_id = target_content_version_id;
    select count(*) into publication_count
    from public.publications as publication
    where publication.workspace_id = target_workspace_id
      and publication.client_id = item.client_id
      and publication.content_item_id = target_content_item_id
      and publication.content_version_id = target_content_version_id;
    if approval_count <> 0 or publication_count <> 0 then
        raise exception 'Content QA requires zero approvals and publications'
            using errcode = '23514';
    end if;

    -- Lock both legacy delivery ledgers before consuming the Content QA key.
    -- The first record may coexist only with no Grok outbox or with one
    -- untouched pending row that has never crossed the provider boundary.
    -- Once Content QA wins, that pending row is atomically made obsolete.
    select existing.* into receipt
    from private.content_qa_jobs as existing
    where existing.workspace_id = target_workspace_id
      and existing.content_version_id = target_content_version_id
      and existing.policy_version = target_policy_version
    for update;
    content_qa_found := found;

    select legacy_receipt.* into grok_receipt
    from private.grok_qa_verdict_receipts as legacy_receipt
    where legacy_receipt.workspace_id = target_workspace_id
      and legacy_receipt.content_version_id = target_content_version_id
    for update;
    grok_receipt_found := found;

    if grok_receipt_found then
        raise exception 'Content QA is blocked by an existing Grok delivery receipt'
            using errcode = '23514';
    end if;
    if grok_dispatch_found and (
        grok_dispatch.content_item_id is distinct from target_content_item_id
        or grok_dispatch.client_id is distinct from item.client_id
        or grok_dispatch.source_item_id is distinct from primary_source.id
        or grok_dispatch.source_url
            is distinct from primary_source.canonical_url
        or grok_dispatch.source_published_at
            is distinct from primary_source.published_at
    ) then
        raise exception 'Content QA legacy Grok identity does not match'
            using errcode = '23514';
    end if;
    if not content_qa_found and grok_dispatch_found and (
        grok_dispatch.status is distinct from 'pending'
        or grok_dispatch.content_qa_job_id is not null
        or grok_dispatch.provider_attempt_started_at is not null
        or grok_dispatch.provider_input_sha256 is not null
        or grok_dispatch.banner_sha256 is not null
        or grok_dispatch.verdict is not null
        or grok_dispatch.verdict_sha256 is not null
        or grok_dispatch.model is not null
        or grok_dispatch.prompt_version is not null
        or grok_dispatch.provider_response_id is not null
        or grok_dispatch.cost_in_usd_ticks is not null
        or grok_dispatch.x_search_citations is not null
        or grok_dispatch.x_search_calls is not null
        or grok_dispatch.completed_at is not null
    ) then
        raise exception 'Content QA is blocked by Grok work that is not pristine pending'
            using errcode = '23514';
    end if;
    if content_qa_found and grok_dispatch_found and (
        grok_dispatch.status is distinct from 'obsolete'
        or grok_dispatch.content_qa_job_id is distinct from receipt.job_id
        or grok_dispatch.provider_attempt_started_at is not null
        or grok_dispatch.verdict is not null
    ) then
        raise exception 'Content QA replay has a different Grok obsolete reason'
            using errcode = '23514';
    end if;

    if (
        target_verdict ->> 'decision' = 'PASS'
        and target_verdict -> 'fact_check' -> 'source_urls'
            is distinct from jsonb_build_array(primary_source.canonical_url)
    ) or (
        target_verdict ->> 'decision' <> 'PASS'
        and target_verdict -> 'fact_check' -> 'source_urls'
            not in ('[]'::jsonb, jsonb_build_array(primary_source.canonical_url))
    ) or exists (
        select 1
        from jsonb_array_elements(target_verdict -> 'issues') as verdict_issue(value)
        where verdict_issue.value ? 'evidence_url'
          and verdict_issue.value ->> 'evidence_url'
                is distinct from primary_source.canonical_url
    ) then
        raise exception 'Content QA verdict source evidence is invalid'
            using errcode = '22023';
    end if;

    input_document := jsonb_build_object(
        'schema_version', 'coineasy.content_qa.review_input.v1',
        'workspace_id', target_workspace_id,
        'client_id', item.client_id,
        'content_item_id', target_content_item_id,
        'content_version', jsonb_build_object(
            'id', target_content_version_id,
            'version_number', version.version_number,
            'prompt_version', version.prompt_version,
            'locale', version.locale,
            'title', version.title,
            'content', version.content,
            'channel_copy', version.channel_copy,
            'deliverables', version.deliverables,
            'qa', version.qa,
            'generation_meta', version.generation_meta
        ),
        'generate_job_id', generate_job_id,
        'policy_version', target_policy_version,
        'source_item_id', primary_source.id,
        'source_canonical_url', primary_source.canonical_url,
        'source_published_at', primary_source.published_at,
        'banner_sha256', banner_hash
    );
    calculated_input_sha256 := encode(extensions.digest(
        convert_to(input_document::text, 'UTF8'), 'sha256'
    ), 'hex');
    calculated_verdict_sha256 := encode(extensions.digest(
        convert_to(target_verdict::text, 'UTF8'), 'sha256'
    ), 'hex');

    insert into private.content_qa_jobs (
        workspace_id, client_id, content_item_id, content_version_id,
        source_item_id, source_canonical_url, source_published_at,
        banner_sha256, input_sha256, policy_version, decision, verdict,
        verdict_sha256, reviewer_principal, reviewer_model,
        reviewer_release_sha
    ) values (
        target_workspace_id, item.client_id, target_content_item_id,
        target_content_version_id, primary_source.id,
        primary_source.canonical_url, primary_source.published_at,
        banner_hash, calculated_input_sha256, target_policy_version,
        target_verdict ->> 'decision', target_verdict,
        calculated_verdict_sha256, target_reviewer_principal,
        target_reviewer_model, target_reviewer_release_sha
    )
    on conflict (workspace_id, content_version_id, policy_version) do nothing
    returning * into receipt;

    recorded := found;
    if not recorded then
        select existing.* into receipt
        from private.content_qa_jobs as existing
        where existing.workspace_id = target_workspace_id
          and existing.content_version_id = target_content_version_id
          and existing.policy_version = target_policy_version
        for update;
    end if;

    if recorded and grok_dispatch_found then
        update private.grok_qa_dispatch_outbox as legacy
        set status = 'obsolete',
            locked_by = null,
            locked_at = null,
            lease_expires_at = null,
            completed_at = statement_timestamp(),
            updated_at = statement_timestamp(),
            content_qa_job_id = receipt.job_id
        where legacy.workspace_id = target_workspace_id
          and legacy.content_version_id = target_content_version_id
          and legacy.status = 'pending'
          and legacy.content_qa_job_id is null
          and legacy.provider_attempt_started_at is null
          and legacy.verdict is null;
        if not found then
            raise exception 'Content QA could not atomically fence the Grok path'
                using errcode = '40001';
        end if;
    end if;

    if not recorded and (
        receipt.content_item_id is distinct from target_content_item_id
        or receipt.client_id is distinct from item.client_id
        or receipt.source_item_id is distinct from primary_source.id
        or receipt.source_canonical_url
            is distinct from primary_source.canonical_url
        or receipt.source_published_at is distinct from primary_source.published_at
        or receipt.banner_sha256 is distinct from banner_hash
        or receipt.input_sha256 is distinct from calculated_input_sha256
        or receipt.decision is distinct from target_verdict ->> 'decision'
        or receipt.verdict is distinct from target_verdict
        or receipt.verdict_sha256 is distinct from calculated_verdict_sha256
        or receipt.reviewer_principal is distinct from target_reviewer_principal
        or receipt.reviewer_model is distinct from target_reviewer_model
        or receipt.reviewer_release_sha
            is distinct from target_reviewer_release_sha
        or receipt.status is distinct from 'reviewed'
    ) then
        return jsonb_build_object(
            'recorded', false,
            'status', 'duplicate_conflict',
            'job_id', receipt.job_id,
            'input_sha256', receipt.input_sha256,
            'verdict_sha256', receipt.verdict_sha256,
            'decision', receipt.decision,
            'policy_version', receipt.policy_version,
            'reviewer_principal', receipt.reviewer_principal,
            'reviewer_model', receipt.reviewer_model,
            'reviewer_release_sha', receipt.reviewer_release_sha
        );
    end if;

    return jsonb_build_object(
        'recorded', recorded,
        'status', 'reviewed',
        'job_id', receipt.job_id,
        'input_sha256', receipt.input_sha256,
        'verdict_sha256', receipt.verdict_sha256,
        'decision', receipt.decision,
        'policy_version', receipt.policy_version,
        'reviewer_principal', receipt.reviewer_principal,
        'reviewer_model', receipt.reviewer_model,
        'reviewer_release_sha', receipt.reviewer_release_sha
    );
end;
$$;

comment on function public.record_content_qa_verdict(
    uuid, uuid, uuid, text, text, text, text,
    uuid, uuid, text, timestamptz, text, jsonb
) is
    'Records one provider-neutral advisory Content QA verdict after exact-version, source, banner, approval, and publication fences.';

create or replace function public.get_content_qa_job(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_policy_version text
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object(
        'job_id', job.job_id,
        'workspace_id', job.workspace_id,
        'content_item_id', job.content_item_id,
        'content_version_id', job.content_version_id,
        'source_item_id', job.source_item_id,
        'banner_sha256', job.banner_sha256,
        'input_sha256', job.input_sha256,
        'verdict_sha256', job.verdict_sha256,
        'decision', job.decision,
        'status', job.status,
        'policy_version', job.policy_version,
        'reviewer_principal', job.reviewer_principal,
        'reviewer_model', job.reviewer_model,
        'reviewer_release_sha', job.reviewer_release_sha,
        'reviewed_at', job.reviewed_at
    )
    from private.content_qa_jobs as job
    where job.workspace_id = target_workspace_id
      and job.content_item_id = target_content_item_id
      and job.content_version_id = target_content_version_id
      and job.policy_version = target_policy_version
      and private.content_qa_scope_matches(target_workspace_id)
$$;

comment on function public.get_content_qa_job(uuid, uuid, uuid, text) is
    'Returns bounded Content QA receipt identity and hashes without copy, source body, provider payloads, approval, or publication authority.';

create or replace function public.list_content_qa_library(
    target_workspace_id uuid,
    target_client_id text default null,
    target_content_kind text default 'daily_news',
    target_status text default 'needs_review',
    target_limit integer default 5,
    target_before_created_at timestamptz default null,
    target_before_id uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if not private.content_qa_scope_matches(target_workspace_id)
       or target_client_id is not null
          and target_client_id not in ('yellow', 'squid', 'babylon')
       or target_content_kind is distinct from 'daily_news'
       or target_status is distinct from 'needs_review'
       or target_limit not between 1 and 5 then
        raise exception 'Content QA library scope is invalid'
            using errcode = '42501';
    end if;
    return public.list_content_library(
        target_workspace_id, target_client_id, target_content_kind,
        target_status, target_limit, target_before_created_at,
        target_before_id
    );
end;
$$;

create or replace function public.get_content_qa_library_item(
    target_workspace_id uuid,
    target_content_item_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    result jsonb;
begin
    if not private.content_qa_scope_matches(target_workspace_id) then
        raise exception 'Content QA item scope is invalid'
            using errcode = '42501';
    end if;
    result := public.get_content_library_item(
        target_workspace_id, target_content_item_id
    );
    if result is not null and (
        result ->> 'client_id' not in ('yellow', 'squid', 'babylon')
        or result ->> 'content_kind' <> 'daily_news'
        or result ->> 'status' <> 'needs_review'
    ) then
        return null;
    end if;
    -- The model-facing package needs generated copy and source identity, not
    -- raw imported source/provider bodies or design metadata.
    result := result
        #- '{content,source,submitted_content}'
        #- '{content,source,resolved_content}'
        #- '{content,source,raw_source}'
        #- '{content,source,raw_payload}'
        #- '{generation_meta,provider_response}'
        #- '{generation_meta,response_payload}';
    result := pg_catalog.jsonb_set(result, '{figma_links}', '[]'::jsonb, true);
    return result;
end;
$$;

create or replace function public.get_content_qa_readiness(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if not private.content_qa_scope_matches(target_workspace_id) then
        raise exception 'Content QA readiness scope is invalid'
            using errcode = '42501';
    end if;
    return public.get_content_review_readiness(
        target_workspace_id, target_content_item_id,
        target_content_version_id
    );
end;
$$;

revoke all on function public.list_content_qa_library(
    uuid, text, text, text, integer, timestamptz, uuid
) from public, anon, authenticated, service_role;
revoke all on function public.get_content_qa_library_item(uuid, uuid)
from public, anon, authenticated, service_role;
revoke all on function public.get_content_qa_readiness(uuid, uuid, uuid)
from public, anon, authenticated, service_role;

revoke all on function public.record_content_qa_verdict(
    uuid, uuid, uuid, text, text, text, text,
    uuid, uuid, text, timestamptz, text, jsonb
) from public, anon, authenticated, service_role;
revoke all on function public.get_content_qa_job(uuid, uuid, uuid, text)
from public, anon, authenticated, service_role;

grant usage on schema public to coineasy_content_qa;
grant execute on function public.record_content_qa_verdict(
    uuid, uuid, uuid, text, text, text, text,
    uuid, uuid, text, timestamptz, text, jsonb
) to coineasy_content_qa;
grant execute on function public.get_content_qa_job(uuid, uuid, uuid, text)
to coineasy_content_qa;
grant execute on function public.list_content_qa_library(
    uuid, text, text, text, integer, timestamptz, uuid
) to coineasy_content_qa;
grant execute on function public.get_content_qa_library_item(uuid, uuid)
to coineasy_content_qa;
grant execute on function public.get_content_qa_readiness(uuid, uuid, uuid)
to coineasy_content_qa;

grant usage on schema storage to coineasy_content_qa;
grant select on table storage.objects to coineasy_content_qa;
create policy content_qa_objects_select on storage.objects
for select to coineasy_content_qa
using (
    bucket_id = 'content-studio'
    and private.content_qa_can_read_storage_object(name)
);

commit;
