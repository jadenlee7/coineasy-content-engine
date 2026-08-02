from __future__ import annotations

import json

import httpx
import pytest

from core.automation.generation_client import (
    GenerationRequestError,
    StudioGenerationClient,
)
from core.automation.models import StyleReference


REQUEST_ID = "11111111-1111-4111-8111-111111111111"
VERSION_ID = "22222222-2222-4222-8222-222222222222"
ASSET_ID = "33333333-3333-4333-8333-333333333333"
AUTOMATION_TOKEN = "automation-token-that-is-longer-than-32-bytes"
SOURCE_IMAGE_URL = "https://pbs.twimg.com/media/source.jpg?name=orig"
SOURCE_IMAGE_SHA256 = "a" * 64


@pytest.mark.asyncio
async def test_daily_news_uses_review_generation_route_and_classic_by_default():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={
            "content_item_id": REQUEST_ID,
            "content_version_id": VERSION_ID,
            "asset_ids": [ASSET_ID],
            "storage_backend": "supabase",
            "reused": False,
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    result = await client.generate(
        client_id="squid",
        content_kind="daily_news",
        request_id=REQUEST_ID,
        source_content="Squid official product update",
        source_url="https://x.com/SquidRouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg",
    )

    request = captured["request"]
    assert request.url.path == "/api/news-card/squid"
    assert "/publish/" not in request.url.path
    assert request.headers["idempotency-key"] == REQUEST_ID
    assert request.headers["x-studio-automation-key"] == AUTOMATION_TOKEN
    body = json.loads(request.content)
    assert body["template_style"] == "classic"
    assert body["source_image_url"] == ""
    assert body["mock_mode"] is False
    assert result.asset_ids == (ASSET_ID,)


@pytest.mark.asyncio
async def test_squid_remix_forwards_only_the_official_x_image():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "content_item_id": REQUEST_ID,
            "content_version_id": VERSION_ID,
            "asset_ids": [ASSET_ID],
            "storage_backend": "supabase",
            "reused": False,
            "requested_template_style": "remix",
            "template_style": "remix",
            "source_image_used": True,
            "source_image_url": SOURCE_IMAGE_URL,
            "source_media_status": "present",
            "source_image_sha256": SOURCE_IMAGE_SHA256,
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    await client.generate(
        client_id="squid",
        content_kind="daily_news",
        request_id=REQUEST_ID,
        source_content="Squid official product update",
        source_url="https://x.com/SquidRouter/status/123",
        source_image_url="https://pbs.twimg.com/media/source.jpg",
        template_style="remix",
    )

    assert captured["template_style"] == "remix"
    assert captured["source_image_url"] == SOURCE_IMAGE_URL


@pytest.mark.asyncio
async def test_squid_remix_rejects_a_result_without_the_pinned_source_proof():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "content_item_id": REQUEST_ID,
            "content_version_id": VERSION_ID,
            "asset_ids": [ASSET_ID],
            "storage_backend": "supabase",
            "reused": False,
            "requested_template_style": "remix",
            "template_style": "remix",
            "source_image_used": False,
            "source_image_url": SOURCE_IMAGE_URL,
            "source_media_status": "present",
            "source_image_sha256": SOURCE_IMAGE_SHA256,
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GenerationRequestError, match="studio_generation_invalid_response") as error:
        await client.generate(
            client_id="squid",
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="Squid official product update",
            source_url="https://x.com/SquidRouter/status/123",
            source_image_url=SOURCE_IMAGE_URL,
            template_style="remix",
        )
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_complete_note_can_use_article_route_without_assets():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/article/yellow"
        return httpx.Response(200, json={
            "content_item_id": REQUEST_ID,
            "content_version_id": VERSION_ID,
            "asset_ids": [],
            "storage_backend": "supabase",
            "reused": True,
        })

    client = StudioGenerationClient(
        base_url="http://localhost:8888",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    result = await client.generate(
        client_id="yellow",
        content_kind="article",
        request_id=REQUEST_ID,
        source_content="a" * 300,
        source_url="https://x.com/Yellow/status/123",
    )
    assert result.reused is True
    assert result.asset_ids == ()


@pytest.mark.asyncio
async def test_automation_forwards_bounded_style_pack_separately_from_source():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "content_item_id": REQUEST_ID,
            "content_version_id": VERSION_ID,
            "asset_ids": [ASSET_ID],
            "storage_backend": "supabase",
            "reused": False,
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    reference = StyleReference(
        source_item_id="44444444-4444-4444-8444-444444444444",
        source_url="https://x.com/SquidRouter/status/122",
        text="A prior official post used only for rhythm.",
        published_at="2026-07-21T00:00:00Z",
    )
    await client.generate(
        client_id="squid",
        content_kind="daily_news",
        request_id=REQUEST_ID,
        source_content="Squid official product update",
        source_url="https://x.com/SquidRouter/status/123",
        style_references=(reference,),
        style_reference_pack_hash="a" * 32,
    )

    assert captured["source_url"].endswith("/status/123")
    assert captured["style_references"] == [reference.generation_payload()]
    assert captured["style_reference_pack_hash"] == "a" * 32


def test_generation_target_and_token_are_fail_closed():
    with pytest.raises(ValueError, match="allowlist"):
        StudioGenerationClient(
            base_url="https://attacker.example",
            automation_token=AUTOMATION_TOKEN,
        )
    with pytest.raises(ValueError, match="32"):
        StudioGenerationClient(
            base_url="https://coineasy-newscard.netlify.app",
            automation_token="too-short",
        )


@pytest.mark.asyncio
async def test_generation_rejects_redirects_and_untracked_results():
    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(302, headers={"location": "https://attacker.example"})
        ),
    )
    with pytest.raises(GenerationRequestError) as error:
        await client.generate(
            client_id="yellow",
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="official update",
            source_url="https://x.com/Yellow/status/123",
        )
    assert error.value.retryable is False

    invalid = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={
            "content_item_id": "44444444-4444-4444-8444-444444444444",
            "content_version_id": VERSION_ID,
            "asset_ids": [ASSET_ID],
            "storage_backend": "supabase",
        })),
    )
    with pytest.raises(GenerationRequestError, match="studio_generation_invalid_response"):
        await invalid.generate(
            client_id="yellow",
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="official update",
            source_url="https://x.com/Yellow/status/123",
        )


@pytest.mark.asyncio
async def test_generation_rejects_noncanonical_source_url_before_network():
    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
    )
    with pytest.raises(ValueError, match="canonical X status URL"):
        await client.generate(
            client_id="yellow",
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="A sufficiently long official update.",
            source_url="https://x.com.evil.test/Yellow/status/123",
        )


@pytest.mark.asyncio
async def test_generation_does_not_expose_provider_error_text():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "raw source text: do not log me"})

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GenerationRequestError) as error:
        await client.generate(
            client_id="yellow",
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="A sufficiently long official update.",
            source_url="https://x.com/Yellow/status/123",
        )
    assert error.value.code == "studio_generation_failed"
