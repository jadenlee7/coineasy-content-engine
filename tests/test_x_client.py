from __future__ import annotations

import pytest

from core.sources.x_client import (
    XClient,
    XRateLimitError,
    XRequestError,
    XTransientError,
)


class _Response:
    def __init__(self, payload: dict, status_code: int = 200, headers: dict | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = type("Request", (), {"url": "https://api.x.com/test"})()
            response = type("Response", (), {"status_code": self.status_code})()
            raise RuntimeError(f"HTTP {self.status_code}: {request.url}: {response.status_code}")

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_recent_tweets_include_only_allowlisted_x_media(monkeypatch):
    calls: list[tuple[str, dict | None]] = []

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            calls.append((url, params))
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "1234567890",
                    "text": "short text",
                    "note_tweet": {"text": "full official announcement"},
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [],
                    "public_metrics": {"like_count": 12},
                    "attachments": {"media_keys": ["good", "bad"]},
                }],
                "includes": {"media": [
                    {
                        "media_key": "good",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/official.jpg",
                        "width": 1200,
                        "height": 675,
                    },
                    {
                        "media_key": "bad",
                        "type": "photo",
                        "url": "https://example.com/untrusted.jpg",
                    },
                ]},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets("@SquidRouter", hours=30)

    assert len(tweets) == 1
    assert tweets[0]["text"] == "full official announcement"
    assert tweets[0]["is_note_tweet"] is True
    assert tweets[0]["url"] == "https://x.com/SquidRouter/status/1234567890"
    assert tweets[0]["source_image_url"] == "https://pbs.twimg.com/media/official.jpg"
    assert tweets[0]["media"] == [{
        "media_key": "good",
        "type": "photo",
        "url": "https://pbs.twimg.com/media/official.jpg",
        "width": 1200,
        "height": 675,
    }]
    timeline_params = calls[1][1]
    assert timeline_params is not None
    assert timeline_params["expansions"] == "attachments.media_keys"
    assert "note_tweet" in timeline_params["tweet.fields"]


def test_x_media_allowlist_rejects_lookalike_and_credentials():
    assert XClient._allowed_media_url("https://pbs.twimg.com/media/ok.png") is True
    assert XClient._allowed_media_url("http://pbs.twimg.com/media/no.png") is False
    assert XClient._allowed_media_url("https://pbs.twimg.com.evil.test/no.png") is False
    assert XClient._allowed_media_url("https://user@pbs.twimg.com/no.png") is False
    assert XClient._allowed_media_url(None) is False


def test_x_client_requires_a_server_bearer_token(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="X_BEARER_TOKEN"):
        XClient()


@pytest.mark.asyncio
async def test_since_cursor_takes_precedence_and_pagination_is_bounded(monkeypatch):
    timeline_calls: list[dict] = []

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers["Authorization"] == "Bearer x-token"
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            timeline_calls.append(params)
            index = len(timeline_calls)
            return _Response({
                "data": [{
                    "id": str(100 + index),
                    "text": f"post {index}",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [],
                    "attachments": {"media_keys": [f"video-{index}"]},
                }],
                "includes": {"media": [{
                    "media_key": f"video-{index}",
                    "type": "video",
                    "preview_image_url": f"https://pbs.twimg.com/media/video-{index}.jpg",
                }]},
                "meta": (
                    {"next_token": f"page-{index}"}
                    if index == 1
                    else {}
                ),
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)
    tweets = await XClient("x-token").get_recent_tweets(
        "Yellow",
        max_results=6,
        since_id="123456789",
    )

    assert len(timeline_calls) == 2
    assert all(call["since_id"] == "123456789" for call in timeline_calls)
    assert all("start_time" not in call for call in timeline_calls)
    assert all(call["exclude"] == "replies,retweets" for call in timeline_calls)
    assert timeline_calls[1]["pagination_token"] == "page-1"
    assert tweets[0]["media"][0]["type"] == "video"
    assert tweets[0]["source_image_url"] == ""


@pytest.mark.asyncio
async def test_timeline_truncation_fails_before_a_cursor_can_advance(monkeypatch):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers["Authorization"] == "Bearer x-token"
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            page = str(params.get("pagination_token") or "first")
            return _Response({
                "data": [{
                    "id": "201" if page == "first" else "200",
                    "text": f"post {page}",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [],
                }],
                "meta": {"next_token": f"after-{page}"},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    with pytest.raises(
        XTransientError,
        match="bounded collection window",
    ):
        await XClient("x-token").get_recent_tweets(
            "Yellow",
            max_results=200,
            since_id="123456789",
            require_complete=True,
        )


@pytest.mark.asyncio
async def test_manual_sample_returns_bounded_results_when_more_pages_exist(monkeypatch):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers["Authorization"] == "Bearer x-token"
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            page = str(params.get("pagination_token") or "first")
            return _Response({
                "data": [{
                    "id": "201" if page == "first" else "200",
                    "text": f"post {page}",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [],
                }],
                "meta": {"next_token": f"after-{page}"},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets(
        "Yellow",
        max_results=30,
    )

    assert [tweet["id"] for tweet in tweets] == ["201", "200"]


@pytest.mark.asyncio
async def test_rate_limit_preserves_provider_reset_time(monkeypatch):
    monkeypatch.setattr("core.sources.x_client.time.time", lambda: 1_900_000_000)
    reset_at = 1_900_000_600

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return _Response({}, status_code=429, headers={"x-rate-limit-reset": str(reset_at)})

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)
    with pytest.raises(XRateLimitError) as error:
        await XClient("x-token").get_user_id("Yellow")
    assert error.value.reset_at == reset_at


def test_x_client_reads_rotated_bearer_token_at_construction(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "first-token")
    assert XClient().bearer == "first-token"
    monkeypatch.setenv("X_BEARER_TOKEN", "rotated-token")
    assert XClient().bearer == "rotated-token"


@pytest.mark.asyncio
async def test_x_client_rejects_partial_provider_responses(monkeypatch):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return _Response({
                "data": {"id": "42"},
                "errors": [{"detail": "provider-specific source detail"}],
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)
    with pytest.raises(XTransientError, match="incomplete response") as error:
        await XClient("x-token").get_user_id("Yellow")
    assert "provider-specific" not in str(error.value)


@pytest.mark.parametrize("username", ["", "bad/name", "too-long-user-name", "yellow.example"])
def test_x_client_rejects_invalid_usernames(username):
    with pytest.raises(ValueError, match="username"):
        XClient._normalize_username(username)


@pytest.mark.asyncio
async def test_x_client_wraps_nonretryable_http_status_without_response_body(monkeypatch):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return _Response(
                {"detail": "secret provider diagnostic"},
                status_code=401,
            )

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)
    with pytest.raises(XRequestError) as error:
        await XClient("x-token").get_user_id("Yellow")
    assert error.value.status_code == 401
    assert "diagnostic" not in str(error.value)
