from __future__ import annotations

from core.automation.mode_router import choose_content_mode, select_official_candidate


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
