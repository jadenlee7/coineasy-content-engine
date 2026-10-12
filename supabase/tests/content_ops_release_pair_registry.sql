-- Synthetic rollback-only registry/ACL test. No provider or production calls.
begin;
do $$
declare
    w uuid:=gen_random_uuid(); e text:=repeat('a',40); b text:=repeat('b',40);
    p text; other_p text; original_time timestamptz; changed_time timestamptz; role_name text;
begin
    p:=encode(sha256(convert_to(E'content-ops-release-pair@1\nengine='||e||E'\ncallback='||b||E'\n','UTF8')),'hex');
    other_p:=encode(sha256(convert_to(E'content-ops-release-pair@1\nengine='||e||E'\ncallback='||repeat('c',40)||E'\n','UTF8')),'hex');
    insert into public.workspaces(id,name,slug) values(w,'Synthetic pairs','pair-'||w::text);
    insert into public.workspace_clients(workspace_id,client_id,display_name) values(w,'yellow','Synthetic');
    insert into private.content_ops_release_pairs(release_sha,callback_release_sha,release_pair_sha256)
        values(e,b,p),(e,repeat('c',40),other_p);
    begin
        insert into private.content_ops_release_pairs(release_sha,callback_release_sha,release_pair_sha256)
            values(e,b,repeat('0',64));
        raise exception 'invented_pair_digest_accepted';
    exception when check_violation then null;end;
    begin
        insert into private.content_ops_release_pairs(release_sha,callback_release_sha,release_pair_sha256)
            values(upper(e),b,p);
        raise exception 'uppercase_engine_sha_accepted';
    exception when check_violation then null;end;
    insert into private.content_ops_current_release_pairs(workspace_id,client_id,release_pair_sha256,selected_at)
        values(w,'yellow',p,'2000-01-01Z');
    select selected_at into original_time from private.content_ops_current_release_pairs where workspace_id=w;
    if original_time<clock_timestamp()-interval '1 minute' then raise exception 'selection_backdate_accepted';end if;
    perform private.require_content_ops_release_pair(w,'yellow',e,b,p,clock_timestamp());
    begin
        perform private.require_content_ops_release_pair(w,'babylon',e,b,p);
        raise exception 'foreign_scope_pair_accepted';
    exception when check_violation then null;end;
    begin
        update private.content_ops_release_pairs set callback_release_sha=repeat('d',40) where release_pair_sha256=p;
        raise exception 'registry_mutated';
    exception when check_violation then null;end;
    begin
        delete from private.content_ops_current_release_pairs where workspace_id=w;
        raise exception 'selection_deleted';
    exception when check_violation then null;end;
    begin
        truncate private.content_ops_current_release_pairs;
        raise exception 'selection_truncated';
    exception when check_violation then null;end;
    update private.content_ops_current_release_pairs set release_pair_sha256=other_p where workspace_id=w;
    update private.content_ops_current_release_pairs set release_pair_sha256=p,selected_at=original_time where workspace_id=w;
    select selected_at into changed_time from private.content_ops_current_release_pairs where workspace_id=w;
    if changed_time<=original_time then raise exception 'pair_rollback_reset_clock';end if;
    begin
        perform private.require_content_ops_release_pair(w,'yellow',e,b,p,original_time);
        raise exception 'old_card_revived_after_pair_rollback';
    exception when check_violation then null;end;
    foreach role_name in array array['anon','authenticated','service_role'] loop
        if has_table_privilege(role_name,'private.content_ops_release_pairs','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
           or has_table_privilege(role_name,'private.content_ops_current_release_pairs','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
           or has_function_privilege(role_name,'private.require_content_ops_release_pair(uuid,text,text,text,text,timestamptz)','EXECUTE') then
            raise exception 'release_pair_runtime_acl_leaked';
        end if;
    end loop;
end $$;
rollback;
