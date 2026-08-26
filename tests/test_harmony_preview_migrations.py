from __future__ import annotations

import re
from pathlib import Path

from core.agent_control.harmony import HARMONY_TOPIC_CODES


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = tuple(
    ROOT / "supabase" / "migrations" / name
    for name in (
        "20260825132000_harmony_preview_collaboration.sql",
        "20260825133000_harmony_preview_vertical_slice.sql",
        "20260825134000_harmony_preview_stage_chain.sql",
        "20260825135000_harmony_preview_dashboard_roles.sql",
        "20260825140000_harmony_preview_fixed_specialist_chain.sql",
        "20260826210000_harmony_preview_trust_hardening.sql",
    )
)
SECURITY = (
    ROOT / "supabase" / "tests" / "harmony_preview_collaboration_security.sql"
)
PROBE = ROOT / "scripts" / "probe_harmony_preview_concurrency.py"
TRUST_MIGRATION = MIGRATIONS[5]


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _array_after(sql: str, marker: str) -> tuple[str, ...]:
    start = sql.index(marker) + len(marker)
    end = sql.index("]::text[]", start)
    return tuple(re.findall(r"'([a-z][a-z0-9_:-]+)'", sql[start:end]))


def test_preview_migrations_are_single_transaction_files() -> None:
    for migration in MIGRATIONS:
        sql = _sql(migration)
        assert len(re.findall(r"^begin;$", sql, flags=re.MULTILINE)) == 1
        assert len(re.findall(r"^commit;$", sql, flags=re.MULTILINE)) == 1
        assert sql.index("begin;") < sql.rindex("commit;")


def test_sql_topic_taxonomy_exactly_matches_python_and_excludes_actions() -> None:
    signal_sql = _sql(MIGRATIONS[0])
    plan_sql = _sql(MIGRATIONS[1])
    expected = tuple(HARMONY_TOPIC_CODES)
    assert _array_after(
        signal_sql, "or not (item.value = any(array["
    ) == expected
    assert _array_after(
        plan_sql, "or not (target_topic_code = any(array["
    ) == expected
    for unsafe in ("transfer_funds", "delete_account", "unknown_topic"):
        assert unsafe not in expected


def test_stage_receipts_are_unique_per_verified_jwt_binding() -> None:
    sql = _sql(MIGRATIONS[0])
    assert "unique (workspace_id, client_id, binding_receipt_sha256)" in sql
    assert "input_sha256 text not null" in sql
    assert "output_sha256 text not null" in sql
    assert "qa_receipt_id uuid not null" in sql


def test_plan_replay_uses_stable_specialist_operation_not_transport_ids() -> None:
    sql = _sql(MIGRATIONS[4])
    request_hash_start = sql.index(
        "request_sha := private.agent_json_sha256"
    )
    replay_start = sql.index("if found then", request_hash_start)
    replay_end = sql.index("created_time :=", replay_start)
    request_hash = sql[request_hash_start:replay_start]
    replay = sql[replay_start:replay_end]
    for field in (
        "workspace_id",
        "client_id",
        "round_id",
        "plan_id",
        "input_set_sha256",
        "specialist_binding_sha256",
        "stage",
        "topic_code",
    ):
        assert f"'{field}'" in request_hash
    for transport_field in ("stage_receipt_id", "jti", "iat", "exp"):
        assert f"'{transport_field}'" not in request_hash
    for binding in (
        "existing.round_id <> target_round_id",
        "existing.plan_id <> target_plan_id",
        "existing_stage.principal_id",
        "existing_stage.producer_release_sha",
        "existing_stage.config_sha256",
        "existing_stage.capability",
        "existing_stage.specialist_binding_sha256",
    ):
        assert binding in replay
    assert "existing_stage.receipt_id <> target_stage_receipt_id" not in replay
    assert "binding_receipt_sha256" not in replay
    assert "harmony_preview_plan_idempotency_conflict" in replay


