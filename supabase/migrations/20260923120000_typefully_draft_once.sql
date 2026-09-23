-- Private Typefully draft-only, exact-item single-attempt fence. This migration
-- installs no trigger, schedule, worker, API key, upload, or provider call.
-- The service-role caller must hash the actual uploaded canonical PNG bytes and
-- perform authenticated Typefully account/media readbacks before using these RPCs.
begin;

create table private.typefully_media_upload_receipts (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null check (client_id in ('yellow', 'origintrail', 'squid', 'babylon')),
    content_item_id uuid not null,
    content_version_id uuid not null,
    asset_id uuid not null references public.assets(id) on delete restrict,
    asset_sha256 text not null check (asset_sha256 ~ '^[a-f0-9]{64}$'),
    uploaded_bytes_sha256 text not null check (uploaded_bytes_sha256 ~ '^[a-f0-9]{64}$'),
    social_set_id bigint not null check (social_set_id > 0),
    media_id uuid not null,
    created_at timestamptz not null default statement_timestamp(),
    unique (workspace_id, content_item_id, social_set_id),
    unique (social_set_id, media_id),
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id) on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id) on delete restrict,
    check (asset_sha256 = uploaded_bytes_sha256)
);

create table private.typefully_draft_attempts (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    approval_id uuid not null references public.approvals(id) on delete restrict,
    media_receipt_id uuid not null unique
        references private.typefully_media_upload_receipts(id) on delete restrict,
    social_set_id bigint not null check (social_set_id > 0),
    -- Reservation is unknown before the external POST. Crash or lost response
    -- must never transition this row back to a sendable state.
    status text not null default 'delivery_unknown'
        check (status in ('delivery_unknown', 'draft_created')),
    provider_draft_id bigint unique check (provider_draft_id > 0),
    reserved_at timestamptz not null default statement_timestamp(),
    confirmed_at timestamptz,
    unique (workspace_id, content_item_id),
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id) on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id) on delete restrict,
    check ((status = 'draft_created') = (provider_draft_id is not null)),
    check ((status = 'draft_created') = (confirmed_at is not null))
);

alter table private.typefully_media_upload_receipts enable row level security;
alter table private.typefully_media_upload_receipts force row level security;
alter table private.typefully_draft_attempts enable row level security;
alter table private.typefully_draft_attempts force row level security;
revoke all on table private.typefully_media_upload_receipts,
    private.typefully_draft_attempts from public, anon, authenticated, service_role;

-- An attestation, not an upload implementation. The trusted caller must pass
-- the hash of bytes actually sent to Typefully, not a copied database value.
create function private.record_typefully_media_upload(
    target_workspace_id uuid, target_content_item_id uuid,
    target_content_version_id uuid, target_asset_id uuid,
    target_uploaded_bytes_sha256 text, target_social_set_id bigint,
    target_media_id uuid
)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    banner public.assets%rowtype;
    receipt_id uuid;
