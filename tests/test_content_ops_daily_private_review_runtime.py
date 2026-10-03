"""Default-OFF daily CLI and exact runtime settings; zero real provider I/O."""
import asyncio
import json
from pathlib import Path

import pytest

from scripts import run_daily_private_review_cards as cli
from test_content_ops_private_review_card_runtime import config, SHA
from test_content_ops_daily_private_review import NOW, ident, raw_claim, receipt
from core.content_ops.private_review_bot_policy import ExistingReviewBotPolicy


ROOT = Path(__file__).resolve().parents[1]
POLICY = json.dumps({"schema": "existing-review-bot-policy@1", "bot_id": 123456789,
    "chat_id": -1001234567890, "room_binding": "fixture-existing-owner-room-v1",
    "membership_status": "administrator"})


def daily_config(**overrides):
    return config(CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED="false",
        CONTENT_OPS_REVIEW_MODE="daily", CONTENT_OPS_REVIEW_PACKET_MODE="daily_button_card_v1",
        CONTENT_OPS_REVIEW_CANARY_VERSION_ID="",
        CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON=POLICY, **overrides)


def test_disabled_cli_reads_only_daily_flag_and_never_constructs_runtime():
    class OnlyFlag(dict):
        def get(self, key, default=None):
            assert key == "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED"
            return "false"
    assert cli.run(environ=OnlyFlag(), runner_factory=lambda _: 1) == {
        "ok": True, "mode": "run", "enabled": False, "public_send_attempted": False}


@pytest.mark.parametrize("flag", [None, True, 1, "TRUE", "yes", " false "])
def test_enabled_flag_is_strict(flag):
    assert cli.run(environ={"CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED": flag}) == {
        "ok": False, "mode": "run", "error": "daily_private_card_failed"}


def test_validate_only_checks_pinned_existing_bot_without_clients_or_secret_output(monkeypatch):
    import httpx
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError("validate-only constructed client or runtime")
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = cli.run(validate_only=True, environ=daily_config(), stamp_reader=lambda: SHA,
                     runner_factory=forbidden)
    assert result == {"ok": True, "mode": "validate_only", "enabled": False,
        "network_calls": False, "database_calls": False, "telegram_calls": False,
        "public_send_attempted": False, "provenance": {
            "schema_version": "private-card-runtime-provenance@1",
            "build_release_verified": True, "runtime_git_sha_state": "match",
            "runtime_release_verified": True}}
    output = json.dumps(result)
    for key in ("TELEGRAM_REVIEW_BOT_TOKEN", "TELEGRAM_REVIEW_CHAT_ID",
                "CONTENT_OPS_GATEWAY_TOKEN", "CONTENT_OPS_BUTTON_SIGNING_KEY",
                "CONTENT_OPS_EDIT_BINDING_KEY", "CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON"):
        assert daily_config()[key] not in output
    assert SHA not in output


@pytest.mark.parametrize("changes", [
    {"CONTENT_OPS_BUTTON_CARD_ENABLED": "true"},
    {"CONTENT_OPS_REVIEW_ENABLED": "true"},
    {"CONTENT_OPS_REVIEW_MODE": "canary"},
    {"CONTENT_OPS_REVIEW_PACKET_MODE": "button_card_v1"},
    {"CONTENT_OPS_REVIEW_CANARY_VERSION_ID": ident(1)},
    {"CONTENT_OPS_GATEWAY_URL": "https://other.invalid"},
    {"RAILWAY_GIT_COMMIT_SHA": "b" * 40},
    {"CONTENT_OPS_REVIEW_RELEASE_SHA": "b" * 40},
    {"CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON": None},
    {"CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON": "{}"},
    {"CONTENT_OPS_EXISTING_REVIEW_BOT_POLICY_JSON": POLICY.replace("123456789", "123456780")},
    {"CONTENT_OPS_BUTTON_SIGNING_KEY": "2" * 64},
    {"AUTOMATION_DAILY_REVIEW_START_KST": "9:00"},
    {"AUTOMATION_DAILY_REVIEW_START_KST": None},
    {"SUPABASE_SERVICE_ROLE_KEY": "forbidden"},
    {"PGPASSWORD": "forbidden"},
    {"TYPEFULLY_API_KEY": "forbidden"},
    {"OPENAI_API_KEY": "forbidden"},
    {"TELEGRAM_BOT_TOKEN": "forbidden"},
])
def test_invalid_scope_policy_or_credentials_never_construct_runner(changes):
    env = {**daily_config(), **changes}
    calls = []
    result = cli.run(validate_only=True, environ=env, stamp_reader=lambda: SHA,
                     runner_factory=lambda _: calls.append(True))
    assert result == {"ok": False, "mode": "validate_only", "error": "daily_private_card_failed",
        "network_calls": False, "database_calls": False, "telegram_calls": False}
    assert calls == []


