-- LOCAL PROPOSAL ONLY. No runtime grants, route, sender, retry queue or approval.
-- The trusted courier must authenticate full packet/card delivery before record.
-- Supplied keyed hashes are opaque evidence, NOT verified provider authentication.
begin;
create table private.content_ops_button_cards (
    id uuid primary key,
    review_id uuid not null references private.content_ops_button_reviews(id),
    epoch bigint not null check(epoch>=0),
    version_fingerprint text not null check(version_fingerprint ~ '^[a-f0-9]{64}$'),
    bindings jsonb not null,
    parts jsonb not null,
    delivered_at timestamptz not null,
    expires_at timestamptz not null,
    recorded_at timestamptz not null default clock_timestamp(),
    active boolean not null default false,
    unique(review_id,epoch),
    check(delivered_at<=recorded_at and expires_at>delivered_at
        and expires_at<=delivered_at+interval '30 minutes')
);
create unique index content_ops_button_card_message_idx on private.content_ops_button_cards
    ((bindings->>'bot'),(bindings->>'room'),(bindings->>'message'));
create table private.content_ops_button_prompt_attempts (
    id uuid primary key,
    card_id uuid not null references private.content_ops_button_cards(id),
    review_id uuid not null references private.content_ops_button_reviews(id),
    actor_id uuid not null references auth.users(id),
    epoch bigint not null check(epoch>0),
    edit_action_key text not null,
    version_fingerprint text not null check(version_fingerprint ~ '^[a-f0-9]{64}$'),
    bot_binding text not null check(bot_binding ~ '^[a-f0-9]{64}$'),
    room_binding text not null check(room_binding ~ '^[a-f0-9]{64}$'),
    human_binding text not null check(human_binding ~ '^[a-f0-9]{64}$'),
    thread_id bigint check(thread_id>0 and thread_id<4503599627370496),
    parent_binding_sha256 text not null check(parent_binding_sha256 ~ '^[a-f0-9]{64}$'),
    expected_text_sha256 text not null check(expected_text_sha256 ~ '^[a-f0-9]{64}$'),
    started_at timestamptz not null,
    expires_at timestamptz not null,
    unique(review_id,epoch),
    foreign key(review_id,edit_action_key)
        references private.content_ops_button_actions(review_id,idempotency_key),
    check(expires_at>started_at and expires_at<=started_at+interval '30 minutes')
);
alter table private.content_ops_button_cards enable row level security;
alter table private.content_ops_button_cards force row level security;
alter table private.content_ops_button_prompt_attempts enable row level security;
alter table private.content_ops_button_prompt_attempts force row level security;
revoke all on private.content_ops_button_cards,private.content_ops_button_prompt_attempts
    from public,anon,authenticated,service_role;

create function private.guard_content_ops_button_durable_record()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    if tg_table_name='content_ops_button_cards' and tg_op='UPDATE' then
        if old.active and new.active is false
            and (to_jsonb(old)-'active')=(to_jsonb(new)-'active') then return new;end if;
    end if;
    raise exception 'button_durable_record_immutable' using errcode='23514';
end $$;
create trigger content_ops_button_card_immutable before update or delete
    on private.content_ops_button_cards for each row
    execute function private.guard_content_ops_button_durable_record();
create trigger content_ops_button_attempt_immutable before update or delete
    on private.content_ops_button_prompt_attempts for each row
    execute function private.guard_content_ops_button_durable_record();
revoke all on function private.guard_content_ops_button_durable_record()
    from public,anon,authenticated,service_role;

