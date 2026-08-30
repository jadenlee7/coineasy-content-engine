import re
from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20260829120000_content_qa_jobs.sql"
).read_text(encoding="utf-8").lower()


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _private_function(name: str) -> str:
    match = re.search(
        rf"create or replace function private\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def test_content_qa_receipt_is_provider_neutral_private_and_exactly_once():
    table = re.search(
        r"create table private\.content_qa_jobs \(.*?\n\);",
        MIGRATION,
        re.DOTALL,
    )
    assert table is not None
    table_sql = table.group(0)
    assert "create table private.content_qa_jobs" in MIGRATION
    assert "primary key (workspace_id, content_version_id, policy_version)" in MIGRATION
    assert "unique (job_id)" in MIGRATION
    assert "status text not null default 'reviewed' check (status = 'reviewed')" in MIGRATION
    assert "references public.content_versions" in MIGRATION
    assert "references public.source_items" in MIGRATION
    assert "enable row level security" in MIGRATION
    assert "force row level security" in MIGRATION
    assert "revoke all on table private.content_qa_jobs" in MIGRATION
    assert "from public, anon, authenticated, service_role" in MIGRATION
    assert "provider_response" not in table_sql
    assert "x_search" not in table_sql
    assert "delivery" not in table_sql
    assert "grok" not in table_sql
    assert "xai" not in table_sql
    assert "telegram" not in table_sql


def test_record_rpc_locks_and_fences_the_exact_current_review_version():
    record = _function("record_content_qa_verdict")
    assert "from public.content_items as current_item" in record
    assert "for update" in record
    assert "item.content_kind is distinct from 'daily_news'" in record
    assert "item.status is distinct from 'needs_review'" in record
    assert "item.current_version_id is distinct from target_content_version_id" in record
    assert "version.generation_meta -> 'mock_mode' = 'true'::jsonb" in record
    assert "primary_source_count <> 1" in record
    assert "primary_source.source_type is distinct from 'tweet'" in record
    assert "primary_source.author_handle is distinct from expected_handle" in record
    assert "primary_source.external_id is distinct from" in record
    assert "statement_timestamp() - interval '24 hours'" in record
    assert "official_feed.provider is distinct from 'x'" in record
    assert "official_feed.active is not true" in record
    assert "official_feed.poll_interval_minutes is distinct from 15" in record
    assert "statement_timestamp() - interval '30 minutes'" in record
    assert "latest_source_id is distinct from primary_source.id" in record
    assert "version.deliverables ->> 'primary_asset_id'" in record
    assert "asset.asset_kind = 'png'" in record
    assert "asset.storage_bucket = 'content-studio'" in record
    assert "asset.mime_type = 'image/png'" in record
    assert "asset.metadata ->> 'filename' = 'news-card.png'" in record
    assert "asset.sha256 ~ '^[a-f0-9]{64}$'" in record
    assert "approval_count <> 0 or publication_count <> 0" in record


def test_record_rpc_requires_one_natural_official_x_generation():
    record = _function("record_content_qa_verdict")
    assert "from public.jobs as review_job" in record
    assert "review_job.workspace_id = target_workspace_id" in record
    assert "review_job.client_id = item.client_id" in record
    assert "review_job.content_item_id = target_content_item_id" in record
    assert "review_job.job_kind = 'generate'" in record
    assert "review_job.status = 'succeeded'" in record
    assert "review_job.input ->> 'workflow' = 'official_x_review_draft_v1'" in record
    assert "review_job.input -> 'manual_only' = 'false'::jsonb" in record
    assert "jsonb_build_array(primary_source.id::text)" in record
    assert "review_job.output ->> 'content_item_id'" in record
    assert "review_job.output ->> 'content_version_id'" in record
    assert "generate_job_count <> 1" in record
    assert "'generate_job_id', generate_job_id" in record


def test_record_rpc_requires_the_exact_expected_provenance_tuple():
    record = _function("record_content_qa_verdict")
    for parameter in (
        "target_expected_generate_job_id uuid",
        "target_expected_source_item_id uuid",
        "target_expected_source_canonical_url text",
        "target_expected_source_published_at timestamptz",
        "target_expected_banner_sha256 text",
    ):
        assert parameter in record
    assert "target_expected_generate_job_id is distinct from generate_job_id" in record
    assert "target_expected_source_item_id is distinct from primary_source.id" in record
    assert "is distinct from primary_source.canonical_url" in record
    assert "is distinct from primary_source.published_at" in record
    assert "target_expected_banner_sha256 is distinct from banner_hash" in record
    assert "content qa expected provenance does not match current evidence" in record
    assert record.index("target_expected_generate_job_id is distinct") < record.index(
        "insert into private.content_qa_jobs"
    )


def test_record_rpc_validates_bounded_verdict_and_official_source_subset():
    record = _function("record_content_qa_verdict")
    assert "target_policy_version is distinct from 'official-x-content-qa@1'" in record
    assert "target_reviewer_principal is distinct from 'codex:content-qa'" in record
    assert "target_reviewer_model is distinct from 'codex'" in record
    assert "target_verdict ->> 'decision' not in ('pass', 'warn', 'block')" in record
    assert "jsonb_array_length(target_verdict -> 'issues') > 3" in record
    assert "content qa pass evidence is incomplete" in record
    assert "content qa block evidence is incomplete" in record
    assert "jsonb_build_array(primary_source.canonical_url)" in record
    assert "content qa verdict source evidence is invalid" in record
    assert "verdict_issue.value ->> 'evidence_url'" in record


def test_record_rpc_calculates_hashes_and_is_idempotent_without_side_effects():
    record = _function("record_content_qa_verdict")
    assert "coineasy.content_qa.review_input.v1" in record
    assert "calculated_input_sha256 := encode(extensions.digest" in record
    assert "calculated_verdict_sha256 := encode(extensions.digest" in record
    assert "insert into private.content_qa_jobs" in record
    assert (
        "on conflict (workspace_id, content_version_id, policy_version) do nothing"
        in record
    )
    assert "'recorded', false" in record
    assert "'status', 'duplicate_conflict'" in record
    assert "'recorded', recorded" in record
    assert "'status', 'reviewed'" in record
    assert "insert into public.approvals" not in record
    assert "insert into public.publications" not in record
    assert "update public.content_items" not in record
    assert "update public.content_versions" not in record


def test_record_rpc_atomically_fences_the_legacy_grok_path():
    record = _function("record_content_qa_verdict")
    outbox_insert_fence = _private_function("fence_grok_insert_after_content_qa")
    receipt_insert_fence = _private_function("block_grok_receipt_after_content_qa")
    assert "from private.grok_qa_dispatch_outbox as legacy" in record
    assert "from private.grok_qa_verdict_receipts as legacy_receipt" in record
    assert "for update" in record
    assert "grok_receipt_found" in record
    assert "grok_dispatch.status is distinct from 'pending'" in record
    assert "grok_dispatch.provider_attempt_started_at is not null" in record
    assert "grok_dispatch.verdict is not null" in record
    assert "grok_dispatch.status is distinct from 'obsolete'" in record
    assert "grok_dispatch.content_qa_job_id is distinct from receipt.job_id" in record
    assert "update private.grok_qa_dispatch_outbox as legacy" in record
    assert "set status = 'obsolete'" in record
    assert "content_qa_job_id = receipt.job_id" in record
    assert "content qa could not atomically fence the grok path" in record
    assert "create trigger enforce_content_qa_grok_fence" in MIGRATION
    assert "content qa fenced grok rows cannot become claimable" in MIGRATION
    assert "create trigger fence_grok_insert_after_content_qa" in MIGRATION
    assert "before insert on private.grok_qa_dispatch_outbox" in MIGRATION
    assert "private.fence_grok_insert_after_content_qa()" in MIGRATION
    assert "new.status := 'obsolete'" in MIGRATION
    assert "new.content_qa_job_id := existing_content_qa_job_id" in MIGRATION
    assert "create trigger block_grok_receipt_after_content_qa" in MIGRATION
    assert "before insert on private.grok_qa_verdict_receipts" in MIGRATION
    assert "private.block_grok_receipt_after_content_qa()" in MIGRATION
    assert "grok receipt is blocked by an existing content qa receipt" in MIGRATION
    advisory_key = "'content-qa:' ||"
    assert MIGRATION.count(advisory_key) == 3
    assert MIGRATION.count("pg_catalog.pg_advisory_xact_lock(") == 3
    assert record.index("from public.content_items as current_item") < record.index(
        "pg_catalog.pg_advisory_xact_lock("
    )
    for insert_fence in (outbox_insert_fence, receipt_insert_fence):
        assert "from public.content_items as target_item" in insert_fence
        assert "for key share" in insert_fence
        assert insert_fence.index("for key share") < insert_fence.index(
            "pg_catalog.pg_advisory_xact_lock("
        )
    assert MIGRATION.count("grok_dispatch_found boolean := false;") == 1


def test_rpcs_use_dedicated_least_privilege_role_and_readback_is_bounded():
    readback = _function("get_content_qa_job")
    assert "security definer" in readback
    assert "stable" in readback
    assert "source_canonical_url" not in readback
    assert "source_published_at" not in readback
    assert "job.verdict," not in readback
    assert "reviewed_at" in readback
    assert "banner_sha256" in readback
    assert "input_sha256" in readback
    assert "verdict_sha256" in readback
    assert "grant execute on function public.record_content_qa_verdict" in MIGRATION
    assert "grant execute on function public.get_content_qa_job" in MIGRATION
    assert "create role coineasy_content_qa" in MIGRATION
    assert "grant coineasy_content_qa to authenticator" in MIGRATION
    assert "to coineasy_content_qa" in MIGRATION
    assert "grant execute on function public.list_content_qa_library" in MIGRATION
    assert "grant execute on function public.get_content_qa_library_item" in MIGRATION
    assert "grant execute on function public.get_content_qa_readiness" in MIGRATION
    assert "grant select on table storage.objects to coineasy_content_qa" in MIGRATION
    assert "create policy content_qa_objects_select on storage.objects" in MIGRATION
    assert "private.content_qa_can_read_storage_object(name)" in MIGRATION
    storage_guard = _private_function("content_qa_can_read_storage_object")
    assert "item.current_version_id = asset.content_version_id" in storage_guard
    assert "item.status = 'needs_review'" in storage_guard
    assert "item.content_kind = 'daily_news'" in storage_guard
    assert "version.deliverables ->> 'primary_asset_id' = asset.id::text" in storage_guard
    assert "asset.metadata ->> 'filename' = 'news-card.png'" in storage_guard
    assert "private.content_qa_scope_matches(target_workspace_id)" in MIGRATION
    grants = MIGRATION.split(
        "grant execute on function public.record_content_qa_verdict", 1
    )[1]
    assert "to authenticated" not in grants
    assert "to service_role" not in grants
