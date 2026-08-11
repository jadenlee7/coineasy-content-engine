-- Transactional security and idempotency smoke for the durable Buzz review
-- acknowledgement outbox. No relay, publication, provider, or network call is
-- made and every fixture/state transition rolls back.

begin;

do $test$
declare
    signature text;
begin
    foreach signature in array array[
        'public.list_origintrail_buzz_review_targets(uuid,integer,bigint,text)',
        'public.record_origintrail_buzz_review_decision(uuid,uuid,text,uuid,text,text,text,bigint,text,text,text,text,text,bigint)',
        'public.record_origintrail_buzz_review_decision_with_ack(uuid,uuid,text,uuid,text,text,text,bigint,text,text,text,text,text,bigint)',
        'public.claim_origintrail_buzz_review_ack(uuid,uuid,text,integer)',
        'public.mark_origintrail_buzz_review_ack_attempt(uuid,uuid,text,text,text)',
        'public.complete_origintrail_buzz_review_ack(uuid,uuid,text,text,text,boolean)',
        'public.fail_origintrail_buzz_review_ack(uuid,uuid,text,text,boolean)',
        'public.reconcile_origintrail_buzz_review_ack_leases(uuid,integer)',
        'public.list_origintrail_buzz_review_ack_unknown(uuid,integer)'
    ]
    loop
        if has_function_privilege('anon', signature, 'execute')
           or has_function_privilege('authenticated', signature, 'execute') then
            raise exception 'Buzz review RPC leaked to browser role: %', signature;
        end if;
        if not has_function_privilege('service_role', signature, 'execute')
           or not has_function_privilege(
                'coineasy_buzz_review_decider', signature, 'execute'
           ) then
            raise exception 'Buzz review RPC unavailable to required server role: %', signature;
        end if;
    end loop;
    if has_table_privilege(
        'service_role', 'agent_runtime.buzz_review_decisions', 'select'
    ) or has_table_privilege(
        'coineasy_buzz_review_decider',
        'agent_runtime.buzz_review_decisions', 'insert'
    ) or has_table_privilege(
        'coineasy_buzz_review_decider',
        'agent_runtime.buzz_review_decisions', 'select'
    ) or has_table_privilege(
        'service_role', 'agent_runtime.buzz_review_ack_receipts', 'select'
    ) or has_table_privilege(
        'coineasy_buzz_review_decider',
        'agent_runtime.buzz_review_ack_receipts', 'insert'
    ) or has_table_privilege(
        'coineasy_buzz_review_decider',
        'agent_runtime.buzz_review_ack_receipts', 'select'
    ) then
        raise exception 'Buzz review or acknowledgement table leaked direct access';
    end if;
    if not exists (
        select 1
        from pg_catalog.pg_class as relation
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'agent_runtime'
          and relation.relname = 'buzz_review_decisions'
          and relation.relrowsecurity
          and relation.relforcerowsecurity
    ) then
        raise exception 'Buzz review decisions do not force RLS';
    end if;
    if not exists (
        select 1
        from pg_catalog.pg_class as relation
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'agent_runtime'
          and relation.relname = 'buzz_review_ack_receipts'
          and relation.relrowsecurity
          and relation.relforcerowsecurity
    ) then
        raise exception 'Buzz review acknowledgement receipts do not force RLS';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'f0000000-0000-4000-8000-000000000001',
    'OriginTrail Buzz Review Test',
    'origintrail-buzz-review-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'origintrail', 'OriginTrail', true, null
);

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, active, created_by
)
values (
    'f4000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail', 'x', 'OriginTrail Buzz review source',
    'https://x.com/origin_trail', '@origin_trail', 15, true, null
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, title, body, media,
    raw_payload, source_hash, ingested_by
)
values (
    'f5000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'f4000000-0000-4000-8000-000000000001',
    '2082883998829752783', 'tweet',
    'https://x.com/origin_trail/status/2082883998829752783',
    '@origin_trail', statement_timestamp() - interval '1 hour',
    'Verifiable AI needs trusted knowledge',
    E'[X Article]\nTitle: Verifiable AI needs trusted knowledge\nOriginTrail DKG connects claims to their original sources so agents can verify context before using it.',
    '[]'::jsonb,
    jsonb_build_object(
        'is_note_tweet', false,
        'is_quote', false,
        'is_retweet', false,
        'is_reply', false,
        'article_id', '2082883998829752000'
    ),
    pg_catalog.md5('origintrail-buzz-review-source'),
    null
);

