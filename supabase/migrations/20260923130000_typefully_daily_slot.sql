-- Approval-first Typefully daily draft selection. This installs no cron,
-- provider call, Telegram send, schedule, or public X publication path.
begin;

create table private.typefully_daily_slots (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null check (client_id in ('yellow','origintrail','squid','babylon')),
    kst_date date not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    approval_id uuid not null references public.approvals(id) on delete restrict,
    selected_at timestamptz not null default statement_timestamp(),
    unique (workspace_id, client_id, kst_date),
    unique (workspace_id, content_item_id),
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id) on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id) on delete restrict
);

alter table private.typefully_daily_slots enable row level security;
alter table private.typefully_daily_slots force row level security;
revoke all on table private.typefully_daily_slots
    from public, anon, authenticated, service_role;

-- A replay returns the same exact IDs. Selection writes a single private slot
-- only after the existing candidate projection has checked current approval,
-- source, feed, canonical PNG, and publication absence. Downstream workers
-- repeat mutable checks before either provider POST.
create function public.claim_typefully_daily_slot(
    target_workspace_id uuid, target_client_id text
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    today_kst date := (statement_timestamp() at time zone 'Asia/Seoul')::date;
    slot private.typefully_daily_slots%rowtype;
    selected record;
    reused boolean := false;
begin
    if current_setting('request.jwt.claim.role', true) is distinct from 'service_role' then
        raise exception 'typefully_service_role_required' using errcode = '42501';
    end if;
    if target_workspace_id is null
       or target_client_id is null
       or target_client_id not in ('yellow','origintrail','squid','babylon') then
        raise exception 'typefully_daily_client_invalid' using errcode = '23514';
    end if;
    select candidate.* into slot from private.typefully_daily_slots as candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.kst_date = today_kst;
    if found then
        reused := true;
    else
        select item.id as content_item_id,
               item.current_version_id as content_version_id,
               approval.id as approval_id
        into selected
        from public.content_items as item
        join public.approvals as approval
          on approval.workspace_id = item.workspace_id
         and approval.client_id = item.client_id
         and approval.content_item_id = item.id
         and approval.content_version_id = item.current_version_id
        where item.workspace_id = target_workspace_id
          and item.client_id = target_client_id
          and item.content_kind = 'daily_news'
          and item.status = 'approved'
          and approval.decision = 'approved'
          and (approval.created_at at time zone 'Asia/Seoul')::date = today_kst
          and public.get_typefully_draft_candidate(
              item.workspace_id, item.id, item.current_version_id, approval.id
          ) is not null
          and not exists (select 1 from private.typefully_media_allocation_attempts as media
              where media.workspace_id = item.workspace_id
                and media.content_item_id = item.id)
          and not exists (select 1 from private.typefully_draft_attempts as draft
              where draft.workspace_id = item.workspace_id
                and draft.content_item_id = item.id)
        order by approval.created_at desc, approval.id desc
        limit 1;
        if not found then return null; end if;
        insert into private.typefully_daily_slots (
            workspace_id, client_id, kst_date, content_item_id,
            content_version_id, approval_id
        ) values (
            target_workspace_id, target_client_id, today_kst,
            selected.content_item_id, selected.content_version_id,
            selected.approval_id
        ) on conflict (workspace_id, client_id, kst_date) do nothing
        returning * into slot;
        if not found then
            select candidate.* into slot from private.typefully_daily_slots as candidate
            where candidate.workspace_id = target_workspace_id
              and candidate.client_id = target_client_id
              and candidate.kst_date = today_kst;
            reused := true;
        end if;
    end if;
    if slot.id is null then
        raise exception 'typefully_daily_slot_unknown' using errcode = '23514';
    end if;
    return jsonb_build_object(
        'slot_id', slot.id, 'workspace_id', slot.workspace_id,
        'client_id', slot.client_id, 'kst_date', slot.kst_date,
        'content_item_id', slot.content_item_id,
        'content_version_id', slot.content_version_id,
        'approval_id', slot.approval_id,
        'reused', reused
    );
end;
$$;

revoke all on function public.claim_typefully_daily_slot(uuid,text)
    from public, anon, authenticated, service_role;
grant execute on function public.claim_typefully_daily_slot(uuid,text)
    to service_role;

commit;
