-- LOCAL-ONLY PROPOSAL. Never reapply/edit historical Typefully migrations.
-- Dependencies: both Typefully migrations, final approval attribution, final
-- decision/card/handoff/routes proposals, and the exact TG annotation contract.
-- No role grant, schedule, activation, route seed, approval, or provider call.
begin;
set local lock_timeout='2s';
set local statement_timeout='15s';

create function private.content_ops_typefully_route_binding(
    w uuid,c text,release text,social_set bigint
) returns text language sql immutable security invoker set search_path='' as $$
    select case when w is not null and c in ('yellow','origintrail','squid','babylon')
        and release ~ '^[a-f0-9]{40}$' and social_set between 1 and 9007199254740991
    then encode(sha256(convert_to(format(
        '{"action":"x_draft_only","client_id":%s,"provider":"typefully_x","release_sha":%s,"schema":"content-ops-publication-route@1","social_set_id":%s,"username":%s,"workspace_id":%s}',
        to_json(c)::text,to_json(release)::text,social_set,
        to_json(case c when 'yellow' then 'yellow__korea'
            when 'origintrail' then 'origin_trail_kr' when 'squid' then 'squidkorea'
            when 'babylon' then 'babylonkorean' end)::text,to_json(w::text)::text
    ),'UTF8')),'hex') end
$$;