insert into private.origintrail_standalone_sources (
    workspace_id, client_id, source_item_id, is_quote,
    first_poll_request_id, verified_at
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'f5000000-0000-4000-8000-000000000001',
    false,
    'f6000000-0000-4000-8000-000000000001',
    statement_timestamp() - interval '1 hour'
);

insert into private.official_x_poll_receipts (
    workspace_id, client_id, poll_request_id, source_feed_id,
    expected_cursor, next_cursor, payload_hash, source_item_ids,
    inserted_count, polled_at
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'f6000000-0000-4000-8000-000000000001',
    'f4000000-0000-4000-8000-000000000001',
    null,
    '2082883998829752783',
    pg_catalog.md5('origintrail-buzz-review-poll'),
    array['f5000000-0000-4000-8000-000000000001'::uuid],
    1,
    statement_timestamp() - interval '1 hour'
);

insert into private.origintrail_x_article_evidence (
    workspace_id, client_id, source_item_id, external_id, article_id,
    article_url, title, source_content_sha256, retrieval_method,
    first_poll_request_id, recorded_at
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'origintrail',
    'f5000000-0000-4000-8000-000000000001',
    '2082883998829752783',
    '2082883998829752000',
    'https://x.com/i/article/2082883998829752000',
    'Verifiable AI needs trusted knowledge',
    encode(extensions.digest(pg_catalog.convert_to(
        E'[X Article]\nTitle: Verifiable AI needs trusted knowledge\nOriginTrail DKG connects claims to their original sources so agents can verify context before using it.',
        'UTF8'
    ), 'sha256'), 'hex'),
    'x_api_post_lookup',
    'f6000000-0000-4000-8000-000000000001',
    statement_timestamp() - interval '1 hour'
);

insert into agent_runtime.batch_budgets (
    workspace_id, budget_key, period_start, period_end,
    hard_limit_microusd, reserved_microusd, spent_microusd
)
values (
    'f0000000-0000-4000-8000-000000000001',
    'buzz-review-security:2099-01-01',
    statement_timestamp() - interval '1 hour',
    statement_timestamp() + interval '23 hours',
    50000, 0, 2200
);

insert into public.jobs (
    id, workspace_id, client_id, job_kind, status, input, output,
    attempts, finished_at
)
values (
    'f1000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail', 'generate', 'succeeded',
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'content_kind', 'daily_news',
        'manual_only', false,
        'request_id', 'f6000000-0000-4000-8000-000000000001'::uuid,
        'source_item_ids', jsonb_build_array(
            'f5000000-0000-4000-8000-000000000001'::uuid
        ),
        'source_content',
            E'[X Article]\nTitle: Verifiable AI needs trusted knowledge\nOriginTrail DKG connects claims to their original sources so agents can verify context before using it.',
        'source_url',
            'https://x.com/origin_trail/status/2082883998829752783'
    ),
    jsonb_build_object(
        'workflow', 'agent_batch_review_handoff_v1',
        'handoff', 'openai_batch',
        'batch_job_id', 'f1000000-0000-4000-8000-000000000001'::uuid,
        'input_sha256', repeat('1', 64),
        'review_state', 'pending'
    ),
    1, statement_timestamp()
);

