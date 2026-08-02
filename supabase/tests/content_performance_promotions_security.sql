-- Transactional exact-link and recommendation-only promotion smoke.
-- Run after all migrations as the database owner; every row is rolled back.

begin;

insert into auth.users (id)
values ('c9000000-0000-4000-8000-000000000001');

do $test$
declare
    signature text;
begin
    foreach signature in array array[
        'public.record_manual_publication_observation(uuid,uuid,uuid,text,text)',
        'public.record_content_promotion_candidates(uuid,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone,text,jsonb)',
        'public.list_content_promotion_recommendations(uuid,uuid,uuid)'
    ] loop
        if has_function_privilege('anon', signature, 'execute')
           or has_function_privilege('authenticated', signature, 'execute') then
            raise exception 'promotion RPC leaked to a browser role: %', signature;
        end if;
        if not has_function_privilege('service_role', signature, 'execute') then
            raise exception 'promotion RPC unavailable to service_role: %', signature;
        end if;
    end loop;

    if has_table_privilege(
        'service_role', 'private.content_performance_evidence', 'select'
    ) or has_table_privilege(
        'service_role', 'private.content_promotion_candidate_receipts', 'select'
    ) or has_table_privilege(
        'service_role', 'private.content_promotion_recommendations', 'select'
    ) then
        raise exception 'private promotion evidence leaked direct access';
    end if;

    if has_function_privilege(
        'anon',
        'public.request_content_publication(uuid,uuid,text,timestamp with time zone,text)',
        'execute'
    ) or not has_function_privilege(
        'authenticated',
        'public.request_content_publication(uuid,uuid,text,timestamp with time zone,text)',
        'execute'
    ) then
        raise exception 'publication request RPC grant contract changed';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'c0000000-0000-4000-8000-000000000001',
    'Content Performance Security Test',
    'content-performance-security-test',
    null
);

