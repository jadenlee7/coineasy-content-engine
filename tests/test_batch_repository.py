from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from core.batch.models import (
    BatchSnapshot,
    BatchWorkItem,
    canonical_input_sha256,
)
from core.batch.repository import (
    BatchRepositoryError,
    SupabaseBatchRepository,
    batch_dispatch_key,
    provider_create_request_sha256,
)


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)
WORKSPACE_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "11111111-1111-4111-8111-111111111111"
CONFIG_APPROVAL_ID = "22222222-2222-4222-8222-222222222222"
DISPATCH_APPROVAL_ID = "33333333-3333-4333-8333-333333333333"
CONFIG_SUBJECT_SHA256 = "a" * 64
DISPATCH_SUBJECT_SHA256 = "b" * 64
DISPATCH_KEY = "c" * 64
INTENT_ID = "44444444-4444-4444-8444-444444444444"
CREATE_REQUEST_SHA256 = provider_create_request_sha256(
    input_file_id="file_input123",
    completion_window="24h",
    metadata={
        "dispatch_key": DISPATCH_KEY,
        "bundle_id": INTENT_ID,
        "client_id": "origintrail",
        "policy": "batch_first_v1",
    },
    output_expires_after={"anchor": "created_at", "seconds": 604800},
)
SERVICE_KEY = "s" * 64
SCHEMA = {
    "type": "object",
    "properties": {"draft": {"type": "string"}},
    "required": ["draft"],
    "additionalProperties": False,
}


def _item(**overrides):
    values = {
        "job_id": JOB_ID,
        "client_id": "squid",
        "agent_id": "squid_client_agent",
        "workflow_kind": "official_source_nonurgent_pack",
        "stage": "generate",
        "attempt": 1,
        "priority": "P0",
        "risk_tier": "T1",
        "deadline_at": NOW + timedelta(hours=30),
        "model_tier": "S",
        "model": "gpt-5.6-luna",
        "instructions": "Return a Korean draft.",
        "input_text": "Pinned evidence.",
        "output_schema": SCHEMA,
        "max_output_tokens": 1_000,
        "estimated_input_tokens": 1_000,
        "estimated_output_tokens": 500,
        "max_cost_usd": Decimal("0.05"),
    }
    values.update(overrides)
    values.setdefault(
        "input_sha256",
        canonical_input_sha256(
            instructions=values["instructions"],
            input_text=values["input_text"],
            output_schema=values["output_schema"],
        ),
    )
    return BatchWorkItem(**values)


def _repository(handler):
    return SupabaseBatchRepository(
        supabase_url="https://project-ref.supabase.co",
        service_role_key=SERVICE_KEY,
        workspace_id=WORKSPACE_ID,
        transport=httpx.MockTransport(handler),
    )


def _origintrail_item(**overrides):
    return _item(
        client_id="origintrail",
        agent_id="origintrail_client_agent",
        **overrides,
    )


def _canary_binding(item, *, expires_at=NOW + timedelta(hours=1)):
    return {
        "config_subject_sha256": CONFIG_SUBJECT_SHA256,
        "config_approval_id": CONFIG_APPROVAL_ID,
        "dispatch_subject_sha256": DISPATCH_SUBJECT_SHA256,
        "dispatch_approval_id": DISPATCH_APPROVAL_ID,
        "job_id": item.job_id,
        "input_sha256": item.input_sha256,
        "request_sha256": item.request_sha256,
        "expires_at": expires_at,
        "hard_limit_usd": Decimal("0.05"),
    }


