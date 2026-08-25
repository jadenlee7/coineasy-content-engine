-- Fixed-specialist binding for the disposable Squid Harmony Preview chain.
--
-- The roster is empty by default.  A database owner may seed exactly one
-- short-lived Preview-only specialist per stage after the existing exact
-- branch fence is active.  No dispatcher, provider, message, approval
-- decision, Buzz, or publication path is introduced here.

begin;

-- This additive migration deliberately has no backfill semantics.  It must be
-- applied before a disposable Preview rehearsal creates any Harmony rows.
do $fresh_preview$
begin
    if exists (select 1 from agent_runtime.harmony_signals)
       or exists (select 1 from agent_runtime.harmony_connector_attestation_receipts)
       or exists (select 1 from agent_runtime.harmony_rounds)
       or exists (select 1 from agent_runtime.harmony_plans)
       or exists (select 1 from agent_runtime.harmony_stage_receipts)
       or exists (select 1 from agent_runtime.harmony_operator_inbox)
       or exists (select 1 from private.harmony_preview_environment_fence)
    then
        raise exception 'harmony_preview_fixed_specialist_requires_empty_ledger';
    end if;
end
$fresh_preview$;

-- The approved disposable branch window is two hours.  JWTs may carry a
-- longer cryptographic expiry, but every accepted call remains bounded by this
-- immutable database fence and therefore loses authority within that window.
alter table private.harmony_preview_environment_fence
    add constraint harmony_preview_environment_fence_two_hour_check
    check (expires_at - created_at <= interval '2 hours');

create or replace function private.harmony_preview_specialist_binding_sha(
    target_branch_ref text,
    target_workspace_id uuid,
    target_client_id text,
    target_stage text,
    target_specialist_code text,
    target_role_name text,
    target_capability text,
    target_actor text,
    target_principal_id uuid,
    target_release_sha text,
    target_config_sha256 text,
    target_expires_at timestamptz
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'actor', target_actor,
        'branch_ref', target_branch_ref,
        'capability', target_capability,
        'client_id', target_client_id,
        'config_sha256', target_config_sha256,
        'expires_at', pg_catalog.to_char(
            target_expires_at at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'principal_id', target_principal_id::text,
        'producer_release_sha', target_release_sha,
        'role', target_role_name,
        'schema_version', 'harmony-fixed-specialist-binding@1',
        'specialist_code', target_specialist_code,
        'stage', target_stage,
        'workspace_id', target_workspace_id::text
    ))
$$;

create table private.harmony_preview_squid_specialist_bindings (
    branch_ref text not null,
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    stage text not null check (stage in (
        'plan', 'private_content', 'independent_qa', 'operator_inbox', 'recap'
    )),
    specialist_code text not null check (
        specialist_code ~ '^[a-z][a-z0-9_]{2,63}$'
    ),
    role_name text not null,
    capability text not null,
    actor text not null,
    principal_id uuid not null,
    producer_release_sha text not null check (
        producer_release_sha ~ '^[a-f0-9]{40}$'
    ),
    config_sha256 text not null check (config_sha256 ~ '^[a-f0-9]{64}$'),
    expires_at timestamptz not null,
    created_at timestamptz not null default statement_timestamp(),
    binding_sha256 text generated always as (
        private.harmony_preview_specialist_binding_sha(
            branch_ref, workspace_id, client_id, stage, specialist_code,
            role_name, capability, actor, principal_id,
            producer_release_sha, config_sha256, expires_at
        )
    ) stored,
    primary key (workspace_id, client_id, stage),
    unique (workspace_id, client_id, specialist_code),
    unique (workspace_id, client_id, principal_id),
    unique (workspace_id, client_id, binding_sha256),
    unique (
        workspace_id, client_id, stage, actor, principal_id,
        producer_release_sha, config_sha256, capability, binding_sha256
    ),
    foreign key (branch_ref)
        references private.harmony_preview_environment_fence(branch_ref)
        on delete restrict,
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    check (branch_ref ~ '^[a-z0-9]{20}$'),
    check (expires_at > created_at),
    check (expires_at - created_at <= interval '2 hours'),
    check ((
        stage, specialist_code, role_name, capability, actor
    ) in (
        ('plan', 'squid_planner', 'coineasy_harmony_orchestrator',
            'harmony_plan', 'grok_bot'),
        ('private_content', 'squid_private_content_producer',
            'coineasy_harmony_content',
            'harmony_prepare_private_content', 'content_engine'),
        ('independent_qa', 'squid_independent_qa',
            'coineasy_harmony_qa', 'harmony_independent_qa', 'codex'),
        ('operator_inbox', 'coineasy_representative_inbox',
            'coineasy_harmony_operator',
            'harmony_operator_inbox', 'human_operator_inbox'),
        ('recap', 'squid_recap', 'coineasy_harmony_recap',
            'harmony_recap', 'coineasy_recap')
    ))
);

alter table private.harmony_preview_squid_specialist_bindings
    enable row level security;
alter table private.harmony_preview_squid_specialist_bindings
    force row level security;
revoke all on table private.harmony_preview_squid_specialist_bindings
from public, anon, authenticated, service_role,
    coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_qa,
    coineasy_harmony_operator, coineasy_harmony_recap,
    coineasy_harmony_dashboard;
create trigger harmony_preview_squid_specialist_bindings_immutable
before update or delete
on private.harmony_preview_squid_specialist_bindings
for each row execute function private.agent_immutable_row();

alter table agent_runtime.harmony_stage_receipts
    add column specialist_binding_sha256 text,
    add column operation_key_sha256 text;
alter table agent_runtime.harmony_stage_receipts
    alter column specialist_binding_sha256 set not null,
    alter column operation_key_sha256 set not null;
