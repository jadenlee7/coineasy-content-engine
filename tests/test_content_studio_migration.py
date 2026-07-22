"""Static safety checks for the proposed Content Studio Supabase migration.

These tests do not need a linked Supabase project. A staging database must still
execute the migration before production rollout; these checks prevent easy-to-miss
RLS, grant, queue-index, and private-bucket regressions in code review.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = next(
    (ROOT / "supabase" / "migrations").glob("*_content_studio_foundation.sql")
)
SQL = MIGRATION.read_text(encoding="utf-8")
DB_SECURITY_TEST = (
    ROOT / "supabase" / "tests" / "content_studio_security.sql"
).read_text(encoding="utf-8")

PUBLIC_TABLES = {
    "workspaces",
    "workspace_members",
    "workspace_clients",
    "source_feeds",
    "source_items",
    "content_items",
    "content_source_links",
    "content_versions",
    "assets",
    "jobs",
    "approvals",
    "publications",
    "figma_links",
    "event_log",
}


def test_all_content_studio_tables_enable_rls_and_have_policies() -> None:
    created = set(re.findall(r"create table public\.([a-z_]+)\s*\(", SQL))
    assert created == PUBLIC_TABLES

    for table in PUBLIC_TABLES:
        assert f"alter table public.{table} enable row level security;" in SQL
        assert re.search(rf"create policy [a-z_]+ on public\.{table}\b", SQL)


def test_public_tables_have_explicit_grants_and_no_anon_policy() -> None:
    revoke_block = re.search(
        r"revoke all on table\s+(.*?)\s+from public, anon, authenticated;",
        SQL,
        re.DOTALL,
    )
    assert revoke_block is not None
    for table in PUBLIC_TABLES:
        assert f"public.{table}" in revoke_block.group(1)
    assert "from public, anon, authenticated, service_role;" in SQL

    policy_statements = re.findall(r"create policy .*?;", SQL, re.DOTALL)
    assert policy_statements
    assert all(re.search(r"\bto authenticated\b", policy) for policy in policy_statements)
    assert not re.search(r"\bto\s+(anon|public)\b", "\n".join(policy_statements))


def test_membership_helpers_are_private_hardened_and_not_jwt_metadata_based() -> None:
    helper_names = (
        "current_workspace_ids",
        "current_write_workspace_ids",
        "current_admin_workspace_ids",
        "current_owner_workspace_ids",
        "can_read_storage_path",
    )
    for name in helper_names:
        match = re.search(
            rf"create or replace function private\.{name}\([^)]*\).*?\$\$;",
            SQL,
            re.DOTALL,
        )
        assert match is not None, name
        function_sql = match.group(0)
        assert "security definer" in function_sql
        assert "set search_path = ''" in function_sql

    assert "raw_user_meta_data" not in SQL
    assert "user_metadata" not in SQL
    assert "from public.workspace_members" in SQL


def test_queue_has_partial_claim_recovery_and_idempotency_indexes() -> None:
    assert re.search(
        r"create index jobs_dequeue_idx\s+on public\.jobs .*?"
        r"where status in \('queued', 'retrying'\);",
        SQL,
        re.DOTALL,
    )
    assert re.search(
        r"create index jobs_lease_recovery_idx\s+on public\.jobs .*?"
        r"where status = 'running' and lease_expires_at is not null;",
        SQL,
        re.DOTALL,
    )
    assert re.search(
        r"create unique index jobs_idempotency_idx\s+on public\.jobs .*?"
        r"where idempotency_key is not null;",
        SQL,
        re.DOTALL,
    )


def test_foreign_key_and_rls_lookup_indexes_are_declared() -> None:
    required_indexes = {
        "workspace_members_user_id_idx",
        "workspace_members_invited_by_idx",
        "source_feeds_workspace_client_idx",
        "source_items_workspace_feed_idx",
        "content_items_workspace_client_kind_idx",
        "content_source_links_workspace_source_idx",
        "content_versions_workspace_item_idx",
        "assets_workspace_version_idx",
        "jobs_workspace_content_idx",
        "approvals_workspace_version_idx",
        "approvals_reviewer_id_idx",
        "publications_workspace_version_idx",
        "figma_links_workspace_version_idx",
        "event_log_actor_id_idx",
    }
    declared = set(
        re.findall(r"create (?:unique )?index ([a-z_]+)\s+on public\.", SQL)
    )
    assert required_indexes <= declared


def test_storage_bucket_is_private_and_workspace_scoped() -> None:
    assert "'content-studio',\n    'content-studio',\n    false," in SQL
    assert "create policy content_studio_objects_select on storage.objects" in SQL
    assert "private.can_read_storage_path(name)" in SQL
    assert "create policy content_studio_objects_insert" not in SQL
    assert "create policy content_studio_objects_update" not in SQL
    assert "create policy content_studio_objects_delete" not in SQL


def test_append_only_tables_are_not_mutable_by_authenticated_role() -> None:
    assert "grant select, insert on table public.content_versions to authenticated;" in SQL
    assert "grant select on table public.approvals to authenticated;" in SQL

    for table in ("content_versions", "approvals", "event_log", "assets"):
        assert not re.search(
            rf"grant .*\b(update|delete)\b.*public\.{table} to authenticated;",
            SQL,
        )


def test_service_role_can_append_but_not_rewrite_history() -> None:
    append_grant = re.search(
        r"grant select, insert on table\s*\n"
        r"\s*public\.content_versions,\s*\n"
        r"\s*public\.approvals,\s*\n"
        r"\s*public\.event_log\s*\n"
        r"to service_role;",
        SQL,
    )
    assert append_grant is not None

    service_all_grant = re.search(
        r"grant all privileges on table\s+(.*?)\s+to service_role;",
        SQL,
        re.DOTALL,
    )
    assert service_all_grant is not None
    for table in ("content_versions", "approvals", "event_log"):
        assert f"public.{table}" not in service_all_grant.group(1)
    for table in ("workspaces", "content_items"):
        assert f"public.{table}" not in service_all_grant.group(1)


def test_workspace_and_version_history_cannot_be_cascade_deleted() -> None:
    assert "grant select, insert, delete on table public.workspaces to authenticated;" not in SQL
    assert "create policy workspaces_delete_owner" not in SQL
    assert (
        "references public.content_items(workspace_id, id) on delete restrict"
        in SQL
    )
    assert (
        "workspace_id uuid not null references public.workspaces(id) on delete restrict"
        in SQL
    )


def test_last_owner_changes_are_serialized_by_workspace_lock() -> None:
    function_sql = re.search(
        r"create or replace function private\.prevent_last_workspace_owner\(\)"
        r".*?\$\$;",
        SQL,
        re.DOTALL,
    )
    assert function_sql is not None
    body = function_sql.group(0)
    assert "from public.workspaces" in body
    assert "where id = old.workspace_id" in body
    assert "for update;" in body
    assert body.index("for update;") < body.index("from public.workspace_members")


def test_version_children_cannot_reference_a_version_from_another_item() -> None:
    assert "unique (workspace_id, content_item_id, id)" in SQL
    composite_version_fk = re.compile(
        r"foreign key \(workspace_id, content_item_id, content_version_id\)\s+"
        r"references public\.content_versions\(workspace_id, content_item_id, id\)",
        re.DOTALL,
    )
    assert len(composite_version_fk.findall(SQL)) == 4

    current_version_check = re.search(
        r"create or replace function private\.validate_current_version\(\).*?\$\$;",
        SQL,
        re.DOTALL,
    )
    assert current_version_check is not None
    assert "cv.content_item_id = new.id" in current_version_check.group(0)
    assert "cv.workspace_id = new.workspace_id" in current_version_check.group(0)


def test_client_and_item_relationships_use_composite_foreign_keys() -> None:
    assert "unique (workspace_id, client_id, id)" in SQL
    assert SQL.count("unique (workspace_id, client_id, id)") == 3
    assert SQL.count(
        "foreign key (workspace_id, client_id, content_item_id)"
    ) == 4
    assert (
        "foreign key (workspace_id, client_id, source_feed_id)\n"
        "        references public.source_feeds(workspace_id, client_id, id)"
    ) in SQL
    assert (
        "foreign key (workspace_id, client_id, source_item_id)\n"
        "        references public.source_items(workspace_id, client_id, id)"
    ) in SQL


def test_sensitive_tables_use_safe_views_and_server_managed_writes() -> None:
    for view in (
        "content_studio_source_feeds",
        "content_studio_source_items",
        "content_studio_job_statuses",
        "content_studio_publication_statuses",
        "content_studio_activity",
    ):
        assert f"create view public.{view}" in SQL
    assert SQL.count("security_invoker = true, security_barrier = true") == 5

    assert "credential_ref" not in re.search(
        r"create view public\.content_studio_source_feeds.*?from public\.source_feeds;",
        SQL,
        re.DOTALL,
    ).group(0)
    assert "raw_payload" not in re.search(
        r"create view public\.content_studio_source_items.*?from public\.source_items;",
        SQL,
        re.DOTALL,
    ).group(0)
    job_view = re.search(
        r"create view public\.content_studio_job_statuses.*?from public\.jobs;",
        SQL,
        re.DOTALL,
    ).group(0)
    for field in ("input", "output", "locked_by", "lease_expires_at", "last_error_message"):
        assert field not in job_view
    publication_view = re.search(
        r"create view public\.content_studio_publication_statuses.*?"
        r"from public\.publications;",
        SQL,
        re.DOTALL,
    ).group(0)
    for field in ("request_payload", "response_payload", "last_error"):
        assert field not in publication_view

    assert "grant select, insert on table public.jobs to authenticated;" not in SQL
    assert "grant select, insert on table public.publications to authenticated;" not in SQL
    assert "grant update (title) on table public.content_items to authenticated;" in SQL


def test_workflow_transitions_are_membership_checked_rpcs() -> None:
    for function in (
        "queue_content_generation",
        "review_content_version",
        "request_content_publication",
        "record_approved_figma_link",
    ):
        match = re.search(
            rf"create or replace function public\.{function}\(.*?\n\$\$;",
            SQL,
            re.DOTALL,
        )
        assert match is not None, function
        function_sql = match.group(0)
        assert "security definer" in function_sql
        assert "set search_path = ''" in function_sql
        assert "from public.workspace_members" in function_sql

    assert "and status = 'draft'" in SQL
    assert "and current_version_id is null" in SQL


def test_tutorial_catalog_rpc_is_service_only_idempotent_and_storage_aligned() -> None:
    function_sql = re.search(
        r"create or replace function public\.record_tutorial_generation\(.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert function_sql is not None
    body = function_sql.group(0)
    assert "security definer" in body
    assert "set search_path = ''" in body
    assert "pg_catalog.pg_advisory_xact_lock" in body
    assert "asset_id := path_parts[3]::uuid" in body
    assert "from storage.objects" in body
    assert "for key share" in body
    assert "path_parts[1] <> target_workspace_id::text" in body
    assert "(slide ->> 'number')::integer <> slide_ordinal" in body
    assert "jsonb_typeof(target_slides) is distinct from 'array'" in body
    assert "content_version_id = existing_version.id" in body
    assert "effective_generation_meta" in body
    assert "target_content ->> 'request_hash'" in body
    assert "target_generation_meta ->> 'request_hash'" in body
    assert "tutorial request hash is missing or inconsistent" in body
    assert re.search(
        r"revoke all on function public\.record_tutorial_generation\(.*?\)\s*"
        r"from public, anon, authenticated;",
        SQL,
        re.DOTALL,
    )
    assert re.search(
        r"grant execute on function public\.record_tutorial_generation\(.*?\)\s*"
        r"to service_role;",
        SQL,
        re.DOTALL,
    )
    lookup_sql = re.search(
        r"create or replace function public\.get_tutorial_generation\(.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert lookup_sql is not None
    assert "security definer" in lookup_sql.group(0)
    assert "set search_path = ''" in lookup_sql.group(0)
    assert "version_number = 1" in lookup_sql.group(0)
    assert "version.deliverables -> 'asset_ids'" in lookup_sql.group(0)
    assert "jsonb_array_elements_text(expected_asset_ids) with ordinality" in lookup_sql.group(0)
    assert "join storage.objects as stored" in lookup_sql.group(0)
    assert "resolved_asset_count <> expected_asset_count" in lookup_sql.group(0)
    assert "order by expected.ordinality" in lookup_sql.group(0)
    assert "revoke all on function public.get_tutorial_generation" in SQL
    assert re.search(
        r"grant execute on function public\.get_tutorial_generation\(.*?\)\s*"
        r"to service_role;",
        SQL,
        re.DOTALL,
    )


def test_publication_rpc_supports_channels_and_idempotent_late_retries() -> None:
    function_sql = re.search(
        r"create or replace function public\.request_content_publication\(.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert function_sql is not None
    body = function_sql.group(0)
    assert body.index("if found then") < body.index("scheduled time must be in the future")
    assert "item.status not in ('approved', 'scheduled')" in body
    assert "least(item.scheduled_for, target_scheduled_for)" in body


def test_mock_versions_cannot_cross_approval_or_publication_boundaries() -> None:
    review_sql = re.search(
        r"create or replace function public\.review_content_version\(.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    publication_sql = re.search(
        r"create or replace function public\.request_content_publication\(.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert review_sql is not None
    assert publication_sql is not None
    review_body = review_sql.group(0)
    publication_body = publication_sql.group(0)
    assert "review_decision = 'approved'" in review_body
    assert "target_version.generation_meta @> '{\"mock_mode\":true}'::jsonb" in review_body
    assert "mock/test content cannot be approved" in review_body
    assert "target_version.generation_meta @> '{\"mock_mode\":true}'::jsonb" in publication_body
    assert "mock/test content cannot be published" in publication_body


def test_figma_links_are_rpc_only_and_require_current_approval() -> None:
    assert "grant select on table public.figma_links to authenticated;" in SQL
    assert not re.search(
        r"grant .*\b(insert|update|delete)\b.*public\.figma_links to authenticated;",
        SQL,
    )
    for policy in (
        "figma_links_insert_editor",
        "figma_links_update_editor",
        "figma_links_delete_editor",
    ):
        assert policy not in SQL

    function_sql = re.search(
        r"create or replace function public\.record_approved_figma_link\(.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert function_sql is not None
    body = function_sql.group(0)
    assert "item.current_version_id is distinct from target_content_version_id" in body
    assert "from public.approvals" in body
    assert "and decision = 'approved'" in body
    assert "on conflict (content_version_id, file_key, node_id) do update" in body


def test_transactional_database_smoke_covers_integrity_and_role_matrix() -> None:
    assert DB_SECURITY_TEST.startswith("-- Integration smoke")
    assert DB_SECURITY_TEST.rstrip().endswith("rollback;")
    for expected_failure in (
        "cross-item asset was accepted",
        "cross-item publication was accepted",
        "cross-item approval was accepted",
        "cross-item Figma link was accepted",
        "cross-client job was accepted",
        "cross-client source feed was accepted",
        "credential_ref was exposed",
        "raw source payload was exposed",
        "job input was exposed",
        "publication payload was exposed",
        "editor directly changed workflow status",
        "editor directly inserted a job",
        "editor directly inserted a publication",
        "editor directly inserted a Figma link",
        "editor directly updated a Figma link",
        "editor directly deleted a Figma link",
        "editor cataloged a tutorial",
        "unapproved Figma link was accepted",
        "editor deleted an immutable asset",
        "viewer queued generation",
        "multi-channel publication did not queue both channels",
        "multi-channel publication changed the approved version",
        "service role rewrote a content version",
        "service role deleted an approval",
        "service role rewrote event history",
        "owner directly deleted a workspace",
        "service role deleted a versioned content item",
        "service role deleted a workspace history",
        "last workspace owner was deleted",
        "tutorial catalog RPC was not idempotent",
        "tutorial catalog asset path was not aligned",
        "tutorial catalog lookup did not return the committed generation",
        "null tutorial slides were accepted",
        "null tutorial slide fields were accepted",
        "out-of-order tutorial slides were accepted",
        "missing tutorial storage object was accepted",
        "mock tutorial was approved",
        "mock tutorial was queued for publication",
        "mock tutorial could not be rejected",
    ):
        assert expected_failure in DB_SECURITY_TEST
