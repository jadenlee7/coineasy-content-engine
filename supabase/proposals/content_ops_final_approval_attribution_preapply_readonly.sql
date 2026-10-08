-- Exact hosted catalog gate for the local-only attribution proposal.
-- No row reads, DDL, provider calls, grants, approvals or publications.
begin transaction read only;
do $$
declare
    source_constraint text;
begin
    if to_regclass('public.approvals') is null
       or to_regclass('private.content_ops_review_principals') is null then
        raise exception 'final_attribution_base_missing';
    end if;
    if exists (select 1 from pg_attribute a
        where a.attrelid='public.approvals'::regclass
          and a.attname='review_principal_id' and a.attnum>0 and not a.attisdropped) then
        raise exception 'final_attribution_partial_installation';
    end if;
    select pg_get_constraintdef(c.oid) into source_constraint
    from pg_constraint c where c.conrelid='public.approvals'::regclass
      and c.conname='approvals_reviewer_source_check' and c.contype='c';
    if source_constraint is distinct from
       'CHECK ((((reviewer_source = ''supabase_auth''::text) AND (reviewer_id IS NOT NULL)) OR ((reviewer_source = ''studio_session''::text) AND (reviewer_id IS NULL))))' then
        raise exception 'final_attribution_source_constraint_mismatch';
    end if;
    if not exists (select 1 from pg_constraint c
        where c.conrelid='private.content_ops_review_principals'::regclass
          and c.contype='u' and pg_get_constraintdef(c.oid)='UNIQUE (workspace_id, id)') then
        raise exception 'final_attribution_principal_scope_mismatch';
    end if;
    if not exists (select 1 from pg_constraint c
        where c.conrelid='public.approvals'::regclass and c.contype='f'
          and pg_get_constraintdef(c.oid)=
          'FOREIGN KEY (workspace_id, content_item_id, content_version_id) REFERENCES content_versions(workspace_id, content_item_id, id) ON DELETE RESTRICT') then
        raise exception 'final_attribution_version_scope_mismatch';
    end if;
    if not exists (select 1 from pg_attribute a
        where a.attrelid='public.approvals'::regclass and a.attname='reviewer_source'
          and a.atttypid='text'::regtype and a.attnotnull and not a.attisdropped)
       or not exists (select 1 from pg_attribute a
        where a.attrelid='public.approvals'::regclass and a.attname='reviewer_id'
          and a.atttypid='uuid'::regtype and not a.attnotnull and not a.attisdropped)
       or not exists (select 1 from pg_attribute a
        where a.attrelid='private.content_ops_review_principals'::regclass
          and a.attname='id' and a.atttypid='uuid'::regtype and a.attnotnull
          and not a.attisdropped)
       or not exists (select 1 from pg_attribute a
        where a.attrelid='private.content_ops_review_principals'::regclass
          and a.attname='workspace_id' and a.atttypid='uuid'::regtype
          and a.attnotnull and not a.attisdropped) then
        raise exception 'final_attribution_column_mismatch';
    end if;
    if not (select c.relrowsecurity and c.relforcerowsecurity
        from pg_class c where c.oid='private.content_ops_review_principals'::regclass)
       or not (select c.relrowsecurity from pg_class c
        where c.oid='public.approvals'::regclass) then
        raise exception 'final_attribution_rls_mismatch';
    end if;
end $$;
select jsonb_build_object('final_attribution_preapply','pass','read_only',true,
    'changes',0,'provider_calls',0,'hosted_runtime_verified',false);
commit;