def test_off_only_missing_runtime_is_distinguished_and_stamp_read_once():
    env = daily_config()
    del env["RAILWAY_GIT_COMMIT_SHA"]
    reads = []
    def stamp():
        reads.append(True)
        return SHA
    result = cli.run(validate_only=True, environ=env, stamp_reader=stamp)
    assert result["ok"] is True
    assert result["provenance"]["runtime_git_sha_state"] == "missing"
    assert result["provenance"]["runtime_release_verified"] is False
    assert reads == [True]
    env["CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED"] = "true"
    assert cli.run(validate_only=True, environ=env, stamp_reader=lambda: SHA)["ok"] is False
    assert cli.run(environ=env, stamp_reader=lambda: SHA)["ok"] is False
    assert cli.run(validate_only=True, environ=daily_config(),
                   stamp_reader=lambda: "b" * 40)["ok"] is False


def test_enabled_cli_one_run_bounded_receipt_and_uncertainty_is_failure():
    env = {**daily_config(), "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED": "true"}
    calls = []
    class FakeRunner:
        async def run(self, *, enabled=False):
            assert enabled is True
            calls.append(True)
            return {"status": "completed", "queued": 4, "claimed": 4,
                "cards_recorded": 4, "confirmed_parts": 16, "public_send_attempted": False}
    result = cli.run(environ=env, stamp_reader=lambda: SHA, runner_factory=lambda _: FakeRunner())
    assert result["ok"] is True and result["cards_recorded"] == 4
    assert calls == [True]
    class BadRunner:
        async def run(self, *, enabled=False):
            return {"status": "delivery_unknown", "queued": 4, "claimed": 1,
                "cards_recorded": 0, "confirmed_parts": 1, "public_send_attempted": False}
    result = cli.run(environ=env, stamp_reader=lambda: SHA, runner_factory=lambda _: BadRunner())
    assert result["ok"] is False and result["status"] == "delivery_unknown"


@pytest.mark.parametrize("change", [
    {"queued": 5}, {"claimed": True}, {"cards_recorded": 2},
    {"confirmed_parts": 17}, {"public_send_attempted": True}, {"provider_response": "do-not-emit"},
])
def test_cli_rejects_unbounded_or_inconsistent_internal_receipt(change):
    class FakeRunner:
        async def run(self, *, enabled=False):
            return {"status": "completed", "queued": 1, "claimed": 1,
                "cards_recorded": 1, "confirmed_parts": 4, "public_send_attempted": False, **change}
    result = cli.run(environ={**daily_config(), "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED": "true"},
        stamp_reader=lambda: SHA, runner_factory=lambda _: FakeRunner())
    assert result == {"ok": False, "mode": "run", "error": "daily_private_card_failed"}


def test_production_composition_uses_fresh_sender_per_claim_same_existing_policy():
    # Build/inspect only; the injected discovery responses never use real I/O.
    import httpx
    settings = cli.DailyPrivateCardSettings.from_env(daily_config(), stamp_reader=lambda: SHA)
    assert type(settings.bot_policy) is ExistingReviewBotPolicy
    worker = cli.build_runner(settings)
    discovery = worker._gateway
    discovery._clock = lambda: NOW
    def handler(req):
        body = json.loads(req.content)
        if body["action"] == "reconcile":
            return receipt("queued", 4)
        index = len(discovery._claims)
        return receipt("claim", raw_claim(index, body["claim_token"]))
    discovery._transport = httpx.MockTransport(handler)
    async def build():
        await discovery.reconcile()
        return [worker._factory(discovery.bind(await discovery.claim(ident(100 + i))))
                for i in range(4)]
    runners = asyncio.run(build())
    senders = [runner._courier._sender for runner in runners]
    assert len({id(sender) for sender in senders}) == 4
    assert all(sender._membership_status == "administrator" for sender in senders)
    assert all(sender._token == settings.bot_token and sender._chat_id == settings.chat_id
               for sender in senders)
    assert all(runner._bot_policy is settings.bot_policy for runner in runners)
    assert all(runner._reader is runner._gateway and runner._owner.gateway is runner._gateway
               for runner in runners)


def test_daily_packaging_is_unmounted_off_and_preserves_old_default_command():
    docker = (ROOT / "Dockerfile.private-review-card").read_text()
    manifest = json.loads((ROOT / "ops/content-ops/railway.private-card.json").read_text())
    assert "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED=false" in docker
    assert "COPY scripts/run_daily_private_review_cards.py" in docker
    assert 'CMD ["python", "-m", "scripts.run_private_review_card_canary"]' in docker
    assert "cronSchedule" not in manifest["deploy"]
    assert manifest["deploy"]["restartPolicyType"] == "NEVER"
