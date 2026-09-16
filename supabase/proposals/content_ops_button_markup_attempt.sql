-- LOCAL ONLY. No transport, runtime grant, approval or retry eligibility.
-- Keyed plan/receipt hashes are owner evidence, not SQL provider authentication.
begin;
create table private.content_ops_button_markup_attempts (
    id uuid primary key check(id <> '00000000-0000-0000-0000-000000000000'),
    card_id uuid not null unique references private.content_ops_button_cards(id),
    actor_id uuid not null references auth.users(id),
    bot_binding text not null check(bot_binding ~ '^[a-f0-9]{64}$'),
    human_binding text not null check(human_binding ~ '^[a-f0-9]{64}$'),
    plan_seal text not null check(plan_seal ~ '^[a-f0-9]{64}$'),
    parent_binding text not null check(parent_binding ~ '^[a-f0-9]{64}$'),
    expected_receipt_sha256 text not null check(expected_receipt_sha256 ~ '^[a-f0-9]{64}$'),
    started_at timestamptz not null,
    expires_at timestamptz not null,
    recorded_at timestamptz not null default clock_timestamp(),
    status text not null default 'unknown' check(status in ('unknown','response_matched')),
    receipt_sha256 text,
    observed_at timestamptz,
    matched_at timestamptz,
    check(isfinite(started_at) and isfinite(expires_at) and isfinite(recorded_at)
        and started_at<=recorded_at and recorded_at<expires_at
        and expires_at<=started_at+interval '30 minutes'),
    check((status='unknown' and receipt_sha256 is null and observed_at is null and matched_at is null)
        or (status='response_matched' and receipt_sha256 is not null
            and receipt_sha256=expected_receipt_sha256 and observed_at is not null and matched_at is not null
            and isfinite(observed_at) and isfinite(matched_at)
            and recorded_at<=observed_at and observed_at<expires_at and observed_at<=matched_at))
);
alter table private.content_ops_button_markup_attempts enable row level security;
alter table private.content_ops_button_markup_attempts force row level security;
revoke all on private.content_ops_button_markup_attempts from public,anon,authenticated,service_role;

create function private.guard_content_ops_button_markup_attempt()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    if tg_op='UPDATE' and old.status='unknown' and new.status='response_matched'
        and (to_jsonb(old)-array['status','receipt_sha256','observed_at','matched_at'])
            =(to_jsonb(new)-array['status','receipt_sha256','observed_at','matched_at']) then return new;end if;
    raise exception 'button_markup_attempt_immutable' using errcode='23514';
end $$;
create trigger content_ops_button_markup_attempt_immutable before update or delete
    on private.content_ops_button_markup_attempts for each row
    execute function private.guard_content_ops_button_markup_attempt();

create function private.lock_content_ops_button_markup_card(
    target_card uuid,target_actor uuid,verified_bot text,verified_human text
) returns private.content_ops_button_cards language plpgsql volatile security invoker set search_path='' as $$
declare initial private.content_ops_button_reviews; r private.content_ops_button_reviews;
    c private.content_ops_button_cards; actor uuid;
begin
    if target_card is null or target_actor is null
        or target_card='00000000-0000-0000-0000-000000000000'
        or target_actor='00000000-0000-0000-0000-000000000000'
        or verified_bot is null or verified_bot !~ '^[a-f0-9]{64}$'
        or verified_human is null or verified_human !~ '^[a-f0-9]{64}$' then
        raise exception 'button_markup_invalid' using errcode='22023';end if;
    select r0.* into initial from private.content_ops_button_reviews r0
        join private.content_ops_button_cards c0 on c0.review_id=r0.id where c0.id=target_card;
    if not found then raise exception 'button_markup_unknown' using errcode='P0002';end if;
    perform 1 from public.content_items where id=initial.content_item_id and workspace_id=initial.workspace_id for update;
    if not found then raise exception 'button_markup_unknown' using errcode='P0002';end if;
    select * into r from private.content_ops_button_reviews where id=initial.id for update;
    select * into c from private.content_ops_button_cards where id=target_card for update;
    if not found or c.review_id is distinct from r.id or r.workspace_id is distinct from initial.workspace_id
        or r.content_item_id is distinct from initial.content_item_id
        or c.version_fingerprint is distinct from r.version_fingerprint
        or c.bindings->>'bot' is distinct from verified_bot then
        raise exception 'button_markup_lineage_invalid' using errcode='23514';end if;
    select actor_id into actor from private.content_ops_button_identities i
        where i.workspace_id=r.workspace_id and i.bot_binding=verified_bot
        and i.human_binding=verified_human and i.active for share;
    if not found or actor is distinct from target_actor then
        raise exception 'button_markup_forbidden' using errcode='42501';end if;
    perform 1 from private.content_ops_button_reviewers v where v.workspace_id=r.workspace_id
        and v.client_id=r.client_id and v.actor_id=actor and v.active for share;
    if not found then raise exception 'button_markup_forbidden' using errcode='42501';end if;
    perform 1 from public.workspace_clients where workspace_id=r.workspace_id and client_id=r.client_id and active for share;
    if not found then raise exception 'button_markup_forbidden' using errcode='42501';end if;
    return c;
end $$;

