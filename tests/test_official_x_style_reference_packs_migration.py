"""Static security contract for immutable official-X style references."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260727165803_official_x_style_reference_packs.sql"
).read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing {name}"
    return match.group(0)


def test_pack_is_private_bounded_and_immutable():
    assert "create table private.official_x_style_reference_packs" in MIGRATION
    assert "jsonb_array_length(style_references) between 0 and 3" in MIGRATION
    assert "octet_length(style_references::text) <= 8192" in MIGRATION
    assert "before update or delete" in MIGRATION
    assert re.search(
        r"revoke all on table private\.official_x_style_reference_packs\s+"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
    )
    assert MIGRATION.rstrip().endswith("commit;")


def test_pack_selects_prior_posts_from_the_exact_official_feed_only():
    function = _function("get_or_create_official_x_style_reference_pack")
    for handle in ("@Yellow", "@origin_trail", "@SquidRouter", "@babylonlabs_io"):
        assert f"'{handle}'" in function
    assert "feed.id = primary_source.source_feed_id" in function
    assert "source.id <> primary_source.id" in function
    assert "source.published_at < primary_source.published_at" in function
    assert "limit target_reference_limit" in function
    assert "pg_advisory_xact_lock" in function
    assert "style reference retry changed the primary source" in function


def test_pack_never_creates_factual_links_or_publication_work():
    function = _function("get_or_create_official_x_style_reference_pack")
    assert "content_source_links" not in function
    assert "public.publications" not in function
    assert "insert into public.jobs" not in function
    assert "update public.source_items" not in function


def test_pack_rpc_is_service_role_only():
    signature = "uuid, text, uuid, uuid, integer"
    escaped = re.escape(signature).replace(r"\ ", r"\s*")
    assert re.search(
        rf"revoke all on function "
        rf"public\.get_or_create_official_x_style_reference_pack\(\s*"
        rf"{escaped}\s*\)\s*"
        rf"from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        rf"grant execute on function "
        rf"public\.get_or_create_official_x_style_reference_pack\(\s*"
        rf"{escaped}\s*\)\s*"
        rf"to service_role;",
        MIGRATION,
        re.DOTALL,
    )
