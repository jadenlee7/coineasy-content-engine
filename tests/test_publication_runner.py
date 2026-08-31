from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.publications.settings import PublicationSettings
from scripts import run_telegram_publications as runner


ROOT = Path(__file__).resolve().parents[1]
BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijk12345"


def _env(*, enabled: str = "false") -> dict[str, str]:
    return {
        "TELEGRAM_PUBLICATION_ENABLED": enabled,
        "TELEGRAM_PUBLICATION_ALLOWED_CLIENTS": "squid",
        "TELEGRAM_PUBLICATION_LEASE_SECONDS": "180",
        "TELEGRAM_PUBLICATION_MAX_CLAIMS": "1",
        "SUPABASE_URL": "https://ci.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "s" * 40,
        "CONTENT_STUDIO_WORKSPACE_ID": "11111111-1111-4111-8111-111111111111",
        "TELEGRAM_BOT_TOKEN_SQUID": BOT_TOKEN,
        "TELEGRAM_CHANNEL_SQUID": "@squid_kor_update",
        "RAILWAY_GIT_COMMIT_SHA": "d" * 40,
        "TELEGRAM_PUBLICATION_RELEASE_SHA": "d" * 40,
    }


def _install_env(monkeypatch, env: dict[str, str]) -> None:
    monkeypatch.delenv("API_SECRET", raising=False)
    monkeypatch.delenv("STUDIO_ACCESS_TOKEN", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_validate_only_with_disabled_flag_has_structurally_zero_calls(
    monkeypatch,
    capsys,
):
    _install_env(monkeypatch, _env(enabled="false"))
    monkeypatch.chdir(ROOT)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("validate-only constructed an I/O worker")

    monkeypatch.setattr(runner, "_build_worker", forbidden)
    monkeypatch.setattr(runner.asyncio, "run", forbidden)

    assert runner.main(["--validate-only"]) == 0
    raw = capsys.readouterr().out.strip()
    result = json.loads(raw)
    assert result == {
        "ok": True,
        "mode": "validate_only",
        "enabled": False,
        "client_id": "squid",
        "public_username": "squid_kor_update",
        "runtime_release_verified": True,
        "provider_calls": False,
        "database_calls": False,
    }
    assert BOT_TOKEN not in raw
    assert "s" * 40 not in raw


@pytest.mark.parametrize(
    "invalid_env",
    [
        {"TELEGRAM_PUBLICATION_ENABLED": "1"},
        {"TELEGRAM_PUBLICATION_LEASE_SECONDS": "179"},
        {"TELEGRAM_PUBLICATION_ALLOWED_CLIENTS": "yellow"},
        {"SUPABASE_URL": ""},
        {"SUPABASE_SERVICE_ROLE_KEY": ""},
        {"CONTENT_STUDIO_WORKSPACE_ID": "invalid"},
        {"TELEGRAM_CHANNEL_SQUID": "@attacker_channel"},
        {"TELEGRAM_BOT_TOKEN_SQUID": "invalid"},
        {"RAILWAY_GIT_COMMIT_SHA": "D" * 40},
        {"RAILWAY_GIT_COMMIT_SHA": " " + ("d" * 40)},
        {"TELEGRAM_PUBLICATION_RELEASE_SHA": "d" * 39},
        {"TELEGRAM_PUBLICATION_RELEASE_SHA": ("d" * 40) + " "},
        {"TELEGRAM_PUBLICATION_RELEASE_SHA": "e" * 40},
    ],
)
def test_validate_only_fails_closed_with_explicit_no_call_json(
    monkeypatch,
    capsys,
    invalid_env,
):
    env = _env()
    env.update(invalid_env)
    _install_env(monkeypatch, env)
    monkeypatch.chdir(ROOT)

    assert runner.main(["--validate-only"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "ok": False,
        "mode": "validate_only",
        "error": "telegram_publication_validation_failed",
        "provider_calls": False,
        "database_calls": False,
    }


@pytest.mark.parametrize("missing", runner._VALIDATE_REQUIRED_ENV)
def test_validate_only_requires_every_cron_deployment_input(
    monkeypatch,
    capsys,
    missing,
):
    env = _env()
    del env[missing]
    monkeypatch.delenv(missing, raising=False)
    _install_env(monkeypatch, env)
    monkeypatch.chdir(ROOT)

    assert runner.main(["--validate-only"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "validate_only"
    assert result["provider_calls"] is False
    assert result["database_calls"] is False


@pytest.mark.parametrize("lease", ["180", "600"])
def test_publication_settings_accept_only_operational_lease_range(lease):
    env = _env(enabled="false")
    env["TELEGRAM_PUBLICATION_LEASE_SECONDS"] = lease
    settings = PublicationSettings.from_env_for_validation(
        env,
        clients_dir=ROOT / "clients",
    )
    assert settings.lease_seconds == int(lease)


def test_publication_settings_reject_short_lease_even_for_validation():
    env = _env(enabled="false")
    env["TELEGRAM_PUBLICATION_LEASE_SECONDS"] = "179"
    with pytest.raises(ValueError, match="between 180 and 600"):
        PublicationSettings.from_env_for_validation(
            env,
            clients_dir=ROOT / "clients",
        )


def test_normal_settings_loader_still_requires_the_execution_flag():
    with pytest.raises(ValueError, match="must be true"):
        PublicationSettings.from_env(
            _env(enabled="false"),
            clients_dir=ROOT / "clients",
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RAILWAY_GIT_COMMIT_SHA", None),
        ("TELEGRAM_PUBLICATION_RELEASE_SHA", None),
        ("RAILWAY_GIT_COMMIT_SHA", "D" * 40),
        ("RAILWAY_GIT_COMMIT_SHA", " " + ("d" * 40)),
        ("TELEGRAM_PUBLICATION_RELEASE_SHA", "d" * 39),
        ("TELEGRAM_PUBLICATION_RELEASE_SHA", ("d" * 40) + " "),
        ("TELEGRAM_PUBLICATION_RELEASE_SHA", "e" * 40),
    ],
)
def test_release_fence_is_exact_and_required(name, value):
    env = _env(enabled="false")
    if value is None:
        del env[name]
    else:
        env[name] = value
    with pytest.raises(ValueError, match="release SHA fence"):
        PublicationSettings.from_env_for_validation(
            env,
            clients_dir=ROOT / "clients",
        )


def test_live_release_mismatch_fails_before_worker_with_no_io(
    monkeypatch,
    capsys,
):
    env = _env(enabled="true")
    env["TELEGRAM_PUBLICATION_RELEASE_SHA"] = "e" * 40
    _install_env(monkeypatch, env)
    monkeypatch.chdir(ROOT)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("release mismatch constructed an I/O worker")

    monkeypatch.setattr(runner, "_build_worker", forbidden)
    monkeypatch.setattr(runner.asyncio, "run", forbidden)

    assert runner.main([]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "enabled": True,
        "error": "telegram_publication_configuration_invalid",
        "provider_calls": False,
        "database_calls": False,
    }
