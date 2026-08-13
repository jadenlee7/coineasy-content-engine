from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

import httpx
import pytest

from core.grok_qa.broker import GROK_QA_DISPATCH_URL, HttpGrokQaBroker
from core.grok_qa.models import GrokQaVerdict, GrokQaWorkClaim


TOKEN = "dispatch-token-that-is-dedicated-and-long-enough"
ITEM = "11111111-1111-4111-8111-111111111111"
VERSION = "22222222-2222-4222-8222-222222222222"
SOURCE = "https://x.com/SquidRouter/status/2083266484789514640"
PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"


def package() -> dict[str, object]:
    return {
        "content_item_id": ITEM,
        "content_version_id": VERSION,
        "client_id": "squid",
        "content_kind": "daily_news",
        "title": "Squid 한국 공지",
        "status": "needs_review",
        "version_number": 1,
        "locale": "ko-KR",
        "generated_content": {"spec": {"headline": "한국 공지"}},
        "channel_copy": {"x": "한국 공지입니다."},
        "automated_qa": {
            "content_qa": {"status": "needs_review"},
            "brand_qa": {"status": "pass"},
            "fact_check": {"status": "review"},
        },
        "brand_contract": {
            "profile_version": "squid/brand-review@1",
            "identity": ["Short and playful"],
            "avoid": ["Corporate tone"],
            "x_rule": "Preserve the source rhythm.",
            "banner_rule": "Preserve the official composition.",
        },
        "source_urls": [SOURCE],
        "banner": {
            "available": True,
            "mime_type": "image/png",
            "width": 1080,
            "height": 1080,
            "sha256": hashlib.sha256(PNG).hexdigest(),
        },
        "review_rules": ["공식 원문을 확인한다."],
    }


def job(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "content_item_id": ITEM,
        "content_version_id": VERSION,
        "client_id": "squid",
        "content_kind": "daily_news",
        "source_item_id": "33333333-3333-4333-8333-333333333333",
        "source_url": SOURCE,
        "source_author_handle": "@SquidRouter",
        "source_published_at": "2026-08-13T08:00:00Z",
        "source_event_id": 17,
        "source_event_type": "official_x_review_draft_completed",
        "status": "claimed",
        "attempts": 1,
        "max_attempts": 3,
        "lease_expires_at": "2026-08-13T08:05:00Z",
        "verdict": None,
        "verdict_sha256": None,
        "model": None,
        "prompt_version": None,
        "input_sha256": None,
        "banner_sha256": None,
        "provider_attempt_started_at": None,
        "provider_response_id": None,
        "cost_in_usd_ticks": None,
        "x_search_citations": None,
        "x_search_calls": None,
        "provider_call_required": True,
        "claim_granted": True,
    }
    values.update(overrides)
    return values


def response_job(**overrides: object) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "mode": "official_x_grok_qa_dispatch",
        "workspace_id": "44444444-4444-4444-8444-444444444444",
        "job": job(**overrides),
        "review_package": package(),
        "banner_image": {
            "data": base64.b64encode(PNG).decode("ascii"),
            "mime_type": "image/png",
        },
    }


def claim() -> GrokQaWorkClaim:
    return GrokQaWorkClaim(
        content_item_id=ITEM,
        content_version_id=VERSION,
        client_id="squid",
        content_kind="daily_news",
        title="Squid 한국 공지",
        source_url=SOURCE,
        source_published_at=datetime(2026, 8, 13, 8, tzinfo=timezone.utc),
        review_text="한국 GTM 초안과 브랜드 검수 입력입니다.",
        image_png=PNG,
        image_sha256=hashlib.sha256(PNG).hexdigest(),
        attempt=1,
        max_attempts=3,
    )


def verdict() -> GrokQaVerdict:
    return GrokQaVerdict.model_validate({
        "decision": "PASS",
        "summary": "공식 원문과 Squid 브랜드가 모두 일치합니다.",
        "fact_check": {
            "status": "PASS",
            "checks": ["공식 X 원문을 확인함"],
            "source_urls": [SOURCE],
        },
        "brand_check": {
            "status": "PASS",
            "checks": ["Squid 톤과 배너 구성을 확인함"],
        },
        "issues": [],
        "next_action": "ready_for_human_approval",
    })