def _canary_receipt(
    item,
    *,
    consumed=0,
    recovery_required=False,
    provider_create_allowed=None,
    expires_at=NOW + timedelta(hours=1),
):
    receipt = {
        "canary_config_subject_sha256": CONFIG_SUBJECT_SHA256,
        "canary_config_approval_id": CONFIG_APPROVAL_ID,
        "canary_dispatch_subject_sha256": DISPATCH_SUBJECT_SHA256,
        "canary_dispatch_approval_id": DISPATCH_APPROVAL_ID,
        "canary_job_id": item.job_id,
        "canary_input_sha256": item.input_sha256,
        "canary_request_sha256": item.request_sha256,
        "canary_expires_at": expires_at.isoformat(),
        "canary_hard_limit_microusd": 50_000,
        "canary_max_provider_batches": 1,
        "canary_provider_batches_consumed": consumed,
        "canary_consumed_at": NOW.isoformat() if consumed else None,
        "reused": recovery_required,
    }
    if provider_create_allowed is None:
        return receipt
    receipt.update({
        "job_id": item.job_id,
        "custom_id": item.custom_id,
        "client_id": item.client_id,
        "agent_id": item.agent_id,
        "workflow_kind": item.workflow_kind,
        "stage": item.stage,
        "priority": 3,
        "latency_class": "batch_24h",
        "model": item.model,
        "model_tier": item.model_tier,
        "deadline": item.deadline_at.isoformat(),
        "input_payload": {
            "instructions": item.instructions,
            "input": item.input_text,
            "output_schema": SCHEMA,
            "estimated_output_tokens": item.estimated_output_tokens,
            "risk_tier": item.risk_tier,
            "approval_required": True,
            "interactive": False,
            "incident_or_release_blocker": False,
            "live_tools_required": False,
            "source_snapshot_complete": True,
            "input_immutable": True,
            "retry_idempotent": True,
            "remaining_batch_stages": 1,
            "request_sha256": item.request_sha256,
        },
        "input_sha256": item.input_sha256,
        "estimated_input_tokens": item.estimated_input_tokens,
        "max_output_tokens": item.max_output_tokens,
        "max_cost_microusd": 50_000,
        "budget_key": "batch-general:2026-07-31",
        "attempt": 1,
        "recovery_required": recovery_required,
        "attempt_started_at": NOW.isoformat(),
        "lease_expires_at": (NOW + timedelta(minutes=15)).isoformat(),
        "provider_create_allowed": provider_create_allowed,
    })
    return receipt


def _overage_receipt(*, outcome_kind: str, reused: bool = False):
    return {
        "job_id": JOB_ID,
        "status": "failed",
        "settlement": "cost_cap_breached",
        "error_code": "batch_cost_cap_breached",
        "provider_batch_id": "batch_abc123",
        "outcome_kind": outcome_kind,
        "input_tokens": 100,
        "output_tokens": 50,
        "reservation_cap_microusd": 50_000,
        "actual_cost_microusd": 60_000,
        "overage_microusd": 10_000,
        "budget_spent_microusd": 50_000,
        "outcome_fingerprint": "c" * 64,
        "resolution_status": "unresolved",
        "reused": reused,
    }


def _authorization_receipt(item, **overrides):
    receipt = {
        "provider_create_intent_id": INTENT_ID,
        "intent_status": "armed",
        "provider_create_allowed": True,
        "create_not_after": (NOW + timedelta(minutes=2)).isoformat(),
        "job_id": item.job_id,
        "attempt": 1,
        "config_subject_sha256": CONFIG_SUBJECT_SHA256,
        "config_approval_id": CONFIG_APPROVAL_ID,
        "dispatch_subject_sha256": DISPATCH_SUBJECT_SHA256,
        "dispatch_approval_id": DISPATCH_APPROVAL_ID,
        "input_sha256": item.input_sha256,
        "request_sha256": item.request_sha256,
        "dispatch_key": DISPATCH_KEY,
        "create_request_sha256": CREATE_REQUEST_SHA256,
        "input_file_id": "file_input123",
        "reused": False,
    }
    receipt.update(overrides)
    return receipt


def _snapshot(item, **overrides):
    values = {
        "provider_batch_id": "batch_abc123",
        "input_file_id": "file_input123",
        "status": "validating",
        "output_file_id": None,
        "error_file_id": None,
        "request_total": 1,
        "request_completed": 0,
        "request_failed": 0,
        "metadata": {
            "dispatch_key": DISPATCH_KEY,
            "bundle_id": INTENT_ID,
            "client_id": item.client_id,
            "policy": "batch_first_v1",
        },
    }
    values.update(overrides)
    return BatchSnapshot(**values)


@pytest.mark.asyncio
async def test_budget_rpc_uses_exact_period_and_microusd_contract():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"budget_key": "batch-general:2026-07-31"})

    repository = _repository(handler)
    await repository.configure_daily_budget(
        budget_key="batch-general:2026-07-31",
        window_start=NOW,
        window_end=NOW + timedelta(days=1),
        limit_usd=Decimal("6.00"),
    )

    assert captured["path"].endswith("/rpc/configure_agent_batch_budget")
    assert captured["body"]["target_period_start"] == NOW.isoformat()
    assert captured["body"]["target_period_end"] == (
        NOW + timedelta(days=1)
    ).isoformat()
    assert captured["body"]["target_hard_limit_microusd"] == 6_000_000


@pytest.mark.asyncio
async def test_budget_rpc_rejects_a_window_longer_than_one_day():
    with pytest.raises(ValueError, match="budget window"):
        await _repository(lambda _request: httpx.Response(200, json={})).configure_daily_budget(
            budget_key="batch-general:2026-07-31",
            window_start=NOW,
            window_end=NOW + timedelta(days=1, seconds=1),
            limit_usd=Decimal("6.00"),
        )


