"""Static fail-closed contract for the durable planning-only agent ledger."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from core.agent_control.models import AgentWorkOrder, ForbiddenAction


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "supabase/migrations/20260825130000_agent_work_order_ledger.sql"
)
ROLE_MIGRATION_PATH = (
    ROOT / "supabase/migrations/20260825131000_agent_work_order_roles.sql"
)
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
ROLE_MIGRATION = ROLE_MIGRATION_PATH.read_text(encoding="utf-8")

TABLES = (
    "agent_work_orders",
    "agent_work_order_events",
    "agent_runs",
    "agent_dispatch_outbox",
    "agent_action_receipts",
    "agent_incidents",
)

RPC_SIGNATURES = {
    "propose_agent_work_order": "uuid, jsonb, text",
    "authorize_agent_work_order": "uuid, uuid, text, bigint",
    "record_agent_operator_decision": (
        "uuid, uuid, text, bigint, text, text"
    ),
    "complete_agent_work_order": "uuid, uuid, text, bigint",
    "list_agent_operator_inbox": "uuid, integer, timestamptz, uuid",
    "get_agent_work_order": "uuid, uuid",
    "get_agent_company_dashboard": "uuid",
}


def _function(name: str, *, schema: str = "public") -> str:
    match = re.search(
        rf"create or replace function {schema}[.]{name}[(].*?\n[$][$];",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, name
    return match.group(0)


def _role_routines(array_name: str) -> frozenset[str]:
    block = re.search(
        rf"{array_name} constant text\[\] := array\[(.*?)\n    \];",
        ROLE_MIGRATION,
        re.DOTALL,
    )
    assert block is not None, array_name
    return frozenset(re.findall(r"'public[.]([a-z_]+)[(]", block.group(1)))


def test_all_six_tables_are_private_force_rls_without_table_grants() -> None:
    lowered = MIGRATION.lower()
    for table in TABLES:
        qualified = f"agent_runtime.{table}"
        assert f"create table {qualified}" in lowered
        assert f"alter table {qualified} enable row level security" in lowered
        assert f"alter table {qualified} force row level security" in lowered
    revoked = re.search(
        r"revoke all on table\s+"
        r"agent_runtime[.]agent_work_orders,.*?"
        r"agent_runtime[.]agent_incidents\s+"
        r"from public, anon, authenticated, service_role;",
        lowered,
        re.DOTALL,
    )
    assert revoked is not None
    assert "create policy" not in lowered
    assert "grant select on table" not in lowered
    assert "grant insert on table" not in lowered
    assert "grant update on table" not in lowered
    assert "grant delete on table" not in lowered


def test_scope_is_exactly_the_existing_zero_authority_contract() -> None:
    validator = _function("agent_work_order_scope_valid", schema="private")
    assert "jsonb_object_keys(target)) <> 31" in validator
    for fragment in (
        "agent-work-order@1",
        "jsonb_typeof(target -> scalar.key) <> 'string'",
        "jsonb_typeof(target -> 'automatic_publication')",
        "jsonb_typeof(target -> 'max_runtime_seconds') <> 'number'",
        "target ->> 'max_runtime_seconds', '') !~ '^[0-9]+$'",
        "target ->> 'requested_by' <> 'human_operator'",
        "target ->> 'work_type' <> 'engineering'",
        "target ->> 'risk_tier' <> 'R1'",
        "target ->> 'allowed_environment' <> 'local'",
        "target -> 'automatic_publication' is distinct from 'false'::jsonb",
        "target -> 'max_cost_microusd' is distinct from '0'::jsonb",
        "target -> 'max_external_actions' is distinct from '0'::jsonb",
        "target -> 'max_handoffs' is distinct from '1'::jsonb",
        "expires_at_value - created_at_value > interval '14 days'",
        "required_actions constant text[]",
        "paid_provider_call",
        "public_message",
        "publication",
    ):
        assert fragment in validator
    assert "owner' = target ->> 'reviewer'" in validator
    assert "agent_safe_text(\n                    target ->> 'client_id'" in validator
    assert "jsonb_typeof(target -> 'parent_work_order_id')" in validator
    assert "verification_commands" in validator
    assert "target = pg_catalog.btrim(target)" in MIGRATION
    assert "glpat-" in MIGRATION
    assert "AKIA[0-9A-Z]{16}" in MIGRATION
    assert "BEGIN [A-Z ]*PRIVATE KEY" in MIGRATION
    assert "count(distinct item.data #>> '{}')" in validator
    assert "item.data #>> '{}', 3, 200, true" in validator
    assert "item.data #>> '{}', 3, 500, true" in validator
    assert "evidence.value ->> 'uri', 1, 2048, false" in validator
    assert "evidence.value ->> 'uri' = '.git'" in validator
    assert "pg_catalog.strpos(path.value #>> '{}', '..') > 0" in validator
    assert "pg_catalog.strpos(evidence.value ->> 'uri', '..') > 0" in validator
    assert "pg_catalog.position(" not in validator
    assert "https" not in validator.lower().split("evidence", 1)[-1]


def test_existing_python_contract_still_has_the_same_canonical_shape() -> None:
    payload = json.loads(
        (ROOT / "examples/agent-work-order-devin-preview.json").read_text(
            encoding="utf-8"
        )
    )
    order = AgentWorkOrder.model_validate(payload)
    canonical = order.canonical_scope()
    assert len(canonical) == 31
    assert canonical["schema_version"] == "agent-work-order@1"
    assert canonical["max_cost_microusd"] == 0
    assert canonical["max_external_actions"] == 0
    assert canonical["automatic_publication"] is False
    assert set(canonical["forbidden_actions"]) == {
        action.value for action in ForbiddenAction
    }
    for evidence in order.evidence:
        assert hashlib.sha256((ROOT / evidence.uri).read_bytes()).hexdigest() == (
            evidence.sha256
        )
    assert re.fullmatch(r"[a-f0-9]{64}", order.scope_sha256)


def test_recursive_canonical_json_matches_the_python_algorithm_contract() -> None:
    canonical = _function("agent_json_canonical", schema="private")
    digest = _function("agent_json_sha256", schema="private")
    assert "jsonb_typeof(target)" in canonical
    assert "jsonb_each(target)" in canonical
    assert "jsonb_array_elements(target)" in canonical
    assert "with ordinality" in canonical
    assert "convert_to(pair.key, 'UTF8')" in canonical
    assert "to_json(pair.key)::text || ':'" in canonical
    assert "',' order by element.ordinality" in canonical
    assert "private.agent_json_canonical(target)" in digest
    assert "convert_to(private.agent_json_canonical(target), 'UTF8')" in digest
    assert "extensions.digest" in digest
    assert "digest(convert_to(target::text" not in MIGRATION.lower()


def test_branch_and_path_collisions_are_casefolded_and_transaction_fenced() -> None:
    branch_key = _function("agent_branch_scope_key", schema="private")
    authorize = _function("authorize_agent_work_order")
    assert "decode('00', 'hex')" in branch_key
    assert "lower(target_repository)" in branch_key
    assert "lower(target_branch_name)" in branch_key
    assert "agent_work_orders_active_branch_idx" in MIGRATION
    assert "where status in (" in MIGRATION
    assert "pg_advisory_xact_lock" in authorize
    assert "hashtextextended" in authorize
    assert (
        "pg_catalog.lower(other.repository) = pg_catalog.lower(work.repository)"
        in authorize
    )
    assert "cross join unnest(work.allowed_paths)" in authorize
    assert "lower(other_path.value) like" in authorize
    assert "lower(target_path.value) like" in authorize


def test_events_and_receipts_are_hash_bound_and_append_only() -> None:
    append = _function("agent_append_event", schema="private")
    immutable = _function("agent_immutable_row", schema="private")
    assert "previous_event_sha256" in MIGRATION
    assert "event_sha256" in MIGRATION
    assert "private.agent_json_sha256(event_body)" in append
    assert "event_seq" in append
    assert "agent_work_order_events_immutable" in MIGRATION
    assert "agent_action_receipts_immutable" in MIGRATION
    assert "before update or delete" in MIGRATION
    assert "append-only" in immutable
    assert "unique (workspace_id, work_order_id, receipt_kind)" in MIGRATION
    assert "payload ->> 'schema_version' = schema_version" in MIGRATION
    assert "payload ->> 'work_order_id' = work_order_id::text" in MIGRATION
    assert "payload ->> 'scope_sha256' = scope_sha256" in MIGRATION
    assert "payload -> 'automatic_publication' = 'false'::jsonb" in MIGRATION
    assert "receipt_kind <> 'verification'" in MIGRATION
    assert "payload -> 'passed' = 'true'::jsonb" in MIGRATION
    for receipt in (
        "authorization",
        "dispatch_delivery",
        "work_result",
        "verification",
        "operator_decision",
        "completion",
    ):
        assert f"'{receipt}'" in MIGRATION


def test_propose_is_human_admin_only_hash_checked_and_idempotent() -> None:
    propose = _function("propose_agent_work_order")
    assert "actor_id uuid := (select auth.uid())" in propose
    assert "private.agent_operator_can_write(target_workspace_id)" in propose
    assert "private.agent_work_order_scope_valid(target_scope)" in propose
    assert "private.agent_json_sha256(target_scope)" in propose
    assert "pg_advisory_xact_lock" in propose
    assert "'agent_work_order_propose:' || target_workspace_id::text" in propose
    assert "work.idempotency_key = target_scope ->> 'idempotency_key'" in propose
    assert "work.scope_sha256 = expected_sha" in propose
    assert "existing.scope is distinct from target_scope" in propose
    assert "'reused', true" in propose
    assert "insert into agent_runtime.agent_work_orders" in propose
    assert "private.agent_append_event" in propose


def test_authorization_atomically_creates_one_receipt_and_pending_dispatch() -> None:
    authorize = _function("authorize_agent_work_order")
    assert "for update" in authorize.lower()
    assert "work.status <> 'proposed'" in authorize
    assert "work.status_version <> target_expected_status_version" in authorize
    assert "work.expires_at <= statement_timestamp()" in authorize
    assert "insert into agent_runtime.agent_action_receipts" in authorize
    assert "'agent-authorization-receipt@1'" in authorize
    assert "insert into agent_runtime.agent_dispatch_outbox" in authorize
    assert "'agent-dispatch-packet@1'" in authorize
    assert "'pending'" in authorize
    assert "'automatic_publication', false" in authorize
    assert "'max_cost_microusd', 0" in authorize
    assert "'max_external_actions', 0" in authorize
    assert authorize.index("insert into agent_runtime.agent_action_receipts") < (
        authorize.index("insert into agent_runtime.agent_dispatch_outbox")
    ) < authorize.index("update agent_runtime.agent_work_orders")


def test_operator_decision_and_completion_require_exact_receipt_chain() -> None:
    decision = _function("record_agent_operator_decision")
    complete = _function("complete_agent_work_order")
    assert "target_decision not in ('approved', 'blocked', 'cancelled')" in decision
    assert "work.status <> 'verified'" in decision
    assert "verification_receipt.result_sha256" in decision
    assert "result_receipt.result_sha256" in decision
    assert "result_receipt.actor_kind is distinct from work.owner" in decision
    assert "verification_receipt.actor_kind is distinct from work.reviewer" in decision
    assert "result_receipt.scope_sha256 is distinct from work.scope_sha256" in decision
    assert "verification_receipt.payload -> 'passed'" in decision
    assert "is distinct from 'true'::jsonb" in decision
    assert "work.status not in ('proposed', 'authorized')" in decision
    assert "status = 'cancelled'" in decision
    assert "work.status <> 'approved'" in complete
    assert "receipt_kind = 'work_result'" in complete
    assert "receipt_kind = 'verification'" in complete
    assert "receipt_kind = 'operator_decision'" in complete
    assert "decision_receipt.payload ->> 'decision' <> 'approved'" in complete
    assert "verification_receipt.result_sha256" in complete
    assert "decision_receipt.verification_sha256" in complete
    assert "'agent-completion-receipt@1'" in complete
    assert "set status = 'completed'" in complete


def test_dashboard_distinguishes_observed_zero_from_unobserved_cost() -> None:
    dashboard = _function("get_agent_company_dashboard")
    assert "sum(run.actual_cost_microusd)" in dashboard
    assert "where run.actual_cost_microusd is null" in dashboard
    assert "'unobserved_run_count', unobserved_runs" in dashboard
    assert "'cost_observation_complete', unobserved_runs = 0" in dashboard
    assert "'max_external_actions', 0" in dashboard
    assert "'automatic_publication', false" in dashboard


def test_public_rpcs_are_hardened_and_service_role_gets_no_grant() -> None:
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
    assert not re.search(
        r"grant execute on function public[.][a-z_]+[(].*?[)]\s*"
        r"to service_role;",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )
    assert "to authenticated;" in MIGRATION


def test_scoped_roles_are_exact_minimal_and_workspace_claim_bound() -> None:
    assert _role_routines("dashboard_routines") == {
        "list_agent_operator_inbox",
        "get_agent_work_order",
        "get_agent_company_dashboard",
    }
    assert _role_routines("control_plane_routines") == {
        "complete_agent_work_order"
    }
    lowered = ROLE_MIGRATION.lower()
    assert "coineasy_agent_dashboard" in lowered
    assert "coineasy_agent_control_plane" in lowered
    for role_attribute in (
        "nologin",
        "noinherit",
        "nobypassrls",
        "nosuperuser",
        "nocreatedb",
        "nocreaterole",
        "noreplication",
    ):
        assert role_attribute in lowered
    assert "grant %i to authenticator" in lowered
    assert "revoke all on table" in lowered
    assert "from service_role" in lowered
    assert "grant select" not in lowered
    scoped = _function("agent_scoped_workspace_matches", schema="private")
    assert "current_setting(" in scoped
    assert "request.jwt.claims" in scoped
    assert "claims ->> 'role'" in scoped
    assert "claims ->> 'workspace_id'" in scoped
    assert "auth.jwt()" not in scoped


def test_no_worker_provider_message_or_publication_path_is_exposed() -> None:
    lowered = MIGRATION.lower()
    for forbidden in (
        "create or replace function public.claim_agent_dispatch",
        "create or replace function public.mark_agent_dispatch_attempt",
        "create or replace function public.complete_agent_dispatch",
        "create or replace function public.fail_agent_dispatch",
        "insert into public.approvals",
        "insert into public.publications",
        "update public.publications",
        "queue_agent_batch_job",
        "claim_grok_qa_dispatch_job",
        "origintrail_buzz",
        "net.http",
        "pg_net",
        "openai_api_key",
        "xai_api_key",
        "telegram_bot_token",
    ):
        assert forbidden not in lowered
    assert "automatic_publication false" not in lowered  # no SQL identifier drift
    assert "automatic_publication', false" in lowered


def test_role_migration_follows_ledger_migration() -> None:
    assert MIGRATION_PATH.name < ROLE_MIGRATION_PATH.name
    for function in RPC_SIGNATURES:
        assert f"public.{function}(" in ROLE_MIGRATION or function in {
            "propose_agent_work_order",
            "authorize_agent_work_order",
            "record_agent_operator_decision",
        }
