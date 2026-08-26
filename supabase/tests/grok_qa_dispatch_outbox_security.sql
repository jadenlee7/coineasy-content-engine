-- Transactional security and state-machine smoke for official-X Grok QA
-- dispatch. Run as database owner after all migrations; all rows roll back.

begin;

do $test$
declare
    signature text;
begin
    foreach signature in array array[
        'public.claim_grok_qa_dispatch_job(uuid,text,integer,text[],integer,uuid)',
        'public.mark_grok_qa_dispatch_provider_attempt(uuid,uuid,text,text,text)',
        'public.stage_grok_qa_dispatch_verdict(uuid,uuid,text,jsonb,text,text,text,text,text,bigint,jsonb,smallint)',
        'public.complete_grok_qa_dispatch_job(uuid,uuid,text,text,text,text)',
        'public.fail_grok_qa_dispatch_job(uuid,uuid,text,text,boolean,timestamp with time zone)',
        'public.reconcile_grok_qa_dispatch_leases(uuid,integer)'
    ] loop
        if has_function_privilege('anon', signature, 'execute')
           or has_function_privilege('authenticated', signature, 'execute')
           or not has_function_privilege('service_role', signature, 'execute') then
            raise exception 'Grok QA dispatch RPC privilege is invalid: %', signature;
        end if;
    end loop;
    if to_regprocedure(
        'public.claim_grok_qa_dispatch_job(uuid,text,integer,text[],uuid)'
    ) is not null then
        raise exception 'legacy Grok QA claim RPC bypass remains available';
    end if;
    if has_table_privilege(
        'anon', 'private.grok_qa_dispatch_outbox', 'select'
    ) or has_table_privilege(
        'authenticated', 'private.grok_qa_dispatch_outbox', 'select'
    ) or has_table_privilege(
        'service_role', 'private.grok_qa_dispatch_outbox', 'select'
    ) then
        raise exception 'private Grok QA dispatch outbox leaked direct access';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug)
values (
    'e1000000-0000-4000-8000-000000000001',
    'Grok QA Dispatch Security',
    'grok-qa-dispatch-security'
);
insert into public.workspace_clients (
    workspace_id, client_id, display_name, active
) values (
    'e1000000-0000-4000-8000-000000000001',
    'squid', 'Squid', true
);
insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, active
) values (
    'e1100000-0000-4000-8000-000000000001',
    'e1000000-0000-4000-8000-000000000001',
    'squid', 'x', 'Squid official X', 'https://x.com/SquidRouter',
    '@SquidRouter', 15, true
);
insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, source_hash
) values (
    'e1200000-0000-4000-8000-000000000001',
    'e1000000-0000-4000-8000-000000000001',
    'squid',
    'e1100000-0000-4000-8000-000000000001',
    '2083266484789514640', 'tweet',
    'https://x.com/SquidRouter/status/2083266484789514640',
    '@SquidRouter',
    transaction_timestamp() - interval '1 hour',
    'Squid official source used by the immutable Korean GTM review package.',
    'official-x:squid:2083266484789514640'
);

insert into public.content_items (
    id, workspace_id, client_id, content_kind, title, status
) values
    (
        'e1300000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        'squid', 'daily_news', 'Squid QA dispatch one', 'needs_review'
    ),
    (
        'e1300000-0000-4000-8000-000000000002',
        'e1000000-0000-4000-8000-000000000001',
        'squid', 'daily_news', 'Squid QA dispatch two', 'needs_review'
    );
insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version,
    locale, title, content, channel_copy, generation_meta, deliverables
) values
    (
        'e1400000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        'e1300000-0000-4000-8000-000000000001',
        1, 'grok-dispatch-security@1', 'ko-KR', 'Squid QA dispatch one',
        '{"spec":{"headline":"Squid Telegram"}}'::jsonb,
        '{"telegram":"검토 문구","x":"검토 문구"}'::jsonb,
        '{"mock_mode":false}'::jsonb,
        '{"primary_asset_id":"e1600000-0000-4000-8000-000000000001","asset_ids":["e1600000-0000-4000-8000-000000000001"]}'::jsonb
    ),
    (
        'e1400000-0000-4000-8000-000000000002',
        'e1000000-0000-4000-8000-000000000001',
        'e1300000-0000-4000-8000-000000000002',
        1, 'grok-dispatch-security@1', 'ko-KR', 'Squid QA dispatch two',
        '{"spec":{"headline":"Squid Telegram"}}'::jsonb,
        '{"telegram":"검토 문구","x":"검토 문구"}'::jsonb,
        '{"mock_mode":false}'::jsonb,
        '{"primary_asset_id":"e1600000-0000-4000-8000-000000000002","asset_ids":["e1600000-0000-4000-8000-000000000002"]}'::jsonb
    );
insert into storage.objects (bucket_id, name) values
    (
        'content-studio',
        'e1000000-0000-4000-8000-000000000001/squid/e1600000-0000-4000-8000-000000000001/news-card.png'
    ),
    (
        'content-studio',
        'e1000000-0000-4000-8000-000000000001/squid/e1600000-0000-4000-8000-000000000002/news-card.png'
    );
