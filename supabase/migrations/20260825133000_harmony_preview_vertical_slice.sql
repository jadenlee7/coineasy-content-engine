-- Start the synthetic, private Squid Preview vertical slice.
--
-- The plan is derived only from four current, database-attested inputs.  It
-- records a planned immutable round and stage-1 receipt; it does not call a
-- provider, deliver a message, approve, publish, or mutate source content.

begin;

create or replace function private.harmony_preview_stage_claims_match(
    target_workspace_id uuid,
    target_client_id text,
    target_role text,
    target_capability text
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb;
begin
    begin
        claims := coalesce(
            nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb,
            '{}'::jsonb
        );
    exception when others then
        return false;
    end;
    return private.harmony_preview_scope_matches(
        target_workspace_id, target_client_id, array[target_role]::text[]
    )
       and claims ->> 'capability' = target_capability
       and coalesce(claims ->> 'producer_principal_id', '')
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       and claims ->> 'sub' = claims ->> 'producer_principal_id'
       and coalesce(claims ->> 'release_sha', '') ~ '^[a-f0-9]{40}$'
       and coalesce(claims ->> 'config_sha256', '') ~ '^[a-f0-9]{64}$'
       and coalesce(claims ->> 'jti', '')
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$';
end;
$$;

create or replace function private.harmony_preview_stage_binding()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb := nullif(
        pg_catalog.current_setting('request.jwt.claims', true), ''
    )::jsonb;
    safe_claims jsonb;
begin
    safe_claims := pg_catalog.jsonb_build_object(
        'capability', claims ->> 'capability',
        'client_id', claims ->> 'client_id',
        'config_sha256', claims ->> 'config_sha256',
        'environment', claims ->> 'environment',
        'exp', (claims ->> 'exp')::bigint,
        'iat', (claims ->> 'iat')::bigint,
        'jti', claims ->> 'jti',
        'producer_principal_id', claims ->> 'producer_principal_id',
        'ref', claims ->> 'ref',
        'release_sha', claims ->> 'release_sha',
        'role', claims ->> 'role',
        'workspace_id', claims ->> 'workspace_id'
    );
    return pg_catalog.jsonb_build_object(
        'binding_receipt_sha256', private.agent_json_sha256(safe_claims),
        'capability', claims ->> 'capability',
        'config_sha256', claims ->> 'config_sha256',
        'principal_id', claims ->> 'producer_principal_id',
        'producer_release_sha', claims ->> 'release_sha'
    );
end;
$$;

create or replace function private.harmony_preview_stage_receipt_payload(
    target_receipt_id uuid,
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid,
    target_plan_id uuid,
    target_stage text,
    target_ordinal smallint,
    target_actor text,
    target_previous_receipt_sha256 text,
    target_input_sha256 text,
    target_output_sha256 text,
    target_recorded_at timestamptz,
    target_verdict text default null,
    target_reviewer_principal_id uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    binding jsonb := private.harmony_preview_stage_binding();
    body jsonb;
begin
    body := pg_catalog.jsonb_build_object(
        'actor', target_actor,
        'actual_cost_microusd', 0,
        'aggregate_only', true,
        'automatic_publication', false,
        'binding_receipt_sha256', binding ->> 'binding_receipt_sha256',
        'capability', binding ->> 'capability',
        'client_id', target_client_id,
        'config_sha256', binding ->> 'config_sha256',
        'external_calls', false,
        'input_sha256', target_input_sha256,
        'ordinal', target_ordinal,
        'output_sha256', target_output_sha256,
        'plan_id', target_plan_id::text,
        'previous_receipt_sha256', target_previous_receipt_sha256,
        'principal_id', binding ->> 'principal_id',
        'producer_release_sha', binding ->> 'producer_release_sha',
        'provider_calls', false,
        'publication_calls', false,
        'receipt_id', target_receipt_id::text,
        'recorded_at', pg_catalog.to_char(
            target_recorded_at at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'reviewer_principal_id', target_reviewer_principal_id,
        'round_id', target_round_id::text,
        'schema_version', 'harmony-stage-receipt@1',
        'stage', target_stage,
        'synthetic', true,
        'verdict', target_verdict,
        'workspace_id', target_workspace_id::text
    );
    return body || pg_catalog.jsonb_build_object(
        'receipt_sha256', private.agent_json_sha256(body)
    );
end;
$$;

create or replace function public.create_preview_harmony_squid_plan(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid,
    target_plan_id uuid,
    target_stage_receipt_id uuid,
    target_signal_payload_sha256s text[],
    target_topic_code text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    signal_manifest jsonb;
    input_set_sha text;
    request_sha text;
    plan_payload jsonb;
    plan_sha text;
    round_body jsonb;
    round_sha text;
    stage_payload jsonb;
    stage_sha text;
    created_time timestamptz;
    existing agent_runtime.harmony_rounds%rowtype;
    existing_stage agent_runtime.harmony_stage_receipts%rowtype;
    support_count integer;
begin
    if target_client_id <> 'squid'
       or not private.harmony_preview_stage_claims_match(
            target_workspace_id, target_client_id,
            'coineasy_harmony_orchestrator', 'harmony_plan'
       )
       or target_signal_payload_sha256s is null
       or pg_catalog.cardinality(target_signal_payload_sha256s) <> 4
       or target_signal_payload_sha256s <> (
            select pg_catalog.array_agg(value order by value)
            from unnest(target_signal_payload_sha256s) item(value)
       )
       or (select pg_catalog.count(distinct value)
           from unnest(target_signal_payload_sha256s) item(value)) <> 4
       or not private.agent_safe_text(target_topic_code, 2, 31, true)
       or target_topic_code !~ '^[a-z][a-z0-9_:-]{1,30}$'
       or not (target_topic_code = any(array[
            'community_faq', 'integration_update', 'launch_status',
            'market_context', 'official_update', 'performance_gap',
            'product_mechanics', 'routing_basics', 'security_safety',
            'staking_basics', 'technical_architecture',
            'tutorial_demand', 'user_guide', 'wallet_safety'
       ]::text[]))
    then
        raise exception 'harmony_preview_plan_scope_invalid';
    end if;
    claims := nullif(
        pg_catalog.current_setting('request.jwt.claims', true), ''
    )::jsonb;
    if (
        select pg_catalog.count(*)
        from agent_runtime.harmony_signals signal
        join agent_runtime.harmony_connector_attestation_receipts receipt
          on receipt.workspace_id = signal.workspace_id
         and receipt.client_id = signal.client_id
         and receipt.receipt_id = signal.connector_receipt_id
         and receipt.payload_sha256 = signal.connector_receipt_sha256
        where signal.workspace_id = target_workspace_id
          and signal.client_id = target_client_id
          and signal.payload_sha256 = any(target_signal_payload_sha256s)
          and signal.observed_at <= statement_timestamp()
          and signal.expires_at > statement_timestamp()
          and receipt.verified_at <= statement_timestamp()
          and receipt.expires_at > statement_timestamp()
    ) <> 4 or (
        select pg_catalog.count(distinct signal.lane)
        from agent_runtime.harmony_signals signal
        where signal.workspace_id = target_workspace_id
          and signal.client_id = target_client_id
          and signal.payload_sha256 = any(target_signal_payload_sha256s)
    ) <> 4 then
        raise exception 'harmony_preview_plan_input_incomplete';
    end if;
    if not exists (
        select 1
        from agent_runtime.harmony_signals signal
        where signal.workspace_id = target_workspace_id
          and signal.client_id = target_client_id
          and signal.payload_sha256 = any(target_signal_payload_sha256s)
          and signal.lane = 'content_source'
          and signal.official_source_binding_sha256
                = signal.upstream_receipt_sha256
          and private.harmony_preview_squid_official_source_binding(
                signal.payload
              ) = signal.official_source_binding_sha256
          and signal.payload -> 'topic_codes' ? target_topic_code
    ) then
        raise exception 'harmony_preview_official_topic_missing';
    end if;
    select pg_catalog.count(*) into support_count
    from agent_runtime.harmony_signals signal
    where signal.workspace_id = target_workspace_id
      and signal.client_id = target_client_id
      and signal.payload_sha256 = any(target_signal_payload_sha256s)
      and signal.payload -> 'topic_codes' ? target_topic_code
      and (
          signal.lane in ('quiz_bot', 'community_ops')
          or (
              signal.lane = 'recap'
              and exists (
                  select 1
                  from pg_catalog.jsonb_array_elements(
                      signal.payload -> 'metrics'
                  ) metric(value)
                  where metric.value -> 'observed' = 'true'::jsonb
              )
          )
      );
    if support_count < 2 then
        raise exception 'harmony_preview_topic_consensus_missing';
    end if;

    select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
        'connector_receipt_id', signal.connector_receipt_id::text,
        'connector_receipt_sha256', signal.connector_receipt_sha256,
        'content_factual_authority', signal.lane = 'content_source',
        'lane', signal.lane,
        'official_content_version_id', signal.official_content_version_id,
        'official_source_binding_sha256',
            signal.official_source_binding_sha256,
        'signal_id', signal.signal_id::text,
        'signal_kind', signal.signal_kind,
        'signal_payload_sha256', signal.payload_sha256,
        'upstream_receipt_sha256', signal.upstream_receipt_sha256
    ) order by signal.lane)
    into signal_manifest
    from agent_runtime.harmony_signals signal
    where signal.workspace_id = target_workspace_id
      and signal.client_id = target_client_id
      and signal.payload_sha256 = any(target_signal_payload_sha256s);
    input_set_sha := private.agent_json_sha256(signal_manifest);
    request_sha := private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'config_sha256', claims ->> 'config_sha256',
        'input_set_sha256', input_set_sha,
        'plan_id', target_plan_id::text,
        'principal_id', claims ->> 'producer_principal_id',
        'release_sha', claims ->> 'release_sha',
        'round_id', target_round_id::text,
        'stage_receipt_id', target_stage_receipt_id::text,
        'topic_code', target_topic_code
    ));
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_plan:' || target_workspace_id::text || ':' ||
        target_client_id || ':' || input_set_sha, 0
    ));
    select * into existing
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.input_set_sha256 = input_set_sha
    for update;
    if found then
        if existing.request_sha256 <> request_sha
           or existing.round_id <> target_round_id
           or existing.plan_id <> target_plan_id then
            raise exception 'harmony_preview_plan_idempotency_conflict';
        end if;
        select receipt.* into existing_stage
        from agent_runtime.harmony_stage_receipts receipt
        where receipt.workspace_id = existing.workspace_id
          and receipt.client_id = existing.client_id
          and receipt.plan_id = existing.plan_id
          and receipt.stage = 'plan'
        for update;
        if not found
           or existing_stage.receipt_id <> target_stage_receipt_id
           or existing_stage.round_id <> target_round_id
           or existing_stage.principal_id
                <> (claims ->> 'producer_principal_id')::uuid
           or existing_stage.producer_release_sha <> claims ->> 'release_sha'
           or existing_stage.config_sha256 <> claims ->> 'config_sha256'
           or existing_stage.capability <> claims ->> 'capability'
        then
            raise exception 'harmony_preview_plan_idempotency_conflict';
        end if;
        stage_payload := existing_stage.payload;
        return pg_catalog.jsonb_build_object(
            'ok', true, 'reused', true,
            'round_id', existing.round_id,
            'plan_id', existing.plan_id,
            'stage_receipt', stage_payload,
            'external_calls', false, 'provider_calls', false,
            'publication_calls', false, 'automatic_publication', false
        );
    end if;

    created_time := pg_catalog.date_trunc('second', statement_timestamp());
    plan_payload := pg_catalog.jsonb_build_object(
        'automatic_publication', false,
        'client_id', target_client_id,
        'external_calls', false,
        'input_set_sha256', input_set_sha,
        'plan_id', target_plan_id::text,
        'provider_calls', false,
        'publication_calls', false,
        'round_id', target_round_id::text,
        'schema_version', 'harmony-plan@1',
        'synthetic', true,
        'topic_code', target_topic_code,
        'visibility', 'private',
        'workspace_id', target_workspace_id::text
    );
    plan_sha := private.agent_json_sha256(plan_payload);
    stage_payload := private.harmony_preview_stage_receipt_payload(
        target_stage_receipt_id, target_workspace_id, target_client_id,
        target_round_id, target_plan_id, 'plan', 1::smallint, 'grok_bot',
        null, input_set_sha, plan_sha, created_time
    );
    stage_sha := stage_payload ->> 'receipt_sha256';
    round_body := pg_catalog.jsonb_build_object(
        'automatic_publication', false,
        'client_id', target_client_id,
        'external_calls', false,
        'input_set_sha256', input_set_sha,
        'plan_id', target_plan_id::text,
        'provider_calls', false,
        'publication_calls', false,
        'request_sha256', request_sha,
        'round_id', target_round_id::text,
        'schema_version', 'harmony-round-core@1',
        'signal_manifest', signal_manifest,
        'status', 'planned',
        'synthetic', true,
        'workspace_id', target_workspace_id::text
    );
    round_sha := private.agent_json_sha256(round_body);
    round_body := round_body || pg_catalog.jsonb_build_object(
        'round_sha256', round_sha
    );
    insert into agent_runtime.harmony_rounds (
        workspace_id, client_id, round_id, plan_id, input_set_sha256,
        request_sha256, signal_manifest, payload, round_sha256, status,
        synthetic, automatic_publication, created_at
    ) values (
        target_workspace_id, target_client_id, target_round_id, target_plan_id,
        input_set_sha, request_sha, signal_manifest, round_body, round_sha,
        'planned', true, false, created_time
    );
    insert into agent_runtime.harmony_plans (
        workspace_id, client_id, plan_id, round_id, payload,
        payload_sha256, state, created_at
    ) values (
        target_workspace_id, target_client_id, target_plan_id, target_round_id,
        plan_payload, plan_sha, 'planned', created_time
    );
    insert into agent_runtime.harmony_stage_receipts (
        workspace_id, client_id, receipt_id, round_id, plan_id, stage,
        ordinal, actor, principal_id, producer_release_sha, config_sha256,
        capability, binding_receipt_sha256, verdict, reviewer_principal_id,
        previous_receipt_sha256, input_sha256, output_sha256,
        artifact, artifact_sha256, payload, receipt_sha256, created_at
    ) values (
        target_workspace_id, target_client_id, target_stage_receipt_id,
        target_round_id, target_plan_id, 'plan', 1, 'grok_bot',
        (claims ->> 'producer_principal_id')::uuid,
        claims ->> 'release_sha', claims ->> 'config_sha256',
        claims ->> 'capability',
        stage_payload ->> 'binding_receipt_sha256', null, null, null,
        input_set_sha, plan_sha, plan_payload, plan_sha,
        stage_payload, stage_sha, created_time
    );
    return pg_catalog.jsonb_build_object(
        'ok', true, 'reused', false,
        'round_id', target_round_id, 'plan_id', target_plan_id,
        'stage_receipt', stage_payload,
        'external_calls', false, 'provider_calls', false,
        'publication_calls', false, 'automatic_publication', false
    );
end;
$$;

revoke all on function private.harmony_preview_stage_claims_match(
    uuid, text, text, text
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_stage_binding()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_stage_receipt_payload(
    uuid, uuid, text, uuid, uuid, text, smallint, text, text, text, text,
    timestamptz, text, uuid
) from public, anon, authenticated, service_role;
revoke all on function public.create_preview_harmony_squid_plan(
    uuid, text, uuid, uuid, uuid, text[], text
) from public, anon, authenticated, service_role;

commit;
