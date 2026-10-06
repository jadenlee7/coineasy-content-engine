-- LOCAL-ONLY, UNMOUNTED PROPOSAL. Records a second-stage human decision.
-- A confirmation is NOT a public approval, publication, job, or provider send.
-- No runtime grants. The eventual approval/outbox owner remains separate.
begin;

create table private.content_ops_final_decisions (
    id uuid primary key default gen_random_uuid(),
    card_id uuid not null unique references private.content_ops_final_cards(id),
    review_id uuid not null unique references private.content_ops_button_reviews(id),
    actor_id uuid not null references private.content_ops_review_principals(id),
    content_version_id uuid not null,
    version_fingerprint text not null check (version_fingerprint ~ '^[a-f0-9]{64}$'),
    snapshot_sha256 text not null check (snapshot_sha256 ~ '^[a-f0-9]{64}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    idempotency_key text not null unique check (idempotency_key ~ '^[a-f0-9]{64}$'),
    action text not null check (action in ('confirm_publication','hold')),
    status text not null check (status in ('confirmed_pending_publication_owner','held')),
    decided_at timestamptz not null default clock_timestamp()
);
alter table private.content_ops_final_decisions enable row level security;
alter table private.content_ops_final_decisions force row level security;
revoke all on private.content_ops_final_decisions
    from public,anon,authenticated,service_role;
create trigger final_decisions_immutable before update or delete
    on private.content_ops_final_decisions for each row
    execute function private.guard_content_ops_final_card_ledger();

create function private.record_content_ops_final_decision(
    target_card_id uuid, target_actor_id uuid, target_content_version_id uuid,
    expected_fingerprint text, expected_snapshot_sha256 text,
    verified_control_message_binding text, verified_bot_binding text,
    verified_room_binding text, verified_human_binding text,
    verified_runtime_release_sha text, requested_action text, operation_key text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    card private.content_ops_final_cards;
    delivery private.content_ops_final_card_deliveries;
    review private.content_ops_button_reviews;
    previous private.content_ops_final_decisions;
    preflight jsonb;
    decision_status text;
    decision_id uuid;
begin
    if target_card_id is null or target_actor_id is null
       or target_content_version_id is null
       or expected_fingerprint is null or expected_fingerprint !~ '^[a-f0-9]{64}$'
       or expected_snapshot_sha256 is null or expected_snapshot_sha256 !~ '^[a-f0-9]{64}$'
       or verified_control_message_binding is null
       or verified_control_message_binding !~ '^[a-f0-9]{64}$'
       or verified_bot_binding is null or verified_bot_binding !~ '^[a-f0-9]{64}$'
       or verified_room_binding is null or verified_room_binding !~ '^[a-f0-9]{64}$'
       or verified_human_binding is null or verified_human_binding !~ '^[a-f0-9]{64}$'
       or verified_runtime_release_sha is null
       or verified_runtime_release_sha !~ '^[a-f0-9]{40}$'
       or requested_action is null or requested_action not in ('confirm_publication','hold')
       or operation_key is null or operation_key !~ '^[a-f0-9]{64}$' then
        raise exception 'final_decision_arguments_invalid' using errcode='22023';
    end if;
    select * into card from private.content_ops_final_cards where id=target_card_id;
    if not found then raise exception 'final_decision_card_unknown' using errcode='P0002';end if;
    select * into review from private.content_ops_button_reviews where id=card.review_id;
    if not found then raise exception 'final_decision_review_unknown' using errcode='P0002';end if;
    -- All decision/edit/card writers serialize on the content item first.
    perform 1 from public.content_items where workspace_id=review.workspace_id
        and id=review.content_item_id for update;
    select * into review from private.content_ops_button_reviews
        where id=card.review_id for update;
    select * into card from private.content_ops_final_cards
        where id=target_card_id for share;
    select * into delivery from private.content_ops_final_card_deliveries
        where id=card.id for share;
    if card.actor_id is distinct from target_actor_id
       or card.review_id is distinct from review.id
       or card.control_message_binding is distinct from verified_control_message_binding
       or card.snapshot_sha256 is distinct from expected_snapshot_sha256
       or card.version_fingerprint is distinct from expected_fingerprint
       or review.content_version_id is distinct from target_content_version_id
       or delivery.actor_id is distinct from target_actor_id
       or delivery.bot_binding is distinct from verified_bot_binding
       or delivery.room_binding is distinct from verified_room_binding
       or delivery.human_binding is distinct from verified_human_binding
       or delivery.release_sha is distinct from verified_runtime_release_sha then
        raise exception 'final_decision_binding_conflict' using errcode='23514';
    end if;
    select * into previous from private.content_ops_final_decisions
        where review_id=review.id;
    if found then
        if previous.card_id is distinct from card.id
           or previous.actor_id is distinct from target_actor_id
           or previous.content_version_id is distinct from target_content_version_id
           or previous.version_fingerprint is distinct from expected_fingerprint
           or previous.snapshot_sha256 is distinct from expected_snapshot_sha256
           or previous.release_sha is distinct from verified_runtime_release_sha
           or previous.action is distinct from requested_action
           or previous.idempotency_key is distinct from operation_key then
            raise exception 'final_decision_replay_conflict' using errcode='23505';
        end if;
        return jsonb_build_object('status',previous.status,'decision_id',previous.id,
            'reused',true,'execution_authorized',false);
    end if;
    if clock_timestamp()>=card.expires_at or review.epoch is distinct from card.epoch
       or review.state is distinct from 'active' then
        raise exception 'final_decision_card_expired_or_superseded' using errcode='23514';
    end if;
    preflight:=private.content_ops_final_card_preflight(review.id,
        card.parent_card_id,target_actor_id,expected_fingerprint,
        verified_bot_binding,verified_room_binding,verified_human_binding);
    if preflight->>'status' is distinct from 'ready_for_final_card'
       or (preflight->>'review_epoch')::bigint is distinct from card.epoch
       or preflight->>'content_version_id' is distinct from target_content_version_id::text then
        raise exception 'final_decision_candidate_ineligible' using errcode='23514';
    end if;
    decision_status:=case when requested_action='hold' then 'held'
        else 'confirmed_pending_publication_owner' end;
    if requested_action='confirm_publication' then
        perform private.require_content_ops_publication_routes(review.workspace_id,
            review.client_id,verified_runtime_release_sha,delivery.telegram_route_binding,
            delivery.typefully_route_binding);
    end if;
    insert into private.content_ops_final_decisions(card_id,review_id,actor_id,
        content_version_id,version_fingerprint,snapshot_sha256,release_sha,
        idempotency_key,action,status)
    values(card.id,review.id,target_actor_id,target_content_version_id,
        expected_fingerprint,expected_snapshot_sha256,verified_runtime_release_sha,
        operation_key,requested_action,decision_status)
    returning id into decision_id;
    if requested_action='hold' then
        update private.content_ops_button_reviews set epoch=epoch+1,state='held'
            where id=review.id;
    end if;
    return jsonb_build_object('status',decision_status,'decision_id',decision_id,
        'reused',false,'execution_authorized',false);
end $$;

create function private.read_content_ops_final_decision_terminal(
    target_card_id uuid, target_actor_id uuid, operation_key text
) returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare d private.content_ops_final_decisions;
begin
    if target_card_id is null or target_actor_id is null or operation_key is null
       or operation_key !~ '^[a-f0-9]{64}$' then
        raise exception 'final_decision_terminal_arguments_invalid' using errcode='22023';
    end if;
    select * into d from private.content_ops_final_decisions
        where card_id=target_card_id and actor_id=target_actor_id
          and idempotency_key=operation_key;
    return jsonb_build_object('status',case when found then d.status else 'not_recorded' end,
        'decision_id',d.id,'execution_authorized',false);
end $$;

revoke all on function private.record_content_ops_final_decision(
    uuid,uuid,uuid,text,text,text,text,text,text,text,text,text)
    from public,anon,authenticated,service_role;
revoke all on function private.read_content_ops_final_decision_terminal(uuid,uuid,text)
    from public,anon,authenticated,service_role;
commit;