@pytest.mark.asyncio
async def test_claim_sends_atomic_client_allowlist_and_builds_hash_bound_input():
    bodies: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == GROK_QA_DISPATCH_URL
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=response_job())

    broker = HttpGrokQaBroker(
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    item = await broker.claim(
        worker_id="grok-qa:test-worker",
        lease_seconds=300,
        allowed_clients=("squid",),
        canary_content_version_id=None,
    )
    assert item is not None
    assert item.client_id == "squid"
    assert "squid/brand-review@1" in item.review_text
    assert bodies == [{
        "action": "claim",
        "worker_id": "grok-qa:test-worker",
        "lease_seconds": 300,
        "allowed_clients": ["squid"],
        "canary_content_version_id": None,
    }]


@pytest.mark.asyncio
async def test_canary_claim_is_bound_to_one_exact_content_version():
    bodies: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=response_job())

    broker = HttpGrokQaBroker(
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    claimed = await broker.claim(
        worker_id="grok-qa:canary-worker",
        lease_seconds=300,
        allowed_clients=("squid",),
        canary_content_version_id=VERSION,
    )
    assert claimed is not None
    assert bodies[0]["canary_content_version_id"] == VERSION


@pytest.mark.asyncio
async def test_canary_claim_rejects_a_different_returned_version():
    async def handler(_request: httpx.Request) -> httpx.Response:
        different = "55555555-5555-4555-8555-555555555555"
        payload = response_job(content_version_id=different)
        payload["review_package"] = {
            **package(),
            "content_version_id": different,
        }
        return httpx.Response(200, json=payload)

    broker = HttpGrokQaBroker(
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(Exception, match="identity_mismatch"):
        await broker.claim(
            worker_id="grok-qa:canary-worker",
            lease_seconds=300,
            allowed_clients=("squid",),
            canary_content_version_id=VERSION,
        )


@pytest.mark.asyncio
async def test_claim_rejects_provider_call_requirement_inconsistent_with_stage():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_job(
            provider_call_required=False,
        ))

    broker = HttpGrokQaBroker(
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(Exception, match="provider_call_state_invalid"):
        await broker.claim(
            worker_id="grok-qa:test-worker",
            lease_seconds=300,
            allowed_clients=("squid",),
            canary_content_version_id=None,
        )


@pytest.mark.asyncio
async def test_delivery_uses_database_authoritative_hash_after_staging():
    bodies: list[dict[str, object]] = []
    database_hash = "f" * 64

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body["action"] == "stage":
            return httpx.Response(200, json={
                "schema_version": "1.0",
                "content_item_id": ITEM,
                "content_version_id": VERSION,
                "status": "claimed",
                "verdict_sha256": database_hash,
                "model": "grok-4.5",
                "prompt_version": "official-x-grok-qa@1",
                "provider_response_id": "response_123456",
                "input_sha256": "a" * 64,
                "banner_sha256": hashlib.sha256(PNG).hexdigest(),
                "cost_in_usd_ticks": 10,
                "x_search_citations": [SOURCE],
                "x_search_calls": 1,
                "reused": False,
            })
        return httpx.Response(200, json={
            "schema_version": "1.0",
            "content_item_id": ITEM,
            "content_version_id": VERSION,
            "status": "sent",
            "reused": False,
            "accepted": True,
            "delivered": True,
            "duplicate": False,
            "delivery_status": "sent",
            "advisory_only": True,
            "public_publish": False,
        })

    broker = HttpGrokQaBroker(
        token=TOKEN,
        transport=httpx.MockTransport(handler),
    )
    delivered = await broker.deliver(
        action="deliver",
        content_item_id=ITEM,
        content_version_id=VERSION,
        worker_id="grok-qa:test-worker",
        verdict=verdict().model_dump(mode="json", exclude_none=True),
        verdict_sha256="a" * 64,
        model="grok-4.5",
        prompt_version="official-x-grok-qa@1",
        provider_response_id="response_123456",
        input_sha256="a" * 64,
        banner_sha256=hashlib.sha256(PNG).hexdigest(),
        cost_in_usd_ticks=10,
        x_search_citations=[SOURCE],
        x_search_calls=1,
    )
    assert delivered.delivery_status == "sent"
    assert [body["action"] for body in bodies] == ["stage", "deliver"]
    assert bodies[1]["verdict_sha256"] == database_hash


def test_broker_rejects_nonproduction_host_and_secret_reuse():
    with pytest.raises(ValueError):
        HttpGrokQaBroker(token=TOKEN, url="https://attacker.example/dispatch")
    with pytest.raises(ValueError):
        HttpGrokQaBroker.from_env({
            "GROK_QA_DISPATCH_TOKEN": TOKEN,
            "XAI_API_KEY": TOKEN,
        })
