from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest

from core.content_ops.banner_rereview import prepare_banner_rereview
from core.content_ops.worker import ReviewClaim, ReviewError
from core.content_ops.squid_bundle import build_client_bundle

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def claim():
    return ReviewClaim(
        outbox_id=str(uuid4()), claim_token=str(uuid4()),
        client_id="squid", kst_date="2026-09-21",
        content_item_id=str(uuid4()), content_version_id=str(uuid4()),
        source_item_id=str(uuid4()), generate_job_id=str(uuid4()),
        banner_sha256="a" * 64, title="제목", telegram_copy="텔레그램 공지",
        x_copy="X 공지", source_url="https://x.com/squidrouter/status/123456",
        source_published_at=NOW - timedelta(minutes=10),
    )


def result(version=None, **extra):
    value = {"content_version_id": version or str(uuid4()), "banner_sha256": "b" * 64,
             "rereview_required": True, "execution_authorized": False}
    value.update(extra)
    return value


def test_only_version_hash_outbox_and_token_change():
    old = claim()
    next_claim = prepare_banner_rereview(old, result(), new_outbox_id=str(uuid4()),
                                         new_claim_token=str(uuid4()), now=NOW)
    assert next_claim.content_item_id == old.content_item_id
    assert next_claim.source_item_id == old.source_item_id
    assert next_claim.generate_job_id == old.generate_job_id
    assert next_claim.content_version_id != old.content_version_id
    assert next_claim.outbox_id != old.outbox_id and next_claim.claim_token != old.claim_token
    assert next_claim.banner_sha256 == "b" * 64


@pytest.mark.parametrize("field,value", [
    ("rereview_required", False), ("execution_authorized", True),
    ("banner_sha256", "not-a-hash"), ("content_version_id", "bad"),
])
def test_invalid_result_receipt_fails_closed(field, value):
    old = claim(); receipt = result(); receipt[field] = value
    with pytest.raises(ReviewError):
        prepare_banner_rereview(old, receipt, new_outbox_id=str(uuid4()),
                                new_claim_token=str(uuid4()), now=NOW)


@pytest.mark.parametrize("outbox,token", [
    ("bad", str(uuid4())), (str(uuid4()), "bad token"),
    (str(uuid4()), "x" * 257),
])
def test_invalid_handoff_inputs_never_claim_success(outbox, token):
    with pytest.raises(ReviewError):
        prepare_banner_rereview(claim(), result(), new_outbox_id=outbox,
                                new_claim_token=token, now=NOW)


def test_stale_source_is_not_rejuvenated():
    with pytest.raises(ReviewError):
        prepare_banner_rereview(claim(), result(), new_outbox_id=str(uuid4()),
                                new_claim_token=str(uuid4()),
                                now=NOW + timedelta(hours=25))


def test_new_claim_can_feed_private_bundle_builder_without_sending():
    old = claim()
    next_claim = prepare_banner_rereview(old, result(), new_outbox_id=str(uuid4()),
                                         new_claim_token=str(uuid4()), now=NOW)
    payload = dict(next_claim.__dict__)
    payload["source_published_at"] = next_claim.source_published_at.isoformat().replace("+00:00", "Z")
    detail = {"claim": payload, "snapshot_sha256": "c" * 64,
              "asset": {"sha256": next_claim.banner_sha256, "byte_size": 100,
                        "width": 1536, "height": 1024}}
    bundle = build_client_bundle(next_claim, detail, "https://coineasy-newscard.netlify.app", NOW)
    assert bundle.claim.content_version_id == next_claim.content_version_id
    assert bundle.claim.banner_sha256 == next_claim.banner_sha256
    assert all(part.kind in {"image", "telegram", "x"} for part in bundle.parts)
