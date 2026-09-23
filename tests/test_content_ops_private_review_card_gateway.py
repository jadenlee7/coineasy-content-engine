"""Synthetic HTTP only: no production gateway or Telegram calls."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from core.content_ops.private_review_card_gateway import (
    ButtonCanaryGateway, PrivateCardGatewayError,
)
from core.content_ops.worker import APP_ORIGIN, GATEWAY_PATH


SHA = "a" * 40
PACKET = "b" * 64
TOKEN = "test_only_button_card_gateway_token_123456"
VERSION = "44444444-4444-4444-8444-444444444444"
CLAIM_TOKEN = "22222222-2222-4222-8222-222222222222"
OUTBOX = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
SCOPE = {"mode": "canary", "content_version_id": VERSION,
         "packet_mode": "button_card_v1"}


def claim():
    return {"outbox_id": OUTBOX, "claim_token": CLAIM_TOKEN,
            "client_id": "yellow", "kst_date": "2026-09-23",
            "content_item_id": "33333333-3333-4333-8333-333333333333",
            "content_version_id": VERSION,
            "source_item_id": "55555555-5555-4555-8555-555555555555",
            "generate_job_id": "66666666-6666-4666-8666-666666666666",
            "banner_sha256": "c" * 64, "title": "검수용 제목",
            "telegram_copy": "검수용 Telegram 전문", "x_copy": "검수용 X 전문",
            "source_url": "https://x.com/yellow/status/123456789",
            "source_published_at": "2026-09-23T08:00:00Z"}


def receipt(key, value):
    return {"ok": True, "release_sha": SHA, "scope": SCOPE, key: value}


def gateway(responses, *, enabled=True):
    calls = []

    def handler(request):
        calls.append(request)
        if not responses:
            raise AssertionError("unexpected gateway request")
        response = responses.pop(0)
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

    result = ButtonCanaryGateway(origin=APP_ORIGIN, gateway_token=TOKEN,
        release_sha=SHA, content_version_id=VERSION, enabled=enabled,
        transport=httpx.MockTransport(handler), clock=lambda: NOW)
    return result, calls


def test_default_off_and_invalid_scope_do_no_network_io():
    disabled, calls = gateway([], enabled=False)
    with pytest.raises(PrivateCardGatewayError, match="private_card_gateway_disabled"):
        asyncio.run(disabled.reconcile())
    assert calls == []
    for override in ({"origin": "https://example.invalid"},
                     {"release_sha": "short"},
                     {"content_version_id": "bad"},
                     {"gateway_token": "bad"}):
        values = {"origin": APP_ORIGIN, "gateway_token": TOKEN,
                  "release_sha": SHA, "content_version_id": VERSION}
        values.update(override)
        with pytest.raises(PrivateCardGatewayError, match="configuration_invalid"):
            ButtonCanaryGateway(**values)


def test_exact_one_shot_reconcile_claim_begin_has_no_finish_or_publish():
    client, calls = gateway([receipt("queued", 1), receipt("claim", claim()),
                             receipt("accepted", True)])

    async def run():
        assert await client.reconcile() == 1
        candidate = await client.claim(CLAIM_TOKEN)
        assert candidate.content_version_id == VERSION
        assert await client.begin(candidate, PACKET) == {
            "status": "begun", "outbox_id": OUTBOX,
            "execution_authorized": False}
        with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
            await client.begin(candidate, PACKET)
        with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
            await client.claim(CLAIM_TOKEN)

    asyncio.run(run())
    assert len(calls) == 3
    for request in calls:
        assert str(request.url) == APP_ORIGIN + GATEWAY_PATH
        assert request.headers["x-content-ops-mode"] == "canary"
        assert request.headers["x-content-ops-version-id"] == VERSION
        assert request.headers["x-content-ops-packet-mode"] == "button_card_v1"
        assert request.headers["x-content-ops-expected-release-sha"] == SHA
    assert [json.loads(request.read())["action"] for request in calls] == [
        "reconcile", "claim", "begin"]
    assert not hasattr(client, "finish")
    assert not hasattr(client, "publish")


@pytest.mark.parametrize("bad", [
    {"queued": 2},
    {"queued": True},
    {"queued": 1, "provider_response": "must not leak"},
    {"release_sha": "b" * 40},
    {"scope": {"mode": "daily", "content_version_id": None}},
])
def test_invalid_reconcile_receipt_stops_without_retry_or_leak(bad):
    response = receipt("queued", 1)
    response.update(bad)
    client, calls = gateway([response])
    with pytest.raises(PrivateCardGatewayError, match="outcome_unknown"):
        asyncio.run(client.reconcile())
    assert len(calls) == 1


@pytest.mark.parametrize("bad", [
    {"content_version_id": "33333333-3333-4333-8333-333333333333"},
    {"claim_token": "77777777-7777-4777-8777-777777777777"},
    {"source_published_at": "2026-09-22T08:00:00Z"},
    {"source_url": "https://x.com/other/status/123456789"},
    {"provider_response": "must not leak"},
])
def test_bad_claim_cannot_begin(bad):
    projected = claim()
    projected.update(bad)
    client, calls = gateway([receipt("queued", 1), receipt("claim", projected)])

    async def run():
        assert await client.reconcile() == 1
        with pytest.raises(PrivateCardGatewayError, match="outcome_unknown"):
            await client.claim(CLAIM_TOKEN)
        with pytest.raises(PrivateCardGatewayError, match="arguments_invalid"):
            await client.begin(None, PACKET)

    asyncio.run(run())
    assert len(calls) == 2


def test_lost_begin_ack_is_terminal_and_never_retries():
    client, calls = gateway([receipt("queued", 1), receipt("claim", claim()),
                             httpx.Response(503, text="sensitive provider response")])

    async def run():
        await client.reconcile()
        candidate = await client.claim(CLAIM_TOKEN)
        with pytest.raises(PrivateCardGatewayError, match="outcome_unknown"):
            await client.begin(candidate, PACKET)
        with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
            await client.begin(candidate, PACKET)

    asyncio.run(run())
    assert len(calls) == 3


def test_no_claim_or_denied_begin_cannot_create_another_attempt():
    empty, empty_calls = gateway([receipt("queued", 0), receipt("claim", None)])

    async def no_claim():
        assert await empty.reconcile() == 0
        assert await empty.claim(CLAIM_TOKEN) is None
        with pytest.raises(PrivateCardGatewayError, match="arguments_invalid"):
            await empty.begin(None, PACKET)

    asyncio.run(no_claim())
    assert len(empty_calls) == 2
    denied, denied_calls = gateway([receipt("queued", 1), receipt("claim", claim()),
                                    receipt("accepted", False)])

    async def denied_begin():
        await denied.reconcile()
        candidate = await denied.claim(CLAIM_TOKEN)
        with pytest.raises(PrivateCardGatewayError, match="begin_denied"):
            await denied.begin(candidate, PACKET)
        with pytest.raises(PrivateCardGatewayError, match="replay_denied"):
            await denied.begin(candidate, PACKET)

    asyncio.run(denied_begin())
    assert len(denied_calls) == 3
