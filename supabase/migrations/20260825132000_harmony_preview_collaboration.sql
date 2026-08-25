-- Disposable-Preview-only Harmony collaboration ledger.
--
-- This migration deliberately exposes no Production adapter, provider call,
-- message delivery, approval mutation, or publication routine.  Client bot
-- claims enter through a client-scoped JWT gate; the database constructs the
-- append-only connector attestation receipt from verified claims.  The only
-- materializer is a synthetic Squid vertical slice ending in a private,
-- pending operator inbox item and a zero-cost recap.

begin;

-- Empty by default.  A disposable Preview branch may be activated only by a
-- DB-owner seed of its exact Supabase branch ref; caller JSON cannot select or
-- create this trust anchor.
create table private.harmony_preview_environment_fence (
    branch_ref text primary key check (branch_ref ~ '^[a-z0-9]{20}$'),
    active boolean not null check (active),
    expires_at timestamptz not null check (
        expires_at > statement_timestamp()
        and expires_at <= statement_timestamp() + interval '31 days'
    ),
    created_at timestamptz not null default statement_timestamp()
);
alter table private.harmony_preview_environment_fence enable row level security;
alter table private.harmony_preview_environment_fence force row level security;
revoke all on table private.harmony_preview_environment_fence
from public, anon, authenticated, service_role;
create trigger harmony_preview_environment_fence_immutable
before update or delete on private.harmony_preview_environment_fence
for each row execute function private.agent_immutable_row();

