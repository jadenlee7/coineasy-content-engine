"""Hosted entrypoint with real protocol objects and synthetic HTTP only."""
import asyncio
from datetime import timedelta
import hashlib
import json
import re
from pathlib import Path
import socket

import httpx
import pytest

from core.content_ops.trial_private_review_runtime import (
    TrialCourierSettings, TrialStateClient, TrialRuntimeError, FreshStateTransport,
    ExactTrialRunner, run_trial, START, END,
)
from core.content_ops.trial_courier_adapter import TrialManifest, canonical_sha256
from scripts import run_trial_private_review_cards as cli
from test_content_ops_daily_private_review_runtime import daily_config, POLICY, SHA

NOW = START + timedelta(minutes=15)
PNG = b"\x89PNG\r\n\x1a\nsynthetic-trial"
TG, X = "합성 검수 공지", "합성 X 문구"


def uid(n):
    return f"{n:08d}-1111-4111-8111-111111111111"


def env(**kwargs):
    return {**daily_config(), "CONTENT_OPS_TRIAL_COURIER_ENABLED": "true",
        "CONTENT_OPS_TRIAL_ID": uid(1), "CONTENT_OPS_TRIAL_PLAN_SHA256": "b"*64,
        "CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256": canonical_sha256(json.loads(POLICY)), **kwargs}


def settings(**kwargs):
    return TrialCourierSettings.from_env(env(**kwargs), stamp_reader=lambda: SHA)


def manifest(clients=("yellow",)):
    entries = []
    for i, client in enumerate(clients):
        n = 10+i*10
        entries.append({"client_id": client, "job_id": uid(n), "request_id": uid(n+1),
            "content_item_id": uid(n+1), "source_item_id": uid(n+2), "content_version_id": uid(n+3),
            "source_sha256": "c"*64, "banner_sha256": hashlib.sha256(PNG).hexdigest(),
            "telegram_copy_sha256": hashlib.sha256(TG.encode()).hexdigest(),
            "x_copy_sha256": hashlib.sha256(X.encode()).hexdigest()})
    p = {"contract": "ybo-trial-courier-manifest@1", "trial_id": uid(1), "plan_sha256": "b"*64,
        "release_sha": SHA, "destination_policy_sha256": env()["CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256"],
        "not_before": "2026-10-14T00:00:00Z", "deadline": "2026-10-14T02:00:00Z", "entries": entries}
    return TrialManifest.from_mapping(p, canonical_sha256(p))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **k): raise AssertionError("no external I/O")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_off_reads_one_flag_only_no_runtime_or_network():
    class OnlyFlag(dict):
        def get(self, key, default=None):
            assert key == "CONTENT_OPS_TRIAL_COURIER_ENABLED"
            return "false"
    r = cli.run(environ=OnlyFlag(), runner=lambda _: pytest.fail("runtime constructed"))
    assert r["status"] == "disabled" and r["invocation_dispatch_enabled"] is False


def test_validate_only_no_http_socket_or_sender(monkeypatch):
    def forbidden(*a, **k): raise AssertionError("no client constructor")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", forbidden)
    r = cli.run(validate_only=True, environ=env(), stamp_reader=lambda: SHA, runner=forbidden)
    assert r["ok"] and not r["network_calls"] and not r["telegram_calls"]
    assert env()["TELEGRAM_REVIEW_BOT_TOKEN"] not in json.dumps(r)


@pytest.mark.parametrize("delta", [
    {"CONTENT_OPS_REVIEW_ENABLED": "true"}, {"CONTENT_OPS_BUTTON_CARD_ENABLED": "true"},
    {"CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED": "true"}, {"API_SECRET": "forbidden-admin"},
    {"SUPABASE_SERVICE_ROLE_KEY": "forbidden-db"}, {"OPENAI_API_KEY": "forbidden-provider"},
    {"RAILWAY_GIT_COMMIT_SHA": "f"*40}, {"CONTENT_OPS_TRIAL_PLAN_SHA256": "bad"},
    {"CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256": "f"*64},
])
def test_configuration_cannot_widen_authority(delta):
    assert cli.run(validate_only=True, environ=env(**delta), stamp_reader=lambda: SHA)["ok"] is False


