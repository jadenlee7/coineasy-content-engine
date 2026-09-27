-- Durable, disposable-Preview-only Squid Codex QA gate.
--
-- This additive migration turns the already-proven Harmony source, fixed
-- specialist, connector-attestation, and private-content receipts into a
-- durable exactly-once QA gate.  Every receipt is append-only.  The database
-- derives source lineage, reviewer identity, release/config, trust expiry,
-- claims, attempts, and transition time.  No routine in this file calls a
-- provider or writes Production, Buzz, an approval decision, a message, or a
-- publication.

begin;

do $fresh_durable_gate$
begin
    -- There is intentionally no backfill from the older synthetic QA stage.
    -- A Preview with a pre-existing positive QA receipt cannot prove that the
    -- receipt passed through this gate, so applying the migration must fail.
    if exists (
        select 1
        from agent_runtime.harmony_stage_receipts receipt
        where receipt.stage = 'independent_qa'
    ) then
        raise exception 'harmony_preview_codex_gate_requires_no_qa_stage';
    end if;
end
$fresh_durable_gate$;

create or replace function private.harmony_preview_codex_timestamp(
    target_value timestamptz
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select pg_catalog.to_char(
        target_value at time zone 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
    )
$$;

create or replace function private.harmony_preview_codex_uuid4_array(
    target_value uuid[]
)
returns boolean
language sql
immutable
strict
set search_path = ''
as $$
    select pg_catalog.cardinality(target_value) = 4
       and target_value = (
            select pg_catalog.array_agg(item.value order by item.value::text)
            from pg_catalog.unnest(target_value) item(value)
       )
       and pg_catalog.cardinality(target_value) = (
            select pg_catalog.count(distinct item.value)
            from pg_catalog.unnest(target_value) item(value)
       )
$$;

create table private.harmony_preview_codex_source_lineage_receipts (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    lineage_receipt_id uuid not null default extensions.gen_random_uuid(),
    round_id uuid not null,
    plan_id uuid not null,
    branch_ref text not null check (branch_ref ~ '^[a-z0-9]{20}$'),
    branch_fence_created_at timestamptz not null,
    branch_fence_expires_at timestamptz not null,
    plan_receipt_id uuid not null,
    plan_receipt_sha256 text not null check (
        plan_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    private_content_receipt_id uuid not null,
    private_content_receipt_sha256 text not null check (
        private_content_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    private_content_output_sha256 text not null check (
        private_content_output_sha256 ~ '^[a-f0-9]{64}$'
    ),
    private_content_principal_id uuid not null,
    private_content_specialist_binding_sha256 text not null check (
        private_content_specialist_binding_sha256 ~ '^[a-f0-9]{64}$'
    ),
    reviewer_principal_id uuid not null,
    reviewer_specialist_binding_sha256 text not null check (
        reviewer_specialist_binding_sha256 ~ '^[a-f0-9]{64}$'
    ),
    reviewer_release_sha text not null check (
        reviewer_release_sha ~ '^[a-f0-9]{40}$'
    ),
    reviewer_config_sha256 text not null check (
        reviewer_config_sha256 ~ '^[a-f0-9]{64}$'
    ),
    signal_manifest jsonb not null,
    signal_manifest_sha256 text not null check (
        signal_manifest_sha256 ~ '^[a-f0-9]{64}$'
    ),
    signal_input_set_sha256 text not null check (
        signal_input_set_sha256 ~ '^[a-f0-9]{64}$'
    ),
    signal_producer_principal_ids uuid[] not null check (
        private.harmony_preview_codex_uuid4_array(
            signal_producer_principal_ids
        )
    ),
    trust_manifest jsonb not null,
    trust_manifest_sha256 text not null check (
        trust_manifest_sha256 ~ '^[a-f0-9]{64}$'
    ),
    source_signal_id uuid not null,
    source_signal_payload_sha256 text not null check (
        source_signal_payload_sha256 ~ '^[a-f0-9]{64}$'
    ),
    source_producer_principal_id uuid not null,
    source_signal_expires_at timestamptz not null,
    source_connector_receipt_id uuid not null,
    source_connector_receipt_sha256 text not null check (
        source_connector_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    source_request_receipt_id uuid not null,
    source_request_receipt_sha256 text not null check (
        source_request_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    official_content_version_id uuid not null,
    official_source_item_id uuid not null,
    official_source_binding_sha256 text not null check (
        official_source_binding_sha256 ~ '^[a-f0-9]{64}$'
    ),
    content_snapshot_sha256 text not null check (
        content_snapshot_sha256 ~ '^[a-f0-9]{64}$'
    ),
    source_status text not null check (source_status = 'needs_review'),
    observed_at timestamptz not null,
    trust_snapshot_expires_at timestamptz not null,
    private_content_only boolean not null check (private_content_only),
    database_currentness_required boolean not null check (
        database_currentness_required
    ),
    automatic_publication boolean not null check (not automatic_publication),
    payload jsonb not null,
    lineage_sha256 text not null check (lineage_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, lineage_receipt_id),
    unique (workspace_id, client_id, plan_id),
    unique (workspace_id, client_id, lineage_sha256),
    unique (
        workspace_id, client_id, lineage_receipt_id, lineage_sha256
    ),
    foreign key (workspace_id, client_id, round_id)
        references agent_runtime.harmony_rounds(
            workspace_id, client_id, round_id
        ) on delete restrict,
    foreign key (
        workspace_id, client_id, plan_receipt_id, plan_id, round_id
    ) references agent_runtime.harmony_stage_receipts(
        workspace_id, client_id, receipt_id, plan_id, round_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, private_content_receipt_id,
        plan_id, round_id
    ) references agent_runtime.harmony_stage_receipts(
        workspace_id, client_id, receipt_id, plan_id, round_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id,
        private_content_specialist_binding_sha256
    ) references private.harmony_preview_squid_specialist_bindings(
        workspace_id, client_id, binding_sha256
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, reviewer_specialist_binding_sha256
    ) references private.harmony_preview_squid_specialist_bindings(
        workspace_id, client_id, binding_sha256
    ) on delete restrict,
    foreign key (branch_ref)
        references private.harmony_preview_environment_fence(branch_ref)
        on delete restrict,
    check (pg_catalog.jsonb_typeof(signal_manifest) = 'array'),
    check (pg_catalog.jsonb_array_length(signal_manifest) = 4),
    check (signal_manifest_sha256 = signal_input_set_sha256),
    check (signal_manifest_sha256 = private.agent_json_sha256(signal_manifest)),
    check (pg_catalog.jsonb_typeof(trust_manifest) = 'array'),
    check (pg_catalog.jsonb_array_length(trust_manifest) = 4),
    check (trust_manifest_sha256 = private.agent_json_sha256(trust_manifest)),
    check (source_producer_principal_id = any(signal_producer_principal_ids)),
    check (branch_fence_created_at <= observed_at),
    check (observed_at < trust_snapshot_expires_at),
    check (trust_snapshot_expires_at <= branch_fence_expires_at),
    check (source_signal_expires_at >= trust_snapshot_expires_at),
    check (payload ->> 'schema_version'
        = 'squid-codex-source-lineage-receipt@1'),
    check (payload ->> 'lineage_receipt_id' = lineage_receipt_id::text),
    check (payload ->> 'workspace_id' = workspace_id::text),
    check (payload ->> 'client_id' = client_id),
    check (payload ->> 'round_id' = round_id::text),
    check (payload ->> 'plan_id' = plan_id::text),
    check (payload ->> 'lineage_sha256' = lineage_sha256),
    check (lineage_sha256 = private.agent_json_sha256(
        payload - 'lineage_sha256'
    )),
    check (payload -> 'private_content_only' = 'true'::jsonb),
    check (payload -> 'database_currentness_required' = 'true'::jsonb),
    check (payload -> 'automatic_publication' = 'false'::jsonb)
);

create table private.harmony_preview_codex_gate_requests (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    request_id uuid not null default extensions.gen_random_uuid(),
    lineage_receipt_id uuid not null,
    lineage_sha256 text not null check (lineage_sha256 ~ '^[a-f0-9]{64}$'),
    round_id uuid not null,
    plan_id uuid not null,
    stage text not null check (stage = 'independent_qa'),
    work_key text not null check (work_key ~ '^[a-f0-9]{64}$'),
    assignment_key text not null check (assignment_key ~ '^[a-f0-9]{64}$'),
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    reviewer_principal_id uuid not null,
    reviewer_specialist_binding_sha256 text not null check (
        reviewer_specialist_binding_sha256 ~ '^[a-f0-9]{64}$'
    ),
    reviewer_release_sha text not null check (
        reviewer_release_sha ~ '^[a-f0-9]{40}$'
    ),
    reviewer_config_sha256 text not null check (
        reviewer_config_sha256 ~ '^[a-f0-9]{64}$'
    ),
    approved_cost_cap_microusd bigint not null check (
        approved_cost_cap_microusd = 0
    ),
    submitted_at timestamptz not null,
    effective_expires_at timestamptz not null,
    automatic_publication boolean not null check (not automatic_publication),
    provider_calls boolean not null check (not provider_calls),
    external_calls boolean not null check (not external_calls),
    publication_calls boolean not null check (not publication_calls),
    payload jsonb not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, request_id),
    unique (workspace_id, client_id, plan_id, stage),
    unique (workspace_id, client_id, work_key),
    unique (workspace_id, client_id, assignment_key),
    unique (workspace_id, client_id, request_key),
    unique (workspace_id, client_id, request_id, request_key),
    foreign key (
        workspace_id, client_id, lineage_receipt_id, lineage_sha256
    ) references private.harmony_preview_codex_source_lineage_receipts(
        workspace_id, client_id, lineage_receipt_id, lineage_sha256
    ) on delete restrict,
    check (submitted_at < effective_expires_at),
    check (payload ->> 'schema_version' = 'squid-codex-gate-request@1'),
    check (payload ->> 'request_id' = request_id::text),
    check (payload ->> 'work_key' = work_key),
    check (payload ->> 'assignment_key' = assignment_key),
    check (payload ->> 'request_key' = request_key),
    check (payload ->> 'payload_sha256' = payload_sha256),
    check (payload_sha256 = private.agent_json_sha256(
        payload - 'payload_sha256'
    )),
    check (payload -> 'automatic_publication' = 'false'::jsonb),
    check (payload -> 'provider_calls' = 'false'::jsonb),
    check (payload -> 'external_calls' = 'false'::jsonb),
    check (payload -> 'publication_calls' = 'false'::jsonb)
);

-- The run is the only mutable projection.  Every mutation is paired with an
-- immutable transition receipt in the same transaction; callers never get
-- direct access to this table.
create table private.harmony_preview_codex_gate_runs (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    work_key text not null check (work_key ~ '^[a-f0-9]{64}$'),
    request_id uuid not null,
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    status text not null check (status in (
        'pending', 'claimed', 'attempt_started', 'result_submitted',
        'verified', 'operator_review_pending', 'needs_changes',
        'blocked', 'outcome_unknown'
    )),
    status_version integer not null check (status_version >= 1),
    claim_attempt integer not null default 0 check (
        claim_attempt >= 0 and claim_attempt <= 3
    ),
    claim_receipt_id uuid,
    claim_fence_sha256 text check (
        claim_fence_sha256 is null
        or claim_fence_sha256 ~ '^[a-f0-9]{64}$'
    ),
    claimed_at timestamptz,
    lease_expires_at timestamptz,
    attempt_receipt_id uuid,
    attempt_fence_sha256 text check (
        attempt_fence_sha256 is null
        or attempt_fence_sha256 ~ '^[a-f0-9]{64}$'
    ),
    attempt_started_at timestamptz,
    result_receipt_id uuid,
    result_submitted_at timestamptz,
    verification_receipt_id uuid,
    last_event_sha256 text not null check (
        last_event_sha256 ~ '^[a-f0-9]{64}$'
    ),
    updated_at timestamptz not null,
    primary key (workspace_id, client_id, work_key),
    unique (workspace_id, client_id, request_id),
    foreign key (
        workspace_id, client_id, request_id, request_key
    ) references private.harmony_preview_codex_gate_requests(
        workspace_id, client_id, request_id, request_key
    ) on delete restrict,
    check (
        (status = 'pending' and lease_expires_at is null)
        or status <> 'pending'
    ),
    check (
        (attempt_started_at is null and attempt_receipt_id is null
            and attempt_fence_sha256 is null)
        or (attempt_started_at is not null and attempt_receipt_id is not null
            and attempt_fence_sha256 is not null)
    ),
    check (
        (result_submitted_at is null and result_receipt_id is null)
        or (result_submitted_at is not null and result_receipt_id is not null)
    )
);

create table private.harmony_preview_codex_gate_transitions (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    transition_id uuid not null default extensions.gen_random_uuid(),
    request_id uuid not null,
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    work_key text not null check (work_key ~ '^[a-f0-9]{64}$'),
    event_seq integer not null check (event_seq >= 1),
    transition_seq integer not null check (transition_seq >= 1),
    transition_kind text not null check (transition_kind in (
        'prepare', 'claim', 'start_attempt', 'submit_result',
        'verify_result', 'reconcile', 'stage_link'
    )),
    from_state text check (from_state is null or from_state in (
        'pending', 'claimed', 'attempt_started', 'result_submitted',
        'verified', 'operator_review_pending', 'needs_changes',
        'blocked', 'outcome_unknown'
    )),
    to_state text not null check (to_state in (
        'pending', 'claimed', 'attempt_started', 'result_submitted',
        'verified', 'operator_review_pending', 'needs_changes',
        'blocked', 'outcome_unknown'
    )),
    terminal_reason text check (terminal_reason is null or terminal_reason in (
        'claim_limit_exhausted', 'result_needs_changes', 'result_blocked',
        'result_receipt_missing', 'request_not_current'
    )),
    occurred_at timestamptz not null,
    previous_event_sha256 text check (
        previous_event_sha256 is null
        or previous_event_sha256 ~ '^[a-f0-9]{64}$'
    ),
    payload jsonb not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    event_sha256 text not null check (event_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, transition_id),
    unique (workspace_id, client_id, request_id, transition_seq),
    unique (workspace_id, client_id, work_key, event_seq),
    unique (workspace_id, client_id, event_sha256),
    unique (workspace_id, client_id, request_id, transition_id),
    foreign key (
        workspace_id, client_id, request_id, request_key
    ) references private.harmony_preview_codex_gate_requests(
        workspace_id, client_id, request_id, request_key
    ) on delete restrict,
    check ((transition_seq = 1) = (from_state is null)),
    check (event_seq = transition_seq),
    check ((event_seq = 1) = (previous_event_sha256 is null)),
    check ((transition_kind = 'prepare') = (transition_seq = 1)),
    check (payload ->> 'schema_version'
        = 'squid-codex-gate-transition@1'),
    check (payload ->> 'transition_id' = transition_id::text),
    check (payload ->> 'request_id' = request_id::text),
    check (payload ->> 'request_key' = request_key),
    check (payload ->> 'work_key' = work_key),
    check ((payload ->> 'event_seq')::integer = event_seq),
    check ((payload ->> 'transition_seq')::integer = transition_seq),
    check (payload ->> 'transition_kind' = transition_kind),
    check (payload ->> 'from_state' is not distinct from from_state),
    check (payload ->> 'to_state' = to_state),
    check (payload ->> 'terminal_reason' is not distinct from terminal_reason),
    check (payload ->> 'payload_sha256' = payload_sha256),
    check (payload_sha256 = private.agent_json_sha256(
        payload - 'payload_sha256'
    )),
    check (event_sha256 = private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'event_seq', event_seq,
        'payload_sha256', payload_sha256,
        'previous_event_sha256', previous_event_sha256,
        'request_key', request_key,
        'work_key', work_key
    )))
);

create table private.harmony_preview_codex_gate_claim_receipts (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    claim_receipt_id uuid not null default extensions.gen_random_uuid(),
    request_id uuid not null,
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    transition_id uuid not null,
    claim_attempt integer not null check (claim_attempt between 1 and 3),
    reviewer_principal_id uuid not null,
    claimed_at timestamptz not null,
    lease_expires_at timestamptz not null,
    claim_fence_sha256 text not null check (
        claim_fence_sha256 ~ '^[a-f0-9]{64}$'
    ),
    payload jsonb not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, claim_receipt_id),
    unique (workspace_id, client_id, request_id, claim_attempt),
    unique (workspace_id, client_id, request_id, claim_fence_sha256),
    unique (
        workspace_id, client_id, request_id,
        claim_receipt_id, claim_fence_sha256
    ),
    foreign key (
        workspace_id, client_id, request_id, request_key
    ) references private.harmony_preview_codex_gate_requests(
        workspace_id, client_id, request_id, request_key
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id, transition_id
    ) references private.harmony_preview_codex_gate_transitions(
        workspace_id, client_id, request_id, transition_id
    ) on delete restrict,
    check (claimed_at < lease_expires_at),
    check (lease_expires_at - claimed_at <= interval '15 minutes'),
    check (payload ->> 'schema_version' = 'squid-codex-claim-receipt@1'),
    check (payload ->> 'claim_receipt_id' = claim_receipt_id::text),
    check (payload ->> 'claim_fence_sha256' = claim_fence_sha256),
    check (payload ->> 'payload_sha256' = payload_sha256),
    check (payload_sha256 = private.agent_json_sha256(
        payload - 'payload_sha256'
    ))
);

create table private.harmony_preview_codex_gate_attempt_receipts (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    attempt_receipt_id uuid not null default extensions.gen_random_uuid(),
    request_id uuid not null,
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    transition_id uuid not null,
    claim_receipt_id uuid not null,
    claim_fence_sha256 text not null check (
        claim_fence_sha256 ~ '^[a-f0-9]{64}$'
    ),
    attempt_started_at timestamptz not null,
    attempt_fence_sha256 text not null check (
        attempt_fence_sha256 ~ '^[a-f0-9]{64}$'
    ),
    execute_authorized boolean not null check (execute_authorized),
    payload jsonb not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, attempt_receipt_id),
    unique (workspace_id, client_id, request_id),
    unique (workspace_id, client_id, request_id, attempt_fence_sha256),
    unique (
        workspace_id, client_id, request_id,
        attempt_receipt_id, attempt_fence_sha256
    ),
    foreign key (
        workspace_id, client_id, request_id, request_key
    ) references private.harmony_preview_codex_gate_requests(
        workspace_id, client_id, request_id, request_key
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id, transition_id
    ) references private.harmony_preview_codex_gate_transitions(
        workspace_id, client_id, request_id, transition_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id,
        claim_receipt_id, claim_fence_sha256
    ) references private.harmony_preview_codex_gate_claim_receipts(
        workspace_id, client_id, request_id,
        claim_receipt_id, claim_fence_sha256
    ) on delete restrict,
    check (payload ->> 'schema_version' = 'squid-codex-attempt-receipt@1'),
    check (payload ->> 'attempt_receipt_id' = attempt_receipt_id::text),
    check (payload ->> 'attempt_fence_sha256' = attempt_fence_sha256),
    check (payload -> 'execute_authorized' = 'true'::jsonb),
    check (payload ->> 'payload_sha256' = payload_sha256),
    check (payload_sha256 = private.agent_json_sha256(
        payload - 'payload_sha256'
    ))
);