def test_projection_fails_closed_when_round_inputs_are_not_current() -> None:
    sql = _sql(MIGRATIONS[4])
    collaboration_start = sql.index(
        "create or replace function private.harmony_preview_collaboration_object"
    )
    current_gate = sql.index(
        "if not private.harmony_preview_round_inputs_current(",
        collaboration_start,
    )
    receipt_projection = sql.index(
        "from agent_runtime.harmony_signals signal", current_gate
    )
    assert collaboration_start < current_gate < receipt_projection
    assert "with current_rounds as materialized" in sql
    assert "join current_rounds round_value" in sql
    assert "'pending_operator_inbox'" in sql
    assert "'client_scope_verified', true" in sql
    assert "'portable_trust', false" in sql


def test_append_rpc_contract_is_eight_arguments_everywhere() -> None:
    signature = (
        "append_preview_harmony_squid_stage("
        "uuid,text,uuid,uuid,text,uuid,uuid,jsonb)"
    )
    compact_stage = re.sub(r"\s+", "", _sql(MIGRATIONS[2]))
    compact_roles = re.sub(r"\s+", "", _sql(MIGRATIONS[3]))
    compact_fixed = re.sub(r"\s+", "", _sql(MIGRATIONS[4]))
    compact_smoke = re.sub(r"\s+", "", _sql(SECURITY))
    assert signature in compact_stage
    assert signature in compact_roles
    assert signature in compact_fixed
    assert signature in compact_smoke
    assert "uuid,text,uuid,uuid,text,uuid,uuid)" not in compact_roles


def test_typed_qa_and_complete_chain_gate_operator_inbox() -> None:
    sql = _sql(MIGRATIONS[4])
    assert "harmony-independent-qa-evidence@1" in sql
    assert "harmony-deterministic-qa@1" in sql
    assert "reviewed_output_sha256" in sql
    assert "harmony_preview_qa_self_review_forbidden" in sql
    assert "candidate.stage in ('plan', 'private_content')" in sql
    assert "previous_row.reviewer_principal_id <> previous_row.principal_id" in sql
    operator_branch = sql.index("if target_stage = 'operator_inbox' then")
    inbox_insert = sql.index(
        "insert into agent_runtime.harmony_operator_inbox", operator_branch
    )
    assert operator_branch < inbox_insert
    assert sql.count("insert into agent_runtime.harmony_operator_inbox") == 1
    assert "'inbox_id', target_inbox_id::text" in sql


def test_fixed_specialist_roster_is_expiring_empty_force_rls_and_immutable() -> None:
    sql = _sql(MIGRATIONS[4])
    digest_start = sql.index(
        "create or replace function private.harmony_preview_specialist_binding_sha"
    )
    digest_end = sql.index("create table", digest_start)
    digest = sql[digest_start:digest_end]
    for binding_field in (
        "target_branch_ref",
        "target_workspace_id",
        "target_client_id",
        "target_stage",
        "target_specialist_code",
        "target_role_name",
        "target_capability",
        "target_actor",
        "target_principal_id",
        "target_release_sha",
        "target_config_sha256",
        "target_expires_at",
    ):
        assert binding_field in digest
    assert "target_expires_at at time zone 'UTC'" in digest
    assert "harmony_preview_fixed_specialist_requires_empty_ledger" in sql
    assert "harmony_preview_environment_fence_two_hour_check" in sql
    assert sql.count("expires_at - created_at <= interval '2 hours'") == 2
    assert "force row level security" in sql.lower()
    assert "harmony_preview_squid_specialist_bindings_immutable" in sql
    assert "revoke all on table private.harmony_preview_squid_specialist_bindings" in sql
    for stage, specialist, actor in (
        ("plan", "squid_planner", "grok_bot"),
        ("private_content", "squid_private_content_producer", "content_engine"),
        ("independent_qa", "squid_independent_qa", "codex"),
        ("operator_inbox", "coineasy_representative_inbox", "human_operator_inbox"),
        ("recap", "squid_recap", "coineasy_recap"),
    ):
        assert f"('{stage}', '{specialist}'" in sql
        assert f"'{actor}'" in sql