def state_response(m, **override):
    return httpx.Response(200, json={"active": True, "observed_at": NOW.isoformat(),
        "manifest": m.payload, "manifest_sha256": m.sha256, **override},
        headers={"X-Content-Ops-Release-Sha": SHA})


def test_state_exact_token_body_manifest_and_no_other_secret():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.path.endswith("content-ops-trial-state")
        assert req.headers["authorization"] == "Bearer " + settings().gateway_token
        assert "x-api-key" not in req.headers
        assert json.loads(req.content) == {"trial_id": uid(1), "plan_sha256": "b"*64}
        return state_response(manifest())
    async def run():
        s = TrialStateClient(settings(), transport=httpx.MockTransport(handler), clock=lambda: NOW)
        try:
            m = await s.read()
            await s.require_same(m)
            assert len(calls) == 2
        finally: await s.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["inactive", "stale", "hash", "wrong_release", "redirect", "big", "unknown409", "unknown"])
def test_state_bad_response_is_terminal_no_retry(kind):
    calls = []
    def handler(req):
        calls.append(req)
        if kind == "unknown": raise RuntimeError("private-provider-secret")
        if kind == "redirect": return httpx.Response(302, headers={"location": "https://other.invalid"})
        if kind == "big": return httpx.Response(200, content=b" "*16385,
            headers={"Content-Type": "application/json", "X-Content-Ops-Release-Sha": SHA})
        if kind == "unknown409": return httpx.Response(409, json={"error": "other"}, headers={"X-Content-Ops-Release-Sha": SHA})
        r = state_response(manifest(), **({"active": False} if kind == "inactive" else
            {"observed_at": (NOW-timedelta(seconds=6)).isoformat()} if kind == "stale" else
            {"manifest_sha256": "e"*64} if kind == "hash" else {}))
        if kind == "wrong_release": r.headers["X-Content-Ops-Release-Sha"] = "f"*40
        return r
    async def run():
        s = TrialStateClient(settings(), transport=httpx.MockTransport(handler), clock=lambda: NOW)
        try:
            with pytest.raises(TrialRuntimeError, match="^trial_private_state_unknown$"): await s.read()
            assert len(calls) == 1
        finally: await s.aclose()
    asyncio.run(run())


class Clock:
    def __init__(self, now=NOW): self.now, self.sleeps = now, []
    def __call__(self): return self.now
    async def sleep(self, seconds):
        self.sleeps.append(seconds); self.now += timedelta(seconds=seconds)


class State:
    def __init__(self, value, *, pending=0): self.value, self.pending, self.reads, self.closed = value, pending, 0, False
    async def read(self):
        self.reads += 1
        if self.reads <= self.pending: return None
        return self.value
    async def require_same(self, value):
        assert value.sha256 == self.value.sha256
    async def aclose(self): self.closed = True


def test_wait_before_window_does_not_construct_state_then_max_three_once():
    clock, calls = Clock(START-timedelta(seconds=31)), []
    state = State(manifest(("yellow", "babylon", "origintrail")), pending=1)
    def state_factory(*a, **k):
        assert clock() >= START
        return state
    class Runner:
        def __init__(self, settings, m, entry, *a): calls.append(entry["client_id"])
        async def run(self): return {"status": "card_recorded", "confirmed_parts": 4, "public_send_attempted": False}
    r = asyncio.run(run_trial(settings(), clock=clock, sleep=clock.sleep, state_factory=state_factory, runner_factory=Runner))
    assert r["status"] == "completed" and r["cards_recorded"] == 3 and r["confirmed_parts"] == 12
    assert calls == ["yellow", "babylon", "origintrail"] and state.closed
    assert clock.sleeps == [30, 1, 15] and r["invocation_dispatch_enabled"] is False
    assert "effective_enabled" not in r  # Local stop is not a claim about shared owner state.


