-- LOCAL PROPOSAL ONLY: not a production migration, grant, sender or approval.
-- Apply only after hosted-version compatibility, ACL and live candidate checks.
-- Requires content_ops_button_review_state, content_ops_button_durable_attempt,
-- and the deployed private.content_ops_review_candidate function.
begin;

create table private.content_ops_button_card_send_attempts (
    review_id uuid not null references private.content_ops_button_reviews(id),
    card_id uuid not null,
    part_index smallint not null check (part_index between 0 and 3),
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    state text not null default 'reserved' check (state in ('reserved','confirmed')),
    message_binding text check (message_binding ~ '^[a-f0-9]{64}$'),
    response_sha256 text check (response_sha256 ~ '^[a-f0-9]{64}$'),
    reserved_at timestamptz not null default clock_timestamp(),
    confirmed_at timestamptz,
    primary key (review_id, part_index),
    unique (card_id, part_index),
    unique (message_binding),
    check ((state = 'reserved' and message_binding is null and response_sha256 is null
            and confirmed_at is null)
        or (state = 'confirmed' and message_binding is not null
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
       and (to_jsonb(old) - 'state' - 'message_binding' - 'response_sha256' - 'confirmed_at')
           = (to_jsonb(new) - 'state' - 'message_binding' - 'response_sha256' - 'confirmed_at')
       and new.message_binding is not null and new.response_sha256 is not null
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
    expected_payload_sha256 text, verified_message_binding text,
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
       or verified_message_binding is null or verified_message_binding !~ '^[a-f0-9]{64}$'
       or verified_response_sha256 is null or verified_response_sha256 !~ '^[a-f0-9]{64}$'
       or observed_at is null or not isfinite(observed_at) then
        raise exception 'button_card_send_arguments_invalid' using errcode = '22023';
    end if;
    select * into initial from private.content_ops_button_reviews
        where id = target_review_id;
    if not found then raise exception 'button_card_review_unknown' using errcode = 'P0002'; end if;
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
        if a.message_binding is distinct from verified_message_binding
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
        set state = 'confirmed', message_binding = verified_message_binding,
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
    end loop;
    return private.record_content_ops_button_card(target_review_id, target_card_id,
        expected_fingerprint, target_epoch, target_bindings, target_parts,
        delivered, expires);
end $$;

revoke all on function private.guard_content_ops_button_card_send_attempt(),
    private.reserve_content_ops_button_card_send(uuid,uuid,smallint,text),
    private.confirm_content_ops_button_card_send(uuid,uuid,smallint,text,text,text,timestamptz),
    private.register_content_ops_button_card_from_sends(
        uuid,uuid,text,bigint,jsonb,jsonb,text,jsonb,timestamptz,timestamptz)
    from public, anon, authenticated, service_role;
commit;
