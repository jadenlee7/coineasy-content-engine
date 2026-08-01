-- Transactional security and state-machine smoke for exact Telegram delivery.
-- Run after all migrations as the database owner; every row is rolled back.

begin;

do $test$
declare
    signature text;
begin
    foreach signature in array array[
        'public.request_studio_telegram_publication(uuid,uuid,uuid,text)',
        'public.get_studio_telegram_publication(uuid,uuid,uuid)',
        'public.reconcile_expired_exact_telegram_publication_leases(uuid,integer)',
        'public.claim_exact_telegram_publication_job(uuid,text,integer)',
        'public.mark_exact_telegram_attempt_started(uuid,text,text)',
        'public.complete_exact_telegram_publication_job(uuid,text,text,bigint,text,timestamp with time zone)',
        'public.fail_exact_telegram_publication_job(uuid,text,text,boolean)'
    ] loop
        if has_function_privilege('anon', signature, 'execute')
           or has_function_privilege('authenticated', signature, 'execute') then
            raise exception 'exact Telegram RPC leaked to a browser role: %', signature;
        end if;
        if not has_function_privilege('service_role', signature, 'execute') then
            raise exception 'exact Telegram RPC unavailable to service_role: %', signature;
        end if;
    end loop;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'd0000000-0000-4000-8000-000000000001',
    'Exact Telegram Publication Security Test',
    'exact-telegram-publication-security-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values
    ('d0000000-0000-4000-8000-000000000001', 'squid', 'Squid', true, null),
    ('d0000000-0000-4000-8000-000000000001', 'yellow', 'Yellow', true, null);

insert into auth.users (id)
values ('d9000000-0000-4000-8000-000000000001');
insert into public.workspace_members (
    workspace_id, user_id, role, status, invited_by
) values (
    'd0000000-0000-4000-8000-000000000001',
    'd9000000-0000-4000-8000-000000000001',
    'owner',
    'active',
    null
);

do $test$
begin
    begin
        perform public.reconcile_expired_exact_telegram_publication_leases(
            'd0000000-0000-4000-8000-000000000001', null
        );
        raise exception 'NULL exact Telegram recovery limit was accepted';
    exception when invalid_parameter_value then null;
    end;
end
$test$;

insert into storage.objects (bucket_id, name)
values
    ('content-studio', 'd0000000-0000-4000-8000-000000000001/yellow/d3000000-0000-4000-8000-000000000001/news-card.png'),
    ('content-studio', 'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000002/news-card.png'),
    ('content-studio', 'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000003/news-card.png'),
    ('content-studio', 'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000004/news-card.png'),
    ('content-studio', 'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000005/news-card.png'),
    ('content-studio', 'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000006/news-card.png'),
    ('content-studio', 'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000007/news-card.png'),
    ('content-studio', 'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000008/news-card.png'),
    ('content-studio', 'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000009/news-card.png');

do $test$
declare
    generated jsonb;
    yellow_version_id uuid;
begin
    generated := public.record_generated_content(
        'd1000000-0000-4000-8000-000000000001',
        'd0000000-0000-4000-8000-000000000001',
        'yellow',
        'daily_news',
        'Yellow canary exclusion',
        '{"request_hash":"1111111111111111111111111111111111111111111111111111111111111111"}'::jsonb,
        '{"telegram":"Yellow must remain outside the Squid-only canary."}'::jsonb,
        '{"request_hash":"1111111111111111111111111111111111111111111111111111111111111111","mock_mode":false}'::jsonb,
        '{"asset_id":"d3000000-0000-4000-8000-000000000001","filename":"news-card.png","storage_path":"d0000000-0000-4000-8000-000000000001/yellow/d3000000-0000-4000-8000-000000000001/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","width":1080,"height":1080}'::jsonb,
        'exact-telegram-smoke@1'
    );
    yellow_version_id := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000001',
        yellow_version_id,
        'approved',
        '{}'::text[],
        null,
        'yellow-approval-smoke'
    );

    begin
        perform public.request_studio_telegram_publication(
            'd0000000-0000-4000-8000-000000000001',
            'd1000000-0000-4000-8000-000000000001',
            yellow_version_id,
            'd4000000-0000-4000-8000-000000000001'
        );
        raise exception 'Yellow escaped the Squid-only Telegram canary';
    exception when check_violation then null;
    end;
