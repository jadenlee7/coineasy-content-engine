-- LOCAL-ONLY. Install after final-decision/handoff, multi-client Telegram and
-- Typefully final-review compatibility proposals. No grants or provider calls.
-- Only a NEW explicitly queue-mode private card can admit both channels.
begin;

create table private.content_ops_final_queue_receipts (
    decision_id uuid primary key references private.content_ops_final_decisions(id),
    approval_id uuid not null unique references public.approvals(id),
    publication_id uuid not null unique references public.publications(id),
    job_id uuid not null unique references public.jobs(id),
    typefully_slot_id uuid not null unique references private.typefully_daily_slots(id),
    created_at timestamptz not null default clock_timestamp()
);
alter table private.content_ops_final_queue_receipts enable row level security;
alter table private.content_ops_final_queue_receipts force row level security;
revoke all on private.content_ops_final_queue_receipts
    from public,anon,authenticated,service_role;
create trigger final_queue_receipts_immutable before update or delete
    on private.content_ops_final_queue_receipts for each row
    execute function private.guard_content_ops_final_card_ledger();

create function private.read_content_ops_final_publication_terminal(
    target_card_id uuid,target_actor_id uuid,operation_key text
) returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare
    d private.content_ops_final_decisions;
    receipt private.content_ops_final_queue_receipts;
begin
    if target_card_id is null or target_actor_id is null or operation_key is null
       or operation_key !~ '^[a-f0-9]{64}$' then
        raise exception 'final_publication_terminal_arguments_invalid' using errcode='22023';
    end if;
    select * into d from private.content_ops_final_decisions
        where card_id=target_card_id and actor_id=target_actor_id
          and idempotency_key=operation_key;
    if found and d.status='held' then
        return jsonb_build_object('status','held','decision_id',d.id,
            'approval_id',null,'publication_id',null,'job_id',null,
            'typefully_slot_id',null,'execution_authorized',false);
    end if;
    select * into receipt from private.content_ops_final_queue_receipts
        where decision_id=d.id;
    if not found then
        return jsonb_build_object('status','not_recorded','decision_id',null,
            'approval_id',null,'publication_id',null,'job_id',null,
            'typefully_slot_id',null,'execution_authorized',false);
    end if;
    -- Reconcile committed identities even after expiry/new source. This read
    -- performs no mutable candidate check and never authorizes another send.
    if not exists(select 1 from public.approvals a
        join private.content_ops_button_reviews r on r.id=d.review_id
        join public.publications p on p.id=receipt.publication_id
        join public.jobs j on j.id=receipt.job_id
        join private.typefully_daily_slots s on s.id=receipt.typefully_slot_id
        where a.id=receipt.approval_id and a.review_principal_id=d.actor_id
          and a.reviewer_source='telegram_principal'
          and a.workspace_id=r.workspace_id and a.content_item_id=r.content_item_id
          and a.content_version_id=d.content_version_id
          and p.workspace_id=r.workspace_id and p.content_item_id=r.content_item_id
          and p.content_version_id=d.content_version_id and p.channel='telegram'
          and p.request_payload->>'approval_id'=a.id::text
          and p.request_payload->>'content_ops_decision_id'=d.id::text
          and j.workspace_id=r.workspace_id and j.content_item_id=r.content_item_id
          and j.input->>'content_version_id'=d.content_version_id::text
          and j.input->>'publication_id'=p.id::text
          and j.input->>'content_ops_decision_id'=d.id::text
          and s.workspace_id=r.workspace_id and s.content_item_id=r.content_item_id
          and s.content_version_id=d.content_version_id and s.approval_id=a.id
          and (select count(*) from private.content_ops_channel_handoffs h
              where h.decision_id=d.id and h.approval_id=a.id
                and h.workspace_id=r.workspace_id
                and h.content_item_id=r.content_item_id
                and h.content_version_id=d.content_version_id)=2) then
        raise exception 'final_publication_terminal_inconsistent' using errcode='23514';
    end if;
    return jsonb_build_object('status','channels_queued','decision_id',d.id,
        'approval_id',receipt.approval_id,'publication_id',receipt.publication_id,
        'job_id',receipt.job_id,'typefully_slot_id',receipt.typefully_slot_id,
        'execution_authorized',false);
end $$;

create function private.record_content_ops_final_publication(
    target_card_id uuid,target_actor_id uuid,target_content_version_id uuid,
    expected_fingerprint text,expected_snapshot_sha256 text,
    verified_control_message_binding text,verified_bot_binding text,
    verified_room_binding text,verified_human_binding text,
    verified_runtime_release_sha text,requested_action text,operation_key text,
    verified_callback_release_sha text,verified_release_pair_sha256 text
) returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare
    d private.content_ops_final_card_deliveries;
    r private.content_ops_button_reviews;
    v public.content_versions;
    decision jsonb;
    handoff jsonb;
    queue jsonb;
    terminal jsonb;
    slot_id uuid;
    pins jsonb;
    tg_units integer;
