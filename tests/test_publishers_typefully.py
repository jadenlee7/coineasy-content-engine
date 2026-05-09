import json
from typing import Any

import httpx
import pytest

from core.publishers.typefully import (
    TypefullyPublisher,
    X_POST_LIMIT,
    _build_x_post,
    _normalize_newlines,
)


SAMPLE_PAYLOAD = {
    "headline": "Yellow Network 신규 파트너십 발표",
    "summary": "Yellow Network이 글로벌 거래소와 신규 파트너십을 체결했습니다.",
    "body": "• 첫째 라인\n• 둘째 라인\n• 셋째 라인",
    "hashtags": ["#Yellow", "#크립토", "#Web3"],
    "source_urls": ["https://x.com/Yellow/status/123"],
    "is_empty": False,
}


def test_normalize_newlines_replaces_raw_backslash_n():
    text = "line1\\nline2\\n\\nline3"
    out = _normalize_newlines(text)
    assert out == "line1\nline2\n\nline3"


def test_build_x_post_assembles_headline_summary_hashtags():
    text = _build_x_post(SAMPLE_PAYLOAD)
    assert SAMPLE_PAYLOAD["headline"] in text
    assert SAMPLE_PAYLOAD["summary"] in text
    assert "#Yellow #크립토 #Web3" in text
    # Source URLs must NOT be embedded in the X post text (X policy)
    assert "https://x.com/" not in text


def test_build_x_post_excludes_source_urls():
    payload = {**SAMPLE_PAYLOAD, "source_urls": ["https://x.com/a/b"]}
    text = _build_x_post(payload)
    assert "https://" not in text


def test_build_x_post_normalizes_raw_newlines():
    payload = {
        "headline": "헤드라인",
        "summary": "요약입니다.\\n다음 줄.",
        "hashtags": ["#태그"],
    }
    text = _build_x_post(payload)
    assert "\\n" not in text
    assert "다음 줄." in text


def test_build_x_post_trims_summary_when_over_limit():
    long_summary = "가" * 400
    payload = {
        "headline": "짧은 헤드라인",
        "summary": long_summary,
        "hashtags": ["#태그"],
    }
    text = _build_x_post(payload)
    assert len(text) <= X_POST_LIMIT
    # Summary section is truncated and gets a trailing "…"; full text still ends
    # with the hashtag block, so check that the truncation marker landed in the
    # middle of the assembled text.
    assert "…" in text
    assert "짧은 헤드라인" in text
    assert "#태그" in text
    # Original summary length far exceeds limit; truncated form must be shorter
    assert text.count("가") < 400


class _FakeResponse:
    def __init__(self, status_code: int, json_body: Any = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.text = text or json.dumps(self._json)

    def json(self):
        return self._json


class _FakeAsyncClient:
    """Records POSTs and returns scripted responses."""

    def __init__(self, responses: list[_FakeResponse]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, *, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if not self.responses:
            raise RuntimeError("No more scripted responses")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_dry_run_makes_no_http_call(monkeypatch):
    fake = _FakeAsyncClient([])
    monkeypatch.setattr(
        "core.publishers.typefully.httpx.AsyncClient",
        lambda *a, **kw: fake,
    )
    publisher = TypefullyPublisher(social_set_id=254669, client_id="yellow", api_key="k")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["would_target_social_set_id"] == 254669
    assert "would_post" in result
    assert SAMPLE_PAYLOAD["headline"] in result["would_post"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_real_post_hits_correct_url(monkeypatch):
    fake = _FakeAsyncClient([_FakeResponse(200, {"id": "draft_abc"})])
    monkeypatch.setattr(
        "core.publishers.typefully.httpx.AsyncClient",
        lambda *a, **kw: fake,
    )
    publisher = TypefullyPublisher(social_set_id=254669, client_id="yellow", api_key="key123")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=False)
    assert result["ok"] is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://api.typefully.com/v2/social-sets/254669/drafts"
    assert call["headers"]["Authorization"] == "Bearer key123"
    body = call["json"]
    assert body["platforms"]["x"]["enabled"] is True
    assert len(body["platforms"]["x"]["posts"]) == 1
    assert "draft_title" in body
    assert body["draft_title"].startswith("Daily News - yellow - ")
    assert "publish_at" in body


@pytest.mark.asyncio
async def test_429_retries_three_times_then_fails(monkeypatch):
    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("core.publishers.typefully.asyncio.sleep", _fake_sleep)
    fake = _FakeAsyncClient([
        _FakeResponse(429, {"error": "rate"}),
        _FakeResponse(429, {"error": "rate"}),
        _FakeResponse(429, {"error": "rate"}),
    ])
    monkeypatch.setattr(
        "core.publishers.typefully.httpx.AsyncClient",
        lambda *a, **kw: fake,
    )
    publisher = TypefullyPublisher(social_set_id=254669, client_id="yellow", api_key="k")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=False)
    assert result["ok"] is False
    assert "429" in result["error"]
    assert len(fake.calls) == 3
    # Two backoff sleeps between attempt 1→2 and 2→3
    assert sleeps == [1, 2]


@pytest.mark.asyncio
async def test_401_does_not_retry(monkeypatch):
    fake = _FakeAsyncClient([_FakeResponse(401, {"error": "unauthorized"})])
    monkeypatch.setattr(
        "core.publishers.typefully.httpx.AsyncClient",
        lambda *a, **kw: fake,
    )
    publisher = TypefullyPublisher(social_set_id=254669, client_id="yellow", api_key="bad")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=False)
    assert result["ok"] is False
    assert "401" in result["error"]
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_timeout_retries(monkeypatch):
    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("core.publishers.typefully.asyncio.sleep", _fake_sleep)

    class _TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(
        "core.publishers.typefully.httpx.AsyncClient",
        lambda *a, **kw: _TimeoutClient(),
    )
    publisher = TypefullyPublisher(social_set_id=254669, client_id="yellow", api_key="k")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=False)
    assert result["ok"] is False
    assert sleeps == [1, 2]


@pytest.mark.asyncio
async def test_missing_api_key_returns_error(monkeypatch):
    monkeypatch.delenv("TYPEFULLY_API_KEY", raising=False)
    publisher = TypefullyPublisher(social_set_id=254669, client_id="yellow", api_key="")
    result = await publisher.publish(SAMPLE_PAYLOAD, dry_run=False)
    assert result["ok"] is False
    assert "TYPEFULLY_API_KEY" in result["error"]
