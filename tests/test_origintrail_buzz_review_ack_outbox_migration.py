from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations/20260811160000_origintrail_buzz_review_ack_outbox.sql"
).read_text()
ROLE_MIGRATION = (
    ROOT / "supabase/migrations/20260811161000_buzz_review_ack_role.sql"
).read_text()
SECURITY_TEST = (
    ROOT / "supabase/tests/origintrail_buzz_review_ack_outbox_security.sql"
).read_text()


def _function(name: str, *, schema: str = "public") -> str:
    match = re.search(
        rf"create or replace function {schema}[.]{name}[(].*?\n[$][$];",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, name
    return match.group(0)


def test_ack_receipt_is_one_private_force_rls_row_per_decision():
    assert "create table agent_runtime.buzz_review_ack_receipts" in MIGRATION
    assert "primary key (workspace_id, job_id)" in MIGRATION
    assert (
        "references agent_runtime.buzz_review_decisions(workspace_id, job_id)"
        in MIGRATION
    )
    assert "unique (decision_event_id)" in MIGRATION
    assert (
        "alter table agent_runtime.buzz_review_ack_receipts force row level security"
        in MIGRATION
    )
    assert "from public, anon, authenticated, service_role" in MIGRATION
    assert "grant select on" not in MIGRATION.lower()


def test_forward_only_record_rpc_is_atomic_without_trigger_or_backfill():
    function = _function("record_origintrail_buzz_review_decision_with_ack")
    assert "public.record_origintrail_buzz_review_decision(" in function
    assert "insert into agent_runtime.buzz_review_ack_receipts" in function
    assert "if (decision_result ->> 'reused')::boolean then" in function
    assert "acknowledgement was not enqueued" in function
    assert "'acknowledgement_status', receipt.status" in function
    assert "create trigger" not in MIGRATION.lower()
    assert not re.search(
        r"insert into agent_runtime[.]buzz_review_ack_receipts\s*[(][^;]+[)]\s*select",
        MIGRATION,
        re.IGNORECASE,
    )


def test_database_owns_fixed_safe_message_and_message_hash():
    message = _function(
        "origintrail_buzz_review_ack_message", schema="private"
    )
    message_hash = _function(
        "origintrail_buzz_review_ack_message_sha256", schema="private"
    )
    for fragment in (
        "✅ 게시 승인 접수",
        "자동 발행: OFF",
        "🛠 수정 요청 접수",
        "자동 재생성·발행: OFF",
    ):
        assert fragment in message
    assert "target_reason" not in message
    assert "extensions.digest" in message_hash
    assert "pg_catalog.convert_to(target_message, 'UTF8')" in message_hash
    assert "message = private.origintrail_buzz_review_ack_message(decision)" in MIGRATION
    assert "message_sha256 =" in MIGRATION
    assert "octet_length(message) between 1 and 1024" in MIGRATION
    assert "position('@' in message) = 0" in MIGRATION
    assert "position('nostr:npub1' in lower(message)) = 0" in MIGRATION


def test_request_hash_is_null_until_single_attempt_binding():
    record = _function("record_origintrail_buzz_review_decision_with_ack")
    claim = _function("claim_origintrail_buzz_review_ack")
    attempt = _function("mark_origintrail_buzz_review_ack_attempt")
    assert "acknowledgement_message_sha256,\n        null, 'pending'" in record
    assert "'message', receipt.message" in MIGRATION
    assert "'request_sha256', receipt.request_sha256" in MIGRATION
    assert "receipt.message_sha256 is distinct from lower(target_message_sha256)" in attempt
    assert "receipt.request_sha256 is not null" in attempt
    assert "request_sha256 = lower(target_request_sha256)" in attempt
    assert "'authorized_once', true" in attempt
    assert "'authorized_once', false" in attempt
    assert "private.origintrail_buzz_review_ack_object" in claim


def test_claim_complete_and_reconcile_are_lease_bounded_and_idempotent():
    claim = _function("claim_origintrail_buzz_review_ack")
    complete = _function("complete_origintrail_buzz_review_ack")
    reconcile = _function("reconcile_origintrail_buzz_review_ack_leases")
    unknown = _function("list_origintrail_buzz_review_ack_unknown")
    assert "for update skip locked" in claim
    assert "target_lease_seconds not between 180 and 600" in claim
    assert "attempts = attempts + 1" in claim
    assert "receipt.status = 'delivered'" in complete
    assert "'reused', true" in complete
    assert "if target_reconciled then" in complete
    assert "receipt.status <> 'delivery_unknown'" in complete
    assert "when receipt.status = 'attempt_started' then 'delivery_unknown'" in reconcile
    assert "when receipt.attempts < receipt.max_attempts then 'pending'" in reconcile
    assert "receipt.status = 'delivery_unknown'" in unknown
    assert "'acknowledgements', result" in unknown


def test_unknown_and_started_outcomes_never_return_to_pending():
    failure = _function("fail_origintrail_buzz_review_ack")
    reconcile = _function("reconcile_origintrail_buzz_review_ack_leases")
    assert "next_status := 'delivery_unknown'" in failure
    assert "target_error_code <> 'buzz_delivery_unknown'" in failure
    assert "target_retryable_before_attempt" in failure
    assert "receipt.status = 'attempt_started' then 'delivery_unknown'" in reconcile
    lowered = MIGRATION.lower()
    assert "delivery_unknown' then 'pending" not in lowered
    assert "delivery_unknown', 'pending" not in lowered


def test_scoped_role_has_exactly_the_nine_review_and_ack_rpcs():
    granted = set(re.findall(r"'public[.]([a-z_]+)[(]", ROLE_MIGRATION))
    assert granted == {
        "list_origintrail_buzz_review_targets",
        "record_origintrail_buzz_review_decision",
        "record_origintrail_buzz_review_decision_with_ack",
        "claim_origintrail_buzz_review_ack",
        "mark_origintrail_buzz_review_ack_attempt",
        "complete_origintrail_buzz_review_ack",
        "fail_origintrail_buzz_review_ack",
        "reconcile_origintrail_buzz_review_ack_leases",
        "list_origintrail_buzz_review_ack_unknown",
    }
    assert "nologin noinherit nobypassrls" in ROLE_MIGRATION
    assert "grant coineasy_buzz_review_decider to authenticator" in ROLE_MIGRATION
    assert "revoke all on table agent_runtime.buzz_review_decisions" in ROLE_MIGRATION
    assert "agent_runtime.buzz_review_ack_receipts" in ROLE_MIGRATION
    assert "grant select" not in ROLE_MIGRATION.lower()


def test_ack_sql_has_no_publication_batch_or_provider_transition():
    lowered = MIGRATION.lower()
    for forbidden in (
        "content_publications",
        "public.approvals",
        "queue_agent_batch_job",
        "openai_api_key",
        "telegram_bot_token",
        "request_studio_telegram_publication",
        "authorize_origintrail_batch_provider_create",
        "register_origintrail_batch_provider_create",
    ):
        assert forbidden not in lowered


def test_transactional_security_fixture_exercises_durable_state_machine_only():
    lowered = SECURITY_TEST.lower()
    assert lowered.startswith("-- transactional security")
    assert lowered.rstrip().endswith("rollback;")
    for routine in (
        "record_origintrail_buzz_review_decision_with_ack",
        "claim_origintrail_buzz_review_ack",
        "mark_origintrail_buzz_review_ack_attempt",
        "reconcile_origintrail_buzz_review_ack_leases",
        "list_origintrail_buzz_review_ack_unknown",
        "complete_origintrail_buzz_review_ack",
    ):
        assert f"public.{routine}(" in SECURITY_TEST
    for invariant in (
        "attempt replay gained a second authorization",
        "unknown acknowledgement was automatically requeued",
        "exact acknowledgement completion replay was not reused",
        "acknowledgement flow mutated publication, approval, or Batch state",
    ):
        assert invariant in SECURITY_TEST