insert into public.assets (
    id, workspace_id, content_item_id, content_version_id, asset_kind,
    storage_bucket, storage_path, mime_type, byte_size, sha256, width,
    height, metadata
) values
    (
        'e1600000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        'e1300000-0000-4000-8000-000000000001',
        'e1400000-0000-4000-8000-000000000001', 'png',
        'content-studio',
        'e1000000-0000-4000-8000-000000000001/squid/e1600000-0000-4000-8000-000000000001/news-card.png',
        'image/png', 128, repeat('c', 64), 1080, 1080,
        '{"filename":"news-card.png"}'::jsonb
    ),
    (
        'e1600000-0000-4000-8000-000000000002',
        'e1000000-0000-4000-8000-000000000001',
        'e1300000-0000-4000-8000-000000000002',
        'e1400000-0000-4000-8000-000000000002', 'png',
        'content-studio',
        'e1000000-0000-4000-8000-000000000001/squid/e1600000-0000-4000-8000-000000000002/news-card.png',
        'image/png', 128, repeat('c', 64), 1080, 1080,
        '{"filename":"news-card.png"}'::jsonb
    );
update public.content_items
set current_version_id = case id
    when 'e1300000-0000-4000-8000-000000000001'::uuid
        then 'e1400000-0000-4000-8000-000000000001'::uuid
    else 'e1400000-0000-4000-8000-000000000002'::uuid
end
where id in (
    'e1300000-0000-4000-8000-000000000001',
    'e1300000-0000-4000-8000-000000000002'
);
insert into public.content_source_links (
    workspace_id, client_id, content_item_id, source_item_id, position
) values
    (
        'e1000000-0000-4000-8000-000000000001', 'squid',
        'e1300000-0000-4000-8000-000000000001',
        'e1200000-0000-4000-8000-000000000001', 0
    ),
    (
        'e1000000-0000-4000-8000-000000000001', 'squid',
        'e1300000-0000-4000-8000-000000000002',
        'e1200000-0000-4000-8000-000000000001', 0
    );

insert into public.jobs (
    id, workspace_id, client_id, content_item_id, job_kind, status,
    input, output, idempotency_key
) values
    (
        'e1500000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001', 'squid',
        'e1300000-0000-4000-8000-000000000001', 'generate', 'succeeded',
        jsonb_build_object(
            'workflow', 'official_x_review_draft_v1',
            'source_item_ids', jsonb_build_array(
                'e1200000-0000-4000-8000-000000000001'
            )
        ),
        jsonb_build_object(
            'content_item_id', 'e1300000-0000-4000-8000-000000000001',
            'content_version_id', 'e1400000-0000-4000-8000-000000000001'
        ),
        'grok-qa-dispatch-security:one'
    ),
    (
        'e1500000-0000-4000-8000-000000000002',
        'e1000000-0000-4000-8000-000000000001', 'squid',
        'e1300000-0000-4000-8000-000000000002', 'generate', 'succeeded',
        jsonb_build_object(
            'workflow', 'official_x_review_draft_v1',
            'source_item_ids', jsonb_build_array(
                'e1200000-0000-4000-8000-000000000001'
            )
        ),
        jsonb_build_object(
            'content_item_id', 'e1300000-0000-4000-8000-000000000002',
            'content_version_id', 'e1400000-0000-4000-8000-000000000002'
        ),
        'grok-qa-dispatch-security:two'
    );

-- Non-completion events never enqueue work.
insert into public.event_log (
    workspace_id, entity_type, entity_id, event_type, data
) values (
    'e1000000-0000-4000-8000-000000000001', 'content_item',
    'e1300000-0000-4000-8000-000000000001', 'unrelated_test_event',
    '{}'::jsonb
);
do $test$
begin
    if exists (
        select 1 from private.grok_qa_dispatch_outbox
        where workspace_id = 'e1000000-0000-4000-8000-000000000001'
    ) then
        raise exception 'unrelated event entered the Grok QA dispatch outbox';
    end if;
end
$test$;

insert into public.event_log (
    workspace_id, entity_type, entity_id, event_type, data
) values
    (
        'e1000000-0000-4000-8000-000000000001', 'content_item',
        'e1300000-0000-4000-8000-000000000001',
        'official_x_review_draft_completed',
        jsonb_build_object(
            'job_id', 'e1500000-0000-4000-8000-000000000001',
            'content_version_id', 'e1400000-0000-4000-8000-000000000001',
            'source_item_ids', jsonb_build_array(
                'e1200000-0000-4000-8000-000000000001'
            )
        )
    ),
    (
        'e1000000-0000-4000-8000-000000000001', 'content_item',
        'e1300000-0000-4000-8000-000000000002',
        'official_x_review_draft_completed',
        jsonb_build_object(
            'job_id', 'e1500000-0000-4000-8000-000000000002',
            'content_version_id', 'e1400000-0000-4000-8000-000000000002',
            'source_item_ids', jsonb_build_array(
                'e1200000-0000-4000-8000-000000000001'
            )
        )
    );

