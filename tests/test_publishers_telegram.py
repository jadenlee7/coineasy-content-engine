import asyncio

import pytest

from core.publishers import telegram as tg_mod
from core.publishers.telegram import TelegramPublisher, compose_telegram_message


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text or ""

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class _FakeHttp:
    def __init__(self):
        self.calls: list[dict] = []
        self.responses: list[_FakeResponse] = []

    def add(self, status_code=200, json_data=None, text=""):
        self.responses.append(_FakeResponse(status_code, json_data, text))

    def install(self, monkeypatch):
        outer = self

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                outer.calls.append({"url": url, "headers": headers, "json": json})
                if outer.responses:
                    return outer.responses.pop(0)
                return _FakeResponse()

        class _Httpx:
            AsyncClient = _Client
            TimeoutException = Exception
            TransportError = Exception

        monkeypatch.setattr(tg_mod, "httpx", _Httpx)


@pytest.fixture
def fake_http(monkeypatch):
    f = _FakeHttp()
    f.install(monkeypatch)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(tg_mod.asyncio, "sleep", _no_sleep)

    monkeypatch.setenv("TG_TOKEN_TEST", "bot_token_xyz")
    monkeypatch.setenv("TG_CHANNEL_TEST", "@test_channel")
    return f


def test_compose_html_escapes_unsafe_chars():
    payload = {
        "headline": "Yellow & friends",
        "summary": "<script>alert(1)</script>",
        "body": "• 라인",
        "hashtags": ["#태그"],
        "source_urls": ["https://x.com/Yellow/status/123"],
    }
    text = compose_telegram_message(payload)
    assert "<b>Yellow &amp; friends</b>" in text
    assert "&lt;script&gt;" in text
    assert "<script>" not in text
    assert "출처: https://x.com/Yellow/status/123" in text


def test_compose_replaces_literal_newlines():
    payload = {
        "headline": "헤드",
        "body": "• 줄1\\n• 줄2",
    }
    text = compose_telegram_message(payload)
    assert "\\n" not in text
    assert "• 줄1\n• 줄2" in text


def test_dry_run_makes_no_http_calls(fake_http):
    pub = TelegramPublisher(bot_env="TG_TOKEN_TEST", channel_env="TG_CHANNEL_TEST")
    result = asyncio.run(pub.publish({"headline": "h", "summary": "s"}, dry_run=True))

    assert fake_http.calls == []
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "would_send" in result
    assert result["would_chat_id"] == "@test_channel"


def test_publish_hits_correct_url(fake_http):
    fake_http.add(status_code=200, json_data={"ok": True, "result": {"message_id": 42}})
    pub = TelegramPublisher(bot_env="TG_TOKEN_TEST", channel_env="TG_CHANNEL_TEST")

    result = asyncio.run(pub.publish({"headline": "헤드", "summary": "요약"}, dry_run=False))

    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    assert call["url"] == "https://api.telegram.org/botbot_token_xyz/sendMessage"
    assert call["json"]["chat_id"] == "@test_channel"
    assert call["json"]["parse_mode"] == "HTML"
    assert result["ok"] is True


def test_missing_bot_token_returns_error(fake_http, monkeypatch):
    monkeypatch.delenv("TG_TOKEN_TEST", raising=False)
    pub = TelegramPublisher(bot_env="TG_TOKEN_TEST", channel_env="TG_CHANNEL_TEST")
    result = asyncio.run(pub.publish({"headline": "h"}, dry_run=False))
    assert fake_http.calls == []
    assert result["ok"] is False
    assert "TG_TOKEN_TEST" in result["error"]


def test_500_retries_three_times(fake_http):
    fake_http.add(status_code=500, text="boom")
    fake_http.add(status_code=500, text="boom")
    fake_http.add(status_code=500, text="boom")
    pub = TelegramPublisher(bot_env="TG_TOKEN_TEST", channel_env="TG_CHANNEL_TEST")
    result = asyncio.run(pub.publish({"headline": "h"}, dry_run=False))
    assert len(fake_http.calls) == 3
    assert result["ok"] is False
