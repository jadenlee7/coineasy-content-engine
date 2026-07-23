-- Transactional security and state-machine smoke for official-X review drafts.
-- Run after all migrations as the database owner; every row is rolled back.

begin;

do $test$
declare
    signature text;
begin
    foreach signature in array array[
        'public.record_official_x_sources(uuid,text,text,uuid,text,text,jsonb,timestamp with time zone)',
        'public.get_official_x_automation_state(uuid,text,date,integer)',
        'public.queue_review_draft_job(uuid,text,date,jsonb,text,uuid,text,text,text,boolean)',
        'public.claim_review_draft_job(uuid,text,integer)',
        'public.complete_review_draft_job(uuid,text,uuid,uuid)',
        'public.fail_review_draft_job(uuid,text,text,text,boolean,timestamp with time zone)'
    ] loop
        if has_function_privilege('anon', signature, 'execute')
           or has_function_privilege('authenticated', signature, 'execute') then
            raise exception 'official-X worker RPC leaked to a browser role: %', signature;
        end if;
        if not has_function_privilege('service_role', signature, 'execute') then
            raise exception 'official-X worker RPC unavailable to service_role: %', signature;
        end if;
    end loop;
    if has_table_privilege(
        'service_role', 'private.official_x_poll_receipts', 'select'
    ) or has_table_privilege(
        'service_role', 'private.official_x_source_state', 'select'
    ) or has_table_privilege(
        'service_role', 'private.official_x_daily_slots', 'select'
    ) then
        raise exception 'private official-X ledgers leaked direct service-role access';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'b0000000-0000-4000-8000-000000000001',
    'Official X Worker Security Test',
    'official-x-worker-security-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values
    ('b0000000-0000-4000-8000-000000000001', 'yellow', 'Yellow', true, null),
    ('b0000000-0000-4000-8000-000000000001', 'origintrail', 'OriginTrail', true, null),
    ('b0000000-0000-4000-8000-000000000001', 'squid', 'Squid', true, null),
    ('b0000000-0000-4000-8000-000000000001', 'babylon', 'Babylon', true, null);

insert into public.source_feeds (
    workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, active, created_by
)
values
    ('b0000000-0000-4000-8000-000000000001', 'yellow', 'x', 'Yellow official X', 'https://x.com/Yellow', '@Yellow', 15, true, null),
    ('b0000000-0000-4000-8000-000000000001', 'origintrail', 'x', 'OriginTrail official X', 'https://x.com/origin_trail', '@origin_trail', 15, true, null),
    ('b0000000-0000-4000-8000-000000000001', 'squid', 'x', 'Squid official X', 'https://x.com/SquidRouter', '@SquidRouter', 15, true, null),
    ('b0000000-0000-4000-8000-000000000001', 'babylon', 'x', 'Babylon official X', 'https://x.com/babylonlabs_io', '@babylonlabs_io', 15, true, null);

do $test$
declare
    poisoned_source_id uuid;
    poisoned_published_at timestamptz := statement_timestamp() - interval '1 minute';
begin
    insert into public.source_items (
        workspace_id, client_id, source_feed_id, external_id, source_type,
        canonical_url, author_handle, published_at, body, media, raw_payload,
        source_hash, ingested_by
    ) values (
        'b0000000-0000-4000-8000-000000000001',
        'squid',
        (
            select id from public.source_feeds
            where workspace_id = 'b0000000-0000-4000-8000-000000000001'
              and client_id = 'squid'
              and provider = 'x'
        ),
        '900000000000000098',
        'tweet',
        'https://x.com/SquidRouter/status/900000000000000098',
        '@SquidRouter',
        poisoned_published_at,
        'poisoned legacy body',
        '[]'::jsonb,
        '{"is_note_tweet":false,"metrics":{}}'::jsonb,
        pg_catalog.md5('official-x:@squidrouter:900000000000000098'),
        null
    ) returning id into poisoned_source_id;

    begin
        perform public.record_official_x_sources(
            'b0000000-0000-4000-8000-000000000001',
            'squid',
            'SquidRouter',
            'b1000000-0000-4000-8000-000000000098',
            null,
            '900000000000000098',
            jsonb_build_array(jsonb_build_object(
                'external_id', '900000000000000098',
                'source_content', 'clean official body',
                'source_url', 'https://x.com/SquidRouter/status/900000000000000098',
                'published_at', poisoned_published_at,
                'media', '[]'::jsonb,
                'metrics', '{}'::jsonb,
                'is_note_tweet', false
            )),
            statement_timestamp()
        );
        raise exception 'mismatched pre-existing X source was accepted';
    exception when check_violation then null;
    end;

    if exists (
        select 1 from private.official_x_source_state
        where source_item_id = poisoned_source_id
    ) then
        raise exception 'mismatched pre-existing X source reached the pending ledger';
    end if;
    delete from public.source_items where id = poisoned_source_id;
