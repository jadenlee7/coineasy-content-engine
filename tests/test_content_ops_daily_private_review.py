"""Synthetic daily discovery + exact-version private packets; no live I/O."""
import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from core.content_ops.daily_private_review import DailyPrivateCardWorker
from core.content_ops.private_review_bot_policy import ExistingReviewBotPolicy
from core.content_ops.private_review_card_canary import PrivateCardCanary
from core.content_ops.private_review_card_courier import PrivateCardCourier
from core.content_ops.private_review_card_gateway import (
    DAILY_PACKET_MODE, DailyButtonGateway, PrivateCardGatewayError,
)
from core.content_ops.private_review_card_owner_gateway import GatewayPrivateCardOwner
from core.content_ops.private_review_card_receipt import ObservedSend
from core.content_ops.review_buttons import ButtonSigner, PRIVATE_CONTROL_LABELS
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.worker import APP_ORIGIN, ReviewClaim


NOW = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)
EPOCH = int(NOW.timestamp())
RELEASE = "a" * 40
TOKEN = "synthetic_gateway_" + "c" * 40
PNG = b"\x89PNG\r\n\x1a\nsynthetic-daily-image"
HASH = hashlib.sha256(PNG).hexdigest()
BOT, ROOM = 123456789, -1001234567890
POLICY = ExistingReviewBotPolicy(BOT, ROOM, "synthetic-existing-owner-room", "administrator")
CLIENTS = ("yellow", "babylon", "squid", "origintrail")
HANDLES = ("Yellow", "babylonlabs_io", "SquidRouter", "origin_trail")


def ident(index):
    return f"{index:08x}-1111-4111-8111-111111111111"


def raw_claim(index, token):
    return {"outbox_id": ident(10 + index), "claim_token": token,
        "client_id": CLIENTS[index], "kst_date": "2026-10-02",
        "content_item_id": ident(20 + index), "content_version_id": ident(30 + index),
        "source_item_id": ident(40 + index), "generate_job_id": ident(50 + index),
        "banner_sha256": HASH, "title": "합성 검수 제목",
        "telegram_copy": f"합성 {CLIENTS[index]} Telegram 전문",
        "x_copy": f"합성 {CLIENTS[index]} X 전문",
        "source_url": f"https://x.com/{HANDLES[index]}/status/123456789",
        "source_published_at": "2026-10-02T08:00:00Z"}


def receipt(key, value, version=None, **overrides):
    return httpx.Response(200, json={"ok": True, "release_sha": RELEASE,
        "scope": {"mode": "daily", "content_version_id": version,
                  "packet_mode": DAILY_PACKET_MODE}, key: value, **overrides})


def gateway(handler, *, enabled=True, clock=lambda: NOW):
    return DailyButtonGateway(origin=APP_ORIGIN, gateway_token=TOKEN,
        release_sha=RELEASE, enabled=enabled, clock=clock,
        transport=httpx.MockTransport(handler))


def test_daily_default_off_does_no_io_and_worker_off_reads_no_clock():
    calls = []
    discovery = gateway(lambda req: calls.append(req), enabled=False)
    with pytest.raises(PrivateCardGatewayError, match="disabled"):
        asyncio.run(discovery.reconcile())
    assert calls == []
    def forbidden():
        raise AssertionError("OFF read clock")
    worker = DailyPrivateCardWorker(gateway=discovery,
        runner_factory=forbidden, clock=forbidden)
    assert asyncio.run(worker.run())["status"] == "disabled"
    assert calls == []


@pytest.mark.parametrize("value", [True, -1, 5, "4", None])
def test_bad_or_lost_reconcile_ack_consumes_discovery(value):
    calls = []
    discovery = gateway(lambda req: (calls.append(req), receipt("queued", value))[1])
    async def run():
        with pytest.raises(PrivateCardGatewayError, match="outcome_unknown"):
            await discovery.reconcile()
        with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
            await discovery.reconcile()
        with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
            await discovery.claim(ident(100))
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("response", [
    httpx.Response(503, text="synthetic_private_error"),
    receipt("claim", None),
    receipt("claim", {**raw_claim(0, ident(100)), "provider_response": "do-not-relay"}),
    receipt("claim", raw_claim(0, ident(100)), scope={"mode": "canary"}),
    receipt("claim", {**raw_claim(0, ident(100)), "source_published_at": "2026-10-01T09:00:00Z"}),
    receipt("claim", {**raw_claim(0, ident(100)), "source_published_at": "2026-10-02T09:00:01Z"}),
])
def test_null_stale_malformed_or_lost_claim_ack_never_claims_again(response):
    calls = []
    def handler(req):
        calls.append(json.loads(req.content)["action"])
        return receipt("queued", 1) if len(calls) == 1 else response
    discovery = gateway(handler)
    async def run():
        await discovery.reconcile()
        try:
            assert await discovery.claim(ident(100)) is None
        except PrivateCardGatewayError as exc:
            assert str(exc) == "private_card_gateway_outcome_unknown"
        with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
            await discovery.claim(ident(101))
    asyncio.run(run())
    assert calls == ["reconcile", "claim"]


