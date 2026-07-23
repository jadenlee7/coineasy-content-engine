from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.automation.daily_runner import OfficialXDailyRunner
from core.automation.generation_client import GeneratedCatalogResult
from core.automation.models import (
    AutomationState,
    ClaimedJob,
    PendingSource,
    QueueResult,
)
from core.automation.repository import AutomationRepositoryError
from core.automation.settings import AUTOMATION_CLIENTS, AutomationSettings
from core.sources.x_client import XTransientError


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
VERSION_ID = "99999999-9999-4999-8999-999999999999"
NOW = datetime(2026, 7, 22, 0, 10, tzinfo=timezone.utc)


def settings(**overrides):
    values = {
        "supabase_url": "https://project-ref.supabase.co",
        "supabase_service_role_key": "s" * 64,
        "workspace_id": WORKSPACE_ID,
        "x_bearer_token": "x" * 32,
        "studio_base_url": "https://coineasy-newscard.netlify.app",
        "studio_automation_token": "a" * 64,
    }
    values.update(overrides)
    return AutomationSettings(**values)


def pending(
    client_id: str,
    *,
    text: str = "Our official mainnet product update is now live.",
    note: bool = False,
) -> PendingSource:
    source_id = str(uuid.uuid5(uuid.UUID(WORKSPACE_ID), f"source:{client_id}"))
    handle = {
        "yellow": "Yellow",
        "origintrail": "origin_trail",
        "squid": "SquidRouter",
        "babylon": "babylonlabs_io",
    }[client_id]
    post_id = str(700 + AUTOMATION_CLIENTS.index(client_id))
    return PendingSource(
        source_item_id=source_id,
        post_id=post_id,
        source_content=text,
        source_url=f"https://x.com/{handle}/status/{post_id}",
        source_image_url="",
        published_at="2026-07-22T00:00:00Z",
        metrics={},
        is_note_tweet=note,
    )


class FakeRepository:
    def __init__(self, states):
        self.states = states
        self.records = []
        self.queues = []
        self.claims = []
        self.completed = []
        self.failed = []
        self.complete_error = False

    async def get_state(self, **kwargs):
        return self.states[kwargs["client_id"]]

    async def record_sources(self, **kwargs):
        self.records.append(kwargs)
        client_id = kwargs["client_id"]
        raw = kwargs["source_items"]
        converted = tuple(
            PendingSource(
                source_item_id=str(uuid.uuid5(uuid.UUID(WORKSPACE_ID), f"source:{client_id}:{item['external_id']}")),
                post_id=item["external_id"],
                source_content=item["source_content"],
                source_url=item["source_url"],
                source_image_url=item["source_image_url"],
                published_at=item["published_at"],
                media=tuple(item["media"]),
                metrics=item["metrics"],
                is_note_tweet=item["is_note_tweet"],
            )
            for item in raw
        )
        state = AutomationState(
            last_cursor=kwargs["next_cursor"],
            draft_reserved_today=False,
            pending_sources=converted,
        )
        self.states[client_id] = state
        return state

    async def queue_job(self, **kwargs):
        self.queues.append(kwargs)
        job_id = str(uuid.uuid5(uuid.UUID(WORKSPACE_ID), f"job:{kwargs['request_id']}"))
        self.claims.append(ClaimedJob(
            job_id=job_id,
            client_id=kwargs["client_id"],
            content_kind=kwargs["content_kind"],
            request_id=kwargs["request_id"],
            source_content=kwargs["source_content"],
            source_url=kwargs["source_url"],
            source_image_url=kwargs["source_image_url"],
            manual_only=kwargs["manual_only"],
            attempts=1,
            max_attempts=3,
            locked_by="placeholder",
        ))
        return QueueResult(job_id=job_id, status="queued")

    async def claim_job(self, **kwargs):
        if not self.claims:
            return None
        item = self.claims.pop(0)
        return ClaimedJob(**{**item.__dict__, "locked_by": kwargs["worker_id"]})

    async def complete_job(self, **kwargs):
        if self.complete_error:
            raise AutomationRepositoryError("automation_database_unavailable", retryable=True)
        self.completed.append(kwargs)

    async def fail_job(self, **kwargs):
        self.failed.append(kwargs)


