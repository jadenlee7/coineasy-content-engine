-- Immutable, aggregate-only evidence for EasyFarm-informed official-X ranking.
-- The worker may use a signal snapshot only after this RPC commits it.

begin;

create table private.content_signal_ranking_evidence (
    workspace_id uuid not null,
    client_id text not null,
    snapshot_hash text not null check (
        snapshot_hash ~ '^[a-f0-9]{64}$'
    ),
    schema_version text not null check (schema_version = '1.0'),
    generated_at timestamptz not null,
    window_start timestamptz not null,
    window_end timestamptz not null,
    ranking_version text not null check (
        ranking_version = 'official-x-demand-v1'
    ),
    sanitized_signal_envelope jsonb not null,
    created_at timestamptz not null default statement_timestamp(),
    primary key (
        workspace_id,
        client_id,
        snapshot_hash,
        window_start,
        window_end,
        ranking_version
    ),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    check (window_end > window_start),
    check (
        octet_length(sanitized_signal_envelope::text) <= 32768
    )
);

create index content_signal_ranking_evidence_created_idx
    on private.content_signal_ranking_evidence (
        workspace_id,
        client_id,
        created_at desc
    );

revoke all on table private.content_signal_ranking_evidence
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
    if target_client_id not in (
        'yellow', 'origintrail', 'squid', 'babylon'
    )
       or target_snapshot_hash is null
       or target_snapshot_hash !~ '^[a-f0-9]{64}$'
       or target_schema_version is distinct from '1.0'
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

revoke all on function public.record_content_signal_ranking_evidence(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) from public, anon, authenticated, service_role;

grant execute on function public.record_content_signal_ranking_evidence(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) to service_role;

notify pgrst, 'reload schema';

commit;