create table private.harmony_preview_codex_semantic_qa_evidence (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    evidence_id uuid not null default extensions.gen_random_uuid(),
    request_id uuid not null,
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    attempt_receipt_id uuid not null,
    attempt_fence_sha256 text not null check (
        attempt_fence_sha256 ~ '^[a-f0-9]{64}$'
    ),
    source_lineage_sha256 text not null check (
        source_lineage_sha256 ~ '^[a-f0-9]{64}$'
    ),
    private_content_receipt_sha256 text not null check (
        private_content_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    reviewed_output_sha256 text not null check (
        reviewed_output_sha256 ~ '^[a-f0-9]{64}$'
    ),
    official_content_version_id uuid not null,
    official_source_item_id uuid not null,
    official_source_binding_sha256 text not null check (
        official_source_binding_sha256 ~ '^[a-f0-9]{64}$'
    ),
    content_snapshot_sha256 text not null check (
        content_snapshot_sha256 ~ '^[a-f0-9]{64}$'
    ),
    reviewer_principal_id uuid not null,
    reviewer_specialist_binding_sha256 text not null check (
        reviewer_specialist_binding_sha256 ~ '^[a-f0-9]{64}$'
    ),
    reviewer_release_sha text not null check (
        reviewer_release_sha ~ '^[a-f0-9]{40}$'
    ),
    reviewer_config_sha256 text not null check (
        reviewer_config_sha256 ~ '^[a-f0-9]{64}$'
    ),
    qa_output_sha256 text not null check (qa_output_sha256 ~ '^[a-f0-9]{64}$'),
    criteria jsonb not null,
    finding_codes text[] not null,
    verdict text not null check (verdict in (
        'pass', 'needs_changes', 'blocked'
    )),
    verifier_contract_version text not null check (
        verifier_contract_version = 'squid-codex-semantic-qa@1'
    ),
    recorded_at timestamptz not null,
    raw_private_content_included boolean not null check (
        not raw_private_content_included
    ),
    credentials_included boolean not null check (not credentials_included),
    automatic_publication boolean not null check (not automatic_publication),
    provider_calls boolean not null check (not provider_calls),
    external_calls boolean not null check (not external_calls),
    publication_calls boolean not null check (not publication_calls),
    payload jsonb not null,
    evidence_sha256 text not null check (evidence_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, evidence_id),
    unique (workspace_id, client_id, request_id),
    unique (workspace_id, client_id, evidence_sha256),
    unique (
        workspace_id, client_id, request_id,
        evidence_id, evidence_sha256
    ),
    foreign key (
        workspace_id, client_id, request_id, request_key
    ) references private.harmony_preview_codex_gate_requests(
        workspace_id, client_id, request_id, request_key
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id,
        attempt_receipt_id, attempt_fence_sha256
    ) references private.harmony_preview_codex_gate_attempt_receipts(
        workspace_id, client_id, request_id,
        attempt_receipt_id, attempt_fence_sha256
    ) on delete restrict,
    check (pg_catalog.jsonb_typeof(criteria) = 'object'),
    check (payload ->> 'schema_version'
        = 'squid-codex-semantic-qa-evidence@1'),
    check (payload ->> 'evidence_id' = evidence_id::text),
    check (payload ->> 'evidence_sha256' = evidence_sha256),
    check (evidence_sha256 = private.agent_json_sha256(
        payload - 'evidence_sha256'
    )),
    check (payload -> 'raw_private_content_included' = 'false'::jsonb),
    check (payload -> 'credentials_included' = 'false'::jsonb),
    check (payload -> 'automatic_publication' = 'false'::jsonb),
    check (payload -> 'provider_calls' = 'false'::jsonb),
    check (payload -> 'external_calls' = 'false'::jsonb),
    check (payload -> 'publication_calls' = 'false'::jsonb)
);

create table private.harmony_preview_codex_gate_result_receipts (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    result_receipt_id uuid not null default extensions.gen_random_uuid(),
    request_id uuid not null,
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    work_key text not null check (work_key ~ '^[a-f0-9]{64}$'),
    assignment_key text not null check (assignment_key ~ '^[a-f0-9]{64}$'),
    transition_id uuid not null,
    attempt_receipt_id uuid not null,
    attempt_fence_sha256 text not null check (
        attempt_fence_sha256 ~ '^[a-f0-9]{64}$'
    ),
    evidence_id uuid not null,
    evidence_sha256 text not null check (evidence_sha256 ~ '^[a-f0-9]{64}$'),
    qa_output_sha256 text not null check (qa_output_sha256 ~ '^[a-f0-9]{64}$'),
    verdict text not null check (verdict in (
        'pass', 'needs_changes', 'blocked'
    )),
    approved_cost_cap_microusd bigint not null check (
        approved_cost_cap_microusd = 0
    ),
    cost_observation text not null check (cost_observation in (
        'observed', 'unobserved'
    )),
    observed_cost_microusd bigint check (
        observed_cost_microusd is null or observed_cost_microusd >= 0
    ),
    recorded_at timestamptz not null,
    automatic_publication boolean not null check (not automatic_publication),
    provider_calls boolean not null check (not provider_calls),
    external_calls boolean not null check (not external_calls),
    publication_calls boolean not null check (not publication_calls),
    payload jsonb not null,
    receipt_sha256 text not null check (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, result_receipt_id),
    unique (workspace_id, client_id, request_id),
    unique (workspace_id, client_id, receipt_sha256),
    unique (
        workspace_id, client_id, request_id, result_receipt_id
    ),
    unique (
        workspace_id, client_id, request_id,
        result_receipt_id, receipt_sha256
    ),
    foreign key (
        workspace_id, client_id, request_id, request_key
    ) references private.harmony_preview_codex_gate_requests(
        workspace_id, client_id, request_id, request_key
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id, transition_id
    ) references private.harmony_preview_codex_gate_transitions(
        workspace_id, client_id, request_id, transition_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id,
        attempt_receipt_id, attempt_fence_sha256
    ) references private.harmony_preview_codex_gate_attempt_receipts(
        workspace_id, client_id, request_id,
        attempt_receipt_id, attempt_fence_sha256
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id, evidence_id, evidence_sha256
    ) references private.harmony_preview_codex_semantic_qa_evidence(
        workspace_id, client_id, request_id, evidence_id, evidence_sha256
    ) on delete restrict,
    check (
        (cost_observation = 'observed'
            and observed_cost_microusd is not null
            and observed_cost_microusd <= approved_cost_cap_microusd)
        or (cost_observation = 'unobserved'
            and observed_cost_microusd is null)
    ),
    check (payload ->> 'schema_version' = 'squid-codex-gate-result@1'),
    check (payload ->> 'result_receipt_id' = result_receipt_id::text),
    check (payload ->> 'work_key' = work_key),
    check (payload ->> 'assignment_key' = assignment_key),
    check (payload ->> 'receipt_sha256' = receipt_sha256),
    check (receipt_sha256 = private.agent_json_sha256(
        payload - 'receipt_sha256'
    )),
    check (payload -> 'automatic_publication' = 'false'::jsonb),
    check (payload -> 'provider_calls' = 'false'::jsonb),
    check (payload -> 'external_calls' = 'false'::jsonb),
    check (payload -> 'publication_calls' = 'false'::jsonb)
);

create table private.harmony_preview_codex_gate_verification_receipts (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    verification_receipt_id uuid not null default extensions.gen_random_uuid(),
    request_id uuid not null,
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    transition_id uuid not null,
    result_receipt_id uuid not null,
    result_receipt_sha256 text not null check (
        result_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    evidence_id uuid not null,
    evidence_sha256 text not null check (evidence_sha256 ~ '^[a-f0-9]{64}$'),
    verification_outcome text not null check (verification_outcome in (
        'passed', 'needs_changes', 'blocked'
    )),
    verified_at timestamptz not null,
    automatic_publication boolean not null check (not automatic_publication),
    external_calls boolean not null check (not external_calls),
    publication_calls boolean not null check (not publication_calls),
    payload jsonb not null,
    receipt_sha256 text not null check (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, verification_receipt_id),
    unique (workspace_id, client_id, request_id),
    unique (workspace_id, client_id, receipt_sha256),
    unique (
        workspace_id, client_id, request_id,
        verification_receipt_id, receipt_sha256
    ),
    foreign key (
        workspace_id, client_id, request_id, request_key
    ) references private.harmony_preview_codex_gate_requests(
        workspace_id, client_id, request_id, request_key
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id, transition_id
    ) references private.harmony_preview_codex_gate_transitions(
        workspace_id, client_id, request_id, transition_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id,
        result_receipt_id, result_receipt_sha256
    ) references private.harmony_preview_codex_gate_result_receipts(
        workspace_id, client_id, request_id,
        result_receipt_id, receipt_sha256
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id, evidence_id, evidence_sha256
    ) references private.harmony_preview_codex_semantic_qa_evidence(
        workspace_id, client_id, request_id, evidence_id, evidence_sha256
    ) on delete restrict,
    check (payload ->> 'schema_version'
        = 'squid-codex-gate-verification@1'),
    check (payload ->> 'verification_receipt_id'
        = verification_receipt_id::text),
    check (payload ->> 'receipt_sha256' = receipt_sha256),
    check (receipt_sha256 = private.agent_json_sha256(
        payload - 'receipt_sha256'
    )),
    check (payload -> 'automatic_publication' = 'false'::jsonb),
    check (payload -> 'external_calls' = 'false'::jsonb),
    check (payload -> 'publication_calls' = 'false'::jsonb)
);

create table private.harmony_preview_codex_gate_reconciliation_receipts (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    reconciliation_receipt_id uuid not null default extensions.gen_random_uuid(),
    request_id uuid not null,
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    transition_id uuid not null,
    claim_receipt_id uuid,
    attempt_receipt_id uuid,
    result_receipt_id uuid,
    reconciliation_action text not null check (reconciliation_action in (
        'claim_released', 'claim_limit_exhausted', 'outcome_unknown',
        'request_not_current', 'result_not_current'
    )),
    reconciled_at timestamptz not null,
    payload jsonb not null,
    receipt_sha256 text not null check (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, reconciliation_receipt_id),
    unique (workspace_id, client_id, transition_id),
    unique (workspace_id, client_id, receipt_sha256),
    foreign key (
        workspace_id, client_id, request_id, request_key
    ) references private.harmony_preview_codex_gate_requests(
        workspace_id, client_id, request_id, request_key
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id, transition_id
    ) references private.harmony_preview_codex_gate_transitions(
        workspace_id, client_id, request_id, transition_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, claim_receipt_id
    ) references private.harmony_preview_codex_gate_claim_receipts(
        workspace_id, client_id, claim_receipt_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, attempt_receipt_id
    ) references private.harmony_preview_codex_gate_attempt_receipts(
        workspace_id, client_id, attempt_receipt_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id, result_receipt_id
    ) references private.harmony_preview_codex_gate_result_receipts(
        workspace_id, client_id, request_id, result_receipt_id
    ) on delete restrict,
    check (
        (reconciliation_action = 'request_not_current'
            and attempt_receipt_id is null
            and result_receipt_id is null)
        or (reconciliation_action = 'result_not_current'
            and claim_receipt_id is not null
            and attempt_receipt_id is not null
            and result_receipt_id is not null)
        or (reconciliation_action = 'outcome_unknown'
            and claim_receipt_id is not null
            and attempt_receipt_id is not null
            and result_receipt_id is null)
        or (reconciliation_action in (
                'claim_released', 'claim_limit_exhausted'
            )
            and claim_receipt_id is not null
            and attempt_receipt_id is null
            and result_receipt_id is null)
    ),
    check (payload ->> 'schema_version'
        = 'squid-codex-gate-reconciliation@1'),
    check (payload ->> 'reconciliation_receipt_id'
        = reconciliation_receipt_id::text),
    check (payload ->> 'claim_receipt_id'
        is not distinct from claim_receipt_id::text),
    check (payload ->> 'attempt_receipt_id'
        is not distinct from attempt_receipt_id::text),
    check (payload ->> 'result_receipt_id'
        is not distinct from result_receipt_id::text),
    check (payload ->> 'reconciliation_action'
        = reconciliation_action),
    check (payload ->> 'receipt_sha256' = receipt_sha256),
    check (receipt_sha256 = private.agent_json_sha256(
        payload - 'receipt_sha256'
    ))
);

create table private.harmony_preview_codex_gate_stage_links (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'squid'),
    stage_link_id uuid not null default extensions.gen_random_uuid(),
    request_id uuid not null,
    request_key text not null check (request_key ~ '^[a-f0-9]{64}$'),
    transition_id uuid not null,
    verification_receipt_id uuid not null,
    verification_receipt_sha256 text not null check (
        verification_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    result_receipt_id uuid not null,
    result_receipt_sha256 text not null check (
        result_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    stage_receipt_id uuid not null,
    round_id uuid not null,
    plan_id uuid not null,
    stage_receipt_sha256 text not null check (
        stage_receipt_sha256 ~ '^[a-f0-9]{64}$'
    ),
    linked_at timestamptz not null,
    payload jsonb not null,
    receipt_sha256 text not null check (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    primary key (workspace_id, client_id, stage_link_id),
    unique (workspace_id, client_id, request_id),
    unique (workspace_id, client_id, verification_receipt_id),
    unique (workspace_id, client_id, stage_receipt_id),
    unique (workspace_id, client_id, receipt_sha256),
    foreign key (
        workspace_id, client_id, request_id, request_key
    ) references private.harmony_preview_codex_gate_requests(
        workspace_id, client_id, request_id, request_key
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id, transition_id
    ) references private.harmony_preview_codex_gate_transitions(
        workspace_id, client_id, request_id, transition_id
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id,
        verification_receipt_id, verification_receipt_sha256
    ) references private.harmony_preview_codex_gate_verification_receipts(
        workspace_id, client_id, request_id,
        verification_receipt_id, receipt_sha256
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, request_id,
        result_receipt_id, result_receipt_sha256
    ) references private.harmony_preview_codex_gate_result_receipts(
        workspace_id, client_id, request_id,
        result_receipt_id, receipt_sha256
    ) on delete restrict,
    foreign key (
        workspace_id, client_id, stage_receipt_id, plan_id, round_id
    ) references agent_runtime.harmony_stage_receipts(
        workspace_id, client_id, receipt_id, plan_id, round_id
    ) on delete restrict,
    check (payload ->> 'schema_version' = 'squid-codex-stage-link@1'),
    check (payload ->> 'stage_link_id' = stage_link_id::text),
    check (payload ->> 'receipt_sha256' = receipt_sha256),
    check (receipt_sha256 = private.agent_json_sha256(
        payload - 'receipt_sha256'
    )),
    check (payload -> 'automatic_publication' = 'false'::jsonb),
    check (payload -> 'operator_decision_recorded' = 'false'::jsonb),
    check (payload -> 'external_calls' = 'false'::jsonb),
    check (payload -> 'publication_calls' = 'false'::jsonb)
);

alter table private.harmony_preview_codex_source_lineage_receipts
    enable row level security;
alter table private.harmony_preview_codex_source_lineage_receipts
    force row level security;
alter table private.harmony_preview_codex_gate_requests
    enable row level security;
alter table private.harmony_preview_codex_gate_requests
    force row level security;
alter table private.harmony_preview_codex_gate_runs
    enable row level security;
alter table private.harmony_preview_codex_gate_runs
    force row level security;
alter table private.harmony_preview_codex_gate_transitions
    enable row level security;
alter table private.harmony_preview_codex_gate_transitions
    force row level security;
alter table private.harmony_preview_codex_gate_claim_receipts
    enable row level security;
alter table private.harmony_preview_codex_gate_claim_receipts
    force row level security;
alter table private.harmony_preview_codex_gate_attempt_receipts
    enable row level security;
alter table private.harmony_preview_codex_gate_attempt_receipts
    force row level security;
alter table private.harmony_preview_codex_semantic_qa_evidence
    enable row level security;
alter table private.harmony_preview_codex_semantic_qa_evidence
    force row level security;
alter table private.harmony_preview_codex_gate_result_receipts
    enable row level security;
alter table private.harmony_preview_codex_gate_result_receipts
    force row level security;
alter table private.harmony_preview_codex_gate_verification_receipts
    enable row level security;
alter table private.harmony_preview_codex_gate_verification_receipts
    force row level security;
alter table private.harmony_preview_codex_gate_reconciliation_receipts
    enable row level security;
alter table private.harmony_preview_codex_gate_reconciliation_receipts
    force row level security;
alter table private.harmony_preview_codex_gate_stage_links
    enable row level security;
alter table private.harmony_preview_codex_gate_stage_links
    force row level security;

revoke all on table
    private.harmony_preview_codex_source_lineage_receipts,
    private.harmony_preview_codex_gate_requests,
    private.harmony_preview_codex_gate_runs,
    private.harmony_preview_codex_gate_transitions,
    private.harmony_preview_codex_gate_claim_receipts,
    private.harmony_preview_codex_gate_attempt_receipts,
    private.harmony_preview_codex_semantic_qa_evidence,
    private.harmony_preview_codex_gate_result_receipts,
    private.harmony_preview_codex_gate_verification_receipts,
    private.harmony_preview_codex_gate_reconciliation_receipts,
    private.harmony_preview_codex_gate_stage_links
from public, anon, authenticated, service_role,
    coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_qa,
    coineasy_harmony_operator, coineasy_harmony_recap,
    coineasy_harmony_dashboard;

-- Keep the broad revoke in separately matchable statements as a static and
-- human-auditable proof that even service_role has no direct ledger access.
revoke all on table private.harmony_preview_codex_source_lineage_receipts
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_gate_requests
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_gate_runs
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_gate_transitions
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_gate_claim_receipts
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_gate_attempt_receipts
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_semantic_qa_evidence
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_gate_result_receipts
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_gate_verification_receipts
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_gate_reconciliation_receipts
from public, anon, authenticated, service_role;
revoke all on table private.harmony_preview_codex_gate_stage_links
from public, anon, authenticated, service_role;

create trigger harmony_preview_codex_source_lineage_immutable
before update or delete
on private.harmony_preview_codex_source_lineage_receipts
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_codex_requests_immutable
before update or delete on private.harmony_preview_codex_gate_requests
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_codex_transitions_immutable
before update or delete on private.harmony_preview_codex_gate_transitions
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_codex_claims_immutable
before update or delete on private.harmony_preview_codex_gate_claim_receipts
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_codex_attempts_immutable
before update or delete on private.harmony_preview_codex_gate_attempt_receipts
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_codex_evidence_immutable
before update or delete on private.harmony_preview_codex_semantic_qa_evidence
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_codex_results_immutable
before update or delete on private.harmony_preview_codex_gate_result_receipts
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_codex_verifications_immutable
before update or delete
on private.harmony_preview_codex_gate_verification_receipts
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_codex_reconciliations_immutable
before update or delete
on private.harmony_preview_codex_gate_reconciliation_receipts
for each row execute function private.agent_immutable_row();
create trigger harmony_preview_codex_stage_links_immutable
before update or delete on private.harmony_preview_codex_gate_stage_links
for each row execute function private.agent_immutable_row();

-- Canonical, target-time connector trust projection.  Unlike the older
-- currentness helper this function never reads statement_timestamp(); callers
-- pass a post-lock clock_timestamp() value.
create or replace function private.harmony_preview_codex_trust_manifest(
    target_workspace_id uuid,
    target_client_id text,
    target_signal_manifest jsonb,
    target_at timestamptz
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select case when pg_catalog.count(*) = 4
                     and pg_catalog.count(distinct signal.lane) = 4
                     and pg_catalog.count(distinct signal.producer_principal_id) = 4
                then pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                    'branch_ref', registration.branch_ref,
                    'connector_receipt_expires_at',
                        private.harmony_preview_codex_timestamp(receipt.expires_at),
                    'connector_receipt_id', receipt.receipt_id::text,
                    'connector_receipt_sha256', receipt.payload_sha256,
                    'connector_request_expires_at',
                        private.harmony_preview_codex_timestamp(request.expires_at),
                    'connector_request_receipt_id',
                        request.request_receipt_id::text,
                    'connector_request_receipt_sha256', request.payload_sha256,
                    'lane', signal.lane,
                    'producer_principal_id', signal.producer_principal_id::text,
                    'registration_expires_at',
                        private.harmony_preview_codex_timestamp(
                            registration.expires_at
                        ),
                    'registration_id', registration.registration_id::text,
                    'registration_sha256', registration.registration_sha256,
                    'signal_expires_at',
                        private.harmony_preview_codex_timestamp(signal.expires_at),
                    'signal_id', signal.signal_id::text,
                    'signal_payload_sha256', signal.payload_sha256
                ) order by signal.lane)
                else null end
    from pg_catalog.jsonb_array_elements(target_signal_manifest) entry(value)
    join agent_runtime.harmony_signals signal
      on signal.workspace_id = target_workspace_id
     and signal.client_id = target_client_id
     and signal.signal_id = (entry.value ->> 'signal_id')::uuid
     and signal.payload_sha256 = entry.value ->> 'signal_payload_sha256'
     and signal.lane = entry.value ->> 'lane'
     and signal.upstream_receipt_sha256
            = entry.value ->> 'upstream_receipt_sha256'
    join agent_runtime.harmony_connector_attestation_receipts receipt
      on receipt.workspace_id = signal.workspace_id
     and receipt.client_id = signal.client_id
     and receipt.receipt_id = signal.connector_receipt_id
     and receipt.payload_sha256 = signal.connector_receipt_sha256
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
     and registration.registration_sha256 = request.registration_sha256
     and registration.attestation_key_id = request.attestation_key_id
     and registration.lane = signal.lane
     and registration.producer_principal_id = signal.producer_principal_id
    join private.harmony_preview_environment_fence fence
      on fence.branch_ref = registration.branch_ref
     and fence.active
     and fence.created_at <= target_at
     and fence.expires_at > target_at
    where signal.observed_at <= target_at
      and signal.expires_at > target_at
      and receipt.verified_at <= target_at
      and receipt.expires_at > target_at
      and request.accepted_at <= target_at
      and request.expires_at > target_at
      and registration.created_at <= target_at
      and registration.expires_at > target_at
      and registration.expires_at <= fence.expires_at
      and request.request_sha256
            = private.harmony_preview_connector_request_sha256(
                signal.workspace_id, signal.client_id,
                registration.registration_id, receipt.receipt_id,
                signal.payload
            )
      and not exists (
            select 1
            from private.harmony_preview_connector_registration_revocations revoked
            where revoked.workspace_id = registration.workspace_id
              and revoked.client_id = registration.client_id
              and revoked.registration_id = registration.registration_id
      )
$$;

create or replace function private.harmony_preview_codex_lock_plan_dependencies(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid,
    target_plan_id uuid
)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    manifest jsonb;
    source_version_id uuid;
begin
    select candidate.signal_manifest into strict manifest
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.round_id = target_round_id
      and candidate.plan_id = target_plan_id
      and candidate.status = 'planned'
    for share;
    perform private.harmony_preview_lock_manifest_registrations(
        target_workspace_id, target_client_id, manifest
    );
    perform 1
    from agent_runtime.harmony_stage_receipts receipt
    where receipt.workspace_id = target_workspace_id
      and receipt.client_id = target_client_id
      and receipt.round_id = target_round_id
      and receipt.plan_id = target_plan_id
      and receipt.stage in ('plan', 'private_content')
    order by receipt.ordinal
    for share;
    perform 1
    from private.harmony_preview_squid_specialist_bindings specialist
    where specialist.workspace_id = target_workspace_id
      and specialist.client_id = target_client_id
      and specialist.stage in ('private_content', 'independent_qa')
    order by specialist.stage
    for share;
    perform 1
    from private.harmony_preview_environment_fence fence
    join private.harmony_preview_squid_specialist_bindings specialist
      on specialist.branch_ref = fence.branch_ref
    where specialist.workspace_id = target_workspace_id
      and specialist.client_id = target_client_id
      and specialist.stage = 'independent_qa'
    for share of fence;
    select signal.official_content_version_id into strict source_version_id
    from agent_runtime.harmony_signals signal
    where signal.workspace_id = target_workspace_id
      and signal.client_id = target_client_id
      and signal.lane = 'content_source'
      and signal.payload_sha256 in (
            select entry.value ->> 'signal_payload_sha256'
            from pg_catalog.jsonb_array_elements(manifest) entry(value)
      );
    perform 1
    from public.content_items item
    where item.workspace_id = target_workspace_id
      and item.client_id = target_client_id
      and item.current_version_id = source_version_id
    for share;
    perform 1
    from public.content_versions version
    where version.workspace_id = target_workspace_id
      and version.id = source_version_id
    for share;
exception
    when no_data_found then
        raise exception 'harmony_preview_codex_gate_dependency_missing';
end;
$$;

create or replace function public.prepare_preview_harmony_squid_codex_qa(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid,
    target_plan_id uuid,
    target_approved_cost_cap_microusd bigint default 0
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    round_row agent_runtime.harmony_rounds%rowtype;
    private_stage agent_runtime.harmony_stage_receipts%rowtype;
    existing private.harmony_preview_codex_gate_requests%rowtype;
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    transition_row private.harmony_preview_codex_gate_transitions%rowtype;
    reviewer_binding jsonb;
    lineage_id uuid := extensions.gen_random_uuid();
    request_id uuid := extensions.gen_random_uuid();
    transition_time timestamptz;
    lineage_body jsonb;
    request_body jsonb;
    request_body_sha text;
    work_key text;
    assignment_key text;
    request_key text;
    effective_expires_at timestamptz;
    producer_ids uuid[];
begin
    if target_client_id <> 'squid'
       or target_approved_cost_cap_microusd <> 0
    then
        raise exception 'harmony_preview_codex_gate_scope_invalid';
    end if;
    perform private.harmony_preview_codex_qa_scope_preflight(
        target_workspace_id, target_client_id,
        pg_catalog.clock_timestamp()
    );
    perform private.harmony_preview_codex_lock_tenant(
        target_workspace_id, target_client_id
    );
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_codex_prepare:' || target_workspace_id::text || ':' ||
        target_client_id || ':' || target_plan_id::text,
        0
    ));
    select candidate.* into strict round_row
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.round_id = target_round_id
      and candidate.plan_id = target_plan_id
      and candidate.status = 'planned'
    for update;
    perform private.harmony_preview_lock_manifest_registrations(
        target_workspace_id, target_client_id, round_row.signal_manifest
    );
    perform private.harmony_preview_codex_lock_plan_dependencies(
        target_workspace_id, target_client_id, target_round_id, target_plan_id
    );
    select candidate.* into strict private_stage
    from agent_runtime.harmony_stage_receipts candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.round_id = target_round_id
      and candidate.plan_id = target_plan_id
      and candidate.stage = 'private_content'
    for share;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_qa_outcome:' || target_workspace_id::text || ':' ||
        target_client_id || ':' || target_plan_id::text || ':' ||
        private_stage.output_sha256,
        0
    ));
    transition_time := pg_catalog.clock_timestamp();
    reviewer_binding := private.harmony_preview_codex_qa_binding(
        target_workspace_id, target_client_id, transition_time
    );
    if not private.harmony_preview_round_inputs_current(
        target_workspace_id, target_client_id, round_row.signal_manifest
    ) then
        raise exception 'harmony_preview_plan_input_not_current';
    end if;
    if not private.harmony_preview_qa_actor_independent(
        target_workspace_id, target_client_id, target_plan_id,
        (reviewer_binding ->> 'principal_id')::uuid
    ) then
        raise exception 'harmony_preview_codex_qa_actor_not_independent';
    end if;
    if exists (
        select 1
        from private.harmony_preview_qa_denial_receipts denial
        where denial.workspace_id = target_workspace_id
          and denial.client_id = target_client_id
          and denial.plan_id = target_plan_id
          and denial.denied_output_sha256 = private_stage.output_sha256
    ) then
        raise exception 'harmony_preview_qa_output_already_denied';
    end if;
    select candidate.* into existing
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.plan_id = target_plan_id
      and candidate.stage = 'independent_qa'
    for update;
    if found then
        if existing.round_id is distinct from target_round_id
           or existing.reviewer_principal_id is distinct from
                (reviewer_binding ->> 'principal_id')::uuid
           or existing.reviewer_specialist_binding_sha256 is distinct from
                reviewer_binding ->> 'binding_sha256'
           or existing.reviewer_release_sha is distinct from
                reviewer_binding ->> 'producer_release_sha'
           or existing.reviewer_config_sha256 is distinct from
                reviewer_binding ->> 'config_sha256'
           or existing.approved_cost_cap_microusd is distinct from
                target_approved_cost_cap_microusd
        then
            raise exception 'harmony_preview_codex_gate_assignment_conflict';
        end if;
        if not private.harmony_preview_codex_request_current(
            existing.request_id, transition_time
        ) then
            raise exception 'harmony_preview_codex_gate_not_current';
        end if;
        return pg_catalog.jsonb_build_object(
            'request_key', existing.request_key,
            'reused', true,
            'status', (select run.status
                       from private.harmony_preview_codex_gate_runs run
                       where run.request_id = existing.request_id),
            'work_key', existing.work_key
        );
    end if;
    lineage_body := private.harmony_preview_codex_build_source_lineage(
        target_workspace_id, target_client_id, target_round_id,
        target_plan_id, reviewer_binding, transition_time, lineage_id
    );
    effective_expires_at := (
        lineage_body ->> 'trust_snapshot_expires_at'
    )::timestamptz;
    work_key := private.harmony_preview_codex_work_key(lineage_body);
    assignment_key := private.harmony_preview_codex_assignment_key(
        work_key, reviewer_binding
    );
    request_key := private.harmony_preview_codex_request_key(
        work_key, assignment_key, lineage_body ->> 'lineage_sha256',
        effective_expires_at, target_approved_cost_cap_microusd
    );
    select pg_catalog.array_agg(value::uuid order by value)
    into strict producer_ids
    from pg_catalog.jsonb_array_elements_text(
        lineage_body -> 'signal_producer_principal_ids'
    ) producer(value);
    insert into private.harmony_preview_codex_source_lineage_receipts (
        workspace_id, client_id, lineage_receipt_id, round_id, plan_id,
        branch_ref, branch_fence_created_at, branch_fence_expires_at,
        plan_receipt_id, plan_receipt_sha256, private_content_receipt_id,
        private_content_receipt_sha256, private_content_output_sha256,
        private_content_principal_id,
        private_content_specialist_binding_sha256, reviewer_principal_id,
        reviewer_specialist_binding_sha256, reviewer_release_sha,
        reviewer_config_sha256, signal_manifest, signal_manifest_sha256,
        signal_input_set_sha256, signal_producer_principal_ids,
        trust_manifest, trust_manifest_sha256, source_signal_id,
        source_signal_payload_sha256, source_producer_principal_id,
        source_signal_expires_at, source_connector_receipt_id,
        source_connector_receipt_sha256, source_request_receipt_id,
        source_request_receipt_sha256, official_content_version_id,
        official_source_item_id, official_source_binding_sha256,
        content_snapshot_sha256, source_status, observed_at,
        trust_snapshot_expires_at, private_content_only,
        database_currentness_required, automatic_publication,
        payload, lineage_sha256
    ) values (
        target_workspace_id, target_client_id, lineage_id, target_round_id,
        target_plan_id, lineage_body ->> 'branch_ref',
        (lineage_body ->> 'branch_fence_created_at')::timestamptz,
        (lineage_body ->> 'branch_fence_expires_at')::timestamptz,
        (lineage_body ->> 'plan_receipt_id')::uuid,
        lineage_body ->> 'plan_receipt_sha256',
        (lineage_body ->> 'private_content_receipt_id')::uuid,
        lineage_body ->> 'private_content_receipt_sha256',
        lineage_body ->> 'private_content_output_sha256',
        (lineage_body ->> 'private_content_principal_id')::uuid,
        lineage_body ->> 'private_content_specialist_binding_sha256',
        (lineage_body ->> 'reviewer_principal_id')::uuid,
        lineage_body ->> 'reviewer_specialist_binding_sha256',
        lineage_body ->> 'reviewer_release_sha',
        lineage_body ->> 'reviewer_config_sha256',
        lineage_body -> 'signal_manifest',
        lineage_body ->> 'signal_manifest_sha256',
        lineage_body ->> 'signal_input_set_sha256', producer_ids,
        lineage_body -> 'trust_manifest',
        lineage_body ->> 'trust_manifest_sha256',
        (lineage_body ->> 'source_signal_id')::uuid,
        lineage_body ->> 'source_signal_payload_sha256',
        (lineage_body ->> 'source_producer_principal_id')::uuid,
        (lineage_body ->> 'source_signal_expires_at')::timestamptz,
        (lineage_body ->> 'source_connector_receipt_id')::uuid,
        lineage_body ->> 'connector_receipt_sha256',
        (lineage_body ->> 'source_request_receipt_id')::uuid,
        lineage_body ->> 'source_request_receipt_sha256',
        (lineage_body ->> 'official_content_version_id')::uuid,
        (lineage_body ->> 'official_source_item_id')::uuid,
        lineage_body ->> 'official_source_binding_sha256',
        lineage_body ->> 'content_snapshot_sha256',
        lineage_body ->> 'status', transition_time, effective_expires_at,
        true, true, false, lineage_body,
        lineage_body ->> 'lineage_sha256'
    );
    request_body := pg_catalog.jsonb_build_object(
        'approved_cost_cap_microusd', target_approved_cost_cap_microusd,
        'assignment_key', assignment_key,
        'automatic_publication', false,
        'client_id', target_client_id,
        'effective_expires_at', private.harmony_preview_codex_timestamp(
            effective_expires_at
        ),
        'external_calls', false,
        'lineage_receipt_id', lineage_id::text,
        'lineage_sha256', lineage_body ->> 'lineage_sha256',
        'plan_id', target_plan_id::text,
        'provider_calls', false,
        'publication_calls', false,
        'request_id', request_id::text,
        'request_key', request_key,
        'reviewer_config_sha256', reviewer_binding ->> 'config_sha256',
        'reviewer_principal_id', reviewer_binding ->> 'principal_id',
        'reviewer_release_sha', reviewer_binding ->> 'producer_release_sha',
        'reviewer_specialist_binding_sha256',
            reviewer_binding ->> 'binding_sha256',
        'round_id', target_round_id::text,
        'schema_version', 'squid-codex-gate-request@1',
        'stage', 'independent_qa',
        'submitted_at', private.harmony_preview_codex_timestamp(
            transition_time
        ),
        'work_key', work_key,
        'workspace_id', target_workspace_id::text
    );
    request_body_sha := private.agent_json_sha256(request_body);
    request_body := request_body || pg_catalog.jsonb_build_object(
        'payload_sha256', request_body_sha
    );
    insert into private.harmony_preview_codex_gate_requests (
        workspace_id, client_id, request_id, lineage_receipt_id,
        lineage_sha256, round_id, plan_id, stage, work_key,
        assignment_key, request_key, reviewer_principal_id,
        reviewer_specialist_binding_sha256, reviewer_release_sha,
        reviewer_config_sha256, approved_cost_cap_microusd, submitted_at,
        effective_expires_at, automatic_publication, provider_calls,
        external_calls, publication_calls, payload, payload_sha256
    ) values (
        target_workspace_id, target_client_id, request_id, lineage_id,
        lineage_body ->> 'lineage_sha256', target_round_id, target_plan_id,
        'independent_qa', work_key, assignment_key, request_key,
        (reviewer_binding ->> 'principal_id')::uuid,
        reviewer_binding ->> 'binding_sha256',
        reviewer_binding ->> 'producer_release_sha',
        reviewer_binding ->> 'config_sha256',
        target_approved_cost_cap_microusd, transition_time,
        effective_expires_at, false, false, false, false,
        request_body, request_body_sha
    ) returning * into strict request_row;
    transition_row := private.harmony_preview_codex_append_transition(
        request_row, 'prepare', null, 'pending', null,
        transition_time, request_id
    );
    insert into private.harmony_preview_codex_gate_runs (
        workspace_id, client_id, work_key, request_id, request_key,
        status, status_version, claim_attempt, last_event_sha256, updated_at
    ) values (
        target_workspace_id, target_client_id, work_key, request_id,
        request_key, 'pending', 1, 0, transition_row.event_sha256,
        transition_time
    );
    return pg_catalog.jsonb_build_object(
        'request_key', request_key,
        'reused', false,
        'status', 'pending',
        'work_key', work_key
    );
