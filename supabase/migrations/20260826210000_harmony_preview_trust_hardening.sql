-- Preview-only revocable connector trust and durable independent-QA denials.
--
-- This is an additive, no-backfill migration.  It deliberately exposes no
-- Production adapter, provider call, message delivery, approval mutation,
-- publication routine, or automatic-publication path.

begin;

-- Re-establish the shared Preview scope gate with an explicit epoch domain.
-- bigint values outside PostgreSQL's operational JWT window must become a
-- typed false result rather than leaking arithmetic/timestamp SQLSTATEs.
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
    if issued_epoch is null
       or expires_epoch is null
       or issued_epoch < 0
       or issued_epoch > 4102444800
       or expires_epoch < 0
       or expires_epoch > 4102444800
    then
        return false;
    end if;
    begin
        return coalesce((
            coalesce(claims ->> 'role', '') = any(target_roles)
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
           and claims -> 'automatic_publication'
                is not distinct from 'false'::jsonb
           and claims -> 'max_cost_microusd' is not distinct from '0'::jsonb
           and claims -> 'max_external_actions' is not distinct from '0'::jsonb
           and issued_epoch <= extract(epoch from statement_timestamp()) + 60
           and expires_epoch > extract(epoch from statement_timestamp())
           and expires_epoch - issued_epoch between 1 and 2678400
        ), false);
    exception when others then
        return false;
    end;
end;
$$;

-- Fixed-specialist authorization must never return SQL NULL.  Every caller
-- uses IF NOT(...), so a missing subject has to become false explicitly.
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
    begin
        return coalesce((
            private.harmony_preview_scope_matches(
                target_workspace_id,
                target_client_id,
                array[target_role]::text[]
            )
            and claims ->> 'capability' = target_capability
            and coalesce(claims ->> 'producer_principal_id', '')
                ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            and coalesce(claims ->> 'sub', '')
                = claims ->> 'producer_principal_id'
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
            )
        ), false);
    exception when others then
        return false;
    end;
end;
$$;

do $fresh_preview$
begin
    if exists (select 1 from agent_runtime.harmony_connector_attestation_receipts)
       or exists (select 1 from agent_runtime.harmony_signals)
       or exists (select 1 from agent_runtime.harmony_rounds)
       or exists (select 1 from agent_runtime.harmony_plans)
       or exists (select 1 from agent_runtime.harmony_stage_receipts)
       or exists (select 1 from agent_runtime.harmony_operator_inbox)
       or exists (select 1 from private.harmony_preview_environment_fence)
       or exists (select 1 from private.harmony_preview_squid_specialist_bindings)
    then
        raise exception 'harmony_preview_trust_hardening_requires_empty_ledger';
    end if;
end
$fresh_preview$;

create or replace function private.harmony_preview_connector_registration_sha256(
    target_branch_ref text,
    target_workspace_id uuid,
    target_client_id text,
    target_registration_id uuid,
    target_lane text,
    target_capability text,
    target_connector_id text,
    target_producer_principal_id uuid,
    target_producer_release_sha text,
    target_config_sha256 text,
    target_attestation_key_id text,
    target_expires_at timestamptz
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'attestation_key_id', target_attestation_key_id,
        'branch_ref', target_branch_ref,
        'capability', target_capability,
        'client_id', target_client_id,
        'config_sha256', target_config_sha256,
        'connector_id', target_connector_id,
        'expires_at', pg_catalog.to_char(
            target_expires_at at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'lane', target_lane,
        'producer_principal_id', target_producer_principal_id::text,
        'producer_release_sha', target_producer_release_sha,
        'registration_id', target_registration_id::text,
        'schema_version', 'harmony-connector-registration@1',
        'workspace_id', target_workspace_id::text
    ))
$$;

create table private.harmony_preview_connector_registrations (
    branch_ref text not null,
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    registration_id uuid not null,
    lane text not null check (
        lane in ('quiz_bot', 'community_ops', 'content_source', 'recap')
    ),
    capability text not null,
    connector_id text not null check (
        connector_id ~ '^[a-z][a-z0-9_:-]{2,63}$'
    ),
    producer_principal_id uuid not null,
    producer_release_sha text not null check (
        producer_release_sha ~ '^[a-f0-9]{40}$'
    ),
    config_sha256 text not null check (config_sha256 ~ '^[a-f0-9]{64}$'),
    attestation_key_id text not null check (
        attestation_key_id ~ '^[a-z][a-z0-9._:-]{2,127}$'
    ),
    expires_at timestamptz not null,
    created_at timestamptz not null default
        pg_catalog.date_trunc('second', statement_timestamp()),
    registration_sha256 text generated always as (
        private.harmony_preview_connector_registration_sha256(
            branch_ref, workspace_id, client_id, registration_id, lane,
            capability, connector_id, producer_principal_id,
            producer_release_sha, config_sha256, attestation_key_id,
            expires_at
        )
    ) stored,
    primary key (workspace_id, client_id, registration_id),
    constraint harmony_connector_registration_lane_once unique (
        branch_ref, workspace_id, client_id, lane
    ),
    constraint harmony_connector_registration_connector_once unique (
        branch_ref, workspace_id, client_id, connector_id
    ),
    constraint harmony_connector_registration_principal_once unique (
        branch_ref, workspace_id, client_id, producer_principal_id
    ),
    constraint harmony_connector_registration_key_once unique (
        branch_ref, workspace_id, client_id, attestation_key_id
    ),
    unique (workspace_id, client_id, registration_sha256),
    unique (
        workspace_id, client_id, registration_id, registration_sha256
    ),
    foreign key (branch_ref)
        references private.harmony_preview_environment_fence(branch_ref)
        on delete restrict,
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    check (branch_ref ~ '^[a-z0-9]{20}$'),
    check (created_at = pg_catalog.date_trunc('second', created_at)),
    check (expires_at > created_at),
    check (expires_at - created_at <= interval '2 hours'),
    check ((lane, capability) in (
        ('quiz_bot', 'harmony_submit_quiz_bot'),
        ('community_ops', 'harmony_submit_community_ops'),
        ('content_source', 'harmony_submit_content_source'),
        ('recap', 'harmony_submit_recap')
    ))
);

create or replace function private.harmony_preview_validate_connector_registration()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    fence private.harmony_preview_environment_fence%rowtype;
begin
    select candidate.* into strict fence
    from private.harmony_preview_environment_fence candidate
    where candidate.branch_ref = new.branch_ref
      and candidate.active
      and candidate.expires_at > statement_timestamp()
    for share;
    if new.created_at < pg_catalog.date_trunc('second', fence.created_at)
       or new.created_at > statement_timestamp() + interval '1 second'
       or new.expires_at > fence.expires_at
    then
        raise exception 'harmony_preview_connector_registration_not_current';
    end if;
    return new;
exception
    when no_data_found then
        raise exception 'harmony_preview_connector_registration_not_current';
end;
$$;

create trigger harmony_preview_connector_registration_validate
before insert on private.harmony_preview_connector_registrations
for each row execute function
    private.harmony_preview_validate_connector_registration();

create or replace function private.harmony_preview_lock_connector_registration(
    target_workspace_id uuid,
    target_client_id text,
    target_registration_id uuid
)
returns private.harmony_preview_connector_registrations
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    registration private.harmony_preview_connector_registrations%rowtype;
begin
    select candidate.* into strict registration
    from private.harmony_preview_connector_registrations candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.registration_id = target_registration_id
    for update;
    return registration;
exception
    when no_data_found then
        raise exception 'harmony_preview_connector_registration_not_found';
end;
$$;

