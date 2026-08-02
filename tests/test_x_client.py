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
                    "attachments": {"media_keys": ["good"]},
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
                    {
                        "media_key": "valid-but-unreferenced",
                        "type": "video",
                        "preview_image_url": (
                            "https://pbs.twimg.com/media/unreferenced.jpg"
                        ),
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
    assert timeline_params["expansions"] == (
        "attachments.media_keys,referenced_tweets.id,"
        "referenced_tweets.id.attachments.media_keys"
    )
    assert "author_id" in timeline_params["tweet.fields"]
    assert "note_tweet" in timeline_params["tweet.fields"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media_records",
    [
        [],
        [{
            "media_key": "referenced",
            "type": "audio",
            "url": "https://pbs.twimg.com/media/invalid-type.jpg",
        }],
        [{
            "media_key": "referenced",
            "type": "video",
        }],
        [{
            "media_key": "referenced",
            "type": "video",
            "preview_image_url": "https://attacker.example/preview.jpg",
        }],
    ],
    ids=[
        "unresolved-key",
        "invalid-type",
        "missing-preview",
        "hostile-preview",
    ],
)
async def test_recent_tweets_fail_closed_on_incomplete_referenced_media(
    monkeypatch,
    media_records,
):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "Official update with referenced media.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [],
                    "attachments": {"media_keys": ["referenced"]},
                }],
                "includes": {"media": media_records},
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    with pytest.raises(XTransientError, match="incomplete media evidence"):
        await XClient("x-token").get_recent_tweets("origin_trail")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attachment_keys", "media_records"),
    [
        (
            ["referenced", "referenced"],
            [{
                "media_key": "referenced",
                "type": "photo",
                "url": "https://pbs.twimg.com/media/duplicate-attachment.jpg",
            }],
        ),
        (
            ["referenced"],
            [
                {
                    "media_key": "referenced",
                    "type": "photo",
                    "url": "https://pbs.twimg.com/media/first-record.jpg",
                },
                {
                    "media_key": "referenced",
                    "type": "photo",
                    "url": "https://pbs.twimg.com/media/second-record.jpg",
                },
            ],
        ),
    ],
    ids=["duplicate-attachment-key", "duplicate-include-record"],
)
async def test_recent_tweets_fail_closed_on_duplicate_referenced_media(
    monkeypatch,
    attachment_keys,
    media_records,
):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "Official update with duplicated media evidence.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [],
                    "attachments": {"media_keys": attachment_keys},
                }],
                "includes": {"media": media_records},
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    with pytest.raises(XTransientError, match="incomplete media evidence"):
        await XClient("x-token").get_recent_tweets("origin_trail")


@pytest.mark.asyncio
async def test_recent_tweets_marks_quoted_sources(monkeypatch):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "We are excited to announce a major partnership.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": "2082000000000000000",
                    }],
                }],
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets("origin_trail")

    assert len(tweets) == 1
    assert tweets[0]["is_quote"] is True
    assert tweets[0]["is_reply"] is False
    assert tweets[0]["is_retweet"] is False


@pytest.mark.asyncio
async def test_recent_tweets_inherits_same_account_quoted_photo(monkeypatch):
    quoted_post_id = "2082000000000000000"
    image_url = "https://pbs.twimg.com/media/squid-quoted.jpg"

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "Canton is now easy to explore with Squid.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": quoted_post_id,
                    }],
                }],
                "includes": {
                    "tweets": [{
                        "id": quoted_post_id,
                        "author_id": "42",
                        "attachments": {"media_keys": ["quoted-photo"]},
                    }],
                    "media": [{
                        "media_key": "quoted-photo",
                        "type": "photo",
                        "url": image_url,
                        "width": 1080,
                        "height": 1080,
                    }],
                },
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets("SquidRouter")

    assert tweets[0]["is_quote"] is True
    assert tweets[0]["source_image_url"] == image_url
    assert tweets[0]["media"] == [{
        "media_key": "quoted-photo",
        "type": "photo",
        "url": image_url,
        "width": 1080,
        "height": 1080,
    }]


