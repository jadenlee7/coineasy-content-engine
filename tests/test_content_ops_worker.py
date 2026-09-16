from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import pytest

from core.content_ops.worker import (
    APP_ORIGIN, DESTINATION_ROLE, GATEWAY_PATH, DeliveryReceipt,
    HttpReviewGateway, ReviewClaim, ReviewError, ReviewSettings, ReviewWorker,
    TelegramReviewRelay, build_packet, review_enabled,
)
from scripts import run_content_ops_review as cli


SHA = "a" * 40
NOW = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)
# Entirely synthetic fixtures; these are not configured credentials or targets.
BOT_ID = 123456789
BOT_TOKEN = str(BOT_ID) + ":" + "a" * 35
CHAT_ID = "-1001234567890"
GATEWAY_TOKEN = "test_gateway_" + "b" * 40
CANARY_VERSION = "44444444-4444-4444-8444-444444444444"
DAILY_SCOPE = {"mode": "daily", "content_version_id": None}
CANARY_SCOPE = {"mode": "canary", "content_version_id": CANARY_VERSION}
HANDLES = {"yellow": "Yellow", "origintrail": "origin_trail", "squid": "SquidRouter", "babylon": "babylonlabs_io"}


def env(**overrides):
    result = {
        "CONTENT_OPS_REVIEW_ENABLED": "true",
        "CONTENT_OPS_REVIEW_MODE": "daily",
        "CONTENT_OPS_GATEWAY_URL": APP_ORIGIN,
        "CONTENT_OPS_GATEWAY_TOKEN": GATEWAY_TOKEN,
        "CONTENT_OPS_REVIEW_RELEASE_SHA": SHA,
        "RAILWAY_GIT_COMMIT_SHA": SHA,
        "CONTENT_OPS_STUDIO_URL": APP_ORIGIN,
        "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN": BOT_TOKEN,
        "TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID": CHAT_ID,
    }
    return {**result, **overrides}


def settings(**overrides):
    return ReviewSettings.from_env(env(**overrides), stamp_reader=lambda: SHA)


def claim(client="squid", **overrides):
    base = {
        "outbox_id": "11111111-1111-4111-8111-111111111111",
        "claim_token": "22222222-2222-4222-8222-222222222222",
        "client_id": client,
        "kst_date": "2026-09-06",
        "content_item_id": "33333333-3333-4333-8333-333333333333",
        "content_version_id": "44444444-4444-4444-8444-444444444444",
        "source_item_id": "55555555-5555-4555-8555-555555555555",
        "generate_job_id": "66666666-6666-4666-8666-666666666666",
        "banner_sha256": "c" * 64,
        "title": "한국어 제목 <b>not markup</b> & details",
        "telegram_copy": "Full Telegram copy stays in Studio.",
        "x_copy": "Full X copy stays in Studio.",
        "source_url": f"https://x.com/{HANDLES[client]}/status/2083266484789514640",
        "source_published_at": "2026-09-06T07:00:00Z",
    }
    return {**base, **overrides}


def fail(*args, **kwargs):
    raise AssertionError("Unexpected I/O or credential access")


class FakeGateway:
    def __init__(self, claims=None, *, wrong_sha=None, fail_action=None,
                 denied=None, mismatch_token=False, events=None, scope=None):
        self.claims = list(claims if claims is not None else [claim()])
        self.calls = []
        self.wrong_sha = wrong_sha
        self.fail_action = fail_action
        self.denied = denied
        self.mismatch_token = mismatch_token
        self.events = events if events is not None else []
        self.scope = scope if scope is not None else DAILY_SCOPE

    async def request(self, action, **fields):
        self.calls.append((action, fields))
        self.events.append(action)
        if action == self.fail_action:
            raise httpx.ReadTimeout("sensitive failure text must not escape")
        result = {"ok": True, "release_sha": "b" * 40 if action == self.wrong_sha else SHA, "scope": self.scope}
        if action == "reconcile":
            result["queued"] = min(1 if self.scope["mode"] == "canary" else 4, len(self.claims))
        elif action == "claim":
            assert set(fields) == {"claim_token"}
            assert str(UUID(fields["claim_token"])) == fields["claim_token"]
            current = self.claims.pop(0) if self.claims else None
            if current is not None and not self.mismatch_token:
                current = {**current, "claim_token": fields["claim_token"]}
            result["claim"] = current
        elif action == "begin":
            assert set(fields) == {"outbox_id", "claim_token", "packet_sha256"}
            result["accepted"] = action != self.denied
        elif action == "finish":
            assert set(fields) == {"outbox_id", "claim_token", "outcome", "message_id"}
            result["accepted"] = action != self.denied
        else:
            raise AssertionError("unexpected action")
        return result