def test_unknown_first_card_stops_remaining_cards_without_retry():
    state, calls = State(manifest(("yellow", "babylon", "origintrail"))), []
    class Runner:
        def __init__(self, *a): calls.append(1)
        async def run(self): return {"status": "delivery_unknown", "confirmed_parts": 2, "public_send_attempted": False}
    r = asyncio.run(run_trial(settings(), clock=lambda: NOW, state_factory=lambda *a, **k: state, runner_factory=Runner))
    assert r["status"] == "delivery_unknown" and r["confirmed_parts"] == 2 and len(calls) == 1 and state.closed


def test_expired_never_constructs_state_or_sender():
    r = asyncio.run(run_trial(settings(), clock=lambda: END,
        state_factory=lambda *a, **k: pytest.fail("state constructed")))
    assert r["status"] == "expired" and r["attempted_cards"] == 0


def test_telegram_requires_fresh_remote_state_for_every_http_no_second_poller():
    events = []
    class Active:
        active = True
        async def require_same(self, m):
            events.append("state")
            if not self.active: raise ValueError("revoked")
    active = Active(); s = settings()
    transport = FreshStateTransport(httpx.MockTransport(lambda req: events.append("telegram") or httpx.Response(200)),
        active, manifest(), s, lambda: NOW)
    async def run():
        req = httpx.Request("POST", "https://api.telegram.org/bot"+s.bot_token+"/sendMessage")
        await transport.handle_async_request(req); active.active = False
        with pytest.raises(TrialRuntimeError): await transport.handle_async_request(req)
        with pytest.raises(TrialRuntimeError): await transport.handle_async_request(req)
    asyncio.run(run())
    assert events == ["state", "telegram", "state"]