@pytest.mark.asyncio
async def test_recent_tweets_inherits_same_account_quoted_video_preview(monkeypatch):
    quoted_post_id = "2079266440268464128"
    image_url = (
        "https://pbs.twimg.com/amplify_video_thumb/"
        "2079266440268464128/img/qxIE-WTD9Fd60z4C.jpg"
    )

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2081031728622178334",
                    "text": "Have you explored Canton yet? With Squid, it is easy.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": quoted_post_id,
                    }],
                }],
                "includes": {
                    "tweets": [{
                        "id": quoted_post_id,
                        "author_id": "42",
                        "attachments": {"media_keys": ["quoted-video"]},
                    }],
                    "media": [{
                        "media_key": "quoted-video",
                        "type": "video",
                        "preview_image_url": image_url,
                        "width": 1080,
                        "height": 1080,
                    }],
                },
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets("SquidRouter")

    assert tweets[0]["source_image_url"] == ""
    assert tweets[0]["media"] == [{
        "media_key": "quoted-video",
        "type": "video",
        "url": image_url,
        "width": 1080,
        "height": 1080,
    }]


@pytest.mark.asyncio
async def test_recent_tweets_prefers_direct_media_over_same_account_quote(
    monkeypatch,
):
    quoted_post_id = "2082000000000000000"
    direct_url = "https://pbs.twimg.com/media/squid-direct.jpg"

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "Our own new visual supersedes the quoted one.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": quoted_post_id,
                    }],
                    "attachments": {"media_keys": ["direct-photo"]},
                }],
                "includes": {
                    "tweets": [{
                        "id": quoted_post_id,
                        "author_id": "42",
                        "attachments": {"media_keys": ["quoted-photo"]},
                    }],
                    "media": [
                        {
                            "media_key": "direct-photo",
                            "type": "photo",
                            "url": direct_url,
                        },
                        {
                            "media_key": "quoted-photo",
                            "type": "photo",
                            "url": (
                                "https://pbs.twimg.com/media/squid-quoted.jpg"
                            ),
                        },
                    ],
                },
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets("SquidRouter")

    assert tweets[0]["source_image_url"] == direct_url
    assert [item["media_key"] for item in tweets[0]["media"]] == [
        "direct-photo"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("quoted_author_id", ["99", None])
async def test_recent_tweets_does_not_inherit_unverified_quoted_media(
    monkeypatch,
    quoted_author_id,
):
    quoted_post_id = "2082000000000000000"

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            referenced = {
                "id": quoted_post_id,
                "attachments": {"media_keys": ["external-photo"]},
            }
            if quoted_author_id is not None:
                referenced["author_id"] = quoted_author_id
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "Read this ecosystem update.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": quoted_post_id,
                    }],
                }],
                "includes": {
                    "tweets": [referenced],
                    "media": [{
                        "media_key": "external-photo",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/external.jpg",
                    }],
                },
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets("SquidRouter")

    assert tweets[0]["media"] == []
    assert tweets[0]["source_image_url"] == ""


