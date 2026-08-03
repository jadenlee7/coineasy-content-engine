from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from core.batch.models import BatchWorkItem, canonical_input_sha256
from core.batch.openai_client import OpenAIBatchClient, OpenAIBatchError


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)
API_KEY = "sk-test-batch-key-that-is-long-enough"
SCHEMA = {
    "type": "object",
    "properties": {"draft": {"type": "string"}},
    "required": ["draft"],
    "additionalProperties": False,
}


def _item(job_id: str = "11111111-1111-4111-8111-111111111111") -> BatchWorkItem:
    instructions = "Return one review-only draft."
    input_text = "Pinned source evidence."
    return BatchWorkItem(
        job_id=job_id,
        client_id="squid",
        agent_id="squid_client_agent",
        workflow_kind="official_source_nonurgent_pack",
        stage="generate",
        attempt=1,
        priority="P2",
        risk_tier="T1",
        deadline_at=NOW + timedelta(hours=30),
        model_tier="S",
        model="gpt-5.6-luna",
        instructions=instructions,
        input_text=input_text,
        input_sha256=canonical_input_sha256(
            instructions=instructions,
            input_text=input_text,
            output_schema=SCHEMA,
        ),
        output_schema=SCHEMA,
        max_output_tokens=2_000,
        estimated_input_tokens=1_000,
        estimated_output_tokens=500,
        max_cost_usd=Decimal("0.05"),
    )


def _snapshot_body(**overrides):
    value = {
        "id": "batch_abc123",
        "input_file_id": "file_abc123",
        "status": "validating",
        "output_file_id": None,
        "error_file_id": None,
        "request_counts": {"total": 0, "completed": 0, "failed": 0},
        "metadata": {"dispatch_key": "a" * 64},
        "created_at": int(NOW.timestamp()),
    }
    value.update(overrides)
    return value


def test_jsonl_uses_unique_custom_ids_and_responses_endpoint():
    payload = OpenAIBatchClient.build_jsonl((
        _item(),
        _item("22222222-2222-4222-8222-222222222222"),
    ))
    lines = [json.loads(line) for line in payload.decode().splitlines()]

    assert len(lines) == 2
    assert lines[0]["custom_id"].endswith(":generate:1")
    assert lines[0]["url"] == "/v1/responses"
    assert lines[0]["body"]["store"] is False
    assert lines[1]["custom_id"] != lines[0]["custom_id"]


def test_request_fingerprint_contains_the_exact_serialized_provider_request():
    item = _item()
    [line] = [
        json.loads(value)
        for value in OpenAIBatchClient.build_jsonl((item,)).decode().splitlines()
    ]

    assert item.request_fingerprint_subject()["provider_request"] == line
    assert replace(item, max_output_tokens=1_999).request_sha256 != (
        item.request_sha256
    )


def test_jsonl_rejects_duplicate_custom_ids():
    with pytest.raises(ValueError, match="unique"):
        OpenAIBatchClient.build_jsonl((_item(), _item()))


@pytest.mark.asyncio
async def test_upload_uses_batch_purpose_jsonl_and_short_retention():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={
            "id": "file_abc123",
            "purpose": "batch",
        })

    client = OpenAIBatchClient(
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    )
    file_id = await client.upload_input_file(
        payload=OpenAIBatchClient.build_jsonl((_item(),)),
        bundle_id="11111111-1111-4111-8111-111111111111",
    )

    request = captured["request"]
    assert request.url.path == "/v1/files"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert b'name="purpose"' in request.content
    assert b"batch" in request.content
    assert b"application/jsonl" in request.content
    assert b"expires_after[seconds]" in request.content
    assert file_id == "file_abc123"


@pytest.mark.asyncio
async def test_create_batch_pins_responses_24h_metadata_and_output_expiry():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_snapshot_body(
            metadata=captured["metadata"],
        ))

    client = OpenAIBatchClient(
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    )
    snapshot = await client.create_batch(
        input_file_id="file_abc123",
        metadata={
            "dispatch_key": "a" * 64,
            "client_id": "squid",
            "policy": "batch_first_v1",
        },
    )

    assert captured["endpoint"] == "/v1/responses"
    assert captured["completion_window"] == "24h"
    assert captured["output_expires_after"]["anchor"] == "created_at"
    assert captured["output_expires_after"]["seconds"] == 604800
    assert snapshot.provider_batch_id == "batch_abc123"


