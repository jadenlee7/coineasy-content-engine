from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations/20260810160000_origintrail_standalone_text_review_evidence.sql"
).read_text()
ROLE_MIGRATION = (
    ROOT
    / "supabase/migrations/20260810161000_origintrail_review_evidence_roles.sql"
).read_text()


def _function(schema: str, name: str) -> str:
    match = re.search(
        rf"create or replace function {schema}[.]{name}[(].*?\n[$][$];",
        MIGRATION,
        re.DOTALL,
    )
    assert match, name
    return match.group(0)


def test_source_evidence_is_bound_to_standalone_marker_and_first_poll_receipt():
    function = _function("private", "origintrail_source_evidence_kind")
    for fragment in (
        "private.origintrail_standalone_sources",
        "private.official_x_poll_receipts",
        "marker.first_poll_request_id = target_first_poll_request_id",
        "target_source_item_id = any(receipt.source_item_ids)",
        "current_source.canonical_url = 'https://x.com/origin_trail/status/'",
        "source.media = '[]'::jsonb",
        "regexp_replace",
        "return 'x_post_text'",
        "return 'x_article'",
    ):
        assert fragment in function
    assert "revoke all on function private.origintrail_source_evidence_kind" in MIGRATION


def test_review_item_uses_one_shared_evidence_predicate_and_preserves_text_only_gate():
    function = _function("public", "get_agent_batch_review_item")
    assert "private.origintrail_source_evidence_kind" in function
    assert "'source_evidence_kind', source_evidence.kind" in function
    assert "source_evidence.kind is not null" in function
    assert "origintrail_review_is_text_only" in function
    assert "source_snapshot_complete" in function
    assert "content_sha256" in function


def test_buzz_review_gate_delegates_to_review_item_and_requires_bound_png_pack():
    function = _function("private", "origintrail_buzz_review_evidence_ready")
    assert "public.get_agent_batch_review_item" in function
    assert "not in ('x_article', 'x_post_text')" in function
    assert "agent_runtime.origintrail_batch_review_packs" in function
    assert "origintrail_review_pack_sha256" in function
    assert "private.origintrail_x_article_evidence" not in function


def test_expansion_cannot_publish_send_or_create_a_batch():
    lowered = MIGRATION.lower()
    for forbidden in (
        "insert into public.publications",
        "insert into agent_runtime.batch_jobs",
        "queue_agent_batch_job",
        "openai_api_key",
        "buzz_delivery_receipts",
        "telegram_bot_token",
    ):
        assert forbidden not in lowered


def test_final_role_guard_preserves_rpc_only_access():
    assert "get_agent_batch_review_item(uuid,uuid)" in ROLE_MIGRATION
    assert "list_origintrail_buzz_review_targets" in ROLE_MIGRATION
    assert "record_origintrail_buzz_review_decision" in ROLE_MIGRATION
    assert "revoke all on function private.origintrail_source_evidence_kind" in ROLE_MIGRATION
    assert "revoke all on table public.source_items" in ROLE_MIGRATION
