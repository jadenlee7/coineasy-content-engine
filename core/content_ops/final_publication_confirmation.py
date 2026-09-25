"""Local-only second-stage publication confirmation for an exact reviewed version.

This module has no transport, database connection, publisher, or runtime mount.
Only a trusted owner can supply the snapshot and durably record the decision.
The current private decision ledger creates no approval or channel outbox. A
rendered card or a valid HMAC is never publication authority by itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from core.content_ops.review_buttons import ReviewSnapshot
from core.publications.handoff import CLIENT_TARGETS


_UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z")
_TOKEN = re.compile(r"ce2:[A-Za-z0-9_-]{51}\Z")
_ACTIONS = {"p": "confirm_publication", "h": "hold"}


class FinalConfirmationError(ValueError):
    """Fixed error code only; never echo copy, destination, or identity."""


def _require(value: bool, code: str = "final_confirmation_invalid") -> None:
    if not value:
        raise FinalConfirmationError(code)


def _uuid(value: str) -> str:
    _require(type(value) is str and bool(_UUID.fullmatch(value)))
    _require(UUID(value).int != 0)
    return value


@dataclass(frozen=True, repr=False)
class FinalConfirmationSnapshot:
    """Trusted owner projection; it must be re-read under lock on decision.

    Both checks must belong to the same reviewer and review epoch. Copy and
    banner are bound through ``review`` and the DB-issued version fingerprint.
    These values are not accepted from a Telegram update or an agent message.
    """

    review: ReviewSnapshot
    review_id: str
    card_id: str
    reviewer_id: str
    source_check_actor_id: str
    claims_check_actor_id: str
    review_epoch: int
    source_check_epoch: int
    claims_check_epoch: int
    version_fingerprint: str
    approval_count: int
    publication_count: int
    release_sha: str

    def validate(self) -> None:
        _require(type(self.review) is ReviewSnapshot)
        self.review.validate()
        _require(self.review.eligibility == "daily_ready", "final_confirmation_not_ready")
        published = self.review.source_published_at
        _require(type(published) is str and bool(_TIME.fullmatch(published)),
                 "final_confirmation_source_time_invalid")
        try:
            parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
            _require(parsed.utcoffset() is not None,
                     "final_confirmation_source_time_invalid")
        except ValueError:
            raise FinalConfirmationError("final_confirmation_source_time_invalid") from None
        for value in (self.review_id, self.card_id, self.reviewer_id,
                      self.source_check_actor_id, self.claims_check_actor_id):
            _uuid(value)
        _require(self.source_check_actor_id == self.reviewer_id
                 and self.claims_check_actor_id == self.reviewer_id,
                 "final_confirmation_checks_incomplete")
        _require(type(self.review_epoch) is int and 0 <= self.review_epoch < 2**53
                 and type(self.source_check_epoch) is int
                 and type(self.claims_check_epoch) is int
                 and self.source_check_epoch == self.review_epoch
                 and self.claims_check_epoch == self.review_epoch,
                 "final_confirmation_checks_incomplete")
        _require(type(self.version_fingerprint) is str
                 and bool(_SHA.fullmatch(self.version_fingerprint)))
        _require(type(self.release_sha) is str
                 and bool(_RELEASE.fullmatch(self.release_sha)))
        _require(type(self.approval_count) is int and self.approval_count == 0
                 and type(self.publication_count) is int and self.publication_count == 0,
                 "final_confirmation_already_acted")

    def digest(self) -> str:
        self.validate()
        payload = dict(review_sha256=self.review.digest(), review_id=self.review_id,
            card_id=self.card_id, reviewer_id=self.reviewer_id,
            source_check_actor_id=self.source_check_actor_id,
            claims_check_actor_id=self.claims_check_actor_id,
            review_epoch=self.review_epoch, source_check_epoch=self.source_check_epoch,
            claims_check_epoch=self.claims_check_epoch,
            version_fingerprint=self.version_fingerprint,
            approval_count=self.approval_count, publication_count=self.publication_count,
            release_sha=self.release_sha)
        return hashlib.sha256(json.dumps(payload, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()


class FinalConfirmationSigner:
    """Dedicated ce2 callback namespace; never reuse the private-card key."""

    def __init__(self, key: bytes):
        _require(type(key) is bytes and 32 <= len(key) <= 256)
        self._key = key

    def _mac(self, body: bytes, snapshot: FinalConfirmationSnapshot,
             room_binding: str) -> bytes:
        _require(type(room_binding) is str and 1 <= len(room_binding) <= 128)
        return hmac.new(self._key, b"final-publication-confirmation@1\0" + body
            + snapshot.digest().encode() + b"\0" + room_binding.encode(),
            hashlib.sha256).digest()[:16]

    def issue(self, snapshot: FinalConfirmationSnapshot, action: str,
              room_binding: str, *, now: int, expires_at: int) -> str:
        _require(type(snapshot) is FinalConfirmationSnapshot)
        snapshot.validate()
        _require(type(now) is int and type(expires_at) is int
                 and 0 <= now < expires_at <= min(now + 900, 2**32 - 1))
        _require(action in _ACTIONS)
        body = b"\x04" + UUID(snapshot.review.content_version_id).bytes
        body += expires_at.to_bytes(4, "big") + action.encode()
        raw = body + self._mac(body, snapshot, room_binding)
        return "ce2:" + base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def verify(self, token: str, snapshot: FinalConfirmationSnapshot,
               room_binding: str, *, now: int) -> str:
        _require(type(snapshot) is FinalConfirmationSnapshot)
        snapshot.validate()
        _require(type(now) is int and now >= 0)
        _require(type(token) is str and bool(_TOKEN.fullmatch(token)),
                 "final_confirmation_token_invalid")
        encoded = token[4:]
        raw = base64.urlsafe_b64decode(encoded + "=")
        _require(len(raw) == 38 and base64.urlsafe_b64encode(raw).rstrip(b"=").decode() == encoded,
                 "final_confirmation_token_invalid")
        body, mac = raw[:22], raw[22:]
        _require(body[0] == 4 and body[1:17] == UUID(snapshot.review.content_version_id).bytes
                 and hmac.compare_digest(mac, self._mac(body, snapshot, room_binding)),
                 "final_confirmation_stale_or_invalid")
        expiry = int.from_bytes(body[17:21], "big")
        _require(now < expiry <= now + 900, "final_confirmation_expired")
        action = chr(body[21])
        _require(action in _ACTIONS, "final_confirmation_token_invalid")
        return _ACTIONS[action]


def final_confirmation_messages(snapshot: FinalConfirmationSnapshot,
        signer: FinalConfirmationSigner, room_binding: str, *, now: int) -> dict:
    """Return a four-part private-room packet, never a send request."""
    snapshot.validate()
    _require(type(signer) is FinalConfirmationSigner)
    telegram_target, typefully_target, _ = CLIENT_TARGETS[snapshot.review.client_id]
    def button(action: str, label: str) -> dict:
        return {"text": label, "callback_data": signer.issue(snapshot, action,
            room_binding, now=now, expires_at=now + 900)}
    controls = (
        f"{snapshot.review.client_id.upper()} · 최종 게시 확인 (2차)\n"
        f"Telegram 공식 채널: @{telegram_target}\n"
        f"X 게시 준비 대상: Typefully {typefully_target}\n"
        f"콘텐츠 버전: {snapshot.review.content_version_id}\n"
        f"검수 카드: {snapshot.card_id}\n"
        f"공식 원문: {snapshot.review.source_url}\n"
        f"공식 게시 시각: {snapshot.review.source_published_at}\n"
        f"배너 SHA-256: {snapshot.review.banner_sha256}\n"
        "아래 배너·Telegram·X 전문을 다시 확인하세요.\n"
        "승인은 이 정확한 버전의 최종 검수 결정만 기록합니다. "
        "공개 게시 대기열이나 전송 완료를 뜻하지 않습니다.\n"
        "버튼은 15분 후 만료되며, 수정·새 버전은 기존 확인을 무효화합니다."
    )
    return {"banner": {"sha256": snapshot.review.banner_sha256},
        "telegram": {"text": snapshot.review.telegram_copy},
        "x": {"text": snapshot.review.x_copy},
        "controls": {"text": controls, "reply_markup": {"inline_keyboard": [[
            button("p", "✅ 이 버전 최종 게시 승인"), button("h", "보류")]]}},
        "snapshot_sha256": snapshot.digest()}


@dataclass(frozen=True, repr=False)
class VerifiedFinalCallback:
    callback_id: str
    actor_id: str
    actor_is_bot: bool
    room_binding: str
    message_binding: str
    bot_binding: str
    human_binding: str
    token: str


class FinalDecisionOwner(Protocol):
    def read_confirmation(self, room_binding: str, message_binding: str,
                          bot_binding: str, human_binding: str) -> FinalConfirmationSnapshot:
        """Read only a fully delivered, registered final card by exact message."""

    def apply_final_decision(self, *, snapshot_sha256: str, version_id: str,
                             card_id: str, version_fingerprint: str,
                             message_binding: str, bot_binding: str,
                             room_binding: str, human_binding: str,
                             runtime_release_sha: str, reviewer_id: str,
                             action: str, idempotency_key: str) -> dict:
        """Recheck and durably record a private decision; never publish."""


def handle_final_confirmation(event: VerifiedFinalCallback, *, enabled: bool,
        signer: FinalConfirmationSigner, owner: FinalDecisionOwner,
        allowed_reviewers: frozenset, room_binding: str,
        runtime_release_sha: str, now: int) -> dict:
    if enabled is not True:
        return {"status": "disabled", "public_send_attempted": False}
    _require(type(event) is VerifiedFinalCallback and event.actor_is_bot is False
             and event.room_binding == room_binding and event.actor_id in allowed_reviewers,
             "final_confirmation_actor_forbidden")
    _require(type(event.callback_id) is str and bool(re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", event.callback_id)))
    _require(type(event.room_binding) is str and bool(_SHA.fullmatch(event.room_binding)))
    _require(all(type(value) is str and bool(_SHA.fullmatch(value)) for value in
                 (event.message_binding, event.bot_binding, event.human_binding)))
    _require(type(runtime_release_sha) is str and bool(_RELEASE.fullmatch(runtime_release_sha)),
             "final_confirmation_release_invalid")
    snapshot = owner.read_confirmation(event.room_binding, event.message_binding,
        event.bot_binding, event.human_binding)
    snapshot.validate()
    _require(event.actor_id == snapshot.reviewer_id,
             "final_confirmation_actor_forbidden")
    _require(snapshot.release_sha == runtime_release_sha,
             "final_confirmation_release_conflict")
    action = signer.verify(event.token, snapshot, room_binding, now=now)
    result = owner.apply_final_decision(snapshot_sha256=snapshot.digest(),
        version_id=snapshot.review.content_version_id, reviewer_id=event.actor_id,
        card_id=snapshot.card_id, version_fingerprint=snapshot.version_fingerprint,
        message_binding=event.message_binding, bot_binding=event.bot_binding,
        room_binding=event.room_binding, human_binding=event.human_binding,
        runtime_release_sha=runtime_release_sha, action=action,
        idempotency_key=hashlib.sha256(event.callback_id.encode()).hexdigest())
    _require(type(result) is dict and set(result) == {"status", "reused", "decision_id"}
             and result["status"] in {"confirmed_pending_publication_owner", "held"}
             and type(result["reused"]) is bool and type(result["decision_id"]) is str
             and bool(_UUID.fullmatch(result["decision_id"])),
             "final_confirmation_owner_readback_unknown")
    return {**result, "public_send_attempted": False}