-- A reviewer_source string alone is never attribution. Only the durable human
-- decision, two matching immutable handoffs, and fresh active routes qualify.
create function private.typefully_final_approval_provenance(a public.approvals)
returns jsonb language sql stable security invoker set search_path='' as $$
    select jsonb_build_object('schema_version','content-ops-final-approval@1',
        'decision_id',d.id,'review_principal_id',d.actor_id,'release_sha',d.release_sha,
        'version_fingerprint',d.version_fingerprint,'snapshot_sha256',d.snapshot_sha256,
        'banner_sha256',hx.banner_sha256,'x_copy_sha256',hx.copy_sha256,
        'telegram_copy_sha256',ht.copy_sha256,'typefully_route_binding',hx.route_binding,
        'telegram_route_binding',ht.route_binding,
        'route_verified_at',least(rx.verified_at,rt.verified_at))
    from public.content_items i
    join public.content_versions v on v.workspace_id=i.workspace_id
        and v.content_item_id=i.id and v.id=i.current_version_id
    join public.assets banner on banner.workspace_id=v.workspace_id
        and banner.content_item_id=v.content_item_id and banner.content_version_id=v.id
        and banner.id::text=v.deliverables->>'primary_asset_id'
    join private.content_ops_final_decisions d
        on (a).idempotency_key='content-ops-final:'||d.id::text
        and d.actor_id=(a).review_principal_id and d.content_version_id=v.id
        and d.action='confirm_publication'
        and d.status='confirmed_pending_publication_owner'
    join private.content_ops_review_principals principal on principal.id=d.actor_id
        and principal.workspace_id=i.workspace_id
    join private.content_ops_button_reviews review on review.id=d.review_id
        and review.workspace_id=i.workspace_id and review.client_id=i.client_id
        and review.content_item_id=i.id and review.content_version_id=v.id
        and review.version_fingerprint=d.version_fingerprint and review.state='active'
    join private.content_ops_final_cards card on card.id=d.card_id
        and card.review_id=review.id and card.actor_id=d.actor_id
        and card.epoch=review.epoch
        and card.version_fingerprint=d.version_fingerprint
        and card.snapshot_sha256=d.snapshot_sha256
    join private.content_ops_final_card_deliveries delivery on delivery.id=card.id
        and delivery.review_id=review.id and delivery.actor_id=d.actor_id
        and delivery.release_sha=d.release_sha and delivery.epoch=card.epoch
        and delivery.parent_card_id=card.parent_card_id
        and delivery.snapshot_sha256=card.snapshot_sha256
        and delivery.version_fingerprint=card.version_fingerprint
        and delivery.decision_mode='telegram_and_typefully_draft'
    join private.content_ops_button_identities identity on identity.workspace_id=i.workspace_id
        and identity.actor_id=principal.id and identity.active
        and identity.bot_binding=principal.bot_binding and identity.human_binding=principal.human_binding
        and identity.bot_binding=delivery.bot_binding and identity.human_binding=delivery.human_binding
    join private.content_ops_button_reviewers reviewer on reviewer.workspace_id=i.workspace_id
        and reviewer.client_id=i.client_id and reviewer.actor_id=principal.id and reviewer.active
    join private.content_ops_channel_handoffs hx on hx.decision_id=d.id
        and hx.approval_id=(a).id and hx.channel='typefully_x'
        and hx.workspace_id=i.workspace_id and hx.client_id=i.client_id
        and hx.content_item_id=i.id and hx.content_version_id=v.id
        and hx.version_fingerprint=d.version_fingerprint and hx.release_sha=d.release_sha
        and hx.status='awaiting_channel_owner'
        and hx.banner_sha256=banner.sha256
        and hx.copy_sha256=encode(sha256(convert_to(v.channel_copy->>'x','UTF8')),'hex')
    join private.content_ops_channel_handoffs ht on ht.decision_id=d.id
        and ht.approval_id=(a).id and ht.channel='telegram'
        and ht.workspace_id=i.workspace_id and ht.client_id=i.client_id
        and ht.content_item_id=i.id and ht.content_version_id=v.id
        and ht.version_fingerprint=d.version_fingerprint and ht.release_sha=d.release_sha
        and ht.status='awaiting_channel_owner'
        and ht.banner_sha256=banner.sha256
        and ht.copy_sha256=encode(sha256(convert_to(v.channel_copy->>'telegram','UTF8')),'hex')
    join private.content_ops_publication_routes rx on rx.workspace_id=i.workspace_id
        and rx.client_id=i.client_id and rx.channel='typefully_x' and rx.active
        and rx.release_sha=d.release_sha and rx.route_binding=hx.route_binding
    join private.content_ops_publication_routes rt on rt.workspace_id=i.workspace_id
        and rt.client_id=i.client_id and rt.channel='telegram' and rt.active
        and rt.release_sha=d.release_sha and rt.route_binding=ht.route_binding
    where i.workspace_id=(a).workspace_id and i.id=(a).content_item_id
        and i.client_id=(a).client_id and v.id=(a).content_version_id
        and (a).reviewer_source='telegram_principal' and (a).reviewer_id is null
        and (a).decision='approved' and (a).fact_check_policy_version='double-fact-check@1'
        and (a).source_facts_verified and (a).output_claims_verified
        and (a).review_sequence=(select max(latest.review_sequence) from public.approvals latest
            where latest.workspace_id=i.workspace_id and latest.content_item_id=i.id)
        and d.version_fingerprint=private.content_ops_button_version_fingerprint(i.workspace_id,i.id,v.id)
        and rx.verified_at between statement_timestamp()-interval '15 minutes' and statement_timestamp()
        and rt.verified_at between statement_timestamp()-interval '15 minutes' and statement_timestamp()
        and (select count(*) from private.content_ops_final_card_parts p
             where p.delivery_id=card.id and p.state='confirmed')=4
$$;

create function private.typefully_approval_attribution_valid(a public.approvals)
returns boolean language sql stable security invoker set search_path='' as $$
    select coalesce(((a).reviewer_source='studio_session' and (a).reviewer_id is null
            and (a).review_principal_id is null)
        or ((a).reviewer_source='telegram_principal'
            and private.typefully_final_approval_provenance(a) is not null),false)
$$;