exception
    when no_data_found then
        raise exception 'harmony_preview_codex_gate_dependency_missing';
end;
$$;

create or replace function private.harmony_preview_codex_work_key(
    target_source_lineage jsonb
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'client_id', target_source_lineage ->> 'client_id',
        'content_snapshot_sha256',
            target_source_lineage ->> 'content_snapshot_sha256',
        'official_content_version_id',
            target_source_lineage ->> 'official_content_version_id',
        'official_source_binding_sha256',
            target_source_lineage ->> 'official_source_binding_sha256',
        'official_source_item_id',
            target_source_lineage ->> 'official_source_item_id',
        'plan_id', target_source_lineage ->> 'plan_id',
        'plan_receipt_sha256',
            target_source_lineage ->> 'plan_receipt_sha256',
        'private_content_output_sha256',
            target_source_lineage ->> 'private_content_output_sha256',
        'private_content_receipt_sha256',
            target_source_lineage ->> 'private_content_receipt_sha256',
        'round_id', target_source_lineage ->> 'round_id',
        'schema_version', 'squid-codex-gate-work@1',
        'signal_input_set_sha256',
            target_source_lineage ->> 'signal_input_set_sha256',
        'signal_manifest_sha256',
            target_source_lineage ->> 'signal_manifest_sha256',
        'signal_producer_principal_ids',
            target_source_lineage -> 'signal_producer_principal_ids',
        'stage', 'independent_qa',
        'workspace_id', target_source_lineage ->> 'workspace_id'
    ))
$$;

create or replace function private.harmony_preview_codex_assignment_key(
    target_work_key text,
    target_reviewer_binding jsonb
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'reviewer_binding_sha256',
            target_reviewer_binding ->> 'binding_sha256',
        'schema_version', 'squid-codex-gate-assignment@1',
        'work_key', target_work_key
    ))