end
$test$;

do $test$
declare
    generated jsonb;
    version_id uuid;
    first_request jsonb;
    second_request jsonb;
    claim jsonb;
    failure jsonb;
    marker jsonb;
    lookup jsonb;
    replay_request jsonb;
    manual_observation jsonb;
    manual_publication_id uuid;
    request_sha text := repeat('c', 64);
begin
    generated := public.record_generated_content(
        'd1000000-0000-4000-8000-000000000002',
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Squid retry boundary',
        '{"request_hash":"2222222222222222222222222222222222222222222222222222222222222222"}'::jsonb,
        '{"telegram":"승인된 Squid 캡션과 정확한 PNG만 전송합니다."}'::jsonb,
        '{"request_hash":"2222222222222222222222222222222222222222222222222222222222222222","mock_mode":false}'::jsonb,
        '{"asset_id":"d3000000-0000-4000-8000-000000000002","filename":"news-card.png","storage_path":"d0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000002/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","width":1080,"height":1080}'::jsonb,
        'exact-telegram-smoke@1'
    );
    version_id := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000002',
        version_id,
        'approved',
        '{}'::text[],
        null,
        'squid-retry-approval-smoke'
    );

    first_request := public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000002',
        version_id,
        'd4000000-0000-4000-8000-000000000002'
    );
    second_request := public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000002',
        version_id,
        'd4000000-0000-4000-8000-000000000003'
    );
    if first_request ->> 'publication_id' is distinct from second_request ->> 'publication_id'
       or first_request ->> 'job_id' is distinct from second_request ->> 'job_id'
       or second_request ->> 'reused' <> 'true'
       or (select count(*) from public.publications
           where content_version_id = version_id and channel = 'telegram') <> 1
       or (select count(*) from public.jobs
           where input ->> 'content_version_id' = version_id::text
             and input ->> 'channel' = 'telegram') <> 1 then
        raise exception 'different idempotency keys did not converge on one exact delivery';
    end if;

    claim := public.claim_exact_telegram_publication_job(
        'd0000000-0000-4000-8000-000000000001',
        'worker-smoke-01',
        300
    );
    if claim ->> 'content_version_id' <> version_id::text
       or claim ->> 'approval_id' is null
       or claim ->> 'telegram_public_username' <> 'squid_kor_update'
       or claim -> 'asset' ->> 'asset_id'
            <> 'd3000000-0000-4000-8000-000000000002' then
        raise exception 'claim did not preserve the approved version and asset pins';
    end if;

    failure := public.fail_exact_telegram_publication_job(
        (claim ->> 'job_id')::uuid,
        'worker-smoke-01',
        'telegram_preflight_unavailable',
        true
    );
    if failure ->> 'status' <> 'queued'
       or failure ->> 'job_status' <> 'retrying' then
        raise exception 'pre-attempt failure did not remain retryable';
    end if;

    update public.jobs
    set available_at = statement_timestamp()
    where id = (claim ->> 'job_id')::uuid;
    claim := public.claim_exact_telegram_publication_job(
        'd0000000-0000-4000-8000-000000000001',
        'worker-smoke-01',
        300
    );
    marker := public.mark_exact_telegram_attempt_started(
        (claim ->> 'job_id')::uuid,
        'worker-smoke-01',
        request_sha
    );
    if marker ->> 'status' <> 'publishing'
       or marker ->> 'attempt_started' <> 'true' then
        raise exception 'provider attempt marker was not persisted';
    end if;

    failure := public.fail_exact_telegram_publication_job(
        (claim ->> 'job_id')::uuid,
        'worker-smoke-01',
        'telegram_delivery_unknown',
        false
    );
    if failure ->> 'status' <> 'delivery_unknown'
       or failure ->> 'job_status' <> 'failed'
       or public.claim_exact_telegram_publication_job(
            'd0000000-0000-4000-8000-000000000001',
            'worker-smoke-01',
            300
          ) is not null then
        raise exception 'post-attempt uncertainty was retried';
    end if;
    lookup := public.get_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000002',
        version_id
    );
    if lookup ->> 'status' <> 'delivery_unknown' then
        raise exception 'delivery_unknown was not visible to Studio';
    end if;
    replay_request := public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000002',
        version_id,
        'd4000000-0000-4000-8000-000000000002'
    );
    if replay_request ->> 'status' <> 'delivery_unknown'
       or replay_request ->> 'error_code' <> 'telegram_delivery_unknown'
       or replay_request ->> 'delivery_started_at' is null then
        raise exception 'request replay lost the exact Telegram failure result';
    end if;

    perform set_config(
        'request.jwt.claim.sub',
        'd9000000-0000-4000-8000-000000000001',
        true
    );
    begin
        perform public.request_content_publication(
            'd1000000-0000-4000-8000-000000000002',
            version_id,
            'telegram',
            null,
            'generic-after-exact-unknown'
        );
        raise exception
            'generic publication request bypassed exact delivery_unknown';
    exception when unique_violation then null;
    end;

    manual_observation := public.record_manual_publication_observation(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000002',
        version_id,
        'telegram',
        'https://t.me/squid_kor_update/987654321'
    );
    manual_publication_id :=
        (manual_observation ->> 'publication_id')::uuid;
    if manual_observation ->> 'external_url'
            <> 'https://t.me/squid_kor_update/987654321'
       or not exists (
           select 1
           from public.publications as publication
           where publication.id = manual_publication_id
             and publication.status = 'published'
             and publication.request_payload = jsonb_build_object(
                 'observation', 'manual_existing_publication',
                 'external_publish_performed', false
             )
             and publication.response_payload = jsonb_build_object(
                 'observed', true,
                 'external_publish_performed', false
             )
       )
       or not exists (
           select 1
           from public.publications as publication
           where publication.id =
                    (first_request ->> 'publication_id')::uuid
             and publication.status = 'delivery_unknown'
       )
       or (select count(*)
           from public.jobs as job
           where job.content_item_id =
                    'd1000000-0000-4000-8000-000000000002'
             and job.job_kind = 'publish') <> 1 then
        raise exception
            'manual observation of exact delivery_unknown failed';
    end if;

    begin
        update public.publications
        set response_payload = jsonb_build_object(
            'observed', false,
            'external_publish_performed', false
        )
        where id = manual_publication_id;
        raise exception
            'manual observation response was weakened beside exact unknown';
    exception when unique_violation then null;
    end;
    begin
        update public.publications
        set external_url = 'https://t.me/not_squid_official/987654321'
        where id = manual_publication_id;
        raise exception
            'manual observation URL was weakened beside exact unknown';
    exception when unique_violation then null;
    end;
    perform set_config('request.jwt.claim.sub', '', true);
