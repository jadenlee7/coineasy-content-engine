from pathlib import Path


ROOT = Path(__file__).parents[1]
TABLE = (
    ROOT
    / "supabase/migrations/20260808122500_origintrail_batch_review_packs.sql"
).read_text()
BIND = (
    ROOT
    / "supabase/migrations/20260808134000_materialize_origintrail_batch_review_pack.sql"
).read_text()
DELIVERY = (
    ROOT
    / "supabase/migrations/20260808135000_origintrail_buzz_delivery_attachment.sql"
).read_text()
ADAPTER = (
    ROOT / "netlify/functions/buzz-delivery-origintrail.mts"
).read_text()


def test_review_pack_ledger_is_private_immutable_and_exactly_bound():
    assert "create table agent_runtime.origintrail_batch_review_packs" in TABLE
    assert "force row level security" in TABLE
    assert "OriginTrail Batch review packs are immutable" in TABLE
    for field in (
        "content_item_id",
        "content_version_id",
        "asset_id",
        "source_item_id",
        "input_sha256",
        "result_sha256",
        "source_content_sha256",
        "banner_sha256",
        "review_pack_sha256",
    ):
        assert field in TABLE
    assert "origintrail_review_pack_sha256" in TABLE


def test_materializer_preserves_sources_and_never_approves_or_publishes():
    assert "bind_origintrail_batch_review_pack" in BIND
    assert "get_agent_batch_review_item" in BIND
    assert "content_source_links" in BIND
    assert "origintrail-batch-review-pack@1" in BIND
    assert "has_valid_double_fact_check_report" in BIND
    assert "generated_asset.width = 1200" in BIND
    assert "generated_asset.height = 630" in BIND
    lowered = BIND.lower()
    assert "insert into public.approvals" not in lowered
    assert "insert into public.publications" not in lowered
    assert "job_kind,\n        status" not in lowered
    assert "automatic_publication', false" in BIND


def test_v2_delivery_claim_requires_same_materialized_attachment():
    assert "claim_origintrail_buzz_delivery_v2" in DELIVERY
    assert "target_attachment_sha256" in DELIVERY
    assert "review_pack.banner_sha256 = lower(target_attachment_sha256)" in DELIVERY
    assert "attachment_sha256" in DELIVERY
    assert "BUZZ_REVIEW_PACK_MATERIALIZATION_ENABLED" in ADAPTER
    assert "materializeOriginTrailReviewPack" in ADAPTER
    assert "pack.bannerSha256 !== action.attachment_sha256" in ADAPTER
