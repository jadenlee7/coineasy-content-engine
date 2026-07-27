-- Exact-link, recommendation-only promotion evidence for published daily news.
-- The promotion RPCs never publish externally, queue work, or create Figma
-- links. EasyFarm candidates remain ranking evidence, never factual copy.

begin;

alter table private.content_signal_ranking_evidence
    drop constraint if exists content_signal_ranking_evidence_schema_version_check;

alter table private.content_signal_ranking_evidence
    add constraint content_signal_ranking_evidence_schema_version_check
    check (schema_version in ('1.0', '1.1'));

create unique index if not exists publications_published_external_url_unique
    on public.publications (workspace_id, client_id, channel, external_url)
    where status = 'published' and external_url is not null;

create unique index if not exists publications_published_version_channel_unique
    on public.publications (workspace_id, content_version_id, channel)
    where status = 'published';

create table private.content_performance_evidence (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null check (
        client_id in ('yellow', 'origintrail', 'squid', 'babylon')
    ),
    publication_id uuid not null
        references public.publications(id) on delete restrict,
    content_item_id uuid not null,
    content_version_id uuid not null,
    candidate_id text not null check (
        candidate_id ~ '^[a-f0-9]{64}$'
    ),
    snapshot_hash text not null check (
        snapshot_hash ~ '^[a-f0-9]{64}$'
    ),
    schema_version text not null check (schema_version = '1.1'),
    policy_version text not null check (
        policy_version = 'content-performance-v1'
    ),
    generated_at timestamptz not null,
    window_start timestamptz not null,
    window_end timestamptz not null,
    channel text not null check (channel in ('x', 'telegram')),
    source_url text not null,
    published_at timestamptz not null,
    score numeric not null check (score between 0.7 and 1),
    reach_percentile numeric not null check (
        reach_percentile between 0 and 1
    ),
    interaction_percentile numeric not null check (
        interaction_percentile between 0 and 1
    ),
    community_match_count integer not null check (
        community_match_count between 0 and 3
    ),
    cohort_size integer not null check (cohort_size >= 5),
    observation_age_hours numeric not null check (
        observation_age_hours between 12 and 72
    ),
    recommended_formats text[] not null check (
        recommended_formats = array['article']::text[]
        or recommended_formats = array['article', 'tutorial']::text[]
    ),
    reason_codes text[] not null check (
        cardinality(reason_codes) between 1 and 4
        and reason_codes <@ array[
            'high_reach',
            'high_interaction',
            'community_alignment',
            'tutorial_learning_signal'
        ]::text[]
    ),
    sanitized_candidate jsonb not null check (
        jsonb_typeof(sanitized_candidate) = 'object'
        and octet_length(sanitized_candidate::text) <= 8192
    ),
    payload_hash text not null check (
        payload_hash ~ '^[a-f0-9]{32}$'
    ),
    created_at timestamptz not null default statement_timestamp(),
    unique (publication_id, candidate_id, policy_version, snapshot_hash),
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id)
        on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete restrict,
    check (window_end > window_start)
);

create index content_performance_evidence_publication_idx
    on private.content_performance_evidence (
        workspace_id,
        content_item_id,
        publication_id,
        generated_at desc
    );

create table private.content_promotion_candidate_receipts (
    workspace_id uuid not null,
    client_id text not null check (
        client_id in ('yellow', 'origintrail', 'squid', 'babylon')
    ),
    snapshot_hash text not null check (
        snapshot_hash ~ '^[a-f0-9]{64}$'
    ),
    schema_version text not null check (schema_version = '1.1'),
    policy_version text not null check (
        policy_version = 'content-performance-v1'
    ),
    generated_at timestamptz not null,
    window_start timestamptz not null,
    window_end timestamptz not null,
    candidate_count integer not null check (
        candidate_count between 0 and 5
    ),
    payload_hash text not null check (
        payload_hash ~ '^[a-f0-9]{32}$'
    ),
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, client_id, snapshot_hash, policy_version),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    check (window_end > window_start)
);