create or replace function private.harmony_preview_connector_revocation_sha256(
    target_workspace_id uuid,
    target_client_id text,
    target_revocation_id uuid,
    target_registration_id uuid,
    target_registration_sha256 text,
    target_reason_code text,
    target_revoked_at timestamptz
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'client_id', target_client_id,
        'reason_code', target_reason_code,
        'registration_id', target_registration_id::text,
        'registration_sha256', target_registration_sha256,
        'revocation_id', target_revocation_id::text,
        'revoked_at', pg_catalog.to_char(
            target_revoked_at at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'schema_version', 'harmony-connector-revocation@1',
        'workspace_id', target_workspace_id::text
    ))
$$;

create table private.harmony_preview_connector_registration_revocations (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    revocation_id uuid not null,
    registration_id uuid not null,
    registration_sha256 text not null check (
        registration_sha256 ~ '^[a-f0-9]{64}$'
    ),
    reason_code text not null check (reason_code in (
        'connector_disabled', 'credential_exposure', 'key_compromise',
        'preview_cleanup', 'signing_key_rotation'
    )),
    revoked_at timestamptz not null default statement_timestamp(),
    revocation_sha256 text generated always as (
        private.harmony_preview_connector_revocation_sha256(
            workspace_id, client_id, revocation_id, registration_id,
            registration_sha256, reason_code, revoked_at
        )
    ) stored,
    primary key (workspace_id, client_id, revocation_id),
    unique (workspace_id, client_id, registration_id),
    unique (workspace_id, client_id, revocation_sha256),
    foreign key (
        workspace_id, client_id, registration_id, registration_sha256
    ) references private.harmony_preview_connector_registrations(
        workspace_id, client_id, registration_id, registration_sha256
    ) on delete restrict
);

create or replace function private.harmony_preview_linearize_connector_revocation()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    registration private.harmony_preview_connector_registrations%rowtype;
begin
    registration := private.harmony_preview_lock_connector_registration(
        new.workspace_id, new.client_id, new.registration_id
    );
    if new.registration_sha256 is distinct from registration.registration_sha256
       or new.revoked_at < registration.created_at
       or new.revoked_at > statement_timestamp() + interval '1 second'
    then
        raise exception 'harmony_preview_connector_revocation_invalid';
    end if;
    return new;
end;
$$;

create trigger harmony_preview_connector_revocation_linearize
before insert on private.harmony_preview_connector_registration_revocations
for each row execute function
    private.harmony_preview_linearize_connector_revocation();

create or replace function private.harmony_preview_connector_request_sha256(
    target_workspace_id uuid,
    target_client_id text,
    target_registration_id uuid,
    target_receipt_id uuid,
    target_signal jsonb
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'client_id', target_client_id,
        'connector_receipt_id', target_receipt_id::text,
        'domain', 'coineasy:harmony:preview:connector-request:v1',
        'lane', target_signal ->> 'lane',
        'producer_principal_id', target_signal ->> 'producer_principal_id',
        'registration_id', target_registration_id::text,
        'rpc', 'public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)',
        'signal_id', target_signal ->> 'signal_id',
        'signal_kind', target_signal ->> 'signal_kind',
        'signal_payload_sha256', private.agent_json_sha256(
            target_signal - 'payload_sha256'
        ),
        'source_event_id', target_signal ->> 'source_event_id',
        'workspace_id', target_workspace_id::text
    ))
$$;

create or replace function private.harmony_preview_connector_request_receipt_shape(
    target jsonb
)
returns boolean
language sql
immutable
set search_path = ''
as $$
    select pg_catalog.jsonb_typeof(target) = 'object'
       and target ?& array[
            'accepted_at', 'attestation_key_id', 'automatic_publication',
            'client_id', 'connector_receipt_id',
            'connector_receipt_sha256', 'expires_at', 'external_calls',
            'payload_sha256', 'provider_calls', 'publication_calls',
            'raw_content_included', 'registration_id',
            'registration_sha256', 'request_nonce', 'request_receipt_id',
            'request_sha256', 'schema_version', 'signal_id',
            'signal_payload_sha256', 'token_claims_sha256', 'workspace_id'
       ]
       and (select pg_catalog.count(*)
            from pg_catalog.jsonb_object_keys(target)) = 22
$$;

create table private.harmony_preview_connector_request_receipts (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    request_receipt_id uuid not null default extensions.gen_random_uuid(),
    registration_id uuid not null,
    registration_sha256 text not null check (
        registration_sha256 ~ '^[a-f0-9]{64}$'
    ),
    attestation_key_id text not null check (
        attestation_key_id ~ '^[a-z][a-z0-9._:-]{2,127}$'
    ),
    request_nonce uuid not null,
    request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
    token_claims_sha256 text not null check (
        token_claims_sha256 ~ '^[a-f0-9]{64}$'
    ),
    signal_id uuid not null,
    signal_payload_sha256 text not null check (
        signal_payload_sha256 ~ '^[a-f0-9]{64}$'
    ),
    connector_receipt_id uuid not null,
    connector_receipt_sha256 text not null check (
        connector_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    accepted_at timestamptz not null,
    expires_at timestamptz not null,
    payload jsonb not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, request_receipt_id),
    unique (workspace_id, client_id, registration_id, request_nonce),
    unique (workspace_id, client_id, registration_id, request_sha256),
    unique (workspace_id, client_id, connector_receipt_id),
    foreign key (
        workspace_id, client_id, registration_id, registration_sha256
    ) references private.harmony_preview_connector_registrations(
        workspace_id, client_id, registration_id, registration_sha256
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, connector_receipt_id, signal_id,
        signal_payload_sha256, connector_receipt_sha256
    ) references agent_runtime.harmony_connector_attestation_receipts(
        workspace_id, client_id, receipt_id, signal_id,
        signal_payload_sha256, payload_sha256
    ) on delete restrict,
    check (expires_at > accepted_at),
    check (payload ->> 'schema_version'
        = 'harmony-connector-request-receipt@1'),
    check (payload ->> 'request_receipt_id' = request_receipt_id::text),
    check (payload ->> 'workspace_id' = workspace_id::text),
    check (payload ->> 'client_id' = client_id),
    check (payload ->> 'registration_id' = registration_id::text),
    check (payload ->> 'registration_sha256' = registration_sha256),
    check (payload ->> 'attestation_key_id' = attestation_key_id),
    check (payload ->> 'request_nonce' = request_nonce::text),
    check (payload ->> 'request_sha256' = request_sha256),
    check (payload ->> 'token_claims_sha256' = token_claims_sha256),
    check (payload ->> 'signal_id' = signal_id::text),
    check (payload ->> 'signal_payload_sha256' = signal_payload_sha256),
    check (payload ->> 'connector_receipt_id' = connector_receipt_id::text),
    check (payload ->> 'connector_receipt_sha256'
        = connector_receipt_sha256),
    check (payload ->> 'accepted_at' = pg_catalog.to_char(
        accepted_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
    )),
    check (payload ->> 'expires_at' = pg_catalog.to_char(
        expires_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
    )),
    check (payload ->> 'payload_sha256' = payload_sha256),
    check (payload_sha256 = private.agent_json_sha256(payload - 'payload_sha256')),
    check (private.harmony_preview_connector_request_receipt_shape(payload)),
    check (payload -> 'raw_content_included' = 'false'::jsonb),
    check (payload -> 'external_calls' = 'false'::jsonb),
    check (payload -> 'provider_calls' = 'false'::jsonb),
    check (payload -> 'publication_calls' = 'false'::jsonb),
    check (payload -> 'automatic_publication' = 'false'::jsonb)
);

create or replace function private.harmony_preview_validate_request_chronology()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    registration_created_at timestamptz;
    connector_verified_at timestamptz;
