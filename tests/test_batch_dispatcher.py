from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.batch.bridge import BatchQueueBridge
from core.batch.dispatcher import BatchDispatcher
from core.batch.models import (
    ActiveBatch,
    BatchSnapshot,
    BatchWorkItem,
    canonical_input_sha256,
)
from core.batch.openai_client import OpenAIBatchClient
from core.batch.policy import BatchPolicy
from core.batch.repository import (
    BatchRepositoryError,
    batch_dispatch_key,
    provider_create_request_sha256,
)


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)
SCHEMA = {
    "type": "object",
    "properties": {"draft": {"type": "string"}},
    "required": ["draft"],
    "additionalProperties": False,
}


def _item(
    job_id: str = "11111111-1111-4111-8111-111111111111",
    **overrides,
) -> BatchWorkItem:
    values = {
        "job_id": job_id,
        "client_id": "squid",
        "agent_id": "squid_client_agent",
        "workflow_kind": "official_source_nonurgent_pack",
        "stage": "generate",
        "attempt": 1,
        "priority": "P2",
        "risk_tier": "T1",
        "deadline_at": NOW + timedelta(hours=30),
        "model_tier": "S",
        "model": "gpt-5.6-luna",
        "instructions": "Write a review-only draft.",
        "input_text": "Immutable evidence.",
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


def _snapshot(**overrides) -> BatchSnapshot:
    values = {
        "provider_batch_id": "batch_abc123",
        "input_file_id": "file_input123",
        "status": "validating",
        "output_file_id": None,
        "error_file_id": None,
        "request_total": 1,
        "request_completed": 0,
        "request_failed": 0,
        "metadata": {"dispatch_key": "a" * 64},
    }
    values.update(overrides)
    return BatchSnapshot(**values)


class FakeRepository:
    def __init__(
        self,
        *,
        claimed=(),
        active=(),
        expired_result=(0, 0),
    ):
        self.claimed = tuple(claimed)
        self.active = tuple(active)
        self.expired_result = expired_result
        self.registered = []
        self.completed = []
        self.failed = []
        self.polled = []
        self.finalized = []
        self.register_error = None
        self.complete_error = None
        self.fail_error = None
        self.complete_settlement = "completed"
        self.fail_settlement = "failed"
        self.authorize_error = None
        self.authorization_not_after = NOW + timedelta(minutes=2)
        self.authorizations = []
        self.canary_registered = []
        self.events = []
        self.claim_kwargs = None
        self.canary_claim_kwargs = None
        self.expire_kwargs = None

    async def claim_jobs(self, **kwargs):
        self.claim_kwargs = kwargs
        return self.claimed

    async def claim_canary_job(self, **kwargs):
        self.canary_claim_kwargs = kwargs
        return self.claimed

    async def authorize_canary_provider_create(self, **kwargs):
        self.events.append("authorize")
        self.authorizations.append(kwargs)
        if self.authorize_error is not None:
            raise self.authorize_error
        return self.authorization_not_after

    async def expire_jobs(self, **kwargs):
        self.expire_kwargs = kwargs
        return self.expired_result

    async def register_batch(self, **kwargs):
        if self.register_error is not None:
            raise self.register_error
        self.registered.append(kwargs)

    async def register_canary_batch(self, **kwargs):
        if self.register_error is not None:
            raise self.register_error
        self.events.append("register_canary")
        self.canary_registered.append(kwargs)

    async def list_active_batches(self, **_kwargs):
        return self.active

    async def update_batch_poll(self, snapshot):
        self.polled.append(snapshot)

    async def complete_job(self, **kwargs):
        if self.complete_error is not None:
            raise self.complete_error
        self.completed.append(kwargs)
        return self.complete_settlement

    async def fail_job(self, **kwargs):
        if self.fail_error is not None:
            raise self.fail_error
        self.failed.append(kwargs)
        return self.fail_settlement

    async def finalize_batch(self, provider_batch_id):
        self.finalized.append(provider_batch_id)


class FakeProvider:
    def __init__(self):
        self.recovered = None
        self.retrieve_snapshot = _snapshot()
        self.files = {}
        self.uploads = []
        self.created = []
        self.downloaded = []
        self.recovery_calls = []
        self.events = []

    @staticmethod
    def build_jsonl(items):
        return OpenAIBatchClient.build_jsonl(items)

    async def find_recent_batch(self, **kwargs):
        self.recovery_calls.append(kwargs)
        return self.recovered

    async def upload_input_file(self, **kwargs):
        self.events.append("upload")
        self.uploads.append(kwargs)
        return "file_input123"

    async def create_batch(self, **kwargs):
        self.events.append("create")
        self.created.append(kwargs)
        return _snapshot()

    async def retrieve_batch(self, _provider_batch_id):
        return self.retrieve_snapshot

    async def download_file(self, file_id):
        self.downloaded.append(file_id)
        return self.files[file_id]

    @staticmethod
    def parse_result_file(payload):
        return OpenAIBatchClient.parse_result_file(payload)


def _dispatcher(repo, provider, *, allowed=frozenset({"squid"})):
    return BatchDispatcher(
        repository=repo,
        provider=provider,
        policy=BatchPolicy(allowed_clients=allowed),
        worker_id="batch:test-worker",
        now_factory=lambda: NOW,
    )


def _origintrail_item(
    job_id: str = "11111111-1111-4111-8111-111111111111",
    **overrides,
):
    return _item(
        job_id,
        client_id="origintrail",
        agent_id="origintrail_client_agent",
        **overrides,
    )


def _canary_dispatcher(repo, provider, item, *, now_factory=lambda: NOW):
    return BatchDispatcher(
        repository=repo,
        provider=provider,
        policy=BatchPolicy(allowed_clients=frozenset({"origintrail"})),
        worker_id="batch:canary-worker",
        max_claims=1,
        max_requests_per_batch=1,
        canary_config_subject_sha256="a" * 64,
        canary_config_approval_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        canary_dispatch_subject_sha256="b" * 64,
        canary_dispatch_approval_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        canary_job_id=item.job_id,
        canary_input_sha256=item.input_sha256,
        canary_request_sha256=item.request_sha256,
        canary_not_after=NOW + timedelta(hours=1),
        now_factory=now_factory,
    )


@pytest.mark.asyncio
async def test_expired_cleanup_releases_only_safe_pre_submission_jobs():
    repo = FakeRepository(expired_result=(2, 1))
    provider = FakeProvider()

    summary = await _dispatcher(repo, provider).cleanup_expired_once()

    assert summary.expired_unsubmitted == 2
    assert summary.ambiguous_claimed == 1
    assert summary.errors == 0
    assert repo.expire_kwargs == {
        "allowed_clients": frozenset({"squid"}),
    }
    assert provider.uploads == []
    assert provider.created == []
    assert {
        "status": "ambiguous_claimed_manual_recovery",
        "detail": "jobs=1",
    } in summary.outcomes


@pytest.mark.asyncio
async def test_poll_error_blocks_claim_and_provider_submission_in_run_once():
    item = _item()

    class UnavailableRepository(FakeRepository):
        async def list_active_batches(self, **_kwargs):
            raise BatchRepositoryError(
                "batch_database_unavailable",
                retryable=True,
            )

    repo = UnavailableRepository(claimed=(item,))
    provider = FakeProvider()

    summary = await _dispatcher(repo, provider).run_once()

    assert summary.errors == 1
    assert repo.claim_kwargs is None
    assert provider.uploads == []
    assert provider.created == []
    assert summary.outcomes[-1] == {
        "status": "submission_blocked_after_poll_error",
        "detail": "active_batch_state_unverified",
    }


@pytest.mark.asyncio
async def test_shared_queue_bridge_admits_batch_and_stops_urgent_work_before_db():
    class QueueRepository:
        def __init__(self):
            self.calls = []
            self.budget_calls = []

        async def configure_daily_budget(self, **kwargs):
            self.budget_calls.append(kwargs)

        async def queue_job(self, **kwargs):
            self.calls.append(kwargs)
            return kwargs["item"].job_id

    repository = QueueRepository()
    bridge = BatchQueueBridge(
        repository=repository,
        policy=BatchPolicy(allowed_clients=frozenset({"squid"})),
    )
    admitted = await bridge.queue(
        item=_item(),
        idempotency_key="a" * 64,
        budget_key="batch-general:2026-07-31",
        budget_window_start=datetime(
            2026,
            7,
            30,
            15,
            tzinfo=timezone.utc,
        ),
        budget_window_end=datetime(
            2026,
            7,
            31,
            15,
            tzinfo=timezone.utc,
        ),
        daily_cap_usd=Decimal("6.00"),
        now=NOW,
    )
    urgent = await bridge.queue(
        item=_item(
            "22222222-2222-4222-8222-222222222222",
            interactive=True,
        ),
        idempotency_key="b" * 64,
        budget_key="batch-general:2026-07-31",
        budget_window_start=datetime(
            2026,
            7,
            30,
            15,
            tzinfo=timezone.utc,
        ),
        budget_window_end=datetime(
            2026,
            7,
            31,
            15,
            tzinfo=timezone.utc,
        ),
        daily_cap_usd=Decimal("6.00"),
        now=NOW,
    )
    with pytest.raises(ValueError, match="no greater than 6"):
        await bridge.queue(
            item=_item("33333333-3333-4333-8333-333333333333"),
            idempotency_key="c" * 64,
            budget_key="batch-general:2026-07-31",
            budget_window_start=datetime(
                2026,
                7,
                30,
                15,
                tzinfo=timezone.utc,
            ),
            budget_window_end=datetime(
                2026,
                7,
                31,
                15,
                tzinfo=timezone.utc,
            ),
            daily_cap_usd=Decimal("6.01"),
            now=NOW,
        )

    assert admitted.mode == "batch"
    assert admitted.job_id == _item().job_id
    assert urgent.mode == "manual_sync"
    assert urgent.job_id is None
    assert len(repository.calls) == 1
    assert len(repository.budget_calls) == 1


@pytest.mark.asyncio
async def test_deadline_readback_is_server_enforced_replay_only():
    class QueueRepository:
        def __init__(self):
            self.calls = []
            self.budget_calls = []

        async def configure_daily_budget(self, **kwargs):
            self.budget_calls.append(kwargs)

        async def queue_job(self, **kwargs):
            self.calls.append(kwargs)
            return kwargs["item"].job_id

    repository = QueueRepository()
    bridge = BatchQueueBridge(
        repository=repository,
        policy=BatchPolicy(allowed_clients=frozenset({"squid"})),
    )
    item = _item(
        deadline_at=NOW + timedelta(hours=40),
        remaining_batch_stages=2,
    )

    admission = await bridge.queue(
        item=item,
        idempotency_key="a" * 64,
        budget_key="batch-general:2026-07-31",
        budget_window_start=datetime(
            2026,
            7,
            30,
            15,
            tzinfo=timezone.utc,
        ),
        budget_window_end=datetime(
            2026,
            7,
            31,
            15,
            tzinfo=timezone.utc,
        ),
        daily_cap_usd=Decimal("6.00"),
        now=NOW,
        allow_existing_readback=True,
    )

    assert admission.mode == "batch"
    assert admission.reason == "batch_idempotent_readback"
    assert repository.budget_calls == []
    assert repository.calls[0]["replay_only"] is True


@pytest.mark.asyncio
async def test_submit_routes_every_eligible_job_to_batch_but_never_auto_syncs():
    eligible = _item()
    other_client = _item(
        "22222222-2222-4222-8222-222222222222",
        client_id="yellow",
        agent_id="yellow_client_agent",
    )
    repo = FakeRepository(claimed=(eligible, other_client))
    provider = FakeProvider()

    summary = await _dispatcher(repo, provider).submit_once()

    assert summary.claimed == 2
    assert summary.submitted == 1
    assert summary.rejected == 1
    assert len(provider.uploads) == 1
    assert len(provider.created) == 1
    assert repo.registered[0]["job_ids"] == [eligible.job_id]
    assert repo.failed[0]["job_id"] == other_client.job_id
    assert repo.failed[0]["provider_batch_id"] is None
    assert repo.claim_kwargs["allowed_clients"] == frozenset({"squid"})


@pytest.mark.asyncio
async def test_clients_and_models_are_never_mixed_in_one_batch_file():
    items = (
        _item(),
        _item(
            "22222222-2222-4222-8222-222222222222",
            model_tier="M",
            model="gpt-5.6-terra",
            max_cost_usd=Decimal("0.50"),
        ),
        _item(
            "33333333-3333-4333-8333-333333333333",
            client_id="yellow",
            agent_id="yellow_client_agent",
        ),
    )
    repo = FakeRepository(claimed=items)
    provider = FakeProvider()

    summary = await _dispatcher(
        repo,
        provider,
        allowed=frozenset({"squid", "yellow"}),
    ).submit_once()

    assert summary.submitted == 3
    assert len(provider.uploads) == 3
    assert all(
        len(payload["payload"].decode().splitlines()) == 1
        for payload in provider.uploads
    )


@pytest.mark.asyncio
async def test_first_pilot_freezes_each_request_in_its_own_batch_envelope():
    items = (
        _item(),
        _item("22222222-2222-4222-8222-222222222222"),
    )
    repo = FakeRepository(claimed=items)
    provider = FakeProvider()

    summary = await _dispatcher(repo, provider).submit_once()

    assert summary.submitted == 2
    assert len(provider.uploads) == 2
    assert len(provider.created) == 2
    assert all(
        len(upload["payload"].decode().splitlines()) == 1
        for upload in provider.uploads
    )


@pytest.mark.asyncio
async def test_recent_provider_batch_is_recovered_without_upload_or_resubmit():
    item = _item(
        recovery_required=True,
        attempt_started_at=NOW - timedelta(minutes=20),
    )
    repo = FakeRepository(claimed=(item,))
    provider = FakeProvider()
    provider.recovered = _snapshot(status="in_progress")

    summary = await _dispatcher(repo, provider).submit_once()

    assert summary.recovered == 1
    assert not provider.uploads
    assert not provider.created
    assert repo.registered[0]["snapshot"].status == "in_progress"
    assert provider.recovery_calls[0]["not_before"] == (
        item.attempt_started_at - timedelta(minutes=5)
    )


@pytest.mark.asyncio
async def test_generic_recovery_miss_is_lookup_only_and_never_creates():
    item = _item(
        recovery_required=True,
        attempt_started_at=NOW - timedelta(minutes=20),
    )
    repo = FakeRepository(claimed=(item,))
    provider = FakeProvider()

    summary = await _dispatcher(repo, provider).submit_once()

    assert summary.errors == 1
    assert len(provider.recovery_calls) == 1
    assert provider.uploads == []
    assert provider.created == []
    assert repo.failed == []
    assert summary.outcomes[-1] == {
        "status": "batch_recovery_lookup_miss",
        "detail": "same_attempt_recovery_is_lookup_only",
    }


@pytest.mark.asyncio
async def test_fresh_claim_never_scans_account_batch_history():
    repo = FakeRepository(claimed=(_item(),))
    provider = FakeProvider()
    provider.recovered = _snapshot(status="in_progress")

    summary = await _dispatcher(repo, provider).submit_once()

    assert summary.submitted == 1
    assert summary.recovered == 0
    assert provider.recovery_calls == []


@pytest.mark.asyncio
async def test_exact_canary_job_and_input_submit_once():
    item = _origintrail_item()
    repo = FakeRepository(claimed=(item,))
    provider = FakeProvider()
    events = []
    repo.events = events
    provider.events = events

    summary = await _canary_dispatcher(repo, provider, item).submit_once()

    assert summary.submitted == 1
    assert len(provider.uploads) == 1
    assert len(provider.created) == 1
    assert events == ["upload", "authorize", "create", "register_canary"]
    assert repo.claim_kwargs is None
    assert repo.canary_claim_kwargs == {
        "worker_id": "batch:canary-worker",
        "config_subject_sha256": "a" * 64,
        "config_approval_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "dispatch_subject_sha256": "b" * 64,
        "dispatch_approval_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "job_id": item.job_id,
        "input_sha256": item.input_sha256,
        "request_sha256": item.request_sha256,
        "expires_at": NOW + timedelta(hours=1),
        "hard_limit_usd": Decimal("0.05"),
        "lease_seconds": 900,
    }
    dispatch_key = batch_dispatch_key((item,))
    create_metadata = provider.created[0]["metadata"]
    assert repo.authorizations == [{
        "worker_id": "batch:canary-worker",
        "config_subject_sha256": "a" * 64,
        "config_approval_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "dispatch_subject_sha256": "b" * 64,
        "dispatch_approval_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "job_id": item.job_id,
        "input_sha256": item.input_sha256,
        "request_sha256": item.request_sha256,
        "expires_at": NOW + timedelta(hours=1),
        "hard_limit_usd": Decimal("0.05"),
        "intent_id": create_metadata["bundle_id"],
        "dispatch_key": dispatch_key,
        "create_request_sha256": provider_create_request_sha256(
            input_file_id="file_input123",
            completion_window="24h",
            metadata=create_metadata,
            output_expires_after={
                "anchor": "created_at",
                "seconds": 604800,
            },
        ),
        "input_file_id": "file_input123",
    }]
    assert repo.registered == []
    assert repo.canary_registered[0]["intent_id"] == (
        create_metadata["bundle_id"]
    )
    assert repo.canary_registered[0]["create_request_sha256"] == (
        repo.authorizations[0]["create_request_sha256"]
    )


@pytest.mark.asyncio
async def test_canary_upload_failure_settles_without_lookup_or_create():
    class UploadFailureProvider(FakeProvider):
        async def upload_input_file(self, **kwargs):
            self.events.append("upload")
            self.uploads.append(kwargs)
            from core.batch.openai_client import OpenAIBatchError

            raise OpenAIBatchError(
                "openai_batch_unavailable",
                retryable=True,
            )

    item = _origintrail_item()
    repo = FakeRepository(claimed=(item,))
    provider = UploadFailureProvider()

    summary = await _canary_dispatcher(repo, provider, item).submit_once()

    assert summary.errors == 1
    assert len(provider.uploads) == 1
    assert provider.recovery_calls == []
    assert provider.created == []
    assert repo.authorizations == []
    assert repo.canary_registered == []
    assert repo.failed == [{
        "job_id": item.job_id,
        "provider_batch_id": None,
        "error_code": "batch_input_upload_failed",
        "retryable": False,
        "available_at": None,
    }]
    assert summary.outcomes[-1] == {
        "status": "input_upload_failed",
        "detail": "openai_batch_unavailable",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable", [True, False])
async def test_generic_upload_failure_retries_only_transient_errors(retryable):
    class UploadFailureProvider(FakeProvider):
        async def upload_input_file(self, **kwargs):
            self.events.append("upload")
            self.uploads.append(kwargs)
            from core.batch.openai_client import OpenAIBatchError

            raise OpenAIBatchError(
                "openai_batch_unavailable",
                retryable=retryable,
            )

    item = _item()
    repo = FakeRepository(claimed=(item,))
    provider = UploadFailureProvider()

    summary = await _dispatcher(repo, provider).submit_once()

    assert summary.errors == 1
    assert provider.recovery_calls == []
    assert provider.created == []
    assert repo.failed == [{
        "job_id": item.job_id,
        "provider_batch_id": None,
        "error_code": "batch_input_upload_failed",
        "retryable": retryable,
        "available_at": (
            NOW + timedelta(minutes=15) if retryable else None
        ),
    }]


@pytest.mark.asyncio
async def test_canary_create_intent_commit_unknown_never_calls_provider_create():
    item = _origintrail_item()
    repo = FakeRepository(claimed=(item,))
    repo.authorize_error = BatchRepositoryError(
        "batch_database_unavailable",
        retryable=True,
    )
    provider = FakeProvider()

    summary = await _canary_dispatcher(repo, provider, item).submit_once()

    assert summary.errors == 1
    assert len(provider.uploads) == 1
    assert len(repo.authorizations) == 1
    assert provider.created == []
    assert repo.canary_registered == []
    assert repo.failed == []
    assert summary.outcomes[-1] == {
        "status": "provider_create_authorization_failed",
        "detail": "batch_database_unavailable",
    }


@pytest.mark.asyncio
async def test_expired_durable_create_authorization_requires_lookup_only():
    item = _origintrail_item()
    repo = FakeRepository(claimed=(item,))
    repo.authorization_not_after = NOW
    provider = FakeProvider()

    summary = await _canary_dispatcher(repo, provider, item).submit_once()

    assert summary.errors == 1
    assert len(provider.uploads) == 1
    assert len(repo.authorizations) == 1
    assert provider.created == []
    assert repo.canary_registered == []
    assert repo.failed == []
    assert summary.outcomes[-1] == {
        "status": "provider_create_authorization_expired",
        "detail": "lookup_only_recovery_required",
    }


@pytest.mark.asyncio
async def test_value_error_after_intent_arm_preserves_lease_for_lookup():
    class InvalidProvider(FakeProvider):
        async def create_batch(self, **_kwargs):
            raise ValueError("invalid provider response")

    item = _origintrail_item()
    repo = FakeRepository(claimed=(item,))
    provider = InvalidProvider()

    summary = await _canary_dispatcher(repo, provider, item).submit_once()

    assert summary.errors == 1
    assert len(repo.authorizations) == 1
    assert repo.failed == []
    assert repo.canary_registered == []
    assert summary.outcomes[-1] == {
        "status": "submit_held",
        "detail": "armed_provider_create_intent_requires_lookup",
    }


@pytest.mark.asyncio
async def test_canary_claim_mismatch_never_reaches_provider():
    authorized = _origintrail_item()
    wrong = _origintrail_item("22222222-2222-4222-8222-222222222222")
    repo = FakeRepository(claimed=(wrong,))
    provider = FakeProvider()

    summary = await _canary_dispatcher(
        repo,
        provider,
        authorized,
    ).submit_once()

    assert summary.errors == 1
    assert not provider.recovery_calls
    assert not provider.uploads
    assert not provider.created
    assert summary.outcomes == [{
        "status": "canary_authorization_mismatch",
        "detail": "claimed_job_or_input_not_authorized",
    }]


@pytest.mark.asyncio
async def test_canary_full_request_mismatch_never_reaches_provider():
    authorized = _origintrail_item()
    wrong = _origintrail_item(max_output_tokens=999)
    repo = FakeRepository(claimed=(wrong,))
    provider = FakeProvider()

    summary = await _canary_dispatcher(
        repo,
        provider,
        authorized,
    ).submit_once()

    assert summary.errors == 1
    assert wrong.input_sha256 == authorized.input_sha256
    assert wrong.request_sha256 != authorized.request_sha256
    assert not provider.recovery_calls
    assert not provider.uploads
    assert not provider.created


@pytest.mark.asyncio
async def test_canary_expiry_before_claim_never_touches_database_or_provider():
    item = _origintrail_item()
    repo = FakeRepository(claimed=(item,))
    provider = FakeProvider()

    summary = await _canary_dispatcher(
        repo,
        provider,
        item,
        now_factory=lambda: NOW + timedelta(hours=1),
    ).submit_once()

    assert summary.errors == 1
    assert repo.canary_claim_kwargs is None
    assert not provider.uploads
    assert not provider.created
    assert summary.outcomes == [{
        "status": "canary_authorization_expired",
        "detail": "before_claim",
    }]


@pytest.mark.asyncio
async def test_canary_expiry_after_upload_blocks_provider_batch_creation():
    item = _origintrail_item()
    repo = FakeRepository(claimed=(item,))
    provider = FakeProvider()
    moments = iter((
        NOW,
        NOW,
        NOW,
        NOW,
        NOW + timedelta(hours=1),
    ))

    summary = await _canary_dispatcher(
        repo,
        provider,
        item,
        now_factory=lambda: next(moments),
    ).submit_once()

    assert summary.errors == 1
    assert len(provider.uploads) == 1
    assert not provider.created
    assert summary.outcomes[-1] == {
        "status": "canary_authorization_expired",
        "detail": "before_create",
    }


@pytest.mark.asyncio
async def test_canary_attempt_two_requires_new_experiment_before_provider_create():
    item = _origintrail_item(attempt=2)
    repo = FakeRepository(claimed=(item,))
    provider = FakeProvider()

    summary = await _canary_dispatcher(repo, provider, item).submit_once()

    assert summary.errors == 1
    assert not provider.recovery_calls
    assert not provider.uploads
    assert not provider.created
    assert summary.outcomes[-1] == {
        "status": "canary_new_batch_forbidden",
        "detail": "attempt_requires_new_experiment",
    }


@pytest.mark.asyncio
async def test_canary_recovery_miss_never_uploads_or_creates_again():
    item = _origintrail_item(
        recovery_required=True,
        attempt_started_at=NOW - timedelta(minutes=20),
    )
    repo = FakeRepository(claimed=(item,))
    provider = FakeProvider()

    summary = await _canary_dispatcher(repo, provider, item).submit_once()

    assert summary.errors == 1
    assert len(provider.recovery_calls) == 1
    assert not provider.uploads
    assert not provider.created
    assert summary.outcomes[-1] == {
        "status": "canary_new_batch_forbidden",
        "detail": "same_attempt_recovery_is_lookup_only",
    }


@pytest.mark.asyncio
async def test_registration_failure_leaves_lease_for_dispatch_key_recovery():
    item = _item()
    repo = FakeRepository(claimed=(item,))
    repo.register_error = BatchRepositoryError(
        "batch_database_unavailable",
        retryable=True,
    )
    provider = FakeProvider()

    summary = await _dispatcher(repo, provider).submit_once()

    assert summary.errors == 1
    assert provider.created
    assert repo.failed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable", [True, False])
async def test_ambiguous_provider_error_leaves_same_attempt_lease(retryable):
    class UnavailableProvider(FakeProvider):
        async def create_batch(self, **_kwargs):
            from core.batch.openai_client import OpenAIBatchError

            raise OpenAIBatchError(
                "openai_batch_unavailable",
                retryable=retryable,
            )

    repo = FakeRepository(claimed=(_item(),))
    provider = UnavailableProvider()

    summary = await _dispatcher(repo, provider).submit_once()

    assert summary.errors == 1
    assert repo.failed == []
    assert provider.uploads
    assert not provider.created


def _success_line(custom_id):
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
                        "text": json.dumps({"draft": "needs review"}),
                    }],
                }],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {
                        "cached_tokens": 10,
                        "cache_write_tokens": 0,
                    },
                    "output_tokens": 50,
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_completed_partial_batch_reconciles_success_and_failure_by_custom_id():
    success = _item()
    failed = _item("22222222-2222-4222-8222-222222222222")
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        error_file_id="file_error123",
        request_total=2,
        request_completed=1,
        request_failed=1,
    )
    provider.files["file_output123"] = (
        json.dumps(_success_line(success.custom_id)) + "\n"
    ).encode()
    provider.files["file_error123"] = (
        json.dumps({
            "custom_id": failed.custom_id,
            "response": None,
            "error": {"code": "request_timeout", "message": "private"},
        }) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.completed == 1
    assert summary.failed == 1
    assert repo.completed[0]["job_id"] == success.job_id
    assert repo.completed[0]["actual_cost_usd"] == Decimal("0.000040")
    assert repo.failed[0]["job_id"] == failed.job_id
    assert repo.failed[0]["retryable"] is True
    assert repo.finalized == ["batch_abc123"]


@pytest.mark.asyncio
async def test_success_cost_overage_is_failed_alerted_and_never_refailed():
    item = _item()
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    repo.complete_settlement = "cost_cap_breached"
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    provider.files["file_output123"] = (
        json.dumps(_success_line(item.custom_id)) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.completed == 0
    assert summary.failed == 1
    assert summary.errors == 1
    assert repo.completed[0]["job_id"] == item.job_id
    assert repo.failed == []
    assert repo.finalized == ["batch_abc123"]
    assert {
        "status": "batch_cost_cap_breached",
        "detail": f"job_id={item.job_id}",
    } in summary.outcomes


@pytest.mark.asyncio
async def test_commit_unknown_settlement_never_switches_to_failure_rpc():
    item = _item()
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    repo.complete_error = BatchRepositoryError(
        "invalid_batch_settlement_response",
        retryable=True,
    )
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    provider.files["file_output123"] = (
        json.dumps(_success_line(item.custom_id)) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.completed == 0
    assert summary.failed == 0
    assert summary.errors == 1
    assert repo.completed == []
    assert repo.failed == []
    assert repo.finalized == []
    assert {
        "status": "poll_failed",
        "detail": "invalid_batch_settlement_response",
    } in summary.outcomes


@pytest.mark.asyncio
async def test_nonretryable_result_rejection_settles_usage_and_finalizes_batch():
    item = _item()
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    repo.complete_error = BatchRepositoryError(
        "batch_database_rpc_failed",
        retryable=False,
    )
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    provider.files["file_output123"] = (
        json.dumps(_success_line(item.custom_id)) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.completed == 0
    assert summary.failed == 1
    assert summary.errors == 0
    assert repo.completed == []
    assert repo.failed == [{
        "job_id": item.job_id,
        "provider_batch_id": "batch_abc123",
        "error_code": "batch_result_rejected",
        "retryable": False,
        "available_at": None,
        "input_tokens": 100,
        "output_tokens": 50,
        "actual_cost_usd": Decimal("0.000040"),
    }]
    assert repo.finalized == ["batch_abc123"]


@pytest.mark.asyncio
@pytest.mark.parametrize("complete_retryable", [True, False])
async def test_unsettled_result_error_never_finalizes_batch(complete_retryable):
    item = _item()
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    repo.complete_error = BatchRepositoryError(
        "batch_database_unavailable",
        retryable=complete_retryable,
    )
    if not complete_retryable:
        repo.fail_error = BatchRepositoryError(
            "batch_database_unavailable",
            retryable=True,
        )
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    provider.files["file_output123"] = (
        json.dumps(_success_line(item.custom_id)) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.errors == 1
    assert summary.completed == 0
    assert summary.failed == 0
    assert repo.finalized == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (b"", "openai_invalid_batch_output"),
        (b"{broken-json\n", "openai_invalid_batch_output"),
    ],
)
async def test_terminal_malformed_result_is_quarantined_and_finalized(
    payload,
    expected_code,
):
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    provider.files["file_output123"] = payload

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.errors == 1
    assert repo.finalized == ["batch_abc123"]
    assert {
        "status": "batch_quarantined",
        "detail": expected_code,
    } in summary.outcomes


@pytest.mark.asyncio
async def test_terminal_unknown_custom_id_is_quarantined_and_finalized():
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    provider.files["file_output123"] = (
        json.dumps(_success_line("unknown:generate:1")) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.errors == 1
    assert repo.finalized == ["batch_abc123"]
    assert {
        "status": "batch_quarantined",
        "detail": "openai_invalid_custom_id",
    } in summary.outcomes


@pytest.mark.asyncio
async def test_terminal_valid_but_nonmember_custom_id_is_finalized():
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    repo.complete_error = BatchRepositoryError(
        "batch_database_rpc_failed",
        retryable=False,
    )
    repo.fail_error = BatchRepositoryError(
        "batch_database_rpc_failed",
        retryable=False,
    )
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    provider.files["file_output123"] = (
        json.dumps(_success_line(
            "22222222-2222-4222-8222-222222222222:generate:1"
        )) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.errors == 1
    assert repo.finalized == ["batch_abc123"]
    assert {
        "status": "batch_quarantined",
        "detail": "batch_database_rpc_failed",
    } in summary.outcomes


@pytest.mark.asyncio
async def test_retryable_terminal_download_error_is_not_finalized():
    class RetryableDownloadProvider(FakeProvider):
        async def download_file(self, _file_id):
            from core.batch.openai_client import OpenAIBatchError

            raise OpenAIBatchError(
                "openai_batch_unavailable",
                retryable=True,
            )

    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    provider = RetryableDownloadProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.errors == 1
    assert repo.finalized == []
    assert {
        "status": "poll_failed",
        "detail": "openai_batch_unavailable",
    } in summary.outcomes


@pytest.mark.asyncio
async def test_provider_identity_mismatch_is_never_finalized():
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        provider_batch_id="batch_other123",
        status="failed",
        request_total=1,
        request_failed=1,
    )

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.errors == 1
    assert repo.polled == []
    assert repo.finalized == []


@pytest.mark.asyncio
async def test_expired_batch_never_downloads_or_completes_an_artifact():
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="expired",
        request_total=2,
        request_completed=0,
        request_failed=2,
    )

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.failed == 2
    assert not provider.downloaded
    assert not repo.completed
    assert repo.finalized == ["batch_abc123"]


@pytest.mark.asyncio
async def test_cancelled_batch_reconciles_present_partial_output_before_missing():
    success = _item()
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="cancelled",
        output_file_id="file_output123",
        request_total=2,
        request_completed=1,
        request_failed=0,
    )
    provider.files["file_output123"] = (
        json.dumps(_success_line(success.custom_id)) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.completed == 1
    assert summary.failed == 1
    assert repo.completed[0]["job_id"] == success.job_id
    assert provider.downloaded == ["file_output123"]
    assert repo.finalized == ["batch_abc123"]


@pytest.mark.asyncio
async def test_billable_refusal_is_settled_and_never_auto_retried():
    item = _item()
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    line = _success_line(item.custom_id)
    line["response"]["body"]["output"] = [{
        "type": "message",
        "content": [{"type": "refusal", "refusal": "Cannot comply."}],
    }]
    provider.files["file_output123"] = (
        json.dumps(line) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.failed == 1
    assert repo.failed[0]["retryable"] is False
    assert repo.failed[0]["actual_cost_usd"] == Decimal("0.000040")
    assert repo.failed[0]["input_tokens"] == 100
    assert repo.failed[0]["output_tokens"] == 50
    assert repo.finalized == ["batch_abc123"]


@pytest.mark.asyncio
async def test_failed_result_cost_overage_is_terminal_and_alerted():
    item = _item()
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    repo.fail_settlement = "cost_cap_breached"
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    line = _success_line(item.custom_id)
    line["response"]["body"]["output"] = [{
        "type": "message",
        "content": [{"type": "refusal", "refusal": "Cannot comply."}],
    }]
    provider.files["file_output123"] = (
        json.dumps(line) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.completed == 0
    assert summary.failed == 1
    assert summary.errors == 1
    assert repo.failed[0]["job_id"] == item.job_id
    assert repo.failed[0]["retryable"] is False
    assert repo.finalized == ["batch_abc123"]
    assert {
        "status": "batch_cost_cap_breached",
        "detail": f"job_id={item.job_id}",
    } in summary.outcomes


@pytest.mark.asyncio
async def test_missing_usage_conservatively_charges_full_reservation():
    item = _item()
    repo = FakeRepository(active=(
        ActiveBatch("batch_abc123", "in_progress"),
    ))
    provider = FakeProvider()
    provider.retrieve_snapshot = _snapshot(
        status="completed",
        output_file_id="file_output123",
        request_total=1,
        request_completed=1,
    )
    line = _success_line(item.custom_id)
    del line["response"]["body"]["usage"]
    provider.files["file_output123"] = (
        json.dumps(line) + "\n"
    ).encode()

    summary = await _dispatcher(repo, provider).poll_once()

    assert summary.failed == 1
    assert repo.failed[0]["retryable"] is False
    assert repo.failed[0]["charge_full_reservation"] is True
    assert "actual_cost_usd" not in repo.failed[0]
    assert repo.finalized == ["batch_abc123"]