def test_real_exact_canary_four_parts_static_controls_mock_http():
    m, s, calls = manifest(), settings(), []
    e = m.payload["entries"][0]
    identity = {"trial_id": m.payload["trial_id"], "plan_sha256": m.payload["plan_sha256"], "manifest_sha256": m.sha256}
    scope = {"mode": "trial", "content_version_id": e["content_version_id"], "packet_mode": "trial_delivery_only_v1", **identity}
    state = State(m)
    def gateway(req):
        b = json.loads(req.content); action = b["action"]; calls.append("gateway:"+action)
        assert req.headers["x-content-ops-packet-mode"] == "trial_delivery_only_v1"
        if action == "image": return httpx.Response(200, content=PNG, headers={
            "Content-Type": "image/png", "Content-Length": str(len(PNG)), "X-Content-Ops-Release-Sha": SHA,
            "X-Content-Ops-Outbox-Id": uid(91), "X-Content-Ops-Item-Id": e["content_item_id"],
            "X-Content-Ops-Version-Id": e["content_version_id"], "X-Content-Ops-Banner-Sha256": e["banner_sha256"],
            "X-Content-Ops-Mode": "trial", "X-Content-Ops-Packet-Mode": "trial_delivery_only_v1",
            "X-Content-Ops-Trial-Id": identity["trial_id"], "X-Content-Ops-Trial-Plan-Sha256": identity["plan_sha256"],
            "X-Content-Ops-Trial-Manifest-Sha256": identity["manifest_sha256"]})
        if action == "reconcile": key, value = "queued", 1
        elif action == "claim": key, value = "claim", {"outbox_id": uid(91), "claim_token": b["claim_token"],
            "client_id": "yellow", "kst_date": "2026-10-14", "content_item_id": e["content_item_id"],
            "content_version_id": e["content_version_id"], "source_item_id": e["source_item_id"],
            "generate_job_id": e["job_id"], "banner_sha256": e["banner_sha256"], "title": "검수용",
            "telegram_copy": TG, "x_copy": X, "source_url": "https://x.com/Yellow/status/123",
            "source_published_at": "2026-10-13T23:00:00Z"}
        elif action == "begin": key, value = "accepted", True
        else:
            key, args, step = "owner", b["args"], b["step"]
            value = {"execution_authorized": False}
            if step == "prepare": value.update(status="review_prepared", review_id=args["review_id"], epoch=0,
                state="active", version_fingerprint="e"*64, expires_at="2026-10-14T00:45:00Z")
            elif step == "bind": value.update(status="bound")
            elif step == "reserve": value.update(status="reserved", new_attempt=True)
            elif step == "confirm": value.update(status="confirmed", new_confirmation=True)
            elif step == "register": value.update(status="card_recorded", card_id=args["card_id"], reused=False)
            elif step == "terminal": value.update(status="sent", card_id=args["card_id"], outbox_id=args["outbox_id"])
            else: pytest.fail("unexpected owner step")
        return httpx.Response(200, json={"ok": True, "release_sha": SHA, "scope": scope, key: value})
    sent = []
    def telegram(req):
        method = req.url.path.rsplit("/",1)[-1]; calls.append("telegram:"+method)
        if method == "getMe": value = {"id": s.bot_id, "is_bot": True, "username": "coineasy_review_bot"}
        elif method == "getChat": value = {"id": s.chat_id, "type": "supergroup"}
        elif method == "getChatMember": value = {"status": "administrator", "user": {"id": s.bot_id, "is_bot": True}}
        else:
            sent.append(method)
            value = {"message_id": 100+len(sent), "date": int(NOW.timestamp()),
                "from": {"id": s.bot_id, "is_bot": True}, "chat": {"id": s.chat_id, "type": "supergroup"}}
            if method == "sendPhoto":
                value["caption"] = re.search(rb'name="caption"\r\n\r\n(.*?)\r\n--', req.content, re.S).group(1).decode()
                value["photo"] = [{"file_id": "fixture"}]
            else:
                body = json.loads(req.content); value["text"] = body["text"]
                if "reply_markup" in body:
                    assert "callback_data" not in json.dumps(body)
                    assert "검수 봇 OFF" in body["text"]
                    value["reply_markup"] = body["reply_markup"]
        return httpx.Response(200, json={"ok": True, "result": value})
    backends = iter([httpx.MockTransport(gateway), httpx.MockTransport(telegram)])
    runner = ExactTrialRunner(s, m, e, state, lambda: NOW, backend_factory=lambda: next(backends))
    r = asyncio.run(runner.run())
    assert r == {"status": "card_recorded", "confirmed_parts": 4, "public_send_attempted": False}
    assert sent == ["sendPhoto", "sendMessage", "sendMessage", "sendMessage"]
    assert not any("getUpdates" in x for x in calls)
    with pytest.raises(TrialRuntimeError): asyncio.run(runner.run())


def test_existing_image_packages_trial_without_changing_default_entrypoint():
    text = (Path(__file__).parents[1]/"Dockerfile.private-review-card").read_text()
    assert "COPY scripts/run_trial_private_review_cards.py" in text
    assert "CONTENT_OPS_TRIAL_COURIER_ENABLED=false" in text
    assert 'CMD ["python", "-m", "scripts.run_private_review_card_canary"]' in text
    config = json.loads((Path(__file__).parents[1]/"ops/content-ops/railway.trial-private-card.json").read_text())
    assert config["build"] == {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile.private-review-card"}
    assert config["deploy"] == {
        "preDeployCommand": "python -m scripts.run_trial_private_review_cards --validate-only",
        "startCommand": "python -m scripts.run_trial_private_review_cards",
        "restartPolicyType": "NEVER",
    }
    assert "cronSchedule" not in json.dumps(config)
