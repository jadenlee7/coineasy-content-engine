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
    "record_origintrail_nonquote_sources": (
        "uuid, text, text, uuid, text, text, jsonb, timestamptz"
    ),
    "bind_review_draft_execution_plane": "uuid, text, text",
    "complete_review_draft_batch_handoff": "uuid, text, uuid, text",
    "recover_review_draft_batch_handoff": "uuid, text",
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


def _runtime_function(name: str) -> str:
    match = re.search(
        rf"create or replace function agent_runtime\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing agent_runtime.{name}"
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
    assert "batch_job.client_id <> 'origintrail'" in handoff
    assert "batch_job.agent_id <> 'origintrail_client_agent'" in handoff
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


def test_origintrail_canary_queue_and_claim_use_the_exact_route() -> None:
    queue = _function("queue_agent_batch_job")
    claim = _function("claim_agent_batch_jobs")

    for identity in (
        "target_agent_id <> 'origintrail_client_agent'",
        "target_workflow_kind <> 'official_source_nonurgent_pack'",
        "target_stage <> 'generate'",
    ):
        assert identity in queue
    assert "review_job.input ->> 'content_kind' = 'daily_news'" in queue
    assert "OriginTrail canary accepts daily news only" in queue
    assert "job.agent_id = 'origintrail_client_agent'" in claim
    assert "job.workflow_kind = 'official_source_nonurgent_pack'" in claim
    assert "job.stage = 'generate'" in claim


def test_origintrail_batch_evidence_is_daily_news_text_only_and_revalidated() -> None:
    evidence = _runtime_function("origintrail_review_is_text_only")
    binding = _function("bind_review_draft_execution_plane")
    handoff = _function("complete_review_draft_batch_handoff")

    assert "is distinct from 'daily_news'" in evidence
    assert "source_image_url" in evidence
    assert "jsonb_array_length(source.media) = 0" in evidence
    assert "text_only_source_count = source_count" in evidence
    assert "private.origintrail_standalone_sources" in evidence
    assert "standalone.is_quote is false" in evidence
    assert "origintrail_review_is_text_only(review_job.id)" in binding
    assert "origintrail_review_is_text_only(review_job.id)" in handoff
    assert "is distinct from 'daily_news'" in handoff


def test_origintrail_nonquote_intake_is_durable_and_legacy_rows_fail_closed() -> None:
    intake = _function("record_origintrail_nonquote_sources")
    claim = _function("claim_review_draft_job")

    assert "create table private.origintrail_standalone_sources" in MIGRATION
    assert "item -> 'is_quote' is distinct from 'false'::jsonb" in intake
    assert "public.record_official_x_sources(" in intake
    assert "insert into private.origintrail_standalone_sources" in intake
    assert "on conflict (source_item_id) do nothing" in intake
    assert "OriginTrail source was not durably verified as standalone" in intake
    assert "'origintrail_batch_eligible'" in claim
    assert "origintrail_review_is_text_only(candidate.id)" in claim


def test_handoff_recovery_uses_only_the_stored_safe_receipt() -> None:
    claim = _function("claim_review_draft_job")
    queue = _function("queue_agent_batch_job")
    recovery = _function("recover_review_draft_batch_handoff")
    recoverable = _runtime_function("has_recoverable_origintrail_handoff")

    assert "job.finished_at <= statement_timestamp()" in claim
    assert "- interval '1 minute'" in claim
    assert "when recovery_only then attempts else attempts + 1" in claim
    assert "batch_handoff_recovery_only" in claim
    assert "'batch_handoff_recovery_only', recovery_only" in claim
    assert "Batch handoff recovery cannot admit new work" in queue
    assert "review_job.output -> 'batch_handoff_recovery_only'" in queue
    assert "review_job.attempts <= 1" in recovery
    assert "review_job.attempts > review_job.max_attempts" in recovery
    assert "review_job.locked_by is distinct from target_worker_id" in recovery
    assert "max-attempt Batch recovery receipt is unavailable" in recovery
    assert "agent_runtime.has_recoverable_origintrail_handoff(" in recovery
    assert "batch_job.input_sha256" in recovery
    assert "return public.complete_review_draft_batch_handoff(" in recovery
    for forbidden in (
        "queue_agent_batch_job",
        "configure_agent_batch_budget",
        "register_agent_batch",
        "input_payload =",
        "insert into agent_runtime.batch_jobs",
    ):
        assert forbidden not in recovery
    assert "batch_job.status = 'completed'" in recoverable
    assert "batch_job.result_code = 'needs_review'" in recoverable
    assert "origintrail_review_is_text_only(target_job_id)" in recoverable


def test_review_completion_enforces_both_execution_planes_at_table_boundary() -> None:
    trigger = _runtime_function("enforce_review_execution_plane")

    assert "new.output ->> 'handoff' = 'openai_batch'" in trigger
    assert "is distinct from 'openai_batch'" in trigger
    assert "new.output ? 'content_item_id'" in trigger
    assert "is distinct from 'studio_sync'" in trigger
    assert "before update on public.jobs" in MIGRATION
    assert "execute function agent_runtime.enforce_review_execution_plane()" in MIGRATION


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
        assert "batch_job.client_id = 'origintrail'" in function
        assert "batch_job.agent_id = 'origintrail_client_agent'" in function
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

    assert "job.client_id = 'origintrail'" in completion
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
        "wrong OriginTrail canary identity was admitted",
        "OriginTrail intake accepted a missing quote signal",
        "OriginTrail intake accepted a non-false quote signal",
        "OriginTrail intake accepted a missing standalone signal",
        "OriginTrail intake accepted a referenced standalone signal",
        "standalone OriginTrail source was not durably allowlisted",
        "OriginTrail standalone allowlist replay was mutable",
        "pre-cutover OriginTrail source was Batch eligible",
        "pre-cutover OriginTrail source did not route Studio",
        "media-backed OriginTrail job entered the Batch plane",
        "unbound review job entered the Batch handoff",
        "max-attempt Batch recovery bypassed its cooldown",
        "stored Batch handoff receipt was not completed",
        "stored receipt recovery created Batch work or cost",
        "ordinary retry did not consume its stored receipt",
        "ordinary retry receipt miss was not nullable",
        "media mutation bypassed Batch handoff revalidation",
        "expired queued Batch handoff receipt was not recovered",
        "expired handoff entered the review inbox",
        "Batch handoff or review reads created Studio side effects",
    ):
        assert evidence in SECURITY_SMOKE
