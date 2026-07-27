"""Static security contract for immutable EasyFarm ranking evidence."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260727120000_content_signal_ranking_evidence.sql"
).read_text(encoding="utf-8")


def _rpc() -> str:
    match = re.search(
        r"create or replace function "
        r"public\.record_content_signal_ranking_evidence\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def test_evidence_table_is_private_append_only_and_forward_only():
    assert "create table private.content_signal_ranking_evidence" in MIGRATION
    assert "snapshot_hash ~ '^[a-f0-9]{64}$'" in MIGRATION
    assert "sanitized_signal_envelope jsonb not null" in MIGRATION
    assert "octet_length(sanitized_signal_envelope::text) <= 32768" in MIGRATION
    assert "primary key (" in MIGRATION
    assert "on conflict (" in MIGRATION
    assert "do nothing" in MIGRATION
    assert "update private.content_signal_ranking_evidence" not in MIGRATION
    assert "delete from private.content_signal_ranking_evidence" not in MIGRATION
    assert not re.search(r"\b(drop|truncate)\b", MIGRATION, re.IGNORECASE)
    assert "notify pgrst, 'reload schema';" in MIGRATION
    assert re.search(
        r"revoke all on table private\.content_signal_ranking_evidence\s+"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )


def test_evidence_rpc_is_service_only_and_validates_bounded_sanitized_terms():
    rpc = _rpc()
    assert "security definer" in rpc
    assert "set search_path = ''" in rpc
    assert "jsonb_array_length(target_demand_terms) > 20" in rpc
    assert "octet_length(target_demand_terms::text) > 16384" in rpc
    assert "char_length(term_item ->> 'term') not between 2 and 80" in rpc
    assert "(term_item ->> 'weight')::numeric not between 0 and 1" in rpc
    assert "[1-9A-HJ-NP-Za-km-z]{32,44}" in rpc
    assert "[a-z0-9]{1,20}1[ac-hj-np-z02-9]{20,71}" in rpc
    assert "^[A-Za-z0-9_-]{48,}$" in rpc
    for source in ("community", "telegram_content", "local_x"):
        assert f"'{source}'" in rpc
    for invariant in (
        "'factual_evidence', false",
        "'translation', false",
        "content signal evidence retry does not match",
    ):
        assert invariant in rpc
    signature = (
        "uuid, text, text, text, timestamptz, timestamptz, "
        "timestamptz, text, jsonb"
    )
    escaped = re.escape(signature).replace(r"\ ", r"\s*")
    assert re.search(
        r"revoke all on function "
        r"public\.record_content_signal_ranking_evidence\(\s*"
        + escaped
        + r"\s*\)\s*from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        r"grant execute on function "
        r"public\.record_content_signal_ranking_evidence\(\s*"
        + escaped
        + r"\s*\)\s*to service_role;",
        MIGRATION,
        re.DOTALL,
    )