do $test$
begin
    if (
        select count(*) from private.grok_qa_dispatch_outbox
        where workspace_id = 'e1000000-0000-4000-8000-000000000001'
    ) <> 2 or exists (
        select 1 from private.grok_qa_dispatch_outbox
        where workspace_id = 'e1000000-0000-4000-8000-000000000001'
          and (
              status <> 'pending'
              or attempts <> 0
              or source_item_id <>
                    'e1200000-0000-4000-8000-000000000001'
              or source_url <>
                    'https://x.com/SquidRouter/status/2083266484789514640'
              or source_author_handle <> '@SquidRouter'
              or source_published_at <>
                    transaction_timestamp() - interval '1 hour'
          )
    ) then
        raise exception 'exact official-X events did not enqueue frozen sources';
    end if;
end
$test$;

-- Normal FIFO never leases stale official-X work, while an exact immutable
-- canary UUID remains available for an operator-authorized recovery probe.
do $test$
declare
    normal_stale jsonb;
    exact_stale_canary jsonb;
    future_canary jsonb;
begin
    begin
        update public.source_items
        set published_at = transaction_timestamp() - interval '2 days'
        where id = 'e1200000-0000-4000-8000-000000000001';
        update private.grok_qa_dispatch_outbox
        set source_published_at = transaction_timestamp() - interval '2 days'
        where workspace_id = 'e1000000-0000-4000-8000-000000000001';

        normal_stale := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:freshness-worker', 300, array['squid'], 86400
        );
        exact_stale_canary := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:freshness-canary', 300, array['squid'], 86400,
            'e1400000-0000-4000-8000-000000000001'
        );
        if normal_stale -> 'job' <> 'null'::jsonb
           or exact_stale_canary -> 'job' ->> 'content_version_id'
                <> 'e1400000-0000-4000-8000-000000000001' then
            raise exception 'normal FIFO freshness fence or exact canary bypass failed: %, %',
                normal_stale, exact_stale_canary;
        end if;
        raise exception 'rollback Grok QA freshness probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback Grok QA freshness probe' then
            raise;
        end if;
    end;
    begin
        update public.source_items
        set published_at = transaction_timestamp() + interval '10 minutes'
        where id = 'e1200000-0000-4000-8000-000000000001';
        update private.grok_qa_dispatch_outbox
        set source_published_at = transaction_timestamp() + interval '10 minutes'
        where workspace_id = 'e1000000-0000-4000-8000-000000000001';

        future_canary := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:future-canary', 300, array['squid'], 86400,
            'e1400000-0000-4000-8000-000000000001'
        );
        if future_canary -> 'job' <> 'null'::jsonb then
            raise exception 'future-dated exact canary bypassed clock-skew fence: %',
                future_canary;
        end if;
        raise exception 'rollback Grok QA future-source probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback Grok QA future-source probe' then
            raise;
        end if;
    end;
end
$test$;

-- An exact canary target never falls through to another eligible row. This
-- deliberately leaves version one pending while version two is terminal.
update private.grok_qa_dispatch_outbox
set status = 'obsolete',
    completed_at = statement_timestamp(),
    updated_at = statement_timestamp()
where content_version_id = 'e1400000-0000-4000-8000-000000000002';

set local role service_role;

do $test$
declare
    exact_obsolete jsonb;
begin
    exact_obsolete := public.claim_grok_qa_dispatch_job(
        'e1000000-0000-4000-8000-000000000001',
        'grok-qa:canary-worker', 300, array['squid'], 86400,
        'e1400000-0000-4000-8000-000000000002'
    );
    if exact_obsolete -> 'job' <> 'null'::jsonb then
        raise exception 'exact obsolete canary fell through to another row: %',
            exact_obsolete;
    end if;
end
$test$;

reset role;

update private.grok_qa_dispatch_outbox
set status = 'pending',
    completed_at = null,
    updated_at = statement_timestamp()
where content_version_id = 'e1400000-0000-4000-8000-000000000002';

set local role service_role;

do $test$
declare
    claimed jsonb;
    canary_claimed jsonb;
    filtered jsonb;
    fenced jsonb;
    replayed_fence jsonb;
    stale_fence jsonb;
    stale_after_mark jsonb;
    staged jsonb;
    failed jsonb;
    input_hash constant text := repeat('a', 64);
    banner_hash constant text := repeat('c', 64);
    payload constant jsonb := '{
      "decision":"WARN",
      "summary":"공식 원문은 확인했지만 한국어 배너 현지화를 다시 검토해야 합니다.",
      "fact_check":{
        "status":"PASS",
        "checks":["공식 X 원문의 핵심 사실을 확인했습니다."],
        "source_urls":["https://x.com/SquidRouter/status/2083266484789514640"]
      },
      "brand_check":{
        "status":"WARN",
        "checks":["배너의 한국어 가독성을 다시 확인해야 합니다."]
      },
      "issues":[{
        "severity":"WARN",
        "code":"banner_localization",
        "message":"영문 헤드라인을 한국 GTM 문구로 검토하세요."
      }],
      "next_action":"revise_banner"
    }'::jsonb;