insert into agent_runtime.batch_jobs (
    job_id, workspace_id, client_id, idempotency_key, custom_id,
    agent_id, workflow_kind, stage, priority, latency_class, model,
    model_tier, deadline_at, input_payload, input_sha256,
    estimated_input_tokens, max_output_tokens, max_cost_microusd,
    budget_key, status, reservation_state, attempts,
    actual_input_tokens, actual_output_tokens, actual_cost_microusd,
    result_code, result_payload, finished_at
)
values (
    'f1000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'origintrail', repeat('1', 64),
    'f1000000-0000-4000-8000-000000000001:generate:1',
    'origintrail_client_agent', 'official_source_nonurgent_pack',
    'generate', 3, 'batch_24h', 'gpt-5.6-luna', 'S',
    statement_timestamp() + interval '24 hours',
    jsonb_build_object(
        'approval_required', true,
        'input_immutable', true,
        'source_snapshot_complete', true,
        'input', jsonb_build_object(
            'client_id', 'origintrail',
            'content_kind', 'daily_news',
            'request_id', 'f6000000-0000-4000-8000-000000000001'::uuid,
            'source', jsonb_build_object(
                'content',
                    E'[X Article]\nTitle: Verifiable AI needs trusted knowledge\nOriginTrail DKG connects claims to their original sources so agents can verify context before using it.',
                'content_sha256', encode(extensions.digest(
                    pg_catalog.convert_to(
                        E'[X Article]\nTitle: Verifiable AI needs trusted knowledge\nOriginTrail DKG connects claims to their original sources so agents can verify context before using it.',
                        'UTF8'
                    ),
                    'sha256'
                ), 'hex'),
                'url',
                    'https://x.com/origin_trail/status/2082883998829752783'
            )
        )::text
    ),
    repeat('1', 64), 1000, 1000, 2200,
    'buzz-review-security:2099-01-01', 'completed', 'settled', 1,
    500, 200, 2200, 'needs_review',
    jsonb_build_object(
        'headline_ko', 'OriginTrail 검토 제목',
        'body_ko', '검토 본문',
        'x_copy_ko', 'X 검토 문구',
        'telegram_copy_ko', repeat('가', 1024)
    ),
    statement_timestamp()
);

do $test$
declare
    test_workspace_id constant uuid :=
        'f0000000-0000-4000-8000-000000000001';
    test_job_id constant uuid :=
        'f1000000-0000-4000-8000-000000000001';
    content_item_id constant uuid :=
        'f6000000-0000-4000-8000-000000000001';
    content_version_id constant uuid :=
        'f7000000-0000-4000-8000-000000000001';
    asset_id constant uuid :=
        'f8000000-0000-4000-8000-000000000001';
    source_item_id constant uuid :=
        'f5000000-0000-4000-8000-000000000001';
    input_sha256 constant text := repeat('1', 64);
    banner_sha256 constant text := repeat('8', 64);
    result_payload jsonb;
    result_sha256 text;
    source_content_sha256 text;
    review_pack_sha256 text;
    binding jsonb;
    replay jsonb;
