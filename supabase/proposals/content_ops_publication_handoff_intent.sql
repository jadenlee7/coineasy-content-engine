-- LOCAL-ONLY, UNMOUNTED PROPOSAL. No grants, route rows, worker, or provider call.
-- Applying this file alone cannot dispatch either channel. Separately verified
-- destination bindings and a separately authorized channel owner are required.
begin;

create table private.content_ops_channel_handoffs (
    id uuid primary key default gen_random_uuid(),
    decision_id uuid not null references private.content_ops_final_decisions(id),
    approval_id uuid not null references public.approvals(id),
    workspace_id uuid not null,
    client_id text not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    channel text not null check (channel in ('telegram','typefully_x')),
    route_binding text not null check (route_binding ~ '^[a-f0-9]{64}$'),
    version_fingerprint text not null check (version_fingerprint ~ '^[a-f0-9]{64}$'),
    banner_sha256 text not null check (banner_sha256 ~ '^[a-f0-9]{64}$'),
    copy_sha256 text not null check (copy_sha256 ~ '^[a-f0-9]{64}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    status text not null default 'awaiting_channel_owner'
        check (status = 'awaiting_channel_owner'),
    created_at timestamptz not null default clock_timestamp(),
    unique (decision_id,channel),
    unique (workspace_id,content_version_id,channel),
    foreign key (workspace_id,client_id,content_item_id)
        references public.content_items(workspace_id,client_id,id) on delete restrict,
    foreign key (workspace_id,content_item_id,content_version_id)
        references public.content_versions(workspace_id,content_item_id,id) on delete restrict
);
alter table private.content_ops_channel_handoffs enable row level security;
alter table private.content_ops_channel_handoffs force row level security;
revoke all on private.content_ops_channel_handoffs
    from public,anon,authenticated,service_role;
create trigger content_ops_channel_handoffs_immutable before update or delete
    on private.content_ops_channel_handoffs for each row
    execute function private.guard_content_ops_final_card_ledger();

create function private.materialize_content_ops_publication_handoff(
    target_decision_id uuid,target_actor_id uuid,
    verified_runtime_release_sha text,operation_key text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    d private.content_ops_final_decisions;
    r private.content_ops_button_reviews;
    c private.content_ops_final_cards;
    delivery private.content_ops_final_card_deliveries;
    version public.content_versions;
    existing_approval public.approvals;
    candidate jsonb;
    preflight jsonb;
    channel_name text;
    route private.content_ops_publication_routes;
    copy_text text;
    created_approval_id uuid;
begin
    if target_decision_id is null or target_actor_id is null
       or verified_runtime_release_sha is null
       or verified_runtime_release_sha !~ '^[a-f0-9]{40}$'
       or operation_key is null or operation_key !~ '^[a-f0-9]{64}$' then
        raise exception 'publication_handoff_arguments_invalid' using errcode='22023';
    end if;
    select * into d from private.content_ops_final_decisions
        where id=target_decision_id;
    if not found then
        raise exception 'publication_handoff_decision_unknown' using errcode='P0002';
    end if;
    select * into r from private.content_ops_button_reviews where id=d.review_id;
    if not found then
        raise exception 'publication_handoff_review_unknown' using errcode='P0002';
    end if;
    -- Serialize with edit, review, and publication owners on the item first.
    perform 1 from public.content_items where workspace_id=r.workspace_id
        and id=r.content_item_id for update;
    select * into r from private.content_ops_button_reviews where id=d.review_id for share;
    select * into d from private.content_ops_final_decisions where id=target_decision_id for share;
    if d.actor_id is distinct from target_actor_id
       or d.release_sha is distinct from verified_runtime_release_sha
       or d.idempotency_key is distinct from operation_key
       or d.action is distinct from 'confirm_publication'
       or d.status is distinct from 'confirmed_pending_publication_owner'
       or d.content_version_id is distinct from r.content_version_id
       or d.version_fingerprint is distinct from r.version_fingerprint then
        raise exception 'publication_handoff_binding_conflict' using errcode='23514';
    end if;
    -- A lost commit acknowledgement is resolved by the ORIGINAL IDs only.
    select * into existing_approval from public.approvals a
        where a.workspace_id=r.workspace_id and a.content_item_id=r.content_item_id
          and a.content_version_id=r.content_version_id
          and a.idempotency_key='content-ops-final:'||d.id::text;
    if found then
        if existing_approval.reviewer_source is distinct from 'telegram_principal'
           or existing_approval.review_principal_id is distinct from target_actor_id
           or existing_approval.decision is distinct from 'approved'
           or (select count(*) from private.content_ops_channel_handoffs h
               where h.decision_id=d.id and h.approval_id=existing_approval.id
                 and h.workspace_id=r.workspace_id
                 and h.content_version_id=r.content_version_id) <> 2 then
            raise exception 'publication_handoff_replay_conflict' using errcode='23505';
        end if;
        return jsonb_build_object('status','approved_handoff_unwired',
            'approval_id',existing_approval.id,'decision_id',d.id,
            'reused',true,'execution_authorized',false);
    end if;
    select * into c from private.content_ops_final_cards where id=d.card_id for share;
    select * into delivery from private.content_ops_final_card_deliveries
        where id=d.card_id for share;
    if c.id is null or delivery.id is null
       or c.review_id is distinct from r.id or c.actor_id is distinct from target_actor_id
       or c.epoch is distinct from r.epoch or r.state is distinct from 'active'
       or c.version_fingerprint is distinct from d.version_fingerprint
       or c.snapshot_sha256 is distinct from d.snapshot_sha256
       or delivery.release_sha is distinct from verified_runtime_release_sha
       or clock_timestamp()>=c.expires_at
       or (select count(*) from private.content_ops_final_card_parts p
           where p.delivery_id=c.id and p.state='confirmed')<>4 then
        raise exception 'publication_handoff_card_ineligible' using errcode='23514';
    end if;
    preflight:=private.content_ops_final_card_preflight(r.id,c.parent_card_id,
        target_actor_id,d.version_fingerprint,delivery.bot_binding,
        delivery.room_binding,delivery.human_binding);
    candidate:=private.content_ops_review_candidate(r.workspace_id,
        r.content_item_id,r.content_version_id);
    if preflight->>'status' is distinct from 'ready_for_final_card'
       or candidate is null
       or preflight->>'banner_sha256' is distinct from candidate->>'banner_sha256'
       or exists(select 1 from public.approvals a where a.workspace_id=r.workspace_id
            and a.content_item_id=r.content_item_id)
       or exists(select 1 from public.publications p where p.workspace_id=r.workspace_id
            and p.content_item_id=r.content_item_id)
       or exists(select 1 from public.jobs j where j.workspace_id=r.workspace_id
            and j.content_item_id=r.content_item_id and j.job_kind='publish')
       or exists(select 1 from private.content_ops_channel_handoffs h
            where h.workspace_id=r.workspace_id and h.content_item_id=r.content_item_id)
       or exists(select 1 from private.content_qa_jobs q
            where q.workspace_id=r.workspace_id
              and q.content_version_id=r.content_version_id and q.decision='BLOCK') then
        raise exception 'publication_handoff_candidate_ineligible' using errcode='23514';
    end if;
    select * into version from public.content_versions where workspace_id=r.workspace_id
        and content_item_id=r.content_item_id and id=r.content_version_id for share;
    if not private.has_valid_double_fact_check_report(version.generation_meta)
       or version.generation_meta->'fact_check'->>'content_kind' is distinct from 'daily_news'
       or length(version.channel_copy->>'telegram')>2000
       or length(version.channel_copy->>'x')>600 then
        raise exception 'publication_handoff_fact_check_or_copy_invalid' using errcode='23514';
    end if;
    perform private.require_content_ops_publication_routes(r.workspace_id,
        r.client_id,verified_runtime_release_sha,delivery.telegram_route_binding,
        delivery.typefully_route_binding);
    -- The approval and both PRIVATE, non-dispatchable channel intents commit
    -- atomically. They are not public.publications or worker-claimable jobs.
    insert into public.approvals(workspace_id,client_id,content_item_id,
        content_version_id,reviewer_id,review_principal_id,reviewer_source,
        decision,idempotency_key,fact_check_policy_version,
        source_facts_verified,output_claims_verified)
    values(r.workspace_id,r.client_id,r.content_item_id,r.content_version_id,
        null,target_actor_id,'telegram_principal','approved',
        'content-ops-final:'||d.id::text,'double-fact-check@1',true,true)
    returning id into created_approval_id;
    foreach channel_name in array array['telegram','typefully_x'] loop
        select * into route from private.content_ops_publication_routes p
            where p.workspace_id=r.workspace_id and p.client_id=r.client_id
              and p.channel=channel_name and p.active
              and p.release_sha=verified_runtime_release_sha for share;
        if not found then
            raise exception 'publication_handoff_route_changed' using errcode='23514';
        end if;
        copy_text:=version.channel_copy->>case when channel_name='telegram'
            then 'telegram' else 'x' end;
        insert into private.content_ops_channel_handoffs(decision_id,approval_id,
            workspace_id,client_id,content_item_id,content_version_id,channel,
            route_binding,version_fingerprint,banner_sha256,copy_sha256,release_sha)
        values(d.id,created_approval_id,r.workspace_id,r.client_id,r.content_item_id,
            r.content_version_id,channel_name,route.route_binding,
            d.version_fingerprint,candidate->>'banner_sha256',
            encode(sha256(convert_to(copy_text,'UTF8')),'hex'),
            verified_runtime_release_sha);
    end loop;
    update public.content_items set status='approved' where workspace_id=r.workspace_id
        and id=r.content_item_id and status='needs_review';
    if not found then
        raise exception 'publication_handoff_status_changed' using errcode='23514';
    end if;
    return jsonb_build_object('status','approved_handoff_unwired',
        'approval_id',created_approval_id,'decision_id',d.id,
        'reused',false,'execution_authorized',false);
end $$;

create function private.read_content_ops_publication_handoff_terminal(
    target_decision_id uuid,target_actor_id uuid,operation_key text
) returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare
    d private.content_ops_final_decisions;
    approval public.approvals;
begin
    if target_decision_id is null or target_actor_id is null
       or operation_key is null or operation_key !~ '^[a-f0-9]{64}$' then
        raise exception 'publication_handoff_terminal_arguments_invalid' using errcode='22023';
    end if;
    select * into d from private.content_ops_final_decisions where id=target_decision_id
        and actor_id=target_actor_id and idempotency_key=operation_key;
    if not found then
        return jsonb_build_object('status','not_recorded','execution_authorized',false);
    end if;
    select * into approval from public.approvals a
        where a.idempotency_key='content-ops-final:'||d.id::text
          and a.content_version_id=d.content_version_id
          and a.review_principal_id=target_actor_id;
    if not found or (select count(*) from private.content_ops_channel_handoffs h
        where h.decision_id=d.id and h.approval_id=approval.id)<>2 then
        return jsonb_build_object('status','not_recorded','execution_authorized',false);
    end if;
    return jsonb_build_object('status','approved_handoff_unwired',
        'decision_id',d.id,'approval_id',approval.id,'execution_authorized',false);
end $$;

revoke all on function private.materialize_content_ops_publication_handoff(
    uuid,uuid,text,text) from public,anon,authenticated,service_role;
revoke all on function private.read_content_ops_publication_handoff_terminal(
    uuid,uuid,text) from public,anon,authenticated,service_role;
commit;