@pytest.mark.asyncio
async def test_recent_batch_reconciliation_prevents_blind_resubmission():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "100"
        return httpx.Response(200, json={
            "data": [
                _snapshot_body(metadata={"dispatch_key": "b" * 64}),
                _snapshot_body(
                    id="batch_match123",
                    metadata={"dispatch_key": "a" * 64},
                ),
            ],
        })

    client = OpenAIBatchClient(
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    )
    found = await client.find_recent_batch(
        dispatch_key="a" * 64,
        not_before=NOW - timedelta(hours=1),
    )

    assert found is not None
    assert found.provider_batch_id == "batch_match123"


@pytest.mark.asyncio
async def test_recent_batch_recovery_paginates_before_allowing_a_create():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "after" not in request.url.params:
            return httpx.Response(200, json={
                "data": [
                    _snapshot_body(
                        id="batch_cursor123",
                        metadata={"dispatch_key": "b" * 64},
                    ),
                ],
                "has_more": True,
                "last_id": "batch_cursor123",
            })
        assert request.url.params["after"] == "batch_cursor123"
        return httpx.Response(200, json={
            "data": [
                _snapshot_body(
                    id="batch_match123",
                    metadata={"dispatch_key": "a" * 64},
                ),
            ],
            "has_more": False,
        })

    client = OpenAIBatchClient(
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    )
    found = await client.find_recent_batch(
        dispatch_key="a" * 64,
        not_before=NOW - timedelta(hours=1),
    )

    assert found is not None
    assert found.provider_batch_id == "batch_match123"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_recent_batch_recovery_fails_closed_on_repeated_cursor():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "data": [
                _snapshot_body(
                    id="batch_cursor123",
                    metadata={"dispatch_key": "b" * 64},
                ),
            ],
            "has_more": True,
            "last_id": "batch_cursor123",
        })

    client = OpenAIBatchClient(
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OpenAIBatchError, match="invalid_batch_list"):
        await client.find_recent_batch(
            dispatch_key="a" * 64,
            not_before=NOW - timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_recovery_scan_stops_at_the_attempt_time_cutoff():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "data": [
                _snapshot_body(
                    created_at=int((NOW - timedelta(hours=2)).timestamp()),
                    metadata={"dispatch_key": "b" * 64},
                ),
            ],
            "has_more": True,
            "last_id": "batch_abc123",
        })

    client = OpenAIBatchClient(
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    )
    found = await client.find_recent_batch(
        dispatch_key="a" * 64,
        not_before=NOW - timedelta(hours=1),
    )

    assert found is None
    assert len(requests) == 1


def _response_line(custom_id: str, draft: str, *, input_tokens=10, output_tokens=5):
    return {
        "custom_id": custom_id,
        "response": {
            "status_code": 200,
            "body": {
                "status": "completed",
                "model": "gpt-5.6-luna",
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": json.dumps({"draft": draft}),
                    }],
                }],
                "usage": {
                    "input_tokens": input_tokens,
                    "input_tokens_details": {
                        "cached_tokens": 0,
                        "cache_write_tokens": 0,
                    },
                    "output_tokens": output_tokens,
                },
            },
        },
        "error": None,
    }


def test_result_parser_matches_out_of_order_items_by_custom_id():
    first = _item().custom_id
    second = _item("22222222-2222-4222-8222-222222222222").custom_id
    payload = (
        json.dumps(_response_line(second, "second"))
        + "\n"
        + json.dumps(_response_line(first, "first"))
        + "\n"
    ).encode()

    outcomes = OpenAIBatchClient.parse_result_file(payload)

    assert [item.custom_id for item in outcomes] == [second, first]
    assert outcomes[0].output == {"draft": "second"}
    assert outcomes[1].input_tokens == 10