create table private.content_promotion_recommendations (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    client_id text not null check (
        client_id in ('yellow', 'origintrail', 'squid', 'babylon')
    ),
    publication_id uuid not null
        references public.publications(id) on delete restrict,
    content_item_id uuid not null,
    content_version_id uuid not null,
    evidence_id uuid not null
        references private.content_performance_evidence(id) on delete restrict,
    target_kind text not null check (
        target_kind in ('article', 'tutorial')
    ),
    score numeric not null check (score between 0.75 and 1),
    reason_codes text[] not null check (
        cardinality(reason_codes) between 1 and 4
    ),
    policy_version text not null check (
        policy_version = 'content-performance-v1'
    ),
    source_url text not null,
    channel text not null check (channel in ('x', 'telegram')),
    source_ready boolean not null,
    created_at timestamptz not null default statement_timestamp(),
    unique (evidence_id, target_kind),
    foreign key (workspace_id, client_id, content_item_id)
        references public.content_items(workspace_id, client_id, id)
        on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete restrict
);

create index content_promotion_recommendations_item_idx
    on private.content_promotion_recommendations (
        workspace_id,
        content_item_id,
        created_at desc
    );

revoke all on table
    private.content_performance_evidence,
    private.content_promotion_candidate_receipts,
    private.content_promotion_recommendations
from public, anon, authenticated, service_role;

create or replace function private.reject_content_performance_mutation()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    raise exception 'content performance evidence is immutable'
        using errcode = '55000';
end;
$$;

create trigger content_performance_evidence_immutable
before update or delete on private.content_performance_evidence
for each row execute function private.reject_content_performance_mutation();

create trigger content_promotion_candidate_receipts_immutable
before update or delete on private.content_promotion_candidate_receipts
for each row execute function private.reject_content_performance_mutation();

create trigger content_promotion_recommendations_immutable
before update or delete on private.content_promotion_recommendations
for each row execute function private.reject_content_performance_mutation();

revoke all on function private.reject_content_performance_mutation()
from public, anon, authenticated, service_role;

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
       or target_ranking_version is distinct from 'official-x-demand-v1'
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

