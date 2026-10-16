"""Exact recovery against mocked HTTP; never provider/DB/Telegram I/O."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import socket

import httpx
import pytest

from core.content_ops.recovery_courier_adapter import (
    RecoveryPlan, RecoveryStateClient, RecoveryGatewayTransport, RecoveryCourierError,
    canonical_sha256, build_recovery_session, ORIGIN, PATH,
)
from core.content_ops.recovery_private_review_runtime import (
    RecoveryCourierSettings, RecoveryTelegramTransport, ExactRecoveryRunner, run_recovery,
)
from scripts import run_recovery_private_review_cards as cli
from test_content_ops_daily_private_review_runtime import daily_config, POLICY, SHA

START = datetime(2026, 10, 16, 1, tzinfo=timezone.utc)
NOW, END = START + timedelta(minutes=10), START + timedelta(hours=2)
PNG = b"\x89PNG\r\n\x1a\nrecovery-fixture"
TG, X = "공식 원문 검수 fixture", "공식 X 검수 fixture"


def uid(n):
    return f"{n:08d}-1111-4111-8111-111111111111"


def sha(text):
    return hashlib.sha256(text.encode("utf8") if isinstance(text, str) else text).hexdigest()


def plan_value(**overrides):
    return {"schema_version": "private-review-recovery@1", "recovery_id": uid(1),
        "workspace_id": daily_config()["CONTENT_STUDIO_WORKSPACE_ID"], "client_id": "babylon",
        "outbox_id": uid(2), "content_item_id": uid(3), "content_version_id": uid(4),
        "source_item_id": uid(5), "generate_job_id": uid(6), "banner_asset_id": uid(7),
        "banner_sha256": sha(PNG), "telegram_copy_sha256": sha(TG), "x_copy_sha256": sha(X),
        "original_outbox_sha256": "a" * 64, "original_trial_id": uid(8), "original_plan_sha256": "b" * 64,
        "destination_policy_sha256": canonical_sha256(json.loads(POLICY)), "release_sha": SHA,
        "not_before": "2026-10-16T01:00:00Z", "deadline": "2026-10-16T03:00:00Z",
        "estimated_daily_limit_usd_micros": 10_000_000, **overrides}


def plan(**overrides):
    value = plan_value(**overrides)
    return RecoveryPlan.from_mapping(value, canonical_sha256(value))


def env(**overrides):
    p = plan()
    return {**daily_config(), "CONTENT_OPS_RECOVERY_COURIER_ENABLED": "true",
        "CONTENT_OPS_RECOVERY_PLAN_JSON": json.dumps(p.payload), "CONTENT_OPS_RECOVERY_PLAN_SHA256": p.sha256,
        **overrides}


def settings(**overrides):
    return RecoveryCourierSettings.from_env(env(**overrides), stamp_reader=lambda: SHA)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("real network forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_off_returns_after_one_flag_and_no_stamp_or_runtime():
    class OnlyFlag(dict):
        def get(self, key, default=None):
            assert key == "CONTENT_OPS_RECOVERY_COURIER_ENABLED"
            return "false"
    forbidden = lambda *a, **k: pytest.fail("OFF constructed dependency")
    receipt = cli.run(environ=OnlyFlag(), stamp_reader=forbidden, runner=forbidden)
    assert receipt == {"ok": True, "status": "disabled", "attempted_cards": 0, "cards_recorded": 0,
        "confirmed_parts": 0, "invocation_dispatch_enabled": False, "public_send_attempted": False}


@pytest.mark.parametrize("value", [None, True, 1, "TRUE", "yes", " false "])
def test_flag_is_literal(value):
    assert cli.run(environ={"CONTENT_OPS_RECOVERY_COURIER_ENABLED": value})["ok"] is False


@pytest.mark.parametrize("active,configured", [(False, False), (False, True), (True, True)])
def test_validate_only_is_local_no_clients_or_credentials_required(monkeypatch, active, configured):
    def forbidden(*a, **k):
        raise AssertionError("client construction forbidden")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", forbidden)
    values = env(CONTENT_OPS_RECOVERY_COURIER_ENABLED=str(active).lower())
    if not configured:
        values.pop("CONTENT_OPS_RECOVERY_PLAN_JSON")
        values.pop("CONTENT_OPS_RECOVERY_PLAN_SHA256")
        values.pop("RAILWAY_GIT_COMMIT_SHA")
    for key in ("CONTENT_OPS_GATEWAY_TOKEN", "TELEGRAM_REVIEW_BOT_TOKEN", "CONTENT_OPS_BUTTON_SIGNING_KEY", "CONTENT_OPS_EDIT_BINDING_KEY"):
        values.pop(key)
    r = cli.run(validate_only=True, environ=values, stamp_reader=lambda: SHA, runner=forbidden)
    assert r["ok"] and r["plan_configured"] is configured and r["enabled"] is active
    assert r["network_calls"] is r["database_calls"] is r["telegram_calls"] is False
    assert POLICY not in json.dumps(r) and SHA not in json.dumps(r)
    if not configured:
        assert not r["provenance"]["runtime_release_verified"]


@pytest.mark.parametrize("overrides", [
    {"CONTENT_OPS_REVIEW_ENABLED": "true"}, {"CONTENT_OPS_BUTTON_CARD_ENABLED": "true"},
    {"CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED": "true"}, {"CONTENT_OPS_TRIAL_COURIER_ENABLED": "true"},
    {"CONTENT_OPS_TRIAL_GATEWAY_ENABLED": "true"}, {"CONTENT_OPS_GATEWAY_ENABLED": "true"},
    {"TELEGRAM_PUBLICATION_ENABLED": "true"}, {"CONTENT_OPS_GATEWAY_URL": "https://wrong.invalid"},
    {"API_SECRET": "forbidden"}, {"SUPABASE_SERVICE_ROLE_KEY": "forbidden"}, {"PGPASSWORD": "forbidden"},
    {"OPENAI_API_KEY": "forbidden"}, {"RAILWAY_GIT_COMMIT_SHA": "f" * 40},
    {"CONTENT_OPS_RECOVERY_PLAN_JSON": ""}, {"CONTENT_OPS_RECOVERY_PLAN_SHA256": ""},
    {"CONTENT_OPS_RECOVERY_PLAN_SHA256": "f" * 64}, {"CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON": "{}"},
    {"CONTENT_STUDIO_WORKSPACE_ID": uid(99)},
])
def test_configuration_drift_cannot_construct_runtime(overrides):
    assert cli.run(validate_only=True, environ=env(**overrides), stamp_reader=lambda: SHA,
        runner=lambda *_: pytest.fail("runner constructed"))["ok"] is False


def test_explicit_publication_off_preserves_original_environment_and_runtime_settings():
    values = env(TELEGRAM_PUBLICATION_ENABLED="false")
    original = values.copy()
    receipt = cli.run(validate_only=True, environ=values, stamp_reader=lambda: SHA,
        runner=lambda *_: pytest.fail("runner constructed"))
    assert receipt["ok"] and receipt["telegram_calls"] is False
    assert type(RecoveryCourierSettings.from_env(values, stamp_reader=lambda: SHA)) is RecoveryCourierSettings
    assert values == original and values["TELEGRAM_PUBLICATION_ENABLED"] == "false"


@pytest.mark.parametrize("value", ["true", "FALSE", "", False, None])
def test_publication_off_exception_requires_exact_literal_false(value):
    assert cli.run(validate_only=True, environ=env(TELEGRAM_PUBLICATION_ENABLED=value),
        stamp_reader=lambda: SHA)["ok"] is False


def test_publication_off_exception_does_not_widen_other_telegram_credentials():
    assert cli.run(validate_only=True,
        environ=env(TELEGRAM_PUBLICATION_ENABLED="false", TELEGRAM_OTHER_TOKEN="false"),
        stamp_reader=lambda: SHA)["ok"] is False


@pytest.mark.parametrize("delta", [
    {"client_id": "yellow"}, {"client_id": "origintrail"}, {"schema_version": "other"},
    {"extra": True}, {"recovery_id": "00000000-0000-0000-0000-000000000000"},
    {"deadline": "2026-10-16T03:00:01Z"}, {"deadline": "2026-10-16T01:00:00Z"},
    {"deadline": "2026-10-16T00:59:59Z"}, {"not_before": "2026-10-16T01:00:00.000Z"},
    {"not_before": "2026-10-16T01:00:00+00:00"}, {"not_before": "2026-02-30T01:00:00Z"},
    {"banner_sha256": "f" * 63}, {"release_sha": "f" * 39},
    {"estimated_daily_limit_usd_micros": 10_000_001}, {"estimated_daily_limit_usd_micros": True},
])
def test_plan_shape_duration_identity_and_budget_fail_closed(delta):
    with pytest.raises(RecoveryCourierError):
        plan(**delta)


def test_plan_is_immutable_and_utf8_hash_is_canonical():
    p = plan()
    value = p.payload
    value["banner_sha256"] = "f" * 64
    assert p.payload != value
    with pytest.raises(RecoveryCourierError):
        RecoveryPlan.from_mapping(value, p.sha256)
    assert canonical_sha256({"가": "나"}) == sha('{"가":"나"}')


def test_duplicate_json_and_policy_plan_release_drift_fail_locally():
    values = env()
    values["CONTENT_OPS_RECOVERY_PLAN_JSON"] = values["CONTENT_OPS_RECOVERY_PLAN_JSON"].replace(
        '"client_id": "babylon"', '"client_id": "babylon", "client_id": "babylon"')
    assert cli.run(validate_only=True, environ=values, stamp_reader=lambda: SHA)["ok"] is False
    for changes in ({"destination_policy_sha256": "f" * 64}, {"release_sha": "f" * 40}):
        p = plan(**changes)
        values = env(CONTENT_OPS_RECOVERY_PLAN_JSON=json.dumps(p.payload), CONTENT_OPS_RECOVERY_PLAN_SHA256=p.sha256)
        assert cli.run(validate_only=True, environ=values, stamp_reader=lambda: SHA)["ok"] is False


def status_response(p=None, **overrides):
    p = p or plan()
    body = {"active": True, "observed_at": NOW.isoformat(), "plan": p.payload, "plan_sha256": p.sha256, **overrides}
    return httpx.Response(200, json={"ok": True, "release_sha": SHA, "scope": p.scope, "status": body})


def test_state_only_uses_existing_gateway_status_and_exact_pins():
    calls = []
    def handler(req):
        calls.append(req)
        assert str(req.url) == ORIGIN + PATH and json.loads(req.content) == {"action": "status"}
        assert req.headers["x-content-ops-recovery-plan-sha256"] == plan().sha256
        assert req.headers["x-content-ops-recovery-id"] == plan().payload["recovery_id"]
        assert req.headers["x-content-ops-mode"] == "recovery" and "x-api-key" not in req.headers
        return status_response()
    async def run():
        state = RecoveryStateClient(plan(), settings().gateway_token, clock=lambda: NOW, transport=httpx.MockTransport(handler))
        try:
            assert await state.require_active() == NOW
        finally:
            await state.aclose()
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["null", "inactive", "stale", "future", "hash", "plan", "scope", "release",
    "redirect", "oversize", "encoding", "partial", "duplicate", "transport"])
def test_bad_status_is_redacted_and_latched_no_retry(kind):
    calls = []
    def handler(req):
        calls.append(req)
        if kind == "transport":
            raise RuntimeError("secret provider response")
        if kind == "redirect": return httpx.Response(302)
        if kind == "oversize": return httpx.Response(200, content=b" " * 16385, headers={"content-type": "application/json"})
        r = status_response()
        value = json.loads(r.content)
        if kind == "null": value["status"] = None
        if kind == "inactive": value["status"]["active"] = False
        if kind in {"stale", "future"}: value["status"]["observed_at"] = (NOW + timedelta(seconds=1 if kind == "future" else -6)).isoformat()
        if kind == "hash": value["status"]["plan_sha256"] = "f" * 64
        if kind == "plan": value["status"]["plan"]["outbox_id"] = uid(98)
        if kind == "scope": value["scope"]["recovery_id"] = uid(99)
        if kind == "release": value["release_sha"] = "f" * 40
        headers = {"content-encoding": "gzip"} if kind == "encoding" else {"content-range": "bytes 0-1/2"} if kind == "partial" else {}
        if kind == "duplicate":
            return httpx.Response(200, content='{"ok":true,"ok":false}', headers={"content-type": "application/json"})
        # Set encoding after constructing the mock so no decoder masks this check.
        r = httpx.Response(200, json=value); r.headers.update(headers)
        return r
    async def run():
        state = RecoveryStateClient(plan(), settings().gateway_token, clock=lambda: NOW, transport=httpx.MockTransport(handler))
        try:
            for _ in range(2):
                with pytest.raises(RecoveryCourierError, match="^private_recovery_state_unknown$"):
                    await state.require_active()
        finally: await state.aclose()
    asyncio.run(run())
    assert len(calls) == 1


def test_state_clock_rollback_and_window_close_fail_before_next_io():
    times, calls = [NOW], []
    async def run():
        state = RecoveryStateClient(plan(), settings().gateway_token, clock=lambda: times[0],
            transport=httpx.MockTransport(lambda r: calls.append(r) or status_response()))
        try:
            await state.require_active()
            times[0] -= timedelta(seconds=1)
            with pytest.raises(RecoveryCourierError): await state.require_active()
        finally: await state.aclose()
    asyncio.run(run())
    assert len(calls) == 1
    async def expired():
        state = RecoveryStateClient(plan(), settings().gateway_token, clock=lambda: END,
            transport=httpx.MockTransport(lambda _: pytest.fail("expired state I/O")))
        try:
            with pytest.raises(RecoveryCourierError): await state.require_active()
        finally: await state.aclose()
    asyncio.run(expired())


class Active:
    def __init__(self): self.calls, self.active, self.closed = 0, True, False
    async def require_active(self):
        self.calls += 1
        if not self.active: raise ValueError("authority unavailable")
        return NOW
    async def aclose(self): self.closed = True


@pytest.mark.parametrize("now,status", [(START-timedelta(seconds=1), "not_due"), (END, "expired")])
def test_before_start_and_expiry_never_construct_state(now, status):
    r = asyncio.run(run_recovery(settings(), clock=lambda: now,
        state_factory=lambda *a, **k: pytest.fail("state constructed")))
    assert r["status"] == status and r["attempted_cards"] == 0


def test_fresh_authority_before_every_telegram_request_and_unknown_latches():
    state, calls, s = Active(), [], settings()
    transport = RecoveryTelegramTransport(httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)), state, s, lambda: NOW)
    async def run():
        request = httpx.Request("POST", "https://api.telegram.org/bot" + s.bot_token + "/sendMessage")
        await transport.handle_async_request(request)
        state.active = False
        for _ in range(2):
            with pytest.raises(RecoveryCourierError): await transport.handle_async_request(request)
    asyncio.run(run())
    assert len(calls) == 1 and state.calls == 2


@pytest.mark.parametrize("method,path", [("GET", "sendMessage"), ("POST", "getUpdates"), ("POST", "setWebhook")])
def test_telegram_disallowed_routes_fail_before_state_or_provider(method, path):
    s, state = settings(), Active()
    transport = RecoveryTelegramTransport(httpx.MockTransport(lambda _: pytest.fail("provider called")), state, s, lambda: NOW)
    async def run():
        with pytest.raises(RecoveryCourierError):
            await transport.handle_async_request(httpx.Request(method, "https://api.telegram.org/bot" + s.bot_token + "/" + path))
    asyncio.run(run())
    assert state.calls == 0


def internal_request(body, **headers):
    p = plan().payload
    return httpx.Request("POST", ORIGIN + PATH, headers={"x-content-ops-mode": "canary",
        "x-content-ops-packet-mode": "button_card_v1", "x-content-ops-version-id": p["content_version_id"],
        "x-content-ops-expected-release-sha": SHA, **headers}, json=body)


def test_adapter_never_allows_terminal_without_original_register_or_after_claim_failure():
    calls, state = [], Active()
    def handler(req):
        calls.append(req)
        raise RuntimeError("claim ACK unknown")
    transport = RecoveryGatewayTransport(plan=plan(), backend=httpx.MockTransport(handler), state=state, clock=lambda: NOW, enabled=True)
    terminal = {"action": "owner", "step": "terminal", "args": {"review_id": uid(10), "card_id": uid(11), "outbox_id": uid(2)}}
    async def run():
        with pytest.raises(RecoveryCourierError): await transport.handle_async_request(internal_request({"action": "claim", "claim_token": uid(12)}))
        with pytest.raises(RecoveryCourierError): await transport.handle_async_request(internal_request(terminal))
        with pytest.raises(RecoveryCourierError): await transport.handle_async_request(internal_request({"action": "reconcile"}))
    asyncio.run(run())
    assert len(calls) == 1


def test_terminal_only_reads_exact_original_ids_after_deadline_and_never_twice():
    p, now, state, calls = plan(), [NOW], Active(), []
    def handler(req):
        b = json.loads(req.content); calls.append(b)
        if b["step"] == "register":
            now[0] = END
            raise RuntimeError("lost register ACK")
        return httpx.Response(200, json={"ok": True, "release_sha": SHA, "scope": p.scope, "owner": {
            "status": "recovery_sent", "recovery_id": p.payload["recovery_id"], "card_id": uid(11),
            "outbox_id": uid(2), "original_outbox_status": "claimed", "execution_authorized": False}})
    transport = RecoveryGatewayTransport(plan=p, backend=httpx.MockTransport(handler), state=state, clock=lambda: now[0], enabled=True)
    terminal = {"action": "owner", "step": "terminal", "args": {"review_id": uid(10), "card_id": uid(11), "outbox_id": uid(2)}}
    async def run():
        with pytest.raises(RecoveryCourierError):
            await transport.handle_async_request(internal_request({"action": "owner", "step": "register", "args": {"review_id": uid(10), "card_id": uid(11)}}))
        r = await transport.handle_async_request(internal_request(terminal))
        assert r.json()["owner"] == {"status": "sent", "card_id": uid(11), "outbox_id": uid(2), "execution_authorized": False}
        with pytest.raises(RecoveryCourierError): await transport.handle_async_request(internal_request(terminal))
    asyncio.run(run())
    assert len(calls) == 2 and state.calls == 1


class Chain:
    """Original outbox snapshot never mutates in this fake durable owner."""
    def __init__(self, fault=None):
        self.s, self.p, self.state, self.fault = settings(), plan(), Active(), fault
        self.calls, self.sent, self.reserved, self.confirmed = [], [], [], []
        self.original = {"status": "claimed", "lease_expires_at": "2026-10-16T00:01:00Z", "send_started_at": None}
        self.original_before = dict(self.original)
        self.registered = False

    def gateway(self, req):
        b = json.loads(req.content); action = b["action"]
        label = b.get("step", action); self.calls.append("gateway:" + label)
        assert req.headers["x-content-ops-mode"] == "recovery"
        assert req.headers["x-content-ops-packet-mode"] == "recovery_delivery_only_v1"
        assert req.headers["x-content-ops-recovery-plan-sha256"] == self.p.sha256
        p = self.p.payload
        if self.fault == label and label not in {"register", "terminal"}:
            raise RuntimeError("private transport text")
        if action == "image":
            data = PNG + b"changed" if self.fault == "png_hash" else PNG
            return httpx.Response(200, content=data, headers={"Content-Type": "image/png", "Content-Length": str(len(data)),
                "X-Content-Ops-Release-Sha": SHA, "X-Content-Ops-Outbox-Id": p["outbox_id"],
                "X-Content-Ops-Item-Id": p["content_item_id"], "X-Content-Ops-Version-Id": p["content_version_id"],
                "X-Content-Ops-Banner-Sha256": p["banner_sha256"], "X-Content-Ops-Mode": "recovery",
                "X-Content-Ops-Packet-Mode": "recovery_delivery_only_v1", "X-Content-Ops-Recovery-Id": p["recovery_id"],
                "X-Content-Ops-Recovery-Plan-Sha256": self.p.sha256})
        if action == "reconcile": key, value = "queued", 1
        elif action == "claim":
            key, value = "claim", {k: p[k] for k in ("outbox_id", "client_id", "content_item_id", "content_version_id",
                "source_item_id", "generate_job_id", "banner_sha256")}
            value.update(claim_token=b["claim_token"], kst_date="2026-10-16", title="검수 fixture",
                telegram_copy=TG, x_copy=X, source_url="https://x.com/babylonlabs_io/status/123",
                source_published_at="2026-10-16T00:00:00Z")
            if self.fault == "copy_hash": value["telegram_copy"] += "changed"
            if self.fault == "claim_outbox": value["outbox_id"] = uid(99)
            if self.fault == "stale_source": value["source_published_at"] = "2026-10-15T00:00:00Z"
            if self.fault == "wrong_day": value["kst_date"] = "2026-10-15"
        elif action == "begin": key, value = "accepted", True
        else:
            key, args, step = "owner", b["args"], b["step"]
            value = {"execution_authorized": False}
            if step == "prepare": value.update(status="review_prepared", review_id=args["review_id"], epoch=0,
                state="active", version_fingerprint="e" * 64, expires_at="2026-10-16T01:40:00Z")
            elif step == "bind": value.update(status="bound")
            elif step == "reserve":
                self.reserved.append(args["part_index"])
                value.update(status="reserved", new_attempt=self.fault != "reused_reserve")
            elif step == "confirm":
                self.confirmed.append(args["part_index"])
                value.update(status="confirmed", new_confirmation=True)
            elif step == "register":
                assert self.confirmed == [0, 1, 2, 3]
                self.registered = True; self.state.active = False
                if self.fault == "register": raise RuntimeError("registration ACK lost")
                value.update(status="card_recorded", card_id=args["card_id"], reused=False)
            elif step == "terminal":
                assert self.registered
                value.update(status="recovery_sent", recovery_id=p["recovery_id"], card_id=args["card_id"],
                    outbox_id=args["outbox_id"], original_outbox_status="claimed")
                if self.fault == "terminal": value["status"] = "sent"
                if self.fault == "original_mutated": value["original_outbox_status"] = "sent"
                if self.fault == "terminal_recovery": value["recovery_id"] = uid(99)
            else: pytest.fail("unexpected owner step")
        return httpx.Response(200, json={"ok": True, "release_sha": SHA, "scope": self.p.scope, key: value})

    def telegram(self, req):
        method = req.url.path.rsplit("/", 1)[-1]; self.calls.append("telegram:" + method)
        s = self.s
        if method == "getMe": value = {"id": s.bot_id, "is_bot": True, "username": "coineasy_review_bot"}
        elif method == "getChat": value = {"id": s.chat_id, "type": "supergroup"}
        elif method == "getChatMember": value = {"status": "administrator", "user": {"id": s.bot_id, "is_bot": True}}
        else:
            assert self.reserved == list(range(len(self.sent) + 1))
            self.sent.append(method)
            if self.fault == "send": raise RuntimeError("provider unknown")
            value = {"message_id": 100 + len(self.sent), "date": int(NOW.timestamp()),
                "from": {"id": s.bot_id, "is_bot": True}, "chat": {"id": s.chat_id, "type": "supergroup"}}
            if method == "sendPhoto":
                value["caption"] = re.search(rb'name="caption"\r\n\r\n(.*?)\r\n--', req.content, re.S).group(1).decode()
                value["photo"] = [{"file_id": "fixture"}]
            else:
                body = json.loads(req.content); value["text"] = body["text"]
                if "reply_markup" in body:
                    assert "callback_data" not in json.dumps(body) and "검수 봇 OFF" in body["text"]
                    value["reply_markup"] = body["reply_markup"]
        return httpx.Response(200, json={"ok": True, "result": value})

    def runner(self):
        backends = iter([httpx.MockTransport(self.gateway), httpx.MockTransport(self.telegram)])
        return ExactRecoveryRunner(self.s, self.state, lambda: NOW, backend_factory=lambda: next(backends))


@pytest.mark.parametrize("fault", [None, "register"])
def test_real_canary_courier_four_parts_and_lost_register_ack_read_only_terminal(fault):
    c = Chain(fault)
    runner = c.runner()
    result = asyncio.run(runner.run())
    assert result == {"status": "card_recorded", "confirmed_parts": 4, "public_send_attempted": False}
    assert c.sent == ["sendPhoto", "sendMessage", "sendMessage", "sendMessage"]
    assert c.reserved == c.confirmed == [0, 1, 2, 3]
    assert c.original == c.original_before and not c.state.active
    assert c.calls[-2:] == ["gateway:register", "gateway:terminal"]
    assert not any("getUpdates" in x or "Webhook" in x for x in c.calls)
    with pytest.raises(RecoveryCourierError): asyncio.run(runner.run())


@pytest.mark.parametrize("fault", ["reconcile", "claim", "image", "png_hash", "copy_hash", "claim_outbox",
    "stale_source", "wrong_day", "prepare", "begin", "bind", "reserve", "reused_reserve", "confirm", "send",
    "terminal", "original_mutated", "terminal_recovery"])
def test_unknown_or_drift_never_retries_or_skips_a_part(fault):
    c = Chain(fault)
    r = asyncio.run(c.runner().run())
    assert r["status"] != "card_recorded" and r["public_send_attempted"] is False
    assert c.original == c.original_before
    assert c.calls.count("gateway:claim") <= 1 and c.calls.count("gateway:begin") <= 1
    assert c.calls.count("gateway:register") <= 1 and c.calls.count("gateway:terminal") <= 1
    if fault in {"confirm", "send"}: assert len(c.sent) == 1
    elif fault in {"terminal", "original_mutated", "terminal_recovery"}: assert len(c.sent) == 4
    else: assert not c.sent


def test_runner_returns_bounded_success_receipt_and_closes_state():
    c = Chain()
    r = asyncio.run(run_recovery(c.s, clock=lambda: NOW,
        state_factory=lambda *a, **k: c.state, runner_factory=lambda *a: c.runner()))
    assert r == {"ok": True, "status": "completed", "attempted_cards": 1, "cards_recorded": 1,
        "confirmed_parts": 4, "invocation_dispatch_enabled": False, "public_send_attempted": False}
    assert c.state.closed


def test_image_packages_recovery_without_command_or_schedule_change():
    root = Path(__file__).parents[1]
    docker = (root / "Dockerfile.private-review-card").read_text()
    assert "COPY scripts/run_recovery_private_review_cards.py" in docker
    assert "CONTENT_OPS_RECOVERY_COURIER_ENABLED=false" in docker
    assert 'CMD ["python", "-m", "scripts.run_private_review_card_canary"]' in docker
    assert "COPY core/content_ops ./core/content_ops" in docker
    assert "core/automation" not in docker
    existing = json.loads((root / "ops/content-ops/railway.trial-private-card.json").read_text())
    assert existing["deploy"]["restartPolicyType"] == "NEVER"
    assert "cronSchedule" not in existing["deploy"]
