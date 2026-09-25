-- LOCAL PROPOSAL ONLY: not a production migration, grant, sender or approval.
-- Apply only after hosted-version compatibility, ACL and live candidate checks.
-- Requires content_ops_button_review_state, content_ops_button_durable_attempt,
-- and the deployed private.content_ops_review_candidate function.
begin;

-- A claimed existing outbox is the only source of a new button review row.
-- This prepares state; it does not begin delivery or authorize a send.
create function private.prepare_content_ops_button_review_from_claim(
    target_workspace_id uuid, target_outbox_id uuid, target_claim_token uuid,
    target_content_version_id uuid, target_review_id uuid
) returns jsonb language plpgsql volatile security invoker set search_path = '' as $$
declare
    q private.content_ops_review_outbox;
    candidate jsonb;
    review private.content_ops_button_reviews;
    fingerprint text;
begin
    if target_workspace_id is null or target_outbox_id is null
       or target_claim_token is null or target_content_version_id is null
       or target_review_id is null
       or target_review_id = '00000000-0000-0000-0000-000000000000'::uuid then
        raise exception 'button_review_claim_arguments_invalid' using errcode = '22023';
    end if;
    select * into q from private.content_ops_review_outbox
        where workspace_id = target_workspace_id and outbox_id = target_outbox_id
        for update;
    if not found or q.status is distinct from 'claimed'
       or q.claim_token is distinct from target_claim_token
       or q.content_version_id is distinct from target_content_version_id
       or q.lease_expires_at <= clock_timestamp()
       or q.send_started_at is not null or q.packet_sha256 is not null
       or q.finished_at is not null then
        raise exception 'button_review_claim_not_owned' using errcode = '23514';
    end if;
    candidate := private.content_ops_review_candidate(q.workspace_id,
        q.content_item_id, q.content_version_id);
    if private.content_ops_review_matches(candidate, q) is not true then
        raise exception 'button_review_claim_ineligible' using errcode = '23514';
    end if;
    fingerprint := private.content_ops_button_version_fingerprint(
        q.workspace_id, q.content_item_id, q.content_version_id);
    if fingerprint is null or fingerprint !~ '^[a-f0-9]{64}$' then
        raise exception 'button_review_claim_ineligible' using errcode = '23514';
    end if;
    insert into private.content_ops_button_reviews
        (id,workspace_id,client_id,content_item_id,content_version_id,version_fingerprint)
        values (target_review_id,q.workspace_id,q.client_id,q.content_item_id,
            q.content_version_id,fingerprint)
        returning * into review;
    return jsonb_build_object('status','review_prepared','review_id',review.id,
        'version_fingerprint',review.version_fingerprint,
        'epoch',review.epoch,'state',review.state,
        'expires_at',review.expires_at,'execution_authorized',false);
end $$;

-- Service-role-only locator for a claimed, exact-version private PNG. This
-- returns no signed URL or bytes; the gateway checks the direct Storage GET
-- against this immutable asset row. No delivery/approval state is changed.
create function public.content_ops_button_card_image_locator(
    target_workspace_id uuid, target_outbox_id uuid, target_claim_token uuid,
    target_content_version_id uuid
) returns jsonb language plpgsql volatile security definer set search_path = '' as $$
declare
    q private.content_ops_review_outbox;
    candidate jsonb;
    banner public.assets;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'button_card_image_service_role_required' using errcode = '42501';
    end if;
    if target_workspace_id is null or target_outbox_id is null
       or target_claim_token is null or target_content_version_id is null then
        raise exception 'button_card_image_arguments_invalid' using errcode = '22023';
    end if;
    select * into q from private.content_ops_review_outbox
        where workspace_id = target_workspace_id and outbox_id = target_outbox_id
        for share;
    if not found or q.status is distinct from 'claimed'
       or q.claim_token is distinct from target_claim_token
       or q.content_version_id is distinct from target_content_version_id
       or q.lease_expires_at <= clock_timestamp()
       or q.packet_sha256 is not null or q.send_started_at is not null
       or q.finished_at is not null then
        return null;
    end if;
    candidate := private.content_ops_review_candidate(q.workspace_id,
        q.content_item_id, q.content_version_id);
    if private.content_ops_review_matches(candidate, q) is not true then
        return null;
    end if;
    select asset.* into banner from public.assets as asset
        join storage.objects as stored on stored.bucket_id = asset.storage_bucket
            and stored.name = asset.storage_path
        where asset.id = q.banner_asset_id
          and asset.workspace_id = q.workspace_id
          and asset.content_item_id = q.content_item_id
          and asset.content_version_id = q.content_version_id
          and asset.asset_kind = 'png' and asset.mime_type = 'image/png'
          and asset.storage_bucket = 'content-studio'
          and asset.metadata ->> 'filename' = 'news-card.png'
          and asset.storage_path = q.workspace_id::text || '/' || q.client_id
                || '/' || asset.id::text || '/news-card.png'
          and asset.sha256 = q.banner_sha256
          and asset.byte_size between 9 and 10000000
          and asset.width > 0 and asset.height > 0
        for share of asset, stored;
    if not found then return null; end if;
    return jsonb_build_object('status','ready','outbox_id',q.outbox_id,
        'client_id',q.client_id,'content_item_id',q.content_item_id,
        'content_version_id',q.content_version_id,'asset_id',banner.id,
        'bucket',banner.storage_bucket,'path',banner.storage_path,
        'sha256',banner.sha256,'byte_size',banner.byte_size,
        'execution_authorized',false);
