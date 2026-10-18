"""Local intake integration only. No real provider, DB, generation or delivery."""

from datetime import timedelta
import socket

import pytest

from core.automation.daily_runner import DailyRunSummary
from core.automation.babylon_source_intake import BabylonIntakeError
from core.automation.models import AutomationState
from core.sources.x_client import XTransientError
from tests.test_automation_daily_runner import (
    FakeGenerationClient, FakeRepository, FakeXClient, NOW, runner,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("test_network_forbidden")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def post(post_id="102", **changes):
    return {
        "id": post_id, "text": "Official Babylon product integration update.",
        "url": f"https://x.com/babylonlabs_io/status/{post_id}",
        "created_at": NOW.isoformat(), "media": [], "metrics": {},
        "source_image_url": "https://pbs.twimg.com/media/synthetic.jpg",
        "is_reply": False, "is_retweet": False, "is_quote": False,
        **changes,
    }


async def intake(posts, *, dry_run=False, allow_queue=False, previous="100", error=None):
    repo = FakeRepository({"babylon": AutomationState(previous, False, ())})
    generation = FakeGenerationClient()
    x = FakeXClient(posts=posts, error=error)
    worker = runner(repo, x, generation, daily_review_mode=True)
    summary = DailyRunSummary(kst_date=NOW.date().isoformat(), dry_run=dry_run)
    await worker._intake_client(
        client_id="babylon", kst_date=NOW.date(), now=NOW, dry_run=dry_run,
        allow_queue=allow_queue, summary=summary,
    )
    assert generation.calls == []
    return repo, x, summary


@pytest.mark.asyncio
async def test_mixed_reply_retweet_quote_preserves_only_eligible_cursor_and_payload():
    quote = post("103", is_quote=True)
    repo, x, _ = await intake([
        post("199", is_reply=True), post("198", is_retweet=True),
        quote, post(),
    ])
    assert len(x.calls) == 1
    assert x.calls[0][1]["require_complete"] is True
    assert len(repo.records) == 1
    record = repo.records[0]
    assert record["expected_cursor"] == "100"
    assert record["next_cursor"] == "103"
    assert [p["external_id"] for p in record["source_items"]] == ["103", "102"]
    assert record["source_items"][0]["is_quote"] is True
    assert all(p["is_reply"] is False and p["is_retweet"] is False
               for p in record["source_items"])
    assert repo.queues == []


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", [None, "100"])
async def test_only_excluded_posts_preserve_previous_cursor_and_do_not_queue(previous):
    repo, _, _ = await intake(
        [post(is_reply=True), post("103", is_retweet=True)],
        previous=previous, allow_queue=True,
    )
    assert repo.records[0]["source_items"] == []
    assert repo.records[0]["next_cursor"] == previous
    assert repo.queues == []


@pytest.mark.asyncio
async def test_dry_run_latest_uses_accepted_cursor_not_newer_reply():
    repo, _, summary = await intake(
        [post("199", is_reply=True), post()], dry_run=True, allow_queue=True,
    )
    assert repo.records == []
    assert repo.queues == []
    assert any(x["status"] == "planned" for x in summary.outcomes)


@pytest.mark.asyncio
async def test_expired_source_is_not_made_queueable_by_reply_filter():
    repo, _, summary = await intake([
        post("199", is_reply=True),
        post(created_at=(NOW - timedelta(hours=25)).isoformat()),
    ], allow_queue=True)
    assert repo.queues == []
    assert any(x["status"] == "no_candidate" for x in summary.outcomes)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    post(is_reply=True, is_quote=None), post("100", is_reply=True),
    post("not-an-id", is_reply=True),
])
async def test_malformed_excluded_post_stops_before_any_write(bad):
    repo = FakeRepository({"babylon": AutomationState("100", False, ())})
    generation = FakeGenerationClient()
    worker = runner(repo, FakeXClient(posts=[post(), bad]), generation,
                    allowed_clients=("babylon",), daily_review_mode=True)
    # Not dry-run: the validation failure itself must prevent record_sources.
    with pytest.raises(BabylonIntakeError):
        await worker._intake_client(
            client_id="babylon", kst_date=NOW.date(), now=NOW,
            dry_run=False, allow_queue=False,
            summary=DailyRunSummary(kst_date=NOW.date().isoformat(), dry_run=False),
        )
    assert repo.records == repo.queues == generation.calls == []
    summary = await worker.run(dry_run=True)
    assert summary.errors == 1
    assert summary.outcomes[0]["detail"].startswith("babylon_intake_")
    assert repo.records == repo.queues == generation.calls == []


@pytest.mark.asyncio
async def test_incomplete_timeline_never_reaches_record_or_fallback():
    repo = FakeRepository({"babylon": AutomationState("100", False, ())})
    generation = FakeGenerationClient()
    x = FakeXClient(error=XTransientError("bounded incomplete fixture"))
    summary = await runner(repo, x, generation, allowed_clients=("babylon",),
                           daily_review_mode=True).run(dry_run=True)
    assert summary.errors == 1
    assert len(x.calls) == 1
    assert repo.records == repo.queues == generation.calls == []
