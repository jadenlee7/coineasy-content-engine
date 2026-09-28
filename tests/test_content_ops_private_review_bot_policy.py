"""Synthetic destination/consumer contracts; no production or provider I/O."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from core.content_ops.private_review_bot_policy import (
    ExistingReviewBotPolicy, PrivateReviewBotPolicyError,
)
from core.content_ops.private_review_card_candidate import PrivateCardCandidateError
from core.content_ops.private_review_card_canary import PrivateCardCanary
from core.content_ops.private_review_card_courier import PrivateCardCourier
from core.content_ops.private_review_card_receipt import prepare_private_card
from core.content_ops.private_review_card_sender import (
    PrivateCardSenderError, TelegramPrivateCardSender,
)
from core.content_ops.review_buttons import ButtonReviewError, ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import IngressPolicy, _parse_callback
from scripts import run_private_review_card_canary as cli
import test_content_ops_private_review_card_candidate as candidate
import test_content_ops_private_review_card_canary as canary
import test_content_ops_private_review_card_runtime as runtime


BOT, ROOM = candidate.BOT, candidate.ROOM
OPAQUE = "fixture-existing-owner-room-v1"
TOKEN = str(BOT) + ":" + "x" * 32


def projection(**changes):
    return {"schema": "existing-review-bot-policy@1", "bot_id": BOT,
            "chat_id": ROOM, "room_binding": OPAQUE,
            "membership_status": "administrator", **changes}


def policy(**changes):
    return ExistingReviewBotPolicy.from_json(json.dumps(projection(**changes)),
                                            bot_id=BOT, chat_id=ROOM)


@pytest.mark.parametrize("changes", [
    {"schema": "other"}, {"bot_id": BOT + 1}, {"bot_id": True},
    {"chat_id": ROOM - 1}, {"chat_id": str(ROOM)}, {"chat_id": 1},
    {"room_binding": ""}, {"room_binding": "a" * 129},
    {"room_binding": "private room"}, {"room_binding": "x\n"},
    {"room_binding": None}, {"membership_status": "creator"},
    {"membership_status": "restricted"}, {"membership_status": True},
    {"membership_status": ["member", "administrator"]},
    {"reviewers": []}, {"token": TOKEN},
])
def test_projection_rejects_drift_and_extra_authority_without_disclosure(changes):
    with pytest.raises(PrivateReviewBotPolicyError) as error:
        policy(**changes)
    assert str(error.value) == "private_review_bot_policy_invalid"
    assert OPAQUE not in repr(policy()) and str(ROOM) not in repr(policy())


@pytest.mark.parametrize("raw", [None, "", "[]", "null", "{", " " * 2049,
    json.dumps(projection()).replace('"bot_id":', '"bot_id": 7, "bot_id":'),
    json.dumps({k: v for k, v in projection().items() if k != "membership_status"}),
])
def test_projection_malformed_or_ambiguous_is_not_a_legacy_fallback(raw):
    with pytest.raises(PrivateReviewBotPolicyError, match="private_review_bot_policy_invalid"):
        ExistingReviewBotPolicy.from_json(raw, bot_id=BOT, chat_id=ROOM)


def test_existing_callback_accepts_opaque_signing_binding_not_db_room_digest():
    prepared = candidate.prepare(bot_policy=policy())
    assert prepared.room_binding == OPAQUE
    packet = prepare_private_card(prepared.snapshot, candidate.SIGNER,
                                  prepared.room_binding, now=prepared.now)
    owner_policy = IngressPolicy("s" * 32, BOT, ROOM, OPAQUE,
                                ((987654, candidate.W),))
    for row in packet[3]["reply_markup"]["inline_keyboard"]:
        for button in row:
            raw = json.dumps({"update_id": 7, "callback_query": {
                "id": "fixture-query", "data": button["callback_data"],
                "from": {"id": 987654, "is_bot": False},
                "message": {"message_id": 103, "date": prepared.now,
                    "chat": {"id": ROOM, "type": "supergroup"},
                    "from": {"id": BOT, "is_bot": True}}}}).encode()
            query, actor = _parse_callback(raw,
                [("content-type", "application/json"),
                 ("x-telegram-bot-api-secret-token", "s" * 32)],
                owner_policy, prepared.now, private_only=True)
            assert actor == candidate.W
            candidate.SIGNER.verify(query["data"], prepared.snapshot,
                                    owner_policy.room_binding, now=prepared.now)
            with pytest.raises(ButtonReviewError):
                candidate.SIGNER.verify(query["data"], prepared.snapshot,
                    candidate.BINDINGS.digest("room", BOT, ROOM), now=prepared.now)
    assert prepared.packet_sha256 != candidate.prepare().packet_sha256


def test_policy_mismatch_rejected_before_card_assembly_or_sender_io():
    wrong = replace(policy(), chat_id=ROOM - 1)
    with pytest.raises(PrivateCardCandidateError):
        candidate.prepare(bot_policy=wrong)
    with pytest.raises(PrivateCardSenderError):
        TelegramPrivateCardSender(bot_token=TOKEN, bot_id=BOT, chat_id=ROOM,
                                  bot_policy=wrong)


@pytest.mark.parametrize("configured,observed,allowed", [
    (None, "member", True), (None, "administrator", False),
    ("administrator", "administrator", True), ("administrator", "member", False),
    ("member", "member", True), ("member", "administrator", False),
    ("administrator", "creator", False), ("administrator", "restricted", False),
    ("administrator", "left", False), ("administrator", "kicked", False),
])
def test_exact_membership_role_not_generic_admin_allowance(configured, observed, allowed):
    calls = []
    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        result = {"getMe": {"id": BOT, "is_bot": True, "username": "coineasy_review_bot"},
            "getChat": {"id": ROOM, "type": "supergroup"},
            "getChatMember": {"status": observed, "user": {"id": BOT, "is_bot": True}}}[method]
        return httpx.Response(200, json={"ok": True, "result": result})
    sender = TelegramPrivateCardSender(bot_token=TOKEN, bot_id=BOT, chat_id=ROOM,
        bot_policy=None if configured is None else policy(membership_status=configured),
        transport=httpx.MockTransport(handler))
    if allowed:
        asyncio.run(sender.preflight(bot_id=BOT, chat_id=ROOM))
    else:
        with pytest.raises(PrivateCardSenderError):
            asyncio.run(sender.preflight(bot_id=BOT, chat_id=ROOM))
    assert calls == ["getMe", "getChat", "getChatMember"]


@pytest.mark.parametrize("changed_method,change", [
    ("getMe", {"username": "other_bot"}), ("getMe", {"id": BOT + 1}),
    ("getChat", {"username": "public_room"}),
    ("getChat", {"active_usernames": []}),
    ("getChat", {"linked_chat_id": -1009999999999}),
    ("getChat", {"type": "channel"}), ("getChat", {"id": ROOM - 1}),
    ("getChatMember", {"user": {"id": BOT + 1, "is_bot": True}}),
    ("getChatMember", {"user": {"id": BOT, "is_bot": False}}),
])
def test_explicit_admin_policy_preserves_all_identity_and_private_room_guards(changed_method, change):
    calls = []
    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        result = {"getMe": {"id": BOT, "is_bot": True, "username": "coineasy_review_bot"},
            "getChat": {"id": ROOM, "type": "supergroup"},
            "getChatMember": {"status": "administrator", "user": {"id": BOT, "is_bot": True}}}[method]
        if method == changed_method:
            result.update(change)
        return httpx.Response(200, json={"ok": True, "result": result})
    sender = TelegramPrivateCardSender(bot_token=TOKEN, bot_id=BOT, chat_id=ROOM,
        bot_policy=policy(), transport=httpx.MockTransport(handler))
    with pytest.raises(PrivateCardSenderError):
        asyncio.run(sender.preflight(bot_id=BOT, chat_id=ROOM))
    assert not any(method.startswith("send") for method in calls)


def test_canary_wires_callback_policy_but_keeps_durable_hmac_room_identity():
    events, captured, requests = [], {}, []
    class Owner(canary.Owner):
        async def register_card(self, evidence):
            captured.update(evidence)
            return await super().register_card(evidence)
    class Sender(canary.Sender):
        async def send_once(self, request, *, png):
            requests.append(request)
            return await super().send_once(request, png=png)
    owner, sender = Owner(events), Sender(events)
    signer, bindings = ButtonSigner(b"s" * 32), EditBindings(b"e" * 32)
    courier = PrivateCardCourier(owner, sender, signer, bindings, clock=lambda: canary.EPOCH)
    ids = iter((canary.T, canary.R, canary.C))
    runner = PrivateCardCanary(workspace_id=canary.W, content_version_id=canary.V,
        bot_id=BOT, chat_id=ROOM, gateway=canary.Gateway(events), owner=owner,
        png_reader=canary.Reader(events), courier=courier, signer=signer, bindings=bindings,
        clock=lambda: canary.NOW, uuid_factory=lambda: next(ids), bot_policy=policy())
    assert asyncio.run(runner.run(enabled=True)) == {
        "status": "card_recorded", "confirmed_parts": 4, "public_send_attempted": False}
    assert captured["target_bindings"]["room"] == bindings.digest("room", BOT, ROOM)
    assert captured["target_bindings"]["room"] != OPAQUE
    assert OPAQUE not in json.dumps(captured)
    snapshot = candidate.prepare(bot_policy=policy(), claim=canary.claimed(canary.T),
                                 png=canary.PNG).snapshot
    for row in requests[3]["reply_markup"]["inline_keyboard"]:
        for button in row:
            signer.verify(button["callback_data"][4:], snapshot, OPAQUE, now=canary.EPOCH)


def test_runtime_config_wires_policy_without_io_or_secret_output():
    env = runtime.config(CONTENT_OPS_BUTTON_CARD_ENABLED="true",
        CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON=json.dumps(projection(bot_id=123456789)))
    settings = cli.PrivateCardRuntimeSettings.from_env(env, stamp_reader=lambda: runtime.SHA)
    assert settings.bot_policy.room_binding == OPAQUE
    runner = cli.build_runner(settings)  # Constructs clients; no network call.
    assert runner._bot_policy is settings.bot_policy
    assert runner._courier._sender._membership_status == "administrator"
    def forbidden(_):
        raise AssertionError("validate-only must not construct a runner")
    result = cli.run(validate_only=True, environ=env, stamp_reader=lambda: runtime.SHA,
                     runner_factory=forbidden)
    assert result["ok"] and result["telegram_calls"] is False
    assert OPAQUE not in str(result) and str(ROOM) not in str(result)
    for raw in ("", "{}", "null", json.dumps(projection())):
        invalid = {**env, "CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON": raw}
        assert cli.run(validate_only=True, environ=invalid,
                       stamp_reader=lambda: runtime.SHA, runner_factory=forbidden)["ok"] is False
    del env["CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON"]
    assert cli.PrivateCardRuntimeSettings.from_env(env,
        stamp_reader=lambda: runtime.SHA).bot_policy is None