$$;

create or replace function private.harmony_preview_codex_request_key(
    target_work_key text,
    target_assignment_key text,
    target_lineage_sha256 text,
    target_effective_expires_at timestamptz,
    target_approved_cost_cap_microusd bigint
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'approved_cost_cap_microusd', target_approved_cost_cap_microusd,
        'assignment_key', target_assignment_key,
        'effective_expires_at', private.harmony_preview_codex_timestamp(
            target_effective_expires_at
        ),
        'lineage_sha256', target_lineage_sha256,
        'work_key', target_work_key
    ))
$$;

create or replace function private.harmony_preview_codex_request_current(
    target_request_id uuid,
    target_at timestamptz
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    lineage private.harmony_preview_codex_source_lineage_receipts%rowtype;
    current_trust jsonb;
begin
    select candidate.* into strict request_row
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.request_id = target_request_id;
    select candidate.* into strict lineage
    from private.harmony_preview_codex_source_lineage_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.lineage_receipt_id = request_row.lineage_receipt_id
      and candidate.lineage_sha256 = request_row.lineage_sha256;
    if target_at < request_row.submitted_at
       or target_at >= request_row.effective_expires_at
    then
        return false;
    end if;
    current_trust := private.harmony_preview_codex_trust_manifest(
        lineage.workspace_id, lineage.client_id,
        lineage.signal_manifest, target_at
    );
    if current_trust is distinct from lineage.trust_manifest
       or private.agent_json_sha256(current_trust)
            is distinct from lineage.trust_manifest_sha256
       or not private.harmony_preview_round_inputs_current(
            lineage.workspace_id, lineage.client_id, lineage.signal_manifest
       )
       or not private.harmony_preview_qa_actor_independent(
            lineage.workspace_id, lineage.client_id, lineage.plan_id,
            lineage.reviewer_principal_id
       )
       or not exists (
            select 1
            from private.harmony_preview_squid_specialist_bindings binding
            join private.harmony_preview_environment_fence fence
              on fence.branch_ref = binding.branch_ref
             and fence.branch_ref = lineage.branch_ref
             and fence.active
             and fence.created_at <= target_at
             and fence.expires_at > target_at
            where binding.workspace_id = lineage.workspace_id
              and binding.client_id = lineage.client_id
              and binding.stage = 'independent_qa'
              and binding.principal_id = lineage.reviewer_principal_id
              and binding.binding_sha256
                    = lineage.reviewer_specialist_binding_sha256
              and binding.producer_release_sha = lineage.reviewer_release_sha
              and binding.config_sha256 = lineage.reviewer_config_sha256
              and binding.created_at <= target_at
              and binding.expires_at > target_at
       )
       or not exists (
            select 1
            from public.content_items item
            join public.content_versions version
              on version.workspace_id = item.workspace_id
             and version.content_item_id = item.id
             and version.id = item.current_version_id
            where item.workspace_id = lineage.workspace_id
              and item.client_id = lineage.client_id
              and item.current_version_id
                    = lineage.official_content_version_id
              and item.status = 'needs_review'
              and version.generation_meta -> 'mock_mode'
                    is distinct from 'true'::jsonb
              and private.agent_json_sha256(pg_catalog.jsonb_build_object(
                    'channel_copy', version.channel_copy,
                    'content', version.content,
                    'deliverables', version.deliverables,
                    'generation_meta', version.generation_meta,
                    'qa', version.qa,
                    'title', version.title
              )) = lineage.content_snapshot_sha256
       )
    then
        return false;
    end if;
    return true;
exception
    when no_data_found then return false;
end;
$$;

create or replace function private.harmony_preview_codex_append_transition(
    target_request private.harmony_preview_codex_gate_requests,
    target_kind text,
    target_expected_from text,
    target_to text,
    target_terminal_reason text,
    target_at timestamptz,
    target_reference_id uuid
)
returns private.harmony_preview_codex_gate_transitions
language plpgsql
security definer
set search_path = ''
as $$
declare
    previous private.harmony_preview_codex_gate_transitions%rowtype;
    stored private.harmony_preview_codex_gate_transitions%rowtype;
    next_seq integer;
    transition_id uuid := extensions.gen_random_uuid();
    body jsonb;
    body_sha text;
    event_sha text;
begin
    select candidate.* into previous
    from private.harmony_preview_codex_gate_transitions candidate
    where candidate.workspace_id = target_request.workspace_id
      and candidate.client_id = target_request.client_id
      and candidate.request_id = target_request.request_id
    order by candidate.event_seq desc
    limit 1;
    next_seq := coalesce(previous.event_seq, 0) + 1;
    if previous.to_state is distinct from target_expected_from then
        raise exception 'harmony_preview_codex_gate_state_invalid';
    end if;
    body := pg_catalog.jsonb_build_object(
        'event_seq', next_seq,
        'from_state', target_expected_from,
        'occurred_at', private.harmony_preview_codex_timestamp(target_at),
        'reference_id', target_reference_id::text,
        'request_id', target_request.request_id::text,
        'request_key', target_request.request_key,
        'schema_version', 'squid-codex-gate-transition@1',
        'terminal_reason', target_terminal_reason,
        'to_state', target_to,
        'transition_id', transition_id::text,
        'transition_kind', target_kind,
        'transition_seq', next_seq,
        'work_key', target_request.work_key
    );
    body_sha := private.agent_json_sha256(body);
    body := body || pg_catalog.jsonb_build_object(
        'payload_sha256', body_sha
    );
    event_sha := private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'event_seq', next_seq,
        'payload_sha256', body_sha,
        'previous_event_sha256', previous.event_sha256,
        'request_key', target_request.request_key,
        'work_key', target_request.work_key
    ));
    insert into private.harmony_preview_codex_gate_transitions (
        workspace_id, client_id, transition_id, request_id, request_key,
        work_key, event_seq, transition_seq, transition_kind, from_state,
        to_state, terminal_reason, occurred_at, previous_event_sha256,
        payload, payload_sha256, event_sha256
    ) values (
        target_request.workspace_id, target_request.client_id, transition_id,
        target_request.request_id, target_request.request_key,
        target_request.work_key, next_seq, next_seq, target_kind,
        target_expected_from, target_to, target_terminal_reason, target_at,
        previous.event_sha256, body, body_sha, event_sha
    ) returning * into strict stored;
    return stored;
end;
$$;

create or replace function private.harmony_preview_codex_qa_binding(
    target_workspace_id uuid,
    target_client_id text,
    target_at timestamptz
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    issued_epoch bigint;
    expires_epoch bigint;
    specialist private.harmony_preview_squid_specialist_bindings%rowtype;
    fence private.harmony_preview_environment_fence%rowtype;
    claim_scope_valid boolean := false;
    claim_policy_valid boolean := false;
    claim_identity_valid boolean := false;
    claim_time_valid boolean := false;
begin
    begin
        claims := nullif(
            pg_catalog.current_setting('request.jwt.claims', true), ''
        )::jsonb;
        issued_epoch := (claims ->> 'iat')::bigint;
        expires_epoch := (claims ->> 'exp')::bigint;
    exception when others then
        raise exception 'harmony_preview_codex_qa_scope_invalid';
    end;
    begin
        claim_scope_valid := coalesce((
        target_client_id = 'squid'
        and private.harmony_preview_stage_claims_match(
            target_workspace_id, target_client_id,
            'coineasy_harmony_qa', 'harmony_independent_qa'
        )
        and coalesce(claims ->> 'role', '') = 'coineasy_harmony_qa'
        and coalesce(claims ->> 'capability', '') = 'harmony_independent_qa'
        and coalesce(claims ->> 'workspace_id', '') = target_workspace_id::text
        and coalesce(claims ->> 'client_id', '') = target_client_id
        and coalesce(claims ->> 'environment', '') = 'preview'
        and coalesce(claims ->> 'iss', '') = 'supabase'
        and coalesce(claims ->> 'aud', '') = 'authenticated'
        ), false);
        claim_policy_valid := coalesce((
        (claims -> 'automatic_publication' is not distinct from 'false'::jsonb)
        and (claims -> 'max_cost_microusd' is not distinct from '0'::jsonb)
        and (claims -> 'max_external_actions' is not distinct from '0'::jsonb)
        ), false);
        -- Validate each identity claim independently and fail closed.
        claim_identity_valid := coalesce(
            coalesce(claims ->> 'ref', '') ~ '^[a-z0-9]{20}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'producer_principal_id', '')
                ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'sub', '')
                = claims ->> 'producer_principal_id',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'release_sha', '') ~ '^[a-f0-9]{40}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'config_sha256', '') ~ '^[a-f0-9]{64}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'jti', '')
                ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            false
        );
        claim_time_valid := coalesce((
        issued_epoch is not null and expires_epoch is not null
        and issued_epoch >= 0 and expires_epoch <= 4102444800
        and expires_epoch - issued_epoch between 1 and 2678400
        and pg_catalog.to_timestamp(issued_epoch) <= target_at + interval '1 minute'
        and pg_catalog.to_timestamp(expires_epoch) > target_at
        ), false);
    exception when others then
        raise exception 'harmony_preview_codex_qa_scope_invalid';
    end;
    if not (
        claim_scope_valid
        and claim_policy_valid
        and claim_identity_valid
        and claim_time_valid
    ) then
        raise exception 'harmony_preview_codex_qa_scope_invalid';
    end if;
    select candidate.* into strict specialist
    from private.harmony_preview_squid_specialist_bindings candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.stage = 'independent_qa'
      and candidate.specialist_code = 'squid_independent_qa'
      and candidate.role_name = 'coineasy_harmony_qa'
      and candidate.capability = 'harmony_independent_qa'
      and candidate.actor = 'codex'
      and candidate.principal_id
            = (claims ->> 'producer_principal_id')::uuid
      and candidate.producer_release_sha = claims ->> 'release_sha'
      and candidate.config_sha256 = claims ->> 'config_sha256'
      and candidate.branch_ref = claims ->> 'ref';
    select candidate.* into strict fence
    from private.harmony_preview_environment_fence candidate
    where candidate.branch_ref = specialist.branch_ref
      and candidate.active;
    if specialist.created_at > target_at
       or specialist.expires_at <= target_at
       or specialist.expires_at > fence.expires_at
       or fence.created_at > target_at
       or fence.expires_at <= target_at
       -- JWT NumericDate has whole-second precision while the binding uses a
       -- PostgreSQL timestamp with microseconds.  Compare at the precision the
       -- signed claim can actually represent so a token minted immediately
       -- after this binding is not rejected within the same second.
       or pg_catalog.to_timestamp(issued_epoch)
            < pg_catalog.date_trunc('second', specialist.created_at)
       or pg_catalog.to_timestamp(expires_epoch) > specialist.expires_at
    then
        raise exception 'harmony_preview_codex_qa_scope_invalid';
    end if;
    return pg_catalog.jsonb_build_object(
        'binding_sha256', specialist.binding_sha256,
        'branch_ref', specialist.branch_ref,
        'config_sha256', specialist.config_sha256,
        'created_at', private.harmony_preview_codex_timestamp(
            specialist.created_at
        ),
        'expires_at', private.harmony_preview_codex_timestamp(
            specialist.expires_at
        ),
        'principal_id', specialist.principal_id::text,
        'producer_release_sha', specialist.producer_release_sha
    );
exception
    when no_data_found then
        raise exception 'harmony_preview_codex_qa_scope_invalid';
end;
$$;

-- Recovery remains available after the current specialist/fence window has
-- expired, but only to the exact QA identity frozen into the immutable
-- request.  This deliberately does not call the current-binding helper: it
-- validates a currently signed, zero-authority QA JWT and binds it back to
-- the original specialist assignment instead.
create or replace function private.harmony_preview_codex_qa_scope_preflight(
    target_workspace_id uuid,
    target_client_id text,
    target_at timestamptz
)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    issued_epoch bigint;
    expires_epoch bigint;
    claim_scope_valid boolean := false;
    claim_policy_valid boolean := false;
    claim_identity_valid boolean := false;
    claim_time_valid boolean := false;
begin
    begin
        claims := nullif(
            pg_catalog.current_setting('request.jwt.claims', true), ''
        )::jsonb;
        issued_epoch := (claims ->> 'iat')::bigint;
        expires_epoch := (claims ->> 'exp')::bigint;
    exception when others then
        raise exception 'harmony_preview_codex_qa_scope_invalid';
    end;
    begin
        claim_scope_valid := coalesce((
            target_client_id = 'squid'
            and coalesce(claims ->> 'role', '') = 'coineasy_harmony_qa'
            and coalesce(claims ->> 'capability', '')
                = 'harmony_independent_qa'
            and coalesce(claims ->> 'workspace_id', '')
                = target_workspace_id::text
            and coalesce(claims ->> 'client_id', '') = target_client_id
            and coalesce(claims ->> 'environment', '') = 'preview'
            and coalesce(claims ->> 'iss', '') = 'supabase'
            and coalesce(claims ->> 'aud', '') = 'authenticated'
        ), false);
        claim_policy_valid := coalesce((
            claims -> 'automatic_publication'
                is not distinct from 'false'::jsonb
            and claims -> 'max_cost_microusd'
                is not distinct from '0'::jsonb
            and claims -> 'max_external_actions'
                is not distinct from '0'::jsonb
        ), false);
        claim_identity_valid := coalesce(
            coalesce(claims ->> 'ref', '') ~ '^[a-z0-9]{20}$', false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'producer_principal_id', '')
                ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'sub', '')
                = claims ->> 'producer_principal_id',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'release_sha', '') ~ '^[a-f0-9]{40}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'config_sha256', '') ~ '^[a-f0-9]{64}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'jti', '')
                ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            false
        );
        claim_time_valid := coalesce((
            target_at is not null
            and issued_epoch is not null
            and expires_epoch is not null
            and issued_epoch >= 0
            and expires_epoch <= 4102444800
            and expires_epoch - issued_epoch between 1 and 2678400
            and pg_catalog.to_timestamp(issued_epoch)
                <= target_at + interval '1 minute'
            and pg_catalog.to_timestamp(expires_epoch) > target_at
        ), false);
    exception when others then
        raise exception 'harmony_preview_codex_qa_scope_invalid';
    end;
    if not (
        claim_scope_valid
        and claim_policy_valid
        and claim_identity_valid
        and claim_time_valid
    ) then
        raise exception 'harmony_preview_codex_qa_scope_invalid';
    end if;
end;
$$;

create or replace function private.harmony_preview_codex_lock_tenant(
    target_workspace_id uuid,
    target_client_id text
)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_codex_gate_tenant:' || target_workspace_id::text ||
        ':' || target_client_id,
        0
    ));
end;
$$;

create or replace function private.harmony_preview_codex_reconciliation_actor(
    target_workspace_id uuid,
    target_client_id text,
    target_request_id uuid,
    target_at timestamptz
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    issued_epoch bigint;
    expires_epoch bigint;
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    specialist private.harmony_preview_squid_specialist_bindings%rowtype;
    claim_scope_valid boolean := false;
    claim_policy_valid boolean := false;
    claim_identity_valid boolean := false;
    claim_time_valid boolean := false;
begin
    begin
        claims := nullif(
            pg_catalog.current_setting('request.jwt.claims', true), ''
        )::jsonb;
        issued_epoch := (claims ->> 'iat')::bigint;
        expires_epoch := (claims ->> 'exp')::bigint;
    exception when others then
        raise exception 'harmony_preview_codex_reconciliation_actor_invalid';
    end;
    begin
        claim_scope_valid := coalesce((
            target_client_id = 'squid'
            and coalesce(claims ->> 'role', '') = 'coineasy_harmony_qa'
            and coalesce(claims ->> 'capability', '')
                = 'harmony_independent_qa'
            and coalesce(claims ->> 'workspace_id', '')
                = target_workspace_id::text
            and coalesce(claims ->> 'client_id', '') = target_client_id
            and coalesce(claims ->> 'environment', '') = 'preview'
            and coalesce(claims ->> 'iss', '') = 'supabase'
            and coalesce(claims ->> 'aud', '') = 'authenticated'
        ), false);
        claim_policy_valid := coalesce((
            claims -> 'automatic_publication'
                is not distinct from 'false'::jsonb
            and claims -> 'max_cost_microusd'
                is not distinct from '0'::jsonb
            and claims -> 'max_external_actions'
                is not distinct from '0'::jsonb
        ), false);
        -- Keep every signed identity field sequential and fail closed so a
        -- missing claim can never pass through SQL's three-valued logic.
        claim_identity_valid := coalesce(
            coalesce(claims ->> 'ref', '') ~ '^[a-z0-9]{20}$', false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'producer_principal_id', '')
                ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'sub', '')
                = claims ->> 'producer_principal_id',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'release_sha', '') ~ '^[a-f0-9]{40}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'config_sha256', '') ~ '^[a-f0-9]{64}$',
            false
        );
        claim_identity_valid := claim_identity_valid and coalesce(
            coalesce(claims ->> 'jti', '')
                ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            false
        );
        claim_time_valid := coalesce((
            target_at is not null
            and issued_epoch is not null
            and expires_epoch is not null
            and issued_epoch >= 0
            and expires_epoch <= 4102444800
            and expires_epoch - issued_epoch between 1 and 2678400
            and pg_catalog.to_timestamp(issued_epoch)
                <= target_at + interval '1 minute'
            and pg_catalog.to_timestamp(expires_epoch) > target_at
        ), false);
    exception when others then
        raise exception 'harmony_preview_codex_reconciliation_actor_invalid';
    end;
    if not (
        claim_scope_valid
        and claim_policy_valid
        and claim_identity_valid
        and claim_time_valid
    ) then
        raise exception 'harmony_preview_codex_reconciliation_actor_invalid';
    end if;
    select candidate.* into strict request_row
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.request_id = target_request_id;
    select candidate.* into strict specialist
    from private.harmony_preview_squid_specialist_bindings candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.stage = 'independent_qa'
      and candidate.specialist_code = 'squid_independent_qa'
      and candidate.role_name = 'coineasy_harmony_qa'
      and candidate.capability = 'harmony_independent_qa'
      and candidate.actor = 'codex'
      and candidate.binding_sha256
            = request_row.reviewer_specialist_binding_sha256
      and candidate.principal_id = request_row.reviewer_principal_id
      and candidate.principal_id
            = (claims ->> 'producer_principal_id')::uuid
      and candidate.producer_release_sha = request_row.reviewer_release_sha
      and candidate.producer_release_sha = claims ->> 'release_sha'
      and candidate.config_sha256 = request_row.reviewer_config_sha256
      and candidate.config_sha256 = claims ->> 'config_sha256'
      and candidate.branch_ref = claims ->> 'ref';
    if specialist.created_at > target_at
       or pg_catalog.to_timestamp(issued_epoch)
            < pg_catalog.date_trunc('second', specialist.created_at)
    then
        raise exception 'harmony_preview_codex_reconciliation_actor_invalid';
    end if;
    return pg_catalog.jsonb_build_object(
        'actor', specialist.actor,
        'binding_sha256', specialist.binding_sha256,
        'branch_ref', specialist.branch_ref,
        'config_sha256', specialist.config_sha256,
        'principal_id', specialist.principal_id::text,
        'producer_release_sha', specialist.producer_release_sha
    );
