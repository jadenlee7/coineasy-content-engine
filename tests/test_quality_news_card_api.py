"""Mock quality routing with private temporary SQLite; no external I/O."""
from copy import deepcopy
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

REQUEST_ID = "12345678-1234-4234-8234-123456789abc"
ADMIN = "quality-synthetic-admin-key"
CLIENT = "quality-synthetic-client-key"
PATH = "/clients/yellow/generate/news-card"
BODY = {
    "source_content": "Official source fixture for a Korean review card.",
    "source_type": "tweet", "source_url": "https://x.com/Yellow/status/123456789",
    "source_image_url": "https://pbs.twimg.com/media/fixture.png",
    "template_style": "remix", "mock_mode": False,
    "quality_context": {
        "job_id": "23456789-1234-4234-8234-123456789abc",
        "request_id": REQUEST_ID,
        "workspace_id": "34567890-1234-4234-8234-123456789abc",
        "client_id": "yellow", "source_item_id": "45678901-1234-4234-8234-123456789abc",
        "source_published_at": "2026-10-12T01:00:00Z", "source_sha256": "a" * 64,
        "source_content": "Official source fixture for a Korean review card.",
        "source_url": "https://x.com/Yellow/status/123456789",
        "source_image_url": "https://pbs.twimg.com/media/fixture.png",
    },
}


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from api import security, server
    from core.automation import quality_news_card
    monkeypatch.setattr(security, "API_KEYS", {"admin": ADMIN, "yellow": CLIENT})
    monkeypatch.setattr(server, "OUTPUT_ROOT", (tmp_path / "output").resolve())
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_ENABLED", "true")
    monkeypatch.setenv("QUALITY_BANNER_ENABLED", "true")
    private = (tmp_path / "private").resolve()
    private.mkdir(mode=0o700)
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT", str(private))
    journal_type = server.NewsGenerationJournal
    journal_type(private, output_root=server.OUTPUT_ROOT, initialize=True)
    calls, factories, journals = [], [], []

    def journal(*args, **kwargs):
        journals.append(True)
        return journal_type(*args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy generation, journal, retention or send forbidden")
    async def async_forbidden(*args, **kwargs):
        raise AssertionError("legacy generation or send forbidden")
    monkeypatch.setattr(server, "NewsGenerationJournal", journal)
    monkeypatch.setattr(server, "mark_run_active", forbidden)
    monkeypatch.setattr(server, "_finish_output_run", forbidden)
    monkeypatch.setattr(server, "generate_news_card", async_forbidden)
    original_guarded = server._guarded_generate_news
    monkeypatch.setattr(server, "_guarded_generate_news", async_forbidden)
    monkeypatch.setattr(server, "send_telegram_review", async_forbidden)
    monkeypatch.setattr(server, "send_telegram_grok_qa_verdict", async_forbidden)
    monkeypatch.setattr(server, "build_exact_telegram_publication_worker", forbidden)

    result = dict(client_id="yellow", content_type="news_card", spec={"headline": "검수용 제목"},
        png_path=str(server.OUTPUT_ROOT / "yellow" / "quality_fixture" / "card.png"),
        manifest_path=str(server.OUTPUT_ROOT / "yellow" / "quality_fixture" / "manifest.json"),
        template_style="remix", requested_template_style="remix", source_image_used=True,
        source_image_url=BODY["source_image_url"], source_image_sha256="b" * 64,
        source_visual_path=None, figma_template=None, duration_ms=1)

    async def generate(**kwargs):
        calls.append(kwargs)
        return deepcopy(result)
    service = SimpleNamespace(generate=generate)
    def factory(env):
        # Never retain or print a copy of the real process environment.
        factories.append(True)
        return service
    monkeypatch.setattr(quality_news_card, "service_from_env", factory)
    return SimpleNamespace(server=server, module=quality_news_card,
        client=TestClient(server.app), calls=calls, factories=factories,
        result=result, service=service, journals=journals, private=private,
        original_guarded=original_guarded)


def post(harness, *, key=ADMIN, request_id=REQUEST_ID, body=None):
    headers = {"x-api-key": key}
    if request_id is not None:
        headers["Idempotency-Key"] = request_id
    return harness.client.post(PATH, headers=headers, json=body if body is not None else deepcopy(BODY))


def test_quality_receives_exact_header_identity_and_full_model_payload(harness):
    response = post(harness)
    assert response.status_code == 200
    assert response.json() == harness.result
    assert len(harness.calls) == len(harness.factories) == 1
    call = harness.calls[0]
    assert call["request_id"] == REQUEST_ID
    assert call["client_id"] == "yellow" and call["output_root"] == harness.server.OUTPUT_ROOT
    expected = harness.server.NewsCardRequest(**deepcopy(BODY)).model_dump(mode="json")
    assert call["payload"] == expected


def test_quality_disabled_with_context_never_falls_back(harness, monkeypatch):
    monkeypatch.setenv("QUALITY_BANNER_ENABLED", "false")
    response = post(harness)
    assert response.status_code == 503 and response.json()["detail"] == "quality_news_disabled"
    assert not harness.calls and not harness.factories and not harness.journals


def test_enabled_quality_without_service_is_configuration_error(harness, monkeypatch):
    monkeypatch.setattr(harness.module, "service_from_env", lambda env: None)
    response = post(harness)
    assert response.status_code == 503
    assert response.json()["detail"] == "quality_news_configuration_invalid"
    assert not harness.calls


@pytest.mark.parametrize("key,request_id,status", [
    (CLIENT, REQUEST_ID, 403), ("not-an-api-key", REQUEST_ID, 401),
    (ADMIN, None, 400), (ADMIN, "bad", 400), (ADMIN, REQUEST_ID.upper(), 400),
])
def test_auth_and_header_reject_before_quality_factory(harness, key, request_id, status):
    response = post(harness, key=key, request_id=request_id)
    assert response.status_code == status
    assert not harness.calls and not harness.factories and not harness.journals


def test_duplicate_header_rejects_before_quality_factory(harness):
    response = harness.client.post(PATH, json=deepcopy(BODY), headers=[("x-api-key", ADMIN),
        ("Idempotency-Key", REQUEST_ID), ("Idempotency-Key", REQUEST_ID)])
    assert response.status_code == 400 and not harness.calls and not harness.factories and not harness.journals


def test_quality_context_requires_idempotency_enablement_without_fallback(harness, monkeypatch):
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_ENABLED", "false")
    response = post(harness, request_id=None)
    assert response.status_code == 403
    assert response.json()["detail"] == "quality_news_admin_idempotency_required"
    assert not harness.calls and not harness.factories and not harness.journals


@pytest.mark.parametrize("code,status", [
    ("quality_banner_pending", 503), ("quality_banner_collect_unknown", 503),
    ("quality_news_configuration_invalid", 503), ("quality_news_store_unavailable", 503),
    ("quality_news_budget_evidence_required", 503), ("quality_news_daily_budget_limit", 409),
    ("quality_banner_submit_unknown", 409), ("quality_news_copy_unknown", 409),
])
def test_known_pending_and_unknown_outcomes_are_distinct_safe_codes(harness, code, status):
    from core.automation.higgsfield_banner import QualityBannerError
    async def fail(**kwargs):
        raise QualityBannerError(code)
    harness.service.generate = fail
    response = post(harness)
    assert response.status_code == status and response.json()["detail"] == code


@pytest.mark.parametrize("kind", ["runtime", "quality_error", "http_error", "invalid_result"])
def test_service_failure_never_echoes_provider_text(harness, capsys, kind):
    from core.automation.higgsfield_banner import QualityBannerError
    secret = "provider-body-and-secret-must-not-leak"
    async def fail(**kwargs):
        if kind == "runtime":
            raise RuntimeError(secret)
        if kind == "quality_error":
            raise QualityBannerError(secret)
        if kind == "http_error":
            raise HTTPException(418, secret)
        return {"spec": {"body": secret}}
    harness.service.generate = fail
    response = post(harness)
    assert response.status_code == 409
    assert response.json()["detail"] == "quality_news_outcome_unknown"
    captured = capsys.readouterr()
    assert secret not in response.text + captured.out + captured.err


def test_factory_failure_is_sanitized_and_never_generates(harness, monkeypatch):
    from core.automation.higgsfield_banner import QualityBannerError
    def fail(env):
        raise QualityBannerError("untrusted-factory-provider-body")
    monkeypatch.setattr(harness.module, "service_from_env", fail)
    response = post(harness)
    assert response.status_code == 409
    assert response.json()["detail"] == "quality_news_outcome_unknown"
    assert not harness.calls


@pytest.mark.parametrize("code", ["quality_banner_pending", "quality_banner_submit_unknown"])
@pytest.mark.parametrize("disable_stable", [False, True])
def test_quality_outcome_cannot_switch_to_legacy_when_context_or_flags_change(
    harness, monkeypatch, code, disable_stable
):
    from core.automation.higgsfield_banner import QualityBannerError
    async def quality_outcome(**kwargs):
        harness.calls.append(kwargs)
        raise QualityBannerError(code)
    harness.service.generate = quality_outcome
    first = post(harness)
    assert first.status_code == (503 if code == "quality_banner_pending" else 409)
    assert len(harness.calls) == 1
    original_journal_count = len(harness.journals)
    monkeypatch.setenv("QUALITY_BANNER_ENABLED", "false")
    monkeypatch.setattr(harness.server, "_guarded_generate_news", harness.original_guarded)
    if disable_stable:
        monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_ENABLED", "false")
    legacy = deepcopy(BODY)
    legacy.pop("quality_context")
    response = post(harness, body=legacy)
    assert response.status_code == (503 if disable_stable else 409)
    assert response.json()["detail"] == (
        "news_generation_idempotency_disabled" if disable_stable else "news_generation_mode_conflict")
    assert len(harness.calls) == len(harness.factories) == 1
    if disable_stable:
        assert len(harness.journals) == original_journal_count
    with sqlite3.connect(harness.private / "news-card-requests.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM news_generation_requests").fetchone()[0] == 0
        assert db.execute("SELECT mode FROM news_generation_modes").fetchall() == [("quality",)]


def test_same_mode_pending_resume_reaches_quality_owner_without_generic_started(harness):
    from core.automation.higgsfield_banner import QualityBannerError
    async def pending(**kwargs):
        harness.calls.append(kwargs)
        raise QualityBannerError("quality_banner_pending")
    harness.service.generate = pending
    assert post(harness).status_code == 503
    assert post(harness).status_code == 503
    assert len(harness.calls) == 2
    assert harness.calls[0] == harness.calls[1]
    with sqlite3.connect(harness.private / "news-card-requests.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM news_generation_requests").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM news_generation_modes").fetchone()[0] == 1


def test_quality_payload_mutation_blocked_before_factory(harness):
    assert post(harness).status_code == 200
    changed = deepcopy(BODY)
    changed["source_content"] = "changed fixture"
    response = post(harness, body=changed)
    assert response.status_code == 409
    assert response.json()["detail"] == "news_generation_request_conflict"
    assert len(harness.factories) == len(harness.calls) == 1


def test_quality_missing_shared_journal_does_not_reinitialize_or_call_factory(harness):
    path = harness.private / "news-card-requests.sqlite3"
    path.unlink()
    response = post(harness)
    assert response.status_code == 503
    assert response.json()["detail"] == "news_generation_journal_unavailable"
    assert not path.exists() and not harness.factories and not harness.calls


def test_quality_journal_failure_is_sanitized_before_factory(harness, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("private-journal-details-must-not-leak")
    monkeypatch.setattr(harness.server, "NewsGenerationJournal", fail)
    response = post(harness)
    assert response.status_code == 503
    assert response.json()["detail"] == "news_generation_outcome_unknown"
    captured = capsys.readouterr()
    assert "private-journal-details-must-not-leak" not in response.text + captured.out + captured.err
    assert not harness.factories and not harness.calls
