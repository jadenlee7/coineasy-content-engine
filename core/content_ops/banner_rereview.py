"""Prepare an exact-version private re-review claim after banner commit.

This is a pure handoff boundary. The content owner supplies a new outbox ID,
claim token and committed version/hash; this module never invents authority,
reads a database, calls Storage/provider, or sends Telegram.
"""
from datetime import datetime

from core.content_ops.banner_regeneration import require
from core.content_ops.worker import ReviewClaim, ReviewError, _uuid

def prepare_banner_rereview(claim, receipt, *, new_outbox_id, new_claim_token, now):
    """Return a new immutable private-review claim, or a fixed error."""
    try:
        require(type(claim) is ReviewClaim, "content_ops_rereview_claim_invalid")
        require(type(receipt) is dict and set(receipt) == {
            "content_version_id", "banner_sha256", "rereview_required", "execution_authorized"
        }, "content_ops_rereview_receipt_invalid")
        require(receipt["rereview_required"] is True
                and receipt["execution_authorized"] is False,
                "content_ops_rereview_receipt_invalid")
        version = _uuid(receipt["content_version_id"])
        require(version != claim.content_version_id, "content_ops_rereview_version_invalid")
        banner = receipt["banner_sha256"]
        require(type(banner) is str and len(banner) == 64
                and all(char in "0123456789abcdef" for char in banner),
                "content_ops_rereview_banner_invalid")
        outbox = _uuid(new_outbox_id)
        token = _uuid(new_claim_token)
        require(type(now) is datetime and now.tzinfo is not None,
                "content_ops_rereview_clock_invalid")
        raw = {
            "outbox_id": outbox, "claim_token": token,
            "client_id": claim.client_id, "kst_date": claim.kst_date,
            "content_item_id": claim.content_item_id,
            "content_version_id": version, "source_item_id": claim.source_item_id,
            "generate_job_id": claim.generate_job_id, "banner_sha256": banner,
            "title": claim.title, "telegram_copy": claim.telegram_copy,
            "x_copy": claim.x_copy, "source_url": claim.source_url,
            "source_published_at": claim.source_published_at.isoformat().replace("+00:00", "Z"),
        }
        next_claim = ReviewClaim.parse(raw, now)
        require(next_claim.client_id == claim.client_id
                and next_claim.content_item_id == claim.content_item_id
                and next_claim.source_item_id == claim.source_item_id
                and next_claim.generate_job_id == claim.generate_job_id
                and next_claim.telegram_copy == claim.telegram_copy
                and next_claim.x_copy == claim.x_copy
                and next_claim.source_url == claim.source_url,
                "content_ops_rereview_lineage_invalid")
        return next_claim
    except ReviewError:
        raise
    except Exception:
        raise ReviewError("content_ops_rereview_claim_invalid") from None


__all__ = ["prepare_banner_rereview"]
