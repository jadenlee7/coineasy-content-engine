-- LOCAL PROPOSAL ONLY. Do not apply without exact hosted-version catalog,
-- owner, ACL and regression review. No deployment, activation or Telegram send.
-- The login role retains NO direct INSERT on attempts, receipts or prompts.
-- This pack assumes the signup-free review-principal schema is already present.
begin;

create function private.reserve_content_ops_button_prompt_for_runtime(
    target_card_id uuid, target_attempt_id uuid, target_actor_id uuid,
    verified_human_binding text, target_action_key text
) returns jsonb language plpgsql volatile security definer set search_path='' as $$
begin
    if session_user is distinct from 'coineasy_private_review' then
        raise exception 'button_runtime_role_forbidden' using errcode='42501';
    end if;
    -- The existing owner function checks the committed action, card, actor,
    -- principal, current version, review state, expiry and exact attempt ID.
    return private.reserve_content_ops_button_prompt_attempt(
        target_card_id,target_attempt_id,target_actor_id,
        verified_human_binding,target_action_key);
end $$;

create function private.register_content_ops_button_prompt_response_for_runtime(
    target_attempt_id uuid, verified_human_binding text,
    target_prompt_binding text, target_card_message_binding text,
    target_receipt_sha256 text,
    observed_at timestamptz
) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare
    attempt private.content_ops_button_prompt_attempts;
    card private.content_ops_button_cards;
    receipt private.content_ops_button_prompt_receipts;
    reservation jsonb;
    observed timestamptz;
begin
    if session_user is distinct from 'coineasy_private_review' then
        raise exception 'button_runtime_role_forbidden' using errcode='42501';
    end if;
    if target_attempt_id is null or verified_human_binding is null
        or verified_human_binding !~ '^[a-f0-9]{64}$'
        or target_prompt_binding is null or target_prompt_binding !~ '^[a-f0-9]{64}$'
        or target_card_message_binding is null or target_card_message_binding !~ '^[a-f0-9]{64}$'
        or target_receipt_sha256 is null or target_receipt_sha256 !~ '^[a-f0-9]{64}$'
        or observed_at is null or not isfinite(observed_at) then
        raise exception 'button_runtime_receipt_invalid' using errcode='22023';
    end if;
    -- The prior reservation has committed before the provider call. Reuse is
    -- only a validation here; an absent/new attempt must never be created by
    -- a response handler. The owner function takes item -> review locks.
    select * into attempt from private.content_ops_button_prompt_attempts
        where id=target_attempt_id;
    if not found then
        raise exception 'button_runtime_attempt_unknown' using errcode='P0002';
    end if;
    reservation:=private.reserve_content_ops_button_prompt_attempt(
        attempt.card_id,attempt.id,attempt.actor_id,
        verified_human_binding,attempt.edit_action_key);
    if reservation->>'status' is distinct from 'attempt_recorded'
        or reservation->>'attempt_id' is distinct from target_attempt_id::text
        or reservation->>'reused' is distinct from 'true'
        or reservation->>'execution_authorized' is distinct from 'false' then
        raise exception 'button_runtime_attempt_ineligible' using errcode='23514';
    end if;
    select * into attempt from private.content_ops_button_prompt_attempts
        where id=target_attempt_id for share;
    select * into card from private.content_ops_button_cards
        where id=attempt.card_id for share;
    observed:=clock_timestamp();
    if attempt.human_binding is distinct from verified_human_binding
        or attempt.review_id is distinct from card.review_id
        or not card.active or observed>=attempt.expires_at
        or observed_at<attempt.started_at or observed_at>=attempt.expires_at
        or observed_at>observed or observed_at<card.delivered_at
        or target_card_message_binding=card.bindings->>'message'
        or exists (select 1 from jsonb_array_elements(card.parts) p
            where p->>'message_binding'=target_card_message_binding) then
        raise exception 'button_runtime_receipt_ineligible' using errcode='23514';
    end if;
    insert into private.content_ops_button_prompt_receipts(
        id,review_id,actor_id,epoch,edit_action_key,bot_binding,room_binding,
        message_binding,receipt_sha256,delivered_at,reservation_expires_at,outcome)
    values(attempt.id,attempt.review_id,attempt.actor_id,attempt.epoch,
        attempt.edit_action_key,attempt.bot_binding,attempt.room_binding,
        target_prompt_binding,target_receipt_sha256,observed_at,
        attempt.expires_at,'sent')
    on conflict (id) do nothing;
    select * into receipt from private.content_ops_button_prompt_receipts
        where id=target_attempt_id for share;
    if receipt.id is distinct from attempt.id
        or receipt.review_id is distinct from attempt.review_id
        or receipt.actor_id is distinct from attempt.actor_id
        or receipt.epoch is distinct from attempt.epoch
        or receipt.edit_action_key is distinct from attempt.edit_action_key
        or receipt.bot_binding is distinct from attempt.bot_binding
        or receipt.room_binding is distinct from attempt.room_binding
        or receipt.message_binding is distinct from target_prompt_binding
        or receipt.receipt_sha256 is distinct from target_receipt_sha256
        or receipt.delivered_at is distinct from observed_at
        or receipt.reservation_expires_at is distinct from attempt.expires_at
        or receipt.outcome is distinct from 'sent' then
        raise exception 'button_runtime_receipt_conflict' using errcode='23505';
    end if;
    -- Registration validates the receipt, current action/version, principal
    -- identity, review state and expiry. Its failure rolls back this insert.
    return private.register_content_ops_button_edit_prompt(
        target_attempt_id,verified_human_binding);
end $$;

revoke all on function private.reserve_content_ops_button_prompt_for_runtime(uuid,uuid,uuid,text,text),
    private.register_content_ops_button_prompt_response_for_runtime(uuid,text,text,text,text,timestamptz)
    from public,anon,authenticated,service_role;
grant execute on function private.reserve_content_ops_button_prompt_for_runtime(uuid,uuid,uuid,text,text),
    private.register_content_ops_button_prompt_response_for_runtime(uuid,text,text,text,text,timestamptz)
    to coineasy_private_review;
commit;
