"""Static architecture guards only, not proof of executed DB behavior or rollout."""
import hashlib
from pathlib import Path
import re

import pytest

from core.content_ops.prompt_attempt import prompt_instruction


ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / "supabase/proposals/content_ops_button_durable_attempt.sql"
SQL = PROPOSAL.read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)


def normalize(value):
    return re.sub(r"\s+", " ", value).strip().lower()


def function(name):
    match = re.search(
        rf"create\s+function\s+private\.{re.escape(name)}\s*\((.*?)\)"
        rf"\s*(returns\b.*?)\bas\s*\$\$(.*?)\$\$\s*;",
        CODE, re.I | re.S,
    )
    assert match, f"Missing private function {name}"
    return tuple(normalize(part) for part in match.groups())


RECORD = "record_content_ops_button_card"
RESERVE = "reserve_content_ops_button_prompt_attempt"
GUARD = "guard_content_ops_button_durable_record"
TABLES = ("content_ops_button_cards", "content_ops_button_prompt_attempts")


def test_proposal_stays_outside_automatic_migration_tree():
    assert PROPOSAL.parent == ROOT / "supabase/proposals"
    assert "LOCAL PROPOSAL ONLY" in SQL
    assert not any("content_ops_button_durable_attempt" in path.name
                   for path in (ROOT / "supabase/migrations").glob("*.sql"))
    assert normalize(CODE).startswith("begin;") and normalize(CODE).endswith("commit;")


@pytest.mark.parametrize("name", [GUARD, RECORD, RESERVE])
def test_all_functions_are_security_invoker_with_empty_search_path(name):
    _, declaration, _ = function(name)
    assert "security invoker" in declaration
    assert re.search(r"set search_path\s*=\s*''", declaration)
    assert "security definer" not in normalize(CODE)
    assert re.search(rf"\bprivate\.{name}\s*\(", CODE)


@pytest.mark.parametrize("table", TABLES)
def test_both_private_tables_enable_and_force_rls(table):
    for action in ("enable", "force"):
        assert re.search(rf"alter\s+table\s+private\.{table}\s+{action}\s+row\s+level\s+security\s*;",
                         CODE, re.I)
    assert not re.search(r"create\s+policy\b|disable\s+row\s+level\s+security", CODE, re.I)


def test_every_new_table_and_function_revokes_all_runtime_roles_without_grants():
    revokes = re.findall(r"\brevoke\s+all\s+on\s+(.*?)\s+from\s+(.*?);", CODE, re.I | re.S)
    assert revokes
    required_roles = {"public", "anon", "authenticated", "service_role"}
    for target in (*TABLES, GUARD, RECORD, RESERVE):
        matching = [roles for objects, roles in revokes if re.search(rf"\b{target}\b", objects)]
        assert matching, f"Missing REVOKE for {target}"
        assert any({role.strip().lower() for role in roles.split(",")} == required_roles
                   for roles in matching)
    assert not re.search(r"\bgrant\s+|alter\s+default\s+privileges", CODE, re.I)


@pytest.mark.parametrize("table", TABLES)
def test_immutable_trigger_covers_update_and_delete_on_each_table(table):
    assert re.search(
        rf"create\s+trigger\s+\w+\s+before\s+update\s+or\s+delete\s+"
        rf"on\s+private\.{table}\s+for\s+each\s+row\s+execute\s+function\s+"
        rf"private\.{GUARD}\s*\(\s*\)\s*;", CODE, re.I | re.S,
    )


def test_only_card_active_to_inactive_transition_can_escape_immutable_guard():
    _, _, body = function(GUARD)
    assert "tg_table_name='content_ops_button_cards'" in body
    assert "tg_op='update'" in body
    assert "old.active and new.active is false" in body
    assert "(to_jsonb(old)-'active')=(to_jsonb(new)-'active')" in body
    assert body.count("return new") == 1
    assert "raise exception 'button_durable_record_immutable'" in body
    assert "errcode='23514'" in body


