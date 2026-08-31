from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.publications.models import PublicationRunResult
from core.publications.settings import (
    PublicationSettings,
    publication_worker_token,
    telegram_publication_enabled,
)
from core.publications import settings as publication_settings


WORKER_TOKEN = "publication-worker-token-32-characters-minimum"


class FakeWorker:
    async def run_once(self):
        return PublicationRunResult(
            ok=True,
            claimed=True,
            status="published",
            publication_id="33333333-3333-4333-8333-333333333333",
        )


@pytest.fixture
def client(monkeypatch):
    from api import server

    monkeypatch.delenv("API_SECRET", raising=False)
    monkeypatch.delenv("STUDIO_ACCESS_TOKEN", raising=False)
    env = {
        "PUBLICATION_WORKER_TOKEN": WORKER_TOKEN,
        "TELEGRAM_PUBLICATION_ENABLED": "true",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "s" * 40,
        "CONTENT_STUDIO_WORKSPACE_ID": "11111111-1111-4111-8111-111111111111",
        "TELEGRAM_PUBLICATION_ALLOWED_CLIENTS": "squid",
        "RAILWAY_GIT_COMMIT_SHA": "d" * 40,
        "TELEGRAM_PUBLICATION_RELEASE_SHA": "d" * 40,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        server,
        "build_exact_telegram_publication_worker",
        lambda _settings: FakeWorker(),
    )
    return TestClient(server.app)


def test_worker_endpoint_accepts_only_dedicated_secret_and_empty_body(client):
    api_secret_only = client.post(
        "/internal/publications/telegram/run-once",
        headers={"X-API-Key": WORKER_TOKEN},
    )
    assert api_secret_only.status_code == 401
    assert api_secret_only.json()["detail"] == "invalid_publication_worker_key"

    with_body = client.post(
        "/internal/publications/telegram/run-once",
        headers={"X-Publication-Worker-Key": WORKER_TOKEN},
        json={},
    )
    assert with_body.status_code == 400
    assert with_body.json()["detail"] == "publication_worker_request_body_not_allowed"


def test_worker_endpoint_runs_one_server_selected_claim(client):
    response = client.post(
        "/internal/publications/telegram/run-once",
        headers={"X-Publication-Worker-Key": WORKER_TOKEN},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "claimed": True,
        "status": "published",
        "publication_id": "33333333-3333-4333-8333-333333333333",
    }