begin
    if current_setting('request.jwt.claim.role', true) is distinct from 'service_role' then
        raise exception 'typefully_service_role_required' using errcode = '42501';
    end if;
    select candidate.* into item from public.content_items as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.id = target_content_item_id for update;
    if not found or item.client_id not in ('yellow', 'origintrail', 'squid', 'babylon')
       or item.status is distinct from 'approved'
       or item.content_kind is distinct from 'daily_news'
       or item.current_version_id is distinct from target_content_version_id
       or target_social_set_id is null or target_social_set_id <= 0
       or target_media_id is null or target_media_id = '00000000-0000-0000-0000-000000000000'::uuid
       or target_uploaded_bytes_sha256 is null
       or target_uploaded_bytes_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'typefully_media_owner_invalid' using errcode = '23514';
    end if;
    select candidate.* into banner from public.assets as candidate
    join public.content_versions as version
      on version.workspace_id = candidate.workspace_id
     and version.content_item_id = candidate.content_item_id
     and version.id = candidate.content_version_id
    join storage.objects as stored
      on stored.bucket_id = candidate.storage_bucket
     and stored.name = candidate.storage_path
    where candidate.workspace_id = target_workspace_id
      and candidate.content_item_id = target_content_item_id
      and candidate.content_version_id = target_content_version_id
      and candidate.id = target_asset_id
      and candidate.id::text = version.deliverables ->> 'primary_asset_id'
      and candidate.asset_kind = 'png' and candidate.mime_type = 'image/png'
      and candidate.storage_bucket = 'content-studio'
      and candidate.metadata ->> 'filename' = 'news-card.png'
      and candidate.storage_path = target_workspace_id::text || '/' || item.client_id
            || '/' || candidate.id::text || '/news-card.png'
      and candidate.sha256 = target_uploaded_bytes_sha256
      and candidate.byte_size > 0
    for share of candidate, version, stored;
    if not found then
        raise exception 'typefully_canonical_png_mismatch' using errcode = '23514';
    end if;
    insert into private.typefully_media_upload_receipts (
        workspace_id, client_id, content_item_id, content_version_id,
        asset_id, asset_sha256, uploaded_bytes_sha256, social_set_id, media_id
    ) values (
        target_workspace_id, item.client_id, item.id, target_content_version_id,
        banner.id, banner.sha256, target_uploaded_bytes_sha256,
        target_social_set_id, target_media_id
    ) returning id into receipt_id;
    return receipt_id;
end;
$$;