def test_card_bindings_and_parts_accept_only_exact_bounded_keys():
    _, _, body = function(RECORD)
    assert "jsonb_typeof(target_bindings) is distinct from 'object'" in body
    assert "jsonb_typeof(target_parts) is distinct from 'array'" in body
    assert "jsonb_array_length(target_parts)<>3" in body
    assert "array['bot','card_receipt','message','packet_receipt','parent_binding','room','thread_id']" in body
    assert "array['kind','message_binding','outcome','payload_sha256']" in body
    assert "jsonb_object_keys(target_bindings)" in body and "jsonb_object_keys(p)" in body
    assert body.count("array_agg(key order by key)") == 2
    assert "jsonb_typeof(target_bindings->k) is distinct from 'string'" in body
    assert "^[a-f0-9]{64}$" in body


def test_three_ordered_sent_parts_and_four_distinct_messages_are_required():
    _, _, body = function(RECORD)
    assert "messages:=array[target_bindings->>'message']" in body
    assert "for n in 0..2 loop" in body
    assert "(array['image','telegram','x'])[n+1]" in body
    assert "p->>'outcome' is distinct from 'sent'" in body
    assert "p->>'message_binding'=any(messages)" in body
    assert "messages:=array_append(messages,p->>'message_binding')" in body
    assert "jsonb_typeof(p->'message_binding') is distinct from 'string'" in body
    assert "jsonb_typeof(p->'payload_sha256') is distinct from 'string'" in body


def test_recording_requires_active_current_exact_version_with_no_approval_or_publication():
    _, _, body = function(RECORD)
    for condition in (
        "r.state is distinct from 'active'", "r.epoch is distinct from target_epoch",
        "r.version_fingerprint is distinct from expected_fingerprint",
        "r.version_fingerprint is distinct from private.content_ops_button_version_fingerprint(",
        "r.workspace_id,r.content_item_id,r.content_version_id",
        "i.workspace_id=r.workspace_id and i.current_version_id=r.content_version_id",
        "i.status='needs_review'", "public.workspace_clients",
        "client_id=r.client_id and active for share",
        "exists(select 1 from public.approvals", "exists(select 1 from public.publications",
    ):
        assert condition in body
    assert "delivered<r.created_at or delivered>observed or expires<=observed" in body
    assert "expires>r.expires_at or expires>delivered+interval '30 minutes'" in body


@pytest.mark.parametrize("name", [RECORD, RESERVE])
def test_wall_clock_checks_happen_after_item_review_and_client_locks(name):
    _, _, body = function(name)
    item = body.index("perform 1 from public.content_items")
    review = body.index("select * into r from private.content_ops_button_reviews")
    client = body.index("perform 1 from public.workspace_clients")
    observed = body.index("observed:=clock_timestamp()")
    assert item < review < client < observed
    assert "for update" in body[item:review]
    assert "for update" in body[review:client]
    assert "for share" in body[client:observed]
    assert "r.workspace_id is distinct from initial.workspace_id" in body
    assert "r.content_item_id is distinct from initial.content_item_id" in body
    assert not re.search(r"\bnow\s*\(|current_timestamp", body)


def test_reservation_uses_actual_active_identity_and_client_scoped_reviewer():
    signature, _, body = function(RESERVE)
    assert "verified_human_binding text" in signature
    assert "target_actor_id uuid" in signature
    assert not any(word in signature for word in ("active", "outcome", "role", "permission"))
    for condition in (
        "private.content_ops_button_identities i", "i.workspace_id=r.workspace_id",
        "i.bot_binding=c.bindings->>'bot'", "i.human_binding=verified_human_binding and i.active for share",
        "actor is distinct from target_actor_id", "private.content_ops_button_reviewers v",
        "v.workspace_id=r.workspace_id", "v.client_id=r.client_id",
        "v.actor_id=actor and v.active for share",
    ):
        assert condition in body
    assert body.count("errcode='42501'") >= 2


def test_reservation_requires_exact_edit_action_and_immediate_successor_epoch():
    _, _, body = function(RESERVE)
    for condition in (
        "idempotency_key=target_action_key for share",
        "a.actor_id is distinct from actor", "a.epoch is distinct from r.epoch",
        "a.action not in ('edit_telegram','edit_x','edit_banner')", "a.result_status is distinct from 'edit_requested'",
        "a.version_fingerprint is distinct from r.version_fingerprint",
        "a.created_at<c.delivered_at", "a.created_at>observed",
        "r.epoch<>c.epoch+1", "not c.active", "r.state is distinct from 'edit_requested'",
        "c.version_fingerprint is distinct from r.version_fingerprint",
        "private.content_ops_button_check_state(r.id,actor)",
        "state->>'status' is distinct from 'edit_requested'",
    ):
        assert condition in body


