from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.automation.daily_runner import (
    OfficialXDailyRunner,
    choose_automation_template_style,
)
from core.automation.content_signals import (
    ContentSignalsError,
    ContentSignalsSnapshot,
    DemandTerm,
    PromotionCandidate,
)
from core.automation.generation_client import GeneratedCatalogResult
from core.automation.models import (
    AutomationState,
    ClaimedJob,
    PendingSource,
    QueueResult,
    StyleReference,
    StyleReferencePack,
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
    source_image_url: str = "",
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
        source_image_url=source_image_url,
        published_at="2026-07-22T00:00:00Z",
        metrics={},
        is_note_tweet=note,
    )


def test_squid_photo_news_uses_original_visual_localization_only():
    assert choose_automation_template_style(
        client_id="squid",
        content_kind="daily_news",
        source_image_url="https://pbs.twimg.com/media/official.jpg",
    ) == "remix"
    assert choose_automation_template_style(
        client_id="squid",
        content_kind="daily_news",
        source_image_url="",
    ) == "classic"
    assert choose_automation_template_style(
        client_id="squid",
        content_kind="article",
        source_image_url="https://pbs.twimg.com/media/official.jpg",
    ) == "classic"
    assert choose_automation_template_style(
        client_id="yellow",
        content_kind="daily_news",
        source_image_url="https://pbs.twimg.com/media/official.jpg",
    ) == "classic"