begin
    select registration.created_at, receipt.verified_at
    into strict registration_created_at, connector_verified_at
    from private.harmony_preview_connector_registrations registration
    join agent_runtime.harmony_connector_attestation_receipts receipt
      on receipt.workspace_id = new.workspace_id
     and receipt.client_id = new.client_id
     and receipt.receipt_id = new.connector_receipt_id
     and receipt.signal_id = new.signal_id
     and receipt.signal_payload_sha256 = new.signal_payload_sha256
     and receipt.payload_sha256 = new.connector_receipt_sha256
    where registration.workspace_id = new.workspace_id
      and registration.client_id = new.client_id
      and registration.registration_id = new.registration_id
      and registration.registration_sha256 = new.registration_sha256;
    if registration_created_at > connector_verified_at
       or connector_verified_at > new.accepted_at
    then
        raise exception 'harmony_preview_connector_request_chronology_invalid';
    end if;
    return new;
exception
    when no_data_found then
        raise exception 'harmony_preview_connector_request_chronology_invalid';
end;
$$;

create trigger harmony_preview_connector_request_chronology
before insert on private.harmony_preview_connector_request_receipts
for each row execute function
    private.harmony_preview_validate_request_chronology();

create or replace function private.harmony_preview_qa_failed_finding_codes(
    target_criteria jsonb
)
returns text[]
language sql
immutable
strict
set search_path = ''
as $$
    select coalesce(pg_catalog.array_agg(code order by code), '{}'::text[])
    from (
        select 'automatic_publication_enabled'::text as code
        where target_criteria -> 'automatic_publication' = 'true'::jsonb
        union all
        select 'external_call_detected'::text
        where target_criteria -> 'no_external_calls' = 'false'::jsonb
        union all
        select 'factual_binding_failed'::text
        where target_criteria -> 'factual_binding' = 'false'::jsonb
        union all
        select 'private_boundary_failed'::text
        where target_criteria -> 'private_only' = 'false'::jsonb
    ) finding
$$;

create or replace function private.harmony_preview_failed_qa_evidence_valid(
    target jsonb,
    target_output_sha256 text
)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    criteria jsonb;
    findings text[];
begin
    if target is null
       or pg_catalog.jsonb_typeof(target) <> 'object'
       or pg_catalog.octet_length(target::text) > 4096
       or not target ?& array[
            'schema_version', 'reviewed_output_sha256', 'criteria',
            'findings', 'verdict', 'verifier_version'
       ]
       or (select pg_catalog.count(*)
           from pg_catalog.jsonb_object_keys(target)) <> 6
       or target ->> 'schema_version'
            <> 'harmony-independent-qa-evidence@1'
       or target ->> 'reviewed_output_sha256' <> target_output_sha256
       or target_output_sha256 !~ '^[a-f0-9]{64}$'
       or target ->> 'verdict' <> 'failed'
       or target ->> 'verifier_version' <> 'harmony-deterministic-qa@1'
       or pg_catalog.jsonb_typeof(target -> 'criteria') <> 'object'
       or not (target -> 'criteria') ?& array[
            'automatic_publication', 'factual_binding',
            'no_external_calls', 'private_only'
       ]
       or (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(
            target -> 'criteria'
          )) <> 4
       or exists (
            select 1
            from pg_catalog.jsonb_each(target -> 'criteria') criterion
            where pg_catalog.jsonb_typeof(criterion.value) <> 'boolean'
       )
       or pg_catalog.jsonb_typeof(target -> 'findings') <> 'array'
    then
        return false;
    end if;
    criteria := target -> 'criteria';
    findings := private.harmony_preview_qa_failed_finding_codes(criteria);
    return pg_catalog.cardinality(findings) between 1 and 4
       and target -> 'findings' = pg_catalog.to_jsonb(findings);
exception
    when others then
        return false;
end;
$$;

create or replace function private.harmony_preview_qa_denial_receipt_shape(
    target jsonb
)
returns boolean
language sql
immutable
set search_path = ''
as $$
    select pg_catalog.jsonb_typeof(target) = 'object'
       and target ?& array[
            'aggregate_only', 'automatic_publication', 'client_id',
            'denial_receipt_id', 'denied_output_sha256', 'evidence_sha256',
            'external_calls', 'finding_codes', 'payload_sha256', 'plan_id',
            'private_content_receipt_id', 'provider_calls',
            'publication_calls', 'raw_content_included', 'recorded_at',
            'reviewer_binding_sha256', 'reviewer_principal_id', 'round_id',
            'schema_version', 'verdict', 'verifier_version', 'workspace_id'
       ]
       and (select pg_catalog.count(*)
            from pg_catalog.jsonb_object_keys(target)) = 22
$$;

create table private.harmony_preview_qa_denial_receipts (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    denial_receipt_id uuid not null,
    round_id uuid not null,
    plan_id uuid not null,
    private_content_receipt_id uuid not null,
    denied_output_sha256 text not null check (
        denied_output_sha256 ~ '^[a-f0-9]{64}$'
    ),
    reviewer_principal_id uuid not null,
    reviewer_binding_sha256 text not null check (
        reviewer_binding_sha256 ~ '^[a-f0-9]{64}$'
    ),
    evidence jsonb not null,
    evidence_sha256 text not null check (evidence_sha256 ~ '^[a-f0-9]{64}$'),
    finding_codes text[] not null,
    verifier_version text not null check (
        verifier_version = 'harmony-deterministic-qa@1'
    ),
    payload jsonb not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz not null,
    primary key (workspace_id, client_id, denial_receipt_id),
    unique (workspace_id, client_id, plan_id, denied_output_sha256),
    unique (workspace_id, client_id, payload_sha256),
    foreign key (
        workspace_id, client_id, private_content_receipt_id, plan_id, round_id
    ) references agent_runtime.harmony_stage_receipts(
        workspace_id, client_id, receipt_id, plan_id, round_id
    ) on delete restrict,
    foreign key (workspace_id, client_id, reviewer_binding_sha256)
        references private.harmony_preview_squid_specialist_bindings(
            workspace_id, client_id, binding_sha256
        ) on delete restrict,
    check (private.harmony_preview_failed_qa_evidence_valid(
        evidence, denied_output_sha256
    )),
    check (evidence_sha256 = private.agent_json_sha256(evidence)),
    check (finding_codes = private.harmony_preview_qa_failed_finding_codes(
        evidence -> 'criteria'
    )),
    check (pg_catalog.cardinality(finding_codes) between 1 and 4),
    check (payload ->> 'schema_version' = 'harmony-qa-denial-receipt@1'),
    check (payload ->> 'denial_receipt_id' = denial_receipt_id::text),
    check (payload ->> 'workspace_id' = workspace_id::text),
    check (payload ->> 'client_id' = client_id),
    check (payload ->> 'round_id' = round_id::text),
    check (payload ->> 'plan_id' = plan_id::text),
    check (payload ->> 'private_content_receipt_id'
        = private_content_receipt_id::text),
    check (payload ->> 'denied_output_sha256' = denied_output_sha256),
    check (payload ->> 'reviewer_principal_id'
        = reviewer_principal_id::text),
    check (payload ->> 'reviewer_binding_sha256'
        = reviewer_binding_sha256),
    check (payload ->> 'evidence_sha256' = evidence_sha256),
    check (payload -> 'finding_codes' = pg_catalog.to_jsonb(finding_codes)),
    check (payload ->> 'verifier_version' = verifier_version),
    check (payload ->> 'verdict' = 'failed'),
    check (payload ->> 'recorded_at' = pg_catalog.to_char(
        created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
    )),
    check (payload ->> 'payload_sha256' = payload_sha256),
    check (payload_sha256 = private.agent_json_sha256(payload - 'payload_sha256')),
    check (private.harmony_preview_qa_denial_receipt_shape(payload)),
    check (payload -> 'aggregate_only' = 'true'::jsonb),
    check (payload -> 'raw_content_included' = 'false'::jsonb),
    check (payload -> 'external_calls' = 'false'::jsonb),
    check (payload -> 'provider_calls' = 'false'::jsonb),
    check (payload -> 'publication_calls' = 'false'::jsonb),
    check (payload -> 'automatic_publication' = 'false'::jsonb)
);

alter table private.harmony_preview_connector_registrations
    enable row level security;