exception
    when no_data_found then
        raise exception 'harmony_preview_codex_reconciliation_actor_invalid';
end;
$$;

create or replace function private.harmony_preview_codex_build_source_lineage(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid,
    target_plan_id uuid,
    target_reviewer_binding jsonb,
    target_observed_at timestamptz,
    target_lineage_receipt_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    round_row agent_runtime.harmony_rounds%rowtype;
    plan_stage agent_runtime.harmony_stage_receipts%rowtype;
    private_stage agent_runtime.harmony_stage_receipts%rowtype;
    private_binding private.harmony_preview_squid_specialist_bindings%rowtype;
    reviewer_binding private.harmony_preview_squid_specialist_bindings%rowtype;
    fence private.harmony_preview_environment_fence%rowtype;
    source_signal agent_runtime.harmony_signals%rowtype;
    source_receipt agent_runtime.harmony_connector_attestation_receipts%rowtype;
    source_request private.harmony_preview_connector_request_receipts%rowtype;
    content_item public.content_items%rowtype;
    content_version public.content_versions%rowtype;
    manifest_sha text;
    trust_manifest jsonb;
    trust_sha text;
    producers uuid[];
    snapshot_sha text;
    trust_expires_at timestamptz;
    body jsonb;
    lineage_sha text;
begin
    if target_client_id <> 'squid' then
        raise exception 'harmony_preview_codex_source_scope_invalid';
    end if;
    select candidate.* into strict round_row
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.round_id = target_round_id
      and candidate.plan_id = target_plan_id
      and candidate.status = 'planned';
    manifest_sha := private.agent_json_sha256(round_row.signal_manifest);
    if manifest_sha <> round_row.input_set_sha256 then
        raise exception 'harmony_preview_codex_manifest_digest_invalid';
    end if;
    trust_manifest := private.harmony_preview_codex_trust_manifest(
        target_workspace_id, target_client_id, round_row.signal_manifest,
        target_observed_at
    );
    if trust_manifest is null then
        raise exception 'harmony_preview_codex_trust_not_current';
    end if;
    trust_sha := private.agent_json_sha256(trust_manifest);
    select pg_catalog.array_agg(
        signal.producer_principal_id order by signal.producer_principal_id::text
    ) into strict producers
    from pg_catalog.jsonb_array_elements(round_row.signal_manifest) entry(value)
    join agent_runtime.harmony_signals signal
      on signal.workspace_id = target_workspace_id
     and signal.client_id = target_client_id
     and signal.signal_id = (entry.value ->> 'signal_id')::uuid
     and signal.payload_sha256 = entry.value ->> 'signal_payload_sha256';
    if not private.harmony_preview_codex_uuid4_array(producers) then
        raise exception 'harmony_preview_codex_producer_set_invalid';
    end if;
    select receipt.* into strict plan_stage
    from agent_runtime.harmony_stage_receipts receipt
    where receipt.workspace_id = target_workspace_id
      and receipt.client_id = target_client_id
      and receipt.round_id = target_round_id
      and receipt.plan_id = target_plan_id
      and receipt.stage = 'plan'
      and receipt.ordinal = 1;
    select receipt.* into strict private_stage
    from agent_runtime.harmony_stage_receipts receipt
    where receipt.workspace_id = target_workspace_id
      and receipt.client_id = target_client_id
      and receipt.round_id = target_round_id
      and receipt.plan_id = target_plan_id
      and receipt.stage = 'private_content'
      and receipt.ordinal = 2;
    if plan_stage.input_sha256 <> round_row.input_set_sha256
       or private_stage.previous_receipt_sha256 <> plan_stage.receipt_sha256
       or private_stage.input_sha256 <> plan_stage.output_sha256
    then
        raise exception 'harmony_preview_codex_stage_lineage_invalid';
    end if;
    select candidate.* into strict private_binding
    from private.harmony_preview_squid_specialist_bindings candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.stage = 'private_content'
      and candidate.binding_sha256 = private_stage.specialist_binding_sha256
      and candidate.principal_id = private_stage.principal_id
      and candidate.producer_release_sha = private_stage.producer_release_sha
      and candidate.config_sha256 = private_stage.config_sha256;
    select candidate.* into strict reviewer_binding
    from private.harmony_preview_squid_specialist_bindings candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.stage = 'independent_qa'
      and candidate.binding_sha256
            = target_reviewer_binding ->> 'binding_sha256'
      and candidate.principal_id
            = (target_reviewer_binding ->> 'principal_id')::uuid
      and candidate.producer_release_sha
            = target_reviewer_binding ->> 'producer_release_sha'
      and candidate.config_sha256
            = target_reviewer_binding ->> 'config_sha256';
    select candidate.* into strict fence
    from private.harmony_preview_environment_fence candidate
    where candidate.branch_ref = reviewer_binding.branch_ref
      and candidate.branch_ref = private_binding.branch_ref
      and candidate.active;
    if not private.harmony_preview_qa_actor_independent(
        target_workspace_id, target_client_id, target_plan_id,
        reviewer_binding.principal_id
    ) then
        raise exception 'harmony_preview_codex_qa_actor_not_independent';
    end if;
    select signal.* into strict source_signal
    from agent_runtime.harmony_signals signal
    where signal.workspace_id = target_workspace_id
      and signal.client_id = target_client_id
      and signal.lane = 'content_source'
      and signal.payload_sha256 in (
            select entry.value ->> 'signal_payload_sha256'
            from pg_catalog.jsonb_array_elements(round_row.signal_manifest)
                entry(value)
      );
    select receipt.* into strict source_receipt
    from agent_runtime.harmony_connector_attestation_receipts receipt
    where receipt.workspace_id = source_signal.workspace_id
      and receipt.client_id = source_signal.client_id
      and receipt.receipt_id = source_signal.connector_receipt_id
      and receipt.payload_sha256 = source_signal.connector_receipt_sha256;
    select request.* into strict source_request
    from private.harmony_preview_connector_request_receipts request
    where request.workspace_id = source_signal.workspace_id
      and request.client_id = source_signal.client_id
      and request.connector_receipt_id = source_receipt.receipt_id
      and request.connector_receipt_sha256 = source_receipt.payload_sha256;
    if source_signal.official_source_binding_sha256 is null
       or source_signal.upstream_receipt_sha256
            <> source_signal.official_source_binding_sha256
       or private.harmony_preview_squid_official_source_binding(
            source_signal.payload
          ) is distinct from source_signal.official_source_binding_sha256
    then
        raise exception 'harmony_preview_codex_official_source_invalid';
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
      and version.id = content_item.current_version_id
      and version.generation_meta -> 'mock_mode' is distinct from 'true'::jsonb;
    snapshot_sha := private.agent_json_sha256(pg_catalog.jsonb_build_object(
        'channel_copy', content_version.channel_copy,
        'content', content_version.content,
        'deliverables', content_version.deliverables,
        'generation_meta', content_version.generation_meta,
        'qa', content_version.qa,
        'title', content_version.title
    ));
    if private_stage.artifact ->> 'content_version_id'
            <> content_version.id::text
       or private_stage.artifact ->> 'source_binding_sha256'
            <> source_signal.official_source_binding_sha256
       or private_stage.artifact ->> 'content_snapshot_sha256' <> snapshot_sha
       or private_stage.artifact ->> 'status' <> 'needs_review'
       or private_stage.artifact -> 'private_content_only'
            is distinct from 'true'::jsonb
       or private_stage.artifact -> 'automatic_publication'
            is distinct from 'false'::jsonb
    then
        raise exception 'harmony_preview_codex_private_binding_invalid';
    end if;
    select pg_catalog.min(value_value) into strict trust_expires_at
    from (
        select (entry.value ->> 'signal_expires_at')::timestamptz value_value
        from pg_catalog.jsonb_array_elements(trust_manifest) entry(value)
        union all
        select (entry.value ->> 'connector_receipt_expires_at')::timestamptz
        from pg_catalog.jsonb_array_elements(trust_manifest) entry(value)
        union all
        select (entry.value ->> 'connector_request_expires_at')::timestamptz
        from pg_catalog.jsonb_array_elements(trust_manifest) entry(value)
        union all
        select (entry.value ->> 'registration_expires_at')::timestamptz
        from pg_catalog.jsonb_array_elements(trust_manifest) entry(value)
        union all select fence.expires_at
        union all select private_binding.expires_at
        union all select reviewer_binding.expires_at
    ) expiry(value_value);
    if not (
        pg_catalog.date_trunc('second', fence.created_at)
            <= plan_stage.created_at
        and plan_stage.created_at <= private_stage.created_at
        and private_stage.created_at <= target_observed_at
        and fence.created_at <= private_binding.created_at
        and pg_catalog.date_trunc('second', private_binding.created_at)
            <= private_stage.created_at
        and private_stage.created_at < private_binding.expires_at
        and fence.created_at <= reviewer_binding.created_at
        and reviewer_binding.created_at <= target_observed_at
        and target_observed_at < trust_expires_at
    ) then
        raise exception 'harmony_preview_codex_lineage_time_invalid';
    end if;
    body := pg_catalog.jsonb_build_object(
        'automatic_publication', false,
        'branch_fence_active', true,
        'branch_fence_created_at', private.harmony_preview_codex_timestamp(
            fence.created_at
        ),
        'branch_fence_expires_at', private.harmony_preview_codex_timestamp(
            fence.expires_at
        ),
        'branch_ref', fence.branch_ref,
        'client_id', target_client_id,
        'connector_receipt_sha256', source_receipt.payload_sha256,
        'content_snapshot_sha256', snapshot_sha,
        'database_currentness_required', true,
        'lineage_receipt_id', target_lineage_receipt_id::text,
        'observed_at', private.harmony_preview_codex_timestamp(
            target_observed_at
        ),
        'official_content_version_id', content_version.id::text,
        'official_source_binding_sha256',
            source_signal.official_source_binding_sha256,
        'official_source_item_id', source_signal.official_source_item_id::text,
        'plan_id', target_plan_id::text,
        'plan_receipt_id', plan_stage.receipt_id::text,
        'plan_receipt_sha256', plan_stage.receipt_sha256,
        'private_content_only', true,
        'private_content_output_sha256', private_stage.output_sha256,
        'private_content_principal_id', private_stage.principal_id::text,
        'private_content_receipt_id', private_stage.receipt_id::text,
        'private_content_receipt_sha256', private_stage.receipt_sha256,
        'private_content_specialist_binding_sha256',
            private_stage.specialist_binding_sha256,
        'reviewer_config_sha256', reviewer_binding.config_sha256,
        'reviewer_principal_id', reviewer_binding.principal_id::text,
        'reviewer_release_sha', reviewer_binding.producer_release_sha,
        'reviewer_specialist_binding_sha256', reviewer_binding.binding_sha256,
        'round_id', target_round_id::text,
        'schema_version', 'squid-codex-source-lineage-receipt@1',
        'signal_input_set_sha256', round_row.input_set_sha256,
        'signal_manifest', round_row.signal_manifest,
        'signal_manifest_sha256', manifest_sha,
        'signal_producer_principal_ids', pg_catalog.to_jsonb(producers),
        'source_connector_receipt_id', source_receipt.receipt_id::text,
        'source_producer_principal_id', source_signal.producer_principal_id::text,
        'source_request_receipt_id', source_request.request_receipt_id::text,
        'source_request_receipt_sha256', source_request.payload_sha256,
        'source_signal_expires_at', private.harmony_preview_codex_timestamp(
            source_signal.expires_at
        ),
        'source_signal_id', source_signal.signal_id::text,
        'source_signal_payload_sha256', source_signal.payload_sha256,
        'status', 'needs_review',
        'trust_manifest', trust_manifest,
        'trust_manifest_sha256', trust_sha,
        'trust_snapshot_expires_at', private.harmony_preview_codex_timestamp(
            trust_expires_at
        ),
        'workspace_id', target_workspace_id::text
    );
    lineage_sha := private.agent_json_sha256(body);
    return body || pg_catalog.jsonb_build_object(
        'lineage_sha256', lineage_sha
    );
exception
    when no_data_found then
        raise exception 'harmony_preview_codex_gate_dependency_missing';
end;
$$;

create or replace function public.claim_preview_harmony_squid_codex_qa(
    target_workspace_id uuid,
    target_client_id text,
    target_lease_seconds integer default 900
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    current_run private.harmony_preview_codex_gate_runs%rowtype;
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    transition_row private.harmony_preview_codex_gate_transitions%rowtype;
    binding jsonb;
    claim_id uuid := extensions.gen_random_uuid();
    next_claim_attempt integer;
    selection_at timestamptz;
    observed_at timestamptz;
    new_lease_expires_at timestamptz;
    new_claim_fence_sha256 text;
    body jsonb;
    body_sha text;
begin
    if target_client_id <> 'squid'
       or target_lease_seconds not between 1 and 900
    then
        raise exception 'harmony_preview_codex_gate_lease_invalid';
    end if;
    selection_at := pg_catalog.clock_timestamp();
    perform private.harmony_preview_codex_qa_scope_preflight(
        target_workspace_id, target_client_id, selection_at
    );
    perform private.harmony_preview_codex_lock_tenant(
        target_workspace_id, target_client_id
    );
    select candidate.* into current_run
    from private.harmony_preview_codex_gate_runs candidate
    join private.harmony_preview_codex_gate_requests queued_request
      on queued_request.workspace_id = candidate.workspace_id
     and queued_request.client_id = candidate.client_id
     and queued_request.request_id = candidate.request_id
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.status = 'pending'
      and candidate.claim_attempt < 3
      and queued_request.effective_expires_at > selection_at
      and private.harmony_preview_codex_request_current(
            queued_request.request_id, selection_at
      )
    order by candidate.updated_at, candidate.work_key
    for update of candidate skip locked
    limit 1;
    if not found then
        return pg_catalog.jsonb_build_object(
            'claim_fence_sha256', null,
            'claimed', false,
            'request_key', null,
            'work_key', null
        );
    end if;
    select candidate.* into strict request_row
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.workspace_id = current_run.workspace_id
      and candidate.client_id = current_run.client_id
      and candidate.request_id = current_run.request_id
    for share;
    perform private.harmony_preview_codex_lock_plan_dependencies(
        request_row.workspace_id, request_row.client_id,
        request_row.round_id, request_row.plan_id
    );
    observed_at := pg_catalog.clock_timestamp();
    binding := private.harmony_preview_codex_qa_binding(
        request_row.workspace_id, request_row.client_id, observed_at
    );
    if (binding ->> 'principal_id')::uuid
            is distinct from request_row.reviewer_principal_id
       or binding ->> 'binding_sha256'
            is distinct from request_row.reviewer_specialist_binding_sha256
       or binding ->> 'producer_release_sha'
            is distinct from request_row.reviewer_release_sha
       or binding ->> 'config_sha256'
            is distinct from request_row.reviewer_config_sha256
       or not private.harmony_preview_codex_request_current(
            request_row.request_id, observed_at
       )
    then
        raise exception 'harmony_preview_codex_gate_not_current';
    end if;
    next_claim_attempt := current_run.claim_attempt + 1;
    new_lease_expires_at := least(
        observed_at + pg_catalog.make_interval(secs => target_lease_seconds),
        request_row.effective_expires_at
    );
    if new_lease_expires_at <= observed_at then
        raise exception 'harmony_preview_codex_gate_request_expired';
    end if;
    new_claim_fence_sha256 := private.agent_json_sha256(
        pg_catalog.jsonb_build_object(
            'claim_attempt', next_claim_attempt,
            'claim_receipt_id', claim_id::text,
            'claimed_at', private.harmony_preview_codex_timestamp(observed_at),
            'lease_expires_at', private.harmony_preview_codex_timestamp(
                new_lease_expires_at
            ),
            'request_key', request_row.request_key,
            'reviewer_principal_id', request_row.reviewer_principal_id::text,
            'work_key', request_row.work_key
        )
    );
    transition_row := private.harmony_preview_codex_append_transition(
        request_row, 'claim', 'pending', 'claimed', null,
        observed_at, claim_id
    );
    body := pg_catalog.jsonb_build_object(
        'claim_attempt', next_claim_attempt,
        'claim_fence_sha256', new_claim_fence_sha256,
        'claim_receipt_id', claim_id::text,
        'claimed_at', private.harmony_preview_codex_timestamp(observed_at),
        'lease_expires_at', private.harmony_preview_codex_timestamp(
            new_lease_expires_at
        ),
        'request_id', request_row.request_id::text,
        'request_key', request_row.request_key,
        'reviewer_principal_id', request_row.reviewer_principal_id::text,
        'schema_version', 'squid-codex-claim-receipt@1',
        'transition_id', transition_row.transition_id::text,
        'work_key', request_row.work_key
    );
    body_sha := private.agent_json_sha256(body);
    body := body || pg_catalog.jsonb_build_object(
        'payload_sha256', body_sha
    );
    insert into private.harmony_preview_codex_gate_claim_receipts (
        workspace_id, client_id, claim_receipt_id, request_id, request_key,
        transition_id, claim_attempt, reviewer_principal_id, claimed_at,
        lease_expires_at, claim_fence_sha256, payload, payload_sha256
    ) values (
        request_row.workspace_id, request_row.client_id, claim_id,
        request_row.request_id, request_row.request_key,
        transition_row.transition_id, next_claim_attempt,
        request_row.reviewer_principal_id, observed_at,
        new_lease_expires_at, new_claim_fence_sha256, body, body_sha
    );
    update private.harmony_preview_codex_gate_runs run
    set status = 'claimed',
        status_version = run.status_version + 1,
        claim_attempt = next_claim_attempt,
        claim_receipt_id = claim_id,
        claim_fence_sha256 = new_claim_fence_sha256,
        claimed_at = observed_at,
        lease_expires_at = new_lease_expires_at,
        last_event_sha256 = transition_row.event_sha256,
        updated_at = observed_at
    where run.workspace_id = request_row.workspace_id
      and run.client_id = request_row.client_id
      and run.work_key = request_row.work_key;
    return pg_catalog.jsonb_build_object(
        'claim_attempt', next_claim_attempt,
        'claim_fence_sha256', new_claim_fence_sha256,
        'claimed', true,
        'lease_expires_at', private.harmony_preview_codex_timestamp(
            new_lease_expires_at
        ),
        'request_key', request_row.request_key,
        'work_key', request_row.work_key
    );
end;
$$;

create or replace function public.start_preview_harmony_squid_codex_qa_attempt(
    target_workspace_id uuid,
    target_client_id text,
    target_work_key text,
    target_claim_fence_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    current_run private.harmony_preview_codex_gate_runs%rowtype;
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    lineage private.harmony_preview_codex_source_lineage_receipts%rowtype;
    claim_row private.harmony_preview_codex_gate_claim_receipts%rowtype;
    existing_attempt private.harmony_preview_codex_gate_attempt_receipts%rowtype;
    transition_row private.harmony_preview_codex_gate_transitions%rowtype;
    binding jsonb;
    attempt_id uuid := extensions.gen_random_uuid();
    observed_at timestamptz;
    generated_attempt_fence text;
    body jsonb;
    body_sha text;
    execute_authorized boolean := false;
begin
    if target_client_id <> 'squid'
       or target_work_key !~ '^[a-f0-9]{64}$'
       or target_claim_fence_sha256 !~ '^[a-f0-9]{64}$'
    then
        raise exception 'harmony_preview_codex_gate_scope_invalid';
    end if;
    perform private.harmony_preview_codex_qa_scope_preflight(
        target_workspace_id, target_client_id,
        pg_catalog.clock_timestamp()
    );
    perform private.harmony_preview_codex_lock_tenant(
        target_workspace_id, target_client_id
    );
    select candidate.* into strict current_run
    from private.harmony_preview_codex_gate_runs candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.work_key = target_work_key
    for update;
    select candidate.* into strict request_row
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.workspace_id = current_run.workspace_id
      and candidate.client_id = current_run.client_id
      and candidate.request_id = current_run.request_id
    for share;
    select candidate.* into strict lineage
    from private.harmony_preview_codex_source_lineage_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.lineage_receipt_id = request_row.lineage_receipt_id;
    perform private.harmony_preview_codex_lock_plan_dependencies(
        request_row.workspace_id, request_row.client_id,
        request_row.round_id, request_row.plan_id
    );
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_qa_outcome:' || request_row.workspace_id::text || ':' ||
        request_row.client_id || ':' || request_row.plan_id::text || ':' ||
        lineage.private_content_output_sha256,
        0
    ));
    if exists (
        select 1
        from private.harmony_preview_qa_denial_receipts denial
        where denial.workspace_id = request_row.workspace_id
          and denial.client_id = request_row.client_id
          and denial.plan_id = request_row.plan_id
          and denial.denied_output_sha256
                = lineage.private_content_output_sha256
    ) then
        raise exception 'harmony_preview_qa_output_already_denied';
    end if;
    observed_at := pg_catalog.clock_timestamp();
    binding := private.harmony_preview_codex_qa_binding(
        request_row.workspace_id, request_row.client_id, observed_at
    );
    if (binding ->> 'principal_id')::uuid
            is distinct from request_row.reviewer_principal_id
       or not private.harmony_preview_codex_request_current(
            request_row.request_id, observed_at
       )
    then
        raise exception 'harmony_preview_codex_gate_not_current';
    end if;
    select candidate.* into existing_attempt
    from private.harmony_preview_codex_gate_attempt_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.request_id = request_row.request_id;
    if found then
        if existing_attempt.claim_fence_sha256 is distinct from
                target_claim_fence_sha256
        then
            raise exception 'harmony_preview_codex_gate_claim_fence_invalid';
        end if;
        return pg_catalog.jsonb_build_object(
            'attempt_fence_sha256', existing_attempt.attempt_fence_sha256,
            'execute_authorized', execute_authorized,
            'request_key', request_row.request_key,
            'reused', true,
            'work_key', request_row.work_key
        );
    end if;
    if current_run.status <> 'claimed'
       or current_run.claim_fence_sha256 is distinct from
            target_claim_fence_sha256
    then
        raise exception 'harmony_preview_codex_gate_claim_fence_invalid';
    end if;
    if observed_at >= current_run.lease_expires_at
       or observed_at >= request_row.effective_expires_at
    then
        raise exception 'harmony_preview_codex_gate_lease_expired';
    end if;
    select candidate.* into strict claim_row
    from private.harmony_preview_codex_gate_claim_receipts candidate
    where candidate.workspace_id = current_run.workspace_id
      and candidate.client_id = current_run.client_id
      and candidate.claim_receipt_id = current_run.claim_receipt_id
      and candidate.claim_fence_sha256 = target_claim_fence_sha256;
    generated_attempt_fence := private.agent_json_sha256(
        pg_catalog.jsonb_build_object(
            'attempt_receipt_id', attempt_id::text,
            'attempt_started_at', private.harmony_preview_codex_timestamp(
                observed_at
            ),
            'claim_fence_sha256', target_claim_fence_sha256,
            'request_key', request_row.request_key,
            'work_key', request_row.work_key
        )
    );
    transition_row := private.harmony_preview_codex_append_transition(
        request_row, 'start_attempt', 'claimed', 'attempt_started', null,
        observed_at, attempt_id
    );
    body := pg_catalog.jsonb_build_object(
        'attempt_fence_sha256', generated_attempt_fence,
        'attempt_receipt_id', attempt_id::text,
        'attempt_started_at', private.harmony_preview_codex_timestamp(
            observed_at
        ),
        'claim_fence_sha256', target_claim_fence_sha256,
        'claim_receipt_id', claim_row.claim_receipt_id::text,
        'execute_authorized', true,
        'request_id', request_row.request_id::text,
        'request_key', request_row.request_key,
        'schema_version', 'squid-codex-attempt-receipt@1',
        'transition_id', transition_row.transition_id::text,
        'work_key', request_row.work_key
    );
    body_sha := private.agent_json_sha256(body);
    body := body || pg_catalog.jsonb_build_object(
        'payload_sha256', body_sha
    );
    insert into private.harmony_preview_codex_gate_attempt_receipts (
        workspace_id, client_id, attempt_receipt_id, request_id,
        request_key, transition_id, claim_receipt_id,
        claim_fence_sha256, attempt_started_at, attempt_fence_sha256,
        execute_authorized, payload, payload_sha256
    ) values (
        request_row.workspace_id, request_row.client_id, attempt_id,
        request_row.request_id, request_row.request_key,
        transition_row.transition_id, claim_row.claim_receipt_id,
        target_claim_fence_sha256, observed_at, generated_attempt_fence,
        true, body, body_sha
    );
    execute_authorized := true;
    update private.harmony_preview_codex_gate_runs run
    set status = 'attempt_started',
        status_version = run.status_version + 1,
        attempt_receipt_id = attempt_id,
        attempt_fence_sha256 = generated_attempt_fence,
        attempt_started_at = observed_at,
        last_event_sha256 = transition_row.event_sha256,
        updated_at = observed_at
    where run.workspace_id = request_row.workspace_id
      and run.client_id = request_row.client_id
      and run.work_key = request_row.work_key;
    return pg_catalog.jsonb_build_object(
        'attempt_fence_sha256', generated_attempt_fence,
        'execute_authorized', execute_authorized,
        'request_key', request_row.request_key,
        'reused', false,
        'work_key', request_row.work_key
    );
end;
$$;

create or replace function public.submit_preview_harmony_squid_codex_qa_result(
    target_workspace_id uuid,
    target_client_id text,
    target_work_key text,
    target_attempt_fence_sha256 text,
    target_criteria jsonb,
    target_qa_output_sha256 text,
    target_finding_codes text[],
    target_verdict text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    current_run private.harmony_preview_codex_gate_runs%rowtype;
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    lineage private.harmony_preview_codex_source_lineage_receipts%rowtype;
    attempt_row private.harmony_preview_codex_gate_attempt_receipts%rowtype;
    existing_result private.harmony_preview_codex_gate_result_receipts%rowtype;
    existing_evidence private.harmony_preview_codex_semantic_qa_evidence%rowtype;
    transition_row private.harmony_preview_codex_gate_transitions%rowtype;
    binding jsonb;
    evidence_id uuid := extensions.gen_random_uuid();
    result_id uuid := extensions.gen_random_uuid();
    observed_at timestamptz;
    evidence_body jsonb;
    evidence_sha text;
    result_body jsonb;
    result_sha text;
    criteria_all_true boolean;
    cost_observation text := 'unobserved';
    observed_cost_microusd bigint := null;
begin
    if target_client_id <> 'squid'
       or target_work_key !~ '^[a-f0-9]{64}$'
       or target_attempt_fence_sha256 !~ '^[a-f0-9]{64}$'
       or target_qa_output_sha256 !~ '^[a-f0-9]{64}$'
    then
        raise exception 'harmony_preview_codex_gate_scope_invalid';
    end if;
    perform private.harmony_preview_codex_qa_scope_preflight(
        target_workspace_id, target_client_id,
        pg_catalog.clock_timestamp()
    );
    if target_verdict not in ('pass', 'needs_changes', 'blocked')
       or target_criteria is null
       or pg_catalog.jsonb_typeof(target_criteria) <> 'object'
       or (select pg_catalog.array_agg(key order by key)
           from pg_catalog.jsonb_object_keys(target_criteria) key)
            is distinct from array[
                'automatic_publication_off', 'factual_binding',
                'no_external_calls', 'output_contract_valid',
                'private_boundary_preserved', 'source_lineage_complete'
            ]::text[]
       or exists (
            select 1 from pg_catalog.jsonb_each(target_criteria) criterion
            where pg_catalog.jsonb_typeof(criterion.value) <> 'boolean'
       )
       or pg_catalog.cardinality(target_finding_codes) > 9
       or target_finding_codes is distinct from coalesce((
            select pg_catalog.array_agg(value order by value)
            from (select distinct value
                  from pg_catalog.unnest(target_finding_codes) value) sorted
       ), array[]::text[])
       or not target_finding_codes <@ array[
            'automatic_publication_enabled', 'evidence_incomplete',
            'external_call_detected', 'factual_binding_failed',
            'language_or_brand_mismatch', 'private_boundary_failed',
            'review_execution_blocked', 'source_version_stale',
            'unsupported_claim'
       ]::text[]
    then
        raise exception 'harmony_preview_codex_gate_evidence_invalid';
    end if;
    select pg_catalog.bool_and((criterion.value #>> '{}')::boolean)
    into strict criteria_all_true
    from pg_catalog.jsonb_each(target_criteria) criterion;
    if (target_verdict = 'pass' and (
            not criteria_all_true
            or pg_catalog.cardinality(target_finding_codes) <> 0
       )) or (target_verdict <> 'pass' and
            pg_catalog.cardinality(target_finding_codes) = 0)
    then
        raise exception 'harmony_preview_codex_gate_evidence_verdict_invalid';
    end if;
    perform private.harmony_preview_codex_lock_tenant(
        target_workspace_id, target_client_id
    );
    select candidate.* into strict current_run
    from private.harmony_preview_codex_gate_runs candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.work_key = target_work_key
    for update;
    select candidate.* into strict request_row
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.workspace_id = current_run.workspace_id
      and candidate.client_id = current_run.client_id
      and candidate.request_id = current_run.request_id
    for share;
    select candidate.* into strict lineage
    from private.harmony_preview_codex_source_lineage_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.lineage_receipt_id = request_row.lineage_receipt_id;
    perform private.harmony_preview_codex_lock_plan_dependencies(
        request_row.workspace_id, request_row.client_id,
        request_row.round_id, request_row.plan_id
    );
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_qa_outcome:' || request_row.workspace_id::text || ':' ||
        request_row.client_id || ':' || request_row.plan_id::text || ':' ||
        lineage.private_content_output_sha256,
        0
    ));
    observed_at := pg_catalog.clock_timestamp();
    binding := private.harmony_preview_codex_qa_binding(
        request_row.workspace_id, request_row.client_id, observed_at
    );
    if (binding ->> 'principal_id')::uuid
            is distinct from request_row.reviewer_principal_id
       or binding ->> 'producer_release_sha'
            is distinct from request_row.reviewer_release_sha
       or binding ->> 'config_sha256'
            is distinct from request_row.reviewer_config_sha256
       or not private.harmony_preview_codex_request_current(
            request_row.request_id, observed_at
       )
    then
        raise exception 'harmony_preview_codex_gate_not_current';
    end if;
    if exists (
        select 1
        from private.harmony_preview_qa_denial_receipts denial
        where denial.workspace_id = request_row.workspace_id
          and denial.client_id = request_row.client_id
          and denial.plan_id = request_row.plan_id
          and denial.denied_output_sha256
                = lineage.private_content_output_sha256
    ) then
        raise exception 'harmony_preview_qa_output_already_denied';
    end if;
    select candidate.* into existing_result
    from private.harmony_preview_codex_gate_result_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.request_id = request_row.request_id;
    if found then
        select candidate.* into strict existing_evidence
        from private.harmony_preview_codex_semantic_qa_evidence candidate
        where candidate.workspace_id = existing_result.workspace_id
          and candidate.client_id = existing_result.client_id
          and candidate.evidence_id = existing_result.evidence_id;
        if existing_result.attempt_fence_sha256 is distinct from
                target_attempt_fence_sha256
           or existing_result.qa_output_sha256 is distinct from
                target_qa_output_sha256
           or existing_result.verdict is distinct from target_verdict
           or existing_evidence.criteria is distinct from target_criteria
           or existing_evidence.finding_codes is distinct from
                target_finding_codes
        then
            raise exception 'harmony_preview_codex_gate_result_conflict';
        end if;
        return pg_catalog.jsonb_build_object(
            'result_sha256', existing_result.receipt_sha256,
            'reused', true,
            'status', current_run.status,
            'work_key', request_row.work_key
        );
    end if;
    if current_run.status <> 'attempt_started'
       or current_run.attempt_fence_sha256 is distinct from
            target_attempt_fence_sha256
    then
        raise exception 'harmony_preview_codex_gate_state_invalid';
    end if;
    if current_run.attempt_started_at is null
       or observed_at < current_run.attempt_started_at
       or observed_at >= current_run.lease_expires_at
       or observed_at >= request_row.effective_expires_at
    then
        raise exception 'harmony_preview_codex_gate_result_time_invalid';
    end if;
    select candidate.* into strict attempt_row
    from private.harmony_preview_codex_gate_attempt_receipts candidate
    where candidate.workspace_id = current_run.workspace_id
      and candidate.client_id = current_run.client_id
      and candidate.attempt_receipt_id = current_run.attempt_receipt_id
      and candidate.attempt_fence_sha256 = target_attempt_fence_sha256;
    evidence_body := pg_catalog.jsonb_build_object(
        'assignment_key', request_row.assignment_key,
        'attempt_fence_sha256', target_attempt_fence_sha256,
        'automatic_publication', false,
        'content_snapshot_sha256', lineage.content_snapshot_sha256,
        'credentials_included', false,
        'criteria', target_criteria,
        'evidence_id', evidence_id::text,
        'external_calls', false,
        'findings', pg_catalog.to_jsonb(target_finding_codes),
        'official_content_version_id',
            lineage.official_content_version_id::text,
        'official_source_binding_sha256',
            lineage.official_source_binding_sha256,
        'official_source_item_id', lineage.official_source_item_id::text,
        'private_content_receipt_sha256',
            lineage.private_content_receipt_sha256,
        'provider_calls', false,
        'publication_calls', false,
        'qa_output_sha256', target_qa_output_sha256,
        'raw_private_content_included', false,
        'recorded_at', private.harmony_preview_codex_timestamp(observed_at),
        'request_key', request_row.request_key,
        'reviewed_output_sha256', lineage.private_content_output_sha256,
        'reviewer_config_sha256', request_row.reviewer_config_sha256,
        'reviewer_principal_id', request_row.reviewer_principal_id::text,
        'reviewer_release_sha', request_row.reviewer_release_sha,
        'reviewer_specialist_binding_sha256',
            request_row.reviewer_specialist_binding_sha256,
        'schema_version', 'squid-codex-semantic-qa-evidence@1',
        'source_lineage_sha256', lineage.lineage_sha256,
        'verdict', target_verdict,
        'verifier_contract_version', 'squid-codex-semantic-qa@1',
        'work_key', request_row.work_key
    );
    evidence_sha := private.agent_json_sha256(evidence_body);
    evidence_body := evidence_body || pg_catalog.jsonb_build_object(
        'evidence_sha256', evidence_sha
    );
    result_body := pg_catalog.jsonb_build_object(
        'approved_cost_cap_microusd', request_row.approved_cost_cap_microusd,
        'assignment_key', request_row.assignment_key,
        'attempt_fence_sha256', target_attempt_fence_sha256,
        'automatic_publication', false,
        'client_id', request_row.client_id,
        'content_snapshot_sha256', lineage.content_snapshot_sha256,
        'cost_observation', cost_observation,
        'evidence_sha256', evidence_sha,
        'external_calls', false,
        'observed_cost_microusd', observed_cost_microusd,
        'official_content_version_id',
            lineage.official_content_version_id::text,
        'official_source_binding_sha256',
            lineage.official_source_binding_sha256,
        'official_source_item_id', lineage.official_source_item_id::text,
        'plan_id', request_row.plan_id::text,
        'private_content_output_sha256',
            lineage.private_content_output_sha256,
        'private_content_producer_principal_id',
            lineage.private_content_principal_id::text,
        'private_content_receipt_id',
            lineage.private_content_receipt_id::text,
        'private_content_receipt_sha256',
            lineage.private_content_receipt_sha256,
        'provider_calls', false,
        'publication_calls', false,
        'qa_output_sha256', target_qa_output_sha256,
        'recorded_at', private.harmony_preview_codex_timestamp(observed_at),
        'request_key', request_row.request_key,
        'result_receipt_id', result_id::text,
        'reviewer_config_sha256', request_row.reviewer_config_sha256,
        'reviewer_principal_id', request_row.reviewer_principal_id::text,
        'reviewer_release_sha', request_row.reviewer_release_sha,
        'reviewer_specialist_binding_sha256',
            request_row.reviewer_specialist_binding_sha256,
        'round_id', request_row.round_id::text,
        'schema_version', 'squid-codex-gate-result@1',
        'signal_input_set_sha256', lineage.signal_input_set_sha256,
        'signal_manifest_sha256', lineage.signal_manifest_sha256,
        'signal_producer_principal_ids',
            pg_catalog.to_jsonb(lineage.signal_producer_principal_ids),
        'source_lineage_sha256', lineage.lineage_sha256,
        'specialist_code', 'squid_independent_qa',
        'verdict', target_verdict,
        'work_key', request_row.work_key,
        'workspace_id', request_row.workspace_id::text
    );
    result_sha := private.agent_json_sha256(result_body);
    result_body := result_body || pg_catalog.jsonb_build_object(
        'receipt_sha256', result_sha
    );
    transition_row := private.harmony_preview_codex_append_transition(
        request_row, 'submit_result', 'attempt_started', 'result_submitted',
        null, observed_at, result_id
    );
    insert into private.harmony_preview_codex_semantic_qa_evidence (
        workspace_id, client_id, evidence_id, request_id, request_key,
        attempt_receipt_id, attempt_fence_sha256, source_lineage_sha256,
        private_content_receipt_sha256, reviewed_output_sha256,
        official_content_version_id, official_source_item_id,
        official_source_binding_sha256, content_snapshot_sha256,
        reviewer_principal_id, reviewer_specialist_binding_sha256,
        reviewer_release_sha, reviewer_config_sha256, qa_output_sha256,
        criteria, finding_codes, verdict, verifier_contract_version,
        recorded_at, raw_private_content_included, credentials_included,
        automatic_publication, provider_calls, external_calls,
        publication_calls, payload, evidence_sha256
    ) values (
        request_row.workspace_id, request_row.client_id, evidence_id,
        request_row.request_id, request_row.request_key,
        attempt_row.attempt_receipt_id, target_attempt_fence_sha256,
        lineage.lineage_sha256, lineage.private_content_receipt_sha256,
        lineage.private_content_output_sha256,
        lineage.official_content_version_id, lineage.official_source_item_id,
        lineage.official_source_binding_sha256,
        lineage.content_snapshot_sha256, request_row.reviewer_principal_id,
        request_row.reviewer_specialist_binding_sha256,
        request_row.reviewer_release_sha, request_row.reviewer_config_sha256,
        target_qa_output_sha256, target_criteria, target_finding_codes,
        target_verdict, 'squid-codex-semantic-qa@1', observed_at,
        false, false, false, false, false, false,
        evidence_body, evidence_sha
    );
    insert into private.harmony_preview_codex_gate_result_receipts (
        workspace_id, client_id, result_receipt_id, request_id,
        request_key, work_key, assignment_key, transition_id,
        attempt_receipt_id, attempt_fence_sha256, evidence_id,
        evidence_sha256, qa_output_sha256, verdict,
        approved_cost_cap_microusd, cost_observation,
        observed_cost_microusd, recorded_at, automatic_publication,
        provider_calls, external_calls, publication_calls,
        payload, receipt_sha256
    ) values (
        request_row.workspace_id, request_row.client_id, result_id,
        request_row.request_id, request_row.request_key, request_row.work_key,
        request_row.assignment_key, transition_row.transition_id,
        attempt_row.attempt_receipt_id, target_attempt_fence_sha256,
        evidence_id, evidence_sha, target_qa_output_sha256, target_verdict,
        request_row.approved_cost_cap_microusd, cost_observation,
        observed_cost_microusd, observed_at, false, false, false, false,
        result_body, result_sha
    );
    update private.harmony_preview_codex_gate_runs run
    set status = 'result_submitted',
        status_version = run.status_version + 1,
        result_receipt_id = result_id,
        result_submitted_at = observed_at,
        last_event_sha256 = transition_row.event_sha256,
        updated_at = observed_at
    where run.workspace_id = request_row.workspace_id
      and run.client_id = request_row.client_id
      and run.work_key = request_row.work_key;
    return pg_catalog.jsonb_build_object(
        'result_sha256', result_sha,
        'reused', false,
        'status', 'result_submitted',
        'work_key', request_row.work_key
    );
end;
$$;

create or replace function public.verify_preview_harmony_squid_codex_qa_result(
    target_workspace_id uuid,
    target_client_id text,
    target_work_key text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    current_run private.harmony_preview_codex_gate_runs%rowtype;
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    lineage private.harmony_preview_codex_source_lineage_receipts%rowtype;
    result_row private.harmony_preview_codex_gate_result_receipts%rowtype;
    evidence_row private.harmony_preview_codex_semantic_qa_evidence%rowtype;
    existing private.harmony_preview_codex_gate_verification_receipts%rowtype;
    existing_stage agent_runtime.harmony_stage_receipts%rowtype;
    private_stage agent_runtime.harmony_stage_receipts%rowtype;
    transition_row private.harmony_preview_codex_gate_transitions%rowtype;
    binding jsonb;
    stage_binding jsonb;
    verification_id uuid := extensions.gen_random_uuid();
    stage_receipt_id uuid := extensions.gen_random_uuid();
    observed_at timestamptz;
    target_status text;
    target_outcome text;
    target_reason text;
    body jsonb;
    body_sha text;
    artifact jsonb;
    artifact_sha text;
    stage_payload jsonb;
    stage_sha text;
begin
    if target_client_id <> 'squid'
       or target_work_key !~ '^[a-f0-9]{64}$'
    then
        raise exception 'harmony_preview_codex_gate_scope_invalid';
    end if;
    perform private.harmony_preview_codex_qa_scope_preflight(
        target_workspace_id, target_client_id,
        pg_catalog.clock_timestamp()
    );
    perform private.harmony_preview_codex_lock_tenant(
        target_workspace_id, target_client_id
    );
    select candidate.* into strict current_run
    from private.harmony_preview_codex_gate_runs candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.work_key = target_work_key
    for update;
    select candidate.* into strict request_row
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.workspace_id = current_run.workspace_id
      and candidate.client_id = current_run.client_id
      and candidate.request_id = current_run.request_id
    for share;
    select candidate.* into strict lineage
    from private.harmony_preview_codex_source_lineage_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.lineage_receipt_id = request_row.lineage_receipt_id;
    perform private.harmony_preview_codex_lock_plan_dependencies(
        request_row.workspace_id, request_row.client_id,
        request_row.round_id, request_row.plan_id
    );
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_qa_outcome:' || request_row.workspace_id::text || ':' ||
        request_row.client_id || ':' || request_row.plan_id::text || ':' ||
        lineage.private_content_output_sha256,
        0
    ));
    observed_at := pg_catalog.clock_timestamp();
    binding := private.harmony_preview_codex_qa_binding(
        request_row.workspace_id, request_row.client_id, observed_at
    );
    if not private.harmony_preview_round_inputs_current(
        request_row.workspace_id, request_row.client_id,
        lineage.signal_manifest
    ) or not private.harmony_preview_qa_actor_independent(
        request_row.workspace_id, request_row.client_id,
        request_row.plan_id, request_row.reviewer_principal_id
    ) or (binding ->> 'principal_id')::uuid is distinct from
        request_row.reviewer_principal_id
      or not private.harmony_preview_codex_request_current(
        request_row.request_id, observed_at
    ) then
        raise exception 'harmony_preview_codex_gate_not_current';
    end if;
    if exists (
        select 1
        from private.harmony_preview_qa_denial_receipts denial
        where denial.workspace_id = request_row.workspace_id
          and denial.client_id = request_row.client_id
          and denial.plan_id = request_row.plan_id
          and denial.denied_output_sha256
                = lineage.private_content_output_sha256
    ) then
        raise exception 'harmony_preview_qa_output_already_denied';
    end if;
    select candidate.* into strict result_row
    from private.harmony_preview_codex_gate_result_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.request_id = request_row.request_id;
    select candidate.* into strict evidence_row
    from private.harmony_preview_codex_semantic_qa_evidence candidate
    where candidate.workspace_id = result_row.workspace_id
      and candidate.client_id = result_row.client_id
      and candidate.evidence_id = result_row.evidence_id;
    if result_row.evidence_sha256 <> evidence_row.evidence_sha256
       or result_row.qa_output_sha256 <> evidence_row.qa_output_sha256
       or result_row.verdict <> evidence_row.verdict
       or result_row.receipt_sha256 <>
            private.agent_json_sha256(result_row.payload - 'receipt_sha256')
       or evidence_row.evidence_sha256 <>
            private.agent_json_sha256(evidence_row.payload - 'evidence_sha256')
    then
        raise exception 'harmony_preview_codex_gate_result_invalid';
    end if;
    select candidate.* into existing
    from private.harmony_preview_codex_gate_verification_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.request_id = request_row.request_id;
    if found then
        select candidate.* into existing_stage
        from agent_runtime.harmony_stage_receipts candidate
        where candidate.workspace_id = request_row.workspace_id
          and candidate.client_id = request_row.client_id
          and candidate.round_id = request_row.round_id
          and candidate.plan_id = request_row.plan_id
          and candidate.stage = 'independent_qa';
        if existing.verification_outcome = 'passed' and not found then
            raise exception 'harmony_preview_codex_verified_stage_missing';
        end if;
        return pg_catalog.jsonb_build_object(
            'reused', true,
            'status', current_run.status,
            'stage_receipt', case
                when existing.verification_outcome = 'passed'
                then existing_stage.payload else null end,
            'verification_receipt_sha256', existing.receipt_sha256,
            'work_key', request_row.work_key
        );
    end if;
    if current_run.status <> 'result_submitted' then
        raise exception 'harmony_preview_codex_gate_state_invalid';
    end if;
    if result_row.verdict = 'pass' then
        target_status := 'verified';
        target_outcome := 'passed';
        target_reason := null;
    elsif result_row.verdict = 'needs_changes' then
        target_status := 'needs_changes';
        target_outcome := 'needs_changes';
        target_reason := 'result_needs_changes';
    else
        target_status := 'blocked';
        target_outcome := 'blocked';
        target_reason := 'result_blocked';
    end if;
    transition_row := private.harmony_preview_codex_append_transition(
        request_row, 'verify_result', 'result_submitted', target_status,
        target_reason, observed_at, verification_id
    );
    body := pg_catalog.jsonb_build_object(
        'automatic_publication', false,
        'evidence_id', evidence_row.evidence_id::text,
        'evidence_sha256', evidence_row.evidence_sha256,
        'external_calls', false,
        'publication_calls', false,
        'request_id', request_row.request_id::text,
        'request_key', request_row.request_key,
        'result_receipt_id', result_row.result_receipt_id::text,
        'result_receipt_sha256', result_row.receipt_sha256,
        'schema_version', 'squid-codex-gate-verification@1',
        'verification_outcome', target_outcome,
        'verification_receipt_id', verification_id::text,
        'verified_at', private.harmony_preview_codex_timestamp(observed_at),
        'work_key', request_row.work_key
    );
    body_sha := private.agent_json_sha256(body);
    body := body || pg_catalog.jsonb_build_object(
        'receipt_sha256', body_sha
    );
    insert into private.harmony_preview_codex_gate_verification_receipts (
        workspace_id, client_id, verification_receipt_id, request_id,
        request_key, transition_id, result_receipt_id,
        result_receipt_sha256, evidence_id, evidence_sha256,
        verification_outcome, verified_at, automatic_publication,
        external_calls, publication_calls, payload, receipt_sha256
    ) values (
        request_row.workspace_id, request_row.client_id, verification_id,
        request_row.request_id, request_row.request_key,
        transition_row.transition_id, result_row.result_receipt_id,
        result_row.receipt_sha256, evidence_row.evidence_id,
        evidence_row.evidence_sha256, target_outcome, observed_at,
        false, false, false, body, body_sha
    );
    update private.harmony_preview_codex_gate_runs run
    set status = target_status,
        status_version = run.status_version + 1,
        verification_receipt_id = verification_id,
        last_event_sha256 = transition_row.event_sha256,
        updated_at = observed_at
    where run.workspace_id = request_row.workspace_id
      and run.client_id = request_row.client_id
      and run.work_key = request_row.work_key;
    if result_row.verdict = 'pass' then
        select candidate.* into strict private_stage
        from agent_runtime.harmony_stage_receipts candidate
        where candidate.workspace_id = request_row.workspace_id
          and candidate.client_id = request_row.client_id
          and candidate.round_id = request_row.round_id
          and candidate.plan_id = request_row.plan_id
          and candidate.stage = 'private_content'
        for share;
        stage_binding := private.harmony_preview_stage_binding();
        if (stage_binding ->> 'principal_id')::uuid
                is distinct from request_row.reviewer_principal_id
           or stage_binding ->> 'producer_release_sha'
                is distinct from request_row.reviewer_release_sha
           or stage_binding ->> 'config_sha256'
                is distinct from request_row.reviewer_config_sha256
           or stage_binding ->> 'specialist_binding_sha256'
                is distinct from request_row.reviewer_specialist_binding_sha256
        then
            raise exception 'harmony_preview_codex_gate_not_current';
        end if;
        artifact := pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'evidence_id', evidence_row.evidence_id::text,
            'evidence_sha256', evidence_row.evidence_sha256,
            'result_receipt_id', result_row.result_receipt_id::text,
            'result_receipt_sha256', result_row.receipt_sha256,
            'reviewed_output_sha256',
                lineage.private_content_output_sha256,
            'reviewer_principal_id',
                request_row.reviewer_principal_id::text,
            'schema_version', 'squid-codex-verified-qa-stage@1',
            'synthetic', true,
            'verdict', 'pass',
            'verification_receipt_id', verification_id::text,
            'verification_receipt_sha256', body_sha,
            'verifier_contract_version', 'squid-codex-semantic-qa@1'
        );
        artifact_sha := private.agent_json_sha256(artifact);
        stage_payload := private.harmony_preview_stage_receipt_payload(
            stage_receipt_id, request_row.workspace_id,
            request_row.client_id, request_row.round_id,
            request_row.plan_id, 'independent_qa', 3::smallint, 'codex',
            private_stage.receipt_sha256,
            lineage.private_content_output_sha256,
            result_row.qa_output_sha256, observed_at, 'passed',
            request_row.reviewer_principal_id
        );
        stage_sha := stage_payload ->> 'receipt_sha256';
        insert into agent_runtime.harmony_stage_receipts (
            workspace_id, client_id, receipt_id, round_id, plan_id, stage,
            ordinal, actor, principal_id, producer_release_sha,
            config_sha256, capability, binding_receipt_sha256, verdict,
            reviewer_principal_id, previous_receipt_sha256, input_sha256,
            output_sha256, artifact, artifact_sha256, payload,
            receipt_sha256, created_at
        ) values (
            request_row.workspace_id, request_row.client_id,
            stage_receipt_id, request_row.round_id, request_row.plan_id,
            'independent_qa', 3, 'codex',
            request_row.reviewer_principal_id,
            request_row.reviewer_release_sha,
            request_row.reviewer_config_sha256, 'harmony_independent_qa',
            stage_binding ->> 'binding_receipt_sha256', 'passed',
            request_row.reviewer_principal_id,
            private_stage.receipt_sha256,
            lineage.private_content_output_sha256,
            result_row.qa_output_sha256, artifact, artifact_sha,
            stage_payload, stage_sha, observed_at
        ) returning * into strict existing_stage;
        target_status := 'operator_review_pending';
    end if;
    return pg_catalog.jsonb_build_object(
        'reused', false,
        'status', target_status,
        'stage_receipt', case when result_row.verdict = 'pass'
            then existing_stage.payload else null end,
        'verification_receipt_sha256', body_sha,
        'work_key', request_row.work_key
    );