end
$test$;

do $test$
declare
    generated jsonb;
    version_id uuid;
    request_result jsonb;
    claim jsonb;
    marker jsonb;
    completed jsonb;
    replay jsonb;
    replay_request jsonb;
    generic_failed_id uuid;
    provider_date timestamptz := statement_timestamp();
    request_sha text := repeat('d', 64);
begin
    generated := public.record_generated_content(
        'd1000000-0000-4000-8000-000000000003',
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Squid exact success',
        '{"request_hash":"3333333333333333333333333333333333333333333333333333333333333333"}'::jsonb,
        '{"telegram":"공식 Squid Korea 채널에 한 번만 발행됩니다."}'::jsonb,
        '{"request_hash":"3333333333333333333333333333333333333333333333333333333333333333","mock_mode":false}'::jsonb,
        '{"asset_id":"d3000000-0000-4000-8000-000000000003","filename":"news-card.png","storage_path":"d0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000003/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","width":1080,"height":1080}'::jsonb,
        'exact-telegram-smoke@1'
    );
    version_id := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000003',
        version_id,
        'approved',
        '{}'::text[],
        null,
        'squid-success-approval-smoke'
    );
    request_result := public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000003',
        version_id,
        'd4000000-0000-4000-8000-000000000004'
    );
    begin
        insert into public.publications (
            workspace_id,
            client_id,
            content_item_id,
            content_version_id,
            channel,
            status
        ) values (
            'd0000000-0000-4000-8000-000000000001',
            'squid',
            'd1000000-0000-4000-8000-000000000003',
            version_id,
            'telegram',
            'queued'
        );
        raise exception
            'direct generic publication insert bypassed exact publication';
    exception when unique_violation then null;
    end;
    insert into public.publications (
        workspace_id,
        client_id,
        content_item_id,
        content_version_id,
        channel,
        status
    ) values (
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'd1000000-0000-4000-8000-000000000003',
        version_id,
        'telegram',
        'failed'
    ) returning id into generic_failed_id;
    begin
        update public.publications
        set status = 'queued'
        where id = generic_failed_id;
        raise exception
            'generic failed publication was reactivated beside exact publication';
    exception when unique_violation then null;
    end;
    claim := public.claim_exact_telegram_publication_job(
        'd0000000-0000-4000-8000-000000000001',
        'worker-smoke-02',
        300
    );
    marker := public.mark_exact_telegram_attempt_started(
        (claim ->> 'job_id')::uuid,
        'worker-smoke-02',
        request_sha
    );
    completed := public.complete_exact_telegram_publication_job(
        (claim ->> 'job_id')::uuid,
        'worker-smoke-02',
        request_sha,
        123456789,
        'Squid_Kor_Update',
        provider_date
    );
    if completed ->> 'status' <> 'published'
       or completed ->> 'external_id' <> '123456789'
       or completed ->> 'external_url'
            <> 'https://t.me/squid_kor_update/123456789'
       or not exists (
           select 1 from public.content_items
           where id = 'd1000000-0000-4000-8000-000000000003'
             and current_version_id = version_id
             and status = 'published'
       ) then
        raise exception 'successful exact Telegram completion was not canonical';
    end if;
    replay := public.complete_exact_telegram_publication_job(
        (claim ->> 'job_id')::uuid,
        'worker-smoke-02',
        request_sha,
        123456789,
        'squid_kor_update',
        provider_date
    );
    if replay ->> 'reused' <> 'true'
       or replay ->> 'external_url' is distinct from completed ->> 'external_url' then
        raise exception 'exact Telegram completion replay was not idempotent';
    end if;
    replay_request := public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000003',
        version_id,
        'd4000000-0000-4000-8000-000000000004'
    );
    if replay_request ->> 'status' <> 'published'
       or replay_request ->> 'external_url'
            <> 'https://t.me/squid_kor_update/123456789'
       or replay_request -> 'error_code' <> 'null'::jsonb then
        raise exception 'request replay lost the published Telegram result';
    end if;