begin
    filtered := public.claim_grok_qa_dispatch_job(
        'e1000000-0000-4000-8000-000000000001',
        'grok-qa:test-worker', 300, array['yellow'], 86400
    );
    if filtered -> 'job' <> 'null'::jsonb then
        raise exception 'allowed_clients leaked a Squid dispatch: %', filtered;
    end if;
    begin
        perform public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:test-worker', 300, array['squid', 'squid'], 86400
        );
        raise exception 'duplicate allowed_clients were accepted';
    exception when invalid_parameter_value then
        null;
    end;

    -- The optional canary UUID selects that exact version, not the FIFO head.
    -- The nested subtransaction rolls its lease back for the normal flow.
    begin
        canary_claimed := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:canary-worker', 300, array['squid'], 86400,
            'e1400000-0000-4000-8000-000000000002'
        );
        if canary_claimed -> 'job' ->> 'content_version_id'
                <> 'e1400000-0000-4000-8000-000000000002'
           or canary_claimed -> 'job' -> 'provider_call_required'
                <> 'true'::jsonb then
            raise exception 'exact canary claim selected the wrong row: %',
                canary_claimed;
        end if;
        raise exception 'rollback exact canary claim probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback exact canary claim probe' then
            raise;
        end if;
    end;

    claimed := public.claim_grok_qa_dispatch_job(
        'e1000000-0000-4000-8000-000000000001',
        'grok-qa:test-worker', 300, array['squid'], 86400
    );
    if claimed -> 'job' ->> 'content_version_id'
            <> 'e1400000-0000-4000-8000-000000000001'
       or claimed -> 'job' ->> 'source_url'
            <> 'https://x.com/SquidRouter/status/2083266484789514640'
       or claimed -> 'job' ->> 'source_author_handle' <> '@SquidRouter'
       or (claimed -> 'job' ->> 'source_published_at')::timestamptz
            <> transaction_timestamp() - interval '1 hour'
       or claimed -> 'job' -> 'claim_granted' <> 'true'::jsonb
       or claimed -> 'job' -> 'provider_call_required' <> 'true'::jsonb
       or claimed -> 'job' ->> 'attempts' <> '1' then
        raise exception 'Grok QA dispatch claim contract is invalid: %', claimed;
    end if;

    -- The final provider fence independently revalidates the frozen official
    -- source. A feed change after claim durably obsoletes the target and never
    -- authorizes spend. Roll back this probe to continue the success path.
    begin
        update public.source_feeds
        set active = false
        where id = 'e1100000-0000-4000-8000-000000000001';
        stale_fence := public.mark_grok_qa_dispatch_provider_attempt(
            'e1000000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001',
            'grok-qa:test-worker', input_hash, banner_hash
        );
        stale_after_mark := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:canary-worker', 300, array['squid'], 86400,
            'e1400000-0000-4000-8000-000000000001'
        );
        if stale_fence -> 'authorized_once' <> 'false'::jsonb
           or stale_fence -> 'input_sha256' <> 'null'::jsonb
           or stale_fence -> 'provider_attempt_started_at' <> 'null'::jsonb
           or stale_after_mark -> 'job' <> 'null'::jsonb then
            raise exception 'stale final source revalidation authorized provider: %, %',
                stale_fence, stale_after_mark;
        end if;
        raise exception 'rollback stale provider fence probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback stale provider fence probe' then
            raise;
        end if;
    end;

    fenced := public.mark_grok_qa_dispatch_provider_attempt(
        'e1000000-0000-4000-8000-000000000001',
        'e1400000-0000-4000-8000-000000000001',
        'grok-qa:test-worker', input_hash, banner_hash
    );
    replayed_fence := public.mark_grok_qa_dispatch_provider_attempt(
        'e1000000-0000-4000-8000-000000000001',
        'e1400000-0000-4000-8000-000000000001',
        'grok-qa:test-worker', input_hash, banner_hash
    );
    if fenced -> 'authorized_once' <> 'true'::jsonb
       or replayed_fence -> 'authorized_once' <> 'false'::jsonb
       or fenced ->> 'input_sha256' <> input_hash
       or fenced ->> 'banner_sha256' <> banner_hash
       or fenced ->> 'provider_attempt_started_at' is null then
        raise exception 'provider attempt fence was not commit-once: %, %',
            fenced, replayed_fence;
    end if;
    begin
        perform public.mark_grok_qa_dispatch_provider_attempt(
            'e1000000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001',
            'grok-qa:test-worker', repeat('b', 64), banner_hash
        );
        raise exception 'conflicting provider input was accepted';
    exception when unique_violation then
        null;
    end;

    -- Once the provider fence is committed, even a retryable failure becomes
    -- provider_unknown. Roll this probe back so the same row can stage success.
    begin
        failed := public.fail_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001',
            'grok-qa:test-worker', 'xai_temporarily_unavailable', true,
            statement_timestamp() + interval '1 minute'
        );
        if failed ->> 'status' <> 'provider_unknown' then
            raise exception 'provider attempt was incorrectly retried: %', failed;
        end if;
        raise exception 'rollback provider_unknown probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback provider_unknown probe' then
            raise;
        end if;
    end;

    begin
        perform public.stage_grok_qa_dispatch_verdict(
            'e1000000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001',
            'grok-qa:test-worker', jsonb_set(
                payload, '{fact_check,source_urls}',
                '["https://x.com/Yellow/status/2083266484789514641"]'::jsonb
            ), 'grok-4.5',
            'official-x-grok-qa@1', 'response_test_00000001', input_hash,
            banner_hash,
            250000000,
            '["https://x.com/SquidRouter/status/2083266484789514640"]'::jsonb,
            1::smallint
        );
        raise exception 'foreign verdict source evidence was accepted';
    exception when invalid_parameter_value then
        null;
    end;

    begin
        perform public.stage_grok_qa_dispatch_verdict(
            'e1000000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001',
            'grok-qa:test-worker', payload, 'grok-4.5',
            'official-x-grok-qa@1', 'response_test_00000001', input_hash,
            banner_hash,
            250000000, '["https://x.com/Other/status/2083266484789514640"]'::jsonb,
            1::smallint
        );
        raise exception 'non-official x_search citation was accepted';
    exception when invalid_parameter_value then
        null;
    end;

    staged := public.stage_grok_qa_dispatch_verdict(
        'e1000000-0000-4000-8000-000000000001',
        'e1400000-0000-4000-8000-000000000001',
        'grok-qa:test-worker', payload, 'grok-4.5',
        'official-x-grok-qa@1', 'response_test_00000001', input_hash,
        banner_hash,
        250000000,
        '["https://x.com/SquidRouter/status/2083266484789514640"]'::jsonb,
        1::smallint
    );
    if staged ->> 'status' <> 'claimed'
       or staged ->> 'reused' <> 'false'
       or staged ->> 'verdict_sha256' !~ '^[a-f0-9]{64}$'
       or staged ->> 'model' <> 'grok-4.5'
       or staged ->> 'provider_response_id' <> 'response_test_00000001'
       or staged ->> 'input_sha256' <> input_hash
       or staged ->> 'banner_sha256' <> banner_hash
       or staged ->> 'cost_in_usd_ticks' <> '250000000'
       or staged ->> 'x_search_calls' <> '1' then
        raise exception 'Grok QA verdict was not staged immutably: %', staged;
    end if;
    staged := public.stage_grok_qa_dispatch_verdict(
        'e1000000-0000-4000-8000-000000000001',
        'e1400000-0000-4000-8000-000000000001',
        'grok-qa:test-worker', payload, 'grok-4.5',
        'official-x-grok-qa@1', 'response_test_00000001', input_hash,
        banner_hash,
        250000000,
        '["https://x.com/SquidRouter/status/2083266484789514640"]'::jsonb,
        1::smallint
    );
    if staged ->> 'reused' <> 'true' then
        raise exception 'exact staged verdict replay was not idempotent';
    end if;
