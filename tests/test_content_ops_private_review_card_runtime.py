"""Default-OFF entrypoint checks; synthetic settings, zero provider I/O."""
import json
from pathlib import Path

from scripts import run_private_review_card_canary as cli


SHA = "a" * 40
W = "11111111-1111-4111-8111-111111111111"
V = "22222222-2222-4222-8222-222222222222"
BOT = "123456789:" + "b" * 35
ROOT = Path(__file__).resolve().parents[1]


def config(**overrides):
    return {"CONTENT_OPS_BUTTON_CARD_ENABLED": "false",
        "CONTENT_OPS_REVIEW_ENABLED": "false",
        "CONTENT_OPS_REVIEW_MODE": "canary",
        "CONTENT_OPS_REVIEW_PACKET_MODE": "button_card_v1",
        "CONTENT_OPS_REVIEW_CANARY_VERSION_ID": V,
        "CONTENT_OPS_GATEWAY_URL": cli.APP_ORIGIN,
        "CONTENT_OPS_GATEWAY_TOKEN": "test_gateway_" + "c" * 40,
        "CONTENT_OPS_REVIEW_RELEASE_SHA": SHA,
        "RAILWAY_GIT_COMMIT_SHA": SHA,
        "CONTENT_STUDIO_WORKSPACE_ID": W,
        "TELEGRAM_REVIEW_BOT_TOKEN": BOT,
        "TELEGRAM_REVIEW_CHAT_ID": "-1001234567890",
        "CONTENT_OPS_BUTTON_SIGNING_KEY": "1" * 64,
        "CONTENT_OPS_EDIT_BINDING_KEY": "2" * 64,
        **overrides}


def test_disabled_run_reads_only_flag_and_creates_no_clients():
    class Guarded(dict):
        def __init__(self):
            self.keys_read = []

        def get(self, key, default=None):
            self.keys_read.append(key)
            if key != "CONTENT_OPS_BUTTON_CARD_ENABLED":
                raise AssertionError("disabled path read another setting")
            return "false"

    env = Guarded()
    assert cli.run(environ=env, runner_factory=lambda _: 1) == {
        "ok": True, "mode": "run", "enabled": False,
        "public_send_attempted": False}
    assert env.keys_read == ["CONTENT_OPS_BUTTON_CARD_ENABLED"]


def test_validate_only_checks_exact_scope_and_creates_no_clients():
    def fail(_):
        raise AssertionError("validate-only constructed a runtime")

    assert cli.run(validate_only=True, environ=config(),
        stamp_reader=lambda: SHA, runner_factory=fail) == {
            "ok": True, "mode": "validate_only", "enabled": False,
            "network_calls": False, "database_calls": False,
            "telegram_calls": False}
    for change in (
        {"CONTENT_OPS_REVIEW_PACKET_MODE": "link_card"},
        {"CONTENT_OPS_REVIEW_MODE": "daily"},
        {"CONTENT_OPS_REVIEW_ENABLED": "true"},
        {"CONTENT_OPS_GATEWAY_URL": "https://example.invalid"},
        {"RAILWAY_GIT_COMMIT_SHA": "b" * 40},
        {"CONTENT_OPS_BUTTON_SIGNING_KEY": "bad"},
        {"CONTENT_OPS_EDIT_BINDING_KEY": "1" * 64},
        {"SUPABASE_SERVICE_ROLE_KEY": "forbidden"},
        {"TYPEFULLY_API_KEY": "forbidden"},
    ):
        result = cli.run(validate_only=True, environ=config(**change),
            stamp_reader=lambda: SHA, runner_factory=fail)
        assert result == {"ok": False, "mode": "validate_only",
            "error": "private_card_canary_failed", "network_calls": False,
            "database_calls": False, "telegram_calls": False}


def test_enabled_entrypoint_is_one_run_and_prints_only_bounded_status():
    calls = []

    class FakeRunner:
        async def run(self, *, enabled=False):
            assert enabled is True
            calls.append("run")
            return {"status": "no_candidate", "public_send_attempted": False}

    result = cli.run(environ=config(CONTENT_OPS_BUTTON_CARD_ENABLED="true"),
        stamp_reader=lambda: SHA, runner_factory=lambda settings: FakeRunner())
    assert result == {"ok": True, "mode": "run", "enabled": True,
                      "status": "no_candidate", "public_send_attempted": False}
    assert calls == ["run"]
    assert BOT not in str(result)

    class BlockedRunner:
        async def run(self, *, enabled=False):
            return {"status": "delivery_unknown", "confirmed_parts": 1,
                    "public_send_attempted": False}

    blocked = cli.run(environ=config(CONTENT_OPS_BUTTON_CARD_ENABLED="true"),
        stamp_reader=lambda: SHA, runner_factory=lambda settings: BlockedRunner())
    assert blocked["ok"] is False and blocked["status"] == "delivery_unknown"


def test_unmounted_service_manifest_is_default_off_and_one_shot():
    docker = (ROOT / "Dockerfile.private-review-card").read_text()
    railway = json.loads((ROOT / "ops/content-ops/railway.private-card.json").read_text())
    assert "ARG RAILWAY_GIT_COMMIT_SHA" in docker
    assert "CONTENT_OPS_BUTTON_CARD_ENABLED=false" in docker
    assert "CONTENT_OPS_REVIEW_ENABLED=false" in docker
    assert "COPY core/publications/handoff.py" in docker
    assert "scripts.run_private_review_card_canary" in docker
    assert railway["build"]["dockerfilePath"] == "Dockerfile.private-review-card"
    assert railway["deploy"]["preDeployCommand"].endswith("--validate-only")
    assert railway["deploy"]["restartPolicyType"] == "NEVER"
    assert "cronSchedule" not in railway["deploy"]
    assert "Dockerfile.private-review-card" not in (
        ROOT / "ops/content-ops/railway.json").read_text()
