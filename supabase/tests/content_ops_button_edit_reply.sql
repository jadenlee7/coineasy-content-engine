-- Synthetic identities and message bindings only; no real Telegram receipts.
create function private.test_button_edit_seed(edit_kind text default 'edit_x') returns jsonb language plpgsql as $$
declare w uuid:=private.test_content_ops_seed(); a uuid:=gen_random_uuid(); i uuid; v uuid;
    rid uuid:=gen_random_uuid(); pid uuid:=gen_random_uuid(); fp text;
begin
    insert into auth.users values(a);
    select id,current_version_id into i,v from public.content_items where workspace_id=w and client_id='squid';
    insert into private.content_ops_button_reviewers values(w,'squid',a,true);
    insert into private.content_ops_button_identities values(w,repeat('b',64),repeat('a',64),a,true);
    fp:=private.content_ops_button_version_fingerprint(w,i,v);
    insert into private.content_ops_button_reviews(id,workspace_id,client_id,content_item_id,content_version_id,version_fingerprint)
        values(rid,w,'squid',i,v,fp);
    perform private.record_content_ops_button_action(rid,a,fp,edit_kind,repeat('e',64));
    insert into private.content_ops_button_edit_prompts(id,review_id,actor_id,epoch,edit_action_key,
        bot_binding,room_binding,message_binding,receipt_sha256,delivered_at,expires_at)
        values(pid,rid,a,1,repeat('e',64),repeat('b',64),repeat('c',64),encode(sha256(convert_to(pid::text,'UTF8')),'hex'),repeat('f',64),
            statement_timestamp(),statement_timestamp()+interval '20 minutes');
    return jsonb_build_object('prompt',pid,'review',rid,'actor',a,'item',i,'version',v,'workspace',w);
end $$;
create function private.test_button_edit_call(pid uuid, body text default '새로운 검수용 X 문안', op text default repeat('1',64))
returns jsonb language sql as $$
    select private.save_content_ops_button_edit_reply(pid,repeat('b',64),repeat('c',64),
        encode(sha256(convert_to(pid::text,'UTF8')),'hex'),repeat('a',64),body,op)
$$;

begin;
create function pg_temp.check_edit(ok boolean,label text) returns void language plpgsql as $$
begin if ok is not true then raise exception 'edit test: %',label;end if;end $$;
do $$
declare ctx jsonb; result jsonb; repeated jsonb; old_doc jsonb; new_doc jsonb; pid uuid; v uuid;
    scenario text; expected text; actual text; body text;
