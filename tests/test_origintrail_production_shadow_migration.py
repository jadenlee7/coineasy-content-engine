from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804130000_origintrail_7d_production_shadow.sql"
).read_text(encoding="utf-8")


def _function(name: str, *, schema: str = "public") -> str:
    match = re.search(
        rf"create or replace function {schema}\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing {schema}.{name}"
    return match.group(0)


def test_shadow_days_are_private_immutable_and_one_per_kst_day() -> None:
    assert MIGRATION.startswith("-- Seven-day OriginTrail Production Shadow")
    assert (
        "create table agent_runtime."
        "origintrail_batch_production_shadow_days"
    ) in MIGRATION
    assert "primary key (workspace_id, kst_date)" in MIGRATION
    assert "unique (workspace_id, job_id)" in MIGRATION
    assert "enable row level security" in MIGRATION
    assert "force row level security" in MIGRATION
    immutable = _function(
        "reject_origintrail_shadow_day_mutation",
        schema="agent_runtime",
    )
    assert "raise exception" in immutable
    assert "before update or delete" in MIGRATION


def test_candidate_is_origintrail_only_and_has_no_mutation() -> None:
    candidate = _function("peek_origintrail_batch_shadow_candidate")

    assert "security definer" in candidate.lower()
    assert "set search_path = ''" in candidate.lower()
    assert "interval '7 days'" in candidate
    assert "queued.client_id = 'origintrail'" in candidate
    assert "queued.agent_id = 'origintrail_client_agent'" in candidate
    assert "queued.status = 'queued'" in candidate
    assert "queued.reservation_state = 'held'" in candidate
    assert "queued.attempts = 0" in candidate
    assert "queued.budget_key = 'batch-general:' || pilot_day::text" in candidate
    assert "origintrail_batch_production_shadow_days" in candidate
    assert "insert into" not in candidate.lower()
    assert "update agent_runtime" not in candidate.lower()


def test_day_configuration_wraps_existing_exact_provider_fence() -> None:
    configure = _function("configure_origintrail_batch_shadow_day")

    assert "security definer" in configure.lower()
    assert "set search_path = ''" in configure.lower()
    assert "interval '7 days'" in configure
    assert "target_hard_limit_microusd is distinct from 50000" in configure
    assert "target_max_provider_batches is distinct from 1" in configure
    assert "target_expires_at > statement_timestamp() + interval '2 hours'" in configure
    assert "pg_catalog.pg_advisory_xact_lock" in configure
    assert "batch_job.client_id <> 'origintrail'" in configure
    assert "batch_job.budget_key" in configure
    assert "public.configure_origintrail_batch_canary_grant(" in configure
    assert configure.index(
        "public.configure_origintrail_batch_canary_grant("
    ) < configure.index(
        "insert into agent_runtime.origintrail_batch_production_shadow_days"
    )


def test_shadow_rpcs_are_service_role_only() -> None:
    for name in (
        "peek_origintrail_batch_shadow_candidate",
        "configure_origintrail_batch_shadow_day",
    ):
        function = _function(name).lower()
        assert "security definer" in function
        assert "set search_path = ''" in function
        assert re.search(
            rf"revoke all on function public\.{name}\(.*?\)\s*"
            rf"from public, anon, authenticated, service_role;",
            MIGRATION,
            re.DOTALL,
        )
        assert re.search(
            rf"grant execute on function public\.{name}\(.*?\)\s*"
            rf"to service_role;",
            MIGRATION,
            re.DOTALL,
        )