begin
    select batch.result_payload into result_payload
    from agent_runtime.batch_jobs as batch
    where batch.workspace_id = test_workspace_id
      and batch.job_id = test_job_id;
    result_sha256 := encode(extensions.digest(
        pg_catalog.convert_to(result_payload::text, 'UTF8'), 'sha256'
    ), 'hex');
    source_content_sha256 := encode(extensions.digest(
        pg_catalog.convert_to(
            E'[X Article]\nTitle: Verifiable AI needs trusted knowledge\nOriginTrail DKG connects claims to their original sources so agents can verify context before using it.',
            'UTF8'
        ),
        'sha256'
    ), 'hex');
    review_pack_sha256 := private.origintrail_review_pack_sha256(
        test_workspace_id,
        test_job_id,
        content_item_id,
        source_item_id,
        input_sha256,
        result_sha256,
        source_content_sha256,
        banner_sha256
    );

    insert into public.content_items (
        id, workspace_id, client_id, content_kind, title, status, created_by
    ) values (
        content_item_id,
        test_workspace_id,
        'origintrail',
        'daily_news',
        'OriginTrail 검토 제목',
        'needs_review',
        null
    );

    insert into public.content_versions (
        id, workspace_id, content_item_id, version_number, prompt_version,
        locale, title, content, channel_copy, deliverables, generation_meta,
        created_by
    ) values (
        content_version_id,
        test_workspace_id,
        content_item_id,
        1,
        'origintrail-batch-review-pack@1',
        'ko-KR',
        'OriginTrail 검토 제목',
        jsonb_build_object(
            'headline_ko', result_payload ->> 'headline_ko',
            'body_ko', result_payload ->> 'body_ko',
            'source_url',
                'https://x.com/origin_trail/status/2082883998829752783',
            'request_hash', review_pack_sha256
        ),
        jsonb_build_object(
            'telegram', result_payload ->> 'telegram_copy_ko',
            'x', result_payload ->> 'x_copy_ko'
        ),
        jsonb_build_object(
            'primary_asset_id', asset_id::text,
            'asset_ids', jsonb_build_array(asset_id::text)
        ),
        jsonb_build_object(
            'request_hash', review_pack_sha256,
            'mock_mode', false,
            'renderer', 'origintrail-deterministic-svg',
            'batch_job_id', test_job_id,
            'batch_input_sha256', input_sha256,
            'batch_result_sha256', result_sha256,
            'banner_sha256', banner_sha256,
            'fact_check', jsonb_build_object(
                'schema_version', '1.0',
                'policy_version', 'double-fact-check@1',
                'content_kind', 'daily_news',
                'status', 'review',
                'human_review_required', true,
                'input_sha256', input_sha256,
                'output_sha256', result_sha256,
                'checks', jsonb_build_array(
                    jsonb_build_object(
                        'id', 'source_evidence',
                        'status', 'review',
                        'label', 'Immutable X Article evidence',
                        'detail', 'Human verification remains required.',
                        'metrics', jsonb_build_object(
                            'source_count', 1,
                            'immutable_evidence_count', 1
                        )
                    ),
                    jsonb_build_object(
                        'id', 'output_claims',
                        'status', 'pass',
                        'label', 'Batch output structure',
                        'detail', 'All bounded result fields are present.',
                        'metrics', jsonb_build_object(
                            'telegram_characters', 1024
                        )
                    )
                )
            )
        ),
        null
    );

    update public.content_items as content_item
    set current_version_id = content_version_id
    where content_item.workspace_id = test_workspace_id
      and content_item.id = content_item_id;

    insert into public.assets (
        id, workspace_id, content_item_id, content_version_id, asset_kind,
        storage_bucket, storage_path, mime_type, byte_size, sha256, width,
        height, metadata, created_by
    ) values (
        asset_id,
        test_workspace_id,
        content_item_id,
        content_version_id,
        'png',
        'content-studio',
        test_workspace_id::text || '/origintrail/' || asset_id::text
            || '/news-card.png',
        'image/png',
        128,
        banner_sha256,
        1200,
        630,
        jsonb_build_object('filename', 'news-card.png'),
        null
    );
    insert into storage.objects (bucket_id, name)
    values (
        'content-studio',
        test_workspace_id::text || '/origintrail/' || asset_id::text
            || '/news-card.png'
    );

    binding := public.bind_origintrail_batch_review_pack(
        test_workspace_id,
        test_job_id,
        content_item_id,
        content_version_id,
        asset_id,
        source_item_id,
        input_sha256,
        result_sha256,
        source_content_sha256,
        banner_sha256,
        review_pack_sha256
    );
    if binding ->> 'protocol_version' <> 'origintrail-review-pack@1'
       or (binding ->> 'reused')::boolean then
        raise exception 'OriginTrail review pack was not bound exactly once';
    end if;

    replay := public.bind_origintrail_batch_review_pack(
        test_workspace_id,
        test_job_id,
        content_item_id,
        content_version_id,
        asset_id,
        source_item_id,
        input_sha256,
        result_sha256,
        source_content_sha256,
        banner_sha256,
        review_pack_sha256
    );
    if not (replay ->> 'reused')::boolean then
        raise exception 'OriginTrail review pack replay was not reused';
    end if;
end
$test$;

insert into agent_runtime.buzz_delivery_receipts (
    workspace_id, event_id, event_type, job_id, client_id, agent_id,
    workflow_kind, channel_id, message_sha256, request_sha256,
    attachment_sha256, status, attempts, delivery_attempt_id,
    attempt_worker_id, delivery_started_at, relay_event_id, delivered_at
)
values (
    'f0000000-0000-4000-8000-000000000001',
    private.origintrail_buzz_event_id(
        'f0000000-0000-4000-8000-000000000001',
        'f1000000-0000-4000-8000-000000000001'
    ),
    'origintrail.batch_review_ready.v1',
    'f1000000-0000-4000-8000-000000000001',
    'origintrail', 'origintrail_client_agent',
    'official_source_nonurgent_pack',
    'f2000000-0000-4000-8000-000000000001',
    repeat('2', 64), repeat('3', 64), repeat('8', 64), 'delivered', 1,
    'f3000000-0000-4000-8000-000000000001',
    'origintrail-buzz:review-security',
    statement_timestamp() - interval '20 seconds',
    repeat('4', 64),
    statement_timestamp() - interval '10 seconds'
);

