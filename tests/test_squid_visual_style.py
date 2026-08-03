from __future__ import annotations

import pytest

from core.squid_visual_style import (
    SQUID_GENERATED_DESIGN_PROFILE_ID,
    SQUID_GENERATED_DESIGN_PROFILE_VERSION,
    SQUID_VISUAL_POLICY_VERSION,
    SQUID_VISUAL_REFERENCE_PACK_VERSION,
    classify_squid_visual_style,
    reference_pack_for_family,
)


@pytest.mark.parametrize(
    ("source_content", "family"),
    [
        (
            "A new era for Squid starts with a major ecosystem update.",
            "editorial_big_type",
        ),
        (
            "Squid has crossed 5 million swaps across connected chains.",
            "milestone_metric",
        ),
        (
            "The TGE allocation checker is live. Claim phase details follow.",
            "status_progress",
        ),
        (
            "Bridge from MiniPay with Squid in a few taps.",
            "product_proof",
        ),
        (
            "Bouncing through the weekend like SQUIB.",
            "worldbuilding",
        ),
    ],
)
def test_family_classification_is_deterministic(
    source_content: str,
    family: str,
):
    first = classify_squid_visual_style(source_content)
    second = classify_squid_visual_style(source_content)

    assert first == second
    assert first.family == family


@pytest.mark.parametrize(
    "source_content",
    [
        "A new era for Squid starts today.",
        "Squid has crossed 5 million swaps across connected chains.",
        "TGE claim phase status is available.",
        "Use MiniPay to bridge with Squid.",
        "Weekend vibes with SQUIB.",
    ],
)
def test_exact_official_media_always_uses_source_remix(source_content: str):
    decision = classify_squid_visual_style(
        source_content,
        has_official_media=True,
    )

    assert decision.channel == "source_remix"
    assert decision.automatic is True
    assert decision.manual_review_required is False


def test_non_boolean_media_marker_cannot_enable_source_remix():
    decision = classify_squid_visual_style(  # type: ignore[arg-type]
        "A factual ecosystem announcement with enough detail for an editorial.",
        has_official_media="https://pbs.twimg.com/media/unverified.jpg",
    )

    assert decision.channel == "generated_gtm"


def test_worldbuilding_without_official_media_fails_closed():
    decision = classify_squid_visual_style(
        "Bouncing through the weekend like SQUIB.",
        has_official_media=False,
    )

    assert decision.family == "worldbuilding"
    assert decision.channel == "manual_review"
    assert decision.automatic is False
    assert decision.manual_review_required is True
    assert "fails closed" in decision.reason


@pytest.mark.parametrize("source_content", ["", "gm", "big mood"])
def test_sparse_text_without_media_is_not_turned_into_generated_art(
    source_content: str,
):
    decision = classify_squid_visual_style(source_content)

    assert decision.family == "worldbuilding"
    assert decision.channel == "manual_review"
    assert decision.automatic is False


def test_substantive_unmatched_news_falls_back_to_editorial_generation():
    decision = classify_squid_visual_style(
        "Squid connects communities across many networks with one simple experience."
    )

    assert decision.family == "editorial_big_type"
    assert decision.channel == "generated_gtm"
    assert decision.automatic is True


def test_instructional_source_routes_to_product_proof():
    decision = classify_squid_visual_style(
        "Follow this step-by-step tutorial to complete the transfer.",
    )

    assert decision.family == "product_proof"
    assert decision.channel == "generated_gtm"


def test_small_instructional_number_is_not_misread_as_a_milestone():
    decision = classify_squid_visual_style(
        "Use this 3 step guide to bridge tokens with Squid.",
    )

    assert decision.family == "product_proof"