alter table private.harmony_preview_connector_registrations
    force row level security;
alter table private.harmony_preview_connector_registration_revocations
    enable row level security;
alter table private.harmony_preview_connector_registration_revocations
    force row level security;
alter table private.harmony_preview_connector_request_receipts
    enable row level security;
alter table private.harmony_preview_connector_request_receipts
    force row level security;
alter table private.harmony_preview_qa_denial_receipts
    enable row level security;
alter table private.harmony_preview_qa_denial_receipts
    force row level security;

revoke all on table
    private.harmony_preview_connector_registrations,
    private.harmony_preview_connector_registration_revocations,
    private.harmony_preview_connector_request_receipts,
    private.harmony_preview_qa_denial_receipts
from public, anon, authenticated, service_role,
    coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_qa,
    coineasy_harmony_operator, coineasy_harmony_recap,
    coineasy_harmony_dashboard;

create trigger harmony_preview_connector_registrations_immutable
before update or delete on private.harmony_preview_connector_registrations
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_connector_revocations_immutable
before update or delete
on private.harmony_preview_connector_registration_revocations
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_connector_requests_immutable
before update or delete on private.harmony_preview_connector_request_receipts
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_qa_denials_immutable
before update or delete on private.harmony_preview_qa_denial_receipts
for each row execute function private.agent_immutable_row();

create or replace function private.harmony_preview_connector_token_claims_sha256()
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    claims jsonb;
begin
    claims := nullif(
        pg_catalog.current_setting('request.jwt.claims', true), ''
    )::jsonb;
    if claims is null or pg_catalog.jsonb_typeof(claims) <> 'object' then
        raise exception 'harmony_preview_connector_trust_claim_invalid';
    end if;
    return private.agent_json_sha256(claims);
exception
    when invalid_text_representation then
        raise exception 'harmony_preview_connector_trust_claim_invalid';
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
    claimed_registration_id uuid;
    claimed_request_nonce uuid;
    issued_epoch bigint;
    expires_epoch bigint;
begin
    begin
        claims := coalesce(
            nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb,
            '{}'::jsonb
        );
        claimed_registration_id :=
            (claims ->> 'attestation_registration_id')::uuid;
        claimed_request_nonce := (claims ->> 'request_nonce')::uuid;
        issued_epoch := (claims ->> 'iat')::bigint;
        expires_epoch := (claims ->> 'exp')::bigint;
    exception when others then
        return false;
    end;
    if issued_epoch is null
       or expires_epoch is null
       or issued_epoch < 0
       or issued_epoch > 4102444800
       or expires_epoch < 0
       or expires_epoch > 4102444800
    then
        return false;
    end if;
    expected_capability := case target_signal ->> 'lane'
        when 'quiz_bot' then 'harmony_submit_quiz_bot'
        when 'community_ops' then 'harmony_submit_community_ops'
        when 'content_source' then 'harmony_submit_content_source'
        when 'recap' then 'harmony_submit_recap'
        else null
    end;
    return coalesce((
       private.harmony_preview_scope_matches(
        target_workspace_id,
        target_client_id,
        array['coineasy_harmony_connector']::text[]
    )
       and target_client_id = 'squid'
       and expected_capability is not null
       and claims ->> 'capability' = expected_capability
       and coalesce(claims ->> 'connector_id', '')
            ~ '^[a-z][a-z0-9_:-]{2,63}$'
       and coalesce(claims ->> 'producer_principal_id', '')
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       and claims ->> 'producer_principal_id'
            = target_signal ->> 'producer_principal_id'
       and claims ->> 'sub' = target_signal ->> 'producer_principal_id'
       and coalesce(claims ->> 'release_sha', '') ~ '^[a-f0-9]{40}$'
       and claims ->> 'release_sha' = target_signal ->> 'producer_release_sha'
       and coalesce(claims ->> 'config_sha256', '') ~ '^[a-f0-9]{64}$'
       and claims ->> 'config_sha256' = target_signal ->> 'config_sha256'
       and coalesce(claims ->> 'attestation_registration_id', '')
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       and coalesce(claims ->> 'attestation_key_id', '')
            ~ '^[a-z][a-z0-9._:-]{2,127}$'
       and coalesce(claims ->> 'request_nonce', '')
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       and coalesce(claims ->> 'jti', '')
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       and claims ->> 'request_nonce' = claims ->> 'jti'
       and claimed_request_nonce::text = claims ->> 'jti'
       and coalesce(claims ->> 'request_sha256', '') ~ '^[a-f0-9]{64}$'
       and exists (
            select 1
            from private.harmony_preview_connector_registrations registration
            join private.harmony_preview_environment_fence fence
              on fence.branch_ref = registration.branch_ref
             and fence.active
             and fence.expires_at > statement_timestamp()
            where registration.workspace_id = target_workspace_id
              and registration.client_id = target_client_id
              and registration.registration_id = claimed_registration_id
              and registration.lane = target_signal ->> 'lane'
              and registration.capability = expected_capability
              and registration.connector_id = claims ->> 'connector_id'
              and registration.producer_principal_id
                    = (claims ->> 'producer_principal_id')::uuid
              and registration.producer_release_sha = claims ->> 'release_sha'
              and registration.config_sha256 = claims ->> 'config_sha256'
              and registration.attestation_key_id
                    = claims ->> 'attestation_key_id'
              and registration.branch_ref = claims ->> 'ref'
              and registration.created_at <= statement_timestamp()
              and pg_catalog.to_timestamp(issued_epoch)
                    >= pg_catalog.date_trunc(
                        'second', registration.created_at
                    )
              and registration.expires_at > statement_timestamp()
              and registration.expires_at <= fence.expires_at
              and pg_catalog.to_timestamp(expires_epoch)
                    <= registration.expires_at
              and not exists (
                    select 1
                    from private.harmony_preview_connector_registration_revocations revoked
                    where revoked.workspace_id = registration.workspace_id
                      and revoked.client_id = registration.client_id
                      and revoked.registration_id = registration.registration_id
              )
       )
    ), false);
end;
$$;

create or replace function private.harmony_preview_connector_verification_reference()
returns text
language sql
stable
security definer
set search_path = ''
as $$
    select private.harmony_preview_connector_token_claims_sha256()
$$;

-- Preserve the successful connector receipt @1 implementation byte-for-byte
-- behind a private, ungranted legacy entry point.  The public name below is a
-- revocable-request wrapper with the original four-argument signature.
alter function public.submit_preview_harmony_signal(uuid, text, uuid, jsonb)
    set schema private;
alter function private.submit_preview_harmony_signal(uuid, text, uuid, jsonb)
    rename to harmony_preview_submit_signal_legacy;
revoke all on function private.harmony_preview_submit_signal_legacy(
    uuid, text, uuid, jsonb
) from public, anon, authenticated, service_role,
    coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_qa,
    coineasy_harmony_operator, coineasy_harmony_recap,
    coineasy_harmony_dashboard;

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
    claimed_registration_id uuid;
    claimed_request_nonce uuid;
    request_sha text;
    token_claims_sha text;
    registration private.harmony_preview_connector_registrations%rowtype;
    existing_request private.harmony_preview_connector_request_receipts%rowtype;
    existing_signal agent_runtime.harmony_signals%rowtype;
    existing_receipt agent_runtime.harmony_connector_attestation_receipts%rowtype;
    legacy_result jsonb;
    request_receipt_id uuid;
    request_body jsonb;
    request_payload jsonb;
    request_payload_sha text;
    accepted_time timestamptz;
    request_expires_at timestamptz;
    expected_capability text;
