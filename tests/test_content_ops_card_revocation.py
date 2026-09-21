"""Static revocation architecture guards; execution/race proof requires local DB."""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
DURABLE_PATH = ROOT / "supabase/proposals/content_ops_button_durable_attempt.sql"
EDIT_PATH = ROOT / "supabase/proposals/content_ops_button_edit_reply.sql"
HELPER = "assert_content_ops_button_prompt_card_active"
REVOKE = "revoke_content_ops_button_card"


def sql(path):
    return re.sub(r"--[^\n]*", "", path.read_text())


def normalized(value):
    return re.sub(r"\s+", " ", value).strip().lower()


def function(path, name):
    match = re.search(
        rf"create\s+(?:or\s+replace\s+)?function\s+private\.{re.escape(name)}\s*\((.*?)\)"
        rf"\s*(returns\b.*?)\bas\s*\$\$(.*?)\$\$\s*;",
        sql(path), re.I | re.S,
    )
    assert match, f"Missing private function {name}"
    return tuple(normalized(value) for value in match.groups())


@pytest.mark.parametrize("name", [HELPER, REVOKE])
def test_private_revocation_functions_remain_invoker_with_empty_search_path(name):
    _, declaration, _ = function(DURABLE_PATH, name)
    assert "security invoker" in declaration
    assert re.search(r"set search_path\s*=\s*''", declaration)
    assert "security definer" not in declaration


@pytest.mark.parametrize("name", [HELPER, REVOKE])
def test_helper_and_revoke_have_no_runtime_execute_grants(name):
    code = sql(DURABLE_PATH)
    revokes = re.findall(r"\brevoke\s+all\s+on\s+(.*?)\s+from\s+(.*?);", code, re.I | re.S)
    matches = [roles for objects, roles in revokes if re.search(rf"\b{name}\b", objects)]
    assert matches
    assert any({part.strip().lower() for part in roles.split(",")} ==
        {"public", "anon", "authenticated", "service_role"} for roles in matches)
    assert not re.search(r"\bgrant\s+|create\s+policy|security\s+definer", code, re.I)


def test_save_checks_card_before_prompt_identity_and_consumed_reply_replay():
    _, _, body = function(EDIT_PATH, "save_content_ops_button_edit_reply")
    item = body.index("select * into item from public.content_items")
    review = body.index("select * into r from private.content_ops_button_reviews", item)
    helper = body.index(f"private.{HELPER}(", review)
    prompt = body.index("select * into p from private.content_ops_button_edit_prompts", helper)
    identity = body.index("private.content_ops_button_identities", prompt)
    consumed = body.index("if p.consumed_key is not null")
    assert item < review < helper < prompt < identity < consumed
    assert "for update" in body[item:review]
    assert "for update" in body[review:helper]
    assert "for update" in body[prompt:identity]
    assert body.count(f"private.{HELPER}(") == 1
    assert f"private.{HELPER}(target_prompt_id,r.id,verified_human_binding)" in body


def test_card_check_is_not_a_mutator_publisher_or_sender():
    _, declaration, body = function(DURABLE_PATH, HELPER)
    assert "returns void" in declaration
    assert not re.search(r"\binsert\s+into|\bupdate\s+(?:private|public)\.|\bdelete\s+from", body)
    assert "raise exception" in body


@pytest.mark.parametrize("name", [HELPER, REVOKE])
def test_no_network_queue_schedule_or_implicit_approval_path(name):
    _, _, body = function(DURABLE_PATH, name)
    assert not re.search(r"\b(?:pg_net|http_post|http_get|dblink|pg_notify|notify|listen|spawn|retry|queue)\b|"
        r"\bcron\.|\bnet\.http|\bschedule\s*\(|https?://", body)
    assert not re.search(r"\b(?:insert\s+into|update|delete\s+from)\s+public\.", body)


def test_linked_helper_locks_card_attempt_receipt_prompt_before_observing_time():
    _, _, body = function(DURABLE_PATH, HELPER)
    card = body.index("select * into c from private.content_ops_button_cards")
    attempt = body.index("select * into a from private.content_ops_button_prompt_attempts", card)
    receipt = body.index("select * into receipt from private.content_ops_button_prompt_receipts", attempt)
    prompt = body.index("select * into p from private.content_ops_button_edit_prompts", receipt)
    action = body.index("select * into act from private.content_ops_button_actions", prompt)
    observed = body.index("observed:=clock_timestamp()", action)
    assert card < attempt < receipt < prompt < action < observed
    assert "for share" in body[card:attempt]
    assert "for share" in body[attempt:receipt]
    assert "for share" in body[receipt:prompt]
    assert "for update" in body[prompt:action]
    assert "for share" in body[action:observed]