alter table agent_runtime.harmony_stage_receipts
    add constraint harmony_stage_receipts_specialist_binding_sha_check
        check (specialist_binding_sha256 ~ '^[a-f0-9]{64}$'),
    add constraint harmony_stage_receipts_specialist_payload_check
        check (
            payload ->> 'specialist_binding_sha256'
                = specialist_binding_sha256
        ),
    add constraint harmony_stage_receipts_operation_key_sha_check
        check (operation_key_sha256 ~ '^[a-f0-9]{64}$'),
    add constraint harmony_stage_receipts_operation_payload_check
        check (payload ->> 'operation_key_sha256' = operation_key_sha256),
    add constraint harmony_stage_receipts_specialist_binding_fk
        foreign key (
            workspace_id, client_id, stage, actor, principal_id,
            producer_release_sha, config_sha256, capability,
            specialist_binding_sha256
        ) references private.harmony_preview_squid_specialist_bindings(
            workspace_id, client_id, stage, actor, principal_id,
            producer_release_sha, config_sha256, capability,
            binding_sha256
        ) on delete restrict;
create unique index harmony_stage_receipts_operation_key_idx
on agent_runtime.harmony_stage_receipts(
    workspace_id, client_id, operation_key_sha256
);

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
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       and exists (
            select 1
            from private.harmony_preview_squid_specialist_bindings specialist
            join private.harmony_preview_environment_fence fence
              on fence.branch_ref = specialist.branch_ref
             and fence.active
             and fence.expires_at > statement_timestamp()
            where specialist.workspace_id = target_workspace_id
              and specialist.client_id = target_client_id
              and specialist.role_name = target_role
              and specialist.capability = target_capability
              and specialist.principal_id
                    = (claims ->> 'producer_principal_id')::uuid
              and specialist.producer_release_sha = claims ->> 'release_sha'
              and specialist.config_sha256 = claims ->> 'config_sha256'
              and specialist.branch_ref = claims ->> 'ref'
              and specialist.expires_at > statement_timestamp()
              and specialist.expires_at <= fence.expires_at
       );
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
    specialist private.harmony_preview_squid_specialist_bindings%rowtype;
    safe_claims jsonb;
begin
    select candidate.* into strict specialist
    from private.harmony_preview_squid_specialist_bindings candidate
    join private.harmony_preview_environment_fence fence
      on fence.branch_ref = candidate.branch_ref
     and fence.active
     and fence.expires_at > statement_timestamp()
    where candidate.workspace_id = (claims ->> 'workspace_id')::uuid
      and candidate.client_id = claims ->> 'client_id'
      and candidate.role_name = claims ->> 'role'
      and candidate.capability = claims ->> 'capability'
      and candidate.principal_id
            = (claims ->> 'producer_principal_id')::uuid
      and candidate.producer_release_sha = claims ->> 'release_sha'
      and candidate.config_sha256 = claims ->> 'config_sha256'
      and candidate.branch_ref = claims ->> 'ref'
      and candidate.expires_at > statement_timestamp()
      and candidate.expires_at <= fence.expires_at;
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
        'specialist_binding_sha256', specialist.binding_sha256,
        'workspace_id', claims ->> 'workspace_id'
    );
    return pg_catalog.jsonb_build_object(
        'binding_receipt_sha256', private.agent_json_sha256(safe_claims),
        'capability', claims ->> 'capability',
        'config_sha256', claims ->> 'config_sha256',
        'principal_id', claims ->> 'producer_principal_id',
        'producer_release_sha', claims ->> 'release_sha',
        'specialist_binding_sha256', specialist.binding_sha256,
        'specialist_code', specialist.specialist_code
    );
exception
    when no_data_found then
        raise exception 'harmony_preview_fixed_specialist_not_bound';
end;
$$;

create or replace function private.harmony_preview_stage_operation_key(
    target_specialist_binding_sha256 text,
    target_workspace_id uuid,
    target_client_id text,
    target_plan_id uuid,
    target_stage text,
    target_input_sha256 text,
    target_output_sha256 text
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'client_id', target_client_id,
        'input_sha256', target_input_sha256,
        'output_sha256', target_output_sha256,
        'plan_id', target_plan_id::text,
        'schema_version', 'harmony-stage-operation@1',
        'specialist_binding_sha256', target_specialist_binding_sha256,
        'stage', target_stage,
        'workspace_id', target_workspace_id::text
    ))
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
    operation_key text;
begin
    operation_key := private.harmony_preview_stage_operation_key(
        binding ->> 'specialist_binding_sha256',
        target_workspace_id, target_client_id, target_plan_id, target_stage,
        target_input_sha256, target_output_sha256
    );
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
        'operation_key_sha256', operation_key,
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
        'specialist_binding_sha256', binding ->> 'specialist_binding_sha256',
        'specialist_code', binding ->> 'specialist_code',
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

create or replace function private.harmony_preview_bind_stage_specialist()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    binding jsonb := private.harmony_preview_stage_binding();
begin
    new.specialist_binding_sha256 :=
        binding ->> 'specialist_binding_sha256';
    new.operation_key_sha256 := private.harmony_preview_stage_operation_key(
        new.specialist_binding_sha256,
        new.workspace_id, new.client_id, new.plan_id, new.stage,
        new.input_sha256, new.output_sha256
    );
    if new.payload ->> 'specialist_binding_sha256'
            is distinct from new.specialist_binding_sha256
       or new.payload ->> 'operation_key_sha256'
            is distinct from new.operation_key_sha256 then
        raise exception 'harmony_preview_stage_specialist_payload_mismatch';
    end if;
    return new;
end;
$$;

