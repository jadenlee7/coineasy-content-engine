-- Employee review delivery only. No trigger, schedule, provider call, approval,
-- publication, QA allowlist change, or historical backfill is installed here.
-- The separately authorized gateway/worker remains OFF by default. Reconcile
-- requires an explicit service-role invocation for today's KST review drafts.
begin;

create table private.content_ops_review_outbox (
    outbox_id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null check (client_id in ('yellow', 'origintrail', 'squid', 'babylon')),
    kst_date date not null,
    destination_role text not null default 'content_ops_private'
        check (destination_role = 'content_ops_private'),
    content_item_id uuid not null,
    content_version_id uuid not null,
    source_item_id uuid not null,
    generate_job_id uuid not null references public.jobs(id) on delete restrict,
    banner_asset_id uuid not null references public.assets(id) on delete restrict,
    banner_sha256 text not null check (banner_sha256 ~ '^[a-f0-9]{64}$'),
    source_url text not null check (
        source_url ~ '^https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{1,19}$'
    ),
    source_published_at timestamptz not null,
    status text not null default 'pending' check (status in (
        'pending', 'claimed', 'sending', 'sent', 'rejected', 'delivery_unknown', 'obsolete'
    )),
    claim_token uuid unique,
    claimed_at timestamptz,
    lease_expires_at timestamptz,
    send_started_at timestamptz,
    packet_sha256 text check (packet_sha256 ~ '^[a-f0-9]{64}$'),
    message_id bigint check (message_id between 1 and 9007199254740991),
    finished_at timestamptz,
    created_at timestamptz not null default statement_timestamp(),
    unique (workspace_id, client_id, kst_date, destination_role),
    unique (workspace_id, content_version_id, destination_role),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id) on delete restrict,
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id) on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id) on delete restrict,
    foreign key (workspace_id, client_id, source_item_id)
        references public.source_items(workspace_id, client_id, id) on delete restrict,
    check ((claim_token is null) = (claimed_at is null)),
    check ((claim_token is null) = (lease_expires_at is null)),
    check (lease_expires_at is null or lease_expires_at > claimed_at),
    check (status not in ('claimed', 'sending', 'sent', 'rejected', 'delivery_unknown')
        or claim_token is not null),
    check (status <> 'pending' or claim_token is null),
    check (status not in ('sending', 'sent', 'rejected')
        or (packet_sha256 is not null and send_started_at is not null)),
    check ((status = 'sent') = (message_id is not null)),
    check ((status in ('sent', 'rejected', 'delivery_unknown', 'obsolete'))
        = (finished_at is not null))
);

create index content_ops_review_pending_idx
    on private.content_ops_review_outbox (workspace_id, created_at, outbox_id)
    where status = 'pending';
create index content_ops_review_lease_idx
    on private.content_ops_review_outbox (workspace_id, lease_expires_at)
    where status in ('claimed', 'sending');
create index content_ops_review_source_idx
    on private.content_ops_review_outbox (workspace_id, client_id, source_item_id);
create index content_ops_review_job_idx
    on private.content_ops_review_outbox (generate_job_id);
create index content_ops_review_banner_idx
    on private.content_ops_review_outbox (banner_asset_id);
create index content_ops_review_item_idx
    on private.content_ops_review_outbox (workspace_id, content_item_id, content_version_id);

alter table private.content_ops_review_outbox enable row level security;
alter table private.content_ops_review_outbox force row level security;
revoke all on table private.content_ops_review_outbox
    from public, anon, authenticated, service_role;

-- Resolve only bounded, immutable card fields; no copy or credential is stored
-- in the delivery ledger. Item/feed locks fence concurrent linked-row inserts.
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

    perform 1 from public.jobs as candidate
    where candidate.workspace_id = target_workspace_id and candidate.client_id = item.client_id
      and candidate.content_item_id = item.id and candidate.job_kind = 'generate'
    order by candidate.id for share;
    -- Ambiguous producer sets are never guessed, including a second failed job.
    if (select count(*) from public.jobs as candidate
        where candidate.workspace_id = target_workspace_id and candidate.client_id = item.client_id
          and candidate.content_item_id = item.id and candidate.job_kind = 'generate') <> 1 then
        return null;
    end if;
    select candidate.* into generation from public.jobs as candidate
    where candidate.workspace_id = target_workspace_id and candidate.client_id = item.client_id
      and candidate.content_item_id = item.id and candidate.job_kind = 'generate'
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

