-- Synthetic fixtures only, in a disposable local database; never hosted.
begin;
create function pg_temp.check_button(ok boolean, label text) returns void language plpgsql as $$
begin if ok is not true then raise exception 'button review test: %',label;end if;end $$;
do $$
declare
    w uuid:=private.test_content_ops_seed(); a uuid:=gen_random_uuid(); b uuid:=gen_random_uuid();
    i uuid; v uuid; rid uuid:=gen_random_uuid(); fp text; result jsonb; before_actions bigint;
begin
    insert into auth.users(id) values(a),(b);
    select id,current_version_id into i,v from public.content_items where workspace_id=w and client_id='yellow';
    insert into private.content_ops_button_reviewers values(w,'yellow',a,true),(w,'yellow',b,true);
    fp:=private.content_ops_button_version_fingerprint(w,i,v);
    insert into private.content_ops_button_reviews(id,workspace_id,client_id,content_item_id,content_version_id,version_fingerprint)
        values(rid,w,'yellow',i,v,fp);
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='checks_pending','initial');
    perform private.record_content_ops_button_action(rid,a,fp,'source_checked',repeat('1',64));
    perform private.record_content_ops_button_action(rid,b,fp,'claims_checked',repeat('2',64));
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='checks_pending','no mixed humans');
    perform private.record_content_ops_button_action(rid,a,fp,'claims_checked',repeat('3',64));
    result:=private.content_ops_button_check_state(rid,a);
    perform pg_temp.check_button(result->>'status'='checks_complete' and result->'execution_authorized'='false','not approval');
    result:=private.record_content_ops_button_action(rid,a,fp,'source_checked',repeat('1',64));
    perform pg_temp.check_button(result->'reused'='true','same click replay');
    perform pg_temp.check_button((select count(*)=3 from private.content_ops_button_actions where review_id=rid),'no duplicate action');
    begin
        perform private.record_content_ops_button_action(rid,b,fp,'source_checked',repeat('1',64));
        raise exception 'expected actor conflict';
    exception when unique_violation then null;end;
    begin
        perform private.record_content_ops_button_action(rid,a,fp,'claims_checked',repeat('1',64));
        raise exception 'expected action conflict';
    exception when unique_violation then null;end;
    begin
        perform private.record_content_ops_button_action(rid,a,repeat('a',64),'source_checked',repeat('4',64));
        raise exception 'expected fingerprint conflict';
    exception when check_violation then null;end;
    begin
        perform private.record_content_ops_button_action(rid,a,fp,'approve_and_publish',repeat('4',64));
        raise exception 'expected publish rejection';
    exception when invalid_parameter_value then null;end;

    update private.content_ops_button_reviewers set active=false where actor_id=a;
    begin
        perform private.record_content_ops_button_action(rid,a,fp,'source_checked',repeat('1',64));
        raise exception 'expected revoked replay rejection';
    exception when insufficient_privilege then null;end;
    update private.content_ops_button_reviewers set active=true where actor_id=a;

    perform private.record_content_ops_button_action(rid,a,fp,'hold',repeat('5',64));
    result:=private.content_ops_button_check_state(rid,a);
    perform pg_temp.check_button(result->>'status'='held' and result->'source_checked'='false'
        and result->'claims_checked'='false','hold invalidates checks');
    result:=private.record_content_ops_button_action(rid,a,fp,'source_checked',repeat('1',64));
    perform pg_temp.check_button(result->>'status'='superseded','old check replay cannot restore');
    result:=private.record_content_ops_button_action(rid,a,fp,'hold',repeat('5',64));
    perform pg_temp.check_button(result->'reused'='true' and result->'checks'->>'epoch'='1','hold once');
    begin
        perform private.record_content_ops_button_action(rid,a,fp,'source_checked',repeat('6',64));
        raise exception 'expected new card requirement';
    exception when check_violation then null;end;

    -- Fixture resets are not owner APIs. Production has no registration/reset grant.
    update private.content_ops_button_reviews set state='active' where id=rid;
    perform private.record_content_ops_button_action(rid,a,fp,'edit_x',repeat('6',64));
    result:=private.content_ops_button_check_state(rid,a);
    perform pg_temp.check_button(result->>'status'='edit_requested' and result->>'epoch'='2','edit invalidates');
    perform pg_temp.check_button((select count(*)=3 from private.content_ops_button_checks where review_id=rid),'history retained');
    before_actions:=(select count(*) from private.content_ops_button_actions where review_id=rid);
    update public.content_versions set channel_copy=jsonb_build_object('telegram','new','x','new') where id=v;
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='stale','copy drift');
    begin
        perform private.record_content_ops_button_action(rid,a,fp,'hold',repeat('7',64));
        raise exception 'expected stale rejection';
    exception when check_violation then null;end;
    perform pg_temp.check_button((select count(*)=before_actions from private.content_ops_button_actions where review_id=rid),'stale zero action');
    fp:=private.content_ops_button_version_fingerprint(w,i,v);
    update private.content_ops_button_reviews set state='active',version_fingerprint=fp where id=rid;
    update public.assets set sha256=repeat('b',64) where content_version_id=v;
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='stale','asset drift');
    fp:=private.content_ops_button_version_fingerprint(w,i,v);
    update private.content_ops_button_reviews set version_fingerprint=fp where id=rid;
    update public.source_items set source_hash=repeat('c',64) where workspace_id=w and client_id='yellow';
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='stale','source drift');
    fp:=private.content_ops_button_version_fingerprint(w,i,v);
    update private.content_ops_button_reviews set version_fingerprint=fp where id=rid;
    update public.content_items set current_version_id=gen_random_uuid() where id=i;
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='stale','new immutable version');
    update public.content_items set current_version_id=v,status='approved' where id=i;
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='blocked','status changed');
    update public.content_items set status='needs_review' where id=i;
    insert into public.publications values(w,i);
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='blocked','any delivery blocks');
    delete from public.publications where content_item_id=i;
    insert into public.approvals values(w,i);
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='blocked','existing approval blocks');
    delete from public.approvals where content_item_id=i;
    update public.workspace_clients set active=false where workspace_id=w and client_id='yellow';
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='blocked','inactive client');
    update public.workspace_clients set active=true where workspace_id=w and client_id='yellow';
    update private.content_ops_button_reviews set created_at=statement_timestamp()-interval '31 minutes',
        expires_at=statement_timestamp()-interval '1 minute' where id=rid;
    perform pg_temp.check_button(private.content_ops_button_check_state(rid,a)->>'status'='expired','expired card');
    begin
        perform private.record_content_ops_button_action(rid,a,fp,'source_checked',repeat('8',64));
        raise exception 'expected expiry rejection';
    exception when check_violation then null;end;
    perform pg_temp.check_button(not exists(select 1 from public.approvals where workspace_id=w)
        and not exists(select 1 from public.publications where workspace_id=w),'no approval or publication writes');
    perform pg_temp.check_button(private.content_ops_button_check_state(gen_random_uuid(),a)->>'status'='not_recorded','missing is not zero');
end $$;
-- Actual execution attempts as runtime roles, in addition to ACL metadata.
do $$ declare role_name text; begin
    foreach role_name in array array['anon','authenticated','service_role'] loop
        execute format('set local role %I',role_name);
        begin
            perform private.content_ops_button_check_state(gen_random_uuid(),gen_random_uuid());
            raise exception 'runtime role unexpectedly executed helper';
        exception when insufficient_privilege then null;end;
        reset role;
    end loop;
end $$;
rollback;