def test_audit_metadata_contains_stable_policy_pack_and_channel_fields():
    decision = classify_squid_visual_style(
        "Squid has crossed 5 million swaps across connected chains."
    )

    assert decision.as_spec_metadata() == {
        "creative_family": "milestone_metric",
        "render_strategy": "generated_gtm",
        "creative_family_policy_version": SQUID_VISUAL_POLICY_VERSION,
        "visual_reference_pack_id": "squid/milestone-metric",
        "visual_reference_pack_version": SQUID_VISUAL_REFERENCE_PACK_VERSION,
        "visual_reference_status_urls": [
            "https://x.com/squidrouter/status/2082889008385044897"
        ],
        "visual_automatic": True,
        "channel_profile": "x_square",
        "visual_metric": "5 million",
        "visual_design_profile_id": SQUID_GENERATED_DESIGN_PROFILE_ID,
        "visual_design_profile_version": SQUID_GENERATED_DESIGN_PROFILE_VERSION,
    }


@pytest.mark.parametrize(
    "family",
    [
        "editorial_big_type",
        "milestone_metric",
        "status_progress",
        "product_proof",
        "worldbuilding",
    ],
)
def test_every_family_has_a_versioned_official_reference_pack(family: str):
    pack = reference_pack_for_family(family)  # type: ignore[arg-type]

    assert pack.pack_id == f"squid/{family.replace('_', '-')}"
    assert pack.version == SQUID_VISUAL_REFERENCE_PACK_VERSION
    assert pack.representative_status_urls
    assert all(
        url.startswith("https://x.com/squidrouter/status/")
        for url in pack.representative_status_urls
    )


@pytest.mark.parametrize(
    ("source_url", "family"),
    [
        (
            "https://x.com/squidrouter/status/2079999207956500971",
            "editorial_big_type",
        ),
        (
            "https://x.com/squidrouter/status/2082889008385044897",
            "milestone_metric",
        ),
        (
            "https://x.com/squidrouter/status/2080668216792129968",
            "status_progress",
        ),
        (
            "https://x.com/squidrouter/status/2079628218403803481",
            "product_proof",
        ),
        (
            "https://x.com/squidrouter/status/2083266484789514640",
            "product_proof",
        ),
        (
            "https://x.com/squidrouter/status/2083583547353501977",
            "worldbuilding",
        ),
    ],
)
def test_reviewed_official_status_urls_pin_their_visual_family(
    source_url: str,
    family: str,
):
    decision = classify_squid_visual_style(
        "Ambiguous short copy",
        source_url=source_url,
        has_official_media=True,
    )

    assert decision.family == family
    assert decision.channel == "source_remix"


def test_non_milestone_metadata_never_invents_a_metric():
    decision = classify_squid_visual_style(
        "Use this 3 step guide to bridge tokens with Squid."
    )

    assert decision.verified_metric is None
    assert "visual_metric" not in decision.as_spec_metadata()


def test_design_profile_is_bound_only_to_generated_gtm():
    generated = classify_squid_visual_style(
        "A factual ecosystem announcement with enough detail for an editorial."
    )
    source_remix = classify_squid_visual_style(
        "Squid is live on Telegram.",
        source_url="https://x.com/squidrouter/status/2083266484789514640",
        has_official_media=True,
    )

    assert generated.as_spec_metadata()["visual_design_profile_id"] == (
        SQUID_GENERATED_DESIGN_PROFILE_ID
    )
    assert generated.as_spec_metadata()["visual_design_profile_version"] == (
        SQUID_GENERATED_DESIGN_PROFILE_VERSION
    )
    assert "visual_design_profile_id" not in source_remix.as_spec_metadata()
    assert "visual_design_profile_version" not in source_remix.as_spec_metadata()


def test_metric_is_copied_from_source_without_synthesizing_a_number():
    decision = classify_squid_visual_style(
        "Squid crossed 5M transactions across connected chains."
    )

    assert decision.family == "milestone_metric"
    assert decision.verified_metric == "5m"
    assert decision.as_spec_metadata()["visual_metric"] == "5m"


def test_milestone_word_without_a_scaled_source_metric_stays_editorial():
    decision = classify_squid_visual_style(
        "Squid is announcing an important transaction milestone for the ecosystem."
    )

    assert decision.family == "editorial_big_type"
    assert decision.verified_metric is None
    assert "visual_metric" not in decision.as_spec_metadata()


def test_community_support_phrase_does_not_claim_product_proof():
    decision = classify_squid_visual_style(
        "Thank you to the entire Squid community for your continuing support."
    )

    assert decision.family == "editorial_big_type"