end
$test$;

do $test$
declare
    generated jsonb;
    version_id uuid;
    claim jsonb;
begin
    generated := public.record_generated_content(
        'd1000000-0000-4000-8000-000000000004',
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Squid final approval fence',
        '{"request_hash":"4444444444444444444444444444444444444444444444444444444444444444"}'::jsonb,
        '{"telegram":"전송 직전에 현재 승인 상태를 다시 확인합니다."}'::jsonb,
        '{"request_hash":"4444444444444444444444444444444444444444444444444444444444444444","mock_mode":false}'::jsonb,
        '{"asset_id":"d3000000-0000-4000-8000-000000000004","filename":"news-card.png","storage_path":"d0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000004/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","width":1080,"height":1080}'::jsonb,
        'exact-telegram-smoke@1'
    );
    version_id := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000004',
        version_id,
        'approved',
        '{}'::text[],
        null,
        'squid-fence-approval-smoke'
    );
    perform public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000004',
        version_id,
        'd4000000-0000-4000-8000-000000000005'
    );
    claim := public.claim_exact_telegram_publication_job(
        'd0000000-0000-4000-8000-000000000001',
        'worker-smoke-03',
        300
    );
    update public.content_items
    set status = 'rejected'
    where id = 'd1000000-0000-4000-8000-000000000004';
    begin
        perform public.mark_exact_telegram_attempt_started(
            (claim ->> 'job_id')::uuid,
            'worker-smoke-03',
            repeat('e', 64)
        );
        raise exception 'stale approval passed the final provider-attempt fence';
    exception when check_violation then null;
    end;
    if exists (
        select 1 from public.publications
        where id = (claim ->> 'publication_id')::uuid
          and delivery_started_at is not null
    ) then
        raise exception 'failed final approval fence persisted an attempt marker';
    end if;
    begin
        perform public.fail_exact_telegram_publication_job(
            (claim ->> 'job_id')::uuid,
            'worker-smoke-03',
            'secret_leak_payload',
            false
        );
        raise exception 'arbitrary worker error code reached the database';
    exception when invalid_parameter_value then null;
    end;