class FakeRepository:
    def __init__(self, states):
        self.states = states
        self.records = []
        self.ranking_evidence = []
        self.learning_evidence = []
        self.promotion_candidates = []
        self.queues = []
        self.style_packs = []
        self.claims = []
        self.completed = []
        self.failed = []
        self.complete_error = False
        self.ranking_evidence_error = None
        self.learning_evidence_error = None
        self.promotion_candidates_error = None

    async def get_state(self, **kwargs):
        return self.states[kwargs["client_id"]]

    async def record_ranking_evidence(self, **kwargs):
        if self.ranking_evidence_error:
            raise self.ranking_evidence_error
        self.ranking_evidence.append(kwargs)
        return "a" * 64

    async def record_learning_evidence(self, **kwargs):
        if self.learning_evidence_error:
            raise self.learning_evidence_error
        self.learning_evidence.append(kwargs)

    async def record_promotion_candidates(self, **kwargs):
        if self.promotion_candidates_error:
            raise self.promotion_candidates_error
        self.promotion_candidates.append(kwargs)
        return len(kwargs["snapshot"].promotion_candidates)

    async def record_sources(self, **kwargs):
        self.records.append(kwargs)
        client_id = kwargs["client_id"]
        previous = self.states[client_id]
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
        by_post_id = {
            item.post_id: item
            for item in previous.pending_sources
        }
        by_post_id.update({
            item.post_id: item
            for item in converted
        })
        state = AutomationState(
            last_cursor=kwargs["next_cursor"],
            draft_reserved_today=previous.draft_reserved_today,
            pending_sources=tuple(by_post_id.values()),
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
            primary_source_item_id=kwargs["source_item_ids"][0],
            source_content=kwargs["source_content"],
            source_url=kwargs["source_url"],
            source_image_url=kwargs["source_image_url"],
            manual_only=kwargs["manual_only"],
            attempts=1,
            max_attempts=3,
            locked_by="placeholder",
        ))
        return QueueResult(job_id=job_id, status="queued")

    async def get_or_create_style_reference_pack(self, **kwargs):
        self.style_packs.append(kwargs)
        client_id = kwargs["client_id"]
        reference_id = str(uuid.uuid5(
            uuid.UUID(WORKSPACE_ID),
            f"style-reference:{client_id}",
        ))
        handle = {
            "yellow": "Yellow",
            "origintrail": "origin_trail",
            "squid": "SquidRouter",
            "babylon": "babylonlabs_io",
        }[client_id]
        return StyleReferencePack(
            request_id=kwargs["request_id"],
            primary_source_item_id=kwargs["primary_source_item_id"],
            reference_pack_hash="a" * 32,
            references=(
                StyleReference(
                    source_item_id=reference_id,
                    source_url=f"https://x.com/{handle}/status/600",
                    text="A prior official post used only for cadence.",
                    published_at="2026-07-21T00:00:00Z",
                ),
            ),
            reused=len(self.style_packs) > 1,
        )

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
    def __init__(self, posts=None, error=None, errors_by_handle=None):
        self.posts = posts or []
        self.error = error
        self.errors_by_handle = errors_by_handle or {}
        self.calls = []

    async def get_recent_tweets(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        handle = args[0].lstrip("@")
        if handle in self.errors_by_handle:
            raise self.errors_by_handle[handle]
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


class FakeSignalsClient:
    def __init__(
        self,
        terms=(),
        error=None,
        *,
        with_candidate=False,
        tutorial_priority=0.0,
    ):
        self.terms = terms
        self.error = error
        self.with_candidate = with_candidate
        self.tutorial_priority = tutorial_priority
        self.calls = []

    async def fetch(self, *, client_id, now):
        self.calls.append({"client_id": client_id, "now": now})
        if self.error:
            raise self.error
        return ContentSignalsSnapshot(
            schema_version="1.2",
            client_id=client_id,
            generated_at=now,
            window_start=now - timedelta(days=7),
            window_end=now,
            demand_terms=tuple(
                DemandTerm(term=term, weight=weight, sources=("community",))
                for term, weight in self.terms
            ),
            promotion_policy_version="content-performance-v1",
            promotion_candidates=(
                PromotionCandidate(
                    candidate_id="b" * 64,
                    channel="x",
                    source_url="https://x.com/squidkorea/status/123456789",
                    published_at=now - timedelta(hours=18),
                    score=0.875,
                    reach_percentile=0.9,
                    interaction_percentile=0.8,
                    community_match_count=3,
                    cohort_size=12,
                    observation_age_hours=18,
                    recommended_formats=("article", "tutorial"),
                    reason_codes=(
                        "high_reach",
                        "high_interaction",
                        "community_alignment",
                        "tutorial_learning_signal",
                    ),
                ),
            ) if self.with_candidate else (),
            tutorial_priority=self.tutorial_priority,
        )


def runner(
    repo,
    x_client,
    generation,
    *,
    content_signals_client=None,
    **setting_overrides,
):
    return OfficialXDailyRunner(
        settings=settings(**setting_overrides),
        repository=repo,
        x_client=x_client,
        generation_client=generation,
        content_signals_client=content_signals_client,
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

    assert [call[0][0].lstrip("@") for call in x_client.calls] == [
        "Yellow",
        "origin_trail",
        "SquidRouter",
        "babylonlabs_io",
    ]
    assert repo.queues[0]["content_kind"] == "article"
    assert repo.queues[0]["manual_only"] is False
    assert generation.calls[0]["content_kind"] == "article"
    assert generation.calls[0]["template_style"] == "classic"
    assert len(repo.style_packs) == 2
    assert repo.style_packs[0]["request_id"] == repo.style_packs[1]["request_id"]
    assert (
        generation.calls[0]["style_reference_pack_hash"]
        == "a" * 32
    )
    assert len(generation.calls[0]["style_references"]) == 1
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
        FakeXClient(errors_by_handle={
            "Yellow": XTransientError("safe provider failure"),
        }),
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
async def test_pending_source_recovers_when_fresh_x_poll_is_unavailable():
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["squid"] = AutomationState(
        "702",
        False,
        (pending("squid"),),
    )
    repo = FakeRepository(states)

    summary = await runner(
        repo,
        FakeXClient(errors_by_handle={
            "SquidRouter": XTransientError("safe provider failure"),
        }),
        FakeGenerationClient(),
    ).run()

    assert len(repo.queues) == 1
    assert summary.generated == 1
    assert summary.errors == 0
    assert any(
        item == {
            "client_id": "squid",
            "status": "source_refresh_unavailable",
            "detail": "x_temporarily_unavailable",
        }
        for item in summary.outcomes
    )


@pytest.mark.asyncio
async def test_squid_photo_daily_news_reaches_generation_as_remix():
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["squid"] = AutomationState(
        "702",
        False,
        (pending(
            "squid",
            source_image_url="https://pbs.twimg.com/media/official.jpg",
        ),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()

    summary = await runner(repo, FakeXClient(), generation).run()

    assert summary.generated == 1
    assert generation.calls[0]["client_id"] == "squid"
    assert generation.calls[0]["template_style"] == "remix"
    assert generation.calls[0]["source_image_url"].endswith("/official.jpg")


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
    assert len(repo.records) == 4
    assert summary.queued == 1
    assert sum(item["status"] == "local_daily_limit" for item in summary.outcomes) == 3


@pytest.mark.asyncio
async def test_dry_run_merges_fresh_post_over_pending_copy_without_writes():
    stale = pending("yellow", text="gm")
    states = {
        client_id: AutomationState(None, client_id != "yellow", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["yellow"] = AutomationState(None, False, (stale,))
    fresh = [{
        "id": stale.post_id,
        "text": "Our official mainnet upgrade is now live.",
        "created_at": "2026-07-22T00:05:00Z",
        "url": stale.source_url,
        "is_retweet": False,
        "is_reply": False,
        "is_note_tweet": False,
        "metrics": {"like_count": 5},
        "media": [],
        "source_image_url": "",
    }]
    repo = FakeRepository(states)

    summary = await runner(
        repo,
        FakeXClient(posts=fresh),
        FakeGenerationClient(),
    ).run(dry_run=True)

    assert repo.records == []
    assert repo.queues == []
    assert summary.errors == 0
    assert any(
        item == {
            "client_id": "yellow",
            "status": "planned",
            "detail": "daily_news",
        }
        for item in summary.outcomes
    )


@pytest.mark.asyncio
async def test_reserved_clients_still_poll_and_record_without_queueing():
    states = {
        client_id: AutomationState(str(800 + index), True, (pending(client_id),))
        for index, client_id in enumerate(AUTOMATION_CLIENTS)
    }
    posts = [{
        "id": "999",
        "text": "A new official ecosystem update is live.",
        "created_at": "2026-07-22T00:05:00Z",
        "url": "https://x.com/Yellow/status/999",
        "is_retweet": False,
        "is_reply": False,
        "is_note_tweet": False,
        "metrics": {},
        "media": [],
        "source_image_url": "",
    }]
    repo = FakeRepository(states)
    x_client = FakeXClient(posts=posts)
    generation = FakeGenerationClient()

    summary = await runner(repo, x_client, generation).run()

    assert len(x_client.calls) == 4
    assert all(call[1]["max_results"] == 200 for call in x_client.calls)
    assert all(call[1]["require_complete"] is True for call in x_client.calls)
    assert len(repo.records) == 4
    assert all(record["source_items"] for record in repo.records)
    assert repo.queues == []
    assert generation.calls == []
    assert sum(
        item["status"] == "already_reserved"
        for item in summary.outcomes
    ) == 4


@pytest.mark.asyncio
async def test_signals_reorder_pending_sources_but_never_enter_generation_input():
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    ecosystem = pending(
        "squid",
        text="A detailed ecosystem update for builders.",
    )
    liquidity = PendingSource(
        **{
            **pending(
                "squid",
                text="A detailed liquidity update for builders.",
            ).__dict__,
            "source_item_id": "77777777-7777-4777-8777-777777777777",
            "post_id": "799",
            "source_url": "https://x.com/SquidRouter/status/799",
            "published_at": "2026-07-22T00:01:00Z",
        }
    )
    states["squid"] = AutomationState(
        "799",
        False,
        (ecosystem, liquidity),
    )
    repo = FakeRepository(states)
    signals = FakeSignalsClient(terms=(("ecosystem", 1.0),))
    generation = FakeGenerationClient()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        content_signals_client=signals,
    ).run()

    assert repo.queues[0]["source_content"] == ecosystem.source_content
    assert len(repo.ranking_evidence) == 1
    assert len(repo.learning_evidence) == 1
    assert repo.ranking_evidence[0]["ranking_version"] == "official-x-demand-v2"
    assert generation.calls[0]["source_content"] == ecosystem.source_content
    assert "demand_terms" not in generation.calls[0]
    assert any(
        item == {
            "client_id": "squid",
            "status": "signals_used",
            "detail": "term_count=1,tutorial_priority=0.000",
        }
        for item in summary.outcomes
    )


@pytest.mark.asyncio
async def test_promotion_persistence_fails_open_after_ranking_evidence():
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    ecosystem = pending(
        "squid",
        text="A detailed ecosystem update for builders.",
    )
    liquidity = PendingSource(
        **{
            **pending(
                "squid",
                text="A detailed liquidity update for builders.",
            ).__dict__,
            "source_item_id": "77777777-7777-4777-8777-777777777777",
            "post_id": "799",
            "source_url": "https://x.com/SquidRouter/status/799",
            "published_at": "2026-07-22T00:01:00Z",
        }
    )
    states["squid"] = AutomationState(None, False, (ecosystem, liquidity))
    repo = FakeRepository(states)
    repo.promotion_candidates_error = AutomationRepositoryError(
        "automation_database_unavailable",
        retryable=True,
    )
    signals = FakeSignalsClient(
        terms=(("ecosystem", 1.0),),
        with_candidate=True,
    )

    summary = await runner(
        repo,
        FakeXClient(),
        FakeGenerationClient(),
        content_signals_client=signals,
    ).run()

    assert len(repo.ranking_evidence) == 1
    assert repo.queues[0]["source_content"] == ecosystem.source_content
    assert summary.errors == 0
    assert any(
        item == {
            "client_id": "squid",
            "status": "promotion_candidates_unavailable",
            "detail": "performance_evidence_not_recorded",
        }
        for item in summary.outcomes
    )


@pytest.mark.asyncio
async def test_signal_failure_falls_back_to_official_ranking_without_run_error():
    states = {
        client_id: AutomationState(None, client_id != "yellow", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["yellow"] = AutomationState(
        None,
        False,
        (pending("yellow"),),
    )
    signals = FakeSignalsClient(error=ContentSignalsError(
        "content_signals_unavailable",
        retryable=True,
    ))
    repo = FakeRepository(states)

    summary = await runner(
        repo,
        FakeXClient(),
        FakeGenerationClient(),
        content_signals_client=signals,
    ).run()

    assert len(repo.queues) == 1
    assert summary.generated == 1
    assert summary.errors == 0
    assert any(
        item == {"client_id": "yellow", "status": "signals_unavailable"}
        for item in summary.outcomes
    )


@pytest.mark.asyncio
async def test_unrecorded_signal_evidence_falls_back_to_official_ranking():
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    ecosystem = pending(
        "squid",
        text="A detailed ecosystem update for builders.",
    )
    liquidity = PendingSource(
        **{
            **pending(
                "squid",
                text="A detailed liquidity update for builders.",
            ).__dict__,
            "source_item_id": "77777777-7777-4777-8777-777777777777",
            "post_id": "799",
            "source_url": "https://x.com/SquidRouter/status/799",
            "published_at": "2026-07-22T00:01:00Z",
        }
    )
    states["squid"] = AutomationState(None, False, (ecosystem, liquidity))
    repo = FakeRepository(states)
    repo.ranking_evidence_error = AutomationRepositoryError(
        "automation_database_unavailable",
        retryable=True,
    )
    signals = FakeSignalsClient(terms=(("ecosystem", 1.0),))

    summary = await runner(
        repo,
        FakeXClient(),
        FakeGenerationClient(),
        content_signals_client=signals,
    ).run()

    assert repo.queues[0]["source_content"] == liquidity.source_content
    assert any(
        item == {
            "client_id": "squid",
            "status": "signals_unavailable",
            "detail": "ranking_evidence_not_recorded",
        }
        for item in summary.outcomes
    )
