"""Static safety contract for unknown Telegram delivery resolution."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260808120000_exact_telegram_delivery_resolution.sql"
).read_text(encoding="utf-8")
SQL_SMOKE = (
    ROOT / "supabase" / "tests" / "exact_telegram_publication_security.sql"
).read_text(encoding="utf-8")


def _function() -> str:
    match = re.search(
        r"create or replace function public\."
        r"cancel_unobserved_exact_telegram_publication\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def test_resolution_is_non_sending_and_can_only_close_a_fenced_unknown() -> None:
    function = _function()
    assert "security definer" in function.lower()
    assert "set search_path = ''" in function
    assert "publication.status <> 'delivery_unknown'" in function
    assert "publication.delivery_attempt_id is null" in function
    assert "publication.delivery_request_sha256 is null" in function
    assert "pg_catalog.date_trunc('milliseconds', publication.delivery_started_at)" in function
    assert "publication.delivery_started_at > statement_timestamp() - interval '10 minutes'" in function
    assert "job.status <> 'failed'" in function
    assert "target_channel_checked is distinct from true" in function
    assert "target_caption_checked is distinct from true" in function
    assert "target_png_checked is distinct from true" in function
    assert "target_public_channel" in function
    assert "'squid_kor_update'" in function
    assert "status = 'cancelled'" in function
    assert "status = 'queued'" not in function
    assert "status = 'retrying'" not in function
    assert "insert into public.jobs" not in function
    assert "telegram" in function.lower()
    assert "sendphoto" not in function.lower()


def test_resolution_preserves_lock_order_and_refuses_an_observed_message() -> None:
    function = _function()
    assert function.index("select content.* into item") < function.index(
        "select queued_job.* into job"
    ) < function.index("select delivery.* into publication")
    assert "pg_catalog.pg_try_advisory_xact_lock(tuple_lock_key)" in function
    assert "pg_catalog.hashtextextended(" in function
    assert "observed.status = 'published'" in function
    assert "^https://t\\.me/squid_kor_update/" in function
    assert "exact Telegram delivery was already observed publicly" in function


def test_resolution_is_idempotent_audited_and_service_role_only() -> None:
    function = _function()
    assert "exact_telegram_delivery_resolution_v1" in function
    assert "confirmed_not_observed_cancelled" in function
    assert "request_idempotency_key" in function
    assert "'reused', true" in function
    assert "exact_telegram_delivery_not_observed_cancelled" in function
    assert "'reviewer_source', 'studio_session'" in function
    signature = (
        "uuid, uuid, uuid, uuid, timestamptz, text, boolean, boolean, "
        "boolean, text"
    )
    escaped = re.escape(signature).replace(r"\ ", r"\s*")
    assert re.search(
        rf"revoke all on function public\."
        rf"cancel_unobserved_exact_telegram_publication\(\s*{escaped}\s*\)\s*"
        rf"from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        rf"grant execute on function public\."
        rf"cancel_unobserved_exact_telegram_publication\(\s*{escaped}\s*\)\s*"
        rf"to service_role;",
        MIGRATION,
        re.DOTALL,
    )


def test_transactional_smoke_covers_resolution_boundaries() -> None:
    for marker in (
        "delivery unknown resolution accepted incomplete operator checks",
        "delivery unknown resolution did not close the exact attempt",
        "delivery unknown resolution replay was not idempotent",
        "delivery unknown resolution accepted a different idempotency key",
        "delivery unknown resolution ignored an observed message",
    ):
        assert marker in SQL_SMOKE
