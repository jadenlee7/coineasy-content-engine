"""Static guards for the unhosted, service-role-only owner gateway proposal."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / "supabase/proposals/content_ops_button_card_owner_gateway.sql"
SQL = PROPOSAL.read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)


def test_owner_gateway_is_one_unapplied_definer_rpc_with_no_table_grants():
    assert PROPOSAL.parent == ROOT / "supabase/proposals"
    assert "LOCAL PROPOSAL ONLY" in SQL
    assert not list((ROOT / "supabase/migrations").glob(
        "*content_ops_button_card_owner_gateway.sql"))
    assert len(re.findall(r"create\s+function", CODE, re.I)) == 1
    assert len(re.findall(r"security\s+definer", CODE, re.I)) == 1
    assert "set search_path = ''" in CODE
    assert re.search(r"revoke all on function public\.content_ops_button_card_owner_step"
                     r"\(uuid,uuid,text,jsonb\)\s+from public, anon, authenticated, service_role", CODE, re.I)
    assert re.search(r"grant execute on function public\.content_ops_button_card_owner_step"
                     r"\(uuid,uuid,text,jsonb\)\s+to service_role", CODE, re.I)
    assert "grant " not in CODE.lower().replace(
        "grant execute on function public.content_ops_button_card_owner_step", "")
    assert CODE.strip().lower().endswith("commit;")


def test_owner_gateway_exact_scope_and_six_actions_only():
    lower = re.sub(r"\s+", " ", CODE).lower()
    assert "auth.role()) is distinct from 'service_role'" in lower
    assert "target_args ?& allowed" in lower
    assert "target_args - allowed <> '{}'::jsonb" in lower
    assert "review.workspace_id is distinct from target_workspace_id" in lower
    assert "review.content_version_id is distinct from target_content_version_id" in lower
    for action in ("prepare", "bind", "reserve", "confirm", "register", "terminal"):
        assert f"when '{action}' then" in lower
    assert "when 'publish'" not in lower
    assert "when 'approve'" not in lower
    assert "execute target_" not in lower
    for name in (
        "prepare_content_ops_button_review_from_claim",
        "bind_content_ops_button_card_outbox",
        "reserve_content_ops_button_card_send",
        "confirm_content_ops_button_card_send",
        "register_content_ops_button_card_from_sends",
        "read_content_ops_button_card_terminal",
    ):
        assert f"private.{name}(" in lower
