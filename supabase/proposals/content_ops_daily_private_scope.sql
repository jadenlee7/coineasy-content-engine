-- LOCAL PROPOSAL. Apply only after exact hosted schema/ACL verification.
-- Initial PRIVATE card discovery only. No jobs, approvals, public queue,
-- callback grants, provider calls, schedule or production enablement.
-- Depends on the latest content_ops_review_candidate/reconcile/claim contracts.
begin;

create function private.content_ops_ybo_quality_version(target_workspace_id uuid, target_version_id uuid)
returns boolean language sql stable security definer set search_path = '' as $$
    select exists (
        select 1 from public.content_versions v
        join public.content_items i on i.id=v.content_item_id and i.workspace_id=v.workspace_id
            and i.current_version_id=v.id
        join public.assets a on a.id::text=v.deliverables->>'primary_asset_id'
            and a.workspace_id=v.workspace_id and a.content_item_id=i.id and a.content_version_id=v.id
        where v.workspace_id=target_workspace_id and v.id=target_version_id
            and i.client_id in ('yellow','babylon','origintrail') and i.status='needs_review'
            and v.prompt_version='official-reference-sunburst-max@2'
            and v.generation_meta->>'renderer'='higgsfield-sunburst-max'
            and v.generation_meta->'quality_banner'->>'policy'='official-reference-sunburst-max@2'
            and v.generation_meta->'quality_banner'->>'request_sha256' ~ '^[a-f0-9]{64}$'
            and v.generation_meta->'quality_banner'->>'png_sha256'=a.sha256
            and v.generation_meta->'quality_banner'->>'width'=a.width::text
            and v.generation_meta->'quality_banner'->>'height'=a.height::text
            and a.asset_kind='png' and a.mime_type='image/png'
            and a.width between 2880 and 4096 and a.height=a.width
            and a.byte_size between 9 and 10000000 and a.sha256 ~ '^[a-f0-9]{64}$'
    );
$$;

create function public.content_ops_reconcile_daily_ybo(target_workspace_id uuid)
returns integer language plpgsql volatile security definer set search_path = '' as $$
declare candidate record; inserted integer:=0;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'content_ops_service_role_required' using errcode='42501'; end if;
    if target_workspace_id is null then
        raise exception 'content_ops_workspace_required' using errcode='22023'; end if;
    -- Only exact versions from real current daily producer slots. The existing
    -- reconciler rechecks complete job/request/output/source/canonical binding
    -- under its own eligibility locks; no succeeded job is manufactured here.
    for candidate in
        select i.current_version_id as version_id
        from private.official_x_daily_slots s
        join public.jobs j on j.id=s.job_id and j.workspace_id=s.workspace_id and j.client_id=s.client_id
        join public.content_items i on i.id::text=j.output->>'content_item_id'
            and i.workspace_id=s.workspace_id and i.client_id=s.client_id
        where s.workspace_id=target_workspace_id
            and s.kst_date=pg_catalog.timezone('Asia/Seoul',statement_timestamp())::date
            and s.client_id in ('yellow','babylon','origintrail')
            and j.job_kind='generate' and j.status='succeeded'
            and j.input->>'workflow'='official_x_review_draft_v1'
            and private.content_ops_ybo_quality_version(target_workspace_id,i.current_version_id)
        order by s.client_id limit 3
    loop
        inserted:=inserted+public.content_ops_reconcile_daily(target_workspace_id,candidate.version_id);
    end loop;
    return inserted;
end $$;

create function public.content_ops_claim_daily_ybo(target_workspace_id uuid, target_claim_token uuid)
returns jsonb language plpgsql volatile security definer set search_path = '' as $$
declare candidate record; card jsonb;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'content_ops_service_role_required' using errcode='42501'; end if;
    if target_workspace_id is null or target_claim_token is null then
        raise exception 'content_ops_claim_invalid' using errcode='22023'; end if;
    -- Match the delegated claim lock order (token, then outbox). An unknown
    -- previous response remains owned, never reissued after process restart.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'content-ops-claim:'||target_claim_token::text,0));
    if exists(select 1 from private.content_ops_review_outbox where claim_token=target_claim_token) then
        return null; end if;
    for candidate in
        select q.content_version_id from private.content_ops_review_outbox q
        where q.workspace_id=target_workspace_id and q.status='pending'
            and q.kst_date=pg_catalog.timezone('Asia/Seoul',statement_timestamp())::date
            and q.client_id in ('yellow','babylon','origintrail')
            and private.content_ops_ybo_quality_version(target_workspace_id,q.content_version_id)
        order by q.created_at,q.outbox_id limit 3 for update of q skip locked
    loop
        card:=public.content_ops_claim_review(target_workspace_id,target_claim_token,candidate.content_version_id);
        if card is not null then return card; end if;
    end loop;
    return null;
end $$;

revoke all on function private.content_ops_ybo_quality_version(uuid,uuid) from public,anon,authenticated,service_role;
revoke all on function public.content_ops_reconcile_daily_ybo(uuid) from public,anon,authenticated,service_role;
revoke all on function public.content_ops_claim_daily_ybo(uuid,uuid) from public,anon,authenticated,service_role;
grant execute on function public.content_ops_reconcile_daily_ybo(uuid) to service_role;
grant execute on function public.content_ops_claim_daily_ybo(uuid,uuid) to service_role;
commit;