end
$test$;

reset role;

-- Provider response evidence is durable before the Telegram relay is allowed.
do $test$
begin
    if not exists (
        select 1
        from private.grok_qa_dispatch_outbox as dispatch
        where dispatch.content_version_id =
                'e1400000-0000-4000-8000-000000000001'
          and dispatch.status = 'claimed'
          and dispatch.model = 'grok-4.5'
          and dispatch.prompt_version = 'official-x-grok-qa@1'
          and dispatch.provider_response_id = 'response_test_00000001'
          and dispatch.provider_input_sha256 = repeat('a', 64)
          and dispatch.banner_sha256 = repeat('c', 64)
          and dispatch.provider_attempt_started_at is not null
          and dispatch.cost_in_usd_ticks = 250000000
          and dispatch.x_search_citations =
                '["https://x.com/SquidRouter/status/2083266484789514640"]'::jsonb
          and dispatch.x_search_calls = 1
    ) or exists (
        select 1
        from private.grok_qa_verdict_receipts as receipt
        where receipt.content_version_id =
                'e1400000-0000-4000-8000-000000000001'
    ) then
        raise exception 'provider evidence was not persisted before relay';
    end if;
end
$test$;

-- Stale delivery-only work never reaches the relay. Without a durable receipt,
-- both an expired claimed verdict and a reconciled staged verdict close as a
-- terminal source-expired failure. With an exact receipt, maintenance mirrors
-- the receipt state instead of overwriting known delivery evidence.
do $test$
declare
    maintenance jsonb;
    reconciled jsonb;
    receipt jsonb;
    finalized jsonb;
    staged_payload jsonb;