do $test$
declare
    workspace_id constant uuid :=
        'f0000000-0000-4000-8000-000000000001';
    job_id constant uuid :=
        'f1000000-0000-4000-8000-000000000001';
    channel_id constant uuid :=
        'f2000000-0000-4000-8000-000000000001';
    delivery_event_id text := private.origintrail_buzz_event_id(
        workspace_id, job_id
    );
    root_event_id constant text := repeat('4', 64);
    message_sha256 constant text := repeat('2', 64);
    protocol_version constant text := 'origintrail-buzz-review@2';
    decision_event_id constant text := repeat('5', 64);
    reviewer constant text := repeat('6', 64);
    protocol_start_epoch bigint := extract(epoch from
        statement_timestamp() - interval '30 seconds'
    )::bigint;
    post_delivery_cutoff bigint := extract(epoch from
        statement_timestamp() + interval '1 second'
    )::bigint;
    subsecond_cutoff bigint := floor(extract(epoch from statement_timestamp()))::bigint;
    command_epoch bigint := extract(epoch from statement_timestamp())::bigint;
    command_sha text;
    ack_worker constant text := 'origintrail-buzz-ack:test';
    ack_request_sha constant text := repeat('9', 64);
    ack_relay_event_id constant text := repeat('a', 64);
    targets jsonb;
    recorded jsonb;
    replayed jsonb;
    claimed jsonb;
    attempt jsonb;
    attempt_replay jsonb;
    reconciled jsonb;
    reconciled_again jsonb;
    unknown_rows jsonb;
    unknown_claim jsonb;
    completed jsonb;
    completed_replay jsonb;
    publication_count_before bigint;
    approval_count_before bigint;
    batch_count_before bigint;