end $$;
revoke all on function public.content_ops_button_card_image_locator(uuid,uuid,uuid,uuid)
    from public, anon, authenticated, service_role;
grant execute on function public.content_ops_button_card_image_locator(uuid,uuid,uuid,uuid)
    to service_role;

-- The existing review outbox, not this ledger, owns the room delivery. A
-- button review cannot reserve a provider call until its exact outbox has
-- been claimed and begun once by the same courier. The old link-card worker
-- cannot claim or begin that outbox a second time.
create table private.content_ops_button_card_outbox_owners (
    review_id uuid primary key references private.content_ops_button_reviews(id),
    outbox_id uuid not null unique references private.content_ops_review_outbox(outbox_id),
    claim_token uuid not null unique,
    packet_sha256 text not null check (packet_sha256 ~ '^[a-f0-9]{64}$'),
    bound_at timestamptz not null default clock_timestamp()
);
alter table private.content_ops_button_card_outbox_owners enable row level security;
alter table private.content_ops_button_card_outbox_owners force row level security;
revoke all on private.content_ops_button_card_outbox_owners
    from public, anon, authenticated, service_role;

create function private.bind_content_ops_button_card_outbox(
    target_review_id uuid, target_outbox_id uuid, target_claim_token uuid,
    expected_packet_sha256 text
) returns jsonb language plpgsql volatile security invoker set search_path = '' as $$
declare
    q private.content_ops_review_outbox;
    r private.content_ops_button_reviews;
begin
    if target_review_id is null or target_outbox_id is null or target_claim_token is null
       or expected_packet_sha256 is null
       or expected_packet_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'button_card_outbox_arguments_invalid' using errcode = '22023';
    end if;
    -- Match the old begin/finish lock order: outbox before item/review.
    select * into q from private.content_ops_review_outbox
        where outbox_id = target_outbox_id for update;
    select * into r from private.content_ops_button_reviews
        where id = target_review_id for update;
    if not found or q.status is distinct from 'sending'
       or q.claim_token is distinct from target_claim_token
       or q.packet_sha256 is distinct from expected_packet_sha256
       or q.lease_expires_at <= clock_timestamp()
       or q.finished_at is not null or q.message_id is not null
       or q.workspace_id is distinct from r.workspace_id
       or q.client_id is distinct from r.client_id
       or q.content_item_id is distinct from r.content_item_id
       or q.content_version_id is distinct from r.content_version_id
       or r.state is distinct from 'active' or r.epoch is distinct from 0
       or r.expires_at <= clock_timestamp() then
        raise exception 'button_card_outbox_not_owned' using errcode = '23514';
    end if;
    insert into private.content_ops_button_card_outbox_owners
        (review_id,outbox_id,claim_token,packet_sha256)
        values (target_review_id,target_outbox_id,target_claim_token,expected_packet_sha256);
    return jsonb_build_object('status','bound','execution_authorized',false);
end $$;

create function private.content_ops_button_card_outbox_owned(target_review_id uuid)
returns boolean language plpgsql volatile security invoker set search_path = '' as $$
declare
    b private.content_ops_button_card_outbox_owners;
    q private.content_ops_review_outbox;
    r private.content_ops_button_reviews;