end
$test$;

do $test$
declare
    intake jsonb;
    state jsonb;
    replay jsonb;
    long_text text := btrim(repeat('Squid official long-form product update. ', 12));
begin
    intake := public.record_official_x_sources(
        'b0000000-0000-4000-8000-000000000001',
        'squid',
        'SquidRouter',
        'b1000000-0000-4000-8000-000000000001',
        null,
        '900000000000000001',
        jsonb_build_array(jsonb_build_object(
            'external_id', '900000000000000001',
            'source_content', long_text,
            'source_url', 'https://x.com/SquidRouter/status/900000000000000001',
            'published_at', statement_timestamp(),
            'media', jsonb_build_array(jsonb_build_object(
                'media_key', 'photo-1',
                'type', 'photo',
                'url', 'https://pbs.twimg.com/media/official-squid.jpg',
                'width', 1200,
                'height', 675,
                'ignored_provider_field', 'must not survive'
            )),
            'metrics', jsonb_build_object(
                'like_count', 12,
                'retweet_count', 3,
                'provider_secret', 'must not survive'
            ),
            'is_note_tweet', true
        )),
        statement_timestamp()
    );
    if intake ->> 'last_cursor' <> '900000000000000001'
       or intake ->> 'inserted_count' <> '1'
       or jsonb_array_length(intake -> 'pending_sources') <> 1
       or intake -> 'pending_sources' -> 0 ->> 'is_note_tweet' <> 'true'
       or intake -> 'pending_sources' -> 0 -> 'metrics' ? 'provider_secret'
       or intake -> 'pending_sources' -> 0 -> 'media' -> 0 ? 'ignored_provider_field' then
        raise exception 'official X intake did not return a sanitized pending source';
    end if;

    replay := public.record_official_x_sources(
        'b0000000-0000-4000-8000-000000000001',
        'squid',
        '@SquidRouter',
        'b1000000-0000-4000-8000-000000000001',
        null,
        '900000000000000001',
        jsonb_build_array(jsonb_build_object(
            'external_id', '900000000000000001',
            'source_content', long_text,
            'source_url', 'https://x.com/SquidRouter/status/900000000000000001',
            'published_at', statement_timestamp(),
            'media', jsonb_build_array(jsonb_build_object(
                'media_key', 'photo-1', 'type', 'photo',
                'url', 'https://pbs.twimg.com/media/official-squid.jpg',
                'width', 1200, 'height', 675,
                'ignored_provider_field', 'must not survive'
            )),
            'metrics', jsonb_build_object(
                'like_count', 12, 'retweet_count', 3,
                'provider_secret', 'must not survive'
            ),
            'is_note_tweet', true
        )),
        statement_timestamp()
    );
    if replay ->> 'reused' <> 'true'
       or replay -> 'source_item_ids' is distinct from intake -> 'source_item_ids' then
        raise exception 'poll receipt replay was not exactly idempotent';
    end if;

    -- Simulate a crash after record and before queue. A later empty poll must
    -- still expose the unreserved source instead of losing it behind since_id.
    perform public.record_official_x_sources(
        'b0000000-0000-4000-8000-000000000001',
        'squid',
        'SquidRouter',
        'b1000000-0000-4000-8000-000000000002',
        '900000000000000001',
        '900000000000000001',
        '[]'::jsonb,
        statement_timestamp()
    );
    state := public.get_official_x_automation_state(
        'b0000000-0000-4000-8000-000000000001',
        'squid',
        pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date,
        8
    );
    if state ->> 'last_cursor' <> '900000000000000001'
       or state ->> 'draft_reserved_today' <> 'false'
       or state ->> 'pending_count' <> '1'
       or state -> 'pending_sources' -> 0 ->> 'external_id' <> '900000000000000001' then
        raise exception 'record-without-queue recovery failed';
    end if;

    begin
        perform public.record_official_x_sources(
            'b0000000-0000-4000-8000-000000000001', 'squid', 'SquidRouter',
            'b1000000-0000-4000-8000-000000000003', null,
            '900000000000000001', '[]'::jsonb, statement_timestamp()
        );
        raise exception 'stale feed cursor was accepted';
    exception when serialization_failure then null;
    end;

    begin
        perform public.record_official_x_sources(
            'b0000000-0000-4000-8000-000000000001', 'squid', 'SquidRouter',
            'b1000000-0000-4000-8000-000000000004',
            '900000000000000001', '900000000000000002',
            jsonb_build_array(jsonb_build_object(
                'id', '900000000000000002',
                'text', 'spoofed official source',
                'url', 'https://x.com/attacker/status/900000000000000002',
                'created_at', statement_timestamp(),
                'media', '[]'::jsonb
            )),
            statement_timestamp()
        );
        raise exception 'spoofed X URL was accepted';
    exception when invalid_parameter_value then null;
    end;