end;
$$;

create or replace function public.reconcile_preview_harmony_squid_codex_qa_lease(
    target_workspace_id uuid,
    target_client_id text,
    target_limit integer default 64
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    current_run private.harmony_preview_codex_gate_runs%rowtype;
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    lineage private.harmony_preview_codex_source_lineage_receipts%rowtype;
    result_row private.harmony_preview_codex_gate_result_receipts%rowtype;
    transition_row private.harmony_preview_codex_gate_transitions%rowtype;
    reconciliation_actor jsonb;
    actor_claims jsonb;
    actor_principal_id uuid;
    actor_release_sha text;
    actor_config_sha256 text;
    actor_branch_ref text;
    actor_issued_epoch bigint;
    reconciliation_id uuid := extensions.gen_random_uuid();
    selection_at timestamptz;
    observed_at timestamptz;
    request_is_current boolean;
    output_denied boolean;
    target_status text;
    target_action text;
    target_reason text;
    target_attempt_id uuid;
    target_result_id uuid;
    body jsonb;
    body_sha text;
begin
    if target_client_id <> 'squid' or target_limit not between 1 and 64 then
        raise exception 'harmony_preview_codex_gate_scope_invalid';
    end if;
    selection_at := pg_catalog.clock_timestamp();
    perform private.harmony_preview_codex_qa_scope_preflight(
        target_workspace_id, target_client_id, selection_at
    );
    begin
        actor_claims := nullif(
            pg_catalog.current_setting('request.jwt.claims', true), ''
        )::jsonb;
        actor_principal_id :=
            (actor_claims ->> 'producer_principal_id')::uuid;
        actor_release_sha := actor_claims ->> 'release_sha';
        actor_config_sha256 := actor_claims ->> 'config_sha256';
        actor_branch_ref := actor_claims ->> 'ref';
        actor_issued_epoch := (actor_claims ->> 'iat')::bigint;
    exception when others then
        raise exception 'harmony_preview_codex_reconciliation_actor_invalid';
    end;
    perform private.harmony_preview_codex_lock_tenant(
        target_workspace_id, target_client_id
    );
    select candidate.* into current_run
    from private.harmony_preview_codex_gate_runs candidate
    join private.harmony_preview_codex_gate_requests queued_request
      on queued_request.workspace_id = candidate.workspace_id
     and queued_request.client_id = candidate.client_id
     and queued_request.request_id = candidate.request_id
    join private.harmony_preview_squid_specialist_bindings actor_specialist
      on actor_specialist.workspace_id = queued_request.workspace_id
     and actor_specialist.client_id = queued_request.client_id
     and actor_specialist.stage = 'independent_qa'
     and actor_specialist.specialist_code = 'squid_independent_qa'
     and actor_specialist.role_name = 'coineasy_harmony_qa'
     and actor_specialist.capability = 'harmony_independent_qa'
     and actor_specialist.actor = 'codex'
     and actor_specialist.binding_sha256
            = queued_request.reviewer_specialist_binding_sha256
     and actor_specialist.principal_id = queued_request.reviewer_principal_id
     and actor_specialist.principal_id = actor_principal_id
     and actor_specialist.producer_release_sha
            = queued_request.reviewer_release_sha
     and actor_specialist.producer_release_sha = actor_release_sha
     and actor_specialist.config_sha256 = queued_request.reviewer_config_sha256
     and actor_specialist.config_sha256 = actor_config_sha256
     and actor_specialist.branch_ref = actor_branch_ref
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and actor_specialist.created_at <= selection_at
      and pg_catalog.to_timestamp(actor_issued_epoch)
            >= pg_catalog.date_trunc('second', actor_specialist.created_at)
      and (
        candidate.status in ('claimed', 'attempt_started')
        or (
            candidate.status = 'pending'
            and not private.harmony_preview_codex_request_current(
                queued_request.request_id, selection_at
            )
        )
        or (
            candidate.status = 'result_submitted'
            and (
                not private.harmony_preview_codex_request_current(
                    queued_request.request_id, selection_at
                )
                or exists (
                    select 1
                    from private.harmony_preview_codex_source_lineage_receipts
                        queued_lineage
                    join private.harmony_preview_qa_denial_receipts denial
                      on denial.workspace_id = queued_lineage.workspace_id
                     and denial.client_id = queued_lineage.client_id
                     and denial.plan_id = queued_lineage.plan_id
                     and denial.denied_output_sha256
                            = queued_lineage.private_content_output_sha256
                    where queued_lineage.workspace_id
                            = queued_request.workspace_id
                      and queued_lineage.client_id = queued_request.client_id
                      and queued_lineage.lineage_receipt_id
                            = queued_request.lineage_receipt_id
                )
            )
        )
      )
    order by candidate.lease_expires_at nulls first, candidate.work_key
    for update of candidate skip locked
    limit 1;
    if not found then
        return pg_catalog.jsonb_build_object(
            'blocked', false,
            'outcome_unknown', false,
            'pending', false,
            'reconciled', false,
            'work_key', null
        );
    end if;
    select candidate.* into strict request_row
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.workspace_id = current_run.workspace_id
      and candidate.client_id = current_run.client_id
      and candidate.request_id = current_run.request_id
    for share;
    select candidate.* into strict lineage
    from private.harmony_preview_codex_source_lineage_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.lineage_receipt_id = request_row.lineage_receipt_id
      and candidate.lineage_sha256 = request_row.lineage_sha256
    for share;
    perform private.harmony_preview_codex_lock_plan_dependencies(
        request_row.workspace_id, request_row.client_id,
        request_row.round_id, request_row.plan_id
    );
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_qa_outcome:' || request_row.workspace_id::text || ':' ||
        request_row.client_id || ':' || request_row.plan_id::text || ':' ||
        lineage.private_content_output_sha256,
        0
    ));
    observed_at := pg_catalog.clock_timestamp();
    reconciliation_actor :=
        private.harmony_preview_codex_reconciliation_actor(
            request_row.workspace_id, request_row.client_id,
            request_row.request_id, observed_at
        );
    request_is_current := private.harmony_preview_codex_request_current(
        request_row.request_id, observed_at
    );
    output_denied := exists (
        select 1
        from private.harmony_preview_qa_denial_receipts denial
        where denial.workspace_id = request_row.workspace_id
          and denial.client_id = request_row.client_id
          and denial.plan_id = request_row.plan_id
          and denial.denied_output_sha256
                = lineage.private_content_output_sha256
    );
    target_attempt_id := null;
    target_result_id := null;
    if current_run.status = 'result_submitted' then
        select candidate.* into strict result_row
        from private.harmony_preview_codex_gate_result_receipts candidate
        where candidate.workspace_id = request_row.workspace_id
          and candidate.client_id = request_row.client_id
          and candidate.request_id = request_row.request_id
          and candidate.result_receipt_id = current_run.result_receipt_id
          and candidate.attempt_receipt_id = current_run.attempt_receipt_id
        for share;
        if request_is_current and not output_denied then
            return pg_catalog.jsonb_build_object(
                'blocked', false,
                'outcome_unknown', false,
                'pending', false,
                'reconciled', false,
                'status', current_run.status,
                'work_key', current_run.work_key
            );
        end if;
        target_status := 'blocked';
        target_action := 'result_not_current';
        target_reason := 'request_not_current';
        target_attempt_id := result_row.attempt_receipt_id;
        target_result_id := result_row.result_receipt_id;
    elsif current_run.status = 'pending' then
        if request_is_current and not output_denied then
            return pg_catalog.jsonb_build_object(
                'blocked', false,
                'outcome_unknown', false,
                'pending', true,
                'reconciled', false,
                'status', current_run.status,
                'work_key', current_run.work_key
            );
        end if;
        target_status := 'blocked';
        target_action := 'request_not_current';
        target_reason := 'request_not_current';
    elsif observed_at < current_run.lease_expires_at then
        return pg_catalog.jsonb_build_object(
            'blocked', false,
            'outcome_unknown', false,
            'pending', false,
            'reconciled', false,
            'status', current_run.status,
            'work_key', current_run.work_key
        );
    elsif current_run.attempt_started_at is not null then
        target_status := 'outcome_unknown';
        target_action := 'outcome_unknown';
        target_reason := 'result_receipt_missing';
        target_attempt_id := current_run.attempt_receipt_id;
    elsif not request_is_current or output_denied then
        target_status := 'blocked';
        target_action := 'request_not_current';
        target_reason := 'request_not_current';
    elsif current_run.claim_attempt < 3
          and observed_at < request_row.effective_expires_at
          and request_is_current then
        target_status := 'pending';
        target_action := 'claim_released';
        target_reason := null;
    else
        target_status := 'blocked';
        target_action := 'claim_limit_exhausted';
        target_reason := 'claim_limit_exhausted';
    end if;
    transition_row := private.harmony_preview_codex_append_transition(
        request_row, 'reconcile', current_run.status, target_status,
        target_reason, observed_at, reconciliation_id
    );
    body := pg_catalog.jsonb_build_object(
        'attempt_receipt_id', target_attempt_id::text,
        'claim_receipt_id', current_run.claim_receipt_id::text,
        'reconciled_at', private.harmony_preview_codex_timestamp(observed_at),
        'reconciler_binding_sha256',
            reconciliation_actor ->> 'binding_sha256',
        'reconciler_principal_id',
            reconciliation_actor ->> 'principal_id',
        'reconciliation_action', target_action,
        'reconciliation_receipt_id', reconciliation_id::text,
        'request_id', request_row.request_id::text,
        'request_key', request_row.request_key,
        'result_receipt_id', target_result_id::text,
        'schema_version', 'squid-codex-gate-reconciliation@1',
        'status', target_status,
        'transition_id', transition_row.transition_id::text,
        'work_key', request_row.work_key
    );
    body_sha := private.agent_json_sha256(body);
    body := body || pg_catalog.jsonb_build_object(
        'receipt_sha256', body_sha
    );
    insert into private.harmony_preview_codex_gate_reconciliation_receipts (
        workspace_id, client_id, reconciliation_receipt_id, request_id,
        request_key, transition_id, claim_receipt_id, attempt_receipt_id,
        result_receipt_id, reconciliation_action, reconciled_at,
        payload, receipt_sha256
    ) values (
        request_row.workspace_id, request_row.client_id, reconciliation_id,
        request_row.request_id, request_row.request_key,
        transition_row.transition_id, current_run.claim_receipt_id,
        target_attempt_id, target_result_id, target_action, observed_at,
        body, body_sha
    );
    update private.harmony_preview_codex_gate_runs run
    set status = target_status,
        status_version = run.status_version + 1,
        claim_receipt_id = case when target_status = 'pending'
            then null else run.claim_receipt_id end,
        claim_fence_sha256 = case when target_status = 'pending'
            then null else run.claim_fence_sha256 end,
        claimed_at = case when target_status = 'pending'
            then null else run.claimed_at end,
        lease_expires_at = case when target_status = 'pending'
            then null else run.lease_expires_at end,
        last_event_sha256 = transition_row.event_sha256,
        updated_at = observed_at
    where run.workspace_id = request_row.workspace_id
      and run.client_id = request_row.client_id
      and run.work_key = request_row.work_key;
    return pg_catalog.jsonb_build_object(
        'blocked', target_status = 'blocked',
        'outcome_unknown', target_status = 'outcome_unknown',
        'pending', target_status = 'pending',
        'reconciled', true,
        'status', target_status,
        'work_key', request_row.work_key
    );
