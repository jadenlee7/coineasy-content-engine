-- LOCAL-ONLY, UNMOUNTED PROPOSAL. This records private final-card transport
-- attempts and bounded courier receipts. It does NOT send, approve or publish.
-- No runtime role can call these functions or read the tables until a separate
-- reviewed grant/deployment. A supplied receipt is not provider authentication.
begin;

create table private.content_ops_final_card_deliveries (
    id uuid primary key check (id <> '00000000-0000-0000-0000-000000000000'::uuid),
    review_id uuid not null references private.content_ops_button_reviews(id),
    parent_card_id uuid not null unique references private.content_ops_button_cards(id),
    actor_id uuid not null references private.content_ops_review_principals(id),
    epoch bigint not null check (epoch >= 0),
    version_fingerprint text not null check (version_fingerprint ~ '^[a-f0-9]{64}$'),
    snapshot_sha256 text not null check (snapshot_sha256 ~ '^[a-f0-9]{64}$'),
    packet_sha256 text not null check (packet_sha256 ~ '^[a-f0-9]{64}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    telegram_route_binding text not null check (telegram_route_binding ~ '^[a-f0-9]{64}$'),
    typefully_route_binding text not null check (typefully_route_binding ~ '^[a-f0-9]{64}$'),
    bot_binding text not null check (bot_binding ~ '^[a-f0-9]{64}$'),
    room_binding text not null check (room_binding ~ '^[a-f0-9]{64}$'),
    human_binding text not null check (human_binding ~ '^[a-f0-9]{64}$'),
    created_at timestamptz not null default clock_timestamp(),
    expires_at timestamptz not null,
    unique (review_id, epoch),
    check (expires_at > created_at and expires_at <= created_at + interval '15 minutes')
);

-- Part 0=image, 1=Telegram text, 2=X text, 3=final controls.
-- Writing `attempted` BEFORE an external call makes a lost acknowledgement
-- delivery_unknown, never permission to invoke Telegram a second time.
create table private.content_ops_final_card_parts (
    delivery_id uuid not null references private.content_ops_final_card_deliveries(id),
    part_index smallint not null check (part_index between 0 and 3),
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    state text not null default 'attempted' check (state in ('attempted','confirmed')),
    message_binding text unique check (message_binding ~ '^[a-f0-9]{64}$'),
    response_sha256 text check (response_sha256 ~ '^[a-f0-9]{64}$'),
    started_at timestamptz not null default clock_timestamp(),
    confirmed_at timestamptz,
    primary key (delivery_id, part_index),
    check ((state='confirmed') = (message_binding is not null)),
    check ((state='confirmed') = (response_sha256 is not null)),
    check ((state='confirmed') = (confirmed_at is not null)),
    check (confirmed_at is null or confirmed_at >= started_at)
);

-- A card is registered only after four different confirmed private messages.
-- This is not a final decision or a public-channel handoff.
create table private.content_ops_final_cards (
    id uuid primary key references private.content_ops_final_card_deliveries(id),
    review_id uuid not null references private.content_ops_button_reviews(id),
    parent_card_id uuid not null unique references private.content_ops_button_cards(id),
    actor_id uuid not null references private.content_ops_review_principals(id),
    epoch bigint not null,
    version_fingerprint text not null check (version_fingerprint ~ '^[a-f0-9]{64}$'),
    snapshot_sha256 text not null check (snapshot_sha256 ~ '^[a-f0-9]{64}$'),
    control_message_binding text not null unique check (control_message_binding ~ '^[a-f0-9]{64}$'),
    registered_at timestamptz not null default clock_timestamp(),
    expires_at timestamptz not null,
    unique (review_id, epoch),
    check (expires_at > registered_at)
);

alter table private.content_ops_final_card_deliveries enable row level security;
alter table private.content_ops_final_card_deliveries force row level security;
alter table private.content_ops_final_card_parts enable row level security;
alter table private.content_ops_final_card_parts force row level security;
alter table private.content_ops_final_cards enable row level security;
alter table private.content_ops_final_cards force row level security;
revoke all on private.content_ops_final_card_deliveries,
    private.content_ops_final_card_parts,private.content_ops_final_cards
    from public,anon,authenticated,service_role;

create function private.guard_content_ops_final_card_ledger()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    if tg_table_name='content_ops_final_card_parts' and tg_op='UPDATE'
       and old.state='attempted' and new.state='confirmed'
       and old.message_binding is null and old.response_sha256 is null
       and old.confirmed_at is null
       and (to_jsonb(old)-'state'-'message_binding'-'response_sha256'-'confirmed_at')
           = (to_jsonb(new)-'state'-'message_binding'-'response_sha256'-'confirmed_at') then
        return new;
    end if;
    raise exception 'final_card_ledger_immutable' using errcode='23514';
end $$;
create trigger final_card_deliveries_immutable before update or delete
    on private.content_ops_final_card_deliveries for each row
    execute function private.guard_content_ops_final_card_ledger();
create trigger final_card_parts_immutable before update or delete
    on private.content_ops_final_card_parts for each row
    execute function private.guard_content_ops_final_card_ledger();
create trigger final_cards_immutable before update or delete
    on private.content_ops_final_cards for each row
    execute function private.guard_content_ops_final_card_ledger();

create function private.reserve_content_ops_final_card_delivery(
    target_id uuid,target_review_id uuid,target_parent_card_id uuid,
    target_actor_id uuid,expected_fingerprint text,verified_bot_binding text,
    verified_room_binding text,verified_human_binding text,
    target_snapshot_sha256 text,target_packet_sha256 text,target_release_sha text,
    expected_telegram_binding text,expected_typefully_binding text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    r private.content_ops_button_reviews;
    parent private.content_ops_button_cards;
    preflight jsonb;
    observed timestamptz;
    expiry timestamptz;
begin
    if target_id is null or target_id='00000000-0000-0000-0000-000000000000'::uuid
       or target_snapshot_sha256 is null or target_snapshot_sha256 !~ '^[a-f0-9]{64}$'
       or target_packet_sha256 is null or target_packet_sha256 !~ '^[a-f0-9]{64}$'
       or target_release_sha is null or target_release_sha !~ '^[a-f0-9]{40}$' then
        raise exception 'final_card_delivery_arguments_invalid' using errcode='22023';
    end if;
    select * into r from private.content_ops_button_reviews where id=target_review_id;
    if not found then raise exception 'final_card_review_unknown' using errcode='P0002';end if;
    perform 1 from public.content_items where workspace_id=r.workspace_id
        and id=r.content_item_id for update;
    select * into r from private.content_ops_button_reviews where id=target_review_id for share;
    select * into parent from private.content_ops_button_cards
        where id=target_parent_card_id and review_id=r.id for share;
    if not found then raise exception 'final_card_parent_unknown' using errcode='P0002';end if;
    preflight:=private.content_ops_final_card_preflight(target_review_id,
        target_parent_card_id,target_actor_id,expected_fingerprint,
        verified_bot_binding,verified_room_binding,verified_human_binding);
    if preflight->>'status' is distinct from 'ready_for_final_card' then
        raise exception 'final_card_delivery_ineligible' using errcode='23514';end if;
    perform private.require_content_ops_publication_routes(r.workspace_id,r.client_id,
        target_release_sha,expected_telegram_binding,expected_typefully_binding);
    observed:=clock_timestamp();
    expiry:=least(parent.expires_at,r.expires_at,observed+interval '15 minutes');
    if expiry<=observed then raise exception 'final_card_delivery_expired' using errcode='23514';end if;
    insert into private.content_ops_final_card_deliveries(id,review_id,parent_card_id,
        actor_id,epoch,version_fingerprint,snapshot_sha256,packet_sha256,
        release_sha,telegram_route_binding,typefully_route_binding,
        bot_binding,room_binding,human_binding,expires_at)
    values(target_id,r.id,parent.id,target_actor_id,r.epoch,r.version_fingerprint,
        target_snapshot_sha256,target_packet_sha256,target_release_sha,
        expected_telegram_binding,expected_typefully_binding,
        verified_bot_binding,verified_room_binding,verified_human_binding,expiry);
    return jsonb_build_object('status','delivery_reserved','delivery_id',target_id,
        'expires_at',expiry,'execution_authorized',false);
end $$;

create function private.begin_content_ops_final_card_part(
    target_delivery_id uuid,target_part_index smallint,target_payload_sha256 text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    d private.content_ops_final_card_deliveries;
    prior private.content_ops_final_card_parts;
    preflight jsonb;
begin
    if target_delivery_id is null or target_part_index is null
       or target_part_index not between 0 and 3 or target_payload_sha256 is null
       or target_payload_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'final_card_part_arguments_invalid' using errcode='22023';end if;
    -- Observe identity first, then acquire the content-item lock before
    -- locking ledger rows, matching the existing edit/decision lock order.
    select * into d from private.content_ops_final_card_deliveries
        where id=target_delivery_id;
    if not found then raise exception 'final_card_delivery_unknown' using errcode='P0002';end if;
    perform 1 from public.content_items i join private.content_ops_button_reviews r
        on r.content_item_id=i.id and r.workspace_id=i.workspace_id
        where r.id=d.review_id for update of i;
    select * into d from private.content_ops_final_card_deliveries
        where id=target_delivery_id for share;
    select * into prior from private.content_ops_final_card_parts
        where delivery_id=d.id and part_index=target_part_index;
    if found then
        if prior.payload_sha256 is distinct from target_payload_sha256 then
            raise exception 'final_card_part_conflict' using errcode='23505';end if;
        return jsonb_build_object('status',case when prior.state='confirmed'
            then 'confirmed' else 'delivery_unknown' end,'reused',true,
            'execution_authorized',false);
    end if;
    if clock_timestamp()>=d.expires_at or exists(
        select 1 from private.content_ops_final_cards where id=d.id)
       or (select count(*) from private.content_ops_final_card_parts
           where delivery_id=d.id and part_index<target_part_index
             and state='confirmed')<>target_part_index
       or exists(select 1 from private.content_ops_final_card_parts
           where delivery_id=d.id and part_index>target_part_index) then
        raise exception 'final_card_part_ineligible' using errcode='23514';end if;
    preflight:=private.content_ops_final_card_preflight(d.review_id,
        d.parent_card_id,d.actor_id,d.version_fingerprint,d.bot_binding,
        d.room_binding,d.human_binding);
    if preflight->>'status' is distinct from 'ready_for_final_card' then
        raise exception 'final_card_part_ineligible' using errcode='23514';end if;
    perform private.require_content_ops_publication_routes(r.workspace_id,r.client_id,
        d.release_sha,d.telegram_route_binding,d.typefully_route_binding)
        from private.content_ops_button_reviews r where r.id=d.review_id;
    insert into private.content_ops_final_card_parts(delivery_id,part_index,payload_sha256)
        values(d.id,target_part_index,target_payload_sha256);
    return jsonb_build_object('status','attempt_recorded','reused',false,
        'execution_authorized',false);
end $$;

create function private.confirm_content_ops_final_card_part(
    target_delivery_id uuid,target_part_index smallint,target_payload_sha256 text,
    verified_message_binding text,verified_response_sha256 text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare p private.content_ops_final_card_parts;
begin
    if target_delivery_id is null or target_part_index is null
       or target_part_index not between 0 and 3 or target_payload_sha256 is null
       or target_payload_sha256 !~ '^[a-f0-9]{64}$'
       or verified_message_binding is null
       or verified_message_binding !~ '^[a-f0-9]{64}$'
       or verified_response_sha256 is null
       or verified_response_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'final_card_receipt_arguments_invalid' using errcode='22023';end if;
    select * into p from private.content_ops_final_card_parts
        where delivery_id=target_delivery_id and part_index=target_part_index for update;
    if not found then raise exception 'final_card_part_unattempted' using errcode='P0002';end if;
    if p.payload_sha256 is distinct from target_payload_sha256 then
        raise exception 'final_card_receipt_conflict' using errcode='23505';end if;
    if p.state='confirmed' then
        if p.message_binding is distinct from verified_message_binding
           or p.response_sha256 is distinct from verified_response_sha256 then
            raise exception 'final_card_receipt_conflict' using errcode='23505';end if;
        return jsonb_build_object('status','confirmed','reused',true,
            'execution_authorized',false);
    end if;
    update private.content_ops_final_card_parts set state='confirmed',
        message_binding=verified_message_binding,
        response_sha256=verified_response_sha256,confirmed_at=clock_timestamp()
        where delivery_id=target_delivery_id and part_index=target_part_index;
    return jsonb_build_object('status','confirmed','reused',false,
        'execution_authorized',false);
end $$;

create function private.register_content_ops_final_card(target_delivery_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare d private.content_ops_final_card_deliveries;
    c private.content_ops_final_cards;
    preflight jsonb;
    control_binding text;
begin
    select * into d from private.content_ops_final_card_deliveries
        where id=target_delivery_id;
    if not found then raise exception 'final_card_delivery_unknown' using errcode='P0002';end if;
    perform 1 from public.content_items i join private.content_ops_button_reviews r
        on r.content_item_id=i.id and r.workspace_id=i.workspace_id
        where r.id=d.review_id for update of i;
    select * into d from private.content_ops_final_card_deliveries
        where id=target_delivery_id for share;
    select * into c from private.content_ops_final_cards where id=d.id;
    if found then
        return jsonb_build_object('status','card_registered','card_id',c.id,
            'reused',true,'execution_authorized',false);
    end if;
    if clock_timestamp()>=d.expires_at or (
        select count(*) from private.content_ops_final_card_parts
        where delivery_id=d.id and state='confirmed')<>4 then
        raise exception 'final_card_receipts_incomplete' using errcode='23514';end if;
    preflight:=private.content_ops_final_card_preflight(d.review_id,
        d.parent_card_id,d.actor_id,d.version_fingerprint,d.bot_binding,
        d.room_binding,d.human_binding);
    if preflight->>'status' is distinct from 'ready_for_final_card'
       or (preflight->>'review_epoch')::bigint is distinct from d.epoch then
        raise exception 'final_card_registration_ineligible' using errcode='23514';end if;
    perform private.require_content_ops_publication_routes(r.workspace_id,r.client_id,
        d.release_sha,d.telegram_route_binding,d.typefully_route_binding)
        from private.content_ops_button_reviews r where r.id=d.review_id;
    select message_binding into control_binding
      from private.content_ops_final_card_parts
      where delivery_id=d.id and part_index=3 and state='confirmed';
    insert into private.content_ops_final_cards(id,review_id,parent_card_id,
        actor_id,epoch,version_fingerprint,snapshot_sha256,
        control_message_binding,expires_at)
    values(d.id,d.review_id,d.parent_card_id,d.actor_id,d.epoch,
        d.version_fingerprint,d.snapshot_sha256,control_binding,d.expires_at);
    return jsonb_build_object('status','card_registered','card_id',d.id,
        'reused',false,'execution_authorized',false);
end $$;

-- Read-only reconciliation for a lost registration acknowledgement. This is
-- evidence of private card registration, never permission to approve or send.
create function private.read_content_ops_final_card_terminal(target_delivery_id uuid)
returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare registered_id uuid;
begin
    if target_delivery_id is null or
       target_delivery_id='00000000-0000-0000-0000-000000000000'::uuid then
        raise exception 'final_card_terminal_arguments_invalid' using errcode='22023';
    end if;
    select id into registered_id from private.content_ops_final_cards
        where id=target_delivery_id;
    return jsonb_build_object(
        'status',case when registered_id is null then 'not_registered'
                      else 'card_registered' end,
        'card_id',registered_id,'execution_authorized',false);
end $$;

revoke all on function private.guard_content_ops_final_card_ledger()
    from public,anon,authenticated,service_role;
revoke all on function private.reserve_content_ops_final_card_delivery(
    uuid,uuid,uuid,uuid,text,text,text,text,text,text,text,text,text)
    from public,anon,authenticated,service_role;
revoke all on function private.begin_content_ops_final_card_part(uuid,smallint,text)
    from public,anon,authenticated,service_role;
revoke all on function private.confirm_content_ops_final_card_part(
    uuid,smallint,text,text,text)
    from public,anon,authenticated,service_role;
revoke all on function private.register_content_ops_final_card(uuid)
    from public,anon,authenticated,service_role;
revoke all on function private.read_content_ops_final_card_terminal(uuid)
    from public,anon,authenticated,service_role;
commit;
