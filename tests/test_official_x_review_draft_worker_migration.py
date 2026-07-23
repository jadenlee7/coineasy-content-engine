"""Static safety contract for the official-X review-draft worker migration."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
MIGRATION_PATHS = sorted(MIGRATIONS.glob("*_official_x_review_draft_worker.sql"))
assert len(MIGRATION_PATHS) == 1
MIGRATION = MIGRATION_PATHS[0].read_text(encoding="utf-8")
INDEX_MIGRATION_PATHS = sorted(
    MIGRATIONS.glob("*_add_official_x_worker_fk_indexes.sql")
)
assert len(INDEX_MIGRATION_PATHS) == 1
INDEX_MIGRATION = INDEX_MIGRATION_PATHS[0].read_text(encoding="utf-8")
SQL_SMOKE = (
    ROOT / "supabase" / "tests" / "official_x_review_draft_worker_security.sql"
)


RPC_SIGNATURES = {
    "record_official_x_sources": "uuid, text, text, uuid, text, text, jsonb, timestamptz",
    "get_official_x_automation_state": "uuid, text, date, integer",
    "queue_review_draft_job": (
        "uuid, text, date, jsonb, text, uuid, text, text, text, boolean"
    ),
    "claim_review_draft_job": "uuid, text, integer",
    "complete_review_draft_job": "uuid, text, uuid, uuid",
    "fail_review_draft_job": "uuid, text, text, text, boolean, timestamptz",
}


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing {name}"
    return match.group(0)


def test_migration_is_forward_only_and_seeds_exact_official_feeds() -> None:
    assert re.search(r"\bbegin;", MIGRATION)
    assert MIGRATION.rstrip().endswith("commit;")
    assert not re.search(r"\b(drop|truncate)\b", MIGRATION, re.IGNORECASE)
    assert "source_feeds_active_x_client_idx" in MIGRATION
    assert "source_items_x_external_id_idx" in MIGRATION
    for client_id, handle in (
        ("yellow", "@Yellow"),
        ("origintrail", "@origin_trail"),
        ("squid", "@SquidRouter"),
        ("babylon", "@babylonlabs_io"),
    ):
        assert f"('{client_id}'," in MIGRATION
        assert f"'{handle}'" in MIGRATION
    upsert = MIGRATION.split("on conflict (workspace_id, client_id)", 1)[1].split(";", 1)[0]
    assert "last_cursor" not in upsert
    assert "last_polled_at" not in upsert


def test_private_ledgers_close_intake_and_daily_limit_races() -> None:
    assert "private.official_x_poll_receipts" in MIGRATION
    assert "private.official_x_source_state" in MIGRATION
    assert "private.official_x_daily_slots" in MIGRATION
    assert "primary key (workspace_id, kst_date, client_id)" in MIGRATION
    assert "unique (workspace_id, kst_date, slot)" in MIGRATION
    assert "check (slot between 1 and 4)" in MIGRATION
    assert re.search(
        r"revoke all on table.*?official_x_daily_slots.*?"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )


def test_worker_foreign_keys_have_covering_indexes() -> None:
    assert INDEX_MIGRATION.rstrip().endswith("commit;")
    assert not re.search(r"\b(drop|truncate)\b", INDEX_MIGRATION, re.IGNORECASE)
    for index_name in (
        "official_x_poll_receipts_feed_fk_idx",
        "official_x_source_state_source_fk_idx",
        "official_x_source_state_queued_job_fk_idx",
        "official_x_daily_slots_client_fk_idx",
    ):
        assert f"create index if not exists {index_name}" in INDEX_MIGRATION


def test_intake_is_cursor_cas_append_only_and_recovers_unqueued_sources() -> None:
    function = _function("record_official_x_sources")
    assert "for update" in function.lower()
    assert "feed.last_cursor is distinct from normalized_expected_cursor" in function
    assert "using errcode = '40001'" in function
    assert "on conflict (workspace_id, client_id, source_feed_id, external_id)" in function
    assert "do nothing" in function
    assert "update public.source_items" not in function
    assert "private.official_x_poll_receipts" in function
    assert "private.official_x_pending_sources" in function
    assert "'pending_sources'" in function
    assert "'is_note_tweet'" in function
    assert "'metrics'" in function
    assert "existing official X source does not match normalized intake" in function
    for immutable_field in (
        "source_type",
        "canonical_url",
        "author_handle",
        "published_at",
        "body",
        "media",
        "raw_payload",
        "source_hash",
        "ingested_by",
    ):
        assert f"committed_source.{immutable_field}" in function


def test_queue_reserves_one_client_and_four_global_kst_slots_atomically() -> None:
    function = _function("queue_review_draft_job")
    assert "pg_advisory_xact_lock" in function
    assert "generate_series(1, 4)" in function
    assert "global KST-day review draft limit of four" in function
    assert "tutorials must remain manual-only" in function
    assert "job_kind" in function and "'generate'" in function
    for key in (
        "source_item_ids",
        "content_kind",
        "request_id",
        "source_content",
        "source_url",
        "source_image_url",
        "manual_only",
    ):
        assert f"'{key}'" in function
    assert "insert into private.official_x_daily_slots" in function
    assert "update private.official_x_source_state" in function


def test_claim_is_lease_owned_bounded_and_never_claims_manual_or_publish_jobs() -> None:
    function = _function("claim_review_draft_job")
    assert "for update skip locked" in function.lower()
    assert "job.job_kind = 'generate'" in function
    assert "official_x_review_draft_v1" in function
    assert "job.input -> 'manual_only' = 'false'::jsonb" in function
    assert "job.attempts < job.max_attempts" in function
    assert "attempts = attempts + 1" in function
    assert "lease_expires_at" in function
    assert "'locked_by'" in function
    assert "'publish'" not in function
    assert "'figma_export'" not in function


def test_complete_requires_current_needs_review_and_links_every_pinned_source() -> None:
    function = _function("complete_review_draft_job")
    assert "job.locked_by is distinct from target_worker_id" in function
    assert "job.lease_expires_at <= statement_timestamp()" in function
    assert "item.status <> 'needs_review'" in function
    assert "item.current_version_id <> target_content_version_id" in function
    assert "version.generation_meta ->> 'request_id'" in function
    assert "content_source_links" in function
    assert "linked_count <> source_count" in function
    assert "status = 'succeeded'" in function
    assert "update public.content_items" not in function
    assert "insert into public.publications" not in function


def test_failure_is_retry_aware_bounded_and_clears_the_lease() -> None:
    function = _function("fail_review_draft_job")
    assert "target_retryable boolean default true" in function
    assert "char_length(target_error_message) not between 1 and 1000" in function
    assert "interval '24 hours'" in function
    assert "job.attempts < job.max_attempts" in function
    assert "next_status := 'retrying'" in function
    assert "next_status := 'failed'" in function
    assert "locked_by = null" in function
    assert "lease_expires_at = null" in function
    assert "'last_failure'" in function


def test_all_public_worker_rpcs_are_service_role_only_and_hardened() -> None:
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


def test_transactional_sql_smoke_covers_crash_recovery_and_stale_workers() -> None:
    assert SQL_SMOKE.exists()
    sql = SQL_SMOKE.read_text(encoding="utf-8")
    assert sql.lstrip().startswith("--")
    assert "begin;" in sql
    assert sql.rstrip().endswith("rollback;")
    assert "record-without-queue recovery failed" in sql
    assert "stale worker completed another lease" in sql
    assert "manual-only tutorial was claimed" in sql
    assert "global KST-day slot ledger is invalid" in sql
    assert "automation created a publication or export job" in sql
    assert "mismatched pre-existing X source reached the pending ledger" in sql