begin
    select * into b from private.content_ops_button_card_outbox_owners
        where review_id = target_review_id;
    if not found then return false; end if;
    select * into q from private.content_ops_review_outbox
        where outbox_id = b.outbox_id for update;
    select * into r from private.content_ops_button_reviews
        where id = target_review_id;
    return q.status = 'sending' and q.claim_token = b.claim_token
        and q.packet_sha256 = b.packet_sha256
        and q.lease_expires_at > clock_timestamp()
        and q.finished_at is null and q.message_id is null
        and q.workspace_id = r.workspace_id and q.client_id = r.client_id
        and q.content_item_id = r.content_item_id
        and q.content_version_id = r.content_version_id;
end $$;

create table private.content_ops_button_card_send_attempts (
    review_id uuid not null references private.content_ops_button_reviews(id),
    card_id uuid not null,
    part_index smallint not null check (part_index between 0 and 3),
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    state text not null default 'reserved' check (state in ('reserved','confirmed')),
    message_id bigint check (message_id between 1 and 9007199254740991),
    message_binding text check (message_binding ~ '^[a-f0-9]{64}$'),
    response_sha256 text check (response_sha256 ~ '^[a-f0-9]{64}$'),
    reserved_at timestamptz not null default clock_timestamp(),
    confirmed_at timestamptz,
    primary key (review_id, part_index),
    unique (card_id, part_index),
    unique (message_binding),
    check ((state = 'reserved' and message_id is null
            and message_binding is null and response_sha256 is null
            and confirmed_at is null)
        or (state = 'confirmed' and message_id is not null and message_binding is not null
            and response_sha256 is not null and confirmed_at is not null
            and confirmed_at >= reserved_at))
);
create index content_ops_button_card_send_card_idx
    on private.content_ops_button_card_send_attempts (card_id, part_index);
alter table private.content_ops_button_card_send_attempts enable row level security;
alter table private.content_ops_button_card_send_attempts force row level security;
revoke all on private.content_ops_button_card_send_attempts
    from public, anon, authenticated, service_role;

create function private.guard_content_ops_button_card_send_attempt()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
    if tg_op = 'UPDATE' and old.state = 'reserved' and new.state = 'confirmed'
       and (to_jsonb(old) - 'state' - 'message_id' - 'message_binding' - 'response_sha256' - 'confirmed_at')
           = (to_jsonb(new) - 'state' - 'message_id' - 'message_binding' - 'response_sha256' - 'confirmed_at')
       and new.message_id is not null and new.message_binding is not null
       and new.response_sha256 is not null
       and new.confirmed_at >= old.reserved_at then
        return new;
    end if;
    raise exception 'button_card_send_attempt_immutable' using errcode = '23514';
end $$;
create trigger content_ops_button_card_send_attempt_immutable
    before update or delete on private.content_ops_button_card_send_attempts
    for each row execute function private.guard_content_ops_button_card_send_attempt();

-- A committed NEW result is the only permission to make one provider call.
-- Reused, conflicting, lost-ACK and unknown reservations never grant a retry.
create function private.reserve_content_ops_button_card_send(
    target_review_id uuid, target_card_id uuid, target_part_index smallint,
    expected_payload_sha256 text
) returns jsonb language plpgsql volatile security invoker set search_path = '' as $$
declare
    initial private.content_ops_button_reviews;
    r private.content_ops_button_reviews;
    previous private.content_ops_button_card_send_attempts;
    candidate jsonb;
    decision_now timestamptz;
