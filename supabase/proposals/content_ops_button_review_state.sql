-- LOCAL PROPOSAL, deliberately outside migrations. NOT an approval/publisher.
-- No runtime role may call these helpers or access these tables yet.
begin;

create table private.content_ops_button_reviewers (
    workspace_id uuid not null,
    client_id text not null,
    actor_id uuid not null references auth.users(id),
    active boolean not null default false,
    primary key (workspace_id, client_id, actor_id),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
);

create table private.content_ops_button_reviews (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    -- Database-derived fingerprint, NOT Python ReviewSnapshot.digest().
    version_fingerprint text not null check (version_fingerprint ~ '^[a-f0-9]{64}$'),
    epoch bigint not null default 0 check (epoch >= 0),
    state text not null default 'active' check (state in ('active','held','edit_requested')),
    created_at timestamptz not null default statement_timestamp(),
    expires_at timestamptz not null default (statement_timestamp() + interval '30 minutes'),
    check (expires_at > created_at and expires_at <= created_at + interval '30 minutes'),
    unique (workspace_id, content_item_id, content_version_id),
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id),
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
);

create table private.content_ops_button_checks (
    review_id uuid not null references private.content_ops_button_reviews(id),
    epoch bigint not null,
    actor_id uuid not null references auth.users(id),
    check_kind text not null check (check_kind in ('source_checked','claims_checked')),
    created_at timestamptz not null default clock_timestamp(),
    primary key (review_id, epoch, actor_id, check_kind)
);

create table private.content_ops_button_actions (
    review_id uuid not null references private.content_ops_button_reviews(id),
    idempotency_key text not null check (idempotency_key ~ '^[a-f0-9]{64}$'),
    actor_id uuid not null references auth.users(id),
    action text not null check (action in (
        'source_checked','claims_checked','hold','edit_telegram','edit_x','edit_banner')),
    version_fingerprint text not null,
    epoch bigint not null,
    result_status text not null check (result_status in ('checked','held','edit_requested')),
    created_at timestamptz not null default clock_timestamp(),
    primary key (review_id, idempotency_key)
);

alter table private.content_ops_button_reviewers enable row level security;
alter table private.content_ops_button_reviews enable row level security;
alter table private.content_ops_button_checks enable row level security;
alter table private.content_ops_button_actions enable row level security;
revoke all on private.content_ops_button_reviewers, private.content_ops_button_reviews,
    private.content_ops_button_checks, private.content_ops_button_actions
    from public, anon, authenticated, service_role;

create function private.content_ops_button_version_fingerprint(w uuid, i uuid, v uuid)
returns text language sql stable security invoker set search_path='' as $$
    select encode(sha256(convert_to(jsonb_build_object(
        'version', to_jsonb(cv),
        'assets', coalesce((select jsonb_agg(to_jsonb(a) order by a.id)
            from public.assets a where a.workspace_id=w and a.content_item_id=i
            and a.content_version_id=v), '[]'::jsonb),
        'sources', coalesce((select jsonb_agg(jsonb_build_object(
            'position', l.position, 'source', to_jsonb(s)) order by l.position, s.id)
            from public.content_source_links l join public.source_items s
            on s.workspace_id=l.workspace_id and s.client_id=l.client_id and s.id=l.source_item_id
            where l.workspace_id=w and l.content_item_id=i), '[]'::jsonb)
        )::text, 'UTF8')), 'hex')
    from public.content_versions cv
    where cv.workspace_id=w and cv.content_item_id=i and cv.id=v
$$;

create function private.content_ops_button_check_state(
    target_review_id uuid, target_actor_id uuid
) returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare
    r private.content_ops_button_reviews;
    item public.content_items;
    state text;
    source_ok boolean:=false;
    claims_ok boolean:=false;
begin
    select * into r from private.content_ops_button_reviews where id=target_review_id;
    if not found then
        return jsonb_build_object('status','not_recorded','execution_authorized',false);
    end if;
    if not exists(select 1 from private.content_ops_button_reviewers a
        where a.workspace_id=r.workspace_id and a.client_id=r.client_id
        and a.actor_id=target_actor_id and a.active) then
        raise exception 'button_review_actor_forbidden' using errcode='42501';
    end if;
    select * into item from public.content_items where workspace_id=r.workspace_id and id=r.content_item_id;
    if r.expires_at <= statement_timestamp() then
        state:='expired';
    elsif item.current_version_id is distinct from r.content_version_id
        or r.version_fingerprint is distinct from private.content_ops_button_version_fingerprint(
            r.workspace_id,r.content_item_id,r.content_version_id) then
        state:='stale';
    elsif item.status is distinct from 'needs_review'
        or not exists(select 1 from public.workspace_clients c
            where c.workspace_id=r.workspace_id and c.client_id=r.client_id and c.active)
        or exists(select 1 from public.approvals a where a.workspace_id=r.workspace_id and a.content_item_id=r.content_item_id)
        or exists(select 1 from public.publications p where p.workspace_id=r.workspace_id and p.content_item_id=r.content_item_id) then
        state:='blocked';
    elsif r.state<>'active' then
        state:=r.state;
    else
        select exists(select 1 from private.content_ops_button_checks c
                where c.review_id=r.id and c.epoch=r.epoch and c.actor_id=target_actor_id and c.check_kind='source_checked'),
            exists(select 1 from private.content_ops_button_checks c
                where c.review_id=r.id and c.epoch=r.epoch and c.actor_id=target_actor_id and c.check_kind='claims_checked')
            into source_ok,claims_ok;
        state:=case when source_ok and claims_ok then 'checks_complete' else 'checks_pending' end;
    end if;
    return jsonb_build_object('status',state,'epoch',r.epoch,
        'source_checked',source_ok,'claims_checked',claims_ok,'execution_authorized',false);