create function private.reserve_content_ops_button_markup_attempt(
    target_attempt uuid,target_card uuid,target_actor uuid,verified_bot text,verified_human text,
    expected_parent text,target_plan_seal text,expected_receipt text,started timestamptz,expires timestamptz
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare c private.content_ops_button_cards; a private.content_ops_button_markup_attempts; observed timestamptz;
begin
    if target_attempt is null or target_attempt='00000000-0000-0000-0000-000000000000'
        or expected_parent is null or expected_parent !~ '^[a-f0-9]{64}$'
        or target_plan_seal is null or target_plan_seal !~ '^[a-f0-9]{64}$'
        or expected_receipt is null or expected_receipt !~ '^[a-f0-9]{64}$'
        or started is null or expires is null or not isfinite(started) or not isfinite(expires)
        or expires<=started or expires>started+interval '30 minutes' then
        raise exception 'button_markup_invalid' using errcode='22023';end if;
    c:=private.lock_content_ops_button_markup_card(target_card,target_actor,verified_bot,verified_human);
    if c.bindings->>'parent_binding' is distinct from expected_parent then
        raise exception 'button_markup_lineage_invalid' using errcode='23514';end if;
    select * into a from private.content_ops_button_markup_attempts where card_id=c.id for update;
    if found then
        if a.id is distinct from target_attempt or a.actor_id is distinct from target_actor
            or a.bot_binding is distinct from verified_bot or a.human_binding is distinct from verified_human
            or a.plan_seal is distinct from target_plan_seal or a.parent_binding is distinct from expected_parent
            or a.expected_receipt_sha256 is distinct from expected_receipt
            or a.started_at is distinct from started or a.expires_at is distinct from expires then
            raise exception 'button_markup_attempt_conflict' using errcode='23514';end if;
        -- Even after expiry/revocation: evidence readback, never another send.
        return jsonb_build_object('status',a.status,'attempt_id',a.id,'card_id',a.card_id,
            'new_attempt',false,'execution_authorized',false);
    end if;
    observed:=clock_timestamp();
    if not c.active or started>observed or expires<=observed then
        raise exception 'button_markup_stale' using errcode='23514';end if;
    insert into private.content_ops_button_markup_attempts(id,card_id,actor_id,bot_binding,human_binding,
        plan_seal,parent_binding,expected_receipt_sha256,started_at,expires_at,recorded_at)
        values(target_attempt,c.id,target_actor,verified_bot,verified_human,target_plan_seal,
            expected_parent,expected_receipt,started,expires,observed);
    return jsonb_build_object('status','unknown','attempt_id',target_attempt,'card_id',c.id,
        'new_attempt',true,'execution_authorized',false);
end $$;

create function private.record_content_ops_button_markup_response(
    target_attempt uuid,target_card uuid,target_actor uuid,verified_bot text,verified_human text,
    expected_plan_seal text,matched_receipt text,observed timestamptz
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare c private.content_ops_button_cards; a private.content_ops_button_markup_attempts; db_now timestamptz; reused boolean;
begin
    if target_attempt is null or expected_plan_seal is null or expected_plan_seal !~ '^[a-f0-9]{64}$'
        or matched_receipt is null or matched_receipt !~ '^[a-f0-9]{64}$'
        or observed is null or not isfinite(observed) then
        raise exception 'button_markup_invalid' using errcode='22023';end if;
    c:=private.lock_content_ops_button_markup_card(target_card,target_actor,verified_bot,verified_human);
    select * into a from private.content_ops_button_markup_attempts where id=target_attempt for update;
    if not found then raise exception 'button_markup_unknown' using errcode='P0002';end if;
    if a.card_id is distinct from c.id or a.actor_id is distinct from target_actor
        or a.bot_binding is distinct from verified_bot or a.human_binding is distinct from verified_human
        or a.plan_seal is distinct from expected_plan_seal or a.expected_receipt_sha256 is distinct from matched_receipt
        or a.parent_binding is distinct from c.bindings->>'parent_binding' then
        raise exception 'button_markup_lineage_invalid' using errcode='23514';end if;
    db_now:=clock_timestamp();
    if observed<a.recorded_at or observed>=a.expires_at or observed>db_now then
        raise exception 'button_markup_observation_invalid' using errcode='23514';end if;
    reused:=a.status='response_matched';
    if reused then
        if a.receipt_sha256 is distinct from matched_receipt or a.observed_at is distinct from observed then
            raise exception 'button_markup_response_conflict' using errcode='23514';end if;
    else
        -- Retain evidence even if cancellation committed since the edit attempt.
        -- This is a historical response match, not current button availability.
        update private.content_ops_button_markup_attempts set status='response_matched',
            receipt_sha256=matched_receipt,observed_at=observed,matched_at=db_now where id=a.id;
    end if;
    return jsonb_build_object('status','response_matched','attempt_id',a.id,'card_id',a.card_id,
        'reused',reused,'execution_authorized',false);
end $$;

revoke all on function private.guard_content_ops_button_markup_attempt(),
    private.lock_content_ops_button_markup_card(uuid,uuid,text,text),
    private.reserve_content_ops_button_markup_attempt(uuid,uuid,uuid,text,text,text,text,text,timestamptz,timestamptz),
    private.record_content_ops_button_markup_response(uuid,uuid,uuid,text,text,text,text,timestamptz)
    from public,anon,authenticated,service_role;
commit;