end;
$$;

create or replace function private.harmony_preview_codex_guard_qa_stage_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    run_row private.harmony_preview_codex_gate_runs%rowtype;
    lineage private.harmony_preview_codex_source_lineage_receipts%rowtype;
    result_row private.harmony_preview_codex_gate_result_receipts%rowtype;
    evidence_row private.harmony_preview_codex_semantic_qa_evidence%rowtype;
    verification private.harmony_preview_codex_gate_verification_receipts%rowtype;
    private_stage agent_runtime.harmony_stage_receipts%rowtype;
    observed_at timestamptz;
begin
    if new.stage <> 'independent_qa' then
        return new;
    end if;
    select candidate.* into strict private_stage
    from agent_runtime.harmony_stage_receipts candidate
    where candidate.workspace_id = new.workspace_id
      and candidate.client_id = new.client_id
      and candidate.round_id = new.round_id
      and candidate.plan_id = new.plan_id
      and candidate.stage = 'private_content'
    for share;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_qa_outcome:' || new.workspace_id::text || ':' ||
        new.client_id || ':' || new.plan_id::text || ':' ||
        private_stage.output_sha256,
        0
    ));
    select candidate.* into strict request_row
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.workspace_id = new.workspace_id
      and candidate.client_id = new.client_id
      and candidate.round_id = new.round_id
      and candidate.plan_id = new.plan_id
      and candidate.stage = 'independent_qa'
    for share;
    select candidate.* into strict run_row
    from private.harmony_preview_codex_gate_runs candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.request_id = request_row.request_id
      and candidate.status = 'verified'
    for share;
    select candidate.* into strict lineage
    from private.harmony_preview_codex_source_lineage_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.lineage_receipt_id = request_row.lineage_receipt_id;
    select candidate.* into strict result_row
    from private.harmony_preview_codex_gate_result_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.request_id = request_row.request_id
      and candidate.verdict = 'pass'
    for share;
    select candidate.* into strict evidence_row
    from private.harmony_preview_codex_semantic_qa_evidence candidate
    where candidate.workspace_id = result_row.workspace_id
      and candidate.client_id = result_row.client_id
      and candidate.evidence_id = result_row.evidence_id
    for share;
    select candidate.* into strict verification
    from private.harmony_preview_codex_gate_verification_receipts candidate
    where candidate.workspace_id = result_row.workspace_id
      and candidate.client_id = result_row.client_id
      and candidate.request_id = result_row.request_id
      and candidate.result_receipt_id = result_row.result_receipt_id
      and candidate.verification_outcome = 'passed'
    for share;
    perform private.harmony_preview_codex_lock_plan_dependencies(
        request_row.workspace_id, request_row.client_id,
        request_row.round_id, request_row.plan_id
    );
    observed_at := pg_catalog.clock_timestamp();
    if not private.harmony_preview_codex_request_current(
        request_row.request_id, observed_at
    ) or new.ordinal <> 3
      or new.previous_receipt_sha256 <> private_stage.receipt_sha256
      or new.input_sha256 <> private_stage.output_sha256
      or new.input_sha256 <> lineage.private_content_output_sha256
      or new.output_sha256 <> result_row.qa_output_sha256
      or new.principal_id <> request_row.reviewer_principal_id
      or new.specialist_binding_sha256 <>
            request_row.reviewer_specialist_binding_sha256
      or new.producer_release_sha <> request_row.reviewer_release_sha
      or new.config_sha256 <> request_row.reviewer_config_sha256
      or evidence_row.reviewed_output_sha256 <> new.input_sha256
      or verification.evidence_sha256 <> evidence_row.evidence_sha256
      or new.artifact ->> 'schema_version'
            <> 'squid-codex-verified-qa-stage@1'
      or new.artifact ->> 'result_receipt_id'
            <> result_row.result_receipt_id::text
      or new.artifact ->> 'result_receipt_sha256'
            <> result_row.receipt_sha256
      or new.artifact ->> 'verification_receipt_id'
            <> verification.verification_receipt_id::text
      or new.artifact ->> 'verification_receipt_sha256'
            <> verification.receipt_sha256
      or new.artifact ->> 'evidence_sha256' <> evidence_row.evidence_sha256
      or new.artifact ->> 'verdict' <> 'pass'
      or new.artifact -> 'automatic_publication'
            is distinct from 'false'::jsonb
    then
        raise exception 'harmony_preview_codex_verified_result_required';
    end if;
    return new;
