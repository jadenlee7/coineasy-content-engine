"""Static contract for durable double fact-check approval and publication gates."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260802120000_double_fact_check_approval_gate.sql"
)
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
SQL_SECURITY_PATH = (
    ROOT / "supabase" / "tests" / "double_fact_check_approval_security.sql"
)


def _function(schema: str, name: str) -> str:
    match = re.search(
        rf"create or replace function {schema}\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing {schema}.{name}"
    return match.group(0)


def test_migration_is_forward_only_and_keeps_legacy_approvals_unattested() -> None:
    assert MIGRATION.lstrip().startswith("--")
    assert re.search(r"\bbegin;", MIGRATION)
    assert MIGRATION.rstrip().endswith("commit;")
    assert "add column fact_check_policy_version text" in MIGRATION
    assert "add column source_facts_verified boolean not null default false" in MIGRATION
    assert "add column output_claims_verified boolean not null default false" in MIGRATION
    assert "fact_check_policy_version is null" in MIGRATION
    assert "fact_check_policy_version = 'double-fact-check@1'" in MIGRATION
    assert "create sequence public.approvals_review_sequence_seq" in MIGRATION
    assert "order by approval.created_at, approval.id" in MIGRATION
    assert "alter column review_sequence set not null" in MIGRATION


def test_migration_aborts_while_an_exact_telegram_attempt_is_active() -> None:
    assert "pause and reconcile active exact Telegram attempts" in MIGRATION
    assert "publication.status = 'publishing'" in MIGRATION
    assert "job.status = 'running'" in MIGRATION


def test_report_validator_is_flat_strict_and_allows_human_review() -> None:
    function = _function("private", "has_valid_double_fact_check_report")
    assert "target_generation_meta -> 'fact_check'" in function
    assert "-> 'fact_check' -> 'report'" not in function
    assert "->> 'schema_version' = '1.0'" in function
    assert "= 'double-fact-check@1'" in function
    assert "-> 'human_review_required'" in function
    assert "= 'true'::jsonb" in function
    assert "in ('pass', 'review')" in function
    assert "'blocked'" not in function
    assert function.count("~ '^[a-f0-9]{64}$'") == 2
    assert "-> 'checks'" in function
    assert "= 'array'" in function


def test_v2_approval_requires_and_idempotently_binds_both_attestations() -> None:
    function = _function("public", "record_studio_content_review_v2")
    for argument in (
        "review_fact_check_policy_version text",
        "review_source_facts_verified boolean",
        "review_output_claims_verified boolean",
    ):
        assert argument in function
    assert "review_source_facts_verified is not true" in function
    assert "review_output_claims_verified is not true" in function
    assert "private.has_valid_double_fact_check_report(" in function
    assert "target_version.generation_meta" in function
    assert "existing.fact_check_policy_version" in function
    assert "existing.source_facts_verified" in function
    assert "existing.output_claims_verified" in function
    assert "studio review idempotency conflict" in function
    assert "review_decision = 'approved'" in function
    assert "review_decision = 'rejected'" in function


def test_legacy_v1_remains_and_summary_exposes_attestations() -> None:
    legacy = (
        ROOT
        / "supabase"
        / "migrations"
        / "20260730202817_studio_review_learning.sql"
    ).read_text(encoding="utf-8")
    assert "function public.record_studio_content_review(" in legacy
    summary = _function("public", "get_content_review_summary")
    for field in (
        "fact_check_policy_version",
        "source_facts_verified",
        "output_claims_verified",
    ):
        assert f"'{field}'" in summary


def test_latest_exact_version_approval_and_report_are_one_shared_gate() -> None:
    function = _function("private", "require_double_fact_check_approval")
    assert "review.content_version_id = target_content_version_id" in function
    assert "order by review.review_sequence desc" in function
    assert "latest.decision <> 'approved'" in function
    assert "latest.fact_check_policy_version" in function
    assert "latest.source_facts_verified is not true" in function
    assert "latest.output_claims_verified is not true" in function
    assert "latest.id is distinct from expected_approval_id" in function
    assert "private.has_valid_double_fact_check_report(" in function


def test_every_publication_boundary_calls_the_shared_fail_closed_gate() -> None:
    for name in (
        "request_content_publication",
        "record_manual_publication_observation",
        "request_studio_telegram_publication",
        "claim_exact_telegram_publication_job",
        "mark_exact_telegram_attempt_started",
    ):
        function = _function("public", name)
        assert "private.require_double_fact_check_approval(" in function, name
        assert "security definer" in function.lower()
        assert "set search_path = ''" in function

    exact_request = _function("public", "request_studio_telegram_publication")
    assert exact_request.count("private.require_double_fact_check_approval(") == 2
    assert "publication.request_payload ->> 'approval_id'" in exact_request
    assert "pinned_approval_id" in exact_request
    marker = _function("public", "mark_exact_telegram_attempt_started")
    assert "for update of item" in marker.lower()
    assert marker.index("private.require_double_fact_check_approval(") < marker.index(
        "private.mark_exact_telegram_attempt_before_double_fact_check("
    )


def test_claim_quarantines_invalid_queue_heads_and_makes_bounded_progress() -> None:
    claim = _function("public", "claim_exact_telegram_publication_job")
    assert "quarantine_limit constant integer := 25" in claim
    assert "loop" in claim
    assert "exception when check_violation" in claim
    assert "status = 'failed'" in claim
    assert "double_fact_check_approval_invalid" in claim
    assert "delivery_started_at is null" in claim
    assert "exact_telegram_publication_quarantined" in claim
    assert "quarantined_count >= quarantine_limit" in claim


def test_manual_observation_is_atomic_and_legacy_approval_writers_are_revoked() -> None:
    observation = _function("public", "record_manual_publication_observation")
    assert "private.record_manual_observation_before_double_fact_check(" in observation
    assert observation.index(
        "private.record_manual_observation_before_double_fact_check("
    ) < observation.index("private.require_double_fact_check_approval(")
    assert "revoke all on function private.record_manual_observation_before_double_fact_check(" in MIGRATION
    assert "grant execute on function public.record_manual_publication_observation(" in MIGRATION
    assert re.search(
        r"revoke all on function public\.review_content_version\(uuid, uuid, text, text\)\s*"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
    )
    assert re.search(
        r"revoke all on function public\.record_studio_content_review\(\s*"
        r"uuid, uuid, uuid, text, text\[\], text, text\s*\)\s*"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
    )


def test_legacy_implementations_are_private_and_not_executable() -> None:
    for name in (
        "request_content_publication_before_double_fact_check",
        "request_studio_telegram_before_double_fact_check",
        "claim_exact_telegram_job_before_double_fact_check",
        "mark_exact_telegram_attempt_before_double_fact_check",
    ):
        assert f"revoke all on function private.{name}(" in MIGRATION
    assert "grant execute on function public.record_studio_content_review_v2(" in MIGRATION
    assert "to service_role;" in MIGRATION
    assert "grant execute on function public.request_content_publication(" in MIGRATION
    assert "to authenticated;" in MIGRATION


def test_transactional_security_fixture_covers_fail_closed_cases() -> None:
    assert SQL_SECURITY_PATH.exists()
    smoke = SQL_SECURITY_PATH.read_text(encoding="utf-8")
    assert "begin;" in smoke
    assert smoke.rstrip().endswith("rollback;")
    for marker in (
        "legacy approval authorized publication",
        "legacy approval authorized manual publication observation",
        "failed manual observation leaked into publication ledger",
        "missing fact-check report was approved",
        "blocked fact-check report was approved",
        "one-sided human attestation was approved",
        "v2 review idempotency did not bind attestations",
        "review-status fact check could not be human-approved",
        "review summary omitted double fact-check attestations",
        "later un-attested review did not revoke publication authority",
    ):
        assert marker in smoke
