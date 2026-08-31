from __future__ import annotations

import json

import httpx
import pytest

from core.publications.repository import (
    PublicationRepositoryError,
    SupabasePublicationRecoveryRepository,
)
from core.publications.settings import PublicationRecoverySettings
from scripts import run_telegram_publications as runner


WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
SERVICE_KEY = "s" * 40


def _env(*, enabled: str = "false") -> dict[str, str]:
    return {
        "TELEGRAM_PUBLICATION_ENABLED": enabled,
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
        "CONTENT_STUDIO_WORKSPACE_ID": WORKSPACE_ID,
        "TELEGRAM_PUBLICATION_RECOVERY_LIMIT": "25",
        "RAILWAY_GIT_COMMIT_SHA": "d" * 40,
        "TELEGRAM_PUBLICATION_RELEASE_SHA": "d" * 40,
    }


def _repository(handler) -> SupabasePublicationRecoveryRepository:
    return SupabasePublicationRecoveryRepository(
        supabase_url="https://project.supabase.co",
        service_role_key=SERVICE_KEY,
        workspace_id=WORKSPACE_ID,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_recovery_calls_only_the_exact_rpc_once_and_exposes_counts(monkeypatch):
    calls: list[httpx.Request] = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery attempted to construct a claim worker")

    monkeypatch.setattr(runner, "_build_worker", forbidden)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={
            "workspace_id": WORKSPACE_ID,
            "reconciled_count": 3,
            "retrying_count": 1,
            "failed_count": 1,
            "delivery_unknown_count": 1,
        })

    settings = PublicationRecoverySettings.from_env(_env())
    result = await runner._recover(
        settings,
        transport=httpx.MockTransport(handler),
    )

    assert result == {
        "ok": True,
        "mode": "recovery_only",
        "status": "reconciled",
        "provider_calls": False,
        "reconciled_count": 3,
        "retrying_count": 1,
        "failed_count": 1,
        "delivery_unknown_count": 1,
    }
    assert len(calls) == 1
    request = calls[0]
    assert request.url.path.endswith(
        "/rest/v1/rpc/reconcile_expired_exact_telegram_publication_leases"
    )
    assert json.loads(request.content) == {
        "target_workspace_id": WORKSPACE_ID,
        "target_limit": 25,
    }
    assert request.headers["authorization"] == f"Bearer {SERVICE_KEY}"
    assert "telegram" not in request.url.host
    assert not hasattr(_repository(handler), "claim")


@pytest.mark.asyncio
async def test_empty_recovery_reports_idle_without_row_payloads():
    repository = _repository(lambda _request: httpx.Response(200, json={
        "workspace_id": WORKSPACE_ID,
        "reconciled_count": 0,
        "retrying_count": 0,
        "failed_count": 0,
        "delivery_unknown_count": 0,
    }))
    summary = await repository.reconcile_expired_leases(limit=100)
    assert summary.reconciled_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {
            "workspace_id": WORKSPACE_ID,
            "reconciled_count": 1,
            "retrying_count": 0,
            "failed_count": 0,
            "delivery_unknown_count": 0,
        },
        {
            "workspace_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
            "reconciled_count": 0,
            "retrying_count": 0,
            "failed_count": 0,
            "delivery_unknown_count": 0,
        },
        {
            "workspace_id": WORKSPACE_ID,
            "reconciled_count": True,
            "retrying_count": 1,
            "failed_count": 0,
            "delivery_unknown_count": 0,
        },
        {
            "workspace_id": WORKSPACE_ID,
            "reconciled_count": 0,
            "retrying_count": 0,
            "failed_count": 0,
            "delivery_unknown_count": 0,
            "rows": [],
        },
    ],
)
async def test_recovery_response_parser_rejects_inconsistent_or_extra_data(response):
    repository = _repository(
        lambda _request: httpx.Response(200, json=response)
    )
    with pytest.raises(PublicationRepositoryError):
        await repository.reconcile_expired_leases(limit=100)


