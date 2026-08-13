from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase/migrations/20260813130000_origintrail_autonomous_ops_pilot.sql").read_text()
ROLE = (ROOT / "supabase/migrations/20260813131000_autonomous_ops_role.sql").read_text()


def test_autonomous_ledgers_are_force_rls_and_immutable():
    for table in (
        "agent_runtime.autonomous_ops_observations",
        "agent_runtime.autonomous_ops_tasks",
    ):
        assert f"alter table {table} enable row level security" in MIGRATION
        assert f"alter table {table} force row level security" in MIGRATION
        assert f"revoke all on table {table}" in MIGRATION
    assert "Autonomous Ops evidence is immutable" in MIGRATION


def test_observation_reads_only_bounded_operational_ledgers():
    assert "create or replace function public.observe_origintrail_autonomous_ops" in MIGRATION
    for source in (
        "agent_runtime.batch_jobs",
        "agent_runtime.batch_cost_overage_incidents",
        "agent_runtime.buzz_delivery_receipts",
        "agent_runtime.buzz_review_ack_receipts",
        "agent_runtime.buzz_operations_commands",
        "public.publications",
    ):
        assert source in MIGRATION
    observe = MIGRATION.split(
        "create or replace function public.observe_origintrail_autonomous_ops", 1
    )[1].split(
        "create or replace function public.record_origintrail_autonomous_ops_plan", 1
    )[0]
    assert "insert into" not in observe.lower()
    assert "update " not in observe.lower()
    assert "delete " not in observe.lower()


def test_record_can_only_create_propose_only_evidence():
    record = MIGRATION.split(
        "create or replace function public.record_origintrail_autonomous_ops_plan", 1
    )[1]
    assert "insert into agent_runtime.autonomous_ops_observations" in record
    assert "insert into agent_runtime.autonomous_ops_tasks" in record
    assert "target_execution_mode <> 'propose_only'" in record
    assert "target_automatic_publication is distinct from false" in record
    assert "target_external_writes is distinct from false" in record
    assert "when 'batch_failed' then" in record
    assert "(metrics ->> 'batch_failed_count')::bigint" in record
    assert "end) <= 0" in record
    for forbidden in (
        "insert into public.publications",
        "insert into public.approvals",
        "insert into agent_runtime.batch_jobs",
        "insert into agent_runtime.batch_members",
        "insert into agent_runtime.provider_create_intents",
        "openai",
        "messages send",
    ):
        assert forbidden not in record.lower()


def test_scoped_role_has_two_rpcs_and_zero_tables():
    assert "create role coineasy_autonomous_ops_worker" in ROLE
    assert "nologin noinherit nobypassrls" in ROLE
    assert "revoke all on table agent_runtime.autonomous_ops_observations" in ROLE
    assert "revoke all on table agent_runtime.autonomous_ops_tasks" in ROLE
    assert ROLE.count("grant execute on function public.") == 2
    assert "grant execute on function public.observe_origintrail_autonomous_ops" in ROLE
    assert "grant execute on function public.record_origintrail_autonomous_ops_plan" in ROLE


def test_task_schema_structurally_forbids_execution_and_publication():
    assert "status text not null default 'proposed' check (status = 'proposed')" in MIGRATION
    assert "execution_mode text not null check (execution_mode = 'propose_only')" in MIGRATION
    assert "automatic_execution boolean not null default false check (not automatic_execution)" in MIGRATION
    assert "automatic_publication boolean not null default false check (not automatic_publication)" in MIGRATION
    assert "external_writes boolean not null default false check (not external_writes)" in MIGRATION