def test_worker_endpoint_is_fail_closed_when_feature_is_disabled(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_PUBLICATION_ENABLED", "false")
    response = client.post(
        "/internal/publications/telegram/run-once",
        headers={"X-Publication-Worker-Key": WORKER_TOKEN},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "telegram_publication_worker_disabled"


def test_worker_endpoint_release_mismatch_fails_before_worker(
    client,
    monkeypatch,
):
    from api import server

    def forbidden(*_args, **_kwargs):
        raise AssertionError("release mismatch constructed an I/O worker")

    monkeypatch.setattr(
        server,
        "build_exact_telegram_publication_worker",
        forbidden,
    )
    monkeypatch.setenv("TELEGRAM_PUBLICATION_RELEASE_SHA", "e" * 40)

    response = client.post(
        "/internal/publications/telegram/run-once",
        headers={"X-Publication-Worker-Key": WORKER_TOKEN},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "telegram_publication_worker_not_configured"


@pytest.mark.parametrize("value", ["1", "yes", "on", "TRUE", "true "])
def test_feature_flag_only_accepts_literal_true_or_false(value):
    with pytest.raises(ValueError, match="literal true or false"):
        telegram_publication_enabled({"TELEGRAM_PUBLICATION_ENABLED": value})


@pytest.mark.parametrize(
    "allowlist",
    ["squid, yellow", "yellow", "squid,yellow", "SQUID"],
)
def test_worker_token_and_non_squid_allowlist_fail_closed(allowlist):
    with pytest.raises(ValueError, match="invalid format"):
        publication_worker_token({"PUBLICATION_WORKER_TOKEN": " " + WORKER_TOKEN})
    with pytest.raises(ValueError, match="dedicated secret"):
        publication_worker_token({
            "PUBLICATION_WORKER_TOKEN": WORKER_TOKEN,
            "API_SECRET": WORKER_TOKEN,
        })

    env = {
        "TELEGRAM_PUBLICATION_ENABLED": "true",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "s" * 40,
        "CONTENT_STUDIO_WORKSPACE_ID": "11111111-1111-4111-8111-111111111111",
        "TELEGRAM_PUBLICATION_ALLOWED_CLIENTS": allowlist,
    }
    with pytest.raises(ValueError, match="ALLOWED_CLIENTS is invalid"):
        PublicationSettings.from_env(env)


@pytest.mark.parametrize(
    "secret_name",
    [
        "API_SECRET",
        "STUDIO_ACCESS_TOKEN",
        "SUPABASE_SERVICE_ROLE_KEY",
        "TELEGRAM_BOT_TOKEN_SQUID",
        "TELEGRAM_BOT_TOKEN_YELLOW",
        "TELEGRAM_BOT_TOKEN_ORIGINTRAIL",
        "TELEGRAM_BOT_TOKEN_BABYLON",
    ],
)
def test_worker_token_cannot_reuse_any_privileged_secret(secret_name):
    with pytest.raises(ValueError, match="dedicated secret"):
        publication_worker_token({
            "PUBLICATION_WORKER_TOKEN": WORKER_TOKEN,
            secret_name: WORKER_TOKEN,
        })


def test_worker_token_reuse_check_matches_effective_trimmed_credentials():
    with pytest.raises(ValueError, match="dedicated secret"):
        publication_worker_token({
            "PUBLICATION_WORKER_TOKEN": WORKER_TOKEN,
            "SUPABASE_SERVICE_ROLE_KEY": f"  {WORKER_TOKEN}  ",
        })


def test_worker_token_rejects_unicode_and_tolerates_unicode_forbidden_secrets():
    with pytest.raises(ValueError, match="invalid format"):
        publication_worker_token({
            "PUBLICATION_WORKER_TOKEN": "é" * 32,
        })

    assert publication_worker_token({
        "PUBLICATION_WORKER_TOKEN": WORKER_TOKEN,
        "API_SECRET": "별도의-한글-시크릿",
    }) == WORKER_TOKEN


def test_worker_token_compares_every_configured_secret_in_constant_time(monkeypatch):
    compared: list[tuple[bytes, bytes]] = []

    def compare_digest(left: bytes, right: bytes) -> bool:
        compared.append((left, right))
        return left == right

    monkeypatch.setattr(publication_settings.secrets, "compare_digest", compare_digest)
    env = {
        "PUBLICATION_WORKER_TOKEN": WORKER_TOKEN,
        "API_SECRET": WORKER_TOKEN,
        "STUDIO_ACCESS_TOKEN": "studio-access-token-not-the-worker-value",
        "SUPABASE_SERVICE_ROLE_KEY": "supabase-service-role-not-worker-value",
        "TELEGRAM_BOT_TOKEN_SQUID": "squid-bot-token-not-worker-value",
        "TELEGRAM_BOT_TOKEN_YELLOW": "yellow-bot-token-not-worker-value",
        "TELEGRAM_BOT_TOKEN_ORIGINTRAIL": "otrac-bot-token-not-worker-value",
        "TELEGRAM_BOT_TOKEN_BABYLON": "babylon-bot-token-not-worker-value",
    }

    with pytest.raises(ValueError, match="dedicated secret"):
        publication_worker_token(env)

    assert len(compared) == 7
    assert all(left == WORKER_TOKEN.encode("ascii") for left, _right in compared)


def test_publication_deployment_defaults_are_disabled_and_separate():
    root = Path(__file__).resolve().parents[1]
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile.publication").read_text(encoding="utf-8")
    ci_workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "STUDIO_TELEGRAM_PUBLISH_ENABLED=false" in env_example
    assert "TELEGRAM_PUBLICATION_ENABLED=false" in env_example
    assert "PUBLICATION_WORKER_TOKEN=" in env_example
    assert "ENV TELEGRAM_PUBLICATION_ENABLED=false" in dockerfile
    assert "Dockerfile.publication" in ci_workflow
    assert "scripts.run_telegram_publications --validate-only" in ci_workflow
    assert '"provider_calls": False' in ci_workflow
    assert '"database_calls": False' in ci_workflow
    assert ci_workflow.count("--network none") >= 2
