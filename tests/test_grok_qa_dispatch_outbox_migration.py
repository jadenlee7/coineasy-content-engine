"""Static safety contract for the durable official-X Grok QA outbox."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "supabase/migrations/20260813143000_grok_qa_dispatch_outbox.sql"
)
SECURITY_TEST_PATH = (
    ROOT / "supabase/tests/grok_qa_dispatch_outbox_security.sql"
)
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
SECURITY_TEST = SECURITY_TEST_PATH.read_text(encoding="utf-8")


RPC_SIGNATURES = {
    "claim_grok_qa_dispatch_job": "uuid, text, integer, text[], uuid",
    "mark_grok_qa_dispatch_provider_attempt": "uuid, uuid, text, text, text",
    "stage_grok_qa_dispatch_verdict": (
        "uuid, uuid, text, jsonb, text, text, text, text, text, bigint, jsonb, "
        "smallint"
    ),
    "complete_grok_qa_dispatch_job": "uuid, uuid, text, text, text, text",
    "fail_grok_qa_dispatch_job": (
        "uuid, uuid, text, text, boolean, timestamptz"
    ),
    "reconcile_grok_qa_dispatch_leases": "uuid, integer",
}


def _function(name: str, *, schema: str = "public") -> str:
    match = re.search(
        rf"create or replace function {schema}[.]{name}[(].*?\n[$][$];",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, name
    return match.group(0)


def test_outbox_is_private_immutable_version_work() -> None:
    lowered = MIGRATION.lower()
    assert "create table private.grok_qa_dispatch_outbox" in lowered
    assert "primary key (workspace_id, content_version_id)" in lowered
    assert "unique (workspace_id, source_event_id)" in lowered
    assert "source_event_id between 1 and 9007199254740991" in lowered
    assert "references public.content_versions" in lowered
    assert "enable row level security" in lowered
    assert "force row level security" in lowered
    assert re.search(
        r"revoke all on table private[.]grok_qa_dispatch_outbox\s*"
        r"from public, anon, authenticated, service_role;",
        lowered,
    )
    assert "grant select on" not in lowered
    assert "content_kind text not null check (content_kind = 'daily_news')" in lowered


def test_exact_two_completion_events_enqueue_atomically_without_backfill() -> None:
    trigger = re.search(
        r"create trigger enqueue_official_x_grok_qa_dispatch\s*"
        r"after insert on public[.]event_log.*?execute function",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )
    assert trigger is not None
    enqueue = _function("enqueue_official_x_grok_qa_dispatch", schema="private")
    for event_type in (
        "official_x_review_draft_completed",
        "origintrail_batch_review_pack_materialized",
    ):
        assert event_type in trigger.group(0)
        assert event_type in enqueue
    assert "insert into private.grok_qa_dispatch_outbox" in enqueue
    assert "source_event_id, source_event_type" in enqueue
    assert "source_published_at" in enqueue
    assert "on conflict (workspace_id, content_version_id) do nothing" in enqueue
    assert not re.search(
        r"insert into private[.]grok_qa_dispatch_outbox\s*[(][^;]+[)]\s*select",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )


def test_enqueue_and_claim_pin_and_revalidate_exact_official_x_source() -> None:
    enqueue = _function("enqueue_official_x_grok_qa_dispatch", schema="private")
    claim = _function("claim_grok_qa_dispatch_job")
    for body in (enqueue, claim):
        assert "link.position = 0" in body
        assert "source.source_type = 'tweet'" in body
        assert "feed.provider = 'x'" in body
        assert "feed.active is true" in body
        assert "primary_source_count" in body
        assert "source_author_handle" in body
        assert "source_published_at" in body
        assert "split_part(primary_source.canonical_url, '/', 6)" in body
    assert "primary_source.id is distinct from dispatch.source_item_id" in claim
    assert "primary_source.canonical_url is distinct from dispatch.source_url" in claim
    assert re.search(
        r"primary_source[.]published_at\s+is distinct from\s+"
        r"dispatch[.]source_published_at",
        claim,
    )


def test_claim_is_client_scoped_and_has_a_bounded_lease() -> None:
    claim = _function("claim_grok_qa_dispatch_job")
    assert "target_lease_seconds not between 180 and 600" in claim
    assert "cardinality(target_allowed_clients) not between 1 and 4" in claim
    assert "count(distinct allowed.client_id)" in claim
    assert "queued.client_id = any(target_allowed_clients)" in claim
    assert "target_canary_content_version_id uuid default null" in claim
    assert "queued.content_version_id = target_canary_content_version_id" in claim
    assert "for update skip locked" in claim.lower()
    assert "then attempts + 1 else attempts end" in claim
    assert "while scan_count < 32 loop" in claim
    assert "return public.claim_grok_qa_dispatch_job(" not in claim
    assert "'provider_call_required', dispatch.verdict is null" in MIGRATION
    assert "'source_url', dispatch.source_url" in MIGRATION
    assert "'source_published_at', dispatch.source_published_at" in MIGRATION


def test_provider_attempt_fence_is_commit_once_and_never_retried() -> None:
    attempt = _function("mark_grok_qa_dispatch_provider_attempt")
    failure = _function("fail_grok_qa_dispatch_job")
    reconcile = _function("reconcile_grok_qa_dispatch_leases")
    assert "provider_input_sha256 = target_input_sha256" in attempt
    assert "banner_sha256 = target_banner_sha256" in attempt
    assert "provider_attempt_started_at = statement_timestamp()" in attempt
    assert "'authorized_once', authorized_once" in attempt
    assert "authorized_once := true" in attempt
    assert "provider attempt input conflicts" in attempt.lower()
    assert "item.status is distinct from 'needs_review'" in attempt
    assert "item.current_version_id is distinct from dispatch.content_version_id" in attempt
    assert "link.position = 0" in attempt
    assert "source.source_type = 'tweet'" in attempt
    assert "feed.provider = 'x'" in attempt
    assert "feed.handle = expected_handle" in attempt
    assert "feed.active is true" in attempt
    assert "for share of link" in attempt
    assert "for share of source" in attempt
    assert "for share of feed" in attempt
    assert "for key share of link, source, feed" not in attempt
    assert "primary_source.id is distinct from dispatch.source_item_id" in attempt
    assert "primary_source.canonical_url is distinct from dispatch.source_url" in attempt
    assert "primary_source.published_at" in attempt
    assert attempt.index("for share of link") < attempt.index(
        "select count(*) into primary_source_count"
    )
    assert "candidate.sha256 = target_banner_sha256" in attempt
    assert "for share of candidate, stored" in attempt
    assert "set status = 'obsolete'" in attempt
    assert "'authorized_once', false" in attempt
    assert "dispatch.provider_attempt_started_at is not null" in failure
    assert "next_status := 'provider_unknown'" in failure
    assert "dispatch.provider_attempt_started_at is not null" in reconcile
    assert "next_status := 'provider_unknown'" in reconcile
    assert "provider_unknown' then 'pending" not in MIGRATION.lower()


def test_staged_result_is_delivery_only_reclaimable_at_max_attempts() -> None:
    claim = _function("claim_grok_qa_dispatch_job")
    reconcile = _function("reconcile_grok_qa_dispatch_leases")
    assert "'staged'" in re.search(
        r"status in [(].*?[)]", MIGRATION, re.DOTALL
    ).group(0)
    assert "queued.status = 'staged'" in claim
    assert "queued.status = 'claimed'" in claim
    assert "queued.verdict is not null" in claim
    assert "case when provider_call_required" in claim
    assert "then attempts + 1 else attempts end" in claim
    assert "next_status := 'staged'" in reconcile
    assert "next_status in ('pending', 'staged')" in reconcile


def test_stage_persists_strict_bounded_provider_evidence_before_relay() -> None:
    stage = _function("stage_grok_qa_dispatch_verdict")
    citations = _function("grok_qa_dispatch_citations_valid", schema="private")
    verdict_valid = _function("grok_qa_dispatch_verdict_valid", schema="private")
    assert "target_model is distinct from 'grok-4.5'" in stage
    assert "target_prompt_version is distinct from 'official-x-grok-qa@1'" in stage
    assert "target_input_sha256" in stage
    assert "target_banner_sha256" in stage
    assert "dispatch.provider_input_sha256" in stage
    assert "dispatch.banner_sha256" in stage
    assert "target_cost_in_usd_ticks not between 0 and 5000000000" in stage
    assert "target_x_search_calls not between 1 and 3" in stage
    assert "target_provider_response_id" in stage
    assert "provider_response_id = target_provider_response_id" in stage
    assert "x_search_citations = target_x_search_citations" in stage
    assert "jsonb_build_array(dispatch.source_url)" in stage
    assert "target_verdict ->> 'decision' = 'PASS'" in stage
    assert "target_verdict ->> 'decision' <> 'PASS'" in stage
    assert "issue.value ->> 'evidence_url'" in stage
    assert "jsonb_array_length(target_citations) not between 1 and 8" in citations
    assert "lower(rtrim(citation, '/')) is distinct from" in citations
    assert "lower(target_source_url)" in citations
    assert "'https://x.com/i/status/' || lower(source_post_id)" in citations
    assert "raw_provider" not in MIGRATION.lower()
    assert "provider_response_body" not in MIGRATION.lower()
    assert "count(*) from jsonb_object_keys(issue)) = 3" in verdict_valid
    assert "count(*) from jsonb_object_keys(issue)) = 4" in verdict_valid
    assert "and issue ? 'evidence_url'" in verdict_valid


def test_complete_requires_matching_staged_evidence_and_relay_receipt() -> None:
    complete = _function("complete_grok_qa_dispatch_job")
    assert "dispatch.model is distinct from 'grok-4.5'" in complete
    assert "dispatch.provider_attempt_started_at is null" in complete
    assert "dispatch.provider_response_id is null" in complete
    assert "receipt.payload_sha256 is distinct from target_verdict_sha256" in complete
    assert "receipt.status is distinct from 'sent'" in complete
    assert "receipt.status is distinct from 'failed'" in complete


def test_all_dispatch_rpcs_are_service_role_only_and_hardened() -> None:
    for name, signature in RPC_SIGNATURES.items():
        function = _function(name)
        assert "security definer" in function.lower()
        assert "set search_path = ''" in function
        escaped = re.escape(signature).replace(r"\ ", r"\s*")
        assert re.search(
            rf"revoke all on function public[.]{name}[(]\s*{escaped}\s*[)]\s*"
            rf"from public, anon, authenticated, service_role;",
            MIGRATION,
            re.DOTALL,
        )
        assert re.search(
            rf"grant execute on function public[.]{name}[(]\s*{escaped}\s*[)]\s*"
            rf"to service_role;",
            MIGRATION,
            re.DOTALL,
        )


def test_dispatch_cannot_approve_publish_export_or_store_credentials() -> None:
    lowered = MIGRATION.lower()
    for forbidden in (
        "insert into public.approvals",
        "insert into public.publications",
        "update public.content_items",
        "job_kind = 'publish'",
        "job_kind = 'figma_export'",
        "telegram_bot_token",
        "xai_api_key",
        "studio_access_token",
        "api_secret",
    ):
        assert forbidden not in lowered


def test_transactional_security_fixture_covers_state_and_side_effect_fences() -> None:
    lowered = SECURITY_TEST.lower()
    assert lowered.startswith("-- transactional security")
    assert lowered.rstrip().endswith("rollback;")
    for fragment in (
        "allowed_clients leaked a squid dispatch",
        "exact obsolete canary fell through to another row",
        "exact canary claim selected the wrong row",
        "stale final source revalidation authorized provider",
        "provider attempt fence was not commit-once",
        "provider attempt was incorrectly retried",
        "provider evidence was not persisted before relay",
        "staged verdict was not safely replay-claimed",
        "foreign verdict source evidence was accepted",
        "non-official x_search citation was accepted",
        "advisory qa dispatch changed approval/publication state",
    ):
        assert fragment in lowered
