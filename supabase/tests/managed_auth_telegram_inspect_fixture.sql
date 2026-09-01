-- Synthetic fixture helper only: run in a disposable local database as owner.
-- No provider, Telegram or production access. Caller owns transaction cleanup.
create function pg_temp.managed_inspect_fixture(workspace_uuid uuid, owner_uuid uuid)
returns jsonb language plpgsql as $$
declare
    item_uuid uuid := gen_random_uuid();
    asset_uuid uuid := gen_random_uuid();
    generated jsonb;
    requested jsonb;
    claimed jsonb;
    version_uuid uuid;
    previous_sub text := current_setting('request.jwt.claim.sub', true);
    path text := workspace_uuid::text || '/squid/' || asset_uuid::text || '/news-card.png';
begin
    insert into public.workspaces(id,name,slug,created_by)
      values(workspace_uuid,'Synthetic managed inspect', 'managed-inspect-' || workspace_uuid::text, null);
    insert into public.workspace_clients(workspace_id,client_id,display_name,active)
      values(workspace_uuid,'squid','Squid',true);
    insert into public.workspace_members(workspace_id,user_id,role,status)
      values(workspace_uuid,owner_uuid,'owner','active');
    insert into storage.objects(bucket_id,name) values('content-studio',path);
    perform set_config('request.jwt.claim.sub',owner_uuid::text,true);
    generated := public.record_generated_content(
        item_uuid,workspace_uuid,'squid','daily_news','Synthetic managed inspect',
        jsonb_build_object('request_hash',repeat('a',64)),
        jsonb_build_object('telegram','Synthetic test caption.'),
        jsonb_build_object('request_hash',repeat('a',64),'mock_mode',false,'fact_check',jsonb_build_object(
            'schema_version','1.0','policy_version','double-fact-check@1','content_kind','daily_news',
            'status','review','human_review_required',true,'input_sha256',repeat('a',64),'output_sha256',repeat('b',64),
            'checks',jsonb_build_array(
                jsonb_build_object('id','source_evidence','status','review','label','Source','detail','Synthetic human verification.','metrics','{}'::jsonb),
                jsonb_build_object('id','output_claims','status','pass','label','Output','detail','Synthetic output.','metrics','{}'::jsonb)
            ))),
        jsonb_build_object('asset_id',asset_uuid,'filename','news-card.png','storage_path',path,
            'mime_type','image/png','byte_size',128,'sha256',repeat('a',64),'width',1080,'height',1080),
        'managed-inspect-local-test@1'
    );
    version_uuid := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review_v2(workspace_uuid,item_uuid,version_uuid,
        'approved','double-fact-check@1',true,true,'{}'::text[],null,'managed-inspect-' || item_uuid::text);
    requested := public.request_studio_telegram_publication(workspace_uuid,item_uuid,version_uuid,gen_random_uuid()::text);
    claimed := public.claim_exact_telegram_publication_job(workspace_uuid,'managed-inspect-fixture',300);
    if claimed ->> 'job_id' is distinct from requested ->> 'job_id' then
        raise exception 'local fixture claimed another job';
    end if;
    perform public.mark_exact_telegram_attempt_started((requested ->> 'job_id')::uuid,'managed-inspect-fixture',repeat('b',64));
    perform public.fail_exact_telegram_publication_job((requested ->> 'job_id')::uuid,'managed-inspect-fixture','telegram_delivery_unknown',false);
    update public.publications set delivery_started_at=clock_timestamp()-interval '20 minutes'
      where id=(requested ->> 'publication_id')::uuid;
    perform set_config('request.jwt.claim.sub',coalesce(previous_sub,''),true);
    return jsonb_build_object('workspace_id',workspace_uuid,'content_item_id',item_uuid,
        'content_version_id',version_uuid,'publication_id',requested ->> 'publication_id','job_id',requested ->> 'job_id',
        'asset_id',asset_uuid);
end;
$$;
