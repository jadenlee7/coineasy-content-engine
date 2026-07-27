from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from core.automation.content_signals import (
    CONTENT_SIGNALS_URL,
    ContentSignalsError,
    EasyFarmContentSignalsClient,
)


NOW = datetime(2026, 7, 27, 6, 0, tzinfo=timezone.utc)
TOKEN = "e" * 64
PROMOTION_URL = "https://x.com/squidkorea/status/123456789"
PROMOTION_CANDIDATE_ID = hashlib.sha256(
    (
        "content-performance-v1\n"
        f"squid\nx\n{PROMOTION_URL}"
    ).encode("utf-8")
).hexdigest()


def response_body(**overrides):
    start = NOW - timedelta(days=7)
    body = {
        "ok": True,
        "schema_version": "1.1",
        "generated_at": "2026-07-27T06:00:00Z",
        "client_id": "squid",
        "window": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": NOW.isoformat().replace("+00:00", "Z"),
            "days": 7,
            "timezone": "Asia/Seoul",
        },
        "freshness": {
            "community": {
                "as_of": "2026-07-27T05:30:00Z",
                "stale": False,
                "stale_after_hours": 48,
                "basis": "metrics_event",
            },
            "telegram_content": {
                "as_of": "2026-07-27T05:40:00Z",
                "stale": False,
                "stale_after_hours": 24,
                "basis": "metrics_update",
            },
            "local_x": {
                "as_of": "2026-07-27T05:50:00Z",
                "stale": False,
                "stale_after_hours": 24,
                "basis": "metrics_fetch",
            },
        },
        "availability": {
            "community": "ok",
            "telegram_content": "ok",
            "local_x": "ok",
        },
        "community": {
            "method": {
                "aggregation": "content_signals_community-v1-explicit-room",
                "sentiment": "aggregate-tone-balance-v1",
            },
            "suppressed": False,
            "sample_size": {
                "meets_threshold": True,
                "meaningful_messages": 20,
                "active_users": 5,
                "tone_total": 20,
                "minimum_meaningful_messages": 20,
                "minimum_active_users": 5,
            },
            "sentiment": {
                "index": 60,
                "label": "안정",
                "tone": {
                    "positive": 8,
                    "question": 4,
                    "negative": 2,
                    "neutral": 6,
                },
                "method": "aggregate-tone-balance-v1",
            },
            "topics": [{
                "label": "bridge",
                "mentions": 10,
                "distinct_users": 5,
            }],
        },
        "telegram_content": {
            "method": "telethon-channel-metrics-v1",
            "sample_size": 0,
            "by_kind": [],
            "metrics": {
                "views_total": 0,
                "views_avg": 0,
                "forwards_total": 0,
                "reactions_total": 0,
                "clicks_total": 0,
            },
            "top_posts": [],
            "top_links": [],
            "daily": [],
        },
        "local_x": {
            "method": "typefully-analytics-v2",
            "sample_size": {
                "returned": 0,
                "total": 0,
                "truncated": False,
            },
            "metrics": {
                "impressions": 0,
                "engagement_total": 0,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "quotes": 0,
                "saves": 0,
                "link_clicks": 0,
                "link_clicks_available_posts": 0,
                "engagement_rate_pct": 0,
            },
            "followers": {
                "current": None,
                "delta": None,
                "as_of": None,
            },
            "by_type": [],
            "top_posts": [],
        },
        "demand_terms": [{
            "term": "bridge",
            "weight": 0.9,
            "sources": ["community"],
        }],
        "demand_terms_meta": {
            "method": "deterministic-aggregate-public-v1",
            "max_items": 20,
            "stale_sources_excluded": True,
            "factual_evidence": False,
            "translation": False,
        },
        "promotion_candidates": [{
            "candidate_id": PROMOTION_CANDIDATE_ID,
            "channel": "x",
            "source_url": PROMOTION_URL,
            "published_at": "2026-07-26T12:00:00Z",
            "score": 0.875,
            "reach_percentile": 0.9,
            "interaction_percentile": 0.8,
            "community_match_count": 3,
            "cohort_size": 12,
            "observation_age_hours": 18,
            "recommended_formats": ["article", "tutorial"],
            "reason_codes": [
                "high_reach",
                "high_interaction",
                "community_alignment",
                "tutorial_learning_signal",
            ],
        }],
        "promotion_candidates_meta": {
            "policy_version": "content-performance-v1",
            "method": "same-client-channel-percentile-v1",
            "max_items": 5,
            "score_threshold": 0.7,
            "minimum_cohort_size": 5,
            "minimum_post_age_hours": 12,
            "maximum_post_age_hours": 72,
            "maximum_freshness_hours": 24,
            "factual_evidence": False,
            "auto_publish": False,
        },
        "privacy": {
            "raw_messages_included": False,
            "user_identifiers_included": False,
        },
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_client_posts_exact_contract_and_returns_bounded_signals():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == CONTENT_SIGNALS_URL
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["accept"] == "application/json"
        assert json.loads(request.content) == {
            "schema_version": "1.1",
            "client_id": "squid",
            "start": "2026-07-20T06:00:00Z",
            "end": "2026-07-27T06:00:00Z",
        }
        return httpx.Response(200, json=response_body())

    client = EasyFarmContentSignalsClient(
        endpoint_url=CONTENT_SIGNALS_URL,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )

    snapshot = await client.fetch(client_id="squid", now=NOW)

    assert snapshot.client_id == "squid"
    assert [(item.term, item.weight, item.sources) for item in snapshot.demand_terms] == [
        ("bridge", 0.9, ("community",)),
    ]
    assert snapshot.promotion_policy_version == "content-performance-v1"
    assert len(snapshot.promotion_candidates) == 1
    assert snapshot.promotion_candidates[0].source_url == (
        PROMOTION_URL
    )
    assert snapshot.promotion_candidates[0].recommended_formats == (
        "article",
        "tutorial",
    )
    assert not hasattr(snapshot, "community")
    assert not hasattr(snapshot, "telegram_content")
    assert not hasattr(snapshot, "local_x")


