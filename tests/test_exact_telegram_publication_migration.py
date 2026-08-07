"""Static contract for exact approved-version Telegram publication."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260801120000_exact_telegram_publication.sql"
).read_text(encoding="utf-8")
SQL_SMOKE_PATH = (
    ROOT / "supabase" / "tests" / "exact_telegram_publication_security.sql"
)
SQL_SMOKE = SQL_SMOKE_PATH.read_text(encoding="utf-8")


RPC_SIGNATURES = {
    "request_studio_telegram_publication": "uuid, uuid, uuid, text",
    "get_studio_telegram_publication": "uuid, uuid, uuid",
    "reconcile_expired_exact_telegram_publication_leases": "uuid, integer",
    "claim_exact_telegram_publication_job": "uuid, text, integer",
    "mark_exact_telegram_attempt_started": "uuid, text, text",
    "complete_exact_telegram_publication_job": (
        "uuid, text, text, bigint, text, timestamptz"
    ),
    "fail_exact_telegram_publication_job": "uuid, text, text, boolean",
}


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing {name}"
    return match.group(0)


def test_migration_adds_a_terminal_delivery_unknown_state_and_exact_uniqueness() -> None:
    assert re.search(r"\bbegin;", MIGRATION)
    assert MIGRATION.rstrip().endswith("commit;")
    assert "'delivery_unknown'" in MIGRATION
    assert "publications_delivery_attempt_check" in MIGRATION
    assert "delivery_request_sha256 ~ '^[a-f0-9]{64}$'" in MIGRATION
    assert "publications_exact_telegram_once_idx" in MIGRATION
    assert "jobs_exact_telegram_once_idx" in MIGRATION
    assert "exact_telegram_publication_v1" in MIGRATION


def test_request_is_squid_only_and_pins_one_current_production_approval() -> None:
    function = _function("request_studio_telegram_publication")
    assert "item.client_id <> 'squid'" in function
    assert "item.content_kind <> 'daily_news'" in function
    assert "item.status <> 'approved'" in function
    assert "item.current_version_id is distinct from target_content_version_id" in function
    assert "version.generation_meta -> 'mock_mode' is distinct from 'false'::jsonb" in function
    assert "client.active is true" in function
    assert "approval.decision <> 'approved'" in function
    assert "char_length(telegram_text) > 1024" in function
    assert "asset_count <> 1" in function
    assert "candidate.byte_size between 8 and 10485760" in function
    assert "candidate.storage_bucket = 'content-studio'" in function
    assert "candidate.storage_path = target_workspace_id::text" in function
    for pin in ("approval_id", "asset_id", "content_version_id"):
        assert f"'{pin}'" in function
    assert function.count("'asset_snapshot', asset_snapshot") == 2
    for field in (
        "asset_id",
        "sha256",
        "byte_size",
        "storage_bucket",
        "storage_path",
        "mime_type",
        "width",
        "height",
        "storage_object",
    ):
        assert f"'{field}'" in MIGRATION


def test_request_replays_and_different_keys_converge_without_losing_result() -> None:
    function = _function("request_studio_telegram_publication")
    assert "idempotency_key = lower(request_idempotency_key)" in function
    assert "Different request keys still converge" in function
    assert re.search(
        r"other_publication\.status in \(\s*"
        r"'queued', 'publishing', 'published', 'delivery_unknown'\s*\)",
        function,
    )
    assert "other_job.status in ('queued', 'running', 'retrying', 'succeeded')" in function
    assert function.count("'external_url', existing_publication.external_url") == 2
    assert function.count(
        "'error_code', existing_publication.response_payload ->> 'error_code'"
    ) == 2
    assert "'external_url', null" in function
    assert "'error_code', null" in function
    assert function.count(
        "'delivery_started_at', existing_publication.delivery_started_at"
    ) == 2
    assert "'delivery_started_at', null" in function


def test_claim_is_lease_owned_and_revalidates_every_pinned_input() -> None:
    function = _function("claim_exact_telegram_publication_job")
    assert "for update skip locked" in function.lower()
    assert "target_lease_seconds not between 180 and 600" in function
    assert "queued_job.status in ('queued', 'retrying')" in function
    assert "attempts = attempts + 1" in function
    assert "publication.request_payload ->> 'approval_id'" in function
    assert "candidate.input ->> 'approval_id'" in function
    assert "publication.request_payload ->> 'asset_id'" in function
    assert "candidate.input ->> 'asset_id'" in function
    assert "candidate.input -> 'asset_snapshot'" in function
    assert "publication.request_payload -> 'asset_snapshot'" in function
    assert "current_asset_snapshot" in function
    assert "item.client_id <> 'squid'" in function
    assert "item.status <> 'approved'" in function
    assert "version.generation_meta -> 'mock_mode' is distinct from 'false'::jsonb" in function
    assert "stored_asset.byte_size between 8 and 10485760" in function
    assert "stored_asset.storage_path = candidate.workspace_id::text" in function
    assert "'approval_id', approval.id" in function
    assert "'telegram_public_username', telegram_public_username" in function
    assert "'asset', jsonb_build_object(" in function


def test_reciprocal_guard_is_race_serialized_with_one_manual_observation_exception() -> None:
    trigger = re.search(
        r"create or replace function private\."
        r"enforce_exact_telegram_publication_exclusivity\(\).*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert trigger is not None
    body = trigger.group(0)
    assert "pg_catalog.pg_advisory_xact_lock(" in body
    assert "pg_catalog.pg_try_advisory_xact_lock(" in body
    assert "pg_catalog.hashtextextended(" in body
    assert "from public.content_items" not in body
    assert "existing.id <> new.id" in body
    assert "'queued', 'publishing', 'published', 'delivery_unknown'" in body
    assert "incoming_is_manual_observation" in body
    assert "existing.status = 'delivery_unknown'" in body
    assert "new.request_payload = jsonb_build_object(" in body
    assert "new.response_payload = jsonb_build_object(" in body
    assert re.search(
        r"before insert or update of\s+workspace_id, content_item_id, "
        r"content_version_id, channel, status,\s+request_payload,\s+"
        r"response_payload, external_url\s+"
        r"on public\.publications",
        MIGRATION,
    )


def test_all_mutating_exact_rpcs_acquire_item_then_job_then_publication() -> None:
    request = _function("request_studio_telegram_publication")
    assert request.index("select content.* into item") < request.index(
        "select queued_job.* into existing_job", request.index("Probe without")
    )
    assert request.index("insert into public.jobs") < request.index(
        "insert into public.publications"
    )

    claim = _function("claim_exact_telegram_publication_job")
    assert claim.index("for update of content skip locked") < claim.index(
        "select queued_job.* into candidate"
    ) < claim.index("select delivery.* into publication")

    reconcile = _function("reconcile_expired_exact_telegram_publication_leases")
    assert reconcile.index("for update of content skip locked") < reconcile.index(
        "select queued_job.* into job"
    ) < reconcile.index("select delivery.* into publication")

    for name in (
        "mark_exact_telegram_attempt_started",
        "complete_exact_telegram_publication_job",
        "fail_exact_telegram_publication_job",
    ):
        function = _function(name)
        assert function.index("select content.* into item") < function.index(
            "select queued_job.* into job"
        ) < function.index("select delivery.* into publication")


def test_recovery_only_rpc_is_bounded_and_never_claims_or_calls_a_provider() -> None:
    function = _function("reconcile_expired_exact_telegram_publication_leases")
    assert "target_limit is null" in function
    assert "target_limit not between 1 and 100" in function
    assert "queued_job.lease_expires_at <= statement_timestamp()" in function
    assert "for update of content skip locked" in function.lower()
    assert "for update skip locked" in function.lower()
    assert "next_publication" not in function
    assert "status = 'retrying'" in function
    assert "status = 'delivery_unknown'" in function
    assert "'lease_reconciliation', true" in function
    assert "'reconciled_count', reconciled_count" in function
    assert "'retrying_count', retrying_count" in function
    assert "'failed_count', failed_count" in function
    assert "'delivery_unknown_count', delivery_unknown_count" in function


def test_attempt_marker_is_the_final_current_approval_fence() -> None:
    function = _function("mark_exact_telegram_attempt_started")
    assert "delivery_request_sha256" in function
    assert "item.current_version_id is distinct from publication.content_version_id" in function
    assert "item.status <> 'approved'" in function
    assert "item.client_id <> 'squid'" in function
    assert "review.decision = 'approved'" in function
    assert "active_client.active is true" in function
    assert "publication.request_payload ->> 'approval_id'" in function
    assert "'attempt_started', true" in function
    assert "'exact_telegram_attempt_started'" in function


def test_completion_builds_only_a_canonical_public_telegram_url() -> None:
    function = _function("complete_exact_telegram_publication_job")
    assert "when 'squid' then 'squid_kor_update'" in function
    assert "canonical_url := 'https://t.me/'" in function
    assert "normalized_chat_username is distinct from expected_chat_username" in function
    assert "publication.status <> 'publishing'" in function
    assert "publication.delivery_request_sha256" in function
    assert "status = 'published'" in function
    assert "status = 'succeeded'" in function
    assert "current_version_id = publication.content_version_id" in function


def test_failure_accepts_only_bounded_codes_and_never_retries_after_attempt() -> None:
    function = _function("fail_exact_telegram_publication_job")
    assert "target_error_message" not in MIGRATION
    for code in (
        "publication_client_not_allowed",
        "telegram_publication_config_invalid",
        "telegram_publication_channel_inactive",
        "telegram_publication_credentials_invalid",
        "telegram_publication_target_invalid",
        "telegram_publication_target_mismatch",
        "telegram_preflight_unavailable",
        "telegram_preflight_rejected",
        "telegram_response_invalid",
        "publication_asset_unavailable",
        "publication_asset_invalid",
        "telegram_publication_preflight_failed",
        "telegram_publication_request_invalid",
        "telegram_delivery_unknown",
    ):
        assert f"'{code}'" in function
    assert "publication.delivery_started_at is not null" in function
    assert "next_publication_status := 'delivery_unknown'" in function
    assert "next_job_status := 'failed'" in function
    assert "next_publication_status := 'queued'" in function
    assert "'The exact Telegram worker reported an allowlisted failure.'" in function
    assert "'error_code', target_error_code" in function
    assert "response_payload = jsonb_build_object(" in function


def test_every_rpc_is_hardened_and_service_role_only() -> None:
    for name, signature in RPC_SIGNATURES.items():
        function = _function(name)
        assert "security definer" in function.lower()
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


def test_transactional_sql_smoke_covers_canary_replay_and_attempt_boundaries() -> None:
    assert SQL_SMOKE_PATH.exists()
    assert SQL_SMOKE.lstrip().startswith("--")
    assert "begin;" in SQL_SMOKE
    assert SQL_SMOKE.rstrip().endswith("rollback;")
    for marker in (
        "Yellow escaped the Squid-only Telegram canary",
        "different idempotency keys did not converge on one exact delivery",
        "pre-attempt failure did not remain retryable",
        "post-attempt uncertainty was retried",
        "request replay lost the exact Telegram failure result",
        "request replay lost the published Telegram result",
        "stale approval passed the final provider-attempt fence",
        "invalid legacy exact job starved a later attested job",
        "arbitrary worker error code reached the database",
        "expired post-fence lease became claimable again",
        "NULL exact Telegram recovery limit was accepted",
        "generic publication request bypassed exact delivery_unknown",
        "manual observation of exact delivery_unknown failed",
        "manual observation response was weakened beside exact unknown",
        "manual observation URL was weakened beside exact unknown",
        "direct generic publication insert bypassed exact publication",
        "generic failed publication was reactivated beside exact publication",
        "exact request bypassed competing generic delivery_unknown",
        "competing generic delivery_unknown left an exact publish job",
        "asset snapshot drift reached the worker claim",
        "Storage object replacement reached the worker claim",
        "recovery-only pre-attempt lease was not retried",
    ):
        assert marker in SQL_SMOKE