end $$;

create function private.record_content_ops_button_action(
    target_review_id uuid, target_actor_id uuid, expected_fingerprint text,
    requested_action text, operation_key text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    r private.content_ops_button_reviews;
    previous private.content_ops_button_actions;
    check_state jsonb;
    result_status text;
begin
    if target_review_id is null or target_actor_id is null
        or expected_fingerprint is null or expected_fingerprint !~ '^[a-f0-9]{64}$'
        or operation_key is null or operation_key !~ '^[a-f0-9]{64}$'
        or requested_action is null or requested_action not in (
            'source_checked','claims_checked','hold','edit_telegram','edit_x','edit_banner') then
        raise exception 'button_review_action_invalid' using errcode='22023';
    end if;
    -- Shared ordering with existing content edits: item first, review second.
    select * into r from private.content_ops_button_reviews where id=target_review_id;
    if not found then raise exception 'button_review_unregistered' using errcode='P0002';end if;
    perform 1 from public.content_items where workspace_id=r.workspace_id and id=r.content_item_id for update;
    select * into r from private.content_ops_button_reviews where id=target_review_id for update;
    if not found then raise exception 'button_review_unregistered' using errcode='P0002';end if;
    -- Revocation is rechecked and locked, never inferred from request booleans.
    perform 1 from private.content_ops_button_reviewers a where a.workspace_id=r.workspace_id
        and a.client_id=r.client_id and a.actor_id=target_actor_id and a.active for share;
    if not found then raise exception 'button_review_actor_forbidden' using errcode='42501';end if;
    if r.version_fingerprint is distinct from expected_fingerprint then
        raise exception 'button_review_version_conflict' using errcode='23514';end if;
    -- Use wall clock AFTER lock acquisition, not transaction-start time.
    if clock_timestamp() >= r.expires_at then
        raise exception 'button_review_expired' using errcode='23514';end if;
    check_state:=private.content_ops_button_check_state(r.id,target_actor_id);
    if check_state->>'status' in ('stale','blocked','expired','not_recorded') then
        raise exception 'button_review_version_ineligible' using errcode='23514';end if;

    select * into previous from private.content_ops_button_actions
        where review_id=r.id and idempotency_key=operation_key;
    if found then
        if previous.actor_id is distinct from target_actor_id
            or previous.action is distinct from requested_action
            or previous.version_fingerprint is distinct from expected_fingerprint then
            raise exception 'button_review_idempotency_conflict' using errcode='23505';end if;
        return jsonb_build_object('status',case when previous.epoch=r.epoch then previous.result_status else 'superseded' end,
            'reused',true,'checks',check_state,'execution_authorized',false);
    end if;
    if r.state<>'active' then
        raise exception 'button_review_new_card_required' using errcode='23514';end if;
    if requested_action in ('source_checked','claims_checked') then
        insert into private.content_ops_button_checks(review_id,epoch,actor_id,check_kind)
            values(r.id,r.epoch,target_actor_id,requested_action) on conflict do nothing;
        result_status:='checked';
    else
        result_status:=case when requested_action='hold' then 'held' else 'edit_requested' end;
        update private.content_ops_button_reviews set epoch=epoch+1,state=result_status
            where id=r.id returning * into r;
    end if;
    insert into private.content_ops_button_actions(review_id,idempotency_key,actor_id,action,
        version_fingerprint,epoch,result_status)
        values(r.id,operation_key,target_actor_id,requested_action,expected_fingerprint,r.epoch,result_status);
    return jsonb_build_object('status',result_status,'reused',false,
        'checks',private.content_ops_button_check_state(r.id,target_actor_id),'execution_authorized',false);
end $$;

-- No grants, triggers, cron, seed registrations, policies, approval or queue writes.
revoke all on function private.content_ops_button_version_fingerprint(uuid,uuid,uuid)
    from public,anon,authenticated,service_role;
revoke all on function private.content_ops_button_check_state(uuid,uuid)
    from public,anon,authenticated,service_role;
revoke all on function private.record_content_ops_button_action(uuid,uuid,text,text,text)
    from public,anon,authenticated,service_role;
commit;
