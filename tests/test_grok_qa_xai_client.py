from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import httpx
import pytest

from core.grok_qa.models import GrokQaWorkClaim
from core.grok_qa.xai_client import XAI_RESPONSES_URL, XaiQaClient, XaiQaError


KEY = "xai-" + "a" * 40
PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"
SOURCE = "https://x.com/squidrouter/status/2083266484789514640"


def claim() -> GrokQaWorkClaim:
    return GrokQaWorkClaim(
        content_item_id="11111111-1111-4111-8111-111111111111",
        content_version_id="22222222-2222-4222-8222-222222222222",
        client_id="squid",
        content_kind="daily_news",
        title="Squid 한국 공지",
        source_url=SOURCE,
        source_published_at=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
        review_text="한국 GTM 초안과 자동 QA 결과를 검토해 주세요.",
        image_png=PNG,
        image_sha256=hashlib.sha256(PNG).hexdigest(),
        attempt=1,
        max_attempts=3,
    )


def verdict(source: str = SOURCE) -> dict[str, object]:
    return {
        "decision": "PASS",
        "summary": "공식 원문과 브랜드 배너가 모두 일치합니다.",
        "fact_check": {
            "status": "PASS",
            "checks": ["공식 원문을 직접 확인함"],
            "source_urls": [source],
        },
        "brand_check": {
            "status": "PASS",
            "checks": ["Squid 브랜드 표현과 일치함"],
        },
        "issues": [],
        "next_action": "ready_for_human_approval",
    }