@pytest.mark.asyncio
async def test_queue_rpc_is_batch_only_deterministic_and_priority_correct():
    captured = {}
    item = _item()

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "custom_id": item.custom_id,
            "status": "queued",
            "reserved_microusd": 50_000,
            "budget_key": "batch-general:2026-07-31",
            "reused": False,
        })

    repository = _repository(handler)
    result = await repository.queue_job(
        item=item,
        idempotency_key="a" * 64,
        budget_key="batch-general:2026-07-31",
    )

    assert result == JOB_ID
    assert captured["target_latency_class"] == "batch_24h"
    assert captured["target_priority"] == 3
    assert captured["target_model"] == "gpt-5.6-luna"
    assert captured["target_custom_id"] == item.custom_id
    assert captured["target_replay_only"] is False
    assert captured["target_input_payload"]["approval_required"] is True
    assert captured["target_input_payload"]["request_sha256"] == item.request_sha256
    assert captured["target_max_cost_microusd"] == 50_000


@pytest.mark.asyncio
@pytest.mark.parametrize("consumed", [0, 1])
async def test_canary_grant_configuration_is_exact_and_idempotently_reused(
    consumed,
):
    item = _origintrail_item()
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        receipt = _canary_receipt(item, consumed=consumed)
        receipt["reused"] = consumed == 1
        return httpx.Response(200, json=receipt)

    await _repository(handler).configure_canary_grant(
        **_canary_binding(item),
    )

    assert captured["path"].endswith(
        "/rpc/configure_origintrail_batch_canary_grant"
    )
    assert captured["body"] == {
        "target_workspace_id": WORKSPACE_ID,
        "target_config_subject_sha256": CONFIG_SUBJECT_SHA256,
        "target_config_approval_id": CONFIG_APPROVAL_ID,
        "target_dispatch_subject_sha256": DISPATCH_SUBJECT_SHA256,
        "target_dispatch_approval_id": DISPATCH_APPROVAL_ID,
        "target_job_id": item.job_id,
        "target_input_sha256": item.input_sha256,
        "target_request_sha256": item.request_sha256,
        "target_expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "target_hard_limit_microusd": 50_000,
        "target_max_provider_batches": 1,
    }


@pytest.mark.asyncio
async def test_canary_grant_receipt_mismatch_fails_closed():
    item = _origintrail_item()
    receipt = _canary_receipt(item)
    receipt["canary_job_id"] = "44444444-4444-4444-8444-444444444444"

    with pytest.raises(BatchRepositoryError) as caught:
        await _repository(
            lambda _request: httpx.Response(200, json=receipt)
        ).configure_canary_grant(**_canary_binding(item))

    assert caught.value.code == "invalid_batch_canary_grant_response"
    assert caught.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recovery_required", "provider_create_allowed"),
    [(False, True), (True, False)],
)
async def test_exact_canary_claim_binds_full_request_and_attempt_one(
    recovery_required,
    provider_create_allowed,
):
    item = _origintrail_item(
        recovery_required=recovery_required,
        attempt_started_at=NOW if recovery_required else None,
    )
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[_canary_receipt(
            item,
            consumed=1,
            recovery_required=recovery_required,
            provider_create_allowed=provider_create_allowed,
        )])

    claimed = await _repository(handler).claim_canary_job(
        worker_id="batch:canary-worker",
        **_canary_binding(item),
    )

    assert claimed == (item,)
    assert claimed[0].attempt == 1
    assert captured["path"].endswith(
        "/rpc/claim_origintrail_batch_canary_job"
    )
    assert captured["body"]["target_request_sha256"] == item.request_sha256
    assert captured["body"]["target_max_provider_batches"] == 1


