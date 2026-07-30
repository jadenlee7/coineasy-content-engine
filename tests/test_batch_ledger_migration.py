from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260731120000_agent_batch_ledger.sql"
).read_text(encoding="utf-8")
SECURITY_SMOKE = (
    ROOT / "supabase" / "tests" / "agent_batch_ledger_security.sql"
).read_text(encoding="utf-8")


RPC_SIGNATURES = {
    "bind_review_draft_execution_plane": "uuid, text, text",
    "complete_review_draft_batch_handoff": "uuid, text, uuid, text",
    "list_agent_batch_review_inbox": (
        "uuid, integer, timestamptz, uuid"
    ),
    "get_agent_batch_review_item": "uuid, uuid",
}


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing {name}"
    return match.group(0)


def test_batch_review_rpcs_are_service_role_only_and_hardened() -> None:
    for name, signature in RPC_SIGNATURES.items():
        function = _function(name).lower()
        assert "security definer" in function
        assert "set search_path = ''" in function
        escaped = re.escape(signature).replace(r"\ ", r"\s*")
        assert re.search(
            rf"revoke all on function public\.{name}\(\s*{escaped}\s*\)"
            rf"\s*from public, anon, authenticated, service_role;",
            MIGRATION,
            re.DOTALL,
        )
        assert re.search(
            rf"grant execute on function public\.{name}\(\s*{escaped}\s*\)"
            rf"\s*to service_role;",
            MIGRATION,
            re.DOTALL,
        )


def test_batch_handoff_is_same_uuid_lease_fenced_and_side_effect_free() -> None:
    handoff = _function("complete_review_draft_batch_handoff")

    assert "target_batch_job_id is distinct from target_job_id" in handoff
    assert "review_job.status <> 'running'" in handoff
    assert "review_job.locked_by is distinct from target_worker_id" in handoff
    assert "review_job.lease_expires_at <= statement_timestamp()" in handoff
    assert "batch_job.deadline_at <= statement_timestamp()" not in handoff
    assert "Deadline is admission-time policy" in handoff
    assert "batch_job.client_id <> 'squid'" in handoff
    assert (
        "batch_job.workflow_kind\n"
        "            <> 'official_source_nonurgent_pack'"
    ) in handoff
    assert "batch_job.input_sha256 is distinct from target_input_sha256" in handoff
    assert "'workflow', 'agent_batch_review_handoff_v1'" in handoff
    assert "'handoff', 'openai_batch'" in handoff
    assert "'review_state', 'pending'" in handoff
    assert "'completed_by'" not in handoff
    for forbidden in (
        "insert into public.content_items",
        "insert into public.content_versions",
        "insert into public.approvals",
        "insert into public.publications",
    ):
        assert forbidden not in handoff.lower()


def test_budget_key_binds_an_immutable_period_and_hard_cap() -> None:
    configure = _function("configure_agent_batch_budget")

    assert "hard_limit_microusd between 1 and 6000000" in MIGRATION
    assert "target_hard_limit_microusd not between 1 and 6000000" in configure
    assert "pg_catalog.pg_advisory_xact_lock" in configure
    assert "budget.period_start is distinct from target_period_start" in configure
    assert "budget.period_end is distinct from target_period_end" in configure
    assert (
        "budget.hard_limit_microusd\n"
        "           is distinct from target_hard_limit_microusd"
    ) in configure
    assert "batch budget hard limit is immutable for its key" in configure
    assert "set hard_limit_microusd = target_hard_limit_microusd" not in configure
    assert "'reused', reused" in configure


