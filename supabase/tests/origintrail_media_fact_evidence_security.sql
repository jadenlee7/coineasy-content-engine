-- Transactional security smoke for reviewed OriginTrail media evidence.
-- Run after every migration as the database owner. Every test row rolls back.

begin;

do $privileges$
declare
    role_name text;
    table_privilege text;
begin
    if to_regprocedure(
        'public.get_origintrail_reviewed_source_evidence(uuid,uuid,text)'
    ) is null
       or to_regprocedure(
           'agent_runtime.origintrail_reviewed_media_evidence(uuid)'
       ) is null
       or to_regprocedure(
           'agent_runtime.enforce_origintrail_media_fact_evidence()'
       ) is null then
        raise exception 'OriginTrail media evidence routines are missing';
    end if;

    if not has_function_privilege(
        'service_role',
        'public.get_origintrail_reviewed_source_evidence(uuid,uuid,text)',
        'execute'
    ) or not has_function_privilege(
        'coineasy_batch_producer',
        'public.get_origintrail_reviewed_source_evidence(uuid,uuid,text)',
        'execute'
    ) then
        raise exception 'media evidence RPC is unavailable to its producer';
    end if;

    foreach role_name in array array[
        'public',
        'anon',
        'authenticated',
        'coineasy_batch_dispatcher',
        'coineasy_batch_reviewer',
        'coineasy_buzz_delivery'
    ]
    loop
        if has_function_privilege(
            role_name,
            'public.get_origintrail_reviewed_source_evidence(uuid,uuid,text)',
            'execute'
        ) then
            raise exception 'media evidence RPC leaked to %', role_name;
        end if;
    end loop;

    foreach role_name in array array[
        'public',
        'anon',
        'authenticated',
        'service_role',
        'coineasy_batch_dispatcher',
        'coineasy_batch_producer',
        'coineasy_batch_reviewer',
        'coineasy_buzz_delivery'
    ]
    loop
        if has_function_privilege(
            role_name,
            'agent_runtime.origintrail_reviewed_media_evidence(uuid)',
            'execute'
        ) or has_function_privilege(
            role_name,
            'agent_runtime.enforce_origintrail_media_fact_evidence()',
            'execute'
        ) or has_function_privilege(
            role_name,
            'agent_runtime.canonical_json_text(jsonb)',
            'execute'
        ) then
            raise exception 'internal media evidence routine leaked to %', role_name;
        end if;

        foreach table_privilege in array array[
            'select', 'insert', 'update', 'delete', 'truncate'
        ]
        loop
            if has_table_privilege(
                role_name,
                'private.origintrail_reviewed_source_evidence',
                table_privilege
            ) then
                raise exception 'registry % leaked to %',
                    table_privilege, role_name;
            end if;
        end loop;
    end loop;

    if not exists (
        select 1
        from pg_catalog.pg_class as relation
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'private'
          and relation.relname = 'origintrail_reviewed_source_evidence'
          and relation.relrowsecurity
          and relation.relforcerowsecurity
    ) or exists (
        select 1
        from pg_catalog.pg_policies
        where schemaname = 'private'
          and tablename = 'origintrail_reviewed_source_evidence'
    ) then
        raise exception 'reviewed media registry RLS boundary is open';
    end if;
end
$privileges$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'f0000000-0000-4000-8000-000000000001',
    'OriginTrail Media Evidence Security Test',
    'origintrail-media-evidence-security-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'OriginTrail',
    true,
    null
);

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, active, created_by
)
values (
    'f1000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'x',
    'OriginTrail official X',
    'https://x.com/origin_trail',
    '@origin_trail',
    15,
    true,
    null
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, media, raw_payload,
    source_hash, ingested_by
)
values (
    'f2000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'f1000000-0000-4000-8000-000000000001',
    '2085782218815775024',
    'tweet',
    'https://x.com/origin_trail/status/2085782218815775024',
    '@origin_trail',
    '2026-08-07T17:36:47Z',
    'Day 1: @PrimeIntellect''s Prime Agent launches — past human-expert baseline on ARC-AGI-3.'
        || E' \n\n'
        || 'Day 3: its fleets get Shared Context Graphs on @origin_trail.'
        || E' \n\n'
        || 'Every agent writes to one verifiable graph. Each run compounds the next. Owned, not rented.'
        || E' \n\n'
        || '🔌Open-source adapter, live. https://t.co/VUbwVopCEY',
    jsonb_build_array(jsonb_build_object(
        'media_key', '13_2085781578374860800',
        'type', 'video',
        'url',
            'https://pbs.twimg.com/amplify_video_thumb/2085781578374860800/img/vH2LVZnApTMbJhq2.jpg',
        'width', 1920,
        'height', 1920
    )),
    '{"is_note_tweet":false,"metrics":{}}'::jsonb,
    pg_catalog.md5('official-x:@origin_trail:2085782218815775024'),
    null
);

