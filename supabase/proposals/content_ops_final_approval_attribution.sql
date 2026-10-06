-- LOCAL-ONLY PROPOSAL. NOT A DEPLOYABLE MIGRATION OR AUTHORIZATION.
-- Requires content_ops_final_approval_attribution_preapply_readonly.sql=pass
-- against the same hosted catalog immediately before a separately approved apply.
-- Does not grant an owner role, record an approval, queue a job or send anything.
begin;

alter table public.approvals
    add column review_principal_id uuid;

alter table public.approvals
    add constraint approvals_review_principal_workspace_fkey
    foreign key (workspace_id, review_principal_id)
    references private.content_ops_review_principals(workspace_id, id)
    on delete restrict;

alter table public.approvals
    drop constraint approvals_reviewer_source_check;

alter table public.approvals
    add constraint approvals_reviewer_source_check check (
        (reviewer_source='supabase_auth' and reviewer_id is not null
            and review_principal_id is null)
        or (reviewer_source='studio_session' and reviewer_id is null
            and review_principal_id is null)
        or (reviewer_source='telegram_principal' and reviewer_id is null
            and review_principal_id is not null)
    );

create unique index approvals_one_telegram_principal_approval_per_version
    on public.approvals(workspace_id,content_item_id,content_version_id)
    where reviewer_source='telegram_principal' and decision='approved';

-- Existing table RLS and grants remain unchanged. No new INSERT capability.
commit;