begin
    select * into d from private.content_ops_final_card_deliveries
        where id=target_card_id;
    if not found or d.decision_mode is distinct from 'telegram_and_typefully_draft' then
        raise exception 'final_publication_explicit_queue_mode_required' using errcode='23514';
    end if;
    select * into r from private.content_ops_button_reviews where id=d.review_id;
    perform 1 from public.content_items where workspace_id=r.workspace_id
        and id=r.content_item_id for update;
    -- The decision function authenticates all stored bindings, exact key,
    -- action and mode-bound snapshot. Its exact replay does not refresh them.
    decision:=private.record_content_ops_final_decision(target_card_id,
        target_actor_id,target_content_version_id,expected_fingerprint,
        expected_snapshot_sha256,verified_control_message_binding,
        verified_bot_binding,verified_room_binding,verified_human_binding,
        verified_runtime_release_sha,requested_action,operation_key,
        verified_callback_release_sha,verified_release_pair_sha256);
    terminal:=private.read_content_ops_final_publication_terminal(
        target_card_id,target_actor_id,operation_key);
    if terminal->>'status' in ('held','channels_queued') then
        return terminal || jsonb_build_object('reused',
            case when terminal->>'status'='held' then (decision->>'reused')::boolean
            else true end);
    end if;
    perform private.require_content_ops_release_pair(r.workspace_id,r.client_id,
        verified_runtime_release_sha,verified_callback_release_sha,verified_release_pair_sha256,
        d.created_at);
    select * into v from public.content_versions where workspace_id=r.workspace_id
        and content_item_id=r.content_item_id and id=target_content_version_id;
    select coalesce(sum(case when ascii(ch)>65535 then 2 else 1 end),0)::integer
        into tg_units from regexp_split_to_table(v.channel_copy->>'telegram','') ch;
    if tg_units not between 1 and 1024
       or length(v.channel_copy->>'x') not between 1 and 280 then
        raise exception 'final_publication_channel_copy_invalid' using errcode='23514';
    end if;
    handoff:=private.materialize_content_ops_publication_handoff(
        (decision->>'decision_id')::uuid,target_actor_id,
        verified_runtime_release_sha,operation_key);
    queue:=public.request_studio_telegram_publication(r.workspace_id,
        r.content_item_id,target_content_version_id,decision->>'decision_id');
    if queue->>'status' is distinct from 'queued' or queue->'reused' is distinct from 'false'::jsonb
       or queue->>'delivery_started_at' is not null then
        raise exception 'final_publication_queue_conflict' using errcode='23514';
    end if;
    pins:=jsonb_build_object('content_ops_decision_id',decision->>'decision_id',
        'telegram_route_binding',d.telegram_route_binding,
        'content_ops_release_sha',verified_runtime_release_sha);
    update public.jobs set input=input||pins where id=(queue->>'job_id')::uuid
        and workspace_id=r.workspace_id and content_item_id=r.content_item_id
        and status='queued' and input->>'approval_id'=handoff->>'approval_id';
    if not found then raise exception 'final_publication_job_conflict' using errcode='23514';end if;
    update public.publications set request_payload=request_payload||pins
        where id=(queue->>'publication_id')::uuid and workspace_id=r.workspace_id
          and content_item_id=r.content_item_id and status='queued'
          and request_payload->>'approval_id'=handoff->>'approval_id';
    if not found then raise exception 'final_publication_receipt_conflict' using errcode='23514';end if;
    slot_id:=private.reserve_content_ops_typefully_slot(
        (decision->>'decision_id')::uuid,target_actor_id,verified_runtime_release_sha);
    if slot_id is null then
        raise exception 'final_publication_typefully_slot_missing' using errcode='23514';
    end if;
    insert into private.content_ops_final_queue_receipts(decision_id,approval_id,
        publication_id,job_id,typefully_slot_id)
    values((decision->>'decision_id')::uuid,(handoff->>'approval_id')::uuid,
        (queue->>'publication_id')::uuid,(queue->>'job_id')::uuid,slot_id);
    return private.read_content_ops_final_publication_terminal(
        target_card_id,target_actor_id,operation_key)||jsonb_build_object('reused',false);
end $$;
revoke all on function private.record_content_ops_final_publication(
    uuid,uuid,uuid,text,text,text,text,text,text,text,text,text,text,text)
    from public,anon,authenticated,service_role;
revoke all on function private.read_content_ops_final_publication_terminal(uuid,uuid,text)
    from public,anon,authenticated,service_role;
commit;
