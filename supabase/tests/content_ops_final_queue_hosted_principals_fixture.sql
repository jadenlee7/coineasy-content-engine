-- TEST ONLY: hosted signup-free FK subset for the final-queue fixture.
-- Include inside the local verifier's transaction, after all final
-- queue proposals. Never install this as a production or historical migration.
-- Replicates the principal/FK portion of bot schema release 20260922173000;
-- does not replay its hosted PG17 catalog seal, roles, runtime grants, edit
-- function replacement, or absent banner tables.
do $guard$
declare t text; occupied boolean; constraint_name text;
begin
    if current_setting('coineasy.signup_free_fixture',true) is distinct from 'local-only'
       or inet_server_addr() is not null
       or current_database() !~ '^coineasy_[a-z0-9_]+_test$' then
        raise exception 'signup_free_fixture_requires_disposable_local_socket';
    end if;
    if to_regprocedure('private.record_content_ops_final_publication(uuid,uuid,uuid,text,text,text,text,text,text,text,text,text,text,text)') is null then
        raise exception 'signup_free_fixture_final_owner_missing';
    end if;
    foreach t in array array['content_ops_review_principals',
        'content_ops_button_reviewers','content_ops_button_checks',
        'content_ops_button_actions','content_ops_button_identities',
        'content_ops_button_edit_prompts','content_ops_button_prompt_receipts',
        'content_ops_button_prompt_attempts'] loop
        execute format('select exists(select 1 from private.%I)',t) into occupied;
        if occupied then raise exception 'signup_free_fixture_requires_empty_review_tables';end if;
    end loop;
    foreach t in array array['content_ops_button_reviewers','content_ops_button_checks',
        'content_ops_button_actions','content_ops_button_identities',
        'content_ops_button_edit_prompts','content_ops_button_prompt_receipts',
        'content_ops_button_prompt_attempts'] loop
        select c.conname into strict constraint_name from pg_constraint c
        where c.conrelid=format('private.%I',t)::regclass
          and c.confrelid='auth.users'::regclass and c.contype='f'
          and pg_get_constraintdef(c.oid)='FOREIGN KEY (actor_id) REFERENCES auth.users(id)';
        execute format('alter table private.%I drop constraint %I',t,constraint_name);
        execute format('alter table private.%I add constraint %I foreign key(actor_id) '
            'references private.content_ops_review_principals(id)',t,constraint_name);
    end loop;
end $guard$;

alter table private.content_ops_review_principals
    add constraint signup_free_principal_nonzero
        check(id<>'00000000-0000-0000-0000-000000000000'::uuid),
    add constraint signup_free_principal_identity_unique
        unique(workspace_id,bot_binding,human_binding,id);
create trigger content_ops_principal_immutable before update or delete
    on private.content_ops_review_principals for each row
    execute function private.guard_content_ops_button_durable_record();
alter table private.content_ops_button_reviewers
    add constraint review_principal_workspace foreign key(workspace_id,actor_id)
        references private.content_ops_review_principals(workspace_id,id);
alter table private.content_ops_button_identities
    add constraint review_principal_identity foreign key(workspace_id,bot_binding,human_binding,actor_id)
        references private.content_ops_review_principals(workspace_id,bot_binding,human_binding,id);

-- Prove these are enforced constraints, not just catalog labels.
do $negative$
declare w uuid:=gen_random_uuid(); other_w uuid:=gen_random_uuid();
    principal uuid:=gen_random_uuid(); auth_only uuid:=gen_random_uuid();
begin
    insert into public.workspaces(id,name,slug) values
        (w,'Synthetic principal','principal-'||w::text),
        (other_w,'Synthetic other principal','principal-'||other_w::text);
    insert into public.workspace_clients(workspace_id,client_id,display_name) values
        (w,'yellow','Synthetic'),(other_w,'yellow','Synthetic');
    insert into private.content_ops_review_principals(id,workspace_id,bot_binding,human_binding)
        values(principal,w,repeat('1',64),repeat('2',64));
    insert into auth.users(id) values(auth_only);
    begin
        insert into private.content_ops_button_reviewers(workspace_id,client_id,actor_id)
            values(w,'yellow',auth_only);
        raise exception 'signup_free_fixture_auth_user_accepted_as_principal';
    exception when foreign_key_violation then null;end;
    begin
        insert into private.content_ops_button_reviewers(workspace_id,client_id,actor_id)
            values(other_w,'yellow',principal);
        raise exception 'signup_free_fixture_cross_workspace_reviewer_accepted';
    exception when foreign_key_violation then null;end;
    begin
        insert into private.content_ops_button_identities(workspace_id,bot_binding,human_binding,actor_id)
            values(w,repeat('1',64),repeat('3',64),principal);
        raise exception 'signup_free_fixture_wrong_human_binding_accepted';
    exception when foreign_key_violation then null;end;
    begin
        insert into private.content_ops_button_identities(workspace_id,bot_binding,human_binding,actor_id)
            values(w,repeat('3',64),repeat('2',64),principal);
        raise exception 'signup_free_fixture_wrong_bot_binding_accepted';
    exception when foreign_key_violation then null;end;
    begin
        update private.content_ops_review_principals set human_binding=repeat('4',64)
            where id=principal;
        raise exception 'signup_free_fixture_principal_rebound';
    exception when check_violation then null;end;
    if exists(select 1 from auth.users where id=principal) then
        raise exception 'signup_free_fixture_principal_has_auth_user';
    end if;
end $negative$;
