-- LOCAL-ONLY, UNMOUNTED PROPOSAL. A bounded final-card PREPARATION check.
-- No approval, publication, outbox, grant, provider call or execution right.
-- Requires the hosted principal bindings and existing private button-card owner.
begin;

create function private.content_ops_final_card_preflight(
    target_review_id uuid, target_card_id uuid, target_actor_id uuid,
    expected_fingerprint text, verified_bot_binding text,
    verified_room_binding text, verified_human_binding text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    r private.content_ops_button_reviews;
    c private.content_ops_button_cards;
    candidate jsonb;
    observed timestamptz;
    part jsonb;
    part_index integer;
begin
    if target_review_id is null or target_card_id is null or target_actor_id is null
       or expected_fingerprint !~ '^[a-f0-9]{64}$'
       or verified_bot_binding !~ '^[a-f0-9]{64}$'
       or verified_room_binding !~ '^[a-f0-9]{64}$'
       or verified_human_binding !~ '^[a-f0-9]{64}$'
       or expected_fingerprint is null or verified_bot_binding is null
       or verified_room_binding is null or verified_human_binding is null then
        raise exception 'final_card_preflight_arguments_invalid' using errcode='22023';
    end if;
    select * into r from private.content_ops_button_reviews
        where id=target_review_id;
    if not found then
        return jsonb_build_object('status','blocked','execution_authorized',false);
    end if;
    -- Existing edit/action owners lock item before review. Keep that order.
    perform 1 from public.content_items where workspace_id=r.workspace_id
        and id=r.content_item_id for update;
    select * into r from private.content_ops_button_reviews
        where id=target_review_id for share;
    select * into c from private.content_ops_button_cards
        where id=target_card_id and review_id=r.id for share;
    observed:=clock_timestamp();
    if not found or not c.active or c.epoch is distinct from r.epoch
       or c.version_fingerprint is distinct from r.version_fingerprint
       or r.version_fingerprint is distinct from expected_fingerprint
       or r.state is distinct from 'active'
       or c.delivered_at>observed or c.expires_at<=observed
       or r.expires_at<=observed
       or c.bindings->>'bot' is distinct from verified_bot_binding
       or c.bindings->>'room' is distinct from verified_room_binding
       or coalesce(c.bindings->>'message','') !~ '^[a-f0-9]{64}$'
       or (case when jsonb_typeof(c.parts)='array'
                then jsonb_array_length(c.parts) else -1 end)<>3 then
        return jsonb_build_object('status','blocked','execution_authorized',false);
    end if;
    for part_index in 0..2 loop
        part:=c.parts->part_index;
        if jsonb_typeof(part) is distinct from 'object'
           or part->>'kind' is distinct from (array['image','telegram','x'])[part_index+1]
           or part->>'outcome' is distinct from 'sent'
           or coalesce(part->>'message_binding','') !~ '^[a-f0-9]{64}$' then
            return jsonb_build_object('status','blocked','execution_authorized',false);
        end if;
    end loop;
    perform 1 from private.content_ops_review_principals p
      join private.content_ops_button_reviewers a
        on a.workspace_id=p.workspace_id and a.actor_id=p.id
      where p.id=target_actor_id and p.workspace_id=r.workspace_id
        and p.bot_binding=verified_bot_binding
        and p.human_binding=verified_human_binding
        and a.client_id=r.client_id and a.active for share of p,a;
    if not found then
        return jsonb_build_object('status','blocked','execution_authorized',false);
    end if;
    if private.content_ops_button_check_state(r.id,target_actor_id)->>'status'
        is distinct from 'checks_complete' then
        return jsonb_build_object('status','blocked','execution_authorized',false);
    end if;
    candidate:=private.content_ops_review_candidate(r.workspace_id,r.content_item_id,
        r.content_version_id);
    if candidate is null or r.version_fingerprint is distinct from
        private.content_ops_button_version_fingerprint(r.workspace_id,
            r.content_item_id,r.content_version_id) then
        return jsonb_build_object('status','blocked','execution_authorized',false);
    end if;
    -- Candidate rechecks latest official source, 24h age, recent feed poll,
    -- immutable version, canonical PNG, natural job and no prior publication.
    -- This does NOT authenticate a Telegram update or prove a new card sent.
    return jsonb_build_object('status','ready_for_final_card',
        'review_id',r.id,'parent_card_id',c.id,'review_epoch',r.epoch,
        'client_id',r.client_id,'content_version_id',r.content_version_id,
        'version_fingerprint',r.version_fingerprint,
        'banner_sha256',candidate->>'banner_sha256',
        'execution_authorized',false);
end $$;

revoke all on function private.content_ops_final_card_preflight(
    uuid,uuid,uuid,text,text,text,text)
    from public,anon,authenticated,service_role;
commit;
