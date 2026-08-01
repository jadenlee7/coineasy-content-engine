from typing import Any

import pytest
from fastapi.testclient import TestClient


ADMIN_KEY = "test_admin_key_publish"


@pytest.fixture(autouse=True)
def patch_security(monkeypatch, tmp_path):
    from api import security
    monkeypatch.setattr(security, "API_KEYS", {"admin": ADMIN_KEY})
    monkeypatch.setattr(security, "OUTPUT_ROOT", tmp_path.resolve())
    yield


@pytest.fixture
def client():
    from api.server import app
    return TestClient(app)


SAMPLE_NEWS_FILLED = {
    "headline": "Yellow 신규 파트너십",
    "summary": "Yellow가 새로운 파트너십을 발표했습니다.",
    "body": "• 파트너십 체결\n• 기술 통합 진행 중",
    "hashtags": ["#Yellow", "#크립토"],
    "source_urls": ["https://x.com/Yellow/status/1"],
    "source_tweet_ids": ["1"],
    "is_empty": False,
}

SAMPLE_NEWS_EMPTY = {
    "headline": "",
    "summary": "",
    "body": "",
    "hashtags": [],
    "is_empty": True,
}


def _patch_generation(monkeypatch, news: dict[str, Any]):
    """Replace the daily-news pipeline with a deterministic stub."""
    from api import server

    async def _fake_run(client_id, hours, max_results):
        return {
            "client_id": client_id,
            "content_type": "daily_news",
            "handle": "@Yellow",
            "fetched_count": 1,
            "filtered_count": 1,
            "news": news,
            "duration_ms": 1,
        }

    monkeypatch.setattr(server, "_run_daily_news_generation", _fake_run)


def test_unauthenticated_returns_401(client):
    r = client.post(
        "/clients/yellow/publish/daily-news",
        json={"hours": 24, "dry_run": True},
    )
    assert r.status_code == 401


def test_invalid_channel_rejected_by_pydantic(client):
    r = client.post(
        "/clients/yellow/publish/daily-news",
        json={"hours": 24, "dry_run": True, "channels": ["facebook"]},
        headers={"x-api-key": ADMIN_KEY},
    )
    assert r.status_code == 422


def test_live_squid_telegram_is_exclusive_to_exact_studio_publication(
    client, monkeypatch
):
    from api import server

    async def _generation_must_not_run(*_args, **_kwargs):
        raise AssertionError("legacy generation must not run")

    monkeypatch.setattr(
        server,
        "_run_daily_news_generation",
        _generation_must_not_run,
    )
    response = client.post(
        "/clients/squid/publish/daily-news",
        json={"hours": 24, "dry_run": False, "channels": ["telegram"]},
        headers={"x-api-key": ADMIN_KEY},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "squid_telegram_exact_publication_required"


def test_empty_news_skips_publishing(client, monkeypatch):
    _patch_generation(monkeypatch, SAMPLE_NEWS_EMPTY)
    r = client.post(
        "/clients/yellow/publish/daily-news",
        json={"hours": 24, "dry_run": True},
        headers={"x-api-key": ADMIN_KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["publishing"] == {"skipped": True, "reason": "no_tweets_in_window"}


def test_dry_run_returns_would_post_for_both_channels(client, monkeypatch):
    _patch_generation(monkeypatch, SAMPLE_NEWS_FILLED)
    r = client.post(
        "/clients/yellow/publish/daily-news",
        json={"hours": 24, "dry_run": True, "channels": ["typefully", "telegram"]},
        headers={"x-api-key": ADMIN_KEY},
    )
    assert r.status_code == 200
    pub = r.json()["publishing"]
    assert pub["typefully"]["dry_run"] is True
    assert "would_post" in pub["typefully"]
    assert pub["telegram"]["dry_run"] is True
    assert "would_send" in pub["telegram"]


def test_inactive_channel_skipped_other_channel_runs(client, monkeypatch, tmp_path):
    """If a channel is set active=false in config, that channel reports skipped
    while the other still runs."""
    _patch_generation(monkeypatch, SAMPLE_NEWS_FILLED)

    # Stage a temp client config with telegram inactive, typefully active
    from api import server
    from core import client_config

    clients_dir = tmp_path / "clients"
    (clients_dir / "tempyellow").mkdir(parents=True)
    (clients_dir / "tempyellow" / "config.yaml").write_text(
        "client_id: tempyellow\n"
        "name: Temp Yellow\n"
        "active: true\n"
        "content_sources:\n"
        "  twitter:\n"
        "    handle: '@Yellow'\n"
        "publishing:\n"
        "  typefully:\n"
        "    social_set_id: 254669\n"
        "    active: true\n"
        "  telegram:\n"
        "    bot_env: TELEGRAM_BOT_TOKEN_YELLOW\n"
        "    channel_env: TELEGRAM_CHANNEL_YELLOW\n"
        "    active: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(client_config, "CLIENTS_DIR", clients_dir)
    monkeypatch.setattr(server, "CLIENTS_DIR", clients_dir)

    r = client.post(
        "/clients/tempyellow/publish/daily-news",
        json={"hours": 24, "dry_run": True, "channels": ["typefully", "telegram"]},
        headers={"x-api-key": ADMIN_KEY},
    )
    assert r.status_code == 200
    pub = r.json()["publishing"]
    assert pub["typefully"]["dry_run"] is True
    assert pub["telegram"]["skipped"] is True
    assert pub["telegram"]["skipped_reason"] == "channel_inactive"


def test_one_channel_failure_does_not_block_other(client, monkeypatch, tmp_path):
    """Channel isolation: typefully publisher raising must not block telegram."""
    _patch_generation(monkeypatch, SAMPLE_NEWS_FILLED)

    from api import server
    from core import client_config

    clients_dir = tmp_path / "clients"
    (clients_dir / "isoclient").mkdir(parents=True)
    (clients_dir / "isoclient" / "config.yaml").write_text(
        "client_id: isoclient\n"
        "name: Iso Client\n"
        "active: true\n"
        "content_sources:\n"
        "  twitter:\n"
        "    handle: '@Iso'\n"
        "publishing:\n"
        "  typefully:\n"
        "    social_set_id: 9999\n"
        "    active: true\n"
        "  telegram:\n"
        "    bot_env: TG_ISO_BOT\n"
        "    channel_env: TG_ISO_CHAN\n"
        "    active: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(client_config, "CLIENTS_DIR", clients_dir)
    monkeypatch.setattr(server, "CLIENTS_DIR", clients_dir)

    # Make typefully raise unconditionally, telegram succeed in dry_run
    from core.publishers import typefully as typefully_mod

    class _BoomPublisher:
        async def publish(self, *a, **kw):
            raise RuntimeError("boom")

    def _fake_build(channel, ch_cfg, client_id):
        if channel == "typefully":
            return _BoomPublisher()
        from core.publishers.telegram import TelegramPublisher
        return TelegramPublisher(
            bot_env=ch_cfg["bot_env"],
            channel_env=ch_cfg["channel_env"],
            client_id=client_id,
        )

    monkeypatch.setattr(server, "_build_publisher", _fake_build)

    r = client.post(
        "/clients/isoclient/publish/daily-news",
        json={"hours": 24, "dry_run": True, "channels": ["typefully", "telegram"]},
        headers={"x-api-key": ADMIN_KEY},
    )
    assert r.status_code == 200
    pub = r.json()["publishing"]
    assert pub["typefully"]["ok"] is False
    assert "boom" in pub["typefully"]["error"]
    # Telegram still ran in dry_run mode
    assert pub["telegram"]["ok"] is True
    assert pub["telegram"]["dry_run"] is True
