-- LOCAL ONLY. Owner-recorded evidence, not a send queue or public endpoint.
-- Requires both button proposals. No runtime grants or provider execution.
begin;
create table private.content_ops_button_prompt_receipts (
    id uuid primary key,
    review_id uuid not null references private.content_ops_button_reviews(id),
    actor_id uuid not null references auth.users(id),
    epoch bigint not null check (epoch>0),
    edit_action_key text not null,
    bot_binding text not null check (bot_binding ~ '^[a-f0-9]{64}$'),
    room_binding text not null check (room_binding ~ '^[a-f0-9]{64}$'),
    message_binding text not null check (message_binding ~ '^[a-f0-9]{64}$'),
    receipt_sha256 text not null check (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    outcome text not null check (outcome in ('sent','rejected','delivery_unknown')),
    delivered_at timestamptz,
    recorded_at timestamptz not null default clock_timestamp(),
    -- Null is legacy local-fixture compatibility only. The reserved-response
    -- owner always pins the original precise attempt expiry here.
    reservation_expires_at timestamptz,
    check (reservation_expires_at is null or (isfinite(reservation_expires_at)
        and delivered_at is not null and reservation_expires_at>delivered_at
        and reservation_expires_at<=delivered_at+interval '30 minutes')),
    check ((outcome='sent' and delivered_at is not null and delivered_at<=recorded_at)
        or (outcome<>'sent' and delivered_at is null)),
    unique(bot_binding,room_binding,message_binding),
    foreign key(review_id,edit_action_key)
        references private.content_ops_button_actions(review_id,idempotency_key)
);
alter table private.content_ops_button_prompt_receipts enable row level security;
revoke all on private.content_ops_button_prompt_receipts from public,anon,authenticated,service_role;
alter table private.content_ops_button_edit_prompts
    add column owner_receipt_id uuid unique references private.content_ops_button_prompt_receipts(id);
-- Null only supports earlier local fixtures. New registration always supplies it.

create function private.register_content_ops_button_edit_prompt(
    target_receipt_id uuid, verified_human_binding text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    receipt private.content_ops_button_prompt_receipts;
    review private.content_ops_button_reviews;
    action private.content_ops_button_actions;
    prompt private.content_ops_button_edit_prompts;
    actor uuid;
    current_state jsonb;
    observed timestamptz;
    expiry timestamptz;
begin
    if target_receipt_id is null or verified_human_binding is null
        or verified_human_binding !~ '^[a-f0-9]{64}$' then
        raise exception 'button_prompt_arguments_invalid' using errcode='22023';end if;
    select * into receipt from private.content_ops_button_prompt_receipts where id=target_receipt_id;
    if not found then raise exception 'button_prompt_receipt_unknown' using errcode='P0002';end if;
    select * into review from private.content_ops_button_reviews where id=receipt.review_id;
    -- Same global lock order as editing: item -> review -> receipt -> identity.
    perform 1 from public.content_items where workspace_id=review.workspace_id
        and id=review.content_item_id for update;
    select * into review from private.content_ops_button_reviews where id=receipt.review_id for update;
    select * into receipt from private.content_ops_button_prompt_receipts where id=target_receipt_id for share;
    select actor_id into actor from private.content_ops_button_identities i
        where i.workspace_id=review.workspace_id and i.bot_binding=receipt.bot_binding
        and i.human_binding=verified_human_binding and i.active for share;
    if not found or actor is distinct from receipt.actor_id then
        raise exception 'button_prompt_actor_forbidden' using errcode='42501';end if;
    perform 1 from private.content_ops_button_reviewers r where r.workspace_id=review.workspace_id
        and r.client_id=review.client_id and r.actor_id=actor and r.active for share;
    if not found then raise exception 'button_prompt_actor_forbidden' using errcode='42501';end if;
    observed:=clock_timestamp();
    expiry:=least(review.expires_at,receipt.delivered_at+interval '30 minutes',receipt.reservation_expires_at);
    if receipt.outcome is distinct from 'sent' or receipt.delivered_at is null
        or receipt.delivered_at>observed or receipt.recorded_at>observed or expiry<=observed
        or review.state is distinct from 'edit_requested' or receipt.epoch is distinct from review.epoch then
        raise exception 'button_prompt_receipt_ineligible' using errcode='23514';end if;
    select * into action from private.content_ops_button_actions
        where review_id=review.id and idempotency_key=receipt.edit_action_key;
    if not found or action.actor_id is distinct from actor or action.epoch is distinct from receipt.epoch
        or action.action not in ('edit_telegram','edit_x','edit_banner') or action.result_status<>'edit_requested'
        or action.version_fingerprint is distinct from review.version_fingerprint
        or receipt.delivered_at<action.created_at then
        raise exception 'button_prompt_action_ineligible' using errcode='23514';end if;
    -- Banner feedback never uses the historical fixture-only unreserved path.
    if action.action='edit_banner' and not exists (
        select 1 from private.content_ops_button_prompt_attempts a
        join private.content_ops_button_cards c on c.id=a.card_id
        where a.review_id=review.id and a.epoch=receipt.epoch
          and a.edit_action_key=receipt.edit_action_key and a.actor_id=actor
          and a.human_binding=verified_human_binding and c.active
          and a.bot_binding=receipt.bot_binding and a.room_binding=receipt.room_binding
          and a.expires_at=receipt.reservation_expires_at
          and a.started_at<=receipt.delivered_at and a.expires_at>observed
    ) then raise exception 'button_prompt_action_ineligible' using errcode='23514';end if;
    current_state:=private.content_ops_button_check_state(review.id,actor);
    if current_state->>'status' is distinct from 'edit_requested' then
        raise exception 'button_prompt_version_ineligible' using errcode='23514';end if;
    select * into prompt from private.content_ops_button_edit_prompts
        where review_id=review.id and epoch=review.epoch;
    if found then
        if prompt.owner_receipt_id is distinct from receipt.id or prompt.actor_id is distinct from actor
            or prompt.edit_action_key is distinct from receipt.edit_action_key
            or prompt.bot_binding is distinct from receipt.bot_binding
            or prompt.room_binding is distinct from receipt.room_binding
            or prompt.message_binding is distinct from receipt.message_binding
            or prompt.receipt_sha256 is distinct from receipt.receipt_sha256
            or prompt.delivered_at is distinct from receipt.delivered_at
            or prompt.expires_at is distinct from expiry or prompt.consumed_key is not null then
            raise exception 'button_prompt_registration_conflict' using errcode='23505';end if;
        return jsonb_build_object('status','prompt_registered','prompt_id',prompt.id,
            'reused',true,'execution_authorized',false);
    end if;
    insert into private.content_ops_button_edit_prompts(review_id,actor_id,epoch,edit_action_key,
        bot_binding,room_binding,message_binding,receipt_sha256,delivered_at,expires_at,owner_receipt_id)
    values(review.id,actor,review.epoch,receipt.edit_action_key,receipt.bot_binding,receipt.room_binding,
        receipt.message_binding,receipt.receipt_sha256,receipt.delivered_at,expiry,receipt.id)
    returning * into prompt;
    return jsonb_build_object('status','prompt_registered','prompt_id',prompt.id,
        'reused',false,'execution_authorized',false);
end $$;
revoke all on function private.register_content_ops_button_edit_prompt(uuid,text)
    from public,anon,authenticated,service_role;
commit;