begin
    ctx:=private.test_button_edit_seed();pid:=(ctx->>'prompt')::uuid;v:=(ctx->>'version')::uuid;
    select to_jsonb(cv) into old_doc from public.content_versions cv where id=v;
    result:=private.test_button_edit_call(pid);
    repeated:=private.test_button_edit_call(pid);
    perform pg_temp.check_edit(result->>'status'='revision_saved' and result->'reused'='false'
        and repeated->'reused'='true' and result->>'content_version_id'=repeated->>'content_version_id','exact one revision');
    perform pg_temp.check_edit((select to_jsonb(cv)=old_doc from public.content_versions cv where id=v),'old immutable version untouched');
    select to_jsonb(cv) into new_doc from public.content_versions cv where id=(result->>'content_version_id')::uuid;
    perform pg_temp.check_edit(new_doc#>>'{channel_copy,x}'='새로운 검수용 X 문안'
        and new_doc#>'{channel_copy,telegram}'=old_doc#>'{channel_copy,telegram}','only selected channel changes');
    perform pg_temp.check_edit(new_doc->'deliverables'='{}'::jsonb
        and new_doc#>>'{generation_meta,fact_check,status}'='needs_review'
        and new_doc#>>'{generation_meta,brand_qa,status}'='needs_review'
        and new_doc#>>'{generation_meta,review_revision,previous_version_id}'=v::text,'banner and QA do not carry');
    perform pg_temp.check_edit((select status='draft' and current_version_id=(result->>'content_version_id')::uuid
        from public.content_items where id=(ctx->>'item')::uuid),'new draft current');
    perform pg_temp.check_edit((select count(*)=2 from public.content_versions where content_item_id=(ctx->>'item')::uuid),'no duplicate revision');
    perform pg_temp.check_edit(not exists(select 1 from public.assets where content_version_id=(result->>'content_version_id')::uuid),'no inherited asset');
    perform pg_temp.check_edit(result->'execution_authorized'='false' and result->'rereview_required'='true','not publication');
    begin
        perform private.test_button_edit_call(pid,'different body'); raise exception 'expected body conflict';
    exception when unique_violation then null;end;
    begin
        perform private.test_button_edit_call(pid,'새로운 검수용 X 문안',repeat('2',64)); raise exception 'expected op conflict';
    exception when unique_violation then null;end;

    ctx:=private.test_button_edit_seed('edit_telegram');
    result:=private.test_button_edit_call((ctx->>'prompt')::uuid,'수정한 텔레그램 공지');
    perform pg_temp.check_edit((select channel_copy->>'telegram'='수정한 텔레그램 공지'
        from public.content_versions where id=(result->>'content_version_id')::uuid),'telegram branch');

    foreach scenario in array array['wrong_bot','wrong_room','wrong_message','wrong_human',
        'identity_revoked','reviewer_revoked','inactive_client','expired','future_receipt',
        'stale_epoch','stale_source','old_version','existing_approval','existing_publication',
        'banner_edit','no_change','oversize_utf16','private_invite','empty','control_character'] loop
        ctx:=private.test_button_edit_seed(case when scenario='banner_edit' then 'edit_banner' else 'edit_x' end);
        pid:=(ctx->>'prompt')::uuid;body:='새로운 검수용 X 문안';expected:='23514';
        case scenario
            when 'wrong_bot','wrong_room','wrong_message','wrong_human' then expected:='42501';
            when 'identity_revoked' then
                update private.content_ops_button_identities set active=false where actor_id=(ctx->>'actor')::uuid;expected:='42501';
            when 'reviewer_revoked' then
                update private.content_ops_button_reviewers set active=false where actor_id=(ctx->>'actor')::uuid;expected:='42501';
            when 'inactive_client' then update public.workspace_clients set active=false where workspace_id=(ctx->>'workspace')::uuid;
            when 'expired' then update private.content_ops_button_edit_prompts set delivered_at=statement_timestamp()-interval '21 minutes',
                expires_at=statement_timestamp()-interval '1 minute' where id=pid;
            when 'future_receipt' then update private.content_ops_button_edit_prompts set delivered_at=statement_timestamp()+interval '1 minute',
                expires_at=statement_timestamp()+interval '20 minutes' where id=pid;
            when 'stale_epoch' then update private.content_ops_button_reviews set epoch=epoch+1 where id=(ctx->>'review')::uuid;
            when 'stale_source' then update public.source_items set source_hash=repeat('9',64) where workspace_id=(ctx->>'workspace')::uuid;
            when 'old_version' then update public.content_items set current_version_id=gen_random_uuid() where id=(ctx->>'item')::uuid;
            when 'existing_approval' then insert into public.approvals values((ctx->>'workspace')::uuid,(ctx->>'item')::uuid);
            when 'existing_publication' then insert into public.publications values((ctx->>'workspace')::uuid,(ctx->>'item')::uuid);
            when 'banner_edit' then null;
            when 'no_change' then body:='Synthetic X review';expected:='22023';
            when 'oversize_utf16' then body:=repeat('😀',501);expected:='22023';
            when 'private_invite' then body:='t.me/+synthetic';expected:='22023';
            when 'empty' then body:=E' \n\t';expected:='22023';
            when 'control_character' then body:=E'text\x01';expected:='22023';
        end case;
        actual:=null;
        begin
            perform private.save_content_ops_button_edit_reply(pid,
                repeat(case when scenario='wrong_bot' then '9' else 'b' end,64),
                repeat(case when scenario='wrong_room' then '9' else 'c' end,64),
                case when scenario='wrong_message' then repeat('9',64) else encode(sha256(convert_to(pid::text,'UTF8')),'hex') end,
                repeat(case when scenario='wrong_human' then '9' else 'a' end,64),body,repeat('1',64));
        exception when others then get stacked diagnostics actual=returned_sqlstate;end;
        perform pg_temp.check_edit(actual=expected,'rejection '||scenario);
        perform pg_temp.check_edit((select count(*)=1 from public.content_versions where content_item_id=(ctx->>'item')::uuid),'no mutation '||scenario);
        perform pg_temp.check_edit((select consumed_key is null from private.content_ops_button_edit_prompts where id=pid),'not consumed '||scenario);
    end loop;
end $$;
rollback;