def test_stage_replay_is_stable_across_receipt_uuid_and_refreshed_jwt() -> None:
    sql = _sql(MIGRATIONS[4])
    append_start = sql.index(
        "create or replace function public.append_preview_harmony_squid_stage"
    )
    replay_start = sql.index("select * into existing", append_start)
    replay_end = sql.index("select * into strict previous_row", replay_start)
    replay = sql[replay_start:replay_end]
    assert "existing.receipt_id <> target_receipt_id" not in replay
    assert "existing.binding_receipt_sha256" not in replay
    assert "existing.specialist_binding_sha256" in replay
    assert "private.agent_json_sha256(target_qa_evidence)" in replay
    assert "existing.artifact ->> 'inbox_id'" in replay
    operation_start = sql.index(
        "create or replace function private.harmony_preview_stage_operation_key"
    )
    operation_end = sql.index("create or replace function", operation_start + 1)
    operation = sql[operation_start:operation_end]
    for field in (
        "specialist_binding_sha256", "workspace_id", "client_id",
        "plan_id", "stage", "input_sha256", "output_sha256",
    ):
        assert f"'{field}'" in operation


def test_dashboard_v2_projects_full_chain_and_nullable_recap_binding() -> None:
    sql = _sql(MIGRATIONS[4])
    start = sql.index(
        "create or replace function public.get_preview_harmony_dashboard"
    )
    dashboard = sql[start:]
    for schema in (
        "harmony-preview-dashboard@2",
        "harmony-dashboard-round@2",
        "harmony-dashboard-inbox@2",
        "harmony-dashboard-recap@1",
    ):
        assert schema in dashboard
    for key in (
        "'actor'", "'capability'", "'specialist_code'",
        "'specialist_binding_sha256'", "'operation_key_sha256'",
        "'principal_id'", "'producer_release_sha'", "'config_sha256'",
        "'receipt_sha256'",
        "'input_sha256'", "'output_sha256'", "'round_sha256'",
        "'recap_receipt_sha256'", "'recap_output_sha256'",
        "'operator_decision_recorded'", "'operator_decision_observed'",
        "'actual_cost_microusd'", "'publication_count'",
        "'stage_receipt_count'", "'synthetic'",
    ):
        assert key in dashboard
    assert "left join agent_runtime.harmony_stage_receipts recap_stage" in dashboard
    assert "join private.harmony_preview_squid_specialist_bindings specialist" in dashboard
    assert "pg_catalog.left(\n                content_stage.artifact ->> 'headline_ko', 160" in dashboard
    assert "pg_catalog.left(\n                content_stage.artifact ->> 'summary_ko', 600" in dashboard


def test_official_binding_hash_uses_immutable_identity_not_dispatch_state() -> None:
    sql = _sql(MIGRATIONS[0])
    start = sql.index(
        "select private.agent_json_sha256(pg_catalog.jsonb_build_object(",
        sql.index("harmony_preview_squid_official_source_binding"),
    )
    end = sql.index("from private.grok_qa_dispatch_outbox dispatch", start)
    digest = sql[start:end]
    for mutable in (
        "dispatch.status",
        "dispatch.attempts",
        "dispatch.provider_attempt_started_at",
        "dispatch.provider_verdict",
        "dispatch.banner_sha256",
    ):
        assert mutable not in digest
    assert "and dispatch.status <> 'obsolete'" in sql[end:]


def test_preview_slice_has_no_external_or_publication_write_path() -> None:
    sql = "\n".join(_sql(path).lower() for path in MIGRATIONS)
    for forbidden in (
        "insert into public.publications",
        "update public.publications",
        "insert into public.approvals",
        "insert into agent_runtime.buzz",
        "update agent_runtime.buzz",
        "insert into private.grok_qa_dispatch_outbox",
        "update private.grok_qa_dispatch_outbox",
    ):
        assert forbidden not in sql


