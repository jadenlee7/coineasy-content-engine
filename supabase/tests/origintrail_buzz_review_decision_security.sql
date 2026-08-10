-- Transactional security and idempotency smoke for the Buzz review ledger.
-- No relay, publication, provider, or external network call is made.

begin;

do $test$
declare
    signature text;
begin
    foreach signature in array array[
        'public.list_origintrail_buzz_review_targets(uuid,integer,bigint,text)',
        'public.record_origintrail_buzz_review_decision(uuid,uuid,text,uuid,text,text,text,bigint,text,text,text,text,text,bigint)'
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
    ) then
        raise exception 'Buzz review decision table leaked direct access';
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
    targets jsonb;
    recorded jsonb;
    replayed jsonb;
begin
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
        perform public.record_origintrail_buzz_review_decision(
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
        perform public.record_origintrail_buzz_review_decision(
            workspace_id, job_id, delivery_event_id, channel_id,
            root_event_id, message_sha256, protocol_version,
            protocol_start_epoch, decision_event_id, reviewer, 'approved',
            null, repeat('f', 64), command_epoch
        );
        raise exception 'forged Buzz review command hash was accepted';
    exception when check_violation then null;
    end;

    recorded := public.record_origintrail_buzz_review_decision(
        workspace_id, job_id, delivery_event_id, channel_id,
        root_event_id, message_sha256, protocol_version,
        protocol_start_epoch, decision_event_id, reviewer, 'approved',
        null, command_sha, command_epoch
    );
    if recorded ->> 'decision' <> 'approved'
       or recorded ->> 'message_sha256' <> message_sha256
       or recorded ->> 'protocol_version' <> protocol_version
       or (recorded ->> 'reused')::boolean then
        raise exception 'fresh Buzz review decision was not recorded exactly once';
    end if;

    replayed := public.record_origintrail_buzz_review_decision(
        workspace_id, job_id, delivery_event_id, channel_id,
        root_event_id, message_sha256, protocol_version,
        protocol_start_epoch, decision_event_id, reviewer, 'approved',
        null, command_sha, command_epoch
    );
    if not (replayed ->> 'reused')::boolean then
        raise exception 'exact Buzz review decision replay was not reused';
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
end
$test$;

rollback;