insert into private.origintrail_standalone_sources (
    workspace_id, client_id, source_item_id, is_quote,
    first_poll_request_id
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'f2000000-0000-4000-8000-000000000001',
    false,
    'f3000000-0000-4000-8000-000000000001'
);

insert into public.jobs (
    id, workspace_id, client_id, job_kind, status, priority, input, output,
    idempotency_key, attempts, max_attempts, available_at
)
select
    'f4000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'generate',
    'queued',
    0,
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'kst_date',
            (statement_timestamp() at time zone 'Asia/Seoul')::date,
        'source_item_ids', jsonb_build_array(source.id),
        'content_kind', 'daily_news',
        'request_id', 'f5000000-0000-4000-8000-000000000001'::uuid,
        'source_content', source.body,
        'source_url', source.canonical_url,
        'source_image_url', source.media -> 0 ->> 'url',
        'manual_only', false
    ),
    '{}'::jsonb,
    'origintrail-reviewed-media-security-test',
    0,
    3,
    statement_timestamp()
from public.source_items as source
where source.id = 'f2000000-0000-4000-8000-000000000001';

do $media_path$
declare
    test_workspace_id constant uuid :=
        'f0000000-0000-4000-8000-000000000001';
    media_job_id constant uuid :=
        'f4000000-0000-4000-8000-000000000001';
    worker_id constant text := 'official-x:reviewed-media-test';
    source_body text;
    source_url text;
    source_image_url text;
    claimed jsonb;
    evidence jsonb;
    provider_input jsonb;
    input_payload jsonb;
    input_sha256 constant text := repeat('b', 64);
    route_receipt jsonb;
    handoff_receipt jsonb;
    detail jsonb;
    negative_job_id uuid;
    negative_input jsonb;
    negative_payload jsonb;
