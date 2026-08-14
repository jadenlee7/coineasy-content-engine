from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from core.grok_qa.models import (
    GrokQaDeliveryResult,
    GrokQaModelResult,
    GrokQaVerdict,
    GrokQaWorkClaim,
)
from core.grok_qa.worker import GrokQaWorker, verdict_sha256
from core.grok_qa.xai_client import PROMPT_VERSION, XaiQaError


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"
SOURCE = "https://x.com/squidrouter/status/2083266484789514640"


def claim(**overrides) -> GrokQaWorkClaim:
    values = {
        "content_item_id": "11111111-1111-4111-8111-111111111111",
        "content_version_id": "22222222-2222-4222-8222-222222222222",
        "client_id": "squid",
        "content_kind": "daily_news",
        "title": "Squid 한국 공지",
        "source_url": SOURCE,
        "source_published_at": datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
        "review_text": "한국 GTM 초안과 자동 QA 결과를 검토해 주세요.",
        "image_png": PNG,
        "image_sha256": hashlib.sha256(PNG).hexdigest(),
        "attempt": 1,
        "max_attempts": 3,
    }
    values.update(overrides)
    return GrokQaWorkClaim(**values)


def result(item: GrokQaWorkClaim) -> GrokQaModelResult:
    verdict = GrokQaVerdict.model_validate({
        "decision": "PASS",
        "summary": "공식 원문과 브랜드 배너가 모두 일치합니다.",
        "fact_check": {
            "status": "PASS",
            "checks": ["공식 원문을 직접 확인함"],
            "source_urls": [SOURCE],
        },
        "brand_check": {
            "status": "PASS",
            "checks": ["Squid 브랜드 표현과 일치함"],
        },
        "issues": [],
        "next_action": "ready_for_human_approval",
    })
    return GrokQaModelResult(
        provider_response_id="resp_abc123",
        model="grok-4.5",
        cost_in_usd_ticks=100,
        input_sha256=item.input_sha256,
        x_search_performed=True,
        x_search_citations=[SOURCE],
        x_search_calls=1,
        verdict=verdict,
    )


class FakeBroker:
    def __init__(self, item: GrokQaWorkClaim | None):
        self.item = item
        self.claim_args = None
        self.deliver_args = None
        self.fail_args = None
        self.raise_on_delivery = False
        self.raise_on_mark = False
        self.mark_args = None

    async def claim(self, **kwargs):
        self.claim_args = kwargs
        return self.item

    async def deliver(self, **kwargs):
        self.deliver_args = kwargs
        if self.raise_on_delivery:
            raise RuntimeError("private broker body must not leak")
        return GrokQaDeliveryResult(
            accepted=True,
            duplicate=False,
            delivery_status="sent",
        )

    async def mark_provider_attempt(self, **kwargs):
        self.mark_args = kwargs
        if self.raise_on_mark:
            raise RuntimeError("lost provider fence response")
        return True

    async def fail(self, **kwargs):
        self.fail_args = kwargs


class FakeProvider:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = 0

    async def review(self, _claim):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value


@pytest.mark.asyncio
async def test_worker_claims_reviews_and_delivers_exact_hashed_verdict():
    item = claim()
    model_result = result(item)
    broker = FakeBroker(item)
    provider = FakeProvider(model_result)
    worker = GrokQaWorker(
        broker=broker,
        provider=provider,
        worker_id="grok-qa:test-worker",
    )

    run = await worker.run_once()

    assert run.status == "delivered"
    assert broker.claim_args == {
        "worker_id": "grok-qa:test-worker",
        "lease_seconds": 300,
        "allowed_clients": ("squid",),
        "canary_content_version_id": None,
    }
    assert broker.mark_args == {
        "claim": item,
        "worker_id": "grok-qa:test-worker",
        "input_sha256": item.input_sha256,
        "banner_sha256": item.image_sha256,
        "model": "grok-4.5",
        "prompt_version": PROMPT_VERSION,
    }
    assert broker.deliver_args == {
        "action": "deliver",
        "content_item_id": item.content_item_id,
        "content_version_id": item.content_version_id,
        "worker_id": "grok-qa:test-worker",
        "verdict": model_result.verdict.model_dump(
            mode="json", exclude_none=True
        ),
        "verdict_sha256": verdict_sha256(model_result),
        "model": "grok-4.5",
        "prompt_version": PROMPT_VERSION,
        "provider_response_id": "resp_abc123",
        "input_sha256": item.input_sha256,
        "banner_sha256": item.image_sha256,
        "cost_in_usd_ticks": 100,
        "x_search_citations": [SOURCE],
        "x_search_calls": 1,
    }


