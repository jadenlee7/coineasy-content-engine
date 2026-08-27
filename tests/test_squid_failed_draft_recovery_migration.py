from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260825120000_squid_failed_draft_recovery.sql"
).read_text(encoding="utf-8")
ADDITIVE_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260827150000_squid_copy_discovery_failed_draft_recovery.sql"
).read_text(encoding="utf-8")


def _function(
    name: str,
    *,
    schema: str = "public",
    migration: str = MIGRATION,
) -> str:
    match = re.search(
        rf"create or replace function {schema}\.{name}\(.*?\n\$\$;",
        migration,
        re.DOTALL,
    )
    assert match is not None, f"missing {schema}.{name}"
    return match.group(0)


def test_copy_discovery_extension_is_additive_exact_and_fail_closed() -> None:
    assert ADDITIVE_MIGRATION.lstrip().startswith("-- Add one bounded transient")
    assert ADDITIVE_MIGRATION.rstrip().endswith("commit;")
    assert (
        "drop constraint "
        "official_x_failed_draft_recovery_grants_failure_code_check"
    ) in ADDITIVE_MIGRATION
    constraint = re.search(
        r"add constraint "
        r"official_x_failed_draft_recovery_grants_failure_code_check\s+"
        r"check \(\s*failure_code in \(\s*"
        r"'squid_visual_localization_incomplete',\s*"
        r"'squid_copy_discovery_unavailable'\s*\)\s*\)",
        ADDITIVE_MIGRATION,
        re.DOTALL,
    )
    assert constraint is not None
    assert "squid_placement_audit_unavailable" not in ADDITIVE_MIGRATION


def test_copy_discovery_subject_changes_only_the_exact_failure_allowlist() -> None:
    old_subject = _function(
        "squid_failed_draft_recovery_subject",
        schema="private",
    )
    new_subject = _function(
        "squid_failed_draft_recovery_subject",
        schema="private",
        migration=ADDITIVE_MIGRATION,
    )
    old_predicate = (
        "or failed_job.last_error_code\n"
        "            is distinct from 'squid_visual_localization_incomplete'"
    )
    new_predicate = (
        "or failed_job.last_error_code is null\n"
        "       or failed_job.last_error_code not in (\n"
        "            'squid_visual_localization_incomplete',\n"
        "            'squid_copy_discovery_unavailable'\n"
        "       )"
    )
    assert old_predicate in old_subject
    assert new_predicate in new_subject
    assert old_subject.replace(old_predicate, "<failure-allowlist>") == (
        new_subject.replace(new_predicate, "<failure-allowlist>")
    )
    assert "security definer" in new_subject.lower()
    assert "set search_path = ''" in new_subject.lower()
    assert re.search(
        r"revoke all on function "
        r"private\.squid_failed_draft_recovery_subject\(\s*"
        r"uuid, uuid, uuid, uuid, text, timestamptz, timestamptz, text\s*\)\s*"
        r"from public, anon, authenticated, service_role;",
        ADDITIVE_MIGRATION,
        re.DOTALL,
    )


def test_copy_discovery_extension_does_not_replace_public_control_rpcs() -> None:
    for name in (
        "inspect_squid_failed_draft_recovery",
        "authorize_squid_failed_draft_recovery",
        "claim_squid_failed_draft_recovery",
    ):
        assert f"create or replace function public.{name}(" not in (
            ADDITIVE_MIGRATION
        )


def test_private_grant_is_exact_one_shot_and_directly_inaccessible() -> None:
    assert "create table private.official_x_failed_draft_recovery_grants" in MIGRATION
    for binding in (
        "primary key (workspace_id, recovery_id)",
        "unique (workspace_id, job_id)",
        "unique (workspace_id, approval_id)",
        "unique (workspace_id, approval_subject_sha256)",
        "claims_allowed = 1",
        "claims_consumed between 0 and claims_allowed",
        "release_sha ~ '^[a-f0-9]{40}$'",
        "failure_code = 'squid_visual_localization_incomplete'",
    ):
        assert binding in MIGRATION
    assert "enable row level security" in MIGRATION
    assert "force row level security" in MIGRATION
    assert re.search(
        r"revoke all on table private\.official_x_failed_draft_recovery_grants\s*"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
    )


def test_grant_binding_is_immutable_and_consumption_cannot_reverse() -> None:
    immutable = _function(
        "enforce_failed_draft_recovery_grant_immutable",
        schema="private",
    )
    for field in (
        "recovery_id",
        "job_id",
        "request_id",
        "source_item_id",
        "kst_date",
        "job_input_sha256",
        "source_snapshot_sha256",
        "style_pack_sha256",
        "failed_output_snapshot",
        "failed_output_sha256",
        "approval_id",
        "approval_subject",
        "approval_subject_sha256",
        "approved_by",
        "approved_at",
        "expires_at",
        "release_sha",
        "claims_allowed",
        "authorized_at",
    ):
        assert f"new.{field} is distinct from old.{field}" in immutable
    assert "tg_op = 'DELETE'" in immutable
    assert "new.claims_consumed < old.claims_consumed" in immutable
    assert "consumption is irreversible" in immutable