create function private.require_typefully_approval_route(
    target_approval_id uuid,expected_social_set_id bigint
) returns void language plpgsql volatile security invoker set search_path='' as $$
declare a public.approvals; proof jsonb;
begin
    select * into a from public.approvals where id=target_approval_id;
    if not found then raise exception 'typefully_final_approval_invalid' using errcode='23514';end if;
    if a.reviewer_source='studio_session' then return;end if;
    -- Item is already locked by each mutating reservation. Prevent permission
    -- revocation racing the proof check and durable attempt marker.
    perform 1 from private.content_ops_button_reviews r
        join private.content_ops_final_decisions d on d.review_id=r.id
        where a.idempotency_key='content-ops-final:'||d.id::text for share of r;
    perform 1 from private.content_ops_button_identities identity
        where identity.workspace_id=a.workspace_id and identity.actor_id=a.review_principal_id
        for share;
    perform 1 from private.content_ops_button_reviewers reviewer
        where reviewer.workspace_id=a.workspace_id and reviewer.client_id=a.client_id
          and reviewer.actor_id=a.review_principal_id for share;
    perform 1 from private.content_ops_publication_routes r
        where r.workspace_id=a.workspace_id and r.client_id=a.client_id
        order by r.channel for share;
    proof:=private.typefully_final_approval_provenance(a);
    if proof is null or expected_social_set_id is null
       or proof->>'typefully_route_binding' is distinct from
          private.content_ops_typefully_route_binding(a.workspace_id,a.client_id,
              proof->>'release_sha',expected_social_set_id) then
        raise exception 'typefully_final_route_invalid' using errcode='23514';
    end if;
end $$;

-- Only one SAME-version exact TG publication is compatible. Unknown, cancelled,
-- foreign/public X, legacy, wrong route/approval/asset, or extra jobs block X.
create function private.typefully_telegram_coexistence_valid(
    w uuid,i uuid,v uuid,approval_id uuid
) returns boolean language plpgsql stable security invoker set search_path='' as $$
declare a public.approvals; item public.content_items; p public.publications;
    job public.jobs; proof jsonb; asset uuid; expected_tg text;
begin
    select * into item from public.content_items where workspace_id=w and id=i;
    if not found or item.current_version_id is distinct from v then return false;end if;
    if not exists(select 1 from public.publications z where z.workspace_id=w and z.content_item_id=i)
       and not exists(select 1 from public.jobs z where z.workspace_id=w
           and z.content_item_id=i and z.job_kind='publish') then
        return item.status='approved';
    end if;
    select * into a from public.approvals where id=approval_id and workspace_id=w
        and content_item_id=i and content_version_id=v;
    proof:=private.typefully_final_approval_provenance(a);
    if proof is null or (select count(*) from public.publications z
        where z.workspace_id=w and z.content_item_id=i)<>1
       or (select count(*) from public.jobs z where z.workspace_id=w
            and z.content_item_id=i and z.job_kind='publish')<>1 then return false;end if;
    select * into p from public.publications where workspace_id=w and content_item_id=i;
    select * into job from public.jobs where workspace_id=w and content_item_id=i and job_kind='publish';
    select banner.id into asset from public.assets banner join public.content_versions version
        on version.id=v and version.workspace_id=w and version.content_item_id=i
        where banner.workspace_id=w and banner.content_item_id=i and banner.content_version_id=v
          and banner.id::text=version.deliverables->>'primary_asset_id';
    expected_tg:=case item.client_id when 'yellow' then 'yellowkorea_ann'
        when 'origintrail' then 'origintrailkr' when 'squid' then 'squid_kor_update'
        when 'babylon' then 'babylonbtc' end;
    return coalesce(p.client_id=item.client_id and p.content_version_id=v and p.channel='telegram'
        and p.status in ('queued','publishing','published')
        and ((p.status='queued' and job.status in ('queued','retrying'))
             or (p.status='publishing' and job.status='running')
             or (p.status='published' and job.status='succeeded'))
        and p.request_payload->>'workflow'='exact_telegram_publication_v1'
        and p.request_payload->>'approval_id'=a.id::text
        and p.request_payload->>'asset_id'=asset::text
        and p.request_payload#>>'{asset_snapshot,asset_id}'=asset::text
        and p.request_payload#>>'{asset_snapshot,sha256}'=proof->>'banner_sha256'
        and p.request_payload->>'content_ops_decision_id'=proof->>'decision_id'
        and p.request_payload->>'telegram_route_binding'=proof->>'telegram_route_binding'
        and p.request_payload->>'content_ops_release_sha'=proof->>'release_sha'
        and p.request_payload->>'request_idempotency_key'=proof->>'decision_id'
        and job.idempotency_key=proof->>'decision_id'
        and job.client_id=item.client_id
        and job.input->>'workflow'='exact_telegram_publication_v1'
        and job.input->>'publication_id'=p.id::text and job.input->>'channel'='telegram'
        and job.input->>'content_version_id'=v::text and job.input->>'approval_id'=a.id::text
        and job.input->>'asset_id'=asset::text
        and job.input#>>'{asset_snapshot,asset_id}'=asset::text
        and job.input#>>'{asset_snapshot,sha256}'=proof->>'banner_sha256'
        and job.input->>'content_ops_decision_id'=proof->>'decision_id'
        and job.input->>'telegram_route_binding'=proof->>'telegram_route_binding'
        and job.input->>'content_ops_release_sha'=proof->>'release_sha'
        and (item.status='approved' or (item.status='published' and p.status='published'))
        and (p.status<>'published' or p.external_url ~ ('^https://t\.me/'||expected_tg||'/[1-9][0-9]*$')),
        false);