def test_four_unique_claims_bind_once_and_only_discovery_is_unversioned():
    calls = []
    def handler(req):
        body = json.loads(req.content)
        calls.append((body, req.headers))
        if body["action"] == "reconcile":
            return receipt("queued", 4)
        return receipt("claim", raw_claim(len(calls) - 2, body["claim_token"]))
    discovery = gateway(handler)
    async def run():
        await discovery.reconcile()
        for index in range(4):
            claimed = await discovery.claim(ident(100 + index))
            with pytest.raises(PrivateCardGatewayError, match="arguments_invalid"):
                discovery.bind(replace(claimed))
            bound = discovery.bind(claimed)
            assert bound.bound_claim is claimed
            assert bound.content_version_id == ident(30 + index)
            assert bound._headers()["x-content-ops-version-id"] == ident(30 + index)
            assert bound._headers()["x-content-ops-packet-mode"] == DAILY_PACKET_MODE
            with pytest.raises(PrivateCardGatewayError, match="arguments_invalid"):
                discovery.bind(claimed)
            with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
                await bound.claim(ident(200 + index))
            with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
                await bound.reconcile()
        with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
            await discovery.claim(ident(104))
    asyncio.run(run())
    assert len(calls) == 5
    assert all("x-content-ops-version-id" not in headers for _, headers in calls)


@pytest.mark.parametrize("duplicate", ["client", "version"])
def test_duplicate_claim_ends_batch_without_another_attempt(duplicate):
    calls = []
    def handler(req):
        body = json.loads(req.content)
        calls.append(body["action"])
        if body["action"] == "reconcile":
            return receipt("queued", 4)
        index = len(calls) - 2
        raw = raw_claim(0 if duplicate == "client" else index, body["claim_token"])
        if duplicate == "version":
            raw["content_version_id"] = ident(30)
        return receipt("claim", raw)
    discovery = gateway(handler)
    async def run():
        await discovery.reconcile()
        await discovery.claim(ident(100))
        with pytest.raises(PrivateCardGatewayError, match="outcome_unknown"):
            await discovery.claim(ident(101))
        with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
            await discovery.claim(ident(102))
    asyncio.run(run())
    assert calls == ["reconcile", "claim", "claim"]


