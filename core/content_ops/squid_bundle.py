"""Private review payloads, not an approval or a second send engine.

The existing worker/gateway/outbox owns delivery. All three parts are validated
before its one-shot begin; full copy is never silently truncated or rewritten.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import struct
import zlib
from datetime import datetime

from core.content_ops.worker import ReviewClaim, ReviewError, build_packet


KINDS = ("image", "telegram", "x")


def _check(value):
    if not value:
        raise ReviewError("content_ops_bundle_invalid")


def _hash(value: str):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, repr=False)
class BundlePart:
    index: int
    kind: str
    text: str
    sha256: str


@dataclass(frozen=True, repr=False)
class SquidBundle:
    claim: ReviewClaim
    snapshot_sha256: str
    asset: dict
    parts: tuple[BundlePart, ...]
    packet_sha256: str

    @property
    def manifest(self):
        return [{"kind": p.kind, "sha256": p.sha256} for p in self.parts]


def build_squid_bundle(claim: ReviewClaim, detail: object, origin: str, now: datetime) -> SquidBundle:
    # Keep the existing transport's Squid-only boundary unchanged.
    _check(claim.client_id == "squid")
    return build_client_bundle(claim, detail, origin, now)


def build_client_bundle(claim: ReviewClaim, detail: object, origin: str, now: datetime) -> SquidBundle:
    """Four-client rendering only; does not widen worker/gateway send scopes."""
    names = {"squid": "Squid", "yellow": "Yellow", "babylon": "Babylon", "origintrail": "OriginTrail"}
    _check(claim.client_id in names and type(detail) is dict
           and set(detail) == {"claim", "asset", "snapshot_sha256"})
    current = ReviewClaim.parse(detail["claim"], now)
    # Legacy claim copy/title may be shortened. Every identity/source/hash must
    # still match; only these three text fields may be replaced by full DB copy.
    _check(replace(current, title=claim.title, telegram_copy=claim.telegram_copy,
                   x_copy=claim.x_copy) == claim)
    snapshot, asset = detail["snapshot_sha256"], detail["asset"]
    _check(type(snapshot) is str and len(snapshot) == 64 and all(c in "0123456789abcdef" for c in snapshot))
    _check(type(asset) is dict and set(asset) == {"sha256", "byte_size", "width", "height"}
           and asset["sha256"] == claim.banner_sha256)
    for key in ("byte_size", "width", "height"):
        _check(type(asset[key]) is int and asset[key] > 0)
    _check(45 <= asset["byte_size"] <= 10_000_000
           and asset["width"] + asset["height"] <= 10000
           and max(asset["width"], asset["height"]) / min(asset["width"], asset["height"]) <= 20)
    review_url = build_packet(current, origin, now).review_url
    name = names[claim.client_id]
    caption = (f"{name} 데일리 뉴스 · 팀 검수용 (승인·공개 게시 아님)\n"
               f"공식 원문: {current.source_url}\n검수: {review_url}\n"
               f"버전: {current.content_version_id}\n"
               "이미지·Telegram·X 세 부분을 모두 확인해주세요.")
    texts = (caption,
             f"[{name} · Telegram 공지 전문]\n버전: {current.content_version_id}\n\n{current.telegram_copy}",
             f"[{name} · X 게시글 전문]\n버전: {current.content_version_id}\n\n{current.x_copy}")
    parts = []
    for index, (kind, text) in enumerate(zip(KINDS, texts)):
        # Plain text avoids HTML entity expansion and preserves original copy.
        _check(len(text.encode("utf-16-le")) // 2 <= (1024 if kind == "image" else 4096))
        payload = text + ("\n" + claim.banner_sha256 if kind == "image" else "")
        parts.append(BundlePart(index, kind, text, _hash(payload)))
    digest = _hash(json.dumps({"snapshot_sha256": snapshot,
        "version": claim.content_version_id, "parts": [(p.kind, p.sha256) for p in parts]},
        sort_keys=True, separators=(",", ":")))
    return SquidBundle(current, snapshot, dict(asset), tuple(parts), digest)


def validate_bundle_image(data: bytes, bundle: SquidBundle) -> bytes:
    _check(type(data) is bytes and len(data) == bundle.asset["byte_size"]
           and hashlib.sha256(data).hexdigest() == bundle.claim.banner_sha256
           and data.startswith(b"\x89PNG\r\n\x1a\n"))
    try:
        # Full pixel decode is the server-side gateway's responsibility. The
        # isolated courier adds a bounded PNG envelope/CRC check to its exact
        # byte/hash binding without acquiring another runtime dependency.
        offset = 8
        chunks = []
        while offset < len(data):
            _check(len(chunks) < 65536 and offset + 12 <= len(data))
            size = struct.unpack_from(">I", data, offset)[0]
            kind = data[offset + 4:offset + 8]
            end = offset + 8 + size
            _check(end + 4 <= len(data) and kind not in (b"acTL", b"fcTL", b"fdAT"))
            _check(zlib.crc32(data[offset + 4:end]) == struct.unpack_from(">I", data, end)[0])
            if not chunks:
                _check(kind == b"IHDR" and size == 13)
                _check(struct.unpack_from(">II", data, offset + 8) == (bundle.asset["width"], bundle.asset["height"]))
            else:
                _check(kind != b"IHDR")
            if kind == b"IEND":
                _check(size == 0 and end + 4 == len(data))
            chunks.append(kind)
            offset = end + 4
        _check(chunks[-1] == b"IEND" and b"IDAT" in chunks)
    except Exception:
        raise ReviewError("content_ops_bundle_image_invalid") from None
    return data  # No resizing, re-encoding, font/character/layout changes.


def bundle_delivery_label(status: str, receipts: list) -> str:
    valid = (type(receipts) is list and len(receipts) == 3
             and all(type(r) is dict and set(r) == {"index", "kind", "outcome", "message_id"}
                     and type(r.get("index")) is int and r["index"] == i and r.get("kind") == KINDS[i]
                     and r.get("outcome") == "sent" and type(r.get("message_id")) is int
                     and 0 < r["message_id"] <= 2**53 - 1 for i, r in enumerate(receipts))
             and len({r["message_id"] for r in receipts}) == 3)
    if status == "sent" and valid:
        return "팀 전달 완료"
    if status == "obsolete" and not receipts:
        return "수정본 검수 필요"
    if receipts or status in {"delivery_unknown", "rejected", "sent"}:
        return "전달 확인 필요 · 자동 재전송 안 함"
    return "검수 묶음 전달 대기"
