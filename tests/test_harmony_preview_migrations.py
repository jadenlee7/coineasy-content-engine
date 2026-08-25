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
    )
)
SECURITY = (
    ROOT / "supabase" / "tests" / "harmony_preview_collaboration_security.sql"
)
PROBE = ROOT / "scripts" / "probe_harmony_preview_concurrency.py"


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


def test_plan_replay_requires_exact_ids_and_jwt_binding() -> None:
    sql = _sql(MIGRATIONS[1])
    request_hash_start = sql.index(
        "request_sha := private.agent_json_sha256"
    )
    replay_start = sql.index("if found then", request_hash_start)
    replay_end = sql.index("created_time :=", replay_start)
    request_hash = sql[request_hash_start:replay_start]
    replay = sql[replay_start:replay_end]
    for field in (
        "round_id",
        "plan_id",
        "stage_receipt_id",
        "principal_id",
        "release_sha",
        "config_sha256",
    ):
        assert f"'{field}'" in request_hash
    for binding in (
        "existing.round_id <> target_round_id",
        "existing.plan_id <> target_plan_id",
        "existing_stage.receipt_id <> target_stage_receipt_id",
        "existing_stage.principal_id",
        "existing_stage.producer_release_sha",
        "existing_stage.config_sha256",
        "existing_stage.capability",
    ):
        assert binding in replay
    assert "harmony_preview_plan_idempotency_conflict" in replay


def test_projection_fails_closed_when_round_inputs_are_not_current() -> None:
    sql = _sql(MIGRATIONS[3])
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
    compact_smoke = re.sub(r"\s+", "", _sql(SECURITY))
    assert signature in compact_stage
    assert signature in compact_roles
    assert signature in compact_smoke
    assert "uuid,text,uuid,uuid,text,uuid,uuid)" not in compact_roles


def test_typed_qa_and_complete_chain_gate_operator_inbox() -> None:
    sql = _sql(MIGRATIONS[2])
    assert "harmony-independent-qa-evidence@1" in sql
    assert "harmony-deterministic-qa@1" in sql
    assert "reviewed_output_sha256" in sql
    assert "harmony_preview_qa_self_review_forbidden" in sql
    assert "candidate.stage in ('plan', 'private_content')" in sql
    assert "previous_row.reviewer_principal_id <> previous_row.principal_id" in sql
    recap_branch = sql.index("if target_stage = 'recap' then")
    inbox_insert = sql.index(
        "insert into agent_runtime.harmony_operator_inbox", recap_branch
    )
    assert recap_branch < inbox_insert


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