create or replace function private.harmony_preview_scope_matches(
    target_workspace_id uuid,
    target_client_id text,
    target_roles text[]
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    issued_epoch bigint;
    expires_epoch bigint;
begin
    begin
        claims := coalesce(
            nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb,
            '{}'::jsonb
        );
        issued_epoch := (claims ->> 'iat')::bigint;
        expires_epoch := (claims ->> 'exp')::bigint;
    exception when others then
        return false;
    end;
    return coalesce(claims ->> 'role', '') = any(target_roles)
       and coalesce(claims ->> 'workspace_id', '') = target_workspace_id::text
       and coalesce(claims ->> 'client_id', '') = target_client_id
       and coalesce(claims ->> 'environment', '') = 'preview'
       and coalesce(claims ->> 'ref', '') ~ '^[a-z0-9]{20}$'
       and exists (
            select 1
            from private.harmony_preview_environment_fence fence
            where fence.branch_ref = claims ->> 'ref'
              and fence.active
              and fence.expires_at > statement_timestamp()
       )
       and coalesce(claims ->> 'iss', '') = 'supabase'
       and coalesce(claims ->> 'aud', '') = 'authenticated'
       and claims -> 'automatic_publication' is not distinct from 'false'::jsonb
       and claims -> 'max_cost_microusd' is not distinct from '0'::jsonb
       and claims -> 'max_external_actions' is not distinct from '0'::jsonb
       and issued_epoch <= extract(epoch from statement_timestamp())
            + 60
       and expires_epoch > extract(epoch from statement_timestamp())
       and expires_epoch - issued_epoch between 1 and 2678400;
end;
$$;

create or replace function private.harmony_preview_lane_visible(
    target_workspace_id uuid,
    target_client_id text,
    target_lane text,
    target_roles text[]
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    expected_capability text;
begin
    begin
        claims := coalesce(
            nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb,
            '{}'::jsonb
        );
    exception when others then
        return false;
    end;
    if not private.harmony_preview_scope_matches(
        target_workspace_id, target_client_id, target_roles
    ) then
        return false;
    end if;
    if claims ->> 'role' <> 'coineasy_harmony_connector' then
        return true;
    end if;
    expected_capability := case target_lane
        when 'quiz_bot' then 'harmony_submit_quiz_bot'
        when 'community_ops' then 'harmony_submit_community_ops'
        when 'content_source' then 'harmony_submit_content_source'
        when 'recap' then 'harmony_submit_recap'
        else null
    end;
    return expected_capability is not null
       and claims ->> 'capability' = expected_capability;
end;
$$;

create or replace function private.harmony_preview_connector_claims_match(
    target_workspace_id uuid,
    target_client_id text,
    target_signal jsonb
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    expected_capability text;
begin
    begin
        claims := coalesce(
            nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb,
            '{}'::jsonb
        );
    exception when others then
        return false;
    end;
    expected_capability := case target_signal ->> 'lane'
        when 'quiz_bot' then 'harmony_submit_quiz_bot'
        when 'community_ops' then 'harmony_submit_community_ops'
        when 'content_source' then 'harmony_submit_content_source'
        when 'recap' then 'harmony_submit_recap'
        else null
    end;
    return private.harmony_preview_scope_matches(
        target_workspace_id,
        target_client_id,
        array['coineasy_harmony_connector']::text[]
    )
       and expected_capability is not null
       and claims ->> 'capability' = expected_capability
       and coalesce(claims ->> 'connector_id', '')
            ~ '^[a-z][a-z0-9_:-]{2,63}$'
       and coalesce(claims ->> 'producer_principal_id', '')
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       and claims ->> 'producer_principal_id' = target_signal ->> 'producer_principal_id'
       and claims ->> 'sub' = target_signal ->> 'producer_principal_id'
       and coalesce(claims ->> 'release_sha', '') ~ '^[a-f0-9]{40}$'
       and claims ->> 'release_sha' = target_signal ->> 'producer_release_sha'
       and coalesce(claims ->> 'config_sha256', '') ~ '^[a-f0-9]{64}$'
       and claims ->> 'config_sha256' = target_signal ->> 'config_sha256'
       and coalesce(claims ->> 'jti', '')
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$';
end;
$$;

create or replace function private.harmony_preview_connector_verification_reference()
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb := nullif(
        pg_catalog.current_setting('request.jwt.claims', true), ''
    )::jsonb;
begin
    return private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'aud', claims ->> 'aud',
        'capability', claims ->> 'capability',
        'client_id', claims ->> 'client_id',
        'config_sha256', claims ->> 'config_sha256',
        'environment', claims ->> 'environment',
        'exp', (claims ->> 'exp')::bigint,
        'iat', (claims ->> 'iat')::bigint,
        'iss', claims ->> 'iss',
        'jti', claims ->> 'jti',
        'producer_principal_id', claims ->> 'producer_principal_id',
        'ref', claims ->> 'ref',
        'release_sha', claims ->> 'release_sha',
        'role', claims ->> 'role',
        'workspace_id', claims ->> 'workspace_id'
    ));
end;
$$;

create or replace function private.harmony_preview_signal_valid(target jsonb)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    observed_value timestamptz;
    expires_value timestamptz;
    period_start_value timestamptz;
    period_end_value timestamptz;
    allowed_keys text[];
    topic_values text[];
    sorted_topics text[];
begin
    if target is null
       or pg_catalog.jsonb_typeof(target) <> 'object'
       or pg_catalog.octet_length(target::text) > 65536
       or not target ?& array[
            'schema_version', 'signal_id', 'workspace_id', 'client_id',
            'signal_kind', 'lane', 'source_event_id',
            'producer_principal_id', 'producer_release_sha', 'config_sha256',
            'upstream_receipt_sha256', 'observed_at', 'expires_at',
            'evidence_sha256', 'topic_codes', 'content_factual_authority',
            'raw_messages_included', 'personal_data_included',
            'instructions_allowed', 'advisory_only', 'max_cost_microusd',
            'max_external_actions', 'automatic_publication', 'payload_sha256'
       ]
       or target ->> 'schema_version' <> 'agent-harmony-signal@1'
       or target ->> 'client_id' not in ('babylon', 'origintrail', 'squid', 'yellow')
       or coalesce(target ->> 'signal_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or coalesce(target ->> 'workspace_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or coalesce(target ->> 'source_event_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or coalesce(target ->> 'producer_principal_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or coalesce(target ->> 'producer_release_sha', '') !~ '^[a-f0-9]{40}$'
       or coalesce(target ->> 'config_sha256', '') !~ '^[a-f0-9]{64}$'
       or coalesce(target ->> 'upstream_receipt_sha256', '') !~ '^[a-f0-9]{64}$'
       or coalesce(target ->> 'evidence_sha256', '') !~ '^[a-f0-9]{64}$'
       or coalesce(target ->> 'payload_sha256', '') !~ '^[a-f0-9]{64}$'
       or target -> 'raw_messages_included' is distinct from 'false'::jsonb
       or target -> 'personal_data_included' is distinct from 'false'::jsonb
       or target -> 'instructions_allowed' is distinct from 'false'::jsonb
       or target -> 'advisory_only' is distinct from 'true'::jsonb
       or target -> 'max_cost_microusd' is distinct from '0'::jsonb
       or target -> 'max_external_actions' is distinct from '0'::jsonb
       or target -> 'automatic_publication' is distinct from 'false'::jsonb
       or private.agent_json_sha256(target - 'payload_sha256')
            <> target ->> 'payload_sha256'
       or pg_catalog.jsonb_typeof(target -> 'topic_codes') <> 'array'
       or pg_catalog.jsonb_array_length(target -> 'topic_codes') not between 1 and 12
    then
        return false;
    end if;

    begin
        observed_value := (target ->> 'observed_at')::timestamptz;
        expires_value := (target ->> 'expires_at')::timestamptz;
    exception when others then
        return false;
    end;
    if target ->> 'observed_at' !~ 'Z$'
       or target ->> 'expires_at' !~ 'Z$'
       or expires_value <= observed_value
       or expires_value - observed_value > interval '31 days' then
        return false;
    end if;

    select pg_catalog.array_agg(topic.value order by topic.ordinality),
           pg_catalog.array_agg(topic.value order by topic.value)
    into topic_values, sorted_topics
    from pg_catalog.jsonb_array_elements_text(target -> 'topic_codes')
        with ordinality as topic(value, ordinality);
    if topic_values is distinct from sorted_topics
       or pg_catalog.cardinality(topic_values)
            <> (select pg_catalog.count(distinct value) from unnest(topic_values) as item(value))
       or exists (
            select 1 from unnest(topic_values) as item(value)
            where item.value !~ '^[a-z][a-z0-9_:-]{1,30}$'
               or not private.agent_safe_text(item.value, 2, 31, true)
               or not (item.value = any(array[
                    'community_faq', 'integration_update', 'launch_status',
                    'market_context', 'official_update', 'performance_gap',
                    'product_mechanics', 'routing_basics', 'security_safety',
                    'staking_basics', 'technical_architecture',
                    'tutorial_demand', 'user_guide', 'wallet_safety'
               ]::text[]))
               or item.value ~ '(^|[_:-])(credential|execute|ignore|instruction|prompt|publish|secret|send|tool_call)($|[_:-])'
       ) then
        return false;
    end if;

    allowed_keys := array[
        'schema_version', 'signal_id', 'workspace_id', 'client_id',
        'signal_kind', 'lane', 'source_event_id', 'producer_principal_id',
        'producer_release_sha', 'config_sha256', 'upstream_receipt_sha256',
        'observed_at', 'expires_at', 'evidence_sha256', 'topic_codes',
        'content_factual_authority', 'raw_messages_included',
        'personal_data_included', 'instructions_allowed', 'advisory_only',
        'max_cost_microusd', 'max_external_actions', 'automatic_publication',
        'payload_sha256'
    ];

    case target ->> 'signal_kind'
        when 'quiz_learning' then
            allowed_keys := allowed_keys || array[
                'data_classification', 'attempts', 'participants',
                'accuracy_basis_points', 'tutorial_priority_basis_points'
            ];
            if target ->> 'lane' <> 'quiz_bot'
               or target ->> 'data_classification' <> 'aggregate_anonymous'
               or target -> 'content_factual_authority' is distinct from 'false'::jsonb
               or coalesce(target ->> 'attempts', '') !~ '^[0-9]+$'
               or (target ->> 'attempts')::bigint < 20
               or coalesce(target ->> 'participants', '') !~ '^[0-9]+$'
               or (target ->> 'participants')::bigint < 5
               or (target ->> 'participants')::bigint > (target ->> 'attempts')::bigint
               or coalesce(target ->> 'accuracy_basis_points', '') !~ '^[0-9]+$'
               or (target ->> 'accuracy_basis_points')::integer > 10000
               or coalesce(target ->> 'tutorial_priority_basis_points', '') !~ '^[0-9]+$'
               or (target ->> 'tutorial_priority_basis_points')::integer > 10000
            then return false; end if;
        when 'community_demand' then
            allowed_keys := allowed_keys || array[
                'data_classification', 'room_mapping_count', 'sample_size',
                'demand_score_basis_points'
            ];
            if target ->> 'lane' <> 'community_ops'
               or target ->> 'data_classification' <> 'aggregate_anonymous'
               or target -> 'content_factual_authority' is distinct from 'false'::jsonb
               or target -> 'room_mapping_count' is distinct from '1'::jsonb
               or coalesce(target ->> 'sample_size', '') !~ '^[0-9]+$'
               or (target ->> 'sample_size')::bigint < 5
               or coalesce(target ->> 'demand_score_basis_points', '') !~ '^[0-9]+$'
               or (target ->> 'demand_score_basis_points')::integer > 10000
            then return false; end if;
        when 'official_source' then
            allowed_keys := allowed_keys || array[
                'data_classification', 'source_item_id', 'source_body_sha256',
                'source_kind', 'source_verified', 'eligible_content_kinds'
            ];
            if target ->> 'lane' <> 'content_source'
               or target ->> 'data_classification' <> 'public_official'
               or target -> 'content_factual_authority' is distinct from 'true'::jsonb
               or coalesce(target ->> 'source_item_id', '')
                    !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               or coalesce(target ->> 'source_body_sha256', '') !~ '^[a-f0-9]{64}$'
               or target ->> 'source_kind' not in ('x_post_text', 'x_article', 'official_document')
               or target -> 'source_verified' is distinct from 'true'::jsonb
               or pg_catalog.jsonb_typeof(target -> 'eligible_content_kinds') <> 'array'
               or pg_catalog.jsonb_array_length(target -> 'eligible_content_kinds') not between 1 and 3
               or exists (
                    select 1
                    from pg_catalog.jsonb_array_elements_text(
                        target -> 'eligible_content_kinds'
                    ) kind(value)
                    where kind.value not in ('article', 'daily_news', 'tutorial')
               )
               or (
                    select pg_catalog.array_agg(kind.value order by kind.ordinality)
                    from pg_catalog.jsonb_array_elements_text(
                        target -> 'eligible_content_kinds'
                    ) with ordinality kind(value, ordinality)
               ) is distinct from (
                    select pg_catalog.array_agg(distinct kind.value order by kind.value)
                    from pg_catalog.jsonb_array_elements_text(
                        target -> 'eligible_content_kinds'
                    ) kind(value)
               )
            then return false; end if;
        when 'recap_metric' then
            allowed_keys := allowed_keys || array[
                'data_classification', 'period_start', 'period_end', 'metrics'
            ];
            begin
                period_start_value := (target ->> 'period_start')::timestamptz;
                period_end_value := (target ->> 'period_end')::timestamptz;
            exception when others then
                return false;
            end;
            if target ->> 'lane' <> 'recap'
               or target ->> 'data_classification' <> 'aggregate_anonymous'
               or target -> 'content_factual_authority' is distinct from 'false'::jsonb
               or period_end_value <= period_start_value
               or period_end_value > observed_value
               or period_end_value - period_start_value > interval '31 days'
               or pg_catalog.jsonb_typeof(target -> 'metrics') <> 'array'
               or pg_catalog.jsonb_array_length(target -> 'metrics') not between 1 and 16
               or exists (
                    select 1
                    from pg_catalog.jsonb_array_elements(target -> 'metrics') metric(value)
                    where pg_catalog.jsonb_typeof(metric.value) <> 'object'
                       or (select pg_catalog.count(*)
                           from pg_catalog.jsonb_object_keys(metric.value)) <> 4
                       or not metric.value ?& array[
                            'metric_code', 'unit', 'observed', 'value'
                       ]
                       or coalesce(metric.value ->> 'metric_code', '')
                            !~ '^[a-z][a-z0-9_:-]{1,30}$'
                       or not private.agent_safe_text(
                            metric.value ->> 'metric_code', 2, 31, true
                       )
                       or metric.value ->> 'metric_code'
                            ~ '(^|[_:-])(credential|execute|instruction|prompt|publish|secret|send|tool_call)($|[_:-])'
                       or metric.value ->> 'unit' not in (
                            'count', 'basis_points', 'microusd', 'seconds'
                       )
                       or pg_catalog.jsonb_typeof(metric.value -> 'observed') <> 'boolean'
                       or (
                            (metric.value ->> 'observed')::boolean
                            <> (metric.value -> 'value' <> 'null'::jsonb)
                       )
                       or (
                            metric.value -> 'value' <> 'null'::jsonb
                            and coalesce(metric.value ->> 'value', '') !~ '^[0-9]+$'
                       )
                       or (
                            metric.value -> 'value' <> 'null'::jsonb
                            and (metric.value ->> 'value')::numeric > 1000000000000
                       )
                       or (
                            metric.value ->> 'unit' = 'basis_points'
                            and metric.value -> 'value' <> 'null'::jsonb
                            and (metric.value ->> 'value')::numeric > 10000
                       )
                       or (
                            metric.value ->> 'unit' = 'microusd'
                            and metric.value -> 'value' <> 'null'::jsonb
                            and (metric.value ->> 'value')::numeric > 1000000000
                       )
                       or (
                            metric.value ->> 'unit' = 'seconds'
                            and metric.value -> 'value' <> 'null'::jsonb
                            and (metric.value ->> 'value')::numeric > 31536000
                       )
               )
               or (
                    select pg_catalog.array_agg(
                        metric.value ->> 'metric_code' order by metric.ordinality
                    )
                    from pg_catalog.jsonb_array_elements(target -> 'metrics')
                        with ordinality metric(value, ordinality)
               ) is distinct from (
                    select pg_catalog.array_agg(
                        distinct metric.value ->> 'metric_code'
                        order by metric.value ->> 'metric_code'
                    )
                    from pg_catalog.jsonb_array_elements(target -> 'metrics') metric(value)
               )
            then return false; end if;
        else
            return false;
    end case;
    if exists (
        select 1 from pg_catalog.jsonb_object_keys(target) key(value)
        where not (key.value = any(allowed_keys))
    ) or (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(target))
            <> pg_catalog.cardinality(allowed_keys) then
        return false;
    end if;
    return true;
exception when others then
    return false;
end;
$$;

-- A caller boolean is never factual authority.  The Squid Preview source lane
-- must point at the exact immutable Content Studio needs_review version and
-- its naturally enqueued Grok QA outbox row.  The returned digest is both the
-- upstream receipt fence and the compact private-content reference.
create or replace function private.harmony_preview_squid_official_source_binding(
    target_signal jsonb
)
returns text
language sql
stable
security definer
set search_path = ''
as $$
    select private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'client_id', dispatch.client_id,
        'content_item_id', dispatch.content_item_id::text,
        'content_snapshot_sha256', private.agent_json_sha256(
            pg_catalog.jsonb_build_object(
                'channel_copy', version.channel_copy,
                'content', version.content,
                'deliverables', version.deliverables,
                'generation_meta', version.generation_meta,
                'qa', version.qa,
                'title', version.title
            )
        ),
        'content_version_id', dispatch.content_version_id::text,
        'source_body_sha256', pg_catalog.encode(extensions.digest(
            pg_catalog.convert_to(source.body, 'UTF8'), 'sha256'
        ), 'hex'),
        'source_event_type', dispatch.source_event_type,
        'source_item_id', dispatch.source_item_id::text,
        'source_url', dispatch.source_url,
        'workspace_id', dispatch.workspace_id::text
    ))
    from private.grok_qa_dispatch_outbox dispatch
    join public.content_items item
      on item.workspace_id = dispatch.workspace_id
     and item.id = dispatch.content_item_id
     and item.client_id = dispatch.client_id
     and item.current_version_id = dispatch.content_version_id
     and item.status = 'needs_review'
    join public.content_versions version
      on version.workspace_id = item.workspace_id
     and version.content_item_id = item.id
     and version.id = item.current_version_id
    join public.source_items source
      on source.workspace_id = dispatch.workspace_id
     and source.client_id = dispatch.client_id
     and source.id = dispatch.source_item_id
    where target_signal ->> 'client_id' = 'squid'
      and target_signal ->> 'signal_kind' = 'official_source'
      and target_signal ->> 'lane' = 'content_source'
      and dispatch.workspace_id = (target_signal ->> 'workspace_id')::uuid
      and dispatch.client_id = 'squid'
      and dispatch.content_version_id = (target_signal ->> 'source_event_id')::uuid
      and dispatch.source_item_id = (target_signal ->> 'source_item_id')::uuid
      and dispatch.content_kind = 'daily_news'
      and dispatch.source_author_handle = '@SquidRouter'
      and dispatch.source_event_type = 'official_x_review_draft_completed'
      and dispatch.status <> 'obsolete'
      and source.canonical_url = dispatch.source_url
      and source.canonical_url ~ '^https://x\.com/SquidRouter/status/[0-9]{1,19}$'
      and source.source_type = 'tweet'
      and source.media = '[]'::jsonb
      and pg_catalog.length(pg_catalog.btrim(source.body)) > 0
      and pg_catalog.encode(extensions.digest(
            pg_catalog.convert_to(source.body, 'UTF8'), 'sha256'
          ), 'hex') = target_signal ->> 'source_body_sha256'
      and target_signal ->> 'source_kind' = 'x_post_text'
      and target_signal -> 'eligible_content_kinds' = '["daily_news"]'::jsonb
$$;

create or replace function private.harmony_preview_connector_receipt_shape(
    target jsonb
)
returns boolean
language sql
immutable
set search_path = ''
as $$
    select pg_catalog.jsonb_typeof(target) = 'object'
       and target ?& array[
            'audience', 'automatic_publication', 'capability', 'client_id',
            'config_sha256', 'connector_id', 'environment', 'evidence_sha256',
            'expires_at', 'issuer', 'lane', 'payload_sha256',
            'producer_principal_id', 'producer_release_sha', 'raw_data_included',
            'receipt_id', 'schema_version', 'side_effects_performed', 'signal_id',
            'signal_kind', 'signal_payload_sha256', 'source_event_id',
            'upstream_receipt_sha256', 'verification_method',
            'verification_reference_sha256', 'verified_at', 'workspace_id'
       ]
       and (select pg_catalog.count(*)
            from pg_catalog.jsonb_object_keys(target)) = 27
$$;

create table agent_runtime.harmony_connector_attestation_receipts (
    workspace_id uuid not null,
    client_id text not null,
    receipt_id uuid not null,
    signal_id uuid not null,
    source_event_id uuid not null,
    connector_id text not null check (connector_id ~ '^[a-z][a-z0-9_:-]{2,63}$'),
    producer_principal_id uuid not null,
    producer_release_sha text not null check (producer_release_sha ~ '^[a-f0-9]{40}$'),
    config_sha256 text not null check (config_sha256 ~ '^[a-f0-9]{64}$'),
    signal_kind text not null,
    lane text not null,
    upstream_receipt_sha256 text not null check (upstream_receipt_sha256 ~ '^[a-f0-9]{64}$'),
    capability text not null,
    signal_payload_sha256 text not null check (signal_payload_sha256 ~ '^[a-f0-9]{64}$'),
    evidence_sha256 text not null check (evidence_sha256 ~ '^[a-f0-9]{64}$'),
    verification_reference_sha256 text not null check (
        verification_reference_sha256 ~ '^[a-f0-9]{64}$'
    ),
    payload jsonb not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    verified_at timestamptz not null,
    expires_at timestamptz not null,
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, client_id, receipt_id),
    unique (workspace_id, client_id, signal_id),
    unique (workspace_id, client_id, signal_payload_sha256),
    unique (workspace_id, client_id, verification_reference_sha256),
    unique (
        workspace_id, client_id, receipt_id, signal_id,
        signal_payload_sha256, payload_sha256
    ),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    check (client_id in ('babylon', 'origintrail', 'squid', 'yellow')),
    check (expires_at > verified_at and expires_at - verified_at <= interval '31 days'),
    check (private.harmony_preview_connector_receipt_shape(payload)),
    check (payload ->> 'schema_version' = 'harmony-connector-attestation-receipt@1'),
    check (payload ->> 'receipt_id' = receipt_id::text),
    check (payload ->> 'workspace_id' = workspace_id::text),
    check (payload ->> 'client_id' = client_id),
    check (payload ->> 'signal_id' = signal_id::text),
    check (payload ->> 'source_event_id' = source_event_id::text),
    check (payload ->> 'connector_id' = connector_id),
    check (payload ->> 'producer_principal_id' = producer_principal_id::text),
    check (payload ->> 'producer_release_sha' = producer_release_sha),
    check (payload ->> 'config_sha256' = config_sha256),
    check (payload ->> 'signal_kind' = signal_kind),
    check (payload ->> 'lane' = lane),
    check (payload ->> 'capability' = capability),
    check (payload ->> 'signal_payload_sha256' = signal_payload_sha256),
    check (payload ->> 'upstream_receipt_sha256' = upstream_receipt_sha256),
    check (payload ->> 'evidence_sha256' = evidence_sha256),
    check (payload ->> 'verification_reference_sha256'
        = verification_reference_sha256),
    check (payload ->> 'verified_at' = pg_catalog.to_char(
        verified_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
    )),
    check (payload ->> 'expires_at' = pg_catalog.to_char(
        expires_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
    )),
    check (payload ->> 'payload_sha256' = payload_sha256),
    check (payload_sha256 = private.agent_json_sha256(payload - 'payload_sha256')),
    check (payload ->> 'environment' = 'preview'),
    check (payload ->> 'issuer' = 'supabase'),
    check (payload ->> 'audience' = 'authenticated'),
    check (payload ->> 'verification_method' = 'jwt'),
    check (payload -> 'raw_data_included' = 'false'::jsonb),
    check (payload -> 'side_effects_performed' = 'false'::jsonb),
    check (payload -> 'automatic_publication' = 'false'::jsonb)
);