def integration(*, clients=4, fail=None, fail_client=0, worker_clock=lambda: NOW, start="09:00"):
    """Real gateway/owner/courier/coordinator with only synthetic HTTP/sends."""
    network, sends, preflights, sessions = [], [], [], []
    states = {}
    next_claim = 0
    signer, bindings = ButtonSigner(b"s" * 32), EditBindings(b"e" * 32)

    def handler(req):
        nonlocal next_claim
        body = json.loads(req.content)
        action, step = body["action"], body.get("step")
        version = req.headers.get("x-content-ops-version-id")
        assert req.headers["x-content-ops-mode"] == "daily"
        assert req.headers["x-content-ops-packet-mode"] == DAILY_PACKET_MODE
        network.append((action, step, version))
        if action == "reconcile":
            assert version is None
            return receipt("queued", clients)
        if action == "claim":
            assert version is None
            if next_claim == clients:
                return receipt("claim", None)
            raw = raw_claim(next_claim, body["claim_token"])
            next_claim += 1
            states[raw["content_version_id"]] = {"raw": raw}
            return receipt("claim", raw)
        assert version in states  # no wildcard send or cross-version transport
        state, raw = states[version], states[version]["raw"]
        if (version == ident(30 + fail_client) and fail is not None
            and (fail == action or fail == step)):
            return httpx.Response(503, text="synthetic_unknown_ack")
        if action == "image":
            assert body["outbox_id"] == raw["outbox_id"]
            return httpx.Response(200, content=PNG, headers={
                "Content-Type": "image/png", "Content-Length": str(len(PNG)),
                "X-Content-Ops-Release-Sha": RELEASE,
                "X-Content-Ops-Outbox-Id": raw["outbox_id"],
                "X-Content-Ops-Item-Id": raw["content_item_id"],
                "X-Content-Ops-Version-Id": (ident(300) if fail == "cross_image"
                    and version == ident(30 + fail_client) else version),
                "X-Content-Ops-Banner-Sha256": HASH})
        if action == "begin":
            state["packet"] = body["packet_sha256"]
            return receipt("accepted", True, version)
        assert action == "owner"
        args = body["args"]
        assert not {"workspace_id", "content_version_id"} & set(args)
        if step == "prepare":
            state["review"] = args["review_id"]
            value = {"status": "review_prepared", "review_id": args["review_id"],
                "version_fingerprint": "b" * 64, "epoch": 0, "state": "active",
                "expires_at": "2026-10-02T09:30:00Z", "execution_authorized": False}
        elif step == "bind":
            assert args["packet_sha256"] == state["packet"]
            value = {"status": "bound", "execution_authorized": False}
        elif step in {"reserve", "confirm"}:
            state["card"] = args["card_id"]
            value = {"status": "reserved" if step == "reserve" else "confirmed",
                "new_attempt" if step == "reserve" else "new_confirmation": True,
                "execution_authorized": False}
        elif step == "register":
            assert len(args["parts"]) == 3 and len(args["response_sha256s"]) == 4
            value = {"status": "card_recorded", "card_id": args["card_id"],
                     "reused": False, "execution_authorized": False}
        else:
            assert step == "terminal"
            value = {"status": "sent", "card_id": args["card_id"],
                     "outbox_id": raw["outbox_id"], "execution_authorized": False}
        return receipt("owner", value, version)

    class Sender:
        def __init__(self, version):
            self.version, self.index = version, 0

        async def preflight(self, **args):
            assert args == {"bot_id": BOT, "chat_id": ROOM}
            preflights.append(self.version)

        async def send_once(self, request, *, png):
            index = self.index
            self.index += 1
            sends.append((self.version, request))
            if fail == "send" and self.version == ident(30 + fail_client) and index == 1:
                raise TimeoutError("synthetic_unknown_send")
            assert (png == PNG) if index == 0 else png is None
            result = {"message_id": 100 + len(sends), "date": EPOCH,
                "chat": {"id": ROOM, "type": "supergroup"},
                "from": {"id": BOT, "is_bot": True}}
            result["caption" if index == 0 else "text"] = request["text"]
            if index == 0:
                result["photo"] = [{"file_id": "synthetic-file"}]
            if index == 3:
                result["reply_markup"] = request["reply_markup"]
            return ObservedSend(request["method"], 200,
                json.dumps({"ok": True, "result": result}, ensure_ascii=False).encode(), NOW)

    def exact_runner(bound):
        owner = GatewayPrivateCardOwner(bound, enabled=True)
        sender = Sender(bound.content_version_id)
        courier = PrivateCardCourier(owner, sender, signer, bindings, clock=lambda: EPOCH)
        ids = iter((ident(60 + len(sessions)), ident(70 + len(sessions))))
        runner = PrivateCardCanary(workspace_id=ident(1),
            content_version_id=bound.content_version_id, bot_id=BOT, chat_id=ROOM,
            gateway=bound, owner=owner, png_reader=bound, courier=courier,
            signer=signer, bindings=bindings, bot_policy=POLICY,
            clock=lambda: NOW, uuid_factory=lambda: next(ids))
        sessions.append(runner)
        return runner

    tokens = iter(ident(100 + i) for i in range(4))
    worker = DailyPrivateCardWorker(gateway=gateway(handler), runner_factory=exact_runner,
        start_kst=start, clock=worker_clock, uuid_factory=lambda: next(tokens))
    return worker, network, sends, preflights, sessions


def test_four_client_full_private_packets_and_six_edit_only_buttons_are_version_bound():
    worker, network, sends, preflights, sessions = integration()
    assert asyncio.run(worker.run(enabled=True)) == {"status": "completed",
        "queued": 4, "claimed": 4, "cards_recorded": 4, "confirmed_parts": 16,
        "public_send_attempted": False}
    assert len(preflights) == 4 and len(sends) == 16
    for index in range(4):
        parts = sends[index * 4:(index + 1) * 4]
        assert {version for version, _ in parts} == {ident(30 + index)}
        assert [req["kind"] for _, req in parts] == ["image", "telegram", "x", "controls"]
        keyboard = parts[3][1]["reply_markup"]["inline_keyboard"]
        assert tuple(tuple(b["text"] for b in row) for row in keyboard) == PRIVATE_CONTROL_LABELS
        assert len({b["callback_data"] for row in keyboard for b in row}) == 6
        assert all("승인·게시" not in b["text"] for row in keyboard for b in row)
    for action, step, version in network:
        assert (version is None) == (action in {"reconcile", "claim"})
        assert action not in {"finish", "approve", "publish"}
    prior_calls = len(network)
    assert asyncio.run(worker.run(enabled=True))["status"] == "replay_denied"
    assert asyncio.run(sessions[0].run_bound(enabled=True))["status"] == "replay_denied"
    assert len(network) == prior_calls and len(sends) == 16