@pytest.mark.asyncio
async def test_exact_canary_claim_returns_empty_without_mutating_fallback():
    item = _origintrail_item()

    claimed = await _repository(
        lambda _request: httpx.Response(200, json=[])
    ).claim_canary_job(
        worker_id="batch:canary-worker",
        **_canary_binding(item),
    )

    assert claimed == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        {"provider_create_allowed": False},
        {"canary_request_sha256": "c" * 64},
        {"canary_provider_batches_consumed": True},
        {"attempt": 2, "custom_id": f"{JOB_ID}:generate:2"},
    ],
)
async def test_exact_canary_claim_rejects_tampered_receipts(tamper):
    item = _origintrail_item()
    receipt = _canary_receipt(
        item,
        consumed=1,
        provider_create_allowed=True,
    )
    receipt.update(tamper)

    with pytest.raises(BatchRepositoryError) as caught:
        await _repository(
            lambda _request: httpx.Response(200, json=[receipt])
        ).claim_canary_job(
            worker_id="batch:canary-worker",
            **_canary_binding(item),
        )

    assert caught.value.code in {
        "invalid_batch_canary_claim_response",
        "invalid_batch_claim_response",
    }
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_exact_provider_create_authorization_binds_uploaded_request():
    item = _origintrail_item()
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_authorization_receipt(item))

    create_not_after = await _repository(
        handler
    ).authorize_canary_provider_create(
        worker_id="batch:canary-worker",
        **_canary_binding(item),
        intent_id=INTENT_ID,
        dispatch_key=DISPATCH_KEY,
        create_request_sha256=CREATE_REQUEST_SHA256,
        input_file_id="file_input123",
    )

    assert create_not_after == NOW + timedelta(minutes=2)
    assert captured["path"].endswith(
        "/rpc/authorize_origintrail_batch_provider_create"
    )
    assert captured["body"] == {
        "target_workspace_id": WORKSPACE_ID,
        "target_worker_id": "batch:canary-worker",
        "target_config_subject_sha256": CONFIG_SUBJECT_SHA256,
        "target_config_approval_id": CONFIG_APPROVAL_ID,
        "target_dispatch_subject_sha256": DISPATCH_SUBJECT_SHA256,
        "target_dispatch_approval_id": DISPATCH_APPROVAL_ID,
        "target_job_id": item.job_id,
        "target_input_sha256": item.input_sha256,
        "target_request_sha256": item.request_sha256,
        "target_expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "target_hard_limit_microusd": 50_000,
        "target_max_provider_batches": 1,
        "target_intent_id": INTENT_ID,
        "target_dispatch_key": DISPATCH_KEY,
        "target_create_request_sha256": CREATE_REQUEST_SHA256,
        "target_input_file_id": "file_input123",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        {"reused": True},
        {"provider_create_allowed": False},
        {"create_request_sha256": "e" * 64},
        {"input_file_id": "file_other"},
    ],
)
async def test_replayed_or_tampered_create_authorization_fails_closed(tamper):
    item = _origintrail_item()
    receipt = _authorization_receipt(item, **tamper)

    with pytest.raises(BatchRepositoryError) as caught:
        await _repository(
            lambda _request: httpx.Response(200, json=receipt)
        ).authorize_canary_provider_create(
            worker_id="batch:canary-worker",
            **_canary_binding(item),
            intent_id=INTENT_ID,
            dispatch_key=DISPATCH_KEY,
            create_request_sha256=CREATE_REQUEST_SHA256,
            input_file_id="file_input123",
        )

    assert caught.value.code == "invalid_batch_provider_create_authorization"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_provider_create_fence_sqlstate_is_retryable():
    item = _origintrail_item()

    with pytest.raises(BatchRepositoryError) as caught:
        await _repository(
            lambda _request: httpx.Response(
                409,
                json={"code": "55P03", "message": "fenced"},
            )
        ).authorize_canary_provider_create(
            worker_id="batch:canary-worker",
            **_canary_binding(item),
            intent_id=INTENT_ID,
            dispatch_key=DISPATCH_KEY,
            create_request_sha256=CREATE_REQUEST_SHA256,
            input_file_id="file_input123",
        )

    assert caught.value.code == "batch_provider_create_fenced"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_exact_provider_registration_closes_the_same_intent():
    item = _origintrail_item()
    snapshot = _snapshot(item)
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "provider_create_intent_id": INTENT_ID,
            "intent_status": "registered",
            "job_id": item.job_id,
            "attempt": 1,
            "dispatch_key": DISPATCH_KEY,
            "create_request_sha256": CREATE_REQUEST_SHA256,
            "input_file_id": snapshot.input_file_id,
            "provider_batch_id": snapshot.provider_batch_id,
            "registered_at": NOW.isoformat(),
            "reused": False,
        })

    await _repository(handler).register_canary_batch(
        worker_id="batch:canary-worker",
        intent_id=INTENT_ID,
        config_subject_sha256=CONFIG_SUBJECT_SHA256,
        job_id=item.job_id,
        input_sha256=item.input_sha256,
        request_sha256=item.request_sha256,
        dispatch_key=DISPATCH_KEY,
        create_request_sha256=CREATE_REQUEST_SHA256,
        input_file_id=snapshot.input_file_id,
        snapshot=snapshot,
    )

    assert captured["path"].endswith(
        "/rpc/register_origintrail_batch_provider_create"
    )
    assert captured["body"]["target_intent_id"] == INTENT_ID
    assert captured["body"]["target_batch_id"] == snapshot.provider_batch_id
    assert captured["body"]["target_create_request_sha256"] == (
        CREATE_REQUEST_SHA256
    )