def test_helper_also_acquires_item_review_locks_when_invoked_directly():
    _, _, body = function(DURABLE_PATH, HELPER)
    item = body.index("perform 1 from public.content_items")
    review = body.index("select * into r from private.content_ops_button_reviews", item)
    card = body.index("select * into c from private.content_ops_button_cards", review)
    assert item < review < card
    assert "for update" in body[item:review]
    assert "for update" in body[review:card]
    assert "r.content_item_id is distinct from initial_review.content_item_id" in body
    assert "r.workspace_id is distinct from initial_review.workspace_id" in body


def test_legacy_fallback_only_when_entire_review_has_no_cards_or_attempts():
    _, _, body = function(DURABLE_PATH, HELPER)
    start = body.index("select * into a from private.content_ops_button_prompt_attempts")
    end = body.index("select * into c from private.content_ops_button_cards", start)
    legacy = body[start:end]
    assert "where review_id=r.id and epoch=initial.epoch" in legacy
    assert "if not found then" in legacy
    assert "exists(select 1 from private.content_ops_button_cards where review_id=r.id)" in legacy
    assert "or exists(select 1 from private.content_ops_button_prompt_attempts where review_id=r.id)" in legacy
    # An inactive card or another epoch still makes this a linked review.
    assert not re.search(r"where review_id=r\.id\s+and\s+(?:active|epoch)", legacy.split("if not found then", 1)[1])
    assert "receipt.reservation_expires_at is not null" in legacy
    assert legacy.count("raise exception 'button_prompt_card_reservation_missing'") == 2
    assert "p.review_id is distinct from initial.review_id" in legacy
    assert "p.epoch is distinct from initial.epoch" in legacy
    assert "p.owner_receipt_id is distinct from initial.owner_receipt_id" in legacy
    assert legacy.count("return;") == 1
    assert body.count("return;") == 1


def test_linked_helper_checks_full_prompt_receipt_attempt_card_lineage():
    _, _, body = function(DURABLE_PATH, HELPER)
    linked = body[body.index("select * into c from private.content_ops_button_cards"):]
    for condition in (
        "p.review_id is distinct from r.id", "p.epoch is distinct from a.epoch",
        "p.owner_receipt_id is distinct from a.id", "p.actor_id is distinct from a.actor_id",
        "a.human_binding is distinct from verified_human_binding",
        "p.edit_action_key is distinct from a.edit_action_key",
        "p.bot_binding is distinct from a.bot_binding", "p.room_binding is distinct from a.room_binding",
        "receipt.review_id is distinct from r.id", "receipt.actor_id is distinct from a.actor_id",
        "receipt.epoch is distinct from a.epoch", "receipt.edit_action_key is distinct from a.edit_action_key",
        "receipt.bot_binding is distinct from p.bot_binding", "receipt.room_binding is distinct from p.room_binding",
        "receipt.message_binding is distinct from p.message_binding",
        "receipt.receipt_sha256 is distinct from p.receipt_sha256",
        "receipt.delivered_at is distinct from p.delivered_at", "receipt.outcome is distinct from 'sent'",
        "receipt.reservation_expires_at is distinct from a.expires_at",
        "a.version_fingerprint is distinct from r.version_fingerprint",
        "c.version_fingerprint is distinct from a.version_fingerprint",
        "c.epoch+1 is distinct from a.epoch",
        "a.bot_binding is distinct from c.bindings->>'bot'",
        "a.room_binding is distinct from c.bindings->>'room'",
        "a.thread_id is distinct from (c.bindings->>'thread_id')::bigint",
        "a.parent_binding_sha256 is distinct from c.bindings->>'parent_binding'",
    ):
        assert condition in linked
    assert linked.count("if not found then raise exception 'button_prompt_card_lineage_invalid'") >= 3


def test_helper_pins_original_human_binding_even_if_actor_mapping_is_reassigned():
    signature, _, body = function(DURABLE_PATH, HELPER)
    assert signature == "target_prompt_id uuid,expected_review_id uuid,verified_human_binding text"
    assert "verified_human_binding is null" in body
    assert "verified_human_binding !~ '^[a-f0-9]{64}$'" in body
    assert "a.human_binding is distinct from verified_human_binding" in body