@pytest.mark.asyncio
async def test_recovery_timeout_is_safe_and_never_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("must not leak headers", request=request)

    repository = _repository(handler)
    with pytest.raises(PublicationRepositoryError) as error:
        await repository.reconcile_expired_leases(limit=100)

    assert error.value.code == "publication_database_unavailable"
    assert error.value.retryable is True
    assert SERVICE_KEY not in str(error.value)
    assert calls == 1


def test_recovery_cli_works_while_disabled_without_telegram_config(
    monkeypatch,
    capsys,
):
    for key, value in _env(enabled="false").items():
        monkeypatch.setenv(key, value)
    for key in (
        "TELEGRAM_BOT_TOKEN_SQUID",
        "TELEGRAM_CHANNEL_SQUID",
        "TELEGRAM_PUBLICATION_ALLOWED_CLIENTS",
    ):
        monkeypatch.delenv(key, raising=False)

    async def recover(settings):
        assert settings.workspace_id == WORKSPACE_ID
        return {
            "ok": True,
            "mode": "recovery_only",
            "status": "idle",
            "provider_calls": False,
            "reconciled_count": 0,
            "retrying_count": 0,
            "failed_count": 0,
            "delivery_unknown_count": 0,
        }

    def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery constructed a publisher or claim worker")

    monkeypatch.setattr(runner, "_recover", recover)
    monkeypatch.setattr(runner, "_build_worker", forbidden)
    monkeypatch.setattr(runner, "_validate_only", forbidden)

    assert runner.main(["--recovery-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "recovery_only"
    assert result["provider_calls"] is False
    assert set(result) == {
        "ok",
        "mode",
        "status",
        "provider_calls",
        "reconciled_count",
        "retrying_count",
        "failed_count",
        "delivery_unknown_count",
    }


@pytest.mark.parametrize("enabled", ["true", "1", "FALSE", "false ", None])
def test_recovery_requires_the_literal_disabled_flag(enabled):
    env = _env()
    if enabled is None:
        del env["TELEGRAM_PUBLICATION_ENABLED"]
    else:
        env["TELEGRAM_PUBLICATION_ENABLED"] = enabled
    with pytest.raises(ValueError, match="literal false"):
        PublicationRecoverySettings.from_env(env)


def test_enabled_recovery_cli_fails_before_repository_construction(
    monkeypatch,
    capsys,
):
    for key, value in _env(enabled="true").items():
        monkeypatch.setenv(key, value)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("enabled recovery constructed a repository")

    monkeypatch.setattr(runner, "_build_recovery_repository", forbidden)
    assert runner.main(["--recovery-only"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    assert result["provider_calls"] is False


def test_recovery_release_mismatch_fails_before_repository_construction(
    monkeypatch,
    capsys,
):
    env = _env(enabled="false")
    env["TELEGRAM_PUBLICATION_RELEASE_SHA"] = "e" * 40
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("release mismatch constructed a recovery repository")

    monkeypatch.setattr(runner, "_build_recovery_repository", forbidden)
    assert runner.main(["--recovery-only"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "mode": "recovery_only",
        "status": "failed",
        "error": "telegram_publication_recovery_failed",
        "provider_calls": False,
    }


def test_recovery_cli_failure_exposes_no_secret_or_row_payload(monkeypatch, capsys):
    for key, value in _env(enabled="false").items():
        monkeypatch.setenv(key, value)

    async def fail(_settings):
        raise PublicationRepositoryError(
            "publication_database_unavailable",
            retryable=True,
        )

    monkeypatch.setattr(runner, "_recover", fail)
    assert runner.main(["--recovery-only"]) == 1
    raw = capsys.readouterr().out
    assert json.loads(raw) == {
        "ok": False,
        "mode": "recovery_only",
        "status": "failed",
        "error": "telegram_publication_recovery_failed",
        "provider_calls": False,
    }
    assert SERVICE_KEY not in raw
    assert WORKSPACE_ID not in raw


@pytest.mark.parametrize("limit", ["0", "101", "not-an-integer"])
def test_recovery_limit_is_bounded(limit):
    env = _env()
    env["TELEGRAM_PUBLICATION_RECOVERY_LIMIT"] = limit
    with pytest.raises(ValueError):
        PublicationRecoverySettings.from_env(env)