begin
    if target_review_id is null or target_card_id is null
       or target_card_id = '00000000-0000-0000-0000-000000000000'::uuid
       or target_part_index is null or target_part_index not between 0 and 3
       or expected_payload_sha256 is null
       or expected_payload_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'button_card_send_arguments_invalid' using errcode = '22023';
    end if;
    select * into initial from private.content_ops_button_reviews
        where id = target_review_id;
    if not found then raise exception 'button_card_review_unknown' using errcode = 'P0002'; end if;
    if private.content_ops_button_card_outbox_owned(target_review_id) is not true then
        raise exception 'button_card_outbox_not_owned' using errcode = '23514';
    end if;
    -- The candidate function locks item/feed/source and checks natural producer,
    -- latest official X source, fresh poll, canonical PNG and zero publication.
    candidate := private.content_ops_review_candidate(initial.workspace_id,
        initial.content_item_id, initial.content_version_id);
    if candidate is null then
        raise exception 'button_card_send_ineligible' using errcode = '23514';
    end if;
    select * into r from private.content_ops_button_reviews
        where id = target_review_id for update;
    decision_now := clock_timestamp();
    if r.workspace_id is distinct from initial.workspace_id
       or r.content_item_id is distinct from initial.content_item_id
       or r.content_version_id is distinct from initial.content_version_id
       or r.client_id is distinct from candidate->>'client_id'
       or r.state is distinct from 'active' or r.epoch is distinct from 0
       or r.expires_at <= decision_now
       or r.version_fingerprint is distinct from
           private.content_ops_button_version_fingerprint(
               r.workspace_id, r.content_item_id, r.content_version_id)
       or exists (select 1 from private.content_ops_button_cards c
           where c.review_id = r.id)
       or exists (select 1 from private.content_ops_button_card_send_attempts a
           where a.review_id = r.id and a.card_id <> target_card_id) then
        raise exception 'button_card_send_ineligible' using errcode = '23514';
    end if;
    select * into previous from private.content_ops_button_card_send_attempts
        where review_id = r.id and part_index = target_part_index for update;
    if found then
        if previous.card_id is distinct from target_card_id
           or previous.payload_sha256 is distinct from expected_payload_sha256 then
            raise exception 'button_card_send_conflict' using errcode = '23505';
        end if;
        return jsonb_build_object('status', previous.state, 'new_attempt', false,
            'execution_authorized', false);
    end if;
    if target_part_index > 0 and not exists (
        select 1 from private.content_ops_button_card_send_attempts a
        where a.review_id = r.id and a.card_id = target_card_id
          and a.part_index = target_part_index - 1 and a.state = 'confirmed'
    ) then
        raise exception 'button_card_send_order_invalid' using errcode = '23514';
    end if;
    insert into private.content_ops_button_card_send_attempts
        (review_id, card_id, part_index, payload_sha256)
        values (r.id, target_card_id, target_part_index, expected_payload_sha256);
    return jsonb_build_object('status', 'reserved', 'new_attempt', true,
        'execution_authorized', false);
end $$;

create function private.confirm_content_ops_button_card_send(
    target_review_id uuid, target_card_id uuid, target_part_index smallint,
    expected_payload_sha256 text, verified_message_id bigint,
    verified_message_binding text,
    verified_response_sha256 text, observed_at timestamptz
) returns jsonb language plpgsql volatile security invoker set search_path = '' as $$
declare
    initial private.content_ops_button_reviews;
    r private.content_ops_button_reviews;
    a private.content_ops_button_card_send_attempts;
    candidate jsonb;
    decision_now timestamptz;
begin
    if target_review_id is null or target_card_id is null
       or target_part_index is null or target_part_index not between 0 and 3
       or expected_payload_sha256 is null or expected_payload_sha256 !~ '^[a-f0-9]{64}$'
       or verified_message_id is null
       or verified_message_id not between 1 and 9007199254740991
       or verified_message_binding is null or verified_message_binding !~ '^[a-f0-9]{64}$'
       or verified_response_sha256 is null or verified_response_sha256 !~ '^[a-f0-9]{64}$'
       or observed_at is null or not isfinite(observed_at) then
        raise exception 'button_card_send_arguments_invalid' using errcode = '22023';
    end if;
    select * into initial from private.content_ops_button_reviews
        where id = target_review_id;
    if not found then raise exception 'button_card_review_unknown' using errcode = 'P0002'; end if;
    if private.content_ops_button_card_outbox_owned(target_review_id) is not true then
        raise exception 'button_card_outbox_not_owned' using errcode = '23514';
    end if;
    candidate := private.content_ops_review_candidate(initial.workspace_id,
        initial.content_item_id, initial.content_version_id);
    if candidate is null then
        raise exception 'button_card_send_ineligible' using errcode = '23514';
    end if;
    select * into r from private.content_ops_button_reviews
        where id = target_review_id for update;
    select * into a from private.content_ops_button_card_send_attempts
        where review_id = r.id and part_index = target_part_index for update;
    decision_now := clock_timestamp();
    if not found or r.workspace_id is distinct from initial.workspace_id
       or r.content_item_id is distinct from initial.content_item_id
       or r.content_version_id is distinct from initial.content_version_id
       or r.client_id is distinct from candidate->>'client_id'
       or r.state is distinct from 'active' or r.epoch is distinct from 0
       or r.expires_at <= decision_now
       or r.version_fingerprint is distinct from
           private.content_ops_button_version_fingerprint(
               r.workspace_id, r.content_item_id, r.content_version_id)
       or a.card_id is distinct from target_card_id
       or a.payload_sha256 is distinct from expected_payload_sha256
       or observed_at < a.reserved_at or observed_at > decision_now
       or decision_now - observed_at > interval '60 seconds' then
        raise exception 'button_card_send_ineligible' using errcode = '23514';
    end if;
    if a.state = 'confirmed' then
        if a.message_id is distinct from verified_message_id
           or a.message_binding is distinct from verified_message_binding
           or a.response_sha256 is distinct from verified_response_sha256 then
            raise exception 'button_card_send_conflict' using errcode = '23505';
        end if;
        return jsonb_build_object('status', 'confirmed', 'new_confirmation', false,
            'execution_authorized', false);
    end if;
    if a.state is distinct from 'reserved' then
        raise exception 'button_card_send_ineligible' using errcode = '23514';
    end if;
    update private.content_ops_button_card_send_attempts
        set state = 'confirmed', message_id = verified_message_id,
            message_binding = verified_message_binding,
            response_sha256 = verified_response_sha256,
            confirmed_at = decision_now
        where review_id = r.id and part_index = target_part_index;
    return jsonb_build_object('status', 'confirmed', 'new_confirmation', true,
        'execution_authorized', false);
