"""Static contract for the normal-FIFO Grok QA source freshness fence."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations/20260826193000_grok_qa_dispatch_freshness_fence.sql"
).read_text(encoding="utf-8")
SECURITY_TEST = (
    ROOT / "supabase/tests/grok_qa_dispatch_outbox_security.sql"
).read_text(encoding="utf-8")


def test_migration_is_atomic_and_removes_the_unfenced_rpc_signature() -> None:
    lowered = MIGRATION.lower()
    assert lowered.startswith("-- fail closed")
    assert re.search(r"\nbegin;\s", lowered)
    assert lowered.rstrip().endswith("commit;")
    assert re.search(r"notify pgrst, 'reload schema';\s*commit;\s*$", lowered)
    assert re.search(
        r"drop function public[.]claim_grok_qa_dispatch_job[(]\s*"
        r"uuid,\s*text,\s*integer,\s*text\[\],\s*uuid\s*[)];",
        lowered,
    )


def test_new_claim_rpc_requires_a_bounded_source_age() -> None:
    assert "target_max_source_age_seconds integer" in MIGRATION
    assert "target_max_source_age_seconds is null" in MIGRATION
    assert "target_max_source_age_seconds not between 300 and 604800" in MIGRATION
    assert "make_interval(secs => target_max_source_age_seconds)" in MIGRATION
    assert "queued.source_published_at >= statement_timestamp()" in MIGRATION
    assert "queued.source_published_at <= statement_timestamp()" in MIGRATION


def test_normal_fifo_is_fenced_but_exact_uuid_canary_is_preserved() -> None:
    assert re.search(
        r"source_published_at <= statement_timestamp[(][)]\s*"
        r"[+] interval '5 minutes'\s*and [(]\s*"
        r"target_canary_content_version_id is not null\s*or "
        r"queued[.]source_published_at >= statement_timestamp[(][)]",
        MIGRATION,
    )
    assert "future-dated exact canary bypassed clock-skew fence" in SECURITY_TEST
    assert "normal FIFO freshness fence or exact canary bypass failed" in SECURITY_TEST


def test_stale_prefix_is_terminalized_without_a_claim_or_attempt() -> None:
    maintenance = re.search(
        r"if target_canary_content_version_id is null then\s*"
        r"with stale_work as [(].*?end if;",
        MIGRATION,
        re.DOTALL,
    )
    assert maintenance is not None
    body = maintenance.group(0)
    assert "queued.status = 'pending'" in body
    assert "queued.provider_attempt_started_at is null" in body
    assert "queued.verdict is null" in body
    assert "limit 32" in body.lower()
    assert "for update of queued skip locked" in body.lower()
    assert "then 'obsolete'" in body
    assert "else 'failed'" in body
    assert "grok_qa_source_expired" in body
    assert "attempts =" not in body
    assert "target_canary_content_version_id is not null" in MIGRATION
    assert "queued.status = 'obsolete'" in MIGRATION


def test_stale_delivery_only_work_reconciles_receipts_without_relay() -> None:
    for fragment in (
        "left join private.grok_qa_verdict_receipts",
        "grok_qa_receipt_payload_conflict",
        "when stale_work.receipt_status = 'sent' then 'sent'",
        "when stale_work.receipt_status = 'failed' then 'failed'",
        "grok_qa_receipt_claimed",
        "stale claimed verdict was not terminalized without relay",
        "stale staged verdict was not terminalized without relay",
        "stale claimed receipt was not reconciled as delivery_unknown",
        "stale failed receipt was not reconciled exactly",
        "stale staged sent receipt was not reconciled without relay",
        "stale pending manual receipt was not imported exactly",
        "grok-qa-external-receipt@1",
    ):
        assert fragment in MIGRATION or fragment in SECURITY_TEST


def test_only_the_fenced_signature_is_granted_to_service_role() -> None:
    signature = "uuid, text, integer, text[], integer, uuid"
    escaped = re.escape(signature).replace(r"\ ", r"\s*")
    assert re.search(
        rf"revoke all on function public[.]claim_grok_qa_dispatch_job[(]\s*"
        rf"{escaped}\s*[)]\s*from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        rf"grant execute on function public[.]claim_grok_qa_dispatch_job[(]\s*"
        rf"{escaped}\s*[)]\s*to service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert "legacy Grok QA claim RPC bypass remains available" in SECURITY_TEST