@pytest.mark.parametrize("fail,status,sent,confirmed", [
    ("image", "blocked", 0, 0), ("cross_image", "blocked", 0, 0),
    ("prepare", "blocked", 0, 0), ("begin", "outbox_unknown", 0, 0),
    ("bind", "blocked", 0, 0), ("reserve", "delivery_unknown", 0, 0),
    ("send", "delivery_unknown", 2, 1), ("confirm", "delivery_unknown", 1, 0),
    ("terminal", "delivery_unknown", 4, 4),
])
def test_uncertain_or_invalid_first_card_stops_later_clients_without_retry(fail, status, sent, confirmed):
    worker, network, sends, _, _ = integration(fail=fail)
    outcome = asyncio.run(worker.run(enabled=True))
    assert outcome == {"status": status, "queued": 4, "claimed": 1,
        "cards_recorded": 0, "confirmed_parts": confirmed, "public_send_attempted": False}
    assert len(sends) == sent
    assert sum(action == "claim" for action, _, _ in network) == 1
    before = len(network)
    assert asyncio.run(worker.run(enabled=True))["status"] == "replay_denied"
    assert len(network) == before and len(sends) == sent


def test_second_client_unknown_preserves_first_receipt_and_never_starts_third():
    worker, network, sends, _, _ = integration(fail="send", fail_client=1)
    assert asyncio.run(worker.run(enabled=True)) == {"status": "delivery_unknown",
        "queued": 4, "claimed": 2, "cards_recorded": 1, "confirmed_parts": 5,
        "public_send_attempted": False}
    assert len(sends) == 6
    assert sum(action == "claim" for action, _, _ in network) == 2
    assert not any(version == ident(32) for _, _, version in network)


def test_day_rollover_after_first_card_preserves_receipt_without_claiming_second():
    times = iter((NOW, NOW, NOW + timedelta(days=1)))
    worker, network, sends, _, _ = integration(worker_clock=lambda: next(times))
    assert asyncio.run(worker.run(enabled=True)) == {"status": "day_rolled_over",
        "queued": 4, "claimed": 1, "cards_recorded": 1, "confirmed_parts": 4,
        "public_send_attempted": False}
    assert len(sends) == 4
    assert sum(action == "claim" for action, _, _ in network) == 1


@pytest.mark.parametrize("clients", [0, 1, 3])
def test_empty_or_partial_owner_queue_is_not_a_duplicate_daily_send(clients):
    worker, network, sends, _, _ = integration(clients=clients)
    outcome = asyncio.run(worker.run(enabled=True))
    assert outcome["status"] == ("completed" if clients else "no_candidate")
    assert outcome["cards_recorded"] == clients
    assert len(sends) == clients * 4
    assert sum(action == "claim" for action, _, _ in network) == clients + 1


def test_before_kst_window_naive_clock_and_day_rollover_never_start_a_card():
    for now, status in [(NOW.replace(hour=23) - timedelta(days=1), "waiting_for_window"),
                        (NOW.replace(tzinfo=None), "blocked")]:
        worker, network, sends, _, _ = integration(worker_clock=lambda: now)
        assert asyncio.run(worker.run(enabled=True))["status"] == status
        assert network == [] and sends == []
    times = iter((NOW, NOW + timedelta(days=1)))
    worker, network, sends, _, _ = integration(worker_clock=lambda: next(times))
    assert asyncio.run(worker.run(enabled=True))["status"] == "day_rolled_over"
    assert network == [("reconcile", None, None)] and sends == []


@pytest.mark.parametrize("start", ["9:00", "24:00", "12:60", "09:00\n", None, True])
def test_start_setting_is_strict_and_constructs_no_clients(start):
    with pytest.raises(ValueError, match="configuration_invalid"):
        DailyPrivateCardWorker(gateway=gateway(lambda _: None), runner_factory=lambda _: None,
                               start_kst=start)