def test_trust_hardening_is_empty_append_only_force_rls() -> None:
    sql = _sql(TRUST_MIGRATION).lower()
    assert "harmony_preview_trust_hardening_requires_empty_ledger" in sql
    for table in (
        "private.harmony_preview_connector_registrations",
        "private.harmony_preview_connector_registration_revocations",
        "private.harmony_preview_connector_request_receipts",
        "private.harmony_preview_qa_denial_receipts",
    ):
        assert f"create table {table}" in sql
        assert f"alter table {table}\n    enable row level security" in sql
        assert f"alter table {table}\n    force row level security" in sql
    for trigger in (
        "harmony_preview_connector_registrations_immutable",
        "harmony_preview_connector_revocations_immutable",
        "harmony_preview_connector_requests_immutable",
        "harmony_preview_qa_denials_immutable",
    ):
        assert f"create trigger {trigger}" in sql
    assert "from public, anon, authenticated, service_role" in sql


def test_connector_registrations_are_one_shot_unique_and_iat_bound() -> None:
    sql = _sql(TRUST_MIGRATION).lower()
    assert "pg_catalog.date_trunc('second', statement_timestamp())" in sql
    assert "check (created_at = pg_catalog.date_trunc('second', created_at))" in sql
    for constraint in (
        "harmony_connector_registration_lane_once",
        "harmony_connector_registration_connector_once",
        "harmony_connector_registration_principal_once",
        "harmony_connector_registration_key_once",
    ):
        assert f"constraint {constraint} unique" in sql
    claims_start = sql.index(
        "create or replace function private.harmony_preview_connector_claims_match"
    )
    claims_end = sql.index("create or replace function", claims_start + 1)
    claims = sql[claims_start:claims_end]
    assert "return coalesce((" in claims
    assert "pg_catalog.coalesce" not in claims
    assert "issued_epoch := (claims ->> 'iat')::bigint" in claims
    assert "pg_catalog.to_timestamp(issued_epoch)" in claims
    assert "pg_catalog.date_trunc(\n                        'second', registration.created_at" in claims
    assert "issued_epoch < 0" in claims
    assert "issued_epoch > 4102444800" in claims
    assert "expires_epoch > 4102444800" in claims


def test_shared_preview_scope_rejects_extreme_epochs_without_sqlstate_leak() -> None:
    sql = _sql(TRUST_MIGRATION).lower()
    start = sql.index(
        "create or replace function private.harmony_preview_scope_matches"
    )
    end = sql.index("create or replace function", start + 1)
    scope = sql[start:end]
    assert "issued_epoch is null" in scope
    assert "expires_epoch is null" in scope
    assert "return coalesce((" in scope
    assert "pg_catalog.coalesce" not in scope
    assert "issued_epoch < 0" in scope
    assert "expires_epoch > 4102444800" in scope
    assert scope.count("exception when others then") >= 2


def test_fixed_specialist_claims_are_fail_closed_for_missing_subject() -> None:
    sql = _sql(TRUST_MIGRATION).lower()
    start = sql.index(
        "create or replace function private.harmony_preview_stage_claims_match"
    )
    end = sql.index("do $fresh_preview$", start)
    claims = sql[start:end]
    assert "return coalesce((" in claims
    assert "pg_catalog.coalesce" not in claims
    assert "coalesce(claims ->> 'sub', '')" in claims
    assert "= claims ->> 'producer_principal_id'" in claims
    assert claims.count("exception when others then") >= 2


def test_connector_request_receipt_enforces_durable_chronology() -> None:
    sql = _sql(TRUST_MIGRATION).lower()
    assert "create trigger harmony_preview_connector_request_chronology" in sql
    assert "registration_created_at > connector_verified_at" in sql
    assert "connector_verified_at > new.accepted_at" in sql
    assert "harmony_preview_connector_request_chronology_invalid" in sql
    assert (
        "revoke all on function private.harmony_preview_validate_request_chronology()"
        in sql
    )