@pytest.mark.asyncio
async def test_recent_tweets_fails_closed_on_incomplete_same_account_quote_media(
    monkeypatch,
):
    quoted_post_id = "2082000000000000000"

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "Canton is now easy to explore with Squid.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": quoted_post_id,
                    }],
                }],
                "includes": {
                    "tweets": [{
                        "id": quoted_post_id,
                        "author_id": "42",
                        "attachments": {"media_keys": ["missing-photo"]},
                    }],
                    "media": [],
                },
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    with pytest.raises(XTransientError, match="incomplete media evidence"):
        await XClient("x-token").get_recent_tweets("SquidRouter")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        "https://api.x.com/2/problems/resource-not-found",
        "https://api.x.com/2/problems/not-authorized-for-resource",
    ],
    ids=["deleted", "protected"],
)
async def test_recent_tweets_excludes_explicitly_unavailable_quoted_media(
    monkeypatch,
    error_type,
):
    quoted_post_id = "2082000000000000000"

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "The outer official post remains usable.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": quoted_post_id,
                    }],
                }],
                "errors": [{
                    "resource_id": quoted_post_id,
                    "resource_type": "tweet",
                    "type": error_type,
                    "title": "Unavailable quoted post",
                }],
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets("SquidRouter")

    assert len(tweets) == 1
    assert tweets[0]["media"] == []
    assert tweets[0]["source_image_url"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resource_id", "error_type"),
    [
        (
            "2082883998829752783",
            "https://api.x.com/2/problems/resource-not-found",
        ),
        (
            "2082000000000000000",
            "https://api.x.com/2/problems/usage-capped",
        ),
        (
            "2081999999999999999",
            "https://api.x.com/2/problems/resource-not-found",
        ),
    ],
    ids=["primary-post", "unexpected-type", "unrelated-resource"],
)
async def test_recent_tweets_rejects_non_quote_partial_errors(
    monkeypatch,
    resource_id,
    error_type,
):
    quoted_post_id = "2082000000000000000"

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "Provider errors outside quote availability are unsafe.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": quoted_post_id,
                    }],
                }],
                "errors": [{
                    "resource_id": resource_id,
                    "resource_type": "tweet",
                    "type": error_type,
                }],
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    with pytest.raises(XTransientError, match="incomplete response"):
        await XClient("x-token").get_recent_tweets("SquidRouter")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "references",
    [
        {"type": "quoted", "id": "2082000000000000000"},
        [None],
        [{"id": "2082000000000000000"}],
        [{"type": "unknown", "id": "2082000000000000000"}],
        [{"type": "quoted", "id": "not-a-post-id"}],
    ],
    ids=[
        "non-list",
        "non-object-entry",
        "missing-type",
        "unknown-type",
        "invalid-id",
    ],
)
async def test_recent_tweets_fail_closed_on_invalid_reference_evidence(
    monkeypatch,
    references,
):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            return _Response({
                "data": [{
                    "id": "2082883998829752783",
                    "text": "Official update with invalid references.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": references,
                }],
                "meta": {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    with pytest.raises(XTransientError, match="invalid reference evidence"):
        await XClient("x-token").get_recent_tweets("origin_trail")


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
async def test_same_quote_across_pages_ignores_mutable_metric_changes(monkeypatch):
    timeline_calls: list[dict] = []
    quoted_post_id = "2082000000000000000"
    image_url = "https://pbs.twimg.com/media/repeated-quote.jpg"

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            timeline_calls.append(params)
            page = len(timeline_calls)
            return _Response({
                "data": [{
                    "id": str(2082883998829752784 - page),
                    "text": f"Official resurface {page}.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": quoted_post_id,
                    }],
                }],
                "includes": {
                    "tweets": [{
                        "id": quoted_post_id,
                        "author_id": "42",
                        "attachments": {"media_keys": ["same-photo"]},
                        "public_metrics": {"like_count": page},
                    }],
                    "media": [{
                        "media_key": "same-photo",
                        "type": "photo",
                        "url": image_url,
                    }],
                },
                "meta": {"next_token": "page-2"} if page == 1 else {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets(
        "SquidRouter",
        max_results=6,
    )

    assert len(tweets) == 2
    assert [tweet["source_image_url"] for tweet in tweets] == [
        image_url,
        image_url,
    ]


@pytest.mark.asyncio
async def test_conflicting_external_quote_expansions_do_not_abort(monkeypatch):
    timeline_calls: list[dict] = []
    quoted_post_id = "2082000000000000000"

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers, params=None):
            assert headers == {"Authorization": "Bearer x-token"}
            if "/users/by/username/" in url:
                return _Response({"data": {"id": "42"}})
            timeline_calls.append(params)
            page = len(timeline_calls)
            media_key = f"external-photo-{page}"
            return _Response({
                "data": [{
                    "id": str(2082883998829752784 - page),
                    "text": f"External ecosystem quote {page}.",
                    "created_at": "2026-07-22T08:00:00Z",
                    "referenced_tweets": [{
                        "type": "quoted",
                        "id": quoted_post_id,
                    }],
                }],
                "includes": {
                    "tweets": [{
                        "id": quoted_post_id,
                        "author_id": "99",
                        "attachments": {"media_keys": [media_key]},
                    }],
                    "media": [{
                        "media_key": media_key,
                        "type": "photo",
                        "url": f"https://pbs.twimg.com/media/{media_key}.jpg",
                    }],
                },
                "meta": {"next_token": "page-2"} if page == 1 else {},
            })

    monkeypatch.setattr("core.sources.x_client.httpx.AsyncClient", _Client)

    tweets = await XClient("x-token").get_recent_tweets(
        "SquidRouter",
        max_results=6,
    )

    assert len(tweets) == 2
    assert all(tweet["media"] == [] for tweet in tweets)
    assert all(tweet["source_image_url"] == "" for tweet in tweets)


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
