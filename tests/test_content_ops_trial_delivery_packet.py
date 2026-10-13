"""Delivery-only exact packet: real courier/sender, mocked HTTP, no bot poller."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json

import httpx
import pytest

from core.content_ops.private_review_card_candidate import build_prepared_card
from core.content_ops.private_review_card_courier import PrivateCardCourier
from core.content_ops.private_review_card_receipt import prepare_private_card, private_card_packet_sha256
from core.content_ops.private_review_card_sender import PrivateCardSenderError, TelegramPrivateCardSender
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from test_content_ops_private_review_card_canary import (BOT, ROOM, W, C, R, O, T,
    NOW, EPOCH, PNG, claimed, Owner)


def fixture():
    signer, bindings = ButtonSigner(b"s"*32), EditBindings(b"e"*32)
    claim = claimed(T)
    receipt = {"status": "review_prepared", "review_id": R,
        "version_fingerprint": "a"*64, "epoch": 0, "state": "active",
        "expires_at": "2026-09-23T09:30:00Z", "execution_authorized": False}
    prepared = build_prepared_card(claim=claim, review_receipt=receipt, workspace_id=W,
        card_id=C, png=PNG, signer=signer, bindings=bindings, bot_id=BOT,
        chat_id=ROOM, now=EPOCH, delivery_only=True)
    return signer, bindings, prepared


def test_static_packet_mints_no_callback_and_changes_exact_packet_hash(monkeypatch):
    signer, _, prepared = fixture()
    interactive = prepare_private_card(prepared.snapshot, signer, prepared.room_binding, now=EPOCH)
    def forbidden(*a, **k):
        raise AssertionError("delivery-only must not mint a callback")
    monkeypatch.setattr(signer, "issue", forbidden)
    packet = prepare_private_card(prepared.snapshot, signer, prepared.room_binding, now=EPOCH, delivery_only=True)
    assert len(packet) == 4 and all("callback_data" not in json.dumps(p) for p in packet)
    assert "needs_review" in packet[3]["text"] and "검수 봇 OFF" in packet[3]["text"]
    assert "공식 채널 게시 승인이 아닙니다." in packet[3]["text"]
    assert "버튼은 이번 시험에서 비활성" in packet[3]["text"]
    assert packet[3]["reply_markup"] == {"inline_keyboard": [[{
        "text": "공식 원문 보기", "url": prepared.snapshot.source_url}]]}
    assert prepared.packet_sha256 == private_card_packet_sha256(packet,
        prepared.snapshot.banner_sha256, R, C)
    assert prepared.packet_sha256 != private_card_packet_sha256(interactive,
        prepared.snapshot.banner_sha256, R, C)


def test_real_courier_sender_registration_keeps_delivery_only_packet():
    signer, bindings, prepared = fixture()
    packet = prepare_private_card(prepared.snapshot, signer, prepared.room_binding, now=EPOCH, delivery_only=True)
    methods, events = [], []
    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        if method == "getMe":
            result = {"id": BOT, "is_bot": True, "username": "coineasy_review_bot"}
        elif method == "getChat":
            result = {"id": ROOM, "type": "supergroup"}
        elif method == "getChatMember":
            result = {"status": "member", "user": {"id": BOT, "is_bot": True}}
        else:
            index = len(methods)-4
            part = packet[index]
            assert method == part["method"]
            if index:
                body = json.loads(request.content)
                assert body["text"] == part["text"] and body["chat_id"] == ROOM
                assert "callback_data" not in json.dumps(body)
                if index == 3:
                    assert body["reply_markup"] == part["reply_markup"]
            result = {"message_id": 100+index, "date": EPOCH+index,
                "from": {"id": BOT, "is_bot": True}, "chat": {"id": ROOM, "type": "supergroup"}}
            result["caption" if index == 0 else "text"] = part["text"]
            if index == 0:
                result["photo"] = [{"file_id": "synthetic-only"}]
            if index == 3:
                result["reply_markup"] = part["reply_markup"]
        return httpx.Response(200, json={"ok": True, "result": result})
    sender = TelegramPrivateCardSender(bot_token=f"{BOT}:"+"x"*32, bot_id=BOT, chat_id=ROOM,
        transport=httpx.MockTransport(handler), clock=lambda: datetime.fromtimestamp(EPOCH+3, timezone.utc),
        delivery_only=True)
    owner = Owner(events)
    courier = PrivateCardCourier(owner, sender, signer, bindings, clock=lambda: EPOCH)
    assert asyncio.run(courier.run(prepared, enabled=True)) == {
        "status": "card_recorded", "confirmed_parts": 4, "public_send_attempted": False}
    assert events[-2:] == ["register", "terminal"]
    assert methods == ["getMe", "getChat", "getChatMember", "sendPhoto", "sendMessage", "sendMessage", "sendMessage"]
    assert owner.packet_sha == prepared.packet_sha256


@pytest.mark.parametrize("delivery_only", [False, True])
def test_sender_does_not_cross_packet_modes(delivery_only):
    signer, _, prepared = fixture()
    packet = prepare_private_card(prepared.snapshot, signer, prepared.room_binding,
                                  now=EPOCH, delivery_only=not delivery_only)
    calls = []
    sender = TelegramPrivateCardSender(bot_token=f"{BOT}:"+"x"*32, bot_id=BOT, chat_id=ROOM,
        transport=httpx.MockTransport(lambda r: calls.append(r)), delivery_only=delivery_only)
    sender._verified, sender._next_index = True, 3  # Isolate sender format gate.
    with pytest.raises(PrivateCardSenderError):
        asyncio.run(sender.send_once(packet[3], png=None))
    assert calls == []


def test_delivery_mode_cannot_change_after_packet_begin():
    signer, bindings, prepared = fixture()
    class NoIO:
        async def preflight(self, **kw):
            raise AssertionError("packet mutation must stop before any preflight")
    courier = PrivateCardCourier(NoIO(), NoIO(), signer, bindings, clock=lambda: EPOCH)
    result = asyncio.run(courier.run(replace(prepared, delivery_only=False), enabled=True))
    assert result["status"] == "blocked" and result["confirmed_parts"] == 0