create or replace function public.record_manual_publication_observation(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_channel text,
    target_external_url text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    existing public.publications%rowtype;
    publication_id uuid;
    canonical_url text;
    external_id text;
    observed_handle text;
    expected_handle text;
    url_parts text[];
    reused boolean := false;
begin
    if target_workspace_id is null
       or target_content_item_id is null
       or target_content_version_id is null
       or target_channel not in ('x', 'telegram')
       or target_external_url is null
       or char_length(target_external_url) > 200 then
        raise exception 'manual publication observation is invalid'
            using errcode = '22023';
    end if;

    if target_channel = 'x' then
        url_parts := pg_catalog.regexp_match(
            target_external_url,
            '^https://x\.com/([A-Za-z0-9_]{1,15})/status/([1-9][0-9]{0,18})$'
        );
        if url_parts is null then
            raise exception 'manual X publication URL is not canonical'
                using errcode = '22023';
        end if;
        observed_handle := lower(url_parts[1]);
        canonical_url := 'https://x.com/' || lower(url_parts[1])
            || '/status/' || url_parts[2];
        external_id := url_parts[2];
    else
        url_parts := pg_catalog.regexp_match(
            target_external_url,
            '^https://t\.me/([A-Za-z][A-Za-z0-9_]{4,31})/([1-9][0-9]{0,18})$'
        );
        if url_parts is null then
            raise exception 'manual Telegram publication URL is not public'
                using errcode = '22023';
        end if;
        observed_handle := lower(url_parts[1]);
        canonical_url := 'https://t.me/' || lower(url_parts[1])
            || '/' || url_parts[2];
        external_id := url_parts[2];
    end if;

    select content.* into item
    from public.content_items as content
    where content.id = target_content_item_id
      and content.workspace_id = target_workspace_id
    for update;

    if not found
       or item.content_kind <> 'daily_news'
       or item.current_version_id is distinct from target_content_version_id then
        raise exception 'manual observation requires current daily news'
            using errcode = '23514';
    end if;

    select content_version.* into version
    from public.content_versions as content_version
    where content_version.id = target_content_version_id
      and content_version.workspace_id = item.workspace_id
      and content_version.content_item_id = item.id
    for key share;

    if not found
       or version.generation_meta -> 'mock_mode'
            is distinct from 'false'::jsonb then
        raise exception 'manual observation requires a production version'
            using errcode = '23514';
    end if;

    expected_handle := case target_channel
        when 'x' then case item.client_id
            when 'yellow' then 'yellow__korea'
            when 'origintrail' then 'origin_trail_kr'
            when 'squid' then 'squidkorea'
            when 'babylon' then 'babylonkorean'
        end
        when 'telegram' then case item.client_id
            when 'yellow' then 'yellowkorea_ann'
            when 'origintrail' then 'origintrailkr'
            when 'squid' then 'squid_kor_update'
            when 'babylon' then 'babylonbtc'
        end
    end;
    if observed_handle is distinct from expected_handle then
        raise exception 'publication URL is outside the client local channel'
            using errcode = '22023';
    end if;

    -- Both this RPC and request_content_publication lock the content item first.
    -- That shared lock order makes the two operation orderings deterministic:
    -- an already active publish wins, while an already observed manual publish
    -- is protected by the reciprocal guard in request_content_publication.
    if exists (
        select 1
        from public.publications as publication
        where publication.workspace_id = item.workspace_id
          and publication.content_item_id = item.id
          and publication.content_version_id = version.id
          and publication.channel = target_channel
          and publication.status in ('queued', 'publishing')
    ) or exists (
        select 1
        from public.jobs as job
        where job.workspace_id = item.workspace_id
          and job.content_item_id = item.id
          and job.job_kind = 'publish'
          and job.status in ('queued', 'running', 'retrying')
          and job.input ->> 'content_version_id' = version.id::text
          and job.input ->> 'channel' = target_channel
    ) then
        raise exception
            'manual observation conflicts with an active publication request'
            -- PostgREST maps 23505 to HTTP 409, allowing the team UI to show a
            -- stable conflict instead of presenting a retryable gateway error.
            using errcode = '23505';
    end if;

    select publication.* into existing
    from public.publications as publication
    where publication.workspace_id = item.workspace_id
      and publication.client_id = item.client_id
      and publication.channel = target_channel
      and publication.status = 'published'
      and publication.external_url = canonical_url
    for update;

    if found then
        if existing.content_item_id <> item.id
           or existing.content_version_id <> version.id then
            raise exception 'publication URL is already mapped elsewhere'
                using errcode = '23505';
        end if;
        publication_id := existing.id;
        reused := true;
    else
        select publication.* into existing
        from public.publications as publication
        where publication.workspace_id = item.workspace_id
          and publication.content_version_id = version.id
          and publication.channel = target_channel
          and publication.status = 'published'
        for update;

        if found then
            if existing.external_url is distinct from canonical_url then
                raise exception 'published version already has another URL'
                    using errcode = '23505';
            end if;
            publication_id := existing.id;
            reused := true;
        else
            insert into public.publications (
                workspace_id,
                client_id,
                content_item_id,
                content_version_id,
                channel,
                status,
                published_at,
                external_id,
                external_url,
                request_payload,
                response_payload
            ) values (
                item.workspace_id,
                item.client_id,
                item.id,
                version.id,
                target_channel,
                'published',
                statement_timestamp(),
                external_id,
                canonical_url,
                jsonb_build_object(
                    'observation', 'manual_existing_publication',
                    'external_publish_performed', false
                ),
                jsonb_build_object(
                    'observed', true,
                    'external_publish_performed', false
                )
            )
            returning id into publication_id;
        end if;
    end if;

    if not exists (
        select 1
        from public.event_log as event
        where event.workspace_id = item.workspace_id
          and event.entity_type = 'publication'
          and event.entity_id = publication_id
          and event.event_type = 'manual_publication_observed'
    ) then
        insert into public.event_log (
            workspace_id,
            entity_type,
            entity_id,
            event_type,
            data
        ) values (
            item.workspace_id,
            'publication',
            publication_id,
            'manual_publication_observed',
            jsonb_build_object(
                'content_item_id', item.id,
                'content_version_id', version.id,
                'channel', target_channel,
                'external_url', canonical_url,
                'reused', reused,
                'external_publish_performed', false
            )
        );
    end if;

    return jsonb_build_object(
        'publication_id', publication_id,
        'content_version_id', version.id,
        'channel', target_channel,
        'external_url', canonical_url,
        'reused', reused
    );
end;
$$;

create or replace function public.request_content_publication(
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_channel text,
    target_scheduled_for timestamptz default null,
    request_idempotency_key text default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := (select auth.uid());
    item public.content_items%rowtype;
    target_version public.content_versions%rowtype;
    effective_key text;
    existing_publication_id uuid;
    publication_id uuid;
begin
    if actor_id is null then
        raise exception 'authentication required' using errcode = '42501';
    end if;
    if target_channel not in ('telegram', 'x', 'web') then
        raise exception 'invalid publication channel' using errcode = '22023';
    end if;
    if request_idempotency_key is not null
       and char_length(request_idempotency_key) not between 8 and 200 then
        raise exception 'idempotency key must be 8 to 200 characters'
            using errcode = '22023';
    end if;

    select * into item
    from public.content_items
    where id = target_content_item_id
    for update;
    if not found then
        raise exception 'content item not found' using errcode = 'P0002';
    end if;
    if not exists (
        select 1
        from public.workspace_members
        where workspace_id = item.workspace_id
          and user_id = actor_id
          and status = 'active'
          and role in ('owner', 'admin', 'editor')
    ) then
        raise exception 'workspace editor role required' using errcode = '42501';
    end if;
    select * into target_version
    from public.content_versions
    where workspace_id = item.workspace_id
      and content_item_id = item.id
      and id = target_content_version_id;
    if found
       and target_version.generation_meta @> '{"mock_mode":true}'::jsonb then
        raise exception 'mock/test content cannot be published'
            using errcode = '23514';
    end if;
    effective_key := coalesce(
        request_idempotency_key,
        'publish:' || item.id::text || ':' || target_content_version_id::text
            || ':' || target_channel || ':'
            || coalesce(target_scheduled_for::text, 'now')
    );
    select (input ->> 'publication_id')::uuid into existing_publication_id
    from public.jobs
    where workspace_id = item.workspace_id
      and content_item_id = item.id
      and job_kind = 'publish'
      and input ->> 'content_version_id' = target_content_version_id::text
      and input ->> 'channel' = target_channel
      and idempotency_key = effective_key;
    if found then
        return existing_publication_id;
    end if;

    -- The item lock serializes this check with manual observation. A published
    -- row carrying the observation event represents an external action that
    -- already happened, so no second queue may be created for that version and
    -- channel. An idempotent retry of an older real job still returns above.
    if exists (
        select 1
        from public.publications as publication
        where publication.workspace_id = item.workspace_id
          and publication.content_item_id = item.id
          and publication.content_version_id = target_content_version_id
          and publication.channel = target_channel
          and publication.status = 'published'
          and exists (
              select 1
              from public.event_log as event
              where event.workspace_id = publication.workspace_id
                and event.entity_type = 'publication'
                and event.entity_id = publication.id
                and event.event_type = 'manual_publication_observed'
          )
    ) then
        raise exception
            'publication was already observed on the external channel'
            using errcode = '23505';
    end if;

    -- Validate time only after the idempotency lookup. A legitimate retry must
    -- keep returning its original publication after its scheduled time passes.
    if target_scheduled_for is not null and target_scheduled_for < now() then
        raise exception 'scheduled time must be in the future' using errcode = '22023';
    end if;
    if item.status not in ('approved', 'scheduled')
       or item.current_version_id is distinct from target_content_version_id
       or not exists (
           select 1
           from public.content_versions
           where workspace_id = item.workspace_id
             and content_item_id = item.id
             and id = target_content_version_id
       ) then
        raise exception 'only the current approved version can be published'
            using errcode = '23514';
    end if;

    insert into public.publications (
        workspace_id,
        client_id,
        content_item_id,
        content_version_id,
        channel,
        scheduled_for,
        requested_by
    ) values (
        item.workspace_id,
        item.client_id,
        item.id,
        target_content_version_id,
        target_channel,
        target_scheduled_for,
        actor_id
    ) returning id into publication_id;

    insert into public.jobs (
        workspace_id,
        client_id,
        content_item_id,
        job_kind,
        input,
        idempotency_key,
        available_at,
        created_by
    ) values (
        item.workspace_id,
        item.client_id,
        item.id,
        'publish',
        jsonb_build_object(
            'publication_id', publication_id,
            'content_version_id', target_content_version_id,
            'channel', target_channel
        ),
        effective_key,
        coalesce(target_scheduled_for, now()),
        actor_id
    );

    update public.content_items
    set status = case
            when item.status = 'scheduled' or target_scheduled_for is not null
                then 'scheduled'
            else 'approved'
        end,
        scheduled_for = case
            when target_scheduled_for is null then item.scheduled_for
            when item.scheduled_for is null then target_scheduled_for
            else least(item.scheduled_for, target_scheduled_for)
        end
    where id = item.id;

    insert into public.event_log (
        workspace_id, actor_id, entity_type, entity_id, event_type, data
    ) values (
        item.workspace_id,
        actor_id,
        'content_item',
        item.id,
        'publication_requested',
        jsonb_build_object(
            'publication_id', publication_id,
            'content_version_id', target_content_version_id,
            'channel', target_channel
        )
    );
    return publication_id;
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
          and ranking.ranking_version = 'official-x-demand-v1'
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

create or replace function public.list_content_promotion_recommendations(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    result jsonb;
    publication_result jsonb;
begin
    if target_workspace_id is null
       or target_content_item_id is null
       or target_content_version_id is null
       or not exists (
            select 1
            from public.content_items as item
            where item.workspace_id = target_workspace_id
              and item.id = target_content_item_id
              and item.current_version_id = target_content_version_id
       ) then
        raise exception 'promotion recommendation item is invalid'
            using errcode = '22023';
    end if;

    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'recommendation_id', recommendation.id,
                'content_version_id', recommendation.content_version_id,
                'target_kind', recommendation.target_kind,
                'score', recommendation.score,
                'reason_codes', to_jsonb(recommendation.reason_codes),
                'policy_version', recommendation.policy_version,
                'source_url', recommendation.source_url,
                'channel', recommendation.channel,
                'observed_at', recommendation.observed_at,
                'source_ready', recommendation.source_ready
            )
            order by
                recommendation.score desc,
                recommendation.observed_at desc
        ),
        '[]'::jsonb
    )
    into result
    from (
        select
            candidate_recommendation.*,
            evidence.generated_at as observed_at,
            row_number() over (
                partition by
                    candidate_recommendation.publication_id,
                    candidate_recommendation.target_kind,
                    candidate_recommendation.policy_version
                order by
                    evidence.generated_at desc,
                    candidate_recommendation.created_at desc,
                    candidate_recommendation.id desc
            ) as recency
        from private.content_promotion_recommendations
            as candidate_recommendation
        join private.content_performance_evidence as evidence
          on evidence.id = candidate_recommendation.evidence_id
        where candidate_recommendation.workspace_id = target_workspace_id
          and candidate_recommendation.content_item_id
                = target_content_item_id
          and candidate_recommendation.content_version_id
                = target_content_version_id
          and evidence.generated_at
                >= statement_timestamp() - interval '24 hours'
          and evidence.generated_at
                <= statement_timestamp() + interval '5 minutes'
    ) as recommendation
    where recommendation.recency = 1;

    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'publication_id', publication.id,
                'content_version_id', publication.content_version_id,
                'channel', publication.channel,
                'external_url', publication.external_url
            )
            order by publication.channel
        ),
        '[]'::jsonb
    )
    into publication_result
    from public.publications as publication
    where publication.workspace_id = target_workspace_id
      and publication.content_item_id = target_content_item_id
      and publication.content_version_id = target_content_version_id
      and publication.status = 'published'
      and exists (
            select 1
            from public.event_log as event
            where event.workspace_id = publication.workspace_id
              and event.entity_type = 'publication'
              and event.entity_id = publication.id
              and event.event_type = 'manual_publication_observed'
      );

    return jsonb_build_object(
        'items', result,
        'publications', publication_result
    );
