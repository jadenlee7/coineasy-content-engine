-- Disposable full-schema behavioral checks after the LOCAL proposal only.
-- All synthetic rows roll back. No runtime grant, callback or provider call.
begin;
do $$
declare
    w uuid:=gen_random_uuid(); other_w uuid:=gen_random_uuid();
    i uuid:=gen_random_uuid(); v uuid:=gen_random_uuid();
    principal uuid:=gen_random_uuid(); other_principal uuid:=gen_random_uuid();
    auth_actor uuid:=gen_random_uuid();
begin
    insert into public.workspaces(id,name,slug) values
        (w,'Synthetic final attribution','synthetic-final-'||substr(w::text,1,8)),
        (other_w,'Synthetic other workspace','synthetic-other-'||substr(other_w::text,1,8));
    insert into public.workspace_clients(workspace_id,client_id,display_name)
        values(w,'yellow','Synthetic Yellow');
    insert into public.content_items(id,workspace_id,client_id,content_kind,status)
        values(i,w,'yellow','daily_news','needs_review');
    insert into public.content_versions(id,workspace_id,content_item_id,version_number,prompt_version)
        values(v,w,i,1,'synthetic@1');
    update public.content_items set current_version_id=v where id=i;
    insert into private.content_ops_review_principals(id,workspace_id,bot_binding,human_binding)
        values(principal,w,repeat('a',64),repeat('b',64)),
              (other_principal,other_w,repeat('c',64),repeat('d',64));

    insert into public.approvals(workspace_id,client_id,content_item_id,
        content_version_id,reviewer_source,reviewer_id,review_principal_id,
        decision,idempotency_key,fact_check_policy_version,
        source_facts_verified,output_claims_verified)
        values(w,'yellow',i,v,'telegram_principal',null,principal,
            'approved','final-test-1','double-fact-check@1',true,true);
    if not exists(select 1 from public.approvals
        where workspace_id=w and content_item_id=i and content_version_id=v
          and reviewer_source='telegram_principal' and reviewer_id is null
          and review_principal_id=principal and decision='approved') then
        raise exception 'final_attribution_approved_row_missing';
    end if;

    begin
        insert into public.approvals(workspace_id,client_id,content_item_id,
            content_version_id,reviewer_source,reviewer_id,review_principal_id,decision)
            values(w,'yellow',i,v,'telegram_principal',null,other_principal,'commented');
        raise exception 'cross_workspace_principal_accepted';
    exception when foreign_key_violation then null; end;
    begin
        insert into public.approvals(workspace_id,client_id,content_item_id,
            content_version_id,reviewer_source,reviewer_id,review_principal_id,decision)
            values(w,'yellow',i,v,'telegram_principal',null,null,'commented');
        raise exception 'missing_principal_accepted';
    exception when check_violation then null; end;
    begin
        insert into public.approvals(workspace_id,client_id,content_item_id,
            content_version_id,reviewer_source,reviewer_id,review_principal_id,decision)
            values(w,'yellow',i,v,'studio_session',null,principal,'commented');
        raise exception 'studio_identity_spoof_accepted';
    exception when check_violation then null; end;
    begin
        insert into public.approvals(workspace_id,client_id,content_item_id,
            content_version_id,reviewer_source,reviewer_id,review_principal_id,decision)
            values(w,'yellow',i,v,'telegram_principal',null,principal,'approved');
        raise exception 'second_telegram_approval_accepted';
    exception when unique_violation then null; end;

    insert into public.approvals(workspace_id,client_id,content_item_id,
        content_version_id,reviewer_source,reviewer_id,review_principal_id,decision)
        values(w,'yellow',i,v,'studio_session',null,null,'commented');
    if not exists(select 1 from public.approvals
        where workspace_id=w and content_item_id=i
          and reviewer_source='studio_session' and review_principal_id is null) then
        raise exception 'historical_studio_shape_rejected';
    end if;

    -- A Supabase Auth fixture only checks the preserved constraint shape.
    -- The real auth.users FK remains in force, so no forged auth row is made.
    if not exists(select 1 from pg_constraint c
        where c.conrelid='public.approvals'::regclass
          and c.conname='approvals_reviewer_id_fkey' and c.contype='f') then
        raise exception 'auth_reviewer_fk_removed';
    end if;
    if has_table_privilege('anon','public.approvals','INSERT')
       or has_table_privilege('authenticated','public.approvals','INSERT') then
        raise exception 'approval_direct_insert_leaked';
    end if;
end $$;
rollback;