create trigger harmony_stage_receipts_bind_fixed_specialist
before insert on agent_runtime.harmony_stage_receipts
for each row execute function private.harmony_preview_bind_stage_specialist();

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
    binding jsonb;
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
    binding := private.harmony_preview_stage_binding();
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
    -- Stable logical identity deliberately excludes the caller receipt UUID
    -- and short-lived JWT jti/iat/exp.  Those values remain attestation
    -- metadata on the first durable receipt, not retry identity.
    request_sha := private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'client_id', target_client_id,
        'input_set_sha256', input_set_sha,
        'plan_id', target_plan_id::text,
        'round_id', target_round_id::text,
        'schema_version', 'harmony-plan-operation@1',
        'specialist_binding_sha256',
            binding ->> 'specialist_binding_sha256',
        'stage', 'plan',
        'topic_code', target_topic_code,
        'workspace_id', target_workspace_id::text
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
           or existing_stage.round_id <> target_round_id
           or existing_stage.principal_id
                <> (claims ->> 'producer_principal_id')::uuid
           or existing_stage.producer_release_sha <> claims ->> 'release_sha'
           or existing_stage.config_sha256 <> claims ->> 'config_sha256'
           or existing_stage.capability <> claims ->> 'capability'
           or existing_stage.specialist_binding_sha256
                <> binding ->> 'specialist_binding_sha256'
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