end;
$$;

revoke all on function public.record_content_signal_ranking_evidence(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) from public, anon, authenticated, service_role;

revoke all on function public.record_manual_publication_observation(
    uuid, uuid, uuid, text, text
) from public, anon, authenticated, service_role;

revoke all on function public.record_content_promotion_candidates(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) from public, anon, authenticated, service_role;

revoke all on function public.list_content_promotion_recommendations(
    uuid, uuid, uuid
) from public, anon, authenticated, service_role;

-- CREATE OR REPLACE retains the prior ACL, and these statements restate the
-- Content Studio contract explicitly: browser editors may request publishing,
-- while anon/public callers cannot call this user-authorized RPC.
revoke all on function public.request_content_publication(
    uuid, uuid, text, timestamptz, text
) from public, anon, authenticated;

grant execute on function public.request_content_publication(
    uuid, uuid, text, timestamptz, text
) to authenticated;

grant execute on function public.record_content_signal_ranking_evidence(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) to service_role;

grant execute on function public.record_manual_publication_observation(
    uuid, uuid, uuid, text, text
) to service_role;

grant execute on function public.record_content_promotion_candidates(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) to service_role;

grant execute on function public.list_content_promotion_recommendations(
    uuid, uuid, uuid
) to service_role;

notify pgrst, 'reload schema';

commit;
