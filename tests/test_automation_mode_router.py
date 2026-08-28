from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.automation.mode_router import (
    _normalize_demand_term,
    choose_content_mode,
    select_official_candidate,
)


def post(post_id: str, text: str, **extra):
    return {
        "id": post_id,
        "text": text,
        "created_at": f"2026-07-22T08:00:{int(post_id) % 60:02d}Z",
        "is_retweet": False,
        "is_reply": False,
        "metrics": {},
        **extra,
    }


def test_candidate_selection_prefers_meaningful_official_announcement():
    selected = select_official_candidate([
        post("1", "gm"),
        post("2", "Long week?"),
        post("3", "Our mainnet upgrade is now live with a new developer API."),
        post("4", "A regular community note with no concrete product detail at all."),
    ])
    assert selected is not None
    assert selected["id"] == "3"


def test_complete_note_is_article_but_short_post_stays_daily_news():
    article = choose_content_mode(
        "babylon",
        post("10", "A" * 320, is_note_tweet=True),
    )
    assert article.content_kind == "article"
    assert article.automatic is True

    daily = choose_content_mode(
        "babylon",
        post("11", "Our new integration is live today.", is_note_tweet=False),
    )
    assert daily.content_kind == "daily_news"


def test_tutorial_rollout_is_explicit_and_limited_to_supported_clients():
    source = post(
        "20",
        "This step-by-step guide explains how to use the API. " + "A" * 300,
        is_note_tweet=True,
    )
    guarded = choose_content_mode("squid", source, enable_tutorials=False)
    assert guarded.content_kind == "article"
    assert guarded.recommendation == "tutorial"

    enabled = choose_content_mode("squid", source, enable_tutorials=True)
    assert enabled.content_kind == "tutorial"
    assert enabled.automatic is True

    unsupported = choose_content_mode("origintrail", source, enable_tutorials=True)
    assert unsupported.content_kind == "article"
    assert unsupported.recommendation is None


def test_replies_and_retweets_never_become_candidates():
    assert select_official_candidate([
        post("30", "A significant launch is live.", is_reply=True),
        post("31", "A significant upgrade is live.", is_retweet=True),
    ]) is None


def test_client_brand_skip_patterns_are_applied_before_selection():
    assert select_official_candidate(
        [post("40", "We are live in 15 minutes for the community AMA")],
        skip_patterns=["AMA", "15 minutes"],
    ) is None


def test_bounded_demand_term_overlap_deterministically_reorders_valid_posts():
    posts = [
        post("50", "A detailed ecosystem update for builders."),
        post("51", "A detailed liquidity update for builders."),
    ]

    assert select_official_candidate(posts)["id"] == "51"
    assert select_official_candidate(
        posts,
        demand_terms=[("ecosystem", 1.0)],
    )["id"] == "50"


def test_demand_term_quality_guard_drops_dates_cadence_and_tokenization_noise():
    assert _normalize_demand_term("2026") == ""
    assert _normalize_demand_term("7월") == ""
    assert _normalize_demand_term("넷째주") == ""
    assert _normalize_demand_term("기다려온") == ""
    assert _normalize_demand_term("channel의") == ""
    assert _normalize_demand_term("AI의") == "ai"
    assert _normalize_demand_term("비트코인 담보") == "비트코인 담보"


def test_demand_terms_cannot_admit_low_signal_or_skipped_posts():
    assert select_official_candidate(
        [post("60", "gm liquidity")],
        demand_terms=[("liquidity", 1.0)],
        skip_patterns=["liquidity"],
    ) is None
    assert select_official_candidate(
        [post("61", "gm", is_reply=True)],
        demand_terms=[("gm", 1.0)],
    ) is None


def test_quiz_learning_priority_reorders_only_eligible_official_how_to_posts():
    tutorial = post("70", "Our new developer guide is available.")
    product = post("71", "Our new developer update is available.")

    assert select_official_candidate([tutorial, product])["id"] == "71"
    assert select_official_candidate(
        [tutorial, product],
        tutorial_priority=1.0,
    )["id"] == "70"
    assert select_official_candidate(
        [post("72", "Our guide is available.", is_reply=True)],
        tutorial_priority=1.0,
    ) is None


def test_equal_scores_use_numeric_post_id_not_lexicographic_order():
    older_digits = post(
        "9",
        "A detailed ecosystem update for builders.",
        created_at="2026-07-22T08:00:00Z",
    )
    newer_digits = post(
        "10",
        "A detailed ecosystem update for builders.",
        created_at="2026-07-22T08:00:00Z",
    )

    assert select_official_candidate([older_digits, newer_digits])["id"] == "10"