def test_partial_batch_accepts_success_and_sanitizes_provider_failure():
    success = _item().custom_id
    failed = _item("22222222-2222-4222-8222-222222222222").custom_id
    payload = (
        json.dumps(_response_line(success, "ok"))
        + "\n"
        + json.dumps({
            "custom_id": failed,
            "response": None,
            "error": {
                "code": "secret provider detail that must not escape",
                "message": "raw customer prompt",
            },
        })
        + "\n"
    ).encode()

    outcomes = OpenAIBatchClient.parse_result_file(payload)

    assert outcomes[0].ok is True
    assert outcomes[1].ok is False
    assert outcomes[1].error_code == "openai_batch_item_failed"
    assert outcomes[1].cost_basis == "none"


def test_billable_refusal_preserves_usage_for_failure_settlement():
    custom_id = _item().custom_id
    line = _response_line(custom_id, "unused", input_tokens=100, output_tokens=50)
    body = line["response"]["body"]
    body["output"] = [{
        "type": "message",
        "content": [{"type": "refusal", "refusal": "Cannot comply."}],
    }]
    body["usage"]["input_tokens_details"]["cached_tokens"] = 10

    outcome = OpenAIBatchClient.parse_result_file(
        (json.dumps(line) + "\n").encode()
    )[0]

    assert outcome.ok is False
    assert outcome.error_code == "openai_response_refused"
    assert outcome.cost_basis == "usage"
    assert outcome.input_tokens == 100
    assert outcome.cached_input_tokens == 10
    assert outcome.output_tokens == 50


def test_missing_usage_charges_reservation_instead_of_zero():
    custom_id = _item().custom_id
    line = _response_line(custom_id, "valid output")
    del line["response"]["body"]["usage"]

    outcome = OpenAIBatchClient.parse_result_file(
        (json.dumps(line) + "\n").encode()
    )[0]

    assert outcome.ok is False
    assert outcome.error_code == "openai_invalid_response_usage"
    assert outcome.cost_basis == "reservation"
    assert outcome.input_tokens == 0
    assert outcome.output_tokens == 0


@pytest.mark.parametrize("cache_write_tokens", [None, 1, True])
def test_unverifiable_cache_write_usage_charges_full_reservation(
    cache_write_tokens,
):
    custom_id = _item().custom_id
    line = _response_line(custom_id, "valid output")
    input_details = line["response"]["body"]["usage"]["input_tokens_details"]
    if cache_write_tokens is None:
        del input_details["cache_write_tokens"]
    else:
        input_details["cache_write_tokens"] = cache_write_tokens

    outcome = OpenAIBatchClient.parse_result_file(
        (json.dumps(line) + "\n").encode()
    )[0]

    assert outcome.ok is False
    assert outcome.error_code == "openai_invalid_response_usage"
    assert outcome.cost_basis == "reservation"
    assert outcome.input_tokens == 0
    assert outcome.output_tokens == 0


def test_duplicate_or_non_json_results_fail_closed():
    custom_id = _item().custom_id
    line = json.dumps(_response_line(custom_id, "ok"))
    with pytest.raises(OpenAIBatchError, match="openai_invalid_batch_output"):
        OpenAIBatchClient.parse_result_file(f"{line}\n{line}\n".encode())
    with pytest.raises(OpenAIBatchError, match="openai_invalid_batch_output"):
        OpenAIBatchClient.parse_result_file(b"not-json\n")


def test_completed_provider_batch_without_any_result_file_fails_closed():
    with pytest.raises(OpenAIBatchError, match="invalid_batch_response"):
        OpenAIBatchClient._snapshot(_snapshot_body(
            status="completed",
            request_counts={"total": 1, "completed": 1, "failed": 0},
        ))


@pytest.mark.asyncio
async def test_api_target_redirects_and_provider_error_bodies_fail_closed():
    with pytest.raises(ValueError, match="allowlist"):
        OpenAIBatchClient(api_key=API_KEY, base_url="https://attacker.example")

    client = OpenAIBatchClient(
        api_key=API_KEY,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                302,
                headers={"location": "https://attacker.example"},
                json={"error": "raw secret"},
            )
        ),
    )
    with pytest.raises(OpenAIBatchError) as error:
        await client.retrieve_batch("batch_abc123")
    assert error.value.code == "openai_batch_request_failed"
    assert "raw secret" not in str(error.value)
