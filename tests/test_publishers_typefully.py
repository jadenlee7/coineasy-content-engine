import asyncio

import pytest

from core.publishers import typefully as tf_mod
from core.publishers.typefully import TypefullyPublisher, compose_x_post, X_POST_LIMIT


# ────────────────────────────────────────────────────
# httpx fake
# ────────────────────────────────────────────────────

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

        monkeypatch.setattr(tf_mod, "httpx", _Httpx)


@pytest.fixture
def fake_http(monkeypatch):
    f = _FakeHttp()
    f.install(monkeypatch)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(tf_mod.asyncio, "sleep", _no_sleep)
    return f


# ────────────────────────────────────────────────────
# compose_x_post
# ────────────────────────────────────────────────────

def test_compose_replaces_literal_newlines():
    payload = {
        "headline": "헤드라인",
        "summary": "라인1\\n라인2",
        "hashtags": ["#한국어"],
    }
    text = compose_x_post(payload)
    assert "라인1\n라인2" in text
    assert "\\n" not in text


def test_compose_excludes_source_urls():
    payload = {
        "headline": "헤드",
        "summary": "요약",
        "hashtags": ["#태그"],
        "source_urls": ["https://x.com/Yellow/status/123"],
    }
    text = compose_x_post(payload)
    assert "x.com" not in text


def test_compose_trims_to_280():
    payload = {
        "headline": "짧은 헤드라인",
        "summary": "가" * 500,
        "hashtags": ["#한국어크립토"],
    }
    text = compose_x_post(payload)
    assert len(text) <= X_POST_LIMIT
    assert "…" in text
    assert text.startswith("짧은 헤드라인")
    assert "#한국어크립토" in text  # hashtags must survive the trim


# ────────────────────────────────────────────────────
# publish behavior
# ────────────────────────────────────────────────────

def test_dry_run_makes_no_http_calls(fake_http):
    pub = TypefullyPublisher(social_set_id=12345, api_key="key_xxx")
    payload = {"headline": "h", "summary": "s", "hashtags": ["#t"]}
    result = asyncio.run(pub.publish(payload, dry_run=True))

    assert fake_http.calls == []
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["would_target_social_set_id"] == 12345
    assert "would_post" in result


def test_publish_hits_correct_url_and_body(fake_http):
    fake_http.add(status_code=200, json_data={"id": "draft_abc"})
    pub = TypefullyPublisher(social_set_id=254669, api_key="key_xxx")
    payload = {"headline": "헤드", "summary": "요약", "hashtags": ["#한국어"]}

    result = asyncio.run(pub.publish(payload, dry_run=False))

    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    assert call["url"] == "https://api.typefully.com/v2/social-sets/254669/drafts"
    assert call["headers"]["Authorization"] == "Bearer key_xxx"
    assert call["json"]["platforms"]["x"]["enabled"] is True
    assert len(call["json"]["platforms"]["x"]["posts"]) == 1
    assert "publish_at" not in call["json"]
    assert result["ok"] is True


def test_publish_at_is_forwarded(fake_http):
    fake_http.add(status_code=200, json_data={"id": "x"})
    pub = TypefullyPublisher(social_set_id=1, api_key="k")
    asyncio.run(pub.publish({"headline": "h"}, dry_run=False, publish_at="next-free-slot"))
    assert fake_http.calls[0]["json"]["publish_at"] == "next-free-slot"


def test_429_retries_three_times_then_fails(fake_http):
    fake_http.add(status_code=429, text="rate limited")
    fake_http.add(status_code=429, text="rate limited")
    fake_http.add(status_code=429, text="rate limited")

    pub = TypefullyPublisher(social_set_id=1, api_key="k")
    result = asyncio.run(pub.publish({"headline": "h"}, dry_run=False))

    assert len(fake_http.calls) == 3
    assert result["ok"] is False
    assert result["error"] == "http_429"


def test_403_does_not_retry(fake_http):
    fake_http.add(status_code=403, text="forbidden")
    pub = TypefullyPublisher(social_set_id=1, api_key="bad")
    result = asyncio.run(pub.publish({"headline": "h"}, dry_run=False))

    assert len(fake_http.calls) == 1
    assert result["ok"] is False
    assert "403" in result["error"]


def test_missing_api_key_returns_error_without_calling(fake_http):
    pub = TypefullyPublisher(social_set_id=1, api_key="")
    result = asyncio.run(pub.publish({"headline": "h"}, dry_run=False))
    assert fake_http.calls == []
    assert result["ok"] is False
    assert "TYPEFULLY_API_KEY" in result["error"]
