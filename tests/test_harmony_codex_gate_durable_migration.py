"""Static fail-closed contract for the durable Squid Codex QA gate.

These tests deliberately inspect the migration source instead of claiming a
database proof.  The disposable Preview integration suite remains responsible
for exercising the same RPCs with 64 independent database connections.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase/migrations/20260827220000_harmony_preview_codex_gate_durable.sql"
)

RPCS = (
    "prepare_preview_harmony_squid_codex_qa",
    "claim_preview_harmony_squid_codex_qa",
    "start_preview_harmony_squid_codex_qa_attempt",
    "submit_preview_harmony_squid_codex_qa_result",
    "verify_preview_harmony_squid_codex_qa_result",
    "reconcile_preview_harmony_squid_codex_qa_lease",
)

FORBIDDEN_DML_TARGETS = (
    "public.approvals",
    "public.publications",
    "private.grok_qa_dispatch_outbox",
    "private.grok_qa_verdict_receipts",
)


def _migration() -> str:
    assert MIGRATION_PATH.is_file(), (
        "durable Codex gate migration is missing: " f"{MIGRATION_PATH}"
    )
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _function(name: str, *, schema: str = "public") -> str:
    sql = _migration()
    match = re.search(
        rf"create\s+or\s+replace\s+function\s+{schema}[.]{name}\s*[(]"
        rf".*?\n\$(?:[a-z_][a-z0-9_]*)?\$;",
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, f"missing function: {schema}.{name}"
    return match.group(0).lower()


def _function_parameters(function: str) -> str:
    start = function.index("(") + 1
    depth = 1
    for index in range(start, len(function)):
        if function[index] == "(":
            depth += 1
        elif function[index] == ")":
            depth -= 1
            if depth == 0:
                return function[start:index]
    raise AssertionError("function parameter list is not closed")


def _created_table_bodies() -> dict[str, str]:
    sql = _migration().lower()
    return {
        match.group("name"): match.group("body")
        for match in re.finditer(
            r"create\s+table\s+"
            r"(?P<name>(?:agent_runtime|private)[.][a-z0-9_]*codex[a-z0-9_]*)"
            r"\s*[(](?P<body>.*?)\n[)];",
            sql,
            re.DOTALL,
        )
    }


def _one_table_with(*fragments: str) -> tuple[str, str]:
    candidates = [
        (name, body)
        for name, body in _created_table_bodies().items()
        if all(fragment in body for fragment in fragments)
    ]
    assert len(candidates) == 1, (
        f"expected one Codex table containing {fragments}, got "
        f"{[name for name, _ in candidates]}"
    )
    return candidates[0]


def _assert_post_lock_clock(function: str, *lock_fragments: str) -> None:
    lock_indexes = [
        function.find(fragment)
        for fragment in lock_fragments
        if function.find(fragment) >= 0
    ]
    assert lock_indexes, f"no required lock found in {lock_fragments}"
    clock_index = function.rfind("clock_timestamp()")
    assert clock_index > max(lock_indexes), (
        "authoritative transition clock must be sampled after lock acquisition"
    )


def test_migration_is_one_additive_transaction() -> None:
    sql = _migration()
    meaningful = [
        line.strip().lower()
        for line in sql.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    ]
    assert meaningful[0] == "begin;"
    assert meaningful[-1] == "commit;"
    assert len(re.findall(r"(?mi)^begin;$", sql)) == 1
    assert len(re.findall(r"(?mi)^commit;$", sql)) == 1
    lowered = sql.lower()
    for destructive in (
        "drop table",
        "truncate table",
        "alter table agent_runtime.harmony_stage_receipts drop",
    ):
        assert destructive not in lowered


def test_ledger_has_typed_append_only_receipts_for_every_transition() -> None:
    tables = _created_table_bodies()
    assert len(tables) >= 10
    request_name, request = _one_table_with(
        "work_key",
        "assignment_key",
        "request_key",
        "lineage_receipt_id",
        "effective_expires_at",
    )
    run_name, run = _one_table_with(
        "work_key", "status_version", "claim_attempt", "lease_expires_at"
    )
    transition_name, transition = _one_table_with(
        "request_key", "transition_seq", "transition_kind", "to_state"
    )
    claim_name, claim = _one_table_with(
        "claim_receipt_id",
        "request_key",
        "claim_attempt",
        "claimed_at",
        "lease_expires_at",
        "payload jsonb",
    )
    attempt_name, attempt = _one_table_with(
        "attempt_receipt_id",
        "request_key",
        "claim_fence_sha256",
        "attempt_fence_sha256",
        "payload jsonb",
    )
    evidence_name, evidence = _one_table_with(
        "request_key", "reviewed_output_sha256", "evidence_sha256", "criteria"
    )
    result_name, result = _one_table_with(
        "request_key", "verdict", "evidence_sha256", "cost_observation"
    )
    verification_name, verification = _one_table_with(
        "request_key", "verification_outcome", "verification_receipt_id"
    )
    reconciliation_name, reconciliation = _one_table_with(
        "request_key", "reconciliation_action", "reconciliation_receipt_id"
    )
    stage_link_name, stage_link = _one_table_with(
        "request_key", "stage_receipt_id", "verification_receipt_id"
    )
    assert len({
        request_name,
        run_name,
        transition_name,
        claim_name,
        attempt_name,
        evidence_name,
        result_name,
        verification_name,
        reconciliation_name,
        stage_link_name,
    }) == 10

    assert "client_id text not null" in request
    assert "client_id = 'squid'" in request
    assert "automatic_publication" in request
    assert "provider_calls" in request
    assert "external_calls" in request
    assert "publication_calls" in request
    assert "on delete restrict" in _migration().lower()

    assert "status_version" in run
    assert "attempt_started_at" in run
    assert "result_submitted_at" in run
    assert "last_event_sha256" in run
    assert "claim_attempt >= 0 and claim_attempt <= 3" in run

    for state in (
        "pending",
        "claimed",
        "attempt_started",
        "result_submitted",
        "verified",
        "operator_review_pending",
        "needs_changes",
        "blocked",
        "outcome_unknown",
    ):
        assert state in transition
    assert "payload_sha256" in transition
    assert "previous_event_sha256" in transition
    assert "event_sha256" in transition
    assert "event_seq = transition_seq" in transition
    assert "claim_attempt between 1 and 3" in claim
    assert "interval '15 minutes'" in claim
    assert "execute_authorized boolean not null check (execute_authorized)" in attempt
    assert "unique (workspace_id, client_id, request_id)" in attempt
    assert "provider_calls boolean not null check (not provider_calls)" in evidence
    assert "evidence_sha256 = private.agent_json_sha256" in evidence
    assert "receipt_sha256" in result
    assert all(verdict in result for verdict in ("pass", "needs_changes", "blocked"))
    assert all(
        outcome in verification for outcome in ("passed", "needs_changes", "blocked")
    )
    assert "outcome_unknown" in reconciliation
    assert "result_not_current" in reconciliation
    assert "result_receipt_id uuid," in reconciliation
    assert "operator_decision_recorded" in stage_link

    sql = _migration().lower()
    for append_only in set(tables) - {run_name}:
        assert re.search(
            rf"create\s+trigger\s+[a-z0-9_]+\s+"
            rf"before\s+update\s+or\s+delete\s+on\s+"
            rf"{re.escape(append_only)}",
            sql,
        ), f"{append_only} lacks an append-only trigger"


def test_every_new_table_is_force_rls_without_direct_grants() -> None:
    sql = _migration().lower()
    tables = _created_table_bodies()
    assert tables
    for table in tables:
        assert re.search(
            rf"alter\s+table\s+{re.escape(table)}\s+"
            rf"enable\s+row\s+level\s+security\s*;",
            sql,
        )
        assert re.search(
            rf"alter\s+table\s+{re.escape(table)}\s+"
            rf"force\s+row\s+level\s+security\s*;",
            sql,
        )
    revoke = re.search(
        r"revoke\s+all\s+on\s+table\s+(?P<tables>.*?)\s+from\s+"
        r"(?P<roles>.*?)\s*;",
        sql,
        re.DOTALL,
    )
    assert revoke is not None
    for table in tables:
        assert table in revoke.group("tables")
    for role in ("public", "anon", "authenticated", "service_role"):
        assert role in revoke.group("roles")
    assert not re.search(
        r"create\s+policy\s+.*?\s+for\s+(?:insert|update|delete|all)\b",
        sql,
        re.DOTALL,
    )
    assert not re.search(
        r"grant\s+(?:select|insert|update|delete|all)[^;]*?\s+on\s+"
        r"(?:table\s+)?(?:agent_runtime|private)[.]",
        sql,
    )


def test_work_key_is_stable_and_excludes_execution_and_result_state() -> None:
    sql = _migration().lower()
    match = re.search(
        r"create\s+or\s+replace\s+function\s+private[.]"
        r"(?P<name>[a-z0-9_]*codex[a-z0-9_]*work[a-z0-9_]*key[a-z0-9_]*)"
        r"\s*[(].*?\n\$(?:[a-z_][a-z0-9_]*)?\$;",
        sql,
        re.DOTALL,
    )
    assert match is not None
    function = match.group(0)
    for stable in (
        "workspace_id",
        "client_id",
        "round_id",
        "plan_id",
        "plan_receipt_sha256",
        "private_content_receipt_sha256",
        "private_content_output_sha256",
        "official_content_version_id",
        "official_source_item_id",
        "official_source_binding_sha256",
        "content_snapshot_sha256",
        "signal_input_set_sha256",
        "signal_manifest_sha256",
        "signal_producer_principal_ids",
        "squid-codex-gate-work@1",
        "independent_qa",
    ):
        assert stable in function
    for unstable in (
        "'lineage_sha256'",
        "'lineage_receipt_id'",
        "'observed_at'",
        "'trust_snapshot_expires_at'",
        "'reviewer_principal_id'",
        "'reviewer_release_sha'",
        "'reviewer_config_sha256'",
        "'reviewer_specialist_binding_sha256'",
        "qa_output_sha256",
        "result_payload_sha256",
        "result_sha256",
        "verdict",
        "lease_expires_at",
        "worker_id",
        "claimed_at",
        "attempt_started_at",
    ):
        assert unstable not in function
    assert "private.agent_json_sha256" in function


def test_qa_binding_reuses_hardened_claim_contract_and_fails_closed() -> None:
    binding = _function("harmony_preview_codex_qa_binding", schema="private")
    for required in (
        "claim_scope_valid := coalesce((",
        "claim_policy_valid := coalesce((",
        "claim_identity_valid := coalesce(",
        "claim_time_valid := coalesce((",
        "if not (",
        "harmony_preview_stage_claims_match",
        "coineasy_harmony_qa",
        "harmony_independent_qa",
        "coalesce(claims ->> 'role', '')",
        "coalesce(claims ->> 'capability', '')",
        "coalesce(claims ->> 'workspace_id', '')",
        "coalesce(claims ->> 'client_id', '')",
        "coalesce(claims ->> 'environment', '')",
        "coalesce(claims ->> 'iss', '')",
        "coalesce(claims ->> 'aud', '')",
        "claims -> 'max_cost_microusd' is not distinct from '0'::jsonb",
        "claims -> 'max_external_actions' is not distinct from '0'::jsonb",
        "coalesce(claims ->> 'jti', '')",
        "expires_epoch - issued_epoch between 1 and 2678400",
        "date_trunc('second', specialist.created_at)",
    ):
        assert required in binding


def test_stale_reconciliation_actor_is_frozen_assignment_bound() -> None:
    recovery = _function(
        "harmony_preview_codex_reconciliation_actor", schema="private"
    )
    for required in (
        "security definer",
        "set search_path = ''",
        "claim_scope_valid := coalesce((",
        "claim_policy_valid := coalesce((",
        "claim_identity_valid := coalesce(",
        "claim_time_valid := coalesce((",
        "coineasy_harmony_qa",
        "harmony_independent_qa",
        "candidate.specialist_code = 'squid_independent_qa'",
        "candidate.actor = 'codex'",
        "candidate.binding_sha256",
        "request_row.reviewer_specialist_binding_sha256",
        "candidate.principal_id = request_row.reviewer_principal_id",
        "candidate.producer_release_sha = request_row.reviewer_release_sha",
        "candidate.config_sha256 = request_row.reviewer_config_sha256",
        "candidate.branch_ref = claims ->> 'ref'",
        "date_trunc('second', specialist.created_at)",
    ):
        assert required in recovery
    # Stale cleanup authenticates the immutable owner, not a currently active
    # specialist/fence.  The caller JWT itself must still be currently valid.
    assert "harmony_preview_stage_claims_match" not in recovery
    assert "harmony_preview_environment_fence" not in recovery
    assert "specialist.expires_at" not in recovery
    assert "to_timestamp(expires_epoch) > target_at" in recovery


def test_assignment_key_matches_the_offline_runner_contract() -> None:
    assignment = _function(
        "harmony_preview_codex_assignment_key", schema="private"
    )
    for required in (
        "'reviewer_binding_sha256'",
        "target_reviewer_binding ->> 'binding_sha256'",
        "'schema_version', 'squid-codex-gate-assignment@1'",
        "'work_key', target_work_key",
    ):
        assert required in assignment
    for drift in (
        "'reviewer_principal_id'",
        "'reviewer_release_sha'",
        "'reviewer_config_sha256'",
        "'reviewer_specialist_binding_sha256'",
    ):
        assert drift not in assignment


def test_all_public_rpcs_are_security_definer_and_broadly_revoked() -> None:
    sql = _migration().lower()
    for name in RPCS:
        function = _function(name)
        assert "security definer" in function
        assert "set search_path = ''" in function
        assert "statement_timestamp()" not in function
        assert re.search(
            rf"revoke\s+all\s+on\s+function\s+public[.]"
            rf"{name}\s*[(].*?[)]\s+from\s+public\s*,\s*anon\s*,\s*"
            rf"authenticated\s*,\s*service_role\s*;",
            sql,
            re.DOTALL,
        )
    assert not re.search(
        r"grant\s+execute\s+on\s+function\s+public[.][a-z0-9_]+"
        r"\s*[(][^;]*?[)]\s+to\s+"
        r"(?:public|anon|authenticated|service_role)\s*;",
        sql,
    )


def test_prepare_derives_current_squid_lineage_and_is_exactly_idempotent() -> None:
    prepare = _function("prepare_preview_harmony_squid_codex_qa")
    lineage = _function("harmony_preview_codex_build_source_lineage", schema="private")
    binding = _function("harmony_preview_codex_qa_binding", schema="private")
    trust = _function("harmony_preview_codex_trust_manifest", schema="private")
    parameters = _function_parameters(prepare)
    assert "jsonb" not in parameters
    for required in (
        "harmony_preview_lock_manifest_registrations",
        "harmony_preview_round_inputs_current",
        "harmony_preview_qa_actor_independent",
        "harmony_rounds",
        "harmony_stage_receipts",
        "private_content",
        "independent_qa",
        "squid",
        "pg_advisory_xact_lock",
        "for update",
        "existing",
        "reused",
        "work_key",
        "request_key",
    ):
        assert required in prepare
    authoritative_projection = prepare + lineage + binding + trust
    for dependency in (
        "needs_review",
        "harmony_preview_squid_specialist_bindings",
        "harmony_connector_attestation_receipts",
        "harmony_preview_connector_request_receipts",
        "harmony_preview_connector_registrations",
        "harmony_preview_environment_fence",
        "content_items",
        "content_versions",
    ):
        assert dependency in authoritative_projection
    assert "date_trunc('second', fence.created_at)" in lineage
    assert "date_trunc('second', private_binding.created_at)" in lineage
    _assert_post_lock_clock(
        prepare,
        "harmony_preview_lock_manifest_registrations",
        "pg_advisory_xact_lock",
        "for update",
    )
    assert "private.agent_json_sha256" in prepare
    assert "insert into private.harmony_preview_codex" in prepare
    assert "on conflict" not in prepare or "is distinct from" in prepare


def test_claim_is_bounded_skip_locked_and_returns_a_fenced_probe_contract() -> None:
    claim = _function("claim_preview_harmony_squid_codex_qa")
    assert re.search(r"for update(?: of [a-z0-9_]+)? skip locked", claim)
    assert "status = 'pending'" in claim or "status is not distinct from 'pending'" in claim
    assert "claim_attempt" in claim
    assert "< 3" in claim
    assert "claim_fence_sha256" in claim
    assert "request_key" in claim
    assert "lease_expires_at" in claim
    assert re.search(
        r"target_lease_seconds\s+(?:not\s+between\s+\d+\s+and\s+900|>\s+900)",
        claim,
    )
    for key in ("'claimed'", "'work_key'", "'claim_fence_sha256'"):
        assert key in claim
    candidate_lock = claim.index("for update of candidate skip locked")
    assert claim.index("queued_request.effective_expires_at") < candidate_lock
    assert claim.index("harmony_preview_codex_request_current") < candidate_lock
    _assert_post_lock_clock(claim, "for update of candidate skip locked")


def test_start_attempt_has_one_irreversible_execute_authorization() -> None:
    start = _function("start_preview_harmony_squid_codex_qa_attempt")
    for required in (
        "for update",
        "claim_fence_sha256",
        "request_key",
        "attempt_started",
        "attempt_fence_sha256",
        "execute_authorized boolean := false",
        "execute_authorized := true",
        "'execute_authorized', execute_authorized",
        "'reused'",
        "lease_expires_at",
        "harmony_preview_qa_outcome:",
        "pg_advisory_xact_lock",
        "harmony_preview_qa_denial_receipts",
        "harmony_preview_qa_output_already_denied",
    ):
        assert required in start
    dependency_lock = start.index("harmony_preview_codex_lock_plan_dependencies")
    outcome_lock = start.index("harmony_preview_qa_outcome:")
    denial_check = start.index("harmony_preview_qa_denial_receipts")
    authorization = start.index("execute_authorized := true")
    assert dependency_lock < outcome_lock < denial_check < authorization
    _assert_post_lock_clock(start, "for update")
    assert re.search(
        r"(?:transition_time|observed_at|started_at)\s*>=\s*"
        r"(?:run[.]|current_run[.])?lease_expires_at",
        start,
    )
    assert "insert into" in start
    assert "on conflict do update" not in start

    _, attempt = _one_table_with(
        "attempt_receipt_id",
        "request_key",
        "attempt_fence_sha256",
        "execute_authorized",
    )
    assert "unique (workspace_id, client_id, request_id)" in attempt


def test_result_is_typed_fenced_replay_safe_and_side_effect_free() -> None:
    result = _function("submit_preview_harmony_squid_codex_qa_result")
    for required in (
        "for update",
        "attempt_started",
        "attempt_fence_sha256",
        "work_key",
        "assignment_key",
        "request_key",
        "plan_id",
        "private_content_receipt_sha256",
        "private_content_output_sha256",
        "source_lineage_sha256",
        "official_content_version_id",
        "official_source_item_id",
        "official_source_binding_sha256",
        "content_snapshot_sha256",
        "signal_manifest_sha256",
        "signal_input_set_sha256",
        "producer_principal",
        "reviewer_principal_id",
        "reviewer_release_sha",
        "reviewer_config_sha256",
        "evidence_sha256",
        "verdict",
        "needs_changes",
        "blocked",
        "cost",
        "automatic_publication",
        "provider_calls",
        "external_calls",
        "publication_calls",
        "is distinct from",
        "reused",
    ):
        assert required in result
    assert "clock_timestamp()" in result
    _assert_post_lock_clock(result, "for update")
    assert "private.agent_json_sha256" in result
    assert "on conflict do update" not in result


def test_verify_rechecks_trust_and_only_pass_can_be_verified() -> None:
    verify = _function("verify_preview_harmony_squid_codex_qa_result")
    for required in (
        "for update",
        "harmony_preview_codex_lock_plan_dependencies",
        "harmony_preview_round_inputs_current",
        "harmony_preview_qa_actor_independent",
        "private.agent_json_sha256",
        "result_submitted",
        "pass",
        "verified",
        "needs_changes",
        "blocked",
    ):
        assert required in verify
    _assert_post_lock_clock(
        verify, "for update", "harmony_preview_codex_lock_plan_dependencies"
    )
    assert "harmony_preview_lock_manifest_registrations" not in verify
    assert re.search(
        r"(?:verdict|result_verdict).*?=\s*'pass'.*?'verified'",
        verify,
        re.DOTALL,
    )


def test_cross_rpc_dependency_lock_order_and_reconcile_preflight_are_canonical() -> None:
    dependency_lock = _function(
        "harmony_preview_codex_lock_plan_dependencies", schema="private"
    )
    prepare = _function("prepare_preview_harmony_squid_codex_qa")
    verify = _function("verify_preview_harmony_squid_codex_qa_result")
    reconcile = _function("reconcile_preview_harmony_squid_codex_qa_lease")
    preflight = _function(
        "harmony_preview_codex_qa_scope_preflight",
        schema="private",
    )
    tenant_lock = _function(
        "harmony_preview_codex_lock_tenant",
        schema="private",
    )

    assert dependency_lock.index("for share") < dependency_lock.index(
        "harmony_preview_lock_manifest_registrations"
    )
    assert prepare.index("for update") < prepare.index(
        "harmony_preview_lock_manifest_registrations"
    )
    assert "harmony_preview_lock_manifest_registrations" not in verify
    assert "harmony_preview_lock_manifest_registrations" not in reconcile
    first_tenant_source = {
        "prepare_preview_harmony_squid_codex_qa": "from agent_runtime.harmony_rounds",
        "claim_preview_harmony_squid_codex_qa": (
            "from private.harmony_preview_codex_gate_runs"
        ),
        "start_preview_harmony_squid_codex_qa_attempt": (
            "from private.harmony_preview_codex_gate_runs"
        ),
        "submit_preview_harmony_squid_codex_qa_result": (
            "from private.harmony_preview_codex_gate_runs"
        ),
        "verify_preview_harmony_squid_codex_qa_result": (
            "from private.harmony_preview_codex_gate_runs"
        ),
        "reconcile_preview_harmony_squid_codex_qa_lease": (
            "from private.harmony_preview_codex_gate_runs"
        ),
    }
    for rpc_name, tenant_source in first_tenant_source.items():
        function = _function(rpc_name)
        preflight_index = function.index(
            "harmony_preview_codex_qa_scope_preflight"
        )
        tenant_lock_index = function.index("harmony_preview_codex_lock_tenant")
        tenant_source_index = function.index(tenant_source)
        assert preflight_index < tenant_lock_index < tenant_source_index
    for required in (
        "claim_scope_valid",
        "claim_policy_valid",
        "claim_identity_valid",
        "claim_time_valid",
        "workspace_id",
        "max_external_actions",
    ):
        assert required in preflight
    assert not re.search(r"\bfrom\s+(?:agent_runtime|private|public)[.]", preflight)
    assert "pg_advisory_xact_lock" in tenant_lock
    assert "harmony_preview_codex_gate_tenant:" in tenant_lock


def test_reconcile_never_retries_and_recovers_stale_submitted_results() -> None:
    reconcile = _function("reconcile_preview_harmony_squid_codex_qa_lease")
    for required in (
        "lease_expires_at",
        "attempt_started_at",
        "outcome_unknown",
        "pending",
        "blocked",
        "request_not_current",
        "harmony_preview_codex_request_current",
        "claim_attempt",
        "< 3",
        "result_submitted",
        "result_not_current",
        "result_receipt_id",
        "harmony_preview_codex_qa_scope_preflight",
        "harmony_preview_codex_reconciliation_actor",
        "harmony_preview_qa_outcome:",
        "harmony_preview_qa_denial_receipts",
    ):
        assert required in reconcile
    assert re.search(r"for update(?: of [a-z0-9_]+)? skip locked", reconcile)
    assert reconcile.index(
        "harmony_preview_codex_qa_scope_preflight"
    ) < reconcile.index("select candidate.* into current_run")
    candidate_lock = reconcile.index("for update of candidate skip locked")
    for pre_lock_actor_binding in (
        "actor_claims := nullif(",
        "actor_principal_id :=",
        "actor_release_sha :=",
        "actor_config_sha256 :=",
        "actor_branch_ref :=",
        "actor_issued_epoch :=",
        "join private.harmony_preview_squid_specialist_bindings actor_specialist",
        "actor_specialist.binding_sha256",
        "queued_request.reviewer_specialist_binding_sha256",
        "actor_specialist.principal_id = queued_request.reviewer_principal_id",
        "actor_specialist.principal_id = actor_principal_id",
        "actor_specialist.producer_release_sha = actor_release_sha",
        "actor_specialist.config_sha256 = actor_config_sha256",
        "actor_specialist.branch_ref = actor_branch_ref",
        "date_trunc('second', actor_specialist.created_at)",
    ):
        assert reconcile.index(pre_lock_actor_binding) < candidate_lock
    assert reconcile.index(
        "harmony_preview_codex_reconciliation_actor("
    ) > candidate_lock
    assert "harmony_preview_lock_manifest_registrations" not in reconcile
    _assert_post_lock_clock(
        reconcile,
        "for update of candidate skip locked",
        "harmony_preview_codex_lock_plan_dependencies",
        "harmony_preview_qa_outcome:",
    )
    assert "harmony_preview_codex_qa_binding(" not in reconcile
    assert reconcile.index("harmony_preview_codex_lock_plan_dependencies") < (
        reconcile.index("harmony_preview_qa_outcome:")
    ) < reconcile.rfind("clock_timestamp()")
    post_attempt = re.search(
        r"if\s+[^;]*attempt_started_at\s+is\s+not\s+null\s+then"
        r"(?P<body>.*?)\b(?:elsif|else|end\s+if)\b",
        reconcile,
        re.DOTALL,
    )
    assert post_attempt is not None
    assert "outcome_unknown" in post_attempt.group("body")
    assert "pending" not in post_attempt.group("body")
    assert not re.search(
        r"attempt_started_at\s*=\s*null|attempt_fence_sha256\s*=\s*null",
        reconcile,
    )
    result_recovery = re.search(
        r"if\s+current_run[.]status\s*=\s*'result_submitted'\s+then"
        r"(?P<body>.*?)\belsif\b",
        reconcile,
        re.DOTALL,
    )
    assert result_recovery is not None
    assert "target_status := 'blocked'" in result_recovery.group("body")
    assert "target_action := 'result_not_current'" in result_recovery.group("body")
    assert "target_reason := 'request_not_current'" in result_recovery.group("body")
    for forbidden in (
        "insert into agent_runtime.harmony_stage_receipts",
        "insert into private.harmony_preview_codex_gate_verification_receipts",
        "update private.harmony_preview_codex_gate_result_receipts",
        "delete from private.harmony_preview_codex_gate_result_receipts",
    ):
        assert forbidden not in reconcile

    _, receipt = _one_table_with(
        "reconciliation_receipt_id",
        "reconciliation_action",
        "claim_receipt_id",
    )
    assert "'request_not_current'" in receipt
    assert "'result_not_current'" in receipt
    assert "claim_receipt_id uuid," in receipt
    assert "attempt_receipt_id uuid," in receipt
    assert "result_receipt_id uuid," in receipt
    assert "result_receipt_id is not null" in receipt
    assert "references private.harmony_preview_codex_gate_result_receipts" in receipt


def test_every_independent_qa_stage_insert_requires_verified_durable_result() -> None:
    sql = _migration().lower()
    trigger = re.search(
        r"create\s+trigger\s+(?P<trigger>[a-z0-9_]*codex[a-z0-9_]*)"
        r"\s+before\s+insert\s+on\s+"
        r"agent_runtime[.]harmony_stage_receipts.*?"
        r"execute\s+function\s+private[.]"
        r"(?P<function>[a-z0-9_]+)\s*[(][)]\s*;",
        sql,
        re.DOTALL,
    )
    assert trigger is not None
    guard = _function(trigger.group("function"), schema="private")
    for required in (
        "new.stage",
        "independent_qa",
        "verified",
        "pass",
        "workspace_id",
        "client_id",
        "round_id",
        "plan_id",
        "private_content",
        "reviewer_principal_id",
        "result",
    ):
        assert required in guard
    assert "security definer" in guard
    assert "set search_path = ''" in guard
    assert "for share" in guard or "for update" in guard


def test_migration_has_no_production_provider_buzz_approval_or_publication_path() -> None:
    sql = _migration().lower()
    for target in FORBIDDEN_DML_TARGETS:
        assert not re.search(
            rf"(?:insert\s+into|update|delete\s+from)\s+{re.escape(target)}\b",
            sql,
        )
    assert not re.search(
        r"(?:insert\s+into|update|delete\s+from)\s+agent_runtime[.]buzz_",
        sql,
    )
    for external_call in (
        "net.http_",
        "extensions.http_",
        "http_post(",
        "dblink(",
    ):
        assert external_call not in sql
    assert "'automatic_publication', true" not in sql
    assert "environment = 'production'" not in sql
    assert "harmony_preview_environment_fence" in sql
    for false_flag in (
        "'automatic_publication', false",
        "'provider_calls', false",
        "'external_calls', false",
        "'publication_calls', false",
    ):
        assert false_flag in sql


def test_rpc_responses_expose_unambiguous_64_way_probe_hooks() -> None:
    expected = {
        "prepare_preview_harmony_squid_codex_qa": (
            "'work_key'",
            "'request_key'",
            "'reused'",
        ),
        "claim_preview_harmony_squid_codex_qa": (
            "'claimed'",
            "'work_key'",
            "'claim_fence_sha256'",
        ),
        "start_preview_harmony_squid_codex_qa_attempt": (
            "'work_key'",
            "'attempt_fence_sha256'",
            "'execute_authorized'",
            "'reused'",
        ),
        "submit_preview_harmony_squid_codex_qa_result": (
            "'work_key'",
            "'result_sha256'",
            "'reused'",
        ),
        "verify_preview_harmony_squid_codex_qa_result": (
            "'work_key'",
            "'status'",
            "'reused'",
        ),
        "reconcile_preview_harmony_squid_codex_qa_lease": (
            "'reconciled'",
            "'outcome_unknown'",
            "'pending'",
            "'blocked'",
        ),
    }
    for name, keys in expected.items():
        function = _function(name)
        assert "returns jsonb" in function
        for key in keys:
            assert key in function, f"{name} lacks 64-way probe key {key}"