def test_connector_request_digest_binds_rpc_registration_and_payload() -> None:
    sql = _sql(TRUST_MIGRATION)
    start = sql.index(
        "create or replace function private.harmony_preview_connector_request_sha256"
    )
    end = sql.index("create or replace function", start + 1)
    digest = sql[start:end]
    for field in (
        "'domain', 'coineasy:harmony:preview:connector-request:v1'",
        "'rpc', 'public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)'",
        "'workspace_id'",
        "'client_id'",
        "'registration_id'",
        "'connector_receipt_id'",
        "'signal_id'",
        "'source_event_id'",
        "'producer_principal_id'",
        "'signal_kind'",
        "'lane'",
        "'signal_payload_sha256'",
        "target_signal - 'payload_sha256'",
    ):
        assert field in digest
    assert "claims ->> 'request_nonce' is distinct from claims ->> 'jti'" in sql
    assert "harmony_preview_connector_request_idempotency_conflict" in sql
    assert "harmony_preview_connector_request_replay_conflict" in sql


def test_revocation_and_request_admission_share_one_registration_lock() -> None:
    sql = _sql(TRUST_MIGRATION)
    lock_start = sql.index(
        "create or replace function private.harmony_preview_lock_connector_registration"
    )
    lock_end = sql.index("create or replace function", lock_start + 1)
    assert "for update" in sql[lock_start:lock_end]
    assert sql.count("private.harmony_preview_lock_connector_registration(") >= 3
    assert "harmony_preview_connector_registration_revoked" in sql
    assert "harmony_preview_connector_registration_not_current" in sql


def test_signal_wrapper_keeps_public_signature_and_adds_request_receipt() -> None:
    source = _sql(TRUST_MIGRATION)
    sql = re.sub(r"\s+", "", source)
    signature = "public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)"
    assert f"alterfunction{signature}setschemaprivate" in sql
    assert "create or replace function public.submit_preview_harmony_signal(" in source
    assert f"grantexecuteonfunction{signature}" in sql
    assert "'connector_request_receipt',request_payload" in sql
    assert "harmony-connector-request-receipt@1" in sql


def test_valid_failed_qa_is_separate_and_cannot_open_inbox() -> None:
    sql = _sql(TRUST_MIGRATION)
    signature = (
        "record_preview_harmony_squid_qa_denial("
        "uuid,text,uuid,uuid,uuid,jsonb)"
    )
    compact = re.sub(r"\s+", "", sql)
    assert f"public.{signature}" in compact
    assert "harmony-qa-denial-receipt@1" in sql
    assert "harmony_preview_qa_denial_idempotency_conflict" in sql
    denial_start = compact.index(
        "createorreplacefunctionpublic."
        "record_preview_harmony_squid_qa_denial("
    )
    denial = compact[denial_start:]
    assert "'ok',false" in denial
    assert "'denied',true" in denial
    assert "'external_calls',false" in denial
    assert "'provider_calls',false" in denial
    assert "'publication_calls',false" in denial
    assert "'automatic_publication',false" in denial
    assert "insertintoagent_runtime.harmony_stage_receipts" not in denial
    assert "insertintoagent_runtime.harmony_operator_inbox" not in denial


def test_denied_or_revoked_work_is_not_current_or_passable() -> None:
    sql = _sql(TRUST_MIGRATION)
    assert "create or replace function private.harmony_preview_round_inputs_current" in sql
    assert "harmony_preview_connector_request_receipts" in sql
    assert "harmony_preview_connector_registration_revocations" in sql
    assert "harmony_stage_receipts_guard_positive_qa" in sql
    assert "harmony_preview_qa_output_already_denied" in sql


def test_concurrency_probe_uses_64_connections_and_closed_topic() -> None:
    probe = _sql(PROBE)
    assert "CONCURRENCY = 64" in probe
    assert "ThreadPoolExecutor(max_workers=CONCURRENCY)" in probe
    assert 'topic = "official_update"' in probe
    assert "side_effect_baseline_unchanged" in probe
    assert "--release-sha" in probe
    assert "--config-sha256" in probe
    assert "psql_timeout_commit_state_unknown_no_retry" in probe
    assert "insufficient_direct_connection_capacity_for_64_way_probe" in probe
    assert "approved 120-minute TTL" in probe