def test_subject_is_source_locked_newest_first_and_zero_catalog() -> None:
    subject = _function("squid_failed_draft_recovery_subject", schema="private")
    for fence in (
        "failed_job.client_id <> 'squid'",
        "failed_job.status <> 'failed'",
        "failed_job.attempts <> 3",
        "failed_job.max_attempts <> 3",
        "failed_job.output ->> 'execution_plane' is distinct from 'studio_sync'",
        "squid_visual_localization_incomplete",
        "timezone('Asia/Seoul', clock_timestamp())::date",
        "slot.job_id = target_job_id",
        "state.queued_job_id = target_job_id",
        "feed.handle = '@SquidRouter'",
        "source_feed.last_polled_at < clock_timestamp() - make_interval",
        "source_feed.last_cursor::numeric",
        "< source.external_id::numeric",
        "source.body is distinct from failed_job.input ->> 'source_content'",
        "source.canonical_url is distinct from failed_job.input ->> 'source_url'",
        "clock_timestamp() - interval '24 hours'",
        "A newer official Squid source supersedes this recovery",
        "private.official_x_style_reference_packs",
        "public.content_items",
        "public.content_versions",
        "public.content_source_links",
        "private.grok_qa_dispatch_outbox",
    ):
        assert fence in subject
    feed_lock = subject.index("from public.source_feeds as feed")
    newer_source_check = subject.index("from public.source_items as newer")
    assert "for share" in subject[feed_lock:newer_source_check]
    durable_check = subject.index(
        "Squid recovery already has durable or duplicate output"
    )
    final_decision = subject.index("decision_now := clock_timestamp()")
    input_hash = subject.index("input_sha :=")
    assert feed_lock < newer_source_check < durable_check < final_decision < input_hash
    final_fences = subject[final_decision:input_hash]
    for fence in (
        "target_expires_at <= decision_now",
        "timezone('Asia/Seoul', decision_now)::date",
        "source.published_at < decision_now - interval '24 hours'",
        "source_feed.last_polled_at < decision_now - make_interval",
    ):
        assert fence in final_fences
    assert "source_snapshot_sha256" in subject
    assert "style_pack_sha256" in subject
    assert "failed_output_sha256" in subject
    assert "'automatic_approval', false" in subject
    assert "'automatic_publication', false" in subject


def test_inspect_is_read_only_and_authorize_does_not_touch_job_or_source() -> None:
    inspect = _function("inspect_squid_failed_draft_recovery")
    lowered = inspect.lower()
    assert "security definer" in lowered
    assert "set search_path = ''" in lowered
    assert "insert into" not in lowered
    assert "update " not in lowered
    assert "delete " not in lowered

    authorize = _function("authorize_squid_failed_draft_recovery")
    assert "pg_catalog.pg_advisory_xact_lock" in authorize
    assert "from public.jobs as job" in authorize
    assert "for update" in authorize
    assert "insert into private.official_x_failed_draft_recovery_grants" in authorize
    assert "update public.jobs" not in authorize
    assert "update private.official_x_daily_slots" not in authorize
    assert "update private.official_x_source_state" not in authorize
    assert "delete from" not in authorize.lower()


def test_targeted_claim_consumes_before_leasing_same_max_attempt_job() -> None:
    claim = _function("claim_squid_failed_draft_recovery")
    advisory_lock = claim.index("pg_catalog.pg_advisory_xact_lock")
    grant_lock = claim.index(
        "from private.official_x_failed_draft_recovery_grants as grant_row"
    )
    job_lock = claim.index("from public.jobs as job")
    consume = claim.index(
        "update private.official_x_failed_draft_recovery_grants"
    )
    lease = claim.index("update public.jobs")
    assert advisory_lock < grant_lock < job_lock < consume < lease
    assert "exact_grant.expires_at <= clock_timestamp()" in claim
    assert "lease_expires_at = clock_timestamp()" in claim
    job_update = claim[lease:].split("returning * into failed_job;", 1)[0]
    job_set_clause = job_update.split("where", 1)[0]
    for forbidden in (
        "attempts =",
        "max_attempts =",
        "input =",
        "output =",
        "last_error_code =",
        "last_error_message =",
        "finished_at =",
    ):
        assert forbidden not in job_set_clause
    assert "set status = 'running'" in job_update
    assert "and attempts = max_attempts" in job_update
    assert "'generation_allowed', true" in claim
    assert "'failed_draft_recovery_only', true" in claim
    assert "insert into public.jobs" not in claim
    assert "update private.official_x_daily_slots" not in claim
    assert "update private.official_x_source_state" not in claim


def test_recovery_terminal_evidence_is_preserved_without_new_retry() -> None:
    preserve = _function(
        "preserve_failed_draft_recovery_evidence",
        schema="private",
    )
    assert "new.output := old.output || new.output" in preserve
    assert "'last_failure', old.output -> 'last_failure'" in preserve
    assert "'recovery_failure', recovery_failure" in preserve
    assert "new.status not in ('succeeded', 'failed')" in preserve
    assert "retrying" not in preserve


def test_all_recovery_rpcs_are_service_role_only() -> None:
    signatures = {
        "inspect_squid_failed_draft_recovery": (
            "uuid, uuid, uuid, uuid, text, timestamptz, timestamptz, text"
        ),
        "authorize_squid_failed_draft_recovery": (
            "uuid, uuid, uuid, uuid, text, timestamptz, timestamptz, text, text"
        ),
        "claim_squid_failed_draft_recovery": (
            "uuid, uuid, uuid, text, text, text, integer"
        ),
    }
    for name, signature in signatures.items():
        function = _function(name).lower()
        assert "security definer" in function
        assert "set search_path = ''" in function
        escaped = re.escape(signature).replace(r"\ ", r"\s*")
        assert re.search(
            rf"revoke all on function public\.{name}\(\s*{escaped}\s*\)\s*"
            rf"from public, anon, authenticated, service_role;",
            MIGRATION,
            re.DOTALL,
        )
        assert re.search(
            rf"grant execute on function public\.{name}\(\s*{escaped}\s*\)\s*"
            rf"to service_role;",
            MIGRATION,
            re.DOTALL,
        )
