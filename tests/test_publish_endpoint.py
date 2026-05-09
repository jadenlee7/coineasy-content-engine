import pytest
from fastapi.testclient import TestClient

ADMIN_KEY = "test_admin_key_xyz"


@pytest.fixture(autouse=True)
def patch_keys(monkeypatch):
    from api import security
    monkeypatch.setattr(security, "API_KEYS", {
        "admin": ADMIN_KEY,
        "yellow": None,
        "squid": None,
    })
    yield


@pytest.fixture
def client():
    from api.server import app
    return TestClient(app)


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Replace _run_daily_news_generation with a controllable stub."""
    state: dict = {
        "news": {
            "headline": "테스트 헤드",
            "summary": "테스트 요약",
            "body": "• 라인1",
            "hashtags": ["#테스트"],
            "is_empty": False,
            "source_urls": ["https://x.com/Yellow/status/1"],
            "source_tweet_ids": ["1"],
        },
    }

    async def fake(client_id, hours=24, max_results=30):
        return {
            "client_id": client_id,
            "content_type": "daily_news",
            "handle": "@Yellow",
            "fetched_count": 1,
            "filtered_count": 1,
            "news": state["news"],
            "duration_ms": 1,
        }

    from api import server as api_server
    monkeypatch.setattr(api_server, "_run_daily_news_generation", fake)
    return state


def test_missing_api_key_returns_401(client):
    r = client.post(
        "/clients/yellow/publish/daily-news",
        json={"hours": 24, "dry_run": True, "channels": ["typefully", "telegram"]},
    )
    assert r.status_code == 401


def test_dry_run_returns_composed_payloads_for_both_channels(client, stub_pipeline):
    r = client.post(
        "/clients/yellow/publish/daily-news",
        headers={"x-api-key": ADMIN_KEY},
        json={"hours": 24, "dry_run": True, "channels": ["typefully", "telegram"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["client_id"] == "yellow"
    assert body["generation"]["news"]["headline"] == "테스트 헤드"
    pub = body["publishing"]
    assert pub["typefully"]["ok"] is True
    assert pub["typefully"]["dry_run"] is True
    assert pub["typefully"]["would_target_social_set_id"] == 254669
    assert pub["telegram"]["ok"] is True
    assert pub["telegram"]["dry_run"] is True


def test_is_empty_news_skips_publishing(client, stub_pipeline):
    stub_pipeline["news"] = {
        "headline": "",
        "summary": "",
        "body": "",
        "hashtags": [],
        "is_empty": True,
    }

    r = client.post(
        "/clients/yellow/publish/daily-news",
        headers={"x-api-key": ADMIN_KEY},
        json={"hours": 24, "dry_run": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["publishing"]["skipped"] is True
    assert body["publishing"]["reason"] == "no_tweets_in_window"


def test_inactive_channel_is_skipped(client, stub_pipeline, monkeypatch):
    """When typefully.active=False in config, that channel gets skipped but telegram still runs."""
    from api import server as api_server
    real_load = api_server.load_client_config

    def fake_load(client_id):
        cfg = real_load(client_id)
        if cfg.publishing and cfg.publishing.typefully:
            cfg.publishing.typefully.active = False
        return cfg

    monkeypatch.setattr(api_server, "load_client_config", fake_load)

    r = client.post(
        "/clients/yellow/publish/daily-news",
        headers={"x-api-key": ADMIN_KEY},
        json={"hours": 24, "dry_run": True, "channels": ["typefully", "telegram"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["publishing"]["typefully"]["skipped"] is True
    assert body["publishing"]["typefully"]["reason"] == "channel_inactive_or_unconfigured"
    # Telegram must still run normally.
    assert body["publishing"]["telegram"]["ok"] is True
    assert body["publishing"]["telegram"]["dry_run"] is True


def test_typefully_failure_does_not_block_telegram(client, stub_pipeline, monkeypatch):
    """Channel isolation: an exception in typefully publish must not stop telegram from running."""
    from api import server as api_server

    class _BoomPublisher:
        async def publish(self, payload, dry_run, publish_at=None):
            raise RuntimeError("typefully blew up")

    class _OkPublisher:
        async def publish(self, payload, dry_run, publish_at=None):
            return {"ok": True, "channel": "telegram", "dry_run": dry_run}

    def fake_build(channel, config):
        return _BoomPublisher() if channel == "typefully" else _OkPublisher()

    monkeypatch.setattr(api_server, "_build_publisher", fake_build)

    r = client.post(
        "/clients/yellow/publish/daily-news",
        headers={"x-api-key": ADMIN_KEY},
        json={"hours": 24, "dry_run": True, "channels": ["typefully", "telegram"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["publishing"]["typefully"]["ok"] is False
    assert "typefully blew up" in body["publishing"]["typefully"]["error"]
    assert body["publishing"]["telegram"]["ok"] is True
