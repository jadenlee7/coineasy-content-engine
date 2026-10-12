-- LOCAL PROPOSAL ONLY. No runtime grants, scheduler, approval or public send.
-- Requires final-card preflight, release-pair/routes and delivery-ledger proposals.
begin;
create table private.content_ops_final_card_preparations (
    id uuid primary key check(id<>'00000000-0000-0000-0000-000000000000'::uuid),
    review_id uuid not null references private.content_ops_button_reviews(id),
    parent_card_id uuid not null unique references private.content_ops_button_cards(id),
    actor_id uuid not null references private.content_ops_review_principals(id),
    epoch bigint not null check(epoch>=0),
    version_fingerprint text not null check(version_fingerprint ~ '^[a-f0-9]{64}$'),
    bot_binding text not null check(bot_binding ~ '^[a-f0-9]{64}$'),
    room_binding text not null check(room_binding ~ '^[a-f0-9]{64}$'),
    human_binding text not null check(human_binding ~ '^[a-f0-9]{64}$'),
    release_sha text not null, callback_release_sha text not null, release_pair_sha256 text not null,
    telegram_route_binding text not null check(telegram_route_binding ~ '^[a-f0-9]{64}$'),
    typefully_route_binding text not null check(typefully_route_binding ~ '^[a-f0-9]{64}$'),
    created_at timestamptz not null default clock_timestamp(),
    claimed_at timestamptz,
    unique(review_id,epoch),
    check(claimed_at is null or claimed_at>=created_at),
    foreign key(release_sha,callback_release_sha,release_pair_sha256)
      references private.content_ops_release_pairs(release_sha,callback_release_sha,release_pair_sha256)
);
alter table private.content_ops_final_card_preparations enable row level security;
alter table private.content_ops_final_card_preparations force row level security;
revoke all on private.content_ops_final_card_preparations from public,anon,authenticated,service_role;

create function private.guard_content_ops_final_card_preparation()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    if tg_op='UPDATE' and old.claimed_at is null and new.claimed_at is not null
       and (to_jsonb(old)-'claimed_at')=(to_jsonb(new)-'claimed_at') then return new; end if;
    raise exception 'final_card_preparation_immutable' using errcode='23514';
end $$;
create trigger final_card_preparation_immutable before update or delete
    on private.content_ops_final_card_preparations for each row
    execute function private.guard_content_ops_final_card_preparation();

