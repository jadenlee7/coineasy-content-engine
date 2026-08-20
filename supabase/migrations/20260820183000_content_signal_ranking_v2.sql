-- Additive contract repair for the deployed official-x-demand-v2 scorer.
-- EasyFarm schema 1.2 remains the source snapshot contract. The ranking ledger
-- stays on its narrower sanitized schema 1.1 while recording the algorithm
-- version explicitly. Existing v1 evidence and callers remain valid.

begin;

alter table private.content_signal_ranking_evidence
    drop constraint if exists
    content_signal_ranking_evidence_ranking_version_check;

alter table private.content_signal_ranking_evidence
    add constraint content_signal_ranking_evidence_ranking_version_check
    check (
        ranking_version in (
            'official-x-demand-v1',
            'official-x-demand-v2'
        )
    );

create or replace function public.record_content_signal_ranking_evidence(
    target_workspace_id uuid,
    target_client_id text,
    target_snapshot_hash text,
    target_schema_version text,
    target_generated_at timestamptz,
    target_window_start timestamptz,
    target_window_end timestamptz,
    target_ranking_version text,
    target_demand_terms jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    term_item jsonb;
    term_sources jsonb;
    sanitized_envelope jsonb;
    committed private.content_signal_ranking_evidence%rowtype;
    inserted boolean := false;
begin
    if target_client_id is null
       or target_client_id not in (
        'yellow', 'origintrail', 'squid', 'babylon'
    )
       or target_snapshot_hash is null
       or target_snapshot_hash !~ '^[a-f0-9]{64}$'
       or target_schema_version is null
       or target_schema_version not in ('1.0', '1.1')
       or target_ranking_version is null
       or target_ranking_version not in (
            'official-x-demand-v1', 'official-x-demand-v2'
       )
       or (
            target_ranking_version = 'official-x-demand-v2'
            and target_schema_version <> '1.1'
       )
       or target_generated_at is null
       or target_generated_at < statement_timestamp() - interval '24 hours'
       or target_generated_at > statement_timestamp() + interval '5 minutes'
       or target_window_start is null
       or target_window_end is null
       or target_window_end - target_window_start < interval '1 day'
       or target_window_end - target_window_start > interval '31 days'
       or target_window_end < statement_timestamp() - interval '24 hours'
       or target_window_end > statement_timestamp() + interval '5 minutes'
       or jsonb_typeof(target_demand_terms) is distinct from 'array'
       or jsonb_array_length(target_demand_terms) > 20
       or octet_length(target_demand_terms::text) > 16384 then
        raise exception 'content signal ranking evidence is invalid'
            using errcode = '22023';
    end if;

    if not exists (
        select 1
        from public.workspace_clients as client
        where client.workspace_id = target_workspace_id
          and client.client_id = target_client_id
          and client.active is true
    ) then
        raise exception 'content signal workspace client is not active'
            using errcode = '23514';
    end if;

    for term_item in
        select value from jsonb_array_elements(target_demand_terms)
    loop
        if jsonb_typeof(term_item) is distinct from 'object'
           or not (term_item ?& array['term', 'weight', 'sources'])
           or term_item - array['term', 'weight', 'sources'] <> '{}'::jsonb
           or jsonb_typeof(term_item -> 'term') is distinct from 'string'
           or char_length(term_item ->> 'term') not between 2 and 80
           or term_item ->> 'term' is distinct from pg_catalog.regexp_replace(
                btrim(term_item ->> 'term'), '\s+', ' ', 'g'
           )
           or lower(term_item ->> 'term') ~ 'https?://'
           or term_item ->> 'term' like '%@%'
           or term_item ->> 'term' ~* '\m0x[0-9a-f]{40}\M'
           or term_item ->> 'term' ~ '\m[1-9A-HJ-NP-Za-km-z]{32,44}\M'
           or term_item ->> 'term'
                ~* '\m[a-z0-9]{1,20}1[ac-hj-np-z02-9]{20,71}\M'
           or term_item ->> 'term' ~ '^[A-Za-z0-9_-]{48,}$'
           or jsonb_typeof(term_item -> 'weight') is distinct from 'number'
           or (term_item ->> 'weight')::numeric not between 0 and 1
           or jsonb_typeof(term_item -> 'sources') is distinct from 'array'
           or jsonb_array_length(term_item -> 'sources') not between 1 and 3
           or exists (
                select 1
                from jsonb_array_elements(term_item -> 'sources') as source(value)
                where jsonb_typeof(source.value) is distinct from 'string'
           ) then
            raise exception 'content signal demand term is invalid'
                using errcode = '22023';
        end if;
        term_sources := term_item -> 'sources';
        if exists (
            select 1
            from jsonb_array_elements_text(term_sources) as source(value)
            where source.value not in (
                'community', 'telegram_content', 'local_x'
            )
        ) or (
            select count(*) from jsonb_array_elements_text(term_sources)
        ) <> (
            select count(distinct source.value)
            from jsonb_array_elements_text(term_sources) as source(value)
        ) then
            raise exception 'content signal demand sources are invalid'
                using errcode = '22023';
        end if;
    end loop;

    if (
        select count(*)
        from (
            select lower(term.value ->> 'term')
            from jsonb_array_elements(target_demand_terms) as term(value)
            group by lower(term.value ->> 'term')
        ) as unique_terms
    ) <> jsonb_array_length(target_demand_terms) then
        raise exception 'content signal demand terms are duplicated'
            using errcode = '22023';
    end if;

    sanitized_envelope := jsonb_build_object(
        'schema_version', target_schema_version,
        'client_id', target_client_id,
        'generated_at', target_generated_at,
        'window', jsonb_build_object(
            'start', target_window_start,
            'end', target_window_end
        ),
        'ranking_version', target_ranking_version,
        'demand_terms', target_demand_terms,
        'demand_terms_meta', jsonb_build_object(
            'method', 'deterministic-aggregate-public-v1',
            'max_items', 20,
            'stale_sources_excluded', true,
            'factual_evidence', false,
            'translation', false
        )
    );

    insert into private.content_signal_ranking_evidence (
        workspace_id,
        client_id,
        snapshot_hash,
        schema_version,
        generated_at,
        window_start,
        window_end,
        ranking_version,
        sanitized_signal_envelope
    ) values (
        target_workspace_id,
        target_client_id,
        target_snapshot_hash,
        target_schema_version,
        target_generated_at,
        target_window_start,
        target_window_end,
        target_ranking_version,
        sanitized_envelope
    )
    on conflict (
        workspace_id,
        client_id,
        snapshot_hash,
        window_start,
        window_end,
        ranking_version
    ) do nothing
    returning true into inserted;
    inserted := coalesce(inserted, false);

    select evidence.* into committed
    from private.content_signal_ranking_evidence as evidence
    where evidence.workspace_id = target_workspace_id
      and evidence.client_id = target_client_id
      and evidence.snapshot_hash = target_snapshot_hash
      and evidence.window_start = target_window_start
      and evidence.window_end = target_window_end
      and evidence.ranking_version = target_ranking_version;

    if not found
       or committed.schema_version is distinct from target_schema_version
       or committed.generated_at is distinct from target_generated_at
       or committed.sanitized_signal_envelope is distinct from sanitized_envelope
       then
        raise exception 'content signal evidence retry does not match'
            using errcode = '23505';
    end if;

    return jsonb_build_object(
        'recorded', true,
        'snapshot_hash', target_snapshot_hash,
        'reused', not inserted
    );
end;
$$;

create or replace function public.record_content_promotion_candidates(
    target_workspace_id uuid,
    target_client_id text,
    target_snapshot_hash text,
    target_schema_version text,
    target_generated_at timestamptz,
    target_window_start timestamptz,
    target_window_end timestamptz,
    target_policy_version text,
    target_candidates jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    candidate_item jsonb;
    candidate_identifier text;
    candidate_channel text;
    candidate_url text;
    candidate_published_at timestamptz;
    candidate_score numeric;
    reach_score numeric;
    interaction_score numeric;
    match_count integer;
    cohort_count integer;
    reported_age numeric;
    computed_age numeric;
    expected_score numeric;
    candidate_formats jsonb;
    candidate_reasons jsonb;
    expected_reasons jsonb;
    expected_handle text;
    expected_official_source_handle text;
    reason_codes text[];
    matched_publication public.publications%rowtype;
    evidence private.content_performance_evidence%rowtype;
    receipt private.content_promotion_candidate_receipts%rowtype;
    evidence_inserted boolean;
    receipt_inserted boolean;
    recommendation_inserted boolean;
    source_character_count bigint;
    source_ready boolean;
    official_howto_signal boolean;
    tutorial_requested boolean;
    seen_candidate_ids text[] := '{}'::text[];
    seen_candidate_links text[] := '{}'::text[];
    candidate_count integer := 0;
    matched_count integer := 0;
    evidence_count integer := 0;
    recommendation_count integer := 0;
    inserted_any boolean := false;
begin
    if target_client_id is null
       or target_client_id not in (
        'yellow', 'origintrail', 'squid', 'babylon'
    )
       or target_snapshot_hash is null
       or target_snapshot_hash !~ '^[a-f0-9]{64}$'
       or target_schema_version is distinct from '1.1'
       or target_policy_version is distinct from 'content-performance-v1'
       or target_generated_at is null
       or target_generated_at < statement_timestamp() - interval '24 hours'
       or target_generated_at > statement_timestamp() + interval '5 minutes'
       or target_window_start is null
       or target_window_end is null
       or target_window_end - target_window_start < interval '1 day'
       or target_window_end - target_window_start > interval '31 days'
       or target_window_end < statement_timestamp() - interval '24 hours'
       or target_window_end > statement_timestamp() + interval '5 minutes'
       or jsonb_typeof(target_candidates) is distinct from 'array'
       or jsonb_array_length(target_candidates) > 5
       or octet_length(target_candidates::text) > 32768 then
        raise exception 'content promotion candidate envelope is invalid'
            using errcode = '22023';
    end if;

    if not exists (
        select 1
        from public.workspace_clients as client
        where client.workspace_id = target_workspace_id
          and client.client_id = target_client_id
          and client.active is true
    ) or not exists (
        select 1
        from private.content_signal_ranking_evidence as ranking
        where ranking.workspace_id = target_workspace_id
          and ranking.client_id = target_client_id
          and ranking.snapshot_hash = target_snapshot_hash
          and ranking.schema_version = target_schema_version
          and ranking.generated_at = target_generated_at
          and ranking.window_start = target_window_start
          and ranking.window_end = target_window_end
          and ranking.ranking_version in (
                'official-x-demand-v1', 'official-x-demand-v2'
          )
    ) then
        raise exception 'promotion candidates lack committed ranking evidence'
            using errcode = '23514';
    end if;

    candidate_count := jsonb_array_length(target_candidates);
    receipt_inserted := false;
    insert into private.content_promotion_candidate_receipts (
        workspace_id,
        client_id,
        snapshot_hash,
        schema_version,
        policy_version,
        generated_at,
        window_start,
        window_end,
        candidate_count,
        payload_hash
    ) values (
        target_workspace_id,
        target_client_id,
        target_snapshot_hash,
        target_schema_version,
        target_policy_version,
        target_generated_at,
        target_window_start,
        target_window_end,
        candidate_count,
        pg_catalog.md5(target_candidates::text)
    )
    on conflict (workspace_id, client_id, snapshot_hash, policy_version)
    do nothing
    returning * into receipt;
    receipt_inserted := found;

    if not receipt_inserted then
        select committed.* into receipt
        from private.content_promotion_candidate_receipts as committed
        where committed.workspace_id = target_workspace_id
          and committed.client_id = target_client_id
          and committed.snapshot_hash = target_snapshot_hash
          and committed.policy_version = target_policy_version;
        if not found
           or receipt.schema_version is distinct from target_schema_version
           or receipt.generated_at is distinct from target_generated_at
           or receipt.window_start is distinct from target_window_start
           or receipt.window_end is distinct from target_window_end
           or receipt.candidate_count is distinct from candidate_count
           or receipt.payload_hash is distinct from
                pg_catalog.md5(target_candidates::text) then
            raise exception 'promotion candidate retry does not match'
                using errcode = '23505';
        end if;
    else
        inserted_any := true;
    end if;

    expected_official_source_handle := case target_client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
    end;
    for candidate_item in
        select value from jsonb_array_elements(target_candidates)
    loop
        if jsonb_typeof(candidate_item) is distinct from 'object'
           or not (candidate_item ?& array[
                'candidate_id',
                'channel',
                'source_url',
                'published_at',
                'score',
                'reach_percentile',
                'interaction_percentile',
                'community_match_count',
                'cohort_size',
                'observation_age_hours',
                'recommended_formats',
                'reason_codes'
           ])
           or candidate_item - array[
                'candidate_id',
                'channel',
                'source_url',
                'published_at',
                'score',
                'reach_percentile',
                'interaction_percentile',
                'community_match_count',
                'cohort_size',
                'observation_age_hours',
                'recommended_formats',
                'reason_codes'
           ] <> '{}'::jsonb
           or jsonb_typeof(candidate_item -> 'candidate_id')
                is distinct from 'string'
           or (candidate_item ->> 'candidate_id') !~ '^[a-f0-9]{64}$'
           or jsonb_typeof(candidate_item -> 'channel')
                is distinct from 'string'
           or candidate_item ->> 'channel' not in ('x', 'telegram')
           or jsonb_typeof(candidate_item -> 'source_url')
                is distinct from 'string'
           or char_length(candidate_item ->> 'source_url') not between 1 and 160
           or jsonb_typeof(candidate_item -> 'published_at')
                is distinct from 'string'
           or jsonb_typeof(candidate_item -> 'score')
                is distinct from 'number'
           or (candidate_item ->> 'score')::numeric not between 0.7 and 1
           or jsonb_typeof(candidate_item -> 'reach_percentile')
                is distinct from 'number'
           or (candidate_item ->> 'reach_percentile')::numeric
                not between 0 and 1
           or jsonb_typeof(candidate_item -> 'interaction_percentile')
                is distinct from 'number'
           or (candidate_item ->> 'interaction_percentile')::numeric
                not between 0 and 1
           or jsonb_typeof(candidate_item -> 'community_match_count')
                is distinct from 'number'
           or (candidate_item ->> 'community_match_count')::numeric
                not between 0 and 3
           or (candidate_item ->> 'community_match_count')::numeric
                <> trunc(
                    (candidate_item ->> 'community_match_count')::numeric
                )
           or jsonb_typeof(candidate_item -> 'cohort_size')
                is distinct from 'number'
           or (candidate_item ->> 'cohort_size')::numeric < 5
           or (candidate_item ->> 'cohort_size')::numeric
                <> trunc((candidate_item ->> 'cohort_size')::numeric)
           or jsonb_typeof(candidate_item -> 'observation_age_hours')
                is distinct from 'number'
           or (candidate_item ->> 'observation_age_hours')::numeric
                not between 12 and 72
           or jsonb_typeof(candidate_item -> 'recommended_formats')
                is distinct from 'array'
           or candidate_item -> 'recommended_formats' not in (
                '["article"]'::jsonb,
                '["article", "tutorial"]'::jsonb
           )
           or jsonb_typeof(candidate_item -> 'reason_codes')
                is distinct from 'array'
           or jsonb_array_length(candidate_item -> 'reason_codes')
                not between 1 and 4
           or exists (
                select 1
                from jsonb_array_elements(
                    candidate_item -> 'reason_codes'
                ) as reason(value)
                where jsonb_typeof(reason.value) is distinct from 'string'
           ) then
            raise exception 'content promotion candidate is invalid'
                using errcode = '22023';
        end if;

        candidate_identifier := candidate_item ->> 'candidate_id';
        candidate_channel := candidate_item ->> 'channel';
        candidate_url := candidate_item ->> 'source_url';
        expected_handle := case candidate_channel
            when 'x' then case target_client_id
                when 'yellow' then 'yellow__korea'
                when 'origintrail' then 'origin_trail_kr'
                when 'squid' then 'squidkorea'
                when 'babylon' then 'babylonkorean'
            end
            when 'telegram' then case target_client_id
                when 'yellow' then 'yellowkorea_ann'
                when 'origintrail' then 'origintrailkr'
                when 'squid' then 'squid_kor_update'
                when 'babylon' then 'babylonbtc'
            end
        end;

        if candidate_identifier = any(seen_candidate_ids)
           or (candidate_channel || ':' || candidate_url)
                = any(seen_candidate_links)
           or split_part(candidate_url, '/', 4)
                is distinct from expected_handle
           or (
                candidate_channel = 'x'
                and candidate_url
                    !~ '^https://x\.com/[a-z0-9_]{1,15}/status/[1-9][0-9]{0,18}$'
           )
           or (
                candidate_channel = 'telegram'
                and candidate_url
                    !~ '^https://t\.me/[a-z][a-z0-9_]{4,31}/[1-9][0-9]{0,18}$'
           ) then
            raise exception 'content promotion candidate URL is invalid'
                using errcode = '22023';
        end if;

        seen_candidate_ids := array_append(
            seen_candidate_ids,
            candidate_identifier
        );
        seen_candidate_links := array_append(
            seen_candidate_links,
            candidate_channel || ':' || candidate_url
        );

        candidate_published_at := (
            candidate_item ->> 'published_at'
        )::timestamptz;
        candidate_score := (candidate_item ->> 'score')::numeric;
        reach_score := (
            candidate_item ->> 'reach_percentile'
        )::numeric;
        interaction_score := (
            candidate_item ->> 'interaction_percentile'
        )::numeric;
        match_count := (
            candidate_item ->> 'community_match_count'
        )::integer;
        cohort_count := (candidate_item ->> 'cohort_size')::integer;
        reported_age := (
            candidate_item ->> 'observation_age_hours'
        )::numeric;
        computed_age := extract(
            epoch from target_generated_at - candidate_published_at
        ) / 3600;
        expected_score := (
            0.55 * reach_score
            + 0.35 * interaction_score
            + 0.10 * least(match_count, 3)::numeric / 3
        );
        candidate_formats := candidate_item -> 'recommended_formats';
        candidate_reasons := candidate_item -> 'reason_codes';
        tutorial_requested := (
            candidate_formats = '["article", "tutorial"]'::jsonb
        );

        expected_reasons := '[]'::jsonb;
        if reach_score >= 0.7 then
            expected_reasons := expected_reasons
                || jsonb_build_array('high_reach');
        end if;
        if interaction_score >= 0.7 then
            expected_reasons := expected_reasons
                || jsonb_build_array('high_interaction');
        end if;
        if match_count > 0 then
            expected_reasons := expected_reasons
                || jsonb_build_array('community_alignment');
        end if;
        if tutorial_requested then
            expected_reasons := expected_reasons
                || jsonb_build_array('tutorial_learning_signal');
        end if;

        if candidate_published_at < target_window_start
           or candidate_published_at > target_window_end
           or candidate_published_at > target_generated_at
           or computed_age not between 12 and 72
           or abs(computed_age - reported_age) > 0.25
           or abs(candidate_score - expected_score) > 0.001
           or candidate_reasons is distinct from expected_reasons
           or (
                tutorial_requested
                and (
                    target_client_id not in ('yellow', 'squid')
                    or candidate_score < 0.8
                    or match_count = 0
                )
           ) then
            raise exception 'content promotion candidate policy mismatch'
                using errcode = '22023';
        end if;

        select publication.* into matched_publication
        from public.publications as publication
        join public.content_items as publication_item
          on publication_item.id = publication.content_item_id
         and publication_item.workspace_id = publication.workspace_id
         and publication_item.client_id = publication.client_id
         and publication_item.content_kind = 'daily_news'
         and publication_item.current_version_id
                = publication.content_version_id
        where publication.workspace_id = target_workspace_id
          and publication.client_id = target_client_id
          and publication.channel = candidate_channel
          and publication.external_url = candidate_url
          and publication.status = 'published';

        if not found then
            continue;
        end if;
        matched_count := matched_count + 1;

        reason_codes := array(
            select value
            from jsonb_array_elements_text(candidate_reasons) as reason(value)
        );
        evidence_inserted := false;
        insert into private.content_performance_evidence (
            workspace_id,
            client_id,
            publication_id,
            content_item_id,
            content_version_id,
            candidate_id,
            snapshot_hash,
            schema_version,
            policy_version,
            generated_at,
            window_start,
            window_end,
            channel,
            source_url,
            published_at,
            score,
            reach_percentile,
            interaction_percentile,
            community_match_count,
            cohort_size,
            observation_age_hours,
            recommended_formats,
            reason_codes,
            sanitized_candidate,
            payload_hash
        ) values (
            target_workspace_id,
            target_client_id,
            matched_publication.id,
            matched_publication.content_item_id,
            matched_publication.content_version_id,
            candidate_identifier,
            target_snapshot_hash,
            target_schema_version,
            target_policy_version,
            target_generated_at,
            target_window_start,
            target_window_end,
            candidate_channel,
            candidate_url,
            candidate_published_at,
            candidate_score,
            reach_score,
            interaction_score,
            match_count,
            cohort_count,
            reported_age,
            array(
                select value
                from jsonb_array_elements_text(candidate_formats)
                    as format(value)
            ),
            reason_codes,
            candidate_item,
            pg_catalog.md5(candidate_item::text)
        )
        on conflict (
            publication_id,
            candidate_id,
            policy_version,
            snapshot_hash
        ) do nothing
        returning * into evidence;

        evidence_inserted := found;
        if not evidence_inserted then
            select committed.* into evidence
            from private.content_performance_evidence as committed
            where committed.publication_id = matched_publication.id
              and committed.candidate_id = candidate_identifier
              and committed.policy_version = target_policy_version
              and committed.snapshot_hash = target_snapshot_hash;

            if not found
               or evidence.workspace_id <> target_workspace_id
               or evidence.client_id <> target_client_id
               or evidence.content_item_id
                    <> matched_publication.content_item_id
               or evidence.content_version_id
                    <> matched_publication.content_version_id
               or evidence.sanitized_candidate is distinct from candidate_item
               or evidence.payload_hash
                    is distinct from pg_catalog.md5(candidate_item::text) then
                raise exception 'promotion evidence retry does not match'
                    using errcode = '23505';
            end if;
        else
            inserted_any := true;
        end if;
        evidence_count := evidence_count + 1;

        select
            coalesce(sum(char_length(source.body)), 0),
            coalesce(bool_or(
                source.body ~* (
                    '\m(how[[:space:]-]+to|guide|tutorial|'
                    || 'step[[:space:]-]+by[[:space:]-]+step|'
                    || 'getting[[:space:]-]+started|documentation|docs?\.?)\M'
                )
            ), false)
        into source_character_count, official_howto_signal
        from public.content_source_links as link
        join public.source_items as source
          on source.id = link.source_item_id
         and source.workspace_id = link.workspace_id
         and source.client_id = link.client_id
        join public.source_feeds as feed
          on feed.id = source.source_feed_id
         and feed.workspace_id = source.workspace_id
         and feed.client_id = source.client_id
        where link.workspace_id = target_workspace_id
          and link.client_id = target_client_id
          and link.content_item_id = matched_publication.content_item_id
          and feed.provider = 'x'
          and feed.active is true
          and feed.handle = expected_official_source_handle;

        source_ready := source_character_count >= 300;

        if candidate_score >= 0.75
           and candidate_formats @> '["article"]'::jsonb then
            recommendation_inserted := false;
            insert into private.content_promotion_recommendations (
                workspace_id,
                client_id,
                publication_id,
                content_item_id,
                content_version_id,
                evidence_id,
                target_kind,
                score,
                reason_codes,
                policy_version,
                source_url,
                channel,
                source_ready
            ) values (
                target_workspace_id,
                target_client_id,
                matched_publication.id,
                matched_publication.content_item_id,
                matched_publication.content_version_id,
                evidence.id,
                'article',
                candidate_score,
                reason_codes,
                target_policy_version,
                candidate_url,
                candidate_channel,
                source_ready
            )
            on conflict (evidence_id, target_kind)
            do nothing
            returning true into recommendation_inserted;
            if coalesce(recommendation_inserted, false) then
                inserted_any := true;
            end if;
            if exists (
                select 1
                from private.content_promotion_recommendations as recommendation
                where recommendation.evidence_id = evidence.id
                  and recommendation.target_kind = 'article'
            ) then
                recommendation_count := recommendation_count + 1;
            end if;
        end if;

        if tutorial_requested
           and candidate_score >= 0.8
           and target_client_id in ('yellow', 'squid')
           and match_count > 0
           and candidate_reasons @> '["tutorial_learning_signal"]'::jsonb
           and official_howto_signal then
            recommendation_inserted := false;
            insert into private.content_promotion_recommendations (
                workspace_id,
                client_id,
                publication_id,
                content_item_id,
                content_version_id,
                evidence_id,
                target_kind,
                score,
                reason_codes,
                policy_version,
                source_url,
                channel,
                source_ready
            ) values (
                target_workspace_id,
                target_client_id,
                matched_publication.id,
                matched_publication.content_item_id,
                matched_publication.content_version_id,
                evidence.id,
                'tutorial',
                candidate_score,
                reason_codes,
                target_policy_version,
                candidate_url,
                candidate_channel,
                source_ready
            )
            on conflict (evidence_id, target_kind)
            do nothing
            returning true into recommendation_inserted;
            if coalesce(recommendation_inserted, false) then
                inserted_any := true;
            end if;
            if exists (
                select 1
                from private.content_promotion_recommendations as recommendation
                where recommendation.evidence_id = evidence.id
                  and recommendation.target_kind = 'tutorial'
            ) then
                recommendation_count := recommendation_count + 1;
            end if;
        end if;
    end loop;

    return jsonb_build_object(
        'recorded', true,
        'snapshot_hash', target_snapshot_hash,
        'candidate_count', candidate_count,
        'matched_count', matched_count,
        'evidence_count', evidence_count,
        'recommendation_count', recommendation_count,
        'reused', not inserted_any
    );
end;
$$;

revoke all on function public.record_content_signal_ranking_evidence(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) from public, anon, authenticated, service_role;

grant execute on function public.record_content_signal_ranking_evidence(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) to service_role;

revoke all on function public.record_content_promotion_candidates(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) from public, anon, authenticated, service_role;

grant execute on function public.record_content_promotion_candidates(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) to service_role;

notify pgrst, 'reload schema';

commit;