end $$;

-- The fourth confirmed send is the controls card. No packet may be registered
-- from caller-provided hashes alone: every part must match this durable ledger.
create function private.register_content_ops_button_card_from_sends(
    target_review_id uuid, target_card_id uuid, expected_fingerprint text,
    target_epoch bigint, target_bindings jsonb, target_parts jsonb,
    controls_payload_sha256 text, target_response_sha256s jsonb,
    delivered timestamptz, expires timestamptz
) returns jsonb language plpgsql volatile security invoker set search_path = '' as $$
declare
    initial private.content_ops_button_reviews;
    r private.content_ops_button_reviews;
    a private.content_ops_button_card_send_attempts;
    candidate jsonb;
    n integer;
    controls_message_id bigint;
    owner_outbox_id uuid;
    receipt jsonb;
    affected_rows integer;
begin
    if target_review_id is null or target_card_id is null
       or controls_payload_sha256 is null
       or controls_payload_sha256 !~ '^[a-f0-9]{64}$'
       or jsonb_typeof(target_parts) is distinct from 'array'
       or jsonb_array_length(target_parts) <> 3
       or jsonb_typeof(target_response_sha256s) is distinct from 'array'
       or jsonb_array_length(target_response_sha256s) <> 4
       or jsonb_typeof(target_bindings) is distinct from 'object' then
        raise exception 'button_card_send_arguments_invalid' using errcode = '22023';
    end if;
    select * into initial from private.content_ops_button_reviews
        where id = target_review_id;
    if not found then raise exception 'button_card_review_unknown' using errcode = 'P0002'; end if;
    if private.content_ops_button_card_outbox_owned(target_review_id) is not true then
        raise exception 'button_card_outbox_not_owned' using errcode = '23514';
    end if;
    candidate := private.content_ops_review_candidate(initial.workspace_id,
        initial.content_item_id, initial.content_version_id);
    if candidate is null then
        raise exception 'button_card_send_ineligible' using errcode = '23514';
    end if;
    select * into r from private.content_ops_button_reviews
        where id = target_review_id for update;
    if r.workspace_id is distinct from initial.workspace_id
       or r.content_item_id is distinct from initial.content_item_id
       or r.content_version_id is distinct from initial.content_version_id
       or r.client_id is distinct from candidate->>'client_id'
       or r.state is distinct from 'active' or r.epoch is distinct from target_epoch
       or r.expires_at <= clock_timestamp()
       or r.version_fingerprint is distinct from expected_fingerprint
       or r.version_fingerprint is distinct from
           private.content_ops_button_version_fingerprint(
               r.workspace_id, r.content_item_id, r.content_version_id) then
        raise exception 'button_card_send_ineligible' using errcode = '23514';
    end if;
    if (select count(*) from private.content_ops_button_card_send_attempts
        where review_id = r.id and card_id = target_card_id) <> 4 then
        raise exception 'button_card_send_incomplete' using errcode = '23514';
    end if;
    for n in 0..3 loop
        select * into a from private.content_ops_button_card_send_attempts
            where review_id = r.id and card_id = target_card_id
              and part_index = n for share;
        if not found or a.state is distinct from 'confirmed'
           or jsonb_typeof(target_response_sha256s->n) is distinct from 'string'
           or a.response_sha256 is distinct from target_response_sha256s->>n
           or (n < 3 and (a.payload_sha256 is distinct from
                   target_parts->n->>'payload_sha256'
               or a.message_binding is distinct from
                   target_parts->n->>'message_binding'))
           or (n = 3 and (a.payload_sha256 is distinct from controls_payload_sha256
               or a.message_binding is distinct from target_bindings->>'message')) then
            raise exception 'button_card_send_incomplete' using errcode = '23514';
        end if;
        if n = 3 then controls_message_id := a.message_id; end if;
    end loop;
    receipt := private.record_content_ops_button_card(target_review_id, target_card_id,
        expected_fingerprint, target_epoch, target_bindings, target_parts,
        delivered, expires);
    if receipt->>'status' is distinct from 'card_recorded'
       or receipt->'reused' is distinct from 'false'::jsonb
       or controls_message_id is null then
        raise exception 'button_card_send_incomplete' using errcode = '23514';
    end if;
    select outbox_id into owner_outbox_id
        from private.content_ops_button_card_outbox_owners
        where review_id = target_review_id;
    update private.content_ops_review_outbox
        set status = 'sent', message_id = controls_message_id,
            finished_at = clock_timestamp()
        where outbox_id = owner_outbox_id and status = 'sending'
          and lease_expires_at > clock_timestamp();
    get diagnostics affected_rows = row_count;
    if affected_rows <> 1 then
        raise exception 'button_card_outbox_not_owned' using errcode = '23514';
    end if;
    return receipt;
