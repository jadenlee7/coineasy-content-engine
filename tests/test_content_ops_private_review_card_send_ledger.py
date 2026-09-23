"""Static guards for a local-only SQL proposal, not a hosted DB proof."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / "supabase/proposals/content_ops_button_card_send_ledger.sql"
SQL = PROPOSAL.read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)


def body(name):
    match = re.search(
        rf"create\s+function\s+private\.{re.escape(name)}\s*\(.*?\)"
        r"\s*returns\b.*?\bas\s*\$\$(.*?)\$\$\s*;",
        CODE, re.I | re.S,
    )
    assert match, name
    return re.sub(r"\s+", " ", match.group(1)).lower()


def test_proposal_is_unapplied_and_has_no_runtime_grants():
    assert PROPOSAL.parent == ROOT / "supabase/proposals"
    assert "LOCAL PROPOSAL ONLY" in SQL
    assert not list((ROOT / "supabase/migrations").glob(
        "*content_ops_button_card_send_ledger.sql"))
    assert not re.search(r"\bgrant\s+|security\s+definer|disable\s+row\s+level",
                         CODE, re.I)
    assert re.search(r"alter table private\.content_ops_button_card_send_attempts"
                     r" force row level security", CODE, re.I)
    assert "revoke all on private.content_ops_button_card_send_attempts" in CODE
    assert re.sub(r"\s+", " ", CODE).strip().lower().endswith("commit;")


def test_one_review_part_and_one_card_part_cannot_be_reserved_twice():
    assert "primary key (review_id, part_index)" in CODE
    assert "unique (card_id, part_index)" in CODE
    assert "unique (message_binding)" in CODE
    reserve = body("reserve_content_ops_button_card_send")
    assert "where review_id = r.id and part_index = target_part_index for update" in reserve
    assert "'new_attempt', false" in reserve
    assert "'new_attempt', true" in reserve
    assert reserve.index("insert into private.content_ops_button_card_send_attempts") < \
        reserve.index("'new_attempt', true")
    assert "a.part_index = target_part_index - 1 and a.state = 'confirmed'" in reserve


def test_every_send_transition_rechecks_current_official_candidate():
    for name in ("reserve_content_ops_button_card_send",
                 "confirm_content_ops_button_card_send",
                 "register_content_ops_button_card_from_sends"):
        value = body(name)
        assert "private.content_ops_review_candidate(" in value
        assert "private.content_ops_button_version_fingerprint(" in value
        assert "r.state is distinct from 'active'" in value
        assert "r.expires_at <= " in value


def test_confirmation_is_one_way_and_cannot_reauthorize_a_send():
    guard = body("guard_content_ops_button_card_send_attempt")
    assert "old.state = 'reserved' and new.state = 'confirmed'" in guard
    assert "return new" in guard
    assert "raise exception 'button_card_send_attempt_immutable'" in guard
    assert "before update or delete" in CODE
    confirm = body("confirm_content_ops_button_card_send")
    assert "a.payload_sha256 is distinct from expected_payload_sha256" in confirm
    assert "observed_at < a.reserved_at or observed_at > decision_now" in confirm
    assert "'new_confirmation', false" in confirm
    assert "'new_confirmation', true" in confirm
    assert "'execution_authorized', false" in confirm


def test_registration_matches_all_four_confirmed_parts_and_existing_card_gate():
    register = body("register_content_ops_button_card_from_sends")
    assert "<> 4" in register and "for n in 0..3 loop" in register
    assert "a.state is distinct from 'confirmed'" in register
    assert "target_parts->n->>'payload_sha256'" in register
    assert "target_parts->n->>'message_binding'" in register
    assert "a.payload_sha256 is distinct from controls_payload_sha256" in register
    assert "a.message_binding is distinct from target_bindings->>'message'" in register
    assert "a.response_sha256 is distinct from target_response_sha256s->>n" in register
    assert "return private.record_content_ops_button_card(" in register


def test_all_new_functions_are_invoker_only_and_not_granted_to_runtime_roles():
    names = ("guard_content_ops_button_card_send_attempt",
             "reserve_content_ops_button_card_send",
             "confirm_content_ops_button_card_send",
             "register_content_ops_button_card_from_sends")
    for name in names:
        declaration = re.search(rf"create\s+function\s+private\.{name}\s*\(.*?"
                                r"\)\s*returns\b.*?\bas\s*\$\$", CODE, re.I | re.S)
        assert declaration, name
        assert "security invoker" in declaration.group(0).lower()
        assert "set search_path = ''" in declaration.group(0).lower()
        assert name in CODE[CODE.rfind("revoke all on function"):]
    assert "from public, anon, authenticated, service_role;" in CODE
