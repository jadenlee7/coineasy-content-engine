from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations/20260813120000_origintrail_buzz_operations_agent.sql"
).read_text()
ROLE_MIGRATION = (
    ROOT / "supabase/migrations/20260813121000_buzz_operations_role.sql"
).read_text()
SECURITY_TEST = (
    ROOT / "supabase/tests/origintrail_buzz_operations_security.sql"
).read_text()


def _function(name: str, *, schema: str = "public") -> str:
    match = re.search(
        rf"create or replace function {schema}[.]{name}[(].*?\n[$][$];",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, name
    return match.group(0)


def test_operations_tables_are_force_rls_without_direct_grants():
    for table in ("buzz_operations_tasks", "buzz_operations_commands"):
        assert f"create table agent_runtime.{table}" in MIGRATION
        assert f"alter table agent_runtime.{table} enable row level security" in MIGRATION
        assert f"alter table agent_runtime.{table} force row level security" in MIGRATION
    assert "from public, anon, authenticated, service_role" in MIGRATION
    assert "grant select on" not in MIGRATION.lower()
    assert "grant insert on" not in MIGRATION.lower()


def test_record_is_atomic_and_supports_only_four_commands():
    record = _function("record_origintrail_buzz_operations_command")
    for command in ("status", "plan_today", "next_task", "hold"):
        assert f"'{command}'" in record
    assert "insert into agent_runtime.buzz_operations_tasks" in record
    assert "insert into agent_runtime.buzz_operations_commands" in record
    assert "on conflict (workspace_id, task_type, task_day) do nothing" in record
    assert "at time zone 'Asia/Seoul'" in record
    assert "response_relay_event_id = target_reply_to_event_id" in record
    assert "for update of task" in record
    assert "자동 발행: OFF" in record


def test_command_hash_matches_python_domain_and_binds_reply_target():
    record = _function("record_origintrail_buzz_operations_command")
    command_hash = _function(
        "origintrail_buzz_operations_command_sha256", schema="private"
    )
    assert "convert_to('coineasy-buzz-operations-command', 'UTF8')" in command_hash
    assert command_hash.count("decode('00', 'hex')") == 7
    for binding in (
        "target_protocol_version",
        "target_channel_id::text",
        "target_command_event_id",
        "target_reviewer_pubkey",
        "target_command",
        "target_command_created_at_epoch::text",
        "coalesce(target_reply_to_event_id, '')",
    ):
        assert binding in command_hash
    assert "private.origintrail_buzz_operations_command_sha256(" in record
    assert "target_command_sha256 <> expected_sha" in record


def test_response_outbox_is_at_most_one_attempt_and_never_blind_requeued():
    claim = _function("claim_origintrail_buzz_operations_response")
    attempt = _function("mark_origintrail_buzz_operations_response_attempt")
    complete = _function("complete_origintrail_buzz_operations_response")
    failure = _function("fail_origintrail_buzz_operations_response")
    reconcile = _function("reconcile_origintrail_buzz_operations_leases")
    assert "for update skip locked" in claim
    assert "attempts = command_row.attempts + 1" in claim
    assert "'authorized_once', target_authorized_once" in MIGRATION
    assert "response_status = 'attempt_started'" in attempt
    assert "response_request_sha256 is null" in attempt
    assert "target_reconciled and response_status = 'delivery_unknown'" in complete
    assert "response_status = 'delivery_unknown'" in failure
    assert "response_status = 'delivery_unknown'" in reconcile
    lowered = MIGRATION.lower()
    assert "delivery_unknown' then 'pending" not in lowered
    assert "delivery_unknown', 'pending" not in lowered


def test_scoped_role_has_exactly_seven_operations_rpcs_and_zero_tables():
    granted = set(re.findall(r"'public[.]([a-z_]+)[(]", ROLE_MIGRATION))
    assert granted == {
        "record_origintrail_buzz_operations_command",
        "claim_origintrail_buzz_operations_response",
        "mark_origintrail_buzz_operations_response_attempt",
        "complete_origintrail_buzz_operations_response",
        "fail_origintrail_buzz_operations_response",
        "reconcile_origintrail_buzz_operations_leases",
        "list_origintrail_buzz_operations_unknown",
    }
    assert "nologin noinherit nobypassrls" in ROLE_MIGRATION
    assert "grant coineasy_buzz_operations_worker to authenticator" in ROLE_MIGRATION
    assert "revoke all on table agent_runtime.buzz_operations_tasks" in ROLE_MIGRATION
    assert "agent_runtime.buzz_operations_commands" in ROLE_MIGRATION
    assert "grant select" not in ROLE_MIGRATION.lower()


def test_operations_plane_has_no_provider_batch_or_publication_transition():
    lowered = MIGRATION.lower()
    for forbidden in (
        "content_publications",
        "public.approvals",
        "queue_agent_batch_job",
        "batch_jobs",
        "batch_runs",
        "batch_members",
        "provider_create_intents",
        "openai",
        "telegram",
        "request_content_publication",
        "authorize_origintrail_batch_provider_create",
        "register_origintrail_batch_provider_create",
    ):
        assert forbidden not in lowered


def test_transactional_fixture_proves_atomic_tasks_and_response_fence():
    lowered = SECURITY_TEST.lower()
    assert lowered.startswith("-- transactional security")
    assert lowered.rstrip().endswith("rollback;")
    for routine in (
        "record_origintrail_buzz_operations_command",
        "claim_origintrail_buzz_operations_response",
        "mark_origintrail_buzz_operations_response_attempt",
        "complete_origintrail_buzz_operations_response",
        "reconcile_origintrail_buzz_operations_leases",
    ):
        assert f"public.{routine}(" in SECURITY_TEST
    for invariant in (
        "Exact command replay duplicated durable state",
        "Attempt replay gained a second relay authorization",
        "Unknown response was automatically requeued",
        "Operations flow mutated publication, approval, or Batch state",
    ):
        assert invariant in SECURITY_TEST