end
$test$;

-- Record one recoverable source for each remaining official client.
select public.record_official_x_sources(
    'b0000000-0000-4000-8000-000000000001', 'yellow', 'Yellow',
    'b1000000-0000-4000-8000-000000000011', null, '900000000000000011',
    jsonb_build_array(jsonb_build_object(
        'id', '900000000000000011', 'text', 'Yellow official tutorial guide is live today.',
        'url', 'https://x.com/Yellow/status/900000000000000011',
        'created_at', statement_timestamp(), 'media', '[]'::jsonb,
        'metrics', '{}'::jsonb, 'is_note_tweet', false
    )), statement_timestamp()
);

select public.record_official_x_sources(
    'b0000000-0000-4000-8000-000000000001', 'origintrail', 'origin_trail',
    'b1000000-0000-4000-8000-000000000012', null, '900000000000000012',
    jsonb_build_array(jsonb_build_object(
        'id', '900000000000000012', 'text', 'OriginTrail official network update is now live.',
        'url', 'https://x.com/origin_trail/status/900000000000000012',
        'created_at', statement_timestamp(), 'media', '[]'::jsonb,
        'metrics', '{}'::jsonb, 'is_note_tweet', false
    )), statement_timestamp()
);

select public.record_official_x_sources(
    'b0000000-0000-4000-8000-000000000001', 'babylon', 'babylonlabs_io',
    'b1000000-0000-4000-8000-000000000013', null, '900000000000000013',
    jsonb_build_array(jsonb_build_object(
        'id', '900000000000000013', 'text', 'Babylon official protocol update is available now.',
        'url', 'https://x.com/babylonlabs_io/status/900000000000000013',
        'created_at', statement_timestamp(), 'media', '[]'::jsonb,
        'metrics', '{}'::jsonb, 'is_note_tweet', false
    )), statement_timestamp()
);

do $test$
declare
    squid_source uuid;
    yellow_source uuid;
    queued jsonb;
    replay jsonb;
    claimed jsonb;
    long_text text := btrim(repeat('Squid official long-form product update. ', 12));
    kst_day date := pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date;
