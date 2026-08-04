from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804180000_origintrail_x_article_evidence.sql"
).read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def test_article_evidence_is_private_append_only_and_source_bound() -> None:
    assert "create table private.origintrail_x_article_evidence" in MIGRATION
    assert "force row level security" in MIGRATION
    assert re.search(
        r"revoke all on table private\.origintrail_x_article_evidence\s+"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
    )
    assert "before update or delete" in MIGRATION
    assert "OriginTrail X Article evidence is immutable" in MIGRATION
    assert (
        "references public.source_items(workspace_id, client_id, id)"
        in MIGRATION
    )
    assert "check (client_id = 'origintrail')" in MIGRATION


def test_origintrail_intake_verifies_article_identity_and_content_hash() -> None:
    intake = _function("record_origintrail_nonquote_sources")

    assert "paired.item -> 'article_evidence'" in intake
    assert "'https://x.com/i/article/'" in intake
    assert "extensions.digest(" in intake
    assert "pg_catalog.convert_to(btrim(source_content), 'UTF8')" in intake
    assert "'sha256'" in intake
    assert "source.body = btrim(source_content)" in intake
    assert "position('[X Article]' in source_content)" in intake
    assert "OriginTrail X Article evidence hash does not match" in intake
    assert "on conflict (source_item_id) do nothing" in intake
    assert "OriginTrail X Article evidence retry does not match" in intake


def test_article_intake_rpc_remains_service_role_only() -> None:
    signature = "uuid, text, text, uuid, text, text, jsonb, timestamptz"
    escaped = re.escape(signature).replace(r"\ ", r"\s*")
    assert re.search(
        r"revoke all on function public\.record_origintrail_nonquote_sources\("
        rf"\s*{escaped}\s*\)\s*"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        r"grant execute on function public\.record_origintrail_nonquote_sources\("
        rf"\s*{escaped}\s*\)\s*to service_role;",
        MIGRATION,
        re.DOTALL,
    )


def test_article_snapshot_is_not_duplicated_in_the_sidecar() -> None:
    table = MIGRATION.split(
        "create table private.origintrail_x_article_evidence",
        1,
    )[1].split(");", 1)[0]
    assert "plain_text" not in table
    assert "source_content_sha256" in table
