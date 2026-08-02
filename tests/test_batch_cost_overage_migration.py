from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260802130000_batch_cost_overage_incidents.sql"
).read_text(encoding="utf-8")
CANARY_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260802120000_origintrail_batch_canary_grant.sql"
).read_text(encoding="utf-8")


def _function(name: str, *, schema: str, source: str = MIGRATION) -> str:
    match = re.search(
        rf"create or replace function {schema}\.{name}\(.*?\n\$\$;",
        source,
        re.DOTALL,
    )
    assert match is not None, f"missing {schema}.{name}"
    return match.group(0)


def test_forward_migration_creates_private_immutable_incident_evidence() -> None:
    assert MIGRATION.startswith("-- Forward-only settlement hardening")
    assert "create table agent_runtime.batch_cost_overage_incidents" in MIGRATION
    assert "primary key (workspace_id, job_id)" in MIGRATION
    assert "unique (workspace_id, outcome_fingerprint)" in MIGRATION
    assert (
        "foreign key (workspace_id, provider_batch_id, job_id)\n"
        "        references agent_runtime.batch_members("
        "workspace_id, batch_id, job_id)"
    ) in MIGRATION
    assert "actual_cost_microusd > reservation_cap_microusd" in MIGRATION
    assert (
        "actual_cost_microusd\n"
        "            = reservation_cap_microusd + overage_microusd"
    ) in MIGRATION
    assert "budget_spent_microusd = reservation_cap_microusd" in MIGRATION
    assert "resolution_status = 'unresolved'" in MIGRATION
    assert "enable row level security" in MIGRATION
    assert "force row level security" in MIGRATION
    assert re.search(
        r"revoke all on table "
        r"agent_runtime\.batch_cost_overage_incidents\s*"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
    )

    immutable = _function(
        "reject_batch_cost_overage_mutation",
        schema="agent_runtime",
    )
    assert "evidence is immutable" in immutable
    assert (
        "before update or delete on "
        "agent_runtime.batch_cost_overage_incidents"
    ) in MIGRATION


def test_overage_settlement_requires_exact_active_provider_membership() -> None:
    settle = _function("settle_batch_cost_overage", schema="agent_runtime")

    assert "security definer" in settle.lower()
    assert "set search_path = ''" in settle.lower()
    for required_nonnull in (
        "target_input_tokens is null",
        "target_output_tokens is null",
        "target_actual_cost_microusd is null",
    ):
        assert required_nonnull in settle
    assert "from agent_runtime.batch_jobs as current_job" in settle
    assert "for update" in settle
    assert "job.status not in ('submitted', 'in_progress')" in settle
    assert "job.reservation_state <> 'held'" in settle
    assert "job.current_batch_id is distinct from target_batch_id" in settle
    assert "from agent_runtime.batch_runs as current_run" in settle
    assert "run.provider_status not in (" in settle
    assert "'completed', 'failed', 'expired', 'cancelled'" in settle
    assert "run.output_file_id is null and run.error_file_id is null" in settle
    assert "run.finalized_at is not null" in settle
    assert "from agent_runtime.batch_members as member" in settle
    assert "member.batch_id = target_batch_id" in settle
    assert "member.job_id = target_job_id" in settle
    assert "member.attempt = job.attempts" in settle


def test_exact_outcome_fingerprint_replays_or_rejects_without_double_charge() -> None:
    settle = _function("settle_batch_cost_overage", schema="agent_runtime")

    assert "target_outcome_payload::text" in settle
    assert "'schema', 'coineasy.batch.cost_overage.v1'" in settle
    for fingerprint_binding in (
        "'workspace_id', target_workspace_id::text",
        "'job_id', target_job_id::text",
        "'provider_batch_id', target_batch_id",
        "'attempt', job.attempts",
        "'outcome_kind', target_outcome_kind",
        "'outcome_code', target_outcome_code",
        "'outcome_payload_sha256', payload_sha256",
        "'input_tokens', target_input_tokens",
        "'output_tokens', target_output_tokens",
        "'reservation_cap_microusd', job.max_cost_microusd",
        "'actual_cost_microusd', target_actual_cost_microusd",
    ):
        assert fingerprint_binding in settle
    assert "pg_catalog.sha256" in settle
    replay = settle.split("if found then", 1)[1].split(
        "if target_actual_cost_microusd <= job.max_cost_microusd then",
        1,
    )[0]
    for immutable_field in (
        "provider_batch_id",
        "attempt",
        "outcome_kind",
        "outcome_code",
        "outcome_payload_sha256",
        "input_tokens",
        "output_tokens",
        "reservation_cap_microusd",
        "actual_cost_microusd",
        "outcome_fingerprint",
    ):
        assert f"incident.{immutable_field}" in replay
    assert "Batch cost overage outcome cannot change" in replay
    assert "'reused', true" in replay
    assert settle.count(
        "insert into agent_runtime.batch_cost_overage_incidents"
    ) == 1