begin
    select source.body, source.canonical_url, source.media -> 0 ->> 'url'
      into source_body, source_url, source_image_url
    from public.source_items as source
    where source.id = 'f2000000-0000-4000-8000-000000000001';

    if encode(
        extensions.digest(
            pg_catalog.convert_to(source_body, 'UTF8'),
            'sha256'
        ),
        'hex'
    ) <> 'aa1676bb2f98b8f35ee7de430c161c9a4ba39a8d4a9c728b8abd93dba3655d74' then
        raise exception 'reviewed source body hash is not exact';
    end if;

    claimed := public.claim_review_draft_job(
        test_workspace_id,
        worker_id,
        900
    );
    if claimed ->> 'job_id' <> media_job_id::text
       or claimed -> 'origintrail_batch_eligible' <> 'true'::jsonb
       or claimed -> 'input' ->> 'source_image_url' <> source_image_url then
        raise exception 'reviewed media job was not Batch-eligible at claim';
    end if;

    if public.get_origintrail_reviewed_source_evidence(
        test_workspace_id,
        media_job_id,
        'official-x:wrong-worker'
    ) is not null
       or public.get_origintrail_reviewed_source_evidence(
           'f0000000-0000-4000-8000-000000000002',
           media_job_id,
           worker_id
       ) is not null then
        raise exception 'media evidence RPC crossed its workspace or lease';
    end if;

    evidence := public.get_origintrail_reviewed_source_evidence(
        test_workspace_id,
        media_job_id,
        worker_id
    );
    if jsonb_typeof(evidence) <> 'object'
       or (select count(*) from jsonb_object_keys(evidence)) <> 2
       or evidence -> 'payload' ->> 'source_url' <> source_url
       or evidence -> 'payload' ->> 'source_content_sha256' <>
            encode(
                extensions.digest(
                    pg_catalog.convert_to(source_body, 'UTF8'),
                    'sha256'
                ),
                'hex'
            )
       or evidence -> 'payload' -> 'media' ->> 'recorded_url'
            <> source_image_url
       or evidence -> 'payload' -> 'media' -> 'factual_evidence'
            <> 'false'::jsonb
       or evidence ->> 'evidence_sha256' <>
            encode(
                extensions.digest(
                    pg_catalog.convert_to(
                        agent_runtime.canonical_json_text(
                            evidence -> 'payload'
                        ),
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            ) then
        raise exception 'lease-fenced media evidence envelope is invalid';
    end if;

    update public.jobs
    set lease_expires_at = statement_timestamp() - interval '1 second'
    where id = media_job_id;
    if public.get_origintrail_reviewed_source_evidence(
        test_workspace_id,
        media_job_id,
        worker_id
    ) is not null then
        raise exception 'expired lease read reviewed media evidence';
    end if;
    update public.jobs
    set lease_expires_at = statement_timestamp() + interval '15 minutes'
    where id = media_job_id;

    provider_input := jsonb_build_object(
        'client_id', 'origintrail',
        'content_kind', 'daily_news',
        'request_id', 'f5000000-0000-4000-8000-000000000001'::uuid,
        'source', jsonb_build_object(
            'content', source_body,
            'content_sha256',
                encode(
                    extensions.digest(
                        pg_catalog.convert_to(source_body, 'UTF8'),
                        'sha256'
                    ),
                    'hex'
                ),
            'url', source_url,
            'image_url', source_image_url
        ),
        'style_reference_pack', '{}'::jsonb,
        'fact_check_evidence', evidence
    );
    input_payload := jsonb_build_object(
        'instructions', 'Return review-only Korean copy.',
        'input', provider_input::text,
        'output_schema', '{}'::jsonb,
        'estimated_output_tokens', 200,
        'risk_tier', 'T1',
        'approval_required', true,
        'interactive', false,
        'incident_or_release_blocker', false,
        'live_tools_required', false,
        'source_snapshot_complete', true,
        'input_immutable', true,
        'retry_idempotent', true,
        'remaining_batch_stages', 1,
        'request_sha256', repeat('e', 64)
    );

    perform public.configure_agent_batch_budget(
        test_workspace_id,
        'batch-general:reviewed-media-test',
        statement_timestamp() - interval '1 hour',
        statement_timestamp() + interval '23 hours',
        100000
    );
    perform public.queue_agent_batch_job(
        test_workspace_id,
        'origintrail',
        media_job_id,
        repeat('a', 64),
        'origintrail_client_agent',
        'official_source_nonurgent_pack',
        'generate',
        0::smallint,
        'batch_24h',
        'gpt-5.6-luna',
        'S',
        statement_timestamp() + interval '30 hours',
        input_payload,
        input_sha256,
        500::bigint,
        500,
        10000::bigint,
        'batch-general:reviewed-media-test',
        media_job_id::text || ':generate:1',
        false
    );

    if not exists (
        select 1
        from agent_runtime.batch_jobs as batch_job
        where batch_job.job_id = media_job_id
          and (batch_job.input_payload ->> 'input')::jsonb
                -> 'fact_check_evidence' = evidence
    ) then
        raise exception 'exact reviewed media sidecar was not admitted';
    end if;

    -- Missing sidecar must fail at the table boundary.
    negative_job_id := 'f4000000-0000-4000-8000-000000000002';
    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority, input,
        output, idempotency_key, attempts, max_attempts, available_at
    )
    select
        negative_job_id, test_workspace_id, 'origintrail', 'generate', 'queued',
        0, job.input, '{}'::jsonb, 'media-negative-missing-sidecar',
        0, 3, statement_timestamp()
    from public.jobs as job where job.id = media_job_id;
    negative_input := provider_input - 'fact_check_evidence';
    negative_payload := jsonb_set(
        input_payload,
        '{input}',
        to_jsonb(negative_input::text)
    );
    begin
        perform public.queue_agent_batch_job(
            test_workspace_id, 'origintrail', negative_job_id, repeat('c', 64),
            'origintrail_client_agent', 'official_source_nonurgent_pack',
            'generate', 0::smallint, 'batch_24h', 'gpt-5.6-luna', 'S',
            statement_timestamp() + interval '30 hours', negative_payload,
            repeat('1', 64), 500::bigint, 500, 10000::bigint,
            'batch-general:reviewed-media-test',
            negative_job_id::text || ':generate:1', false
        );
        raise exception 'media input without its sidecar was admitted';
    exception when check_violation then null;
    end;

    -- An exact sidecar plus one unreviewed key must also fail.
    negative_job_id := 'f4000000-0000-4000-8000-000000000003';
    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority, input,
        output, idempotency_key, attempts, max_attempts, available_at
    )
    select
        negative_job_id, test_workspace_id, 'origintrail', 'generate', 'queued',
        0, job.input, '{}'::jsonb, 'media-negative-expanded-sidecar',
        0, 3, statement_timestamp()
    from public.jobs as job where job.id = media_job_id;
    negative_input := jsonb_set(
        provider_input,
        '{fact_check_evidence,unexpected}',
        'true'::jsonb
    );
    negative_payload := jsonb_set(
        input_payload,
        '{input}',
        to_jsonb(negative_input::text)
    );
    begin
        perform public.queue_agent_batch_job(
            test_workspace_id, 'origintrail', negative_job_id,
            repeat('c', 63) || '3',
            'origintrail_client_agent', 'official_source_nonurgent_pack',
            'generate', 0::smallint, 'batch_24h', 'gpt-5.6-luna', 'S',
            statement_timestamp() + interval '30 hours', negative_payload,
            repeat('2', 64), 500::bigint, 500, 10000::bigint,
            'batch-general:reviewed-media-test',
            negative_job_id::text || ':generate:1', false
        );
        raise exception 'expanded media evidence sidecar was admitted';
    exception when check_violation then null;
    end;

    -- Hash substitution must fail even when the payload is unchanged.
    negative_job_id := 'f4000000-0000-4000-8000-000000000004';
    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority, input,
        output, idempotency_key, attempts, max_attempts, available_at
    )
    select
        negative_job_id, test_workspace_id, 'origintrail', 'generate', 'queued',
        0, job.input, '{}'::jsonb, 'media-negative-hash-substitution',
        0, 3, statement_timestamp()
    from public.jobs as job where job.id = media_job_id;
    negative_input := jsonb_set(
        provider_input,
        '{fact_check_evidence,evidence_sha256}',
        to_jsonb(repeat('0', 64))
    );
    negative_payload := jsonb_set(
        input_payload,
        '{input}',
        to_jsonb(negative_input::text)
    );
    begin
        perform public.queue_agent_batch_job(
            test_workspace_id, 'origintrail', negative_job_id,
            repeat('c', 63) || '4',
            'origintrail_client_agent', 'official_source_nonurgent_pack',
            'generate', 0::smallint, 'batch_24h', 'gpt-5.6-luna', 'S',
            statement_timestamp() + interval '30 hours', negative_payload,
            repeat('3', 64), 500::bigint, 500, 10000::bigint,
            'batch-general:reviewed-media-test',
            negative_job_id::text || ':generate:1', false
        );
        raise exception 'media evidence hash substitution was admitted';
    exception when check_violation then null;
    end;

    -- The job's durable media identity must still match the registry.
    negative_job_id := 'f4000000-0000-4000-8000-000000000005';
    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority, input,
        output, idempotency_key, attempts, max_attempts, available_at
    )
    select
        negative_job_id, test_workspace_id, 'origintrail', 'generate', 'queued',
        0,
        jsonb_set(
            job.input,
            '{source_image_url}',
            to_jsonb('https://pbs.twimg.com/media/unreviewed.jpg'::text)
        ),
        '{}'::jsonb,
        'media-negative-source-substitution',
        0, 3, statement_timestamp()
    from public.jobs as job where job.id = media_job_id;
    negative_input := jsonb_set(
        provider_input,
        '{source,image_url}',
        to_jsonb('https://pbs.twimg.com/media/unreviewed.jpg'::text)
    );
    negative_payload := jsonb_set(
        input_payload,
        '{input}',
        to_jsonb(negative_input::text)
    );
    begin
        perform public.queue_agent_batch_job(
            test_workspace_id, 'origintrail', negative_job_id,
            repeat('c', 63) || '5',
            'origintrail_client_agent', 'official_source_nonurgent_pack',
            'generate', 0::smallint, 'batch_24h', 'gpt-5.6-luna', 'S',
            statement_timestamp() + interval '30 hours', negative_payload,
            repeat('4', 64), 500::bigint, 500, 10000::bigint,
            'batch-general:reviewed-media-test',
            negative_job_id::text || ':generate:1', false
        );
        raise exception 'unreviewed durable media identity was admitted';
    exception when check_violation then null;
    end;

    -- A text-only job may never carry the media evidence sidecar.
    negative_job_id := 'f4000000-0000-4000-8000-000000000006';
    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority, input,
        output, idempotency_key, attempts, max_attempts, available_at
    )
    select
        negative_job_id, test_workspace_id, 'origintrail', 'generate', 'queued',
        0,
        jsonb_set(job.input, '{source_image_url}', to_jsonb(''::text)),
        '{}'::jsonb,
        'text-negative-media-sidecar',
        0, 3, statement_timestamp()
    from public.jobs as job where job.id = media_job_id;
    begin
        perform public.queue_agent_batch_job(
            test_workspace_id, 'origintrail', negative_job_id,
            repeat('c', 63) || '6',
            'origintrail_client_agent', 'official_source_nonurgent_pack',
            'generate', 0::smallint, 'batch_24h', 'gpt-5.6-luna', 'S',
            statement_timestamp() + interval '30 hours', input_payload,
            repeat('5', 64), 500::bigint, 500, 10000::bigint,
            'batch-general:reviewed-media-test',
            negative_job_id::text || ':generate:1', false
        );
        raise exception 'text-only job carried media fact evidence';
    exception when check_violation then null;
    end;

    route_receipt := public.bind_review_draft_execution_plane(
        media_job_id,
        worker_id,
        'openai_batch'
    );
    if route_receipt ->> 'execution_plane' <> 'openai_batch'
       or not agent_runtime.has_recoverable_origintrail_handoff(
            media_job_id
       ) then
        raise exception 'reviewed media handoff is not safely recoverable';
    end if;

    handoff_receipt := public.complete_review_draft_batch_handoff(
        media_job_id,
        worker_id,
        media_job_id,
        input_sha256
    );
    if handoff_receipt ->> 'status' <> 'succeeded'
       or handoff_receipt ->> 'input_sha256' <> input_sha256 then
        raise exception 'reviewed media Batch handoff did not commit';
    end if;

    update agent_runtime.batch_jobs
    set status = 'completed',
        reservation_state = 'settled',
        actual_input_tokens = 1000,
        actual_output_tokens = 200,
        actual_cost_microusd = 3000,
        result_code = 'needs_review',
        result_payload = jsonb_build_object(
            'headline_ko', '검토된 OriginTrail 미디어 업데이트',
            'body_ko', '사람의 이중 사실 확인이 필요한 본문입니다.',
            'x_copy_ko', '검토가 필요한 X 초안입니다.',
            'telegram_copy_ko', '검토가 필요한 텔레그램 초안입니다.'
        ),
        finished_at = statement_timestamp()
    where job_id = media_job_id;

    detail := public.get_agent_batch_review_item(
        test_workspace_id,
        media_job_id
    );
    if detail is null
       or detail -> 'fact_check_evidence' is distinct from evidence
       or detail ->> 'source_content' <> source_body
       or detail ->> 'source_url' <> source_url
       or detail ? 'input_payload' then
        raise exception 'review detail did not expose only bound media evidence';
    end if;
end
$media_path$;

do $immutable_registry$
begin
    begin
        update private.origintrail_reviewed_source_evidence
        set verified_at = verified_at
        where source_external_id = '2085782218815775024';
        raise exception 'reviewed media registry accepted update';
    exception when sqlstate '55000' then null;
    end;

    begin
        delete from private.origintrail_reviewed_source_evidence
        where source_external_id = '2085782218815775024';
        raise exception 'reviewed media registry accepted delete';
    exception when sqlstate '55000' then null;
    end;

    begin
        truncate table private.origintrail_reviewed_source_evidence;
        raise exception 'reviewed media registry accepted truncate';
    exception when sqlstate '55000' then null;
    end;

    if not exists (
        select 1
        from private.origintrail_reviewed_source_evidence
        where source_external_id = '2085782218815775024'
    ) then
        raise exception 'reviewed media registry row was not preserved';
    end if;
end
$immutable_registry$;

rollback;