begin
    select verdict into staged_payload
    from private.grok_qa_dispatch_outbox
    where workspace_id = 'e1000000-0000-4000-8000-000000000001'
      and content_version_id = 'e1400000-0000-4000-8000-000000000001';

    -- A manually recorded durable receipt can predate this dispatcher's
    -- staged state. Stale maintenance imports that exact evidence and closes
    -- the pending row without a provider attempt or Telegram relay.
    begin
        receipt := public.claim_grok_qa_verdict(
            'e1000000-0000-4000-8000-000000000001',
            'e1300000-0000-4000-8000-000000000002',
            'e1400000-0000-4000-8000-000000000002', staged_payload
        );
        finalized := public.finalize_grok_qa_verdict(
            'e1000000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000002',
            receipt ->> 'payload_sha256', 'sent', null
        );
        update public.source_items
        set published_at = transaction_timestamp() - interval '2 days'
        where id = 'e1200000-0000-4000-8000-000000000001';
        update private.grok_qa_dispatch_outbox
        set source_published_at = transaction_timestamp() - interval '2 days',
            locked_at = case when status = 'claimed'
                then statement_timestamp() - interval '10 minutes'
                else locked_at end,
            lease_expires_at = case when status = 'claimed'
                then statement_timestamp() - interval '5 minutes'
                else lease_expires_at end
        where workspace_id = 'e1000000-0000-4000-8000-000000000001';
        maintenance := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:manual-receipt', 300, array['squid'], 86400
        );
        if finalized ->> 'status' <> 'sent'
           or maintenance -> 'job' <> 'null'::jsonb or not exists (
                select 1 from private.grok_qa_dispatch_outbox
                where content_version_id = 'e1400000-0000-4000-8000-000000000002'
                  and status = 'sent'
                  and error_code is null
                  and attempts = 0
                  and provider_attempt_started_at is null
                  and verdict = staged_payload
                  and verdict_sha256 = receipt ->> 'payload_sha256'
                  and model is null
                  and prompt_version = 'grok-qa-external-receipt@1'
           ) then
            raise exception 'stale pending manual receipt was not imported exactly: %, %, %',
                finalized, receipt, maintenance;
        end if;
        raise exception 'rollback stale pending manual-receipt probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback stale pending manual-receipt probe' then
            raise;
        end if;
    end;

    begin
        update public.source_items
        set published_at = transaction_timestamp() - interval '2 days'
        where id = 'e1200000-0000-4000-8000-000000000001';
        update private.grok_qa_dispatch_outbox
        set source_published_at = transaction_timestamp() - interval '2 days',
            locked_at = case when status = 'claimed'
                then statement_timestamp() - interval '10 minutes'
                else locked_at end,
            lease_expires_at = case when status = 'claimed'
                then statement_timestamp() - interval '5 minutes'
                else lease_expires_at end
        where workspace_id = 'e1000000-0000-4000-8000-000000000001';
        maintenance := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:stale-claimed', 300, array['squid'], 86400
        );
        if maintenance -> 'job' <> 'null'::jsonb or not exists (
            select 1 from private.grok_qa_dispatch_outbox
            where content_version_id = 'e1400000-0000-4000-8000-000000000001'
              and status = 'failed'
              and error_code = 'grok_qa_source_expired'
              and attempts = 1
        ) then
            raise exception 'stale claimed verdict was not terminalized without relay: %',
                maintenance;
        end if;
        raise exception 'rollback stale claimed-verdict probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback stale claimed-verdict probe' then
            raise;
        end if;
    end;

    begin
        update private.grok_qa_dispatch_outbox
        set locked_at = statement_timestamp() - interval '10 minutes',
            lease_expires_at = statement_timestamp() - interval '5 minutes'
        where content_version_id = 'e1400000-0000-4000-8000-000000000001';
        reconciled := public.reconcile_grok_qa_dispatch_leases(
            'e1000000-0000-4000-8000-000000000001', 10
        );
        update public.source_items
        set published_at = transaction_timestamp() - interval '2 days'
        where id = 'e1200000-0000-4000-8000-000000000001';
        update private.grok_qa_dispatch_outbox
        set source_published_at = transaction_timestamp() - interval '2 days'
        where workspace_id = 'e1000000-0000-4000-8000-000000000001';
        maintenance := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:stale-staged', 300, array['squid'], 86400
        );
        if reconciled ->> 'pending' <> '1'
           or maintenance -> 'job' <> 'null'::jsonb or not exists (
                select 1 from private.grok_qa_dispatch_outbox
                where content_version_id = 'e1400000-0000-4000-8000-000000000001'
                  and status = 'failed'
                  and error_code = 'grok_qa_source_expired'
                  and attempts = 1
           ) then
            raise exception 'stale staged verdict was not terminalized without relay: %, %',
                reconciled, maintenance;
        end if;
        raise exception 'rollback stale staged-verdict probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback stale staged-verdict probe' then
            raise;
        end if;
    end;

    begin
        receipt := public.claim_grok_qa_verdict(
            'e1000000-0000-4000-8000-000000000001',
            'e1300000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001', staged_payload
        );
        update public.source_items
        set published_at = transaction_timestamp() - interval '2 days'
        where id = 'e1200000-0000-4000-8000-000000000001';
        update private.grok_qa_dispatch_outbox
        set source_published_at = transaction_timestamp() - interval '2 days',
            locked_at = case when status = 'claimed'
                then statement_timestamp() - interval '10 minutes'
                else locked_at end,
            lease_expires_at = case when status = 'claimed'
                then statement_timestamp() - interval '5 minutes'
                else lease_expires_at end
        where workspace_id = 'e1000000-0000-4000-8000-000000000001';
        maintenance := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:claimed-receipt', 300, array['squid'], 86400
        );
        if maintenance -> 'job' <> 'null'::jsonb or not exists (
            select 1 from private.grok_qa_dispatch_outbox
            where content_version_id = 'e1400000-0000-4000-8000-000000000001'
              and status = 'delivery_unknown'
              and error_code = 'grok_qa_receipt_claimed'
              and verdict_sha256 = receipt ->> 'payload_sha256'
        ) then
            raise exception 'stale claimed receipt was not reconciled as delivery_unknown: %, %',
                receipt, maintenance;
        end if;
        raise exception 'rollback stale claimed-receipt probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback stale claimed-receipt probe' then
            raise;
        end if;
    end;

    begin
        receipt := public.claim_grok_qa_verdict(
            'e1000000-0000-4000-8000-000000000001',
            'e1300000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001', staged_payload
        );
        finalized := public.finalize_grok_qa_verdict(
            'e1000000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001',
            receipt ->> 'payload_sha256', 'failed', 'grok_qa_relay_failed'
        );
        update public.source_items
        set published_at = transaction_timestamp() - interval '2 days'
        where id = 'e1200000-0000-4000-8000-000000000001';
        update private.grok_qa_dispatch_outbox
        set source_published_at = transaction_timestamp() - interval '2 days',
            locked_at = case when status = 'claimed'
                then statement_timestamp() - interval '10 minutes'
                else locked_at end,
            lease_expires_at = case when status = 'claimed'
                then statement_timestamp() - interval '5 minutes'
                else lease_expires_at end
        where workspace_id = 'e1000000-0000-4000-8000-000000000001';
        maintenance := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:failed-receipt', 300, array['squid'], 86400
        );
        if finalized ->> 'status' <> 'failed'
           or maintenance -> 'job' <> 'null'::jsonb or not exists (
                select 1 from private.grok_qa_dispatch_outbox
                where content_version_id = 'e1400000-0000-4000-8000-000000000001'
                  and status = 'failed'
                  and error_code = 'grok_qa_relay_failed'
                  and verdict_sha256 = receipt ->> 'payload_sha256'
           ) then
            raise exception 'stale failed receipt was not reconciled exactly: %, %, %',
                finalized, receipt, maintenance;
        end if;
        raise exception 'rollback stale failed-receipt probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback stale failed-receipt probe' then
            raise;
        end if;
    end;

    begin
        update private.grok_qa_dispatch_outbox
        set locked_at = statement_timestamp() - interval '10 minutes',
            lease_expires_at = statement_timestamp() - interval '5 minutes'
        where content_version_id = 'e1400000-0000-4000-8000-000000000001';
        reconciled := public.reconcile_grok_qa_dispatch_leases(
            'e1000000-0000-4000-8000-000000000001', 10
        );
        receipt := public.claim_grok_qa_verdict(
            'e1000000-0000-4000-8000-000000000001',
            'e1300000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001', staged_payload
        );
        finalized := public.finalize_grok_qa_verdict(
            'e1000000-0000-4000-8000-000000000001',
            'e1400000-0000-4000-8000-000000000001',
            receipt ->> 'payload_sha256', 'sent', null
        );
        update public.source_items
        set published_at = transaction_timestamp() - interval '2 days'
        where id = 'e1200000-0000-4000-8000-000000000001';
        update private.grok_qa_dispatch_outbox
        set source_published_at = transaction_timestamp() - interval '2 days'
        where workspace_id = 'e1000000-0000-4000-8000-000000000001';
        maintenance := public.claim_grok_qa_dispatch_job(
            'e1000000-0000-4000-8000-000000000001',
            'grok-qa:stale-receipt', 300, array['squid'], 86400
        );
        if finalized ->> 'status' <> 'sent'
           or reconciled ->> 'pending' <> '1'
           or maintenance -> 'job' <> 'null'::jsonb or not exists (
                select 1 from private.grok_qa_dispatch_outbox
                where content_version_id = 'e1400000-0000-4000-8000-000000000001'
                  and status = 'sent'
                  and error_code is null
                  and verdict_sha256 = receipt ->> 'payload_sha256'
           ) then
            raise exception 'stale staged sent receipt was not reconciled without relay: %, %, %, %',
                finalized, reconciled, receipt, maintenance;
        end if;
        raise exception 'rollback stale receipt-reconciliation probe';
    exception when raise_exception then
        if sqlerrm <> 'rollback stale receipt-reconciliation probe' then
            raise;
        end if;
    end;