end
$test$;

do $test$
declare
    generated jsonb;
    version_id uuid;
    claim jsonb;
    recovery jsonb;
begin
    generated := public.record_generated_content(
        'd1000000-0000-4000-8000-000000000005',
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Squid expired delivery fence',
        '{"request_hash":"5555555555555555555555555555555555555555555555555555555555555555"}'::jsonb,
        '{"telegram":"전송 fence 이후 lease가 끝나면 다시 보내지 않습니다."}'::jsonb,
        '{"request_hash":"5555555555555555555555555555555555555555555555555555555555555555","mock_mode":false}'::jsonb,
        '{"asset_id":"d3000000-0000-4000-8000-000000000005","filename":"news-card.png","storage_path":"d0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000005/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","width":1080,"height":1080}'::jsonb,
        'exact-telegram-smoke@1'
    );
    version_id := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000005',
        version_id,
        'approved',
        '{}'::text[],
        null,
        'squid-expired-fence-approval-smoke'
    );
    perform public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000005',
        version_id,
        'd4000000-0000-4000-8000-000000000006'
    );
    claim := public.claim_exact_telegram_publication_job(
        'd0000000-0000-4000-8000-000000000001',
        'worker-smoke-04',
        300
    );
    perform public.mark_exact_telegram_attempt_started(
        (claim ->> 'job_id')::uuid,
        'worker-smoke-04',
        repeat('f', 64)
    );
    update public.jobs
    set lease_expires_at = statement_timestamp() - interval '1 second'
    where id = (claim ->> 'job_id')::uuid;

    recovery := public.reconcile_expired_exact_telegram_publication_leases(
        'd0000000-0000-4000-8000-000000000001',
        10
    );
    if (select count(*) from jsonb_object_keys(recovery)) <> 5
       or not recovery ?& array[
           'workspace_id',
           'reconciled_count',
           'retrying_count',
           'failed_count',
           'delivery_unknown_count'
       ]
       or (recovery ->> 'workspace_id')::uuid
            <> 'd0000000-0000-4000-8000-000000000001'
       or (recovery ->> 'reconciled_count')::integer <> 1
       or (recovery ->> 'retrying_count')::integer <> 0
       or (recovery ->> 'failed_count')::integer <> 0
       or (recovery ->> 'delivery_unknown_count')::integer <> 1
       or (recovery ->> 'reconciled_count')::integer <>
            (recovery ->> 'retrying_count')::integer
            + (recovery ->> 'failed_count')::integer
            + (recovery ->> 'delivery_unknown_count')::integer
       or not exists (
           select 1
           from public.publications as publication
           join public.jobs as job
             on job.input ->> 'publication_id' = publication.id::text
           where publication.id = (claim ->> 'publication_id')::uuid
             and publication.status = 'delivery_unknown'
             and job.status = 'failed'
             and job.last_error_code = 'delivery_outcome_unknown'
       ) then
        raise exception 'expired post-fence lease became claimable again';
    end if;
end
$test$;

do $test$
declare
    generated jsonb;
    version_id uuid;