def test_provider_create_fingerprint_binds_file_window_and_metadata():
    metadata = {
        "dispatch_key": DISPATCH_KEY,
        "bundle_id": INTENT_ID,
        "client_id": "origintrail",
        "policy": "batch_first_v1",
    }
    output_expiry = {"anchor": "created_at", "seconds": 604800}
    fingerprint = provider_create_request_sha256(
        input_file_id="file_input123",
        completion_window="24h",
        metadata=metadata,
        output_expires_after=output_expiry,
    )

    assert len(fingerprint) == 64
    assert fingerprint != provider_create_request_sha256(
        input_file_id="file_input124",
        completion_window="24h",
        metadata=metadata,
        output_expires_after=output_expiry,
    )
    assert fingerprint != provider_create_request_sha256(
        input_file_id="file_input123",
        completion_window="24h",
        metadata={**metadata, "policy": "changed"},
        output_expires_after=output_expiry,
    )
    with pytest.raises(ValueError, match="request binding"):
        provider_create_request_sha256(
            input_file_id="file_input123",
            completion_window="48h",
            metadata=metadata,
            output_expires_after=output_expiry,
        )
    with pytest.raises(ValueError, match="request binding"):
        provider_create_request_sha256(
            input_file_id="file_input123",
            completion_window="24h",
            metadata=metadata,
            output_expires_after={
                "anchor": "created_at",
                "seconds": 86400,
            },
        )


@pytest.mark.asyncio
async def test_canary_grant_rejects_noncanonical_or_over_cap_arguments():
    item = _origintrail_item()
    repository = _repository(lambda _request: httpx.Response(200, json={}))

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        await repository.configure_canary_grant(
            **{
                **_canary_binding(item),
                "request_sha256": "C" * 64,
            },
        )
    with pytest.raises(ValueError, match="exceeds 50000"):
        await repository.configure_canary_grant(
            **{
                **_canary_binding(item),
                "hard_limit_usd": Decimal("0.050001"),
            },
        )


@pytest.mark.asyncio
async def test_queue_replay_accepts_the_ledger_bound_prior_day_budget():
    item = _item()

    def handler(_request):
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "custom_id": item.custom_id,
            "status": "queued",
            "reserved_microusd": 50_000,
            "budget_key": "batch-general:2026-07-30",
            "reused": True,
        })

    result = await _repository(handler).queue_job(
        item=item,
        idempotency_key="a" * 64,
        budget_key="batch-general:2026-07-31",
        replay_only=True,
    )

    assert result == JOB_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.Response(200, text="not-json"), "batch_database_invalid_response"),
        (httpx.Response(200, json=[]), "invalid_batch_queue_response"),
        (
            httpx.Response(
                200,
                json={"job_id": "22222222-2222-4222-8222-222222222222"},
            ),
            "invalid_batch_queue_response",
        ),
        (
            httpx.Response(200, json={"job_id": "not-a-uuid"}),
            "invalid_batch_queue_response",
        ),
        (
            httpx.Response(
                200,
                json={
                    "job_id": JOB_ID,
                    "custom_id": _item().custom_id,
                    "status": "queued",
                    "reserved_microusd": 50_000,
                    "budget_key": "batch-general:2026-07-31",
                    "reused": 1,
                },
            ),
            "invalid_batch_queue_response",
        ),
    ],
)
async def test_uncertain_queue_receipt_is_retryable_for_idempotent_readback(
    response,
    expected_code,
):
    async def queue():
        return await _repository(lambda _request: response).queue_job(
            item=_item(),
            idempotency_key="a" * 64,
            budget_key="batch-general:2026-07-31",
        )

    with pytest.raises(BatchRepositoryError) as caught:
        await queue()

    assert caught.value.code == expected_code
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_replay_only_rejects_a_nonreused_queue_receipt():
    item = _item()

    def handler(_request):
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "custom_id": item.custom_id,
            "status": "queued",
            "reserved_microusd": 50_000,
            "budget_key": "batch-general:2026-07-31",
            "reused": False,
        })

    with pytest.raises(BatchRepositoryError) as caught:
        await _repository(handler).queue_job(
            item=item,
            idempotency_key="a" * 64,
            budget_key="batch-general:2026-07-31",
            replay_only=True,
        )

    assert caught.value.code == "invalid_batch_queue_response"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_claim_response_reconstructs_and_rehashes_immutable_work_item():
    item = _item()
    captured = {}
    input_payload = {
        "instructions": item.instructions,
        "input": item.input_text,
        "output_schema": SCHEMA,
        "estimated_output_tokens": item.estimated_output_tokens,
        "risk_tier": item.risk_tier,
        "approval_required": True,
        "interactive": False,
        "incident_or_release_blocker": False,
        "live_tools_required": False,
        "source_snapshot_complete": True,
        "input_immutable": True,
        "retry_idempotent": True,
        "remaining_batch_stages": 1,
    }

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=[{
            "job_id": item.job_id,
            "custom_id": item.custom_id,
            "client_id": item.client_id,
            "agent_id": item.agent_id,
            "workflow_kind": item.workflow_kind,
            "stage": item.stage,
            "priority": 3,
            "latency_class": "batch_24h",
            "model": item.model,
            "model_tier": item.model_tier,
            "deadline": item.deadline_at.isoformat(),
            "input_payload": input_payload,
            "input_sha256": item.input_sha256,
            "estimated_input_tokens": item.estimated_input_tokens,
            "max_output_tokens": item.max_output_tokens,
            "max_cost_microusd": 50_000,
            "budget_key": "batch-general:2026-07-31",
            "attempt": 1,
            "recovery_required": False,
            "attempt_started_at": NOW.isoformat(),
            "lease_expires_at": (NOW + timedelta(minutes=15)).isoformat(),
        }])

    claimed = await _repository(handler).claim_jobs(
        worker_id="batch:test-worker",
        allowed_clients=frozenset({"squid"}),
        limit=10,
    )

    assert claimed == (item,)
    assert claimed[0].batch_request()["url"] == "/v1/responses"
    assert captured["target_client_ids"] == ["squid"]


