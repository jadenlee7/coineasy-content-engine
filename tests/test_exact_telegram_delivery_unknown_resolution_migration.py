"""Static fail-closed contract for non-resend Telegram unknown resolution."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260831120000_exact_telegram_delivery_unknown_resolution.sql"
)
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")

ROLE = "coineasy_telegram_resolution"
INSPECT_RPC = "inspect_exact_telegram_delivery_unknown_resolution"
APPROVE_RPC = "approve_exact_telegram_delivery_unknown_resolution"
RESOLVE_RPC = "resolve_exact_telegram_delivery_unknown_without_resend"
INSPECT_SIGNATURE = (
    "uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
    "text, text, timestamptz, text, jsonb"
)
APPROVE_SIGNATURE = (
    "uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
    "text, timestamptz, text, jsonb, text"
)
RESOLVE_SIGNATURE = (
    "uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
    "text, text, jsonb, text"
)


def _function(name: str, *, schema: str = "public") -> str:
    match = re.search(
        rf"create or replace function {schema}[.]{name}[(].*?\n[$][$];",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, f"missing {schema}.{name}"
    return match.group(0)


def _table(name: str, *, schema: str = "private") -> str:
    match = re.search(
        rf"create table {schema}[.]{name}\s*[(].*?\n[)];",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, f"missing {schema}.{name}"
    return match.group(0)


def _signature_pattern(signature: str) -> str:
    return re.escape(signature).replace(r"\ ", r"\s*")


def _assert_revoked_from_all_runtime_roles(
    name: str,
    signature: str,
    *,
    schema: str = "public",
) -> None:
    assert re.search(
        rf"revoke all on function\s+{schema}[.]{name}[(]\s*"
        rf"{_signature_pattern(signature)}\s*[)]\s*from\s+"
        rf"public,\s*anon,\s*authenticated,\s*service_role,\s*{ROLE};",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )


def _assert_granted_to_resolution_role(name: str, signature: str) -> None:
    assert re.search(
        rf"grant execute on function\s+public[.]{name}[(]\s*"
        rf"{_signature_pattern(signature)}\s*[)]\s*to\s+{ROLE};",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )
    assert not re.search(
        rf"grant execute on function\s+public[.]{name}[(].*?[)]\s*"
        r"to\s+(?:public|anon|authenticated|service_role);",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )


def test_migration_is_transactional_and_creates_a_dedicated_no_login_role() -> None:
    assert MIGRATION.lstrip().startswith("-- Audited operational closure")
    assert re.search(r"\bbegin;", MIGRATION, re.IGNORECASE)
    assert MIGRATION.rstrip().endswith("commit;")
    for attribute in (
        "nologin",
        "noinherit",
        "nosuperuser",
        "nocreaterole",
        "nocreatedb",
        "noreplication",
        "nobypassrls",
    ):
        assert attribute in MIGRATION.lower()
    assert f"create role {ROLE}" in MIGRATION.lower()
    assert f"grant {ROLE} to authenticator" in MIGRATION.lower()
    assert f"grant usage on schema public to {ROLE}" in MIGRATION.lower()


def test_force_rls_function_owner_is_asserted_fail_closed() -> None:
    assert re.search(
        r"do [$]owner[$].*?where rolname = current_user.*?"
        r"[(]rolsuper or rolbypassrls[)].*?"
        r"Telegram resolution function owner must bypass row security.*?"
        r"[$]owner[$];",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )


def test_common_jwt_claims_split_three_zero_authority_capabilities() -> None:
    claims = _function("require_telegram_resolution_claims", schema="private")
    lowered = claims.lower()
    for property_name in ("stable", "security definer", "set search_path = ''"):
        assert property_name in lowered
    for fence in (
        "current_setting('request.jwt.claims', true)",
        "claims ->> 'role' is distinct from",
        "'coineasy_telegram_resolution'",
        "claims ->> 'workspace_id' is distinct from target_workspace_id::text",
        "claims ->> 'sub' is distinct from target_principal",
        "claims ->> 'capability' is distinct from target_capability",
        "'telegram_delivery_unknown_inspect'",
        "'telegram_delivery_unknown_approve'",
        "'telegram_delivery_unknown_resolve'",
        "claims ->> 'environment' is distinct from 'production'",
        "claims ->> 'release_sha' is distinct from target_release_sha",
        "claims -> 'automatic_publication' is distinct from 'false'::jsonb",
        "claims -> 'resend_authorized' is distinct from 'false'::jsonb",
        "claims -> 'max_external_actions' is distinct from '0'::jsonb",
        "using errcode = '42501'",
    ):
        assert fence in claims
    _assert_revoked_from_all_runtime_roles(
        "require_telegram_resolution_claims",
        "uuid, text, text, text",
        schema="private",
    )


def test_inspect_approval_and_resolve_jwts_are_exactly_bound() -> None:
    inspect = _function(
        "require_telegram_resolution_inspect_claims", schema="private"
    )
    approval = _function(
        "require_telegram_resolution_approval_claims", schema="private"
    )
    resolve = _function(
        "require_telegram_resolution_resolve_claims", schema="private"
    )
    for helper in (inspect, approval, resolve):
        lowered = helper.lower()
        assert "stable" in lowered
        assert "security definer" in lowered
        assert "set search_path = ''" in lowered
        for claim in (
            "content_item_id",
            "content_version_id",
            "publication_id",
            "job_id",
            "resolution_id",
            "operator_approval_id",
        ):
            assert f"claims ->> '{claim}'" in helper
        assert "using errcode = '42501'" in helper
    assert "claims ->> 'jti' is distinct from target_resolution_id::text" in inspect
    assert "claims ->> 'approved_by' is distinct from target_approved_by" in inspect
    assert "claims ->> 'expires_at'" in inspect
    assert "is distinct from target_expires_at" in inspect
    assert "claims ->> 'public_audit_sha256'" in inspect
    assert "is distinct from target_public_audit_sha256" in inspect
    for helper in (approval, resolve):
        assert "claims ->> 'approval_subject_sha256'" in helper
        assert "is distinct from target_approval_subject_sha256" in helper
    assert "claims ->> 'jti' is distinct from target_operator_approval_id::text" in (
        approval
    )
    assert "claims ->> 'expires_at'" in approval
    assert "is distinct from target_expires_at" in approval
    assert "claims ->> 'jti' is distinct from target_resolution_id::text" in resolve
    _assert_revoked_from_all_runtime_roles(
        "require_telegram_resolution_inspect_claims",
        "jsonb, uuid, uuid, uuid, uuid, uuid, uuid, text, timestamptz, text",
        schema="private",
    )
    _assert_revoked_from_all_runtime_roles(
        "require_telegram_resolution_approval_claims",
        "jsonb, uuid, uuid, uuid, uuid, uuid, uuid, text, timestamptz",
        schema="private",
    )
    _assert_revoked_from_all_runtime_roles(
        "require_telegram_resolution_resolve_claims",
        "jsonb, uuid, uuid, uuid, uuid, uuid, uuid, text",
        schema="private",
    )


def test_public_rpcs_are_security_definer_and_only_the_dedicated_role_executes() -> None:
    contracts = (
        (
            INSPECT_RPC,
            INSPECT_SIGNATURE,
            "'telegram_delivery_unknown_inspect'",
            "private.require_telegram_resolution_inspect_claims(",
        ),
        (
            APPROVE_RPC,
            APPROVE_SIGNATURE,
            "'telegram_delivery_unknown_approve'",
            "private.require_telegram_resolution_approval_claims(",
        ),
        (
            RESOLVE_RPC,
            RESOLVE_SIGNATURE,
            "'telegram_delivery_unknown_resolve'",
            "private.require_telegram_resolution_resolve_claims(",
        ),
    )
    for name, signature, capability, exact_helper in contracts:
        function = _function(name)
        assert "security definer" in function.lower()
        assert "set search_path = ''" in function.lower()
        assert "private.require_telegram_resolution_claims(" in function
        assert capability in function
        if exact_helper is not None:
            assert exact_helper in function
        _assert_revoked_from_all_runtime_roles(name, signature)
        _assert_granted_to_resolution_role(name, signature)


def test_approval_receipt_is_private_exact_bounded_and_append_only() -> None:
    name = "exact_telegram_delivery_unknown_approvals"
    table = _table(name)
    for binding in (
        "primary key (workspace_id, operator_approval_id)",
        "unique (workspace_id, approval_subject_sha256)",
        "references public.publications(id) on delete restrict",
        "references public.jobs(id) on delete restrict",
        "references public.content_versions(workspace_id, content_item_id, id)",
        "octet_length(approval_subject::text) <= 16384",
        "approval_subject_sha256 ~ '^[a-f0-9]{64}$'",
        "approved_by ~ '^[A-Za-z0-9@._:-]{3,120}$'",
        "approved_at timestamptz not null default pg_catalog.clock_timestamp()",
        "expires_at > approved_at",
        "expires_at <= approved_at + interval '2 hours'",
        "approved_release_sha ~ '^[a-f0-9]{40}$'",
    ):
        assert binding in table
    for rls_clause in ("enable row level security", "force row level security"):
        assert re.search(
            rf"alter table private[.]{name}\s+{rls_clause}",
            MIGRATION,
            re.DOTALL | re.IGNORECASE,
        )
    assert re.search(
        rf"revoke all on table private[.]{name}\s+from\s+"
        rf"public,\s*anon,\s*authenticated,\s*service_role,\s*{ROLE};",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )
    assert re.search(
        rf"before update or delete\s+on private[.]{name}",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )


def test_resolution_receipt_is_private_exact_bounded_and_append_only() -> None:
    name = "exact_telegram_delivery_unknown_resolutions"
    table = _table(name)
    for binding in (
        "primary key (workspace_id, resolution_id)",
        "unique (workspace_id, publication_id)",
        "unique (workspace_id, job_id)",
        "unique (workspace_id, delivery_attempt_id)",
        "unique (workspace_id, operator_approval_id)",
        "unique (workspace_id, approval_subject_sha256)",
        "references private.exact_telegram_delivery_unknown_approvals(",
        "disposition = 'operator_closed_without_resend'",
        "delivery_outcome = 'unknown'",
        "public_observation = 'not_observed_at_checked_at'",
        "octet_length(public_audit::text) <= 4096",
        "octet_length(approval_subject::text) <= 16384",
        "expires_at > resolved_at",
        "expires_at <= approved_at + interval '2 hours'",
    ):
        assert binding in table
    for digest in (
        "delivery_request_sha256",
        "publication_request_sha256",
        "publication_response_sha256",
        "job_input_sha256",
        "job_output_sha256",
        "content_item_row_sha256",
        "content_version_row_sha256",
        "publication_row_sha256",
        "job_row_sha256",
        "publication_approval_row_sha256",
        "asset_row_sha256",
        "caption_sha256",
        "asset_sha256",
        "public_audit_sha256",
        "approval_subject_sha256",
    ):
        assert f"{digest} ~ '^[a-f0-9]{{64}}$'" in table
    for rls_clause in ("enable row level security", "force row level security"):
        assert re.search(
            rf"alter table private[.]{name}\s+{rls_clause}",
            MIGRATION,
            re.DOTALL | re.IGNORECASE,
        )
    assert re.search(
        rf"revoke all on table private[.]{name}\s+from\s+"
        rf"public,\s*anon,\s*authenticated,\s*service_role,\s*{ROLE};",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )
    immutable = _function(
        "enforce_exact_telegram_resolution_immutable", schema="private"
    )
    assert "exact Telegram delivery resolutions are immutable" in immutable
    assert "using errcode = '55000'" in immutable
    assert re.search(
        rf"before update or delete\s+on private[.]{name}",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )


def test_forensic_freeze_blocks_only_resolved_rows_and_passes_other_updates() -> None:
    freeze = _function(
        "enforce_resolved_exact_telegram_row_immutable", schema="private"
    )
    assert "receipt.publication_id = old.id" in freeze
    assert "receipt.job_id = old.id" in freeze
    assert "resolved exact Telegram publication is immutable" in freeze
    assert "resolved exact Telegram job is immutable" in freeze
    assert re.search(
        r"if\s+tg_op\s*=\s*'delete'\s+then\s+return\s+old;\s+"
        r"end\s+if;\s+return\s+new;",
        freeze,
        re.DOTALL | re.IGNORECASE,
    )
    assert "before update or delete on public.publications" in MIGRATION
    assert "before update or delete on public.jobs" in MIGRATION


def test_stale_transaction_snapshots_cannot_bypass_resolution_fences() -> None:
    claims = _function("require_telegram_resolution_claims", schema="private")
    freeze = _function(
        "enforce_resolved_exact_telegram_row_immutable", schema="private"
    )
    for guarded in (claims, freeze):
        assert "current_setting('transaction_isolation') <> 'read committed'" in guarded
        assert "using errcode = '25001'" in guarded
    for scope in (
        "old.client_id = 'squid'",
        "old.channel = 'telegram'",
        "old.status = 'delivery_unknown'",
        "old.job_kind = 'publish'",
        "old.status = 'failed'",
        "old.input ->> 'channel' = 'telegram'",
        "'exact_telegram_publication_v1'",
    ):
        assert scope in freeze


def test_full_row_subject_hashes_are_independent_of_caller_timezone() -> None:
    for name in (INSPECT_RPC, APPROVE_RPC, RESOLVE_RPC):
        assert "set timezone = 'UTC'" in _function(name)
    assert "set timezone = 'UTC'" in _function(
        "exact_telegram_delivery_resolution_subject", schema="private"
    )


def test_subject_is_exact_terminal_unknown_and_bounded_to_public_audit() -> None:
    subject = _function(
        "exact_telegram_delivery_resolution_subject", schema="private"
    )
    for property_name in ("security definer", "set search_path = ''"):
        assert property_name in subject.lower()
    for fence in (
        "item.client_id <> 'squid'",
        "item.content_kind <> 'daily_news'",
        "job.status <> 'failed'",
        "job.last_error_code is distinct from 'delivery_outcome_unknown'",
        "job.output -> 'last_failure' ->> 'error_code'",
        "is distinct from 'telegram_delivery_unknown'",
        "job.output -> 'last_failure' -> 'retryable_before_attempt'",
        "is distinct from 'false'::jsonb",
        "job.output -> 'last_failure' -> 'attempt_started'",
        "is distinct from 'true'::jsonb",
        "publication.status <> 'delivery_unknown'",
        "publication.delivery_attempt_id is null",
        "publication.delivery_started_at is null",
        "publication.published_at is not null",
        "publication.external_id is not null",
        "publication.external_url is not null",
        "publication.response_payload ->> 'error_code'",
        "publication_approval.decision <> 'approved'",
        "publication_approval.fact_check_policy_version",
        "publication_approval.source_facts_verified is not true",
        "publication_approval.output_claims_verified is not true",
        "stored_asset.asset_kind = 'png'",
        "stored_asset.mime_type = 'image/png'",
        "exact Telegram delivery is already observed publicly",
    ):
        assert fence in subject
    for audit_key in (
        "caption_match_count",
        "png_match_count",
        "checked_at",
        "first_message_id",
        "last_message_id",
        "message_count",
        "public_channel",
        "scan_source",
        "schema_version",
        "snapshot_sha256",
    ):
        assert f"'{audit_key}'" in subject
    for bounded_value in (
        "telegram-public-channel-audit@1",
        "public_telegram_web_history",
        "squid_kor_update",
        "target_expires_at",
        "> target_validation_reference_at + interval '2 hours'",
    ):
        assert bounded_value in subject
    assert re.search(
        r"checked_at\s*<\s*target_validation_reference_at\s*"
        r"-\s*interval '30 minutes'",
        subject,
    )
    assert "'caption_match_count'" in subject and "'0'::jsonb" in subject
    assert "'png_match_count'" in subject
    assert "'delivery_outcome', 'unknown'" in subject
    assert "'disposition', 'operator_closed_without_resend'" in subject
    assert "'resend_authorized', false" in subject
    assert "'provider_calls', 0" in subject
    assert "'database_claims', 0" in subject
    _assert_revoked_from_all_runtime_roles(
        "exact_telegram_delivery_resolution_subject",
        (
            "uuid, uuid, uuid, uuid, uuid, uuid, uuid, text, "
            "timestamptz, timestamptz, text, jsonb"
        ),
        schema="private",
    )


def test_subject_validation_reference_is_clock_for_preview_and_approval() -> None:
    inspect = _function(INSPECT_RPC)
    approve = _function(APPROVE_RPC)
    resolve = _function(RESOLVE_RPC)
    for function in (inspect, approve):
        subject_call = function.split(
            "subject := private.exact_telegram_delivery_resolution_subject(", 1
        )[1].split(");", 1)[0]
        assert "target_expires_at" in subject_call
        assert "pg_catalog.clock_timestamp()" in subject_call
    resolution_subject_call = resolve.split(
        "subject := private.exact_telegram_delivery_resolution_subject(", 1
    )[1].split(");", 1)[0]
    assert "approved.expires_at" in resolution_subject_call
    assert "approved.approved_at" in resolution_subject_call
    # The validation reference must not become part of the durable subject hash.
    subject = _function(
        "exact_telegram_delivery_resolution_subject", schema="private"
    )
    returned_subject = subject.split(
        "return pg_catalog.jsonb_build_object(", 1
    )[1]
    assert "target_validation_reference_at" not in returned_subject


def test_subject_and_receipt_bind_full_forensic_row_digests() -> None:
    subject = _function(
        "exact_telegram_delivery_resolution_subject", schema="private"
    )
    resolution_table = _table("exact_telegram_delivery_unknown_resolutions")
    row_sources = {
        "content_item_row_sha256": "pg_catalog.to_jsonb(item)::text",
        "content_version_row_sha256": "pg_catalog.to_jsonb(version)::text",
        "publication_row_sha256": "pg_catalog.to_jsonb(publication)::text",
        "job_row_sha256": "pg_catalog.to_jsonb(job)::text",
        "publication_approval_row_sha256": (
            "pg_catalog.to_jsonb(publication_approval)::text"
        ),
        "asset_row_sha256": "pg_catalog.to_jsonb(asset)::text",
    }
    for field, source in row_sources.items():
        assert f"'{field}'" in subject
        assert field in resolution_table
        assert source in subject
    for source in (
        "publication.request_payload::text",
        "publication.response_payload::text",
        "job.input::text",
        "job.output::text",
        "version.channel_copy ->> 'telegram'",
        "target_public_audit::text",
    ):
        assert source in subject
    assert subject.count("extensions.digest(") >= 12


def test_inspect_is_read_only_and_reports_durable_approval_state() -> None:
    inspect = _function(INSPECT_RPC)
    lowered = inspect.lower()
    exact_claims_call = inspect.split(
        "perform private.require_telegram_resolution_inspect_claims(", 1
    )[1].split("\n    select receipt.* into existing", 1)[0]
    for binding in (
        "target_content_item_id",
        "target_content_version_id",
        "target_publication_id",
        "target_job_id",
        "target_resolution_id",
        "target_operator_approval_id",
        "target_approved_by",
        "target_expires_at",
        "public_audit_sha",
    ):
        assert binding in exact_claims_call
    audit_hash_setup = inspect.split(
        "perform private.require_telegram_resolution_inspect_claims(", 1
    )[0]
    assert "target_public_audit::text" in audit_hash_setup
    assert "extensions.digest(" in audit_hash_setup
    assert not re.search(r"\binsert\s+into\b", lowered)
    assert not re.search(r"\bupdate\s+(?:public|private)[.]", lowered)
    assert not re.search(r"\bdelete\s+from\b", lowered)
    assert "existing.resolution_id is distinct from target_resolution_id" in inspect
    assert "existing.operator_approval_id" in inspect
    assert "existing.public_audit is distinct from target_public_audit" in inspect
    assert "existing_approval.approval_subject is distinct from subject" in inspect
    assert "exact Telegram approval conflicts with inspection" in inspect
    assert "'resolved', true" in inspect
    assert "'reused', true" in inspect
    assert "'resolved', false" in inspect
    assert "'reused', false" in inspect
    assert "'approval_subject', subject" in inspect
    assert "'approval_subject_sha256', subject_sha" in inspect
    assert "'approved', found" in inspect


def test_approve_creates_one_immutable_receipt_and_one_audit_event_only() -> None:
    approve = _function(APPROVE_RPC)
    lowered = approve.lower()
    assert "pg_advisory_xact_lock" in approve
    assert "target_workspace_id::text" in approve
    assert "target_operator_approval_id::text" in approve
    assert "subject_sha is distinct from target_approval_subject_sha256" in approve
    assert "exact Telegram approval subject changed" in approve
    assert lowered.count(
        "insert into private.exact_telegram_delivery_unknown_approvals"
    ) == 1
    assert lowered.count("insert into public.event_log") == 1
    assert approve.count(
        "'exact_telegram_delivery_unknown_resolution_approved'"
    ) == 1
    for forbidden in (
        "insert into public.jobs",
        "insert into public.publications",
        "update public.jobs",
        "update public.publications",
        "delete from public.jobs",
        "delete from public.publications",
        "record_manual_publication_observation",
        "claim_exact_telegram_publication_job",
        "mark_exact_telegram_attempt_started",
        "complete_exact_telegram_publication_job",
        "fail_exact_telegram_publication_job",
    ):
        assert forbidden not in lowered
    for no_send_fact in (
        "'resend_authorized', false",
        "'automatic_publication', false",
        "'provider_calls', 0",
        "'database_claims', 0",
    ):
        assert no_send_fact in approve


def test_resolve_uses_canonical_lock_order_and_shared_evidence_locks() -> None:
    resolve = _function(RESOLVE_RPC)
    item_lock = resolve.index("select content.* into ignored_item")
    job_lock = resolve.index("select queued_job.* into ignored_job")
    publication_lock = resolve.index("select delivery.* into ignored_publication")
    version_lock = resolve.index("select immutable_version.* into ignored_version")
    approval_lock = resolve.index(
        "select approval.* into ignored_publication_approval"
    )
    asset_lock = resolve.index("select asset.* into ignored_asset")
    replay_lookup = resolve.index(
        "from private.exact_telegram_delivery_unknown_resolutions as receipt"
    )
    durable_approval_lookup = resolve.index(
        "from private.exact_telegram_delivery_unknown_approvals as approval"
    )
    subject_recheck = resolve.index(
        "subject := private.exact_telegram_delivery_resolution_subject"
    )
    receipt_insert = resolve.index(
        "insert into private.exact_telegram_delivery_unknown_resolutions"
    )
    event_insert = resolve.index("insert into public.event_log")
    assert (
        item_lock
        < job_lock
        < publication_lock
        < version_lock
        < approval_lock
        < asset_lock
        < replay_lookup
        < durable_approval_lookup
        < subject_recheck
        < receipt_insert
        < event_insert
    )
    for lock_start in (version_lock, approval_lock, asset_lock):
        lock_end = resolve.index("if not found then", lock_start)
        assert "for share;" in resolve[lock_start:lock_end]
    for lock_start in (item_lock, job_lock, publication_lock):
        lock_end = resolve.index("if not found then", lock_start)
        assert "for update;" in resolve[lock_start:lock_end]
    assert "ignored_publication.request_payload ->> 'approval_id'" in resolve
    assert "ignored_publication.request_payload ->> 'asset_id'" in resolve
    assert "approved.expires_at <= pg_catalog.clock_timestamp()" in resolve
    assert "subject is distinct from approved.approval_subject" in resolve
    assert "subject_sha is distinct from target_approval_subject_sha256" in resolve


def test_resolution_preserves_source_rows_and_cannot_publish_claim_or_resend() -> None:
    resolve = _function(RESOLVE_RPC)
    lowered = resolve.lower()
    for forbidden in (
        "insert into public.jobs",
        "insert into public.publications",
        "update public.jobs",
        "update public.publications",
        "update public.content_items",
        "update public.content_versions",
        "delete from public.jobs",
        "delete from public.publications",
        "record_manual_publication_observation",
        "claim_exact_telegram_publication_job",
        "mark_exact_telegram_attempt_started",
        "complete_exact_telegram_publication_job",
        "fail_exact_telegram_publication_job",
    ):
        assert forbidden not in lowered
    assert lowered.count(
        "insert into private.exact_telegram_delivery_unknown_resolutions"
    ) == 1
    assert lowered.count("insert into public.event_log") == 1
    assert "'publication_status', 'delivery_unknown'" in resolve
    assert "'job_status', 'failed'" in resolve
    assert "'delivery_outcome', committed.delivery_outcome" in resolve
    assert "'resend_authorized', false" in resolve
    assert "'provider_calls', 0" in resolve
    assert "'database_claims', 0" in resolve


def test_approval_and_resolution_events_are_exact_once_and_never_claim_success() -> None:
    approve = _function(APPROVE_RPC)
    resolve = _function(RESOLVE_RPC)
    assert approve.count(
        "'exact_telegram_delivery_unknown_resolution_approved'"
    ) == 1
    resolution_event_type = (
        "exact_telegram_delivery_unknown_resolved_without_resend"
    )
    assert resolve.count(f"'{resolution_event_type}'") == 1
    resolution_event = resolve.split("insert into public.event_log", 1)[1].split(
        "return pg_catalog.jsonb_build_object", 1
    )[0]
    for fact in (
        "'delivery_outcome', committed.delivery_outcome",
        "'disposition', committed.disposition",
        "'public_observation', committed.public_observation",
        "'resend_authorized', false",
        "'automatic_publication', false",
        "'provider_calls', 0",
        "'database_claims', 0",
        "'publication_state_changed', false",
        "'job_state_changed', false",
    ):
        assert fact in resolution_event
    for ambiguous in (
        "'published'",
        "'delivered'",
        "'not_delivered'",
        "'external_publish_performed', false",
        "'message_id'",
        "'external_url'",
    ):
        assert ambiguous not in resolution_event


def test_approval_and_resolution_replay_are_receipt_backed_and_write_nothing() -> None:
    approve = _function(APPROVE_RPC)
    resolve = _function(RESOLVE_RPC)
    approval_replay = approve.split("\n    if found then", 1)[1].split(
        "subject := private.exact_telegram_delivery_resolution_subject", 1
    )[0]
    assert "'reused', true" in approval_replay
    assert "insert into" not in approval_replay.lower()
    assert "existing.resolution_id is distinct from target_resolution_id" in (
        approval_replay
    )
    assert "existing.approval_subject_sha256" in approval_replay

    resolution_replay = resolve.split("\n    if found then", 1)[1].split(
        "select approval.* into approved", 1
    )[0]
    assert "'reused', true" in resolution_replay
    assert "insert into" not in resolution_replay.lower()
    assert resolution_replay.count(
        "exact_telegram_delivery_unknown_resolved_without_resend"
    ) == 0
    assert "existing.resolution_id is distinct from target_resolution_id" in (
        resolution_replay
    )
    assert "existing.public_audit is distinct from target_public_audit" in (
        resolution_replay
    )
    assert "existing.approval_subject_sha256" in resolution_replay