def test_helper_checks_historical_action_not_only_live_edit_requested_state():
    _, _, body = function(DURABLE_PATH, HELPER)
    for condition in (
        "act.actor_id is distinct from a.actor_id", "act.epoch is distinct from a.epoch",
        "act.version_fingerprint is distinct from a.version_fingerprint",
        "act.action not in ('edit_telegram','edit_x','edit_banner')",
        "act.result_status is distinct from 'edit_requested'",
        "act.created_at<c.delivered_at", "act.created_at>a.started_at",
    ):
        assert condition in body
    # The save function already handles consumed replay after its epoch moves;
    # the card guard must not incorrectly require the old live review epoch.
    assert "r.epoch is distinct from a.epoch" not in body
    assert "r.state is distinct from 'edit_requested'" not in body


def test_revocation_and_all_linked_expiries_block_fresh_and_consumed_paths():
    _, _, body = function(DURABLE_PATH, HELPER)
    for condition in (
        "not c.active", "observed>=c.expires_at", "observed>=a.expires_at",
        "observed>=p.expires_at", "p.expires_at>a.expires_at", "a.expires_at>c.expires_at",
        "c.delivered_at>a.started_at", "a.started_at>receipt.delivered_at",
        "receipt.delivered_at>observed", "receipt.recorded_at>observed",
    ):
        assert condition in body
    assert "raise exception 'button_prompt_card_inactive_or_expired'" in body
    assert "consumed_key" not in body  # No bypass for an already-consumed reply.


def test_revoke_locks_item_review_card_and_pins_expected_lineage():
    signature, _, body = function(DURABLE_PATH, REVOKE)
    for field in ("target_card_id uuid", "expected_review_id uuid", "expected_fingerprint text",
                  "target_actor_id uuid", "verified_bot_binding text", "verified_human_binding text"):
        assert field in signature
    item = body.index("perform 1 from public.content_items")
    review = body.index("select * into r from private.content_ops_button_reviews", item)
    card = body.index("select * into c from private.content_ops_button_cards", review)
    identity = body.index("private.content_ops_button_identities", card)
    assert item < review < card < identity
    for section in (body[item:review], body[review:card], body[card:identity]):
        assert "for update" in section
    for condition in (
        "c.review_id is distinct from r.id", "r.content_item_id is distinct from initial.content_item_id",
        "r.workspace_id is distinct from initial.workspace_id",
        "c.version_fingerprint is distinct from expected_fingerprint",
        "r.version_fingerprint is distinct from expected_fingerprint",
        "c.bindings->>'bot' is distinct from verified_bot_binding",
    ):
        assert condition in body


def test_revoke_requires_real_identity_reviewer_and_active_client_even_for_replay():
    _, _, body = function(DURABLE_PATH, REVOKE)
    for condition in (
        "i.workspace_id=r.workspace_id", "i.bot_binding=verified_bot_binding",
        "i.human_binding=verified_human_binding and i.active for share",
        "actor is distinct from target_actor_id", "private.content_ops_button_reviewers v",
        "v.workspace_id=r.workspace_id", "v.client_id=r.client_id",
        "v.actor_id=actor and v.active for share", "public.workspace_clients",
        "workspace_id=r.workspace_id and client_id=r.client_id and active for share",
    ):
        assert condition in body
    assert body.count("errcode='42501'") == 3
    assert body.index("public.workspace_clients") < body.index("if not c.active then")


def test_revoke_only_sets_inactive_and_repeated_call_is_noop_not_reactivation():
    _, _, body = function(DURABLE_PATH, REVOKE)
    writes = re.findall(r"\bupdate\s+(\w+\.\w+)\s+set\s+(.*?)\s+where\s+(.*?);", body)
    assert writes == [("private.content_ops_button_cards", "active=false", "id=c.id")]
    assert not re.search(r"\binsert\s+into|\bdelete\s+from", body)
    assert body.index("if not c.active then") < body.index("'reused',true") < body.index("update private")
    results = re.findall(r"return\s+jsonb_build_object\s*\((.*?)\)\s*;", body)
    assert len(results) == 2
    assert all("'status','card_revoked'" in value and "'card_id',c.id" in value
        and "'execution_authorized',false" in value for value in results)
    assert "'reused',true" in results[0] and "'reused',false" in results[1]
    # Cancellation remains available after TTL or a successful edit; it must
    # never mutate a saved version or depend on its previous live state.
    assert "expires_at" not in body and "r.state" not in body
