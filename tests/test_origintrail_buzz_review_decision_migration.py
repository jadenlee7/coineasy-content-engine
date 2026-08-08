from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "supabase/migrations/20260808130000_origintrail_buzz_review_decisions.sql"
).read_text()
ROLE_MIGRATION = (
    ROOT / "supabase/migrations/20260808133000_buzz_review_decider_role.sql"
).read_text()


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public[.]{name}[(].*?\n[$][$];",
        MIGRATION,
        re.DOTALL,
    )
    assert match, name
    return match.group(0)


def test_decision_table_is_private_force_rls_and_immutable():
    assert "alter table agent_runtime.buzz_review_decisions force row level security" in MIGRATION
    assert "from public, anon, authenticated, service_role" in MIGRATION
    assert "before update or delete" in MIGRATION
    assert "Buzz review decisions are immutable" in MIGRATION
    assert "grant select on" not in MIGRATION.lower()


def test_list_is_delivered_origintrail_needs_review_only():
    function = _function("list_origintrail_buzz_review_targets")
    for fragment in (
        "delivery.status = 'delivered'",
        "private.origintrail_buzz_review_evidence_ready",
        "target_protocol_start_epoch",
        "delivery.message_sha256",
        "decision.job_id is null",
        "limit target_limit",
    ):
        assert fragment in function


def test_record_recomputes_hash_and_is_first_decision_wins():
    function = _function("record_origintrail_buzz_review_decision")
    assert "private.origintrail_buzz_review_command_sha256" in function
    assert "pg_advisory_xact_lock" in function
    assert "existing.reason is distinct from target_reason" in function
    assert "using errcode = '23505'" in function
    assert "'reused', true" in function
    assert "'reused', false" in function
    assert "target_command_created_at_epoch < target_protocol_start_epoch" in function
    assert "receipt.message_sha256 = target_message_sha256" in function


def test_shared_evidence_gate_rejects_url_only_and_unverified_sources():
    function = re.search(
        r"create or replace function private[.]origintrail_buzz_review_evidence_ready"
        r"[(].*?\n[$][$];",
        MIGRATION,
        re.DOTALL,
    )
    assert function
    body = function.group(0)
    for fragment in (
        "source_snapshot_complete",
        "origintrail_standalone_sources",
        "origintrail_x_article_evidence",
        "evidence.source_content_sha256 = computed_source_sha256",
        "origintrail_batch_review_packs",
        "origintrail_review_pack_sha256",
        "regexp_replace",
        "not between 1 and 1024",
    ):
        assert fragment in body


def test_decider_role_has_exactly_two_rpc_grants_and_no_table_access():
    routines = set(re.findall(r"'public[.]([a-z_]+)[(]", ROLE_MIGRATION))
    assert routines == {
        "list_origintrail_buzz_review_targets",
        "record_origintrail_buzz_review_decision",
    }
    assert "revoke all on table agent_runtime.buzz_review_decisions" in ROLE_MIGRATION
    assert "nologin noinherit nobypassrls" in ROLE_MIGRATION
    assert "grant coineasy_buzz_review_decider to authenticator" in ROLE_MIGRATION


def test_decision_boundary_has_no_publish_regeneration_or_provider_transition():
    lowered = MIGRATION.lower()
    for forbidden in (
        "content_publications",
        "queue_agent_batch_job",
        "openai_api_key",
        "telegram_bot_token",
        "request_studio_telegram_publication",
        "result_code = 'approved'",
    ):
        assert forbidden not in lowered