create or replace function private.content_ops_review_matches(
    candidate jsonb, queued private.content_ops_review_outbox
)
returns boolean
language sql
stable
set search_path = ''
as $$
    select candidate is not null
       and candidate ->> 'client_id' = queued.client_id
       and candidate ->> 'kst_date' = queued.kst_date::text
       and candidate ->> 'content_item_id' = queued.content_item_id::text
       and candidate ->> 'content_version_id' = queued.content_version_id::text
       and candidate ->> 'source_item_id' = queued.source_item_id::text
       and candidate ->> 'generate_job_id' = queued.generate_job_id::text
       and candidate ->> 'banner_asset_id' = queued.banner_asset_id::text
       and candidate ->> 'banner_sha256' = queued.banner_sha256
       and candidate ->> 'source_url' = queued.source_url
       and (candidate ->> 'source_published_at')::timestamptz = queued.source_published_at
$$;

-- NULL target means normal daily scope; a non-NULL target fences every write
-- to one immutable version, including cleanup. No candidate-count preflight.
create or replace function public.content_ops_reconcile_daily(
    target_workspace_id uuid, target_content_version_id uuid default null
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    candidate_item record;
    card jsonb;
    inserted integer := 0;
    affected_rows integer;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'content_ops_service_role_required' using errcode = '42501';
    end if;
    if target_workspace_id is null then
        raise exception 'content_ops_workspace_required' using errcode = '22023';
    end if;
    update private.content_ops_review_outbox as queued
    set status = 'delivery_unknown', finished_at = clock_timestamp()
    where queued.workspace_id = target_workspace_id and queued.status in ('claimed', 'sending')
      and (target_content_version_id is null or queued.content_version_id = target_content_version_id)
      and queued.lease_expires_at <= clock_timestamp();
    update private.content_ops_review_outbox as queued
    set status = 'obsolete', finished_at = clock_timestamp()
    where queued.workspace_id = target_workspace_id and queued.status = 'pending'
      and (target_content_version_id is null or queued.content_version_id = target_content_version_id)
      and queued.kst_date <> pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date;
    -- At most the four actual daily slot owners, not an unbounded item scan.
    for candidate_item in
        select item.id, item.current_version_id
        from private.official_x_daily_slots as slot
        join public.jobs as job on job.id = slot.job_id and job.workspace_id = slot.workspace_id
        join public.content_items as item on item.id = job.content_item_id
          and item.workspace_id = slot.workspace_id and item.client_id = slot.client_id
        where slot.workspace_id = target_workspace_id
          and slot.kst_date = pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date
          and slot.client_id in ('yellow', 'origintrail', 'squid', 'babylon')
          and (target_content_version_id is null or item.current_version_id = target_content_version_id)
          and not exists (select 1 from private.content_ops_review_outbox as queued
            where queued.workspace_id = slot.workspace_id and queued.client_id = slot.client_id
              and queued.kst_date = slot.kst_date and queued.destination_role = 'content_ops_private')
        order by slot.client_id limit 4
    loop
        card := private.content_ops_review_candidate(
            target_workspace_id, candidate_item.id, candidate_item.current_version_id);
        if card is null or (target_content_version_id is not null
            and card ->> 'content_version_id' is distinct from target_content_version_id::text) then
            continue;
        end if;
        insert into private.content_ops_review_outbox (
            workspace_id, client_id, kst_date, content_item_id, content_version_id,
            source_item_id, generate_job_id, banner_asset_id, banner_sha256,
            source_url, source_published_at
        ) values (
            target_workspace_id, card ->> 'client_id', (card ->> 'kst_date')::date,
            (card ->> 'content_item_id')::uuid, (card ->> 'content_version_id')::uuid,
            (card ->> 'source_item_id')::uuid, (card ->> 'generate_job_id')::uuid,
            (card ->> 'banner_asset_id')::uuid, card ->> 'banner_sha256',
            card ->> 'source_url', (card ->> 'source_published_at')::timestamptz
        ) on conflict do nothing;
        get diagnostics affected_rows = row_count;
        inserted := inserted + affected_rows;
    end loop;
    return inserted;
end;
$$;

create or replace function public.content_ops_claim_review(
    target_workspace_id uuid, target_claim_token uuid,
    target_content_version_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    queued private.content_ops_review_outbox%rowtype;
    card jsonb;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'content_ops_service_role_required' using errcode = '42501';
    end if;
    if target_workspace_id is null or target_claim_token is null then
        raise exception 'content_ops_claim_invalid' using errcode = '22023';
    end if;
    -- Reusing a claim token never retrieves a card for a second send.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'content-ops-claim:' || target_claim_token::text, 0));
    -- A globally reused token (including another workspace/version) fails
    -- before any cleanup; it can never migrate ownership to this canary.
    if exists (select 1 from private.content_ops_review_outbox as existing
        where existing.claim_token = target_claim_token) then return null; end if;
    update private.content_ops_review_outbox as expired
    set status = 'delivery_unknown', finished_at = clock_timestamp()
    where expired.workspace_id = target_workspace_id and expired.status in ('claimed', 'sending')
      and (target_content_version_id is null or expired.content_version_id = target_content_version_id)
      and expired.lease_expires_at <= clock_timestamp();
    for queued in
        select pending.* from private.content_ops_review_outbox as pending
        where pending.workspace_id = target_workspace_id and pending.status = 'pending'
          and (target_content_version_id is null or pending.content_version_id = target_content_version_id)
        order by pending.created_at, pending.outbox_id limit 4 for update skip locked
    loop
        card := private.content_ops_review_candidate(
            target_workspace_id, queued.content_item_id, queued.content_version_id);
        if private.content_ops_review_matches(card, queued) is not true then
            update private.content_ops_review_outbox set status = 'obsolete', finished_at = clock_timestamp()
            where outbox_id = queued.outbox_id
              and (target_content_version_id is null or content_version_id = target_content_version_id);
            continue;
        end if;
        update private.content_ops_review_outbox
        set status = 'claimed', claim_token = target_claim_token,
            claimed_at = clock_timestamp(), lease_expires_at = clock_timestamp() + interval '120 seconds'
        where outbox_id = queued.outbox_id
          and (target_content_version_id is null or content_version_id = target_content_version_id);
        return (card - 'banner_asset_id') || jsonb_build_object('outbox_id', queued.outbox_id,
            'claim_token', target_claim_token);
    end loop;
    return null;