create function private.record_content_ops_button_card(
    target_review_id uuid,target_card_id uuid,expected_fingerprint text,target_epoch bigint,
    target_bindings jsonb,target_parts jsonb,delivered timestamptz,expires timestamptz
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    r private.content_ops_button_reviews; initial private.content_ops_button_reviews;
    c private.content_ops_button_cards; p jsonb; k text; n integer;
    observed timestamptz; messages text[]; topic numeric;
begin
    if target_review_id is null or target_card_id is null
        or target_card_id='00000000-0000-0000-0000-000000000000'::uuid
        or expected_fingerprint is null or expected_fingerprint !~ '^[a-f0-9]{64}$'
        or target_epoch is null or target_epoch<0
        or delivered is null or expires is null or not isfinite(delivered) or not isfinite(expires)
        or jsonb_typeof(target_bindings) is distinct from 'object'
        or jsonb_typeof(target_parts) is distinct from 'array' then
        raise exception 'button_card_arguments_invalid' using errcode='22023';end if;
    if (select array_agg(key order by key) from jsonb_object_keys(target_bindings) key)
        is distinct from array['bot','card_receipt','message','packet_receipt','parent_binding','room','thread_id']
        or jsonb_array_length(target_parts)<>3 then
        raise exception 'button_card_arguments_invalid' using errcode='22023';end if;
    foreach k in array array['bot','room','message','packet_receipt','card_receipt','parent_binding'] loop
        if jsonb_typeof(target_bindings->k) is distinct from 'string'
            or target_bindings->>k !~ '^[a-f0-9]{64}$' then
            raise exception 'button_card_arguments_invalid' using errcode='22023';end if;
    end loop;
    if target_bindings->'thread_id' is distinct from 'null'::jsonb then
        if jsonb_typeof(target_bindings->'thread_id') is distinct from 'number'
            or target_bindings->>'thread_id' !~ '^[1-9][0-9]{0,15}$' then
            raise exception 'button_card_arguments_invalid' using errcode='22023';end if;
        topic:=(target_bindings->>'thread_id')::numeric;
        if topic>=4503599627370496 then
            raise exception 'button_card_arguments_invalid' using errcode='22023';end if;
    end if;
    messages:=array[target_bindings->>'message'];
    for n in 0..2 loop
        p:=target_parts->n;
        if jsonb_typeof(p) is distinct from 'object' then
            raise exception 'button_card_parts_invalid' using errcode='22023';end if;
        if (select array_agg(key order by key) from jsonb_object_keys(p) key)
            is distinct from array['kind','message_binding','outcome','payload_sha256']
            or p->>'kind' is distinct from (array['image','telegram','x'])[n+1]
            or p->>'outcome' is distinct from 'sent'
            or jsonb_typeof(p->'message_binding') is distinct from 'string'
            or coalesce(p->>'message_binding','') !~ '^[a-f0-9]{64}$'
            or jsonb_typeof(p->'payload_sha256') is distinct from 'string'
            or coalesce(p->>'payload_sha256','') !~ '^[a-f0-9]{64}$'
            or p->>'message_binding'=any(messages) then
            raise exception 'button_card_parts_invalid' using errcode='22023';end if;
        messages:=array_append(messages,p->>'message_binding');
    end loop;
    select * into initial from private.content_ops_button_reviews where id=target_review_id;
    if not found then raise exception 'button_card_review_unknown' using errcode='P0002';end if;
    perform 1 from public.content_items where id=initial.content_item_id
        and workspace_id=initial.workspace_id for update;
    select * into r from private.content_ops_button_reviews where id=target_review_id for update;
    if r.workspace_id is distinct from initial.workspace_id
        or r.content_item_id is distinct from initial.content_item_id then
        raise exception 'button_card_lineage_changed' using errcode='23514';end if;
    perform 1 from public.workspace_clients where workspace_id=r.workspace_id
        and client_id=r.client_id and active for share;
    if not found then raise exception 'button_card_ineligible' using errcode='23514';end if;
    observed:=clock_timestamp();
    if r.state is distinct from 'active' or r.epoch is distinct from target_epoch
        or r.version_fingerprint is distinct from expected_fingerprint
        or r.version_fingerprint is distinct from private.content_ops_button_version_fingerprint(
            r.workspace_id,r.content_item_id,r.content_version_id)
        or delivered<r.created_at or delivered>observed or expires<=observed
        or expires>r.expires_at or expires>delivered+interval '30 minutes'
        or not exists(select 1 from public.content_items i where i.id=r.content_item_id
            and i.workspace_id=r.workspace_id and i.current_version_id=r.content_version_id
            and i.status='needs_review')
        or exists(select 1 from public.approvals a where a.workspace_id=r.workspace_id and a.content_item_id=r.content_item_id)
        or exists(select 1 from public.publications p where p.workspace_id=r.workspace_id and p.content_item_id=r.content_item_id) then
        raise exception 'button_card_ineligible' using errcode='23514';end if;
    select * into c from private.content_ops_button_cards where review_id=r.id and epoch=r.epoch;
    if found then
        if c.id is distinct from target_card_id or not c.active
            or c.version_fingerprint is distinct from expected_fingerprint
            or c.bindings is distinct from target_bindings or c.parts is distinct from target_parts
            or c.delivered_at is distinct from delivered or c.expires_at is distinct from expires then
            raise exception 'button_card_conflict' using errcode='23505';end if;
        return jsonb_build_object('status','card_recorded','card_id',c.id,'reused',true,'execution_authorized',false);
    end if;
    insert into private.content_ops_button_cards(id,review_id,epoch,version_fingerprint,
        bindings,parts,delivered_at,expires_at,active)
    values(target_card_id,r.id,r.epoch,r.version_fingerprint,target_bindings,target_parts,delivered,expires,true);
    return jsonb_build_object('status','card_recorded','card_id',target_card_id,'reused',false,'execution_authorized',false);
end $$;

create function private.reserve_content_ops_button_prompt_attempt(
    target_card_id uuid,target_attempt_id uuid,target_actor_id uuid,
    verified_human_binding text,target_action_key text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    c private.content_ops_button_cards; initial private.content_ops_button_reviews;
    r private.content_ops_button_reviews; a private.content_ops_button_actions;
    prior private.content_ops_button_prompt_attempts; actor uuid;
    observed timestamptz; expiry timestamptz; state jsonb; text_hash text;
begin
    if target_card_id is null or target_attempt_id is null or target_actor_id is null
        or target_attempt_id='00000000-0000-0000-0000-000000000000'::uuid
        or verified_human_binding is null or verified_human_binding !~ '^[a-f0-9]{64}$'
        or target_action_key is null or target_action_key !~ '^[a-f0-9]{64}$' then
        raise exception 'button_attempt_arguments_invalid' using errcode='22023';end if;
    select * into c from private.content_ops_button_cards where id=target_card_id;
    if not found then raise exception 'button_attempt_card_unknown' using errcode='P0002';end if;
    select * into initial from private.content_ops_button_reviews where id=c.review_id;
    perform 1 from public.content_items where id=initial.content_item_id
        and workspace_id=initial.workspace_id for update;
    select * into r from private.content_ops_button_reviews where id=c.review_id for update;
    select * into c from private.content_ops_button_cards where id=target_card_id for share;
    if c.review_id is distinct from r.id or r.workspace_id is distinct from initial.workspace_id
        or r.content_item_id is distinct from initial.content_item_id then
        raise exception 'button_attempt_lineage_changed' using errcode='23514';end if;
    select actor_id into actor from private.content_ops_button_identities i
        where i.workspace_id=r.workspace_id and i.bot_binding=c.bindings->>'bot'
        and i.human_binding=verified_human_binding and i.active for share;
    if not found or actor is distinct from target_actor_id then
        raise exception 'button_attempt_actor_forbidden' using errcode='42501';end if;
    perform 1 from private.content_ops_button_reviewers v where v.workspace_id=r.workspace_id
        and v.client_id=r.client_id and v.actor_id=actor and v.active for share;
    if not found then raise exception 'button_attempt_actor_forbidden' using errcode='42501';end if;
    perform 1 from public.workspace_clients where workspace_id=r.workspace_id
        and client_id=r.client_id and active for share;
    if not found then raise exception 'button_attempt_ineligible' using errcode='23514';end if;
    select * into a from private.content_ops_button_actions
        where review_id=r.id and idempotency_key=target_action_key for share;
    if not found or a.actor_id is distinct from actor or a.epoch is distinct from r.epoch
        or a.action not in ('edit_telegram','edit_x') or a.result_status is distinct from 'edit_requested'
        or a.version_fingerprint is distinct from r.version_fingerprint
        or a.created_at<c.delivered_at then
        raise exception 'button_attempt_action_ineligible' using errcode='23514';end if;
    observed:=clock_timestamp();
    expiry:=least(r.expires_at,c.expires_at,c.delivered_at+interval '30 minutes',observed+interval '30 minutes');
    state:=private.content_ops_button_check_state(r.id,actor);
    if not c.active or r.state is distinct from 'edit_requested' or r.epoch<>c.epoch+1
        or c.version_fingerprint is distinct from r.version_fingerprint or expiry<=observed
        or c.delivered_at>observed or a.created_at>observed
        or state->>'status' is distinct from 'edit_requested' then
        raise exception 'button_attempt_ineligible' using errcode='23514';end if;
    text_hash:=encode(sha256(convert_to(case a.action when 'edit_telegram' then
        '수정할 Telegram 공지 전문을 이 메시지에 답장해주세요. 저장 후 다시 검수하며, 자동 게시되지 않습니다.'
        else '수정할 X 게시글 전문을 이 메시지에 답장해주세요. 저장 후 다시 검수하며, 자동 게시되지 않습니다.' end,'UTF8')),'hex');
    select * into prior from private.content_ops_button_prompt_attempts where review_id=r.id and epoch=r.epoch;
    if found then
        if prior.id is distinct from target_attempt_id or prior.card_id is distinct from c.id
            or prior.actor_id is distinct from actor or prior.human_binding is distinct from verified_human_binding
            or prior.edit_action_key is distinct from target_action_key or prior.expires_at<=observed
            or prior.version_fingerprint is distinct from r.version_fingerprint
            or prior.expected_text_sha256 is distinct from text_hash then
            raise exception 'button_attempt_conflict' using errcode='23505';end if;
        return jsonb_build_object('status','attempt_recorded','attempt_id',prior.id,'reused',true,'execution_authorized',false);
    end if;
    insert into private.content_ops_button_prompt_attempts(id,card_id,review_id,actor_id,epoch,
        edit_action_key,version_fingerprint,bot_binding,room_binding,human_binding,thread_id,
        parent_binding_sha256,expected_text_sha256,started_at,expires_at)
    values(target_attempt_id,c.id,r.id,actor,r.epoch,target_action_key,r.version_fingerprint,
        c.bindings->>'bot',c.bindings->>'room',verified_human_binding,(c.bindings->>'thread_id')::bigint,
        c.bindings->>'parent_binding',text_hash,observed,expiry);
    return jsonb_build_object('status','attempt_recorded','attempt_id',target_attempt_id,'reused',false,'execution_authorized',false);
end $$;
revoke all on function private.record_content_ops_button_card(uuid,uuid,text,bigint,jsonb,jsonb,timestamptz,timestamptz),
    private.reserve_content_ops_button_prompt_attempt(uuid,uuid,uuid,text,text)
    from public,anon,authenticated,service_role;

-- Guard both fresh edits and consumed replays. Historical linkage survives a
-- successful edit's held/epoch+1 transition; card revocation never does.
create function private.assert_content_ops_button_prompt_card_active(
    target_prompt_id uuid,expected_review_id uuid,verified_human_binding text
) returns void language plpgsql volatile security invoker set search_path='' as $$
declare
    p private.content_ops_button_edit_prompts; initial private.content_ops_button_edit_prompts;
    r private.content_ops_button_reviews; initial_review private.content_ops_button_reviews;
    c private.content_ops_button_cards; a private.content_ops_button_prompt_attempts;
    receipt private.content_ops_button_prompt_receipts; act private.content_ops_button_actions;
    observed timestamptz;
begin
    if target_prompt_id is null or expected_review_id is null
        or verified_human_binding is null or verified_human_binding !~ '^[a-f0-9]{64}$' then
        raise exception 'button_prompt_card_arguments_invalid' using errcode='22023';end if;
    select * into initial from private.content_ops_button_edit_prompts where id=target_prompt_id;
    if not found or initial.review_id is distinct from expected_review_id then
        raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
    select * into initial_review from private.content_ops_button_reviews where id=expected_review_id;
    if not found then raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
    perform 1 from public.content_items where id=initial_review.content_item_id
        and workspace_id=initial_review.workspace_id for update;
    if not found then raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
    select * into r from private.content_ops_button_reviews where id=expected_review_id for update;
    if r.content_item_id is distinct from initial_review.content_item_id
        or r.workspace_id is distinct from initial_review.workspace_id then
        raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
    select * into a from private.content_ops_button_prompt_attempts
        where review_id=r.id and epoch=initial.epoch;
    if not found then
        -- Earlier synthetic fixtures only. Never downgrade a linked review:
        -- even inactive/old-epoch cards or attempts make this fallback illegal.
        if exists(select 1 from private.content_ops_button_cards where review_id=r.id)
            or exists(select 1 from private.content_ops_button_prompt_attempts where review_id=r.id) then
            raise exception 'button_prompt_card_reservation_missing' using errcode='23514';end if;
        select * into receipt from private.content_ops_button_prompt_receipts
            where id=initial.owner_receipt_id for share;
        if receipt.reservation_expires_at is not null then
            raise exception 'button_prompt_card_reservation_missing' using errcode='23514';end if;
        select * into p from private.content_ops_button_edit_prompts where id=target_prompt_id for update;
        if p.review_id is distinct from initial.review_id or p.epoch is distinct from initial.epoch
            or p.owner_receipt_id is distinct from initial.owner_receipt_id then
            raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
        return;
    end if;
    select * into c from private.content_ops_button_cards where id=a.card_id and review_id=r.id for share;
    if not found then raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
    select * into a from private.content_ops_button_prompt_attempts
        where review_id=r.id and epoch=initial.epoch and card_id=c.id for share;
    if not found then raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
    select * into receipt from private.content_ops_button_prompt_receipts where id=a.id for share;
    if not found then raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
    select * into p from private.content_ops_button_edit_prompts where id=target_prompt_id for update;
    if not found or p.review_id is distinct from r.id or p.epoch is distinct from a.epoch
        or p.owner_receipt_id is distinct from a.id or p.actor_id is distinct from a.actor_id
        or a.human_binding is distinct from verified_human_binding
        or p.edit_action_key is distinct from a.edit_action_key
        or p.bot_binding is distinct from a.bot_binding or p.room_binding is distinct from a.room_binding
        or receipt.review_id is distinct from r.id or receipt.actor_id is distinct from a.actor_id
        or receipt.epoch is distinct from a.epoch or receipt.edit_action_key is distinct from a.edit_action_key
        or receipt.bot_binding is distinct from p.bot_binding or receipt.room_binding is distinct from p.room_binding
        or receipt.message_binding is distinct from p.message_binding
        or receipt.receipt_sha256 is distinct from p.receipt_sha256
        or receipt.delivered_at is distinct from p.delivered_at or receipt.outcome is distinct from 'sent'
        or receipt.reservation_expires_at is distinct from a.expires_at
        or a.version_fingerprint is distinct from r.version_fingerprint
        or c.version_fingerprint is distinct from a.version_fingerprint or c.epoch+1 is distinct from a.epoch
        or a.bot_binding is distinct from c.bindings->>'bot' or a.room_binding is distinct from c.bindings->>'room'
        or a.thread_id is distinct from (c.bindings->>'thread_id')::bigint
        or a.parent_binding_sha256 is distinct from c.bindings->>'parent_binding' then
        raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
    select * into act from private.content_ops_button_actions
        where review_id=r.id and idempotency_key=a.edit_action_key for share;
    if not found or act.actor_id is distinct from a.actor_id or act.epoch is distinct from a.epoch
        or act.version_fingerprint is distinct from a.version_fingerprint
        or act.action not in ('edit_telegram','edit_x') or act.result_status is distinct from 'edit_requested'
        or act.created_at<c.delivered_at or act.created_at>a.started_at then
        raise exception 'button_prompt_card_lineage_invalid' using errcode='23514';end if;
    observed:=clock_timestamp();
    if not c.active or observed>=c.expires_at or observed>=a.expires_at
        or observed>=p.expires_at or p.expires_at>a.expires_at
        or a.expires_at>c.expires_at or c.delivered_at>a.started_at
        or a.started_at>receipt.delivered_at or receipt.delivered_at>observed
        or receipt.recorded_at>observed then
        raise exception 'button_prompt_card_inactive_or_expired' using errcode='23514';end if;
end $$;

create function private.revoke_content_ops_button_card(
    target_card_id uuid,expected_review_id uuid,expected_fingerprint text,
    target_actor_id uuid,verified_bot_binding text,verified_human_binding text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    c private.content_ops_button_cards; r private.content_ops_button_reviews;
    initial private.content_ops_button_reviews; actor uuid;
begin
    if target_card_id is null or expected_review_id is null or target_actor_id is null
        or expected_fingerprint is null or expected_fingerprint !~ '^[a-f0-9]{64}$'
        or verified_bot_binding is null or verified_bot_binding !~ '^[a-f0-9]{64}$'
        or verified_human_binding is null or verified_human_binding !~ '^[a-f0-9]{64}$' then
        raise exception 'button_card_revoke_arguments_invalid' using errcode='22023';end if;
    select * into initial from private.content_ops_button_reviews where id=expected_review_id;
    if not found then raise exception 'button_card_revoke_unknown' using errcode='P0002';end if;
    perform 1 from public.content_items where id=initial.content_item_id and workspace_id=initial.workspace_id for update;
    if not found then raise exception 'button_card_revoke_unknown' using errcode='P0002';end if;
    select * into r from private.content_ops_button_reviews where id=expected_review_id for update;
    select * into c from private.content_ops_button_cards where id=target_card_id for update;
    if not found or c.review_id is distinct from r.id
        or r.content_item_id is distinct from initial.content_item_id or r.workspace_id is distinct from initial.workspace_id
        or c.version_fingerprint is distinct from expected_fingerprint
        or r.version_fingerprint is distinct from expected_fingerprint
        or c.bindings->>'bot' is distinct from verified_bot_binding then
        raise exception 'button_card_revoke_lineage_invalid' using errcode='23514';end if;
    select actor_id into actor from private.content_ops_button_identities i
        where i.workspace_id=r.workspace_id and i.bot_binding=verified_bot_binding
        and i.human_binding=verified_human_binding and i.active for share;
    if not found or actor is distinct from target_actor_id then
        raise exception 'button_card_revoke_actor_forbidden' using errcode='42501';end if;
    perform 1 from private.content_ops_button_reviewers v where v.workspace_id=r.workspace_id
        and v.client_id=r.client_id and v.actor_id=actor and v.active for share;
    if not found then raise exception 'button_card_revoke_actor_forbidden' using errcode='42501';end if;
    perform 1 from public.workspace_clients where workspace_id=r.workspace_id and client_id=r.client_id and active for share;
    if not found then raise exception 'button_card_revoke_actor_forbidden' using errcode='42501';end if;
    -- Cancel the original card even after an edit commits or its TTL elapses.
    -- This never undoes a saved draft or touches an approval/publication record.
    if not c.active then
        return jsonb_build_object('status','card_revoked','card_id',c.id,'reused',true,'execution_authorized',false);
    end if;
    update private.content_ops_button_cards set active=false where id=c.id;
    return jsonb_build_object('status','card_revoked','card_id',c.id,'reused',false,'execution_authorized',false);
end $$;
revoke all on function private.assert_content_ops_button_prompt_card_active(uuid,uuid,text),
    private.revoke_content_ops_button_card(uuid,uuid,text,uuid,text,text)
    from public,anon,authenticated,service_role;
commit;