-- Called inside the authenticated ce1 owner's SAME transaction, after recording
-- a source/claims check. The checks are human records, never agent approval.
create function private.request_content_ops_final_card_preparation(
    target_id uuid,target_review_id uuid,target_parent_card_id uuid,target_actor_id uuid,
    expected_fingerprint text,verified_bot_binding text,verified_room_binding text,
    verified_human_binding text,target_release_sha text,target_callback_release_sha text,
    target_release_pair_sha256 text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    r private.content_ops_button_reviews; prior private.content_ops_final_card_preparations;
    preflight jsonb; tg text; x text;
begin
    if target_id is null or target_id='00000000-0000-0000-0000-000000000000'::uuid then
        raise exception 'final_card_preparation_arguments_invalid' using errcode='22023'; end if;
    select * into r from private.content_ops_button_reviews where id=target_review_id;
    if not found then raise exception 'final_card_preparation_unknown' using errcode='P0002'; end if;
    perform 1 from public.content_items where id=r.content_item_id and workspace_id=r.workspace_id for update;
    select * into r from private.content_ops_button_reviews where id=target_review_id for share;
    preflight:=private.content_ops_final_card_preflight(r.id,target_parent_card_id,target_actor_id,
        expected_fingerprint,verified_bot_binding,verified_room_binding,verified_human_binding);
    if preflight->>'status' is distinct from 'ready_for_final_card' then
        return jsonb_build_object('status','not_ready','intent_id',null,'reused',false,'execution_authorized',false);
    end if;
    perform private.require_content_ops_release_pair(r.workspace_id,r.client_id,
        target_release_sha,target_callback_release_sha,target_release_pair_sha256);
    select route_binding into tg from private.content_ops_publication_routes
        where workspace_id=r.workspace_id and client_id=r.client_id and channel='telegram';
    select route_binding into x from private.content_ops_publication_routes
        where workspace_id=r.workspace_id and client_id=r.client_id and channel='typefully_x';
    perform private.require_content_ops_publication_routes(r.workspace_id,r.client_id,target_release_sha,tg,x);
    select * into prior from private.content_ops_final_card_preparations where review_id=r.id and epoch=r.epoch;
    if found then
        if (prior.parent_card_id,prior.actor_id,prior.version_fingerprint,prior.bot_binding,
            prior.room_binding,prior.human_binding,prior.release_sha,prior.callback_release_sha,
            prior.release_pair_sha256,prior.telegram_route_binding,prior.typefully_route_binding)
          is distinct from (target_parent_card_id,target_actor_id,expected_fingerprint,verified_bot_binding,
            verified_room_binding,verified_human_binding,target_release_sha,target_callback_release_sha,
            target_release_pair_sha256,tg,x) then
            raise exception 'final_card_preparation_conflict' using errcode='23505'; end if;
        perform private.require_content_ops_release_pair(r.workspace_id,r.client_id,
            prior.release_sha,prior.callback_release_sha,prior.release_pair_sha256,prior.created_at);
        return jsonb_build_object('status','preparation_recorded','intent_id',prior.id,
            'reused',true,'execution_authorized',false);
    end if;
    if exists(select 1 from private.content_ops_final_card_deliveries where review_id=r.id and epoch=r.epoch) then
        raise exception 'final_card_preparation_delivery_exists' using errcode='23514'; end if;
    insert into private.content_ops_final_card_preparations(id,review_id,parent_card_id,actor_id,epoch,
        version_fingerprint,bot_binding,room_binding,human_binding,release_sha,callback_release_sha,
        release_pair_sha256,telegram_route_binding,typefully_route_binding)
    values(target_id,r.id,target_parent_card_id,target_actor_id,r.epoch,expected_fingerprint,
        verified_bot_binding,verified_room_binding,verified_human_binding,target_release_sha,
        target_callback_release_sha,target_release_pair_sha256,tg,x);
    return jsonb_build_object('status','preparation_recorded','intent_id',target_id,
        'reused',false,'execution_authorized',false);
end $$;

-- No lease/expiry/reclaim: claiming commits BEFORE asset/provider I/O. Lost
-- response or any subsequent error holds the same ID; never allocates another.
create function private.claim_content_ops_final_card_preparation(
    target_id uuid,expected_release_sha text,expected_callback_release_sha text,expected_pair text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    p private.content_ops_final_card_preparations; r private.content_ops_button_reviews;
    candidate jsonb; preflight jsonb;
begin
    select * into p from private.content_ops_final_card_preparations where id=target_id;
    if not found then raise exception 'final_card_preparation_unknown' using errcode='P0002'; end if;
    select * into r from private.content_ops_button_reviews where id=p.review_id;
    perform 1 from public.content_items where id=r.content_item_id and workspace_id=r.workspace_id for update;
    select * into p from private.content_ops_final_card_preparations where id=target_id for update;
    select * into r from private.content_ops_button_reviews where id=p.review_id for share;
    if (p.release_sha,p.callback_release_sha,p.release_pair_sha256)
       is distinct from (expected_release_sha,expected_callback_release_sha,expected_pair) then
        raise exception 'final_card_preparation_release_mismatch' using errcode='23514'; end if;
    if p.claimed_at is not null then
        return jsonb_build_object('status','outcome_unknown','execution_authorized',false);
    end if;
    perform private.require_content_ops_release_pair(r.workspace_id,r.client_id,
        p.release_sha,p.callback_release_sha,p.release_pair_sha256,p.created_at);
    preflight:=private.content_ops_final_card_preflight(p.review_id,p.parent_card_id,p.actor_id,
        p.version_fingerprint,p.bot_binding,p.room_binding,p.human_binding);
    if preflight->>'status' is distinct from 'ready_for_final_card' or p.epoch<>r.epoch then
        raise exception 'final_card_preparation_ineligible' using errcode='23514'; end if;
    perform 1 from private.content_ops_button_identities where workspace_id=r.workspace_id
        and actor_id=p.actor_id and bot_binding=p.bot_binding and human_binding=p.human_binding and active for share;
    if not found then raise exception 'final_card_preparation_identity_inactive' using errcode='23514'; end if;
    perform private.require_content_ops_publication_routes(r.workspace_id,r.client_id,
        p.release_sha,p.telegram_route_binding,p.typefully_route_binding);
    candidate:=private.content_ops_review_candidate(r.workspace_id,r.content_item_id,r.content_version_id);
    if candidate is null or exists(select 1 from private.content_ops_final_card_deliveries
        where review_id=r.id and epoch=r.epoch) then
        raise exception 'final_card_preparation_ineligible' using errcode='23514'; end if;
    update private.content_ops_final_card_preparations set claimed_at=clock_timestamp() where id=p.id;
    return jsonb_build_object('status','preparation_claimed','execution_authorized',false,
        'intent_id',p.id,'workspace_id',r.workspace_id,'review_id',r.id,'parent_card_id',p.parent_card_id,
        'review_epoch',r.epoch,'reviewer_id',p.actor_id,'version_fingerprint',p.version_fingerprint,
        'bot_binding',p.bot_binding,'room_binding',p.room_binding,'human_binding',p.human_binding,
        'release_sha',p.release_sha,'callback_release_sha',p.callback_release_sha,'release_pair_sha256',p.release_pair_sha256,
        'telegram_route_binding',p.telegram_route_binding,'typefully_route_binding',p.typefully_route_binding,
        'candidate',candidate);
end $$;
-- Couple the original preparation generation to the existing transport ledger.
-- This closes a pair P1 -> P2 -> P1 switch between claim and reservation: a new
-- delivery timestamp must never make an old preparation current again.
create function private.reserve_prepared_content_ops_final_card_delivery(
    target_id uuid,target_review_id uuid,target_parent_card_id uuid,
    target_actor_id uuid,expected_fingerprint text,verified_bot_binding text,
    verified_room_binding text,verified_human_binding text,
    target_snapshot_sha256 text,target_packet_sha256 text,target_release_sha text,
    expected_telegram_binding text,expected_typefully_binding text,
    requested_decision_mode text,target_callback_release_sha text,target_release_pair_sha256 text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare p private.content_ops_final_card_preparations; r private.content_ops_button_reviews;
begin
    select * into r from private.content_ops_button_reviews where id=target_review_id;
    if not found then raise exception 'final_card_preparation_unknown' using errcode='P0002'; end if;
    perform 1 from public.content_items where id=r.content_item_id and workspace_id=r.workspace_id for update;
    select * into p from private.content_ops_final_card_preparations where id=target_id for share;
    if not found or p.claimed_at is null or requested_decision_mode is distinct from 'telegram_and_typefully_draft'
       or (p.review_id,p.parent_card_id,p.actor_id,p.version_fingerprint,p.bot_binding,p.room_binding,
           p.human_binding,p.release_sha,p.callback_release_sha,p.release_pair_sha256,
           p.telegram_route_binding,p.typefully_route_binding)
          is distinct from (target_review_id,target_parent_card_id,target_actor_id,expected_fingerprint,
           verified_bot_binding,verified_room_binding,verified_human_binding,target_release_sha,
           target_callback_release_sha,target_release_pair_sha256,expected_telegram_binding,expected_typefully_binding) then
        raise exception 'final_card_preparation_reservation_invalid' using errcode='23514'; end if;
    perform private.require_content_ops_release_pair(r.workspace_id,r.client_id,
        p.release_sha,p.callback_release_sha,p.release_pair_sha256,p.created_at);
    perform 1 from private.content_ops_button_identities where workspace_id=r.workspace_id
        and actor_id=p.actor_id and bot_binding=p.bot_binding and human_binding=p.human_binding and active for share;
    if not found then raise exception 'final_card_preparation_identity_inactive' using errcode='23514'; end if;
    return private.reserve_content_ops_final_card_delivery(target_id,target_review_id,target_parent_card_id,
        target_actor_id,expected_fingerprint,verified_bot_binding,verified_room_binding,verified_human_binding,
        target_snapshot_sha256,target_packet_sha256,target_release_sha,expected_telegram_binding,expected_typefully_binding,
        requested_decision_mode,target_callback_release_sha,target_release_pair_sha256);
end $$;
-- Each new part gets a fresh identity/pair admission before recording its
-- attempt. Confirm/terminal receipt reconciliation intentionally stays on the
-- original ledger: revocation stops new I/O, not evidence of an earlier send.
create function private.begin_prepared_content_ops_final_card_part(
    target_delivery_id uuid,target_part_index smallint,target_payload_sha256 text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare p private.content_ops_final_card_preparations; r private.content_ops_button_reviews;
    d private.content_ops_final_card_deliveries;
begin
    select * into p from private.content_ops_final_card_preparations where id=target_delivery_id;
    if not found then raise exception 'final_card_preparation_unknown' using errcode='P0002'; end if;
    select * into r from private.content_ops_button_reviews where id=p.review_id;
    perform 1 from public.content_items where id=r.content_item_id and workspace_id=r.workspace_id for update;
    select * into p from private.content_ops_final_card_preparations where id=target_delivery_id for share;
    select * into d from private.content_ops_final_card_deliveries where id=target_delivery_id for share;
    if not found or p.claimed_at is null or d.decision_mode is distinct from 'telegram_and_typefully_draft'
       or (p.review_id,p.parent_card_id,p.actor_id,p.epoch,p.version_fingerprint,p.bot_binding,p.room_binding,
           p.human_binding,p.release_sha,p.callback_release_sha,p.release_pair_sha256,
           p.telegram_route_binding,p.typefully_route_binding)
          is distinct from (d.review_id,d.parent_card_id,d.actor_id,d.epoch,d.version_fingerprint,d.bot_binding,d.room_binding,
           d.human_binding,d.release_sha,d.callback_release_sha,d.release_pair_sha256,
           d.telegram_route_binding,d.typefully_route_binding) then
        raise exception 'final_card_preparation_part_invalid' using errcode='23514'; end if;
    perform private.require_content_ops_release_pair(r.workspace_id,r.client_id,
        p.release_sha,p.callback_release_sha,p.release_pair_sha256,p.created_at);
    perform 1 from private.content_ops_button_identities where workspace_id=r.workspace_id
        and actor_id=p.actor_id and bot_binding=p.bot_binding and human_binding=p.human_binding and active for share;
    if not found then raise exception 'final_card_preparation_identity_inactive' using errcode='23514'; end if;
    return private.begin_content_ops_final_card_part(target_delivery_id,target_part_index,target_payload_sha256);
end $$;
revoke all on function private.guard_content_ops_final_card_preparation() from public,anon,authenticated,service_role;
revoke all on function private.request_content_ops_final_card_preparation(uuid,uuid,uuid,uuid,text,text,text,text,text,text,text)
    from public,anon,authenticated,service_role;
revoke all on function private.claim_content_ops_final_card_preparation(uuid,text,text,text)
    from public,anon,authenticated,service_role;
revoke all on function private.reserve_prepared_content_ops_final_card_delivery(uuid,uuid,uuid,uuid,text,text,text,text,text,text,text,text,text,text,text,text)
    from public,anon,authenticated,service_role;
revoke all on function private.begin_prepared_content_ops_final_card_part(uuid,smallint,text)
    from public,anon,authenticated,service_role;
commit;