class FakeRelay:
    def __init__(self, *, receipt=None, preflight_error=False, events=None):
        self.receipt = receipt or DeliveryReceipt("sent", 17)
        self.preflight_error = preflight_error
        self.packets = []
        self.events = events if events is not None else []

    async def preflight(self):
        self.events.append("preflight")
        if self.preflight_error:
            raise ReviewError("preflight_failed")

    async def send(self, packet):
        self.events.append("send")
        self.packets.append(packet)
        return self.receipt


def run_worker(gateway=None, relay=None, *, configured=None):
    gateway = gateway or FakeGateway()
    relay = relay or FakeRelay()
    return asyncio.run(ReviewWorker(configured or settings(), gateway, relay, now=lambda: NOW).run())


@pytest.mark.parametrize("value", ["", "TRUE", "False", "1", "0", " true", "false "])
def test_enable_is_strict(value):
    with pytest.raises(ValueError, match="flag_invalid"):
        review_enabled({"CONTENT_OPS_REVIEW_ENABLED": value})


def test_disabled_uses_only_enable_flag_and_zero_io(monkeypatch):
    class FlagOnly(dict):
        def get(self, name, default=None):
            assert name == "CONTENT_OPS_REVIEW_ENABLED"
            return "false"
        def __iter__(self):
            fail()
    monkeypatch.setattr(cli, "_build_worker", fail)
    monkeypatch.setattr(httpx, "AsyncClient", fail)
    result = cli.run(environ=FlagOnly(), stamp_reader=fail)
    assert result == {"ok": True, "mode": "run", "enabled": False, "claimed": 0}
    assert review_enabled({}) is False


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_validation_never_constructs_network_clients(monkeypatch, enabled):
    monkeypatch.setattr(cli, "_build_worker", fail)
    monkeypatch.setattr(httpx, "AsyncClient", fail)
    result = cli.run(validate_only=True, environ=env(CONTENT_OPS_REVIEW_ENABLED=enabled), stamp_reader=lambda: SHA)
    assert result["ok"] is True
    assert result["network_calls"] is result["database_calls"] is result["telegram_calls"] is False


@pytest.mark.parametrize("mode", ["", "CANARY", "Daily", "true", "canary ", " daily"])
def test_review_mode_is_explicit_and_strict_before_stamp_or_io(mode):
    with pytest.raises(ValueError, match="content_ops_review_mode_invalid"):
        ReviewSettings.from_env(env(CONTENT_OPS_REVIEW_MODE=mode), stamp_reader=fail)


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_missing_mode_rejected_by_validation_without_network(monkeypatch, enabled):
    values = env(CONTENT_OPS_REVIEW_ENABLED=enabled)
    values.pop("CONTENT_OPS_REVIEW_MODE")
    monkeypatch.setattr(httpx, "AsyncClient", fail)
    monkeypatch.setattr(cli, "_build_worker", fail)
    result = cli.run(validate_only=True, environ=values, stamp_reader=fail)
    assert result["ok"] is False and result["network_calls"] is False
    if enabled == "true":
        assert cli.run(environ=values, stamp_reader=fail)["ok"] is False
    else:
        assert cli.run(environ=values, stamp_reader=fail)["enabled"] is False


@pytest.mark.parametrize("version", [
    "", "not-a-uuid", "00000000-0000-0000-0000-000000000000",
    "aaaaaaaa-aaaa-4aaa-aaaa-AAAAAAAAAAAA", CANARY_VERSION + " ",
    "44444444-4444-7444-8444-444444444444", "44444444-4444-4444-7444-444444444444",
])
def test_canary_requires_canonical_supported_uuid(version):
    with pytest.raises(ValueError, match="content_ops_canary_version_invalid"):
        ReviewSettings.from_env(env(
            CONTENT_OPS_REVIEW_MODE="canary", CONTENT_OPS_REVIEW_CANARY_VERSION_ID=version,
        ), stamp_reader=fail)