begin
    select id into squid_source from public.source_items
    where workspace_id = 'b0000000-0000-4000-8000-000000000001'
      and client_id = 'squid' and external_id = '900000000000000001';
    queued := public.queue_review_draft_job(
        'b0000000-0000-4000-8000-000000000001', 'squid', kst_day,
        jsonb_build_array(squid_source), 'article',
        'b2000000-0000-4000-8000-000000000001', long_text,
        'https://x.com/SquidRouter/status/900000000000000001',
        'https://pbs.twimg.com/media/official-squid.jpg', false
    );
    replay := public.queue_review_draft_job(
        'b0000000-0000-4000-8000-000000000001', 'squid', kst_day,
        jsonb_build_array(squid_source), 'article',
        'b2000000-0000-4000-8000-000000000001', long_text,
        'https://x.com/SquidRouter/status/900000000000000001',
        'https://pbs.twimg.com/media/official-squid.jpg', false
    );
    if queued ->> 'status' <> 'queued'
       or replay ->> 'reused' <> 'true'
       or replay ->> 'job_id' <> queued ->> 'job_id' then
        raise exception 'KST-day client reservation was not idempotent';
    end if;
    begin
        perform public.queue_review_draft_job(
            'b0000000-0000-4000-8000-000000000001', 'squid', kst_day,
            jsonb_build_array(squid_source), 'daily_news',
            'b2000000-0000-4000-8000-000000000099', long_text,
            'https://x.com/SquidRouter/status/900000000000000001',
            '', false
        );
        raise exception 'different retry reused a client KST-day reservation';
    exception when unique_violation then null;
    end;

    select id into yellow_source from public.source_items
    where workspace_id = 'b0000000-0000-4000-8000-000000000001'
      and client_id = 'yellow' and external_id = '900000000000000011';
    perform public.queue_review_draft_job(
        'b0000000-0000-4000-8000-000000000001', 'yellow', kst_day,
        jsonb_build_array(yellow_source), 'tutorial',
        'b2000000-0000-4000-8000-000000000011',
        'Yellow official tutorial guide is live today.',
        'https://x.com/Yellow/status/900000000000000011', '', true
    );
    claimed := public.claim_review_draft_job(
        'b0000000-0000-4000-8000-000000000001',
        'official-x:test-worker', 900
    );
    if claimed ->> 'client_id' <> 'squid'
       or claimed ->> 'locked_by' <> 'official-x:test-worker'
       or claimed ->> 'attempts' <> '1'
       or claimed -> 'input' ->> 'request_id' <> 'b2000000-0000-4000-8000-000000000001'
       or claimed -> 'input' -> 'source_item_ids' is distinct from jsonb_build_array(squid_source) then
        raise exception 'automatic review-draft claim contract is invalid';
    end if;
    begin
        perform public.complete_review_draft_job(
            (claimed ->> 'job_id')::uuid,
            'official-x:stale-worker',
            'b2000000-0000-4000-8000-000000000001',
            'b3000000-0000-4000-8000-000000000001'
        );
        raise exception 'stale worker completed another lease';
    exception when object_not_in_prerequisite_state then null;
    end;
end
$test$;

insert into public.content_items (
    id, workspace_id, client_id, content_kind, title, status
)
values (
    'b2000000-0000-4000-8000-000000000001',
    'b0000000-0000-4000-8000-000000000001',
    'squid', 'article', 'Squid 한국어 아티클', 'needs_review'
);

insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version,
    locale, title, content, channel_copy, deliverables, qa, generation_meta
)
values (
    'b3000000-0000-4000-8000-000000000001',
    'b0000000-0000-4000-8000-000000000001',
    'b2000000-0000-4000-8000-000000000001',
    1, 'article@1', 'ko-KR', 'Squid 한국어 아티클',
    '{"request_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'::jsonb,
    '{}'::jsonb, '{"asset_ids":[]}'::jsonb,
    '{"manual_review_required":true}'::jsonb,
    '{"request_id":"b2000000-0000-4000-8000-000000000001","mock_mode":false}'::jsonb
);

update public.content_items
set current_version_id = 'b3000000-0000-4000-8000-000000000001'
where id = 'b2000000-0000-4000-8000-000000000001';

do $test$
declare
    job_id uuid;
    completed jsonb;
    replay jsonb;
    claim_after jsonb;
begin
    select id into job_id from public.jobs
    where input ->> 'request_id' = 'b2000000-0000-4000-8000-000000000001';
    completed := public.complete_review_draft_job(
        job_id, 'official-x:test-worker',
        'b2000000-0000-4000-8000-000000000001',
        'b3000000-0000-4000-8000-000000000001'
    );
    replay := public.complete_review_draft_job(
        job_id, 'official-x:test-worker',
        'b2000000-0000-4000-8000-000000000001',
        'b3000000-0000-4000-8000-000000000001'
    );
    if completed ->> 'status' <> 'succeeded'
       or replay ->> 'reused' <> 'true'
       or (select count(*) from public.content_source_links
           where content_item_id = 'b2000000-0000-4000-8000-000000000001') <> 1 then
        raise exception 'review draft completion was not exactly idempotent';
    end if;
    claim_after := public.claim_review_draft_job(
        'b0000000-0000-4000-8000-000000000001',
        'official-x:manual-check', 900
    );
    if claim_after is not null then
        raise exception 'manual-only tutorial was claimed';
    end if;
end
$test$;

do $test$
declare
    source_id uuid;
    queued jsonb;
    claimed jsonb;
    failed jsonb;
    reclaimed jsonb;
    terminal jsonb;
    kst_day date := pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date;