end $$;

-- Guard exact installed old bodies, then preserve all unrelated validations,
-- RPC signatures/ACLs and exactly-once state transitions. No historical edit.
do $upgrade$
declare sig text; expected text; body text; old text; new text;
begin
    for sig,expected in select * from (values
      ('public.get_typefully_draft_candidate(uuid,uuid,uuid,uuid)',
       '2e6a62a1d990b89a1a54b2ea10346ce24a85954b46fbe8f27330d8e3120d7a52'),
      ('private.reserve_typefully_draft_once(uuid,uuid,uuid,uuid,uuid,bigint,text,timestamptz,timestamptz,text)',
       '5dbb1cc35dafef6e636183fc80e346213cd8b8382caa5b12b4380dc603ae3da4'),
      ('private.reserve_typefully_media_allocation_once(uuid,uuid,uuid,uuid,bigint,text,timestamptz)',
       'da4cf1953b6a3f3b70dd6cc4eb4afceea9dee6e2547d2ca38db0f55eaf512616'),
      ('private.record_typefully_media_upload(uuid,uuid,uuid,uuid,uuid,text,bigint,uuid)',
       'e7877c8e9f4c4146ca3d3a052fff4ceeb868e0854f6290897ac6d0c73631bcc5'),
      ('public.claim_typefully_daily_slot(uuid,text)',
       '508a928f076ff6addfcb379db12b4efec7a8795c3b3d83a67b631fd637e60167')
    ) pinned(signature,sha) loop
        if not exists(select 1 from pg_proc p join pg_roles r on r.oid=p.proowner
            where p.oid=to_regprocedure(sig) and p.prosecdef
              and p.proconfig @> array['search_path=""']
              and (r.rolsuper or r.rolbypassrls)
              and encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')=expected) then
            raise exception 'typefully_final_baseline_drift';
        end if;
        body:=pg_get_functiondef(to_regprocedure(sig));
        if sig like 'public.claim_typefully_daily_slot%' then
            body:=replace(body,'and item.status = ''approved''','and item.status in (''approved'',''published'')');
        elsif sig not like 'private.reserve_typefully_media_allocation_once%' then
            body:=replace(body,'item.status is distinct from ''approved''','item.status not in (''approved'',''published'')');
        end if;
        if sig like 'public.get_typefully_draft_candidate%' or sig like 'private.reserve_typefully_draft_once%' then
            body:=replace(body,'or approval.reviewer_source is distinct from ''studio_session''',
                'or private.typefully_approval_attribution_valid(approval) is not true');
            if sig like 'public.get_typefully_draft_candidate%' then
                old:=$old$or exists (select 1 from public.publications as publication
           where publication.workspace_id = target_workspace_id
             and publication.content_item_id = item.id)$old$;
                new:='or private.typefully_telegram_coexistence_valid(target_workspace_id,item.id,target_content_version_id,target_approval_id) is not true';
                if strpos(body,old)=0 then raise exception 'typefully_final_patch_missing';end if;
                body:=replace(body,old,new);
                body:=replace(body,$old$'approval_id', approval.id, 'version_created_at', version.created_at,$old$,
                    $new$'approval_id', approval.id, 'version_created_at', version.created_at,
        'reviewer_source', approval.reviewer_source,
        'telegram_principal_approval', private.typefully_final_approval_provenance(approval),$new$);
                old:=$old$    if current_setting('request.jwt.claim.role', true) is distinct from 'service_role' then
        raise exception 'typefully_service_role_required' using errcode = '42501';
    end if;
$old$;
                if strpos(body,old)=0 then raise exception 'typefully_final_auth_patch_missing';end if;
                body:=replace(body,old,'');
                body:=replace(body,'FUNCTION public.get_typefully_draft_candidate(',
                    'FUNCTION private.typefully_draft_candidate_owner(');
                body:=replace(body,'SECURITY DEFINER','SECURITY INVOKER');
            else
                old:=$old$if exists (select 1 from public.publications as publication
        where publication.workspace_id = target_workspace_id
          and publication.content_item_id = item.id) then$old$;
                new:='if private.typefully_telegram_coexistence_valid(target_workspace_id,item.id,target_content_version_id,target_approval_id) is not true then';
                if strpos(body,old)=0 then raise exception 'typefully_final_patch_missing';end if;
                body:=replace(body,old,new);
                body:=replace(body,'    select candidate.* into receipt from private.typefully_media_upload_receipts as candidate',
                    '    perform private.require_typefully_approval_route(target_approval_id,expected_social_set_id);'||chr(10)||
                    '    select candidate.* into receipt from private.typefully_media_upload_receipts as candidate');
            end if;
        elsif sig like 'private.reserve_typefully_media_allocation_once%' then
            body:=replace(body,'    candidate := public.get_typefully_draft_candidate(',
                '    perform private.require_typefully_approval_route(target_approval_id,expected_social_set_id);'||chr(10)||
                '    candidate := public.get_typefully_draft_candidate(');
        elsif sig like 'private.record_typefully_media_upload%' then
            body:=replace(body,'    select candidate.* into banner from public.assets as candidate',
                '    if private.typefully_draft_candidate_owner(target_workspace_id,target_content_item_id,target_content_version_id,allocation.approval_id) is null then'||chr(10)||
                '        raise exception ''typefully_final_approval_invalid'' using errcode=''23514'';'||chr(10)||
                '    end if;'||chr(10)||
                '    perform private.require_typefully_approval_route(allocation.approval_id,target_social_set_id);'||chr(10)||
                '    select candidate.* into banner from public.assets as candidate');
        end if;
        execute body;
    end loop;
end;
$upgrade$;

-- Public compatibility wrapper retains existing service-only auth and ACL.
create or replace function public.get_typefully_draft_candidate(
    target_workspace_id uuid,target_content_item_id uuid,
    target_content_version_id uuid,target_approval_id uuid
) returns jsonb language plpgsql stable security definer set search_path='' as $$
begin
    if current_setting('request.jwt.claim.role',true) is distinct from 'service_role' then
        raise exception 'typefully_service_role_required' using errcode='42501';
    end if;
    return private.typefully_draft_candidate_owner(target_workspace_id,
        target_content_item_id,target_content_version_id,target_approval_id);
end $$;

-- Called only by the narrow final-human-decision owner in its same transaction.
-- No service-role claim is forged, and no provider attempt is made here.
create function private.reserve_content_ops_typefully_slot(
    target_decision_id uuid,target_actor_id uuid,target_release_sha text
) returns uuid language plpgsql volatile security invoker set search_path='' as $$
declare d private.content_ops_final_decisions; h private.content_ops_channel_handoffs;
    candidate jsonb; slot private.typefully_daily_slots; today date;
begin
    if target_decision_id is null or target_actor_id is null
        or target_release_sha is null or target_release_sha !~ '^[a-f0-9]{40}$' then
        raise exception 'typefully_final_slot_arguments_invalid' using errcode='22023';
    end if;
    select * into d from private.content_ops_final_decisions where id=target_decision_id
        and actor_id=target_actor_id and release_sha=target_release_sha
        and action='confirm_publication' and status='confirmed_pending_publication_owner';
    if not found then raise exception 'typefully_final_slot_decision_invalid' using errcode='23514';end if;
    select * into h from private.content_ops_channel_handoffs where decision_id=d.id
        and channel='typefully_x' and release_sha=target_release_sha;
    if not found then raise exception 'typefully_final_slot_handoff_missing' using errcode='23514';end if;
    perform 1 from public.content_items where workspace_id=h.workspace_id and id=h.content_item_id for update;
    candidate:=private.typefully_draft_candidate_owner(h.workspace_id,h.content_item_id,
        h.content_version_id,h.approval_id);
    if candidate is null then raise exception 'typefully_final_slot_candidate_invalid' using errcode='23514';end if;
    today:=(clock_timestamp() at time zone 'Asia/Seoul')::date;
    if (select count(*) from private.typefully_daily_slots where workspace_id=h.workspace_id
        and (content_item_id=h.content_item_id or (client_id=h.client_id and kst_date=today)))>1 then
        raise exception 'typefully_final_slot_conflict' using errcode='23514';
    end if;
    select * into slot from private.typefully_daily_slots where workspace_id=h.workspace_id
        and (content_item_id=h.content_item_id or (client_id=h.client_id and kst_date=today)) for update;
    if found then
        if slot.workspace_id is distinct from h.workspace_id or slot.client_id is distinct from h.client_id
           or slot.kst_date is distinct from today or slot.content_item_id is distinct from h.content_item_id
           or slot.content_version_id is distinct from h.content_version_id
           or slot.approval_id is distinct from h.approval_id then
            raise exception 'typefully_final_slot_conflict' using errcode='23514';
        end if;
        return slot.id;
    end if;
    insert into private.typefully_daily_slots(workspace_id,client_id,kst_date,
        content_item_id,content_version_id,approval_id)
    values(h.workspace_id,h.client_id,today,h.content_item_id,h.content_version_id,h.approval_id)
    returning id into slot.id;
    return slot.id;
end $$;

revoke all on function private.content_ops_typefully_route_binding(uuid,text,text,bigint)
    from public,anon,authenticated,service_role;
revoke all on function private.typefully_final_approval_provenance(public.approvals)
    from public,anon,authenticated,service_role;
revoke all on function private.typefully_approval_attribution_valid(public.approvals)
    from public,anon,authenticated,service_role;
revoke all on function private.require_typefully_approval_route(uuid,bigint)
    from public,anon,authenticated,service_role;
revoke all on function private.typefully_telegram_coexistence_valid(uuid,uuid,uuid,uuid)
    from public,anon,authenticated,service_role;
revoke all on function private.typefully_draft_candidate_owner(uuid,uuid,uuid,uuid)
    from public,anon,authenticated,service_role;
revoke all on function private.reserve_content_ops_typefully_slot(uuid,uuid,text)
    from public,anon,authenticated,service_role;
commit;