-- Replaces the stage append implementation without changing its public
-- signature or grants.  Stage 4 now atomically creates the pending inbox;
-- stage 5 only verifies and recaps that exact immutable inbox.
create or replace function public.append_preview_harmony_squid_stage(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid,
    target_plan_id uuid,
    target_stage text,
    target_receipt_id uuid,
    target_inbox_id uuid default null,
    target_qa_evidence jsonb default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    binding jsonb;
    expected_role text;
    expected_capability text;
    stage_ordinal smallint;
    stage_actor text;
    round_row agent_runtime.harmony_rounds%rowtype;
    previous_row agent_runtime.harmony_stage_receipts%rowtype;
    existing agent_runtime.harmony_stage_receipts%rowtype;
    qa_row agent_runtime.harmony_stage_receipts%rowtype;
    source_signal agent_runtime.harmony_signals%rowtype;
    content_item public.content_items%rowtype;
    content_version public.content_versions%rowtype;
    inbox_row agent_runtime.harmony_operator_inbox%rowtype;
    created_time timestamptz;
    headline text;
    summary text;
    content_snapshot_sha text;
    artifact jsonb;
    artifact_sha text;
    stage_payload jsonb;
    stage_sha text;
    inbox_payload jsonb;
begin
    if target_client_id <> 'squid' or target_stage not in (
        'private_content', 'independent_qa', 'operator_inbox', 'recap'
    ) then
        raise exception 'harmony_preview_stage_scope_invalid';
    end if;
    select lane.role_name, lane.capability_name, lane.ordinal, lane.actor_name
    into expected_role, expected_capability, stage_ordinal, stage_actor
    from (values
        ('private_content', 'coineasy_harmony_content',
            'harmony_prepare_private_content', 2::smallint, 'content_engine'),
        ('independent_qa', 'coineasy_harmony_qa',
            'harmony_independent_qa', 3::smallint, 'codex'),
        ('operator_inbox', 'coineasy_harmony_operator',
            'harmony_operator_inbox', 4::smallint, 'human_operator_inbox'),
        ('recap', 'coineasy_harmony_recap',
            'harmony_recap', 5::smallint, 'coineasy_recap')
    ) lane(stage_name, role_name, capability_name, ordinal, actor_name)
    where lane.stage_name = target_stage;
    if not private.harmony_preview_stage_claims_match(
        target_workspace_id, target_client_id,
        expected_role, expected_capability
    ) or ((target_stage in ('operator_inbox', 'recap'))
            <> (target_inbox_id is not null))
      or ((target_stage = 'independent_qa')
            <> (target_qa_evidence is not null))
    then
        raise exception 'harmony_preview_stage_claim_invalid';
    end if;
    claims := nullif(
        pg_catalog.current_setting('request.jwt.claims', true), ''
    )::jsonb;
    binding := private.harmony_preview_stage_binding();
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_stage:' || target_workspace_id::text || ':' ||
        target_client_id || ':' || target_plan_id::text || ':' || target_stage,
        0
    ));
    select * into strict round_row
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.round_id = target_round_id
      and candidate.plan_id = target_plan_id
      and candidate.status = 'planned';
    if not private.harmony_preview_round_inputs_current(
        target_workspace_id, target_client_id, round_row.signal_manifest
    ) then
        raise exception 'harmony_preview_stage_input_expired_or_tampered';
    end if;
    select * into existing
    from agent_runtime.harmony_stage_receipts candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.plan_id = target_plan_id
      and candidate.stage = target_stage
    for update;
    if found then
        -- Caller receipt UUIDs and short-lived JWT jti/iat/exp values are
        -- transport metadata, not the logical operation identity.  A retry
        -- from the same fixed specialist release/config/ref returns the
        -- durable receipt already committed for the same logical payload.
        if existing.round_id <> target_round_id
           or existing.principal_id
                <> (claims ->> 'producer_principal_id')::uuid
           or existing.producer_release_sha <> claims ->> 'release_sha'
           or existing.config_sha256 <> claims ->> 'config_sha256'
           or existing.specialist_binding_sha256
                <> binding ->> 'specialist_binding_sha256'
           or (
                target_stage = 'independent_qa'
                and existing.artifact ->> 'evidence_sha256'
                    <> private.agent_json_sha256(target_qa_evidence)
           )
           or (
                target_stage in ('operator_inbox', 'recap')
                and (
                    existing.artifact ->> 'inbox_id'
                        is distinct from target_inbox_id::text
                    or not exists (
                    select 1
                    from agent_runtime.harmony_operator_inbox inbox
                    join agent_runtime.harmony_stage_receipts operator_stage
                      on operator_stage.workspace_id = inbox.workspace_id
                     and operator_stage.client_id = inbox.client_id
                     and operator_stage.receipt_id = inbox.stage_receipt_id
                     and operator_stage.plan_id = inbox.plan_id
                     and operator_stage.round_id = inbox.round_id
                     and operator_stage.stage = 'operator_inbox'
                     and operator_stage.output_sha256 = inbox.scope_sha256
                    where inbox.workspace_id = existing.workspace_id
                      and inbox.client_id = existing.client_id
                      and inbox.plan_id = existing.plan_id
                      and inbox.round_id = existing.round_id
                      and inbox.inbox_id = target_inbox_id
                      and inbox.status = 'pending'
                    )
                )
           ) then
            raise exception 'harmony_preview_stage_idempotency_conflict';
        end if;
        return pg_catalog.jsonb_build_object(
            'ok', true, 'reused', true, 'stage_receipt', existing.payload,
            'database_calls', true, 'external_calls', false,
            'provider_calls', false, 'publication_calls', false,
            'automatic_publication', false
        );
    end if;
    select * into strict previous_row
    from agent_runtime.harmony_stage_receipts candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.plan_id = target_plan_id
      and candidate.ordinal = stage_ordinal - 1;
    if exists (
        select 1
        from agent_runtime.harmony_stage_receipts later
        where later.workspace_id = target_workspace_id
          and later.client_id = target_client_id
          and later.plan_id = target_plan_id
          and later.ordinal >= stage_ordinal
    ) then
        raise exception 'harmony_preview_stage_order_invalid';
    end if;
    created_time := pg_catalog.date_trunc('second', statement_timestamp());

    if target_stage = 'private_content' then
        select signal.* into strict source_signal
        from agent_runtime.harmony_signals signal
        where signal.workspace_id = target_workspace_id
          and signal.client_id = target_client_id
          and signal.lane = 'content_source'
          and signal.payload_sha256 in (
              select value ->> 'signal_payload_sha256'
              from pg_catalog.jsonb_array_elements(round_row.signal_manifest)
          );
        if source_signal.official_source_binding_sha256 is null
           or source_signal.upstream_receipt_sha256
                <> source_signal.official_source_binding_sha256
           or private.harmony_preview_squid_official_source_binding(
                source_signal.payload
              ) is distinct from source_signal.official_source_binding_sha256
        then
            raise exception 'harmony_preview_private_source_stale';
        end if;
        select item.* into strict content_item
        from public.content_items item
        where item.workspace_id = target_workspace_id
          and item.client_id = target_client_id
          and item.current_version_id = source_signal.official_content_version_id
          and item.status = 'needs_review';
        select version.* into strict content_version
        from public.content_versions version
        where version.workspace_id = content_item.workspace_id
          and version.content_item_id = content_item.id
          and version.id = content_item.current_version_id;
        headline := pg_catalog.btrim(content_version.title);
        summary := pg_catalog.btrim(coalesce(
            content_version.content ->> 'summary_ko',
            content_version.content ->> 'summary',
            content_version.content ->> 'body_ko',
            content_version.content ->> 'body',
            content_version.title
        ));
        if not private.agent_safe_text(headline, 1, 480, true)
           or not private.agent_safe_text(summary, 1, 1800, false)
           or headline !~ '[가-힣]'
           or summary !~ '[가-힣]'
        then
            raise exception 'harmony_preview_private_content_unsafe';
        end if;
        content_snapshot_sha := private.agent_json_sha256(
            pg_catalog.jsonb_build_object(
                'channel_copy', content_version.channel_copy,
                'content', content_version.content,
                'deliverables', content_version.deliverables,
                'generation_meta', content_version.generation_meta,
                'qa', content_version.qa,
                'title', content_version.title
            )
        );
        artifact := pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'content_snapshot_sha256', content_snapshot_sha,
            'content_version_id', content_version.id::text,
            'headline_ko', headline,
            'private_content_only', true,
            'schema_version', 'harmony-private-content@1',
            'source_binding_sha256',
                source_signal.official_source_binding_sha256,
            'status', 'needs_review',
            'summary_ko', summary,
            'synthetic', true
        );
    elsif target_stage = 'independent_qa' then
        if (claims ->> 'producer_principal_id')::uuid in (
            select principal_id
            from agent_runtime.harmony_stage_receipts candidate
            where candidate.workspace_id = target_workspace_id
              and candidate.client_id = target_client_id
              and candidate.plan_id = target_plan_id
              and candidate.stage in ('plan', 'private_content')
        ) then
            raise exception 'harmony_preview_qa_self_review_forbidden';
        end if;
        if pg_catalog.jsonb_typeof(target_qa_evidence) <> 'object'
           or (select pg_catalog.count(*)
               from pg_catalog.jsonb_object_keys(target_qa_evidence)) <> 6
           or not target_qa_evidence ?& array[
                'schema_version', 'reviewed_output_sha256', 'criteria',
                'findings', 'verdict', 'verifier_version'
           ]
           or target_qa_evidence ->> 'schema_version'
                <> 'harmony-independent-qa-evidence@1'
           or target_qa_evidence ->> 'reviewed_output_sha256'
                <> previous_row.output_sha256
           or target_qa_evidence ->> 'verdict' <> 'passed'
           or target_qa_evidence ->> 'verifier_version'
                <> 'harmony-deterministic-qa@1'
           or target_qa_evidence -> 'findings' <> '[]'::jsonb
           or pg_catalog.jsonb_typeof(target_qa_evidence -> 'criteria')
                <> 'object'
           or (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(
                target_qa_evidence -> 'criteria'
              )) <> 4
           or target_qa_evidence -> 'criteria'
                <> pg_catalog.jsonb_build_object(
                    'automatic_publication', false,
                    'factual_binding', true,
                    'no_external_calls', true,
                    'private_only', true
                )
        then
            raise exception 'harmony_preview_qa_evidence_invalid';
        end if;
        artifact := pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'criteria_sha256', private.agent_json_sha256(
                target_qa_evidence -> 'criteria'
            ),
            'evidence_sha256', private.agent_json_sha256(target_qa_evidence),
            'reviewed_output_sha256', previous_row.output_sha256,
            'reviewer_principal_id', claims ->> 'producer_principal_id',
            'schema_version', 'harmony-independent-qa@1',
            'synthetic', true,
            'verdict', 'passed',
            'verifier_version', 'harmony-deterministic-qa@1'
        );
    elsif target_stage = 'operator_inbox' then
        if previous_row.stage <> 'independent_qa'
           or previous_row.verdict <> 'passed'
           or previous_row.reviewer_principal_id <> previous_row.principal_id
        then
            raise exception 'harmony_preview_qa_receipt_invalid';
        end if;
        qa_row := previous_row;
        artifact := pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'inbox_id', target_inbox_id::text,
            'operator_decision_recorded', false,
            'qa_output_sha256', qa_row.output_sha256,
            'qa_receipt_id', qa_row.receipt_id::text,
            'qa_receipt_sha256', qa_row.receipt_sha256,
            'schema_version', 'harmony-operator-scope@1',
            'status', 'pending',
            'synthetic', true
        );
    else
        select candidate.* into strict qa_row
        from agent_runtime.harmony_stage_receipts candidate
        where candidate.workspace_id = target_workspace_id
          and candidate.client_id = target_client_id
          and candidate.plan_id = target_plan_id
          and candidate.stage = 'independent_qa'
          and candidate.verdict = 'passed'
          and candidate.reviewer_principal_id = candidate.principal_id;
        select inbox.* into strict inbox_row
        from agent_runtime.harmony_operator_inbox inbox
        where inbox.workspace_id = target_workspace_id
          and inbox.client_id = target_client_id
          and inbox.round_id = target_round_id
          and inbox.plan_id = target_plan_id
          and inbox.inbox_id = target_inbox_id
          and inbox.stage_receipt_id = previous_row.receipt_id
          and inbox.scope_sha256 = previous_row.output_sha256
          and inbox.qa_receipt_id = qa_row.receipt_id
          and inbox.qa_receipt_sha256 = qa_row.receipt_sha256
          and inbox.qa_output_sha256 = qa_row.output_sha256
          and inbox.status = 'pending'
          and inbox.payload -> 'operator_decision_recorded' = 'false'::jsonb;
        if previous_row.stage <> 'operator_inbox'
           or previous_row.artifact ->> 'qa_receipt_id'
                <> qa_row.receipt_id::text
           or previous_row.artifact ->> 'qa_receipt_sha256'
                <> qa_row.receipt_sha256
           or previous_row.artifact ->> 'qa_output_sha256'
                <> qa_row.output_sha256
        then
            raise exception 'harmony_preview_operator_scope_invalid';
        end if;
        artifact := pg_catalog.jsonb_build_object(
            'actual_cost_microusd', 0,
            'automatic_publication', false,
            'inbox_id', inbox_row.inbox_id::text,
            'operator_decision_observed', false,
            'operator_inbox_receipt_sha256', previous_row.receipt_sha256,
            'publication_count', 0,
            'schema_version', 'harmony-recap@1',
            'stage_receipt_count', 5,
            'synthetic', true
        );
    end if;
    artifact_sha := private.agent_json_sha256(artifact);
    stage_payload := private.harmony_preview_stage_receipt_payload(
        target_receipt_id, target_workspace_id, target_client_id,
        target_round_id, target_plan_id, target_stage, stage_ordinal,
        stage_actor, previous_row.receipt_sha256,
        previous_row.output_sha256, artifact_sha, created_time,
        case when target_stage = 'independent_qa' then 'passed' end,
        case when target_stage = 'independent_qa'
            then (claims ->> 'producer_principal_id')::uuid end
    );
    stage_sha := stage_payload ->> 'receipt_sha256';
    insert into agent_runtime.harmony_stage_receipts (
        workspace_id, client_id, receipt_id, round_id, plan_id, stage,
        ordinal, actor, principal_id, producer_release_sha, config_sha256,
        capability, binding_receipt_sha256, verdict, reviewer_principal_id,
        previous_receipt_sha256, input_sha256, output_sha256,
        artifact, artifact_sha256, payload, receipt_sha256, created_at
    ) values (
        target_workspace_id, target_client_id, target_receipt_id,
        target_round_id, target_plan_id, target_stage, stage_ordinal,
        stage_actor, (claims ->> 'producer_principal_id')::uuid,
        claims ->> 'release_sha', claims ->> 'config_sha256',
        expected_capability, binding ->> 'binding_receipt_sha256',
        case when target_stage = 'independent_qa' then 'passed' end,
        case when target_stage = 'independent_qa'
            then (claims ->> 'producer_principal_id')::uuid end,
        previous_row.receipt_sha256, previous_row.output_sha256, artifact_sha,
        artifact, artifact_sha, stage_payload, stage_sha, created_time
    );
    if target_stage = 'operator_inbox' then
        inbox_payload := pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'client_id', target_client_id,
            'created_at', pg_catalog.to_char(
                created_time at time zone 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS"Z"'
            ),
            'external_delivery_attempted', false,
            'inbox_id', target_inbox_id::text,
            'operator_decision_recorded', false,
            'plan_id', target_plan_id::text,
            'qa_output_sha256', qa_row.output_sha256,
            'qa_receipt_id', qa_row.receipt_id::text,
            'qa_receipt_sha256', qa_row.receipt_sha256,
            'round_id', target_round_id::text,
            'schema_version', 'harmony-operator-inbox@1',
            'scope_sha256', artifact_sha,
            'stage_receipt_id', target_receipt_id::text,
            'status', 'pending',
            'workspace_id', target_workspace_id::text
        );
        insert into agent_runtime.harmony_operator_inbox (
            workspace_id, client_id, inbox_id, round_id, plan_id,
            stage_receipt_id, scope_sha256, qa_receipt_id,
            qa_receipt_sha256, qa_output_sha256, payload, status, created_at
        ) values (
            target_workspace_id, target_client_id, target_inbox_id,
            target_round_id, target_plan_id, target_receipt_id, artifact_sha,
            qa_row.receipt_id, qa_row.receipt_sha256, qa_row.output_sha256,
            inbox_payload, 'pending', created_time
        );
    end if;
    return pg_catalog.jsonb_build_object(
        'ok', true, 'reused', false, 'stage_receipt', stage_payload,
        'database_calls', true, 'external_calls', false,
        'provider_calls', false, 'publication_calls', false,
        'automatic_publication', false
    );