create table agent_runtime.harmony_signals (
    workspace_id uuid not null,
    client_id text not null,
    signal_id uuid not null,
    source_event_id uuid not null,
    producer_principal_id uuid not null,
    signal_kind text not null,
    lane text not null,
    upstream_receipt_sha256 text not null check (
        upstream_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    idempotency_key text not null check (idempotency_key ~ '^[a-f0-9]{64}$'),
    payload jsonb not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    connector_receipt_id uuid not null,
    connector_receipt_sha256 text not null check (connector_receipt_sha256 ~ '^[a-f0-9]{64}$'),
    official_content_version_id uuid,
    official_source_item_id uuid,
    official_source_binding_sha256 text,
    observed_at timestamptz not null,
    expires_at timestamptz not null,
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, client_id, signal_id),
    unique (workspace_id, client_id, idempotency_key),
    unique (workspace_id, client_id, payload_sha256),
    unique (workspace_id, client_id, lane, upstream_receipt_sha256),
    unique (workspace_id, client_id, signal_id, payload_sha256),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    foreign key (
        workspace_id, client_id, connector_receipt_id, signal_id,
        payload_sha256, connector_receipt_sha256
    ) references agent_runtime.harmony_connector_attestation_receipts(
        workspace_id, client_id, receipt_id, signal_id,
        signal_payload_sha256, payload_sha256
    ) on delete restrict,
    foreign key (workspace_id, official_content_version_id)
        references private.grok_qa_dispatch_outbox(
            workspace_id, content_version_id
        ) on delete restrict,
    foreign key (workspace_id, client_id, official_source_item_id)
        references public.source_items(workspace_id, client_id, id)
        on delete restrict,
    check (private.harmony_preview_signal_valid(payload)),
    check (payload ->> 'payload_sha256' = payload_sha256),
    check (payload ->> 'signal_id' = signal_id::text),
    check (payload ->> 'workspace_id' = workspace_id::text),
    check (payload ->> 'client_id' = client_id),
    check (payload ->> 'signal_kind' = signal_kind),
    check (payload ->> 'lane' = lane),
    check (payload ->> 'source_event_id' = source_event_id::text),
    check (payload ->> 'producer_principal_id' = producer_principal_id::text),
    check (payload ->> 'upstream_receipt_sha256' = upstream_receipt_sha256),
    check (
        (lane = 'content_source'
            and official_content_version_id = source_event_id
            and official_source_item_id = (payload ->> 'source_item_id')::uuid
            and official_source_binding_sha256 = upstream_receipt_sha256)
        or (lane <> 'content_source'
            and official_content_version_id is null
            and official_source_item_id is null
            and official_source_binding_sha256 is null)
    ),
    check (payload ->> 'observed_at' = to_char(observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
    check (payload ->> 'expires_at' = to_char(expires_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
);

create table agent_runtime.harmony_rounds (
    workspace_id uuid not null,
    client_id text not null,
    round_id uuid not null,
    plan_id uuid not null,
    input_set_sha256 text not null check (input_set_sha256 ~ '^[a-f0-9]{64}$'),
    request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
    signal_manifest jsonb not null,
    payload jsonb not null,
    round_sha256 text not null check (round_sha256 ~ '^[a-f0-9]{64}$'),
    status text not null check (status = 'planned'),
    synthetic boolean not null check (synthetic),
    automatic_publication boolean not null check (not automatic_publication),
    created_at timestamptz not null,
    primary key (workspace_id, client_id, round_id),
    unique (workspace_id, client_id, input_set_sha256),
    unique (workspace_id, client_id, plan_id),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    check (client_id = 'squid'),
    check (pg_catalog.jsonb_typeof(signal_manifest) = 'array'),
    check (pg_catalog.jsonb_array_length(signal_manifest) = 4),
    check (payload ->> 'round_id' = round_id::text),
    check (payload ->> 'plan_id' = plan_id::text),
    check (payload ->> 'round_sha256' = round_sha256),
    check (round_sha256 = private.agent_json_sha256(payload - 'round_sha256')),
    check (payload -> 'automatic_publication' = 'false'::jsonb),
    check (payload -> 'external_calls' = 'false'::jsonb),
    check (payload -> 'provider_calls' = 'false'::jsonb),
    check (payload -> 'publication_calls' = 'false'::jsonb)
);

create table agent_runtime.harmony_plans (
    workspace_id uuid not null,
    client_id text not null,
    plan_id uuid not null,
    round_id uuid not null,
    payload jsonb not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    state text not null check (state = 'planned'),
    created_at timestamptz not null,
    primary key (workspace_id, client_id, plan_id),
    unique (workspace_id, client_id, round_id),
    foreign key (workspace_id, client_id, round_id)
        references agent_runtime.harmony_rounds(workspace_id, client_id, round_id)
        on delete restrict,
    check (payload_sha256 = private.agent_json_sha256(payload)),
    check (payload -> 'automatic_publication' = 'false'::jsonb),
    check (payload -> 'synthetic' = 'true'::jsonb)
);

create table agent_runtime.harmony_stage_receipts (
    workspace_id uuid not null,
    client_id text not null,
    receipt_id uuid not null,
    round_id uuid not null,
    plan_id uuid not null,
    stage text not null check (stage in (
        'plan', 'private_content', 'independent_qa', 'operator_inbox', 'recap'
    )),
    ordinal smallint not null check (ordinal between 1 and 5),
    actor text not null,
    principal_id uuid not null,
    producer_release_sha text not null check (producer_release_sha ~ '^[a-f0-9]{40}$'),
    config_sha256 text not null check (config_sha256 ~ '^[a-f0-9]{64}$'),
    capability text not null,
    binding_receipt_sha256 text not null check (
        binding_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    verdict text check (verdict is null or verdict = 'passed'),
    reviewer_principal_id uuid,
    previous_receipt_sha256 text check (
        previous_receipt_sha256 is null
        or previous_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    input_sha256 text not null check (input_sha256 ~ '^[a-f0-9]{64}$'),
    output_sha256 text not null check (output_sha256 ~ '^[a-f0-9]{64}$'),
    artifact jsonb not null,
    artifact_sha256 text not null check (artifact_sha256 ~ '^[a-f0-9]{64}$'),
    payload jsonb not null,
    receipt_sha256 text not null check (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz not null,
    primary key (workspace_id, client_id, receipt_id),
    unique (workspace_id, client_id, plan_id, stage),
    unique (workspace_id, client_id, plan_id, ordinal),
    unique (workspace_id, client_id, binding_receipt_sha256),
    unique (workspace_id, client_id, receipt_id, plan_id, round_id),
    foreign key (workspace_id, client_id, plan_id)
        references agent_runtime.harmony_plans(workspace_id, client_id, plan_id)
        on delete restrict,
    check (artifact_sha256 = private.agent_json_sha256(artifact)),
    check (payload ->> 'receipt_sha256' = receipt_sha256),
    check (receipt_sha256 = private.agent_json_sha256(payload - 'receipt_sha256')),
    check ((ordinal = 1) = (previous_receipt_sha256 is null)),
    check (payload ->> 'input_sha256' = input_sha256),
    check (payload ->> 'output_sha256' = output_sha256),
    check (payload ->> 'previous_receipt_sha256' is not distinct from previous_receipt_sha256),
    check ((stage, ordinal, actor) in (
        ('plan', 1, 'grok_bot'),
        ('private_content', 2, 'content_engine'),
        ('independent_qa', 3, 'codex'),
        ('operator_inbox', 4, 'human_operator_inbox'),
        ('recap', 5, 'coineasy_recap')
    )),
    check ((stage, capability) in (
        ('plan', 'harmony_plan'),
        ('private_content', 'harmony_prepare_private_content'),
        ('independent_qa', 'harmony_independent_qa'),
        ('operator_inbox', 'harmony_operator_inbox'),
        ('recap', 'harmony_recap')
    )),
    check (
        (stage = 'independent_qa' and verdict = 'passed'
            and reviewer_principal_id = principal_id)
        or (stage <> 'independent_qa' and verdict is null
            and reviewer_principal_id is null)
    ),
    check (payload -> 'synthetic' = 'true'::jsonb),
    check (payload -> 'aggregate_only' = 'true'::jsonb),
    check (payload -> 'external_calls' = 'false'::jsonb),
    check (payload -> 'provider_calls' = 'false'::jsonb),
    check (payload -> 'publication_calls' = 'false'::jsonb),
    check (payload -> 'automatic_publication' = 'false'::jsonb)
);

create table agent_runtime.harmony_operator_inbox (
    workspace_id uuid not null,
    client_id text not null,
    inbox_id uuid not null,
    round_id uuid not null,
    plan_id uuid not null,
    stage_receipt_id uuid not null,
    scope_sha256 text not null check (scope_sha256 ~ '^[a-f0-9]{64}$'),
    qa_receipt_id uuid not null,
    qa_receipt_sha256 text not null check (qa_receipt_sha256 ~ '^[a-f0-9]{64}$'),
    qa_output_sha256 text not null check (qa_output_sha256 ~ '^[a-f0-9]{64}$'),
    payload jsonb not null,
    status text not null check (status = 'pending'),
    created_at timestamptz not null,
    primary key (workspace_id, client_id, inbox_id),
    unique (workspace_id, client_id, plan_id),
    foreign key (
        workspace_id, client_id, stage_receipt_id, plan_id, round_id
    ) references agent_runtime.harmony_stage_receipts(
        workspace_id, client_id, receipt_id, plan_id, round_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, qa_receipt_id, plan_id, round_id
    ) references agent_runtime.harmony_stage_receipts(
        workspace_id, client_id, receipt_id, plan_id, round_id
    ) on delete restrict,
    check (payload ->> 'schema_version' = 'harmony-operator-inbox@1'),
    check (payload ->> 'inbox_id' = inbox_id::text),
    check (payload ->> 'scope_sha256' = scope_sha256),
    check (payload ->> 'qa_receipt_id' = qa_receipt_id::text),
    check (payload ->> 'qa_receipt_sha256' = qa_receipt_sha256),
    check (payload ->> 'qa_output_sha256' = qa_output_sha256),
    check (payload ->> 'status' = 'pending'),
    check (payload -> 'operator_decision_recorded' = 'false'::jsonb),
    check (payload -> 'external_delivery_attempted' = 'false'::jsonb),
    check (payload -> 'automatic_publication' = 'false'::jsonb)
);

alter table agent_runtime.harmony_connector_attestation_receipts
    enable row level security;
alter table agent_runtime.harmony_connector_attestation_receipts
    force row level security;
alter table agent_runtime.harmony_signals enable row level security;
alter table agent_runtime.harmony_signals force row level security;
alter table agent_runtime.harmony_rounds enable row level security;
alter table agent_runtime.harmony_rounds force row level security;
alter table agent_runtime.harmony_plans enable row level security;
alter table agent_runtime.harmony_plans force row level security;
alter table agent_runtime.harmony_stage_receipts enable row level security;
alter table agent_runtime.harmony_stage_receipts force row level security;
alter table agent_runtime.harmony_operator_inbox enable row level security;
alter table agent_runtime.harmony_operator_inbox force row level security;

create policy harmony_connector_receipts_client_select
on agent_runtime.harmony_connector_attestation_receipts
for select using (private.harmony_preview_lane_visible(
    workspace_id, client_id,
    lane,
    array['coineasy_harmony_connector', 'coineasy_harmony_orchestrator',
          'coineasy_harmony_operator']::text[]
));
create policy harmony_signals_client_select
on agent_runtime.harmony_signals
for select using (private.harmony_preview_lane_visible(
    workspace_id, client_id,
    lane,
    array['coineasy_harmony_connector', 'coineasy_harmony_orchestrator',
          'coineasy_harmony_operator']::text[]
));
create policy harmony_rounds_client_select
on agent_runtime.harmony_rounds
for select using (private.harmony_preview_scope_matches(
    workspace_id, client_id,
    array['coineasy_harmony_orchestrator', 'coineasy_harmony_operator']::text[]
));
create policy harmony_plans_client_select
on agent_runtime.harmony_plans
for select using (private.harmony_preview_scope_matches(
    workspace_id, client_id,
    array['coineasy_harmony_orchestrator', 'coineasy_harmony_operator']::text[]
));
create policy harmony_stage_receipts_client_select
on agent_runtime.harmony_stage_receipts
for select using (private.harmony_preview_scope_matches(
    workspace_id, client_id,
    array['coineasy_harmony_orchestrator', 'coineasy_harmony_operator']::text[]
));
create policy harmony_operator_inbox_client_select
on agent_runtime.harmony_operator_inbox
for select using (private.harmony_preview_scope_matches(
    workspace_id, client_id,
    array['coineasy_harmony_operator']::text[]
));

revoke all on table
    agent_runtime.harmony_connector_attestation_receipts,
    agent_runtime.harmony_signals,
    agent_runtime.harmony_rounds,
    agent_runtime.harmony_plans,
    agent_runtime.harmony_stage_receipts,
    agent_runtime.harmony_operator_inbox
from public, anon, authenticated, service_role;

create trigger harmony_connector_receipts_immutable
before update or delete on agent_runtime.harmony_connector_attestation_receipts
for each row execute function private.agent_immutable_row();
create trigger harmony_signals_immutable
before update or delete on agent_runtime.harmony_signals
for each row execute function private.agent_immutable_row();
create trigger harmony_rounds_immutable
before update or delete on agent_runtime.harmony_rounds
for each row execute function private.agent_immutable_row();
create trigger harmony_plans_immutable
before update or delete on agent_runtime.harmony_plans
for each row execute function private.agent_immutable_row();
create trigger harmony_stage_receipts_immutable
before update or delete on agent_runtime.harmony_stage_receipts
for each row execute function private.agent_immutable_row();
create trigger harmony_operator_inbox_immutable
before update or delete on agent_runtime.harmony_operator_inbox
for each row execute function private.agent_immutable_row();

create or replace function public.submit_preview_harmony_signal(
    target_workspace_id uuid,
    target_client_id text,
    target_receipt_id uuid,
    target_signal jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    existing agent_runtime.harmony_signals%rowtype;
    existing_receipt agent_runtime.harmony_connector_attestation_receipts%rowtype;
    receipt_payload jsonb;
    receipt_sha text;
    verification_reference_sha text;
    idempotency_sha text;
    verified_time timestamptz;
    receipt_expires_at timestamptz;
    official_source_binding text;
begin
    if not private.harmony_preview_signal_valid(target_signal)
       or target_signal ->> 'workspace_id' <> target_workspace_id::text
       or target_signal ->> 'client_id' <> target_client_id
       or not private.harmony_preview_connector_claims_match(
            target_workspace_id, target_client_id, target_signal
       ) then
        raise exception 'harmony_preview_connector_scope_invalid';
    end if;
    if (target_signal ->> 'observed_at')::timestamptz > statement_timestamp()
       or (target_signal ->> 'expires_at')::timestamptz
            <= statement_timestamp() then
        raise exception 'harmony_preview_signal_not_current';
    end if;
    if target_signal ->> 'lane' = 'content_source' then
        official_source_binding :=
            private.harmony_preview_squid_official_source_binding(target_signal);
        if official_source_binding is null
           or target_signal ->> 'upstream_receipt_sha256'
                <> official_source_binding then
            raise exception 'harmony_preview_official_source_binding_invalid';
        end if;
    end if;
    claims := nullif(
        pg_catalog.current_setting('request.jwt.claims', true), ''
    )::jsonb;
    verification_reference_sha :=
        private.harmony_preview_connector_verification_reference();
    idempotency_sha := private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'workspace_id', target_workspace_id::text,
        'client_id', target_client_id,
        'producer_principal_id', target_signal ->> 'producer_principal_id',
        'signal_kind', target_signal ->> 'signal_kind',
        'source_event_id', target_signal ->> 'source_event_id',
        'schema_version', target_signal ->> 'schema_version'
    ));
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_signal:' || target_workspace_id::text || ':' ||
        target_client_id || ':' || idempotency_sha,
        0
    ));
    select * into existing
    from agent_runtime.harmony_signals signal
    where signal.workspace_id = target_workspace_id
      and signal.client_id = target_client_id
      and (
          signal.idempotency_key = idempotency_sha
          or signal.signal_id = (target_signal ->> 'signal_id')::uuid
          or signal.payload_sha256 = target_signal ->> 'payload_sha256'
      )
    for update;
    if found then
        if existing.payload_sha256 <> target_signal ->> 'payload_sha256'
           or existing.signal_id <> (target_signal ->> 'signal_id')::uuid
           or existing.idempotency_key <> idempotency_sha
           or existing.connector_receipt_id <> target_receipt_id then
            raise exception 'harmony_preview_signal_idempotency_conflict';
        end if;
        select receipt.* into strict existing_receipt
        from agent_runtime.harmony_connector_attestation_receipts receipt
        where receipt.workspace_id = existing.workspace_id
          and receipt.client_id = existing.client_id
          and receipt.receipt_id = existing.connector_receipt_id;
        if existing_receipt.verification_reference_sha256
                <> verification_reference_sha
           or existing_receipt.verified_at > statement_timestamp()
           or existing_receipt.expires_at <= statement_timestamp()
        then
            raise exception 'harmony_preview_signal_replay_receipt_invalid';
        end if;
        receipt_payload := existing_receipt.payload;
        return pg_catalog.jsonb_build_object(
            'ok', true,
            'reused', true,
            'signal', existing.payload,
            'connector_receipt', receipt_payload,
            'database_calls', true,
            'external_calls', false,
            'provider_calls', false,
            'publication_calls', false,
            'automatic_publication', false
        );
    end if;

    verified_time := pg_catalog.date_trunc('second', statement_timestamp());
    receipt_expires_at := pg_catalog.date_trunc('second', least(
        (target_signal ->> 'expires_at')::timestamptz,
        pg_catalog.to_timestamp((claims ->> 'exp')::bigint)
    ));
    if receipt_expires_at <= verified_time then
        raise exception 'harmony_preview_connector_receipt_not_current';
    end if;
    receipt_payload := pg_catalog.jsonb_build_object(
        'audience', 'authenticated',
        'automatic_publication', false,
        'capability', claims ->> 'capability',
        'client_id', target_client_id,
        'config_sha256', claims ->> 'config_sha256',
        'connector_id', claims ->> 'connector_id',
        'environment', 'preview',
        'evidence_sha256', target_signal ->> 'evidence_sha256',
        'expires_at', pg_catalog.to_char(
            receipt_expires_at at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'issuer', 'supabase',
        'lane', target_signal ->> 'lane',
        'producer_principal_id', target_signal ->> 'producer_principal_id',
        'producer_release_sha', target_signal ->> 'producer_release_sha',
        'raw_data_included', false,
        'receipt_id', target_receipt_id::text,
        'schema_version', 'harmony-connector-attestation-receipt@1',
        'side_effects_performed', false,
        'signal_id', target_signal ->> 'signal_id',
        'signal_kind', target_signal ->> 'signal_kind',
        'signal_payload_sha256', target_signal ->> 'payload_sha256',
        'source_event_id', target_signal ->> 'source_event_id',
        'upstream_receipt_sha256',
            target_signal ->> 'upstream_receipt_sha256',
        'verification_method', 'jwt',
        'verification_reference_sha256', verification_reference_sha,
        'verified_at', pg_catalog.to_char(
            verified_time at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'workspace_id', target_workspace_id::text
    );
    receipt_sha := private.agent_json_sha256(receipt_payload);
    receipt_payload := receipt_payload || pg_catalog.jsonb_build_object(
        'payload_sha256', receipt_sha
    );
    insert into agent_runtime.harmony_connector_attestation_receipts (
        workspace_id, client_id, receipt_id, signal_id, source_event_id,
        connector_id, producer_principal_id, producer_release_sha,
        config_sha256, signal_kind, lane, capability,
        signal_payload_sha256, upstream_receipt_sha256, evidence_sha256,
        verification_reference_sha256, payload, payload_sha256,
        verified_at, expires_at
    ) values (
        target_workspace_id,
        target_client_id,
        target_receipt_id,
        (target_signal ->> 'signal_id')::uuid,
        (target_signal ->> 'source_event_id')::uuid,
        claims ->> 'connector_id',
        (target_signal ->> 'producer_principal_id')::uuid,
        target_signal ->> 'producer_release_sha',
        target_signal ->> 'config_sha256',
        target_signal ->> 'signal_kind',
        target_signal ->> 'lane',
        claims ->> 'capability',
        target_signal ->> 'payload_sha256',
        target_signal ->> 'upstream_receipt_sha256',
        target_signal ->> 'evidence_sha256',
        verification_reference_sha,
        receipt_payload,
        receipt_sha,
        verified_time,
        receipt_expires_at
    );
    insert into agent_runtime.harmony_signals (
        workspace_id, client_id, signal_id, source_event_id,
        producer_principal_id, signal_kind, lane, idempotency_key,
        payload, payload_sha256, connector_receipt_id,
        connector_receipt_sha256, upstream_receipt_sha256,
        official_content_version_id, official_source_item_id,
        official_source_binding_sha256, observed_at, expires_at
    ) values (
        target_workspace_id,
        target_client_id,
        (target_signal ->> 'signal_id')::uuid,
        (target_signal ->> 'source_event_id')::uuid,
        (target_signal ->> 'producer_principal_id')::uuid,
        target_signal ->> 'signal_kind',
        target_signal ->> 'lane',
        idempotency_sha,
        target_signal,
        target_signal ->> 'payload_sha256',
        target_receipt_id,
        receipt_sha,
        target_signal ->> 'upstream_receipt_sha256',
        case when target_signal ->> 'lane' = 'content_source'
            then (target_signal ->> 'source_event_id')::uuid end,
        case when target_signal ->> 'lane' = 'content_source'
            then (target_signal ->> 'source_item_id')::uuid end,
        official_source_binding,
        (target_signal ->> 'observed_at')::timestamptz,
        (target_signal ->> 'expires_at')::timestamptz
    );
    return pg_catalog.jsonb_build_object(
        'ok', true,
        'reused', false,
        'signal', target_signal,
        'connector_receipt', receipt_payload,
        'database_calls', true,
        'external_calls', false,
        'provider_calls', false,
        'publication_calls', false,
        'automatic_publication', false
    );
end;
$$;

revoke all on function private.harmony_preview_scope_matches(uuid, text, text[])
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_lane_visible(
    uuid, text, text, text[]
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_connector_claims_match(
    uuid, text, jsonb
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_connector_verification_reference()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_signal_valid(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_squid_official_source_binding(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_connector_receipt_shape(jsonb)
from public, anon, authenticated, service_role;
revoke all on function public.submit_preview_harmony_signal(
    uuid, text, uuid, jsonb
) from public, anon, authenticated, service_role;

commit;