@pytest.mark.asyncio
async def test_provider_failure_after_fence_is_terminal_provider_unknown():
    item = claim(attempt=1, max_attempts=2)
    broker = FakeBroker(item)
    provider = FakeProvider(error=XaiQaError(
        "xai_qa_unavailable", retryable=True
    ))
    run = await GrokQaWorker(
        broker=broker,
        provider=provider,
        worker_id="grok-qa:test-worker",
    ).run_once()
    assert run.status == "failed"
    assert run.error == "xai_qa_unavailable"
    assert broker.fail_args["error_code"] == "grok_qa_provider_unknown"
    assert broker.fail_args["retryable"] is False


@pytest.mark.asyncio
async def test_lost_provider_fence_response_never_calls_xai():
    item = claim()
    broker = FakeBroker(item)
    broker.raise_on_mark = True
    provider = FakeProvider(result(item))
    run = await GrokQaWorker(
        broker=broker,
        provider=provider,
        worker_id="grok-qa:test-worker",
    ).run_once()
    assert run.status == "provider_unknown"
    assert provider.calls == 0
    assert broker.deliver_args is None
    assert broker.fail_args is None


@pytest.mark.asyncio
async def test_staged_result_replay_skips_provider_attempt_and_xai():
    original = claim()
    model_result = result(original)
    item = claim(
        provider_call_required=False,
        staged_result=model_result,
        staged_verdict_sha256=verdict_sha256(model_result),
        staged_prompt_version=PROMPT_VERSION,
    )
    broker = FakeBroker(item)
    provider = FakeProvider(error=AssertionError("xAI must not be called"))
    run = await GrokQaWorker(
        broker=broker,
        provider=provider,
        worker_id="grok-qa:test-worker",
    ).run_once()
    assert run.status == "delivered"
    assert broker.mark_args is None
    assert provider.calls == 0
    assert broker.deliver_args["verdict_sha256"] == verdict_sha256(model_result)


@pytest.mark.asyncio
async def test_delivery_unknown_does_not_call_provider_a_second_time():
    item = claim()
    broker = FakeBroker(item)
    broker.raise_on_delivery = True
    provider = FakeProvider(result(item))
    run = await GrokQaWorker(
        broker=broker,
        provider=provider,
        worker_id="grok-qa:test-worker",
    ).run_once()
    assert run.status == "delivery_unknown"
    assert provider.calls == 1
    assert broker.fail_args is None


@pytest.mark.asyncio
async def test_idle_claim_never_calls_provider_or_delivery():
    broker = FakeBroker(None)
    provider = FakeProvider()
    run = await GrokQaWorker(
        broker=broker,
        provider=provider,
        worker_id="grok-qa:test-worker",
    ).run_once()
    assert run.status == "idle"
    assert provider.calls == 0
    assert broker.mark_args is None
    assert broker.deliver_args is None


@pytest.mark.asyncio
async def test_worker_passes_exact_canary_scope_to_claim():
    item = claim()
    broker = FakeBroker(item)
    provider = FakeProvider(result(item))
    worker = GrokQaWorker(
        broker=broker,
        provider=provider,
        canary_content_version_id=item.content_version_id,
        worker_id="grok-qa:canary-worker",
    )

    run = await worker.run_once()

    assert run.status == "delivered"
    assert broker.claim_args["canary_content_version_id"] == (
        item.content_version_id
    )
