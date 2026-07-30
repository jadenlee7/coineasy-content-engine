from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260730120000_monthly_content_kpi.sql"
).read_text(encoding="utf-8")


def test_kpi_counts_exact_published_content_items_in_kst_month():
    assert "create or replace function public.get_monthly_content_kpi" in MIGRATION
    assert "publication.status = 'published'" in MIGRATION
    assert "publication.published_at is not null" in MIGRATION
    assert "count(distinct publication.content_item_id)" in MIGRATION
    assert "at time zone 'Asia/Seoul'" in MIGRATION
    assert "'counting_rule', 'distinct_published_content_item'" in MIGRATION


def test_kpi_contract_has_user_targets_without_claiming_notion_events():
    for target in (
        "'daily_news', month_days",
        "'article', 2",
        "'tutorial', 2",
        "'community_event', 2",
    ):
        assert target in MIGRATION
    assert "'community_event_source', 'notion'" in MIGRATION
    published = re.search(
        r"'published', jsonb_build_object\(.*?\),\s*'gaps_to_target'",
        MIGRATION,
        re.DOTALL,
    )
    assert published is not None
    assert "'community_event'," not in published.group(0)


def test_kpi_rpc_is_read_only_and_service_role_only():
    function = re.search(
        r"create or replace function public\.get_monthly_content_kpi\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert function is not None
    body = function.group(0).lower()
    for mutation in ("insert into", "update public.", "delete from"):
        assert mutation not in body
    assert re.search(
        r"revoke all on function public\.get_monthly_content_kpi\(uuid, date\)\s*"
        r"from public, anon, authenticated;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        r"grant execute on function public\.get_monthly_content_kpi\(uuid, date\)\s*"
        r"to service_role;",
        MIGRATION,
        re.DOTALL,
    )