def response_body(**overrides) -> dict[str, object]:
    value = {
        "id": "resp_abc123",
        "model": "grok-4.5",
        "status": "completed",
        "citations": ["https://x.com/i/status/2083266484789514640"],
        "output": [
            {"type": "x_search_call", "id": "xsearch_123"},
            {
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "text": json.dumps(verdict(), ensure_ascii=False),
                    "annotations": [{
                        "type": "url_citation",
                        "url": SOURCE,
                    }],
                }],
            },
        ],
        "usage": {
            "cost_in_usd_ticks": 100_000_000,
            "num_server_side_tools_used": 1,
        },
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_request_is_fixed_private_bounded_structured_x_search_with_png():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=response_body())

    client = XaiQaClient(
        api_key=KEY,
        max_turns=2,
        max_cost_in_usd_ticks=200_000_000,
        transport=httpx.MockTransport(handler),
    )
    result = await client.review(claim())
    body = captured["body"]

    assert captured["url"] == XAI_RESPONSES_URL
    assert captured["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "grok-4.5"
    assert body["store"] is False
    assert body["include"] == ["no_inline_citations"]
    assert body["tool_choice"] == "required"
    assert body["max_turns"] == 2
    assert body["tools"] == [{
        "type": "x_search",
        "allowed_x_handles": ["SquidRouter"],
        "from_date": "2026-08-11",
        "to_date": "2026-08-13",
        "enable_image_understanding": True,
    }]
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    source_urls_schema = body["text"]["format"]["schema"]["properties"][
        "fact_check"
    ]["properties"]["source_urls"]
    assert source_urls_schema == {
        "type": "array",
        "minItems": 1,
        "maxItems": 1,
        "items": {"type": "string", "const": SOURCE},
    }
    evidence_url_schema = body["text"]["format"]["schema"]["properties"][
        "issues"
    ]["items"]["properties"]["evidence_url"]
    assert evidence_url_schema == {
        "type": "string",
        "const": SOURCE,
    }
    prompt = body["input"][0]["content"][0]["text"]
    assert prompt.startswith(f"Use X Search now to retrieve and cite exactly {SOURCE}")
    assert '"content_item_id"' not in prompt
    assert '"content_version_id"' not in prompt
    assert body["input"][0]["content"][1]["image_url"].startswith(
        "data:image/png;base64,"
    )
    assert result.cost_in_usd_ticks == 100_000_000
    assert result.input_sha256 == claim().input_sha256
    assert result.x_search_citations == [
        "https://x.com/i/status/2083266484789514640",
        SOURCE,
    ]
    assert result.x_search_calls == 1


@pytest.mark.asyncio
async def test_completed_provider_x_tool_call_is_accepted_with_usage_and_exact_citation():
    body = response_body(output=[
        {
            "type": "custom_tool_call",
            "name": "x_thread_fetch",
            "status": "completed",
        },
        {
            "type": "message",
            "content": [{
                "type": "output_text",
                "text": json.dumps(verdict()),
                "annotations": [{
                    "type": "url_citation",
                    "url": "https://x.com/i/status/2083266484789514640",
                }],
            }],
        },
    ])
    client = XaiQaClient(
        api_key=KEY,
        max_cost_in_usd_ticks=200_000_000,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=body)
        ),
    )

    result = await client.review(claim())

    assert result.x_search_calls == 1
    assert result.x_search_citations == [
        "https://x.com/i/status/2083266484789514640",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("body,code", [
    (
        response_body(output=[{
            "type": "message",
            "content": [{"type": "output_text", "text": json.dumps(verdict())}],
        }]),
        "xai_qa_x_search_missing",
    ),
    (
        response_body(
            citations=[],
            output=[
                {"type": "reasoning"},
                {
                    "type": "custom_tool_call",
                    "name": "x_thread_fetch",
                    "status": "failed",
                },
                {"type": "message", "content": [{
                    "type": "output_text",
                    "text": json.dumps(verdict()),
                }]},
            ],
            usage={
                "cost_in_usd_ticks": 100_000_000,
                "num_server_side_tools_used": 0,
            },
        ),
        "xai_qa_x_search_failed",
    ),
    (
        response_body(
            output=[
                {
                    "type": "custom_tool_call",
                    "name": "x_thread_fetch",
                    "status": "completed",
                },
                {"type": "message", "content": [{
                    "type": "output_text",
                    "text": json.dumps(verdict()),
                }]},
            ],
            usage={
                "cost_in_usd_ticks": 100_000_000,
                "num_server_side_tools_used": 0,
            },
        ),
        "xai_qa_x_search_missing",
    ),
    (
        response_body(output=[
            {
                "type": "custom_tool_call",
                "name": "untrusted_tool_name",
                "status": "completed",
            },
            {"type": "message", "content": [{
                "type": "output_text",
                "text": json.dumps(verdict()),
            }]},
        ]),
        "xai_qa_x_search_missing",
    ),
    (
        response_body(citations=[
            "https://x.com/i/status/2083266484789514641"
        ], output=[
            {"type": "x_search_call"},
            {"type": "message", "content": [{
                "type": "output_text",
                "text": json.dumps(verdict()),
                "annotations": [],
            }]},
        ]),
        "xai_qa_citation_outside_source_boundary",
    ),
    (
        response_body(citations=[
            "https://x.com/i/status/2083266484789514640",
            "https://x.com/squidrouter/status/2083266484789514641",
        ]),
        "xai_qa_citation_outside_source_boundary",
    ),
    (
        response_body(usage={
            "cost_in_usd_ticks": 200_000_001,
            "num_server_side_tools_used": 1,
        }),
        "xai_qa_cost_cap_exceeded",
    ),
])
async def test_response_fails_closed_without_search_source_or_cost_proof(
    body: dict[str, object],
    code: str,
):
    client = XaiQaClient(
        api_key=KEY,
        max_cost_in_usd_ticks=200_000_000,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=body)
        ),
    )
    with pytest.raises(XaiQaError, match=code) as caught:
        await client.review(claim())
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_provider_error_body_and_transport_details_are_not_exposed():
    provider_secret = "provider leaked private prompt"
    client = XaiQaClient(
        api_key=KEY,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(500, text=provider_secret)
        ),
    )
    with pytest.raises(XaiQaError) as caught:
        await client.review(claim())
    assert caught.value.code == "xai_qa_request_failed"
    assert provider_secret not in str(caught.value)
    assert caught.value.retryable is True


def test_client_rejects_arbitrary_model_and_unbounded_controls():
    with pytest.raises(ValueError, match="configuration"):
        XaiQaClient(api_key=KEY, model="grok-beta")
    with pytest.raises(ValueError, match="configuration"):
        XaiQaClient(api_key=KEY, max_turns=10)
