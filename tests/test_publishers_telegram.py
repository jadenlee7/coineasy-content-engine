import json
from typing import Any

import pytest

from core.publishers.telegram import TelegramPublisher, _build_message, _esc


SAMPLE_PAYLOAD = {
    "headline": "Yellow Network 신규 파트너십",
    "summary": "Yellow Network이 글로벌 거래소와 신규 파트너십을 체결했습니다.",
    "body": "• 첫째 라인\n• 둘째 라인",
    "hashtags": ["#Yellow", "#크립토"],
    "source_urls": ["https://x.com/Yellow/status/123"],
    "is_empty": False,
}


def test_html_escape_handles_special_chars():
    assert _esc("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_build_message_wraps_headline_in_b_tag():
    msg = _build_message(SAMPLE_PAYLOAD)
    assert msg.startswith("<b>Yellow Network 신규 파트너십</b>")
    assert "출처: https://x.com/Yellow/status/123" in msg
    assert "• 첫째 라인" in msg


def test_build_message_escapes_html_in_user_content():
    payload = {
        "headline": "Headline <script>",
        "summary": "summary & more",
        "body": "body > here",
        "hashtags": ["#tag"],
        "source_urls": [],
    }
    msg = _build_message(payload)
    # Bold tags around headline are intact
    assert "<b>Headline &lt;script&gt;</b>" in msg
    assert "summary &amp; more" in msg
    assert "body &gt; here" in msg


def test_build_message_normalizes_raw_newlines():
    payload = {
        "headline": "헤드",
        "summary": "줄1\\n줄2",
        "body": "",
        "hashtags": [],
        "source_urls": [],
    }
    msg = _build_message(payload)
    assert "\\n" not in msg
    assert "줄1\n줄2" in msg


class _FakeResponse:
    def __init__(self, status_code: int, json_body: Any = None):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.text = json.dumps(self._json)

    def json(self):
        return self._json


class _FakeAsyncClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url: str, *, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_dry_run_makes_no_http_call(monkeypatch):
    fake = _FakeAsyncClient([])
    monkeypatch.setattr(
        "core.publishers.telegram.httpx.AsyncClient",
        lambda *a, **kw: fake,
    )
    monkeypatch.setenv("TG_BOT_X", "tok123")
    monkeypatch.setenv("TG_CHAN_X", "@channel")
    publisher = TelegramPublisher(bot_env="TG_BOT_X", channel_env="TG_CHAN_X", client_id="yellow")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "would_send" in result
    assert "<b>" in result["would_send"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_real_post_hits_correct_url(monkeypatch):
    fake = _FakeAsyncClient([_FakeResponse(200, {"ok": True, "result": {"message_id": 9}})])
    monkeypatch.setattr(
        "core.publishers.telegram.httpx.AsyncClient",
        lambda *a, **kw: fake,
    )
    monkeypatch.setenv("TG_BOT_X", "secrettoken123")
    monkeypatch.setenv("TG_CHAN_X", "@yellow_kr_test")
    publisher = TelegramPublisher(bot_env="TG_BOT_X", channel_env="TG_CHAN_X", client_id="yellow")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=False)
    assert result["ok"] is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://api.telegram.org/botsecrettoken123/sendMessage"
    body = call["json"]
    assert body["chat_id"] == "@yellow_kr_test"
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] is False
    assert "<b>" in body["text"]


@pytest.mark.asyncio
async def test_missing_bot_token_returns_error(monkeypatch):
    monkeypatch.delenv("TG_BOT_MISSING", raising=False)
    monkeypatch.setenv("TG_CHAN_MISSING", "@x")
    publisher = TelegramPublisher(bot_env="TG_BOT_MISSING", channel_env="TG_CHAN_MISSING", client_id="yellow")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=False)
    assert result["ok"] is False
    assert "TG_BOT_MISSING" in result["error"]


@pytest.mark.asyncio
async def test_503_retries_then_fails(monkeypatch):
    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("core.publishers.telegram.asyncio.sleep", _fake_sleep)
    fake = _FakeAsyncClient([
        _FakeResponse(503),
        _FakeResponse(503),
        _FakeResponse(503),
    ])
    monkeypatch.setattr(
        "core.publishers.telegram.httpx.AsyncClient",
        lambda *a, **kw: fake,
    )
    monkeypatch.setenv("TG_BOT_R", "t")
    monkeypatch.setenv("TG_CHAN_R", "@c")
    publisher = TelegramPublisher(bot_env="TG_BOT_R", channel_env="TG_CHAN_R", client_id="yellow")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=False)
    assert result["ok"] is False
    assert "503" in result["error"]
    assert len(fake.calls) == 3
    assert sleeps == [1, 2]