def test_canary_missing_version_rejected_and_daily_nonempty_version_forbidden():
    with pytest.raises(ValueError, match="content_ops_canary_version_invalid"):
        settings(CONTENT_OPS_REVIEW_MODE="canary")
    for version in [CANARY_VERSION, " ", "not-a-uuid"]:
        with pytest.raises(ValueError, match="content_ops_daily_version_forbidden"):
            settings(CONTENT_OPS_REVIEW_MODE="daily", CONTENT_OPS_REVIEW_CANARY_VERSION_ID=version)
    assert settings().scope == DAILY_SCOPE
    assert settings(CONTENT_OPS_REVIEW_CANARY_VERSION_ID="").max_claims == 4


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_canary_validation_is_zero_network_even_enabled(monkeypatch, enabled):
    monkeypatch.setattr(httpx, "AsyncClient", fail)
    monkeypatch.setattr(cli, "_build_worker", fail)
    values = env(
        CONTENT_OPS_REVIEW_ENABLED=enabled, CONTENT_OPS_REVIEW_MODE="canary",
        CONTENT_OPS_REVIEW_CANARY_VERSION_ID=CANARY_VERSION,
    )
    result = cli.run(validate_only=True, environ=values, stamp_reader=lambda: SHA)
    assert result["ok"] is True
    assert result["network_calls"] is result["database_calls"] is result["telegram_calls"] is False


@pytest.mark.parametrize("stamp", ["", "b" * 40, "a" * 39, SHA + "\n"])
def test_invalid_build_stamp_fails_without_client_construction(monkeypatch, stamp):
    monkeypatch.setattr(cli, "_build_worker", fail)
    assert cli.run(environ=env(), stamp_reader=lambda: stamp)["ok"] is False
    assert cli.run(validate_only=True, environ=env(), stamp_reader=lambda: stamp)["ok"] is False


def test_missing_build_stamp_fails_closed(monkeypatch, tmp_path):
    from core.content_ops import worker
    monkeypatch.setattr(worker, "BUILD_SHA_PATH", tmp_path / "does-not-exist")
    monkeypatch.setattr(cli, "_build_worker", fail)
    assert cli.run(environ=env())["ok"] is False


@pytest.mark.parametrize("name", [
    "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_URL", "SUPABASE_SECRET_KEY",
    "DATABASE_URL", "DB_ADMIN_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "TYPEFULLY_API_KEY", "FIGMA_ACCESS_TOKEN", "X_BEARER_TOKEN", "PUBLICATION_WORKER_TOKEN",
    "STUDIO_ACCESS_TOKEN", "API_SECRET", "CONTENT_STUDIO_WORKSPACE_ID",
    "TELEGRAM_REVIEW_BOT_TOKEN", "TELEGRAM_REVIEW_CHAT_ID", "TELEGRAM_ADMIN_TOKEN",
    "TELEGRAM_BOT_TOKEN_SQUID", "TELEGRAM_CHANNEL_BABYLON",
])
def test_forbidden_credentials_rejected_without_disclosure(name):
    with pytest.raises(ValueError, match="content_ops_forbidden_credential") as error:
        settings(**{name: "sensitive-test-value"})
    assert "sensitive-test-value" not in str(error.value)
    assert name not in str(error.value)


@pytest.mark.parametrize("name,value", [
    ("CONTENT_OPS_GATEWAY_URL", "http://coineasy-newscard.netlify.app"),
    ("CONTENT_OPS_GATEWAY_URL", "https://attacker.example"),
    ("CONTENT_OPS_GATEWAY_URL", APP_ORIGIN + GATEWAY_PATH),
    ("CONTENT_OPS_STUDIO_URL", "https://other.example"),
    ("CONTENT_OPS_STUDIO_URL", APP_ORIGIN + "/?token=secret"),
    ("CONTENT_OPS_GATEWAY_TOKEN", "a" * 31),
    ("CONTENT_OPS_GATEWAY_TOKEN", "a" * 257),
    ("CONTENT_OPS_GATEWAY_TOKEN", "ab!" * 20),
    ("TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID", "123456789"),
    ("TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID", "@public_channel"),
    ("TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", "invalid"),
    ("RAILWAY_GIT_COMMIT_SHA", "b" * 40),
    ("CONTENT_OPS_REVIEW_RELEASE_SHA", "A" * 40),
])
def test_configuration_fences(name, value):
    with pytest.raises(ValueError):
        settings(**{name: value})