insert into public.workspace_members (
    workspace_id, user_id, role, status, invited_by
)
values (
    'c0000000-0000-4000-8000-000000000001',
    'c9000000-0000-4000-8000-000000000001',
    'editor',
    'active',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values
    (
        'c0000000-0000-4000-8000-000000000001',
        'squid',
        'Squid',
        true,
        null
    ),
    (
        'c0000000-0000-4000-8000-000000000001',
        'yellow',
        'Yellow',
        true,
        null
    );

insert into public.source_feeds (
    id,
    workspace_id,
    client_id,
    provider,
    name,
    source_url,
    handle,
    active,
    created_by
)
values
    (
        'c1000000-0000-4000-8000-000000000001',
        'c0000000-0000-4000-8000-000000000001',
        'squid',
        'x',
        'Squid official X',
        'https://x.com/SquidRouter',
        '@SquidRouter',
        true,
        null
    ),
    (
        'c1000000-0000-4000-8000-000000000002',
        'c0000000-0000-4000-8000-000000000001',
        'yellow',
        'x',
        'Yellow official X',
        'https://x.com/Yellow',
        '@Yellow',
        true,
        null
    );

insert into public.source_items (
    id,
    workspace_id,
    client_id,
    source_feed_id,
    external_id,
    source_type,
    canonical_url,
    author_handle,
    published_at,
    body,
    media,
    raw_payload,
    source_hash,
    ingested_by
)
values
    (
        'c2000000-0000-4000-8000-000000000001',
        'c0000000-0000-4000-8000-000000000001',
        'squid',
        'c1000000-0000-4000-8000-000000000001',
        '900000000000000101',
        'tweet',
        'https://x.com/SquidRouter/status/900000000000000101',
        '@SquidRouter',
        statement_timestamp() - interval '20 hours',
        btrim(repeat(
            'How to use the official Squid API step-by-step tutorial with docs. ',
            8
        )),
        '[]'::jsonb,
        '{}'::jsonb,
        pg_catalog.md5('promotion-squid-source'),
        null
    ),
    (
        'c2000000-0000-4000-8000-000000000002',
        'c0000000-0000-4000-8000-000000000001',
        'yellow',
        'c1000000-0000-4000-8000-000000000002',
        '900000000000000102',
        'tweet',
        'https://x.com/Yellow/status/900000000000000102',
        '@Yellow',
        statement_timestamp() - interval '20 hours',
        'Short official update.',
        '[]'::jsonb,
        '{}'::jsonb,
        pg_catalog.md5('promotion-yellow-source'),
        null
    );

insert into public.content_items (
    id,
    workspace_id,
    client_id,
    content_kind,
    title,
    status,
    created_by
)
values
    (
        'c3000000-0000-4000-8000-000000000001',
        'c0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Squid daily news',
        'needs_review',
        null
    ),
    (
        'c3000000-0000-4000-8000-000000000002',
        'c0000000-0000-4000-8000-000000000001',
        'yellow',
        'daily_news',
        'Yellow daily news',
        'needs_review',
        null
    );

insert into public.content_versions (
    id,
    workspace_id,
    content_item_id,
    version_number,
    prompt_version,
    locale,
    title,
    content,
    generation_meta,
    created_by
)
values
    (
        'c4000000-0000-4000-8000-000000000001',
        'c0000000-0000-4000-8000-000000000001',
        'c3000000-0000-4000-8000-000000000001',
        1,
        'security-test-v1',
        'ko-KR',
        'Squid daily news',
        '{}'::jsonb,
        '{"mock_mode":false,"fact_check":{"schema_version":"1.0","policy_version":"double-fact-check@1","content_kind":"daily_news","status":"review","human_review_required":true,"input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","output_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","checks":[{"id":"source_evidence","status":"review","label":"Source evidence","detail":"Human verification fixture.","metrics":{}},{"id":"output_claims","status":"pass","label":"Output claims","detail":"Output fixture.","metrics":{}}]}}'::jsonb,
        null
    ),
    (
        'c4000000-0000-4000-8000-000000000002',
        'c0000000-0000-4000-8000-000000000001',
        'c3000000-0000-4000-8000-000000000002',
        1,
        'security-test-v1',
        'ko-KR',
        'Yellow daily news',
        '{}'::jsonb,
        '{"mock_mode":false,"fact_check":{"schema_version":"1.0","policy_version":"double-fact-check@1","content_kind":"daily_news","status":"review","human_review_required":true,"input_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","output_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","checks":[{"id":"source_evidence","status":"review","label":"Source evidence","detail":"Human verification fixture.","metrics":{}},{"id":"output_claims","status":"pass","label":"Output claims","detail":"Output fixture.","metrics":{}}]}}'::jsonb,
        null
    );

update public.content_items
set current_version_id = case client_id
        when 'squid' then 'c4000000-0000-4000-8000-000000000001'::uuid
        else 'c4000000-0000-4000-8000-000000000002'::uuid
    end
where workspace_id = 'c0000000-0000-4000-8000-000000000001';

insert into public.approvals (
    id, workspace_id, client_id, content_item_id, content_version_id,
    reviewer_id, reviewer_source, decision, fact_check_policy_version,
    source_facts_verified, output_claims_verified
) values
(
    'c5000000-0000-4000-8000-000000000001',
    'c0000000-0000-4000-8000-000000000001',
    'squid',
    'c3000000-0000-4000-8000-000000000001',
    'c4000000-0000-4000-8000-000000000001',
    'c9000000-0000-4000-8000-000000000001',
    'supabase_auth',
    'approved',
    'double-fact-check@1',
    true,
    true
),
(
    'c5000000-0000-4000-8000-000000000003',
    'c0000000-0000-4000-8000-000000000001',
    'yellow',
    'c3000000-0000-4000-8000-000000000002',
    'c4000000-0000-4000-8000-000000000002',
    'c9000000-0000-4000-8000-000000000001',
    'supabase_auth',
    'approved',
    'double-fact-check@1',
    true,
    true
);

insert into public.content_source_links (
    workspace_id,
    client_id,
    content_item_id,
    source_item_id,
    position
)
values
    (
        'c0000000-0000-4000-8000-000000000001',
        'squid',
        'c3000000-0000-4000-8000-000000000001',
        'c2000000-0000-4000-8000-000000000001',
        0
    ),
    (
        'c0000000-0000-4000-8000-000000000001',
        'yellow',
        'c3000000-0000-4000-8000-000000000002',
        'c2000000-0000-4000-8000-000000000002',
        0
    );

do $test$
declare
    observed jsonb;
    replay jsonb;
    yellow_observed jsonb;
begin
    begin
        perform public.record_manual_publication_observation(
            'c0000000-0000-4000-8000-000000000001',
            'c3000000-0000-4000-8000-000000000001',
            'c4000000-0000-4000-8000-000000000002',
            'x',
            'https://x.com/SquidKorea/status/900000000000000111'
        );
        raise exception 'stale content version was accepted';
    exception when check_violation then
        null;
    end;

    begin
        perform public.record_manual_publication_observation(
            'c0000000-0000-4000-8000-000000000001',
            'c3000000-0000-4000-8000-000000000001',
            'c4000000-0000-4000-8000-000000000001',
            'x',
            'https://x.com/SquidRouter/status/900000000000000111'
        );
        raise exception 'non-local X channel was accepted';
    exception when invalid_parameter_value then
        null;
    end;

    observed := public.record_manual_publication_observation(
        'c0000000-0000-4000-8000-000000000001',
        'c3000000-0000-4000-8000-000000000001',
        'c4000000-0000-4000-8000-000000000001',
        'x',
        'https://x.com/SquidKorea/status/900000000000000111'
    );
    if observed ->> 'external_url'
            <> 'https://x.com/squidkorea/status/900000000000000111'
       or observed ->> 'reused' <> 'false'
       or observed ->> 'content_version_id'
            <> 'c4000000-0000-4000-8000-000000000001' then
        raise exception 'manual publication observation was not canonical';
    end if;

    replay := public.record_manual_publication_observation(
        'c0000000-0000-4000-8000-000000000001',
        'c3000000-0000-4000-8000-000000000001',
        'c4000000-0000-4000-8000-000000000001',
        'x',
        'https://x.com/squidkorea/status/900000000000000111'
    );
    if replay ->> 'publication_id' <> observed ->> 'publication_id'
       or replay ->> 'reused' <> 'true' then
        raise exception 'manual publication observation was not idempotent';
    end if;
    if (
        select count(*)
        from public.event_log
        where workspace_id = 'c0000000-0000-4000-8000-000000000001'
          and event_type = 'manual_publication_observed'
          and data ->> 'content_item_id'
                = 'c3000000-0000-4000-8000-000000000001'
    ) <> 1 then
        raise exception 'idempotent observation duplicated its event';
    end if;

    insert into public.publications (
        id,
        workspace_id,
        client_id,
        content_item_id,
        content_version_id,
        channel,
        status,
        published_at,
        external_id,
        external_url
    ) values (
        'c5000000-0000-4000-8000-000000000002',
        'c0000000-0000-4000-8000-000000000001',
        'yellow',
        'c3000000-0000-4000-8000-000000000002',
        'c4000000-0000-4000-8000-000000000002',
        'x',
        'published',
        statement_timestamp() - interval '18 hours',
        '900000000000000112',
        'https://x.com/yellow__korea/status/900000000000000112'
    );

    yellow_observed := public.record_manual_publication_observation(
        'c0000000-0000-4000-8000-000000000001',
        'c3000000-0000-4000-8000-000000000002',
        'c4000000-0000-4000-8000-000000000002',
        'x',
        'https://x.com/Yellow__Korea/status/900000000000000112'
    );
    if yellow_observed ->> 'reused' <> 'true'
       or (
            select count(*)
            from public.event_log
            where entity_id = 'c5000000-0000-4000-8000-000000000002'
              and event_type = 'manual_publication_observed'
       ) <> 1 then
        raise exception 'pre-existing publication observation was not recorded';
    end if;

    if exists (
        select 1
        from public.jobs
        where workspace_id = 'c0000000-0000-4000-8000-000000000001'
    ) then
        raise exception 'manual observation queued external publishing';
    end if;
end
$test$;

-- Operation ordering 1: a real publication request gets the item lock first.
-- Manual observation for that exact current version/channel must not create a
-- second published row while either the publication or its job is active.
select set_config(
    'request.jwt.claim.sub',
    'c9000000-0000-4000-8000-000000000001',
    true
);

update public.content_items
set status = 'approved', scheduled_for = null
where id = 'c3000000-0000-4000-8000-000000000001';

do $test$
declare
    queued_publication_id uuid;
begin
    queued_publication_id := public.request_content_publication(
        'c3000000-0000-4000-8000-000000000001',
        'c4000000-0000-4000-8000-000000000001',
        'telegram',
        null,
        'promotion-race-queue-first'
    );

    begin
        perform public.record_manual_publication_observation(
            'c0000000-0000-4000-8000-000000000001',
            'c3000000-0000-4000-8000-000000000001',
            'c4000000-0000-4000-8000-000000000001',
            'telegram',
            'https://t.me/Squid_Kor_Update/900000000000000113'
        );
        raise exception 'manual observation bypassed an active publish';
    exception when unique_violation then
        null;
    end;

    if not exists (
        select 1
        from public.publications as publication
        where publication.id = queued_publication_id
          and publication.status = 'queued'
    ) or not exists (
        select 1
        from public.jobs as job
        where job.content_item_id
                = 'c3000000-0000-4000-8000-000000000001'
          and job.job_kind = 'publish'
          and job.status = 'queued'
          and job.input ->> 'publication_id' = queued_publication_id::text
    ) then
        raise exception 'queue-first test lost its active publish state';
    end if;

    if exists (
        select 1
        from public.publications as publication
        where publication.content_version_id
                = 'c4000000-0000-4000-8000-000000000001'
          and publication.channel = 'telegram'
          and publication.status = 'published'
    ) or exists (
        select 1
        from public.event_log as event
        where event.event_type = 'manual_publication_observed'
          and event.data ->> 'content_version_id'
                = 'c4000000-0000-4000-8000-000000000001'
          and event.data ->> 'channel' = 'telegram'
    ) then
        raise exception 'queue-first conflict created a manual publication';
    end if;
end
$test$;

-- Operation ordering 2: the external/manual observation got the item lock
-- first above. A later request must not enqueue another X publication.
do $test$
begin
    begin
        perform public.request_content_publication(
            'c3000000-0000-4000-8000-000000000001',
            'c4000000-0000-4000-8000-000000000001',
            'x',
            null,
            'promotion-race-observation-first'
        );
        raise exception 'publication queued after a manual observation';
    exception when unique_violation then
        null;
    end;

    if (
        select count(*)
        from public.publications as publication
        where publication.content_version_id
                = 'c4000000-0000-4000-8000-000000000001'
          and publication.channel = 'x'
    ) <> 1 or exists (
        select 1
        from public.jobs as job
        where job.content_item_id
                = 'c3000000-0000-4000-8000-000000000001'
          and job.job_kind = 'publish'
          and job.input ->> 'content_version_id'
                = 'c4000000-0000-4000-8000-000000000001'
          and job.input ->> 'channel' = 'x'
    ) then
        raise exception 'observation-first conflict created queued work';
    end if;
end
$test$;

select public.record_content_signal_ranking_evidence(
    'c0000000-0000-4000-8000-000000000001',
    'squid',
    repeat('a', 64),
    '1.1',
    statement_timestamp(),
    statement_timestamp() - interval '7 days',
    statement_timestamp(),
    'official-x-demand-v1',
    '[]'::jsonb
);

select public.record_content_signal_ranking_evidence(
    'c0000000-0000-4000-8000-000000000001',
    'yellow',
    repeat('c', 64),
    '1.1',
    statement_timestamp(),
    statement_timestamp() - interval '7 days',
    statement_timestamp(),
    'official-x-demand-v1',
    '[]'::jsonb
);

do $test$
declare
    generated_at timestamptz;
    window_start timestamptz;
    window_end timestamptz;
    result jsonb;
    replay jsonb;
    recommendations jsonb;
    publication_count integer;
    job_count integer;
    figma_count integer;
    candidates jsonb;
begin
    select
        ranking.generated_at,
        ranking.window_start,
        ranking.window_end
    into generated_at, window_start, window_end
    from private.content_signal_ranking_evidence as ranking
    where ranking.workspace_id = 'c0000000-0000-4000-8000-000000000001'
      and ranking.client_id = 'squid'
      and ranking.snapshot_hash = repeat('a', 64);

    candidates := jsonb_build_array(
        jsonb_build_object(
            'candidate_id', repeat('1', 64),
            'channel', 'x',
            'source_url',
                'https://x.com/squidkorea/status/900000000000000111',
            'published_at', generated_at - interval '18 hours',
            'score', 0.875,
            'reach_percentile', 0.9,
            'interaction_percentile', 0.8,
            'community_match_count', 3,
            'cohort_size', 12,
            'observation_age_hours', 18,
            'recommended_formats', jsonb_build_array('article', 'tutorial'),
            'reason_codes', jsonb_build_array(
                'high_reach',
                'high_interaction',
                'community_alignment',
                'tutorial_learning_signal'
            )
        ),
        jsonb_build_object(
            'candidate_id', repeat('2', 64),
            'channel', 'x',
            'source_url',
                'https://x.com/squidkorea/status/900000000000000999',
            'published_at', generated_at - interval '18 hours',
            'score', 0.875,
            'reach_percentile', 0.9,
            'interaction_percentile', 0.8,
            'community_match_count', 3,
            'cohort_size', 12,
            'observation_age_hours', 18,
            'recommended_formats', jsonb_build_array('article', 'tutorial'),
            'reason_codes', jsonb_build_array(
                'high_reach',
                'high_interaction',
                'community_alignment',
                'tutorial_learning_signal'
            )
        )
    );

    select count(*) into publication_count
    from public.publications
    where workspace_id = 'c0000000-0000-4000-8000-000000000001';
    select count(*) into job_count
    from public.jobs
    where workspace_id = 'c0000000-0000-4000-8000-000000000001';
    select count(*) into figma_count
    from public.figma_links
    where workspace_id = 'c0000000-0000-4000-8000-000000000001';

    result := public.record_content_promotion_candidates(
        'c0000000-0000-4000-8000-000000000001',
        'squid',
        repeat('a', 64),
        '1.1',
        generated_at,
        window_start,
        window_end,
        'content-performance-v1',
        candidates
    );

    if result ->> 'candidate_count' <> '2'
       or result ->> 'matched_count' <> '1'
       or result ->> 'evidence_count' <> '1'
       or result ->> 'recommendation_count' <> '2'
       or result ->> 'reused' <> 'false' then
        raise exception 'exact-match promotion receipt is incorrect: %', result;
    end if;

    if publication_count <> (
        select count(*) from public.publications
        where workspace_id = 'c0000000-0000-4000-8000-000000000001'
    ) or job_count <> (
        select count(*) from public.jobs
        where workspace_id = 'c0000000-0000-4000-8000-000000000001'
    ) or figma_count <> (
        select count(*) from public.figma_links
        where workspace_id = 'c0000000-0000-4000-8000-000000000001'
    ) then
        raise exception 'candidate recording created an external side effect';
    end if;

    replay := public.record_content_promotion_candidates(
        'c0000000-0000-4000-8000-000000000001',
        'squid',
        repeat('a', 64),
        '1.1',
        generated_at,
        window_start,
        window_end,
        'content-performance-v1',
        candidates
    );
    if replay ->> 'recommendation_count' <> '2'
       or replay ->> 'reused' <> 'true' then
        raise exception 'promotion candidate replay was not idempotent: %', replay;
    end if;

    begin
        perform public.record_content_promotion_candidates(
            'c0000000-0000-4000-8000-000000000001',
            'squid',
            repeat('a', 64),
            '1.1',
            generated_at,
            window_start,
            window_end,
            'content-performance-v1',
            jsonb_build_array(candidates -> 0)
        );
        raise exception 'divergent candidate replay was accepted';
    exception when unique_violation then
        null;
    end;

    recommendations := public.list_content_promotion_recommendations(
        'c0000000-0000-4000-8000-000000000001',
        'c3000000-0000-4000-8000-000000000001',
        'c4000000-0000-4000-8000-000000000001'
    );
    if jsonb_array_length(recommendations -> 'items') <> 2
       or exists (
            select 1
            from jsonb_array_elements(recommendations -> 'items') as item(value)
            where item.value - array[
                'recommendation_id',
                'content_version_id',
                'target_kind',
                'score',
                'reason_codes',
                'policy_version',
                'source_url',
                'channel',
                'observed_at',
                'source_ready'
            ] <> '{}'::jsonb
               or item.value ->> 'source_ready' <> 'true'
       ) then
        raise exception 'promotion recommendation list is not bounded: %',
            recommendations;
    end if;
    if jsonb_array_length(recommendations -> 'publications') <> 1
       or recommendations -> 'publications' -> 0 ->> 'external_url'
            <> 'https://x.com/squidkorea/status/900000000000000111' then
        raise exception 'manual publication state was not returned: %',
            recommendations;
    end if;

    begin
        update private.content_performance_evidence
        set score = score
        where workspace_id = 'c0000000-0000-4000-8000-000000000001';
        raise exception 'performance evidence was mutable';
    exception when sqlstate '55000' then
        null;
    end;

    begin
        delete from private.content_promotion_recommendations
        where workspace_id = 'c0000000-0000-4000-8000-000000000001';
        raise exception 'promotion recommendation was mutable';
    exception when sqlstate '55000' then
        null;
    end;
end
$test$;

select public.record_content_signal_ranking_evidence(
    'c0000000-0000-4000-8000-000000000001',
    'squid',
    repeat('d', 64),
    '1.1',
    statement_timestamp(),
    statement_timestamp() - interval '7 days',
    statement_timestamp(),
    'official-x-demand-v1',
    '[]'::jsonb
);

do $test$
declare
    generated_at timestamptz;
    window_start timestamptz;
    window_end timestamptz;
    result jsonb;
    recommendations jsonb;
begin
    select
        ranking.generated_at,
        ranking.window_start,
        ranking.window_end
    into generated_at, window_start, window_end
    from private.content_signal_ranking_evidence as ranking
    where ranking.workspace_id = 'c0000000-0000-4000-8000-000000000001'
      and ranking.client_id = 'squid'
      and ranking.snapshot_hash = repeat('d', 64);

    result := public.record_content_promotion_candidates(
        'c0000000-0000-4000-8000-000000000001',
        'squid',
        repeat('d', 64),
        '1.1',
        generated_at,
        window_start,
        window_end,
        'content-performance-v1',
        jsonb_build_array(jsonb_build_object(
            'candidate_id', repeat('1', 64),
            'channel', 'x',
            'source_url',
                'https://x.com/squidkorea/status/900000000000000111',
            'published_at', generated_at - interval '18 hours',
            'score', 0.875,
            'reach_percentile', 0.9,
            'interaction_percentile', 0.8,
            'community_match_count', 3,
            'cohort_size', 12,
            'observation_age_hours', 18,
            'recommended_formats', jsonb_build_array('article', 'tutorial'),
            'reason_codes', jsonb_build_array(
                'high_reach',
                'high_interaction',
                'community_alignment',
                'tutorial_learning_signal'
            )
        ))
    );
    if result ->> 'recommendation_count' <> '2'
       or result ->> 'reused' <> 'false' then
        raise exception 'fresh promotion evidence was not retained: %', result;
    end if;

    if (
        select count(*)
        from private.content_promotion_recommendations as recommendation
        where recommendation.workspace_id
                = 'c0000000-0000-4000-8000-000000000001'
          and recommendation.client_id = 'squid'
    ) <> 4 then
        raise exception 'immutable recommendation history was overwritten';
    end if;

    recommendations := public.list_content_promotion_recommendations(
        'c0000000-0000-4000-8000-000000000001',
        'c3000000-0000-4000-8000-000000000001',
        'c4000000-0000-4000-8000-000000000001'
    );
    if jsonb_array_length(recommendations -> 'items') <> 2
       or exists (
            select 1
            from jsonb_array_elements(recommendations -> 'items') as item(value)
            where (item.value ->> 'observed_at')::timestamptz
                    is distinct from generated_at
       ) then
        raise exception 'list did not collapse to latest fresh evidence: %',
            recommendations;
    end if;
end
$test$;

do $test$
declare
    generated_at timestamptz;
    window_start timestamptz;
    window_end timestamptz;
    result jsonb;
    recommendations jsonb;
begin
    select
        ranking.generated_at,
        ranking.window_start,
        ranking.window_end
    into generated_at, window_start, window_end
    from private.content_signal_ranking_evidence as ranking
    where ranking.workspace_id = 'c0000000-0000-4000-8000-000000000001'
      and ranking.client_id = 'yellow'
      and ranking.snapshot_hash = repeat('c', 64);

    result := public.record_content_promotion_candidates(
        'c0000000-0000-4000-8000-000000000001',
        'yellow',
        repeat('c', 64),
        '1.1',
        generated_at,
        window_start,
        window_end,
        'content-performance-v1',
        jsonb_build_array(jsonb_build_object(
            'candidate_id', repeat('3', 64),
            'channel', 'x',
            'source_url',
                'https://x.com/yellow__korea/status/900000000000000112',
            'published_at', generated_at - interval '18 hours',
            'score', 0.775,
            'reach_percentile', 0.9,
            'interaction_percentile', 0.8,
            'community_match_count', 0,
            'cohort_size', 12,
            'observation_age_hours', 18,
            'recommended_formats', jsonb_build_array('article'),
            'reason_codes', jsonb_build_array(
                'high_reach',
                'high_interaction'
            )
        ))
    );

    if result ->> 'recommendation_count' <> '1' then
        raise exception 'short-source article recommendation was lost: %', result;
    end if;
    recommendations := public.list_content_promotion_recommendations(
        'c0000000-0000-4000-8000-000000000001',
        'c3000000-0000-4000-8000-000000000002',
        'c4000000-0000-4000-8000-000000000002'
    );
    if jsonb_array_length(recommendations -> 'items') <> 1
       or recommendations -> 'items' -> 0 ->> 'target_kind' <> 'article'
       or recommendations -> 'items' -> 0 ->> 'source_ready' <> 'false' then
        raise exception 'source_ready=false recommendation was not retained: %',
            recommendations;
    end if;
end
$test$;

rollback;