begin
    select count(*) into publication_count_before
    from public.publications;
    select count(*) into approval_count_before
    from public.approvals;
    select count(*) into batch_count_before
    from agent_runtime.batch_jobs;
    if (
        select char_length(batch.result_payload ->> 'telegram_copy_ko')
        from agent_runtime.batch_jobs as batch
        where batch.workspace_id =
                'f0000000-0000-4000-8000-000000000001'::uuid
          and batch.job_id =
                'f1000000-0000-4000-8000-000000000001'::uuid
    ) <> 1024 then
        raise exception 'Telegram review caption did not exercise 1024-char boundary';
    end if;

    begin
        perform public.list_origintrail_buzz_review_targets(
            workspace_id, 1, protocol_start_epoch,
            'origintrail-buzz-review@1'
        );
        raise exception 'legacy Buzz review protocol was accepted';
    exception when invalid_parameter_value then null;
    end;

    targets := public.list_origintrail_buzz_review_targets(
        workspace_id, 1, post_delivery_cutoff, protocol_version
    );
    if jsonb_array_length(targets -> 'targets') <> 0 then
        raise exception 'pre-cutover Buzz delivery crossed protocol cutoff';
    end if;

    update agent_runtime.buzz_delivery_receipts as receipt
    set delivered_at = pg_catalog.to_timestamp(subsecond_cutoff - 0.4)
    where receipt.workspace_id =
            'f0000000-0000-4000-8000-000000000001'::uuid
      and receipt.event_id = delivery_event_id;
    targets := public.list_origintrail_buzz_review_targets(
        workspace_id, 1, subsecond_cutoff, protocol_version
    );
    if jsonb_array_length(targets -> 'targets') <> 0 then
        raise exception 'subsecond pre-cutover delivery was rounded into eligibility';
    end if;
    update agent_runtime.buzz_delivery_receipts as receipt
    set delivered_at = statement_timestamp() - interval '10 seconds'
    where receipt.workspace_id =
            'f0000000-0000-4000-8000-000000000001'::uuid
      and receipt.event_id = delivery_event_id;

    targets := public.list_origintrail_buzz_review_targets(
        workspace_id, 1, protocol_start_epoch, protocol_version
    );
    if jsonb_array_length(targets -> 'targets') <> 1
       or targets ->> 'schema_version' <> '2.0'
       or targets ->> 'mode' <> 'publish_intent_review'
       or targets #>> '{targets,0,job_id}' <> job_id::text
       or targets #>> '{targets,0,message_sha256}' <> message_sha256
       or targets #>> '{targets,0,protocol_version}' <> protocol_version then
        raise exception 'eligible delivered Buzz review target was not listed';
    end if;

    command_sha := private.origintrail_buzz_review_command_sha256(
        workspace_id, job_id, delivery_event_id, channel_id, root_event_id,
        message_sha256, protocol_version, decision_event_id, reviewer,
        'approved', null, command_epoch
    );

    begin
        perform public.record_origintrail_buzz_review_decision_with_ack(
            workspace_id, job_id, delivery_event_id, channel_id,
            root_event_id, repeat('a', 64), protocol_version,
            protocol_start_epoch, decision_event_id, reviewer, 'approved',
            null,
            private.origintrail_buzz_review_command_sha256(
                workspace_id, job_id, delivery_event_id, channel_id,
                root_event_id, repeat('a', 64), protocol_version,
                decision_event_id, reviewer, 'approved', null, command_epoch
            ),
            command_epoch
        );
        raise exception 'forged Buzz delivery message hash was accepted';
    exception when check_violation then null;
    end;

    begin
        perform public.record_origintrail_buzz_review_decision_with_ack(
            workspace_id, job_id, delivery_event_id, channel_id,
            root_event_id, message_sha256, protocol_version,
            protocol_start_epoch, decision_event_id, reviewer, 'approved',
            null, repeat('f', 64), command_epoch
        );
        raise exception 'forged Buzz review command hash was accepted';
    exception when check_violation then null;
    end;

    if (
        select count(*)
        from agent_runtime.buzz_review_ack_receipts as receipt
        where receipt.workspace_id =
                'f0000000-0000-4000-8000-000000000001'::uuid
    ) <> 0 then
        raise exception 'failed decision attempt created an acknowledgement';
    end if;

    recorded := public.record_origintrail_buzz_review_decision_with_ack(
        workspace_id, job_id, delivery_event_id, channel_id,
        root_event_id, message_sha256, protocol_version,
        protocol_start_epoch, decision_event_id, reviewer, 'approved',
        null, command_sha, command_epoch
    );
    if recorded ->> 'decision' <> 'approved'
       or recorded ->> 'message_sha256' <> message_sha256
       or recorded ->> 'protocol_version' <> protocol_version
       or recorded ->> 'acknowledgement_status' <> 'pending'
       or (recorded ->> 'reused')::boolean then
        raise exception 'fresh Buzz review decision and acknowledgement were not atomic';
    end if;

    if (
        select count(*)
        from agent_runtime.buzz_review_ack_receipts as receipt
        where receipt.workspace_id =
                'f0000000-0000-4000-8000-000000000001'::uuid
          and receipt.job_id =
                'f1000000-0000-4000-8000-000000000001'::uuid
          and receipt.status = 'pending'
          and receipt.request_sha256 is null
          and receipt.message =
                private.origintrail_buzz_review_ack_message('approved')
          and receipt.message_sha256 =
                private.origintrail_buzz_review_ack_message_sha256(
                    receipt.message
                )
    ) <> 1 then
        raise exception 'fresh decision did not create one fixed pending acknowledgement';
    end if;

    replayed := public.record_origintrail_buzz_review_decision_with_ack(
        workspace_id, job_id, delivery_event_id, channel_id,
        root_event_id, message_sha256, protocol_version,
        protocol_start_epoch, decision_event_id, reviewer, 'approved',
        null, command_sha, command_epoch
    );
    if not (replayed ->> 'reused')::boolean
       or replayed ->> 'acknowledgement_status' <> 'pending'
       or (
            select count(*)
            from agent_runtime.buzz_review_ack_receipts as receipt
            where receipt.workspace_id =
                    'f0000000-0000-4000-8000-000000000001'::uuid
              and receipt.job_id =
                    'f1000000-0000-4000-8000-000000000001'::uuid
          ) <> 1 then
        raise exception 'exact Buzz review decision replay was not reused';
    end if;

    claimed := public.claim_origintrail_buzz_review_ack(
        workspace_id, job_id, ack_worker, 180
    );
    if claimed ->> 'schema_version' <> '1.0'
       or claimed ->> 'mode' <> 'durable_review_acknowledgement'
       or claimed #>> '{acknowledgement,job_id}' <> job_id::text
       or claimed #>> '{acknowledgement,status}' <> 'claimed'
       or not (claimed #>> '{acknowledgement,claim_granted}')::boolean
       or (claimed #>> '{acknowledgement,reused}')::boolean
       or claimed #>> '{acknowledgement,template_version}' <>
            'origintrail-buzz-review-ack@1'
       or claimed #>> '{acknowledgement,message}' <>
            private.origintrail_buzz_review_ack_message('approved')
       or claimed #>> '{acknowledgement,message_sha256}' <>
            private.origintrail_buzz_review_ack_message_sha256(
                private.origintrail_buzz_review_ack_message('approved')
            )
       or claimed #>> '{acknowledgement,request_sha256}' is not null then
        raise exception 'pending acknowledgement was not claimed with stored payload';
    end if;

    attempt := public.mark_origintrail_buzz_review_ack_attempt(
        workspace_id,
        job_id,
        ack_worker,
        claimed #>> '{acknowledgement,message_sha256}',
        ack_request_sha
    );
    if attempt ->> 'status' <> 'attempt_started'
       or not (attempt ->> 'authorized_once')::boolean
       or (attempt ->> 'reused')::boolean
       or attempt ->> 'message_sha256' <>
            claimed #>> '{acknowledgement,message_sha256}'
       or attempt ->> 'request_sha256' <> ack_request_sha then
        raise exception 'first acknowledgement attempt was not authorized once';
    end if;

    attempt_replay := public.mark_origintrail_buzz_review_ack_attempt(
        workspace_id,
        job_id,
        ack_worker,
        claimed #>> '{acknowledgement,message_sha256}',
        ack_request_sha
    );
    if attempt_replay ->> 'status' <> 'attempt_started'
       or (attempt_replay ->> 'authorized_once')::boolean
       or not (attempt_replay ->> 'reused')::boolean
       or attempt_replay ->> 'request_sha256' <> ack_request_sha then
        raise exception 'acknowledgement attempt replay gained a second authorization';
    end if;

    update agent_runtime.buzz_review_ack_receipts as receipt
    set locked_at = statement_timestamp() - interval '10 minutes',
        lease_expires_at = statement_timestamp() - interval '1 second'
    where receipt.workspace_id =
            'f0000000-0000-4000-8000-000000000001'::uuid
      and receipt.job_id =
            'f1000000-0000-4000-8000-000000000001'::uuid;

    reconciled := public.reconcile_origintrail_buzz_review_ack_leases(
        workspace_id, 10
    );
    if (reconciled ->> 'reconciled_count')::integer <> 1
       or (reconciled ->> 'delivery_unknown_count')::integer <> 1
       or (reconciled ->> 'pending_count')::integer <> 0
       or (reconciled ->> 'failed_count')::integer <> 0 then
        raise exception 'expired started attempt did not reconcile to unknown';
    end if;

    reconciled_again := public.reconcile_origintrail_buzz_review_ack_leases(
        workspace_id, 10
    );
    if (reconciled_again ->> 'reconciled_count')::integer <> 0
       or (
            select receipt.status
            from agent_runtime.buzz_review_ack_receipts as receipt
            where receipt.workspace_id =
                    'f0000000-0000-4000-8000-000000000001'::uuid
              and receipt.job_id =
                    'f1000000-0000-4000-8000-000000000001'::uuid
          ) <> 'delivery_unknown' then
        raise exception 'unknown acknowledgement was automatically requeued';
    end if;

    unknown_rows := public.list_origintrail_buzz_review_ack_unknown(
        workspace_id, 10
    );
    if jsonb_array_length(unknown_rows -> 'acknowledgements') <> 1
       or unknown_rows #>> '{acknowledgements,0,job_id}' <> job_id::text
       or unknown_rows #>> '{acknowledgements,0,status}' <>
            'delivery_unknown'
       or unknown_rows #>> '{acknowledgements,0,request_sha256}' <>
            ack_request_sha
       or (unknown_rows #>> '{acknowledgements,0,claim_granted}')::boolean then
        raise exception 'unknown acknowledgement was not listed safely';
    end if;

    unknown_claim := public.claim_origintrail_buzz_review_ack(
        workspace_id, job_id, ack_worker, 180
    );
    if unknown_claim #>> '{acknowledgement,status}' <>
            'delivery_unknown'
       or (unknown_claim #>> '{acknowledgement,claim_granted}')::boolean
       or unknown_claim #>> '{acknowledgement,request_sha256}' <>
            ack_request_sha then
        raise exception 'unknown acknowledgement was claimable or changed';
    end if;

    completed := public.complete_origintrail_buzz_review_ack(
        workspace_id,
        job_id,
        ack_worker,
        ack_request_sha,
        ack_relay_event_id,
        true
    );
    if completed ->> 'status' <> 'delivered'
       or (completed ->> 'reused')::boolean
       or completed ->> 'request_sha256' <> ack_request_sha
       or completed ->> 'relay_event_id' <> ack_relay_event_id then
        raise exception 'reconciled acknowledgement was not completed';
    end if;

    completed_replay := public.complete_origintrail_buzz_review_ack(
        workspace_id,
        job_id,
        ack_worker,
        ack_request_sha,
        ack_relay_event_id,
        true
    );
    if completed_replay ->> 'status' <> 'delivered'
       or not (completed_replay ->> 'reused')::boolean
       or completed_replay ->> 'request_sha256' <> ack_request_sha
       or completed_replay ->> 'relay_event_id' <> ack_relay_event_id then
        raise exception 'exact acknowledgement completion replay was not reused';
    end if;

    begin
        perform public.record_origintrail_buzz_review_decision(
            workspace_id, job_id, delivery_event_id, channel_id,
            root_event_id, message_sha256, protocol_version,
            protocol_start_epoch, repeat('7', 64), reviewer,
            'approved', null,
            private.origintrail_buzz_review_command_sha256(
                workspace_id, job_id, delivery_event_id, channel_id,
                root_event_id, message_sha256, protocol_version,
                repeat('7', 64), reviewer, 'approved', null, command_epoch
            ),
            command_epoch
        );
        raise exception 'conflicting second Buzz review decision was accepted';
    exception when unique_violation then null;
    end;

    if jsonb_array_length(
        public.list_origintrail_buzz_review_targets(
            workspace_id, 1, protocol_start_epoch, protocol_version
        )
        -> 'targets'
    ) <> 0 then
        raise exception 'decided job remained in the Buzz review target list';
    end if;

    begin
        update agent_runtime.buzz_review_decisions as decision_row
        set decision = 'changes_requested'
        where decision_row.workspace_id =
                'f0000000-0000-4000-8000-000000000001'::uuid
          and decision_row.job_id =
                'f1000000-0000-4000-8000-000000000001'::uuid;
        raise exception 'immutable Buzz review decision was updated';
    exception when object_not_in_prerequisite_state then null;
    end;

    if (
        select count(*)
        from agent_runtime.buzz_review_ack_receipts as receipt
        where receipt.workspace_id =
                'f0000000-0000-4000-8000-000000000001'::uuid
          and receipt.job_id =
                'f1000000-0000-4000-8000-000000000001'::uuid
          and receipt.status = 'delivered'
          and receipt.request_sha256 = ack_request_sha
          and receipt.relay_event_id = ack_relay_event_id
    ) <> 1 then
        raise exception 'acknowledgement receipt final state is not unique delivered';
    end if;
    if (select count(*) from public.publications) <>
            publication_count_before
       or (select count(*) from public.approvals) <> approval_count_before
       or (select count(*) from agent_runtime.batch_jobs) <>
            batch_count_before then
        raise exception 'acknowledgement flow mutated publication, approval, or Batch state';
    end if;
end
$test$;

rollback;