exception
    when no_data_found then
        raise exception 'harmony_preview_codex_verified_result_required';
end;
$$;

-- Trigger names are ordered alphabetically: the existing fixed-specialist
-- binder runs first, then this durable guard validates the bound fields.
create trigger harmony_stage_receipts_codex_verified_guard
before insert on agent_runtime.harmony_stage_receipts
for each row execute function private.harmony_preview_codex_guard_qa_stage_insert();

create or replace function private.harmony_preview_codex_link_qa_stage_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    request_row private.harmony_preview_codex_gate_requests%rowtype;
    run_row private.harmony_preview_codex_gate_runs%rowtype;
    result_row private.harmony_preview_codex_gate_result_receipts%rowtype;
    verification private.harmony_preview_codex_gate_verification_receipts%rowtype;
    transition_row private.harmony_preview_codex_gate_transitions%rowtype;
    link_id uuid := extensions.gen_random_uuid();
    observed_at timestamptz;
    body jsonb;
    body_sha text;
begin
    if new.stage <> 'independent_qa' then
        return new;
    end if;
    select candidate.* into strict request_row
    from private.harmony_preview_codex_gate_requests candidate
    where candidate.workspace_id = new.workspace_id
      and candidate.client_id = new.client_id
      and candidate.round_id = new.round_id
      and candidate.plan_id = new.plan_id
      and candidate.stage = 'independent_qa'
    for share;
    select candidate.* into strict run_row
    from private.harmony_preview_codex_gate_runs candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.request_id = request_row.request_id
      and candidate.status = 'verified'
    for update;
    select candidate.* into strict result_row
    from private.harmony_preview_codex_gate_result_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.request_id = request_row.request_id
      and candidate.verdict = 'pass';
    select candidate.* into strict verification
    from private.harmony_preview_codex_gate_verification_receipts candidate
    where candidate.workspace_id = request_row.workspace_id
      and candidate.client_id = request_row.client_id
      and candidate.request_id = request_row.request_id
      and candidate.verification_outcome = 'passed';
    perform private.harmony_preview_codex_lock_plan_dependencies(
        request_row.workspace_id, request_row.client_id,
        request_row.round_id, request_row.plan_id
    );
    observed_at := pg_catalog.clock_timestamp();
    if not private.harmony_preview_codex_request_current(
        request_row.request_id, observed_at
    ) then
        raise exception 'harmony_preview_codex_gate_not_current';
    end if;
    transition_row := private.harmony_preview_codex_append_transition(
        request_row, 'stage_link', 'verified',
        'operator_review_pending', null, observed_at, link_id
    );
    body := pg_catalog.jsonb_build_object(
        'automatic_publication', false,
        'external_calls', false,
        'linked_at', private.harmony_preview_codex_timestamp(observed_at),
        'operator_decision_recorded', false,
        'plan_id', new.plan_id::text,
        'publication_calls', false,
        'request_id', request_row.request_id::text,
        'request_key', request_row.request_key,
        'result_receipt_id', result_row.result_receipt_id::text,
        'result_receipt_sha256', result_row.receipt_sha256,
        'round_id', new.round_id::text,
        'schema_version', 'squid-codex-stage-link@1',
        'stage_link_id', link_id::text,
        'stage_receipt_id', new.receipt_id::text,
        'stage_receipt_sha256', new.receipt_sha256,
        'verification_receipt_id', verification.verification_receipt_id::text,
        'verification_receipt_sha256', verification.receipt_sha256,
        'work_key', request_row.work_key
    );
    body_sha := private.agent_json_sha256(body);
    body := body || pg_catalog.jsonb_build_object(
        'receipt_sha256', body_sha
    );
    insert into private.harmony_preview_codex_gate_stage_links (
        workspace_id, client_id, stage_link_id, request_id, request_key,
        transition_id, verification_receipt_id,
        verification_receipt_sha256, result_receipt_id,
        result_receipt_sha256, stage_receipt_id, round_id, plan_id,
        stage_receipt_sha256, linked_at, payload, receipt_sha256
    ) values (
        new.workspace_id, new.client_id, link_id, request_row.request_id,
        request_row.request_key, transition_row.transition_id,
        verification.verification_receipt_id, verification.receipt_sha256,
        result_row.result_receipt_id, result_row.receipt_sha256,
        new.receipt_id, new.round_id, new.plan_id, new.receipt_sha256,
        observed_at, body, body_sha
    );
    update private.harmony_preview_codex_gate_runs run
    set status = 'operator_review_pending',
        status_version = run.status_version + 1,
        last_event_sha256 = transition_row.event_sha256,
        updated_at = observed_at
    where run.workspace_id = request_row.workspace_id
      and run.client_id = request_row.client_id
      and run.work_key = request_row.work_key;
    return new;
end;
$$;

create trigger harmony_stage_receipts_codex_verified_link
after insert on agent_runtime.harmony_stage_receipts
for each row execute function private.harmony_preview_codex_link_qa_stage_insert();

revoke all on function private.harmony_preview_codex_timestamp(timestamptz)
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_uuid4_array(uuid[])
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_trust_manifest(
    uuid, text, jsonb, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_lock_plan_dependencies(
    uuid, text, uuid, uuid
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_qa_binding(
    uuid, text, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_qa_scope_preflight(
    uuid, text, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_lock_tenant(uuid, text)
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_reconciliation_actor(
    uuid, text, uuid, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_build_source_lineage(
    uuid, text, uuid, uuid, jsonb, timestamptz, uuid
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_work_key(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_assignment_key(text, jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_request_key(
    text, text, text, timestamptz, bigint
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_request_current(
    uuid, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_append_transition(
    private.harmony_preview_codex_gate_requests,
    text, text, text, text, timestamptz, uuid
) from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_guard_qa_stage_insert()
from public, anon, authenticated, service_role;
revoke all on function private.harmony_preview_codex_link_qa_stage_insert()
from public, anon, authenticated, service_role;

revoke all on function public.prepare_preview_harmony_squid_codex_qa(
    uuid, text, uuid, uuid, bigint
) from public, anon, authenticated, service_role;
revoke all on function public.claim_preview_harmony_squid_codex_qa(
    uuid, text, integer
) from public, anon, authenticated, service_role;
revoke all on function public.start_preview_harmony_squid_codex_qa_attempt(
    uuid, text, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.submit_preview_harmony_squid_codex_qa_result(
    uuid, text, text, text, jsonb, text, text[], text
) from public, anon, authenticated, service_role;
revoke all on function public.verify_preview_harmony_squid_codex_qa_result(
    uuid, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.reconcile_preview_harmony_squid_codex_qa_lease(
    uuid, text, integer
) from public, anon, authenticated, service_role;

revoke all on function public.prepare_preview_harmony_squid_codex_qa(
    uuid, text, uuid, uuid, bigint
) from coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_operator,
    coineasy_harmony_recap, coineasy_harmony_dashboard;
revoke all on function public.claim_preview_harmony_squid_codex_qa(
    uuid, text, integer
) from coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_operator,
    coineasy_harmony_recap, coineasy_harmony_dashboard;
revoke all on function public.start_preview_harmony_squid_codex_qa_attempt(
    uuid, text, text, text
) from coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_operator,
    coineasy_harmony_recap, coineasy_harmony_dashboard;
revoke all on function public.submit_preview_harmony_squid_codex_qa_result(
    uuid, text, text, text, jsonb, text, text[], text
) from coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_operator,
    coineasy_harmony_recap, coineasy_harmony_dashboard;
revoke all on function public.verify_preview_harmony_squid_codex_qa_result(
    uuid, text, text
) from coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_operator,
    coineasy_harmony_recap, coineasy_harmony_dashboard;
revoke all on function public.reconcile_preview_harmony_squid_codex_qa_lease(
    uuid, text, integer
) from coineasy_harmony_connector, coineasy_harmony_orchestrator,
    coineasy_harmony_content, coineasy_harmony_operator,
    coineasy_harmony_recap, coineasy_harmony_dashboard;

grant execute on function public.prepare_preview_harmony_squid_codex_qa(
    uuid, text, uuid, uuid, bigint
) to coineasy_harmony_qa;
grant execute on function public.claim_preview_harmony_squid_codex_qa(
    uuid, text, integer
) to coineasy_harmony_qa;
grant execute on function public.start_preview_harmony_squid_codex_qa_attempt(
    uuid, text, text, text
) to coineasy_harmony_qa;
grant execute on function public.submit_preview_harmony_squid_codex_qa_result(
    uuid, text, text, text, jsonb, text, text[], text
) to coineasy_harmony_qa;
grant execute on function public.verify_preview_harmony_squid_codex_qa_result(
    uuid, text, text
) to coineasy_harmony_qa;
grant execute on function public.reconcile_preview_harmony_squid_codex_qa_lease(
    uuid, text, integer
) to coineasy_harmony_qa;

-- Positive QA can only be emitted by verify_result above.  This closes the
-- former generic append path while preserving the separate denial receipt RPC.
revoke execute on function public.append_preview_harmony_squid_stage(
    uuid, text, uuid, uuid, text, uuid, uuid, jsonb
) from coineasy_harmony_qa;

commit;