end
$test$;

-- An expired lease with a fully staged provider result is delivery-only work.
-- Even at max_attempts it must be reclaimable with all evidence preserved and
-- without another provider fence or attempt increment.
update private.grok_qa_dispatch_outbox
set attempts = max_attempts,
    locked_at = statement_timestamp() - interval '10 minutes',
    lease_expires_at = statement_timestamp() - interval '5 minutes',
    updated_at = statement_timestamp()
where content_version_id = 'e1400000-0000-4000-8000-000000000001';

set local role service_role;

do $test$
declare
    reconciled jsonb;
    replayed jsonb;
begin
    reconciled := public.reconcile_grok_qa_dispatch_leases(
        'e1000000-0000-4000-8000-000000000001', 10
    );
    replayed := public.claim_grok_qa_dispatch_job(
        'e1000000-0000-4000-8000-000000000001',
        'grok-qa:test-worker', 300, array['squid'], 86400,
        'e1400000-0000-4000-8000-000000000001'
    );
    if reconciled ->> 'pending' <> '1'
       or replayed -> 'job' ->> 'content_version_id'
            <> 'e1400000-0000-4000-8000-000000000001'
       or replayed -> 'job' -> 'provider_call_required' <> 'false'::jsonb
       or replayed -> 'job' ->> 'attempts' <> '3'
       or replayed -> 'job' ->> 'provider_response_id'
            <> 'response_test_00000001'
       or replayed -> 'job' ->> 'input_sha256' <> repeat('a', 64)
       or replayed -> 'job' ->> 'cost_in_usd_ticks' <> '250000000'
       or replayed -> 'job' ->> 'x_search_calls' <> '1'
       or replayed -> 'job' -> 'verdict' is null then
        raise exception 'staged verdict was not safely replay-claimed: %, %',
            reconciled, replayed;
    end if;