@pytest.mark.asyncio
async def test_expiry_rpc_requires_an_exact_bounded_receipt():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "expired_job_count": 2,
            "released_microusd": 100_000,
            "ambiguous_claimed_count": 1,
        })

    result = await _repository(handler).expire_jobs(
        allowed_clients=frozenset({"squid"}),
    )

    assert result == (2, 1)
    assert captured == {
        "target_workspace_id": WORKSPACE_ID,
        "target_client_ids": ["squid"],
    }


@pytest.mark.asyncio
async def test_invalid_expiry_receipt_fails_closed():
    repository = _repository(
        lambda _request: httpx.Response(200, json={
            "expired_job_count": 1,
            "released_microusd": 50_000,
            "ambiguous_claimed_count": True,
        })
    )

    with pytest.raises(BatchRepositoryError) as caught:
        await repository.expire_jobs(
            allowed_clients=frozenset({"squid"}),
        )

    assert caught.value.code == "invalid_batch_expiry_response"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_second_attempt_claim_accepts_attempt_bound_custom_id():
    item = _item(attempt=2)

    def handler(_request):
        return httpx.Response(200, json=[{
            "job_id": item.job_id,
            "custom_id": item.custom_id,
            "client_id": item.client_id,
            "agent_id": item.agent_id,
            "workflow_kind": item.workflow_kind,
            "stage": item.stage,
            "priority": 3,
            "latency_class": "batch_24h",
            "model": item.model,
            "model_tier": item.model_tier,
            "deadline": item.deadline_at.isoformat(),
            "input_payload": {
                "instructions": item.instructions,
                "input": item.input_text,
                "output_schema": SCHEMA,
                "estimated_output_tokens": item.estimated_output_tokens,
                "risk_tier": item.risk_tier,
                "approval_required": True,
                "interactive": False,
                "incident_or_release_blocker": False,
                "live_tools_required": False,
                "source_snapshot_complete": True,
                "input_immutable": True,
                "retry_idempotent": True,
                "remaining_batch_stages": 1,
            },
            "input_sha256": item.input_sha256,
            "estimated_input_tokens": item.estimated_input_tokens,
            "max_output_tokens": item.max_output_tokens,
            "max_cost_microusd": 50_000,
            "budget_key": "batch-general:2026-07-31",
            "attempt": 2,
            "recovery_required": False,
            "attempt_started_at": NOW.isoformat(),
            "lease_expires_at": (NOW + timedelta(minutes=15)).isoformat(),
        }])

    claimed = await _repository(handler).claim_jobs(
        worker_id="batch:test-worker",
        allowed_clients=frozenset({"squid"}),
        limit=10,
    )

    assert claimed == (item,)
    assert claimed[0].custom_id.endswith(":generate:2")