-- Commits the sole provider attempt before any POST. A repeated call fails;
-- callers must reconcile the existing ID and must not manufacture a new one.
create function private.reserve_typefully_draft_once(
    target_workspace_id uuid, target_content_item_id uuid,
    target_content_version_id uuid, target_approval_id uuid,
    target_media_receipt_id uuid, expected_social_set_id bigint,
    observed_x_username text, account_observed_at timestamptz,
    media_observed_at timestamptz, observed_media_status text
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    approval public.approvals%rowtype;
    receipt private.typefully_media_upload_receipts%rowtype;
    expected_handle text;
    x_copy text;
    attempt_id uuid;
    decision_now timestamptz := statement_timestamp();
begin
    if current_setting('request.jwt.claim.role', true) is distinct from 'service_role' then
        raise exception 'typefully_service_role_required' using errcode = '42501';
    end if;
    select candidate.* into item from public.content_items as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.id = target_content_item_id for update;
    if not found or item.client_id not in ('yellow', 'origintrail', 'squid', 'babylon')
       or item.status is distinct from 'approved'
       or item.content_kind is distinct from 'daily_news'
       or item.current_version_id is distinct from target_content_version_id then
        raise exception 'typefully_current_approval_required' using errcode = '23514';
    end if;
    if exists (select 1 from private.typefully_draft_attempts as prior
        where prior.workspace_id = target_workspace_id
          and prior.content_item_id = target_content_item_id) then
        raise exception 'typefully_attempt_already_reserved' using errcode = '23514';
    end if;
    perform 1 from public.workspace_clients as client
    where client.workspace_id = target_workspace_id
      and client.client_id = item.client_id and client.active is true for share;
    if not found then
        raise exception 'typefully_client_inactive' using errcode = '23514';
    end if;
    select candidate.* into version from public.content_versions as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.content_item_id = item.id
      and candidate.id = target_content_version_id for share;
    if not found or version.generation_meta -> 'mock_mode' is distinct from 'false'::jsonb
       or not private.has_valid_double_fact_check_report(version.generation_meta) then
        raise exception 'typefully_version_invalid' using errcode = '23514';
    end if;
    select candidate.* into approval from public.approvals as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.content_item_id = item.id
    order by candidate.review_sequence desc limit 1 for share;
    if not found or approval.id is distinct from target_approval_id
       or approval.content_version_id is distinct from version.id
       or approval.decision is distinct from 'approved'
       or approval.reviewer_source is distinct from 'studio_session'
       or approval.fact_check_policy_version is distinct from 'double-fact-check@1'
       or approval.source_facts_verified is not true
       or approval.output_claims_verified is not true then
        raise exception 'typefully_latest_approval_mismatch' using errcode = '23514';
    end if;
    if exists (select 1 from public.publications as publication
        where publication.workspace_id = target_workspace_id
          and publication.content_item_id = item.id) then
        raise exception 'typefully_existing_publication' using errcode = '23514';
    end if;
    select candidate.* into receipt from private.typefully_media_upload_receipts as candidate
    where candidate.id = target_media_receipt_id for share;
    if not found or receipt.workspace_id is distinct from target_workspace_id
       or receipt.client_id is distinct from item.client_id
       or receipt.content_item_id is distinct from item.id
       or receipt.content_version_id is distinct from version.id
       or receipt.social_set_id is distinct from expected_social_set_id
       or receipt.uploaded_bytes_sha256 is distinct from receipt.asset_sha256 then
        raise exception 'typefully_media_owner_mismatch' using errcode = '23514';
    end if;
    perform 1 from public.assets as banner
    join storage.objects as stored
      on stored.bucket_id = banner.storage_bucket and stored.name = banner.storage_path
    where banner.id = receipt.asset_id
      and banner.workspace_id = target_workspace_id
      and banner.content_item_id = item.id
      and banner.content_version_id = version.id
      and banner.id::text = version.deliverables ->> 'primary_asset_id'
      and banner.asset_kind = 'png' and banner.mime_type = 'image/png'
      and banner.storage_bucket = 'content-studio'
      and banner.metadata ->> 'filename' = 'news-card.png'
      and banner.storage_path = target_workspace_id::text || '/' || item.client_id
            || '/' || banner.id::text || '/news-card.png'
      and banner.sha256 = receipt.asset_sha256
      and banner.byte_size > 0
    for share of banner, stored;
    if not found then
        raise exception 'typefully_canonical_png_mismatch' using errcode = '23514';
    end if;
    perform 1 from public.content_source_links as link
    join public.source_items as source
      on source.workspace_id = link.workspace_id
     and source.client_id = link.client_id
     and source.id = link.source_item_id
    where link.workspace_id = target_workspace_id
      and link.client_id = item.client_id
      and link.content_item_id = item.id
      and link.position = 0 and source.source_type = 'tweet'
      and source.published_at <= version.created_at
      and source.canonical_url ~ ('^https://x\.com/' || case item.client_id
        when 'yellow' then 'Yellow'
        when 'origintrail' then 'origin_trail'
        when 'squid' then 'SquidRouter'
        when 'babylon' then 'babylonlabs_io' end || '/status/[1-9][0-9]{0,18}$')
    for share of link, source;
    if not found or (select count(*) from public.content_source_links as link
        where link.workspace_id = target_workspace_id
          and link.content_item_id = item.id and link.position = 0) <> 1 then
        raise exception 'typefully_primary_source_invalid' using errcode = '23514';
    end if;
    expected_handle := case item.client_id
        when 'yellow' then 'yellow__korea'
        when 'origintrail' then 'origin_trail_kr'
        when 'squid' then 'squidkorea'
        when 'babylon' then 'babylonkorean' end;
    if observed_x_username is distinct from expected_handle
       or observed_media_status is distinct from 'ready'
       or account_observed_at is null or media_observed_at is null
       or account_observed_at > decision_now or media_observed_at > decision_now
       or account_observed_at < decision_now - interval '15 minutes'
       or media_observed_at < decision_now - interval '15 minutes'
       or media_observed_at < version.created_at then
        raise exception 'typefully_live_readback_required' using errcode = '23514';
    end if;
    x_copy := version.channel_copy ->> 'x';
    if jsonb_typeof(version.channel_copy -> 'x') is distinct from 'string'
       or x_copy is null or btrim(x_copy) = '' or char_length(x_copy) > 280 then
        raise exception 'typefully_exact_x_copy_invalid' using errcode = '23514';
    end if;
    insert into private.typefully_draft_attempts (
        workspace_id, client_id, content_item_id, content_version_id,
        approval_id, media_receipt_id, social_set_id
    ) values (
        target_workspace_id, item.client_id, item.id, version.id,
        approval.id, receipt.id, receipt.social_set_id
    ) returning id into attempt_id;
    return jsonb_build_object(
        'attempt_id', attempt_id, 'content_version_id', version.id,
        'approval_id', approval.id, 'social_set_id', receipt.social_set_id,
        'request_body', jsonb_build_object(
            'platforms', jsonb_build_object('x', jsonb_build_object(
                'enabled', true, 'posts', jsonb_build_array(jsonb_build_object(
                    'text', x_copy, 'media_ids', jsonb_build_array(receipt.media_id)
                )))),
            'draft_title', 'CoinEasy ' || item.client_id || ' ' || version.id::text,
            'publish_at', null
        ),
        'status', 'delivery_unknown'
    );
end;
$$;

-- Only a verified 201 draft response with matching social set can confirm.
-- No function transitions an unknown attempt back to sendable.
create function private.confirm_typefully_draft_once(
    target_attempt_id uuid, observed_social_set_id bigint,
    observed_provider_draft_id bigint, observed_status text
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    attempt private.typefully_draft_attempts%rowtype;
begin
    if current_setting('request.jwt.claim.role', true) is distinct from 'service_role' then
        raise exception 'typefully_service_role_required' using errcode = '42501';
    end if;
    select candidate.* into attempt from private.typefully_draft_attempts as candidate
    where candidate.id = target_attempt_id for update;
    if not found or observed_social_set_id is distinct from attempt.social_set_id
       or observed_provider_draft_id is null or observed_provider_draft_id <= 0
       or observed_status is distinct from 'draft' then
        raise exception 'typefully_draft_receipt_invalid' using errcode = '23514';
    end if;
    if attempt.status = 'draft_created' then
        if attempt.provider_draft_id is distinct from observed_provider_draft_id then
            raise exception 'typefully_draft_receipt_invalid' using errcode = '23514';
        end if;
        return jsonb_build_object('attempt_id', attempt.id,
            'status', 'draft_created', 'provider_draft_id', attempt.provider_draft_id,
            'social_set_id', attempt.social_set_id, 'reused', true);
    end if;
    update private.typefully_draft_attempts
    set status = 'draft_created', provider_draft_id = observed_provider_draft_id,
        confirmed_at = statement_timestamp()
    where id = attempt.id;
    return jsonb_build_object('attempt_id', attempt.id, 'status', 'draft_created',
        'provider_draft_id', observed_provider_draft_id,
        'social_set_id', attempt.social_set_id, 'reused', false);
end;
$$;

-- PostgREST exposes public RPCs, not the private schema. These wrappers expose
-- only the three bounded operations; the ledger tables stay unreadable.
create function public.record_typefully_media_upload(
    target_workspace_id uuid, target_content_item_id uuid,
    target_content_version_id uuid, target_asset_id uuid,
    target_uploaded_bytes_sha256 text, target_social_set_id bigint,
    target_media_id uuid
)
returns uuid
language sql security definer set search_path = ''
as $$
    select private.record_typefully_media_upload(
        target_workspace_id, target_content_item_id, target_content_version_id,
        target_asset_id, target_uploaded_bytes_sha256, target_social_set_id,
        target_media_id)
$$;

create function public.reserve_typefully_draft_once(
    target_workspace_id uuid, target_content_item_id uuid,
    target_content_version_id uuid, target_approval_id uuid,
    target_media_receipt_id uuid, expected_social_set_id bigint,
    observed_x_username text, account_observed_at timestamptz,
    media_observed_at timestamptz, observed_media_status text
)
returns jsonb
language sql security definer set search_path = ''
as $$
    select private.reserve_typefully_draft_once(
        target_workspace_id, target_content_item_id, target_content_version_id,
        target_approval_id, target_media_receipt_id, expected_social_set_id,
        observed_x_username, account_observed_at, media_observed_at,
        observed_media_status)
$$;

create function public.confirm_typefully_draft_once(
    target_attempt_id uuid, observed_social_set_id bigint,
    observed_provider_draft_id bigint, observed_status text
)
returns jsonb
language sql security definer set search_path = ''
as $$
    select private.confirm_typefully_draft_once(
        target_attempt_id, observed_social_set_id,
        observed_provider_draft_id, observed_status)
$$;

-- A lost reservation response is reconciled by exact item ID. This read never
-- authorizes another POST and does not expose copy, media URLs, or credentials.
create function public.get_typefully_draft_attempt(
    target_workspace_id uuid, target_content_item_id uuid
)
returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
    attempt private.typefully_draft_attempts%rowtype;
begin
    if current_setting('request.jwt.claim.role', true) is distinct from 'service_role' then
        raise exception 'typefully_service_role_required' using errcode = '42501';
    end if;
    select candidate.* into attempt from private.typefully_draft_attempts as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.content_item_id = target_content_item_id;
    if not found then return null; end if;
    return jsonb_build_object(
        'attempt_id', attempt.id, 'content_version_id', attempt.content_version_id,
        'approval_id', attempt.approval_id, 'media_receipt_id', attempt.media_receipt_id,
        'social_set_id', attempt.social_set_id, 'status', attempt.status,
        'provider_draft_id', attempt.provider_draft_id,
        'reserved_at', attempt.reserved_at, 'confirmed_at', attempt.confirmed_at
    );
end;
$$;

revoke all on function private.record_typefully_media_upload(
    uuid,uuid,uuid,uuid,text,bigint,uuid) from public, anon, authenticated, service_role;
revoke all on function private.reserve_typefully_draft_once(
    uuid,uuid,uuid,uuid,uuid,bigint,text,timestamptz,timestamptz,text)
    from public, anon, authenticated, service_role;
revoke all on function private.confirm_typefully_draft_once(uuid,bigint,bigint,text)
    from public, anon, authenticated, service_role;
grant execute on function private.record_typefully_media_upload(
    uuid,uuid,uuid,uuid,text,bigint,uuid) to service_role;
grant execute on function private.reserve_typefully_draft_once(
    uuid,uuid,uuid,uuid,uuid,bigint,text,timestamptz,timestamptz,text) to service_role;
grant execute on function private.confirm_typefully_draft_once(uuid,bigint,bigint,text)
    to service_role;

revoke all on function public.record_typefully_media_upload(
    uuid,uuid,uuid,uuid,text,bigint,uuid) from public, anon, authenticated, service_role;
revoke all on function public.reserve_typefully_draft_once(
    uuid,uuid,uuid,uuid,uuid,bigint,text,timestamptz,timestamptz,text)
    from public, anon, authenticated, service_role;
revoke all on function public.confirm_typefully_draft_once(uuid,bigint,bigint,text)
    from public, anon, authenticated, service_role;
revoke all on function public.get_typefully_draft_attempt(uuid,uuid)
    from public, anon, authenticated, service_role;
grant execute on function public.record_typefully_media_upload(
    uuid,uuid,uuid,uuid,text,bigint,uuid) to service_role;
grant execute on function public.reserve_typefully_draft_once(
    uuid,uuid,uuid,uuid,uuid,bigint,text,timestamptz,timestamptz,text) to service_role;
grant execute on function public.confirm_typefully_draft_once(uuid,bigint,bigint,text)
    to service_role;
grant execute on function public.get_typefully_draft_attempt(uuid,uuid)
    to service_role;

commit;