end $$;

-- Read-only reconciliation after a lost registration commit ACK. A positive
-- receipt proves the exact card and outbox committed together; it never
-- authorizes another provider call or registration retry.
create function private.read_content_ops_button_card_terminal(
    target_review_id uuid, target_card_id uuid, target_outbox_id uuid
) returns jsonb language plpgsql stable security invoker set search_path = '' as $$
declare
    b private.content_ops_button_card_outbox_owners;
    q private.content_ops_review_outbox;
    r private.content_ops_button_reviews;
    c private.content_ops_button_cards;
    controls private.content_ops_button_card_send_attempts;
begin
    if target_review_id is null or target_card_id is null or target_outbox_id is null then
        raise exception 'button_card_terminal_arguments_invalid' using errcode = '22023';
    end if;
    select * into b from private.content_ops_button_card_outbox_owners
        where review_id = target_review_id and outbox_id = target_outbox_id;
    if not found then
        return jsonb_build_object('status','not_confirmed','card_id',null,
            'outbox_id',null,'execution_authorized',false);
    end if;
    select * into q from private.content_ops_review_outbox
        where outbox_id = b.outbox_id;
    select * into r from private.content_ops_button_reviews
        where id = target_review_id;
    select * into c from private.content_ops_button_cards
        where id = target_card_id and review_id = target_review_id;
    select * into controls from private.content_ops_button_card_send_attempts
        where review_id = target_review_id and card_id = target_card_id
          and part_index = 3 and state = 'confirmed';
    if q.status is distinct from 'sent' or q.message_id is null
       or q.message_id is distinct from controls.message_id
       or q.claim_token is distinct from b.claim_token
       or q.packet_sha256 is distinct from b.packet_sha256
       or q.workspace_id is distinct from r.workspace_id
       or q.content_version_id is distinct from r.content_version_id
       or c.id is distinct from target_card_id
       or c.bindings->>'message' is distinct from controls.message_binding
       or (select count(*) from private.content_ops_button_card_send_attempts
           where review_id = target_review_id and card_id = target_card_id
             and state = 'confirmed') <> 4 then
        return jsonb_build_object('status','not_confirmed','card_id',null,
            'outbox_id',null,'execution_authorized',false);
    end if;
    return jsonb_build_object('status','sent','card_id',target_card_id,
        'outbox_id',target_outbox_id,'execution_authorized',false);
end $$;

revoke all on function private.prepare_content_ops_button_review_from_claim(uuid,uuid,uuid,uuid,uuid),
    private.bind_content_ops_button_card_outbox(uuid,uuid,uuid,text),
    private.content_ops_button_card_outbox_owned(uuid),
    private.guard_content_ops_button_card_send_attempt(),
    private.reserve_content_ops_button_card_send(uuid,uuid,smallint,text),
    private.confirm_content_ops_button_card_send(uuid,uuid,smallint,text,bigint,text,text,timestamptz),
    private.register_content_ops_button_card_from_sends(
        uuid,uuid,text,bigint,jsonb,jsonb,text,jsonb,timestamptz,timestamptz),
    private.read_content_ops_button_card_terminal(uuid,uuid,uuid)
    from public, anon, authenticated, service_role;
commit;