@pytest.mark.asyncio
async def test_stale_claim_requires_bounded_provider_recovery():
    item = _item(
        recovery_required=True,
        attempt_started_at=NOW - timedelta(minutes=20),
    )

    def handler(_request):
        payload = {
            "instructions": item.instructions,
            "input": item.input_text,
            "output_schema": SCHEMA,
            "estimated_output_tokens": item.estimated_output_tokens,
            "risk_tier": item.risk_tier,
            "approval_required": True,
            "interactive": False,
            "incident_or_release_blocker": False,
            "live_tools_required": False,
            "source_snapshot_complete": True,
            "input_immutable": True,
            "retry_idempotent": True,
            "remaining_batch_stages": 1,
        }
        return httpx.Response(200, json=[{
            "job_id": item.job_id,
            "custom_id": item.custom_id,
            "client_id": item.client_id,
            "agent_id": item.agent_id,
            "workflow_kind": item.workflow_kind,
            "stage": item.stage,
            "priority": 3,
            "latency_class": "batch_24h",
            "model": item.model,
            "model_tier": item.model_tier,
            "deadline": item.deadline_at.isoformat(),
            "input_payload": payload,
            "input_sha256": item.input_sha256,
            "estimated_input_tokens": item.estimated_input_tokens,
            "max_output_tokens": item.max_output_tokens,
            "max_cost_microusd": 50_000,
            "budget_key": "batch-general:2026-07-31",
            "attempt": 1,
            "recovery_required": True,
            "attempt_started_at": item.attempt_started_at.isoformat(),
            "lease_expires_at": (NOW + timedelta(minutes=15)).isoformat(),
        }])

    claimed = await _repository(handler).claim_jobs(
        worker_id="batch:test-worker",
        allowed_clients=frozenset({"squid"}),
        limit=10,
    )

    assert claimed == (item,)


@pytest.mark.asyncio
async def test_claim_tampering_or_nonbatch_latency_fails_closed():
    item = _item()

    def handler(_request):
        return httpx.Response(200, json=[{
            "job_id": item.job_id,
            "custom_id": item.custom_id,
            "client_id": item.client_id,
            "agent_id": item.agent_id,
            "workflow_kind": item.workflow_kind,
            "stage": item.stage,
            "priority": 3,
            "latency_class": "sync",
            "model": item.model,
            "model_tier": item.model_tier,
            "deadline": item.deadline_at.isoformat(),
            "input_payload": {
                "instructions": item.instructions,
                "input": "tampered",
                "output_schema": SCHEMA,
                "estimated_output_tokens": 500,
                "risk_tier": "T1",
                "approval_required": True,
                "source_snapshot_complete": True,
                "input_immutable": True,
                "retry_idempotent": True,
            },
            "input_sha256": item.input_sha256,
            "estimated_input_tokens": 1_000,
            "max_output_tokens": 1_000,
            "max_cost_microusd": 50_000,
            "attempt": 1,
        }])

    with pytest.raises(
        BatchRepositoryError,
        match="invalid_batch_claim_response",
    ):
        await _repository(handler).claim_jobs(
            worker_id="batch:test-worker",
            allowed_clients=frozenset({"squid"}),
            limit=10,
        )


@pytest.mark.asyncio
async def test_active_batch_rpc_accepts_provider_status_field():
    def handler(_request):
        return httpx.Response(200, json=[{
            "batch_id": "batch_abc123",
            "provider_status": "in_progress",
        }])

    active = await _repository(handler).list_active_batches()
    assert active[0].provider_batch_id == "batch_abc123"
    assert active[0].status == "in_progress"