def test_queue_replay_uses_the_durable_stored_budget_binding() -> None:
    queue = _function("queue_agent_batch_job")
    replay_path = queue.split("if found then", 1)[1].split(
        "-- These checks are admission-time policy",
        1,
    )[0]

    assert "stored binding is authoritative" in replay_path
    assert (
        "existing_job.budget_key is distinct from target_budget_key"
        not in replay_path
    )
    assert "'custom_id', expected_first_custom_id" in replay_path
    assert "'custom_id', existing_job.custom_id" not in replay_path
    assert "'budget_key', existing_job.budget_key" in replay_path
    assert "'budget_key', target_budget_key" in queue
    assert "target_replay_only boolean" in queue
    assert "target_replay_only is null" in queue
    assert (
        queue.index("if target_replay_only then")
        < queue.index("-- These checks are admission-time policy")
    )
    assert "replay-only batch job was not previously committed" in queue
    signature = (
        "uuid, text, uuid, text, text, text, text, smallint, text, text, "
        "text, timestamptz, jsonb, text, bigint, integer, bigint, text, "
        "text, boolean"
    )
    escaped = re.escape(signature).replace(r"\ ", r"\s*")
    assert re.search(
        rf"revoke all on function public\.queue_agent_batch_job\("
        rf"\s*{escaped}\s*\)\s*"
        rf"from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        rf"grant execute on function public\.queue_agent_batch_job\("
        rf"\s*{escaped}\s*\)\s*to service_role;",
        MIGRATION,
        re.DOTALL,
    )


def test_expiration_rpc_only_releases_known_pre_provider_work() -> None:
    expiration = _function("expire_agent_batch_jobs")
    claim = _function("claim_agent_batch_jobs")
    lowered = expiration.lower()

    assert "security definer" in lowered
    assert "set search_path = ''" in lowered
    assert "job.status in ('queued', 'retry_wait')" in expiration
    assert "job.reservation_state = 'held'" in expiration
    assert "job.deadline_at <= statement_timestamp()" in expiration
    assert "job.attempts >= job.max_attempts" in expiration
    assert "job.status = 'claimed'" in expiration
    assert "'expired_job_count'" in expiration
    assert "'released_microusd'" in expiration
    assert "'ambiguous_claimed_count'" in expiration
    assert "from public.workspace_clients as client" in expiration
    assert "client.active" not in expiration
    assert "active is true" not in expiration
    assert "perform public.expire_agent_batch_jobs(" in claim
    assert "for stale_job in" not in claim
    assert re.search(
        r"revoke all on function public\.expire_agent_batch_jobs\(\s*"
        r"uuid,\s*text\[\]\s*\)\s*"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        r"grant execute on function public\.expire_agent_batch_jobs\(\s*"
        r"uuid,\s*text\[\]\s*\)\s*to service_role;",
        MIGRATION,
        re.DOTALL,
    )


def test_review_execution_plane_is_immutable_and_lease_fenced() -> None:
    binding = _function("bind_review_draft_execution_plane")
    handoff = _function("complete_review_draft_batch_handoff")

    assert "for update" in binding
    assert "review_job.status <> 'running'" in binding
    assert "review_job.locked_by is distinct from target_worker_id" in binding
    assert "review_job.lease_expires_at <= statement_timestamp()" in binding
    assert "target_requested_plane not in ('studio_sync', 'openai_batch')" in binding
    assert "review_job.output ? 'execution_plane'" in binding
    assert "bound_plane := review_job.output ->> 'execution_plane'" in binding
    assert "bound_plane := target_requested_plane" in binding
    assert "'official_x_review_draft_execution_plane_bound'" in binding
    assert "'execution_plane', bound_plane" in binding
    assert "'reused', reused" in binding
    assert "review_job.output ->> 'execution_plane'" in handoff
    assert "is distinct from 'openai_batch'" in handoff
    assert (
        handoff.index("if review_job.status = 'succeeded' then")
        < handoff.index("is distinct from 'openai_batch'")
    )


def test_review_reads_are_stable_keyset_paginated_and_fail_closed() -> None:
    listing = _function("list_agent_batch_review_inbox")
    detail = _function("get_agent_batch_review_item")

    for function in (listing, detail):
        lowered = function.lower()
        assert "\nstable\nsecurity definer" in lowered
        assert "batch_job.client_id = 'squid'" in function
        assert (
            "batch_job.workflow_kind = 'official_source_nonurgent_pack'"
            in function
        )
        assert "batch_job.status = 'completed'" in function
        assert "batch_job.result_code = 'needs_review'" in function
        assert (
            "batch_job.input_payload -> 'approval_required' = 'true'::jsonb"
            in function
        )
        assert "review_job.status = 'succeeded'" in function
        assert "'workflow', 'agent_batch_review_handoff_v1'" in function
        assert "'handoff', 'openai_batch'" in function

    assert "target_before_finished_at" in listing
    assert "target_before_job_id" in listing
    assert (
        "(batch_job.finished_at, batch_job.job_id)\n"
        "                 < (target_before_finished_at, target_before_job_id)"
    ) in listing
    assert "'next_cursor'" in listing
    assert "'result_payload'" in detail
    assert "'input_sha256'" in detail
    assert "'actual_input_tokens'" in detail
    assert "'actual_output_tokens'" in detail
    assert listing.count("~ '[^[:space:]]'") >= 4
    assert detail.count("~ '[^[:space:]]'") >= 4
    for field, maximum in (
        ("headline_ko", 120),
        ("body_ko", 1800),
        ("x_copy_ko", 500),
        ("telegram_copy_ko", 1800),
    ):
        assert f"result_payload -> '{field}'" in listing
        assert f"result_payload -> '{field}'" in detail
        assert f"result_payload ->> '{field}'" in listing
        assert f"between 1 and {maximum}" in listing


def test_official_source_completion_revalidates_the_strict_result_schema() -> None:
    completion = _function("complete_agent_batch_job")

    assert "job.client_id = 'squid'" in completion
    assert "job.workflow_kind = 'official_source_nonurgent_pack'" in completion
    assert "target_result_code is distinct from 'needs_review'" in completion
    assert "from jsonb_object_keys(target_result_payload)" in completion
    assert ") <> 4" in completion
    assert completion.count("~ '[^[:space:]]'") >= 4
    for field, maximum in (
        ("headline_ko", 120),
        ("body_ko", 1800),
        ("x_copy_ko", 500),
        ("telegram_copy_ko", 1800),
    ):
        assert f"target_result_payload -> '{field}'" in completion
        assert f"target_result_payload ->> '{field}'" in completion
        assert f"not between 1 and {maximum}" in completion


def test_security_smoke_covers_review_auth_replay_pagination_and_no_side_effects() -> None:
    for evidence in (
        "complete_review_draft_batch_handoff(uuid,text,uuid,text)",
        "list_agent_batch_review_inbox(uuid,integer,timestamp with time zone,uuid)",
        "get_agent_batch_review_item(uuid,uuid)",
        "another worker completed the active handoff lease",
        "Batch handoff replay was not worker-independent",
        "Batch review inbox keyset pagination is invalid",
        "Batch review inbox exposed a private or invalid field",
        "official-source Batch accepted an extra result key",
        "official-source Batch accepted a whitespace-only field",
        "Batch budget key accepted a hard-cap raise",
        "Batch budget accepted more than the $6 pilot ceiling",
        "cross-budget queue replay changed its durable reservation",
        "queue replay did not return the immutable producer custom id",
        "replay-only miss created a job or reservation",
        "replay-only immutable mismatch was accepted",
        "expire_agent_batch_jobs(uuid,text[])",
        "unconfigured client entered the expiration scope",
        "expiration cleanup did not release all three pre-provider cases",
        "expiration cleanup was not idempotent",
        "expiration cleanup mutated submitted provider work",
        "expiration cleanup mutated in-progress provider work",
        "expiration cleanup mutated completed provider work",
        "expiration cleanup released ambiguous claimed provider work",
        "ambiguous claimed cleanup receipt was not idempotent",
        "claim cleanup reclaimed expired ambiguous provider work",
        "bind_review_draft_execution_plane(uuid,text,text)",
        "wrong worker bound a review execution plane",
        "expired lease bound a review execution plane",
        "same lease changed the bound Batch execution plane",
        "sync-bound retry under Batch phase changed planes",
        "Batch-bound retry after phase changed planes",
        "review draft failure erased the execution plane marker",
        "unbound review job entered the Batch handoff",
        "expired queued Batch handoff receipt was not recovered",
        "expired handoff entered the review inbox",
        "Batch handoff or review reads created Studio side effects",
    ):
        assert evidence in SECURITY_SMOKE
