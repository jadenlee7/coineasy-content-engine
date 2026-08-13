import re
from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20260811160500_grok_qa_verdict_receipts.sql"
).read_text(encoding="utf-8").lower()


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def test_receipt_is_private_and_unique_per_immutable_version():
    assert "create table private.grok_qa_verdict_receipts" in MIGRATION
    assert "primary key (workspace_id, content_version_id)" in MIGRATION
    assert "references public.content_versions" in MIGRATION
    assert "revoke all on table private.grok_qa_verdict_receipts" in MIGRATION
    assert "from public, anon, authenticated, service_role" in MIGRATION


def test_claim_requires_current_nonmock_needs_review_and_never_retries():
    claim = _function("claim_grok_qa_verdict")
    assert "item.status <> 'needs_review'" in claim
    assert "item.current_version_id is distinct from target_content_version_id" in claim
    assert "version.generation_meta -> 'mock_mode' = 'true'::jsonb" in claim
    assert "on conflict (workspace_id, content_version_id) do nothing" in claim
    assert "returning * into receipt" in claim
    assert "'claimed', new_claim" in claim
    assert "duplicate_conflict" in claim
    assert "update private.grok_qa_verdict_receipts" not in claim
    assert "insert into public.approvals" not in claim
    assert "update public.content_items" not in claim


def test_claim_validates_bounded_structured_verdict():
    claim = _function("claim_grok_qa_verdict")
    assert "jsonb_array_length(target_payload -> 'issues') > 3" in claim
    assert "jsonb_array_length(target_payload -> 'fact_check' -> 'checks')" in claim
    assert "jsonb_array_length(target_payload -> 'brand_check' -> 'checks')" in claim
    assert "grok qa pass evidence is incomplete" in claim
    assert "grok qa block evidence is incomplete" in claim
    assert "extensions.digest" in claim


def test_finalize_is_one_way_and_only_service_role_can_call_rpcs():
    finalize = _function("finalize_grok_qa_verdict")
    assert "if receipt.status = 'claimed'" in finalize
    assert "set status = target_outcome" in finalize
    assert "target_outcome not in ('sent', 'failed')" in finalize
    assert "grant execute on function public.claim_grok_qa_verdict" in MIGRATION
    assert "grant execute on function public.finalize_grok_qa_verdict" in MIGRATION
    assert "to service_role" in MIGRATION
    assert "to authenticated" not in MIGRATION.split(
        "grant execute on function public.claim_grok_qa_verdict", 1
    )[1]