def test_overage_moves_only_the_cap_but_preserves_full_actual_cost() -> None:
    settle = _function("settle_batch_cost_overage", schema="agent_runtime")

    assert "target_actual_cost_microusd <= job.max_cost_microusd" in settle
    assert (
        "reserved_microusd = reserved_microusd - job.max_cost_microusd"
        in settle
    )
    assert "spent_microusd = spent_microusd + job.max_cost_microusd" in settle
    assert "reserved_microusd >= job.max_cost_microusd" in settle
    assert "spent_microusd + target_actual_cost_microusd" not in settle
    assert "set status = 'failed'" in settle
    assert "reservation_state = 'released'" in settle
    assert "actual_input_tokens = target_input_tokens" in settle
    assert "actual_output_tokens = target_output_tokens" in settle
    assert "actual_cost_microusd = target_actual_cost_microusd" in settle
    assert "error_code = 'batch_cost_cap_breached'" in settle
    assert "'budget_spent_microusd', incident.budget_spent_microusd" in settle
    assert settle.index(
        "insert into agent_runtime.batch_cost_overage_incidents"
    ) < settle.index("update agent_runtime.batch_budgets")


def test_existing_settlement_rpc_signatures_return_overage_signal() -> None:
    complete = _function("complete_agent_batch_job", schema="public")
    fail = _function("fail_agent_batch_job", schema="public")

    assert "agent_runtime.settle_batch_cost_overage(" in complete
    assert "'completion'" in complete
    assert "if overage_receipt is not null then" in complete
    assert "return overage_receipt" in complete
    assert "complete_agent_batch_job_within_cap(" in complete

    assert "target_retryable is false" in fail
    assert "target_charge_full_reservation is false" in fail
    assert "target_expected_batch_id is not null" in fail
    assert "target_actual_cost_microusd is not null" in fail
    assert "'failure'" in fail
    assert "return overage_receipt" in fail
    assert "fail_agent_batch_job_within_cap(" in fail

    signatures = {
        "complete_agent_batch_job": (
            "uuid, uuid, text, text, jsonb, bigint, bigint, bigint"
        ),
        "fail_agent_batch_job": (
            "uuid, uuid, text, text, boolean, timestamptz, "
            "bigint, bigint, bigint, boolean"
        ),
    }
    for name, signature in signatures.items():
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
    assert "grant execute on function agent_runtime.settle_batch_cost_overage" not in MIGRATION


def test_unresolved_incident_blocks_generic_and_exact_claim_mutations() -> None:
    block = _function(
        "block_batch_claim_after_cost_overage",
        schema="agent_runtime",
    )

    assert "old.status in ('queued', 'retry_wait')" in block
    assert "new.status = 'claimed'" in block
    assert "incident.workspace_id = new.workspace_id" in block
    assert "incident.resolution_status = 'unresolved'" in block
    assert "intent.status = 'armed'" in block
    assert "Batch safety fence blocks fresh claims" in block
    assert (
        "before update of status, locked_by, lease_expires_at\n"
        "on agent_runtime.batch_jobs"
    ) in MIGRATION
    assert "create or replace function public.claim_agent_batch_jobs" not in MIGRATION
    assert (
        "create or replace function public.claim_origintrail_batch_canary_job"
        not in MIGRATION
    )


