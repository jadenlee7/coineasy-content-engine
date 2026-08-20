"""Static contract for additive official-X demand ranking v2 support."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260820183000_content_signal_ranking_v2.sql"
).read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing {name}"
    return match.group(0)


def test_ranking_v2_extension_is_additive_and_keeps_v1():
    assert "create table" not in MIGRATION.lower()
    assert "drop table" not in MIGRATION.lower()
    assert "delete from private.content_signal_ranking_evidence" not in MIGRATION
    assert "update private.content_signal_ranking_evidence" not in MIGRATION
    assert "content_signal_ranking_evidence_ranking_version_check" in MIGRATION
    assert "ranking_version in (" in MIGRATION
    assert "'official-x-demand-v1'" in MIGRATION
    assert "'official-x-demand-v2'" in MIGRATION


def test_ranking_rpc_accepts_v2_only_with_evidence_schema_11():
    function = _function("record_content_signal_ranking_evidence")
    assert "target_schema_version not in ('1.0', '1.1')" in function
    assert "target_ranking_version not in (" in function
    assert "target_ranking_version = 'official-x-demand-v2'" in function
    assert "target_schema_version <> '1.1'" in function
    assert "'ranking_version', target_ranking_version" in function
    assert "content signal evidence retry does not match" in function


def test_promotion_receipt_can_link_to_v1_or_v2_ranking_evidence():
    function = _function("record_content_promotion_candidates")
    assert "target_schema_version is distinct from '1.1'" in function
    assert "ranking.schema_version = target_schema_version" in function
    assert "ranking.ranking_version in (" in function
    assert "'official-x-demand-v1', 'official-x-demand-v2'" in function
    assert "promotion candidates lack committed ranking evidence" in function


def test_v2_rpcs_remain_service_role_only():
    signature = (
        "uuid, text, text, text, timestamptz, timestamptz, "
        "timestamptz, text, jsonb"
    )
    escaped = re.escape(signature).replace(r"\ ", r"\s*")
    for name in (
        "record_content_signal_ranking_evidence",
        "record_content_promotion_candidates",
    ):
        assert re.search(
            rf"revoke all on function public\.{name}\(\s*"
            + escaped
            + r"\s*\) from public, anon, authenticated, service_role;",
            MIGRATION,
            re.DOTALL,
        )
        assert re.search(
            rf"grant execute on function public\.{name}\(\s*"
            + escaped
            + r"\s*\) to service_role;",
            MIGRATION,
            re.DOTALL,
        )
    assert "notify pgrst, 'reload schema';" in MIGRATION
