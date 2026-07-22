"""Safety checks for the shared meme-engine / Content Studio database history."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
LEGACY = next(MIGRATIONS.glob("*_create_daily_memes_schema.sql")).read_text(
    encoding="utf-8"
)
FOUNDATION = next(MIGRATIONS.glob("*_content_studio_foundation.sql")).read_text(
    encoding="utf-8"
)
HARDENING = next(MIGRATIONS.glob("*_harden_legacy_daily_memes.sql")).read_text(
    encoding="utf-8"
)
BOOTSTRAP = next(MIGRATIONS.glob("*_bootstrap_content_studio_workspace.sql")).read_text(
    encoding="utf-8"
)
FK_INDEXES = next(MIGRATIONS.glob("*_add_content_studio_fk_indexes.sql")).read_text(
    encoding="utf-8"
)


def test_recorded_legacy_schema_is_owned_by_this_repository() -> None:
    assert "create table if not exists daily_memes" in LEGACY
    assert "create or replace view memes_awaiting_metrics" in LEGACY
    assert "create index if not exists daily_memes_created_at_idx" in LEGACY


def test_legacy_table_and_view_are_service_only() -> None:
    assert "alter table public.daily_memes enable row level security;" in HARDENING
    assert "security_invoker = true, security_barrier = true" in HARDENING
    assert re.search(
        r"revoke all on table public\.daily_memes\s+"
        r"from public, anon, authenticated, service_role;",
        HARDENING,
    )
    assert re.search(
        r"revoke all on table public\.memes_awaiting_metrics\s+"
        r"from public, anon, authenticated, service_role;",
        HARDENING,
    )
    assert "grant select, insert, update on table public.daily_memes to service_role;" in HARDENING
    assert "grant select on table public.memes_awaiting_metrics to service_role;" in HARDENING
    assert not re.search(r"grant .*\b(delete|truncate)\b", HARDENING)


def test_service_workspace_bootstrap_is_ownerless_but_browser_insert_is_not() -> None:
    owner_trigger = re.search(
        r"create or replace function private\.add_workspace_owner\(\).*?\$\$;",
        FOUNDATION,
        re.DOTALL,
    )
    assert owner_trigger is not None
    assert "if new.created_by is null then" in owner_trigger.group(0)
    assert "return new;" in owner_trigger.group(0)

    browser_policy = re.search(
        r"create policy workspaces_insert_self.*?;",
        FOUNDATION,
        re.DOTALL,
    )
    assert browser_policy is not None
    assert "created_by = (select auth.uid())" in browser_policy.group(0)
    assert "(select auth.uid()) is not null" in browser_policy.group(0)


def test_bootstrap_registers_the_four_clients_without_fake_auth_users() -> None:
    assert "insert into auth.users" not in BOOTSTRAP
    assert "'coineasy-content-studio'" in BOOTSTRAP
    assert "on conflict (slug) do update" in BOOTSTRAP
    assert "on conflict (workspace_id, client_id) do update" in BOOTSTRAP
    for client_id in ("yellow", "origintrail", "squid", "babylon"):
        assert f"('{client_id}'," in BOOTSTRAP
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        BOOTSTRAP,
        re.IGNORECASE,
    )


def test_composite_version_foreign_keys_have_covering_indexes() -> None:
    for table in ("approvals", "assets", "figma_links", "publications"):
        assert re.search(
            rf"create index if not exists {table}_workspace_item_version_idx\s+"
            rf"on public\.{table} \(workspace_id, content_item_id, content_version_id\);",
            FK_INDEXES,
        )