begin
    generated := public.record_generated_content(
        'd1000000-0000-4000-8000-000000000006',
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Squid generic delivery unknown exclusion',
        '{"request_hash":"6666666666666666666666666666666666666666666666666666666666666666"}'::jsonb,
        '{"telegram":"기존 일반 발행 결과가 불명확하면 exact 전송을 시작하지 않습니다."}'::jsonb,
        '{"request_hash":"6666666666666666666666666666666666666666666666666666666666666666","mock_mode":false}'::jsonb,
        '{"asset_id":"d3000000-0000-4000-8000-000000000006","filename":"news-card.png","storage_path":"d0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000006/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"6666666666666666666666666666666666666666666666666666666666666666","width":1080,"height":1080}'::jsonb,
        'exact-telegram-smoke@1'
    );
    version_id := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000006',
        version_id,
        'approved',
        '{}'::text[],
        null,
        'squid-generic-unknown-approval-smoke'
    );
    insert into public.publications (
        workspace_id,
        client_id,
        content_item_id,
        content_version_id,
        channel,
        status
    ) values (
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'd1000000-0000-4000-8000-000000000006',
        version_id,
        'telegram',
        'delivery_unknown'
    );
    begin
        perform public.request_studio_telegram_publication(
            'd0000000-0000-4000-8000-000000000001',
            'd1000000-0000-4000-8000-000000000006',
            version_id,
            'd4000000-0000-4000-8000-000000000007'
        );
        raise exception
            'exact request bypassed competing generic delivery_unknown';
    exception when unique_violation then null;
    end;
    if exists (
        select 1
        from public.jobs as job
        where job.content_item_id =
                'd1000000-0000-4000-8000-000000000006'
          and job.input ->> 'workflow' = 'exact_telegram_publication_v1'
    ) then
        raise exception
            'competing generic delivery_unknown left an exact publish job';
    end if;
end
$test$;

do $test$
declare
    generated jsonb;
    version_id uuid;
    request_result jsonb;
    claim jsonb;
begin
    generated := public.record_generated_content(
        'd1000000-0000-4000-8000-000000000007',
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Squid asset snapshot drift',
        '{"request_hash":"7777777777777777777777777777777777777777777777777777777777777777"}'::jsonb,
        '{"telegram":"승인된 PNG의 전체 스냅샷이 바뀌면 전송하지 않습니다."}'::jsonb,
        '{"request_hash":"7777777777777777777777777777777777777777777777777777777777777777","mock_mode":false}'::jsonb,
        '{"asset_id":"d3000000-0000-4000-8000-000000000007","filename":"news-card.png","storage_path":"d0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000007/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"7777777777777777777777777777777777777777777777777777777777777777","width":1080,"height":1080}'::jsonb,
        'exact-telegram-smoke@1'
    );
    version_id := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000007',
        version_id,
        'approved',
        '{}'::text[],
        null,
        'squid-asset-drift-approval-smoke'
    );
    request_result := public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000007',
        version_id,
        'd4000000-0000-4000-8000-000000000008'
    );
    update public.assets
    set sha256 = repeat('9', 64)
    where id = 'd3000000-0000-4000-8000-000000000007';
    claim := public.claim_exact_telegram_publication_job(
        'd0000000-0000-4000-8000-000000000001',
        'worker-smoke-05',
        300
    );
    if claim is not null
       or not exists (
           select 1
           from public.jobs as job
           join public.publications as publication
             on publication.id = (job.input ->> 'publication_id')::uuid
           where job.id = (request_result ->> 'job_id')::uuid
             and job.status = 'failed'
             and job.last_error_code = 'approved_asset_invalid'
             and publication.status = 'failed'
       ) then
        raise exception 'asset snapshot drift reached the worker claim';
    end if;
end
$test$;

do $test$
declare
    generated jsonb;
    version_id uuid;
    request_result jsonb;
    claim jsonb;