end
$test$;

do $test$
declare
    receipt jsonb;
    finalized jsonb;
    completed jsonb;
    payload constant jsonb := '{
      "decision":"WARN",
      "summary":"공식 원문은 확인했지만 한국어 배너 현지화를 다시 검토해야 합니다.",
      "fact_check":{
        "status":"PASS",
        "checks":["공식 X 원문의 핵심 사실을 확인했습니다."],
        "source_urls":["https://x.com/SquidRouter/status/2083266484789514640"]
      },
      "brand_check":{
        "status":"WARN",
        "checks":["배너의 한국어 가독성을 다시 확인해야 합니다."]
      },
      "issues":[{
        "severity":"WARN",
        "code":"banner_localization",
        "message":"영문 헤드라인을 한국 GTM 문구로 검토하세요."
      }],
      "next_action":"revise_banner"
    }'::jsonb;
begin

    receipt := public.claim_grok_qa_verdict(
        'e1000000-0000-4000-8000-000000000001',
        'e1300000-0000-4000-8000-000000000001',
        'e1400000-0000-4000-8000-000000000001', payload
    );
    finalized := public.finalize_grok_qa_verdict(
        'e1000000-0000-4000-8000-000000000001',
        'e1400000-0000-4000-8000-000000000001',
        receipt ->> 'payload_sha256', 'sent', null
    );
    completed := public.complete_grok_qa_dispatch_job(
        'e1000000-0000-4000-8000-000000000001',
        'e1400000-0000-4000-8000-000000000001',
        'grok-qa:test-worker', receipt ->> 'payload_sha256',
        'sent', null
    );
    if finalized ->> 'status' <> 'sent'
       or completed ->> 'status' <> 'sent' then
        raise exception 'sent Grok QA receipt did not complete the outbox';
    end if;
end
$test$;

do $test$
declare
    claimed jsonb;
    failed jsonb;
begin
    claimed := public.claim_grok_qa_dispatch_job(
        'e1000000-0000-4000-8000-000000000001',
        'grok-qa:retry-worker', 300, array['squid'], 86400
    );
    failed := public.fail_grok_qa_dispatch_job(
        'e1000000-0000-4000-8000-000000000001',
        'e1400000-0000-4000-8000-000000000002',
        'grok-qa:retry-worker', 'xai_temporarily_unavailable', true,
        statement_timestamp() + interval '1 minute'
    );
    if claimed -> 'job' ->> 'content_version_id'
            <> 'e1400000-0000-4000-8000-000000000002'
       or failed ->> 'status' <> 'pending'
       or failed ->> 'attempts' <> '1' then
        raise exception 'pre-verdict retry did not return work to the outbox';
    end if;
end
$test$;

reset role;

-- A frozen source whose official feed is disabled becomes obsolete before the
-- next provider claim; the dispatcher receives no job.
update public.source_feeds
set active = false
where id = 'e1100000-0000-4000-8000-000000000001';
update private.grok_qa_dispatch_outbox
set available_at = statement_timestamp() - interval '1 second'
where content_version_id = 'e1400000-0000-4000-8000-000000000002';

set local role service_role;
do $test$
declare
    result jsonb;
begin
    result := public.claim_grok_qa_dispatch_job(
        'e1000000-0000-4000-8000-000000000001',
        'grok-qa:stale-source', 300, array['squid'], 86400,
        'e1400000-0000-4000-8000-000000000002'
    );
    if result -> 'job' <> 'null'::jsonb then
        raise exception 'stale official source reached the provider claim: %', result;
    end if;
end
$test$;
reset role;

do $test$
begin
    if (
        select status from private.grok_qa_dispatch_outbox
        where content_version_id = 'e1400000-0000-4000-8000-000000000001'
    ) <> 'sent' or (
        select status from private.grok_qa_dispatch_outbox
        where content_version_id = 'e1400000-0000-4000-8000-000000000002'
    ) <> 'obsolete' then
        raise exception 'Grok QA dispatch terminal states are invalid';
    end if;
    if exists (
        select 1 from public.approvals
        where workspace_id = 'e1000000-0000-4000-8000-000000000001'
    ) or exists (
        select 1 from public.publications
        where workspace_id = 'e1000000-0000-4000-8000-000000000001'
    ) or exists (
        select 1 from public.jobs
        where workspace_id = 'e1000000-0000-4000-8000-000000000001'
          and job_kind in ('publish', 'figma_export')
    ) or exists (
        select 1 from public.content_items
        where workspace_id = 'e1000000-0000-4000-8000-000000000001'
          and status <> 'needs_review'
    ) then
        raise exception 'advisory QA dispatch changed approval/publication state';
    end if;
end
$test$;

rollback;