class FakeXClient:
    def __init__(self, posts=None, error=None):
        self.posts = posts or []
        self.error = error
        self.calls = []

    async def get_recent_tweets(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.posts


class FakeGenerationClient:
    def __init__(self):
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return GeneratedCatalogResult(
            content_item_id=kwargs["request_id"],
            content_version_id=VERSION_ID,
            asset_ids=() if kwargs["content_kind"] == "article" else (
                "88888888-8888-4888-8888-888888888888",
            ),
            reused=False,
        )


def runner(repo, x_client, generation, **setting_overrides):
    return OfficialXDailyRunner(
        settings=settings(**setting_overrides),
        repository=repo,
        x_client=x_client,
        generation_client=generation,
        clients_dir=ROOT / "clients",
        now_factory=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_recorded_note_recovers_without_x_and_finishes_as_review_article():
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["squid"] = AutomationState(
        "701",
        False,
        (pending("squid", text="A" * 320, note=True),),
    )
    repo = FakeRepository(states)
    x_client = FakeXClient()
    generation = FakeGenerationClient()

    summary = await runner(repo, x_client, generation).run()

    assert x_client.calls == []
    assert repo.queues[0]["content_kind"] == "article"
    assert repo.queues[0]["manual_only"] is False
    assert generation.calls[0]["content_kind"] == "article"
    assert generation.calls[0]["template_style"] == "classic"
    assert len(repo.completed) == 1
    assert summary.generated == 1
    assert any(item["status"] == "needs_review" for item in summary.outcomes)


@pytest.mark.asyncio
async def test_dry_run_reads_and_plans_but_never_writes_or_generates():
    states = {
        client_id: AutomationState(None, client_id != "yellow", ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    x_client = FakeXClient(posts=[{
        "id": "123",
        "text": "Our official integration update is now live.",
        "created_at": "2026-07-22T00:00:00Z",
        "url": "https://x.com/Yellow/status/123",
        "is_retweet": False,
        "is_reply": False,
        "is_note_tweet": False,
        "metrics": {},
        "media": [],
        "source_image_url": "",
    }])
    generation = FakeGenerationClient()

    summary = await runner(repo, x_client, generation).run(dry_run=True)

    assert repo.records == []
    assert repo.queues == []
    assert repo.completed == []
    assert generation.calls == []
    assert any(
        item == {"client_id": "yellow", "status": "planned", "detail": "daily_news"}
        for item in summary.outcomes
    )


@pytest.mark.asyncio
async def test_catalog_completion_failure_leaves_lease_for_idempotent_retry():
    states = {
        client_id: AutomationState(None, client_id != "yellow", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["yellow"] = AutomationState(None, False, (pending("yellow"),))
    repo = FakeRepository(states)
    repo.complete_error = True
    generation = FakeGenerationClient()

    summary = await runner(repo, FakeXClient(), generation).run()

    assert len(generation.calls) == 1
    assert repo.completed == []
    assert repo.failed == []
    assert summary.errors == 1
    assert any(item.get("detail") == "automation_database_unavailable" for item in summary.outcomes)


def test_request_uuid_is_stable_and_bound_to_mode():
    states = {client_id: AutomationState(None, True, ()) for client_id in AUTOMATION_CLIENTS}
    daily_runner = runner(FakeRepository(states), FakeXClient(), FakeGenerationClient())

    first = daily_runner._request_id(
        client_id="yellow",
        source_item_id="22222222-2222-4222-8222-222222222222",
        content_kind="daily_news",
    )
    second = daily_runner._request_id(
        client_id="yellow",
        source_item_id="22222222-2222-4222-8222-222222222222",
        content_kind="daily_news",
    )
    article = daily_runner._request_id(
        client_id="yellow",
        source_item_id="22222222-2222-4222-8222-222222222222",
        content_kind="article",
    )

    assert first == second
    assert first != article
    assert str(uuid.UUID(first)) == first


def test_scheduled_runner_rejects_tutorial_auto_generation():
    states = {client_id: AutomationState(None, True, ()) for client_id in AUTOMATION_CLIENTS}
    with pytest.raises(ValueError, match="tutorial automation"):
        runner(
            FakeRepository(states),
            FakeXClient(),
            FakeGenerationClient(),
            enable_tutorials=True,
        )


@pytest.mark.asyncio
async def test_one_client_intake_failure_does_not_stop_other_clients():
    states = {
        client_id: AutomationState(None, client_id not in {"yellow", "origintrail"}, ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail"),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()

    summary = await runner(
        repo,
        FakeXClient(error=XTransientError("safe provider failure")),
        generation,
    ).run()

    assert summary.errors == 1
    assert summary.generated == 1
    assert generation.calls[0]["client_id"] == "origintrail"
    assert any(
        item.get("client_id") == "yellow"
        and item.get("detail") == "x_temporarily_unavailable"
        for item in summary.outcomes
    )


@pytest.mark.asyncio
async def test_local_daily_limit_bounds_new_queue_reservations():
    states = {
        client_id: AutomationState(None, False, (pending(client_id),))
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    generation = FakeGenerationClient()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        daily_draft_limit=1,
    ).run()

    assert len(repo.queues) == 1
    assert summary.queued == 1
    assert sum(item["status"] == "local_daily_limit" for item in summary.outcomes) == 3