@pytest.mark.asyncio
async def test_completion_settlement_receipt_is_exact_and_returned():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "job_id": JOB_ID,
            "status": "completed",
            "actual_cost_microusd": 196,
            "reused": False,
        })

    settlement = await _repository(handler).complete_job(
        job_id=JOB_ID,
        provider_batch_id="batch_abc123",
        output={"draft": "review me"},
        input_tokens=100,
        output_tokens=50,
        actual_cost_usd=Decimal("0.000196"),
    )

    assert settlement == "completed"
    assert captured == {
        "target_workspace_id": WORKSPACE_ID,
        "target_job_id": JOB_ID,
        "target_batch_id": "batch_abc123",
        "target_result_code": "needs_review",
        "target_result_payload": {"draft": "review me"},
        "target_input_tokens": 100,
        "target_output_tokens": 50,
        "target_actual_cost_microusd": 196,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("reused", [False, True])
async def test_completion_overage_receipt_converges_to_terminal_signal(reused):
    receipt = _overage_receipt(outcome_kind="completion", reused=reused)

    settlement = await _repository(
        lambda _request: httpx.Response(200, json=receipt)
    ).complete_job(
        job_id=JOB_ID,
        provider_batch_id="batch_abc123",
        output={"draft": "review me"},
        input_tokens=100,
        output_tokens=50,
        actual_cost_usd=Decimal("0.060000"),
    )

    assert settlement == "cost_cap_breached"


@pytest.mark.asyncio
async def test_exact_failure_overage_receipt_is_terminally_successful():
    receipt = _overage_receipt(outcome_kind="failure")

    settlement = await _repository(
        lambda _request: httpx.Response(200, json=receipt)
    ).fail_job(
        job_id=JOB_ID,
        provider_batch_id="batch_abc123",
        error_code="openai_response_refused",
        retryable=False,
        available_at=None,
        input_tokens=100,
        output_tokens=50,
        actual_cost_usd=Decimal("0.060000"),
    )

    assert settlement == "cost_cap_breached"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        {"job_id": "{" + JOB_ID + "}"},
        {"outcome_kind": "failure"},
        {"input_tokens": True},
        {"actual_cost_microusd": 60_001},
        {"reservation_cap_microusd": True, "budget_spent_microusd": True},
        {"outcome_fingerprint": "C" * 64},
        {"resolution_status": "resolved"},
    ],
)
async def test_tampered_overage_receipt_is_commit_unknown_and_retryable(tamper):
    receipt = _overage_receipt(outcome_kind="completion")
    receipt.update(tamper)

    with pytest.raises(BatchRepositoryError) as caught:
        await _repository(
            lambda _request: httpx.Response(200, json=receipt)
        ).complete_job(
            job_id=JOB_ID,
            provider_batch_id="batch_abc123",
            output={"draft": "review me"},
            input_tokens=100,
            output_tokens=50,
            actual_cost_usd=Decimal("0.060000"),
        )

    assert caught.value.code == "invalid_batch_settlement_response"
    assert caught.value.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, content=b"{not-json"),
    ],
)
async def test_malformed_completion_receipt_is_commit_unknown_and_retryable(
    response,
):
    with pytest.raises(BatchRepositoryError) as caught:
        await _repository(lambda _request: response).complete_job(
            job_id=JOB_ID,
            provider_batch_id="batch_abc123",
            output={"draft": "review me"},
            input_tokens=100,
            output_tokens=50,
            actual_cost_usd=Decimal("0.000196"),
        )

    assert caught.value.code == "invalid_batch_settlement_response"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_failed_provider_result_rpc_carries_exact_or_conservative_cost():
    captured = []

    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        receipt = {
            "job_id": JOB_ID,
            "status": "failed",
            "error_code": body["target_error_code"],
            "reused": False,
        }
        if body["target_actual_cost_microusd"] is not None:
            receipt.update({
                "actual_input_tokens": body["target_input_tokens"],
                "actual_output_tokens": body["target_output_tokens"],
                "actual_cost_microusd": body["target_actual_cost_microusd"],
            })
        return httpx.Response(200, json=receipt)

    repository = _repository(handler)
    await repository.fail_job(
        job_id=JOB_ID,
        provider_batch_id="batch_abc123",
        error_code="openai_response_refused",
        retryable=False,
        available_at=None,
        input_tokens=100,
        output_tokens=50,
        actual_cost_usd=Decimal("0.000196"),
    )
    await repository.fail_job(
        job_id=JOB_ID,
        provider_batch_id="batch_abc123",
        error_code="openai_invalid_response_usage",
        retryable=False,
        available_at=None,
        charge_full_reservation=True,
    )

    assert captured[0]["target_actual_cost_microusd"] == 196
    assert captured[0]["target_input_tokens"] == 100
    assert captured[0]["target_charge_full_reservation"] is False
    assert captured[1]["target_actual_cost_microusd"] is None
    assert captured[1]["target_charge_full_reservation"] is True


def test_dispatch_key_is_order_independent_but_input_sensitive():
    first = _item()
    second = _item(job_id="22222222-2222-4222-8222-222222222222")

    assert batch_dispatch_key((first, second)) == batch_dispatch_key((second, first))
    changed = _item(
        job_id=second.job_id,
        input_text="Different immutable evidence.",
    )
    assert batch_dispatch_key((first, second)) != batch_dispatch_key((first, changed))
    changed_controls = _item(
        job_id=second.job_id,
        max_output_tokens=999,
    )
    assert batch_dispatch_key((first, second)) != batch_dispatch_key((
        first,
        changed_controls,
    ))
    assert batch_dispatch_key((first,)) != batch_dispatch_key((
        _item(attempt=2),
    ))


def test_repository_target_and_service_secret_are_fail_closed():
    with pytest.raises(ValueError, match="allowlist"):
        SupabaseBatchRepository(
            supabase_url="https://supabase.co.evil.test",
            service_role_key=SERVICE_KEY,
            workspace_id=WORKSPACE_ID,
        )
    with pytest.raises(ValueError, match="invalid length"):
        SupabaseBatchRepository(
            supabase_url="https://project-ref.supabase.co",
            service_role_key="short",
            workspace_id=WORKSPACE_ID,
        )
