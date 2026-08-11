from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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
from core.batch.bridge import BatchQueueBridge
from core.batch.canary import (
    CanaryConfigApproval,
    canonical_sha256,
    config_subject,
)
from core.batch.policy import BatchPolicy
from core.batch.repository import BatchRepositoryError
from core.batch.settings import BatchSettings
from core.sources.x_client import XTransientError
from core.squid_visual_style import SQUID_VISUAL_POLICY_VERSION


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
VERSION_ID = "99999999-9999-4999-8999-999999999999"
NOW = datetime(2026, 7, 22, 0, 10, tzinfo=timezone.utc)
CANARY_KST_DAY_START = datetime(2026, 7, 21, 15, tzinfo=timezone.utc)


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
    media: tuple[dict[str, object], ...] = (),
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
        media=media,
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


def test_origintrail_source_payload_preserves_x_article_evidence():
    article_evidence = {
        "article_id": "2084276731330936832",
        "article_url": "https://x.com/i/article/2084276731330936832",
        "title": "July 2026 OriginTrail recap",
        "source_content_sha256": "a" * 64,
        "retrieval_method": "x_api_post_lookup",
    }
    payload = OfficialXDailyRunner._source_payload(
        {
            "id": "2084283287518798116",
            "text": "Pinned X Article body.",
            "url": (
                "https://x.com/origin_trail/status/2084283287518798116"
            ),
            "created_at": "2026-07-22T00:00:00Z",
            "is_retweet": False,
            "is_reply": False,
            "is_quote": False,
            "article_evidence": article_evidence,
        },
        include_standalone_signals=True,
    )

    assert payload["article_evidence"] == article_evidence
    assert payload["article_evidence"] is not article_evidence


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
        self.batch_handoffs = []
        self.batch_recoveries = []
        self.batch_recovery_result = False
        self.batch_recovery_error = None
        self.execution_planes = {}
        self.failed = []
        self.complete_error = False
        self.batch_handoff_error = None
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
            kst_date=kwargs["kst_date"],
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
            origintrail_batch_eligible=(
                kwargs["client_id"] == "origintrail"
                and kwargs["content_kind"] == "daily_news"
                and not kwargs["source_image_url"]
            ),
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

    async def bind_execution_plane(self, **kwargs):
        plane = self.execution_planes.get(kwargs["job_id"])
        if plane is None:
            plane = kwargs["requested_plane"]
            self.execution_planes[kwargs["job_id"]] = plane
        return plane

    async def complete_job(self, **kwargs):
        if self.complete_error:
            raise AutomationRepositoryError("automation_database_unavailable", retryable=True)
        self.completed.append(kwargs)

    async def complete_batch_handoff(self, **kwargs):
        if self.batch_handoff_error:
            raise self.batch_handoff_error
        self.batch_handoffs.append(kwargs)

    async def recover_batch_handoff(self, **kwargs):
        self.batch_recoveries.append(kwargs)
        if self.batch_recovery_error:
            raise self.batch_recovery_error
        return self.batch_recovery_result

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
    batch_settings=None,
    batch_bridge=None,
    **setting_overrides,
):
    return OfficialXDailyRunner(
        settings=settings(**setting_overrides),
        repository=repo,
        x_client=x_client,
        generation_client=generation,
        batch_settings=batch_settings,
        batch_bridge=batch_bridge,
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
async def test_allowed_clients_scope_prevents_other_client_intake():
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    x_client = FakeXClient()

    summary = await runner(
        FakeRepository(states),
        x_client,
        FakeGenerationClient(),
        allowed_clients=("origintrail",),
    ).run(dry_run=True)

    assert [call[0][0].lstrip("@") for call in x_client.calls] == [
        "origin_trail",
    ]
    assert {item["client_id"] for item in summary.outcomes} <= {
        "origintrail",
    }


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
    squid = daily_runner._request_id(
        client_id="squid",
        source_item_id="22222222-2222-4222-8222-222222222222",
        content_kind="daily_news",
    )
    squid_article = daily_runner._request_id(
        client_id="squid",
        source_item_id="22222222-2222-4222-8222-222222222222",
        content_kind="article",
    )

    assert first == second
    assert first != article
    assert str(uuid.UUID(first)) == first
    assert squid != str(uuid.uuid5(
        uuid.UUID(WORKSPACE_ID),
        "official-x-review:v1:squid:"
        "22222222-2222-4222-8222-222222222222:daily_news",
    ))
    assert squid == str(uuid.uuid5(
        uuid.UUID(WORKSPACE_ID),
        "official-x-review:v3:double-fact-check@1:"
        f"{SQUID_VISUAL_POLICY_VERSION}:squid:"
        "22222222-2222-4222-8222-222222222222:daily_news",
    ))
    assert squid_article == str(uuid.uuid5(
        uuid.UUID(WORKSPACE_ID),
        "official-x-review:v2:double-fact-check@1:squid:"
        "22222222-2222-4222-8222-222222222222:article",
    ))
    assert first == str(uuid.uuid5(
        uuid.UUID(WORKSPACE_ID),
        "official-x-review:v2:double-fact-check@1:yellow:"
        "22222222-2222-4222-8222-222222222222:daily_news",
    ))


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
@pytest.mark.parametrize(
    "source_text",
    [
        "A new era for Squid starts today.",
        "Squid has crossed 5 million swaps across connected chains.",
        "The TGE claim phase status is available.",
        "Use MiniPay to bridge with Squid.",
        "Bouncing through the weekend with the official SQUIB mascot.",
    ],
)
async def test_every_squid_photo_daily_news_family_reaches_generation_as_remix(
    source_text,
):
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["squid"] = AutomationState(
        "702",
        False,
        (pending(
            "squid",
            text=source_text,
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
async def test_text_only_squid_worldbuilding_stops_for_manual_visual_review():
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["squid"] = AutomationState(
        "702",
        False,
        (pending("squid", text="Bouncing through the weekend like SQUIB."),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()

    summary = await runner(repo, FakeXClient(), generation).run()

    assert summary.generated == 0
    assert summary.skipped >= 1
    assert repo.queues == []
    assert generation.calls == []
    assert {
        "client_id": "squid",
        "status": "manual_visual_review_required",
        "detail": "worldbuilding",
    } in summary.outcomes


@pytest.mark.asyncio
async def test_text_only_squid_worldbuilding_note_still_queues_as_article():
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    article_text = (
        "Announcing SQUIB, our mascot and guide to the Squid ecosystem. "
        + "This complete official note explains the story, design, and role " * 8
    )
    states["squid"] = AutomationState(
        "702",
        False,
        (pending("squid", text=article_text, note=True),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()

    summary = await runner(repo, FakeXClient(), generation).run()

    assert summary.generated == 1
    assert repo.queues[0]["content_kind"] == "article"
    assert generation.calls[0]["content_kind"] == "article"
    assert not any(
        item["status"] == "manual_visual_review_required"
        for item in summary.outcomes
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_flag", ["is_retweet", "is_reply"])
async def test_squid_filters_reply_and_retweet_before_generic_source_rpc(blocked_flag):
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    valid_id = "2082883998829752783"
    blocked_id = "2082883998829752784"
    valid = {
        "id": valid_id,
        "text": "Squid routing update is now live for users.",
        "created_at": "2026-07-22T00:00:00Z",
        "url": f"https://x.com/SquidRouter/status/{valid_id}",
        "is_retweet": False,
        "is_reply": False,
        "is_quote": False,
        "metrics": {},
        "media": [],
        "source_image_url": "",
        "is_note_tweet": False,
    }
    blocked = {
        **valid,
        "id": blocked_id,
        "url": f"https://x.com/SquidRouter/status/{blocked_id}",
        blocked_flag: True,
    }
    repo = FakeRepository(states)

    summary = await runner(
        repo,
        FakeXClient(posts=[blocked, valid]),
        FakeGenerationClient(),
    ).run()

    squid_record = next(
        item for item in repo.records
        if item["client_id"] == "squid"
    )
    assert [item["external_id"] for item in squid_record["source_items"]] == [valid_id]
    assert squid_record["source_items"][0]["is_retweet"] is False
    assert squid_record["source_items"][0]["is_reply"] is False
    assert squid_record["next_cursor"] == valid_id
    assert summary.generated == 1


@pytest.mark.asyncio
async def test_fresh_squid_quote_photo_reaches_daily_news_render_path():
    image_url = "https://pbs.twimg.com/media/squid-quoted.jpg"
    post_id = "2082883998829752783"
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    quote = {
        "id": post_id,
        "text": "Canton support is now live and easy to explore with Squid.",
        "created_at": "2026-07-22T00:00:00Z",
        "url": f"https://x.com/SquidRouter/status/{post_id}",
        "is_retweet": False,
        "is_reply": False,
        "is_quote": True,
        "metrics": {},
        "media": [{
            "media_key": "quoted-photo",
            "type": "photo",
            "url": image_url,
            "width": 1080,
            "height": 1080,
        }],
        "source_image_url": image_url,
        "is_note_tweet": False,
    }
    repo = FakeRepository(states)
    generation = FakeGenerationClient()

    summary = await runner(
        repo,
        FakeXClient(posts=[quote]),
        generation,
    ).run()

    squid_record = next(
        item for item in repo.records
        if item["client_id"] == "squid"
    )
    assert squid_record["source_items"][0]["media"] == quote["media"]
    assert squid_record["source_items"][0]["is_retweet"] is False
    assert squid_record["source_items"][0]["is_reply"] is False
    assert repo.queues[0]["source_image_url"] == image_url
    assert summary.generated == 1
    assert generation.calls[0]["template_style"] == "remix"
    assert generation.calls[0]["source_image_url"] == image_url


@pytest.mark.asyncio
async def test_fresh_squid_quote_video_preview_reaches_remix_render_path():
    image_url = (
        "https://pbs.twimg.com/amplify_video_thumb/"
        "2079266440268464128/img/qxIE-WTD9Fd60z4C.jpg"
    )
    post_id = "2081031728622178334"
    states = {
        client_id: AutomationState(None, client_id != "squid", ())
        for client_id in AUTOMATION_CLIENTS
    }
    quote = {
        "id": post_id,
        "text": "Have you explored Canton yet? With Squid, it is easy.",
        "created_at": "2026-07-22T00:00:00Z",
        "url": f"https://x.com/SquidRouter/status/{post_id}",
        "is_retweet": False,
        "is_reply": False,
        "is_quote": True,
        "metrics": {},
        "media": [{
            "media_key": "13_2079266440268464128",
            "type": "video",
            "url": image_url,
            "width": 1080,
            "height": 1080,
        }],
        # XClient preserves video previews in media; source_image_url remains
        # photo-only for backwards compatibility.
        "source_image_url": "",
        "is_note_tweet": False,
    }
    repo = FakeRepository(states)
    generation = FakeGenerationClient()

    summary = await runner(
        repo,
        FakeXClient(posts=[quote]),
        generation,
    ).run()

    assert repo.queues[0]["source_image_url"] == image_url
    assert summary.generated == 1
    assert generation.calls[0]["template_style"] == "remix"
    assert generation.calls[0]["source_image_url"] == image_url


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


class FakeBatchQueueRepository:
    def __init__(self, *, error=None, budget_error=None):
        self.error = error
        self.budget_error = budget_error
        self.calls = []
        self.budget_calls = []
        self.operations = []

    async def configure_daily_budget(self, **kwargs):
        self.operations.append("configure_budget")
        self.budget_calls.append(kwargs)
        if self.budget_error:
            raise self.budget_error

    async def queue_job(self, **kwargs):
        self.operations.append("queue_job")
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return kwargs["item"].job_id


def canary_batch_settings(*, experiment_start_at, experiment_end_at):
    subject = config_subject(
        environment="staging",
        release_sha="a" * 40,
        supabase_url="https://project-ref.supabase.co",
        workspace_id=WORKSPACE_ID,
        allowed_clients=frozenset({"origintrail"}),
        daily_cap_usd=Decimal("0.05"),
        max_claims=1,
        max_requests_per_batch=1,
        experiment_start_at=experiment_start_at,
        experiment_end_at=experiment_end_at,
        timezone_name="Asia/Seoul",
    )
    subject_sha256 = canonical_sha256(subject)
    return BatchSettings(
        mode="live",
        allowed_clients=frozenset({"origintrail"}),
        daily_cap_usd=Decimal("0.05"),
        max_claims=1,
        max_requests_per_batch=1,
        supabase_url="https://project-ref.supabase.co",
        supabase_service_role_key="s" * 64,
        workspace_id=WORKSPACE_ID,
        experiment_start_at=experiment_start_at,
        experiment_end_at=experiment_end_at,
        canary_environment="staging",
        runtime_environment="staging",
        canary_release_sha="a" * 40,
        runtime_release_sha="a" * 40,
        canary_subject_sha256=subject_sha256,
        canary_approval=CanaryConfigApproval(
            approval_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            approved_by="operator:test",
            approved_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            subject_sha256=subject_sha256,
        ),
    )


def active_batch_settings(*, start_offset_days=0, end_offset_days=2):
    return canary_batch_settings(
        experiment_start_at=(
            CANARY_KST_DAY_START + timedelta(days=start_offset_days)
        ),
        experiment_end_at=(
            CANARY_KST_DAY_START + timedelta(days=end_offset_days)
        ),
    )


def batch_bridge(repository):
    return BatchQueueBridge(
        repository=repository,
        policy=BatchPolicy(allowed_clients=frozenset({"origintrail"})),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_id", "expected_generated"),
    [("origintrail", 0), ("yellow", 1)],
)
async def test_quoted_source_is_skipped_only_for_origintrail_canary(
    client_id,
    expected_generated,
):
    states = {
        item: AutomationState(None, item != client_id, ())
        for item in AUTOMATION_CLIENTS
    }
    handle = "origin_trail" if client_id == "origintrail" else "Yellow"
    quote_id = "2082883998829752783"
    quote = {
        "id": quote_id,
        "text": "Our official mainnet product update is now live.",
        "created_at": "2026-07-22T00:00:00Z",
        "url": f"https://x.com/{handle}/status/{quote_id}",
        "is_retweet": False,
        "is_reply": False,
        "is_quote": True,
        "metrics": {},
        "media": [],
        "source_image_url": "",
        "is_note_tweet": False,
    }
    repo = FakeRepository(states)
    generation = FakeGenerationClient()

    summary = await runner(
        repo,
        FakeXClient(posts=[quote]),
        generation,
    ).run()

    target_record = next(
        record for record in repo.records
        if record["client_id"] == client_id
    )
    assert target_record["next_cursor"] == (
        None if client_id == "origintrail" else quote_id
    )
    assert len(target_record["source_items"]) == expected_generated
    assert summary.generated == expected_generated
    if client_id == "origintrail":
        assert repo.queues == []
        assert generation.calls == []
    else:
        assert repo.queues[0]["client_id"] == "yellow"
        assert generation.calls[0]["client_id"] == "yellow"


@pytest.mark.asyncio
async def test_latest_origintrail_quote_does_not_advance_past_committed_post():
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    latest_quote = {
        "id": "2082883998829752783",
        "text": "Quoted evidence is deliberately incomplete.",
        "created_at": "2026-07-22T00:01:00Z",
        "url": "https://x.com/origin_trail/status/2082883998829752783",
        "is_retweet": False,
        "is_reply": False,
        "is_quote": True,
        "metrics": {},
        "media": [],
        "source_image_url": "",
        "is_note_tweet": False,
    }
    committed = {
        **latest_quote,
        "id": "2082883998829752782",
        "text": "A complete standalone OriginTrail product update.",
        "created_at": "2026-07-22T00:00:00Z",
        "url": "https://x.com/origin_trail/status/2082883998829752782",
        "is_quote": False,
    }
    repo = FakeRepository(states)

    summary = await runner(
        repo,
        FakeXClient(posts=[latest_quote, committed]),
        FakeGenerationClient(),
    ).run()

    record = next(
        item for item in repo.records
        if item["client_id"] == "origintrail"
    )
    assert record["next_cursor"] == committed["id"]
    assert [item["external_id"] for item in record["source_items"]] == [
        committed["id"]
    ]
    assert record["source_items"][0]["is_quote"] is False
    assert record["source_items"][0]["is_retweet"] is False
    assert record["source_items"][0]["is_reply"] is False
    assert summary.generated == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_flag", ["is_retweet", "is_reply"])
async def test_origintrail_references_other_than_quotes_are_not_allowlisted(
    blocked_flag,
):
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    post_id = "2082883998829752783"
    post = {
        "id": post_id,
        "text": "Referenced evidence is not a standalone source.",
        "created_at": "2026-07-22T00:00:00Z",
        "url": f"https://x.com/origin_trail/status/{post_id}",
        "is_retweet": False,
        "is_reply": False,
        "is_quote": False,
        "metrics": {},
        "media": [],
        "source_image_url": "",
        "is_note_tweet": False,
    }
    post[blocked_flag] = True
    repo = FakeRepository(states)

    summary = await runner(
        repo,
        FakeXClient(posts=[post]),
        FakeGenerationClient(),
    ).run()

    record = next(
        item for item in repo.records
        if item["client_id"] == "origintrail"
    )
    assert record["next_cursor"] is None
    assert record["source_items"] == []
    assert repo.queues == []
    assert summary.generated == 0


@pytest.mark.asyncio
async def test_active_origintrail_job_hands_immutable_copy_only_work_to_batch():
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail"),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert generation.calls == []
    assert repo.completed == []
    assert repo.failed == []
    assert len(repo.batch_handoffs) == 1
    assert set(repo.execution_planes.values()) == {"openai_batch"}
    assert len(batch_repository.calls) == 1
    assert len(batch_repository.budget_calls) == 1
    assert batch_repository.operations == [
        "configure_budget",
        "queue_job",
    ]
    assert batch_repository.budget_calls[0] == {
        "budget_key": "batch-general:2026-07-22",
        "window_start": datetime(
            2026,
            7,
            21,
            15,
            tzinfo=timezone.utc,
        ),
        "window_end": datetime(
            2026,
            7,
            22,
            15,
            tzinfo=timezone.utc,
        ),
        "limit_usd": Decimal("0.05"),
    }
    queued = batch_repository.calls[0]
    item = queued["item"]
    assert item.job_id == repo.batch_handoffs[0]["batch_job_id"]
    assert item.client_id == "origintrail"
    assert item.agent_id == "origintrail_client_agent"
    assert item.model_tier == "S"
    assert item.model == "gpt-5.6-luna"
    assert item.priority == "P2"
    assert item.risk_tier == "T1"
    assert item.approval_required is True
    assert item.max_cost_usd == Decimal("0.05")
    assert item.max_output_tokens == 2_000
    assert item.deadline_at.isoformat() == "2026-07-23T15:00:00+00:00"
    evidence = json.loads(item.input_text)
    assert evidence["source"]["content_sha256"] == hashlib.sha256(
        evidence["source"]["content"].encode("utf-8")
    ).hexdigest()
    assert "visual" not in item.output_schema["properties"]
    assert {
        name: field["pattern"]
        for name, field in item.output_schema["properties"].items()
    } == {
        "headline_ko": r"^[\s\S]{0,119}\S$",
        "body_ko": r"^[\s\S]{0,1799}\S$",
        "x_copy_ko": r"^[\s\S]{0,499}\S$",
        "telegram_copy_ko": r"^[\s\S]{0,1023}\S$",
    }
    assert all(
        "minLength" not in field and "maxLength" not in field
        for field in item.output_schema["properties"].values()
    )
    assert queued["budget_key"] == "batch-general:2026-07-22"
    assert len(queued["idempotency_key"]) == 64
    assert (
        repo.batch_handoffs[0]["input_sha256"]
        == item.input_sha256
    )
    assert summary.generated == 0
    assert any(
        outcome == {
            "client_id": "origintrail",
            "status": "batch_queued",
            "detail": "daily_news",
        }
        for outcome in summary.outcomes
    )


@pytest.mark.asyncio
async def test_image_backed_origintrail_source_stays_on_the_sync_plane():
    image_url = "https://pbs.twimg.com/media/origintrail-official.jpg"
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail", source_image_url=image_url),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.generated == 1
    assert repo.queues[0]["source_image_url"] == image_url
    assert generation.calls[0]["client_id"] == "origintrail"
    assert generation.calls[0]["source_image_url"] == image_url
    assert set(repo.execution_planes.values()) == {"studio_sync"}
    assert batch_repository.calls == []
    assert batch_repository.budget_calls == []


@pytest.mark.asyncio
async def test_origintrail_article_stays_on_the_sync_plane():
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail", text="A" * 320, note=True),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.generated == 1
    assert generation.calls[0]["content_kind"] == "article"
    assert set(repo.execution_planes.values()) == {"studio_sync"}
    assert batch_repository.calls == []
    assert batch_repository.budget_calls == []


@pytest.mark.asyncio
async def test_pre_cutover_origintrail_source_stays_on_the_sync_plane():
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    repo.claims.append(ClaimedJob(
        job_id="77777777-7777-4777-8777-777777777777",
        client_id="origintrail",
        kst_date=date(2026, 7, 22),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id=(
            "55555555-5555-4555-8555-555555555555"
        ),
        source_content="A pre-cutover source with unknown quote status.",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=1,
        max_attempts=3,
        locked_by="placeholder",
        origintrail_batch_eligible=False,
    ))
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.generated == 1
    assert len(generation.calls) == 1
    assert set(repo.execution_planes.values()) == {"studio_sync"}
    assert batch_repository.calls == []
    assert batch_repository.budget_calls == []


def test_origintrail_batch_item_rejects_article_work():
    job = ClaimedJob(
        job_id="77777777-7777-4777-8777-777777777777",
        client_id="origintrail",
        kst_date=date(2026, 7, 22),
        content_kind="article",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id="55555555-5555-4555-8555-555555555555",
        source_content="A" * 320,
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=1,
        max_attempts=3,
        locked_by="official-x:test",
        origintrail_batch_eligible=True,
    )
    reference_pack = StyleReferencePack(
        request_id=job.request_id,
        primary_source_item_id=job.primary_source_item_id,
        reference_pack_hash="a" * 32,
        references=(),
    )

    with pytest.raises(ValueError, match="unsupported evidence"):
        OfficialXDailyRunner._origintrail_batch_item(
            job=job,
            reference_pack=reference_pack,
            experiment_end_at=None,
        )


def test_origintrail_batch_item_rejects_url_only_source_evidence():
    job = ClaimedJob(
        job_id="77777777-7777-4777-8777-777777777777",
        client_id="origintrail",
        kst_date=date(2026, 7, 22),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id="55555555-5555-4555-8555-555555555555",
        source_content="https://t.co/BFl2YSh2VB",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=1,
        max_attempts=3,
        locked_by="official-x:test",
        origintrail_batch_eligible=True,
    )
    reference_pack = StyleReferencePack(
        request_id=job.request_id,
        primary_source_item_id=job.primary_source_item_id,
        reference_pack_hash="a" * 32,
        references=(),
    )

    with pytest.raises(ValueError, match="substantive source evidence"):
        OfficialXDailyRunner._origintrail_batch_item(
            job=job,
            reference_pack=reference_pack,
            experiment_end_at=None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type", ["video", "animated_gif"])
async def test_visual_media_origintrail_source_stays_on_the_sync_plane(
    media_type,
):
    preview_url = f"https://pbs.twimg.com/media/origintrail-{media_type}.jpg"
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (
            pending(
                "origintrail",
                media=({"type": media_type, "url": preview_url},),
            ),
        ),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.generated == 1
    assert repo.queues[0]["source_image_url"] == preview_url
    assert generation.calls[0]["source_image_url"] == preview_url
    assert set(repo.execution_planes.values()) == {"studio_sync"}
    assert batch_repository.calls == []
    assert batch_repository.budget_calls == []


@pytest.mark.asyncio
async def test_visual_media_fallback_is_limited_to_origintrail_canary():
    preview_url = "https://pbs.twimg.com/media/yellow-video.jpg"
    states = {
        client_id: AutomationState(None, client_id != "yellow", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["yellow"] = AutomationState(
        None,
        False,
        (
            pending(
                "yellow",
                media=({"type": "video", "url": preview_url},),
            ),
        ),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()

    summary = await runner(repo, FakeXClient(), generation).run()

    assert summary.generated == 1
    assert repo.queues[0]["source_image_url"] == ""
    assert generation.calls[0]["source_image_url"] == ""


@pytest.mark.asyncio
async def test_active_backlog_uses_the_queue_attempt_kst_budget():
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    repo.claims.append(ClaimedJob(
        job_id="77777777-7777-4777-8777-777777777777",
        client_id="origintrail",
        kst_date=date(2026, 7, 21),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id=(
            "55555555-5555-4555-8555-555555555555"
        ),
        source_content="An immutable official OriginTrail backlog update.",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=1,
        max_attempts=3,
        locked_by="placeholder",
        origintrail_batch_eligible=True,
    ))
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.errors == 0
    assert generation.calls == []
    assert batch_repository.calls[0]["budget_key"] == (
        "batch-general:2026-07-22"
    )
    assert batch_repository.budget_calls[0]["window_start"] == datetime(
        2026,
        7,
        21,
        15,
        tzinfo=timezone.utc,
    )
    assert len(repo.batch_handoffs) == 1


@pytest.mark.asyncio
async def test_last_26_hours_are_a_batch_drain_window_without_sync_fallback():
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail"),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()
    ending_soon = canary_batch_settings(
        experiment_start_at=datetime(
            2026, 7, 20, 15, tzinfo=timezone.utc
        ),
        experiment_end_at=datetime(
            2026, 7, 22, 15, tzinfo=timezone.utc
        ),
    )

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=ending_soon,
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert generation.calls == []
    assert batch_repository.budget_calls == []
    assert batch_repository.calls == []
    assert repo.batch_handoffs == []
    assert len(repo.failed) == 1
    assert repo.failed[0]["error_code"] == "batch_handoff_not_admitted"
    assert repo.failed[0]["retryable"] is False
    assert summary.errors == 1


@pytest.mark.asyncio
async def test_batch_deadline_is_clamped_to_the_experiment_end():
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail"),),
    )
    repo = FakeRepository(states)
    batch_repository = FakeBatchQueueRepository()
    clamped = active_batch_settings()
    experiment_end = clamped.experiment_end_at

    await runner(
        repo,
        FakeXClient(),
        FakeGenerationClient(),
        batch_settings=clamped,
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert batch_repository.calls[0]["item"].deadline_at == experiment_end


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline_window", ["open", "drain"])
async def test_retry_always_uses_replay_only_without_new_budget(deadline_window):
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    repo.claims.append(ClaimedJob(
        job_id="77777777-7777-4777-8777-777777777777",
        client_id="origintrail",
        kst_date=date(2026, 7, 22),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id=(
            "55555555-5555-4555-8555-555555555555"
        ),
        source_content="An immutable official OriginTrail retry.",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=2,
        max_attempts=3,
        locked_by="placeholder",
        origintrail_batch_eligible=True,
    ))
    batch_repository = FakeBatchQueueRepository()
    batch_window = (
        active_batch_settings()
        if deadline_window == "open"
        else canary_batch_settings(
            experiment_start_at=datetime(
                2026, 7, 20, 15, tzinfo=timezone.utc
            ),
            experiment_end_at=datetime(
                2026, 7, 22, 15, tzinfo=timezone.utc
            ),
        )
    )

    summary = await runner(
        repo,
        FakeXClient(),
        FakeGenerationClient(),
        batch_settings=batch_window,
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.errors == 0
    assert batch_repository.budget_calls == []
    assert len(batch_repository.calls) == 1
    assert batch_repository.calls[0]["replay_only"] is True
    assert len(repo.batch_handoffs) == 1


@pytest.mark.asyncio
async def test_expired_experiment_recovers_only_a_prior_batch_attempt():
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    job_id = "77777777-7777-4777-8777-777777777777"
    repo.claims.append(ClaimedJob(
        job_id=job_id,
        client_id="origintrail",
        kst_date=date(2026, 7, 22),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id=(
            "55555555-5555-4555-8555-555555555555"
        ),
        source_content="An immutable official OriginTrail retry.",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=2,
        max_attempts=3,
        locked_by="placeholder",
    ))
    repo.execution_planes[job_id] = "openai_batch"
    repo.batch_recovery_result = True
    batch_repository = FakeBatchQueueRepository()
    expired = canary_batch_settings(
        experiment_start_at=datetime(
            2026, 7, 19, 15, tzinfo=timezone.utc
        ),
        experiment_end_at=datetime(
            2026, 7, 21, 15, tzinfo=timezone.utc
        ),
    )

    summary = await runner(
        repo,
        FakeXClient(),
        FakeGenerationClient(),
        batch_settings=expired,
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.errors == 0
    assert batch_repository.budget_calls == []
    assert batch_repository.calls == []
    assert repo.style_packs == []
    assert len(repo.batch_recoveries) == 1
    assert repo.batch_recoveries[0]["job_id"] == job_id
    assert repo.batch_recoveries[0]["worker_id"].startswith("official-x:")
    assert repo.batch_handoffs == []


@pytest.mark.asyncio
async def test_max_attempt_claim_uses_stored_receipt_without_admission():
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    job_id = "77777777-7777-4777-8777-777777777777"
    repo.claims.append(ClaimedJob(
        job_id=job_id,
        client_id="origintrail",
        kst_date=date(2026, 7, 22),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id=(
            "55555555-5555-4555-8555-555555555555"
        ),
        source_content="An immutable final OriginTrail Batch recovery.",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=3,
        max_attempts=3,
        locked_by="placeholder",
        batch_handoff_recovery_only=True,
    ))
    repo.execution_planes[job_id] = "openai_batch"
    repo.batch_recovery_result = True
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()
    expired = canary_batch_settings(
        experiment_start_at=datetime(
            2026, 7, 19, 15, tzinfo=timezone.utc
        ),
        experiment_end_at=datetime(
            2026, 7, 21, 15, tzinfo=timezone.utc
        ),
    )

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=expired,
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.errors == 0
    assert generation.calls == []
    assert repo.style_packs == []
    assert batch_repository.budget_calls == []
    assert batch_repository.calls == []
    assert len(repo.batch_recoveries) == 1
    assert repo.batch_handoffs == []
    assert repo.failed == []


@pytest.mark.asyncio
async def test_max_attempt_missing_receipt_never_falls_back_to_admission():
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    job_id = "77777777-7777-4777-8777-777777777777"
    repo.claims.append(ClaimedJob(
        job_id=job_id,
        client_id="origintrail",
        kst_date=date(2026, 7, 22),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id=(
            "55555555-5555-4555-8555-555555555555"
        ),
        source_content="An immutable final OriginTrail Batch recovery.",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=3,
        max_attempts=3,
        locked_by="placeholder",
        batch_handoff_recovery_only=True,
    ))
    repo.execution_planes[job_id] = "openai_batch"
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.errors == 1
    assert generation.calls == []
    assert repo.style_packs == []
    assert len(repo.batch_recoveries) == 1
    assert batch_repository.calls == []
    assert batch_repository.budget_calls == []
    assert repo.batch_handoffs == []
    assert repo.failed == []


@pytest.mark.asyncio
async def test_sync_plane_binding_survives_experiment_start():
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    job_id = "77777777-7777-4777-8777-777777777777"
    repo.claims.append(ClaimedJob(
        job_id=job_id,
        client_id="origintrail",
        kst_date=date(2026, 7, 22),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id=(
            "55555555-5555-4555-8555-555555555555"
        ),
        source_content="An immutable official OriginTrail sync retry.",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=2,
        max_attempts=3,
        locked_by="placeholder",
    ))
    repo.execution_planes[job_id] = "studio_sync"
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.generated == 1
    assert len(generation.calls) == 1
    assert batch_repository.calls == []


@pytest.mark.asyncio
async def test_batch_plane_binding_never_falls_back_to_sync_when_disabled():
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    job_id = "77777777-7777-4777-8777-777777777777"
    repo.claims.append(ClaimedJob(
        job_id=job_id,
        client_id="origintrail",
        kst_date=date(2026, 7, 22),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id=(
            "55555555-5555-4555-8555-555555555555"
        ),
        source_content="An immutable official OriginTrail Batch retry.",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=2,
        max_attempts=3,
        locked_by="placeholder",
    ))
    repo.execution_planes[job_id] = "openai_batch"
    generation = FakeGenerationClient()
    disabled = BatchSettings(
        mode="off",
        allowed_clients=frozenset({"origintrail"}),
        daily_cap_usd=Decimal("0.50"),
        max_claims=1,
        max_requests_per_batch=1,
    )

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=disabled,
        batch_bridge=None,
    ).run()

    assert generation.calls == []
    assert len(repo.failed) == 1
    assert summary.errors == 1


@pytest.mark.asyncio
async def test_batch_queue_failure_never_falls_through_to_sync_generation():
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail"),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository(error=BatchRepositoryError(
        "batch_database_unavailable",
        retryable=True,
    ))

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert generation.calls == []
    assert repo.batch_handoffs == []
    assert len(repo.failed) == 1
    assert repo.failed[0]["retryable"] is True
    assert summary.errors == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable", [True, False])
async def test_producer_first_budget_failure_never_queues_or_syncs(retryable):
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail"),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository(
        budget_error=BatchRepositoryError(
            "batch_budget_unavailable",
            retryable=retryable,
        )
    )

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert len(batch_repository.budget_calls) == 1
    assert batch_repository.calls == []
    assert generation.calls == []
    assert repo.batch_handoffs == []
    assert len(repo.failed) == 1
    assert repo.failed[0]["retryable"] is retryable
    assert summary.errors == 1


@pytest.mark.asyncio
async def test_batch_policy_rejection_never_falls_through_to_sync_generation():
    states = {
        client_id: AutomationState(None, True, ())
        for client_id in AUTOMATION_CLIENTS
    }
    repo = FakeRepository(states)
    repo.claims.append(ClaimedJob(
        job_id="77777777-7777-4777-8777-777777777777",
        client_id="origintrail",
        kst_date=date(2026, 7, 20),
        content_kind="daily_news",
        request_id="66666666-6666-4666-8666-666666666666",
        primary_source_item_id=(
            "55555555-5555-4555-8555-555555555555"
        ),
        source_content="An immutable official OriginTrail update.",
        source_url="https://x.com/origin_trail/status/456",
        source_image_url="",
        manual_only=False,
        attempts=1,
        max_attempts=3,
        locked_by="placeholder",
        origintrail_batch_eligible=True,
    ))
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert generation.calls == []
    assert batch_repository.calls == []
    assert len(repo.failed) == 1
    assert repo.failed[0]["retryable"] is False
    assert summary.errors == 1


@pytest.mark.asyncio
async def test_batch_handoff_completion_failure_leaves_durable_queue_for_replay():
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail"),),
    )
    repo = FakeRepository(states)
    repo.batch_handoff_error = AutomationRepositoryError(
        "automation_database_unavailable",
        retryable=True,
    )
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert len(batch_repository.calls) == 1
    assert generation.calls == []
    assert repo.failed == []
    assert summary.errors == 1
    assert any(
        outcome.get("detail") == "automation_database_unavailable"
        for outcome in summary.outcomes
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("client_id", ["yellow", "squid", "babylon"])
async def test_batch_experiment_does_not_change_non_origintrail_generation(
    client_id,
):
    states = {
        candidate: AutomationState(None, candidate != client_id, ())
        for candidate in AUTOMATION_CLIENTS
    }
    states[client_id] = AutomationState(
        None,
        False,
        (pending(client_id),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.generated == 1
    assert generation.calls[0]["client_id"] == client_id
    assert batch_repository.calls == []


@pytest.mark.asyncio
async def test_origintrail_uses_existing_sync_path_outside_experiment_window():
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail"),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(
            start_offset_days=1,
            end_offset_days=3,
        ),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.generated == 1
    assert generation.calls[0]["client_id"] == "origintrail"
    assert batch_repository.calls == []


@pytest.mark.asyncio
async def test_new_origintrail_job_resumes_sync_after_experiment_expiry():
    states = {
        client_id: AutomationState(None, client_id != "origintrail", ())
        for client_id in AUTOMATION_CLIENTS
    }
    states["origintrail"] = AutomationState(
        None,
        False,
        (pending("origintrail"),),
    )
    repo = FakeRepository(states)
    generation = FakeGenerationClient()
    batch_repository = FakeBatchQueueRepository()

    summary = await runner(
        repo,
        FakeXClient(),
        generation,
        batch_settings=active_batch_settings(
            start_offset_days=-3,
            end_offset_days=-1,
        ),
        batch_bridge=batch_bridge(batch_repository),
    ).run()

    assert summary.generated == 1
    assert generation.calls[0]["client_id"] == "origintrail"
    assert batch_repository.calls == []


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
        "is_quote": False,
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