def test_gateway_token_and_relay_are_dedicated_and_repr_redacted():
    for alias in [{"OTHER_ACCESS_TOKEN": BOT_TOKEN}, {"OTHER_API_KEY": GATEWAY_TOKEN}, {"OTHER_CHAT_ID": CHAT_ID}]:
        with pytest.raises(ValueError):
            settings(**alias)
    rendered = repr(settings())
    assert all(value not in rendered for value in [BOT_TOKEN, CHAT_ID, GATEWAY_TOKEN])


@pytest.mark.parametrize("override", [
    {"extra_private_field": "not allowed"}, {"outbox_id": "bad-id"},
    {"content_version_id": "00000000-0000-0000-0000-000000000000"},
    {"client_id": "unknown"}, {"kst_date": "2026-09-05"},
    {"source_url": "https://x.com/attacker/status/123"},
    {"source_url": "https://x.com/SquidRouter/status/123?token=secret"},
    {"source_published_at": "2026-09-05T08:00:00Z"},
    {"source_published_at": "2026-09-06T08:00:01Z"},
    {"source_published_at": "2026-09-06T07:00:00"},
    {"banner_sha256": "z" * 64}, {"title": "bad\x00text"}, {"title": "a" * 241},
    {"telegram_copy": "a" * 3401}, {"x_copy": "a" * 1001},
    {"telegram_copy": BOT_TOKEN}, {"title": CHAT_ID},
    {"x_copy": "https://t.me/+privateinvite"},
])
def test_untrusted_claim_rejected_before_begin(override):
    gateway, relay = FakeGateway([claim(**override)]), FakeRelay()
    summary = run_worker(gateway, relay)
    assert summary["blocked"] == 1
    assert not relay.packets
    assert "begin" not in [a for a, _ in gateway.calls]


def test_source_within_24h_can_cross_kst_midnight():
    value = claim(source_published_at="2026-09-05T09:00:01Z")
    assert ReviewClaim.parse(value, NOW).source_published_at < NOW