@pytest.mark.parametrize(
    "client_id",
    ["yellow", "origintrail", "squid", "babylon"],
)
def test_fresh_post_beats_four_day_old_high_scoring_backlog(client_id):
    latest = post(
        "100",
        "Squid has moved $800m in and out of Celo since launch.",
        created_at="2026-08-20T15:51:32Z",
    )
    old_high_score = post(
        "99",
        "Our major mainnet integration launch is live with a release update. "
        "[X-provided link metadata]\n\n"
        + "A detailed partner announcement with support now available. " * 4,
        created_at="2026-08-16T16:00:00Z",
        metrics={"like_count": 500},
    )

    selected = select_official_candidate(
        [old_high_score, latest],
        client_id=client_id,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
    )

    assert selected is latest


@pytest.mark.parametrize(
    "client_id",
    ["yellow", "origintrail", "squid", "babylon"],
)
def test_freshness_bucket_still_uses_relevance_signals(client_id):
    newest = post(
        "110",
        "A short ecosystem update is available.",
        created_at="2026-08-20T15:55:00Z",
    )
    relevant = post(
        "109",
        "Our major mainnet integration launch is live with a developer update.",
        created_at="2026-08-19T21:00:00Z",
    )

    selected = select_official_candidate(
        [newest, relevant],
        client_id=client_id,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
    )

    assert selected is relevant


@pytest.mark.parametrize("client_id", ["yellow", "babylon"])
def test_next_kst_slot_expiry_rescue_prefers_latest_eligible_source(client_id):
    """Do not let an older relevance-heavy post starve the latest daily source.

    Both posts were published before the current KST day began, so neither will
    remain inside the inclusive 24-hour window when the next KST slot opens.
    Yellow and Babylon therefore use publication recency inside this bounded
    expiry-risk bucket; the normal eligibility filters still run first.
    """
    older_relevance_heavy = post(
        "200",
        "Our major mainnet integration launch is live with a release update. "
        * 4,
        created_at="2026-08-27T09:00:06Z",
        metrics={"like_count": 500},
    )
    latest_eligible = post(
        "201",
        "A product update is available.",
        created_at="2026-08-27T11:00:12Z",
    )

    selected = select_official_candidate(
        [older_relevance_heavy, latest_eligible],
        client_id=client_id,
        now=datetime(2026, 8, 27, 20, 22, tzinfo=timezone.utc),
    )

    assert selected is latest_eligible


@pytest.mark.parametrize("client_id", ["yellow", "babylon"])
def test_next_kst_slot_expiry_rescue_runs_after_eligibility_guards(client_id):
    eligible = post(
        "205",
        "A product update is available.",
        created_at="2026-08-27T11:00:12Z",
    )
    newer_low_signal = post(
        "206",
        "gm",
        created_at="2026-08-27T14:00:00Z",
    )
    newer_reply = post(
        "207",
        "Our major mainnet integration launch is live with a release update.",
        created_at="2026-08-27T14:05:10Z",
        is_reply=True,
    )

    selected = select_official_candidate(
        [eligible, newer_low_signal, newer_reply],
        client_id=client_id,
        now=datetime(2026, 8, 27, 20, 22, tzinfo=timezone.utc),
    )

    assert selected is eligible


@pytest.mark.parametrize("client_id", ["yellow", "babylon"])
def test_mixed_expiry_bucket_keeps_relevance_ranking(client_id):
    expiring_relevance_heavy = post(
        "208",
        "Our major mainnet integration launch is live with a release update. "
        * 4,
        created_at="2026-08-27T14:05:10Z",
        metrics={"like_count": 500},
    )
    safe_latest = post(
        "209",
        "A product update is available.",
        created_at="2026-08-27T18:00:00Z",
    )

    selected = select_official_candidate(
        [expiring_relevance_heavy, safe_latest],
        client_id=client_id,
        now=datetime(2026, 8, 27, 20, 22, tzinfo=timezone.utc),
    )

    assert selected is expiring_relevance_heavy


@pytest.mark.parametrize("client_id", ["yellow", "babylon"])
def test_expiry_rescue_includes_next_slot_cron_interval_boundary(client_id):
    expiring_relevance_heavy = post(
        "214",
        "Our major mainnet integration launch is live with a release update. "
        * 4,
        created_at="2026-08-27T15:14:00Z",
        metrics={"like_count": 500},
    )
    latest = post(
        "215",
        "A product update is available.",
        created_at="2026-08-27T15:15:00Z",
    )

    selected = select_official_candidate(
        [expiring_relevance_heavy, latest],
        client_id=client_id,
        now=datetime(2026, 8, 27, 20, 22, tzinfo=timezone.utc),
    )

    assert selected is latest


@pytest.mark.parametrize("client_id", ["yellow", "babylon"])
def test_sources_safe_for_next_kst_slot_keep_relevance_ranking(client_id):
    older_relevance_heavy = post(
        "212",
        "Our major mainnet integration launch is live with a release update. "
        * 4,
        created_at="2026-08-27T18:00:00Z",
        metrics={"like_count": 500},
    )
    latest = post(
        "213",
        "A product update is available.",
        created_at="2026-08-27T20:00:00Z",
    )

    selected = select_official_candidate(
        [older_relevance_heavy, latest],
        client_id=client_id,
        now=datetime(2026, 8, 27, 20, 22, tzinfo=timezone.utc),
    )

    assert selected is older_relevance_heavy


