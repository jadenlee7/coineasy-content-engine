"""Button-first review controller for the EXISTING content owner, not a publisher.

No network, credentials, polling, DB connection, or deploy-time wiring here.
The transport must authenticate Telegram updates. The owner adapter must perform
atomic exact-version CAS, reviewer attestations and per-destination outbox writes
in the existing owner DB. It must not delegate that authority to the courier.
There is deliberately no production adapter in this increment.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class ButtonReviewError(ValueError):
    """Fixed codes only: no user content, chat identity or credentials."""


ACTIONS = {"t": "edit_telegram", "x": "edit_x", "b": "edit_banner",
           "s": "source_checked", "c": "claims_checked", "h": "hold",
           "a": "approve_and_publish"}
LABELS = {"t": "✏️ Telegram 수정", "x": "✏️ X 수정", "b": "🎨 배너 수정",
          "s": "원문 사실 확인", "c": "문안·배너 확인", "h": "보류",
          "a": "✅ 두 채널 승인·게시"}


def _check(condition, code="review_buttons_invalid"):
    if not condition:
        raise ButtonReviewError(code)


def _uuid(value):
    _check(type(value) is str and bool(re.fullmatch(
        r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}", value)))
    return UUID(value)


def _text(value, limit):
    _check(type(value) is str and value.strip() and len(value.encode("utf-16-le")) // 2 <= limit)
    _check(not re.search(r"t\.me/(?:\+|joinchat/)|api\.telegram\.org/bot|"
                         r"[1-9][0-9]{4,15}:[A-Za-z0-9_-]{30,100}", value, re.I))
    _check(not any(ord(c) < 32 and c not in "\n\t" for c in value))
    return value


@dataclass(frozen=True, repr=False)
class ReviewSnapshot:
    workspace_id: str
    client_id: str
    content_item_id: str
    content_version_id: str
    source_url: str
    source_published_at: str
    telegram_copy: str
    x_copy: str
    banner_sha256: str
    # Checked by owner, not sourced from callback payload or a Telegram message.
    eligibility: str  # daily_ready, recap_requires_separate_approval, blocked

    def validate(self):
        # Owner review UI only. The private confirmation relay imports shared
        # codecs from this module without carrying publication preparation code.
        from core.publications.handoff import CLIENT_TARGETS
        for value in (self.workspace_id, self.content_item_id, self.content_version_id):
            _uuid(value)
        _check(self.client_id in CLIENT_TARGETS)
        handle = CLIENT_TARGETS[self.client_id][2]
        _check(bool(re.fullmatch(r"https://x\.com/" + re.escape(handle) + r"/status/[0-9]{1,30}", self.source_url, re.I)))
        _text(self.source_published_at, 40)
        _text(self.telegram_copy, 3700)
        _text(self.x_copy, 1000)
        _check(bool(re.fullmatch(r"[a-f0-9]{64}", self.banner_sha256)))
        _check(self.eligibility in {"daily_ready", "recap_requires_separate_approval", "blocked"})

    def digest(self):
        self.validate()
        return hashlib.sha256(json.dumps(self.__dict__, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ButtonSigner:
    """51 ASCII bytes: version UUID + expiry + action + 128-bit HMAC.

    MAC binds full snapshot AND trusted private room binding. No destination,
    text, chat ID, private URL or credentials are carried by the callback.
    Key must be a dedicated server-side secret, never the relay token.
    """
    def __init__(self, key: bytes):
        _check(type(key) is bytes and len(key) >= 32)
        self._key = key

    def _mac(self, body, snapshot, room_binding):
        _check(type(room_binding) is str and 1 <= len(room_binding) <= 128)
        return hmac.new(self._key, body + snapshot.digest().encode() + b"\0" +
                        room_binding.encode(), hashlib.sha256).digest()[:16]

    def issue(self, snapshot, action, room_binding, *, now, expires_at):
        _check(type(now) is int and type(expires_at) is int
               and 0 <= now < expires_at <= min(now + 1800, 2**32 - 1))
        _check(action in ACTIONS)
        body = b"\x01" + _uuid(snapshot.content_version_id).bytes + expires_at.to_bytes(4, "big") + action.encode()
        return base64.urlsafe_b64encode(body + self._mac(body, snapshot, room_binding)).rstrip(b"=").decode()

    def verify(self, token, snapshot, room_binding, *, now):
        _check(type(now) is int and now >= 0)
        _check(type(token) is str and bool(re.fullmatch(r"[A-Za-z0-9_-]{51}", token)), "review_button_invalid")
        raw = base64.urlsafe_b64decode(token + "=")
        # Require canonical base64 as well as a valid MAC (unused bits matter).
        _check(base64.urlsafe_b64encode(raw).rstrip(b"=").decode() == token, "review_button_invalid")
        body, mac = raw[:22], raw[22:]
        _check(body[0] == 1 and body[1:17] == _uuid(snapshot.content_version_id).bytes
               and hmac.compare_digest(mac, self._mac(body, snapshot, room_binding)), "review_button_stale_or_invalid")
        expires = int.from_bytes(body[17:21], "big")
        _check(now < expires <= now + 1800, "review_button_expired")
        action = chr(body[21])
        _check(action in ACTIONS, "review_button_invalid")
        return ACTIONS[action]


def review_messages(snapshot: ReviewSnapshot, signer, room_binding, *, now, private_only=False):
    """Request-shaped text parts only. Existing courier owns actual delivery.

    Bind buttons to the review-control message receipt after all three parts
    (canonical image + full TG copy + full X copy) have confirmed delivery.
    Never put buttons on a partially delivered or uncertain bundle.
    """
    snapshot.validate()
    _check(type(private_only) is bool)
    from core.publications.handoff import CLIENT_TARGETS
    tg, x, _ = CLIENT_TARGETS[snapshot.client_id]
    def button(action):
        return {"text": LABELS[action], "callback_data": signer.issue(snapshot,
            action, room_binding, now=now, expires_at=now + 1800)}
    status = "최신 뉴스 검수" if snapshot.eligibility == "daily_ready" else "게시 보류 · 리캡/출처 별도 검토 필요"
    controls = (f"{snapshot.client_id.upper()} · {status}\n"
                f"Telegram → @{tg}\nX → @{x} (Typefully)\n"
                f"공식 원문: {snapshot.source_url}\n공식 게시: {snapshot.source_published_at}\n"
                f"버전: {snapshot.content_version_id}\n"
                "원문 사실과 최종 문안·배너를 각각 확인한 뒤 두 채널 게시를 승인하세요.\n"
                "수정하면 이전 확인·승인이 초기화됩니다. 버튼은 30분 후 만료됩니다.")
    if private_only:
        controls = (f"{snapshot.client_id.upper()} · 비공개 제작·검수\n"
                    f"공식 원문: {snapshot.source_url}\n공식 게시: {snapshot.source_published_at}\n"
                    f"버전: {snapshot.content_version_id}\n"
                    "아래 두 확인은 이 버전의 비공개 검수 기록입니다. 공식 채널 게시 승인이 아닙니다.\n"
                    "수정·재제작은 새 검토본을 만들고 기존 확인을 무효화합니다.\n"
                    "버튼은 30분 후 만료됩니다.")
    rows = [[button("t"), button("x")], [button("b"), button("h")],
            [button("s"), button("c")]]
    if private_only:
        rows[1][0]["text"] = "🎨 배너 다시 만들기"
        rows[2][0]["text"] = "✅ 공식 원문 확인"
        rows[2][1]["text"] = "✅ 문안·배너 확인"
        # Reserved routing namespace for the existing shared polling owner.
        # The signed 51-byte token remains unchanged inside this 55-byte value.
        for row in rows:
            for item in row:
                item["callback_data"] = "ce1:" + item["callback_data"]
    else:
        rows.append([button("a")])
    return {
        "telegram": {"text": "[Telegram 공지 전문]\n" + snapshot.telegram_copy},
        "x": {"text": "[X 게시글 전문]\n" + snapshot.x_copy},
        "controls": {"text": controls, "reply_markup": {"inline_keyboard": rows}},
        "snapshot_sha256": snapshot.digest(),
    }


@dataclass(frozen=True, repr=False)
class VerifiedCallback:
    """Only constructed by authenticated update transport; never from body alone."""
    callback_id: str
    actor_id: str
    actor_is_bot: bool
    room_binding: str
    message_binding: str
    token: str


class ReviewActionOwner(Protocol):
    def read_review(self, room_binding: str, message_binding: str) -> ReviewSnapshot:
        """Lookup registered, completely delivered review by exact room/message."""
    def apply_review_action(self, *, snapshot_sha256: str, version_id: str,
                            action: str, actor_id: str, idempotency_key: str) -> dict:
        """ONE owner transaction, under row lock: recheck current version/hash,
        fully delivered bundle, reviewer allowlist, source/destination readiness,
        no prior attempts/unknown receipts; persist action idempotently.
        Each reviewer attests BOTH checks for this version/hash. Approval writes
        double-fact-check@1 plus distinct TG/X outboxes, or nothing. Editing uses
        actor-bound reply prompts; only exact prompt replies create a new immutable
        version. Figma changes require exported PNG hash and a new version.
        Never import a local prototype approval or use V1 workspace-wide claims.
        """


def handle_review_callback(event: VerifiedCallback, *, enabled: bool, signer,
                           owner: ReviewActionOwner, allowed_reviewers: frozenset,
                           room_binding: str, now: int, private_only=False):
    if enabled is not True:
        return {"status": "disabled", "public_send_attempted": False}
    _check(type(private_only) is bool)
    _check(type(event) is VerifiedCallback and event.actor_is_bot is False
           and event.room_binding == room_binding
           and event.actor_id in allowed_reviewers, "review_actor_forbidden")
    _check(type(event.callback_id) is str and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event.callback_id)))
    snapshot = owner.read_review(event.room_binding, event.message_binding)
    action = signer.verify(event.token, snapshot, room_binding, now=now)
    # Hiding the button alone is insufficient: old signed publication callbacks
    # must also fail BEFORE any owner write when serving the private workflow.
    _check(not (private_only and action == "approve_and_publish"),
           "review_private_publication_forbidden")
    if action == "approve_and_publish":
        _check(snapshot.eligibility == "daily_ready", "review_source_requires_separate_approval")
    result = owner.apply_review_action(snapshot_sha256=snapshot.digest(),
        version_id=snapshot.content_version_id, action=action, actor_id=event.actor_id,
        idempotency_key=hashlib.sha256(event.callback_id.encode()).hexdigest())
    # A queued receipt is NOT a public delivery receipt. Never forward raw errors.
    _check(type(result) is dict and set(result) == {"status", "reused"}
           and result["status"] in {"checked", "edit_requested", "held", "queued"}
           and type(result["reused"]) is bool, "review_owner_readback_unknown")
    return {**result, "public_send_attempted": False}


def delivery_label(telegram_status, x_status):
    """Provider attempts/receipts stay separately owned and are never retried here."""
    known = {"pending", "sending", "confirmed", "failed", "unknown"}
    _check(telegram_status in known and x_status in known)
    if "unknown" in (telegram_status, x_status):
        return "전달 확인 필요 · 자동 재전송 안 함"
    if telegram_status == x_status == "confirmed":
        return "두 채널 API 전달 확인 · 공개 링크 확인 필요"
    if "confirmed" in (telegram_status, x_status):
        return "일부 전달 확인 · 완료된 채널 재전송 안 함"
    return "게시 대기/확인 필요"
