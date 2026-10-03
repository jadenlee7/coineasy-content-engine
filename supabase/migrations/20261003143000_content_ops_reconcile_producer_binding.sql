-- Enumerate the natural producer's immutable request/output binding before
-- passing every candidate through the existing locked eligibility helper.
-- No job backfill, new privilege, delivery, enablement or public scope change.
begin;

create or replace function public.content_ops_reconcile_daily(
    target_workspace_id uuid, target_content_version_id uuid default null
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    candidate_item record;
    card jsonb;
    inserted integer := 0;
    affected_rows integer;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'content_ops_service_role_required' using errcode = '42501';
    end if;
    if target_workspace_id is null then
        raise exception 'content_ops_workspace_required' using errcode = '22023';
    end if;
    update private.content_ops_review_outbox as queued
    set status = 'delivery_unknown', finished_at = clock_timestamp()
    where queued.workspace_id = target_workspace_id and queued.status in ('claimed', 'sending')
      and (target_content_version_id is null or queued.content_version_id = target_content_version_id)
      and queued.lease_expires_at <= clock_timestamp();
    update private.content_ops_review_outbox as queued
    set status = 'obsolete', finished_at = clock_timestamp()
    where queued.workspace_id = target_workspace_id and queued.status = 'pending'
      and (target_content_version_id is null or queued.content_version_id = target_content_version_id)
      and queued.kst_date <> pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date;
    -- At most four current daily slot owners. A guarded canonical UUID cast
    -- preserves the item primary-key lookup; malformed input maps to NULL.
    for candidate_item in
        select item.id, item.current_version_id
        from private.official_x_daily_slots as slot
        join public.jobs as job on job.id = slot.job_id and job.workspace_id = slot.workspace_id
          and job.client_id = slot.client_id
        join public.content_items as item
          on item.id = case
              when job.input ->> 'request_id' ~
                '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
              then (job.input ->> 'request_id')::uuid
              else null
            end
          and item.id::text = job.output ->> 'content_item_id'
          and item.current_version_id::text = job.output ->> 'content_version_id'
          and (job.content_item_id is null or job.content_item_id = item.id)
          and item.workspace_id = slot.workspace_id and item.client_id = slot.client_id
        where slot.workspace_id = target_workspace_id
          and slot.kst_date = pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date
          and slot.client_id in ('yellow', 'origintrail', 'squid', 'babylon')
          and job.job_kind = 'generate' and job.status = 'succeeded'
          and job.input ->> 'workflow' = 'official_x_review_draft_v1'
          and (target_content_version_id is null or item.current_version_id = target_content_version_id)
          and not exists (select 1 from private.content_ops_review_outbox as queued
            where queued.workspace_id = slot.workspace_id and queued.client_id = slot.client_id
              and queued.kst_date = slot.kst_date and queued.destination_role = 'content_ops_private')
        order by slot.client_id limit 4
    loop
        card := private.content_ops_review_candidate(
            target_workspace_id, candidate_item.id, candidate_item.current_version_id);
        if card is null or (target_content_version_id is not null
            and card ->> 'content_version_id' is distinct from target_content_version_id::text) then
            continue;
        end if;
        insert into private.content_ops_review_outbox (
            workspace_id, client_id, kst_date, content_item_id, content_version_id,
            source_item_id, generate_job_id, banner_asset_id, banner_sha256,
            source_url, source_published_at
        ) values (
            target_workspace_id, card ->> 'client_id', (card ->> 'kst_date')::date,
            (card ->> 'content_item_id')::uuid, (card ->> 'content_version_id')::uuid,
            (card ->> 'source_item_id')::uuid, (card ->> 'generate_job_id')::uuid,
            (card ->> 'banner_asset_id')::uuid, card ->> 'banner_sha256',
            card ->> 'source_url', (card ->> 'source_published_at')::timestamptz
        ) on conflict do nothing;
        get diagnostics affected_rows = row_count;
        inserted := inserted + affected_rows;
    end loop;
    return inserted;
end;
$$;

revoke all on function public.content_ops_reconcile_daily(uuid,uuid)
    from public, anon, authenticated;
grant execute on function public.content_ops_reconcile_daily(uuid,uuid) to service_role;
commit;