@pytest.mark.parametrize("client_id", ["origintrail", "squid"])
def test_next_kst_slot_expiry_rescue_does_not_change_other_clients(client_id):
    older_relevance_heavy = post(
        "210",
        "Our major mainnet integration launch is live with a release update. "
        * 4,
        created_at="2026-08-27T09:00:06Z",
        metrics={"like_count": 500},
    )
    latest_eligible = post(
        "211",
        "A product update is available.",
        created_at="2026-08-27T11:00:12Z",
    )

    selected = select_official_candidate(
        [older_relevance_heavy, latest_eligible],
        client_id=client_id,
        now=datetime(2026, 8, 27, 20, 22, tzinfo=timezone.utc),
    )

    assert selected is older_relevance_heavy


@pytest.mark.parametrize(
    "client_id",
    ["yellow", "origintrail", "squid", "babylon"],
)
def test_freshness_bucket_includes_exact_24_hour_boundary(client_id):
    at_cutoff = post(
        "115",
        "Our routing update is available.",
        created_at="2026-08-19T16:00:00Z",
    )
    just_outside = post(
        "114",
        "Our major mainnet integration launch is live with a release update.",
        created_at="2026-08-19T15:59:59Z",
        metrics={"like_count": 500},
    )

    selected = select_official_candidate(
        [just_outside, at_cutoff],
        client_id=client_id,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
    )

    assert selected is at_cutoff


@pytest.mark.parametrize(
    "client_id",
    ["yellow", "origintrail", "squid", "babylon"],
)
def test_stale_backlog_is_not_reused_as_new_content(client_id):
    newest = post(
        "120",
        "A short ecosystem update is available.",
        created_at="2026-08-18T12:00:00Z",
    )
    older_high_score = post(
        "119",
        "Our major mainnet integration launch is live with a release update.",
        created_at="2026-08-17T12:00:00Z",
        metrics={"like_count": 500},
    )

    selected = select_official_candidate(
        [older_high_score, newest],
        client_id=client_id,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
    )

    assert selected is None


def test_unscoped_selection_keeps_existing_relevance_first_behavior():
    newest = post(
        "130",
        "A short ecosystem update is available.",
        created_at="2026-08-20T15:55:00Z",
    )
    older_high_score = post(
        "129",
        "Our major mainnet integration launch is live with a release update.",
        created_at="2026-08-16T12:00:00Z",
        metrics={"like_count": 500},
    )

    selected = select_official_candidate(
        [older_high_score, newest],
        now=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
    )

    assert selected is older_high_score


@pytest.mark.parametrize(
    "client_id",
    ["yellow", "origintrail", "squid", "babylon"],
)
def test_ranking_ignores_provider_link_metadata_copy(client_id):
    provider_enriched = post(
        "139",
        "Happy Sunday.\n\n"
        "[X-provided link metadata]\n\n"
        "Major mainnet integration launch release update now live.",
        created_at="2026-08-20T15:00:00Z",
    )
    official_announcement = post(
        "140",
        "Our routing update is live today.",
        created_at="2026-08-20T15:30:00Z",
    )

    selected = select_official_candidate(
        [provider_enriched, official_announcement],
        client_id=client_id,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
    )

    assert selected is official_announcement


@pytest.mark.parametrize(
    "client_id",
    ["yellow", "origintrail", "squid", "babylon"],
)
def test_skip_patterns_ignore_provider_link_metadata_copy(client_id):
    source = post(
        "150",
        "Our routing update is live today.\n\n"
        "[X-provided link metadata]\n\n"
        "Join the partner AMA in 15 minutes.",
        created_at="2026-08-20T15:30:00Z",
    )

    selected = select_official_candidate(
        [source],
        client_id=client_id,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
        skip_patterns=["AMA", "15 minutes"],
    )

    assert selected is source


@pytest.mark.parametrize(
    "client_id",
    ["yellow", "origintrail", "squid", "babylon"],
)
def test_x_article_text_remains_rankable_when_link_metadata_is_ignored(
    client_id,
):
    article = post(
        "160",
        "https://t.co/official-article\n\n"
        "[X Article]\n\n"
        "Title: Major network release\n"
        "Plain text: Our major mainnet integration launch is now live with "
        "a detailed developer update.\n\n"
        "[X-provided link metadata]\n\n"
        "Title: Unrelated partner preview\n"
        "Description: Join the AMA in 15 minutes.",
        created_at="2026-08-20T15:00:00Z",
    )
    short_update = post(
        "161",
        "A short ecosystem update is available.",
        created_at="2026-08-20T15:30:00Z",
    )

    selected = select_official_candidate(
        [article, short_update],
        client_id=client_id,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
        skip_patterns=["AMA", "15 minutes"],
    )

    assert selected is article
