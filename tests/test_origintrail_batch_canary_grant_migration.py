from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260802120000_origintrail_batch_canary_grant.sql"
).read_text(encoding="utf-8")


def _function(name: str, *, schema: str = "public") -> str:
    match = re.search(
        rf"create or replace function {schema}\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing {schema}.{name}"
    return match.group(0)


def test_forward_migration_creates_a_private_one_shot_grant() -> None:
    assert MIGRATION.startswith("-- Durable, exact one-shot authorization")
    assert "create table agent_runtime.origintrail_batch_canary_grants" in MIGRATION
    assert "primary key (workspace_id, config_subject_sha256)" in MIGRATION
    for unique_binding in (
        "unique (workspace_id, config_approval_id)",
        "unique (workspace_id, dispatch_subject_sha256)",
        "unique (workspace_id, dispatch_approval_id)",
        "unique (workspace_id, job_id)",
    ):
        assert unique_binding in MIGRATION
    assert "hard_limit_microusd between 1 and 50000" in MIGRATION
    assert "max_provider_batches = 1" in MIGRATION
    assert "provider_batches_consumed between 0 and max_provider_batches" in MIGRATION
    assert "enable row level security" in MIGRATION
    assert "force row level security" in MIGRATION
    assert re.search(
        r"revoke all on table agent_runtime\.origintrail_batch_canary_grants\s*"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
    )


def test_grant_binding_and_consumption_are_irreversible() -> None:
    immutable = _function(
        "enforce_origintrail_canary_grant_immutable",
        schema="agent_runtime",
    )

    for field in (
        "config_subject_sha256",
        "config_approval_id",
        "dispatch_subject_sha256",
        "dispatch_approval_id",
        "job_id",
        "input_sha256",
        "request_sha256",
        "expires_at",
        "hard_limit_microusd",
        "max_provider_batches",
        "created_at",
    ):
        assert f"new.{field} is distinct from old.{field}" in immutable
    assert (
        "new.provider_batches_consumed < old.provider_batches_consumed"
        in immutable
    )
    assert "consumption is irreversible" in immutable
    assert "before update on agent_runtime.origintrail_batch_canary_grants" in MIGRATION


def test_registration_is_exact_immutable_and_idempotent() -> None:
    configure = _function("configure_origintrail_batch_canary_grant")
    lowered = configure.lower()

    assert "security definer" in lowered
    assert "set search_path = ''" in lowered
    assert "pg_catalog.pg_advisory_xact_lock" in configure
    assert "for update" in configure
    for binding in (
        "config_approval_id",
        "dispatch_subject_sha256",
        "dispatch_approval_id",
        "job_id",
        "input_sha256",
        "request_sha256",
        "expires_at",
        "hard_limit_microusd",
        "max_provider_batches",
    ):
        assert (
            f"canary_grant.{binding} is distinct from target_{binding}"
            in configure
        )
    assert "target_expires_at > statement_timestamp() + interval '2 hours'" in configure
    assert "batch_job.client_id <> 'origintrail'" in configure
    assert "batch_job.attempts <> 0" in configure
    assert "batch_job.status <> 'queued'" in configure
    assert (
        "batch_job.input_payload ->> 'request_sha256'\n"
        "            is distinct from target_request_sha256"
    ) in configure
    assert "'reused', true" in configure
    assert "'reused', false" in configure
    assert (
        "canary_grant.dispatch_subject_sha256 is distinct from "
        "target_dispatch_subject_sha256"
    ) in configure
    assert "grant binding is immutable" in configure


def test_exact_claim_validates_grant_before_locking_or_mutating_job() -> None:
    claim = _function("claim_origintrail_batch_canary_job")

    validate_at = claim.index("-- Validate the complete durable authorization")
    job_lock_at = claim.index("from agent_runtime.batch_jobs as exact_job")
    consume_at = claim.index(
        "update agent_runtime.origintrail_batch_canary_grants"
    )
    job_update_at = claim.index("update agent_runtime.batch_jobs")
    assert validate_at < job_lock_at < consume_at < job_update_at
    assert "canary_grant.job_id is distinct from target_job_id" in claim
    assert "canary_grant.request_sha256 is distinct from target_request_sha256" in claim
    assert "batch_job.input_sha256 is distinct from target_input_sha256" in claim
    assert (
        "batch_job.input_payload ->> 'request_sha256'\n"
        "            is distinct from target_request_sha256"
    ) in claim
    assert "batch_job.max_cost_microusd > target_hard_limit_microusd" in claim
    assert "batch_job.attempts <> 0" in claim
    assert "set provider_batches_consumed = 1" in claim
    assert "set status = 'claimed',\n            attempts = 1" in claim
    assert "'provider_create_allowed', provider_create_allowed" in claim


def test_consumed_grant_only_recovers_the_same_stale_attempt_one() -> None:
    claim = _function("claim_origintrail_batch_canary_job")
    recovery = claim.split(
        "elsif canary_grant.provider_batches_consumed = 1 then",
        1,
    )[1].split("else", 1)[0]

    assert "batch_job.status <> 'claimed'" in recovery
    assert "batch_job.attempts <> 1" in recovery
    assert "batch_job.lease_expires_at > statement_timestamp()" in recovery
    assert "batch_job.claimed_at is distinct from canary_grant.consumed_at" in recovery
    assert "attempts =" not in recovery
    assert "provider_create_allowed := true" not in recovery
    assert "recovery_required := true" in recovery
    assert "provider_create_allowed boolean := false" in claim
    assert claim.count("provider_create_allowed := true") == 1


def test_generic_claim_cannot_take_any_origintrail_job() -> None:
    generic = _function("claim_agent_batch_jobs")

    assert "and job.client_id <> 'origintrail'" in generic
    assert "whole client also closes the queue-to-grant registration race" in generic
    assert "not exists (" in generic
    assert "agent_runtime.origintrail_batch_canary_grants" in generic
    assert "job.client_id <> 'origintrail'\n              or" not in generic
    assert "job.client_id = any(target_client_ids)" in generic
    assert "job.client_id <> 'yellow'" not in generic


def test_canary_rpcs_are_service_role_only() -> None:
    signatures = {
        "configure_origintrail_batch_canary_grant": (
            "uuid, text, uuid, text, uuid, uuid, text, text, timestamptz, "
            "bigint, integer"
        ),
        "claim_origintrail_batch_canary_job": (
            "uuid, text, text, uuid, text, uuid, uuid, text, text, timestamptz, "
            "bigint, integer, integer"
        ),
    }
    for name, signature in signatures.items():
        function = _function(name).lower()
        assert "security definer" in function
        assert "set search_path = ''" in function
        escaped = re.escape(signature).replace(r"\ ", r"\s*")
        assert re.search(
            rf"revoke all on function public\.{name}\(\s*{escaped}\s*\)\s*"
            rf"from public, anon, authenticated, service_role;",
            MIGRATION,
            re.DOTALL,
        )
        assert re.search(
            rf"grant execute on function public\.{name}\(\s*{escaped}\s*\)\s*"
            rf"to service_role;",
            MIGRATION,
            re.DOTALL,
        )