begin
    select id into source_id from public.source_items
    where workspace_id = 'b0000000-0000-4000-8000-000000000001'
      and client_id = 'origintrail' and external_id = '900000000000000012';
    queued := public.queue_review_draft_job(
        'b0000000-0000-4000-8000-000000000001', 'origintrail', kst_day,
        jsonb_build_array(source_id), 'daily_news',
        'b2000000-0000-4000-8000-000000000012',
        'OriginTrail official network update is now live.',
        'https://x.com/origin_trail/status/900000000000000012', '', false
    );
    claimed := public.claim_review_draft_job(
        'b0000000-0000-4000-8000-000000000001',
        'official-x:failure-worker', 900
    );
    failed := public.fail_review_draft_job(
        (queued ->> 'job_id')::uuid,
        'official-x:failure-worker',
        'studio_unavailable', 'studio_unavailable', true,
        statement_timestamp() + interval '1 minute'
    );
    if claimed ->> 'job_id' <> queued ->> 'job_id'
       or failed ->> 'status' <> 'retrying' then
        raise exception 'retryable worker failure did not release its lease';
    end if;
    update public.jobs set available_at = statement_timestamp() - interval '1 second'
    where id = (queued ->> 'job_id')::uuid;
    reclaimed := public.claim_review_draft_job(
        'b0000000-0000-4000-8000-000000000001',
        'official-x:failure-worker-2', 900
    );
    terminal := public.fail_review_draft_job(
        (queued ->> 'job_id')::uuid,
        'official-x:failure-worker-2',
        'invalid_generation', 'invalid_generation', false, null
    );
    if reclaimed ->> 'attempts' <> '2'
       or terminal ->> 'status' <> 'failed' then
        raise exception 'nonretryable worker failure was not terminal';
    end if;
end
$test$;

do $test$
declare
    source_id uuid;
    queued jsonb;
    claimed jsonb;
    recovered jsonb;
    kst_day date := pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date;
begin
    select id into source_id from public.source_items
    where workspace_id = 'b0000000-0000-4000-8000-000000000001'
      and client_id = 'babylon' and external_id = '900000000000000013';
    queued := public.queue_review_draft_job(
        'b0000000-0000-4000-8000-000000000001', 'babylon', kst_day,
        jsonb_build_array(source_id), 'daily_news',
        'b2000000-0000-4000-8000-000000000013',
        'Babylon official protocol update is available now.',
        'https://x.com/babylonlabs_io/status/900000000000000013', '', false
    );
    claimed := public.claim_review_draft_job(
        'b0000000-0000-4000-8000-000000000001',
        'official-x:lease-worker', 900
    );
    update public.jobs
    set attempts = max_attempts,
        lease_expires_at = statement_timestamp() - interval '1 second'
    where id = (queued ->> 'job_id')::uuid;
    recovered := public.claim_review_draft_job(
        'b0000000-0000-4000-8000-000000000001',
        'official-x:lease-recovery', 900
    );
    if claimed ->> 'job_id' <> queued ->> 'job_id'
       or recovered is not null
       or not exists (
           select 1 from public.jobs
           where id = (queued ->> 'job_id')::uuid
             and status = 'failed'
             and locked_by is null
             and lease_expires_at is null
             and last_error_code = 'lease_expired'
       ) then
        raise exception 'expired terminal lease was not recovered safely';
    end if;
end
$test$;

do $test$
declare
    kst_day date := pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date;
begin
    if (select count(*) from private.official_x_daily_slots
        where workspace_id = 'b0000000-0000-4000-8000-000000000001'
          and kst_date = kst_day) <> 4
       or (select count(distinct slot) from private.official_x_daily_slots
           where workspace_id = 'b0000000-0000-4000-8000-000000000001'
             and kst_date = kst_day) <> 4
       or (select min(slot) from private.official_x_daily_slots
           where workspace_id = 'b0000000-0000-4000-8000-000000000001'
             and kst_date = kst_day) <> 1
       or (select max(slot) from private.official_x_daily_slots
           where workspace_id = 'b0000000-0000-4000-8000-000000000001'
             and kst_date = kst_day) <> 4 then
        raise exception 'global KST-day slot ledger is invalid';
    end if;
    if exists (
        select 1 from public.jobs
        where workspace_id = 'b0000000-0000-4000-8000-000000000001'
          and job_kind in ('publish', 'figma_export')
    ) or exists (
        select 1 from public.publications
        where workspace_id = 'b0000000-0000-4000-8000-000000000001'
    ) then
        raise exception 'automation created a publication or export job';
    end if;
end
$test$;

rollback;