@pytest.mark.asyncio
async def test_optional_aggregate_section_can_fail_closed_without_stopping_terms():
    body = response_body()
    body["availability"]["telegram_content"] = "unavailable"
    body["telegram_content"] = None
    body["freshness"]["telegram_content"].update(as_of=None, stale=True)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    snapshot = await EasyFarmContentSignalsClient(
        endpoint_url=CONTENT_SIGNALS_URL,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    ).fetch(client_id="squid", now=NOW)

    assert snapshot.demand_terms[0].sources == ("community",)


@pytest.mark.asyncio
async def test_telegram_candidate_requires_the_client_local_public_channel():
    body = response_body()
    source_url = "https://t.me/squid_kor_update/123456"
    body["promotion_candidates"][0].update(
        candidate_id=hashlib.sha256(
            (
                "content-performance-v1\n"
                f"squid\ntelegram\n{source_url}"
            ).encode("utf-8")
        ).hexdigest(),
        channel="telegram",
        source_url=source_url,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    snapshot = await EasyFarmContentSignalsClient(
        endpoint_url=CONTENT_SIGNALS_URL,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    ).fetch(client_id="squid", now=NOW)

    assert snapshot.promotion_candidates[0].channel == "telegram"
    assert snapshot.promotion_candidates[0].source_url == source_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutator, code",
    [
        (
            lambda body: body["privacy"].update(raw_messages_included=True),
            "content_signals_privacy_violation",
        ),
        (
            lambda body: body["demand_terms"][0].update(weight=1.1),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["demand_terms"][0].update(term="@private-user"),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["demand_terms"][0].update(
                term="1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["demand_terms"][0].update(
                term="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["demand_terms"][0].update(term="a" * 64),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["demand_terms"][0].update(term="O0" * 16),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["demand_terms"][0].update(term=" bridge "),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["demand_terms"][0].update(
                sources=["community", "community"]
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["demand_terms"][0].update(sources=[]),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["community"].update(email="private@example.com"),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["telegram_content"]["metrics"].update(
                message_text="private message"
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["local_x"]["sample_size"].update(returned=501),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["promotion_candidates_meta"].update(
                auto_publish=True
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["promotion_candidates"][0].update(
                source_url="https://x.com/SquidKorea/status/123456789"
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["promotion_candidates"][0].update(
                source_url="https://x.com/squidrouter/status/123456789"
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["promotion_candidates"][0].update(score=0.99),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["promotion_candidates"][0].update(
                candidate_id="f" * 64
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["promotion_candidates"][0].update(
                recommended_formats=["article"],
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["promotion_candidates"][0].update(
                community_match_count=4,
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["freshness"]["community"].update(stale=True),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body["freshness"]["community"].update(
                as_of="2026-07-24T05:30:00Z",
                stale=False,
            ),
            "content_signals_invalid_response",
        ),
        (
            lambda body: body.update(generated_at="2026-07-25T06:00:00Z"),
            "content_signals_stale_response",
        ),
        (
            lambda body: body.update(unexpected="field"),
            "content_signals_invalid_response",
        ),
    ],
)
async def test_client_rejects_invalid_unbounded_stale_or_private_responses(
    mutator,
    code,
):
    body = response_body()
    mutator(body)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = EasyFarmContentSignalsClient(
        endpoint_url=CONTENT_SIGNALS_URL,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ContentSignalsError) as caught:
        await client.fetch(client_id="squid", now=NOW)
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_provider_error_body_and_token_never_escape_the_client():
    leaked = "provider-debug-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": leaked, "authorization": f"Bearer {TOKEN}"},
        )

    client = EasyFarmContentSignalsClient(
        endpoint_url=CONTENT_SIGNALS_URL,
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ContentSignalsError) as caught:
        await client.fetch(client_id="squid", now=NOW)

    assert caught.value.code == "content_signals_request_rejected"
    assert leaked not in str(caught.value)
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        CONTENT_SIGNALS_URL + "/",
        CONTENT_SIGNALS_URL + "?client_id=squid",
        "http://jlxbywqofrltyttklcqy.supabase.co/functions/v1/content-signals-api",
        "https://jlxbywqofrltyttklcqy.supabase.co.evil.test/functions/v1/content-signals-api",
    ],
)
def test_client_rejects_every_endpoint_outside_the_exact_allowlist(url):
    with pytest.raises(ValueError, match="allowlist"):
        EasyFarmContentSignalsClient(endpoint_url=url, token=TOKEN)
