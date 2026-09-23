-- Disposable PostgreSQL fixture only. Not the canonical production migration.
-- Rebind just the actor lineage used by the prompt capability test after all
-- legacy card-ledger tests have rolled back their synthetic rows.
begin;
create table private.content_ops_review_principals(
    id uuid primary key,
    workspace_id uuid not null references public.workspaces(id),
    bot_binding text not null,
    human_binding text not null,
    unique(workspace_id,id),
    unique(workspace_id,bot_binding,human_binding,id)
);
do $$
declare target_table text; old_constraint text;
begin
    foreach target_table in array array[
        'content_ops_button_reviewers','content_ops_button_checks',
        'content_ops_button_actions','content_ops_button_identities',
        'content_ops_button_edit_prompts','content_ops_button_prompt_receipts',
        'content_ops_button_prompt_attempts'] loop
        select c.conname into old_constraint from pg_constraint c
        where c.conrelid=format('private.%I',target_table)::regclass
          and c.confrelid='auth.users'::regclass and c.contype='f';
        if old_constraint is null then
            raise exception 'synthetic principal rebind source missing: %',target_table;
        end if;
        execute format('alter table private.%I drop constraint %I',target_table,old_constraint);
        execute format('alter table private.%I add constraint %I foreign key(actor_id) '
            'references private.content_ops_review_principals(id)',target_table,old_constraint);
    end loop;
end $$;
alter table private.content_ops_button_reviewers
    add constraint synthetic_review_principal_workspace
    foreign key(workspace_id,actor_id)
    references private.content_ops_review_principals(workspace_id,id);
alter table private.content_ops_button_identities
    add constraint synthetic_review_principal_identity
    foreign key(workspace_id,bot_binding,human_binding,actor_id)
    references private.content_ops_review_principals(
        workspace_id,bot_binding,human_binding,id);
commit;