begin
    begin
        claims := nullif(
            pg_catalog.current_setting('request.jwt.claims', true), ''
        )::jsonb;
        claimed_registration_id :=
            (claims ->> 'attestation_registration_id')::uuid;
        claimed_request_nonce := (claims ->> 'request_nonce')::uuid;
    exception when others then
        raise exception 'harmony_preview_connector_trust_claim_invalid';
    end;
    if target_client_id <> 'squid'
       or not private.harmony_preview_signal_valid(target_signal)
       or target_signal ->> 'workspace_id' <> target_workspace_id::text
       or target_signal ->> 'client_id' <> target_client_id
       or coalesce(target_signal ->> 'observed_at', '')
            !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
       or coalesce(target_signal ->> 'expires_at', '')
            !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
       or coalesce(claims ->> 'attestation_registration_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or coalesce(claims ->> 'attestation_key_id', '')
            !~ '^[a-z][a-z0-9._:-]{2,127}$'
       or coalesce(claims ->> 'connector_id', '')
            !~ '^[a-z][a-z0-9_:-]{2,63}$'
       or coalesce(claims ->> 'producer_principal_id', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or claims ->> 'sub' is distinct from claims ->> 'producer_principal_id'
       or coalesce(claims ->> 'release_sha', '') !~ '^[a-f0-9]{40}$'
       or coalesce(claims ->> 'config_sha256', '') !~ '^[a-f0-9]{64}$'
       or coalesce(claims ->> 'request_nonce', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or claims ->> 'request_nonce' is distinct from claims ->> 'jti'
       or coalesce(claims ->> 'request_sha256', '') !~ '^[a-f0-9]{64}$'
    then
        raise exception 'harmony_preview_connector_trust_claim_invalid';
    end if;
    request_sha := private.harmony_preview_connector_request_sha256(
        target_workspace_id, target_client_id, claimed_registration_id,
        target_receipt_id, target_signal
    );
    if claims ->> 'request_sha256' <> request_sha then
        raise exception 'harmony_preview_connector_trust_claim_invalid';
    end if;
    token_claims_sha :=
        private.harmony_preview_connector_token_claims_sha256();
    registration := private.harmony_preview_lock_connector_registration(
        target_workspace_id, target_client_id, claimed_registration_id
    );
    if exists (
        select 1
        from private.harmony_preview_connector_registration_revocations revoked
        where revoked.workspace_id = registration.workspace_id
          and revoked.client_id = registration.client_id
          and revoked.registration_id = registration.registration_id
    ) then
        raise exception 'harmony_preview_connector_registration_revoked';
    end if;
    if registration.created_at > statement_timestamp()
       or registration.expires_at <= statement_timestamp()
       or not exists (
            select 1
            from private.harmony_preview_environment_fence fence
            where fence.branch_ref = registration.branch_ref
              and fence.active
              and fence.expires_at > statement_timestamp()
              and registration.expires_at <= fence.expires_at
       )
    then
        raise exception 'harmony_preview_connector_registration_not_current';
    end if;
    expected_capability := case target_signal ->> 'lane'
        when 'quiz_bot' then 'harmony_submit_quiz_bot'
        when 'community_ops' then 'harmony_submit_community_ops'
        when 'content_source' then 'harmony_submit_content_source'
        when 'recap' then 'harmony_submit_recap'
        else null
    end;
    if expected_capability is null
       or registration.lane <> target_signal ->> 'lane'
       or registration.capability <> expected_capability
       or registration.capability <> claims ->> 'capability'
       or registration.connector_id <> claims ->> 'connector_id'
       or registration.producer_principal_id
            <> (claims ->> 'producer_principal_id')::uuid
       or registration.producer_release_sha <> claims ->> 'release_sha'
       or registration.config_sha256 <> claims ->> 'config_sha256'
       or registration.attestation_key_id <> claims ->> 'attestation_key_id'
       or registration.branch_ref <> claims ->> 'ref'
       or not private.harmony_preview_connector_claims_match(
            target_workspace_id, target_client_id, target_signal
       )
    then
        raise exception 'harmony_preview_connector_registration_invalid';
    end if;

    select candidate.* into existing_request
    from private.harmony_preview_connector_request_receipts candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.registration_id = claimed_registration_id
      and candidate.request_nonce = claimed_request_nonce
    for update;
    if found then
        if existing_request.request_sha256 <> request_sha
           or existing_request.token_claims_sha256 <> token_claims_sha
           or existing_request.signal_id
                <> (target_signal ->> 'signal_id')::uuid
           or existing_request.signal_payload_sha256
                <> target_signal ->> 'payload_sha256'
           or existing_request.connector_receipt_id <> target_receipt_id
           or existing_request.attestation_key_id
                <> claims ->> 'attestation_key_id'
           or existing_request.registration_sha256
                <> registration.registration_sha256
        then
            raise exception 'harmony_preview_connector_request_idempotency_conflict';
        end if;
        if existing_request.expires_at <= statement_timestamp() then
            raise exception 'harmony_preview_connector_registration_not_current';
        end if;
        select signal.* into strict existing_signal
        from agent_runtime.harmony_signals signal
        where signal.workspace_id = existing_request.workspace_id
          and signal.client_id = existing_request.client_id
          and signal.signal_id = existing_request.signal_id
          and signal.payload_sha256 = existing_request.signal_payload_sha256;
        select receipt.* into strict existing_receipt
        from agent_runtime.harmony_connector_attestation_receipts receipt
        where receipt.workspace_id = existing_request.workspace_id
          and receipt.client_id = existing_request.client_id
          and receipt.receipt_id = existing_request.connector_receipt_id
          and receipt.payload_sha256
                = existing_request.connector_receipt_sha256;
        return pg_catalog.jsonb_build_object(
            'ok', true,
            'reused', true,
            'signal', existing_signal.payload,
            'connector_receipt', existing_receipt.payload,
            'connector_request_receipt', existing_request.payload,
            'database_calls', true,
            'external_calls', false,
            'provider_calls', false,
            'publication_calls', false,
            'automatic_publication', false
        );
    end if;
    if exists (
        select 1
        from private.harmony_preview_connector_request_receipts candidate
        where candidate.workspace_id = target_workspace_id
          and candidate.client_id = target_client_id
          and candidate.registration_id = claimed_registration_id
          and candidate.request_sha256 = request_sha
    ) then
        raise exception 'harmony_preview_connector_request_replay_conflict';
    end if;

    legacy_result := private.harmony_preview_submit_signal_legacy(
        target_workspace_id, target_client_id, target_receipt_id, target_signal
    );
    if legacy_result -> 'ok' is distinct from 'true'::jsonb
       or legacy_result -> 'external_calls' is distinct from 'false'::jsonb
       or legacy_result -> 'provider_calls' is distinct from 'false'::jsonb
       or legacy_result -> 'publication_calls' is distinct from 'false'::jsonb
       or legacy_result -> 'automatic_publication'
            is distinct from 'false'::jsonb
       or legacy_result -> 'connector_receipt' ->> 'receipt_id'
            <> target_receipt_id::text
       or legacy_result -> 'connector_receipt' ->> 'signal_id'
            <> target_signal ->> 'signal_id'
       or legacy_result -> 'connector_receipt' ->> 'signal_payload_sha256'
            <> target_signal ->> 'payload_sha256'
       or coalesce(
            legacy_result -> 'connector_receipt' ->> 'payload_sha256', ''
          ) !~ '^[a-f0-9]{64}$'
    then
        raise exception 'harmony_preview_connector_legacy_receipt_invalid';
    end if;

    accepted_time := pg_catalog.date_trunc('second', statement_timestamp());
    request_expires_at := pg_catalog.date_trunc('second', least(
        registration.expires_at,
        pg_catalog.to_timestamp((claims ->> 'exp')::bigint),
        (target_signal ->> 'expires_at')::timestamptz
    ));
    if request_expires_at <= accepted_time then
        raise exception 'harmony_preview_connector_registration_not_current';
    end if;
    request_receipt_id := extensions.gen_random_uuid();
    request_body := pg_catalog.jsonb_build_object(
        'accepted_at', pg_catalog.to_char(
            accepted_time at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'attestation_key_id', registration.attestation_key_id,
        'automatic_publication', false,
        'client_id', target_client_id,
        'connector_receipt_id', target_receipt_id::text,
        'connector_receipt_sha256',
            legacy_result -> 'connector_receipt' ->> 'payload_sha256',
        'expires_at', pg_catalog.to_char(
            request_expires_at at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'external_calls', false,
        'provider_calls', false,
        'publication_calls', false,
        'raw_content_included', false,
        'registration_id', registration.registration_id::text,
        'registration_sha256', registration.registration_sha256,
        'request_nonce', claimed_request_nonce::text,
        'request_receipt_id', request_receipt_id::text,
        'request_sha256', request_sha,
        'schema_version', 'harmony-connector-request-receipt@1',
        'signal_id', target_signal ->> 'signal_id',
        'signal_payload_sha256', target_signal ->> 'payload_sha256',
        'token_claims_sha256', token_claims_sha,
        'workspace_id', target_workspace_id::text
    );
    request_payload_sha := private.agent_json_sha256(request_body);
    request_payload := request_body || pg_catalog.jsonb_build_object(
        'payload_sha256', request_payload_sha
    );
    insert into private.harmony_preview_connector_request_receipts (
        workspace_id, client_id, request_receipt_id, registration_id,
        registration_sha256, attestation_key_id, request_nonce,
        request_sha256, token_claims_sha256, signal_id,
        signal_payload_sha256, connector_receipt_id,
        connector_receipt_sha256, accepted_at, expires_at,
        payload, payload_sha256
    ) values (
        target_workspace_id, target_client_id, request_receipt_id,
        registration.registration_id, registration.registration_sha256,
        registration.attestation_key_id, claimed_request_nonce, request_sha,
        token_claims_sha, (target_signal ->> 'signal_id')::uuid,
        target_signal ->> 'payload_sha256', target_receipt_id,
        legacy_result -> 'connector_receipt' ->> 'payload_sha256',
        accepted_time, request_expires_at, request_payload,
        request_payload_sha
    );
    return legacy_result || pg_catalog.jsonb_build_object(
        'connector_request_receipt', request_payload
    );
end;
$$;

create or replace function private.harmony_preview_lock_manifest_registrations(
    target_workspace_id uuid,
    target_client_id text,
    target_signal_manifest jsonb
)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    registration_key record;
    locked_count integer := 0;
begin
    if pg_catalog.jsonb_typeof(target_signal_manifest) <> 'array'
       or pg_catalog.jsonb_array_length(target_signal_manifest) <> 4
    then
        raise exception 'harmony_preview_plan_input_not_current';
    end if;
    begin
        for registration_key in
            select request.registration_id
            from pg_catalog.jsonb_array_elements(target_signal_manifest)
                entry(value)
            join private.harmony_preview_connector_request_receipts request
              on request.workspace_id = target_workspace_id
             and request.client_id = target_client_id
             and request.connector_receipt_id
                    = (entry.value ->> 'connector_receipt_id')::uuid
             and request.connector_receipt_sha256
                    = entry.value ->> 'connector_receipt_sha256'
             and request.signal_id = (entry.value ->> 'signal_id')::uuid
             and request.signal_payload_sha256
                    = entry.value ->> 'signal_payload_sha256'
            join private.harmony_preview_connector_registrations registration
              on registration.workspace_id = request.workspace_id
             and registration.client_id = request.client_id
             and registration.registration_id = request.registration_id
             and registration.registration_sha256
                    = request.registration_sha256
            group by request.registration_id
            order by request.registration_id
        loop
            perform private.harmony_preview_lock_connector_registration(
                target_workspace_id,
                target_client_id,
                registration_key.registration_id
            );
            locked_count := locked_count + 1;
        end loop;
    exception
        when invalid_text_representation then
            raise exception 'harmony_preview_plan_input_not_current';
    end;
    if locked_count <> 4 then
        raise exception 'harmony_preview_plan_input_not_current';
    end if;
end;
$$;

create or replace function private.harmony_preview_round_inputs_current(
    target_workspace_id uuid,
    target_client_id text,
    target_signal_manifest jsonb
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select pg_catalog.jsonb_typeof(target_signal_manifest) = 'array'
       and pg_catalog.jsonb_array_length(target_signal_manifest) = 4
       and (
            select pg_catalog.count(*)
            from pg_catalog.jsonb_array_elements(target_signal_manifest)
                entry(value)
            join agent_runtime.harmony_signals signal
              on signal.workspace_id = target_workspace_id
             and signal.client_id = target_client_id
             and signal.signal_id = (entry.value ->> 'signal_id')::uuid
             and signal.payload_sha256
                    = entry.value ->> 'signal_payload_sha256'
             and signal.lane = entry.value ->> 'lane'
             and signal.upstream_receipt_sha256
                    = entry.value ->> 'upstream_receipt_sha256'
            join agent_runtime.harmony_connector_attestation_receipts receipt
              on receipt.workspace_id = signal.workspace_id
             and receipt.client_id = signal.client_id
             and receipt.receipt_id
                    = (entry.value ->> 'connector_receipt_id')::uuid
             and receipt.payload_sha256
                    = entry.value ->> 'connector_receipt_sha256'
             and receipt.signal_id = signal.signal_id
             and receipt.signal_payload_sha256 = signal.payload_sha256
            join private.harmony_preview_connector_request_receipts request
              on request.workspace_id = receipt.workspace_id
             and request.client_id = receipt.client_id
             and request.connector_receipt_id = receipt.receipt_id
             and request.connector_receipt_sha256 = receipt.payload_sha256
             and request.signal_id = signal.signal_id
             and request.signal_payload_sha256 = signal.payload_sha256
            join private.harmony_preview_connector_registrations registration
              on registration.workspace_id = request.workspace_id
             and registration.client_id = request.client_id
             and registration.registration_id = request.registration_id
             and registration.registration_sha256
                    = request.registration_sha256
             and registration.attestation_key_id
                    = request.attestation_key_id
             and registration.lane = signal.lane
             and registration.producer_principal_id
                    = signal.producer_principal_id
            join private.harmony_preview_environment_fence fence
              on fence.branch_ref = registration.branch_ref
             and fence.active
             and fence.expires_at > statement_timestamp()
            where signal.observed_at <= statement_timestamp()
              and signal.expires_at > statement_timestamp()
              and receipt.verified_at <= statement_timestamp()
              and receipt.expires_at > statement_timestamp()
              and request.accepted_at <= statement_timestamp()
              and request.expires_at > statement_timestamp()
              and request.request_sha256
                    = private.harmony_preview_connector_request_sha256(
                        signal.workspace_id, signal.client_id,
                        registration.registration_id, receipt.receipt_id,
                        signal.payload
                    )
              and registration.created_at <= statement_timestamp()
              and registration.expires_at > statement_timestamp()
              and registration.expires_at <= fence.expires_at
              and not exists (
                    select 1
                    from private.harmony_preview_connector_registration_revocations revoked
                    where revoked.workspace_id = registration.workspace_id
                      and revoked.client_id = registration.client_id
                      and revoked.registration_id = registration.registration_id
              )
       ) = 4
       and (
            select pg_catalog.count(distinct signal.lane)
            from pg_catalog.jsonb_array_elements(target_signal_manifest)
                entry(value)
            join agent_runtime.harmony_signals signal
              on signal.workspace_id = target_workspace_id
             and signal.client_id = target_client_id
             and signal.signal_id = (entry.value ->> 'signal_id')::uuid
             and signal.payload_sha256
                    = entry.value ->> 'signal_payload_sha256'
       ) = 4
       and exists (
            select 1
            from agent_runtime.harmony_signals signal
            where signal.workspace_id = target_workspace_id
              and signal.client_id = target_client_id
              and signal.lane = 'content_source'
              and signal.payload_sha256 in (
                    select value ->> 'signal_payload_sha256'
                    from pg_catalog.jsonb_array_elements(
                        target_signal_manifest
                    )
              )
              and signal.official_source_binding_sha256
                    = signal.upstream_receipt_sha256
              and private.harmony_preview_squid_official_source_binding(
                    signal.payload
                  ) = signal.official_source_binding_sha256
       )
$$;

create or replace function private.harmony_preview_guard_round_insert_current()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    perform private.harmony_preview_lock_manifest_registrations(
        new.workspace_id, new.client_id, new.signal_manifest
    );
    if not private.harmony_preview_round_inputs_current(
        new.workspace_id, new.client_id, new.signal_manifest
    ) then
        raise exception 'harmony_preview_plan_input_not_current';
    end if;
    return new;
end;
$$;

create trigger harmony_rounds_guard_current_connector_trust
before insert on agent_runtime.harmony_rounds
for each row execute function private.harmony_preview_guard_round_insert_current();

create or replace function private.harmony_preview_guard_stage_insert_current()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    signal_manifest jsonb;
begin
    select round_value.signal_manifest into strict signal_manifest
    from agent_runtime.harmony_rounds round_value
    where round_value.workspace_id = new.workspace_id
      and round_value.client_id = new.client_id
      and round_value.round_id = new.round_id
      and round_value.plan_id = new.plan_id;
    perform private.harmony_preview_lock_manifest_registrations(
        new.workspace_id, new.client_id, signal_manifest
    );
    if not private.harmony_preview_round_inputs_current(
        new.workspace_id, new.client_id, signal_manifest
    ) then
        raise exception 'harmony_preview_plan_input_not_current';
    end if;
    return new;
exception
    when no_data_found then
        raise exception 'harmony_preview_plan_input_not_current';
end;
$$;

create trigger harmony_stage_receipts_guard_current_connector_trust
before insert on agent_runtime.harmony_stage_receipts
for each row execute function private.harmony_preview_guard_stage_insert_current();

create or replace function private.harmony_preview_qa_actor_independent(
    target_workspace_id uuid,
    target_client_id text,
    target_plan_id uuid,
    target_reviewer_principal_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select target_client_id = 'squid'
       and exists (
            select 1
            from agent_runtime.harmony_rounds round_value
            where round_value.workspace_id = target_workspace_id
              and round_value.client_id = target_client_id
              and round_value.plan_id = target_plan_id
       )
       and not exists (
            select 1
            from agent_runtime.harmony_rounds round_value
            cross join lateral pg_catalog.jsonb_array_elements(
                round_value.signal_manifest
            ) entry(value)
            join agent_runtime.harmony_signals signal
              on signal.workspace_id = round_value.workspace_id
             and signal.client_id = round_value.client_id
             and signal.signal_id = (entry.value ->> 'signal_id')::uuid
             and signal.payload_sha256
                    = entry.value ->> 'signal_payload_sha256'
            where round_value.workspace_id = target_workspace_id
              and round_value.client_id = target_client_id
              and round_value.plan_id = target_plan_id
              and signal.producer_principal_id = target_reviewer_principal_id
       )
       and not exists (
            select 1
            from private.harmony_preview_squid_specialist_bindings specialist
            where specialist.workspace_id = target_workspace_id
              and specialist.client_id = target_client_id
              and specialist.stage <> 'independent_qa'
              and specialist.principal_id = target_reviewer_principal_id
       )
       and not exists (
            select 1
            from agent_runtime.harmony_stage_receipts receipt
            where receipt.workspace_id = target_workspace_id
              and receipt.client_id = target_client_id
              and receipt.plan_id = target_plan_id
              and receipt.stage <> 'independent_qa'
              and receipt.principal_id = target_reviewer_principal_id
       )
$$;

create or replace function private.harmony_preview_guard_positive_qa_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.stage <> 'independent_qa' then
        return new;
    end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_qa_outcome:' || new.workspace_id::text || ':' ||
        new.client_id || ':' || new.plan_id::text || ':' || new.input_sha256,
        0
    ));
    if not private.harmony_preview_qa_actor_independent(
        new.workspace_id, new.client_id, new.plan_id, new.principal_id
    ) then
        raise exception 'harmony_preview_qa_actor_not_independent';
    end if;
    if exists (
        select 1
        from private.harmony_preview_qa_denial_receipts denial
        where denial.workspace_id = new.workspace_id
          and denial.client_id = new.client_id
          and denial.plan_id = new.plan_id
          and denial.denied_output_sha256 = new.input_sha256
    ) then
        raise exception 'harmony_preview_qa_output_already_denied';
    end if;
    return new;
end;
$$;

create trigger harmony_stage_receipts_guard_positive_qa
before insert on agent_runtime.harmony_stage_receipts
for each row execute function private.harmony_preview_guard_positive_qa_insert();

create or replace function private.harmony_preview_validate_qa_denial_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    signal_manifest jsonb;
begin
    select round_value.signal_manifest into strict signal_manifest
    from agent_runtime.harmony_rounds round_value
    where round_value.workspace_id = new.workspace_id
      and round_value.client_id = new.client_id
      and round_value.round_id = new.round_id
      and round_value.plan_id = new.plan_id;
    perform private.harmony_preview_lock_manifest_registrations(
        new.workspace_id, new.client_id, signal_manifest
    );
    if not private.harmony_preview_round_inputs_current(
        new.workspace_id, new.client_id, signal_manifest
    ) then
        raise exception 'harmony_preview_plan_input_not_current';
    end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_qa_outcome:' || new.workspace_id::text || ':' ||
        new.client_id || ':' || new.plan_id::text || ':' ||
        new.denied_output_sha256,
        0
    ));
    if not exists (
        select 1
        from agent_runtime.harmony_stage_receipts content_stage
        where content_stage.workspace_id = new.workspace_id
          and content_stage.client_id = new.client_id
          and content_stage.round_id = new.round_id
          and content_stage.plan_id = new.plan_id
          and content_stage.receipt_id = new.private_content_receipt_id
          and content_stage.stage = 'private_content'
          and content_stage.output_sha256 = new.denied_output_sha256
    ) then
        raise exception 'harmony_preview_qa_denial_evidence_invalid';
    end if;
    if not private.harmony_preview_qa_actor_independent(
        new.workspace_id, new.client_id, new.plan_id,
        new.reviewer_principal_id
    ) then
        raise exception 'harmony_preview_qa_actor_not_independent';
    end if;
    if exists (
        select 1
        from agent_runtime.harmony_stage_receipts passed
        where passed.workspace_id = new.workspace_id
          and passed.client_id = new.client_id
          and passed.plan_id = new.plan_id
          and passed.stage = 'independent_qa'
          and passed.verdict = 'passed'
          and passed.input_sha256 = new.denied_output_sha256
    ) then
        raise exception 'harmony_preview_qa_already_passed';
    end if;
    return new;
end;
$$;

create trigger harmony_preview_qa_denial_validate
before insert on private.harmony_preview_qa_denial_receipts
for each row execute function private.harmony_preview_validate_qa_denial_insert();

create or replace function public.record_preview_harmony_squid_qa_denial(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid,
    target_plan_id uuid,
    target_denial_receipt_id uuid,
    target_qa_evidence jsonb
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
    content_stage agent_runtime.harmony_stage_receipts%rowtype;
    existing private.harmony_preview_qa_denial_receipts%rowtype;
    evidence_sha text;
    findings text[];
    recorded_time timestamptz;
    body jsonb;
    receipt_payload jsonb;
    receipt_sha text;
begin
    if target_client_id <> 'squid'
       or target_qa_evidence is null
       or not private.harmony_preview_stage_claims_match(
            target_workspace_id, target_client_id,
            'coineasy_harmony_qa', 'harmony_independent_qa'
       )
    then
        raise exception 'harmony_preview_qa_denial_scope_invalid';
    end if;
    claims := nullif(
        pg_catalog.current_setting('request.jwt.claims', true), ''
    )::jsonb;
    binding := private.harmony_preview_stage_binding();
    select round_value.signal_manifest into strict signal_manifest
    from agent_runtime.harmony_rounds round_value
    where round_value.workspace_id = target_workspace_id
      and round_value.client_id = target_client_id
      and round_value.round_id = target_round_id
      and round_value.plan_id = target_plan_id;
    perform private.harmony_preview_lock_manifest_registrations(
        target_workspace_id, target_client_id, signal_manifest
    );
    if not private.harmony_preview_round_inputs_current(
        target_workspace_id, target_client_id, signal_manifest
    ) then
        raise exception 'harmony_preview_plan_input_not_current';
    end if;
    select candidate.* into strict content_stage
    from agent_runtime.harmony_stage_receipts candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.round_id = target_round_id
      and candidate.plan_id = target_plan_id
      and candidate.stage = 'private_content';
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_qa_outcome:' || target_workspace_id::text || ':' ||
        target_client_id || ':' || target_plan_id::text || ':' ||
        content_stage.output_sha256,
        0
    ));
    if not private.harmony_preview_qa_actor_independent(
        target_workspace_id, target_client_id, target_plan_id,
        (claims ->> 'producer_principal_id')::uuid
    ) then
        raise exception 'harmony_preview_qa_actor_not_independent';
    end if;
    if not private.harmony_preview_failed_qa_evidence_valid(
        target_qa_evidence, content_stage.output_sha256
    ) then
        raise exception 'harmony_preview_qa_denial_evidence_invalid';
    end if;
    if exists (
        select 1
        from agent_runtime.harmony_stage_receipts passed
        where passed.workspace_id = target_workspace_id
          and passed.client_id = target_client_id
          and passed.plan_id = target_plan_id
          and passed.stage = 'independent_qa'
          and passed.verdict = 'passed'
          and passed.input_sha256 = content_stage.output_sha256
    ) then
        raise exception 'harmony_preview_qa_already_passed';
    end if;
    evidence_sha := private.agent_json_sha256(target_qa_evidence);
    findings := private.harmony_preview_qa_failed_finding_codes(
        target_qa_evidence -> 'criteria'
    );
    select candidate.* into existing
    from private.harmony_preview_qa_denial_receipts candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.plan_id = target_plan_id
      and candidate.denied_output_sha256 = content_stage.output_sha256
    for update;
    if found then
        if existing.round_id <> target_round_id
           or existing.private_content_receipt_id <> content_stage.receipt_id
           or existing.reviewer_principal_id
                <> (claims ->> 'producer_principal_id')::uuid
           or existing.reviewer_binding_sha256
                <> binding ->> 'specialist_binding_sha256'
           or existing.evidence_sha256 <> evidence_sha
           or existing.finding_codes <> findings
        then
            raise exception 'harmony_preview_qa_denial_idempotency_conflict';
        end if;
        return pg_catalog.jsonb_build_object(
            'ok', false,
            'denied', true,
            'reused', true,
            'qa_denial_receipt', existing.payload,
            'database_calls', true,
            'external_calls', false,
            'provider_calls', false,
            'publication_calls', false,
            'automatic_publication', false
        );
    end if;
    recorded_time := pg_catalog.date_trunc('second', statement_timestamp());
    body := pg_catalog.jsonb_build_object(
        'aggregate_only', true,
        'automatic_publication', false,
        'client_id', target_client_id,
        'denial_receipt_id', target_denial_receipt_id::text,
        'denied_output_sha256', content_stage.output_sha256,
        'evidence_sha256', evidence_sha,
        'external_calls', false,
        'finding_codes', pg_catalog.to_jsonb(findings),
        'plan_id', target_plan_id::text,
        'private_content_receipt_id', content_stage.receipt_id::text,
        'provider_calls', false,
        'publication_calls', false,
        'raw_content_included', false,
        'recorded_at', pg_catalog.to_char(
            recorded_time at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'reviewer_binding_sha256',
            binding ->> 'specialist_binding_sha256',
        'reviewer_principal_id', claims ->> 'producer_principal_id',
        'round_id', target_round_id::text,
        'schema_version', 'harmony-qa-denial-receipt@1',
        'verdict', 'failed',
        'verifier_version', 'harmony-deterministic-qa@1',
        'workspace_id', target_workspace_id::text
    );
    receipt_sha := private.agent_json_sha256(body);
    receipt_payload := body || pg_catalog.jsonb_build_object(
        'payload_sha256', receipt_sha
    );
    insert into private.harmony_preview_qa_denial_receipts (
        workspace_id, client_id, denial_receipt_id, round_id, plan_id,
        private_content_receipt_id, denied_output_sha256,
        reviewer_principal_id, reviewer_binding_sha256, evidence,
        evidence_sha256, finding_codes, verifier_version,
        payload, payload_sha256, created_at
    ) values (
        target_workspace_id, target_client_id, target_denial_receipt_id,
        target_round_id, target_plan_id, content_stage.receipt_id,
        content_stage.output_sha256,
        (claims ->> 'producer_principal_id')::uuid,
        binding ->> 'specialist_binding_sha256', target_qa_evidence,
        evidence_sha, findings, 'harmony-deterministic-qa@1',
        receipt_payload, receipt_sha, recorded_time
    );
    return pg_catalog.jsonb_build_object(
        'ok', false,
        'denied', true,
        'reused', false,
        'qa_denial_receipt', receipt_payload,
        'database_calls', true,
        'external_calls', false,
        'provider_calls', false,
        'publication_calls', false,
        'automatic_publication', false
    );
exception
    when no_data_found then
        raise exception 'harmony_preview_qa_denial_dependency_missing';
end;
$$;

revoke all on function private.harmony_preview_connector_registration_sha256(
    text, uuid, text, uuid, text, text, text, uuid, text, text, text,
    timestamptz
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_validate_connector_registration()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_lock_connector_registration(
    uuid, text, uuid
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_connector_revocation_sha256(
    uuid, text, uuid, uuid, text, text, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_linearize_connector_revocation()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_connector_request_sha256(
    uuid, text, uuid, uuid, jsonb
) from public, anon, authenticated, service_role;
revoke all on function
    private.harmony_preview_connector_request_receipt_shape(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_validate_request_chronology()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_qa_failed_finding_codes(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_failed_qa_evidence_valid(
    jsonb, text
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_qa_denial_receipt_shape(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_connector_token_claims_sha256()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_connector_claims_match(
    uuid, text, jsonb
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_connector_verification_reference()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_lock_manifest_registrations(
    uuid, text, jsonb
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_round_inputs_current(
    uuid, text, jsonb
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_guard_round_insert_current()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_guard_stage_insert_current()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_qa_actor_independent(
    uuid, text, uuid, uuid
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_guard_positive_qa_insert()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_validate_qa_denial_insert()
from public, anon, authenticated, service_role;

revoke all on function public.submit_preview_harmony_signal(
    uuid, text, uuid, jsonb
) from public, anon, authenticated, service_role,
    coineasy_harmony_orchestrator, coineasy_harmony_content,
    coineasy_harmony_qa, coineasy_harmony_operator,
    coineasy_harmony_recap, coineasy_harmony_dashboard;
grant execute on function public.submit_preview_harmony_signal(
    uuid, text, uuid, jsonb
) to coineasy_harmony_connector;

revoke all on function public.record_preview_harmony_squid_qa_denial(
    uuid, text, uuid, uuid, uuid, jsonb
) from public, anon, authenticated, service_role,
    coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_operator,
    coineasy_harmony_recap, coineasy_harmony_dashboard;
grant execute on function public.record_preview_harmony_squid_qa_denial(
    uuid, text, uuid, uuid, uuid, jsonb
) to coineasy_harmony_qa;

commit;