exception
    when no_data_found then
        raise exception 'harmony_preview_stage_dependency_missing';
end;
$$;

-- A representative can inspect the pending inbox after stage 4.  Recap is an
-- optional fifth immutable receipt and never gates visibility of the inbox.
create or replace function private.harmony_preview_collaboration_object(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    round_row agent_runtime.harmony_rounds%rowtype;
    connector_payloads jsonb;
    stage_payloads jsonb;
    stage_count integer;
    inbox_payload jsonb;
    body jsonb;
begin
    select * into strict round_row
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.round_id = target_round_id;
    if not private.harmony_preview_round_inputs_current(
        round_row.workspace_id,
        round_row.client_id,
        round_row.signal_manifest
    ) then
        return null;
    end if;
    select pg_catalog.jsonb_agg(receipt.payload order by signal.lane)
    into strict connector_payloads
    from agent_runtime.harmony_signals signal
    join agent_runtime.harmony_connector_attestation_receipts receipt
      on receipt.workspace_id = signal.workspace_id
     and receipt.client_id = signal.client_id
     and receipt.receipt_id = signal.connector_receipt_id
     and receipt.payload_sha256 = signal.connector_receipt_sha256
    where signal.workspace_id = round_row.workspace_id
      and signal.client_id = round_row.client_id
      and signal.payload_sha256 in (
          select value ->> 'signal_payload_sha256'
          from pg_catalog.jsonb_array_elements(round_row.signal_manifest)
      )
    having pg_catalog.count(*) = 4;
    select pg_catalog.jsonb_agg(receipt.payload order by receipt.ordinal),
           pg_catalog.count(*)::integer
    into strict stage_payloads, stage_count
    from agent_runtime.harmony_stage_receipts receipt
    where receipt.workspace_id = round_row.workspace_id
      and receipt.client_id = round_row.client_id
      and receipt.plan_id = round_row.plan_id
    having pg_catalog.count(*) between 4 and 5
       and pg_catalog.bool_and(
            receipt.input_sha256 = case receipt.ordinal
                when 1 then round_row.input_set_sha256
                else (
                    select previous.output_sha256
                    from agent_runtime.harmony_stage_receipts previous
                    where previous.workspace_id = receipt.workspace_id
                      and previous.client_id = receipt.client_id
                      and previous.plan_id = receipt.plan_id
                      and previous.ordinal = receipt.ordinal - 1
                )
            end
       )
       and pg_catalog.bool_and(
            receipt.previous_receipt_sha256 is not distinct from
            case receipt.ordinal
                when 1 then null
                else (
                    select previous.receipt_sha256
                    from agent_runtime.harmony_stage_receipts previous
                    where previous.workspace_id = receipt.workspace_id
                      and previous.client_id = receipt.client_id
                      and previous.plan_id = receipt.plan_id
                      and previous.ordinal = receipt.ordinal - 1
                )
            end
       );
    select inbox.payload into strict inbox_payload
    from agent_runtime.harmony_operator_inbox inbox
    join agent_runtime.harmony_stage_receipts operator_stage
      on operator_stage.workspace_id = inbox.workspace_id
     and operator_stage.client_id = inbox.client_id
     and operator_stage.receipt_id = inbox.stage_receipt_id
     and operator_stage.output_sha256 = inbox.scope_sha256
     and operator_stage.stage = 'operator_inbox'
    join agent_runtime.harmony_stage_receipts qa_stage
      on qa_stage.workspace_id = inbox.workspace_id
     and qa_stage.client_id = inbox.client_id
     and qa_stage.receipt_id = inbox.qa_receipt_id
     and qa_stage.receipt_sha256 = inbox.qa_receipt_sha256
     and qa_stage.output_sha256 = inbox.qa_output_sha256
     and qa_stage.stage = 'independent_qa'
     and qa_stage.verdict = 'passed'
     and qa_stage.reviewer_principal_id = qa_stage.principal_id
    where inbox.workspace_id = round_row.workspace_id
      and inbox.client_id = round_row.client_id
      and inbox.plan_id = round_row.plan_id;
    body := pg_catalog.jsonb_build_object(
        'actual_cost_microusd', 0,
        'aggregate_only', true,
        'automatic_publication', false,
        'client_id', round_row.client_id,
        'connector_receipts', connector_payloads,
        'external_calls', false,
        'input_set_sha256', round_row.input_set_sha256,
        'operator_decision_recorded', false,
        'operator_inbox', inbox_payload,
        'plan_id', round_row.plan_id::text,
        'private_content_only', true,
        'provider_calls', false,
        'publication_calls', false,
        'round_id', round_row.round_id::text,
        'schema_version', 'harmony-collaboration-round@1',
        'signal_manifest', round_row.signal_manifest,
        'stage_receipts', stage_payloads,
        'stage_receipt_count', stage_count,
        'status', 'operator_review_pending',
        'synthetic', true,
        'workspace_id', round_row.workspace_id::text
    );
    return body || pg_catalog.jsonb_build_object(
        'round_sha256', private.agent_json_sha256(body)
    );
exception
    when no_data_found then
        return null;
end;
$$;

-- Dashboard v2 is an exact, bounded projection of the immutable stage chain.
-- Stage 4 is already representative-visible; recap hashes remain JSON null
-- until the optional stage 5 receipt exists for that same inbox.
create or replace function public.get_preview_harmony_dashboard(
    target_workspace_id uuid,
    target_client_id text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    latest_round agent_runtime.harmony_rounds%rowtype;
    latest_collaboration jsonb;
    latest_payload jsonb;
    inbox_payload jsonb;
    counter jsonb;
begin
    if target_client_id <> 'squid'
       or not private.harmony_preview_scope_matches(
            target_workspace_id, target_client_id,
            array['coineasy_harmony_dashboard']::text[]
       )
    then
        raise exception 'harmony_preview_dashboard_scope_invalid';
    end if;
    select candidate.* into latest_round
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and private.harmony_preview_collaboration_object(
            candidate.workspace_id, candidate.client_id, candidate.round_id
          ) is not null
    order by candidate.created_at desc, candidate.round_id desc
    limit 1;
    if found then
        latest_collaboration := private.harmony_preview_collaboration_object(
            latest_round.workspace_id,
            latest_round.client_id,
            latest_round.round_id
        );
        select pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'headline_ko', pg_catalog.left(
                content_stage.artifact ->> 'headline_ko', 160
            ),
            'input_set_sha256', latest_round.input_set_sha256,
            'plan_id', latest_round.plan_id::text,
            'recap', (
                select pg_catalog.jsonb_build_object(
                    'actual_cost_microusd',
                        (recap_stage.artifact ->> 'actual_cost_microusd')::bigint,
                    'automatic_publication', false,
                    'input_sha256', recap_stage.input_sha256,
                    'operator_decision_observed', false,
                    'output_sha256', recap_stage.output_sha256,
                    'publication_count', 0,
                    'receipt_sha256', recap_stage.receipt_sha256,
                    'schema_version', 'harmony-dashboard-recap@1',
                    'stage_receipt_count', 5,
                    'synthetic', true
                )
                from agent_runtime.harmony_stage_receipts recap_stage
                where recap_stage.workspace_id = latest_round.workspace_id
                  and recap_stage.client_id = latest_round.client_id
                  and recap_stage.plan_id = latest_round.plan_id
                  and recap_stage.stage = 'recap'
            ),
            'round_id', latest_round.round_id::text,
            'round_sha256', latest_collaboration ->> 'round_sha256',
            'schema_version', 'harmony-dashboard-round@2',
            'stages', (
                select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                    'actor', stage.actor,
                    'capability', stage.capability,
                    'config_sha256', stage.config_sha256,
                    'input_sha256', stage.input_sha256,
                    'operation_key_sha256', stage.operation_key_sha256,
                    'ordinal', stage.ordinal,
                    'output_sha256', stage.output_sha256,
                    'principal_id', stage.principal_id::text,
                    'producer_release_sha', stage.producer_release_sha,
                    'receipt_sha256', stage.receipt_sha256,
                    'recorded_at', pg_catalog.to_char(
                        stage.created_at at time zone 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS"Z"'
                    ),
                    'specialist_binding_sha256',
                        stage.specialist_binding_sha256,
                    'specialist_code', specialist.specialist_code,
                    'stage', stage.stage,
                    'verdict', stage.verdict
                ) order by stage.ordinal)
                from agent_runtime.harmony_stage_receipts stage
                join private.harmony_preview_squid_specialist_bindings specialist
                  on specialist.workspace_id = stage.workspace_id
                 and specialist.client_id = stage.client_id
                 and specialist.stage = stage.stage
                 and specialist.binding_sha256
                        = stage.specialist_binding_sha256
                where stage.workspace_id = latest_round.workspace_id
                  and stage.client_id = latest_round.client_id
                  and stage.plan_id = latest_round.plan_id
            ),
            'status', 'operator_review_pending',
            'summary_ko', pg_catalog.left(
                content_stage.artifact ->> 'summary_ko', 600
            )
        ) into latest_payload
        from agent_runtime.harmony_stage_receipts content_stage
        where content_stage.workspace_id = latest_round.workspace_id
          and content_stage.client_id = latest_round.client_id
          and content_stage.plan_id = latest_round.plan_id
          and content_stage.stage = 'private_content';
    end if;

    select coalesce(
        pg_catalog.jsonb_agg(
            item.dashboard order by item.created_at desc, item.inbox_id desc
        ),
        '[]'::jsonb
    ) into inbox_payload
    from (
        select inbox.created_at, inbox.inbox_id,
               pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'created_at', pg_catalog.to_char(
                inbox.created_at at time zone 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS"Z"'
            ),
            'headline_ko', pg_catalog.left(
                content_stage.artifact ->> 'headline_ko', 160
            ),
            'inbox_id', inbox.inbox_id::text,
            'operator_decision_recorded', false,
            'plan_id', inbox.plan_id::text,
            'qa_output_sha256', inbox.qa_output_sha256,
            'qa_receipt_id', inbox.qa_receipt_id::text,
            'qa_receipt_sha256', inbox.qa_receipt_sha256,
            'recap_output_sha256', recap_stage.output_sha256,
            'recap_receipt_sha256', recap_stage.receipt_sha256,
            'round_id', inbox.round_id::text,
            'round_sha256', collaboration.value ->> 'round_sha256',
            'schema_version', 'harmony-dashboard-inbox@2',
            'scope_sha256', inbox.scope_sha256,
            'status', inbox.status,
            'summary_ko', pg_catalog.left(
                content_stage.artifact ->> 'summary_ko', 600
            )
        ) as dashboard
        from agent_runtime.harmony_operator_inbox inbox
        join agent_runtime.harmony_stage_receipts operator_stage
          on operator_stage.workspace_id = inbox.workspace_id
         and operator_stage.client_id = inbox.client_id
         and operator_stage.round_id = inbox.round_id
         and operator_stage.plan_id = inbox.plan_id
         and operator_stage.receipt_id = inbox.stage_receipt_id
         and operator_stage.stage = 'operator_inbox'
         and operator_stage.output_sha256 = inbox.scope_sha256
        join agent_runtime.harmony_stage_receipts content_stage
          on content_stage.workspace_id = inbox.workspace_id
         and content_stage.client_id = inbox.client_id
         and content_stage.plan_id = inbox.plan_id
         and content_stage.stage = 'private_content'
        left join agent_runtime.harmony_stage_receipts recap_stage
          on recap_stage.workspace_id = inbox.workspace_id
         and recap_stage.client_id = inbox.client_id
         and recap_stage.plan_id = inbox.plan_id
         and recap_stage.stage = 'recap'
         and recap_stage.artifact ->> 'inbox_id' = inbox.inbox_id::text
        cross join lateral (
            select private.harmony_preview_collaboration_object(
                inbox.workspace_id, inbox.client_id, inbox.round_id
            ) as value
        ) collaboration
        where inbox.workspace_id = target_workspace_id
          and inbox.client_id = target_client_id
          and inbox.status = 'pending'
          and collaboration.value is not null
        order by inbox.created_at desc, inbox.inbox_id desc
        limit 25
    ) item;

    with current_rounds as materialized (
        select candidate.workspace_id, candidate.client_id,
               candidate.round_id, candidate.plan_id,
               candidate.signal_manifest
        from agent_runtime.harmony_rounds candidate
        where candidate.workspace_id = target_workspace_id
          and candidate.client_id = target_client_id
          and private.harmony_preview_collaboration_object(
                candidate.workspace_id, candidate.client_id,
                candidate.round_id
              ) is not null
    ), current_signals as materialized (
        select distinct signal.workspace_id, signal.client_id,
               signal.signal_id, signal.connector_receipt_id
        from current_rounds round_value
        cross join lateral pg_catalog.jsonb_array_elements(
            round_value.signal_manifest
        ) entry(value)
        join agent_runtime.harmony_signals signal
          on signal.workspace_id = round_value.workspace_id
         and signal.client_id = round_value.client_id
         and signal.signal_id = (entry.value ->> 'signal_id')::uuid
         and signal.payload_sha256
                = entry.value ->> 'signal_payload_sha256'
    )
    select pg_catalog.jsonb_build_object(
        'connector_receipts', (
            select pg_catalog.count(distinct connector_receipt_id)
            from current_signals
        ),
        'pending_operator_inbox', (
            select pg_catalog.count(*)
            from agent_runtime.harmony_operator_inbox row_value
            join current_rounds round_value
              on round_value.workspace_id = row_value.workspace_id
             and round_value.client_id = row_value.client_id
             and round_value.round_id = row_value.round_id
             and round_value.plan_id = row_value.plan_id
            where row_value.status = 'pending'
        ),
        'plans', (select pg_catalog.count(*) from current_rounds),
        'rounds', (select pg_catalog.count(*) from current_rounds),
        'signals', (select pg_catalog.count(*) from current_signals),
        'stage_receipts', (
            select pg_catalog.count(*)
            from agent_runtime.harmony_stage_receipts row_value
            join current_rounds round_value
              on round_value.workspace_id = row_value.workspace_id
             and round_value.client_id = row_value.client_id
             and round_value.round_id = row_value.round_id
             and round_value.plan_id = row_value.plan_id
        )
    ) into counter;
    return pg_catalog.jsonb_build_object(
        'client_id', target_client_id,
        'counts', counter,
        'flags', pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'external_calls', false,
            'provider_calls', false,
            'publication_calls', false,
            'read_only', true
        ),
        'latest_round', latest_payload,
        'observed_at', pg_catalog.to_char(
            pg_catalog.date_trunc('second', statement_timestamp())
                at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'operator_inbox', inbox_payload,
        'schema_version', 'harmony-preview-dashboard@2',
        'trust', pg_catalog.jsonb_build_object(
            'client_scope_verified', true,
            'environment', 'preview',
            'portable_trust', false
        ),
        'workspace_id', target_workspace_id::text
    );
end;
$$;

revoke all on function private.harmony_preview_specialist_binding_sha(
    text, uuid, text, text, text, text, text, text, uuid, text, text,
    timestamptz
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_stage_operation_key(
    text, uuid, text, uuid, text, text, text
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_stage_claims_match(
    uuid, text, text, text
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_stage_binding()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_stage_receipt_payload(
    uuid, uuid, text, uuid, uuid, text, smallint, text, text, text, text,
    timestamptz, text, uuid
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_bind_stage_specialist()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_collaboration_object(
    uuid, text, uuid
) from public, anon, authenticated, service_role;
revoke all on function public.append_preview_harmony_squid_stage(
    uuid, text, uuid, uuid, text, uuid, uuid, jsonb
) from public, anon, authenticated, service_role;
revoke all on function public.create_preview_harmony_squid_plan(
    uuid, text, uuid, uuid, uuid, text[], text
) from public, anon, authenticated, service_role;
revoke all on function public.get_preview_harmony_dashboard(uuid, text)
from public, anon, authenticated, service_role;

commit;
