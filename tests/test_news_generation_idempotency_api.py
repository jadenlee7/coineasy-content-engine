"""Mock generator only: API retries must never cause another paid attempt."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

REQUEST_ID = "12345678-1234-4234-8234-123456789abc"
ADMIN = "synthetic-admin-key-long"
CLIENT = "synthetic-yellow-key-long"
PATH = "/clients/yellow/generate/news-card"


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from api import security, server
    private = tmp_path / "private"; private.mkdir(mode=0o700)
    monkeypatch.setattr(security, "API_KEYS", {"admin": ADMIN, "yellow": CLIENT})
    monkeypatch.setattr(server, "OUTPUT_ROOT", (tmp_path / "output").resolve())
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_ENABLED", "true")
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT", str(private.resolve()))
    server.NewsGenerationJournal(private.resolve(), output_root=server.OUTPUT_ROOT, initialize=True)
    calls = []

    async def generate(*, output_dir, **kwargs):
        calls.append((output_dir, kwargs))
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "card.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        (output_dir / "manifest.json").write_text("{}")
        return SimpleNamespace(client_id="yellow", content_type="news_card", spec={"title": "검수"},
            png_path=str(output_dir / "card.png"), manifest_path=str(output_dir / "manifest.json"),
            template_style="classic", requested_template_style="classic", source_image_used=False,
            source_image_url="", source_image_sha256="", source_visual_path=None,
            figma_template=None, duration_ms=1)
    monkeypatch.setattr(server, "generate_news_card", generate)
    return server, TestClient(server.app), calls, generate


def post(client, *, key=ADMIN, request_id=REQUEST_ID, body=None):
    headers = {"x-api-key": key}
    if request_id is not None:
        headers["Idempotency-Key"] = request_id
    return client.post(PATH, headers=headers, json=body or {"source_content": "official fixture"})


def test_success_replay_without_generation_and_mutation_conflict(harness):
    server, client, calls, _ = harness
    first = post(client); replay = post(TestClient(server.app))
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() and len(calls) == 1
    assert calls[0][0].name == "news_request_" + REQUEST_ID
    changed = post(client, body={"source_content": "changed"})
    assert changed.status_code == 409 and len(calls) == 1
    assert changed.json()["detail"] == "news_generation_request_conflict"


def test_missing_cached_png_never_regenerates(harness):
    _, client, calls, _ = harness
    first = post(client); Path(first.json()["png_path"]).unlink()
    replay = post(client)
    assert replay.status_code == 409 and len(calls) == 1
    assert replay.json()["detail"] == "news_generation_cached_assets_unavailable"


@pytest.mark.parametrize("failure", [RuntimeError("private-provider-secret"), HTTPException(418, "private-provider-secret")])
def test_provider_error_stays_terminal_and_never_leaks(harness, monkeypatch, capsys, failure):
    server, client, calls, _ = harness
    async def fail(**kwargs):
        calls.append(kwargs)
        raise failure
    monkeypatch.setattr(server, "generate_news_card", fail)
    first = post(client); again = post(client)
    assert first.status_code == 503 and again.status_code == 409
    assert first.json()["detail"] == again.json()["detail"] == "news_generation_outcome_unknown"
    assert len(calls) == 1
    assert "private-provider-secret" not in first.text + again.text + capsys.readouterr().err


def test_concurrent_api_request_is_held_before_second_generator(harness, monkeypatch):
    server, client, calls, original = harness
    started, release = threading.Event(), threading.Event()
    async def pending(**kwargs):
        started.set()
        for _ in range(2000):
            if release.is_set():
                return await original(**kwargs)
            await asyncio.sleep(0.001)
        raise AssertionError("test fixture deadline")
    monkeypatch.setattr(server, "generate_news_card", pending)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post, TestClient(server.app))
        assert started.wait(2)
        try:
            second = post(client)
            assert second.status_code == 409
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize("key,request_id,status", [
    (CLIENT, REQUEST_ID, 403), ("unknown", REQUEST_ID, 401),
    (ADMIN, None, 400), (ADMIN, REQUEST_ID.upper(), 400), (ADMIN, "bad", 400),
])
def test_auth_and_canonical_header_precede_journal(harness, monkeypatch, key, request_id, status):
    server, client, calls, _ = harness
    def forbidden(*args, **kwargs):
        raise AssertionError("journal must not be constructed")
    monkeypatch.setattr(server, "NewsGenerationJournal", forbidden)
    assert post(client, key=key, request_id=request_id).status_code == status
    assert not calls


def test_duplicate_header_refused(harness):
    _, client, calls, _ = harness
    response = client.post(PATH, headers=[("x-api-key", ADMIN),
        ("Idempotency-Key", REQUEST_ID), ("Idempotency-Key", REQUEST_ID)],
        json={"source_content": "fixture"})
    assert response.status_code == 400 and not calls


def test_cancelled_generation_stays_held(harness):
    server, _, calls, _ = harness
    req = server.NewsCardRequest(source_content="cancellation fixture")
    async def cancel(output_dir):
        calls.append(output_dir)
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(server._guarded_generate_news("yellow", req, REQUEST_ID, cancel))
    with pytest.raises(HTTPException) as held:
        asyncio.run(server._guarded_generate_news("yellow", req, REQUEST_ID, cancel))
    assert held.value.status_code == 409 and held.value.detail == "news_generation_outcome_unknown"
    assert len(calls) == 1


def test_public_output_overlap_refused_before_journal_file(harness, monkeypatch):
    server, client, calls, _ = harness
    public = server.OUTPUT_ROOT / "yellow" / "private"
    public.mkdir(mode=0o700, parents=True)
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT", str(public))
    response = post(client)
    assert response.status_code == 503
    assert response.json()["detail"] == "news_generation_output_root_invalid"
    assert not (public / "news-card-requests.sqlite3").exists() and not calls


def test_runtime_missing_journal_never_recreates_or_generates(harness, monkeypatch):
    server, client, calls, _ = harness
    missing = server.OUTPUT_ROOT.parent / "missing-private"
    missing.mkdir(mode=0o700)
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT", str(missing))
    response = post(client)
    assert response.status_code == 503
    assert response.json()["detail"] == "news_generation_journal_unavailable"
    assert not (missing / "news-card-requests.sqlite3").exists() and not calls


def test_default_off_ignores_journal_config_and_preserves_headerless_legacy(harness, monkeypatch):
    server, client, calls, _ = harness
    monkeypatch.delenv("NEWS_CARD_IDEMPOTENCY_ENABLED")
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT", "invalid")
    def forbidden(*args, **kwargs):
        raise AssertionError("journal must not be constructed")
    monkeypatch.setattr(server, "NewsGenerationJournal", forbidden)
    assert post(client, key=CLIENT, request_id=None).status_code == 200
    assert post(client, key=CLIENT, request_id=None).status_code == 200
    assert len(calls) == 2 and calls[0][0] != calls[1][0]
    assert all(path.name.startswith("news_") and not path.name.startswith("news_request_") for path, _ in calls)


@pytest.mark.parametrize("request_id", [REQUEST_ID, "not-a-uuid", ""])
def test_idempotency_off_rejects_any_header_without_journal_or_generation(harness, monkeypatch, request_id):
    server, client, calls, _ = harness
    monkeypatch.setenv("NEWS_CARD_IDEMPOTENCY_ENABLED", "false")
    def forbidden(*args, **kwargs):
        raise AssertionError("OFF must not construct journal")
    monkeypatch.setattr(server, "NewsGenerationJournal", forbidden)
    response = post(client, request_id=request_id)
    assert response.status_code == 503
    assert response.json()["detail"] == "news_generation_idempotency_disabled"
    assert not calls


@pytest.mark.parametrize("legacy_outcome", ["succeeded", "unknown"])
def test_legacy_request_cannot_switch_to_quality(harness, monkeypatch, legacy_outcome):
    from core.automation import quality_news_card
    server, client, calls, _ = harness
    factories = []
    def forbidden_factory(env):
        factories.append(True)
        raise AssertionError("quality factory must not be reached")
    monkeypatch.setattr(quality_news_card, "service_from_env", forbidden_factory)
    monkeypatch.setenv("QUALITY_BANNER_ENABLED", "true")
    if legacy_outcome == "unknown":
        async def unknown(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("provider outcome unknown")
        monkeypatch.setattr(server, "generate_news_card", unknown)
    first = post(client)
    assert first.status_code == (200 if legacy_outcome == "succeeded" else 503)
    response = post(client, body={"source_content": "official fixture", "quality_context": {"request_id": REQUEST_ID}})
    assert response.status_code == 409
    assert response.json()["detail"] == "news_generation_mode_conflict"
    assert len(calls) == 1 and not factories
