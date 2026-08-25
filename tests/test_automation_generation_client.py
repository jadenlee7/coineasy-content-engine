from __future__ import annotations

import json

import httpx
import pytest

from core.automation.generation_client import (
    GenerationRequestError,
    StudioGenerationClient,
)
from core.automation.models import StyleReference
from core.squid_localization_diagnostics import (
    SQUID_LOCALIZATION_DIAGNOSTIC_VERSION,
)


REQUEST_ID = "11111111-1111-4111-8111-111111111111"
VERSION_ID = "22222222-2222-4222-8222-222222222222"
ASSET_ID = "33333333-3333-4333-8333-333333333333"
AUTOMATION_TOKEN = "automation-token-that-is-longer-than-32-bytes"
SOURCE_IMAGE_URL = "https://pbs.twimg.com/media/source.jpg?name=orig"
SOURCE_IMAGE_SHA256 = "a" * 64
RELEASE_SHA = "c" * 40


def generation_capabilities() -> dict:
    return {
        "schema_version": "1.0",
        "generation_contract": "double-fact-check@1",
        "generated_content_kinds": ["daily_news", "article", "tutorial"],
        "tutorial_claims_contract": "lessons@1",
        "netlify_release_sha": RELEASE_SHA,
    }


def capable_transport(handler) -> httpx.MockTransport:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/studio-capabilities":
            assert request.headers["x-studio-automation-key"] == AUTOMATION_TOKEN
            return httpx.Response(200, json=generation_capabilities())
        return handler(request)

    return httpx.MockTransport(wrapped)


def fact_check(content_kind: str) -> dict:
    return {
        "schema_version": "1.0",
        "policy_version": "double-fact-check@1",
        "content_kind": content_kind,
        "status": "review",
        "human_review_required": True,
        "input_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "checks": [
            {
                "id": "source_evidence",
                "status": "review",
                "label": "Source evidence",
                "detail": "Human verification is required.",
                "metrics": {},
            },
            {
                "id": "output_claims",
                "status": "pass",
                "label": "Output claims",
                "detail": "Mechanical anchors were recorded.",
                "metrics": {},
            },
        ],
    }


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
            "fact_check": fact_check("daily_news"),
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(handler),
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
@pytest.mark.parametrize(
    ("client_id", "source_url"),
    [
        ("squid", "https://x.com/SquidRouter/status/123"),
        ("yellow", "https://x.com/Yellow/status/2087177332670750834"),
        ("origintrail", "https://x.com/origin_trail/status/2078063452996661578"),
        ("babylon", "https://x.com/babylonlabs_io/status/2061801513488429361"),
    ],
)
async def test_source_dominant_remix_forwards_only_the_official_x_image(
    client_id,
    source_url,
):
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
            "fact_check": fact_check("daily_news"),
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(handler),
    )
    await client.generate(
        client_id=client_id,
        content_kind="daily_news",
        request_id=REQUEST_ID,
        source_content="Squid official product update",
        source_url=source_url,
        source_image_url="https://pbs.twimg.com/media/source.jpg",
        template_style="remix",
    )

    assert captured["template_style"] == "remix"
    assert captured["source_image_url"] == SOURCE_IMAGE_URL