begin
    generated := public.record_generated_content(
        'd1000000-0000-4000-8000-000000000008',
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Squid Storage object replacement',
        '{"request_hash":"8888888888888888888888888888888888888888888888888888888888888888"}'::jsonb,
        '{"telegram":"같은 경로의 Storage 객체가 교체되어도 전송하지 않습니다."}'::jsonb,
        '{"request_hash":"8888888888888888888888888888888888888888888888888888888888888888","mock_mode":false}'::jsonb,
        '{"asset_id":"d3000000-0000-4000-8000-000000000008","filename":"news-card.png","storage_path":"d0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000008/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"8888888888888888888888888888888888888888888888888888888888888888","width":1080,"height":1080}'::jsonb,
        'exact-telegram-smoke@1'
    );
    version_id := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000008',
        version_id,
        'approved',
        '{}'::text[],
        null,
        'squid-storage-replacement-approval-smoke'
    );
    request_result := public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000008',
        version_id,
        'd4000000-0000-4000-8000-000000000009'
    );
    delete from storage.objects
    where bucket_id = 'content-studio'
      and name =
        'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000008/news-card.png';
    insert into storage.objects (bucket_id, name)
    values (
        'content-studio',
        'd0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000008/news-card.png'
    );
    claim := public.claim_exact_telegram_publication_job(
        'd0000000-0000-4000-8000-000000000001',
        'worker-smoke-06',
        300
    );
    if claim is not null
       or not exists (
           select 1
           from public.jobs as job
           join public.publications as publication
             on publication.id = (job.input ->> 'publication_id')::uuid
           where job.id = (request_result ->> 'job_id')::uuid
             and job.status = 'failed'
             and job.last_error_code = 'approved_asset_invalid'
             and publication.status = 'failed'
       ) then
        raise exception 'Storage object replacement reached the worker claim';
    end if;
end
$test$;

do $test$
declare
    generated jsonb;
    version_id uuid;
    request_result jsonb;
    claim jsonb;
    recovery jsonb;
begin
    generated := public.record_generated_content(
        'd1000000-0000-4000-8000-000000000009',
        'd0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Squid recovery-only pre-attempt lease',
        '{"request_hash":"9999999999999999999999999999999999999999999999999999999999999999"}'::jsonb,
        '{"telegram":"provider 호출 전 만료된 lease는 복구 RPC가 재시도 상태로 돌립니다."}'::jsonb,
        '{"request_hash":"9999999999999999999999999999999999999999999999999999999999999999","mock_mode":false}'::jsonb,
        '{"asset_id":"d3000000-0000-4000-8000-000000000009","filename":"news-card.png","storage_path":"d0000000-0000-4000-8000-000000000001/squid/d3000000-0000-4000-8000-000000000009/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"9999999999999999999999999999999999999999999999999999999999999999","width":1080,"height":1080}'::jsonb,
        'exact-telegram-smoke@1'
    );
    version_id := (generated ->> 'content_version_id')::uuid;
    perform public.record_studio_content_review(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000009',
        version_id,
        'approved',
        '{}'::text[],
        null,
        'squid-recovery-approval-smoke'
    );
    request_result := public.request_studio_telegram_publication(
        'd0000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000009',
        version_id,
        'd4000000-0000-4000-8000-000000000010'
    );
    claim := public.claim_exact_telegram_publication_job(
        'd0000000-0000-4000-8000-000000000001',
        'worker-smoke-07',
        300
    );
    update public.jobs
    set lease_expires_at = statement_timestamp() - interval '1 second'
    where id = (claim ->> 'job_id')::uuid;
    recovery := public.reconcile_expired_exact_telegram_publication_leases(
        'd0000000-0000-4000-8000-000000000001',
        10
    );
    if (select count(*) from jsonb_object_keys(recovery)) <> 5
       or not recovery ?& array[
           'workspace_id',
           'reconciled_count',
           'retrying_count',
           'failed_count',
           'delivery_unknown_count'
       ]
       or (recovery ->> 'reconciled_count')::integer <> 1
       or (recovery ->> 'retrying_count')::integer <> 1
       or (recovery ->> 'failed_count')::integer <> 0
       or (recovery ->> 'delivery_unknown_count')::integer <> 0
       or (recovery ->> 'reconciled_count')::integer <>
            (recovery ->> 'retrying_count')::integer
            + (recovery ->> 'failed_count')::integer
            + (recovery ->> 'delivery_unknown_count')::integer
       or not exists (
           select 1
           from public.jobs as job
           join public.publications as publication
             on publication.id = (job.input ->> 'publication_id')::uuid
           where job.id = (request_result ->> 'job_id')::uuid
             and job.status = 'retrying'
             and job.attempts = 1
             and publication.status = 'queued'
             and publication.delivery_started_at is null
       ) then
        raise exception 'recovery-only pre-attempt lease was not retried';
    end if;
end
$test$;

rollback;
