"""Pure exact-card assembly; no DB, HTTP or Telegram I/O."""
import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from core.content_ops.private_review_card_candidate import (
    PrivateCardCandidateError, build_prepared_card,
)
from core.content_ops.private_review_card_receipt import (
    prepare_private_card, private_card_packet_sha256,
)
from core.content_ops.private_review_owner import private_snapshot
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.worker import ReviewClaim


NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
W = "11111111-1111-4111-8111-111111111111"
I = "22222222-2222-4222-8222-222222222222"
V = "33333333-3333-4333-8333-333333333333"
R = "44444444-4444-4444-8444-444444444444"
C = "55555555-5555-4555-8555-555555555555"
O = "66666666-6666-4666-8666-666666666666"
T = "77777777-7777-4777-8777-777777777777"
BOT, ROOM = 123456, -1001234567890
PNG = b"\x89PNG\r\n\x1a\nsynthetic-canonical-image"
SIGNER = ButtonSigner(b"s" * 32)
BINDINGS = EditBindings(b"e" * 32)


def claim():
    return ReviewClaim.parse({
        "outbox_id": O, "claim_token": T, "client_id": "yellow",
        "kst_date": "2026-09-23", "content_item_id": I,
        "content_version_id": V,
        "source_item_id": "88888888-8888-4888-8888-888888888888",
        "generate_job_id": "99999999-9999-4999-8999-999999999999",
        "banner_sha256": hashlib.sha256(PNG).hexdigest(),
        "title": "합성 검수용 제목", "telegram_copy": "합성 Telegram 전문",
        "x_copy": "합성 X 전문",
        "source_url": "https://x.com/yellow/status/123456789",
        "source_published_at": "2026-09-23T08:00:00Z",
    }, NOW)


def receipt():
    return {"status": "review_prepared", "review_id": R,
            "version_fingerprint": "a" * 64, "epoch": 0, "state": "active",
            "expires_at": "2026-09-23T09:30:00+00:00",
            "execution_authorized": False}


def prepare(**overrides):
    args = {"claim": claim(), "review_receipt": receipt(),
            "workspace_id": W, "card_id": C, "png": PNG,
            "signer": SIGNER, "bindings": BINDINGS,
            "bot_id": BOT, "chat_id": ROOM, "now": int(NOW.timestamp())}
    args.update(overrides)
    return build_prepared_card(**args)


def test_exact_claim_review_and_image_bind_one_four_part_packet():
    candidate = prepare()
    assert candidate.review == {"id": R, "workspace_id": W,
        "client_id": "yellow", "content_item_id": I,
        "content_version_id": V, "version_fingerprint": "a" * 64,
        "epoch": 0, "state": "active",
        "expires_at": "2026-09-23T09:30:00+00:00"}
    assert candidate.snapshot.eligibility == "blocked"
    assert candidate.snapshot.source_published_at == "2026-09-23T08:00:00.000000Z"
    assert candidate.snapshot == private_snapshot(candidate.review,
        {"channel_copy": {"telegram": claim().telegram_copy,
                          "x": claim().x_copy}},
        {"canonical_url": claim().source_url,
         "published_at": claim().source_published_at},
        {"sha256": hashlib.sha256(PNG).hexdigest()})
    assert candidate.outbox_id == O and candidate.claim_token == T
    assert candidate.room_binding == BINDINGS.digest("room", BOT, ROOM)
    requests = prepare_private_card(candidate.snapshot, SIGNER,
        candidate.room_binding, now=candidate.now)
    assert candidate.packet_sha256 == private_card_packet_sha256(requests,
        hashlib.sha256(PNG).hexdigest(), R, C)
    assert requests[-1]["kind"] == "controls"


@pytest.mark.parametrize("overrides", [
    {"png": PNG + b"changed"},
    {"png": b"not-png"},
    {"workspace_id": "bad"},
    {"card_id": "bad"},
    {"chat_id": 1},
    {"bot_id": True},
    {"now": True},
    {"now": int(NOW.timestamp()) + 1800},
    {"claim": replace(claim(), content_version_id="bad")},
    {"claim": replace(claim(), source_published_at=datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc))},
    {"review_receipt": {**receipt(), "version_fingerprint": "bad"}},
    {"review_receipt": {**receipt(), "epoch": True}},
    {"review_receipt": {**receipt(), "state": "held"}},
    {"review_receipt": {**receipt(), "execution_authorized": True}},
    {"review_receipt": {**receipt(), "provider_response": "must not leak"}},
])
def test_unbound_or_stale_candidate_fails_closed(overrides):
    with pytest.raises(PrivateCardCandidateError, match="private_card_candidate_invalid"):
        prepare(**overrides)
