"""Static security checks for generated-content catalog RPCs.

Database integration coverage lives in
``supabase/tests/content_generation_catalog_security.sql``. These checks catch
dangerous privilege and validation regressions without a linked Supabase project.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260722125814_content_generation_catalog.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
DB_SECURITY_TEST = (
    ROOT
    / "supabase"
    / "tests"
    / "content_generation_catalog_security.sql"
).read_text(encoding="utf-8")

RPC_SIGNATURES = {
    "record_generated_content": (
        "uuid, uuid, text, text, text, jsonb, jsonb, jsonb, jsonb, text"
    ),
    "get_generated_content": "uuid, uuid, text, text",
    "list_content_library": (
        "uuid, text, text, text, integer, timestamptz, uuid"
    ),
    "get_content_library_item": "uuid, uuid",
}


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert match is not None, name
    return match.group(0)


def test_migration_is_forward_only_and_adds_cursor_index() -> None:
    assert "create table" not in SQL
    assert "alter table" not in SQL
    assert "drop " not in SQL
    assert (
        "on public.content_items (workspace_id, created_at desc, id desc);"
        in SQL
    )


def test_every_catalog_rpc_is_hardened_and_service_role_only() -> None:
    for name, signature in RPC_SIGNATURES.items():
        body = _function(name)
        assert "security definer" in body
        assert "set search_path = ''" in body
        assert re.search(
            rf"revoke all on function public\.{name}\(\s*"
            + re.escape(signature).replace(r"\ ", r"\s*")
            + r"\s*\)\s+from public, anon, authenticated, service_role;",
            SQL,
            re.DOTALL,
        )
        assert re.search(
            rf"grant execute on function public\.{name}\(\s*"
            + re.escape(signature).replace(r"\ ", r"\s*")
            + r"\s*\)\s+to service_role;",
            SQL,
            re.DOTALL,
        )

    assert "to anon" not in SQL
    assert "to authenticated" not in SQL


def test_record_rpc_is_strictly_idempotent_and_workspace_scoped() -> None:
    body = _function("record_generated_content")
    assert "target_content_kind not in ('daily_news', 'article')" in body
    assert "from public.workspace_clients" in body
    assert "active is true" in body
    assert "target_content ->> 'request_hash'" in body
    assert "target_generation_meta ->> 'request_hash'" in body
    assert "effective_generation_meta" in body
    assert "pg_catalog.pg_advisory_xact_lock" in body
    assert "version_number = 1" in body
    assert "is distinct from target_content" in body
    assert "is distinct from target_channel_copy" in body
    assert "is distinct from effective_generation_meta" in body
    assert "content request retry does not match committed content" in body
    assert "content request retry does not match committed assets" in body
    assert "'needs_review'" in body
    assert "'content_generated'" in body


def test_news_requires_one_private_png_and_article_requires_no_asset() -> None:
    body = _function("record_generated_content")
    assert "daily news requires exactly one complete PNG asset" in body
    assert "target_asset - array[" in body
    assert "file_name is distinct from 'news-card.png'" in body
    assert "target_asset ->> 'mime_type' is distinct from 'image/png'" in body
    assert "path_parts[1] <> target_workspace_id::text" in body
    assert "path_parts[2] <> target_client_id" in body
    assert "path_parts[3] <> asset_id::text" in body
    assert "from storage.objects" in body
    assert "for key share" in body
    assert "articles cannot include generated assets" in body
    assert "elsif existing_asset_count <> 0" in body
    assert "'primary_asset_id', asset_id::text" in body


def test_retry_lookup_fails_closed_on_catalog_or_storage_drift() -> None:
    body = _function("get_generated_content")
    assert "version_number = 1" in body
    assert "committed generated content has invalid request metadata" in body
    assert "catalog_asset_count <> 1" in body
    assert "join storage.objects as stored" in body
    assert "a.metadata ->> 'filename' = 'news-card.png'" in body
    assert "committed daily news asset is invalid or missing" in body
    assert "catalog_asset_count <> 0" in body
    assert "'asset', asset_row" in body


def test_library_list_is_bounded_filtered_and_uses_stable_cursor() -> None:
    body = _function("list_content_library")
    assert "target_limit not between 1 and 100" in body
    assert "content library cursor is incomplete" in body
    assert "target_content_kind not in ('daily_news', 'article', 'tutorial')" in body
    assert "target_status not in" in body
    assert "(item.created_at, item.id)" in body
    assert "< (target_before_created_at, target_before_id)" in body
    assert "order by item.created_at desc, item.id desc" in body
    assert "limit target_limit + 1" in body
    assert "'next_cursor'" in body
    assert "'primary_asset_id'" in body
    assert "storage_path" not in body


def test_library_detail_is_workspace_scoped_and_storage_checked() -> None:
    body = _function("get_content_library_item")
    assert "workspace_id = target_workspace_id" in body
    assert "item.current_version_id" in body
    assert "join storage.objects as stored" in body
    assert "resolved_asset_count <> catalog_asset_count" in body
    assert "content library item has missing Storage objects" in body
    assert "from public.figma_links as link" in body
    assert "'assets', asset_rows" in body
    assert "'figma_links', figma_rows" in body


def test_database_security_smoke_covers_privileges_and_tampering() -> None:
    for role in ("anon", "authenticated", "service_role"):
        assert f"has_function_privilege('{role}'" in DB_SECURITY_TEST
    for function in RPC_SIGNATURES:
        assert function in DB_SECURITY_TEST
    for case in (
        "generated catalog RPC leaked to anon",
        "generated catalog RPC leaked to authenticated",
        "daily news asset path escaped its workspace",
        "daily news without a Storage object was accepted",
        "article accepted a generated asset",
        "generated content retry accepted a different request hash",
        "generated content lookup accepted a missing Storage object",
        "catalog list cursor repeated or skipped its boundary",
    ):
        assert case in DB_SECURITY_TEST