@pytest.mark.asyncio
async def test_legacy_squid_localization_incomplete_keeps_bounded_502_retry():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/news-card/squid"
        return httpx.Response(502, json={
            "error": "squid_visual_localization_incomplete",
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(handler),
    )
    with pytest.raises(
        GenerationRequestError,
        match="squid_visual_localization_incomplete",
    ) as error:
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
    assert error.value.reason_code == ""
    assert error.value.action_code == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason_code", "retryable", "action_code"),
    [
        (
            "squid_placement_audit_unsafe",
            False,
            "prepare_approved_clean_plate",
        ),
        (
            "squid_placement_audit_unavailable",
            True,
            "retry_generation",
        ),
        (
            "squid_approved_clean_plate_unavailable",
            False,
            "repair_approved_clean_plate",
        ),
    ],
)
async def test_squid_localization_diagnostic_controls_bounded_retry(
    reason_code,
    retryable,
    action_code,
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/news-card/squid"
        return httpx.Response(502, json={
            "error": "squid_visual_localization_incomplete",
            "diagnostic_version": SQUID_LOCALIZATION_DIAGNOSTIC_VERSION,
            "reason_code": reason_code,
            # These remote hints are intentionally ignored. The client uses
            # its own allowlist and policy for durable automation decisions.
            "action_code": "publish_now",
            "retryable": not retryable,
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(handler),
    )
    with pytest.raises(GenerationRequestError) as error:
        await client.generate(
            client_id="squid",
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="Squid official product update",
            source_url="https://x.com/SquidRouter/status/123",
            source_image_url=SOURCE_IMAGE_URL,
            template_style="remix",
        )

    assert error.value.code == "squid_visual_localization_incomplete"
    assert error.value.reason_code == reason_code
    assert error.value.retryable is retryable
    assert error.value.action_code == action_code


@pytest.mark.asyncio
async def test_stored_squid_localization_failure_requires_a_new_nonretryable_request():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/news-card/squid"
        return httpx.Response(409, json={
            "error": "squid_visual_localization_regeneration_required",
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(handler),
    )
    with pytest.raises(
        GenerationRequestError,
        match="squid_visual_localization_regeneration_required",
    ) as error:
        await client.generate(
            client_id="squid",
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="Squid official product update",
            source_url="https://x.com/SquidRouter/status/123",
            source_image_url=SOURCE_IMAGE_URL,
            template_style="remix",
        )
    assert error.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_id", "source_url"),
    [
        ("squid", "https://x.com/SquidRouter/status/123"),
        ("yellow", "https://x.com/Yellow/status/2087177332670750834"),
        ("origintrail", "https://x.com/origin_trail/status/2078063452996661578"),
        ("babylon", "https://x.com/babylonlabs_io/status/2061801513488429361"),
    ],
)
async def test_source_dominant_remix_rejects_a_result_without_pinned_source_proof(
    client_id,
    source_url,
):
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
            "fact_check": fact_check("daily_news"),
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(handler),
    )
    with pytest.raises(GenerationRequestError, match="studio_generation_invalid_response") as error:
        await client.generate(
            client_id=client_id,
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="Squid official product update",
            source_url=source_url,
            source_image_url=SOURCE_IMAGE_URL,
            template_style="remix",
        )
    assert error.value.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize("report", [None, fact_check("article")])
async def test_automation_retries_when_generation_lacks_the_current_fact_check(report):
    def handler(_request: httpx.Request) -> httpx.Response:
        body = {
            "content_item_id": REQUEST_ID,
            "content_version_id": VERSION_ID,
            "asset_ids": [ASSET_ID],
            "storage_backend": "supabase",
            "reused": False,
        }
        if report is not None:
            body["fact_check"] = report
        return httpx.Response(200, json=body)

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(handler),
    )
    with pytest.raises(GenerationRequestError, match="studio_generation_invalid_response") as error:
        await client.generate(
            client_id="yellow",
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="A sufficiently long official update.",
            source_url="https://x.com/Yellow/status/123",
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
            "fact_check": fact_check("article"),
        })

    client = StudioGenerationClient(
        base_url="http://localhost:8888",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(handler),
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
            "fact_check": fact_check("daily_news"),
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(handler),
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
@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (404, {"error": "not_found"}),
        (200, {"schema_version": "1.0", "generation_contract": "old-contract@1"}),
    ],
)
async def test_generation_preflight_blocks_mutation_until_the_current_contract_exists(
    status_code,
    body,
):
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(status_code, json=body)

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(
        GenerationRequestError,
        match="studio_generation_contract_unavailable",
    ) as error:
        await client.generate(
            client_id="yellow",
            content_kind="daily_news",
            request_id=REQUEST_ID,
            source_content="A sufficiently long official update.",
            source_url="https://x.com/Yellow/status/123",
        )
    assert error.value.retryable is True
    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_recovery_release_preflight_blocks_before_any_generation_post():
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json={
            **generation_capabilities(),
            "netlify_release_sha": "d" * 40,
        })

    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(
        GenerationRequestError,
        match="studio_generation_contract_unavailable",
    ):
        await client.require_release(RELEASE_SHA)
    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_recovery_generation_rechecks_and_sends_exact_netlify_release():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=generation_capabilities())
        return httpx.Response(200, json={
            "content_item_id": REQUEST_ID,
            "content_version_id": VERSION_ID,
            "asset_ids": [ASSET_ID],
            "storage_backend": "supabase",
            "reused": False,
            "fact_check": fact_check("daily_news"),
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
        source_content="A sufficiently long official Squid update.",
        source_url="https://x.com/SquidRouter/status/123",
        expected_studio_release_sha=RELEASE_SHA,
    )

    assert [request.method for request in requests] == ["GET", "POST"]
    assert requests[1].headers["x-studio-expected-release-sha"] == RELEASE_SHA


@pytest.mark.asyncio
async def test_generation_rejects_redirects_and_untracked_results():
    client = StudioGenerationClient(
        base_url="https://coineasy-newscard.netlify.app",
        automation_token=AUTOMATION_TOKEN,
        transport=capable_transport(
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
        transport=capable_transport(lambda _request: httpx.Response(200, json={
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
        transport=capable_transport(handler),
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