def test_attempt_expiry_is_bounded_by_existing_card_review_and_wall_clock():
    _, _, body = function(RESERVE)
    assert "expiry:=least(r.expires_at,c.expires_at,c.delivered_at+interval '30 minutes',observed+interval '30 minutes')" in body
    assert "expiry<=observed" in body and "c.delivered_at>observed" in body
    assert "text_hash,observed,expiry" in body


def test_sql_fixed_korean_instruction_hashes_match_python_templates():
    # Extract actual SQL literals, then compare bytes/hash; do not accept a comment
    # mentioning the template while the stored text differs.
    case = re.search(
        r"text_hash\s*:=\s*encode\s*\(\s*sha256\s*\(\s*convert_to\s*\(\s*case\s+a\.action"
        r"\s+when\s+'edit_telegram'\s+then\s+'((?:''|[^'])*)'"
        r"\s+when\s+'edit_x'\s+then\s+'((?:''|[^'])*)'"
        r"\s+else\s+'((?:''|[^'])*)'\s+end\s*,\s*'UTF8'\s*\)\s*\)\s*,\s*'hex'\s*\)",
        CODE, re.I | re.S,
    )
    assert case, "Expected UTF8 SHA-256 over fixed channel instruction literals"
    for action, literal in zip(("edit_telegram", "edit_x", "edit_banner"), case.groups()):
        sql_text = literal.replace("''", "'")
        expected = prompt_instruction(action)
        assert sql_text == expected
        assert hashlib.sha256(sql_text.encode("utf-8")).hexdigest() == hashlib.sha256(expected.encode("utf-8")).hexdigest()


def test_attempt_dedupe_keeps_existing_identity_card_action_and_expiry():
    _, _, body = function(RESERVE)
    for condition in (
        "where review_id=r.id and epoch=r.epoch",
        "prior.id is distinct from target_attempt_id", "prior.card_id is distinct from c.id",
        "prior.actor_id is distinct from actor", "prior.human_binding is distinct from verified_human_binding",
        "prior.edit_action_key is distinct from target_action_key", "prior.expires_at<=observed",
        "prior.version_fingerprint is distinct from r.version_fingerprint",
        "prior.expected_text_sha256 is distinct from text_hash",
        "raise exception 'button_attempt_conflict'", "'reused',true",
    ):
        assert condition in body
    assert not re.search(r"\bupdate\s+private\.content_ops_button_prompt_attempts\b", body)
    assert "unique(review_id,epoch)" in normalize(CODE)
    assert "references private.content_ops_button_actions(review_id,idempotency_key)" in normalize(CODE)


def test_only_two_private_durable_tables_are_written_and_no_transport_or_schedule_added():
    writes = re.findall(r"\binsert\s+into\s+([a-z_]+\.[a-z_]+)", CODE, re.I)
    assert set(writes) == {f"private.{name}" for name in TABLES}
    assert len(writes) == 2
    assert not re.search(r"\b(?:insert\s+into|update|delete\s+from)\s+public\.", CODE, re.I)
    assert not re.search(r"\b(?:grant|security\s+definer|create\s+extension|create\s+policy)\b", CODE, re.I)
    assert not re.search(r"\b(?:pg_net|http_post|http_get|dblink|pg_notify|notify|listen|spawn|retry|queue)\b|"
                         r"\bcron\.|\bnet\.http|\bschedule\s*\(|https?://", CODE, re.I)
    for name in (RECORD, RESERVE):
        _, _, body = function(name)
        returns = re.findall(r"return\s+jsonb_build_object\s*\((.*?)\)\s*;", body, re.S)
        assert len(returns) == 2
        assert all("'execution_authorized',false" in value for value in returns)


def test_table_defaults_do_not_create_implicit_activation_or_provider_credentials():
    normalized = normalize(CODE)
    assert "active boolean not null default false" in normalized
    assert not re.search(r"\b(?:token|password|secret|api_key|provider_response|raw_response|chat_id|human_id)\s+", normalized)
    assert "create unique index content_ops_button_card_message_idx" in normalized
    assert "((bindings->>'bot'),(bindings->>'room'),(bindings->>'message'))" in normalized