def test_packet_is_escaped_version_locked_and_hash_has_logical_target_only():
    parsed = ReviewClaim.parse(claim(), NOW)
    packet = build_packet(parsed, APP_ORIGIN, NOW)
    assert "&lt;b&gt;not markup&lt;/b&gt; &amp; details" in packet.text
    assert "view=library&content=" + parsed.content_item_id in packet.review_url
    assert "&version=" + parsed.content_version_id in packet.review_url
    assert parsed.banner_sha256 in packet.text
    assert parsed.telegram_copy not in packet.text and parsed.x_copy not in packet.text
    assert all(value not in packet.text for value in [BOT_TOKEN, CHAT_ID, GATEWAY_TOKEN])
    expected = hashlib.sha256(json.dumps({
        "text": packet.text, "review_url": packet.review_url, "destination_role": DESTINATION_ROLE,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert packet.packet_sha256 == expected
    assert packet.packet_sha256 == build_packet(parsed, APP_ORIGIN, NOW).packet_sha256
    assert parsed.title not in repr(parsed) and packet.text not in repr(packet)


def test_claimed_token_must_match_caller_generated_token():
    relay = FakeRelay()
    summary = run_worker(FakeGateway(mismatch_token=True), relay)
    assert summary["blocked"] == 1 and not relay.packets


def test_claim_cannot_reflect_dedicated_gateway_credential():
    relay = FakeRelay()
    summary = run_worker(FakeGateway([claim(title=GATEWAY_TOKEN)]), relay)
    assert summary["blocked"] == 1 and not relay.packets


def test_preflight_begin_send_finish_order_and_no_approval_or_workspace():
    events = []
    gateway, relay = FakeGateway(events=events), FakeRelay(events=events)
    summary = run_worker(gateway, relay)
    assert summary["ok"] is True and summary["sent"] == 1
    assert events == ["preflight", "reconcile", "claim", "begin", "send", "finish", "claim"]
    serialized = json.dumps(summary)
    assert all(value not in serialized for value in [claim()["title"], BOT_TOKEN, CHAT_ID, GATEWAY_TOKEN])
    assert not any("workspace_id" in fields or "client_id" in fields for _, fields in gateway.calls)
    assert next(fields for action, fields in gateway.calls if action == "finish")["message_id"] == 17


def test_four_client_hard_limit_and_no_fifth_claim():
    gateway = FakeGateway([claim(client) for client in HANDLES] + [claim()])
    relay = FakeRelay()
    summary = run_worker(gateway, relay)
    assert summary["sent"] == summary["claimed"] == 4
    assert len(relay.packets) == 4
    assert [action for action, _ in gateway.calls].count("claim") == 4


def canary_settings():
    return settings(CONTENT_OPS_REVIEW_MODE="canary", CONTENT_OPS_REVIEW_CANARY_VERSION_ID=CANARY_VERSION)


def test_canary_maximum_one_claim_and_no_second_client_consumed():
    gateway = FakeGateway([claim(), claim("yellow")], scope=CANARY_SCOPE)
    relay = FakeRelay()
    configured = canary_settings()
    assert configured.max_claims == 1 and configured.scope == CANARY_SCOPE
    summary = run_worker(gateway, relay, configured=configured)
    assert summary["ok"] is True and summary["sent"] == summary["claimed"] == 1
    assert len(relay.packets) == 1 and len(gateway.claims) == 1
    assert [a for a, _ in gateway.calls] == ["reconcile", "claim", "begin", "finish"]


def test_canary_no_matching_claim_sends_nothing_and_does_not_claim_again():
    gateway, relay = FakeGateway([], scope=CANARY_SCOPE), FakeRelay()
    summary = run_worker(gateway, relay, configured=canary_settings())
    assert summary["ok"] is True and summary["queued"] == summary["claimed"] == summary["sent"] == 0
    assert not relay.packets and [a for a, _ in gateway.calls] == ["reconcile", "claim"]


def test_canary_wrong_claim_version_is_blocked_before_begin():
    gateway = FakeGateway([
        claim(content_version_id="77777777-7777-4777-8777-777777777777"),
    ], scope=CANARY_SCOPE)
    relay = FakeRelay()
    summary = run_worker(gateway, relay, configured=canary_settings())
    assert summary["blocked"] == 1 and not relay.packets
    assert [a for a, _ in gateway.calls] == ["reconcile", "claim"]


def test_canary_reconcile_more_than_one_is_blocked_before_claim():
    class WideReconcile(FakeGateway):
        async def request(self, action, **fields):
            raw = await super().request(action, **fields)
            if action == "reconcile":
                raw["queued"] = 2
            return raw
    gateway, relay = WideReconcile(scope=CANARY_SCOPE), FakeRelay()
    assert run_worker(gateway, relay, configured=canary_settings())["blocked"] == 1
    assert not relay.packets and [a for a, _ in gateway.calls] == ["reconcile"]


@pytest.mark.parametrize("action", ["reconcile", "claim", "begin", "finish"])
@pytest.mark.parametrize("bad_scope", [
    None, {}, DAILY_SCOPE,
    {"mode": "canary", "content_version_id": None},
    {"mode": "canary", "content_version_id": "77777777-7777-4777-8777-777777777777"},
    {**CANARY_SCOPE, "extra": "not-allowed"},
])
def test_every_canary_receipt_requires_exact_scope_before_followup(action, bad_scope):
    class BadScopeGateway(FakeGateway):
        async def request(self, current_action, **fields):
            raw = await super().request(current_action, **fields)
            if current_action == action:
                if bad_scope is None:
                    raw.pop("scope")
                else:
                    raw["scope"] = bad_scope
            return raw
    gateway, relay = BadScopeGateway(scope=CANARY_SCOPE), FakeRelay()
    summary = run_worker(gateway, relay, configured=canary_settings())
    assert summary["ok"] is False
    assert [a for a, _ in gateway.calls].count(action) == 1
    if action == "finish":
        assert summary["ack_unknown"] == 1 and len(relay.packets) == 1
    else:
        assert summary["blocked"] == 1 and not relay.packets


def test_daily_rejects_canary_receipt_before_claim():
    gateway, relay = FakeGateway(scope=CANARY_SCOPE), FakeRelay()
    assert run_worker(gateway, relay)["blocked"] == 1
    assert not relay.packets and [a for a, _ in gateway.calls] == ["reconcile"]


def test_duplicate_client_cannot_send_twice_in_run():
    gateway, relay = FakeGateway([claim(), claim()]), FakeRelay()
    summary = run_worker(gateway, relay)
    assert len(relay.packets) == 1 and summary["blocked"] == 1


@pytest.mark.parametrize("action", ["reconcile", "claim", "begin"])
@pytest.mark.parametrize("failure", ["wrong_sha", "fail_action"])
def test_gateway_early_failures_stop_without_send_or_retry(action, failure):
    gateway, relay = FakeGateway(**{failure: action}), FakeRelay()
    summary = run_worker(gateway, relay)
    assert summary["blocked"] == 1 and not relay.packets
    assert [a for a, _ in gateway.calls].count(action) == 1


def test_denied_begin_never_sends():
    relay = FakeRelay()
    assert run_worker(FakeGateway(denied="begin"), relay)["blocked"] == 1
    assert not relay.packets


@pytest.mark.parametrize("outcome", ["rejected", "delivery_unknown"])
def test_terminal_receipt_finishes_once_and_no_second_claim(outcome):
    gateway = FakeGateway([claim(), claim("yellow")])
    relay = FakeRelay(receipt=DeliveryReceipt(outcome))
    summary = run_worker(gateway, relay)
    assert summary[outcome] == 1 and summary["ok"] is False
    assert len(relay.packets) == 1
    assert [a for a, _ in gateway.calls].count("finish") == 1
    assert [a for a, _ in gateway.calls].count("claim") == 1
    finish = next(fields for action, fields in gateway.calls if action == "finish")
    assert finish["outcome"] == outcome and finish["message_id"] is None


@pytest.mark.parametrize("option", ["wrong_sha", "fail_action", "denied"])
def test_finish_failure_is_ack_unknown_no_replay(option):
    gateway = FakeGateway([claim(), claim("yellow")], **{option: "finish"})
    relay = FakeRelay()
    summary = run_worker(gateway, relay)
    assert summary["ack_unknown"] == 1 and summary["sent"] == 1 and summary["ok"] is False
    assert len(relay.packets) == 1 and [a for a, _ in gateway.calls].count("finish") == 1


def test_failed_preflight_touches_no_gateway():
    gateway = FakeGateway()
    assert run_worker(gateway, FakeRelay(preflight_error=True))["blocked"] == 1
    assert gateway.calls == []


def telegram_handler(*, mutation=None, send_status=200, send_body=None, timeout_method=None, events=None):
    events = events if events is not None else []
    def handle(request):
        assert request.url.host == "api.telegram.org"
        method = request.url.path.rsplit("/", 1)[-1]
        events.append(method)
        assert request.method == "POST"
        if method == timeout_method:
            raise httpx.ReadTimeout("private failure", request=request)
        body = json.loads(request.content)
        if method == "getMe":
            result = {"id": BOT_ID, "is_bot": True}
        elif method == "getChat":
            assert body == {"chat_id": CHAT_ID}
            result = {"id": int(CHAT_ID), "type": "supergroup"}
        elif method == "getChatMember":
            assert body == {"chat_id": CHAT_ID, "user_id": BOT_ID}
            result = {"status": "member", "user": {"id": BOT_ID, "is_bot": True}}
        elif method == "sendMessage":
            assert body["chat_id"] == CHAT_ID and body["parse_mode"] == "HTML"
            assert body["link_preview_options"] == {"is_disabled": True}
            assert body["protect_content"] is True
            assert "reply_markup" not in body and "photo" not in body
            if isinstance(send_body, bytes):
                return httpx.Response(send_status, content=send_body)
            return httpx.Response(send_status, json=send_body if send_body is not None else {
                "ok": True, "result": {"chat": {"id": int(CHAT_ID)}, "message_id": 17},
            })
        else:
            raise AssertionError("unauthorized provider operation")
        if mutation and mutation[0] == method:
            result = {**result, **mutation[1]}
        return httpx.Response(200, json={"ok": True, "result": result})
    return handle


@pytest.mark.parametrize("mutation", [
    ("getMe", {"id": BOT_ID + 1}), ("getMe", {"is_bot": False}),
    ("getChat", {"id": int(CHAT_ID) - 1}), ("getChat", {"type": "channel"}),
    ("getChat", {"username": "public_room"}), ("getChat", {"active_usernames": ["public_room"]}),
    ("getChat", {"linked_chat_id": -123}),
    ("getChatMember", {"status": "administrator"}),
    ("getChatMember", {"status": "creator"}), ("getChatMember", {"status": "restricted"}),
    ("getChatMember", {"user": {"id": BOT_ID + 1, "is_bot": True}}),
])
def test_telegram_preflight_identity_target_role_fail_closed(mutation):
    events = []
    relay = TelegramReviewRelay(settings(), transport=httpx.MockTransport(telegram_handler(mutation=mutation, events=events)))
    gateway = FakeGateway()
    assert run_worker(gateway, relay)["blocked"] == 1
    assert not gateway.calls and "sendMessage" not in events


@pytest.mark.parametrize("method", ["getMe", "getChat", "getChatMember"])
def test_telegram_preflight_timeout_has_no_retry(method):
    events = []
    relay = TelegramReviewRelay(settings(), transport=httpx.MockTransport(telegram_handler(timeout_method=method, events=events)))
    assert run_worker(FakeGateway(), relay)["blocked"] == 1
    assert events.count(method) == 1 and "sendMessage" not in events


@pytest.mark.parametrize("status,body,expected", [
    (200, {"ok": True, "result": {"chat": {"id": int(CHAT_ID)}, "message_id": 17}}, "sent"),
    (400, {"ok": False, "error_code": 400}, "rejected"),
    (403, {"ok": False, "error_code": 403}, "rejected"),
    (429, {"ok": False, "error_code": 429}, "rejected"),
    (500, {"ok": False}, "delivery_unknown"),
    (302, {}, "delivery_unknown"),
    (400, {"ok": True}, "delivery_unknown"),
    (200, {"ok": False}, "delivery_unknown"),
    (200, {"ok": True}, "delivery_unknown"),
    (200, {"ok": True, "result": {"chat": {"id": int(CHAT_ID) - 1}, "message_id": 17}}, "delivery_unknown"),
    (200, {"ok": True, "result": {"chat": {"id": int(CHAT_ID)}, "message_id": True}}, "delivery_unknown"),
    (200, {"ok": True, "result": {"chat": {"id": int(CHAT_ID)}, "message_id": 0}}, "delivery_unknown"),
    (200, {"ok": True, "result": {"chat": {"id": int(CHAT_ID)}, "message_id": 2**53}}, "delivery_unknown"),
    (200, b'{"ok":true,"result":', "delivery_unknown"),
])
def test_telegram_send_receipt_one_attempt(status, body, expected):
    events = []
    relay = TelegramReviewRelay(settings(), transport=httpx.MockTransport(telegram_handler(send_status=status, send_body=body, events=events)))
    gateway = FakeGateway()
    summary = run_worker(gateway, relay)
    assert summary[expected] == 1 and events.count("sendMessage") == 1
    finish = next(fields for action, fields in gateway.calls if action == "finish")
    assert finish["outcome"] == expected
    assert finish["message_id"] == (17 if expected == "sent" else None)


def test_telegram_send_timeout_finishes_unknown_and_never_retries():
    events = []
    relay = TelegramReviewRelay(settings(), transport=httpx.MockTransport(telegram_handler(timeout_method="sendMessage", events=events)))
    gateway = FakeGateway([claim(), claim("yellow")])
    summary = run_worker(gateway, relay)
    assert summary["delivery_unknown"] == 1 and summary["ack_unknown"] == 0
    assert events.count("sendMessage") == 1


def test_gateway_http_request_origin_release_and_exact_schema():
    seen = []
    def handler(request):
        seen.append(request)
        assert str(request.url) == APP_ORIGIN + GATEWAY_PATH
        assert request.headers["authorization"] == "Bearer " + GATEWAY_TOKEN
        assert request.headers["x-content-ops-expected-release-sha"] == SHA
        assert request.headers["x-content-ops-mode"] == "daily"
        assert "x-content-ops-version-id" not in request.headers
        assert json.loads(request.content) == {"action": "reconcile"}
        return httpx.Response(200, json={"ok": True, "release_sha": SHA, "scope": DAILY_SCOPE, "queued": 0})
    gateway = HttpReviewGateway(settings(), transport=httpx.MockTransport(handler))
    assert asyncio.run(gateway.request("reconcile"))["queued"] == 0
    assert len(seen) == 1


@pytest.mark.parametrize("scope", [None, DAILY_SCOPE, {**CANARY_SCOPE, "unexpected": True}])
def test_http_gateway_rejects_wrong_or_missing_canary_scope_without_retry(scope):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["x-content-ops-mode"] == "canary"
        assert request.headers["x-content-ops-version-id"] == CANARY_VERSION
        raw = {"ok": True, "release_sha": SHA, "queued": 0}
        if scope is not None:
            raw["scope"] = scope
        return httpx.Response(200, json=raw)
    gateway = HttpReviewGateway(canary_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ReviewError, match="content_ops_gateway_scope_mismatch"):
        asyncio.run(gateway.request("reconcile"))
    assert len(requests) == 1


@pytest.mark.parametrize("mode", ["daily", "canary"])
def test_full_http_gateway_contract_and_telegram_flow_have_only_known_operations(mode):
    events = []
    token = None
    packet_hash = None
    did_claim = False
    telegram = telegram_handler(events=events)
    def handler(request):
        nonlocal token, packet_hash, did_claim
        if request.url.host == "api.telegram.org":
            return telegram(request)
        assert str(request.url) == APP_ORIGIN + GATEWAY_PATH
        assert request.headers["authorization"] == "Bearer " + GATEWAY_TOKEN
        assert request.headers["x-content-ops-expected-release-sha"] == SHA
        assert request.headers["x-content-ops-mode"] == mode
        if mode == "canary":
            assert request.headers["x-content-ops-version-id"] == CANARY_VERSION
        else:
            assert "x-content-ops-version-id" not in request.headers
        body = json.loads(request.content)
        action = body.pop("action")
        events.append(action)
        response = {"ok": True, "release_sha": SHA, "scope": CANARY_SCOPE if mode == "canary" else DAILY_SCOPE}
        if action == "reconcile":
            assert body == {}
            response["queued"] = 1
        elif action == "claim":
            assert set(body) == {"claim_token"}
            if did_claim:
                response["claim"] = None
            else:
                token = body["claim_token"]
                did_claim = True
                response["claim"] = claim(claim_token=token)
        elif action == "begin":
            assert set(body) == {"outbox_id", "claim_token", "packet_sha256"}
            assert body["claim_token"] == token and body["outbox_id"] == claim()["outbox_id"]
            packet_hash = body["packet_sha256"]
            assert len(packet_hash) == 64
            response["accepted"] = True
        elif action == "finish":
            assert body == {
                "outbox_id": claim()["outbox_id"], "claim_token": token,
                "outcome": "sent", "message_id": 17,
            }
            response["accepted"] = True
        else:
            raise AssertionError("unauthorized gateway action")
        return httpx.Response(200, json=response)
    transport = httpx.MockTransport(handler)
    configured = settings(
        CONTENT_OPS_REVIEW_MODE=mode,
        CONTENT_OPS_REVIEW_CANARY_VERSION_ID=CANARY_VERSION if mode == "canary" else "",
    )
    summary = asyncio.run(ReviewWorker(
        configured, HttpReviewGateway(configured, transport=transport),
        TelegramReviewRelay(configured, transport=transport), now=lambda: NOW,
    ).run())
    assert summary["ok"] is True and summary["sent"] == 1
    assert events == ["getMe", "getChat", "getChatMember", "reconcile", "claim", "begin", "sendMessage", "finish"] + (["claim"] if mode == "daily" else [])


def test_httpx_request_logs_do_not_leak_private_transport(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    relay = TelegramReviewRelay(settings(), transport=httpx.MockTransport(telegram_handler()))
    asyncio.run(relay.preflight())
    assert BOT_TOKEN not in caplog.text and CHAT_ID not in caplog.text
    assert "api.telegram.org/bot" not in caplog.text


def test_cli_prints_only_sanitized_errors(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run", lambda **kwargs: {"ok": False, "mode": "run", "error": "content_ops_review_failed"})
    assert cli.main([]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "content_ops_review_failed"