end;
$$;

create or replace function public.content_ops_begin_review_send(
    target_workspace_id uuid, target_outbox_id uuid, target_claim_token uuid,
    target_packet_sha256 text, target_content_version_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    queued private.content_ops_review_outbox%rowtype;
    card jsonb;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'content_ops_service_role_required' using errcode = '42501';
    end if;
    if target_workspace_id is null or target_outbox_id is null or target_claim_token is null
       or target_packet_sha256 is null or target_packet_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'content_ops_begin_invalid' using errcode = '22023';
    end if;
    select candidate.* into queued from private.content_ops_review_outbox as candidate
    where candidate.workspace_id = target_workspace_id and candidate.outbox_id = target_outbox_id
      and (target_content_version_id is null or candidate.content_version_id = target_content_version_id)
      and candidate.claim_token = target_claim_token for update;
    if not found then return jsonb_build_object('accepted', false, 'status', 'not_owned'); end if;
    if queued.status in ('claimed', 'sending') and queued.lease_expires_at <= clock_timestamp() then
        update private.content_ops_review_outbox set status = 'delivery_unknown', finished_at = clock_timestamp()
        where outbox_id = queued.outbox_id
          and (target_content_version_id is null or content_version_id = target_content_version_id);
        return jsonb_build_object('accepted', false, 'status', 'delivery_unknown');
    end if;
    -- Deliberately no idempotent begin: a lost response must not permit resend.
    if queued.status <> 'claimed' then
        return jsonb_build_object('accepted', false, 'status', queued.status);
    end if;
    card := private.content_ops_review_candidate(
        target_workspace_id, queued.content_item_id, queued.content_version_id);
    if private.content_ops_review_matches(card, queued) is not true then
        update private.content_ops_review_outbox set status = 'obsolete', finished_at = clock_timestamp()
        where outbox_id = queued.outbox_id
          and (target_content_version_id is null or content_version_id = target_content_version_id);
        return jsonb_build_object('accepted', false, 'status', 'obsolete');
    end if;
    if queued.lease_expires_at <= clock_timestamp() then
        update private.content_ops_review_outbox set status = 'delivery_unknown', finished_at = clock_timestamp()
        where outbox_id = queued.outbox_id
          and (target_content_version_id is null or content_version_id = target_content_version_id);
        return jsonb_build_object('accepted', false, 'status', 'delivery_unknown');
    end if;
    update private.content_ops_review_outbox
    set status = 'sending', send_started_at = clock_timestamp(), packet_sha256 = target_packet_sha256,
        lease_expires_at = clock_timestamp() + interval '120 seconds'
    where outbox_id = queued.outbox_id
      and (target_content_version_id is null or content_version_id = target_content_version_id);
    return jsonb_build_object('accepted', true, 'status', 'sending', 'outbox_id', queued.outbox_id);
end;
$$;

create or replace function public.content_ops_finish_review_send(
    target_workspace_id uuid, target_outbox_id uuid, target_claim_token uuid,
    target_outcome text, target_message_id bigint default null,
    target_content_version_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    queued private.content_ops_review_outbox%rowtype;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'content_ops_service_role_required' using errcode = '42501';
    end if;
    if target_workspace_id is null or target_outbox_id is null or target_claim_token is null
       or target_outcome is null or target_outcome not in ('sent', 'rejected', 'delivery_unknown')
       or (target_outcome = 'sent' and (target_message_id is null
            or target_message_id not between 1 and 9007199254740991))
       or (target_outcome <> 'sent' and target_message_id is not null) then
        raise exception 'content_ops_finish_invalid' using errcode = '22023';
    end if;
    select candidate.* into queued from private.content_ops_review_outbox as candidate
    where candidate.workspace_id = target_workspace_id and candidate.outbox_id = target_outbox_id
      and (target_content_version_id is null or candidate.content_version_id = target_content_version_id)
      and candidate.claim_token = target_claim_token for update;
    if not found then return jsonb_build_object('accepted', false, 'status', 'not_owned'); end if;
    if queued.status in ('sent', 'rejected', 'delivery_unknown', 'obsolete') then
        return jsonb_build_object('accepted', queued.status = target_outcome
            and queued.message_id is not distinct from target_message_id,
            'status', queued.status, 'outbox_id', queued.outbox_id);
    end if;
    if queued.status in ('claimed', 'sending') and queued.lease_expires_at <= clock_timestamp() then
        update private.content_ops_review_outbox set status = 'delivery_unknown', finished_at = clock_timestamp()
        where outbox_id = queued.outbox_id
          and (target_content_version_id is null or content_version_id = target_content_version_id);
        return jsonb_build_object('accepted', target_outcome = 'delivery_unknown',
            'status', 'delivery_unknown', 'outbox_id', queued.outbox_id);
    end if;
    if queued.status <> 'sending' then
        return jsonb_build_object('accepted', false, 'status', queued.status);
    end if;
    update private.content_ops_review_outbox set status = target_outcome,
        message_id = target_message_id, finished_at = clock_timestamp()
    where outbox_id = queued.outbox_id
      and (target_content_version_id is null or content_version_id = target_content_version_id);
    return jsonb_build_object('accepted', true, 'status', target_outcome, 'outbox_id', queued.outbox_id);
end;
$$;

revoke all on function private.content_ops_review_candidate(uuid, uuid, uuid)
    from public, anon, authenticated, service_role;
revoke all on function private.content_ops_review_matches(jsonb, private.content_ops_review_outbox)
    from public, anon, authenticated, service_role;
revoke all on function public.content_ops_reconcile_daily(uuid, uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.content_ops_claim_review(uuid, uuid, uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.content_ops_begin_review_send(uuid, uuid, uuid, text, uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.content_ops_finish_review_send(uuid, uuid, uuid, text, bigint, uuid)
    from public, anon, authenticated, service_role;
grant execute on function public.content_ops_reconcile_daily(uuid, uuid) to service_role;
grant execute on function public.content_ops_claim_review(uuid, uuid, uuid) to service_role;
grant execute on function public.content_ops_begin_review_send(uuid, uuid, uuid, text, uuid) to service_role;
grant execute on function public.content_ops_finish_review_send(uuid, uuid, uuid, text, bigint, uuid) to service_role;

commit;
