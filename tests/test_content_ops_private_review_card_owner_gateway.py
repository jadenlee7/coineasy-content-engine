"""Static guards for the unhosted, service-role-only owner gateway proposal."""
import asyncio
import json
from pathlib import Path
import re

import httpx
import pytest

from core.content_ops.private_review_card_gateway import ButtonCanaryGateway
from core.content_ops.private_review_card_owner_gateway import (
    GatewayPrivateCardOwner, GatewayPrivateCardOwnerError,
)
from core.content_ops.worker import APP_ORIGIN


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


W = "11111111-1111-4111-8111-111111111111"
V = "22222222-2222-4222-8222-222222222222"
O = "33333333-3333-4333-8333-333333333333"
T = "44444444-4444-4444-8444-444444444444"
R = "55555555-5555-4555-8555-555555555555"
C = "66666666-6666-4666-8666-666666666666"
SHA = "a" * 40
H = "b" * 64


def owner_with_transport(handler, *, enabled=True):
    gateway = ButtonCanaryGateway(origin=APP_ORIGIN,
        gateway_token="test_only_button_card_gateway_token_123456",
        release_sha=SHA, content_version_id=V, enabled=enabled,
        transport=httpx.MockTransport(handler))
    return GatewayPrivateCardOwner(gateway, enabled=enabled)


def owner_reply(step, args):
    if step == "prepare":
        return {"status": "review_prepared", "review_id": R,
            "version_fingerprint": H, "epoch": 0, "state": "active",
            "expires_at": "2026-09-23T09:30:00Z", "execution_authorized": False}
    if step == "bind":
        return {"status": "bound", "execution_authorized": False}
    if step == "reserve":
        return {"status": "reserved", "new_attempt": True,
            "execution_authorized": False}
    if step == "confirm":
        return {"status": "confirmed", "new_confirmation": True,
            "execution_authorized": False}
    if step == "register":
        return {"status": "card_recorded", "card_id": C,
            "reused": False, "execution_authorized": False}
    return {"status": "sent", "card_id": C, "outbox_id": O,
        "execution_authorized": False}


def evidence():
    bindings = {"bot": H, "room": H, "message": H,
        "packet_receipt": H, "card_receipt": H,
        "parent_binding": H, "thread_id": None}
    parts = [{"kind": kind, "outcome": "sent", "message_binding": H,
              "payload_sha256": H} for kind in ("image", "telegram", "x")]
    return {"target_review_id": R, "target_card_id": C,
        "expected_fingerprint": H, "target_epoch": 0,
        "target_bindings": bindings, "target_parts": parts,
        "controls_payload_sha256": H, "response_sha256s": [H] * 4,
        "delivered": "2026-09-23T09:10:00Z",
        "expires": "2026-09-23T09:30:00Z"}


def test_default_off_owner_does_no_network_io():
    calls = []
    owner = owner_with_transport(lambda request: calls.append(request), enabled=False)
    with pytest.raises(GatewayPrivateCardOwnerError, match="owner_disabled"):
        asyncio.run(owner.prepare_review(workspace_id=W, outbox_id=O,
            claim_token=T, content_version_id=V, review_id=R))
    assert calls == []


def test_owner_rejects_caller_version_override_before_network():
    calls = []
    owner = owner_with_transport(lambda request: calls.append(request))
    with pytest.raises(GatewayPrivateCardOwnerError, match="arguments_invalid"):
        asyncio.run(owner.prepare_review(workspace_id=W, outbox_id=O,
            claim_token=T, content_version_id=O, review_id=R))
    assert calls == []


def test_gateway_owner_enforces_four_part_order_and_terminal_one_shot():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        assert body["action"] == "owner"
        calls.append((body["step"], body["args"]))
        return httpx.Response(200, json={"ok": True, "release_sha": SHA,
            "scope": {"mode": "canary", "content_version_id": V,
                "packet_mode": "button_card_v1"},
            "owner": owner_reply(body["step"], body["args"])})

    owner = owner_with_transport(handler)

    async def run():
        with pytest.raises(GatewayPrivateCardOwnerError, match="replay_denied"):
            await owner.bind_outbox(review_id=R, outbox_id=O,
                claim_token=T, packet_sha256=H)
        await owner.prepare_review(workspace_id=W, outbox_id=O,
            claim_token=T, content_version_id=V, review_id=R)
        with pytest.raises(GatewayPrivateCardOwnerError, match="replay_denied"):
            await owner.prepare_review(workspace_id=W, outbox_id=O,
                claim_token=T, content_version_id=V, review_id=R)
        await owner.bind_outbox(review_id=R, outbox_id=O,
            claim_token=T, packet_sha256=H)
        for index in range(4):
            await owner.reserve_part(review_id=R, card_id=C,
                part_index=index, payload_sha256=H)
            with pytest.raises(GatewayPrivateCardOwnerError, match="replay_denied"):
                await owner.reserve_part(review_id=R, card_id=C,
                    part_index=index, payload_sha256=H)
            await owner.confirm_part(review_id=R, card_id=C, part_index=index,
                payload_sha256=H, message_id=100 + index,
                message_binding=H, response_sha256=H,
                observed_at="2026-09-23T09:10:00Z")
        assert (await owner.register_card(evidence()))["status"] == "card_recorded"
        assert (await owner.read_terminal(review_id=R, card_id=C,
            outbox_id=O))["status"] == "sent"
        with pytest.raises(GatewayPrivateCardOwnerError, match="replay_denied"):
            await owner.read_terminal(review_id=R, card_id=C, outbox_id=O)

    asyncio.run(run())
    assert [step for step, _ in calls] == ["prepare", "bind",
        *[step for _ in range(4) for step in ("reserve", "confirm")],
        "register", "terminal"]
    assert all("workspace_id" not in args and "content_version_id" not in args
               for _, args in calls)


def test_unknown_reservation_ack_is_not_retried_or_a_send_permit():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["step"])
        if body["step"] == "reserve":
            return httpx.Response(503, text="sensitive database error")
        return httpx.Response(200, json={"ok": True, "release_sha": SHA,
            "scope": {"mode": "canary", "content_version_id": V,
                "packet_mode": "button_card_v1"},
            "owner": owner_reply(body["step"], body["args"])})

    owner = owner_with_transport(handler)

    async def run():
        await owner.prepare_review(workspace_id=W, outbox_id=O,
            claim_token=T, content_version_id=V, review_id=R)
        await owner.bind_outbox(review_id=R, outbox_id=O,
            claim_token=T, packet_sha256=H)
        with pytest.raises(GatewayPrivateCardOwnerError, match="outcome_unknown"):
            await owner.reserve_part(review_id=R, card_id=C,
                part_index=0, payload_sha256=H)
        with pytest.raises(GatewayPrivateCardOwnerError, match="replay_denied"):
            await owner.reserve_part(review_id=R, card_id=C,
                part_index=0, payload_sha256=H)

    asyncio.run(run())
    assert calls == ["prepare", "bind", "reserve"]