def test_settlement_and_claim_admission_share_a_pre_snapshot_workspace_lock() -> None:
    lock_statement = """perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'coineasy:batch-cost-overage:' || target_workspace_id::text,
            0
        )
    );"""
    settle = _function("settle_batch_cost_overage", schema="agent_runtime")
    exact_claim = _function(
        "claim_origintrail_batch_canary_job",
        schema="public",
        source=CANARY_MIGRATION,
    )
    generic_claim = _function(
        "claim_agent_batch_jobs",
        schema="public",
        source=CANARY_MIGRATION,
    )
    authorize_create = _function(
        "authorize_origintrail_batch_provider_create",
        schema="public",
    )
    register_create = _function(
        "register_origintrail_batch_provider_create",
        schema="public",
    )

    for function in (
        settle,
        exact_claim,
        generic_claim,
        authorize_create,
        register_create,
    ):
        assert function.count(lock_statement) == 1

    assert settle.index(lock_statement) < settle.index(
        "select current_job.* into job"
    )
    assert exact_claim.index(lock_statement) < exact_claim.index(
        "select registered.* into canary_grant"
    )
    assert generic_claim.index(lock_statement) < generic_claim.index(
        "perform public.expire_agent_batch_jobs("
    )
    assert authorize_create.index(lock_statement) < authorize_create.index(
        "select registered.* into canary_grant"
    )
    assert register_create.index(lock_statement) < register_create.index(
        "select recorded.* into create_intent"
    )


def test_provider_create_intent_is_private_immutable_and_fail_closed() -> None:
    assert (
        "create table "
        "agent_runtime.origintrail_batch_provider_create_intents"
    ) in MIGRATION
    assert "where status = 'armed'" in MIGRATION
    assert "force row level security" in MIGRATION
    assert re.search(
        r"revoke all on table\s+"
        r"agent_runtime\.origintrail_batch_provider_create_intents\s+"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
    )
    assert "create_not_after <= authorized_at + interval '2 minutes'" in MIGRATION
    assert "before update or delete" in MIGRATION
    immutable = _function(
        "enforce_origintrail_batch_provider_create_intent",
        schema="agent_runtime",
    )
    assert "intent binding is immutable" in immutable
    assert "old.status = 'armed'" in immutable
    assert "new.status = 'registered'" in immutable

    settle = _function("settle_batch_cost_overage", schema="agent_runtime")
    assert "intent.status = 'armed'" in settle
    assert "using errcode = '55P03'" in settle
    # TTL never releases the external-call ambiguity fence.
    fenced = settle.split("if exists (", 1)[1].split("end if;", 1)[0]
    assert "create_not_after" not in fenced


def test_exact_provider_create_authorization_is_one_positive_receipt_only() -> None:
    authorize = _function(
        "authorize_origintrail_batch_provider_create",
        schema="public",
    )
    for exact_binding in (
        "canary_grant.consumed_by is distinct from target_worker_id",
        "batch_job.locked_by is distinct from target_worker_id",
        "batch_job.lease_expires_at <= statement_timestamp()",
        "batch_job.attempts <> 1",
        "batch_job.claimed_at is distinct from canary_grant.consumed_at",
        "create_intent.create_request_sha256",
        "target_create_request_sha256",
        "target_input_file_id",
        "target_dispatch_key",
    ):
        assert exact_binding in authorize
    replay = authorize.split("if found then", 1)[1].split(
        "if exists (",
        1,
    )[0]
    assert "'provider_create_allowed', false" in replay
    assert "'reused', true" in replay
    fresh = authorize.rsplit("return jsonb_build_object(", 1)[1]
    assert "'provider_create_allowed', true" in fresh
    assert "'reused', false" in fresh


def test_exact_registration_closes_intent_atomically_and_generic_cannot_bypass() -> None:
    generic = _function("register_agent_batch", schema="public")
    exact = _function(
        "register_origintrail_batch_provider_create",
        schema="public",
    )
    assert "origintrail_batch_canary_grants" in generic
    assert "register_agent_batch_without_canary_intent(" in generic
    assert "Exact canary registration requires provider-create intent" in generic
    assert "register_agent_batch_without_canary_intent(" in exact
    assert exact.index("registration :=") < exact.index(
        "set status = 'registered'"
    )
    assert "create_intent.status = 'registered'" in exact
    assert "from agent_runtime.batch_members as member" in exact
    assert "batch_job.current_batch_id is distinct from target_batch_id" in exact
    assert re.search(
        r"revoke all on function public\.register_agent_batch_without_canary_intent"
        r"\(\s*uuid, text, text, text, uuid\[\]\s*\)\s*"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )


def test_overage_and_exact_registration_lock_runs_before_jobs() -> None:
    settle = _function("settle_batch_cost_overage", schema="agent_runtime")
    register = _function(
        "register_origintrail_batch_provider_create",
        schema="public",
    )
    assert settle.index("select current_run.* into run") < settle.index(
        "select current_job.* into job"
    )
    assert register.index("select current_run.* into batch_run") < register.index(
        "select exact_job.* into batch_job"
    )
